"""The request store (COLLAB-2, #32): request/v1 requests and their claims, recorded durably under the
bridge's outbox authority (docs/collaboration_foundations_design.md sections 1, 3 and 4; D-8 and D-15).

One SQLite file, requests.db in the chat state directory, format request-store/1, with `schema` and
`writer_model` meta rows like outbox.db's. Only the outbox Authority opens it, while it holds the authority
lock, and runs every operation on it in one requests.db transaction; outbox.db is never written by them.
Nothing here imports delivery_store: the authority hands in what it needs (its epoch, and a read-only lookup
of a verified post in outbox.db's messages table).

Opening. An existing requests.db is classified read-only before anything opens it for writing, as the
bridge's BootstrapStore.preflight does for outbox.db: the file is new only when none of requests.db, -wal,
-shm and -journal exists, or an empty requests.db has none of them; a sidecar without a non-empty
requests.db is ambiguous evidence. A non-empty file must be SQLite in WAL mode, with no rollback journal,
whose tables and meta rows are this store's. Anything else is refused with a named reason and every byte
kept (Refused): the authority then runs, and only the request operations are refused (#32 decision 4).
During the bridge's activated start, the pinned state root's identity is checked immediately before and
after the pathname-based open (#21 amendment 7): a changed root is RootChanged, which fails the whole start.

What is recorded, per execution key (request ID and revision):
- registration: one atomic insert-if-absent of the key, its request/v1 digest, the registrant's verdict,
  the room, the decision-maker (in the chat account form), the permitted claimants, the named successors
  and the registrant, all immutable for the key. The same key and digest is a repeat, answered from the
  record; the same key with another digest is a conflicting reuse, refused and recorded, the record
  unchanged. A new revision is registered only by the earlier key's registrant or one of its named
  successors, and is refused while a newer revision exists; an unstarted earlier key is closed
  `superseded` (its claim kept), and the new one waits while an earlier key has started with no outcome;
- acceptance: by the decision-maker, citing a verified post in outbox.db whose account is the decision-maker
  and whose text names the request ID as a whole token. Distinct from receipt, claim and start;
- claim: a compare-and-set holding owner, generation and fence token (D-60's shape). Each key has its own
  generation; the authority's epoch is recorded beside it, for audit only, so a claim survives a restart;
- start: by the claimant, with its generation and fence token, before any external effect;
- reconciliation: by the claimant or the decision-maker, fenced to the generation it reconciles, with a
  free-text evidence reference. `no_effect` makes the key claimable at the next generation (or closes it
  `superseded` when a later revision waits); `effect` closes it `reconciled`, terminal for COLLAB-2.

After a restart, a started key with no outcome is `unknown` and stays so until a reconciliation; nothing
ever takes it over. A refusal of a well-formed operation (a wrong actor, a stale generation, an unverified
verdict, a conflicting reuse...) is recorded before the reply, with the key's record unchanged.

The guarantee covers only callers that check the claim and its start before their side effects (D-11): the
declared agent IDs are cooperative, like writer admission's same-user boundary, and guard against mistakes,
not deliberate tampering. Results, stops, blockers, closure, expiry and tombstones are COLLAB-3; budgets
are COLLAB-4.

Standard library only, Python 3.10 on.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import time
from pathlib import Path

import request_v1

FORMAT = "request-store/1"
WRITER_MODEL = "bridge-exclusive/1"
NAME = "requests.db"
SIDECARS = ("requests.db-wal", "requests.db-shm", "requests.db-journal")
OPEN_WAIT = 5.0  # SQLite's busy timeout
OPS = ("register", "lookup", "accept", "claim", "start", "reconcile")
READ_OPS = ("lookup",)  # answered in every maintenance state, never inside a section, with no write
NEW_WORK = ("claim", "start")  # refused in `draining`, like the registration of a new key (writer admission 4.5)
VERDICTS = (request_v1.VERIFIED, request_v1.UNRESOLVED, request_v1.REFUSED)
OUTCOMES = ("no_effect", "effect")
LIVE = ("claimed", "started", "unknown")      # a key with a live claim
STARTED = ("started", "unknown")              # a start with no recorded outcome
TERMINAL = ("superseded", "reconciled")
LOOKUP_REFUSALS = 20  # the latest refusals a lookup shows per request ID

SCHEMA = """
CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE requests (request_id TEXT NOT NULL, rev INTEGER NOT NULL, digest TEXT NOT NULL, line TEXT NOT NULL,
                       verdict TEXT NOT NULL, reasons TEXT NOT NULL, authority TEXT, room TEXT NOT NULL,
                       decision_maker TEXT NOT NULL, claimants TEXT NOT NULL, successors TEXT NOT NULL,
                       registrant TEXT NOT NULL, state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
                       superseded_by INTEGER, registered_at REAL NOT NULL, epoch INTEGER NOT NULL,
                       PRIMARY KEY (request_id, rev));
