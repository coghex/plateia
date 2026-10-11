#!/usr/bin/env python3
"""Guarded activation of one staged plateia release (PLT-14, #21; D-74, D-26, D-27).

It carries #19's release through the one admitted format transition
(activation.json): from the D-19 baseline formats to #19's, plus the one
designation-bootstrap/1 read input (#21's owner amendment of 2026-10-11).

    activate_release plan --release DIR --dest DIR --root DIR --evidence FILE
                          (--designate NAME ... | --no-designations) --out FILE
    activate_release stage --plan FILE
    activate_release activate --plan FILE --evidence FILE
    activate_release postcheck --plan FILE --evidence FILE
    activate_release rollback --plan FILE --evidence FILE
    activate_release operator-plan --plan FILE
    activate_release status --root DIR

- plan: read-only. It seals one release, the seven D-26 targets as they are,
  one state snapshot and the state root's identity, activation.json's
  transition, a private root and the designation list (or an explicit empty
  one) under one digest, with a new operation id.
- stage: stages the release under the plan's admission (stage_release).
- activate: under a fenced journal, once the old writers are quiet, it
  publishes the bridge's designation bootstrap record (derived from the
  sealed plan; never replacing anything), journals its digest D, and binds
  the seven targets to the staged release, every original backed up
  privately. The chat-bridge LaunchAgent runs the release's chat-bridge with
  exactly `--activation-bootstrap sha256:<D>`. It starts, stops and restarts
  nothing: the operator restarts the bridge at an acknowledged checkpoint, as
  the published operator plan says.
- postcheck: read-only, with the new bridge running; it never opens the
  outbox database. The activation is complete only when it passes.
- rollback: before the first new-format write only (D-74); refused after it.
  It leaves the bootstrap record in place.
- operator-plan: the private plan an operator publishes before any live step
  (A9), with the expected skills-repository change (D-27).

Nothing here is a service action. Implementing or reviewing it never
authorizes a live activation (PLT-14); that rests on the owner's decision and
the published plan (D-74).
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import io
import json
import os
import plistlib
import re
import secrets
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_release  # noqa: E402
import stage_release  # noqa: E402
from build_release import MANIFEST, Refused  # noqa: E402
from stage_release import (digest, first_write_evidence, load_activation, plan_digest, shown,  # noqa: E402
                           state_root_identity)

PLAN_SCHEMA = stage_release.ACTIVATION_PLAN_SCHEMA
JOURNAL_SCHEMA = "plateia-activation-journal/1"
JOURNAL, LOCK, BACKUPS = "activation-journal.jsonl", "activation.lock", "backups"
ACCOUNT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
LEGACY_KINDS = ("bridge", "flusher", "helper", "cached-producer")
BRIDGE_AGENT = "chat-bridge LaunchAgent"
BOOTSTRAP_OPTION = "--activation-bootstrap"
HANDOFF = """{shebang}# plateia handoff layer (D-26, PLT-14): this name runs the selected release's copy,
# {script}
import os as _os
import sys as _sys

_RELEASE = {script!r}
if __name__ == "__main__" and {command!r}:
    _os.execv({command!r}, [{command!r}, *_sys.argv[1:]])
_sys.path.insert(0, _os.path.dirname(_RELEASE))
__file__ = _RELEASE
with open(_RELEASE, "rb") as _f:
    exec(compile(_f.read(), _RELEASE, "exec"))
