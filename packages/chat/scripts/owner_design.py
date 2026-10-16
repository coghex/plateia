"""Durable quiet receipts for verified, exactly bound owner-led work.

This is coordination policy, not hook trust or permission enforcement. Original
chat remains logged; no client input or messaging command is used here.

Two things this copy takes from a private file instead of the code
(docs/chat_capture.md): the one legacy scope that predates enrollment, and the
workspaces and surfaces that are never enrolled. The file is
$CHAT_STATE/owner-led-private.json (an invented example ships as
owner-led-private.json.example). It is read on every use, never at import, and
never written. Missing or invalid, owner-led routing is off: nothing is
enrolled or bound, and every post keeps its ordinary delivery.
"""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import chatlib
import delivery_store

STATE = chatlib.STATE_DIR / "owner-design-deferred.json"
SCHEMA = "owner-led-deferred-v2"
LEGACY_SCHEMA = "owner-design-deferred-v1"
OWNERSHIP = chatlib.STATE_DIR / "owner-led-scopes.json"
PRIVATE_NAME = "owner-led-private.json"
# The projects owner-led routing is rolled out to (the one place real project names stay); enrollment still
# requires exact owner evidence.
ENABLED_PROJECTS = frozenset({"hetoimasia", "moskophoros", "synarchy", "quruntul", "kanban", "plateia"})
SCOPE_FIELDS = ("project", "request", "channel", "worker", "session_id", "surface_id")
PRIVATE_FIELDS = ("legacy_scope", "protected_workspaces", "protected_surfaces")
MAX_PRIVATE = 64 * 1024


class Unavailable(ValueError):
    """The private file is missing or invalid, so owner-led routing is off. `reason` is one of two fixed
    tokens, "missing" or "invalid"; nothing from the file (an exception message could quote its bytes) is
    kept."""

    def __init__(self, reason):
        super().__init__(f"owner-led private file {reason}; owner-led routing is off")
        self.reason = reason


class MaintenanceHeld(ValueError):
    """Maintenance is held (or unknown): acknowledge() changed nothing."""


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 255


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def read_private():
    """The private file's values, read now: SimpleNamespace(legacy, workspaces, surfaces). Raises Unavailable.

    The path must be a regular file, found without following a link and checked on the open descriptor, so a
    FIFO or a device never blocks a message section. Anything unreadable, over 64 KiB, not UTF-8, not JSON,
    not exactly the three fields, or holding a value that is not a non-empty string of at most 255 characters
    is invalid. An empty protected list is valid."""
    path = chatlib.STATE_DIR / PRIVATE_NAME
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_NOCTTY)
    except (FileNotFoundError, NotADirectoryError):
        raise Unavailable("missing") from None
    except OSError:
        raise Unavailable("invalid") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Unavailable("invalid")
        raw = b""
        while len(raw) <= MAX_PRIVATE:
            block = os.read(fd, MAX_PRIVATE + 1 - len(raw))
            if not block:
                break
            raw += block
    except OSError:
        raise Unavailable("invalid") from None
    finally:
        os.close(fd)
    if len(raw) > MAX_PRIVATE:
        raise Unavailable("invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError):
        raise Unavailable("invalid") from None
    scope = value.get("legacy_scope") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) != set(PRIVATE_FIELDS)
            or not isinstance(scope, dict) or set(scope) != set(SCOPE_FIELDS)
            or not all(_text(scope[k]) for k in SCOPE_FIELDS)
            or any(not isinstance(value[k], list) or not all(_text(v) for v in value[k])
                   for k in ("protected_workspaces", "protected_surfaces"))):
        raise Unavailable("invalid")
    return SimpleNamespace(legacy={k: scope[k] for k in SCOPE_FIELDS},
                           workspaces=frozenset(value["protected_workspaces"]),
                           surfaces=frozenset(value["protected_surfaces"]))


def _require(private):
    return private if private is not None else read_private()


def _loaded(private):
    """The values, or None when the file is missing or invalid: the caller does nothing owner-led."""
    try:
        return _require(private)
    except Unavailable:
        return None


def _valid_scope(scope):
    return (isinstance(scope, dict)
            and all(isinstance(scope.get(k), str) and 0 < len(scope[k]) <= 255 for k in SCOPE_FIELDS)
            and re.fullmatch(r"[a-z][a-z0-9-]*", scope["project"])
            and re.fullmatch(r"[A-Za-z0-9_.:-]+", scope["request"])
            and scope["channel"].startswith("#")
            and scope.get("role", "solver") in ("solver", "reviewer", "guide"))