CREATE TABLE acceptances (request_id TEXT NOT NULL, rev INTEGER NOT NULL, msgid TEXT NOT NULL, account TEXT NOT NULL,
                          accepted_at REAL NOT NULL, epoch INTEGER NOT NULL, PRIMARY KEY (request_id, rev));
CREATE TABLE claims (request_id TEXT NOT NULL, rev INTEGER NOT NULL, generation INTEGER NOT NULL, owner TEXT NOT NULL,
                     fence TEXT NOT NULL, claimed_at REAL NOT NULL, epoch INTEGER NOT NULL, started_at REAL,
                     start_epoch INTEGER, PRIMARY KEY (request_id, rev, generation));
CREATE TABLE reconciliations (request_id TEXT NOT NULL, rev INTEGER NOT NULL, generation INTEGER NOT NULL,
                              outcome TEXT NOT NULL, agent TEXT NOT NULL, evidence TEXT NOT NULL,
                              reconciled_at REAL NOT NULL, epoch INTEGER NOT NULL,
                              PRIMARY KEY (request_id, rev, generation));
CREATE TABLE refusals (id INTEGER PRIMARY KEY, request_id TEXT, rev INTEGER, op TEXT NOT NULL, agent TEXT,
                       reason TEXT NOT NULL, detail TEXT, refused_at REAL NOT NULL, epoch INTEGER NOT NULL);
