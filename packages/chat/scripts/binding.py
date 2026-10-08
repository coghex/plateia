"""One session binding: who a caller verifiably is now, and whether a binding
stored earlier still names the same live session.

The identity registry (identities.json) is an assertion: what a session said
about itself when it registered. A binding also needs current evidence, read
fresh on every call and never cached:

- the cmux hook record for the surface: which provider session (session_id)
  and process (pid) run there now, updated no earlier than that process
  started;
- the process start time (ps lstart), because a pid alone is not an identity:
  pids are reused;
- for resolve_self, the caller's own ancestry: the hook record's process must
  be an ancestor of the caller, so a caller cannot borrow another tab's
  session by naming its surface;
- for a manager, manager.json: the surface it was started on and its
  started_at, the manager invocation generation.

Both lookups must agree before a binding is OK. A tab name, a pid, a surface
or a self-declared session is never enough on its own, and a weak registry
record (known only by process: or launch: keys) displays but never
authorizes. Every outcome is one of CODES.

A /clear keeps the surface, the process and manager.json's started_at but
starts a new provider session: verify reports SESSION_CHANGED. A tab reused
for another session reports SESSION_CHANGED too. Nothing here writes the
registry; this module only reads.
"""
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

PM_STATE = Path.home() / ".local/state/project-manager"
CHAT_STATE = Path(os.environ.get("CHAT_STATE", Path.home() / ".local/state/chat"))
BRANDS = ("claude", "codex")
FRESH_SLACK = 2  # seconds: a hook record written as its process started still counts
CODES = ("OK", "NO_SURFACE", "INVENTORY_UNAVAILABLE", "AMBIGUOUS_EVIDENCE", "NO_HOOK_RECORD",
         "AMBIGUOUS_HOOK_RECORD", "INACTIVE_HOOK_RECORD", "STALE_HOOK_RECORD", "PROCESS_GONE", "PID_REUSED",
         "NOT_ANCESTOR", "PROVIDER_MISMATCH", "WORKSPACE_MISMATCH", "NO_REGISTRY_RECORD",
         "AMBIGUOUS_REGISTRY_RECORD", "WEAK_RECORD", "SESSION_MISMATCH", "WRONG_PROJECT", "WRONG_ROLE",
         "NO_MANAGER_RECORD", "SURFACE_CHANGED", "SESSION_CHANGED", "GENERATION_CHANGED", "MALFORMED_BINDING",
         "HOOK_EVIDENCE_MISSING", "SURFACE_TAKEN", "MANAGER_MOVED")
# verify outcomes that positively show a stored session is gone or replaced; every other non-OK
# outcome is unknown evidence, never proof of exit
GONE = ("PROCESS_GONE", "PID_REUSED", "SESSION_CHANGED", "SURFACE_TAKEN", "MANAGER_MOVED", "GENERATION_CHANGED")
VERSION = re.compile(r"\d+\.\d+\.\d+")  # Claude Code's process name is its version
FIELDS = ("project", "role", "name", "brand", "provider_session_id", "pid", "pid_start", "surface_id",
          "manager_generation")
UUIDISH = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{0,127}")


class Unavailable(Exception):
    """An inventory could not be read; never treated as an empty answer."""


class Ambiguous(Unavailable):
    """Evidence that says two things at once (a key given twice)."""


def strict_loads(text):
    """JSON where a key given twice anywhere is refused, never last-wins."""
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise Ambiguous(f"key {k!r} given twice")
            out[k] = v
        return out
    try:
        return json.loads(text, object_pairs_hook=pairs)
    except ValueError:
        raise Unavailable("not JSON") from None


def brand_of(command):
    """The provider a process name shows: codex, claude, or None."""
    name = os.path.basename((command or "").strip())
    return "codex" if name == "codex" else "claude" if name == "claude" or VERSION.fullmatch(name) else None


