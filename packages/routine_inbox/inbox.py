"""Durable transport-neutral inbox and fenced reply lifecycle; Python 3.10+."""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .adapter import AdapterError, matches
from .protocol import TOKEN, envelope, header, recipients, tags


class Blocked(RuntimeError):
    pass


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.timestamp()


def config(value):
    data = json.loads(Path(value).read_text()) if isinstance(value, (str, Path)) else value
    if not isinstance(data, dict) or not {"alias", "assistant", "owner", "projects", "claim_seconds"} <= data.keys():
        raise Blocked("incomplete routing configuration")
    data = json.loads(json.dumps(data))
    names = [data[k] for k in ("alias", "assistant", "owner")]
    if any(not isinstance(n, str) or not TOKEN.fullmatch(n) for n in names):
        raise Blocked("invalid account or recipient")
    for k in ("alias", "assistant", "owner"):
        data[k] = data[k].lower()
    if data["assistant"] == data["owner"]:
        raise Blocked("assistant and owner accounts must differ")
    if type(data["claim_seconds"]) is not int or not 1 <= data["claim_seconds"] <= 86400:
        raise Blocked("claim_seconds must be an integer from 1 through 86400")
    if not isinstance(data["projects"], dict) or not data["projects"]:
        raise Blocked("projects must be a nonempty mapping")
    for name, p in data["projects"].items():
        if not isinstance(name, str) or not TOKEN.fullmatch(name) or not isinstance(p, dict):
            raise Blocked("invalid project")
        if not {"manager", "recipient", "request_prefix"} <= p.keys() or not (p.get("channel") or p.get("prefix")):
            raise Blocked("project lacks routing fields")
        if any(not isinstance(p[k], str) or not TOKEN.fullmatch(p[k]) for k in ("manager", "recipient", "request_prefix")):
            raise Blocked("invalid project account, recipient, or request prefix")
        p["manager"], p["recipient"] = p["manager"].lower(), p["recipient"].lower()
        if p["manager"] == data["assistant"]:
            raise Blocked("assistant and manager accounts must differ")
        if any(not isinstance(p[k], str) or not re.fullmatch(r"#[a-z0-9_-]+", p[k]) for k in ("channel", "prefix") if k in p):
            raise Blocked("invalid channel or channel prefix")
    projects = list(data["projects"].values())
    for i, left in enumerate(projects):
        for right in projects[i + 1:]:
            if (left.get("channel") and matches(left["channel"], right)
                    or right.get("channel") and matches(right["channel"], left)
                    or left.get("prefix") and right.get("prefix") and (
                        left["prefix"].startswith(right["prefix"]) or right["prefix"].startswith(left["prefix"]))
                    or left["request_prefix"].startswith(right["request_prefix"])
                    or right["request_prefix"].startswith(left["request_prefix"])):
                raise Blocked("ambiguous target or request-prefix mapping")
    return data


def decode(record):
    """Validate the normalized adapter record and decode only the protocol layer."""
    if not isinstance(record, dict) or not all(isinstance(record.get(k), str) and record[k]
            for k in ("msgid", "at", "target", "text")):
        raise AdapterError("invalid normalized message")
    try:
        timestamp(record["at"])
    except ValueError as error:
        raise AdapterError("invalid server timestamp") from error
    if not isinstance(record.get("tags", {}), dict):
        raise AdapterError("invalid normalized tags")
    raw = record.get("tags", {})
    routing_error = None
    try:
        routing = envelope(raw)
    except ValueError as error:
        routing, routing_error = None, str(error)
    kind, request, body, continued = header(record["text"])
    account = record.get("account")
    if account is not None and not isinstance(account, str):
        raise AdapterError("invalid attested account")
    if record.get("reply_to") is not None and not isinstance(record["reply_to"], str):
        raise AdapterError("invalid reply correlation")
    return {**record, "account": account.lower() if account and TOKEN.fullmatch(account) else account,
            "routing": routing, "routing_error": routing_error, "kind": kind, "request": request, "body": body,
            "continued": continued}


