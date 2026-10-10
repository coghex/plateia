"""Minimal IRC client for the owner's local chat server (Ergo), stdlib only.

Each agent logs in with its own account over SASL PLAIN, so the server, not
the message text, says who spoke. Config and passwords live in
~/.config/chat/config.json (mode 0600):

    {"server": "127.0.0.1", "port": 6667, "owner": "<owner>", "assistants": ["<assistant>", ...],
     "ntfy": "https://ntfy.sh/<topic>",
     "accounts": {"<account>": "<password>", ...},
     "identities": {"<account>": {"role": "<role>", "project": "<project>|null"}, ...},
     "projects": {"<project>": {"channel": "#<project>", "prefix": "#<short>-",
                                "manager": "<account>", "path": "...", "repo": "<owner>/<repo>"}}}

Agents' own accounts are allocated by identities.py; see the chat skill.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import delivery_store  # noqa: E402  (the outbox's one durable authority, #19)

CONFIG_PATH = Path(os.environ.get("CHAT_CONFIG", Path.home() / ".config" / "chat" / "config.json"))
STATE_DIR = Path(os.environ.get("CHAT_STATE", Path.home() / ".local" / "state" / "chat"))
LOG_DIR = STATE_DIR / "logs"
MAX_TEXT = 400  # bytes per PRIVMSG body, well under IRC's 512-byte line limit


class ChatError(RuntimeError):
    pass


class Refused(ChatError):
    """The server rejected the post itself (FAIL); retrying it cannot help.
    Raised mid-way, `parts` says each part's state: what was already published."""
    parts = None


class Queued(ChatError):
    """The call is queued for the bridge (pchat exit 3), durably: by a queue or
    handoff the outbox authority acknowledged as committed, or else by a
    published fallback file. `parts` gives each part's state when some were
    sent; `fallback` says whether a fallback file carries it."""

    def __init__(self, message: str, parts: list | None = None, fallback: bool = False):
        super().__init__(message)
        self.parts, self.fallback = parts, fallback


def _wallclock() -> float:
    return time.time()


def _mono() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def client_factory():
    """This process's client of the bridge's outbox authority, over outbox.sock."""
    return delivery_store.Client(delivery_store.UnixTransport(STATE_DIR / "outbox.sock", mono=_mono),
                                 clock=_wallclock)


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError as e:
        raise ChatError(f"chat config not found: {CONFIG_PATH}") from e


def channel_log(channel: str) -> Path:
    return LOG_DIR / (channel.lstrip("#").lower() + ".jsonl")


def find_message(msgid: str) -> dict | None:
    """The logged message with this msgid, from any channel's record."""
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        with path.open() as f:
            for line in f:
                if msgid not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("msgid") == msgid:
                    return entry
    return None


def cited_authority(msgid: str, cfg: dict) -> tuple[dict | None, str]:
    """The verified owner or assistant post a delegation cites, or (None, why not)."""
    entry = find_message(msgid)
    if not entry:
        return None, f"no post with msgid {msgid} in the record"
    if not entry.get("verified"):
        return None, "the cited post is not from a verified account"
    if entry.get("from") not in {cfg.get("owner"), *cfg.get("assistants", [])}:
        return None, f"the cited post is from {entry.get('from')}, who has no owner authority"
    return entry, ""


def parse_line(line: str) -> dict:
    """Split one IRC line into tags, prefix, command and params."""
    msg = {"tags": {}, "prefix": "", "command": "", "params": []}
    if line.startswith("@"):
        raw, line = line[1:].split(" ", 1)
        for item in raw.split(";"):
            k, _, v = item.partition("=")
            msg["tags"][k] = v.replace("\\s", " ").replace("\\:", ";").replace("\\\\", "\\")
    if line.startswith(":"):
        msg["prefix"], line = line[1:].split(" ", 1)
    if " :" in line:
        head, trailing = line.split(" :", 1)
        parts = head.split() + [trailing]
    else:
        parts = line.split()
    msg["command"], msg["params"] = (parts[0].upper(), parts[1:]) if parts else ("", [])
    return msg