class Inventory:
    """The live evidence, one fresh read per call. Tests pass a fake. The
    state roots default to this module's and may be given (reconcile passes
    its own)."""

    def __init__(self, chat_state=None, pm_state=None):
        self.chat_state = Path(chat_state) if chat_state else CHAT_STATE
        self.pm_state = Path(pm_state) if pm_state else PM_STATE

    def sessions(self, surface):
        try:
            r = subprocess.run(["cmux", "sessions", "list", "--json", "--surface", surface, "--limit", "20"],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as e:
            raise Unavailable(f"cmux unavailable: {e.__class__.__name__}") from None
        if r.returncode:
            raise Unavailable(f"cmux exited {r.returncode}")
        value = strict_loads(r.stdout)
        rows = value.get("sessions") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise Unavailable("cmux printed no sessions list")
        return rows

    def start(self, pid):
        """The process's start time, or "" if it is gone."""
        try:
            r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise Unavailable(f"ps unavailable: {e.__class__.__name__}") from None
        return " ".join(r.stdout.split()) if r.returncode == 0 else ""

    def command(self, pid):
        """The process's executable name at full width (never its arguments),
        or "" if it is gone. Without -ww, macOS cuts the name at the column
        width (a Claude Code path became "/Users/someone/.")."""
        try:
            r = subprocess.run(["ps", "-ww", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                               timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise Unavailable(f"ps unavailable: {e.__class__.__name__}") from None
        return r.stdout.strip() if r.returncode == 0 else ""

    def parent(self, pid):
        try:
            r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise Unavailable(f"ps unavailable: {e.__class__.__name__}") from None
        out = r.stdout.strip()
        return int(out) if r.returncode == 0 and out.isdigit() else None

    def registry(self):
        try:
            value = strict_loads((self.chat_state / "identities.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError):
            raise Unavailable("identity registry unreadable") from None
        agents = value.get("agents") if isinstance(value, dict) else None
        if not isinstance(agents, dict):
            raise Unavailable("identity registry has no agents")
        return agents

    def manager(self, project):
        try:
            value = strict_loads((self.pm_state / project / "manager.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return None
        except Ambiguous:
            raise
        except Unavailable:
            return None
        return value if isinstance(value, dict) else None


def started_epoch(lstart):
    try:
        return time.mktime(time.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y"))
    except (ValueError, AttributeError):
        return None


def ancestors(inv, pid, limit=64):
    chain = [pid]
    while len(chain) < limit:
        parent = inv.parent(chain[-1])
        if not parent or parent in chain:
            break
        chain.append(parent)
    return chain


def same(a, b):
    return isinstance(a, str) and isinstance(b, str) and a.upper() == b.upper()


def _time(v):
    """A hook record's update time if it is a finite number, else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _rows(inv, surface):
    """The surface's hook records that are well-shaped evidence: a positive
    int pid, a session id and a finite update time. Anything else is ignored
    (it cannot vouch for anything)."""
    return [r for r in inv.sessions(surface) if isinstance(r, dict) and same(r.get("surface_id"), surface)
            and type(r.get("pid")) is int and r["pid"] > 0 and isinstance(r.get("session_id"), str)
            and UUIDISH.fullmatch(r["session_id"]) and _time(r.get("updated_at_unix")) is not None]


def _hook(inv, surface, pids=None):
    """(the surface's current hook record, start, code). With pids, only a
    record whose process is one of them counts (the caller's ancestry)."""
    rows = _rows(inv, surface)
    if not rows:
        return None, None, "NO_HOOK_RECORD"
    if pids is not None:
        rows = [r for r in rows if r["pid"] in pids]
        if not rows:
            return None, None, "NOT_ANCESTOR"
    if len({r["pid"] for r in rows}) > 1:
        return None, None, "AMBIGUOUS_HOOK_RECORD"
    # One process; after a /clear it has several records, and the newest is current.
    rows.sort(key=lambda r: _time(r["updated_at_unix"]), reverse=True)
    newest = rows[0]
    if len(rows) > 1 and _time(rows[1]["updated_at_unix"]) == _time(newest["updated_at_unix"]) \
            and rows[1]["session_id"] != newest["session_id"]:
        return None, None, "AMBIGUOUS_HOOK_RECORD"
    if newest.get("active_for_surface") is not True:
        return None, None, "INACTIVE_HOOK_RECORD"
    start = inv.start(newest["pid"])
    if not start:
        return None, None, "PROCESS_GONE"
    since = started_epoch(start)
    if since is None or _time(newest["updated_at_unix"]) < since - FRESH_SLACK:
        return None, None, "STALE_HOOK_RECORD"
    return newest, start, "OK"


def _keys(r):
    keys = r.get("keys")
    return keys if isinstance(keys, list) and all(isinstance(k, str) for k in keys) else None


def _record(inv, surface, session, pid, start):
    """(the one active registry record that owns this session on this surface,
    code). A record vouches only with a well-formed key list and a positive,
    matching pid and process start; less is weak (display only)."""
    rows = [(n, r) for n, r in inv.registry().items() if isinstance(r, dict) and r.get("status") == "active"
            and same(r.get("surface_id"), surface)]
    if not rows:
        return None, "NO_REGISTRY_RECORD"
    owning = [(n, r) for n, r in rows if _keys(r) is not None
              and any(k in (f"{b}:{session}" for b in BRANDS) for k in _keys(r))]
    if not owning:
        weak = all(_keys(r) is None or all(k.startswith(("process:", "launch:")) for k in _keys(r)) for _, r in rows)
        return None, "WEAK_RECORD" if weak else "SESSION_MISMATCH"
    if len(owning) > 1:
        return None, "AMBIGUOUS_REGISTRY_RECORD"
    name, r = owning[0]
    if type(r.get("pid")) is not int or not isinstance(r.get("pid_start"), str) or not r["pid_start"].strip():
        return None, "WEAK_RECORD"
    if r["pid"] != pid:
        return None, "SESSION_MISMATCH"
    if " ".join(r["pid_start"].split()) != start:
        return None, "PID_REUSED"
    brand = next(b for b in BRANDS if f"{b}:{session}" in _keys(r))
    return {**r, "name": name, "brand": brand}, "OK"


def original_session(record):
    """The first provider session a registry record gained (identities
    appends session keys in order: a /clear adds a later one), or None."""
    keys = _keys(record) if isinstance(record, dict) else None
    for k in keys or []:
        brand, _, session = k.partition(":")
        if brand in BRANDS and session:
            return session
    return None


def _bind(inv, surface, project, role, pids):
    try:
        hook, start, code = _hook(inv, surface, pids)
        if code != "OK":
            return None, code
        record, code = _record(inv, surface, hook["session_id"], hook["pid"], start)
        if code != "OK":
            return None, code
        if brand_of(inv.command(hook["pid"])) != record["brand"]:
            return None, "PROVIDER_MISMATCH"
        if hook.get("workspace_id") and record.get("workspace_id") \
                and not same(hook["workspace_id"], record["workspace_id"]):
            return None, "WORKSPACE_MISMATCH"
        if record.get("project") != project:
            return None, "WRONG_PROJECT"
        if role is not None and record.get("role") != role:
            return None, "WRONG_ROLE"
        generation = None
        if record.get("role") == "manager":
            manager = inv.manager(project)
            if not manager or not isinstance(manager.get("started_at"), str):
                return None, "NO_MANAGER_RECORD"
            if not same(manager.get("surface_id"), surface):
                return None, "SURFACE_CHANGED"
            generation = manager["started_at"]
    except Ambiguous:
        return None, "AMBIGUOUS_EVIDENCE"
    except Unavailable:
        return None, "INVENTORY_UNAVAILABLE"
    return {"project": project, "role": record["role"], "name": record["name"], "brand": record["brand"],
            "provider_session_id": hook["session_id"], "pid": hook["pid"], "pid_start": start,
            "surface_id": hook["surface_id"], "workspace_id": record.get("workspace_id"),
            "manager_generation": generation,
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, "OK"


def resolve_self(project, role=None, *, env=None, inv=None):
    """(binding, code) for the caller itself: its surface's current session,
    which must be the caller's own ancestor process."""
    env = os.environ if env is None else env
    inv = inv or Inventory()
    surface = env.get("CMUX_SURFACE_ID", "")
    if not surface:
        return None, "NO_SURFACE"
    try:
        chain = set(ancestors(inv, os.getpid()))
    except Ambiguous:
        return None, "AMBIGUOUS_EVIDENCE"
    except Unavailable:
        return None, "INVENTORY_UNAVAILABLE"
    return _bind(inv, surface, project, role, chain)


def observe(surface, project, role=None, *, inv=None):
    """(binding, code) for whatever verified session runs on a surface now,
    for a caller that is not that session (the launcher binding a reused tab)."""
    return _bind(inv or Inventory(), surface, project, role, None)


def valid(binding):
    return (isinstance(binding, dict) and all(k in binding for k in FIELDS)
            and all(isinstance(binding[k], str) and binding[k]
                    for k in ("project", "role", "name", "brand", "provider_session_id", "pid_start", "surface_id"))
            and type(binding["pid"]) is int and binding["pid"] > 0
            and (binding["manager_generation"] is None or isinstance(binding["manager_generation"], str)))


def verify(binding, *, inv=None):
    """Does a stored binding still name the live session it named? One code.
    Only GONE codes are positive evidence that it is gone or replaced; a
    missing hook record or manager record is unknown, never proof of exit."""
    if not valid(binding):
        return "MALFORMED_BINDING"
    inv = inv or Inventory()
    try:
        start = inv.start(binding["pid"])
        if not start:
            return "PROCESS_GONE"
        if start != binding["pid_start"]:
            return "PID_REUSED"
        manager = inv.manager(binding["project"]) if binding["role"] == "manager" else None
        if manager and isinstance(manager.get("surface_id"), str) and isinstance(manager.get("started_at"), str):
            # positive evidence of its own, whatever the hook records say
            if not same(manager["surface_id"], binding["surface_id"]):
                return "MANAGER_MOVED"
            if manager["started_at"] != binding["manager_generation"]:
                return "GENERATION_CHANGED"
        hook, start, code = _hook(inv, binding["surface_id"], {binding["pid"]})
        if code in ("NOT_ANCESTOR", "NO_HOOK_RECORD"):
            others = {r["pid"] for r in _rows(inv, binding["surface_id"])} - {binding["pid"]}
            if others and _hook(inv, binding["surface_id"], others)[2] == "OK":
                return "SURFACE_TAKEN"  # another live, fresh, active process owns the tab now
            return "HOOK_EVIDENCE_MISSING"
        if code != "OK":
            return code
        if hook["session_id"] != binding["provider_session_id"]:
            return "SESSION_CHANGED"  # the same process's newest record names another session
        record, code = _record(inv, binding["surface_id"], hook["session_id"], hook["pid"], start)
        if code != "OK":
            return code  # the live session is unchanged; the registry no longer vouches for it
        if record["name"] != binding["name"] or record.get("project") != binding["project"] \
                or record.get("role") != binding["role"]:
            return "SESSION_MISMATCH"
        if binding["role"] == "manager" and not (manager and isinstance(manager.get("surface_id"), str)
                                                 and isinstance(manager.get("started_at"), str)):
            return "NO_MANAGER_RECORD"
    except Ambiguous:
        return "AMBIGUOUS_EVIDENCE"
    except Unavailable:
        return "INVENTORY_UNAVAILABLE"
    return "OK"


def same_session(a, b):
    """Two bindings name the same session (and manager generation)."""
    return valid(a) and valid(b) and all(a[k] == b[k] for k in FIELDS)