def _scopes():
    if not OWNERSHIP.exists():
        return []
    value = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema") != "owner-led-scopes-v1"
            or not isinstance(value.get("scopes"), list)
            or any(not _valid_scope(s) or not isinstance(s.get("authority"), dict)
                   or s["authority"].get("kind") not in ("verified_owner_request", "explicit_owner_approval")
                   or any(not isinstance(s["authority"].get(k), str) or not s["authority"][k]
                          for k in ("owner", "msgid", "at"))
                   for s in value["scopes"])):
        raise ValueError("invalid owner-led ownership record; preserve it and use normal routing")
    return value["scopes"]


def _bound(scope, project, agents, private):
    actor = agents.get(scope["worker"], {})
    return (scope["project"] == project and isinstance(actor, dict)
            and actor.get("status") == "active"
            and actor.get("workspace_id") not in private.workspaces
            and actor.get("surface_id") not in private.surfaces
            and actor.get("role") == scope.get("role", "solver")
            and actor.get("project") == project
            and all(actor.get(k) == scope[k] for k in ("request", "channel", "session_id", "surface_id")))


def scopes_for(project, agents, private=None):
    private = _loaded(private)
    if private is None:
        return []
    scopes = [private.legacy] if project == private.legacy["project"] else []
    if project in ENABLED_PROJECTS:
        scopes += [s for s in _scopes() if s["project"] == project]
    return [s for s in scopes if _bound(s, project, agents, private)]


def scope_for(entry, project, agents, named, private=None):
    if (entry.get("verified") is not True or not isinstance(entry.get("msgid"), str)
            or not entry["msgid"] or not isinstance(entry.get("at"), str)
            or not isinstance(entry.get("text"), str) or entry.get("reply_to") or named
            or entry.get("project") != project):
        return None
    for scope in scopes_for(project, agents, private):
        if (entry.get("from") == scope["worker"]
                and entry.get("role") == scope.get("role", "solver")
                and all(entry.get(k) == scope[k] for k in ("channel", "session_id", "surface_id"))
                and entry["text"].startswith(f"[status {scope['request']}] ")):
            return scope
    return None


def routine(entry, project, agents, named):
    return scope_for(entry, project, agents, named) is not None


def enroll_owner_request(entry, project, agents, named, cfg, private=None):
    """A verified direct owner request enrolls only its one exact worker/run.

    The owner is the one canonical owner (chatlib.owner_identity): a server-verified account that is the
    configured owner or an exact entry of the private `owner_accounts` list. This does not send, accept,
    interrupt or suppress that request. Manager dispatches, assistant dispatches, arbitrary status and old
    bindings do not establish direct owner participation. A new request must be enrolled anew.
    """
    private = _loaded(private)
    if private is None:
        return False
    owner = chatlib.owner_identity(cfg, entry.get("from"), entry.get("verified"))
    if (project not in ENABLED_PROJECTS or owner is None or entry.get("reply_to")
            or not isinstance(entry.get("msgid"), str) or not entry["msgid"]
            or not isinstance(entry.get("at"), str) or len(named) != 1):
        return False
    match = re.match(r"^\[request ([A-Za-z0-9_.:-]+)\] ", entry.get("text", ""))
    if not match:
        return False
    worker = next(iter(named))
    actor = agents.get(worker, {})
    if (not isinstance(actor, dict) or actor.get("status") != "active"
            or actor.get("project") != project or actor.get("parent")
            or actor.get("workspace_id") in private.workspaces
            or actor.get("surface_id") in private.surfaces
            or actor.get("role") not in ("solver", "reviewer", "guide")):
        return False
    p = cfg.get("projects", {}).get(project, {})
    channel = entry.get("channel", "")
    if not channel.startswith(p.get("prefix") or "\0"):
        return False  # main-channel delegated work is not direct owner-worker work
    authority = {"kind": "verified_owner_request", "owner": owner,
                 "msgid": entry["msgid"], "at": entry["at"]}
    if entry["from"] != owner:
        authority["account"] = entry["from"]  # the raw verified account stays as evidence
    scope = {"project": project, "request": match[1], "channel": channel,
             "worker": worker, "role": actor["role"],
             "session_id": actor.get("session_id"), "surface_id": actor.get("surface_id"),
             "authority": authority}
    if not _valid_scope(scope):
        return False
    with _locked():
        scopes = _scopes()
        if any(s == scope for s in scopes):
            return True
        scopes = [s for s in scopes if not (s["project"] == project and s["worker"] == worker)]
        _atomic(OWNERSHIP, json.dumps({"schema": "owner-led-scopes-v1", "scopes": scopes + [scope]},
                                     ensure_ascii=False, indent=2) + "\n")
    return True


def _empty():
    return {"schema": SCHEMA, "generation": 0, "seen": {}, "pending": []}


