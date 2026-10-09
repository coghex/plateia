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
import contextlib
import fcntl
import json
import os
import sys
import socket
import time
import uuid
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("CHAT_CONFIG", Path.home() / ".config" / "chat" / "config.json"))
STATE_DIR = Path(os.environ.get("CHAT_STATE", Path.home() / ".local" / "state" / "chat"))
LOG_DIR = STATE_DIR / "logs"
MAX_TEXT = 400  # bytes per PRIVMSG body, well under IRC's 512-byte line limit
PART_WAIT = 30.0  # seconds the server has, from writing one part of a post, to confirm it


class ChatError(RuntimeError):
    pass


class Refused(ChatError):
    """The server rejected the post itself (FAIL); retrying it cannot help.
    Raised by post() mid-way, `parts` says which parts were already published."""
    parts = None


class PostIncomplete(ChatError):
    """A post stopped after some of its parts were written. `parts` gives each
    part's state: confirmed (the server accepted it and answered after it, in
    order), uncertain (written, at least partly, but never confirmed) or unsent.
    Queue only what is not confirmed; an uncertain part may already be in the
    channel, so it is checked against the record before any resend."""

    def __init__(self, message: str, parts: list):
        super().__init__(message)
        self.parts = parts


def _wallclock() -> float:
    return time.time()


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
                 caps: tuple[str, ...] = (), timeout: float = 10.0):
        cfg = cfg or load_config()
        self.sock = socket.create_connection((cfg.get("server", "127.0.0.1"), cfg.get("port", 6667)), timeout)
        self.buf = b""
        self.deadline = None  # while set (time.monotonic()), no read waits past it
        self.account = account
        self.caps, self.cap_values = set(), {}  # acknowledged caps; values the server offered
        want = set(caps) | ({"sasl"} if account else set())
        nick = account or f"guest{int(time.time()) % 100000}"
        if want:
            self.send("CAP LS 302")
        self.send(f"NICK {nick}")
        self.send(f"USER {nick} 0 * :{nick}")
        if want:
            self._negotiate(want, account, password)
        self._await_welcome()

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


def login(account: str, cfg: dict | None = None, caps: tuple[str, ...] = ()) -> Connection:
    cfg = cfg or load_config()
    password = cfg.get("accounts", {}).get(account)
    if not password:
        raise ChatError(f"no password for account {account!r} in {CONFIG_PATH}")
    return Connection(account, password, cfg=cfg, caps=caps)


def _round(conn: Connection, sends: list[str]) -> set[str]:
    """Send lines, then wait for the server to answer a PING after them.
    Returns the numeric replies seen in between."""
    for line in sends:
        conn.send(line)
    conn.send("PING :round")
    seen = set()
    for msg in conn.lines(10):
        if msg["command"] == "PONG" and msg["params"][-1:] == ["round"]:
            return seen
        if msg["command"].isdigit():
            seen.add(msg["command"])
        elif msg["command"] == "FAIL":  # e.g. a multiline batch over the server's limits
            seen.add("FAIL " + " ".join(msg["params"][:2]))
    return seen


def ensure_channel(conn: Connection, channel: str, topic: str | None = None) -> None:
    """Create `channel` (or join it if it exists) and make sure the bridge is
    in it, so the channel and its record outlive this connection. Idempotent.
    Leaves `conn` inside the channel."""
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


def _confirm(conn, deadline: float) -> set[str]:
    """Wait for the server's answer to the PING sent after a part. Returns the
    numerics and FAILs seen before it. Unrelated traffic does not extend the
    wait: past `deadline` it is a TimeoutError, whatever is still arriving."""
    seen = set()
    conn.deadline = deadline
    try:
        for msg in conn.lines(max(0.001, deadline - time.monotonic())):
            if msg["command"] == "PONG" and msg["params"][-1:] == ["round"]:
                return seen
            if msg["command"].isdigit():
                seen.add(msg["command"])
            elif msg["command"] == "FAIL":  # e.g. a multiline batch over the server's limits
                seen.add("FAIL " + " ".join(msg["params"][:2]))
            if time.monotonic() >= deadline:
                break
    except (OSError, ChatError):
        if not any(s.startswith("FAIL") for s in seen):
            raise
    finally:
        conn.deadline = None
    if any(s.startswith("FAIL") for s in seen):
        return seen  # a refusal already seen stands, whatever happens after it
    raise TimeoutError("timed out waiting for the server to confirm a part")


_progress_sink = None


