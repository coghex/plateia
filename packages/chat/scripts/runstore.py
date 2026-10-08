"""Durable child runs: PM_STATE/<project>/runs/<run_id>/.

- journal.jsonl: append-only, one record per transition, seq 1, 2, 3 ... with
  no gaps. It is the truth; every reader replays it. Every record is read
  strictly: a key given twice, a missing or mistyped field, an unknown type
  or an impossible transition is RUN_CORRUPT, which refuses every change.
- state.json: the replayed state, rewritten after each append for people and
  quick reads. It may lag the journal; nothing trusts it.
- result-<sha12><ext>: a child's result, put in place before the transition
  that names it.
- run.lock: one transition at a time; never held while waiting.
- runs/.claims.lock: one claim (or reuse check) at a time per project, so two
  runs cannot bind the same session.

Durability: each write is synced (F_FULLFSYNC where the platform has it,
else fsync) and so is the folder after a new name appears, so a completed
transition survives a process crash or a reboot. Without F_FULLFSYNC a power
loss can still lose the newest record (the drive may cache it); that run
then reads as it was before. A torn last line (a crash mid-append) was never
acknowledged: readers ignore it and the next locked append cuts it off. If
storage itself fails, the transition fails and is reported; nothing here can
promise to record that failure anywhere.
"""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

import binding

