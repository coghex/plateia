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
import stat
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
ADMIT_WAIT = 2.0         # a shared L_adm acquisition that misses this long is held maintenance (writer admission 4.2)
ADMIT_POLL = 0.05        # between non-blocking tries of L_adm
DRAIN_MAX = 300.0        # the owning operation's bound on one drain: longer than any bounded section (4.4)
HOLD_RECHECK = 10.0      # how often a held bridge reads the maintenance record again (4.6)
MAINT_MAX = 64 << 10     # largest maintenance record

PROTOCOL = "outbox-authority/1"
DB_FORMAT = "outbox-db/1"
FALLBACK_FORMAT = "outbox/2"
MAINT_FORMAT = "maintenance/1"
WRITER_MODEL = "bridge-exclusive/1"
SOCKET_LIMIT = 103 if sys.platform == "darwin" else 107  # bytes of an AF_UNIX path
RESULTS = ("ok", "replay", "conflict", "refused", "busy", "error", "maintenance")
CLIENT_OPS = ("create", "queue", "attempt", "outcome", "retire", "void", "handoff", "done", "query", "status")
READ_OPS = ("query", "status")  # answered in every maintenance state, and never inside a section (writer admission 6.4)
OUTCOMES = {"confirmed": "confirmed", "refused": "refused", "rejected": "rejected", "closed": "ended"}


CANCELLED = object()  # an in-process request that never began (or no authority runs): nothing committed


class Unavailable(Exception):
    """The authority cannot run: its lock is held, its database is refused or busy, or storage failed."""


class Conflict(Exception):
    """A request's guard failed. Nothing changed."""


def now_iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def parse_iso(s):
    """Seconds since the epoch of an ISO-8601 server time with its zone, or None:
    missing, unreadable, or without a zone (whose meaning would be a guess)."""
    import datetime
    if not isinstance(s, str) or not s:
        return None
    try:
        t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t.timestamp() if t.tzinfo is not None else None


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
        self.maintenance = state / "maintenance.json"          # maintenance/1: the record (writer admission 4.3)
        self.admission_lock = state / "admission.lock"         # L_adm, content-free (4.2)
        self.refused = state / "refused.d"                     # the refusal sink: outbox/2 files (5)
        self.refused_staging = state / "refused.d" / "tmp"


# --- durable files (section 2.1) -------------------------------------------------

class FS:
    """Every file operation the durability proofs rely on. "fsync" is the
    platform's full flush; a durable sync is the file, then its directory."""

    # F_FULLFSYNC errors that mean "this filesystem or descriptor does not
    # support the full flush" (code review CODE16), so a plain fsync is the
    # strongest flush there is: ENOTSUP/EOPNOTSUPP (a filesystem without it,
    # such as some network and FUSE mounts), EINVAL (the request is invalid for
    # this descriptor's filesystem) and ENOTTY (an fcntl the file does not
    # handle). Every other error, EIO first, is a failed flush: it propagates,
    # and what it was to make durable never counts as durable.
    FULLFSYNC_UNSUPPORTED = frozenset({errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL, errno.ENOTTY})

    def fsync_fd(self, fd):
        if sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC"):
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                return
            except OSError as err:
                if err.errno not in self.FULLFSYNC_UNSUPPORTED:
                    raise
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

# Where the process table is /proc, it is the only source read, so two readings
# of one process (its own, when it records its identity, and the bridge's
# probe) always come from the same source and compare. Elsewhere, ps and lsof.
HAS_PROC = os.path.isdir("/proc/self")
UNKNOWN = ("", "x", "None", "unknown")  # how a value that could not be read is written


def known(value):
    """A boot id or start time that was read, or None for one that was not."""
    return value if isinstance(value, str) and value not in UNKNOWN else None


def valid_pid(pid):
    return isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid < 2 ** 31