@contextlib.contextmanager
def reporting(sink):
    """While active, post() hands every change to a post's parts to `sink`
    (the outbox flush records it durably before the next part is written)."""
    global _progress_sink
    previous, _progress_sink = _progress_sink, sink
    try:
        yield
    finally:
        _progress_sink = previous


def post(channel: str, text: str, account: str, cfg: dict | None = None, cont: str = "",
         reply_to: str | None = None, *, parts: list | None = None, on_event=None) -> int:
    """Post to a channel without joining it (channels are created without +n),
    creating the channel first if it does not exist. Returns lines sent.
    Where the server offers draft/multiline, each part goes as one batch: one
    message with one msgid. Otherwise it is split into lines.
    `reply_to` marks the post as a reply to that message id (+draft/reply).
    A silent child run's post is Refused here, below every caller.

    Parts go one at a time, each confirmed before the next is written, so a
    failure says exactly what was published: PostIncomplete (or Refused) carries
    every part's state. `parts` resumes a post already divided by an earlier
    attempt; its confirmed parts are never written again. Every state change
    goes to `on_event` (or the active reporting() sink) before the next write;
    if recording it fails, nothing more is written."""
    why = silence_refusal(account)
    if why:
        raise Refused(why)
    report = on_event or _progress_sink or (lambda _parts: None)
    conn = login(account, cfg, caps=("message-tags", "batch", "draft/multiline"))
    sent, joined = 0, False
    try:
        if parts is None:
            parts = fix_parts(text, cont, conn)
        pre = _tag_prefix({"+draft/reply": reply_to} if reply_to else None)

        def progress():
            return [dict(p) for p in parts]

        def stop(err):
            err.parts = progress()
            return err

        for n, part in enumerate(parts):
            if part["state"] == "confirmed":
                continue
            if part["state"] != "unsent":  # uncertain: the caller reconciles it first
                raise stop(PostIncomplete(f"part {n + 1} of {len(parts)} awaits a delivery check", None))
            try:
                lines = _part_lines(conn, channel, part, n, pre)
            except ChatError as err:
                raise stop(PostIncomplete(str(err), None)) from None
            for attempt in range(2):
                part.update(state="writing", written_at=_wallclock())
                report(progress())  # durable before anything of this part is written
                try:
                    for line in lines + ["PING :round"]:
                        conn.send(line)
                except OSError as err:  # how much reached the server is unknown
                    part["state"] = "uncertain"
                    raise stop(PostIncomplete(f"part {n + 1} of {len(parts)}: write failed ({err!r:.80})", None)) from None
                try:
                    replies = _confirm(conn, time.monotonic() + PART_WAIT)
                except (OSError, ChatError) as err:
                    part["state"] = "uncertain"
                    raise stop(PostIncomplete(f"part {n + 1} of {len(parts)} unconfirmed ({err!r:.80})", None)) from None
                refused = any(r.startswith("FAIL") for r in replies)  # a refusal outranks everything else
                if not refused and "403" in replies and attempt == 0 and not joined:
                    # no such channel: nothing was accepted; create it with the bridge inside, then resend
                    part["state"] = "unsent"
                    report(progress())
                    try:
                        ensure_channel(conn, channel)
                    except (OSError, ChatError) as err:
                        raise stop(PostIncomplete(f"cannot create {channel} ({err!r:.80})", None)) from None
                    joined = True
                    continue
                break
            if any(r.startswith("FAIL") for r in replies):
                part["state"] = "refused"
                report(progress())
                raise stop(Refused(f"server refused the post to {channel}: {sorted(replies)}"))
            if replies & {"403", "404", "482"}:
                part["state"] = "unsent"  # the server answered with an error: nothing was accepted
                report(progress())
                raise stop(PostIncomplete(f"cannot post to {channel} (server replied {sorted(replies)})", None))
            errors = sorted(r for r in replies if r.isdigit() and 400 <= int(r) <= 599)
            if errors:  # an error reply is never confirmation; whether anything was accepted is unknown
                part["state"] = "uncertain"
                report(progress())
                raise stop(PostIncomplete(f"part {n + 1} of {len(parts)}: server replied {errors}", None))
            part.update(state="confirmed")
            report(progress())
            sent += sum(1 for line in lines if " PRIVMSG " in line or line.startswith("PRIVMSG "))
        return sent
    except PostIncomplete as err:
        if not any(p["state"] != "unsent" for p in err.parts):
            raise ChatError(str(err)) from None  # nothing was written: a plain failure, as before
        raise
    finally:
        try:  # a failure to leave never undoes what was confirmed, or the error above
            if joined:
                conn.send(f"PART {channel}")
            conn.close("posted")
        except (OSError, ChatError):
            pass