"""
TABLES = frozenset({"meta", "requests", "acceptances", "claims", "reconciliations", "refusals"})


class Refused(Exception):
    """requests.db cannot be used (foreign, corrupt, unreadable or busy): only the request operations are
    refused, with this reason, and the file is kept as it is."""


class RootChanged(Exception):
    """The pinned state root was moved or replaced around requests.db's open: the authority's whole start
    fails closed (#21 amendment 7)."""


# --- what a request operation must be, before any of it is used -------------------------------------------------

def _name(value):
    """An agent ID, account or room: a non-empty string without spaces, commas or control characters."""
    return (isinstance(value, str) and 0 < len(value) <= 200
            and not any(c.isspace() or c == "," or ord(c) < 32 or ord(c) == 127 for c in value))


def _count(value, low):
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit and "\x00" not in value


_REQUEST_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def request_error(r):
    """Why a request-store operation is malformed, or None: every field it uses, checked before any of it is.
    The op and the writer are checked by the caller. The request/v1 ID is `req_id`, apart from the frame's
    own `request_id`."""
    op = r["op"]
    if op == "register":
        verdict = r.get("verdict")
        if not _text(r.get("line"), request_v1.MAX_LINE_BYTES):
            return "invalid request line"
        if not (isinstance(verdict, dict) and verdict.get("status") in VERDICTS
                and isinstance(verdict.get("reasons"), list) and all(_text(x, 200) for x in verdict["reasons"])
                and (verdict.get("authority") is None or _text(verdict["authority"], 200))):
            return "invalid verdict"
        if not (_name(r.get("room")) and _name(r.get("decision_maker"))):
            return "invalid room or decision-maker"
        claimants, successors = r.get("claimants"), r.get("successors", [])
        if not (isinstance(claimants, list) and claimants and all(_name(x) for x in claimants)
                and isinstance(successors, list) and all(_name(x) for x in successors)):
            return "invalid claimants or successors"
        return None if _name(r.get("agent")) else "invalid agent"
    if not (_text(r.get("req_id"), 128) and _REQUEST_ID.fullmatch(r["req_id"])):
        return "invalid request ID"
    if op == "lookup":
        return None if r.get("rev") is None or _count(r.get("rev"), 1) else "invalid revision"
    if not _count(r.get("rev"), 1):
        return "invalid revision"
    if op == "accept":
        return None if _text(r.get("msgid"), 128) else "invalid message ID"
    if not _name(r.get("agent")):
        return "invalid agent"
    if op in ("start", "reconcile") and not _count(r.get("generation"), 1):
        return "invalid generation"
    if op == "start" and not _text(r.get("fence"), 128):
        return "invalid fence token"
    if op == "reconcile" and not (r.get("outcome") in OUTCOMES and _text(r.get("evidence"), 1000)):
        return "invalid reconciliation"
    return None


def names_request(text, request_id):
    """Whether `text` names `request_id` as a whole token: `proj-20261014-12` does not name `proj-20261014-1`."""
    return isinstance(text, str) and re.search(
        r"(?<![A-Za-z0-9_-])" + re.escape(request_id) + r"(?![A-Za-z0-9_-])", text) is not None


def _same(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


class RequestStore:
    """The one connection to requests.db, which the authority opens and closes, and the transitions on it."""

    def __init__(self, state, *, clock=time.time, fs=None):
        self.state, self.clock, self.fs = Path(state), clock, fs
        self.path = self.state / NAME
        self.con = None
        self.why = None  # why the last open was refused, while the store is not open

    # --- opening ---

    def _at(self, name, root_fd):
        return {"dir_fd": root_fd} if root_fd is not None else {}, (name if root_fd is not None else self.state / name)

    def _exists(self, name, root_fd):
        kw, where = self._at(name, root_fd)
        try:
            os.stat(where, follow_symlinks=False, **kw)
            return True
        except FileNotFoundError:
            return False

    def check_root(self, root_fd, *names):
        """The state path still names the pinned directory, and each named entry reached through it is the one
        in that directory: else RootChanged."""
        try:
            ok = _same(os.stat(self.state), os.fstat(root_fd))
            for name in names if ok else ():
                ok = _same(os.stat(name, dir_fd=root_fd, follow_symlinks=False),
                           os.stat(self.state / name, follow_symlinks=False))
        except OSError:
            ok = False
        if not ok:
            raise RootChanged("the state root was moved or replaced around requests.db's open")

    def preflight(self, root_fd=None):
        """Classify requests.db read-only (through the pinned root, when there is one) before any writable open:
        "new", or "ours"; Refused otherwise, with nothing created, changed or removed."""
        sidecars = [n for n in SIDECARS if self._exists(n, root_fd)]
        kw, where = self._at(NAME, root_fd)
        try:
            fd = os.open(where, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), **kw)
        except FileNotFoundError:
            fd = None
        except OSError as err:
            raise Refused(f"requests.db can't be opened to classify it ({err.strerror or err})") from None
        header = b""
        try:
            if fd is not None:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise Refused("requests.db is not a regular file")
                header = os.read(fd, 100) if st.st_size else b""
        except OSError as err:
            raise Refused(f"requests.db can't be read to classify it ({err.strerror or err})") from None
        finally:
            if fd is not None:
                os.close(fd)
        if not header:
            if sidecars:
                raise Refused(f"{', '.join(sidecars)} without a non-empty requests.db: ambiguous evidence, never "
                              "treated as a new store")
            return "new"
        if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
            raise Refused("requests.db is not a SQLite database")
        if header[18:20] != b"\x02\x02":
            raise Refused("requests.db is not in WAL mode, so it is not this store's")
        if "requests.db-journal" in sidecars:
            raise Refused("requests.db has a rollback journal, so it is not this store's")
        return self._classify(root_fd)

    def _classify(self, root_fd):
        """The tables and meta rows of an existing WAL database, read without opening it for writing: on a private
        copy of requests.db and its -wal (so the committed WAL frames are read), in a directory made for it in the
        state root (through the pinned root, when there is one) and removed afterwards."""
        name = f".request-store-classify-{secrets.token_hex(8)}"
        kw, where = self._at(name, root_fd)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            os.mkdir(where, 0o700, **kw)
            tmp = os.open(where, os.O_RDONLY | os.O_DIRECTORY | nofollow, **kw)
        except OSError as err:
            raise Refused(f"requests.db can't be classified ({err.strerror or err})") from None
        try:
            for source in (NAME, "requests.db-wal"):
                skw, swhere = self._at(source, root_fd)
                try:
                    fd = os.open(swhere, os.O_RDONLY | nofollow, **skw)
                except FileNotFoundError:
                    continue
                with open(fd, "rb") as src, \
                        open(os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=tmp),
                             "wb") as dst:
                    shutil.copyfileobj(src, dst)
            con = sqlite3.connect(str(self.state / name / NAME), isolation_level=None)
            try:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                meta = dict(con.execute("SELECT name, value FROM meta").fetchall()) if "meta" in tables else {}
            finally:
                con.close()
        except (OSError, sqlite3.Error) as err:
            raise Refused(f"requests.db can't be read to classify it ({err})") from None
        finally:
            with contextlib.suppress(OSError):
                for entry in os.listdir(tmp):
                    os.unlink(entry, dir_fd=tmp)
            os.close(tmp)
            with contextlib.suppress(OSError):
                os.rmdir(where, **kw)
        if not tables:
            return "new"  # a WAL database with nothing committed: what a start that stopped before its schema left
        if tables != TABLES:
            raise Refused("requests.db was made by other code: its tables are not this store's")
        if meta.get("schema") != FORMAT or meta.get("writer_model") != WRITER_MODEL:
            raise Refused(f"requests.db is {meta.get('schema')!r}, not {FORMAT} with writer_model {WRITER_MODEL}")
        return "ours"

    def open(self, *, root_fd=None):
        """All or nothing: classified read-only first; then opened, its schema made or checked, and a start left
        with no outcome by the previous authority recorded `unknown`, in one transaction. On any failure the
        connection is closed and Refused (or RootChanged) raised, with `why` set. With `root_fd`, the pinned
        root is checked immediately before and after the pathname-based open."""
        self.why = None
        try:
            self._open(root_fd)
        except RootChanged as err:
            self.why = str(err)
            raise
        except (Refused, OSError) as err:
            self.why = str(err) if isinstance(err, Refused) else f"requests.db can't be opened ({err.strerror or err})"
            if root_fd is not None:
                try:
                    self.check_root(root_fd)  # a failure that a changed root caused is that change
                except RootChanged as changed:
                    self.why = str(changed)
                    raise
            raise Refused(self.why) from None

    def _open(self, root_fd):
        self.preflight(root_fd)
        if root_fd is not None:
            self.check_root(root_fd)
        try:
            con = sqlite3.connect(str(self.path), timeout=OPEN_WAIT, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as err:
            raise Refused(f"requests.db can't be opened ({err})") from None
        try:
            if root_fd is not None:
                self.check_root(root_fd, NAME)
            self._prepare(con)
        except sqlite3.Error as err:
            con.close()
            raise Refused(f"requests.db busy or unreadable ({err})") from None
        except BaseException:
            con.close()
            raise

    def _prepare(self, con):
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA locking_mode=EXCLUSIVE")
        journal = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        locking = con.execute("PRAGMA locking_mode").fetchone()[0]
        if str(locking).lower() != "exclusive" or str(journal).lower() != "wal":
            raise Refused(f"requests.db modes are {locking}/{journal}, not exclusive/wal")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA fullfsync=1")
        con.execute("PRAGMA checkpoint_fullfsync=1")
        self.con = con
        try:
            with self.tx():
                names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not names:
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            con.execute(statement)
                    con.executemany("INSERT INTO meta VALUES (?, ?)", (("schema", FORMAT),
                                                                      ("writer_model", WRITER_MODEL)))
                elif names != TABLES or self._meta("schema") != FORMAT or self._meta("writer_model") != WRITER_MODEL:
                    raise Refused("requests.db was made by other code: refused")
                # recovery: a start the previous authority recorded has no outcome, and stays unknown (D-4)
                con.execute("UPDATE requests SET state = 'unknown' WHERE state = 'started'")
            for name in (NAME, "requests.db-wal"):
                if self.fs is not None and (self.state / name).exists():
                    self.fs.durable(self.state / name)
        except BaseException:
            self.con = None
            raise

    def close(self):
        if self.con is not None:
            self.con.close()
            self.con = None

    @contextlib.contextmanager
    def tx(self):
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield self.con
            self.con.execute("COMMIT")
        except BaseException:
            self._rollback()
            raise

    @contextlib.contextmanager
    def read_tx(self):
        """A deferred BEGIN, never committed: no write lock, and no database or WAL file changed."""
        self.con.execute("BEGIN")
        try:
            yield self.con
        finally:
            self._rollback()

    def _rollback(self):
        try:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _meta(self, name):
        row = self.con.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    # --- reading ---

    def _row(self, request_id, rev):
        return self.con.execute("SELECT * FROM requests WHERE request_id = ? AND rev = ?",
                                (request_id, rev)).fetchone()

    def _claim_row(self, row, generation=None):
        generation = row["generation"] if generation is None else generation
        return self.con.execute("SELECT * FROM claims WHERE request_id = ? AND rev = ? AND generation = ?",
                                (row["request_id"], row["rev"], generation)).fetchone()

    def known(self, request_id, rev):
        """Whether the key is registered (False while the store is not open)."""
        return self.con is not None and self._row(request_id, rev) is not None

    def view(self, row):
        """A key's record as a reply carries it. The fence token is never shown here."""
        if row is None:
            return None
        k = (row["request_id"], row["rev"])
        acc = self.con.execute("SELECT * FROM acceptances WHERE request_id = ? AND rev = ?", k).fetchone()
        claim = self._claim_row(row) if row["generation"] else None
        recs = self.con.execute("SELECT * FROM reconciliations WHERE request_id = ? AND rev = ? ORDER BY generation",
                                k).fetchall()
        return {
            "request_id": row["request_id"], "rev": row["rev"], "digest": row["digest"], "state": row["state"],
            "verdict": {"status": row["verdict"], "reasons": json.loads(row["reasons"]), "authority": row["authority"]},
            "room": row["room"], "decision_maker": row["decision_maker"], "claimants": json.loads(row["claimants"]),
            "successors": json.loads(row["successors"]), "registrant": row["registrant"],
            "generation": row["generation"], "superseded_by": row["superseded_by"],
            "acceptance": None if acc is None else {"msgid": acc["msgid"], "account": acc["account"],
                                                    "authority_epoch": acc["epoch"]},
            "claim": None if claim is None else {"owner": claim["owner"], "generation": claim["generation"],
                                                 "authority_epoch": claim["epoch"],
                                                 "started": claim["started_at"] is not None,
                                                 "start_epoch": claim["start_epoch"]},
            "reconciliations": [{"generation": r["generation"], "outcome": r["outcome"], "agent": r["agent"],
                                 "evidence": r["evidence"]} for r in recs],
        }

    def lookup(self, r):
        """`lookup`: every revision of the request ID (or the one named), read-only."""
        with self.read_tx():
            q, args = "SELECT * FROM requests WHERE request_id = ?", [r["req_id"]]
            if r.get("rev") is not None:
                q, args = q + " AND rev = ?", args + [r["rev"]]
            keys = [self.view(row) for row in self.con.execute(q + " ORDER BY rev", args).fetchall()]
            refusals = [{"rev": x["rev"], "op": x["op"], "agent": x["agent"], "reason": x["reason"],
                         "detail": x["detail"]} for x in reversed(self.con.execute(
                            "SELECT * FROM refusals WHERE request_id = ? ORDER BY id DESC LIMIT ?",
                            (r["req_id"], LOOKUP_REFUSALS)).fetchall())]
        return "ok", {"keys": keys, "refusals": refusals}

    # --- transitions: each runs in one requests.db transaction ---

    def apply(self, r, *, epoch, post):
        """A mutating operation, in one transaction: (result, data). A refusal is recorded in that transaction,
        before the reply. `post(msgid)` is the authority's read-only lookup of a verified post in outbox.db:
        a row with `account` and `text`, or None."""
        with self.tx():
            return getattr(self, "_" + r["op"])(r, epoch, post)

    def _refuse(self, r, epoch, reason, detail, row=None, result="refused", request_id=None, rev=None):
        self.con.execute("INSERT INTO refusals (request_id, rev, op, agent, reason, detail, refused_at, epoch)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (request_id or r.get("req_id"), rev or r.get("rev"), r["op"], r.get("agent"), reason,
                          detail, self.clock(), epoch))
        return result, {"refusal": {"reason": reason, "detail": detail, "recorded": True}, "key": self.view(row)}

    def _waiting(self, request_id, rev):
        """Whether an earlier revision has started with no recorded outcome: then this one waits (section 1)."""
        return self.con.execute("SELECT 1 FROM requests WHERE request_id = ? AND rev < ? AND state IN (?, ?)",
                                (request_id, rev, *STARTED)).fetchone() is not None

    def _release_waiting(self, request_id):
        """Later revisions waiting on a start that now has an outcome stop waiting."""
        for w in self.con.execute("SELECT rev FROM requests WHERE request_id = ? AND state = 'waiting'",
                                  (request_id,)).fetchall():
            if not self._waiting(request_id, w["rev"]):
                self.con.execute("UPDATE requests SET state = 'open' WHERE request_id = ? AND rev = ?",
                                 (request_id, w["rev"]))

    def _register(self, r, epoch, post):
        try:
            req = request_v1.parse(r["line"])
            digest = request_v1.digest(req)
        except request_v1.RequestError as err:
            return "refused", {"detail": f"invalid request line ({err.code})"}
        request_id, rev = req.execution_key
        row = self._row(request_id, rev)
        if row is not None:  # a repeat (repost, relay or replay), or a conflicting reuse: never registered again
            if row["digest"] == digest:
                return "replay", {"key": self.view(row)}
            return self._refuse(r, epoch, "conflicting-reuse", f"{request_id} rev {rev} is registered with another"
                                " digest", row, "conflict", request_id, rev)
        newer = self.con.execute("SELECT max(rev) FROM requests WHERE request_id = ? AND rev > ?",
                                 (request_id, rev)).fetchone()[0]
        if newer is not None:
            return self._refuse(r, epoch, "newer-revision", f"revision {newer} is registered", None, "refused",
                                request_id, rev)
        earlier = self.con.execute("SELECT * FROM requests WHERE request_id = ? AND rev < ? ORDER BY rev DESC"
                                   " LIMIT 1", (request_id, rev)).fetchone()
        if earlier is not None and r["agent"] != earlier["registrant"] \
                and r["agent"] not in json.loads(earlier["successors"]):
            return self._refuse(r, epoch, "not-original-authority", f"revision {earlier['rev']} was registered by"
                                " another, who did not name this one", None, "refused", request_id, rev)
        verdict = r["verdict"]
        cur = self.con.execute(
            "INSERT INTO requests (request_id, rev, digest, line, verdict, reasons, authority, room, decision_maker,"
            " claimants, successors, registrant, state, registered_at, epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?) ON CONFLICT (request_id, rev) DO NOTHING",
            (request_id, rev, digest, r["line"], verdict["status"], json.dumps(verdict["reasons"]),
             verdict.get("authority"), r["room"], r["decision_maker"], json.dumps(sorted(set(r["claimants"]))),
             json.dumps(sorted(set(r.get("successors", [])))), r["agent"],
             "waiting" if self._waiting(request_id, rev) else "open", self.clock(), epoch))
        if not cur.rowcount:  # not reachable on the one serialized connection; the insert itself decides
            return self._register(r, epoch, post)
        # supersession: every earlier key that has not started is closed, its claim kept (section 1)
        self.con.execute("UPDATE requests SET state = 'superseded', superseded_by = ? WHERE request_id = ?"
                         " AND rev < ? AND state IN ('open', 'waiting', 'claimed')", (rev, request_id, rev))
        return "ok", {"key": self.view(self._row(request_id, rev))}

    def _unknown_key(self, r, epoch):
        return self._refuse(r, epoch, "unknown-key", f"{r['req_id']} rev {r['rev']} is not registered")

    def _unverified(self, r, epoch, row):
        return self._refuse(r, epoch, "verdict-not-verified", f"the recorded verdict is {row['verdict']}", row)

    def _accept(self, r, epoch, post):
        row = self._row(r["req_id"], r["rev"])
        if row is None:
            return self._unknown_key(r, epoch)
        acc = self.con.execute("SELECT * FROM acceptances WHERE request_id = ? AND rev = ?",
                               (r["req_id"], r["rev"])).fetchone()
        if acc is not None and acc["msgid"] == r["msgid"]:
            return "replay", {"key": self.view(row)}  # answered from the record, whatever outbox.db keeps since
        if row["verdict"] != request_v1.VERIFIED:
            return self._unverified(r, epoch, row)
        m = post(r["msgid"])
        if m is None:
            return self._refuse(r, epoch, "post-unverified", f"no verified post {r['msgid']} is indexed", row)
        if m["account"] != row["decision_maker"]:
            return self._refuse(r, epoch, "wrong-actor", f"the post is {m['account']}'s, not the decision-maker's",
                                row)
        if not names_request(m["text"], row["request_id"]):
            return self._refuse(r, epoch, "post-mismatch", "the post does not name the request ID", row)
        if acc is not None:
            return "replay", {"key": self.view(row)}  # already accepted: the first acceptance stands
        self.con.execute("INSERT INTO acceptances VALUES (?, ?, ?, ?, ?, ?)",
                         (row["request_id"], row["rev"], r["msgid"], m["account"], self.clock(), epoch))
        return "ok", {"key": self.view(row)}

    def _claim(self, r, epoch, post):
        row = self._row(r["req_id"], r["rev"])
        if row is None:
            return self._unknown_key(r, epoch)
        if row["verdict"] != request_v1.VERIFIED:
            return self._unverified(r, epoch, row)
        if r["agent"] not in json.loads(row["claimants"]):
            return self._refuse(r, epoch, "wrong-actor", f"{r['agent']} is not a permitted claimant", row)
        if row["state"] in LIVE:
            claim = self._claim_row(row)
            if claim["owner"] == r["agent"]:  # a lost reply: answered from the record
                return "replay", {"key": self.view(row), "fence": claim["fence"]}
            return self._refuse(r, epoch, "already-claimed", f"claimed by {claim['owner']} at generation"
                                f" {claim['generation']}", row, "conflict")
        if row["state"] == "waiting":
            return self._refuse(r, epoch, "waiting", "an earlier revision has started with no recorded outcome", row)
        if row["state"] in TERMINAL:
            return self._refuse(r, epoch, row["state"], f"the key is {row['state']}", row)
        generation, fence = row["generation"] + 1, secrets.token_hex(16)
        cur = self.con.execute("UPDATE requests SET state = 'claimed', generation = ? WHERE request_id = ? AND"
                               " rev = ? AND state = 'open' AND generation = ?",
                               (generation, row["request_id"], row["rev"], row["generation"]))
        if not cur.rowcount:  # the compare-and-set lost: not reachable on the one serialized connection
            return self._claim(r, epoch, post)
        self.con.execute("INSERT INTO claims (request_id, rev, generation, owner, fence, claimed_at, epoch)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (row["request_id"], row["rev"], generation, r["agent"], fence, self.clock(), epoch))
        return "ok", {"key": self.view(self._row(row["request_id"], row["rev"])), "fence": fence}

    def _start(self, r, epoch, post):
        row = self._row(r["req_id"], r["rev"])
        if row is None:
            return self._unknown_key(r, epoch)
        if row["verdict"] != request_v1.VERIFIED:
            return self._unverified(r, epoch, row)
        if row["state"] in TERMINAL:
            return self._refuse(r, epoch, row["state"], f"the key is {row['state']}; its claim is kept", row)
        if row["state"] not in LIVE:
            return self._refuse(r, epoch, "not-claimed", f"the key is {row['state']}", row)
        if r["generation"] != row["generation"]:  # a stale claimant is fenced by the key's own generation
            reason = "stale-generation" if r["generation"] < row["generation"] else "wrong-generation"
            return self._refuse(r, epoch, reason, f"the key is at generation {row['generation']}", row)
        claim = self._claim_row(row)
        if r["agent"] != claim["owner"]:
            return self._refuse(r, epoch, "wrong-actor", f"only the claimant {claim['owner']} may start it", row)
        if not secrets.compare_digest(r["fence"], claim["fence"]):
            return self._refuse(r, epoch, "wrong-fence", "the fence token is not the claim's", row)
        if row["state"] in STARTED:  # a lost reply, or a start resumed after a restart: from the record
            return "replay", {"key": self.view(row)}
        self.con.execute("UPDATE claims SET started_at = ?, start_epoch = ? WHERE request_id = ? AND rev = ? AND"
                         " generation = ?", (self.clock(), epoch, row["request_id"], row["rev"], row["generation"]))
        self.con.execute("UPDATE requests SET state = 'started' WHERE request_id = ? AND rev = ?",
                         (row["request_id"], row["rev"]))
        return "ok", {"key": self.view(self._row(row["request_id"], row["rev"]))}

    def _reconcile(self, r, epoch, post):
        row = self._row(r["req_id"], r["rev"])
        if row is None:
            return self._unknown_key(r, epoch)
        claim = self._claim_row(row, r["generation"]) if r["generation"] <= row["generation"] else None
        if r["agent"] != row["decision_maker"] and (claim is None or r["agent"] != claim["owner"]):
            return self._refuse(r, epoch, "wrong-actor", "only the claimant of that generation or the"
                                " decision-maker may reconcile it", row)
        done = self.con.execute("SELECT * FROM reconciliations WHERE request_id = ? AND rev = ? AND generation = ?",
                                (row["request_id"], row["rev"], r["generation"])).fetchone()
        if done is not None:  # a retry, or a delayed copy: its recorded outcome, and nothing else changes
            if done["outcome"] == r["outcome"]:
                return "replay", {"key": self.view(row), "outcome": done["outcome"]}
            return self._refuse(r, epoch, "already-reconciled", f"generation {r['generation']} was reconciled"
                                f" {done['outcome']}", row, "conflict")
        if r["generation"] != row["generation"]:
            reason = "stale-generation" if r["generation"] < row["generation"] else "wrong-generation"
            return self._refuse(r, epoch, reason, f"the key is at generation {row['generation']}", row)
        if row["state"] not in STARTED:
            return self._refuse(r, epoch, "not-started", f"the key is {row['state']}: nothing to reconcile", row)
        self.con.execute("INSERT INTO reconciliations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (row["request_id"], row["rev"], r["generation"], r["outcome"], r["agent"], r["evidence"],
                          self.clock(), epoch))
        if r["outcome"] == "effect":
            state, by = "reconciled", None
        else:  # no effect: claimable again, unless a later revision waits on it, which then supersedes it
            later = self.con.execute("SELECT min(rev) FROM requests WHERE request_id = ? AND rev > ? AND state ="
                                     " 'waiting'", (row["request_id"], row["rev"])).fetchone()[0]
            state, by = ("superseded", later) if later is not None else ("open", None)
        self.con.execute("UPDATE requests SET state = ?, superseded_by = coalesce(?, superseded_by) WHERE"
                         " request_id = ? AND rev = ?", (state, by, row["request_id"], row["rev"]))
        self._release_waiting(row["request_id"])
        return "ok", {"key": self.view(self._row(row["request_id"], row["rev"])), "outcome": r["outcome"]}