def conn_port(conn):
    """The local port of a recorded connection identity ("host:port>peer"), or None."""
    if not isinstance(conn, str) or ">" not in conn:
        return None
    try:
        port = int(conn.split(">", 1)[0].rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _boot_id():
    """The boot id (a read-only host interface), or None when it cannot be read."""
    if HAS_PROC:
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or None
        except OSError:
            return None
    try:
        r = subprocess.run(["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # an unreadable probe proves nothing
        return None


def _process_start(pid):
    """A process's start time (the process table), or None when it cannot be read."""
    if HAS_PROC:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            return stat.rsplit(")", 1)[1].split()[19]  # field 22: start time in clock ticks since boot
        except (OSError, IndexError):
            return None
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:  # an unreadable probe proves nothing
        return None


def _tcp_ports(pid):
    """The local ports of `pid`'s TCP sockets, or None when that can't be read."""
    if HAS_PROC:
        proc = Path(f"/proc/{pid}")
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
    if r.returncode not in (0, 1) or (r.returncode == 1 and r.stderr.strip()):
        return None  # lsof exits 1 for "nothing found" and for its own errors alike; only a quiet 1 is "none"
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
        """True only on proof: a boot id, both known, that differs; no such
        process; or a start time, both known, that differs. A value either
        side could not read, or a malformed identity, proves nothing (None)."""
        if not isinstance(writer, dict) or not valid_pid(writer.get("pid")):
            return None
        boot, mine = known(writer.get("boot")), known(self.boot())
        if boot and mine and boot != mine:
            return True
        pid = writer["pid"]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # it exists, as another user's
        except OSError:
            return None
        start, now = known(writer.get("start")), known(_process_start(pid))
        if start is None or now is None:
            return None
        return start != now

    def conn_closed(self, writer):
        if not isinstance(writer, dict) or self.gone(writer) is not False:
            return None
        port = conn_port(writer.get("conn"))
        if port is None:
            return None
        ports = _tcp_ports(writer["pid"])
        if ports is None:
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
        """As HostProbes.gone: only values known on both sides are compared."""
        if self.world["unreadable"] or not isinstance(writer, dict) or not valid_pid(writer.get("pid")):
            return None
        boot = known(writer.get("boot"))
        if (boot and boot != self._boot) or writer["pid"] in self.world["gone"]:
            return True
        start = known(writer.get("start"))
        if start is None:
            return None
        return start != self.world["starts"].get(writer["pid"], "start-1")

    def conn_closed(self, writer):
        if self.world["unreadable"] or not isinstance(writer, dict) or not writer.get("conn"):
            return None
        if self.gone(writer) is not False:
            return None
        return writer["conn"] in self.world["closed"]


PROBES = HostProbes()  # this process's probes; the tests replace them with FakeProbes
FS_DEFAULT = FS()


def same_writer(a, b):
    """The same flow: boot id, pid and process start time (the connection may differ per attempt)."""
    if not a or not b:
        return False
    return (a.get("boot"), a.get("pid"), a.get("start")) == (b.get("boot"), b.get("pid"), b.get("start"))


# --- what every untrusted row and request must be, before any of it is used ------------

ENTRY_STRINGS = ("text", "cont", "reply_to", "ack", "at")
LEGACY_FIELDS = ("channel", "as") + ENTRY_STRINGS
PART_KINDS = ("line", "multiline", "ack")


def valid_name(value):
    """A channel or account as it goes on an IRC command line: a non-empty string
    without spaces, commas or control characters."""
    return (isinstance(value, str) and 0 < len(value) <= 200
            and not any(c.isspace() or c == "," or ord(c) < 32 or ord(c) == 127 for c in value))


def valid_id(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and all(c.isalnum() or c in "_-" for c in value)


def _count(value, low):
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
        and value not in (float("inf"), float("-inf"))


def _optional_str(value):
    return value is None or isinstance(value, str)


def valid_writer(writer, *, optional=True):
    """A writer identity: a valid pid; boot, start and connection strings or None."""
    if writer is None:
        return optional
    return (isinstance(writer, dict) and valid_pid(writer.get("pid"))
            and all(_optional_str(writer.get(k)) for k in ("boot", "start", "conn")))


def valid_attestation(att):
    """None, or one attestation (section 6.4) with every field of the type it is used as."""
    if att is None:
        return True
    if not isinstance(att, dict) or att.get("kind") not in ("void", "outcome"):
        return False
    if not (_count(att.get("n"), 0) and _count(att.get("generation"), 1)
            and isinstance(att.get("a1_nonce"), str) and att["a1_nonce"] and valid_writer(att.get("writer"))):
        return False
    if att["kind"] == "outcome":
        return (isinstance(att.get("outcome"), str) and att["outcome"] in OUTCOMES
                and (att.get("final_at") is None or _number(att.get("final_at"))) and _optional_str(att.get("detail")))
    return True


def valid_entry_row(row):
    """The fields an entry is made from: channel and account names, the text
    fields strings or None, a post's text or an ack's message id."""
    return (isinstance(row, dict) and valid_name(row.get("channel")) and valid_name(row.get("as"))
            and all(_optional_str(row.get(k)) for k in ENTRY_STRINGS) and row.get("kind") in (None, "post", "ack")
            and (row.get("text") is not None or bool(row.get("ack"))))


def valid_fallback(row):
    """A whole outbox/2 fallback file (section 6.4), before any part of it is used."""
    return (isinstance(row, dict) and row.get("v") == FALLBACK_FORMAT and valid_id(row.get("id"))
            and valid_entry_row(row) and valid_writer(row.get("writer")) and valid_attestation(row.get("attestation"))
            and _optional_str(row.get("origin")) and (row.get("epoch_seen") is None or _count(row.get("epoch_seen"), 0)))


def valid_parts(parts):
    """An entry's fixed parts: each a kind, its text, and its lines as [piece, concat] pairs."""
    if not isinstance(parts, list) or not parts:
        return False
    for p in parts:
        if not isinstance(p, dict) or p.get("kind") not in PART_KINDS or not isinstance(p.get("text"), str):
            return False
        lines = p.get("lines")
        if not isinstance(lines, list) or not lines or not all(
                isinstance(x, list) and len(x) == 2 and isinstance(x[0], str) and isinstance(x[1], bool) for x in lines):
            return False
    return True


def request_error(r):
    """Why a request is malformed, or None: every field an op uses, checked before
    any of it is (section 2.4.2). The writer and op are checked by the caller."""
    op = r["op"]
    if op == "status":
        return None
    if not valid_id(r.get("id")):
        return "invalid entry id"
    if op in ("create", "queue") and not valid_entry_row(r.get("entry")):
        return "invalid entry"
    if op == "create" and not valid_parts(r.get("parts")):
        return "invalid parts"
    if op in ("attempt", "outcome") and not (_count(r.get("n"), 0) and _count(r.get("generation"), 0)
                                            and isinstance(r.get("a1_nonce"), str) and r["a1_nonce"]):
        return "invalid attempt"
    if op == "outcome" and not (isinstance(r.get("outcome"), str) and (r.get("final_at") is None
                                                                       or _number(r.get("final_at")))
                                and _optional_str(r.get("detail"))):
        return "invalid outcome"
    if op == "retire" and not _count(r.get("n"), 0):
        return "invalid part"
    if op == "void" and not (isinstance(r.get("attestation"), dict)
                             and valid_attestation(dict(r["attestation"], kind="void"))):
        return "invalid attestation"
    if op == "handoff" and not valid_attestation(r.get("attestation")):
        return "invalid attestation"
    return None


# --- fallback files (sections 2.1 and 6.4) ----------------------------------------

def fallback_name(t, entry_id):
    return f"{int(t * 1e9):020d}-{entry_id}-{secrets.token_hex(4)}.json"


def is_published_name(name):
    return name.endswith(".json") and not name.startswith(".") and "-" in name


def _make_state_tree(fs, path):
    """The state directory and any missing parent, as the old outbox path's
    STATE_DIR.mkdir(parents=True) made them (the default mode): a first post
    before the bridge ever started (code review CODE14). Every level this or
    an earlier attempt may have made has its entry synced in its parent at
    every call, whether it already exists or not: an earlier attempt's sync
    may have failed (code review CODE15). Under the home directory, those are
    the levels below it. Elsewhere, an ancestor an earlier attempt made can no
    longer be told from one that was always there, so every level up to the
    filesystem's mount point is synced (code review CODE18). A level that
    cannot be made, or a sync that fails, raises: none is ever skipped."""
    path = Path(path)
    home = Path(os.path.realpath(os.path.expanduser("~")))
    inside = home in path.parents
    levels = [path]
    while True:
        parent = levels[-1].parent
        if parent == levels[-1] or parent == home:
            break
        if not inside and os.path.ismount(parent):
            break  # the mount point's own entry belongs to another filesystem; levels[-1]'s is synced in it
        levels.append(parent)
    for level in reversed(levels):  # top down
        try:
            os.mkdir(level)
        except FileExistsError:
            pass
        fs.sync_dir(level.parent)


def publish_fallback(fs, paths, row, *, clock=time.time, sink=False):
    """Publish one complete, immutable fallback file for row["id"]: exclusive
    creation under tmp/, the whole content synced, a link() that never replaces
    a file, then outbox.d/ and the state directory synced. Returns its name.
    An OSError up to those syncs means nothing was reported queued (the caller
    fails as today); after them the call is queued, and removing the tmp/
    copy is cleanup only (clean_staging removes it later, once linked).
    With `sink`, the same file goes to the refusal sink refused.d/ instead
    (writer admission 5): only the directory differs."""
    target, staging = (paths.refused, paths.refused_staging) if sink else (paths.fallback, paths.staging)
    _make_state_tree(fs, paths.state)
    fs.mkdir(target)
    fs.mkdir(staging)
    writer = row.get("writer") or {}
    data = (json.dumps({"v": FALLBACK_FORMAT, **row}, sort_keys=True) + "\n").encode()
    tag = f"{writer.get('boot', 'x')}.{writer.get('pid', os.getpid())}.{writer.get('start', 'x')}"
    tmp = staging / (tag.replace("/", "_").replace(" ", "_") + f".{secrets.token_hex(6)}.tmp")
    fs.create_excl(tmp, data)
    for _ in range(8):
        name = fallback_name(clock(), row["id"])
        try:
            fs.link(tmp, target / name)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(errno.EEXIST, "no free fallback name")
    fs.sync_dir(target)
    fs.sync_dir(paths.state)
    try:
        fs.unlink(tmp)  # cleanup only: published and durable already
    except OSError:
        pass
    return name


# --- writer admission (#24; docs/writer_admission.md) -------------------------------------
# Every new-release writer of $CHAT_STATE holds L_adm (a flock on admission.lock) SHARED for
# each section and reads the maintenance record while holding it. The owning operation
# alone writes the record, through Owner below, under a caller-supplied journal or fence.
# Nothing here ever blocks on L_adm: every acquisition is LOCK_NB, retried to a deadline
# by callers that may wait, and tried once by the authority's selector thread.

MAINT_STATES = ("draining", "partial", "full")
MAINT_LEGACY = ("unverified", "retired-and-inhibited")
MAINT_REQUIRED = ("v", "operation", "kind", "plan_sha256", "state", "generation", "acquired_boot", "boot",
                  "bound_boot", "legacy", "binding")
MAINT_INFO = ("pid", "pid_start")  # informational fields (4.3): displayed, never decided from


class Maintenance:
    """What a reader decides from the record (section 4.3): no file means admitted;
    a valid record means held, in its state; anything else (unreadable, not a
    private regular file of this user, not canonical, an unknown version, state or
    field type) is unknown, and unknown means held. A reader never writes it."""

    def __init__(self, present=False, fields=None, raw=None, stat_result=None, why=None):
        self.present, self.fields, self.raw, self.why = present, fields, raw, why
        self.ino = stat_result.st_ino if stat_result is not None else None
        self.dev = stat_result.st_dev if stat_result is not None else None
        self.ctime_ns = stat_result.st_ctime_ns if stat_result is not None else None

    @property
    def valid(self):
        return self.present and self.fields is not None

    @property
    def state(self):
        """none, draining, partial, full, or unknown."""
        if not self.present:
            return "none"
        return self.fields["state"] if self.fields is not None else "unknown"

    def describe(self):
        """The record as a reply or a status shows it: no more than operation, kind, state and generation."""
        if not self.present:
            return None
        if self.fields is None:
            return {"state": "unknown", "why": self.why}
        f = self.fields
        return {"operation": f["operation"], "kind": f["kind"], "state": f["state"], "generation": f["generation"]}

    def evidence(self):
        """The generation, exact bytes (as a digest), inode and change time of the record on disk, for the
        owning operation to journal after each write (4.3)."""
        if self.fields is None:
            return None
        return {"generation": self.fields["generation"], "sha256": hashlib.sha256(self.raw).hexdigest(),
                "ino": self.ino, "dev": self.dev, "ctime_ns": self.ctime_ns}


def _unknown(why, raw=None, stat_result=None):
    return Maintenance(True, None, raw, stat_result, why)


EVIDENCE_KEYS = ("generation", "sha256", "ino", "ctime_ns")  # what a journaled write must match; dev too, when journaled


def same_evidence(journaled, actual):
    """Whether a journaled write's evidence is exactly the record on disk: generation, exact bytes (digest), inode and
    change time, and the device when the journal kept it."""
    if not isinstance(journaled, dict) or not isinstance(actual, dict):
        return False
    keys = EVIDENCE_KEYS + (("dev",) if "dev" in journaled else ())
    return all(journaled.get(k) == actual.get(k) for k in keys)


def _record_problem(rec, raw):
    """Why a parsed record is not a valid maintenance/1 record, or None (section 4.3)."""
    if not isinstance(rec, dict):
        return "not an object"
    keys = set(rec)
    if not set(MAINT_REQUIRED) <= keys:
        return "a field is missing"
    if keys - set(MAINT_REQUIRED) - set(MAINT_INFO):
        return "an unknown field"
    if rec["v"] != MAINT_FORMAT:
        return "an unknown version"
    if not valid_id(rec["operation"]):
        return "invalid operation"
    if not all(isinstance(rec[k], str) and 0 < len(rec[k]) <= 200 for k in ("kind", "plan_sha256", "acquired_boot", "boot")):
        return "a field is not a string"
    if rec["state"] not in MAINT_STATES:
        return "an unknown state"
    if not _count(rec["generation"], 1):
        return "invalid generation"
    if rec["bound_boot"] is not None and not (isinstance(rec["bound_boot"], str) and 0 < len(rec["bound_boot"]) <= 200):
        return "invalid bound_boot"
    if rec["legacy"] not in MAINT_LEGACY:
        return "invalid legacy coverage"
    binding = rec["binding"]
    if not (isinstance(binding, dict) and set(binding) == {"release_id", "bootstrap_digest", "permits"}
            and isinstance(binding["release_id"], str) and _optional_str(binding["bootstrap_digest"])
            and isinstance(binding["permits"], list) and all(isinstance(x, str) for x in binding["permits"])):
        return "invalid binding"
    if "pid" in rec and not valid_pid(rec["pid"]):
        return "invalid pid"
    if "pid_start" in rec and not _optional_str(rec["pid_start"]):
        return "invalid pid_start"
    canonical = jdump(rec).encode()
    if raw not in (canonical, canonical + b"\n"):
        return "not canonical JSON"
    return None


def read_maintenance(paths, *, uid=None):
    """The record, read whole within 64 KiB, opened without following links (4.3).
    Never writes, removes or repairs anything."""
    try:
        fd = os.open(paths.maintenance, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return Maintenance()
    except OSError as err:
        return _unknown(f"cannot be opened ({err.strerror or type(err).__name__})")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return _unknown("not a regular file", None, st)
        if st.st_uid != (os.getuid() if uid is None else uid):
            return _unknown("another user's file", None, st)
        if st.st_mode & 0o077:
            return _unknown("not private to its owner", None, st)
        if st.st_size > MAINT_MAX:
            return _unknown("over 64 KiB", None, st)
        raw = b""
        while len(raw) <= MAINT_MAX:
            chunk = os.read(fd, MAINT_MAX + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    except OSError as err:
        return _unknown(f"cannot be read ({err.strerror or type(err).__name__})")
    finally:
        os.close(fd)
    if len(raw) > MAINT_MAX:
        return _unknown("over 64 KiB", None, st)
    try:
        rec = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _unknown("not JSON", raw, st)
    problem = _record_problem(rec, raw)
    if problem:
        return _unknown(problem, raw, st)
    return Maintenance(True, rec, raw, st)


def sink_count(paths, fs=None):
    """How many refused calls wait in refused.d/: counted by file name, never read. None when the
    directory cannot be listed, which is not zero (5.3)."""
    try:
        names = (fs or FS_DEFAULT).listdir(paths.refused)
    except OSError:
        return None
    return sum(1 for n in names if is_published_name(n))


BRIDGE_START = "bridge-start"  # what a `full` record's binding permits: the bound bridge's start (6.3)


def bound_start_problem(record, binding):
    """Why `record`, read holding L_adm shared, does not admit the operation-owned start that `binding` names
    (writer admission 6.3; #21 amendment 4, K3), or None when it does: the record is valid and `full`, it
    permits the bridge's start, and it names this launch's operation, bootstrap digest and release.
    `binding` is {"operation", "bootstrap_digest", "release_id"}."""
    if not record.valid:
        return f"the maintenance record is unknown ({record.why})"
    f = record.fields
    if f["state"] != "full":
        return f"maintenance is {f['state']}, not full"
    bound = f["binding"]
    if BRIDGE_START not in bound["permits"]:
        return "the record does not permit the bridge's start"
    if f["operation"] != binding.get("operation"):
        return "the record is another operation's"
    if bound["bootstrap_digest"] != binding.get("bootstrap_digest"):
        return "the record binds another bootstrap digest"
    if bound["release_id"] != binding.get("release_id"):
        return "the record binds another release"
    return None


class StorageError(OSError):
    """The admission lock does not exist and could not be created: the state directory cannot be written. That is
    not held maintenance (the owner creates the lock before it writes the record); the call fails as an unwritable
    outbox does today."""


def _open_admission_lock(paths, fs=None, dir_fd=None):
    """L_adm's file descriptor. The file is content-free; a writer that finds it missing creates it, empty:
    the only path of this format the chat package ever creates (4.2). A lock that exists and cannot be opened
    raises OSError (the caller reads that as held); one that cannot be created raises StorageError. With
    `dir_fd` (the bridge's activated start, #21 amendment 7) it is opened, or made, only in that directory."""
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if dir_fd is not None:
        try:
            return os.open(paths.admission_lock.name, flags, 0o600, dir_fd=dir_fd)
        except OSError as err:
            if os.path.lexists(paths.admission_lock):
                raise
            raise StorageError(err.errno, f"the admission lock cannot be created ({err.strerror or err})") from err
    try:
        try:
            return os.open(paths.admission_lock, flags, 0o600)
        except FileNotFoundError:  # a first call before the bridge ever made the state directory
            _make_state_tree(fs or FS_DEFAULT, paths.state)
            return os.open(paths.admission_lock, flags, 0o600)
    except OSError as err:
        if os.path.lexists(paths.admission_lock):
            raise  # it exists and is unusable (a link, no permission): fail closed
        raise StorageError(err.errno, f"the admission lock cannot be created ({err.strerror or err})") from err


_OPEN = threading.local()  # per thread: how many admitted sections it holds open


def in_admitted_section():
    """Whether the calling thread holds an admitted section open: one that began with no maintenance record and is
    still protected by its shared L_adm. In `draining` this is what separates an in-process request that continues
    work already begun from one that would start new work (writer admission 4.5): the table lets only the former
    run."""
    return getattr(_OPEN, "depth", 0) > 0


class Admission:
    """What entering a section found. `locked` says L_adm was held shared; `record` was read while holding it.
    Admitted means locked with no record. A lock that could not be had is held maintenance: refuse."""

    def __init__(self, locked, record):
        self.locked, self.record = locked, record

    @property
    def admitted(self):
        return self.locked and not self.record.present


@contextlib.contextmanager
def section(paths, *, fs=None, wait=None, clock=time.monotonic, sleep=time.sleep, dir_fd=None):
    """One section of a new-release writer (4.2): L_adm shared, taken without blocking and retried to an
    absolute deadline of `wait` (ADMIT_WAIT), then the record read while holding it. Yields an Admission.
    A miss, or a lock that cannot be opened, reads as held maintenance. wait=0 tries exactly once and never
    sleeps: what the authority's selector thread does."""
    wait = ADMIT_WAIT if wait is None else wait
    fd, locked, why = None, False, "the admission lock is busy"
    try:
        try:
            fd = _open_admission_lock(paths, fs, dir_fd)
        except StorageError:
            raise
        except OSError as err:
            why = f"the admission lock cannot be opened ({err.strerror or type(err).__name__})"
        if fd is not None:
            deadline = clock() + wait
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if clock() >= deadline:
                        break
                    sleep(min(ADMIT_POLL, max(0.0, deadline - clock())))
        adm = Admission(locked, read_maintenance(paths) if locked else _unknown(why))
        if adm.admitted:
            _OPEN.depth = getattr(_OPEN, "depth", 0) + 1
        try:
            yield adm
        finally:
            if adm.admitted:
                _OPEN.depth -= 1
    finally:
        if fd is not None:
            os.close(fd)  # closing the description releases its flock


class OwnerError(Exception):
    """The owning operation's primitive refused or could not finish."""


class NotOwner(OwnerError):
    """The record is absent, unknown, another operation's, or not the operation's own last journaled write."""


class FenceFailed(OwnerError):
    """The caller's journal or fence did not permit a step, or failed. It permits nothing further."""


class OutcomeNotRecorded(OwnerError):
    """The effect happened, and the caller's journal did not record its outcome. `result` is what to journal
    on resume; the pending intent stays, and the next primitive settles it from what is on disk."""

    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


class Owner:
    """The owning operation's side of writer admission: acquire, drain, advance, release and a read-only
    reconcile (4.3, 4.4, 4.7, 4.8). It is the only code that writes the record, and it writes it only for
    the fenced operation. Ordinary writers only read it.

    `fence` is the caller's journal and ownership check (for #21, its fenced journal):
      verify(step) -> True     the caller still owns the operation; anything else (False, None, an error) is
                               a failed or unknown fence, and permits no further mutation
      intent(step)             durably journal the step BEFORE its effect; an error stops it
      progress(step, facts)    durably journal facts about the pending step as its effect advances: the
                               identity (inode, device) of the temporary file before it is published, and the
                               evidence of the published record after; an error is a failed fence
      outcome(step, result)    durably journal the result AFTER it; an error leaves the intent pending
      evidence() -> dict|None  the generation, sha256, ino, dev and ctime_ns of the record at the caller's
                               last journaled write, or None when it journaled none
      pending() -> dict|None   the step journaled as an intent that has no outcome, if any, with the facts
                               journaled as its `progress`

    A step names the operation, the plan, the evidence of the record it replaces and the generation and exact
    bytes (as a digest) it writes. Verifying that legacy writers are retired and inhibited, and that targets
    are bound, stays with the caller: it does so in its own `verify` before it permits `full`."""

    def __init__(self, paths, *, operation, kind, plan_sha256, fence, fs=None, probes=None, clock=time.time,
                 mono=time.monotonic, sleep=time.sleep):
        self.paths, self.operation, self.kind, self.plan = paths, operation, kind, plan_sha256
        self.fence, self.fs = fence, fs or FS_DEFAULT
        self.probes = probes
        self.clock, self.mono, self.sleep = clock, mono, sleep
        self.failed = None  # why the fence failed: nothing further is mutated

    def point(self, name):
        """A named place between steps. It does nothing; the crash tests override it."""

    def _probes(self):
        return self.probes or PROBES

    # --- the fence ---

    def _fail(self, why, err=None):
        self.failed = why
        raise FenceFailed(why) from err

    def _live(self):
        if self.failed:
            raise FenceFailed(f"an earlier fence failure permits nothing further ({self.failed})")

    def _fenced(self, step, effect):
        """verify, journal the intent, run the effect, journal the outcome. Any failure of the fence is final."""
        self._live()
        self._verify(step)
        try:
            self.fence.intent(step)
        except Exception as err:
            self._fail(f"the intent was not journaled ({err!r:.80})", err)
        self.point("intent")

        def progress(facts):
            try:
                self.fence.progress(step, facts)
            except Exception as err:
                self._fail(f"progress was not journaled ({err!r:.80})", err)
        result = effect(progress)
        self.point("effect")
        try:
            self.fence.outcome(step, result)
        except Exception as err:
            self.failed = f"the outcome was not journaled ({err!r:.80})"
            raise OutcomeNotRecorded(self.failed, result) from err
        return result

    def _verify(self, step):
        self._live()
        try:
            ok = self.fence.verify(step)
        except Exception as err:
            self._fail(f"ownership was not verified ({err!r:.80})", err)
        if ok is not True:
            self._fail("ownership was not verified")

    def _step(self, name, **more):
        return {"step": name, "operation": self.operation, "kind": self.kind, "plan_sha256": self.plan, **more}

    # --- reading and evidence ---

    def _expect_own(self, rec):
        """The record must be this operation's, and exactly its last journaled write (4.3)."""
        if not rec.present:
            raise NotOwner("there is no maintenance record")
        if not rec.valid:
            raise NotOwner(f"the maintenance record is unknown ({rec.why}); it is never repaired or adopted")
        f = rec.fields
        if (f["operation"], f["kind"], f["plan_sha256"]) != (self.operation, self.kind, self.plan):
            raise NotOwner("the maintenance record belongs to another operation or plan")
        try:
            journaled = self.fence.evidence()
        except Exception as err:
            self._fail(f"the journal's evidence could not be read ({err!r:.80})", err)
        if not journaled:
            raise NotOwner("the journal holds no write of this record")
        actual = rec.evidence()
        for key in EVIDENCE_KEYS + (("dev",) if "dev" in journaled else ()):
            if journaled.get(key) != actual[key]:
                raise NotOwner(f"the record's {key} differs from the operation's last journaled write")
        return rec

    def _record_bytes(self, base, **changes):
        fields = dict(base)
        fields.update(changes)
        who = self._probes().identity()
        fields.update(pid=who.get("pid"), pid_start=who.get("start"))
        if fields["pid_start"] is not None and not isinstance(fields["pid_start"], str):
            fields["pid_start"] = str(fields["pid_start"])
        if not valid_pid(fields["pid"]):
            fields.pop("pid"), fields.pop("pid_start")
        problem = _record_problem(fields, jdump(fields).encode())
        if problem:
            raise OwnerError(f"the record would be invalid ({problem})")
        return fields, (jdump(fields) + "\n").encode()

    @staticmethod
    def _identity(path):
        """The inode and device of a file: what a link or a rename keeps, and so what identifies the published record."""
        st = os.stat(path)
        return {"ino": st.st_ino, "dev": st.st_dev}

    def _fresh_tmp(self):
        return f".maintenance.json.{secrets.token_hex(6)}.tmp"

    # --- settling a pending intent: a crash between an effect and its outcome (4.8) ---

    def _same_publication(self, facts, actual):
        """The record on disk, whose bytes are the journaled ones, must also be the file this step published: the
        temporary file's identity (inode and device, which a link or a rename keeps), journaled before it was
        published, and, once the step journaled it, the published record's evidence including its change time.
        Identical bytes in another file, or a changed change time, are a replacement, never adopted."""
        tmp, published = facts.get("tmp"), facts.get("published")
        if not isinstance(tmp, dict) or (tmp.get("ino"), tmp.get("dev")) != (actual["ino"], actual["dev"]):
            raise NotOwner("the record has the journaled bytes, but is not the file the operation published; a "
                           "replacement is never adopted")
        if published is not None and not same_evidence(published, actual):
            raise NotOwner("the record changed after the operation published it; it is not adopted")

    def _settle(self):
        """Close a journaled intent that has no outcome, from what is on disk, before anything else. A landed write
        is adopted only if it is the file the operation published (its journaled identity, and its exact bytes
        and generation); a record that is neither the journaled prior nor that write is a replacement, and is never
        adopted. What the recovery decides is made durable (the file and the state directory) before its outcome is
        journaled, so a power loss cannot undo an effect already recorded as complete. Removes nothing but a
        temporary file the operation journaled."""
        self._live()
        try:
            pending = self.fence.pending()
        except Exception as err:
            self._fail(f"the journal's pending step could not be read ({err!r:.80})", err)
        if not pending:
            return None
        if pending.get("operation") != self.operation or pending.get("plan_sha256") != self.plan:
            raise NotOwner("the journal's pending step is another operation's")
        rec = read_maintenance(self.paths)
        name = pending.get("step")
        facts = pending.get("progress") or {}
        if name == "create-lock":
            landed = self.paths.admission_lock.exists()
        elif name in ("write-draining", "write-record"):
            want = pending["to"]
            actual = rec.evidence()
            candidate = bool(actual and actual["sha256"] == want["sha256"]
                             and actual["generation"] == want["generation"])
            prior = pending.get("from")
            unchanged = (not rec.present) if prior is None else same_evidence(prior, actual)
            if candidate:
                self._same_publication(facts, actual)
                landed = True
            elif unchanged:
                landed = False
            else:
                raise NotOwner("the record is neither the journaled prior nor the journaled write; a replacement is "
                               "never adopted")
        elif name == "release":
            landed = not rec.present
            prior = pending.get("from")
            actual = rec.evidence()
            if not landed and not same_evidence(prior, actual):
                raise NotOwner("the record is neither the journaled prior nor absent; it is not the operation's own")
        else:
            raise NotOwner(f"the journal's pending step {name!r} is unknown")
        self._verify(pending)
        tmp = pending.get("tmp")
        if tmp and "/" not in tmp:
            with contextlib.suppress(OSError):
                self.fs.unlink(self.paths.state / tmp)  # a temporary file the operation journaled
        # durable before the outcome: the record's content, then the state directory (the link or rename, the
        # temporary file's removal, or the release's unlink). A failure leaves the intent pending, to settle again.
        with contextlib.suppress(FileNotFoundError):
            if landed and name in ("write-draining", "write-record"):
                self.fs.sync_file(self.paths.maintenance)
            self.fs.sync_dir(self.paths.state)
        result = {"landed": landed, "resumed": True}
        if landed and name in ("write-draining", "write-record"):
            result["evidence"] = read_maintenance(self.paths).evidence()
        try:
            self.fence.outcome(pending, result)
        except Exception as err:
            self.failed = f"the outcome was not journaled ({err!r:.80})"
            raise OutcomeNotRecorded(self.failed, result) from err
        return result

    # --- acquiring ---

    def create_lock(self):
        """Create admission.lock if it is missing, empty (4.4 step 2)."""
        self._settle()

        def effect(progress):
            fd = _open_admission_lock(self.paths, self.fs)
            os.close(fd)
            return {"landed": True}
        return self._fenced(self._step("create-lock"), effect)

    def acquire(self):
        """Steps 1 and 2 of acquiring: read the record; if it is present and names another operation, or is
        unknown, stop; if it names this operation, return it to resume from its state (verified as the
        operation's own); otherwise create the lock and link a `draining` record, never replacing a file."""
        self._settle()
        rec = read_maintenance(self.paths)
        if rec.present:
            return self._expect_own(rec)
        self.create_lock()
        boot = self._probes().boot()
        fields, data = self._record_bytes(
            {"v": MAINT_FORMAT, "operation": self.operation, "kind": self.kind, "plan_sha256": self.plan,
             "state": "draining", "generation": 1, "acquired_boot": boot, "boot": boot, "bound_boot": None,
             "legacy": "unverified", "binding": {"release_id": "", "bootstrap_digest": None, "permits": []}})
        tmp = self._fresh_tmp()
        step = self._step("write-draining", **{"from": None, "tmp": tmp,
                                               "to": {"state": "draining", "generation": 1,
                                                      "sha256": hashlib.sha256(data).hexdigest()}})

        def effect(progress):
            path = self.paths.state / tmp
            self.fs.create_excl(path, data)
            self.point("tmp-created")
            progress({"tmp": self._identity(path)})  # before it is published, so a recovery can tell it from another
            self.point("tmp-journaled")
            self.fs.link(path, self.paths.maintenance)  # never replaces a file
            self.point("linked")
            self.fs.sync_dir(self.paths.state)
            with contextlib.suppress(OSError):
                self.fs.unlink(path)
            self.fs.sync_dir(self.paths.state)
            evidence = read_maintenance(self.paths).evidence()
            self.point("published")
            progress({"published": evidence})
            return {"landed": True, "evidence": evidence}
        self._fenced(step, effect)
        return read_maintenance(self.paths)

    # --- the exclusive drain, and every later record write (4.4) ---

    @contextlib.contextmanager
    def _exclusive(self, wait):
        """L_adm exclusive, without blocking, every ADMIT_POLL until `wait`. Yields (held, tries)."""
        fd = _open_admission_lock(self.paths, self.fs)
        try:
            end, tries, held = self.mono() + wait, 0, False
            while True:
                tries += 1
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                    break
                except BlockingIOError:
                    if self.mono() >= end:
                        break
                    self.sleep(min(ADMIT_POLL, max(0.0, end - self.mono())))
            if held:
                self.point("locked")
            yield held, tries
        finally:
            os.close(fd)

    def advance(self, state, *, wait=DRAIN_MAX, boot=None, bound_boot=None, set_bound_boot=False, legacy=None,
                binding=None):
        """Write the next record while holding L_adm exclusive: `partial` (the drain, from `draining`, or again
        from `partial` in a new boot), `full`, or back to `partial`. The exclusive lock is tried without blocking
        until `wait`; the step is journaled first, so the lock is held only for one durable write. Returns
        a result with drained False, and writes nothing, if the lock could not be had: the record stays as it is
        and the drain is reported incomplete. Retirement, inhibition and targets are the caller's to verify,
        in its `verify`, before it asks for `full`."""
        self._settle()
        rec = self._expect_own(read_maintenance(self.paths))
        cur = rec.fields
        if (cur["state"], state) not in (("draining", "partial"), ("partial", "partial"), ("partial", "full"),
                                         ("full", "partial")):
            raise OwnerError(f"the record cannot go from {cur['state']} to {state}")
        boot = boot if boot is not None else self._probes().boot()
        if state == "full" and known(boot) is None:
            raise OwnerError("the boot cannot be read; full is refused as unknown")
        changes = {"state": state, "generation": cur["generation"] + 1, "boot": boot}
        if set_bound_boot:
            changes["bound_boot"] = bound_boot
        if state == "partial" and cur["state"] == "full":
            changes.update(legacy="unverified")
        if legacy is not None:
            changes["legacy"] = legacy
        if binding is not None:
            changes["binding"] = binding
        fields, data = self._record_bytes(cur, **changes)
        tmp = self._fresh_tmp()
        step = self._step("write-record", **{"from": rec.evidence(), "tmp": tmp, "wait": wait,
                                             "to": {"state": state, "generation": fields["generation"],
                                                    "sha256": hashlib.sha256(data).hexdigest()}})

        def effect(progress):
            path = self.paths.state / tmp
            self.fs.create_excl(path, data)  # before the lock: it is held only for the rename and its sync
            self.point("tmp-created")
            progress({"tmp": self._identity(path)})
            self.point("tmp-journaled")
            with self._exclusive(wait) as (held, tries):
                if not held:  # hold nothing, leave the record as it is: the drain is incomplete (4.4)
                    with contextlib.suppress(OSError):
                        self.fs.unlink(path)
                    self.fs.sync_dir(self.paths.state)
                    return {"landed": False, "drained": False, "attempts": tries}
                self._expect_own(read_maintenance(self.paths))  # still exactly the journaled record, under the lock
                self.fs.replace(path, self.paths.maintenance)
                self.point("renamed")
                self.fs.sync_dir(self.paths.state)
                self.point("written")
            evidence = read_maintenance(self.paths).evidence()
            self.point("published")
            progress({"published": evidence})
            return {"landed": True, "drained": True, "attempts": tries, "evidence": evidence}
        return self._fenced(step, effect)

    def drain(self, *, wait=DRAIN_MAX, boot=None):
        """Drain to `partial`: no section of any new-release writer is open, and none can open."""
        return self.advance("partial", wait=wait, boot=boot)

    # --- release (4.7) ---

    def release(self):
        """Unlink the record. The unlink is the moment of release: the next section of every writer is
        admitted, whether or not the outcome is journaled."""
        settled = self._settle()
        rec = read_maintenance(self.paths)
        if settled is not None and settled.get("landed") and not rec.present:
            return settled  # a completed release, found on resume
        self._expect_own(rec)
        step = self._step("release", **{"from": rec.evidence()})

        def effect(progress):
            self.fs.unlink(self.paths.maintenance)
            self.point("unlinked")
            self.fs.sync_dir(self.paths.state)
            return {"landed": True}
        return self._fenced(step, effect)

    # --- reconcile (4.8): read-only ---

    def reconcile(self):
        """Report; write and clean nothing. The record, whether it is exactly the operation's last journaled write,
        whether the acting process is gone, the sink's count, temporary files (which of them the operation
        journaled), a step pending in the journal, and the next permitted step."""
        rec = read_maintenance(self.paths)
        pending = self.fence.pending()
        journaled = self.fence.evidence()
        matches = None
        if rec.valid and journaled:
            actual = rec.evidence()
            matches = same_evidence(journaled, actual)
        gone = None
        if rec.valid and "pid" in rec.fields:
            gone = self._probes().gone({"boot": rec.fields["boot"], "pid": rec.fields["pid"],
                                        "start": rec.fields.get("pid_start")})
        try:
            temps = sorted(n for n in os.listdir(self.paths.state) if n.startswith(".maintenance.json.")
                           and n.endswith(".tmp"))
        except OSError:
            temps = None
        mine = {pending.get("tmp")} if pending and pending.get("tmp") else set()
        if not rec.present:
            nxt = "release-completed" if pending and pending.get("step") == "release" else "acquire"
        elif not rec.valid:
            nxt = "none: the record is unknown; repair is an owner decision"
        elif not rec.fields["operation"] == self.operation:
            nxt = "none: another operation's record"
        else:
            nxt = {"draining": "drain", "partial": "advance to full or release",
                   "full": "post-check, then release"}[rec.fields["state"]]
        if pending:
            nxt = f"settle the pending {pending.get('step')} step"
        return {"record": rec.describe(), "fields": dict(rec.fields) if rec.valid else None,
                "matches_journal": matches, "acting_process_gone": gone, "refused_files": sink_count(self.paths, self.fs),
                "pending": pending, "journaled_temp_files": sorted(set(temps or []) & mine),
                "unjournaled_temp_files": sorted(set(temps or []) - mine), "next": nxt}


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
        self.collect_after = None  # (at, msgid) where the next collection resumes; None from the oldest

    # --- opening (section 2.4.1) ---

    def open(self):
        """All or nothing: on any failure, the database files' durable sync
        included, the connection is closed and the error raised."""
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
            for name in ("outbox.db", "outbox.db-wal"):  # the database's own files are durable too
                if self.fs.exists(self.paths.state / name):
                    self.fs.durable(self.paths.state / name)
        except sqlite3.Error as err:  # busy, locked by another opener, not a database, unreadable
            con.close()
            self.con = None
            raise Unavailable(f"database busy or unreadable: {err}") from None
        except BaseException:
            con.close()
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
        """A read-only transaction (a deferred BEGIN, never committed): it takes no write lock and
        changes no database or WAL file, for `query` and `status` in every maintenance state (writer
        admission 6.4)."""
        self.con.execute("BEGIN")
        try:
            yield self.con
        finally:
            self._rollback()

    def _rollback(self):
        """Never leave the connection inside a transaction: a failed statement or
        COMMIT may or may not have rolled it back already (SQLite's "may or may not")."""
        try:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
        except sqlite3.Error:
            pass

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
        if not valid_fallback(row):  # the whole file, before any part of it is used
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

    def _hold_row(self, name, ordinal, raw, why):
        """One held row per (file, ordinal), or per (file, content, why) for a
        whole file: UNIQUE never matches a NULL ordinal, so that is checked here."""
        if ordinal is None and self.con.execute("SELECT 1 FROM held WHERE file = ? AND ordinal IS NULL AND raw = ?"
                                                " AND why = ?", (name, raw, why)).fetchone():
            return
        self.con.execute("INSERT OR IGNORE INTO held (file, ordinal, raw, why) VALUES (?, ?, ?, ?)",
                         (name, ordinal, raw, why))

    def hold(self, name, ordinal, raw, why):
        self._hold_row(name, ordinal, raw if isinstance(raw, str) else str(raw), why)
        self.raise_alert(f"held:{name}", "held", name,
                         "queued outbox rows could not be imported and are held; see pchat status")

    def hold_changed(self, name, sha, data=None):
        """A file changed after its import began: held, with its exact bytes when
        they are given (a fallback file, removed only after this commits; owner
        amendment 6, F2(b)), or by its sha256 (a legacy claim, which is never
        removed). Never merged or replayed."""
        self._hold_row(name, None, data if data is not None else sha,
                       "changed after import began" + (f" ({sha})" if data is not None else ""))
        self.raise_alert(f"held:{name}:{sha}", "held", name,
                         "queued outbox rows could not be imported and are held; see pchat status")

    def import_legacy(self, name, start_ordinal, lines, new_offset, prefix_sha, close=False, unterminated=None):
        """I2: complete legacy lines of claim `name` from `start_ordinal`, and the
        claim's imports row advanced (or closed), in one transaction. At the
        final read, an `unterminated` last line is held as unreadable, never
        imported, even when it is valid JSON (section 6.3)."""
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
            except (ValueError, RecursionError):
                row = None
            clean = {k: row[k] for k in LEGACY_FIELDS if k in row} if isinstance(row, dict) else None
            ok = (clean is not None and not any(k in row for k in ("parts", "terminal", "id", "fallback", "v"))
                  and valid_entry_row(clean)
                  and (isinstance(clean.get("text"), str) or isinstance(clean.get("ack"), str)))
            if not ok:
                self.hold(name, i, raw if isinstance(raw, str) else raw.decode(errors="replace"),
                          "unsupported or unreadable legacy row")
                continue
            if self.entry(occurrence) is None:
                self._insert_entry(occurrence, clean, owner_kind="outbox", writer=None,
                                   origin="ack" if clean.get("ack") else "legacy",
                                   occ=(name, i, hashlib.sha256(raw.encode() if isinstance(raw, str) else raw)
                                        .hexdigest()))
        if unterminated is not None:
            self.hold(name, start_ordinal + len(lines), unterminated, "unterminated last line: unreadable")
        count = len(lines) + (unterminated is not None)
        self.con.execute("UPDATE imports SET lines = ?, offset = ?, sha256 = ?, epoch = ?, last_import = ?,"
                         " state = ? WHERE name = ?",
                         (start_ordinal + count, new_offset, prefix_sha, epoch, self.clock(),
                          "closed" if close else "open", name))
        return count

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
        """G1: delete only what nothing depends on, at most `limit` rows. Each
        call examines at most `limit` messages, resuming after the last one the
        previous call examined, so messages that must be kept never stop newer
        ones from being examined (fairness under the bound)."""
        now = self.clock()
        horizon = now - A - GC_MARGIN
        hulls = self._dependency_hulls()
        deleted = 0
        q, args = "SELECT * FROM messages WHERE at < ?", [horizon]
        if self.collect_after is not None:
            q += " AND (at > ? OR (at = ? AND msgid > ?))"
            args += [self.collect_after[0], self.collect_after[0], self.collect_after[1]]
        window_rows = self.con.execute(q + " ORDER BY at, msgid LIMIT ?", args + [limit]).fetchall()
        self.collect_after = (window_rows[-1]["at"], window_rows[-1]["msgid"]) if len(window_rows) == limit else None
        for m in window_rows:
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

    def __init__(self, fn, readonly=False, sectioned=False):
        self.fn, self.state, self.result, self.error = fn, "queued", None, None
        self.readonly, self.sectioned = readonly, sectioned  # sectioned: its caller held an admitted section
        self.cond = threading.Condition()


class _Peer:
    def __init__(self, sock, accepted, creds):
        self.sock, self.accepted, self.creds = sock, accepted, creds
        self.buf, self.out, self.reply_ready = b"", b"", None


class StartGate:
    """What an authority's start section found, and its one admission decision (writer admission 6.3)."""

    def __init__(self, authority, adm):
        self.authority, self.adm, self.owned = authority, adm, False

    def admit(self, binding=None):
        """None for an ordinary start (no record); the record, for a start a matching `full` record admits as
        the operation's own section. Otherwise raises Unavailable, having opened nothing. Inside an enclosing
        bound start, a nested start section with no binding of its own is judged by the enclosing one's."""
        adm, a = self.adm, self.authority
        if adm.admitted:
            return None
        if binding is None and a._owned_starts:
            binding = a._owned_binding
        problem = None
        if binding is not None and adm.locked:
            problem = bound_start_problem(adm.record, binding)
            if problem is None:
                if not self.owned:
                    self.owned = True
                    a._owned_starts += 1
                    a._owned_binding = binding
                return adm.record
        info = adm.record.describe() or {"state": "unknown"}
        who = f"held by {info['operation']} ({info['state']})" if "operation" in info else \
            f"unknown ({adm.record.why})"
        a.unavailable = (f"maintenance is {who}: the authority does not start" + (f"; {problem}" if problem else ""),
                         a.unavailable[1] if a.unavailable else a.clock())
        raise Unavailable(a.unavailable[0])


class Authority:
    """The bridge's authority: the only holder of the only connection to
    outbox.db (section 2.4). `launch` is the bridge's whole start: the
    authority lock, the database, outbox.sock and the authority thread, all or
    nothing. `start` is its first part alone: without a running thread (the
    tests), requests run at once."""

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
        self.ready = False  # set only once a start has completed; cleared by close()
        self.bridge_only = False  # no socket: its path is over the platform's limit
        self._bound = False  # outbox.sock was bound by this authority, and is still to be removed
        self._owned_starts = 0  # open start sections admitted by a matching `full` record (6.3)
        self.root_fd = None  # the pinned state root's descriptor, during the bridge's activated start only
        self._sock_id = None  # the (device, inode) of the socket bind made in the pinned root
        self._owned_binding = None  # the binding they admitted

    @property
    def epoch(self):
        return self.store.epoch

    @property
    def running(self):
        """True only after a complete start, and while its thread (if any) lives."""
        return (self.ready and self.store.con is not None
                and (self.thread is None or self.thread.is_alive()))

    @contextlib.contextmanager
    def pinned_root(self, fd):
        """The bridge's activated start (#21 amendment 7): with `fd`, the validated state root's descriptor,
        every effect the standard library can make directory-relative is made through it (the authority lock,
        outbox.d, removing a stale socket), and the state tree is not re-made by path. The database's open and
        the socket's bind stay pathname-based; the caller checks the root's identity immediately before and
        after them. With None, nothing changes."""
        self.root_fd = fd
        try:
            yield
        finally:
            self.root_fd = None

    def _mkdir_at(self, name, parent_fd):
        """`name` made relative to `parent_fd` if missing, then its entry and itself made durable, as FS.mkdir."""
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            self.fs.fsync_fd(fd)
            self.fs.fsync_fd(parent_fd)
            return os.dup(fd)
        finally:
            os.close(fd)

    def start(self, binding=None):
        """Steps 1-4 of section 2.4.1, all or nothing; requests then run at once,
        in this thread (the tests). Raises, having opened and bound nothing.
        `binding` admits it under a matching `full` record (6.3)."""
        with self.start_section() as gate:
            gate.admit(binding)
            self._open()
        self.ready = True
        return self.epoch

    @contextlib.contextmanager
    def start_section(self):
        """The start is one section (writer admission 4.2, 6.3): L_adm shared, held from admission through
        the start, the socket's binding and a failed start's cleanup, with the record read while holding it.
        Yields a StartGate, whose admit() decides once: no record admits the start as before; a `full` record
        whose binding matches the launch admits the operation-owned start (#21 amendment 4, K3), which then
        serves read-only requests only, every mutation being answered `maintenance`; anything else refuses,
        having opened nothing. A caller may judge its launch (the bridge's bootstrap) inside the section
        before it calls admit()."""
        if self.root_fd is None:
            _make_state_tree(self.fs, self.paths.state)  # as _open does, and failing as it does (code review CODE16)
        with section(self.paths, fs=self.fs, dir_fd=self.root_fd) as adm:
            gate = StartGate(self, adm)
            try:
                yield gate
            finally:
                if gate.owned:
                    self._owned_starts -= 1

    def launch(self, binding=None):
        """The bridge's whole start: steps 1-4, the socket (step 5) and the
        authority thread. All or nothing: any failure closes what was opened,
        releases the lock and raises; running only once every step is done."""
        try:
            with self.start_section() as gate:
                gate.admit(binding)
                try:
                    self._open()
                    self.bridge_only = not self.bind()
                    self.run_in_thread()
                except BaseException:
                    # the failure's cleanup (the database's checkpoint, the socket, the locks) is a mutation of the
                    # protected files: it happens while this start still holds L_adm shared, so an owner's drain
                    # cannot complete until it has
                    self.close()
                    raise
        except BaseException:
            self.close()  # a start that was refused opened nothing; after the cleanup above, nothing is left
            raise
        return self.epoch

    def _open(self):
        """Steps 1-4: the authority lock, then the database. Raises, having
        opened nothing and released the lock. First the state tree, made
        durable entry by entry (whoever made it, the bridge's main() included)
        before anything is served, imported or acknowledged (code review CODE16)."""
        if self.root_fd is None:
            _make_state_tree(self.fs, self.paths.state)
            f = open(self.paths.auth_lock, "a")
        else:  # the pinned root: the lock is made and opened only there (#21 amendment 7)
            fd = os.open(self.paths.auth_lock.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                         | getattr(os, "O_CLOEXEC", 0), 0o666, dir_fd=self.root_fd)
            f = open(fd, "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            self.unavailable = ("another bridge holds the authority lock", self.clock())
            raise Unavailable(self.unavailable[0]) from None
        except BaseException:
            f.close()
            raise
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
            if self.root_fd is None:
                self.fs.mkdir(self.paths.fallback)
                self.fs.mkdir(self.paths.staging)
            else:  # outbox.d and outbox.d/tmp only in the pinned root
                fallback = self._mkdir_at(self.paths.fallback.name, self.root_fd)
                try:
                    os.close(self._mkdir_at(self.paths.staging.name, fallback))
                finally:
                    os.close(fallback)
        except OSError as err:
            self.log(f"authority: cannot create outbox.d: {err}")

    def bind(self):
        """Step 5: the socket, mode 0600, unless its path is over the platform's
        limit. Raises, having left nothing bound, on any failure."""
        path = str(self.paths.sock)
        if len(os.fsencode(path)) > SOCKET_LIMIT:
            self.log(f"authority: socket path over {SOCKET_LIMIT} bytes; serving the bridge only")
            return False
        if self.root_fd is None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)
        else:
            self._remove_stale_socket()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            old = os.umask(0o177)  # the socket is created 0600: only this user may connect
            try:
                listener.bind(path)
            finally:
                os.umask(old)
            self.remember_socket()  # what this bind made in the pinned root, if it is there
            if os.stat(path).st_mode & 0o777 != 0o600:
                raise Unavailable("socket mode is not 0600")
            listener.listen(CONN_MAX)
            listener.setblocking(False)
        except BaseException:  # nothing half-bound is left behind: the next start binds again
            listener.close()
            if self.root_fd is None:
                with contextlib.suppress(OSError):
                    os.unlink(path)
            else:
                self._remove_own_socket()
            raise
        self.listener = listener
        self._bound = True
        return True

    def _entry_at(self, sockets_only):
        """(device, inode) of the entry named outbox.sock in the pinned root, never followed; None if it is
        missing (or, with `sockets_only`, not a socket)."""
        try:
            st = os.stat(self.paths.sock.name, dir_fd=self.root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return None if sockets_only and not stat.S_ISSOCK(st.st_mode) else (st.st_dev, st.st_ino)

    def remember_socket(self):
        """After a bind, during a pinned start: the entry that bind made in the pinned root, by identity. A bind
        that landed elsewhere leaves nothing to remember, and so nothing is ever removed for it."""
        if self.root_fd is not None and self._sock_id is None:
            self._sock_id = self._entry_at(sockets_only=False)

    def _remove_stale_socket(self):
        """Before the bind, in the pinned root only: a socket left there is removed, while it is still that
        socket; anything else (and anything at the state path outside the pinned root) is never touched."""
        stale = self._entry_at(sockets_only=True)
        if stale is not None and self._entry_at(sockets_only=True) == stale:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.paths.sock.name, dir_fd=self.root_fd)

    def _remove_own_socket(self):
        """A failed start's cleanup, in the pinned root only: the entry this bind made, while it is still that
        very entry (its identity, recorded right after the bind)."""
        own, self._sock_id = self._sock_id, None
        if own is not None and self._entry_at(sockets_only=False) == own:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.paths.sock.name, dir_fd=self.root_fd)

    def close(self):
        """Stop serving and release everything a start opened, whatever step it
        reached: the thread, the connections, the socket, the database, the lock.
        What touches the protected files (the database's close and checkpoint, the
        socket's removal) is a mutation: it runs inside a section, or in the one
        its caller holds. While maintenance is held and no section is open it is
        deferred, with the connection and the lock kept, until a later close()
        finds no record (writer admission 3.1, rule 1)."""
        self.ready = False
        self.stopping = True
        thread, self.thread = self.thread, None
        if thread is not None and thread.is_alive():
            self._wake()
            thread.join(10)
        for peer in list(self.peers.values()):
            peer.sock.close()
        self.peers.clear()
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        if self.sel is not None:
            with contextlib.suppress(Exception):
                self.sel.close()
            self.sel = None
        for fd in (self.wake_r, self.wake_w):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        self.wake_r = self.wake_w = None
        if self.released:
            return
        if in_admitted_section() or self._owned_starts:  # inside a section: an ordinary one, or a bound start's
            self._release_files()
            return
        try:
            with section(self.paths, fs=self.fs) as adm:
                if adm.admitted:
                    self._release_files()
                else:
                    self.log("authority: closing is deferred while maintenance is held")
        except StorageError:  # no lock file can exist here, so no maintenance can be held
            self._release_files()

    @property
    def released(self):
        """Whether every file this authority opened is released: false after a close() deferred by maintenance."""
        return not (self._bound or self.store.con is not None or self.lock_file is not None)

    def _release_files(self):
        if self._bound:
            self._bound = False
            if self.root_fd is None:
                with contextlib.suppress(OSError):
                    os.unlink(self.paths.sock)
            else:  # a failed activated start: only its own socket, through the pinned root (#21 amendment 7)
                self._remove_own_socket()
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
        if not valid_writer(writer, optional=request["op"] == "status"):
            return reply("refused", detail="no valid writer")
        problem = request_error(request)
        if problem:  # validated whole before any part of it is used
            return reply("refused", detail=problem)
        if peer_pid is not None and isinstance(writer, dict) and writer.get("pid") != peer_pid:
            return reply("refused", detail="the request does not speak for its sender")
        if not self.running:
            return reply("error", detail="authority not running")
        try:
            if request["op"] in READ_OPS:  # answered in every state, in no section and with no write (6.4)
                with self.store.read_tx():
                    result, data = self._dispatch(request, bridge)
            else:
                with section(self.paths, fs=self.fs, wait=0) as adm:  # one try: this may be the selector thread
                    refusal = self._refusal(adm, request, bridge)
                    if refusal is not None:  # committed nothing, begun nothing
                        return reply("maintenance", maintenance=refusal, detail="held for maintenance")
                    with self.store.tx():
                        result, data = self._dispatch(request, bridge)
        except Conflict as err:
            return reply("conflict", detail=str(err))
        except Exception as err:  # rolled back; this request fails alone
            self.log(f"authority: request {request.get('op')} failed: {err!r:.120}")
            return reply("error")
        return reply(result, **data)

    def _refusal(self, adm, request, bridge):
        """The admission table of writer admission 4.5, for a request that mutates: None to run it, else what
        to answer `maintenance`. In `draining` only a request that would create a new entry is refused (any
        other continues an open call); in `partial`, `full` or an unknown record every one is. An in-process
        bridge request runs in `draining`. A lock that cannot be had at once is held maintenance."""
        if not adm.locked:
            return {"state": "unknown", "why": adm.record.why}
        record = adm.record
        if not record.present:
            return None
        if record.state == "draining":
            if bridge:
                return None if in_admitted_section() else record.describe()
            if request["op"] in ("create", "queue") and self.store.entry(request["id"]) is None:
                return record.describe()
            return None
        return record.describe()

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
        status = s.status()
        # the record and the sink's count (5.3): read, never written; an unreadable count is None, not zero
        status.update(maintenance=read_maintenance(self.paths).describe(), refused=sink_count(self.paths, self.fs))
        return "ok", {"status": status}

    def _exec_tx(self, fn, readonly=False, sectioned=False):
        """`fn(store)` in one transaction, by the in-process rule of writer admission 4.5: it begins only holding
        L_adm shared (one non-blocking try: this may be the selector thread, which never waits), and only with no
        record, or in `draining` when its caller holds an admitted section open (`sectioned`): the bridge's requests
        continue a section already begun, and a request made outside one would start new work. Otherwise
        CANCELLED, having begun nothing. A read-only request takes no section and writes nothing."""
        if readonly:
            with self.store.read_tx():
                return fn(self.store)
        with section(self.paths, fs=self.fs, wait=0) as adm:
            if not (adm.locked and (not adm.record.present or (adm.record.state == "draining" and sectioned))):
                return CANCELLED
            with self.store.tx():
                return fn(self.store)

    def run_tx(self, fn, readonly=False):
        """An in-process request: `fn(store)` in one transaction on the authority
        thread. Cancelled, having committed nothing, if it has not begun within
        AUTH_WAIT, or if maintenance does not admit it; then CANCELLED is returned
        and the caller treats it as a failed TX. A guard that fails raises
        Conflict, after the rollback."""
        if not self.running:
            return CANCELLED
        if self.thread is None or threading.current_thread() is self.thread:
            return self._exec_tx(fn, readonly, in_admitted_section())
        job = _Job(fn, readonly, in_admitted_section())
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
            job.result = self._exec_tx(job.fn, job.readonly, job.sectioned)
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
        """The last step of a start. A failure leaves the selector and pipe for
        close(), which launch() calls, and no thread recorded."""
        self.sel = selectors.DefaultSelector()
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_r, False)
        self.sel.register(self.wake_r, selectors.EVENT_READ, "wake")
        if self.listener is not None:
            self.sel.register(self.listener, selectors.EVENT_READ, "listen")
        thread = threading.Thread(target=self._loop, name="outbox-authority", daemon=True)
        self.thread, self.ready = thread, True
        try:
            thread.start()
        except BaseException:
            self.thread, self.ready = None, False
            raise

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
        """At most CONN_MAX connections per turn, rejected ones included: a flood
        of them never keeps the turn from its job, its request and its expiries."""
        for _ in range(CONN_MAX):
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
        except Exception:  # too large, or not JSON this decoder can read: this connection alone ends
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
            left = deadline - self.mono()  # connect and write share the one deadline (code review CODE19)
            if left <= 0:
                return None
            sock.settimeout(left)
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
            if (request.get("op") not in CLIENT_OPS
                    or not valid_writer(request.get("writer"), optional=request.get("op") == "status")
                    or request_error(request)):
                return dict(base, result="refused", authority_epoch=self.authority.epoch)
            try:
                done = self.authority.run_tx(lambda store: self.authority._dispatch(request, True),
                                             readonly=request.get("op") in READ_OPS)
            except Conflict as err:
                done = ("conflict", {"detail": str(err)})
            except Exception:  # rolled back: this request fails alone
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
    ok, replay, conflict or maintenance (which committed nothing, and is not an
    unavailable authority), and None when there is no usable reply: no socket,
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
        if reply.get("result") not in ("ok", "replay", "conflict", "maintenance"):
            return None
        epoch = reply.get("authority_epoch") or 0
        if epoch < self.epoch:
            return None  # a dead authority's buffered reply: true, but not built on
        if self.epoch and epoch > self.epoch:
            self.restarted = True  # the caller queries before its next mutation
        self.epoch = max(self.epoch, epoch)
        return reply


# --- the bridge's file-side import (sections 6.3 and 6.4) ------------------------------

SINK_PREFIX = "refused.d/"  # a refusal-sink file's key (its imports row, held row and alert): its name cannot clash


class Importer:
    """IF (fallback files, from outbox.d/ and from the refusal sink refused.d/) and I1-I3 (the legacy outbox),
    run on the bridge's flush. Holds only L_flush; never takes outbox.lock to import. File work is
    done here; each file or batch of lines is one in-process request. A file
    or claim that fails is kept, with an alert, and every other one proceeds.

    A fallback file is known by its key: its bare name in outbox.d/ (as before), and "refused.d/" plus its name in
    the sink, so two files of one name in the two directories are two files, each with its own receipt. Each is
    applied by its entry id, so two files for one entry are one obligation."""

    def __init__(self, authority, *, log=lambda m: None):
        self.authority, self.fs, self.paths = authority, authority.fs, authority.paths
        self.log = log
        self.pass_names = None  # the keys the current import pass listed when it began
        self.kept = []  # fallback files (keys) kept after a failure, least recently tried first
        self.staging_after = {}  # per tmp/ directory, the last name the previous cleanup examined

    def _sources(self):
        """(key prefix, directory, staging directory) of each place fallback files are imported from."""
        return (("", self.paths.fallback, self.paths.staging),
                (SINK_PREFIX, self.paths.refused, self.paths.refused_staging))

    def _where(self, key):
        """(directory, file name) of a key."""
        if key.startswith(SINK_PREFIX):
            return self.paths.refused, key[len(SINK_PREFIX):]
        return self.paths.fallback, key

    def _sha(self, data):
        return hashlib.sha256(data).hexdigest()

    def unreadable(self, name, err):
        """A file the import cannot list, sync, read, import or remove: it is kept,
        retried at later flushes, and raises one content-free alert; nothing else
        waits for it."""
        self.log(f"outbox: {name} could not be imported ({err!r:.100}); kept, and retried at a later flush")
        try:
            self.authority.run_tx(lambda s: s.raise_alert(
                f"held:{name}:unreadable", "held", name,
                "a queued outbox file could not be read or imported; it is kept and retried; see pchat status"))
        except Exception:  # the alert is raised again with the next failure
            pass

    def _listdir(self, path):
        """The names in `path`, or None when it cannot be listed: a failed
        listing is never read as an empty directory."""
        try:
            return self.fs.listdir(path)
        except OSError as err:
            self.unreadable(Path(path).name, err)
            return None

    def _budget(self, names):
        """This flush's files, at most IMPORT_MAX: the oldest names not kept after
        a failure, then kept ones in rotation, at least one when any is kept. So a
        kept file never stops a later one, and each is tried again in turn."""
        present = set(names)
        self.kept = [n for n in self.kept if n in present]
        kept = set(self.kept)
        reserve = min(len(self.kept), max(1, IMPORT_MAX // 8))
        take = [n for n in names if n not in kept][:IMPORT_MAX - reserve]
        return take + self.kept[:IMPORT_MAX - len(take)]

    def _keys(self):
        """The published files of every source, oldest name first, and whether every listing was read. A
        listing that fails is never read as an empty directory: its files are not listed, and the result is
        incomplete."""
        keys, whole = [], True
        for prefix, directory, _staging in self._sources():
            listed = self._listdir(directory)
            if listed is None:
                whole = False
                continue
            keys += [prefix + n for n in listed if is_published_name(n)]
        return sorted(keys, key=lambda k: (self._where(k)[1], k)), whole

    def import_fallback_files(self):
        """At most IMPORT_MAX published files, oldest name first, from both sources. Returns how many
        were imported. A listing that fails completes no pass, and starts none: the pass in progress stays
        unfinished and import_epoch where it is, so a retained handoff stays valid (section 6.3). The
        sources that could be listed are imported meanwhile."""
        names, whole = self._keys()
        if self.pass_names is None and whole:
            self.pass_names = set(names)
        count = 0
        for name in self._budget(names):
            try:
                outcome = self._import_file(name)
            except Exception as err:  # kept: its obligation is not lost, and it blocks nothing else
                self.unreadable(name, err)
                if name in self.kept:
                    self.kept.remove(name)
                self.kept.append(name)
                continue
            if outcome is CANCELLED:
                break  # not run: try again at the next flush
            if name in self.kept:
                self.kept.remove(name)
            if outcome is not None:
                count += 1
        remaining, whole = self._keys()
        if whole and self.pass_names is not None and not (self.pass_names & set(remaining)):
            self.authority.run_tx(lambda s: s.complete_pass())
            self.pass_names = None
        return count

    def _import_file(self, key):
        """IF for one file: its durable sync, one TX, then its removal. Returns
        the outcome; None if it is gone; CANCELLED if the TX never began."""
        directory, name = self._where(key)
        path = directory / name
        try:
            self.fs.durable(path)
            data = self.fs.read(path)
        except FileNotFoundError:
            return None
        sha = self._sha(data)
        try:
            row = json.loads(data.decode())
        except (ValueError, UnicodeDecodeError, RecursionError):
            row = data.decode(errors="replace")
        outcome = self.authority.run_tx(lambda s: s.import_fallback(key, sha, row))
        if outcome is CANCELLED:
            return CANCELLED
        if outcome == "changed":  # its exact bytes are held first; a hold that fails or never runs keeps the file
            if self.authority.run_tx(lambda s: s.hold_changed(key, sha, data)) is CANCELLED:
                return CANCELLED
        try:
            if self.fs.read(path) != data:  # rewritten since it was read: kept, and held by its bytes at the next pass
                return outcome
        except FileNotFoundError:
            return outcome
        self.fs.unlink(path)  # a failure keeps it: the next try finds its imports row and removes it
        self.fs.sync_dir(directory)
        if self.pass_names is not None:
            self.pass_names.discard(key)
        if outcome not in ("imported", "noop"):
            self.log(f"outbox: fallback file {key}: {outcome}")
        return outcome

    def clean_staging(self):
        """A tmp/ file (outbox.d/tmp/ and the sink's) is removed only once its writer is gone, or once
        published. A writer identity its name does not fully carry is unknown, never gone.
        At most IMPORT_MAX files per directory per flush, in rotation, so every one is reached."""
        for _prefix, _directory, staging in self._sources():
            names = sorted(self._listdir(staging) or [])
            after = self.staging_after.get(staging)
            if after is not None:
                names = [n for n in names if n > after] + [n for n in names if n <= after]
            batch = names[:IMPORT_MAX]
            self.staging_after[staging] = batch[-1] if len(names) > IMPORT_MAX else None
            for name in batch:
                try:
                    self._clean_one(staging, name)
                except Exception as err:  # cleanup only: tried again at the next flush
                    self.log(f"outbox: staging file {name}: {err!r:.80}")

    def _clean_one(self, staging, name):
        path = staging / name
        try:
            if self.fs.nlink(path) > 1:
                self.fs.unlink(path)
                return
        except OSError:
            return
        tag = name.split(".")
        if len(tag) >= 3 and tag[1].isascii() and tag[1].isdigit():
            writer = {"boot": known(tag[0]), "pid": int(tag[1]), "start": known(tag[2]) and tag[2].replace("_", " ")}
            if self.authority.probes.gone(writer) is True:
                with contextlib.suppress(OSError):
                    self.fs.unlink(path)

    def claim_legacy(self):
        """I1: rename a non-empty outbox.jsonl to a never-used claim name, WITHOUT
        outbox.lock; then a durable sync of every claim file present."""
        legacy = self.paths.legacy
        try:
            if self.fs.exists(legacy) and self.fs.size(legacy):
                name = f"outbox.claimed-{time.time_ns()}-{secrets.token_hex(4)}.jsonl"
                self.fs.rename(legacy, self.paths.state / name)
                self.fs.sync_dir(self.paths.state)
        except OSError as err:  # outbox.jsonl stays where it is, claimed at a later flush
            self.unreadable("outbox.jsonl", err)
        claims = []
        for name in self.claim_list():
            try:
                self.fs.durable(self.paths.state / name)
            except FileNotFoundError:
                continue
            except OSError as err:  # never imported before its durable sync; kept, with an alert
                self.unreadable(name, err)
                continue
            claims.append(name)
        return claims

    def claim_list(self):
        return sorted(n for n in self._listdir(self.paths.state) or []
                      if n.startswith("outbox.claimed-") and n.endswith(".jsonl"))

    def _import_row(self, name):
        row = self.authority.run_tx(lambda s: s.con.execute("SELECT * FROM imports WHERE name = ?",
                                                            (name,)).fetchone())
        if row is CANCELLED:
            return None
        return dict(row) if row is not None else {}

    def import_legacy(self, claims, barrier_at=None):
        """I2 for each open claim: one request of at most LINES_MAX lines per
        claim per call, so a large claim never holds the flush (the rest is read
        at the next one); with `barrier_at` (an outbox.lock acquisition after the
        claim was first recorded), the final read and I3. A claim that fails is
        kept, with an alert, and the next one proceeds."""
        for name in claims:
            try:
                if self._import_claim(name, barrier_at) is CANCELLED:
                    return
            except Exception as err:
                self.unreadable(name, err)

    def _import_claim(self, name, barrier_at):
        path = self.paths.state / name
        try:
            data = self.fs.read(path)
        except FileNotFoundError:
            return None
        row = self._import_row(name)
        if row is None:
            return CANCELLED
        if row.get("state") == "closed":
            if len(data) != row.get("offset") or self._sha(data) != row.get("sha256"):
                # not the bytes its import committed: rewritten outside this design. Held with its exact
                # bytes and alerted, never merged, and kept (owner amendment 6, F2(b); code review slot 9)
                self.authority.run_tx(lambda s: s.hold_changed(name, self._sha(data), data))
                return None
            try:
                self.fs.unlink(path)  # I3's unlink, replayed
                self.fs.sync_dir(self.paths.state)
            except OSError as err:
                self.log(f"outbox: {name} retired but not removed ({err!r:.80})")
            return None
        offset, ordinal = row.get("offset", 0), row.get("lines", 0)
        if row and self._sha(data[:offset]) != row.get("sha256"):
            self.authority.run_tx(lambda s: s.hold_changed(name, self._sha(data)))
            return None  # rewritten by something outside this design: held, never unlinked
        tail = data[offset:]
        cut = tail.rfind(b"\n") + 1
        complete = tail[:cut].split(b"\n")[:-1] if cut else []
        take = complete[:LINES_MAX]
        new_offset = offset + sum(len(x) + 1 for x in take)
        final = (barrier_at is not None and row.get("first_seen") is not None
                 and row["first_seen"] < barrier_at and len(take) == len(complete))
        lines = [x.decode(errors="replace") for x in take]
        unterminated = None
        if final and tail[cut:]:
            unterminated = tail[cut:].decode(errors="replace")  # held as unreadable, whatever it holds (I2)
            new_offset = len(data)
        if not lines and not final:
            if not row:
                self.authority.run_tx(lambda s: s.import_legacy(name, 0, [], 0, self._sha(b"")))
            return None
        try:
            n = self.authority.run_tx(lambda s, lines=lines, new_offset=new_offset, final=final:
                                      s.import_legacy(name, ordinal, lines, new_offset,
                                                      self._sha(data[:new_offset]), close=final,
                                                      unterminated=unterminated))
        except Conflict:
            return None
        if n is CANCELLED:
            return CANCELLED
        if final:
            try:
                if self.fs.read(path) != data:  # rewritten since it was read: kept, held at the next pass (I3)
                    return None
                self.fs.unlink(path)
                self.fs.sync_dir(self.paths.state)
            except OSError as err:  # closed: the next flush removes it
                self.log(f"outbox: {name} retired but not removed ({err!r:.80})")
        return None  # one batch per claim per call: the rest at the next
