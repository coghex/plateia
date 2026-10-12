"""Fakes shared by the writer-admission tests (#24, docs/writer_admission.md): an invented journal for the
owning operation, a byte snapshot of a state directory, a guard that fails on any blocking flock, and a base
case that holds invented maintenance on the sandboxed state. Invented operations, boots and plans only
(`activate-0a1b2c3d4e5f6071`, `boot-A`, `boot-B`); no test reads real state. Every test module that imports
this imports _isolation first."""
import contextlib
import fcntl
import hashlib
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from outbox_fakes import CLIENT_PID, Clock, Crash, OutboxCase

OPERATION = "activate-0a1b2c3d4e5f6071"
OTHER_OPERATION = "activate-9f8e7d6c5b4a3921"
PLAN = "p" * 64
KIND = "plateia-activation"


class Journal:
    """The caller's journal and fence, invented: what the owning operation (for #21, its fenced journal) hands
    to Owner. Records every verify, intent and outcome; `verifies`, `fail_intent` and `fail_outcome` make
    the fence fail."""

    def __init__(self):
        self.records = []          # ("intent" | "outcome", step, result)
        self.open = None           # the step journaled as an intent that has no outcome
        self.last = None           # the evidence of the record at the last landed write; None after a release
        self.verifies = True       # True; False; None (unknown); or an Exception to raise
        self.fail_intent = None
        self.fail_outcome = None
        self.fail_progress = None
        self.verified = []

    def verify(self, step):
        self.verified.append(step["step"])
        if isinstance(self.verifies, Exception):
            raise self.verifies
        return self.verifies

    def intent(self, step):
        if self.fail_intent is not None:
            raise self.fail_intent
        self.records.append(("intent", dict(step), None))
        self.open = dict(step)

    def progress(self, step, facts):
        if self.fail_progress is not None:
            raise self.fail_progress
        self.records.append(("progress", dict(step), dict(facts)))
        if self.open is not None:
            self.open.setdefault("progress", {}).update(facts)

    def outcome(self, step, result):
        if self.fail_outcome is not None:
            raise self.fail_outcome
        self.records.append(("outcome", dict(step), dict(result)))
        self.open = None
        if result.get("landed") and step["step"] in ("write-draining", "write-record") and result.get("evidence"):
            self.last = dict(result["evidence"])
        elif result.get("landed") and step["step"] == "release":
            self.last = None

    def evidence(self):
        return dict(self.last) if self.last else None

    def pending(self):
        return dict(self.open) if self.open else None

    def intents(self, name=None):
        return [s for kind, s, _ in self.records if kind == "intent" and (name is None or s["step"] == name)]

    def progresses(self, name=None):
        return [r for kind, s, r in self.records if kind == "progress" and (name is None or s["step"] == name)]

    def outcomes(self, name=None):
        return [r for kind, s, r in self.records if kind == "outcome" and (name is None or s["step"] == name)]


def crash_at(owner, point, nth=1):
    """Make `owner` die (a BaseException no except clause catches) at the named point between its steps, the `nth`
    time it is reached (acquisition journals two steps, the lock and the record, so its second "intent" is the
    record's)."""
    seen = []

    def hit(name):
        if name == point:
            seen.append(name)
            if len(seen) == nth:
                raise Crash(point)
    owner.point = hit


def snapshot(root, skip=()):
    """{relative path: sha256} of every regular file under `root`, and "<dir>/" for each directory:
    what a byte-identical assertion compares. `skip` names path prefixes left out."""
    root = Path(root)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        for d in dirnames:
            out[os.path.normpath(os.path.join(rel, d)) + "/"] = ""
        for f in filenames:
            p = Path(dirpath) / f
            key = os.path.normpath(os.path.join(rel, f))
            if any(key == s or key.startswith(s.rstrip("/") + "/") for s in skip):
                continue
            try:
                out[key] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError:
                out[key] = "unreadable"
    return out


def changed(before, after):
    """(created, removed, modified) paths between two snapshots."""
    return (sorted(set(after) - set(before)), sorted(set(before) - set(after)),
            sorted(k for k in set(before) & set(after) if before[k] != after[k]))


def record_fields(**over):
    """A valid maintenance/1 record, invented."""
    fields = {"v": "maintenance/1", "operation": OPERATION, "kind": KIND, "plan_sha256": PLAN, "state": "partial",
              "generation": 2, "acquired_boot": "boot-A", "boot": "boot-A", "bound_boot": None,
              "legacy": "unverified", "binding": {"release_id": "rel-1", "bootstrap_digest": None, "permits": []}}
    fields.update(over)
    return fields


