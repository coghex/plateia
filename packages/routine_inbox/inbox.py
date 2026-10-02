"""Durable collection and conservative send reconciliation; stdlib only."""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from .protocol import TOKEN, envelope, header, recipients


class Blocked(RuntimeError):
    pass


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.timestamp()


def config(path):
    data = json.loads(Path(path).read_text())
    required = {"alias", "assistant", "owner", "projects", "logs", "acks", "pchat", "manager_state"}
    if not required <= data.keys() or not data["projects"]:
        raise Blocked("incomplete private routing manifest")
    names = [data[k] for k in ("alias", "assistant", "owner")]
    if any(not isinstance(n,str) or not TOKEN.fullmatch(n) for n in names):
        raise Blocked("invalid role or destination name")
    if len({n.lower() for n in names}) != 3:
        raise Blocked("destination alias and authenticated roles must be distinct")
    for project in data["projects"].values():
        if not {"channel", "prefix", "manager"} <= project.keys():
            raise Blocked("project lacks channel, prefix, or manager")
        if not TOKEN.fullmatch(project["manager"]) or not all(
                re.fullmatch(r"#[a-z0-9_-]+",project[k]) for k in ("channel","prefix")):
            raise Blocked("invalid project routing identity")
    routes=list(data["projects"].values())
    for i, left in enumerate(routes):
        for right in routes[i+1:]:
            if (left["channel"] == right["channel"] or left["prefix"].startswith(right["prefix"])
                    or right["prefix"].startswith(left["prefix"])
                    or left["channel"].startswith(right["prefix"])
                    or right["channel"].startswith(left["prefix"])):
                raise Blocked("ambiguous project channel/prefix mapping")
    return data