def _read():
    if not STATE.exists():
        return _empty()
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if isinstance(value, dict) and value.get("schema") == LEGACY_SCHEMA:
        private = _loaded(None)  # a legacy record is migrated only against the private file's legacy scope
        if private is not None and value.get("scope") == private.legacy:
            value = copy.deepcopy(value)
            value["schema"] = SCHEMA
            value.pop("scope", None)
            for event in value.get("pending", []):
                if isinstance(event, dict):
                    event["scope"] = copy.deepcopy(private.legacy)
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or type(value.get("generation")) is not int or value["generation"] < 0
            or not isinstance(value.get("seen"), dict) or not isinstance(value.get("pending"), list)):
        raise ValueError("invalid owner-design deferred record; preserve it for repair")
    if any(not isinstance(k, str) or type(v) is not int or not 0 < v <= value["generation"]
           for k, v in value["seen"].items()):
        raise ValueError("invalid owner-design receipt identities")
    ids = set()
    for event in value["pending"]:
        if (not isinstance(event, dict) or not isinstance(event.get("msgid"), str)
                or event["msgid"] in ids or value["seen"].get(event["msgid"]) != event.get("generation")
                or any(not isinstance(event.get(k), str) for k in ("at", "text"))
                or not _valid_scope(event.get("scope"))):
            raise ValueError("invalid owner-design pending receipts")
        ids.add(event["msgid"])
    return value


@contextlib.contextmanager
def _locked():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(STATE) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _admitted():
    """One writer-admission section (docs/writer_admission.md 4.2) for a caller outside the bridge, whose own
    message section already covers the hook. Held or unknown maintenance changes nothing and is reported."""
    with delivery_store.section(delivery_store.Paths(chatlib.STATE_DIR)) as adm:
        if not adm.admitted:
            raise MaintenanceHeld("maintenance is held; nothing was changed")
        yield


