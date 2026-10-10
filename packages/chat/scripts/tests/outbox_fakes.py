"""Fakes shared by the outbox tests (#19): a chat server that commits each
message as soon as it is written and answers the PING after it as a plan says,
an injected wall and monotonic clock, and invented accounts and channels only.
No socket, server, cmux or real home is ever touched: every test module that
imports this imports _isolation first."""
import datetime
from pathlib import Path

CFG = {"owner": "pat", "assistants": ["sam"],
       "accounts": {a: "x" for a in ("pat", "sam", "alp-solver-2", "alp-manager", "bet-solver-1", "bet-manager",
                                     "chatbridge")},
       "projects": {"alpha": {"channel": "#alpha", "prefix": "#alp-", "manager": "alp-manager"},
                    "beta": {"channel": "#beta", "prefix": "#bet-", "manager": "bet-manager"}}}
TAG = "[question alpha-20261009-1]"
# Three draft/multiline batches of about 4 KB on a server that offers them, or many lines on one that doesn't.
LONG = TAG + " " + "\n".join(f"point {i}: " + "lorem ipsum " * 25 for i in range(30))
T0 = 1791500000.0  # an invented moment in October 2026


class Crash(BaseException):
    """Stands in for a process being killed: no except clause in the code catches it."""


class Clock:
    """The wall clock and the monotonic clock, moved only by the test (or by a
    fake wait, which advances it to its deadline instead of sleeping)."""

    def __init__(self, t=T0):
        self.t = t
        self.mono_t = 1000.0

    def __call__(self):
        return self.t

    def mono(self):
        return self.mono_t

    def advance(self, seconds):
        self.t += seconds
        self.mono_t += seconds


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


class FakeServer:
    """Commits each message when its last line is written, then answers each
    `PING :round` as `plan` says (one answer per PING; "ok" once the plan is
    used up):

    - ok: the PONG;
    - timeout: nothing, until the reader's deadline;
    - late: nothing within the part's wait, then the PONG during the drain
      (late-fail and late-error: a FAIL, or an error reply, with it);
    - error: a 4xx reply, then the PONG (an error with finality);
    - fail: a FAIL refusal, then the PONG;
    - 403: no such channel, then the PONG; nothing is committed for that part;
    - crash: the reader is killed (Crash).

    A part's lines are committed when the PING after them arrives, which is
    when the server has read them all. `stall_after` answers no PING once that
    many messages are committed, and `refuse_login` makes every login of those
    accounts fail."""

    def __init__(self, clock, plan=(), multiline=True):
        self.clock, self.plan, self.multiline = clock, list(plan), multiline
        self.published, self.writes, self.logins = [], [], []
        self.stall_after = None
        self.refuse_login = set()
        self.conns = []
        self.ports = 40000

    def login(self, account, cfg=None, caps=(), **kwargs):
        if account in self.refuse_login:
            from chatlib import ChatError
            raise ChatError(f"login as {account} failed (fake)")
        self.logins.append(account)
        self.ports += 1
        conn = FakeConn(self, account, self.ports)
        self.conns.append(conn)
        return conn

    def texts(self):
        return [m["text"] for m in self.published]

    def by(self, account):
        return [m for m in self.published if m["account"] == account]