"""


class Crash(BaseException):
    """Stands in for the process being killed (tests' crash injection)."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# --- what the controller reads about services, processes and checkpoints -------

class LiveOps:
    """The live reads, all read-only: the bridge service's state (launchctl
    print), and the operator's checkpoint evidence file (the old writers'
    acknowledged checkpoints and each designated account's producers). Tests
    use fakes instead. Nothing here starts, stops or signals anything."""

    LABEL = "com.coghex.chat-bridge"

    def __init__(self, evidence=None):
        self.evidence = Path(evidence) if evidence else None

    def bridge_state(self):
        try:
            out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{self.LABEL}"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            return {"running": None, "release": None, "unknown": f"launchctl print failed ({e.__class__.__name__})"}
        if out.returncode != 0:
            return {"running": False, "release": None}
        running = re.search(r"^\s*state = running\s*$", out.stdout, re.M) is not None
        program = re.search(r"^\s*program = (\S+)\s*$", out.stdout, re.M)
        found = re.search(r"/releases/([^/]+)/env/bin/chat-bridge$", program.group(1)) if program else None
        return {"running": running, "release": found.group(1) if found else "skills-tree"}

    def _evidence(self):
        if self.evidence is None:
            return None
        try:
            data = json.loads(self.evidence.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def writers(self):
        data = self._evidence()
        return data.get("writers") if data and isinstance(data.get("writers"), list) else None

    def producers(self):
        data = self._evidence()
        return data.get("producers") if data and isinstance(data.get("producers"), dict) else None


def bridge_verdict(ops, plan):
    """"stopped", or "expected" for a running bridge of this plan's release;
    Refused for a running bridge of anything else or an unknown state."""
    bridge = ops.bridge_state() or {}
    if bridge.get("running") is None:
        raise Refused(f"blocked: the bridge's state is unknown ({bridge.get('unknown', 'no evidence')}); "
                      "nothing was changed")
    if not bridge["running"]:
        return "stopped"
    if bridge.get("release") == plan["release"]:
        return "expected"
    raise Refused(f"blocked: a chat bridge ({bridge.get('release') or 'unknown release'}) is running that is not "
                  "this operation's; nothing was changed")


def check_quiet(ops, plan):
    """Refused unless the old writers are quiet: every legacy writer (old
    bridge, flusher, helper, already-running or cached producer) at an
    acknowledged checkpoint, and every designated account's producers tracked.
    A delivered quiet request is not an acknowledgement; anything unknown is
    never assumed quiet. Nothing is interrupted, restarted or killed (A3)."""
    writers = ops.writers()
    if writers is None:
        raise Refused("blocked: no checkpoint evidence for the old writers; nothing was changed")
    for w in writers:
        if not isinstance(w, dict) or not isinstance(w.get("name"), str):
            raise Refused("blocked: the checkpoint evidence is malformed; nothing was changed")
        if w.get("release") == plan["release"]:
            continue
        if w.get("kind") not in LEGACY_KINDS:
            raise Refused(f"blocked: {w['name']}: an unknown kind of writer; nothing was changed")
        state = w.get("checkpoint")
        if state == "acknowledged":
            continue
        if state == "requested":
            raise Refused(f"blocked: {w['name']}: a quiet request was delivered but not acknowledged; "
                          "nothing was changed")
        raise Refused(f"blocked: {w['name']}: its checkpoint is unknown, and it is never assumed quiet; "
                      "nothing was changed")
    producers = ops.producers()
    if plan["designations"]:
        if producers is None:
            raise Refused("blocked: no producer evidence for the designated accounts; nothing was changed")
        for account in plan["designations"]:
            if producers.get(account) != "tracked":
                raise Refused(f"blocked: account {account}: its producers are not all tracked "
                              f"({producers.get(account) or 'unknown'}); the designation list is never changed "
                              "silently, and nothing was changed")


# --- the plan ----------------------------------------------------------------------

def make_plan(release, dest, root, designations, ops, spec_path=None, activation_path=None):
    """An activation plan: read-only. Refused if the release can't be staged,
    a target is blocked, the state already holds a new-format write or a
    bootstrap record, or the designation list is invalid."""
    spec = load_activation(activation_path)
    if designations is None or not isinstance(designations, (list, tuple)):
        raise Refused("a designation list, or an explicit empty one, is required")
    names = list(designations)
    if len(set(names)) != len(names) or not all(isinstance(n, str) and ACCOUNT.fullmatch(n) for n in names):
        raise Refused("the designation list holds an invalid or repeated account name")
    release = Path(os.path.abspath(release))
    manifest = build_release.verify(release)
    problem = stage_release.identity_problem(manifest)
    if problem:
        raise Refused(f"the manifest can't be staged: {problem}")
    stage_plan = stage_release.make_plan(release, dest, spec_path)
    if stage_plan["blocked"]:
        raise Refused("blocked: " + "; ".join(stage_plan["blocked"]))
    if [t["name"] for t in stage_plan["targets"]] != spec["targets"]:
        raise Refused("the staging spec's targets are not D-26's seven managed targets")
    root_id = state_root_identity()
    if root_id is None:
        raise Refused("the chat state root can't be read")
    snap = stage_release.state_snapshot()
    written = {k: v for k, v in snap["first_writes"].items() if v != "absent"}
    if written:
        raise Refused("this state already holds a new-format write or can't show it doesn't ("
                      + "; ".join(f"{k}: {v}" for k, v in written.items())
                      + "): this is not a pre-#19 state; the recovery is repair or roll-forward")
    if os.path.lexists(Path(root_id["path"]) / spec["bootstrap"]["file"]):
        raise Refused("a designation bootstrap record is already in the chat state; an activation never replaces "
                      "one it didn't publish: this needs an owner decision")
    writers = ops.writers() or []
    plan = {
        "schema": PLAN_SCHEMA, "operation": "activate-" + secrets.token_hex(8), "made_at": now(),
        "release": manifest["release"], "release_path": str(release),
        "manifest_sha256": stage_release.drift_check.sha256(release / MANIFEST),
        "source_commit": manifest["source"]["commit"],
        "artifacts": [{"name": a["name"], "sha256": a["sha256"]} for a in manifest["artifacts"]],
        "transition": spec["transition"],
        "activation_sha256": stage_release.drift_check.sha256(Path(activation_path or stage_release.ACTIVATION_PATH)),
        "dest": os.path.abspath(dest), "staged_release": os.path.abspath(Path(dest) / "releases" / manifest["release"]),
        "root": os.path.abspath(root), "state_root": root_id,
        "targets": stage_plan["targets"], "importers": stage_plan["importers"], "state_snapshot": snap,
        "designations": sorted(names),
        "checkpoints": [{"name": n, "kind": k, "owner": o} for n, k, o in sorted(
            {(w.get("name"), w.get("kind"), w.get("owner")) for w in writers if isinstance(w, dict)}, key=str)],
    }
    plan["digest"] = plan_digest(plan)
    return plan


def save_plan(plan, path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return Path(path)


def load_plan(path):
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"the activation plan can't be read ({e.__class__.__name__})") from None
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA or plan.get("digest") != plan_digest(plan):
        raise Refused("not an activation plan, or one altered after it was made")
    return plan


# --- the designation bootstrap record (#21's owner amendment, sections 2 and 4) -------

def release_fields(plan):
    """The record's two release fields, from the sealed plan (#21's owner
    amendment 2): the pinned manifest hash and the release identity. The bridge
    checks the identity against its own path; the manifest hash reaches it only
    inside the bytes D covers, and the post-check verifies both against the
    plan and the staged release."""
    return {"release": plan["manifest_sha256"], "release_id": plan["release"]}


def bootstrap_bytes(plan):
    """The record's exact canonical bytes, derived only from the sealed plan:
    the same plan always gives the same bytes, and so the same D."""
    record = {"v": load_activation()["bootstrap"]["schema"], "operation": plan["operation"],
              **release_fields(plan), "state_root": plan["state_root"], "accounts": plan["designations"],
              "plan_sha256": plan["digest"], "time": plan["made_at"]}
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def bootstrap_path(plan):
    return Path(plan["state_root"]["path"]) / load_activation()["bootstrap"]["file"]


def read_private(path):
    """(bytes, (dev, ino), ctime_ns) of a private regular file of this user,
    opened without following links; FileNotFoundError, or Refused otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise
    except OSError as e:
        raise Refused(f"{shown(path)} can't be opened ({e.strerror or e})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise Refused(f"{shown(path)} is not a private regular file of this user")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), (st.st_dev, st.st_ino), st.st_ctime_ns
    finally:
        os.close(fd)


def publish_bootstrap(journal, plan):
    """Publish the record and journal D; returns D, or Refused with everything
    retained.

    Each step is journaled: an intent naming the temporary file; that file
    created exclusively, written and synced, its inode journaled; link() to
    the final path, which never replaces a file; the directory synced. Then
    reconciliation (#21's APPROVE, spec addition 2): the final record is the
    very inode this operation's journal staged, holds exactly the plan's bytes,
    sits in the plan's state root (path, device and inode) and is durable.
    Only then is D journaled. A record whose ownership this operation's own
    journal can't show is foreign: it is retained, and the activation stops."""
    op = plan["operation"]
    data = bootstrap_bytes(plan)
    d = sha256_bytes(data)
    final = bootstrap_path(plan)
    if state_root_identity(plan["state_root"]["path"]) != plan["state_root"]:
        raise Refused("blocked: the chat state root is not the plan's (its path, device or inode differ); "
                      "nothing more was changed")
    mine = [r for r in journal.records if r.get("op") == op]
    last = lambda kind: next((r for r in reversed(mine) if r["kind"] == kind), None)  # noqa: E731
    done = last("bootstrap")
    if done is not None:
        if done.get("digest") != d:
            raise Refused("the journaled bootstrap digest is not this plan's; nothing more was changed")
        reconcile_bootstrap(plan, data, tuple(done["inode"]), done.get("ctime_ns"))
        return d
    published = last("bootstrap-published")
    if published is not None:
        inode = tuple(published["inode"])
        reconcile_bootstrap(plan, data, inode)
    else:
        staged, inode = last("bootstrap-staged"), None
        if staged is not None:
            inode = tuple(staged["inode"])
            try:
                found = os.lstat(final)
            except FileNotFoundError:
                found = None
            if found is not None and (found.st_dev, found.st_ino) != inode:
                raise Refused(f"blocked: {shown(final)} is not the record this operation staged (another inode): "
                              "it is retained, and this needs an owner decision")
            if found is None:
                try:
                    st = os.lstat(final.parent / staged["tmp"])
                except FileNotFoundError:
                    st = None
                if st is None or (st.st_dev, st.st_ino) != inode:
                    inode = None  # the staged copy is gone or replaced: stage again, under a new name
                else:
                    os.link(final.parent / staged["tmp"], final)
        elif os.path.lexists(final):
            raise Refused(f"blocked: {shown(final)} exists, and this operation's journal never staged a record: "
                          "it is retained, never replaced, and this needs an owner decision")
        if inode is None:
            if os.path.lexists(final):
                raise Refused(f"blocked: {shown(final)} appeared, and this operation didn't publish it: it is "
                              "retained, and this needs an owner decision")
            attempt = sum(1 for r in mine if r["kind"] == "bootstrap-intent") + 1
            tmp = final.parent / f".designation-bootstrap.{op}.{attempt}.tmp"
            journal.append({"op": op, "kind": "bootstrap-intent", "tmp": tmp.name, "digest": d})
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
                st = os.fstat(fd)
            finally:
                os.close(fd)
            inode = (st.st_dev, st.st_ino)
            journal.append({"op": op, "kind": "bootstrap-staged", "tmp": tmp.name, "inode": list(inode)})
            os.link(tmp, final)  # FileExistsError: something else published first; nothing is replaced
        fsync_dir(final.parent)
        reconcile_bootstrap(plan, data, inode)
        journal.append({"op": op, "kind": "bootstrap-published", "inode": list(inode)})
    debt = []
    for r in journal.records:
        if r.get("op") != op or r["kind"] not in ("bootstrap-intent", "bootstrap-staged"):
            continue
        tmp = final.parent / r["tmp"]
        try:
            st = os.lstat(tmp)
        except FileNotFoundError:
            continue
        if r["kind"] == "bootstrap-staged" and (st.st_dev, st.st_ino) == tuple(r["inode"]):
            with contextlib.suppress(OSError):
                os.unlink(tmp)  # this operation's own staged copy of the published record: cleanup only
        elif not any(s.get("tmp") == r["tmp"] for s in journal.records
                     if s.get("op") == op and s["kind"] == "bootstrap-staged"):
            debt.append(r["tmp"])  # never journaled as ours: kept, and reported
    # Once the staged copy is gone nothing holds the inode, and a file system may
    # give its number to a replacement: the change time is journaled with it.
    ctime_ns = reconcile_bootstrap(plan, data, inode)
    journal.append({"op": op, "kind": "bootstrap", "digest": d, "inode": list(inode), "ctime_ns": ctime_ns,
                    "cleanup_debt": debt})
    return d


def reconcile_bootstrap(plan, data, inode, ctime_ns=None):
    """The published record is this operation's, exact and durable; returns its
    change time. Refused otherwise, with everything retained. `ctime_ns`, once
    journaled with D, must match too: an inode number alone can be reused."""
    final = bootstrap_path(plan)
    try:
        found, ident, ctime = read_private(final)
    except FileNotFoundError:
        raise Refused(f"blocked: {shown(final)} is missing after this operation published it; nothing more was "
                      "changed") from None
    if ident != inode or (ctime_ns is not None and ctime != ctime_ns):
        raise Refused(f"blocked: {shown(final)} is not the file this operation published (another inode, or "
                      "replaced since); it is retained")
    if found != data:
        raise Refused(f"blocked: {shown(final)} doesn't hold this plan's exact record; it is retained")
    if state_root_identity(plan["state_root"]["path"]) != plan["state_root"]:
        raise Refused("blocked: the chat state root is not the plan's; nothing more was changed")
    fd = os.open(final, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(final.parent)
    return ctime


# --- the journal ----------------------------------------------------------------------

class Journal:
    """The activation journal in the private root, behind an exclusive lock: a
    concurrent upgrader is refused, never interleaved. Each record is appended
    and synced; a torn last record is dropped on reading."""

    def __init__(self, root, crash=None):
        self.root, self.crash = Path(root), crash
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = open(self.root / LOCK, "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise Refused("blocked: another activation holds the journal lock; nothing was changed") from None
        try:
            self.records = read_journal(self.root)
        except BaseException:
            self.close()
            raise

    def append(self, record):
        record = dict(record, schema=JOURNAL_SCHEMA, at=now())
        fd = os.open(self.root / JOURNAL, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        self.records.append(record)
        if self.crash:
            self.crash(record)

    def close(self):
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_journal(root):
    try:
        lines = (Path(root) / JOURNAL).read_text(encoding="utf-8").split("\n")
    except FileNotFoundError:
        return []
    records = []
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            if n == len(lines) - 1:  # a torn last record: the step it began is reconciled from reality
                break
            raise Refused("the activation journal is damaged before its last record; nothing was changed") from None
    return records


def operation_state(records, op):
    mine = [r for r in records if r.get("op") == op]
    kinds = [r["kind"] for r in mine]
    return {"records": mine, "admitted": "admitted" in kinds, "selected": "selected" in kinds,
            "rolled_back": "rolled-back" in kinds,
            "bound": {r["target"] for r in mine if r["kind"] == "bound"},
            "backups": {r["target"]: r for r in mine if r["kind"] == "backup"},
            "digest": next((r["digest"] for r in mine if r["kind"] == "bootstrap"), None)}


# --- the staged release and the seven targets ------------------------------------------

def staged_layout(plan):
    """Where the staged release's commands, scripts and skill are; the stage
    operation must be complete and admitted by this plan."""
    complete = [op for op in stage_release.operations(
        stage_release.read_journal(Path(plan["dest"]) / stage_release.JOURNAL)).values()
        if op["complete"] and (op["binding"] or {}).get("release") == plan["release"]]
    if not any((op["binding"] or {}).get("admission", {}).get("plan_sha256") == plan["digest"] for op in complete):
        raise Refused("the release is not staged under this plan's admission; run stage --plan first")
    rel = Path(plan["staged_release"])
    env = rel / "env"
    sites = sorted(env.glob("lib/python*/site-packages/plateia_chat/scripts"))
    if len(sites) != 1:
        raise Refused("the staged environment has no single plateia_chat/scripts")
    manifest = json.loads((rel / "artifacts" / MANIFEST).read_text(encoding="utf-8"))
    with zipfile.ZipFile(io.BytesIO((rel / "artifacts" / manifest["skill"]["artifact"]).read_bytes())) as z:
        skill_md = z.read(f"{manifest['skill']['name']}/SKILL.md")
    files = sorted(p.name for p in sites[0].iterdir() if p.is_file())
    return {"env": env, "bin": env / "bin", "scripts": sites[0], "skill": skill_md, "files": files}


def target_paths(spec_targets, skills):
    return {t["name"]: (t, stage_release.expand(t["path"], skills)) for t in spec_targets}


def file_entry(p):
    """A regular file's (sha256, mode, bytes); None when missing; anything else
    (a symlink swapped in, say) as what it is, never followed."""
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return {"not-a-file": os.readlink(p) if stat.S_ISLNK(st.st_mode) else stat.S_IFMT(st.st_mode)}
    data = Path(p).read_bytes()
    return {"sha256": sha256_bytes(data), "mode": st.st_mode & 0o777, "bytes": data}


def dir_entry(p):
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return None
    return {"directory": True} if stat.S_ISDIR(st.st_mode) else {"not-a-directory": stat.S_IFMT(st.st_mode)}


def read_target(t, path, layout):
    """A target's current state, as {rel: entry}: a symlink's target, a file's
    (sha256, mode, bytes), a folder's own kind (".") or None for a missing
    entry. Nothing is followed through a link."""
    if t["kind"] == "symlink":
        return {"": {"link": os.readlink(path)} if os.path.islink(path) else
                ({"not-a-link": True} if os.path.lexists(path) else None)}
    if t["kind"] == "launchagent":
        return {"": file_entry(path)}
    folder = {".": dir_entry(path)}
    if folder["."] != {"directory": True}:
        return folder
    if t["kind"] == "skill-folder":
        return dict(folder, **{"SKILL.md": file_entry(path / "SKILL.md")})
    return dict(folder, **{n: file_entry(path / n) for n in layout["files"]})


def effective_executable(plist):
    """What launchd executes: Program when set, else ProgramArguments[0]."""
    if "Program" in plist:
        return plist["Program"]
    arguments = plist.get("ProgramArguments")
    return arguments[0] if isinstance(arguments, list) and arguments else None


def desired_invocation(t, layout, d):
    command = "chat-bridge" if t["name"] == BRIDGE_AGENT else "rotate-logs"
    arguments = [str(layout["bin"] / command)]
    if t["name"] == BRIDGE_AGENT:
        arguments += [BOOTSTRAP_OPTION, f"sha256:{d}"]
    return arguments


def desired_target(t, path, layout, original, d=None):
    if t["kind"] == "symlink" and t["name"] == "Claude chat skill link":
        return original  # unchanged: still a symlink to the chat skill folder
    if t["kind"] == "symlink":
        dest = layout["bin"] / "pchat" if t["name"] == "pchat" else layout["scripts"] / "role_colors.py"
        return {"": {"link": str(dest)}}
    if t["kind"] == "launchagent":
        old = original[""]
        if old is None:
            raise Refused(f"blocked: {t['name']} is missing")
        data = plistlib.loads(old["bytes"])
        data.pop("Program", None)  # a retained Program would override ProgramArguments[0] (review correction C1)
        data["ProgramArguments"] = desired_invocation(t, layout, d)
        new = plistlib.dumps(data)
        return {"": {"sha256": sha256_bytes(new), "mode": old["mode"], "bytes": new}}
    if t["kind"] == "skill-folder":
        return {".": {"directory": True},
                "SKILL.md": {"sha256": sha256_bytes(layout["skill"]), "mode": 0o644, "bytes": layout["skill"]}}
    out = {".": {"directory": True}}
    for name in layout["files"]:
        command = "" if name.endswith(".py") else str(layout["bin"] / name)
        data = HANDOFF.format(shebang="" if not command else "#!/usr/bin/env python3\n",
                              script=str(layout["scripts"] / name), command=command).encode()
        out[name] = {"sha256": sha256_bytes(data), "mode": 0o644 if not command else 0o755, "bytes": data}
    return out


def invocation_problem(t, path, layout, d):
    """For a bound LaunchAgent: None when launchd would run exactly the desired
    invocation (its effective executable and its arguments); else why not."""
    entry = file_entry(path)
    if entry is None:
        return "missing"
    try:
        plist = plistlib.loads(entry["bytes"])
    except (plistlib.InvalidFileException, ValueError):
        return "not a readable property list"
    want = desired_invocation(t, layout, d)
    if plist.get("ProgramArguments") != want:
        return "its ProgramArguments are not the pinned release's invocation"
    if effective_executable(plist) != want[0]:
        return "launchd would execute something else (its Program key)"
    return None


def summary_of(state):
    """A state's comparable digest, without the bytes."""
    return digest({k: (None if v is None else {kk: vv for kk, vv in v.items() if kk != "bytes"})
                   for k, v in sorted(state.items())})


def write_target(t, path, state):
    """Make the target `state`, entry by entry, each atomically (a same-
    directory temporary, synced, renamed into place)."""
    if state.get(".", {"directory": True}) != {"directory": True}:
        raise Refused(f"{t['name']} is not a real directory; nothing was written into it")
    for rel, entry in sorted(state.items()):
        if rel == ".":
            continue  # the folder itself is never written: only checked
        p = path / rel if rel else path
        if entry is None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
            continue
        tmp = p.parent / f".plateia-activation-{secrets.token_hex(4)}"
        if "link" in entry:
            os.symlink(entry["link"], tmp)
        else:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, entry["mode"])
            try:
                os.write(fd, entry["bytes"])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(tmp, entry["mode"])
        os.replace(tmp, p)
        fsync_dir(p.parent)


def private_write(path, data):
    """A new private file (0600), synced: backups can hold a LaunchAgent's
    environment, which is never shown or copied anywhere else."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def backup_target(root, op, t, state):
    """The target's original state, kept privately under the root (D-27)."""
    folder = Path(root) / BACKUPS / op / re.sub(r"[^A-Za-z0-9._-]+", "_", t["name"])
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    index = {}
    for rel, entry in sorted(state.items()):
        if entry is None or "bytes" not in entry:
            index[rel] = entry
            continue
        name = f"entry-{len(index)}"
        private_write(folder / name, entry["bytes"])
        index[rel] = {"sha256": entry["sha256"], "mode": entry["mode"], "file": name}
    private_write(folder / "index.json", (json.dumps(index, sort_keys=True) + "\n").encode())
    fsync_dir(folder)
    return folder


def restore_state(folder):
    index = json.loads((Path(folder) / "index.json").read_text())
    out = {}
    for rel, entry in index.items():
        if entry is None or "file" not in entry:
            out[rel] = entry
            continue
        data = (Path(folder) / entry["file"]).read_bytes()
        if sha256_bytes(data) != entry["sha256"]:
            raise Refused(f"the private backup {shown(folder)} doesn't verify; nothing was restored")
        out[rel] = {"sha256": entry["sha256"], "mode": entry["mode"], "bytes": data}
    return out


def skills_change(skills, layout, paths):
    """The expected change to the skills repository's working tree (D-27):
    paths under the skills root and their new contents; the owner commits it."""
    out = []
    for name in ("chat skill folder", "chat/scripts import location"):
        t, path = paths[name]
        for rel, entry in sorted(desired_target(t, path, layout, None).items()):
            if rel == ".":
                continue
            p = (path / rel) if rel else path
            with contextlib.suppress(ValueError):
                out.append({"path": p.relative_to(skills).as_posix(), "sha256": entry["sha256"],
                            "mode": oct(entry["mode"])})
    return out


def skills_root(spec_path):
    return Path(os.path.expanduser(stage_release.load_spec(spec_path)[0]["skills_root"]))


# --- activate, postcheck, rollback ------------------------------------------------------

def stage_admitted(plan_path, python=None, crash=None, spec_path=None, ops=None):
    """Stage the plan's release under its admission (stage_release)."""
    plan = load_plan(plan_path)
    used = operation_state(read_journal(plan["root"]), plan["operation"])
    if used["selected"] or used["rolled_back"]:
        raise Refused("this activation plan's operation already ran; a new activation needs a new plan")
    adm = stage_release.admission(plan, plan["release_path"], spec_path)
    return stage_release.stage(plan["release_path"], plan["dest"], python, None, spec_path, crash, adm)


def activate(plan_path, ops, spec_path=None, crash=None, activation_path=None):
    """Publish the bootstrap and bind the seven targets, under the fenced journal.
    A resume takes the original plan for its own journaled operation only."""
    plan = load_plan(plan_path)
    spec_targets = stage_release.load_spec(spec_path)[0]["targets"]
    with Journal(plan["root"], crash) as journal:
        for r in journal.records:
            if r.get("op") not in (None, plan["operation"]) and r["kind"] == "admitted":
                other = operation_state(journal.records, r["op"])
                if not other["rolled_back"]:
                    raise Refused(f"blocked: operation {r['op']} is "
                                  f"{'selected' if other['selected'] else 'unfinished'}; a renewed or interleaved "
                                  "migration is refused; nothing was changed")
        mine = operation_state(journal.records, plan["operation"])
        if mine["rolled_back"]:
            raise Refused("this operation was rolled back; a new activation needs a new plan")
        if mine["selected"]:
            return dict(postcheck(plan_path, ops, spec_path), state="already selected")
        if not mine["admitted"]:
            stage_release.admission(plan, plan["release_path"], spec_path, activation_path)
        elif [r for r in mine["records"] if r["kind"] == "admitted"][0].get("plan_sha256") != plan["digest"]:
            raise Refused("the journaled operation was admitted with other inputs; nothing was changed")
        layout = staged_layout(plan)
        # The expected new bridge, started under this operation, allows only a
        # read-only reconcile; any other running bridge blocks (review correction 2).
        read_only = bridge_verdict(ops, plan) == "expected"
        if read_only and mine["digest"] is None:
            raise Refused("blocked: a bridge of this release is running, but this operation never journaled its "
                          "bootstrap; nothing was changed")
        if not read_only:
            check_quiet(ops, plan)
        if not mine["admitted"]:
            journal.append({"op": plan["operation"], "kind": "admitted", "plan_sha256": plan["digest"],
                            "release": plan["release"], "targets": {t["name"]: t["digest"] for t in plan["targets"]}})
        skills = skills_root(spec_path)
        paths = target_paths(spec_targets, skills)
        pending = [t["name"] for t in plan["targets"] if t["name"] not in mine["backups"]]
        if pending:
            # Every original is backed up before anything is published or bound,
            # so a target still unbacked has not been touched: it must still be
            # exactly as the plan observed it, or nothing more happens.
            if read_only or any(r["kind"] in ("binding", "bound", "bootstrap-intent") for r in mine["records"]):
                raise Refused(f"blocked: {', '.join(pending)} has no backup, but this operation already went past "
                              "its backups; nothing was changed")
            now_targets = stage_release.make_plan(plan["release_path"], None, spec_path)["targets"]
            changed = [n["name"] for n, old in zip(now_targets, plan["targets"])
                       if n["name"] in pending and (n["digest"] != old["digest"] or n["problems"])]
            if changed:
                raise Refused(f"blocked: {', '.join(changed)} changed since the activation plan was made; "
                              "nothing more was changed")
            for name in pending:
                t, path = paths[name]
                current = read_target(t, path, layout)
                folder = backup_target(plan["root"], plan["operation"], t, current)
                journal.append({"op": plan["operation"], "kind": "backup", "target": name,
                                "folder": str(folder), "digest": summary_of(current)})
            mine = operation_state(journal.records, plan["operation"])
        d = sha256_bytes(bootstrap_bytes(plan))  # the plan alone determines D
        if mine["digest"] not in (None, d):
            raise Refused("the journaled bootstrap digest is not this plan's; nothing was changed")
        steps = []
        for t_plan in plan["targets"]:  # every target is checked before anything is published or written
            t, path = paths[t_plan["name"]]
            current = read_target(t, path, layout)
            original = restore_state(mine["backups"][t["name"]]["folder"])
            desired = desired_target(t, path, layout, original, d)
            if summary_of(current) == summary_of(desired):
                steps.append((t, path, desired, False))
                continue
            if read_only:
                raise Refused(f"blocked: {t['name']} isn't bound while a bridge runs; binding waits for it to stop "
                              "at an acknowledged checkpoint; nothing was changed")
            if summary_of(current) != summary_of(original):
                raise Refused(f"blocked: {t['name']} is neither as recorded before activation nor as bound: it was "
                              "changed by something else; nothing more was changed")
            steps.append((t, path, desired, True))
        if not read_only:
            publish_bootstrap(journal, plan)
        for t, path, desired, write in steps:
            if not write:
                if t["name"] not in mine["bound"]:
                    journal.append({"op": plan["operation"], "kind": "bound", "target": t["name"],
                                    "digest": summary_of(desired)})
                continue
            journal.append({"op": plan["operation"], "kind": "binding", "target": t["name"],
                            "from": summary_of(original), "to": summary_of(desired)})
            write_target(t, path, desired)
            if t["kind"] == "launchagent":
                problem = invocation_problem(t, path, layout, d)
                if problem:
                    raise Refused(f"{t['name']} doesn't run the pinned release after binding: {problem}")
            journal.append({"op": plan["operation"], "kind": "bound", "target": t["name"],
                            "digest": summary_of(desired)})
        change = skills_change(skills, layout, paths)
        journal.append({"op": plan["operation"], "kind": "selected", "release": plan["release"],
                        "skills_repository_change": change})
        return {"state": "selected", "operation": plan["operation"], "release": plan["release"],
                "bootstrap_digest": d, "skills_repository_change": change,
                "next": "restart the bridge at an acknowledged checkpoint, then run postcheck; the activation is "
                        "complete only when postcheck passes"}


def authority_serving(plan, since):
    """Read-only, without the database: the authority's status snapshot
    (outbox-db/1's outbox-status.json, written only while it serves) reports
    it serving, written after this operation's bootstrap was journaled."""
    try:
        status = json.loads((Path(plan["state_root"]["path"]) / "outbox-status.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(status, dict) and status.get("unavailable") is None and since is not None \
        and isinstance(status.get("written_at"), (int, float)) and status["written_at"] >= since


def postcheck(plan_path, ops, spec_path=None):
    """Read-only: recomputes the record and D from the sealed plan and compares
    them with the state-root record and every bound target, the LaunchAgents by
    their effective executable and exact arguments; checks the first-write
    evidence and the bridge. It never opens the outbox database. The
    activation is complete only when every check passes."""
    plan = load_plan(plan_path)
    mine = operation_state(read_journal(plan["root"]), plan["operation"])
    layout = staged_layout(plan)
    data = bootstrap_bytes(plan)
    d = sha256_bytes(data)
    checks = [("bootstrap digest", "matches" if mine["digest"] == d else "not journaled as this plan's")]
    # The record's two release fields against the pinned release itself (#21's owner amendment 2): the
    # staged release's own manifest hash and its directory's identity.
    staged = Path(plan["staged_release"])
    try:
        staged_sha = stage_release.drift_check.sha256(staged / "artifacts" / MANIFEST)
    except OSError:
        staged_sha = None
    fields = release_fields(plan)
    checks.append(("pinned release", "matches" if staged_sha == fields["release"] and staged.name == fields[
        "release_id"] else "the staged release's manifest or identity is not the record's"))
    try:
        found, _, _ = read_private(bootstrap_path(plan))
        checks.append(("bootstrap record", "matches" if found == data else "differs from the plan's"))
    except (FileNotFoundError, Refused) as e:
        checks.append(("bootstrap record", f"missing or unreadable ({e.__class__.__name__})"))
    paths = target_paths(stage_release.load_spec(spec_path)[0]["targets"], skills_root(spec_path))
    for name, (t, path) in paths.items():
        backup = mine["backups"].get(name)
        if backup is None:
            checks.append((name, "not bound by this operation"))
            continue
        want = desired_target(t, path, layout, restore_state(backup["folder"]), d)
        same = summary_of(read_target(t, path, layout)) == summary_of(want)
        problem = invocation_problem(t, path, layout, d) if t["kind"] == "launchagent" else None
        checks.append((name, "bound" if same and not problem else "differs" + (f": {problem}" if problem else "")))
    try:
        bridge = bridge_verdict(ops, plan)
    except Refused as e:
        bridge = f"blocks any mutation: {e}"
    since = next((r["at"] for r in mine["records"] if r["kind"] == "bootstrap"), None)
    serving = authority_serving(plan, datetime.datetime.fromisoformat(since).timestamp() if since else None)
    ok = mine["selected"] and bridge == "expected" and serving and all(c[1] in ("bound", "matches") for c in checks)
    return {"operation": plan["operation"], "selected": mine["selected"], "checks": checks, "bridge": bridge,
            "authority_serving": serving, "first_writes": first_write_evidence(plan["state_root"]["path"]),
            "ok": ok}


def rollback(plan_path, ops, spec_path=None, crash=None):
    """Before the first new-format write only: restore the recorded targets
    exactly (D-27), which removes the launch option. The bootstrap record is
    left in place, and journaled. After the first write, refused: the recovery
    is a reviewed repair or a roll-forward that keeps the newer state (D-74)."""
    plan = load_plan(plan_path)
    with Journal(plan["root"], crash) as journal:
        mine = operation_state(journal.records, plan["operation"])
        if not mine["admitted"]:
            raise Refused("this operation never began; there is nothing to roll back")
        if mine["rolled_back"]:
            return {"state": "already rolled back", "operation": plan["operation"]}
        bridge = ops.bridge_state() or {}
        if bridge.get("running") is not False:
            raise Refused("rollback refused for now: a bridge is running, or its state is unknown; it must stop at an "
                          "acknowledged checkpoint first; nothing was changed")
        evidence = first_write_evidence(plan["state_root"]["path"])
        written = {k: v for k, v in evidence.items() if v != "absent"}
        if written:
            journal.append({"op": plan["operation"], "kind": "rollback-refused", "evidence": evidence})
            raise Refused("rollback refused: " + "; ".join(f"{k}: {v}" for k, v in sorted(written.items()))
                          + ". Old code can't read the new state, so the recovery is repair or roll-forward: a "
                            "reviewed repair, or a roll-forward that keeps the newer state (D-74). The recorded "
                            "targets, backups and the new state are kept, and nothing was changed")
        layout = staged_layout(plan)
        paths = target_paths(stage_release.load_spec(spec_path)[0]["targets"], skills_root(spec_path))
        plans = []
        for t_plan in reversed(plan["targets"]):
            t, path = paths[t_plan["name"]]
            backup = mine["backups"].get(t["name"])
            if backup is None:
                continue
            original = restore_state(backup["folder"])
            current = read_target(t, path, layout)
            desired = desired_target(t, path, layout, original, mine["digest"])
            if summary_of(current) not in (summary_of(original), summary_of(desired)):
                raise Refused(f"rollback blocked: {t['name']} was changed by something else since activation; "
                              "nothing was changed")
            plans.append((t, path, original, current))
        for t, path, original, current in plans:
            if summary_of(current) != summary_of(original):
                journal.append({"op": plan["operation"], "kind": "restoring", "target": t["name"]})
                write_target(t, path, original)
            journal.append({"op": plan["operation"], "kind": "restored", "target": t["name"]})
        journal.append({"op": plan["operation"], "kind": "bootstrap-retained",
                        "present": os.path.lexists(bootstrap_path(plan)), "digest": mine["digest"]})
        journal.append({"op": plan["operation"], "kind": "rolled-back"})
        return {"state": "rolled-back", "operation": plan["operation"], "release": plan["release"],
                "bootstrap_record": "left in place"}


def status(root):
    records = read_journal(root)
    out = []
    for op in dict.fromkeys(r["op"] for r in records if r.get("op")):
        s = operation_state(records, op)
        state = ("rolled back" if s["rolled_back"] else "selected (complete only once postcheck passes)"
                 if s["selected"] else f"unfinished (last: {s['records'][-1]['kind']})")
        out.append({"operation": op, "state": state})
    return out


def render_operator_plan(plan):
    """The private operator plan (A9): what an operator publishes, after
    removing private references, before any live step."""
    d = sha256_bytes(bootstrap_bytes(plan))
    lines = [f"operator plan for {plan['operation']}: {plan['release']}",
             f"  source commit {plan['source_commit']}; manifest sha256 {plan['manifest_sha256']}"]
    lines += [f"  artifact {a['name']}: sha256 {a['sha256']}" for a in plan["artifacts"]]
    lines += [f"  private environment: {shown(Path(plan['staged_release']) / 'env')}",
              f"  private activation root: {shown(plan['root'])}",
              f"  chat state root: {shown(plan['state_root']['path'])}",
              f"  transition: {plan['transition']['name']} ({', '.join(sorted(plan['transition']['changed']))} "
              f"raised; {', '.join(sorted(plan['transition']['added']))} added)"]
    lines += [f"  target {t['name']}: {t['path']} -> {t['proposed']}" for t in plan["targets"]]
    imp = plan["importers"]
    lines += [f"  affected helper {k['name']}: {k['path']}" for k in imp["known"] if k["found"]]
    lines += [f"  unlisted importer (stays on old code): {u}" for u in imp["unlisted"]]
    lines += [f"  safe checkpoint: {c['name']} ({c['kind']}), owner {c['owner']}" for c in plan["checkpoints"]]
    lines += [f"  preserved state: snapshot {plan['state_snapshot']['digest'][:12]}; no new-format write yet "
              f"({', '.join(sorted(plan['state_snapshot']['first_writes']))} absent)",
              "  migration point: the new bridge's first start (its bootstrap commit is the first outbox-db/1 write)",
              f"  designation list: {', '.join(plan['designations']) or 'explicitly empty'}",
              f"  bootstrap record digest D: sha256:{d} (the chat-bridge LaunchAgent's {BOOTSTRAP_OPTION})",
              "  postchecks: activate_release postcheck (record and D recomputed from this plan, every target "
              "bound, the LaunchAgents' effective executable, the expected new bridge serving)",
              "  permitted recovery: rollback restores the recorded targets only before the first new-format "
              "write, leaving the bootstrap record in place; after it, a reviewed repair or a roll-forward "
              "keeping the newer state",
              "  skills repository: the expected change to the chat skill folder and chat/scripts is reported "
              "by activate; the owner commits it (D-27)"]
    return "\n".join(lines)


# --- command line ----------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="activate_release", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--release", required=True, type=Path)
    p.add_argument("--dest", required=True, type=Path)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--evidence", type=Path)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--designate", nargs="+")
    g.add_argument("--no-designations", action="store_true")
    p.add_argument("--out", required=True, type=Path)
    for name in ("stage", "activate", "postcheck", "rollback", "operator-plan"):
        c = sub.add_parser(name)
        c.add_argument("--plan", required=True, type=Path)
        c.add_argument("--evidence", type=Path)
    s = sub.add_parser("status")
    s.add_argument("--root", required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            plan = make_plan(a.release, a.dest, a.root, [] if a.no_designations else a.designate, LiveOps(a.evidence))
            save_plan(plan, a.out)
            print(render_operator_plan(plan))
        elif a.cmd == "stage":
            summary, _, _ = stage_admitted(a.plan)
            print(f"{summary['state']}: {summary['release_dir']} (operation {summary['operation']})")
        elif a.cmd == "activate":
            print(json.dumps(activate(a.plan, LiveOps(a.evidence)), indent=2, sort_keys=True))
        elif a.cmd == "postcheck":
            report = postcheck(a.plan, LiveOps(a.evidence))
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        elif a.cmd == "rollback":
            print(json.dumps(rollback(a.plan, LiveOps(a.evidence)), indent=2, sort_keys=True))
        elif a.cmd == "operator-plan":
            print(render_operator_plan(load_plan(a.plan)))
        else:
            for op in status(a.root):
                print(f"{op['operation']}: {op['state']}")
        return 0
    except Refused as e:
        print(f"activate_release: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