def _atomic(path, text, mode=0o600):
    """Persist file and rename before publishing success, without shared caches."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _save(value):
    _atomic(STATE, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def record(entry, scope=None, private=None):
    """Persist before omitting the ring; replayed/consumed event IDs stay deduped."""
    scope = _require(private).legacy if scope is None else scope
    if not _valid_scope(scope):
        raise ValueError("invalid exact owner-led scope")
    with _locked():
        state = _read()
        if entry["msgid"] in state["seen"]:
            return
        state["generation"] += 1
        state["seen"][entry["msgid"]] = state["generation"]
        state["pending"].append({"msgid": entry["msgid"], "at": entry["at"],
                                 "text": entry["text"], "generation": state["generation"],
                                 "scope": copy.deepcopy(scope)})
        _save(state)


def snapshot(project):
    """The project's pending receipts, or None. A pure read: it writes nothing (no lock file, no directory),
    so maintenance never needs to refuse it. Every writer replaces the record whole and atomically (_atomic),
    so a read sees the record before or after a change, never a part of one."""
    if not STATE.exists():
        return None
    state = copy.deepcopy(_read())
    state["pending"] = [e for e in state["pending"] if e["scope"]["project"] == project]
    state["project"] = project
    return state if state["pending"] else None


def _groups(snapshot):
    groups = {}
    for event in snapshot["pending"]:
        scope = event["scope"]
        key = tuple(scope[k] for k in SCOPE_FIELDS)
        groups.setdefault(key, {"scope": scope, "pending": []})["pending"].append(event)
    return list(groups.values())


def report(snapshot):
    lines = []
    for group in _groups(snapshot):
        scope, events = group["scope"], group["pending"]
        first, last = events[0], events[-1]
        lines += [f"  owner-managed {scope['request']}: {len(events)} deferred design receipt(s) "
                  f"in {scope['channel']}, msgid={first['msgid']} through msgid={last['msgid']}",
                  "  The owner is already participating. Record this as owner work; do not relay "
                  "choices or ask for readiness unless the owner requests it. Receipts confer no new authority.",
                  f"  latest retained status ({last['at']}): {last['text'][:1200]}"]
    return lines


def _tag(scope, private):
    if scope == private.legacy:
        return "owner-design-deferred:" + scope["request"]
    key = json.dumps([scope[k] for k in SCOPE_FIELDS], separators=(",", ":"))
    return "owner-led-deferred:" + hashlib.sha256(key.encode()).hexdigest()[:20]


def _handoff(text, snapshot, private):
    for group in _groups(snapshot):
        scope, events = group["scope"], group["pending"]
        first, last = events[0], events[-1]
        tag = _tag(scope, private)
        block = (f"<!-- {tag} -->\n- OWNER-MANAGED {scope['request']}: {scope['worker']} "
                 f"({scope['session_id']}, {scope['surface_id'][:8]}), {scope['channel']}. "
                 f"Reconciled {len(events)} receipt(s), msgid={first['msgid']} through "
                 f"msgid={last['msgid']} ({last['at']}). Latest status: "
                 + " ".join(last["text"].split())[:1200]
                 + f"\nOwner participates directly; no redundant recap or readiness request.\n<!-- /{tag} -->")
        pattern = re.compile(rf"<!-- {re.escape(tag)} -->.*?<!-- /{re.escape(tag)} -->", re.S)
        if pattern.search(text):
            text = pattern.sub(lambda _: block, text)
        else:
            heading = "## Owner and other work\n"
            if heading not in text:
                raise ValueError("handoff lacks Owner and other work; deferred receipts remain pending")
            text = text.replace(heading, heading + block + "\n", 1)
    return text


def acknowledge(snapshot, pm_state):
    """Finish a delivered reconciliation; failed writes/newer arrivals survive.

    The project manager's reconcile calls this, never the bridge, so every write is inside its own
    writer-admission section. Under held or unknown maintenance it changes neither the receipt record nor
    handoff.md and raises MaintenanceHeld; the receipts stay pending for a later reconcile. It needs the
    private file (its legacy scope names the handoff tag) and raises Unavailable without it, also changing
    nothing."""
    with _admitted(), _locked():
        private = _require(None)
        state = _read()
        captured = {e["msgid"] for e in snapshot["pending"]}
        remaining = [e for e in state["pending"] if e["msgid"] not in captured]
        if len(remaining) == len(state["pending"]):
            return  # another successful invocation already consumed this snapshot
        project = snapshot["project"]
        if any(e["scope"]["project"] != project for e in snapshot["pending"]):
            raise ValueError("mixed project snapshot; receipts remain pending")
        handoff = Path(pm_state) / project / "handoff.md"
        original = handoff.read_text(encoding="utf-8")
        _atomic(handoff, _handoff(original, snapshot, private), handoff.stat().st_mode & 0o777)
        state["pending"] = remaining
        _save(state)  # only after handoff is durable; retry is idempotent


def deferred_observation(section, line, project, agents):
    """Only lifecycle observations for an exactly verified owner-led run are quiet.

    Generic repository/CI/queue/blocker changes remain actionable. The old
    narrow settings-document observation retains its original behavior.
    """
    try:
        private = _require(None)
        scopes = scopes_for(project, agents, private)
    except (OSError, ValueError, TypeError):
        return False
    for scope in scopes:
        if section in ("sessions", "stalled"):
            if (re.search(r"\b" + re.escape(scope["worker"]) + r"\b", line)
                    or scope["surface_id"][:8].lower() in line.lower()):
                return True
        elif scope == private.legacy and section == "repo" and re.fullmatch(
                r"\s*docs-wip: [ MARC?!]{1,2} docs/designs/settings_design\.md", line):
            return True
    return False


def evaluates(entry, project, named, cfg):
    """Whether the bridge's hook would act on this post were owner-led routing on: a verified direct owner
    `[request ID]` post naming one agent, or a verified, unaddressed, non-reply `[status ...]` post by a
    registered agent of the channel's project. Cheap, and it reads no state."""
    if not project or entry.get("reply_to"):
        return False
    text = entry.get("text")
    if not isinstance(text, str):
        return False
    status = (entry.get("verified") is True and entry.get("project") == project and not named
              and text.startswith("[status "))
    request = (len(named) == 1 and text.startswith("[request ")
               and chatlib.owner_identity(cfg, entry.get("from"), entry.get("verified")) is not None)
    return status or request


def hook(entry, project, agents, named, cfg):
    """The bridge's one call per post: (scope, off). `scope` is the exact owner-led scope the post's receipt
    belongs to, durably recorded before this returns, else None. `off` is "missing" or "invalid" when the post
    is one the hook evaluates in an enabled project but the private file is missing or invalid, so nothing
    was enrolled, bound or recorded; otherwise None. The file is read here, for this post, so a change takes
    effect on the next. (A post in a project that is not enabled only matters to the legacy scope, which
    exists only in the file, so without the file there is nothing to report.)"""
    if not evaluates(entry, project, named, cfg):
        return None, None
    try:
        private = read_private()
    except Unavailable as unavailable:
        return None, (unavailable.reason if project in ENABLED_PROJECTS else None)
    enroll_owner_request(entry, project, agents, named, cfg, private)
    scope = scope_for(entry, project, agents, named, private)
    if scope:
        record(entry, scope, private)  # durable before the caller omits delivery or advances the channel log
    return scope, None