def split_text(text: str, cont: str = "") -> list[str]:
    """IRC lines: one per input line, each at most MAX_TEXT bytes, split at a
    space where possible so words stay searchable. Every line after the first,
    whether a wrapped piece or a later input line, starts with '… ' plus
    `cont` (e.g. the message's "[type request-id]" tag), so every fragment
    stays findable by request."""
    out = []
    first = True
    for line in text.splitlines() or [""]:
        while line:
            prefix = "" if first else ("… " + (cont + " " if cont else ""))
            room = MAX_TEXT - len(prefix.encode())
            if len(line.encode()) <= room:
                out.append(prefix + line)
                line = ""
                break
            cut = len(line.encode()[:room].decode(errors="ignore"))
            space = line.rfind(" ", 0, cut)
            if space > cut // 2:
                cut = space
            out.append(prefix + line[:cut].rstrip())
            line = line[cut:].lstrip()
            first = False
        if out:
            first = False
    return [line for line in out if line.strip()] or ["(empty)"]


class Connection:
    def __init__(self, account: str | None, password: str | None, *, cfg: dict | None = None,
                 caps: tuple[str, ...] = (), timeout: float = 10.0, wait: float | None = None):
        cfg = cfg or load_config()
        start = time.monotonic()
        self.sock = socket.create_connection((cfg.get("server", "127.0.0.1"), cfg.get("port", 6667)),
                                             timeout if wait is None else max(0.01, wait))
        self.buf = b""
        # While set (time.monotonic()), no read waits past it. Connecting, capability negotiation, SASL
        # and the welcome share one absolute deadline: unrelated traffic never extends it.
        self.deadline = None if wait is None else start + wait
        self.account = account
        self.caps, self.cap_values = set(), {}  # acknowledged caps; values the server offered
        want = set(caps) | ({"sasl"} if account else set())
        nick = account or f"guest{int(time.time()) % 100000}"
        try:
            if want:
                self.send("CAP LS 302")
            self.send(f"NICK {nick}")
            self.send(f"USER {nick} 0 * :{nick}")
            if want:
                self._negotiate(want, account, password)
            self._await_welcome()
        except BaseException:
            self.sock.close()
            raise
        self.deadline = None

    def conn_id(self) -> str | None:
        """The local TCP port and the server's address: this connection's identity."""
        try:
            local, peer = self.sock.getsockname(), self.sock.getpeername()
            return f"{local[0]}:{local[1]}>{peer[0]}:{peer[1]}"
        except OSError:
            return None

    def shutdown(self) -> None:
        """Close for good, first shutting the socket down: nothing more can be sent on it."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

    def send(self, line: str) -> None:
        self.sock.sendall(line.encode() + b"\r\n")

    def lines(self, timeout: float | None = None):
        self.sock.settimeout(timeout)
        while True:
            while b"\r\n" in self.buf:
                raw, self.buf = self.buf.split(b"\r\n", 1)
                msg = parse_line(raw.decode(errors="replace"))
                if msg["command"] == "PING":
                    self.send("PONG :" + (msg["params"][-1] if msg["params"] else ""))
                    continue
                yield msg
            if self.deadline is not None:
                left = self.deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("timed out")
                self.sock.settimeout(left)
            data = self.sock.recv(65536)
            if not data:
                raise ChatError("server closed the connection")
            self.buf += data

    def _negotiate(self, want, account, password):
        offered = set()
        for msg in self.lines(10):
            if msg["command"] == "CAP" and msg["params"][1:2] == ["LS"]:
                # CAP LS 302 can span several lines; collect them all.
                for c in msg["params"][-1].split():
                    name, _, value = c.partition("=")
                    offered.add(name)
                    self.cap_values[name] = value
                if msg["params"][2:3] == ["*"]:
                    continue
                missing = {"sasl"} & want - offered
                if missing:
                    raise ChatError("server does not offer SASL")
                self.send("CAP REQ :" + " ".join(sorted(want & offered)))
            elif msg["command"] == "CAP" and msg["params"][1:2] == ["ACK"]:
                self.caps |= set(msg["params"][-1].split())
                if account:
                    self.send("AUTHENTICATE PLAIN")
                else:
                    self.send("CAP END")
                    return
            elif msg["command"] == "AUTHENTICATE" and msg["params"] == ["+"]:
                token = base64.b64encode(f"{account}\0{account}\0{password}".encode()).decode()
                self.send(f"AUTHENTICATE {token}")
            elif msg["command"] == "903":
                self.send("CAP END")
                return
            elif msg["command"] in ("902", "904", "905", "906"):
                raise ChatError(f"login as {account} failed: {msg['params'][-1]}")

    def _await_welcome(self):
        for msg in self.lines(10):
            if msg["command"] == "001":
                return
            if msg["command"] in ("433", "436"):
                raise ChatError("nickname in use and not logged in")
            if msg["command"] == "ERROR":
                raise ChatError(msg["params"][-1] if msg["params"] else "server error")

    def close(self, reason: str = "bye") -> None:
        try:
            self.send(f"QUIT :{reason}")
        finally:
            self.sock.close()


def login(account: str, cfg: dict | None = None, caps: tuple[str, ...] = (), wait: float | None = None) -> Connection:
    cfg = cfg or load_config()
    password = cfg.get("accounts", {}).get(account)
    if not password:
        raise ChatError(f"no password for account {account!r} in {CONFIG_PATH}")
    return Connection(account, password, cfg=cfg, caps=caps, wait=wait)


def ensure_channel(conn: Connection, channel: str, topic: str | None = None, wait: float | None = None) -> None:
    """Create `channel` (or join it if it exists) and make sure the bridge is
    in it, so the channel and its record outlive this connection. Idempotent.
    Leaves `conn` inside the channel. With `wait`, all of it shares that one
    absolute deadline, whatever unrelated traffic arrives."""
    if wait is not None:
        conn.deadline = time.monotonic() + wait
    try:
        _ensure_channel(conn, channel, topic)
    finally:
        if wait is not None:
            conn.deadline = None


def _ensure_channel(conn, channel, topic):
    conn.send(f"JOIN {channel}")
    if topic:
        conn.send(f"TOPIC {channel} :{topic}")
    conn.send(f"NAMES {channel}")
    names = set()
    for msg in conn.lines(10):
        if msg["command"] == "353":
            names |= {n.lstrip("~&@%+").lower() for n in msg["params"][-1].split()}
        elif msg["command"] == "366":
            break
    if "chatbridge" in names:
        return
    conn.send(f"INVITE chatbridge {channel}")
    deadline = time.time() + 15
    for msg in conn.lines(15):
        if (msg["command"] == "JOIN" and msg["params"][:1] == [channel]
                and msg["prefix"].split("!")[0].lower() == "chatbridge"):
            return
        if msg["command"] == "401" or time.time() > deadline:  # bridge not connected
            raise ChatError(f"chat bridge did not join {channel}; is the chat-bridge service running?")


def multiline_batches(text: str, cont: str = "", max_bytes: int = 4096, max_lines: int = 100) -> list:
    """A post as draft/multiline batches: normally one. Each batch is a list of
    (piece, concat) lines; a line over MAX_TEXT bytes is cut, at a space where
    possible, into pieces marked concat, which rejoin exactly. A post over the
    server's limits continues in another batch, a separate message whose text
    starts with '… ' plus `cont`, so every part stays findable by request."""
    lines = text.strip("\n").split("\n")
    batches, batch, size = [], [], 0
    for line in lines:
        pieces, rest = [], line
        while len(rest.encode()) > MAX_TEXT:
            cut = len(rest.encode()[:MAX_TEXT].decode(errors="ignore"))
            space = rest.rfind(" ", 0, cut)
            cut = space if space > cut // 2 else cut
            pieces.append(rest[:cut])
            rest = rest[cut:]  # a leading space stays with the next piece
        pieces.append(rest)
        for i, piece in enumerate(pieces):
            cost = len(piece.encode()) + 1
            if batch and (size + cost > max_bytes or len(batch) >= max_lines):
                batches.append(batch)
                batch, size = [], 0
            if not batch and batches:  # a new message: tag it, and it can't continue a line
                piece = "… " + (cont + " " if cont else "") + piece.lstrip()
                cost = len(piece.encode()) + 1
            batch.append((piece, i > 0 and len(batch) > 0))
            size += cost
    return batches + [batch] if batch else batches or [[("(empty)", False)]]


def _multiline_limits(conn) -> tuple[int, int]:
    values = dict(v.partition("=")[::2] for v in conn.cap_values.get("draft/multiline", "").split(",") if v)
    return int(values.get("max-bytes") or 4096), int(values.get("max-lines") or 100)


def _tag_prefix(tags: dict | None) -> str:
    return ("@" + ";".join(f"{k}={v}" for k, v in tags.items()) + " ") if tags else ""


def silence_refusal(account: str) -> str | None:
    """Why a silent child run forbids this account's post now
    (runstore.silent_refusal), or None; None too where no run store exists."""
    try:
        import runstore
    except ImportError:
        return None
    return runstore.silent_refusal(account, os.environ)


def _joined(lines) -> str:
    """The text the channel shows for one part: what the bridge records."""
    text = ""
    for i, (piece, concat) in enumerate(lines):
        text += piece if i == 0 or concat else "\n" + piece
    return text


def fix_parts(text: str, cont: str, conn) -> list[dict]:
    """Divide a post into the parts it is published as: one draft/multiline
    batch each where the server offers it, otherwise one line each. Fixed the
    first time any part is written; retries send these same parts unchanged."""
    if "draft/multiline" in conn.caps:
        batches = [[[p, bool(c)] for p, c in b] for b in multiline_batches(text, cont, *_multiline_limits(conn))]
        kind = "multiline"
    else:
        batches = [[[line, False]] for line in split_text(text, cont)]
        kind = "line"
    return [{"kind": kind, "lines": b, "text": _joined(b), "state": "unsent"} for b in batches]


def _part_lines(conn, channel: str, part: dict, n: int, pre: str) -> list[str]:
    """The IRC lines that publish `part` unchanged on this connection, or a
    ChatError when this server can no longer carry it as it was fixed."""
    lines = part["lines"]
    if part["kind"] == "multiline" and len(lines) > 1:
        max_bytes, max_lines = _multiline_limits(conn)
        if ("draft/multiline" not in conn.caps or len(lines) > max_lines
                or sum(len(p.encode()) + 1 for p, _ in lines) > max_bytes):
            raise ChatError(f"part {n + 1} needs draft/multiline within its original limits; kept unsent")
        out = [f"{pre}BATCH +p{n} draft/multiline {channel}"]
        out += [f"@batch=p{n}" + (";draft/multiline-concat" if concat else "") + f" PRIVMSG {channel} :{piece}"
                for piece, concat in lines]
        return out + [f"BATCH -p{n}"]
    if part["kind"] == "multiline" and "draft/multiline" in conn.caps:
        return [f"{pre}BATCH +p{n} draft/multiline {channel}",
                f"@batch=p{n} PRIVMSG {channel} :{lines[0][0]}", f"BATCH -p{n}"]
    return [f"{pre}PRIVMSG {channel} :{lines[0][0]}"]


def _confirm(conn, part_deadline: float, drain_deadline: float):
    """Wait for the server's answer to the PING sent after a part: until
    `part_deadline`, then, draining, until `drain_deadline`. Unrelated traffic
    never extends either. Returns (numerics and FAILs seen, when the PONG came
    or None, whether it came only while draining). Finality is that PONG."""
    seen, drained = set(), False
    for deadline in (part_deadline, drain_deadline):
        conn.deadline = deadline
        try:
            for msg in conn.lines(max(0.001, deadline - time.monotonic())):
                if msg["command"] == "PONG" and msg["params"][-1:] == ["round"]:
                    return seen, _wallclock(), drained
                if msg["command"].isdigit():
                    seen.add(msg["command"])
                elif msg["command"] == "FAIL":  # e.g. a multiline batch over the server's limits
                    seen.add("FAIL " + " ".join(msg["params"][:2]))
        except TimeoutError:
            drained = True
            continue
        except (OSError, ChatError):
            break
        finally:
            conn.deadline = None
    return seen, None, drained


def _close(conn, joined_channel=None) -> None:
    """Shut the connection down; a failure to leave never undoes anything."""
    if conn is None:
        return
    try:
        if joined_channel:
            conn.send(f"PART {joined_channel}")
    except (OSError, ChatError):
        pass
    try:
        (getattr(conn, "shutdown", None) or conn.close)()
    except (OSError, ChatError):
        pass


def _exchange(conn, lines: list[str]):
    """Send one part's lines and PING :round, then wait for the in-order reply.
    Returns (outcome, final_at, detail, drained); an outcome other than
    confirmed, refused or rejected leaves the connection closed first."""
    try:
        for line in lines + ["PING :round"]:
            conn.send(line)
    except OSError as err:  # how much reached the server is unknown: no finality
        return "closed", None, f"write failed ({err!r:.80})", False
    replies, pong_at, drained = _confirm(conn, _mono() + delivery_store.PART_WAIT,
                                         _mono() + delivery_store.PART_WAIT + delivery_store.DRAIN_WAIT)
    if any(r.startswith("FAIL") for r in replies):  # a refusal outranks everything else
        return "refused", pong_at, f"server refused it: {sorted(replies)}", drained
    if pong_at is None:
        return "closed", None, "no answer through the drain", drained
    if replies & {"403", "404", "482"}:
        return "rejected", pong_at, f"server replied {sorted(replies)}", drained
    errors = sorted(r for r in replies if r.isdigit() and 400 <= int(r) <= 599)
    if errors:  # an error reply is never confirmation: closed, with finality
        return "closed", pong_at, f"server replied {errors}", drained
    return "confirmed", pong_at, None, drained


def _part_states(parts) -> list[dict]:
    return [{"text": p["text"], "state": p.get("state", "unsent")} for p in parts]


def part_counts(parts: list) -> dict:
    counts = {"confirmed": 0, "uncertain": 0, "unsent": 0, "refused": 0}
    for p in parts:
        state = {"inflight": "uncertain"}.get(p.get("state"), p.get("state", "unsent"))
        counts[state] = counts.get(state, 0) + 1
    return counts


class _Call:
    """One pchat post, pchat ack, agent notice or bridge announcement, with its
    one entry id X (section 3.1). Every byte of a part follows the committed
    reply to that part's attempt (I-1); a call the authority cannot record is
    queued or handed off, and only exits 3 once that is durable (I-5)."""

    def __init__(self, client, entry_id, row, origin, writer=None):
        self.client, self.id, self.row, self.origin = client, entry_id, row, origin
        self.writer = writer or delivery_store.PROBES.identity()
        self.parts, self.attestation, self.conn, self.joined = None, None, None, None

    def _ok(self, reply):
        return reply is not None and reply.get("result") in ("ok", "replay")

    def _stop(self):
        _close(self.conn, self.joined)
        self.conn = None

    def queue(self, why):
        """Q0 before any byte; if unanswered, the fallback file (section 6.4)."""
        self._stop()
        reply = self.client.call("queue", self.writer, id=self.id, entry=self.row, origin=self.origin)
        if self._ok(reply):
            raise Queued(f"{why}; queued in the outbox for the bridge to post", None)
        self.fallback(why)

    def handoff(self, why):
        """E3 after some bytes, with at most one attestation; if unanswered, the fallback file."""
        self._stop()
        reply = self.client.call("handoff", self.writer, id=self.id, attestation=self.attestation)
        if self._ok(reply):
            raise Queued(f"{why}; the rest is queued for the bridge", self.parts)
        self.fallback(why)

    def fallback(self, why):
        row = dict(self.row, id=self.id, writer=self.writer, origin=self.origin, epoch_seen=self.client.epoch,
                   attestation=self.attestation)
        try:
            delivery_store.publish_fallback(delivery_store.FS_DEFAULT, delivery_store.Paths(STATE_DIR), row,
                                            clock=_wallclock)
        except OSError as err:  # fails as today when the outbox cannot be written: nothing reported queued
            raise ChatError(f"{why}; and the outbox cannot be written ({err})") from None
        raise Queued(f"{why}; queued in the outbox for the bridge", self.parts, fallback=True)

    def record(self, n, generation, nonce, outcome, final_at, detail):
        """A2-A5 by request, retried as the same replay-safe request until
        PART_WAIT after the outcome was observed; then carried as an attested
        outcome in the handoff. Returns the reply, or raises Queued."""
        deadline = _mono() + delivery_store.PART_WAIT
        for tries in range(50):
            reply = self.client.call("outcome", self.writer, id=self.id, n=n, generation=generation,
                                     a1_nonce=nonce, outcome=outcome, final_at=final_at, detail=detail)
            if reply is not None:
                return reply
            if tries == 0:
                self._stop()  # keep the outcome in memory; send nothing more on this connection
            if _mono() >= deadline:
                break
            _sleep(0.1)
        self.attestation = {"kind": "outcome", "n": n, "generation": generation, "a1_nonce": nonce,
                            "outcome": outcome, "final_at": final_at, "detail": detail}
        self.handoff("the outbox authority did not record a part's outcome")

    def send_parts(self, channel, reply_to, pre_lines=None):
        """Every part not yet confirmed, in order, each as one attempt. Returns
        the PRIVMSG/TAGMSG lines sent; raises Queued or Refused when the call stops."""
        sent = 0
        pre = _tag_prefix({"+draft/reply": reply_to} if reply_to else None)
        for n, part in enumerate(self.parts):
            if part["state"] == "confirmed":
                continue
            if part["state"] != "unsent":  # uncertain or being written: checked against the record first
                self.handoff(f"part {n + 1} awaits a delivery check")
            generation = part.get("generation", 0)
            for round_ in range(2):  # at most one channel recreation, after a 403
                try:
                    lines = pre_lines(part) if pre_lines else _part_lines(self.conn, channel, part, n, pre)
                except ChatError as err:  # this server can no longer carry it as fixed: kept unsent
                    self.handoff(str(err))
                if self.client.restarted:  # a higher epoch: query before the next mutation
                    self.client.restarted = False
                    view = self.client.call("query", self.writer, id=self.id)
                    if view is None or not view.get("entry"):
                        self.handoff("the outbox authority restarted")
                    known = view["entry"]["parts"][n]
                    if known["state"] == "confirmed":
                        part["state"] = "confirmed"
                        break
                    if known["state"] != "unsent":
                        self.handoff("the outbox authority restarted mid-part")
                    generation = known["generation"]
                nonce = secrets.token_hex(16)
                reply = self.client.call("attempt", self.writer, id=self.id, n=n, generation=generation,
                                         a1_nonce=nonce)
                if reply is None:  # we may own an attempt we never learned of: send nothing for it (A12)
                    self.attestation = {"kind": "void", "n": n, "generation": generation + 1, "a1_nonce": nonce}
                    self.handoff(f"the outbox authority did not answer before part {n + 1}")
                if reply["result"] == "conflict":
                    self.handoff(f"part {n + 1} could not be recorded ({reply.get('detail')})")
                generation = reply["attempt"]["generation"]
                part["state"] = "inflight"
                outcome, final_at, detail, drained = _exchange(self.conn, lines)
                if outcome == "closed" and self.conn is not None:
                    self._stop()  # close before ended (A5)
                reply = self.record(n, generation, nonce, outcome, final_at, detail)
                if reply.get("result") == "conflict":  # it moved without us (A8c): it stays as recorded
                    part["state"] = "uncertain"
                    self.handoff(f"part {n + 1}: {detail or 'unconfirmed'}")
                part["state"] = {"confirmed": "confirmed", "refused": "refused", "rejected": "unsent",
                                 "closed": "uncertain"}[outcome]
                if outcome == "confirmed":
                    sent += sum(1 for x in lines if " PRIVMSG " in x or x.startswith("PRIVMSG ")
                                or " TAGMSG " in x)
                    if n < len(self.parts) - 1 and (drained or self.conn is None):
                        # after the drain, or once a failed record closed the connection, the call stops
                        self.handoff(f"part {n + 1} of {len(self.parts)} posted; the call stopped")
                    break
                if outcome == "refused":
                    self._stop()
                    err = Refused(f"server refused the post to {channel}: {detail}")
                    err.parts = _part_states(self.parts)
                    raise err
                if outcome == "rejected" and "403" in (detail or "") and round_ == 0 and not self.joined:
                    try:  # no such channel: nothing was accepted, and A4 is committed; create it, then resend
                        ensure_channel(self.conn, channel, wait=delivery_store.CHANNEL_WAIT)
                    except (OSError, ChatError) as err:
                        self.handoff(f"cannot create {channel} ({err!r:.80})")
                    self.joined = channel
                    continue
                self.handoff(f"part {n + 1} of {len(self.parts)}: {detail}")
        return sent


def post(channel: str, text: str, account: str, cfg: dict | None = None, cont: str = "",
         reply_to: str | None = None, *, origin: str = "post", entry_id: str | None = None, client=None) -> int:
    """Post to a channel without joining it (channels are created without +n),
    creating the channel first if it does not exist. Returns lines sent.
    Where the server offers draft/multiline, each part goes as one batch: one
    message with one msgid. Otherwise it is split into lines. `reply_to` marks
    the post as a reply to that message id (+draft/reply). A silent child
    run's post is Refused here, below every caller, and never queued.

    Every part's attempt is committed by the bridge's outbox authority before
    any of its bytes are sent (#19). A failure that leaves something owed
    raises Queued once it is durably queued or handed off (pchat exit 3); a
    refusal raises Refused; ChatError otherwise, with nothing reported queued."""
    why = silence_refusal(account)
    if why:
        raise Refused(why)
    row = {"kind": "post", "channel": channel, "as": account, "text": text, "cont": cont, "reply_to": reply_to,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(_wallclock()))}
    call = _Call(client or client_factory(), entry_id or uuid.uuid4().hex, row, origin)
    try:
        call.conn = login(account, cfg, caps=("message-tags", "batch", "draft/multiline"),
                          wait=delivery_store.LOGIN_WAIT)
    except (OSError, ChatError) as err:
        call.queue(f"post failed ({err})")
    try:
        call.writer = delivery_store.PROBES.identity(_conn_id(call.conn))
        call.parts = fix_parts(text, cont, call.conn)
        reply = call.client.call("create", call.writer, id=call.id, entry=row, origin=origin,
                                 parts=[{"kind": p["kind"], "lines": p["lines"], "text": p["text"]}
                                        for p in call.parts])
        if reply is None:  # E1 may have landed: Q0 by the same X hands it off instead
            call.queue("the bridge's outbox authority did not answer")
        if reply["result"] == "conflict":
            call.queue(f"the outbox authority refused the entry ({reply.get('detail')})")
        sent = call.send_parts(channel, reply_to)
        call.client.call("done", call.writer, id=call.id)  # E5; the bridge sets it too
        return sent
    finally:
        call._stop()


def _conn_id(conn):
    try:
        return conn.conn_id()
    except (AttributeError, OSError):
        return None


def ack(channel: str, msgid: str, account: str, cfg: dict | None = None, *, entry_id: str | None = None,
        client=None) -> None:
    """Acknowledge a message without posting text: a TAGMSG carrying
    +draft/reply=<msgid> and +draft/react=ack. Ergo keeps it in history. Its
    attempt is committed before the TAGMSG is sent, like any part; a failure
    raises Queued once the ack is durably queued (pchat exit 3)."""
    row = {"kind": "ack", "channel": channel, "as": account, "ack": msgid, "text": None,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(_wallclock()))}
    call = _Call(client or client_factory(), entry_id or uuid.uuid4().hex, row, "ack")
    try:
        call.conn = login(account, cfg, caps=("message-tags",), wait=delivery_store.LOGIN_WAIT)
    except (OSError, ChatError) as err:
        call.queue(f"ack failed ({err})")
    try:
        call.writer = delivery_store.PROBES.identity(_conn_id(call.conn))
        line = f"@+draft/reply={msgid};+draft/react=ack TAGMSG {channel}"
        call.parts = [{"kind": "ack", "lines": [[line, False]], "text": line, "state": "unsent"}]
        reply = call.client.call("create", call.writer, id=call.id, entry=row, origin="ack",
                                 parts=[{"kind": "ack", "lines": [[line, False]], "text": line}])
        if reply is None:
            call.queue("the bridge's outbox authority did not answer")
        if reply["result"] == "conflict":
            call.queue(f"the outbox authority refused the entry ({reply.get('detail')})")
        call.send_parts(channel, None, pre_lines=lambda part: [part["lines"][0][0]])
        call.client.call("done", call.writer, id=call.id)
    finally:
        call._stop()


class _Stopped(Exception):
    """The bridge's own attempt stopped (B owns the entry already: nothing to hand off)."""


def deliver_entry(entry: dict, cfg: dict | None, client, writer_identity, fix) -> tuple[str, dict | None]:
    """The bridge's flush of one outbox entry it owns (B as the writer): its
    parts fixed once (E4, through `fix(parts) -> bool`), then each part not yet
    confirmed as one attempt. Returns (outcome, attestation): 'done', 'refused',
    or 'waiting' (stopped, still owed; with the attestation the bridge must
    still make durable, if any)."""
    account, channel = entry["account"], entry["channel"]
    conn = login(account, cfg, caps=("message-tags", "batch", "draft/multiline"), wait=delivery_store.LOGIN_WAIT)
    row = {"channel": channel, "as": account, "text": entry.get("text"), "cont": entry.get("cont") or ""}
    call = _Call(client, entry["id"], row, entry.get("origin") or "post", writer=writer_identity(_conn_id(conn)))
    call.conn = conn

    def stop(why=None):
        call._stop()
        raise _Stopped(why)
    call.handoff = call.queue = call.fallback = stop
    try:
        parts = entry.get("parts") or []
        ack_line = f"@+draft/reply={entry['ack']};+draft/react=ack TAGMSG {channel}" if entry.get("ack") else None
        if not parts:
            if ack_line:
                fresh = [{"kind": "ack", "lines": [[ack_line, False]], "text": ack_line}]
            else:
                text = entry.get("text") or ""
                if entry.get("delayed"):  # a whole queued post's marker, fixed here once
                    text = f"{text} (delayed; written {(entry.get('written') or '?')[:19]}Z)"
                fresh = [{"kind": p["kind"], "lines": p["lines"], "text": p["text"]}
                         for p in fix_parts(text, entry.get("cont") or "", conn)]
            if not fix(fresh):
                return "waiting", None
            parts = [dict(p, state="unsent", generation=0) for p in fresh]
        call.parts = [dict(p) for p in parts]
        if any(p["state"] not in ("unsent", "confirmed") for p in call.parts):
            return "waiting", None  # a part awaits a delivery check, or is being written
        if ack_line:
            call.send_parts(channel, None, pre_lines=lambda part: [part["lines"][0][0]])
        else:
            call.send_parts(channel, entry.get("reply_to"))
        client.call("done", call.writer, id=entry["id"])
        return "done", None
    except Refused:
        return "refused", None
    except _Stopped:
        return "waiting", call.attestation
    finally:
        call._stop()


def register(account: str, password: str, cfg: dict | None = None) -> str:
    """Register an account through NickServ."""
    conn = Connection(None, None, cfg=cfg)
    try:
        conn.send(f"NICK {account}")
        conn.send(f"PRIVMSG NickServ :REGISTER {password}")
        for msg in conn.lines(10):
            if msg["command"] == "NOTICE" and msg["prefix"].lower().startswith("nickserv"):
                return msg["params"][-1]
    finally:
        conn.close()
    return ""