PM_STATE = Path.home() / ".local/state/project-manager"
RUN_ID = re.compile(r"run-\d{8}T\d{6}Z-[0-9a-f]{12}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ACTIVE = ("created", "launched", "launch_unverified", "running")
TERMINAL = ("succeeded", "blocked", "failed", "timed_out", "cancelled", "launch_refused")
FINISHED = ("succeeded", "blocked", "failed")  # the child ended its own invocation (finish)
FINISH_STATUS = {"ok": "succeeded", "blocked": "blocked", "error": "failed"}
POLICY = {"publish": ("none", "request"), "notify": ("none", "parent"), "on_failure": ("record", "parent"),
          "wake_adapter": ("none", "native-background-hint", "async-rewake-hint")}
HINT_NONCE = re.compile(r"[0-9a-f]{12}")
HINT_STATES = TERMINAL + ("deadline",)
LAUNCH_KINDS = ("launched", "reused", "owner_surface", "unverified")
ROLES = ("solver", "reviewer")
TIMEOUT_RANGE = (60, 21600)
MAX_RESULT = 1 << 20
LOCK_WAIT = 10


class RunError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(iso):
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t.timestamp() if t.tzinfo else None


def new_run_id():
    return time.strftime("run-%Y%m%dT%H%M%SZ-", time.gmtime()) + secrets.token_hex(6)


def run_dir(project, run_id):
    if not (isinstance(run_id, str) and RUN_ID.fullmatch(run_id)):
        raise RunError("INVALID_ARGUMENT", "malformed run id")
    if not (isinstance(project, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", project)):
        raise RunError("INVALID_ARGUMENT", "malformed project")
    return PM_STATE / project / "runs" / run_id


def find(run_id):
    """The project a run id belongs to, from the store itself."""
    if not (isinstance(run_id, str) and RUN_ID.fullmatch(run_id)):
        raise RunError("INVALID_ARGUMENT", "malformed run id")
    found = [p.parent.parent.parent.name for p in PM_STATE.glob(f"*/runs/{run_id}/journal.jsonl")]
    if len(found) != 1:
        raise RunError("RUN_NOT_FOUND", run_id)
    return found[0]


# --- strict records -----------------------------------------------------------

def _str(v):
    return isinstance(v, str) and v != ""


def _in(v, choices):
    """Membership for untrusted values: only a string can be one of the choices."""
    return isinstance(v, str) and v in choices


def _result(r):
    return (isinstance(r, dict) and _str(r.get("path")) and isinstance(r.get("sha256"), str)
            and HEX64.fullmatch(r["sha256"]) is not None and type(r.get("bytes")) is int and r["bytes"] >= 0
            and _in(r.get("status"), FINISH_STATUS))


def _launch(v):
    return (isinstance(v, dict) and (v.get("kind") is None or _in(v["kind"], LAUNCH_KINDS))
            and (v.get("surface_id") is None or _str(v["surface_id"]))
            and (v.get("candidate") is None or binding.valid(v["candidate"])))


def _policy(p):
    return (isinstance(p, dict) and set(p) == set(POLICY) and all(_in(p[k], POLICY[k]) for k in POLICY)
            and not (p["on_failure"] == "parent" and p["notify"] != "parent")
            and (p["notify"] == "parent") == (p["wake_adapter"] != "none"))


DATA = {
    "created": lambda d: (_str(d.get("run_id")) and RUN_ID.fullmatch(d["run_id"]) is not None
                          and _str(d.get("project")) and _str(d.get("task")) and _str(d.get("request_id"))
                          and _in(d.get("role"), ROLES) and _policy(d.get("policy"))
                          and binding.valid(d.get("parent")) and d["parent"]["project"] == d["project"]
                          and _str(d.get("deadline")) and epoch(d["deadline"]) is not None),
    "launched": lambda d: _launch(d.get("launch")) and d["launch"].get("kind") is not None and d.get("child") is None,
    "launch_failed": lambda d: _launch(d.get("launch")),
    "claimed": lambda d: binding.valid(d.get("child")),
    "finished": lambda d: _in(d.get("status"), FINISH_STATUS) and _result(d.get("result")),
    "late_result": lambda d: _result(d.get("result")),
    "finish_conflict": lambda d: _str(d.get("sha256")) and HEX64.fullmatch(d["sha256"]) is not None
                                 and _in(d.get("status"), FINISH_STATUS),
    "timed_out": lambda d: d == {},
    "cancelled": lambda d: isinstance(d.get("reason"), str),
    "waiter_returned": lambda d: isinstance(d.get("code"), str),
    "withheld": lambda d: isinstance(d.get("code"), str),
    "accepted": lambda d: binding.valid(d.get("by")),
    "released": lambda d: _str(d.get("reason")),
    "adopted": lambda d: binding.valid(d.get("parent")) and isinstance(d.get("old_code"), str),
    # the async-rewake hint: a notification attempt, never delivery, wake or acceptance
    "hint_armed": lambda d: _str(d.get("nonce")) and HINT_NONCE.fullmatch(d["nonce"]) is not None
                            and _str(d.get("session")),
    "hint_fired": lambda d: _str(d.get("nonce")) and HINT_NONCE.fullmatch(d["nonce"]) is not None
                            and _in(d.get("state"), HINT_STATES),
    "hint_received": lambda d: _str(d.get("nonce")) and HINT_NONCE.fullmatch(d["nonce"]) is not None
                               and _str(d.get("session")) and isinstance(d.get("duplicate"), bool),
}


def check_record(rec, seq):
    """Every field typed before it is used: anything else is RUN_CORRUPT,
    never a raw Python error."""
    try:
        ok = (isinstance(rec, dict) and set(rec) == {"seq", "at", "type", "actor", "data"}
              and type(rec["seq"]) is int and rec["seq"] == seq and _str(rec["at"]) and epoch(rec["at"]) is not None
              and _in(rec["type"], DATA) and isinstance(rec["actor"], str) and isinstance(rec["data"], dict)
              and bool(DATA[rec["type"]](rec["data"])))
    except (TypeError, ValueError, KeyError, AttributeError):
        ok = False
    if not ok:
        raise RunError("RUN_CORRUPT", f"journal record {seq} is malformed")


def read_journal(path):
    """(records, torn tail length). Raises RUN_CORRUPT on any bad complete line."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise RunError("RUN_NOT_FOUND", path.parent.name) from None
    except OSError:
        raise RunError("RUN_UNREADABLE", path.parent.name) from None
    end = data.rfind(b"\n") + 1
    records = []
    for raw in data[:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = binding.strict_loads(raw.decode("utf-8"))
        except (UnicodeError, binding.Unavailable):
            raise RunError("RUN_CORRUPT", "a journal line is not strict JSON") from None
        check_record(rec, len(records) + 1)
        records.append(rec)
    return records, len(data) - end


def replay(records, project=None, run_id=None):
    """The state the records lead to. With project and run_id (the folder it
    was read from), every binding in it must belong to that project and run:
    a journal copied into another project's folder is corrupt."""
    state = None
    try:
        for rec in records:
            state = apply(state, rec)
    except (RunError, TypeError, KeyError, ValueError) as e:
        raise RunError("RUN_CORRUPT", f"replay: {getattr(e, 'detail', '') or e}") from None
    if state is None:
        raise RunError("RUN_CORRUPT", "empty journal")
    if project is not None:
        bound = [state["parent"], state["child"], (state["launch"] or {}).get("candidate"),
                 state["delivery"]["accepted"], *state["delivery"]["adopted_from"]]
        if state["project"] != project or state["run_id"] != run_id \
                or any(b is not None and b.get("project") != project for b in bound):
            raise RunError("RUN_CORRUPT", "the journal names another project or run")
    return state


def load(project, run_id):
    """The run's state by replaying its journal. Read-only; no lock."""
    records, _ = read_journal(run_dir(project, run_id) / "journal.jsonl")
    return replay(records, project, run_id)


def apply(state, rec):
    """The pure transition function over checked records. Anything the state
    machine does not allow is INVALID_TRANSITION (RUN_CORRUPT on replay)."""
    t, d = rec["type"], rec["data"]
    if t == "created":
        if state is not None:
            raise RunError("INVALID_TRANSITION", "created twice")
        return {"schema": "childrun/2", "run_id": d["run_id"], "project": d["project"], "task": d["task"],
                "request_id": d["request_id"], "role": d["role"], "policy": d["policy"], "parent": d["parent"],
                "child": None, "launch": None, "launched_at": None, "state": "created", "created_at": rec["at"],
                "ended_at": None, "deadline": d["deadline"], "result": None, "late_results": [], "conflicts": [],
                "cancel_reason": None, "released": None, "hint": None,
                "delivery": {"mode": "wait" if d["policy"]["notify"] == "parent" else "none",
                             "state": "pending" if d["policy"]["notify"] == "parent" else "not_requested",
                             "attempts": [], "accepted": None, "adopted_from": []},
                "seq": rec["seq"], "updated_at": rec["at"]}
    if state is None:
        raise RunError("INVALID_TRANSITION", f"{t} before created")
    s = json.loads(json.dumps(state))
    s["seq"], s["updated_at"] = rec["seq"], rec["at"]
    active, terminal = s["state"] in ACTIVE, s["state"] in TERMINAL

    def end(new):
        s.update(state=new, ended_at=rec["at"])
    if t == "launched" and s["state"] == "created":
        s.update(state="launch_unverified" if d["launch"]["kind"] == "unverified" else "launched",
                 launch=d["launch"], launched_at=rec["at"])
    elif t == "launch_failed" and s["state"] == "created":
        s.update(launch=d["launch"])
        end("launch_refused")
    elif t == "claimed" and s["state"] in ("launched", "launch_unverified") and s["child"] is None:
        s.update(state="running", child=d["child"])
    elif t == "finished" and s["state"] in ("launched", "launch_unverified", "running") and s["child"] is not None:
        s["result"] = d["result"]
        end(FINISH_STATUS[d["status"]])
    elif t == "late_result" and terminal:
        s["late_results"].append(d["result"])
    elif t == "finish_conflict" and terminal:
        s["conflicts"].append({"sha256": d["sha256"], "status": d["status"], "at": rec["at"]})
    elif t == "timed_out" and active:
        end("timed_out")
    elif t == "cancelled" and active:
        s["cancel_reason"] = d["reason"]
        end("cancelled")
    elif t in ("waiter_returned", "withheld") and terminal:
        s["delivery"]["attempts"].append({"outcome": t, "at": rec["at"], "code": d["code"],
                                          "session": d.get("session")})
        if s["delivery"]["state"] != "accepted":
            s["delivery"]["state"] = t
    elif t == "accepted" and terminal and s["delivery"]["state"] != "accepted":
        s["delivery"].update(state="accepted", accepted=d["by"])
        if s["state"] in FINISHED:  # a cancelled or timed-out worker may still run: only release ends that
            s["released"] = s["released"] or {"at": rec["at"], "reason": "accepted"}
    elif t == "released" and terminal and not s["released"]:
        s["released"] = {"at": rec["at"], "reason": d["reason"]}
    elif t == "hint_armed" and s.get("hint") is None and s["policy"]["wake_adapter"] == "async-rewake-hint":
        s["hint"] = {"nonce": d["nonce"], "armed_session": d["session"], "armed_at": rec["at"],
                     "fired_at": None, "fired_state": None, "received": [], "retired": False}
    elif t == "hint_fired" and s.get("hint") and s["hint"]["nonce"] == d["nonce"] and not s["hint"]["fired_at"]:
        s["hint"].update(fired_at=rec["at"], fired_state=d["state"])
    elif t == "hint_received" and s.get("hint") and s["hint"]["nonce"] == d["nonce"]:
        s["hint"]["received"].append({"session": d["session"], "at": rec["at"], "duplicate": d["duplicate"]})
    elif t == "adopted" and s["delivery"]["state"] != "accepted":
        s["delivery"]["adopted_from"].append(s["parent"])
        s["parent"] = d["parent"]
        if s.get("hint"):
            s["hint"]["retired"] = True  # any adoption: the old notification carries no authority
        if s["delivery"]["mode"] == "wait":
            s["delivery"]["state"] = "pending"
    else:
        raise RunError("INVALID_TRANSITION", f"{t} in state {s['state']}")
    return s


# --- durable writes ------------------------------------------------------------

def sync(fd):
    """Flush a file to stable storage: F_FULLFSYNC where it exists (macOS),
    else fsync."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass  # not every file system supports it; fsync still orders the write
    os.fsync(fd)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        sync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _flock(path, busy, wait):
    wait = LOCK_WAIT if wait is None else wait
    try:
        f = path.open("a")
    except OSError:
        raise RunError("RUN_UNREADABLE", path.name) from None
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RunError("RUN_BUSY", busy) from None
                time.sleep(0.05)
        yield
    finally:
        f.close()


def locked(project, run_id, wait=None):
    """The run's lock for one transition, waited for at most LOCK_WAIT."""
    return _flock(run_dir(project, run_id) / "run.lock", "another transition holds the run lock", wait)


def claims_locked(project, wait=None):
    """The project's claim lock: taken before any run lock, never after one."""
    folder = PM_STATE / project / "runs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RunError("PERSIST_FAILED", str(e)) from None
    return _flock(folder / ".claims.lock", "another claim is in progress", wait)


def _write_state(folder, state):
    temp = folder / f"state.json.tmp-{os.getpid()}"
    try:
        temp.write_text(json.dumps(state, indent=1) + "\n")
        os.replace(temp, folder / "state.json")
    except OSError:
        temp.unlink(missing_ok=True)  # the journal is the truth; state.json is a cache


def append(project, run_id, type_, actor, data, *, create=False):
    """Append one checked transition. Call under locked() (create makes the
    folder and needs no lock: the run id is new). Returns the new state."""
    folder = run_dir(project, run_id)
    journal = folder / "journal.jsonl"
    if create:
        try:
            folder.mkdir(parents=True, exist_ok=False)
            sync_dir(folder.parent)
        except FileExistsError:
            raise RunError("INVALID_ARGUMENT", "run id already exists") from None
        except OSError as e:
            raise RunError("PERSIST_FAILED", str(e)) from None
        records, torn = [], 0
    else:
        records, torn = read_journal(journal)
    state = replay(records, project, run_id) if records else None
    rec = {"seq": len(records) + 1, "at": now_iso(), "type": type_, "actor": actor, "data": data}
    check_record(json.loads(json.dumps(rec)), rec["seq"])  # never write what a reader would refuse
    new = apply(state, rec)
    try:
        with journal.open("r+b" if journal.exists() else "wb") as f:
            if torn:
                f.truncate(f.seek(0, os.SEEK_END) - torn)  # the unacknowledged torn tail
            f.seek(0, os.SEEK_END)
            f.write((json.dumps(rec, sort_keys=True) + "\n").encode())
            f.flush()
            sync(f.fileno())
        if create:
            sync_dir(folder)
    except OSError as e:
        raise RunError("PERSIST_FAILED", str(e)) from None
    _write_state(folder, new)
    return new


def store_result(project, run_id, source):
    """Put a result file in the run's folder before anything names it.
    Returns {path, sha256, bytes}. Same content, same name: idempotent."""
    try:
        data = Path(source).read_bytes()
    except OSError:
        raise RunError("RESULT_UNREADABLE", str(source)) from None
    if len(data) > MAX_RESULT:
        raise RunError("RESULT_TOO_LARGE", f"{len(data)} bytes; the limit is {MAX_RESULT}")
    sha = hashlib.sha256(data).hexdigest()
    folder = run_dir(project, run_id)
    ext = re.sub(r"[^A-Za-z0-9.]", "", Path(source).suffix)[:12]
    target = folder / f"result-{sha[:12]}{ext}"
    temp = folder / f".{target.name}.tmp-{os.getpid()}"
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            sync(f.fileno())
        os.replace(temp, target)
        sync_dir(folder)
    except OSError as e:
        temp.unlink(missing_ok=True)
        raise RunError("PERSIST_FAILED", str(e)) from None
    return {"path": str(target), "sha256": sha, "bytes": len(data)}


# --- reading many runs ----------------------------------------------------------

def runs(project):
    """Every run id in a project's store (no validation of their contents)."""
    folder = PM_STATE / project / "runs"
    try:
        return sorted(p.name for p in folder.iterdir() if RUN_ID.fullmatch(p.name))
    except FileNotFoundError:
        return []


def silent(s):
    """Does this run still forbid its child's publication? While it is
    active, and after it ends until the parent accepts or explicitly releases
    it: never by a timer, so a delayed routine "done" post stays refused."""
    return s["policy"]["publish"] == "none" and (s["state"] in ACTIVE or not s["released"])


def attention(project, now=None):
    """Read-only lines for runs that need the manager: a failure or stop not
    yet acknowledged, a run past its deadline, a launch that may have created
    an unbound worker, an awaited result not yet accepted, a corrupt run. A
    silent success needs nothing and shows nothing. Nothing here changes a
    run: expiring one is childrun expire's job."""
    now = time.time() if now is None else now
    out = []
    for run_id in runs(project):
        try:
            s = load(project, run_id)
        except RunError as e:
            out.append(f"  {run_id}: {e.code} ({e.detail}); inspect it with childrun status")
            continue
        accepted = s["delivery"]["state"] == "accepted"
        what = f"  {run_id} {s['task']} (request {s['request_id']})"
        if s["state"] in ACTIVE:
            if (epoch(s["deadline"]) or 0) < now:
                out.append(f"{what} is {s['state']} past its deadline {s['deadline'][11:16]}Z; childrun expire or cancel")
            elif s["state"] == "launch_unverified":
                out.append(f"{what}: the launcher may have created a worker it could not verify; check the pane")
        elif s["state"] != "succeeded" and not accepted:
            out.append(f"{what} ended {s['state']}; read it and childrun accept")
        elif s["delivery"]["mode"] == "wait" and not accepted:
            out.append(f"{what} succeeded; its awaited result is not accepted yet")
        if s["late_results"] and not accepted:
            out.append(f"{what} has {len(s['late_results'])} late result(s) kept after {s['state']}")
    return out


def launch_record(registry, run_id):
    """(the registry record of the process launched for this run, code).
    identities.prepare_launch records CHAT_RUN_ID on the process modelclass
    execs into the provider: that pid, its start and its first provider
    session are the run's positive launch evidence."""
    found = [(n, r) for n, r in registry.items() if isinstance(r, dict) and r.get("run_id") == run_id]
    if len(found) != 1:
        return None, "LAUNCH_EVIDENCE_MISSING" if not found else "LAUNCH_EVIDENCE_AMBIGUOUS"
    name, r = found[0]
    if type(r.get("pid")) is not int or not _str(r.get("pid_start")) or not binding.original_session(r):
        return None, "LAUNCH_EVIDENCE_INCOMPLETE"
    return {**r, "name": name}, "OK"


def launch_refusal(s, me, registry):
    """Why this verified session is not the original invocation launched for
    this run, or None: the same process (pid and start), its FIRST provider
    session (not one after a /clear), the same name, project and role."""
    rec, code = launch_record(registry, s["run_id"])
    if rec is None:
        return code
    if (rec["pid"], " ".join(rec["pid_start"].split())) != (me["pid"], me["pid_start"]):
        return "NOT_THE_LAUNCHED_PROCESS"
    if binding.original_session(rec) != me["provider_session_id"]:
        return "NOT_THE_LAUNCHED_SESSION"
    if rec["name"] != me["name"] or rec.get("project") != s["project"] or rec.get("role") != s["role"]:
        return "LAUNCH_IDENTITY_MISMATCH"
    return None


def lease_path(project, session):
    """One lease per provider session: the run it is working for now."""
    key = hashlib.sha256(f"{session['brand']}:{session['provider_session_id']}".encode()).hexdigest()[:32]
    return PM_STATE / project / "runs" / ".leases" / f"{key}.json"


def read_lease(project, session):
    """The run id this provider session holds its lease for, or None.
    Unreadable or malformed is RUN_CORRUPT: a lease is authority."""
    try:
        lease = binding.strict_loads(lease_path(project, session).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, binding.Unavailable):
        raise RunError("RUN_CORRUPT", "a session lease cannot be read") from None
    if not (isinstance(lease, dict) and isinstance(lease.get("run_id"), str) and RUN_ID.fullmatch(lease["run_id"])
            and lease.get("session") == session["provider_session_id"]):
        raise RunError("RUN_CORRUPT", "a session lease is malformed")
    return lease["run_id"]


def write_lease(project, run_id, session):
    """Give this provider session's lease to a run (call under the claim
    lock). A later claim by the same session moves it: the old run's
    invocation is then over, for finish, publication and release alike."""
    path = lease_path(project, session)
    try:
        created = not path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        if created:  # the new .leases name itself must survive, not only the lease inside it
            sync_dir(path.parent.parent)
        temp = path.with_name(path.name + f".tmp-{os.getpid()}")
        with temp.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": run_id, "session": session["provider_session_id"],
                                "pid": session["pid"], "pid_start": session["pid_start"], "at": now_iso()}))
            f.flush()
            sync(f.fileno())
        os.replace(temp, path)
        sync_dir(path.parent)
    except OSError as e:
        raise RunError("PERSIST_FAILED", str(e)) from None