class FakeConn:
    def __init__(self, server, account, port):
        self.server, self.account, self.port = server, account, port
        self.answers, self.batch, self.closed, self.deadline = [], None, False, None
        self.caps = {"draft/multiline", "message-tags", "batch"} if server.multiline else {"message-tags"}
        self.cap_values = {"draft/multiline": "max-bytes=4096,max-lines=100"} if server.multiline else {}
        self.held = []  # lines of a part refused with 403: never committed

    def conn_id(self):
        return f"127.0.0.1:{self.port}>127.0.0.1:6667"

    def _publish(self, channel, text, tags=""):
        s = self.server
        s.published.append({"account": self.account, "channel": channel, "text": text,
                            "msgid": f"srv{len(s.published) + 1}", "at": s.clock(), "tags": tags})

    def send(self, line):
        s = self.server
        if self.closed:
            raise OSError("socket closed")
        s.writes.append((self.account, line))
        tags, rest = "", line
        if line.startswith("@"):
            tags, rest = line[1:].split(" ", 1)
        if rest.startswith("BATCH +"):
            self.batch = (rest.split()[3], [], tags)
        elif rest.startswith("BATCH -") and self.batch:
            channel, lines, btags = self.batch
            self.batch = None
            self.held.append(("batch", channel, lines, btags))
        elif rest.startswith("PRIVMSG "):
            channel, text = rest[8:].split(" :", 1)
            if "batch=" in tags and self.batch:
                self.batch[1].append((text, "draft/multiline-concat" in tags))
            else:
                self.held.append(("line", channel, text, tags))
        elif rest.startswith("TAGMSG "):
            self.held.append(("tag", rest.split()[1], "", tags))
        elif rest == "PING :round":
            answer = s.plan.pop(0) if s.plan else "ok"
            if answer == "403":
                self.held = []  # no such channel: nothing of the part was accepted
            else:
                self._commit()
            if s.stall_after is not None and len(s.published) >= s.stall_after and answer == "ok":
                answer = "timeout"
            self.answers.append(answer)

    def _commit(self):
        for kind, channel, body, tags in self.held:
            if kind == "batch":
                self._publish(channel, "".join(p if i == 0 or c else "\n" + p for i, (p, c) in enumerate(body)), tags)
            elif kind == "line":
                self._publish(channel, body, tags)
            else:
                self.server.published.append({"account": self.account, "channel": channel, "text": None,
                                              "tagmsg": tags, "msgid": None, "at": self.server.clock()})
        self.held = []

    def lines(self, timeout=None):
        answer = self.answers.pop(0) if self.answers else "timeout"
        pong = {"command": "PONG", "params": ["irc", "round"], "tags": {}, "prefix": "irc"}
        if answer == "ok":
            yield pong
            return
        if answer == "error":
            yield {"command": "479", "params": [self.account, "#alpha", "Illegal channel name"], "tags": {},
                   "prefix": "irc"}
            yield pong
            return
        if answer == "fail":
            yield {"command": "FAIL", "params": ["BATCH", "MULTILINE_MAX_BYTES", "too long"], "tags": {},
                   "prefix": "irc"}
            yield pong
            return
        if answer == "403":
            yield {"command": "403", "params": [self.account, "#alpha", "No such channel"], "tags": {},
                   "prefix": "irc"}
            yield pong
            return
        if answer == "late":  # nothing until the part's wait has passed; then, while draining, the answer
            self.answers.insert(0, "ok")
            raise TimeoutError("timed out")
        if answer == "late-fail":  # nothing within the wait; then, while draining, a FAIL and the answer
            self.answers.insert(0, "fail")
            raise TimeoutError("timed out")
        if answer == "late-error":  # nothing within the wait; then, while draining, an error reply and the answer
            self.answers.insert(0, "error")
            raise TimeoutError("timed out")
        if answer == "crash":
            raise Crash()
        raise TimeoutError("timed out")

    def shutdown(self):
        self.closed = True

    def close(self, reason=""):
        self.closed = True


# --- the harness every outbox test builds on --------------------------------------------

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

CLIENT_PID, OTHER_PID, BRIDGE_PID = 4242, 4343, 5151


