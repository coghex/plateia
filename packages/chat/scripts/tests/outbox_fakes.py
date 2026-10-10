"""Fakes shared by the outbox tests (#19): a chat server that commits each
message as soon as it is written and answers the PING after it as a plan says,
an injected wall and monotonic clock, and invented accounts and channels only.
No socket, server, cmux or real home is ever touched: every test module that
imports this imports _isolation first."""
import datetime

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
    - late: nothing within the part's wait, then the PONG during the drain;
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
        if answer == "crash":
            raise Crash()
        raise TimeoutError("timed out")

    def shutdown(self):
        self.closed = True

    def close(self, reason=""):
        self.closed = True