def invocation_refusal(s, me, inv):
    """Why this verified session is not (or no longer) the run's invocation,
    or None. Rechecked on every claim, finish and publication path:
    - claimed: the same session, still holding this run's lease (a second
      run claimed in the same provider session moves it);
    - launched or unverified tab: also still the run's launch record (a
      replaced run id on the process ends this run's invocation);
    - a reused tab before its claim: never (only a candidate)."""
    kind = (s.get("launch") or {}).get("kind")
    if s["child"]:
        if not binding.same_session(me, s["child"]):
            return "NOT_THE_CHILD"
        lease = read_lease(s["project"], me)
        if lease is None:  # after a claim, a missing lease is unknown, never proof the invocation moved on
            raise RunError("LEASE_MISSING", "the claimed session's lease is missing")
        if lease != s["run_id"]:
            return "LEASE_MOVED"  # positive: the same session holds a lease for another run
    elif kind not in ("launched", "unverified"):
        return "NOT_CLAIMED"
    if kind in ("launched", "unverified"):
        return launch_refusal(s, me, inv.registry())
    return None


def is_invocation(s, me, inv):
    """Is this verified session the run's own invocation right now?"""
    return invocation_refusal(s, me, inv) is None


def silent_refusal(account, env, inv=None):
    """Why this poster may not publish now, or None. Silence applies to a
    run's bound invocation, judged by the poster's verified current session:

    - if the environment names a silent run (CHAT_RUN_ID), the poster is
      refused unless its verified session is positively NOT that run's
      invocation (a /clear in the same process is a new invocation);
    - on a silent run's own tab (its child's or its new tab), a verified
      session that is the run's invocation is refused, and an unverifiable
      session fails closed;
    - an account name or an old registry key never decides it, so another
      job of the same account (a pre-run outbox entry, a later task) is not
      refused for this run.

    Not covered before its claim: a reused tab, whose session is only a
    candidate. An environment naming a run that cannot be read is refused;
    unrelated unreadable runs never block other posters."""
    inv = inv or binding.Inventory(pm_state=PM_STATE)
    run_env = env.get("CHAT_RUN_ID")
    surface = env.get("CMUX_SURFACE_ID") or ""
    selves = {}

    def me(project):
        if project not in selves:
            selves[project] = binding.resolve_self(project, None, env=env, inv=inv)
        return selves[project]
    if run_env:
        try:
            s = load(find(run_env), run_env)
        except RunError as e:
            return f"run {run_env} cannot be read ({e.code}); a silent run's posts are refused"
        if silent(s):
            mine, code = me(s["project"])
            try:
                if code != "OK" or is_invocation(s, mine, inv):
                    return f"run {run_env} is silent (publish none); record the result with childrun finish"
            except (binding.Unavailable, RunError):
                return f"run {run_env} is silent and its invocation evidence cannot be read"
    for folder in PM_STATE.glob("*/runs"):
        project = folder.parent.name
        for rid in runs(project):
            if rid == run_env:
                continue
            try:
                s = load(project, rid)
            except RunError:
                continue
            if not silent(s):
                continue
            launch = s.get("launch") or {}
            watched = (s["child"] or {}).get("surface_id") or (
                launch.get("surface_id") if launch.get("kind") == "launched" and s["state"] in ACTIVE else None)
            if not (surface and watched and surface.upper() == watched.upper()):
                continue
            mine, code = me(project)
            if code != "OK":
                return f"this tab belongs to silent run {rid} and its session cannot be verified ({code})"
            try:
                if is_invocation(s, mine, inv):
                    return f"this session is silent run {rid}'s invocation; record the result with childrun finish"
            except (binding.Unavailable, RunError):
                return f"this tab belongs to silent run {rid} and its invocation evidence cannot be read"
    return None