def put_record(paths, fields=None, *, raw=None, mode=0o600, newline=True):
    """Write a record straight to the final name, as nothing but a test may: a hand-made record, valid or not."""
    paths.state.mkdir(parents=True, exist_ok=True)
    if raw is None:
        raw = json.dumps(record_fields() if fields is None else fields, sort_keys=True,
                         separators=(",", ":")).encode() + (b"\n" if newline else b"")
    paths.maintenance.write_bytes(raw)
    os.chmod(paths.maintenance, mode)
    return raw


@contextlib.contextmanager
def exclusive_lock(paths):
    """Hold L_adm exclusive, as the owning operation does during its one durable write, until the block ends."""
    paths.state.mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.admission_lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def shared_lock(paths):
    """Hold L_adm shared, as an open section of a writer does."""
    paths.state.mkdir(parents=True, exist_ok=True)
    fd = os.open(paths.admission_lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def only_nonblocking_flocks(ds, by_thread=None):
    """Fail on any flock that could block (I-11): every acquisition in delivery_store must be LOCK_NB. Yields
    the list of operations it saw; with `by_thread`, also appends (thread name, operation) to that list."""
    real, seen = ds.fcntl, []

    def flock(fd, op):
        if op != real.LOCK_UN and not op & real.LOCK_NB:
            raise AssertionError(f"a blocking flock ({op})")
        seen.append(op)
        if by_thread is not None:
            by_thread.append((threading.current_thread().name, op))
        return real.flock(fd, op)
    guard = SimpleNamespace(**{n: getattr(real, n) for n in dir(real) if not n.startswith("__") and n != "flock"},
                            flock=flock)
    with mock.patch.object(ds, "fcntl", guard):
        yield seen


class HeldCase(OutboxCase):
    """The outbox case, with helpers to put invented maintenance on its sandboxed state directory."""

    def setUp(self):
        super().setUp()
        self.paths = self.ds.Paths(self.chatlib.STATE_DIR)
        self.journal = Journal()
        self.mclock = Clock()  # the owner's clock: its sleep advances it, so a drain never sleeps for real

    def tearDown(self):
        # release first: closing the authority while maintenance is held is deferred, by design
        self.paths.maintenance.unlink(missing_ok=True)
        super().tearDown()

    def owner(self, journal=None, operation=OPERATION, plan=PLAN, boot="boot-A", probes=None, **kw):
        return self.ds.Owner(self.paths, operation=operation, kind=KIND, plan_sha256=plan,
                             fence=journal or self.journal, probes=probes or self.ds.FakeProbes(boot=boot, pid=7001),
                             mono=self.mclock.mono, sleep=self.mclock.advance, **kw)

    def hold(self, state="partial", owner=None):
        """Acquire (and drain, and advance) the maintenance of the invented operation up to `state`."""
        owner = owner or self.owner()
        owner.acquire()
        if state in ("partial", "full"):
            self.assertTrue(owner.drain()["drained"])
        if state == "full":
            owner.advance("full", legacy="retired-and-inhibited", boot="boot-B",
                          binding={"release_id": "rel-1", "bootstrap_digest": "sha256:" + "d" * 64,
                                   "permits": ["bridge-start"]})
        return owner

    def record(self):
        return self.ds.read_maintenance(self.paths)

    def writer(self, pid=CLIENT_PID):
        """An invented client process's identity, as it sends it in a request."""
        return self.probes.view(pid).identity("127.0.0.1:1>s")

    @staticmethod
    def create_args(entry_id, text="[status] x"):
        return dict(id=entry_id, entry={"channel": "#alpha", "as": "alp-solver-2", "text": text},
                    parts=[{"kind": "line", "lines": [[text, False]], "text": text}])

    def rows(self, sql, *args):
        """Rows read straight from the store's connection, the way a test may: run_tx is (rightly) cancelled while
        maintenance is held."""
        return [dict(r) for r in self.authority.store.con.execute(sql, args)]

    def entry_ids(self):
        return [r["id"] for r in self.rows("SELECT id FROM entries ORDER BY created_at, id")]

    def entry_now(self, entry_id):
        return self.authority.store.entry_view(entry_id)

    def state_snapshot(self, skip=()):
        return snapshot(self.chatlib.STATE_DIR, skip=skip)

    def sink(self):
        d = self.paths.refused
        return sorted(n for n in os.listdir(d) if self.ds.is_published_name(n)) if d.exists() else []

    def sink_rows(self):
        return [json.loads((self.paths.refused / n).read_text()) for n in self.sink()]

    def notice(self, text="[status] build green", account="alp-solver-2"):
        """An agent's notice, as agentcli.notify posts it. Returns what it printed to stderr."""
        import io
        import agentcli
        err = io.StringIO()
        record = {"channel": "#alpha", "name": account, "request": None}
        with mock.patch.object(agentcli.runstore, "silent_refusal", lambda *a: None), \
                contextlib.redirect_stderr(err):
            agentcli.notify(record, text)
        return err.getvalue()