class Inbox:
    def __init__(self, state, cfg):
        self.state, self.cfg = Path(state), cfg
        self.state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state / "inbox.sqlite3", timeout=0)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cursors(path TEXT PRIMARY KEY, inode TEXT, offset INTEGER);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, project TEXT, channel TEXT,
          sender TEXT, request TEXT, kind TEXT, parts INTEGER, root TEXT, updated REAL,
          problem TEXT);
        CREATE TABLE IF NOT EXISTS fragments(batch TEXT, part INTEGER, id TEXT, body TEXT,
          PRIMARY KEY(batch,part));
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, status TEXT,
          claim TEXT, claim_until REAL, action TEXT, decision TEXT, reply_ids TEXT);
        CREATE TABLE IF NOT EXISTS problems(id TEXT PRIMARY KEY, detail TEXT);
        """)

    @contextlib.contextmanager
    def locked(self):
        with (self.state / "run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("another inbox operation holds the lock") from None
            try:
                with self.db:
                    yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def project(self, channel):
        matches = [(name, p) for name, p in self.cfg["projects"].items()
                   if channel == p["channel"] or channel.startswith(p["prefix"])]
        if len(matches) != 1:
            return None, None
        return matches[0]

    def paths(self):
        root = Path(self.cfg["logs"]).expanduser()
        if not root.is_dir():
            raise Blocked("chat log directory is unavailable")
        paths = [p for p in sorted(root.glob("*.jsonl")) if self.project("#" + p.stem)[0]]
        if not {p["channel"] for p in self.cfg["projects"].values()} <= {"#"+p.stem for p in paths}:
            raise Blocked("a configured project's main log is missing; coverage is unknown")
        return paths

    def initialized(self):
        return self.db.execute("SELECT value FROM meta WHERE key='cutover'").fetchone()

    def initialize(self):
        """Explicit first-run baseline. Never retrospectively turn history into requests."""
        with self.locked():
            if self.initialized():
                raise Blocked("already initialized; refusing to discard pending work")
            paths = self.paths()
            mains = {p["channel"] for p in self.cfg["projects"].values()}
            if not mains <= {"#" + p.stem for p in paths}:
                raise Blocked("a configured project's main log is missing")
            cutover = utc()
            for path in paths:
                st = path.stat()
                # A partial final record belongs to the next scan, not the baseline.
                raw = path.read_bytes()
                offset = raw.rfind(b"\n") + 1
                self.db.execute("INSERT INTO cursors VALUES(?,?,?)",
                                (str(path), f"{st.st_dev}:{st.st_ino}", offset))
            self.db.execute("INSERT INTO meta VALUES('cutover',?)", (cutover,))
            self.db.execute("INSERT INTO meta VALUES('manifest',?)", (self.manifest_digest(),))
            return {"initialized": True, "cutover": cutover, "channels": len(paths),
                    "historical_requests_enrolled": 0}

    def problem(self, key, detail):
        self.db.execute("INSERT OR REPLACE INTO problems VALUES(?,?)", (key, detail))

    def manifest_digest(self):
        return hashlib.sha256(json.dumps(self.cfg,sort_keys=True).encode()).hexdigest()

    def ingest(self):
        baseline = self.initialized()
        if not baseline:
            raise Blocked("initialize explicitly before polling")
        pinned = self.db.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
        if not pinned or pinned[0] != self.manifest_digest():
            raise Blocked("routing manifest changed; explicit reconciliation is required before using another identity or source")
        cutoff = timestamp(baseline[0])
        budget = 2 * 1024 * 1024
        paths = self.paths()
        missing = {r[0] for r in self.db.execute("SELECT path FROM cursors")} - {str(p) for p in paths}
        for path in missing:
            self.problem(path,"previously observed channel log is missing; pending work retained")
        for path in paths:
            st = path.stat()
            inode = f"{st.st_dev}:{st.st_ino}"
            cur = self.db.execute("SELECT * FROM cursors WHERE path=?", (str(path),)).fetchone()
            offset = cur["offset"] if cur else 0
            if cur and (cur["inode"] != inode or st.st_size < offset):
                self.problem(str(path), "log replaced or truncated; cursor retained; manual reconciliation required")
                continue
            with path.open("rb") as stream:
                stream.seek(offset)
                raw = stream.read(min(st.st_size - offset,budget))
            complete = raw.rfind(b"\n") + 1
            budget -= len(raw)
            if raw and not complete and st.st_size-offset > len(raw):
                self.problem(str(path),"record exceeds collection budget; manual reconciliation required")
            for line in raw[:complete].splitlines(keepends=True):
                line_at = offset
                offset += len(line)
                try:
                    entry = json.loads(line)
                    if entry.get("channel") != "#" + path.stem:
                        raise ValueError("channel does not match its log")
                    if timestamp(entry["at"]) < cutoff:
                        continue
                    self.message(entry)
                except (ValueError, KeyError, TypeError) as error:
                    self.problem(f"{path}:{line_at}", f"unreadable/untrusted record: {error}")
            self.db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?,?)", (str(path), inode, offset))
            if budget <= 0:
                break

    def message(self, e):
        mid = e.get("msgid")
        if not isinstance(mid, str) or not mid:
            self.problem(hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest(),
                         "record has no stable message ID; never answer automatically")
            return
        previous = self.db.execute("SELECT payload FROM messages WHERE id=?", (mid,)).fetchone()
        if previous:
            old = json.loads(previous[0])
            # Replayed is observation metadata, not message identity.
            if any(old.get(k) != e.get(k) for k in ("channel", "from", "text", "reply_to", "routing")):
                self.problem(mid, "same message ID has conflicting content")
                self.db.execute("UPDATE batches SET problem='conflicting message ID' WHERE id IN "
                                "(SELECT batch FROM fragments WHERE id=?)",(mid,))
            return
        self.db.execute("INSERT INTO messages VALUES(?,?)", (mid, json.dumps(e)))
        project, p = self.project(e["channel"])
        if e.get("verified") is not True or e.get("from") != p["manager"]:
            return
        kind, request, body, continued = header(e.get("text", ""))
        routing = e.get("routing")
        if e.get("routing_error"):
            self.problem(mid, "invalid routing envelope; no automatic action")
            return
        if routing:
            # Revalidate persisted metadata as well as wire metadata.
            routing = envelope(dict(zip(("+plateia/to", "+plateia/key", "+plateia/part", "+plateia/parts"),
                                        (",".join(routing["to"]), routing["key"],
                                         str(routing["part"]), str(routing["parts"])))))
            addressed = self.cfg["alias"].lower() in routing["to"]
        else:
            addressed = self.cfg["alias"] in recipients(e.get("text", ""), {self.cfg["alias"]})
        if not addressed or kind not in ("question", "blocked"):
            return
        if not request:
            self.problem(mid, "addressed question lacks a request ID")
            return
        key = routing["key"] if routing else mid
        bid = hashlib.sha256(f"{project}\0{e['channel']}\0{e['from']}\0{key}".encode()).hexdigest()[:32]
        part, total = (routing["part"], routing["parts"]) if routing else (1, 1)
        old = self.db.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
        problem = None if routing else "legacy unframed question: ask manager to resend using explicit --to metadata"
        if old and (old["parts"] != total or old["request"] != request or old["kind"] != kind):
            problem = "multipart metadata conflicts"
        self.db.execute("INSERT OR IGNORE INTO batches VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (bid, project, e["channel"], e["from"], request, kind, total,
                         mid if part == 1 else None, time.time(), problem))
        prior = self.db.execute("SELECT body FROM fragments WHERE batch=? AND part=?", (bid, part)).fetchone()
        if prior and prior[0] != body:
            problem = "multipart content conflicts"
        if problem:
            self.db.execute("UPDATE batches SET problem=? WHERE id=?", (problem, bid))
        if not prior:
            self.db.execute("INSERT INTO fragments VALUES(?,?,?,?)", (bid, part, mid, body))
            self.db.execute("UPDATE batches SET updated=?, root=COALESCE(root,?) WHERE id=?",
                            (time.time(), mid if part == 1 else None, bid))
        self.db.execute("INSERT OR IGNORE INTO items(id,status) VALUES(?,'pending')", (bid,))

    def reconcile(self):
        messages = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM messages")]
        path = Path(self.cfg["acks"]).expanduser()
        if not path.exists():
            raise Blocked("acceptance log unavailable; refusing to infer manager acceptance")
        acks = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for item in self.db.execute("SELECT * FROM items WHERE status NOT IN ('accepted','resolved')").fetchall():
            batch = self.db.execute("SELECT * FROM batches WHERE id=?", (item["id"],)).fetchone()
            manager = self.cfg["projects"][batch["project"]]["manager"]
            resolutions = [m for m in messages if m.get("verified") is True
                           and m.get("from") in (manager,self.cfg["owner"])
                           and m.get("channel") == batch["channel"]
                           and m.get("reply_to") == batch["root"]
                           and header(m.get("text",""))[:2] in
                           (("answer",batch["request"]),("decision",batch["request"]))]
            if resolutions:
                self.db.execute("UPDATE items SET status='resolved' WHERE id=?",(item["id"],))
                continue
            if not item["action"]:
                continue
            replies = [m for m in messages if m.get("verified") is True
                       and m.get("from") == self.cfg["assistant"] and m.get("channel") == batch["channel"]
                       and m.get("reply_to") == batch["root"]
                       and m.get("routing", {}).get("key") == item["action"]]
            ids = {r["msgid"] for r in replies}
            if not ids:
                continue
            totals = {r["routing"]["parts"] for r in replies}
            parts = {r["routing"]["part"] for r in replies}
            complete = len(totals) == 1 and parts == set(range(1, next(iter(totals)) + 1))
            accepted = {a.get("reply_to") for a in acks if a.get("by") == manager
                        and a.get("channel") == batch["channel"] and a.get("project") == batch["project"]}
            accepted |= {m.get("reply_to") for m in messages if m.get("verified") is True
                         and m.get("from") == manager and m.get("channel") == batch["channel"]}
            status = "accepted" if complete and ids <= accepted else "awaiting_manager_ack"
            if status == "accepted" and json.loads(item["decision"])["kind"] == "escalate":
                status = "waiting_owner"
            self.db.execute("UPDATE items SET status=?, reply_ids=? WHERE id=?",
                            (status, json.dumps(sorted(ids)), item["id"]))

    def poll(self, limit=5):
        with self.locked():
            self.ingest()
            self.reconcile()
            out = []
            for row in self.db.execute("SELECT i.*, b.project,b.channel,b.request,b.kind,b.parts,b.root,b.problem "
                                       "FROM items i JOIN batches b USING(id) WHERE status NOT IN ('accepted','resolved') ORDER BY b.updated"):
                item = dict(row)
                fragments = self.db.execute("SELECT part,body FROM fragments WHERE batch=? ORDER BY part", (row["id"],)).fetchall()
                item["text"] = "\n".join(r["body"] for r in fragments)
                item["complete"] = len(fragments) == row["parts"] and bool(row["root"])
                item["claimable"] = (row["status"] == "pending" and item["complete"] and not row["problem"]
                                     and (not row["claim_until"] or row["claim_until"] < time.time()))
                out.append(item)
            out.sort(key=lambda i:not i["claimable"])
            result = {"items": out[:limit], "remaining": max(0, len(out)-limit),
                      "problems": [dict(r) for r in self.db.execute("SELECT * FROM problems")],
                      "cutover": self.initialized()[0]}
            digest = hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
            prior = self.db.execute("SELECT value FROM meta WHERE key='last_poll'").fetchone()
            result["changed"] = not prior or prior[0] != digest
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('last_poll',?)",(digest,))
            return result

    def claim(self, bid):
        with self.locked():
            row = self.db.execute("SELECT i.*,b.parts,b.root,b.problem FROM items i JOIN batches b USING(id) WHERE id=?", (bid,)).fetchone()
            count = self.db.execute("SELECT count(*) FROM fragments WHERE batch=?", (bid,)).fetchone()[0]
            if not row or row["status"] != "pending" or row["problem"] or not row["root"] or count != row["parts"]:
                raise Blocked("item is incomplete, quarantined, or already acted on")
            if row["claim_until"] and row["claim_until"] > time.time():
                raise Blocked("another run holds this item")
            token = uuid.uuid4().hex
            self.db.execute("UPDATE items SET claim=?,claim_until=? WHERE id=?", (token,time.time()+600,bid))
            return {"id": bid, "claim": token, "expires_in_seconds": 600}

    def prepare(self, bid, token, decision):
        """Commit intent before a sender can run. A repeated call never resends."""
        with self.locked():
            self.ingest()
            self.reconcile()
            row = self.db.execute("SELECT i.*,b.channel,b.root,b.project,b.request,b.kind,b.problem FROM items i JOIN batches b USING(id) WHERE id=?", (bid,)).fetchone()
            if not row or row["problem"] or row["status"] != "pending" or row["claim"] != token or row["claim_until"] < time.time():
                raise Blocked("missing, expired, or already consumed claim")
            if decision.get("kind") not in ("settled", "escalate") or any(
                    not isinstance(decision.get(k),str) or not decision[k].strip() for k in ("source","answer")):
                raise Blocked("decision requires kind, evidence source and answer/question")
            if row["kind"] == "blocked" and decision["kind"] != "escalate":
                raise Blocked("blocked reports require escalation")
            if self.cfg["owner"].lower() in {x.lower() for x in re.findall(r"@([A-Za-z0-9_-]+)",json.dumps(decision))}:
                raise Blocked("reply must not contain a literal owner mention, even as a quotation")
            action = uuid.uuid4().hex
            manager = self.cfg["projects"][row["project"]]["manager"]
            prefix = "Settled clarification" if decision["kind"] == "settled" else "Owner decision needed; please escalate once"
            body = (f"{prefix}: {decision['answer']} Source: {decision['source']}. "
                    "Existing request scope and endpoint unchanged; no new action authority.")
            args = [str(Path(self.cfg["pchat"]).expanduser()), "post", row["channel"],
                    "--as", self.cfg["assistant"], "--to", manager, "--message-key", action,
                    "--reply-to", row["root"], "--type", "answer", "--re", row["request"], body]
            self.db.execute("UPDATE items SET status='prepared',action=?,decision=? WHERE id=?",
                            (action,json.dumps({**decision,"argv":args}),bid))
            return args

    def send(self, bid, run):
        # Hold the nonblocking lock across the bounded subprocess. A crash after
        # this commit leaves uncertain intent; no timer or lease permits a retry.
        with (self.state / "run.lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("another inbox operation holds the lock") from None
            with self.db:
                self.ingest()
                self.reconcile()
            row = self.db.execute("SELECT i.*,b.problem,b.project FROM items i JOIN batches b USING(id) WHERE id=?",(bid,)).fetchone()
            if not row or row["status"] != "prepared" or row["problem"]:
                raise Blocked("send is already attempted or was not prepared; reconcile, never retry blindly")
            self.verify_manager(row["project"])
            with self.db:
                self.db.execute("UPDATE items SET status='uncertain' WHERE id=?",(bid,))
            result = run(json.loads(row["decision"])["argv"])
            with self.db:
                # Even rc=0 proves only submission. rc=3 belongs to pchat's
                # durable outbox; the caller must never create another copy.
                self.db.execute("UPDATE items SET status=? WHERE id=?",
                                ("submitted" if result.returncode == 0 else "outbox_or_uncertain",bid))
            return {"returncode": result.returncode, "resolved": False,
                    "next": "poll for correlated chat evidence and exact manager acceptance"}

    def verify_manager(self, project):
        """Read-only identity preflight; never type, retarget, or weaken permissions."""
        path = Path(self.cfg["manager_state"]).expanduser()/project/'manager.json'
        metadata = json.loads(path.read_text())
        if metadata.get('project') != project or not metadata.get('surface_id') or not metadata.get('workspace_id'):
            raise Blocked("registered manager identity is incomplete")
        target = {k:metadata[k] for k in ('workspace_id','surface_id')}
        result = subprocess.run(['cmux','rpc','surface.input_state',json.dumps(target)],
                                capture_output=True,text=True,timeout=10)
        if result.returncode:
            raise Blocked("live manager preflight denied or unavailable; no send attempted")
        state = json.loads(result.stdout)
        if any(state.get(k) != v for k,v in target.items()) or state.get('agent') is not True:
            raise Blocked("registered manager surface is stale or no longer an agent")
        return {"project":project,"manager":self.cfg['projects'][project]['manager'],"live":True}