def written(err) -> bool:
    """Whether a failed post wrote anything: then only its remainder is queued."""
    return any(p.get("state") != "unsent" for p in (getattr(err, "parts", None) or []))


def queued_entry(base: dict, err) -> dict:
    """The outbox entry to keep after a failed post: the whole post when nothing
    of it was written, as always; otherwise its parts and their states, so a
    confirmed part is never sent again."""
    if not written(err):
        return base
    parts = [{**p, "state": "uncertain"} if p["state"] == "writing" else p for p in err.parts]
    return {**base, "id": base.get("id") or uuid.uuid4().hex, "parts": parts}


def dead_letter_post(entry: dict, detail: str) -> None:
    """Record a post that won't be retried, keeping its parts and their states."""
    path = STATE_DIR / "dead-letters.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({**entry, "kind": "post", "dead_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "last_detail": detail}) + "\n")


def part_counts(parts: list) -> dict:
    counts = {"confirmed": 0, "uncertain": 0, "unsent": 0, "refused": 0}
    for p in parts:
        state = "uncertain" if p.get("state") == "writing" else p.get("state", "unsent")
        counts[state] = counts.get(state, 0) + 1
    return counts


def ack(channel: str, msgid: str, account: str, cfg: dict | None = None) -> None:
    """Acknowledge a message without posting text: a TAGMSG carrying
    +draft/reply=<msgid> and +draft/react=ack. Ergo keeps it in history."""
    conn = login(account, cfg, caps=("message-tags",))
    try:
        replies = _round(conn, [f"@+draft/reply={msgid};+draft/react=ack TAGMSG {channel}"])
        if replies & {"403", "404", "482"}:
            raise ChatError(f"cannot acknowledge in {channel} (server replied {sorted(replies)})")
    finally:
        conn.close("acked")


# --- the outbox: posts that could not be sent yet -----------------------------
# Writers append under an exclusive lock; the bridge claims the whole file by
# renaming it under the same lock, so nothing appended during a flush is lost.

OUTBOX = STATE_DIR / "outbox.jsonl"
_OUTBOX_LOCK = STATE_DIR / "outbox.lock"


@contextlib.contextmanager
def _outbox_locked():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_OUTBOX_LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def outbox_append(entries: list[dict]) -> None:
    kept = []
    for e in entries:  # a silent child run's post is never queued for later publication
        why = silence_refusal(e.get("as", "")) if e.get("text") is not None else None
        if why:
            print(f"chat: outbox entry not queued: {why}", file=sys.stderr)
        else:
            kept.append(e)
    if not kept:
        return
    with _outbox_locked(), OUTBOX.open("a") as f:
        for e in kept:
            f.write(json.dumps(e) + "\n")


def outbox_claim() -> list[Path]:
    """Move the current outbox aside for sending; returns every claimed file,
    including ones left by a flush that crashed."""
    with _outbox_locked():
        if OUTBOX.exists() and OUTBOX.stat().st_size:
            os.replace(OUTBOX, STATE_DIR / f"outbox.claimed-{time.time_ns()}.jsonl")
        return sorted(STATE_DIR.glob("outbox.claimed-*.jsonl"))


def outbox_pending() -> int:
    files = ([OUTBOX] if OUTBOX.exists() else []) + list(STATE_DIR.glob("outbox.claimed-*.jsonl"))
    return sum(1 for f in files for line in f.read_text().splitlines() if line.strip())


def outbox_summary() -> dict:
    """Pending outbox entries by what they wait for: `checking` ones have a part
    that may already be in the channel and is being checked against the record
    before any resend; `unsent` ones simply have parts still to post."""
    files = ([OUTBOX] if OUTBOX.exists() else []) + list(STATE_DIR.glob("outbox.claimed-*.jsonl"))
    summary = {"pending": 0, "checking": 0, "unsent": 0}
    for f in files:
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            summary["pending"] += 1
            try:
                parts = json.loads(line).get("parts") or []
            except (ValueError, AttributeError):
                parts = []
            counts = part_counts(parts)
            summary["checking" if counts["uncertain"] else "unsent"] += 1
    return summary


@contextlib.contextmanager
def outbox_flushing():
    """Yields True while this process alone may flush the outbox, else False."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_DIR / "outbox.flush.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
