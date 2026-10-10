"""The outbox's one durable authority (#19; docs/chat_outbox_state.md revision 8).

One SQLite database holds every post and acknowledgement the transport owes:
its parts, every attempt to send one, the verified messages the bridge indexed,
and how far each channel's record is known complete. Only the bridge opens it,
through one connection on its authority thread. Every other caller (pchat post,
pchat ack, an agent run's notice) asks that thread over a local socket to apply
its transitions, and still sends its own bytes to the chat server. A caller that
gets no committed answer queues durably instead, in one immutable fallback file
per call under outbox.d/, which the bridge imports without taking any lock a
client could hold.

Standard library only; works on Python 3.10 (transactions use explicit BEGIN
IMMEDIATE / COMMIT on an isolation_level=None connection). The section numbers
in comments are the design note's.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import secrets
import selectors
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

# --- constants (section 1) ----------------------------------------------------

A = 5.0                  # allowed difference between a writer's clock and the server's message time
PART_WAIT = 30.0         # absolute deadline for the server to confirm one part
DRAIN_WAIT = 30.0        # then, how much longer the writer reads for the late in-order reply
LOGIN_WAIT = 30.0        # connecting, capabilities, SASL and the welcome
CHANNEL_WAIT = 15.0      # creating or joining a channel and seeing the bridge in it, after a 403
UNDECIDED_MAX = 86400.0  # a part undecided this long after its attempt's write is dead-lettered
GC_MARGIN = 3600.0       # extra age before evidence may be collected
LOCK_WAIT = 5.0          # absolute deadline for the bridge to acquire outbox.lock
OPEN_WAIT = 5.0          # SQLite's busy timeout, which matters only at the authority's start
FRAME_MAX = 1 << 20      # largest request frame
REPLY_MAX = 64 << 10     # largest reply frame
REQ_WAIT = 2.0           # accept to complete request frame
RESP_WAIT = 2.0          # reply ready to its last byte written
CONN_MAX = 32            # open client connections
QUEUE_MAX = 256          # complete client requests waiting to run
IPC_WAIT = 5.0           # a client's absolute deadline for one request
AUTH_WAIT = 5.0          # a bridge thread's deadline for its in-process request to begin
IMPORT_MAX = 256         # fallback files imported per flush, oldest name first
LINES_MAX = 1000         # legacy lines one import request reads from one claim file
COLLECT_MAX = 1000       # rows one collection request deletes

PROTOCOL = "outbox-authority/1"
DB_FORMAT = "outbox-db/1"
FALLBACK_FORMAT = "outbox/2"
WRITER_MODEL = "bridge-exclusive/1"
SOCKET_LIMIT = 103 if sys.platform == "darwin" else 107  # bytes of an AF_UNIX path
RESULTS = ("ok", "replay", "conflict", "refused", "busy", "error")
CLIENT_OPS = ("create", "queue", "attempt", "outcome", "retire", "void", "handoff", "done", "query", "status")
OUTCOMES = {"confirmed": "confirmed", "refused": "refused", "rejected": "rejected", "closed": "ended"}


CANCELLED = object()  # an in-process request that never began (or no authority runs): nothing committed


class Unavailable(Exception):
    """The authority cannot run: its lock is held, its database is refused or busy, or storage failed."""


class Conflict(Exception):
    """A request's guard failed. Nothing changed."""