def complete_copies(records, valid=lambda message: True):
    """Return complete copies and an explicit conflict, preserving every boundary."""
    copies, current = [], []
    for m in records:
        route = m.get("routing")
        if not route or m.get("routing_error") or not valid(m):
            return [], "inconsistent reply framing or correlation"
        part, total = route["part"], route["parts"]
        if part == 1:
            if current:
                return [], "interleaved multipart copies"
            current = [m]
        elif not current or part != len(current) + 1 or total != current[0]["routing"]["parts"]:
            return [], "orphaned or inconsistent multipart boundary"
        else:
            current.append(m)
        if len(current) == total:
            if any(m["kind"] != current[0]["kind"] or m["routing"]["to"] != current[0]["routing"]["to"]
                   or m["routing"].get("auto") != current[0]["routing"].get("auto") for m in current):
                return [], "multipart framing conflicts"
            copies.append(current)
            current = []
    if copies:
        bodies = [[m["text"] for m in copy] for copy in copies]
        if any(body != bodies[0] for body in bodies[1:]):
            return [], "complete copies have conflicting text"
    return copies, None


class Inbox:
    def __init__(self, state, cfg, adapter, clock=time.time):
        self.cfg = config(cfg)  # validate before touching the state directory
        if adapter is None:
            raise Blocked("no adapter supplied; live integration awaits the approved install route")
        self.state, self.adapter, self.clock = Path(state), adapter, clock
        self.db = None

    @contextlib.contextmanager
    def operation(self, initialize=False):
        if initialize and (self.state / "inbox.sqlite3").exists():
            raise Blocked("existing inbox must not be reinitialized or implicitly migrated")
        if initialize:
            self.state.mkdir(parents=True, exist_ok=True)
        elif not self.state.is_dir():
            raise Blocked("initialize explicitly before using an inbox")
        lockpath = self.state / "run.lock"
        if not initialize and not lockpath.exists():
            raise Blocked("inbox lock is missing")
        with lockpath.open("a" if initialize else "r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked("another inbox operation holds the lock") from None
            path = self.state / "inbox.sqlite3"
            try:
                if initialize and path.exists():
                    raise Blocked("inbox appeared during initialization; preserve it")
                if not initialize and not path.exists():
                    raise Blocked("initialize explicitly before using an inbox")
                db = sqlite3.connect(path, timeout=0)
                self.db = db
                db.row_factory = sqlite3.Row
                if initialize:
                    self.schema()
                elif db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != "3":
                    raise Blocked("unsupported inbox schema; preserve it for explicit migration")
                with db:
                    yield
            finally:
                if self.db is not None:
                    self.db.close()
                    self.db = None
                fcntl.flock(lock, fcntl.LOCK_UN)

    def schema(self):
        self.db.executescript("""
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cursors(stream TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(msgid TEXT PRIMARY KEY, payload TEXT NOT NULL,
          account TEXT, target TEXT, message_key TEXT);
        CREATE INDEX IF NOT EXISTS message_key_index ON messages(account,target,message_key);
        CREATE TABLE IF NOT EXISTS acks(identity TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS groups(gid TEXT PRIMARY KEY, project TEXT, target TEXT,
          account TEXT, request TEXT, parts INTEGER, root TEXT, problem TEXT, framing TEXT, context TEXT);
        CREATE TABLE IF NOT EXISTS fragments(gid TEXT, part INTEGER, msgid TEXT, body TEXT,
          PRIMARY KEY(gid,part));
        CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, gid TEXT UNIQUE, status TEXT,
          claim TEXT, claim_until REAL, action TEXT, decision TEXT, attempts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS problems(identity TEXT PRIMARY KEY, detail TEXT);
        CREATE TABLE IF NOT EXISTS audit(at REAL, item TEXT, operation TEXT, reason TEXT);
        """)

    def digest(self):
        return hashlib.sha256(json.dumps(self.cfg, sort_keys=True).encode()).hexdigest()

    def initialize(self):
        with self.operation(initialize=True):
            if self.db.execute("SELECT 1 FROM meta").fetchone():
                raise Blocked("already initialized; preserve pending work")
            self.db.executemany("INSERT INTO meta VALUES(?,?)", [
                ("schema", "3"), ("cutover", str(self.clock())), ("configuration", self.digest()),
                ("configuration_snapshot", json.dumps(self.cfg, sort_keys=True)),
                ("project_cutovers", json.dumps({name: self.clock() for name in self.cfg["projects"]}))])
            return {"initialized": True, "historical_requests_enrolled": 0}

    def project(self, target):
        found = [(n, p) for n, p in self.cfg["projects"].items() if matches(target, p)]
        return found[0] if len(found) == 1 else (None, None)

    def cursor(self, stream):
        row = self.db.execute("SELECT value FROM cursors WHERE stream=?", (stream,)).fetchone()
        return json.loads(row[0]) if row else None

    def read(self):
        if self.db.execute("SELECT value FROM meta WHERE key='configuration'").fetchone()[0] != self.digest():
            raise Blocked("configuration changed; explicit reconciliation required")
        cutoffs = json.loads(self.db.execute("SELECT value FROM meta WHERE key='project_cutovers'").fetchone()[0])
        batches = []
        for name, p in self.cfg["projects"].items():
            selector = {k: p[k] for k in ("channel", "prefix") if k in p}
            batches.append((name, self.adapter.read_messages(selector, self.cursor(name))))
        ack_batch = self.adapter.read_acknowledgements(self.cursor("@acks"))
        # All reads must succeed before any evidence or cursor is committed.
        for name, batch in batches:
            for raw in batch.records:
                entry = decode(raw)
                if self.project(entry["target"])[0] != name:
                    raise AdapterError("adapter returned a record outside its target selector")
                if timestamp(entry["at"]) < cutoffs[name]:
                    continue
                self.message(entry)
        for ack in ack_batch.records:
            if not isinstance(ack, dict) or not all(isinstance(ack.get(k), str) and ack[k]
                    for k in ("msgid", "at", "target")):
                raise AdapterError("invalid normalized acknowledgement")
            timestamp(ack["at"])
            if not isinstance(ack.get("account"), str) or not TOKEN.fullmatch(ack["account"]):
                continue
            ack = {**ack, "account": ack["account"].lower()}
            value = json.dumps(ack, sort_keys=True)
            self.db.execute("INSERT OR IGNORE INTO acks VALUES(?,?)", (hashlib.sha256(value.encode()).hexdigest(), value))
        self.advance_cursors([(n, b.cursor) for n, b in batches] + [("@acks", ack_batch.cursor)])

    def advance_cursors(self, values):
        self.db.executemany("INSERT OR REPLACE INTO cursors VALUES(?,?)",
                            [(stream, json.dumps(cursor)) for stream, cursor in values])

    def problem(self, identity, detail):
        self.db.execute("INSERT OR REPLACE INTO problems VALUES(?,?)", (identity, detail))

    def quarantine_group(self, gid, detail):
        self.problem(gid, detail)
        self.db.execute("UPDATE groups SET problem=? WHERE gid=?", (detail, gid))
        self.db.execute("UPDATE items SET status=CASE WHEN attempts=0 THEN 'quarantined' ELSE 'evidence_conflict' END WHERE gid=?", (gid,))

    def message(self, e):
        mid = e["msgid"]
        payload = json.dumps(e, sort_keys=True)
        previous = self.db.execute("SELECT payload FROM messages WHERE msgid=?", (mid,)).fetchone()
        if previous:
            if previous[0] != payload:
                raise AdapterError("stable message identity has conflicting contents")
            return
        raw_key = e.get("tags", {}).get("+plateia/key")
        raw_key = raw_key if isinstance(raw_key, str) else None
        self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (mid, payload, e["account"], e["target"], raw_key))
        name, p = self.project(e["target"])
        if e.get("routing_error"):
            self.problem(mid, e["routing_error"])
            if e["account"] == p["manager"] and raw_key:
                gid = hashlib.sha256(json.dumps([name, e["target"], e["account"], raw_key]).encode()).hexdigest()
                self.quarantine_group(gid, "invalid multipart question envelope")
            return  # retain its boundary, but never count it as evidence
        if e["account"] != p["manager"] or e["account"] == self.cfg["assistant"]:
            return
        route = e["routing"]
        addressed = self.cfg["alias"] in (route["to"] if route else recipients(e["text"], {self.cfg["alias"]}))
        key = route["key"] if route else mid
        gid = hashlib.sha256(json.dumps([name, e["target"], e["account"], key]).encode()).hexdigest()
        previous = self.db.execute("SELECT * FROM groups WHERE gid=?", (gid,)).fetchone()
        if e["kind"] not in (None, "question", "blocked") and not previous:
            return
        if not addressed and not previous:
            return
        if e["request"] and any(e["request"].startswith(other["request_prefix"])
                for project, other in self.cfg["projects"].items() if project != name):
            self.problem(mid, "request ID belongs to another project")
            return
        part, parts = (route["part"], route["parts"]) if route else (1, 1)
        problem = None if route and e["request"] and e["kind"] == "question" else "legacy or blocked question requires manager reconciliation"
        framing = json.dumps({"kind": e["kind"], "to": route["to"] if route else None,
                              "auto": route.get("auto") if route else None}, sort_keys=True)
        context = json.dumps({"assistant": self.cfg["assistant"], "owner": self.cfg["owner"],
                              "alias": self.cfg["alias"], "manager": p["manager"], "recipient": p["recipient"]})
        if route:
            for raw in self.db.execute("SELECT payload FROM messages WHERE account=? AND target=? AND message_key=?",
                                       (e["account"], e["target"], raw_key)):
                prior = json.loads(raw[0])
                pr = prior["routing"]
                if (not pr or prior.get("routing_error") or prior["kind"] != e["kind"]
                        or prior["request"] != e["request"] or pr["parts"] != parts
                        or pr["to"] != route["to"] or pr.get("auto") != route.get("auto")):
                    problem = "multipart question framing conflicts"
        if previous and (previous["parts"] != parts or previous["request"] != e["request"]
                         or previous["framing"] != framing):
            problem = "multipart question framing conflicts"
        fragment = self.db.execute("SELECT body FROM fragments WHERE gid=? AND part=?", (gid, part)).fetchone()
        if fragment and fragment[0] != e["body"]:
            problem = "multipart question content conflicts"
        self.db.execute("INSERT OR IGNORE INTO groups VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (gid, name, e["target"], e["account"], e["request"], parts,
                         mid if part == 1 else None, problem, framing, context))
        if problem:
            self.quarantine_group(gid, problem)
        self.db.execute("INSERT OR IGNORE INTO fragments VALUES(?,?,?,?)", (gid, part, mid, e["body"]))
        if part == 1:
            self.db.execute("UPDATE groups SET root=COALESCE(root,?) WHERE gid=?", (mid, gid))
        group = self.db.execute("SELECT * FROM groups WHERE gid=?", (gid,)).fetchone()
        count = self.db.execute("SELECT count(*) FROM fragments WHERE gid=?", (gid,)).fetchone()[0]
        if group["root"] and (count == group["parts"] or group["problem"]):
            self.db.execute("INSERT OR IGNORE INTO items(id,gid,status) VALUES(?,?,?)",
                            (group["root"], gid, "quarantined" if group["problem"] else "new"))

    def row(self, item):
        return self.db.execute("SELECT i.*,g.project,g.target,g.request,g.problem,g.context FROM items i JOIN groups g USING(gid) WHERE id=?", (item,)).fetchone()

    def conflict(self, row, detail):
        self.problem(row["id"], detail)
        if row["status"] != "evidence_conflict":
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?)",
                            (self.clock(), row["id"], "evidence_conflict", row["status"] + ": " + detail))
        self.db.execute("UPDATE items SET status='evidence_conflict' WHERE id=?", (row["id"],))

    def resolution_evidence(self, row, messages):
        context = json.loads(row["context"])
        related = [m for m in messages if m["account"] in (context["manager"], context["owner"])
                   and m["target"] == row["target"]]
        def references(m):
            return m.get("reply_to") == row["id"] and m["request"] == row["request"] and m["kind"] in ("answer", "decision")
        groups = {}
        for m in related:
            key = m.get("tags", {}).get("+plateia/key")
            if isinstance(key, str):
                groups.setdefault((m["account"], key), []).append(m)
        complete = False
        for group in groups.values():
            if any(references(m) for m in group):
                copies, conflict = complete_copies(group, references)
                if conflict:
                    return False, True, conflict
                complete |= bool(copies)
        return complete, any(references(m) for m in related), None

    def reconcile(self):
        messages = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM messages ORDER BY rowid")]
        acks = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM acks")]
        for ident in [r[0] for r in self.db.execute("SELECT id FROM items WHERE status NOT IN ('resolved','quarantined')")]:
            row = self.row(ident)
            context = json.loads(row["context"])
            resolved, pending_resolution, conflict = self.resolution_evidence(row, messages)
            if conflict:
                self.conflict(row, conflict)
                continue
            if resolved:
                self.db.execute("DELETE FROM problems WHERE identity IN (?,?)", (ident, ident + ':resolution'))
                self.db.execute("UPDATE items SET status='resolved' WHERE id=?", (ident,))
                continue
            if pending_resolution:
                self.problem(ident + ':resolution', 'incomplete or unframed resolution; automatic handling suspended')
            if row["status"] == "sending" and row["claim_until"] <= self.clock():
                self.db.execute("UPDATE items SET status='uncertain' WHERE id=?", (ident,))
            if not row["attempts"]:
                continue
            decision = json.loads(row["decision"])
            # Preserve every physical boundary from this sender/target/action.
            # Validate correlation and markers only after retaining the sequence.
            replies = [m for m in messages if m["account"] == context["assistant"]
                and m["target"] == row["target"]
                and m.get("tags", {}).get("+plateia/key") == row["action"]]
            def valid_reply(m):
                return (m.get("reply_to") == ident and m["request"] == row["request"]
                        and m["kind"] == "answer" and m["routing"]["to"] == [context["recipient"]]
                        and m["routing"].get("auto") == decision["kind"])
            copies, conflict = complete_copies(replies, valid_reply)
            if conflict:
                self.conflict(row, conflict)
                continue
            if not copies or row["status"] == "evidence_conflict":
                continue
            accepted = {a["msgid"] for a in acks if a["account"] == context["manager"] and a["target"] == row["target"]}
            accepted |= {m.get("reply_to") for m in messages if m["account"] == context["manager"]
                         and m["target"] == row["target"] and not m.get("routing_error")}
            status = "delivered"
            if any({m["msgid"] for m in copy} <= accepted for copy in copies):
                status = "waiting_owner" if decision["kind"] == "escalate" else "accepted"
            self.db.execute("UPDATE items SET status=? WHERE id=?", (status, ident))

    def refresh(self):
        self.read()
        self.reconcile()

    def poll(self, limit=5):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise Blocked("poll limit must be from 1 through 20")
        with self.operation():
            self.refresh()
            items = []
            for ident in [r[0] for r in self.db.execute("SELECT id FROM items WHERE status NOT IN ('accepted','resolved') ORDER BY rowid")]:
                row = dict(self.row(ident))
                row["text"] = "\n".join(r[0] for r in self.db.execute("SELECT body FROM fragments WHERE gid=? ORDER BY part", (row["gid"],)))
                suspended = self.db.execute("SELECT 1 FROM problems WHERE identity=?", (ident + ':resolution',)).fetchone()
                row["claimable"] = not suspended and (row["status"] in ("new", "send_failed") or (
                    row["status"] == "claimed" and row["claim_until"] <= self.clock()))
                row.pop("claim", None)  # only claim() returns the bearer token

                items.append(row)
            items.sort(key=lambda i: not i["claimable"])
            result = {"items": items[:limit], "remaining": max(0, len(items)-limit),
                      "incomplete": self.db.execute("SELECT count(*) FROM groups WHERE gid NOT IN (SELECT gid FROM items)").fetchone()[0],
                      "problems": [dict(r) for r in self.db.execute("SELECT * FROM problems")]}
            digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
            prior = self.db.execute("SELECT value FROM meta WHERE key='last_poll'").fetchone()
            result["changed"] = not prior or prior[0] != digest
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('last_poll',?)", (digest,))
            return result

    def claim(self, ident):
        with self.operation():
            self.refresh()
            row = self.row(ident)
            if self.db.execute("SELECT 1 FROM problems WHERE identity=?", (ident + ':resolution',)).fetchone():
                raise Blocked("resolution evidence requires reconciliation")
            if not row or not (row["status"] in ("new", "send_failed") or
                    row["status"] == "claimed" and row["claim_until"] <= self.clock()):
                raise Blocked("item is not claimable")
            token = uuid.uuid4().hex
            self.db.execute("UPDATE items SET status='claimed',claim=?,claim_until=?,decision=CASE WHEN attempts=0 THEN NULL ELSE decision END WHERE id=?",
                            (token, self.clock()+self.cfg["claim_seconds"], ident))
            return {"id": ident, "claim": token}

    def claimant(self, ident, token):
        row = self.row(ident)
        if not row or row["status"] != "claimed" or row["claim"] != token or row["claim_until"] <= self.clock():
            raise Blocked("missing, expired, or consumed claim")
        return row

    def prepare(self, ident, token, decision):
        with self.operation():
            self.refresh()
            row = self.claimant(ident, token)
            if not isinstance(decision, dict) or decision.get("kind") not in ("settled", "escalate") or any(
                    not isinstance(decision.get(k), str) or not decision[k].strip() for k in ("answer", "source")):
                raise Blocked("decision requires kind, answer or unresolved choice, and source evidence")
            decision = {k: decision[k] for k in ("kind", "answer", "source")}
            if self.cfg["owner"] in {m.lower() for m in re.findall(r"@([A-Za-z0-9_-]+)", json.dumps(decision))}:
                raise Blocked("reply contains an owner mention")
            if row["attempts"] and json.loads(row["decision"]) != decision:
                raise Blocked("a retried action must retain its original content")
            action = row["action"] or uuid.uuid4().hex
            self.db.execute("UPDATE items SET action=?,decision=? WHERE id=?", (action, json.dumps(decision), ident))
            return self.outgoing(self.row(ident))

    def outgoing(self, row):
        decision = json.loads(row["decision"])
        wire = tags([self.cfg["projects"][row["project"]]["recipient"]], row["action"], 1, 1, decision["kind"])
        wire["+draft/reply"] = row["id"]
        text = (f"[answer {row['request']}] Automatic routine clarification ({decision['kind']}): "
                f"{decision['answer']} Source: {decision['source']}. "
                "No new scope, solve, merge, permission, credential or security authority.")
        return {"target": row["target"], "text": text, "tags": wire}

    def send(self, ident, token):
        with self.operation():
            self.refresh()
            row = self.claimant(ident, token)
            if not row["decision"]:
                raise Blocked("prepare the decision before sending")
            # Partial/unframed resolutions suppress sends until reconciled.
            for raw in self.db.execute("SELECT payload FROM messages"):
                m = json.loads(raw[0])
                if m["account"] in (self.cfg["owner"], self.cfg["projects"][row["project"]]["manager"]) and m["target"] == row["target"] and m.get("reply_to") == ident and m["request"] == row["request"] and m["kind"] in ("answer", "decision"):
                    raise Blocked("incomplete resolution evidence; reconcile before sending")
            outgoing = self.outgoing(row)
            self.db.execute("UPDATE items SET status='sending',attempts=attempts+1 WHERE id=?", (ident,))
            self.db.commit()  # crash after this point must never cause an automatic duplicate
            if row["claim_until"] <= self.clock():
                raise Blocked("claim expired before Send; retain committed intent without dispatch")
            try:
                result = self.adapter.send(**outgoing)
            except Exception:
                if row["claim_until"] > self.clock():
                    self.db.execute("UPDATE items SET status='uncertain' WHERE id=?", (ident,))
                    self.db.commit()
                raise
            if row["claim_until"] <= self.clock():
                raise Blocked("claim expired while sending; reconcile the durable sending intent")
            outcome, proof = getattr(result, "outcome", None), getattr(result, "proof", None)
            status = {"sent": "submitted", "uncertain": "uncertain"}.get(outcome, "uncertain")
            if outcome == "failed" and isinstance(proof, str) and proof.strip():
                status = "send_failed"
            self.db.execute("UPDATE items SET status=? WHERE id=?", (status, ident))
            return {"id": ident, "status": status}

    def authorize_resend(self, ident, reason):
        """Explicit operator action only; the periodic skill must never call this."""
        if not isinstance(reason, str) or not reason.strip():
            raise Blocked("operator resend requires its explicit authorization reason")
        with self.operation():
            self.refresh()
            row = self.row(ident)
            if not row or row["status"] != "uncertain":
                raise Blocked("only an uncertain action can receive operator resend authorization")
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?)", (self.clock(), ident, "authorize_resend", reason))
            self.db.execute("UPDATE items SET status='new',claim=NULL,claim_until=NULL WHERE id=?", (ident,))
            return {"id": ident, "action": row["action"], "status": "new"}

    def repin_config(self, reason):
        """Explicit operator-only reconciliation; never abandons pending evidence."""
        if not isinstance(reason, str) or not reason.strip():
            raise Blocked("configuration reconciliation requires an operator reason")
        with self.operation():
            old = json.loads(self.db.execute("SELECT value FROM meta WHERE key='configuration_snapshot'").fetchone()[0])
            proposed = self.cfg
            try:
                self.cfg = old
                self.refresh()  # include unread old-route obligations before changing identities
            finally:
                self.cfg = proposed
            for row in self.db.execute("SELECT g.*,i.status FROM groups g LEFT JOIN items i USING(gid) WHERE i.status IS NULL OR i.status NOT IN ('accepted','resolved','quarantined')"):
                project, route = self.project(row["target"])
                context = json.loads(row["context"])
                if (project != row["project"] or not route or route["manager"] != context["manager"]
                        or route["recipient"] != context["recipient"]
                        or any(self.cfg[k] != context[k] for k in ('assistant','owner','alias'))
                        or row["request"] and any(row["request"].startswith(p["request_prefix"])
                            for n, p in self.cfg["projects"].items() if n != project)):
                    raise Blocked("new configuration changes pending question identity or routing")
            cutoffs = json.loads(self.db.execute("SELECT value FROM meta WHERE key='project_cutovers'").fetchone()[0])
            for name, route in self.cfg["projects"].items():
                before = old['projects'].get(name)
                if before is None:
                    cutoffs[name] = self.clock()
                    self.db.execute("DELETE FROM cursors WHERE stream=?", (name,))
                elif any(before.get(k) != route.get(k) for k in ('channel','prefix')):
                    raise Blocked("changing a recorded project's target selector needs a separately designed history migration")
            self.db.execute("INSERT INTO audit VALUES(?,?,?,?)", (self.clock(), None, 'repin_config',
                json.dumps({'reason': reason, 'before': old, 'after': self.cfg}, sort_keys=True)))
            self.db.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", [
                ('configuration', self.digest()), ('configuration_snapshot', json.dumps(self.cfg, sort_keys=True)),
                ('project_cutovers', json.dumps(cutoffs))])
            return {'configuration_reconciled': True, 'pending_evidence_preserved': True}