class OutboxCase(unittest.TestCase):
    """One sandboxed state directory, an in-process authority the clients reach
    through LocalTransport (no socket), a fake server, an injected clock, and
    invented processes: the client (pid 4242), another client (4343) and the
    bridge (5151) on one fake process table."""

    multiline = True

    def setUp(self):
        from test_bridge import bridge, chatlib, reset_state
        import delivery_store
        self.bridge, self.chatlib, self.ds = bridge, chatlib, delivery_store
        reset_state()
        self.clock = Clock()
        self.server = FakeServer(self.clock, multiline=self.multiline)
        self.probes = delivery_store.FakeProbes(pid=BRIDGE_PID)
        self.client_probes = self.probes.view(CLIENT_PID)
        self.available = True
        self.authority = self.new_authority()
        self.patches = [
            mock.patch.object(chatlib, "login", lambda *a, **k: self.server.login(*a, **k)),
            mock.patch.object(chatlib, "_wallclock", self.clock),
            mock.patch.object(chatlib, "_mono", self.clock.mono),
            mock.patch.object(chatlib, "_sleep", self.clock.advance),
            mock.patch.object(chatlib, "silence_refusal", lambda account: None),
            mock.patch.object(chatlib, "load_config", lambda: CFG),
            mock.patch.object(chatlib, "client_factory", self.client),
            mock.patch.object(delivery_store, "PROBES", self.client_probes),
            mock.patch.object(bridge, "_now", self.clock),
        ]
        for p in self.patches:
            p.start()
        bridge._FLUSHERS.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.authority.close()
        self.bridge._FLUSHERS.clear()

    def fresh(self):
        """A clean case inside one test (for subTest loops): tear down, then set up again."""
        self.tearDown()
        self.setUp()

    # --- the authority and the bridge ---

    def new_authority(self, **kw):
        authority = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, mono=self.clock.mono,
                                      probes=self.probes, **kw)
        authority.start()
        return authority

    def restart_bridge(self):
        """The bridge process ends and a new one starts: a new authority epoch and a new pid."""
        self.authority.close()
        self.probes.world["gone"].add(self.probes.pid)
        self.probes = self.probes.view(self.probes.pid + 1)
        self.authority = self.new_authority()
        self.bridge._FLUSHERS.clear()

    def client(self):
        transport = self.ds.LocalTransport(self.authority) if self.available else None
        return self.ds.Client(transport, clock=self.clock)

    @contextlib.contextmanager
    def unavailable(self):
        self.available = False
        try:
            yield
        finally:
            self.available = True

    @contextlib.contextmanager
    def as_process(self, pid):
        """Calls made inside speak for another invented client process."""
        with mock.patch.object(self.ds, "PROBES", self.probes.view(pid)):
            yield

    def flush(self, deliveries=None):
        self.bridge.flush_outbox(CFG, authority=self.authority, deliveries=deliveries)

    def tx(self, fn):
        return self.authority.run_tx(fn)

    def sql(self, query, *args):
        return self.tx(lambda s: [dict(r) for r in s.con.execute(query, args)])

    # --- callers ---

    def pchat(self, *argv, account="alp-solver-2"):
        from test_outbox_regressions import pchat
        out = io.StringIO()
        with mock.patch.object(pchat, "who", lambda args, cfg, required=True: account), \
                mock.patch.object(pchat.identities, "remember", lambda *a, **k: None), \
                mock.patch.object(pchat.runstore, "silent_refusal", lambda *a: None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pchat.main(list(argv))
        return code, out.getvalue()

    def post(self, text, channel="#alpha", account="alp-solver-2"):
        return self.pchat("post", channel, text, account=account)[0]

    # --- state ---

    def entries(self):
        return self.tx(lambda s: [s.entry_view(r["id"]) for r in s.con.execute(
            "SELECT id FROM entries ORDER BY created_at, id")])

    def entry(self, entry_id=None):
        rows = self.entries()
        return rows[0] if entry_id is None else next(e for e in rows if e["id"] == entry_id)

    def states(self, entry=None):
        return [p["state"] for p in (entry or self.entry())["parts"]]

    def attempts(self, **where):
        rows = self.sql("SELECT * FROM attempts ORDER BY id")
        return [r for r in rows if all(r.get(k) == v for k, v in where.items())]

    def see(self, published, *, at=None, account=None, text=None, msgid=None, channel=None):
        """The bridge indexes a verified message it read (V1)."""
        entry = {"verified": True, "msgid": msgid or published["msgid"], "channel": channel or published["channel"],
                 "from": account or published["account"], "text": published["text"] if text is None else text,
                 "at": iso(published["at"] if at is None else at)}
        self.assertTrue(self.bridge.index_message(entry, self.authority))

    def cover(self, since, through, channel="#alpha"):
        self.tx(lambda s: s.con.execute(
            "INSERT INTO coverage (channel, since, through, mark) VALUES (?, ?, ?, 'msgid=x') ON CONFLICT(channel)"
            " DO UPDATE SET since = excluded.since, through = excluded.through", (channel.lower(), since, through)))

    def designate(self, account, at=0.0):
        self.tx(lambda s: s.designate(account, at))

    def dead_letters(self):
        path = self.chatlib.STATE_DIR / "dead-letters.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

    def fallback_files(self):
        d = self.chatlib.STATE_DIR / "outbox.d"
        return sorted(n for n in (d.iterdir() if d.exists() else []) if n.is_file()) if d.exists() else []

    def alerts(self):
        return self.sql("SELECT * FROM alerts ORDER BY raised_at, alert_key")


@contextlib.contextmanager
def in_process_authority():
    """An outbox authority on the sandboxed state, reached in-process by every
    client in the block, with invented processes: for tests that need only that."""
    import chatlib
    import delivery_store
    probes = delivery_store.FakeProbes(pid=BRIDGE_PID)
    authority = delivery_store.Authority(chatlib.STATE_DIR, probes=probes)
    authority.start()
    client = lambda: delivery_store.Client(delivery_store.LocalTransport(authority))  # noqa: E731
    try:
        with mock.patch.object(chatlib, "client_factory", client), \
                mock.patch.object(delivery_store, "PROBES", probes.view(CLIENT_PID)):
            yield authority
    finally:
        authority.close()


class SimFS:
    """The real files under a sandboxed root, plus a model of what reached stable
    storage: a file's content once it was fsynced, a directory's names once that
    directory was. `power_loss()` puts the real tree back to that model, dropping
    every change that was never synced (content and directory entries apart).
    Built on delivery_store.FS, so every durable step the code takes is seen."""

    def __init__(self, root):
        import os
        import delivery_store
        self.os, self.root = os, Path(root)
        base = delivery_store.FS()
        self.base = base
        self.content, self.names = {}, {}
        for dirpath, dirnames, filenames in os.walk(self.root):  # what exists now counts as durable
            self.names[dirpath] = {n: self._ino(Path(dirpath) / n) for n in dirnames + filenames
                                   if not self.ignored(n)}
            for n in filenames:
                if not self.ignored(n):
                    self.content[self._ino(Path(dirpath) / n)] = (Path(dirpath) / n).read_bytes()
        self.fail = set()  # operations to fail: "link", "create", ...

    @staticmethod
    def ignored(name):
        """SQLite keeps its own durability; locks and the socket hold no data."""
        return name.startswith(("outbox.db", "outbox.authority.lock", "outbox.flush.lock", "outbox.lock",
                                "outbox.sock"))

    def _ino(self, path):
        return self.os.stat(path).st_ino

    # the durable steps
    def fsync_fd(self, fd):
        pass

    def sync_file(self, path):
        self.content[self._ino(path)] = Path(path).read_bytes()

    def sync_dir(self, path):
        path = Path(path)
        self.names[str(path)] = {n: self._ino(path / n) for n in self.os.listdir(path) if not self.ignored(n)}

    def durable(self, path):
        self.sync_file(path)
        self.sync_dir(Path(path).parent)

    def create_excl(self, path, data):
        if "create" in self.fail:
            raise OSError("no space left (fake)")
        self.base.create_excl(path, data)
        self.content[self._ino(path)] = bytes(data)  # create_excl fsyncs its own content

    def link(self, src, dst):
        if "link" in self.fail:
            raise OSError(1, "hard links not supported (fake)")
        self.base.link(src, dst)

    def __getattr__(self, name):  # append, rename, replace, unlink, listdir, read, exists, nlink, size, mkdir...
        return getattr(self.base, name)

    def mkdir(self, path):
        try:
            self.os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        self.sync_dir(path)
        self.sync_dir(Path(path).parent)

    def power_loss(self):
        """Every unsynced change is lost: names a directory never synced vanish or
        come back; content never synced reverts. Directories themselves stay."""
        import shutil
        for dirpath in sorted(self.names, key=len):
            d = Path(dirpath)
            if not d.exists():
                continue
            durable = self.names[dirpath]
            for n in self.os.listdir(d):
                if (d / n).is_dir() or self.ignored(n):
                    continue
                if n not in durable:
                    (d / n).unlink()
            for n, ino in durable.items():
                if ino in self.content:
                    data = self.content[ino]
                    if not (d / n).exists() or (d / n).read_bytes() != data:
                        tmp = d / f".restore-{n}"
                        tmp.write_bytes(data)
                        self.os.replace(tmp, d / n)
                elif (d / n).exists() and not (d / n).is_dir():
                    (d / n).write_bytes(b"")
        del shutil
        self.__init__(self.root)  # what survives is now the durable state