def now_iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def parse_iso(s):
    """Seconds since the epoch of an ISO-8601 server time, or None."""
    import datetime
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def jdump(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class Paths:
    def __init__(self, state):
        state = Path(state)
        self.state = state
        self.db = state / "outbox.db"
        self.auth_lock = state / "outbox.authority.lock"
        self.flush_lock = state / "outbox.flush.lock"
        self.outbox_lock = state / "outbox.lock"
        self.sock = state / "outbox.sock"
        self.status = state / "outbox-status.json"
        self.fallback = state / "outbox.d"
        self.staging = state / "outbox.d" / "tmp"
        self.legacy = state / "outbox.jsonl"
        self.dead_letters = state / "dead-letters.jsonl"


# --- durable files (section 2.1) -------------------------------------------------

class FS:
    """Every file operation the durability proofs rely on. "fsync" is the
    platform's full flush; a durable sync is the file, then its directory."""

    def fsync_fd(self, fd):
        if sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC"):
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                return
            except OSError:
                pass
        os.fsync(fd)

    def _sync(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            self.fsync_fd(fd)
        finally:
            os.close(fd)

    def sync_file(self, path):
        self._sync(path)

    def sync_dir(self, path):
        self._sync(path)

    def durable(self, path):
        path = Path(path)
        self.sync_file(path)
        self.sync_dir(path.parent)

    def create_excl(self, path, data):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            self.fsync_fd(fd)
        finally:
            os.close(fd)

    def append(self, path, data):
        with open(path, "ab") as f:
            f.write(data)
            f.flush()

    def link(self, src, dst):
        os.link(src, dst)

    def rename(self, src, dst):
        os.rename(src, dst)

    def replace(self, src, dst):
        os.replace(src, dst)

    def unlink(self, path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def listdir(self, path):
        try:
            return os.listdir(path)
        except FileNotFoundError:
            return []

    def read(self, path):
        return Path(path).read_bytes()

    def exists(self, path):
        return os.path.exists(path)

    def nlink(self, path):
        return os.stat(path).st_nlink

    def size(self, path):
        return os.stat(path).st_size

    def ino(self, path):
        st = os.stat(path)
        return (st.st_dev, st.st_ino)

    def mkdir(self, path):
        """Create `path` if missing; either way its entry in the parent is made durable."""
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        self.sync_dir(path)
        self.sync_dir(Path(path).parent)


def durable_append(fs, path, line):
    """Append one record line and make it durable, file then directory."""
    path = Path(path)
    data = line.encode() if isinstance(line, str) else line
    if fs.exists(path) and fs.size(path):
        if not fs.read(path).endswith(b"\n"):
            data = b"\n" + data  # a torn last line becomes an unreadable line, which readers skip
    fs.append(path, data if data.endswith(b"\n") else data + b"\n")
    fs.durable(path)


def durable_replace(fs, path, data):
    """Write a temporary file, fsync it, rename it into place, fsync the directory."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fs.create_excl(tmp, data if isinstance(data, bytes) else data.encode())
    fs.replace(tmp, path)
    fs.sync_dir(path.parent)


def sink_append(fs, path, record, key_field, key):
    """The dead-letter sink protocol (section 3.6), under outbox.lock already held:
    append unless a parseable line with the same key is present; then, always,
    a durable sync of the file and its directory, whoever created the file."""
    path = Path(path)
    present = False
    if fs.exists(path):
        for raw in fs.read(path).splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get(key_field) == key:
                present = True
                break
    if not present:
        data = (json.dumps(record) + "\n").encode()
        if fs.exists(path) and fs.size(path) and not fs.read(path).endswith(b"\n"):
            data = b"\n" + data
        fs.append(path, data)
    fs.durable(path)
    return not present


@contextlib.contextmanager
def flock(path, *, wait=None, clock=time.monotonic, sleep=time.sleep):
    """A flock on `path`, taken without blocking. With `wait`, retried until that
    absolute deadline; yields True if held, False if not. Never stolen: a
    holder that never releases is simply waited out to the deadline."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        deadline = None if wait is None else clock() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except BlockingIOError:
                if deadline is None or clock() >= deadline:
                    held = False
                    break
                sleep(min(0.05, max(0.0, deadline - clock())))
        try:
            yield held
        finally:
            if held:
                fcntl.flock(f, fcntl.LOCK_UN)


# --- writer identity and the host probes (section 5.4) ---------------------------

def _boot_id():
    """The boot id (a read-only host interface), or None when it cannot be read."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        pass
    try:
        r = subprocess.run(["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # an unreadable probe proves nothing
        return None


def _process_start(pid):
    """A process's start time (the process table), or None when it cannot be read."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]  # field 22: start time in clock ticks since boot
    except (OSError, IndexError):
        pass
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # an unreadable probe proves nothing
        return None


def _tcp_ports(pid):
    """The local ports of `pid`'s TCP sockets, or None when that can't be read."""
    proc = Path(f"/proc/{pid}")
    if proc.exists():
        try:
            inodes = set()
            for fd in (proc / "fd").iterdir():
                target = os.readlink(fd)
                if target.startswith("socket:["):
                    inodes.add(target[8:-1])
            ports = set()
            for table in ("tcp", "tcp6"):
                for line in (proc / "net" / table).read_text().splitlines()[1:]:
                    cols = line.split()
                    if cols[9] in inodes:
                        ports.add(int(cols[1].rsplit(":", 1)[1], 16))
            return ports
        except (OSError, IndexError, ValueError):
            return None
    try:
        r = subprocess.run(["lsof", "-a", "-p", str(pid), "-iTCP", "-Fn", "-P", "-n"],
                           capture_output=True, text=True, timeout=10)
    except Exception:  # an unreadable probe proves nothing
        return None
    if r.returncode not in (0, 1):
        return None
    ports = set()
    for line in r.stdout.splitlines():
        if line.startswith("n"):
            local = line[1:].split("->")[0]
            try:
                ports.add(int(local.rsplit(":", 1)[1]))
            except (IndexError, ValueError):
                continue
    return ports


class HostProbes:
    """Who a writer is, and whether it is gone or its connection closed. A probe
    that cannot be read proves nothing (None), and a paused writer still holds
    its socket, so neither proof ever applies to it."""

    def __init__(self):
        self._boot = None
        self._own = None  # (pid, start time) of this process, read once

    def boot(self):
        if self._boot is None:
            self._boot = _boot_id() or "unknown"
        return self._boot

    def identity(self, conn_id=None):
        pid = os.getpid()
        if self._own is None or self._own[0] != pid:
            self._own = (pid, _process_start(pid))
        return {"boot": self.boot(), "pid": pid, "start": self._own[1], "conn": conn_id}

    def gone(self, writer):
        if not writer:
            return None
        if writer.get("boot") and writer["boot"] != self.boot() and self.boot() != "unknown":
            return True
        pid = writer.get("pid")
        if not isinstance(pid, int):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        start = _process_start(pid)
        if start is None:
            return None
        return start != writer.get("start")

    def conn_closed(self, writer):
        if not writer or not writer.get("conn"):
            return None
        if self.gone(writer) is not False:
            return None
        ports = _tcp_ports(writer["pid"])
        if ports is None:
            return None
        try:
            port = int(writer["conn"].split(">")[0].rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None
        return port not in ports  # a reused port can only make a closed connection look open


class FakeProbes:
    """The probes the tests drive, over one invented process table that every
    view shares: nothing is gone or closed until a test says so. Each view
    speaks for one invented process (`pid`)."""

    def __init__(self, boot="test-boot", pid=None, world=None):
        self._boot = boot
        self.world = world if world is not None else {"gone": set(), "closed": set(), "unreadable": False,
                                                      "starts": {}}
        self.pid = pid if pid is not None else os.getpid()

    def view(self, pid):
        """The same process table, as another invented process."""
        return FakeProbes(self._boot, pid, self.world)

    @property
    def gone_pids(self):
        return self.world["gone"]

    @property
    def closed_conns(self):
        return self.world["closed"]

    def boot(self):
        return self._boot

    def identity(self, conn_id=None):
        return {"boot": self._boot, "pid": self.pid, "start": self.world["starts"].get(self.pid, "start-1"),
                "conn": conn_id}

    def gone(self, writer):
        if self.world["unreadable"] or not writer:
            return None
        if writer.get("boot") != self._boot or writer.get("pid") in self.world["gone"]:
            return True
        return writer.get("start") != self.world["starts"].get(writer.get("pid"), "start-1")

    def conn_closed(self, writer):
        if self.world["unreadable"] or not writer or not writer.get("conn"):
            return None
        if self.gone(writer):
            return None
        return writer["conn"] in self.world["closed"]


PROBES = HostProbes()  # this process's probes; the tests replace them with FakeProbes
FS_DEFAULT = FS()


def same_writer(a, b):
    """The same flow: boot id, pid and process start time (the connection may differ per attempt)."""
    if not a or not b:
        return False
    return (a.get("boot"), a.get("pid"), a.get("start")) == (b.get("boot"), b.get("pid"), b.get("start"))


# --- fallback files (sections 2.1 and 6.4) ----------------------------------------

def fallback_name(t, entry_id):
    return f"{int(t * 1e9):020d}-{entry_id}-{secrets.token_hex(4)}.json"


def is_published_name(name):
    return name.endswith(".json") and not name.startswith(".") and "-" in name


def publish_fallback(fs, paths, row, *, clock=time.time):
    """Publish one complete, immutable fallback file for row["id"]: exclusive
    creation under tmp/, the whole content synced, a link() that never replaces
    a file, then outbox.d/ and the state directory synced. Returns its name.
    Any OSError means nothing was reported queued (the caller fails as today)."""
    fs.mkdir(paths.fallback)
    fs.mkdir(paths.staging)
    writer = row.get("writer") or {}
    data = (json.dumps({"v": FALLBACK_FORMAT, **row}, sort_keys=True) + "\n").encode()
    tag = f"{writer.get('boot', 'x')}.{writer.get('pid', os.getpid())}.{writer.get('start', 'x')}"
    tmp = paths.staging / (tag.replace("/", "_").replace(" ", "_") + f".{secrets.token_hex(6)}.tmp")
    fs.create_excl(tmp, data)
    for _ in range(8):
        name = fallback_name(clock(), row["id"])
        try:
            fs.link(tmp, paths.fallback / name)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(errno.EEXIST, "no free fallback name")
    fs.sync_dir(paths.fallback)
    fs.sync_dir(paths.state)
    fs.unlink(tmp)  # cleanup only
    return name


# --- the database (sections 2.1 and 2.2) -------------------------------------------

SCHEMA = """
CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE accounts (account TEXT PRIMARY KEY, designated_at REAL, suspended_at REAL, suspended_why TEXT,
                       suspension_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE alerts (alert_key TEXT PRIMARY KEY, kind TEXT, ref TEXT, text TEXT, raised_at REAL, exported_at REAL);
CREATE TABLE entries (id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT, account TEXT NOT NULL,
                      channel TEXT NOT NULL, text TEXT, cont TEXT, reply_to TEXT, ack TEXT, written TEXT,
                      owner_kind TEXT NOT NULL, owner_writer TEXT, state TEXT NOT NULL, abandon_epoch INTEGER,
                      terminal_reason TEXT, export_version INTEGER NOT NULL DEFAULT 0,
                      dead_letter_exported_version INTEGER NOT NULL DEFAULT 0, occ_file TEXT, occ_ordinal INTEGER,
                      occ_sha TEXT, handoff_ref TEXT UNIQUE, created_at REAL, delayed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE parts (entry_id TEXT NOT NULL, n INTEGER NOT NULL, kind TEXT NOT NULL, lines TEXT NOT NULL,
                    text TEXT NOT NULL, multi_line INTEGER NOT NULL, state TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0, msgid TEXT, PRIMARY KEY (entry_id, n));
CREATE TABLE attempts (id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL, n INTEGER NOT NULL,
                       generation INTEGER NOT NULL, account TEXT NOT NULL, channel TEXT NOT NULL, text TEXT,
                       multi_line INTEGER NOT NULL, is_ack INTEGER NOT NULL DEFAULT 0, writer TEXT NOT NULL,
                       conn_id TEXT, a1_nonce TEXT NOT NULL, state TEXT NOT NULL, written_at REAL NOT NULL,
                       final_at REAL, ended_at REAL, end_kind TEXT, detail TEXT, a1_epoch INTEGER,
                       UNIQUE (entry_id, n, generation));
CREATE INDEX attempts_key ON attempts (account, channel, text);
CREATE INDEX attempts_state ON attempts (state);
CREATE TABLE attributions (msgid TEXT PRIMARY KEY, attempt_id INTEGER UNIQUE, made_at REAL);
CREATE TABLE messages (msgid TEXT PRIMARY KEY, channel TEXT NOT NULL, account TEXT NOT NULL, text TEXT NOT NULL,
                       at REAL NOT NULL);
CREATE INDEX messages_key ON messages (account, channel, at);
CREATE TABLE coverage (channel TEXT PRIMARY KEY, since REAL, through REAL, mark TEXT);
CREATE TABLE imports (name TEXT PRIMARY KEY, state TEXT NOT NULL, lines INTEGER NOT NULL DEFAULT 0,
                      offset INTEGER NOT NULL DEFAULT 0, sha256 TEXT, epoch INTEGER, first_seen REAL,
                      last_import REAL);
CREATE TABLE held (id INTEGER PRIMARY KEY, file TEXT NOT NULL, ordinal INTEGER, raw TEXT, why TEXT,
                   UNIQUE (file, ordinal));
"""

UNRESOLVED = ("writing", "ended", "dead")


def window(att):
    """(start, end) of an attempt's window (section 7.1); end None is open:
    writing, or ended or dead without finality."""
    start = att["written_at"] - A
    if att["state"] == "writing" or att["final_at"] is None:
        return start, None
    return start, att["final_at"] + A


def overlaps(w1, w2):
    a1, b1 = w1
    a2, b2 = w2
    return (b1 is None or a2 <= b1) and (b2 is None or a1 <= b2)


def inside(t, w):
    return w[0] <= t and (w[1] is None or t <= w[1])


def max_matching(items, candidates, allowed):
    """Size of a maximum matching of `items` to distinct `candidates`, where
    allowed(item, candidate) says whether the pair may match (Kuhn's algorithm),
    and the matching itself as {item index: candidate index}."""
    match_c = {}

    def augment(i, seen):
        for j, c in enumerate(candidates):
            if j in seen or not allowed(items[i], c):
                continue
            seen.add(j)
            if j not in match_c or augment(match_c[j], seen):
                match_c[j] = i
                return True
        return False

    size = sum(1 for i in range(len(items)) if augment(i, set()))
    return size, {i: j for j, i in match_c.items()}


class Store:
    """The one connection to outbox.db. Every method that changes it runs in
    one BEGIN IMMEDIATE transaction, reading the current rows (I-8)."""

    def __init__(self, paths, *, clock=time.time, fs=None):
        self.paths, self.clock, self.fs = paths, clock, fs or FS()
        self.con = None
        self.epoch = 0

    # --- opening (section 2.4.1) ---

    def open(self):
        try:
            con = sqlite3.connect(str(self.paths.db), timeout=OPEN_WAIT, isolation_level=None,
                                  check_same_thread=False)
        except sqlite3.Error as err:
            raise Unavailable(f"database: {err}") from None
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA locking_mode=EXCLUSIVE")  # before anything reads the database
            journal = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            locking = con.execute("PRAGMA locking_mode").fetchone()[0]
            if str(locking).lower() != "exclusive" or str(journal).lower() != "wal":
                raise Unavailable(f"database modes are {locking}/{journal}, not exclusive/wal")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA fullfsync=1")
            con.execute("PRAGMA checkpoint_fullfsync=1")
            self.con = con
            with self.tx():
                names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "meta" not in names:
                    if names:
                        raise Unavailable("database made by other code: refused")
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            con.execute(statement)
                    for name, value in (("schema", DB_FORMAT), ("writer_model", WRITER_MODEL),
                                        ("cutover", now_iso(self.clock())), ("import_epoch", "0"),
                                        ("authority_epoch", "0")):
                        con.execute("INSERT INTO meta VALUES (?, ?)", (name, value))
                if self.meta("writer_model") != WRITER_MODEL or self.meta("schema") != DB_FORMAT:
                    raise Unavailable("database without writer_model bridge-exclusive/1: refused")
                self.epoch = int(self.meta("authority_epoch") or 0) + 1
                self.set_meta("authority_epoch", self.epoch)
        except sqlite3.Error as err:  # busy, locked by another opener, not a database, unreadable
            con.close()
            self.con = None
            raise Unavailable(f"database busy or unreadable: {err}") from None
        except BaseException:
            con.close()
            self.con = None
            raise
        for name in ("outbox.db", "outbox.db-wal"):  # the database's own files are durable too
            if self.fs.exists(self.paths.state / name):
                self.fs.durable(self.paths.state / name)

    def close(self):
        if self.con is not None:
            self.con.close()
            self.con = None

    @contextlib.contextmanager
    def tx(self):
        self.con.execute("BEGIN IMMEDIATE")
        try:
            yield self.con
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")

    def meta(self, name):
        row = self.con.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_meta(self, name, value):
        self.con.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                         (name, str(value)))

    # --- reading ---

    def entry(self, entry_id):
        return self.con.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()

    def parts(self, entry_id):
        return self.con.execute("SELECT * FROM parts WHERE entry_id = ? ORDER BY n", (entry_id,)).fetchall()

    def attempts(self, entry_id):
        return self.con.execute("SELECT * FROM attempts WHERE entry_id = ? ORDER BY n, generation",
                                (entry_id,)).fetchall()

    def attempt_row(self, entry_id, n, generation):
        return self.con.execute("SELECT * FROM attempts WHERE entry_id = ? AND n = ? AND generation = ?",
                                (entry_id, n, generation)).fetchone()

    def entry_view(self, entry_id, writer=None):
        e = self.entry(entry_id)
        if e is None:
            return None
        view = {k: e[k] for k in e.keys()}
        view["owner_writer"] = json.loads(e["owner_writer"]) if e["owner_writer"] else None
        view["parts"] = [{"n": p["n"], "kind": p["kind"], "lines": json.loads(p["lines"]), "text": p["text"],
                          "state": p["state"], "generation": p["generation"], "msgid": p["msgid"]}
                         for p in self.parts(entry_id)]
        atts = []
        for a in self.attempts(entry_id):
            w = json.loads(a["writer"])
            if writer is None or same_writer(w, writer):
                atts.append({"n": a["n"], "generation": a["generation"], "state": a["state"],
                             "a1_nonce": a["a1_nonce"] if writer is not None else None,
                             "written_at": a["written_at"], "final_at": a["final_at"], "end_kind": a["end_kind"]})
        view["attempts"] = atts
        return view

    # --- export versioning (section 3) ---

    def _touch_terminal(self, entry_id):
        self.con.execute("UPDATE entries SET export_version = export_version + 1 WHERE id = ? AND state = 'terminal'",
                         (entry_id,))

    # --- entry transitions (section 3.1) ---

    def _insert_entry(self, entry_id, row, *, owner_kind, writer, origin, occ=None, created=None):
        self.con.execute(
            "INSERT INTO entries (id, kind, origin, account, channel, text, cont, reply_to, ack, written, owner_kind,"
            " owner_writer, state, occ_file, occ_ordinal, occ_sha, created_at, delayed)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
            (entry_id, row.get("kind") or ("ack" if row.get("ack") else "post"), origin, row["as"],
             row["channel"], row.get("text"), row.get("cont") or "", row.get("reply_to"), row.get("ack"),
             row.get("at"), owner_kind, jdump(writer) if writer else None,
             *(occ or (None, None, None)), created if created is not None else self.clock(),
             1 if owner_kind == "outbox" else 0))

    def create(self, entry_id, row, parts, writer, origin):
        """E1: an entry and its fixed parts, owned by the direct writer."""
        e = self.entry(entry_id)
        if e is not None:
            same = (e["owner_kind"] == "direct" and e["owner_writer"]
                    and same_writer(json.loads(e["owner_writer"]), writer)
                    and [(p["kind"], json.loads(p["lines"])) for p in self.parts(entry_id)]
                    == [(p["kind"], [list(x) for x in p["lines"]]) for p in parts])
            if same or e["owner_kind"] == "outbox":
                return "replay"
            raise Conflict("entry exists")
        self._insert_entry(entry_id, row, owner_kind="direct", writer=writer, origin=origin)
        self._insert_parts(entry_id, parts)
        return "ok"

    def _insert_parts(self, entry_id, parts):
        for n, p in enumerate(parts):
            lines = [list(x) for x in p["lines"]]
            self.con.execute("INSERT INTO parts (entry_id, n, kind, lines, text, multi_line, state) "
                             "VALUES (?, ?, ?, ?, ?, ?, 'unsent')",
                             (entry_id, n, p["kind"], jdump(lines), p["text"], 1 if len(lines) > 1 else 0))

    def queue(self, entry_id, row, writer, origin):
        """Q0: the whole call queued before any byte. If X already exists (an E1
        whose reply was lost had landed), it is handed off instead (E3)."""
        e = self.entry(entry_id)
        if e is None:
            self._insert_entry(entry_id, row, owner_kind="outbox", writer=None, origin=origin)
            return "ok"
        if e["state"] == "open" and e["owner_kind"] == "direct":
            if not same_writer(json.loads(e["owner_writer"] or "null"), writer):
                raise Conflict("not the owner")
            self.con.execute("UPDATE entries SET owner_kind = 'outbox' WHERE id = ?", (entry_id,))
        return "replay"

    def handoff(self, entry_id, writer, attestation=None):
        """E3, with at most one attestation applied under its own guard."""
        e = self.entry(entry_id)
        if e is None:
            raise Conflict("no such entry")
        if e["owner_kind"] == "outbox" or e["state"] != "open":
            if attestation:
                self.apply_attestation(attestation)
            return "replay"
        if not same_writer(json.loads(e["owner_writer"] or "null"), writer):
            raise Conflict("not the owner")
        self.con.execute("UPDATE entries SET owner_kind = 'outbox' WHERE id = ?", (entry_id,))
        if attestation:
            self.apply_attestation(attestation)
        return "ok"

    def done(self, entry_id, writer=None, bridge=False):
        """E5: every part confirmed."""
        e = self.entry(entry_id)
        if e is None:
            raise Conflict("no such entry")
        if e["state"] == "done":
            return "replay"
        if e["state"] != "open":
            raise Conflict("not open")
        if not bridge and not (e["owner_kind"] == "direct"
                               and same_writer(json.loads(e["owner_writer"] or "null"), writer)):
            raise Conflict("not the owner")
        parts = self.parts(entry_id)
        if not parts or any(p["state"] != "confirmed" for p in parts):
            raise Conflict("parts not all confirmed")
        self.con.execute("UPDATE entries SET state = 'done' WHERE id = ?", (entry_id,))
        return "ok"

    # --- attempts (section 3.3) ---

    def _owns(self, e, writer, bridge):
        if e["owner_kind"] == "direct":
            return same_writer(json.loads(e["owner_writer"] or "null"), writer)
        return bridge

    def attempt(self, entry_id, n, generation, nonce, writer, *, bridge=False, epoch=None):
        """A1: a writing attempt, generation + 1, committed before any byte."""
        g1 = generation + 1
        existing = self.attempt_row(entry_id, n, g1)
        if existing is not None:
            if existing["a1_nonce"] == nonce and same_writer(json.loads(existing["writer"]), writer):
                return "replay", self._attempt_view(existing)
            raise Conflict("generation taken")
        e = self.entry(entry_id)
        if e is None or e["state"] != "open" or not self._owns(e, writer, bridge):
            raise Conflict("entry not open, or not the owner")
        part = self.con.execute("SELECT * FROM parts WHERE entry_id = ? AND n = ?", (entry_id, n)).fetchone()
        if part is None or part["state"] != "unsent" or part["generation"] != generation:
            raise Conflict("part not unsent at that generation")
        if self.con.execute("SELECT 1 FROM attempts WHERE entry_id = ? AND n = ? AND state IN ('writing', 'ended')",
                            (entry_id, n)).fetchone():
            raise Conflict("an attempt is writing or ended")
        if self.con.execute("SELECT 1 FROM parts WHERE entry_id = ? AND n < ? AND state != 'confirmed'",
                            (entry_id, n)).fetchone():
            raise Conflict("an earlier part is not confirmed")
        now = self.clock()
        self.con.execute(
            "INSERT INTO attempts (entry_id, n, generation, account, channel, text, multi_line, is_ack, writer,"
            " conn_id, a1_nonce, state, written_at, a1_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'writing', ?, ?)",
            (entry_id, n, g1, e["account"], e["channel"].lower(), None if part["kind"] == "ack" else part["text"],
             part["multi_line"], 1 if part["kind"] == "ack" else 0, jdump(writer), writer.get("conn"), nonce, now,
             epoch))
        self.con.execute("UPDATE parts SET state = 'inflight', generation = ? WHERE entry_id = ? AND n = ?",
                         (g1, entry_id, n))
        return "ok", self._attempt_view(self.attempt_row(entry_id, n, g1))

    @staticmethod
    def _attempt_view(a):
        return {"entry_id": a["entry_id"], "n": a["n"], "generation": a["generation"], "state": a["state"],
                "written_at": a["written_at"], "final_at": a["final_at"]}

    def outcome(self, entry_id, n, generation, nonce, writer, outcome, final_at=None, detail=None):
        """A2 (confirmed), A3 (refused, with E6), A4 (rejected) or A5 (closed, ended)."""
        a = self.attempt_row(entry_id, n, generation)
        if a is None or a["a1_nonce"] != nonce or not same_writer(json.loads(a["writer"]), writer):
            raise Conflict("no such attempt of this writer")
        target = OUTCOMES.get(outcome)
        if target is None:
            raise Conflict("unknown outcome")
        if a["state"] != "writing":
            if a["state"] == target and (a["final_at"] == final_at):
                return "replay"
            raise Conflict(f"attempt is {a['state']}")
        now = self.clock()
        e = self.entry(entry_id)
        if target == "confirmed":
            self.con.execute("UPDATE attempts SET state = 'confirmed', final_at = ?, detail = ? WHERE id = ?",
                             (final_at, detail, a["id"]))
            self.con.execute("UPDATE parts SET state = 'confirmed' WHERE entry_id = ? AND n = ?", (entry_id, n))
        elif target == "refused":
            self.con.execute("UPDATE attempts SET state = 'refused', final_at = ?, detail = ? WHERE id = ?",
                             (final_at, detail, a["id"]))
            self.con.execute("UPDATE parts SET state = 'refused' WHERE entry_id = ? AND n = ?", (entry_id, n))
            if e["state"] == "open":  # E6, in A3's transaction
                self.con.execute("UPDATE entries SET state = 'terminal', terminal_reason = ?,"
                                 " export_version = 1 WHERE id = ?", (f"refused by the server: {detail or ''}",
                                                                      entry_id))
        elif target == "rejected":
            self.con.execute("UPDATE attempts SET state = 'rejected', final_at = ?, detail = ? WHERE id = ?",
                             (final_at, detail, a["id"]))
            self.con.execute("UPDATE parts SET state = 'unsent' WHERE entry_id = ? AND n = ?", (entry_id, n))
        else:  # A5: the connection was closed first
            self.con.execute("UPDATE attempts SET state = 'ended', end_kind = 'closed', ended_at = ?, final_at = ?,"
                             " detail = ? WHERE id = ?", (now, final_at, detail, a["id"]))
            self.con.execute("UPDATE parts SET state = 'uncertain' WHERE entry_id = ? AND n = ?", (entry_id, n))
        if e["state"] == "terminal":
            self._touch_terminal(entry_id)  # F6: a late outcome on a terminal entry exports a new version
        return "ok"

    def retire(self, entry_id, n, writer=None, *, bridge=False):
        """A11: an ack's ended attempt is retired, and its pseudo-part is unsent again."""
        e = self.entry(entry_id)
        if e is None or not self._owns(e, writer, bridge):
            raise Conflict("not the owner")
        a = self.con.execute("SELECT * FROM attempts WHERE entry_id = ? AND n = ? AND is_ack = 1 AND state = 'ended'",
                             (entry_id, n)).fetchone()
        if a is None:
            if self.con.execute("SELECT 1 FROM attempts WHERE entry_id = ? AND n = ? AND state = 'retired'",
                                (entry_id, n)).fetchone():
                return "replay"
            raise Conflict("no ended ack attempt")
        self.con.execute("UPDATE attempts SET state = 'retired' WHERE id = ?", (a["id"],))
        self.con.execute("UPDATE parts SET state = 'unsent' WHERE entry_id = ? AND n = ?", (entry_id, n))
        return "ok"

    def apply_attestation(self, att):
        """A12 (a void: the writer sent no byte of that attempt), or an attested
        outcome, each under exactly its own guard. Returns whether it applied."""
        a = self.attempt_row(att.get("id"), att.get("n"), att.get("generation"))
        if a is None or a["a1_nonce"] != att.get("a1_nonce") \
                or not same_writer(json.loads(a["writer"]), att.get("writer")):
            return False
        if att.get("kind") == "void":
            return self._void(a)
        if a["state"] != "writing":
            return False  # an attested outcome is applied only to an attempt still writing
        try:
            self.outcome(a["entry_id"], a["n"], a["generation"], a["a1_nonce"], json.loads(a["writer"]),
                         att.get("outcome"), att.get("final_at"), att.get("detail"))
        except Conflict:
            return False
        return True

    def _void(self, a):
        if a["state"] == "void":
            return True
        recovered = a["state"] == "ended" and a["end_kind"] in ("writer_gone", "connection_closed") \
            and a["final_at"] is None
        if a["state"] != "writing" and not recovered:
            return False  # never after A5 (the writer's own record that it wrote), never once decided
        part = self.con.execute("SELECT * FROM parts WHERE entry_id = ? AND n = ?", (a["entry_id"], a["n"])).fetchone()
        if part is None or part["generation"] != a["generation"] \
                or part["state"] != ("inflight" if a["state"] == "writing" else "uncertain"):
            return False
        if self.con.execute("SELECT 1 FROM attributions WHERE attempt_id = ?", (a["id"],)).fetchone():
            return False
        e = self.entry(a["entry_id"])
        if e is None or e["state"] != "open":
            return False
        self.con.execute("UPDATE attempts SET state = 'void', ended_at = COALESCE(ended_at, ?) WHERE id = ?",
                         (self.clock(), a["id"]))
        self.con.execute("UPDATE parts SET state = 'unsent' WHERE entry_id = ? AND n = ?", (a["entry_id"], a["n"]))
        return True

    def void(self, att):
        a = self.attempt_row(att.get("id"), att.get("n"), att.get("generation"))
        if a is not None and a["state"] == "void" and a["a1_nonce"] == att.get("a1_nonce"):
            return "replay"
        if not self.apply_attestation(dict(att, kind="void")):
            raise Conflict("A12's guard failed")
        return "ok"

    # --- the bridge's own transitions ---

    def fix_parts(self, entry_id, parts):
        """E4: an outbox entry's parts, fixed once, from the flush's connection."""
        e = self.entry(entry_id)
        if e is None or e["state"] != "open" or e["owner_kind"] != "outbox":
            raise Conflict("not an open outbox entry")
        if self.parts(entry_id):
            return "replay"
        self._insert_parts(entry_id, parts)
        return "ok"

    def end_attempts(self, observed):
        """A8 (writer gone) and A8c (connection closed): [(attempt id, kind, observed at)]."""
        changed = 0
        for attempt_id, kind, at in observed:
            a = self.con.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
            if a is None or a["state"] != "writing":
                continue
            self.con.execute("UPDATE attempts SET state = 'ended', end_kind = ?, ended_at = ?, final_at = NULL"
                             " WHERE id = ?", (kind, at, attempt_id))
            self.con.execute("UPDATE parts SET state = 'uncertain' WHERE entry_id = ? AND n = ? AND generation = ?",
                             (a["entry_id"], a["n"], a["generation"]))
            self._touch_terminal(a["entry_id"])
            changed += 1
        return changed

    def abandon_or_adopt(self, gone_entries):
        """E8 for a gone direct writer's entry (D1: its remainder is not adopted);
        E9 for an earlier bridge process's announcement (adopted by the outbox)."""
        epoch = int(self.meta("import_epoch") or 0)
        result = []
        for entry_id in gone_entries:
            e = self.entry(entry_id)
            if e is None or e["state"] != "open" or e["owner_kind"] != "direct":
                continue
            if self.con.execute("SELECT 1 FROM attempts WHERE entry_id = ? AND state = 'writing'",
                                (entry_id,)).fetchone():
                continue  # A8 first
            if e["origin"] == "announce":
                self.con.execute("UPDATE entries SET owner_kind = 'outbox' WHERE id = ?", (entry_id,))
                result.append((entry_id, "adopted"))
            else:
                self.con.execute("UPDATE entries SET state = 'abandoned', abandon_epoch = ? WHERE id = ?",
                                 (epoch, entry_id))
                result.append((entry_id, "abandoned"))
        return result

    def raise_alert(self, key, kind, ref, text):
        self.con.execute("INSERT OR IGNORE INTO alerts (alert_key, kind, ref, text, raised_at) VALUES (?, ?, ?, ?, ?)",
                         (key, kind, ref, text, self.clock()))

    def expire(self):
        """E7: an entry whose part has been undecided longer than UNDECIDED_MAX
        after its attempt's write becomes terminal, with its alert; its ended
        attempts become dead (A10); a writing attempt is left writing."""
        cutoff = self.clock() - UNDECIDED_MAX
        rows = self.con.execute(
            "SELECT DISTINCT e.id FROM entries e JOIN attempts a ON a.entry_id = e.id"
            " JOIN parts p ON p.entry_id = a.entry_id AND p.n = a.n AND p.generation = a.generation"
            " WHERE e.state IN ('open', 'abandoned') AND a.state IN ('writing', 'ended') AND a.written_at < ?",
            (cutoff,)).fetchall()
        done = []
        for row in rows:
            e = self.entry(row["id"])
            self.con.execute("UPDATE entries SET state = 'terminal', terminal_reason = ?,"
                             " export_version = export_version + 1 WHERE id = ?",
                             (f"a part's delivery could not be confirmed or ruled out for "
                              f"{int(UNDECIDED_MAX // 3600)} h", e["id"]))
            self.con.execute("UPDATE attempts SET state = 'dead' WHERE entry_id = ? AND state = 'ended'", (e["id"],))
            self.raise_alert(f"outbox:{e['id']}", "undecided", e["id"],
                             f"an outbox post from {e['account']} to {e['channel']} could not be confirmed for"
                             f" {int(UNDECIDED_MAX // 3600)} h; dead-lettered")
            done.append(e["id"])
        return done

    # --- imports (sections 6.3 and 6.4) ---

    def import_fallback(self, name, sha, row):
        """IF: one published fallback file, applied by its entry id X, with its
        closed imports row, in one transaction. Returns what it did."""
        known = self.con.execute("SELECT * FROM imports WHERE name = ?", (name,)).fetchone()
        if known is not None:
            return "imported" if known["sha256"] == sha else "changed"
        epoch = int(self.meta("import_epoch") or 0)
        outcome = self._apply_fallback(name, row, epoch)
        self.con.execute("INSERT INTO imports (name, state, lines, offset, sha256, epoch, first_seen, last_import)"
                         " VALUES (?, 'closed', 1, 0, ?, ?, ?, ?)", (name, sha, epoch, self.clock(), self.clock()))
        return outcome

    def _apply_fallback(self, name, row, epoch):
        if not isinstance(row, dict) or row.get("v") != FALLBACK_FORMAT or not row.get("id") \
                or not row.get("channel") or not row.get("as") or (row.get("text") is None and not row.get("ack")):
            self.hold(name, None, json.dumps(row) if not isinstance(row, (bytes, str)) else row, "invalid fallback file")
            return "held"
        e = self.entry(row["id"])
        att = row.get("attestation")
        if att:
            att = dict(att, id=row["id"], writer=att.get("writer") or row.get("writer"))
        if e is None:  # E1 never committed: by I-1 nothing of this call was sent
            origin = row.get("origin") if row.get("origin") in ("post", "ack", "notify") else "post"
            self._insert_entry(row["id"], row, owner_kind="outbox", writer=None, origin=origin,
                               occ=(name, 0, None))
            return "created"
        if e["state"] in ("done", "terminal"):
            return "noop"
        if e["owner_kind"] == "outbox" and e["state"] == "open":
            if att:
                self.apply_attestation(att)
            return "noop"
        owner = json.loads(e["owner_writer"] or "null")
        reopen = e["state"] == "abandoned" and e["abandon_epoch"] is not None and epoch <= e["abandon_epoch"] + 1
        if not same_writer(owner, row.get("writer")) or not (e["state"] == "open" or reopen):
            self.hold(name, None, jdump(row), "fallback file fails H1's guard")
            return "held"
        self.con.execute("UPDATE entries SET owner_kind = 'outbox', state = 'open' WHERE id = ?", (row["id"],))  # H1
        if att:
            self.apply_attestation(att)
        return "handed off"

    def hold(self, name, ordinal, raw, why):
        self.con.execute("INSERT OR IGNORE INTO held (file, ordinal, raw, why) VALUES (?, ?, ?, ?)",
                         (name, ordinal, raw if isinstance(raw, str) else str(raw), why))
        self.raise_alert(f"held:{name}", "held", name,
                         "queued outbox rows could not be imported and are held; see pchat status")

    def hold_changed(self, name, sha):
        self.con.execute("INSERT OR IGNORE INTO held (file, ordinal, raw, why) VALUES (?, NULL, ?, ?)",
                         (name, sha, "changed after import began"))
        self.raise_alert(f"held:{name}:{sha}", "held", name,
                         "queued outbox rows could not be imported and are held; see pchat status")

    def import_legacy(self, name, start_ordinal, lines, new_offset, prefix_sha, close=False):
        """I2: complete legacy lines of claim `name` from `start_ordinal`, and the
        claim's imports row advanced (or closed), in one transaction."""
        epoch = int(self.meta("import_epoch") or 0)
        known = self.con.execute("SELECT * FROM imports WHERE name = ?", (name,)).fetchone()
        if known is not None and known["state"] == "closed":
            return 0
        if known is None:
            self.con.execute("INSERT INTO imports (name, state, lines, offset, sha256, epoch, first_seen)"
                             " VALUES (?, 'open', 0, 0, ?, ?, ?)", (name, hashlib.sha256(b"").hexdigest(), epoch,
                                                                    self.clock()))
            known = self.con.execute("SELECT * FROM imports WHERE name = ?", (name,)).fetchone()
        if known["lines"] != start_ordinal:
            raise Conflict("ordinal mismatch")
        for i, raw in enumerate(lines, start_ordinal):
            if not raw.strip():
                continue  # a blank line is no row; it keeps its ordinal
            occurrence = hashlib.sha256(f"{name}:{i}".encode()).hexdigest()[:32]
            try:
                row = json.loads(raw)
            except ValueError:
                row = None
            ok = (isinstance(row, dict) and row.get("channel") and row.get("as")
                  and not any(k in row for k in ("parts", "terminal", "id", "fallback", "v"))
                  and (isinstance(row.get("text"), str) or isinstance(row.get("ack"), str)))
            if not ok:
                self.hold(name, i, raw if isinstance(raw, str) else raw.decode(errors="replace"),
                          "unsupported or unreadable legacy row")
                continue
            if self.entry(occurrence) is None:
                self._insert_entry(occurrence, row, owner_kind="outbox", writer=None,
                                   origin="ack" if row.get("ack") else "legacy",
                                   occ=(name, i, hashlib.sha256(raw.encode() if isinstance(raw, str) else raw)
                                        .hexdigest()))
        self.con.execute("UPDATE imports SET lines = ?, offset = ?, sha256 = ?, epoch = ?, last_import = ?,"
                         " state = ? WHERE name = ?",
                         (start_ordinal + len(lines), new_offset, prefix_sha, epoch, self.clock(),
                          "closed" if close else "open", name))
        return len(lines)

    def complete_pass(self):
        self.set_meta("import_epoch", int(self.meta("import_epoch") or 0) + 1)

    # --- evidence and coverage (section 3.4) ---

    def index(self, msgid, channel, account, text, at):
        """V1: a verified message, and the designation check in the same transaction."""
        if not msgid or at is None:
            return False
        cur = self.con.execute("INSERT OR IGNORE INTO messages (msgid, channel, account, text, at) VALUES (?, ?, ?, ?, ?)",
                               (msgid, channel.lower(), account, text, at))
        if cur.rowcount:
            self._designation_check(account, channel.lower(), text, at)
        return bool(cur.rowcount)

    def _designation_check(self, account, channel, text, at):
        acct = self.con.execute("SELECT * FROM accounts WHERE account = ?", (account,)).fetchone()
        if acct is None or acct["designated_at"] is None or acct["suspended_at"] is not None \
                or at < acct["designated_at"]:
            return
        for a in self.con.execute("SELECT * FROM attempts WHERE account = ? AND channel = ? AND state NOT IN"
                                  " ('void', 'retired')", (account, channel)):
            w = window(a)
            if not inside(at, w):
                continue
            if a["text"] == text:
                return  # an attempt of its exact text key can explain it
            if a["multi_line"] and a["state"] in UNRESOLVED:
                return  # an unconfirmed multi-line attempt could have produced any text
        seq = acct["suspension_seq"] + 1
        self.con.execute("UPDATE accounts SET suspended_at = ?, suspended_why = ?, suspension_seq = ?"
                         " WHERE account = ?", (self.clock(), f"unexplained message in {channel}", seq, account))
        self.raise_alert(f"suspended:{account}:{seq}", "suspended", account,
                         f"sender designation of {account} suspended: an unexplained message in {channel}")

    def designate(self, account, at):
        self.con.execute("INSERT INTO accounts (account, designated_at) VALUES (?, ?) ON CONFLICT(account)"
                         " DO UPDATE SET designated_at = excluded.designated_at, suspended_at = NULL",
                         (account, at))

    def coverage_begin(self, channel, start_mark, mark):
        """V2: the channel's log just became complete on this connection."""
        ch = channel.lower()
        row = self.con.execute("SELECT * FROM coverage WHERE channel = ?", (ch,)).fetchone()
        if row is not None and start_mark and row["mark"] == start_mark and row["since"] is not None:
            self.con.execute("UPDATE coverage SET mark = ? WHERE channel = ?", (mark, ch))
        else:
            self.con.execute("INSERT INTO coverage (channel, since, through, mark) VALUES (?, ?, NULL, ?)"
                             " ON CONFLICT(channel) DO UPDATE SET since = excluded.since, through = NULL,"
                             " mark = excluded.mark", (ch, self.clock(), mark))

    def coverage_mark(self, channel, mark):
        self.con.execute("UPDATE coverage SET mark = ? WHERE channel = ?", (mark, channel.lower()))

    def coverage_sync(self, channels, sent_at):
        """V3: the server answered a PING sent at `sent_at`; every live channel is complete through then."""
        for ch in channels:
            self.con.execute("UPDATE coverage SET through = MAX(COALESCE(through, 0), ?) WHERE channel = ?"
                             " AND since IS NOT NULL", (sent_at, ch.lower()))

    def covered(self, channel, start, end):
        if end is None:
            return False
        row = self.con.execute("SELECT * FROM coverage WHERE channel = ?", (channel.lower(),)).fetchone()
        return bool(row and row["since"] is not None and row["through"] is not None
                    and row["since"] <= start and row["through"] >= end)

    # --- reconciliation (section 7) ---

    def _key_attempts(self, account, channel, text):
        return [dict(a) for a in self.con.execute(
            "SELECT * FROM attempts WHERE account = ? AND channel = ? AND text = ? AND is_ack = 0",
            (account, channel, text))]

    def _attributed(self, attempt_id):
        return self.con.execute("SELECT msgid FROM attributions WHERE attempt_id = ?", (attempt_id,)).fetchone()

    def _members(self, account, channel, text):
        out = []
        for a in self._key_attempts(account, channel, text):
            if a["state"] in UNRESOLVED or (a["state"] == "confirmed" and not self._attributed(a["id"])):
                out.append(a)
        return out

    def _unattributed(self, account, channel, text=None):
        q = ("SELECT m.* FROM messages m LEFT JOIN attributions t ON t.msgid = m.msgid"
             " WHERE t.msgid IS NULL AND m.account = ? AND m.channel = ?")
        args = [account, channel]
        if text is not None:
            q += " AND m.text = ?"
            args.append(text)
        return [dict(m) for m in self.con.execute(q + " ORDER BY m.at", args)]

    @staticmethod
    def _components(members):
        comps, seen = [], set()
        for i in range(len(members)):
            if i in seen:
                continue
            stack, comp = [i], []
            seen.add(i)
            while stack:
                j = stack.pop()
                comp.append(members[j])
                for k in range(len(members)):
                    if k not in seen and overlaps(window(members[j]), window(members[k])):
                        seen.add(k)
                        stack.append(k)
            comps.append(comp)
        return comps

    def forced(self, m):
        """Whether message m is forced (section 7.2): the confirmed attempts of
        its own text key around it can be fully matched to distinct messages,
        but not without m, and coverage is complete over their hull."""
        confirmed = [a for a in self._members(m["account"], m["channel"], m["text"]) if a["state"] == "confirmed"]
        group = [a for a in confirmed if inside(m["at"], window(a))]
        if not group:
            return False
        grown = True
        while grown:  # their closed-window connections
            grown = False
            for a in confirmed:
                if a not in group and any(overlaps(window(a), window(b)) for b in group):
                    group.append(a)
                    grown = True
        start = min(window(a)[0] for a in group)
        end = max(window(a)[1] for a in group)
        if not self.covered(m["channel"], start, end):
            return False
        candidates = [x for x in self._unattributed(m["account"], m["channel"], m["text"])
                      if any(inside(x["at"], window(a)) for a in group)]
        fits = lambda a, x: inside(x["at"], window(a))  # noqa: E731
        full, _ = max_matching(group, candidates, fits)
        if full < len(group):
            return False
        without, _ = max_matching(group, [x for x in candidates if x["msgid"] != m["msgid"]], fits)
        return without < len(group)

    def reconcile_keys(self):
        """The text keys with something to decide: an ended member, or an ack-free
        confirmed attempt whose msgid is unknown."""
        return [tuple(r) for r in self.con.execute(
            "SELECT DISTINCT account, channel, text FROM attempts WHERE is_ack = 0 AND state IN ('ended', 'confirmed')")]

    def reconcile(self, account, channel, text, log=lambda msg: None):
        """R1 and R2 for one text key, from the current rows, in the caller's
        transaction (one per key). Returns [(verdict, attempt id)]."""
        verdicts = []
        members = self._members(account, channel, text)
        if not members:
            return verdicts
        messages = self._unattributed(account, channel, text)
        for comp in self._components(members):
            K = [a for a in comp if a["state"] == "confirmed"]
            G = [a for a in comp if a["state"] == "ended"]
            D = [a for a in comp if a["state"] == "dead"]
            W = [a for a in comp if a["state"] == "writing"]
            M = [m for m in messages if any(inside(m["at"], window(a)) for a in comp)]
            if self._r1(account, channel, text, comp, K, G, D, W, M, verdicts):
                continue
            for y in G:
                self._r2(account, channel, text, y, members, verdicts, log)
        return verdicts

    def _r1(self, account, channel, text, comp, K, G, D, W, M, verdicts):
        acct = self.con.execute("SELECT * FROM accounts WHERE account = ?", (account,)).fetchone()
        earliest = min(window(a)[0] for a in comp)
        if acct is None or acct["designated_at"] is None or acct["suspended_at"] is not None \
                or acct["designated_at"] > earliest:
            return False
        if W or D or not M or len(M) != len(K) + len(G):
            return False
        for a in self.con.execute("SELECT * FROM attempts WHERE account = ? AND channel = ? AND multi_line = 1"
                                  " AND state IN ('writing', 'ended', 'dead') AND (text IS NULL OR text != ?)",
                                  (account, channel, text)):
            if any(inside(m["at"], window(a)) for m in M):
                return False  # no multi-line hazard (condition 4)
        items = K + G
        size, matching = max_matching(items, M, lambda a, m: inside(m["at"], window(a)))
        if size != len(items):
            return False
        now = self.clock()
        for i, a in enumerate(items):
            msgid = M[matching[i]]["msgid"]
            self.con.execute("INSERT INTO attributions (msgid, attempt_id, made_at) VALUES (?, ?, ?)",
                             (msgid, a["id"], now))
            if a["state"] == "ended":  # A6
                self.con.execute("UPDATE attempts SET state = 'delivered' WHERE id = ?", (a["id"],))
                self.con.execute("UPDATE parts SET state = 'confirmed', msgid = ? WHERE entry_id = ? AND n = ?"
                                 " AND generation = ?", (msgid, a["entry_id"], a["n"], a["generation"]))
                verdicts.append(("delivered", a["id"]))
            else:  # A9
                self.con.execute("UPDATE parts SET msgid = ? WHERE entry_id = ? AND n = ? AND generation = ?",
                                 (msgid, a["entry_id"], a["n"], a["generation"]))
                verdicts.append(("attributed", a["id"]))
            self._touch_terminal(a["entry_id"])
        return True

    def _r2(self, account, channel, text, y, members, verdicts, log):
        wy = window(y)
        if wy[1] is None:
            return  # condition 1: finality
        closed = [a for a in members if window(a)[1] is not None and a["state"] in ("confirmed", "ended", "dead")]
        group, grown = [y], True
        while grown:
            grown = False
            for a in closed:
                if a not in group and any(overlaps(window(a), window(b)) for b in group):
                    group.append(a)
                    grown = True
        Kc = [a for a in group if a["state"] == "confirmed"]
        start = min([wy[0]] + [window(a)[0] for a in Kc])
        end = max([wy[1]] + [window(a)[1] for a in Kc])
        if not self.covered(channel, start, end):
            return  # condition 2
        M = [m for m in self._unattributed(account, channel, text)
             if any(inside(m["at"], window(a)) for a in Kc + [y])]
        size, _ = max_matching(Kc, M, lambda a, m: inside(m["at"], window(a)))
        if size != len(Kc):
            log(f"reconcile: confirmed attempts of a text key in {channel} cannot all be matched; undecided")
            return  # condition 3, an anomaly
        if any(not self.forced(m) for m in M if inside(m["at"], wy)):
            return  # condition 4
        for m in self._unattributed(account, channel):
            if m["text"] != text and inside(m["at"], wy) and not self.forced(m):
                return  # condition 5, for every part (A3 of #19's first amendment)
        self.con.execute("UPDATE attempts SET state = 'absent' WHERE id = ?", (y["id"],))  # A7
        self.con.execute("UPDATE parts SET state = 'unsent' WHERE entry_id = ? AND n = ? AND generation = ?",
                         (y["entry_id"], y["n"], y["generation"]))
        verdicts.append(("absent", y["id"]))

    # --- collection (I-7) ---

    def collect(self, limit=COLLECT_MAX):
        """G1: delete only what nothing depends on, at most `limit` rows."""
        now = self.clock()
        horizon = now - A - GC_MARGIN
        hulls = self._dependency_hulls()
        deleted = 0
        for m in self.con.execute("SELECT * FROM messages WHERE at < ? ORDER BY at LIMIT ?", (horizon, limit)):
            if any(lo <= m["at"] and (hi is None or m["at"] <= hi)
                   for (acct, ch), spans in hulls.items() if (acct, ch) == (m["account"], m["channel"])
                   for lo, hi in spans):
                continue
            self.con.execute("DELETE FROM attributions WHERE msgid = ?", (m["msgid"],))
            self.con.execute("DELETE FROM messages WHERE msgid = ?", (m["msgid"],))
            deleted += 1
        epoch = int(self.meta("import_epoch") or 0)
        for e in self.con.execute("SELECT * FROM entries WHERE state IN ('done', 'terminal', 'abandoned')"):
            if deleted >= limit:
                break
            if e["state"] == "terminal":
                if e["dead_letter_exported_version"] < e["export_version"]:
                    continue
                alert = self.con.execute("SELECT * FROM alerts WHERE alert_key = ?", (f"outbox:{e['id']}",)).fetchone()
                if alert is not None and alert["exported_at"] is None:
                    continue
            if e["state"] == "abandoned" and not (e["abandon_epoch"] is not None and epoch >= e["abandon_epoch"] + 2):
                continue
            atts = self.attempts(e["id"])
            if not atts and not self.parts(e["id"]):
                continue
            ok = True
            for a in atts:
                w = window(a)
                if a["state"] in ("writing", "ended") or (a["state"] == "dead" and w[1] is None):
                    ok = False
                    break
                if w[1] is None or w[1] >= horizon:
                    ok = False
                    break
                for lo, hi in hulls.get((a["account"], a["channel"]), []):
                    if overlaps(w, (lo, hi)):
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            deleted += self.con.execute("DELETE FROM attempts WHERE entry_id = ?", (e["id"],)).rowcount
            deleted += self.con.execute("DELETE FROM parts WHERE entry_id = ?", (e["id"],)).rowcount
        return deleted

    def _dependency_hulls(self):
        """Per (account, channel): the hulls of every unresolved attempt's dependency component."""
        hulls = {}
        rows = [dict(a) for a in self.con.execute("SELECT * FROM attempts WHERE state NOT IN ('void', 'retired')")]
        by_key = {}
        for a in rows:
            by_key.setdefault((a["account"], a["channel"]), []).append(a)
        for key, atts in by_key.items():
            for comp in self._components(atts):
                if any(a["state"] in ("writing", "ended") or (a["state"] == "dead" and window(a)[1] is None)
                       for a in comp):
                    lo = min(window(a)[0] for a in comp)
                    ends = [window(a)[1] for a in comp]
                    hulls.setdefault(key, []).append((lo, None if None in ends else max(ends)))
        return hulls

    # --- status (section 2.4.5) ---

    def status(self):
        q = lambda sql, *a: self.con.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "waiting": q("SELECT COUNT(*) FROM entries e WHERE e.state = 'open' AND NOT EXISTS (SELECT 1 FROM parts p"
                         " WHERE p.entry_id = e.id AND p.state = 'uncertain')"),
            "checking": q("SELECT COUNT(DISTINCT entry_id) FROM parts p JOIN entries e ON e.id = p.entry_id"
                          " WHERE p.state = 'uncertain' AND e.state IN ('open', 'abandoned')"),
            "held": q("SELECT COUNT(*) FROM held"),
            "suspended": [r[0] for r in self.con.execute("SELECT account FROM accounts WHERE suspended_at IS NOT NULL")],
            "dead": q("SELECT COUNT(*) FROM entries WHERE state = 'terminal'"),
            "epoch": self.epoch,
        }


# --- the request protocol (section 2.4.2) ---------------------------------------

def encode_frame(obj, limit):
    data = json.dumps(obj).encode()
    if len(data) > limit:
        raise ValueError("frame too large")
    return struct.pack(">I", len(data)) + data


def decode_frame(buf, limit):
    """(object, rest) once `buf` holds a whole frame; (None, buf) before. ValueError if over `limit`."""
    if len(buf) < 4:
        return None, buf
    size = struct.unpack(">I", buf[:4])[0]
    if size > limit:
        raise ValueError("frame too large")
    if len(buf) < 4 + size:
        return None, buf
    return json.loads(buf[4:4 + size].decode()), buf[4 + size:]


def peer_credentials(sock):
    """(uid, pid or None) of a connected AF_UNIX peer, or None if unreadable."""
    try:
        if sys.platform == "darwin":
            raw = sock.getsockopt(0, 0x001, 76)  # SOL_LOCAL, LOCAL_PEERCRED: struct xucred
            uid = struct.unpack_from("I", raw, 4)[0]
            try:
                pid = struct.unpack("i", sock.getsockopt(0, 0x002, 4))[0]  # LOCAL_PEERPID
            except OSError:
                pid = None
            return uid, pid
        raw = sock.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERCRED", 17), struct.calcsize("3i"))
        pid, uid, _gid = struct.unpack("3i", raw)
        return uid, pid
    except (OSError, struct.error):
        return None


class _Job:
    """An in-process request: queued, then running, then done; or cancelled while still queued."""

    def __init__(self, fn):
        self.fn, self.state, self.result, self.error = fn, "queued", None, None
        self.cond = threading.Condition()


class _Peer:
    def __init__(self, sock, accepted, creds):
        self.sock, self.accepted, self.creds = sock, accepted, creds
        self.buf, self.out, self.reply_ready = b"", b"", None


class Authority:
    """The bridge's authority: the only holder of the only connection to
    outbox.db (section 2.4). `start` takes the authority lock and opens the
    database; `bind` serves clients on outbox.sock; `run` is the authority
    thread. Without a running thread (the tests), requests run at once."""

    def __init__(self, state, *, clock=time.time, mono=time.monotonic, probes=None, fs=None, log=lambda m: None):
        self.paths = Paths(state)
        self.clock, self.mono, self.log = clock, mono, log
        self.probes = probes or HostProbes()
        self.fs = fs or FS()
        self.store = Store(self.paths, clock=clock, fs=self.fs)
        self.lock_file = None
        self.listener = None
        self.peers = {}
        self.client_queue = []
        self.jobs = []
        self.mutex = threading.Lock()
        self.thread = None
        self.stopping = False
        self.sel = None
        self.wake_r = self.wake_w = None
        self.turn = 0
        self.unavailable = None  # (reason, since) while the authority cannot run

    @property
    def epoch(self):
        return self.store.epoch

    @property
    def running(self):
        return self.store.con is not None

    def start(self):
        """Steps 1-4 of section 2.4.1. Raises Unavailable, having opened and bound nothing."""
        self.paths.state.mkdir(parents=True, exist_ok=True)
        f = open(self.paths.auth_lock, "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            self.unavailable = ("another bridge holds the authority lock", self.clock())
            raise Unavailable(self.unavailable[0]) from None
        try:
            self.store.open()
        except BaseException as err:  # opened and bound nothing: the lock is released for the next try
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
            if isinstance(err, Unavailable):
                self.unavailable = (str(err), self.unavailable[1] if self.unavailable else self.clock())
            raise
        self.lock_file = f
        self.unavailable = None
        try:
            self.fs.mkdir(self.paths.fallback)
            self.fs.mkdir(self.paths.staging)
        except OSError as err:
            self.log(f"authority: cannot create outbox.d: {err}")
        return self.epoch

    def bind(self):
        """Step 5: the socket, mode 0600, unless its path is over the platform's limit."""
        path = str(self.paths.sock)
        if len(os.fsencode(path)) > SOCKET_LIMIT:
            self.log(f"authority: socket path over {SOCKET_LIMIT} bytes; serving the bridge only")
            return False
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        old = os.umask(0o177)  # the socket is created 0600: only this user may connect
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(path)
        finally:
            os.umask(old)
        if os.stat(path).st_mode & 0o777 != 0o600:
            listener.close()
            raise Unavailable("socket mode is not 0600")
        listener.listen(CONN_MAX)
        listener.setblocking(False)
        self.listener = listener
        return True

    def close(self):
        self.stopping = True
        if self.thread is not None:
            self._wake()
            self.thread.join(10)
        for peer in list(self.peers.values()):
            peer.sock.close()
        self.peers.clear()
        if self.listener is not None:
            self.listener.close()
            self.listener = None
            with contextlib.suppress(OSError):
                os.unlink(self.paths.sock)
        self.store.close()
        if self.lock_file is not None:
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None

    # --- requests ---

    def handle(self, request, *, peer_pid=None, bridge=False):
        """One request in one transaction; the reply, written only after COMMIT has returned."""
        rid = request.get("request_id") if isinstance(request, dict) else None

        def reply(result, **data):
            return {"v": PROTOCOL, "request_id": rid, "authority_epoch": self.epoch, "result": result, **data}
        if not isinstance(request, dict) or request.get("v") != PROTOCOL or request.get("op") not in CLIENT_OPS:
            return reply("refused", detail="invalid request or unknown version")
        writer = request.get("writer")
        if request["op"] != "status" and not isinstance(writer, dict):
            return reply("refused", detail="no writer")
        if peer_pid is not None and isinstance(writer, dict) and writer.get("pid") != peer_pid:
            return reply("refused", detail="the request does not speak for its sender")
        if not self.running:
            return reply("error", detail="authority not running")
        try:
            with self.store.tx():
                result, data = self._dispatch(request, bridge)
        except Conflict as err:
            return reply("conflict", detail=str(err))
        except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as err:
            self.log(f"authority: request {request.get('op')} failed: {err!r:.120}")
            return reply("error")
        return reply(result, **data)

    def _dispatch(self, r, bridge):
        s, op, writer = self.store, r["op"], r.get("writer")
        origin = r.get("origin") if r.get("origin") in ("post", "ack", "notify") else "post"
        if bridge and r.get("origin") == "announce":
            origin = "announce"
        if op == "create":
            result = s.create(r["id"], r["entry"], r["parts"], writer, origin)
            return result, {"entry": s.entry_view(r["id"], writer)}
        if op == "queue":
            result = s.queue(r["id"], r["entry"], writer, origin)
            return result, {"entry": s.entry_view(r["id"], writer)}
        if op == "attempt":
            result, att = s.attempt(r["id"], r["n"], r["generation"], r["a1_nonce"], writer, bridge=bridge,
                                    epoch=self.epoch)
            return result, {"attempt": att}
        if op == "outcome":
            return s.outcome(r["id"], r["n"], r["generation"], r["a1_nonce"], writer, r["outcome"],
                             r.get("final_at"), r.get("detail")), {}
        if op == "retire":
            return s.retire(r["id"], r["n"], writer, bridge=bridge), {}
        if op == "void":
            return s.void(dict(r["attestation"], id=r["id"], writer=writer)), {}
        if op == "handoff":
            att = r.get("attestation")
            if att:
                att = dict(att, id=r["id"], writer=writer)
            result = s.handoff(r["id"], writer, att)
            return result, {"entry": s.entry_view(r["id"], writer)}
        if op == "done":
            return s.done(r["id"], writer, bridge=bridge), {}
        if op == "query":
            return "ok", {"entry": s.entry_view(r["id"], writer)}
        return "ok", {"status": s.status()}

    def run_tx(self, fn):
        """An in-process request: `fn(store)` in one transaction on the authority
        thread. Cancelled, having committed nothing, if it has not begun within
        AUTH_WAIT; then CANCELLED is returned and the caller treats it as a
        failed TX. A guard that fails raises Conflict, after the rollback."""
        if not self.running:
            return CANCELLED
        if self.thread is None or threading.current_thread() is self.thread:
            with self.store.tx():
                return fn(self.store)
        job = _Job(fn)
        with self.mutex:
            self.jobs.append(job)
        self._wake()
        deadline = self.mono() + AUTH_WAIT
        with job.cond:
            while job.state == "queued":
                left = deadline - self.mono()
                if left <= 0:
                    with self.mutex:
                        if job.state == "queued":
                            job.state = "cancelled"
                            self.jobs.remove(job)
                            return CANCELLED
                job.cond.wait(max(0.01, min(left, 0.5)))
            while job.state == "running":
                job.cond.wait(0.5)  # its duration now depends on local storage only (C-10)
        if job.error is not None:
            raise job.error
        return job.result

    def _run_job(self, job):
        with self.mutex:
            if job.state != "queued":
                return
            job.state = "running"
        try:
            with self.store.tx():
                job.result = job.fn(self.store)
        except BaseException as err:  # reported to the waiting thread
            job.error = err
        with job.cond:
            job.state = "done"
            job.cond.notify_all()

    # --- the authority thread (section 2.4.3) ---

    def _wake(self):
        if self.wake_w is not None:
            with contextlib.suppress(OSError):
                os.write(self.wake_w, b"x")

    def run_in_thread(self):
        self.sel = selectors.DefaultSelector()
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_r, False)
        self.sel.register(self.wake_r, selectors.EVENT_READ, "wake")
        if self.listener is not None:
            self.sel.register(self.listener, selectors.EVENT_READ, "listen")
        self.thread = threading.Thread(target=self._loop, name="outbox-authority", daemon=True)
        self.thread.start()

    def adopt(self, sock, creds=None):
        """Serve one already connected socket (a socketpair end, in the tests)."""
        if self.sel is None:
            self.sel = selectors.DefaultSelector()
        sock.setblocking(False)
        peer = _Peer(sock, self.mono(), creds if creds is not None else peer_credentials(sock))
        if peer.creds is None or peer.creds[0] != os.getuid():
            sock.close()
            return None
        if len(self.peers) >= CONN_MAX:
            sock.close()
            return None
        self.peers[sock.fileno()] = peer
        self.sel.register(sock, selectors.EVENT_READ, "peer")
        return peer

    def _loop(self):
        while not self.stopping:
            try:
                self.serve_once(0.5)
            except Exception as err:  # the authority thread must stay up
                self.log(f"authority: loop error {err!r:.120}")

    def serve_once(self, timeout=0.0):
        """One turn: accept, read, queue complete frames, run at most one
        in-process and one client request, write replies, close the expired."""
        if self.sel is None:
            self.sel = selectors.DefaultSelector()
        for key, _mask in self.sel.select(timeout) if self.sel.get_map() else []:
            if key.data == "wake":
                with contextlib.suppress(OSError):
                    os.read(self.wake_r, 4096)
            elif key.data == "listen":
                self._accept()
            elif key.data == "peer":
                self._read(key.fileobj)
        now = self.mono()
        for fd, peer in list(self.peers.items()):
            if peer.reply_ready is None and now - peer.accepted > REQ_WAIT:
                self._drop(peer)  # an incomplete frame: nothing committed
            elif peer.reply_ready is not None and now - peer.reply_ready > RESP_WAIT:
                self._drop(peer)  # a reply nobody read: the TX committed before it
        self.turn += 1
        job = None
        with self.mutex:
            if self.jobs:
                job = self.jobs.pop(0)
        if job is not None:
            self._run_job(job)
        if self.client_queue:
            peer, request = self.client_queue.pop(0)
            if peer.sock.fileno() in self.peers:
                reply = self.handle(request, peer_pid=peer.creds[1] if peer.creds else None)
                self._send(peer, reply)
        for peer in list(self.peers.values()):
            if peer.out:
                self._flush(peer)

    def _accept(self):
        while True:
            try:
                sock, _ = self.listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if len(self.peers) >= CONN_MAX:
                sock.close()
                continue
            self.adopt(sock)

    def _read(self, sock):
        peer = self.peers.get(sock.fileno())
        if peer is None:
            return
        try:
            data = sock.recv(65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._drop(peer)
            return
        if not data:
            if peer.reply_ready is None or not peer.out:
                self._drop(peer)
            return
        if peer.reply_ready is not None:
            return  # one request per connection
        peer.buf += data
        try:
            request, rest = decode_frame(peer.buf, FRAME_MAX)
        except ValueError:
            self._drop(peer)
            return
        if request is None:
            return
        peer.buf = rest
        peer.reply_ready = self.mono()  # no more reading for this connection
        if len(self.client_queue) >= QUEUE_MAX:
            rid = request.get("request_id") if isinstance(request, dict) else None
            self._send(peer, {"v": PROTOCOL, "request_id": rid, "authority_epoch": self.epoch, "result": "busy"})
            return
        self.client_queue.append((peer, request))

    def _send(self, peer, reply):
        try:
            peer.out = encode_frame(reply, REPLY_MAX)
        except ValueError:
            peer.out = encode_frame({"v": PROTOCOL, "request_id": reply.get("request_id"),
                                     "authority_epoch": self.epoch, "result": "error"}, REPLY_MAX)
        peer.reply_ready = self.mono()
        self._flush(peer)

    def _flush(self, peer):
        try:
            n = peer.sock.send(peer.out)
            peer.out = peer.out[n:]
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._drop(peer)
            return
        if not peer.out:
            self._drop(peer)

    def _drop(self, peer):
        fd = peer.sock.fileno()
        self.peers.pop(fd, None)
        with contextlib.suppress(KeyError, ValueError, OSError):
            self.sel.unregister(peer.sock)
        peer.sock.close()


# --- clients (sections 2.4.2 and 2.4.4) ---------------------------------------------

class UnixTransport:
    """One request over outbox.sock: connect, send, read the whole reply, all
    within one absolute deadline. Any failure is no reply."""

    def __init__(self, path, *, wait=None, mono=time.monotonic):
        self.path, self.wait, self.mono = str(path), wait, mono

    def exchange(self, request):
        if len(os.fsencode(self.path)) > SOCKET_LIMIT:
            return None
        deadline = self.mono() + (self.wait if self.wait is not None else IPC_WAIT)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(max(0.01, deadline - self.mono()))
            sock.connect(self.path)
            sock.sendall(encode_frame(request, FRAME_MAX))
            buf = b""
            while True:
                left = deadline - self.mono()
                if left <= 0:
                    return None
                sock.settimeout(left)
                data = sock.recv(65536)
                if not data:
                    return None
                buf += data
                reply, _ = decode_frame(buf, REPLY_MAX)
                if reply is not None:
                    return reply


class LocalTransport:
    """A request to an authority in this process: the bridge's own (bridge=True),
    or a test's. The request and reply still pass through the frame encoding."""

    def __init__(self, authority, *, bridge=False):
        self.authority, self.bridge = authority, bridge

    def exchange(self, request):
        request, _ = decode_frame(encode_frame(request, FRAME_MAX), FRAME_MAX)
        if self.bridge:
            base = {"v": PROTOCOL, "request_id": request.get("request_id")}
            try:
                done = self.authority.run_tx(lambda store: self.authority._dispatch(request, True))
            except Conflict as err:
                done = ("conflict", {"detail": str(err)})
            except (sqlite3.Error, OSError, KeyError, TypeError, ValueError):
                return dict(base, result="error", authority_epoch=self.authority.epoch)
            if done is CANCELLED:
                return None  # never began: nothing committed
            result, data = done
            reply = dict(base, authority_epoch=self.authority.epoch, result=result, **data)
        else:
            reply = self.authority.handle(request)
        out, _ = decode_frame(encode_frame(reply, REPLY_MAX), REPLY_MAX)
        return out


class Client:
    """A caller's view of the authority. `call` returns the reply when it is
    ok, replay or conflict, and None when there is no usable reply: no socket,
    refused, busy, error (commit unknown), timed out, or an older epoch's."""

    def __init__(self, transport, *, clock=time.time):
        self.transport, self.clock = transport, clock
        self.epoch, self.restarted = 0, False

    @property
    def available(self):
        return self.transport is not None

    def call(self, op, writer, **args):
        if self.transport is None:
            return None
        request = {"v": PROTOCOL, "op": op, "request_id": uuid.uuid4().hex, "writer": writer, **args}
        try:
            reply = self.transport.exchange(request)
        except Exception:  # no usable reply, whatever the transport failed on
            return None
        if not isinstance(reply, dict) or reply.get("v") != PROTOCOL or reply.get("request_id") != request["request_id"]:
            return None
        if reply.get("result") not in ("ok", "replay", "conflict"):
            return None
        epoch = reply.get("authority_epoch") or 0
        if epoch < self.epoch:
            return None  # a dead authority's buffered reply: true, but not built on
        if self.epoch and epoch > self.epoch:
            self.restarted = True  # the caller queries before its next mutation
        self.epoch = max(self.epoch, epoch)
        return reply


# --- the bridge's file-side import (sections 6.3 and 6.4) ------------------------------

class Importer:
    """IF (fallback files) and I1-I3 (the legacy outbox), run on the bridge's
    flush. Holds only L_flush; never takes outbox.lock to import. File work is
    done here; each file or batch of lines is one in-process request."""

    def __init__(self, authority, *, log=lambda m: None):
        self.authority, self.fs, self.paths = authority, authority.fs, authority.paths
        self.log = log
        self.pass_names = None  # the names the current import pass listed when it began

    def _sha(self, data):
        return hashlib.sha256(data).hexdigest()

    def import_fallback_files(self):
        """At most IMPORT_MAX published files, oldest name first. Returns how many."""
        names = sorted(n for n in self.fs.listdir(self.paths.fallback) if is_published_name(n))
        if self.pass_names is None:
            self.pass_names = set(names)
        count = 0
        for name in names[:IMPORT_MAX]:
            path = self.paths.fallback / name
            try:
                self.fs.durable(path)
                data = self.fs.read(path)
            except FileNotFoundError:
                continue
            sha = self._sha(data)
            try:
                row = json.loads(data.decode())
            except (ValueError, UnicodeDecodeError):
                row = data.decode(errors="replace")
            outcome = self.authority.run_tx(lambda s, name=name, sha=sha, row=row: s.import_fallback(name, sha, row))
            if outcome is CANCELLED:
                break  # not run: try again at the next flush
            if outcome == "changed":
                self.authority.run_tx(lambda s, name=name, sha=sha: s.hold_changed(name, sha))
            self.fs.unlink(path)
            self.fs.sync_dir(self.paths.fallback)
            self.pass_names.discard(name)
            count += 1
            if outcome not in ("imported", "noop"):
                self.log(f"outbox: fallback file {name}: {outcome}")
        if not (self.pass_names & set(self.fs.listdir(self.paths.fallback))):
            self.authority.run_tx(lambda s: s.complete_pass())
            self.pass_names = None
        return count

    def clean_staging(self):
        """A tmp/ file is removed only once its writer is gone, or once published."""
        for name in self.fs.listdir(self.paths.staging):
            path = self.paths.staging / name
            try:
                if self.fs.nlink(path) > 1:
                    self.fs.unlink(path)
                    continue
            except FileNotFoundError:
                continue
            tag = name.split(".")
            if len(tag) >= 3 and tag[1].isdigit():
                writer = {"boot": tag[0], "pid": int(tag[1]), "start": tag[2].replace("_", " ")}
                if self.authority.probes.gone(writer) is True:
                    self.fs.unlink(path)

    def claim_legacy(self):
        """I1: rename a non-empty outbox.jsonl to a never-used claim name, WITHOUT
        outbox.lock; then a durable sync of every claim file present."""
        legacy = self.paths.legacy
        if self.fs.exists(legacy) and self.fs.size(legacy):
            name = f"outbox.claimed-{time.time_ns()}-{secrets.token_hex(4)}.jsonl"
            self.fs.rename(legacy, self.paths.state / name)
            self.fs.sync_dir(self.paths.state)
        claims = self.claim_list()
        for name in claims:
            self.fs.durable(self.paths.state / name)
        return claims

    def claim_list(self):
        return sorted(n for n in self.fs.listdir(self.paths.state)
                      if n.startswith("outbox.claimed-") and n.endswith(".jsonl"))

    def _import_row(self, name):
        row = self.authority.run_tx(lambda s: s.con.execute("SELECT * FROM imports WHERE name = ?",
                                                            (name,)).fetchone())
        if row is CANCELLED:
            return None
        return dict(row) if row is not None else {}

    def import_legacy(self, claims, barrier_at=None):
        """I2 for each open claim, at most LINES_MAX lines per request; with
        `barrier_at` (an outbox.lock acquisition after the claim was first
        recorded), the final read and I3."""
        for name in claims:
            path = self.paths.state / name
            while True:
                try:
                    data = self.fs.read(path)
                except FileNotFoundError:
                    break
                row = self._import_row(name)
                if row is None:
                    return
                if row.get("state") == "closed":
                    self.fs.unlink(path)  # I3's unlink, replayed
                    self.fs.sync_dir(self.paths.state)
                    break
                offset, ordinal = row.get("offset", 0), row.get("lines", 0)
                if row and self._sha(data[:offset]) != row.get("sha256"):
                    self.authority.run_tx(lambda s: s.hold_changed(name, self._sha(data)))
                    break  # rewritten by something outside this design: held, never unlinked
                tail = data[offset:]
                cut = tail.rfind(b"\n") + 1
                complete = tail[:cut].split(b"\n")[:-1] if cut else []
                take = complete[:LINES_MAX]
                new_offset = offset + sum(len(x) + 1 for x in take)
                final = (barrier_at is not None and row.get("first_seen") is not None
                         and row["first_seen"] < barrier_at and len(take) == len(complete))
                lines = [x.decode(errors="replace") for x in take]
                if final and tail[cut:]:
                    lines.append(tail[cut:].decode(errors="replace"))  # an unterminated last line: held
                    new_offset = len(data)
                if not lines and not final:
                    if not row:
                        self.authority.run_tx(lambda s: s.import_legacy(name, 0, [], 0, self._sha(b"")))
                    break
                try:
                    n = self.authority.run_tx(lambda s, lines=lines, new_offset=new_offset, final=final:
                                              s.import_legacy(name, ordinal, lines, new_offset,
                                                              self._sha(data[:new_offset]), close=final))
                except Conflict:
                    break
                if n is CANCELLED:
                    return
                if final:
                    self.fs.unlink(path)
                    self.fs.sync_dir(self.paths.state)
                    break
                if len(take) == len(complete):
                    break
