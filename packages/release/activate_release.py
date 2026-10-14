#!/usr/bin/env python3
"""Guarded activation of one staged plateia release (PLT-14, #21; D-74, D-26, D-27).

It carries #19's release through the one admitted format transition
(activation.json): from the D-19 baseline formats to #19's, plus the one
designation-bootstrap/1 read input (#21's owner amendment of 2026-10-11) and
writer admission's maintenance/1 record (#23, #24; amendment 4 of 2026-10-13).

    activate_release plan --release DIR --dest DIR --root DIR --evidence FILE
                          (--designate NAME ... | --no-designations) --out FILE
    activate_release stage --plan FILE
    activate_release activate --plan FILE --evidence FILE
    activate_release postcheck --plan FILE --evidence FILE
    activate_release release --plan FILE --evidence FILE
    activate_release rollback --plan FILE --evidence FILE
    activate_release operator-plan --plan FILE
    activate_release status --root DIR [--plan FILE]

- plan: read-only. It seals one release, the seven D-26 targets as they are,
  one state snapshot and the state root's identity, activation.json's
  transition, a private root and the designation list (or an explicit empty
  one) under one digest, with a new operation id.
- stage: stages the release under the plan's admission (stage_release).
- activate: under a fenced journal it seals the staged release, acquires
  writer admission's maintenance (docs/writer_admission.md: the record
  `draining`, the drain, `partial`), backs up every original privately,
  publishes the bridge's designation bootstrap record (derived from the
  sealed plan; never replacing anything), journals its digest D, and binds
  the seven targets to the staged release. The chat-bridge LaunchAgent runs
  the release's chat-bridge with exactly `--activation-bootstrap
  sha256:<D>`. Run again after the owner's reboot, it verifies retirement
  (a boot change since the binding), inhibition and every target, and writes
  `full`, which admits the bound bridge's start. It starts, stops and
  restarts nothing: the owner stops the legacy bridge before binding and
  reboots after it, as the published operator plan says.
- postcheck: read-only, with the new bridge serving under `full`; it never
  opens the outbox database.
- release: ends maintenance (unlinks the record), only while every
  post-check passes, or, after a rollback, once the restored targets, the
  record, a boot change since the restore and the absence of any first write
  are verified. The activation is complete only once released.
- rollback: before the first new-format write only (D-74); refused after it.
  It leaves the bootstrap record in place and keeps the maintenance record
  until the release.
- operator-plan: the private plan an operator publishes before any live step
  (A9), with the expected skills-repository change (D-27).

Nothing here is a service action. Implementing or reviewing it never
authorizes a live activation (PLT-14); that rests on the owner's decision and
the published plan (D-74).
"""
import argparse
import contextlib
import contextvars
import datetime
import errno
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
import types
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
            if out.returncode == 113 or "could not find service" in (out.stdout + out.stderr).lower():
                return {"running": False, "release": None}
            return {"running": None, "release": None, "unknown": f"launchctl print exited {out.returncode}"}
        running = re.search(r"^\s*state = running\s*$", out.stdout, re.M) is not None
        program = re.search(r"^\s*program = (\S+)\s*$", out.stdout, re.M)
        found = re.search(r"/releases/([^/]+)/env/bin/chat-bridge$", program.group(1)) if program else None
        return {"running": running, "release": found.group(1) if found else "skills-tree"}

    EVIDENCE_MAX_AGE = 900  # seconds: older checkpoint evidence is stale, and so unknown

    def _evidence(self):
        """The operator's checkpoint evidence, only while current: it must say
        when it was observed (`observed_at`, ISO 8601 with a time zone), no more
        than EVIDENCE_MAX_AGE ago. Stale or undated evidence is unknown."""
        if self.evidence is None:
            return None
        try:
            data = json.loads(self.evidence.read_text(encoding="utf-8"))
            at = datetime.datetime.fromisoformat(data["observed_at"]).timestamp()
        except (OSError, ValueError, TypeError, KeyError):
            return None
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - at
        return data if isinstance(data, dict) and -60 <= age <= self.EVIDENCE_MAX_AGE else None

    def writers(self):
        data = self._evidence()
        return data.get("writers") if data and isinstance(data.get("writers"), list) else None

    def producers(self):
        data = self._evidence()
        return data.get("producers") if data and isinstance(data.get("producers"), dict) else None

    def inhibition(self):
        """The owner-authorized inhibition evidence (writer admission 8.2; gate
        G-2), from the operator's evidence file: whether it is complete, and
        each unmanaged launcher found with its coverage. None when absent or
        stale. The controller never searches for launchers itself: that
        inventory, and what it may read, is the owner's (G-2)."""
        data = self._evidence()
        return data.get("inhibition") if data and isinstance(data.get("inhibition"), dict) else None

    def authority_status(self, plan):
        """The bridge authority's live answer to the existing read-only status
        request (outbox-authority/1) over its socket, through the pinned
        release's own client and within its bounded deadline; None when nothing
        answers. It never opens the outbox database."""
        try:
            store = load_release_module(plan, "delivery_store")
            sock = Path(plan["state_root"]["path"]) / "outbox.sock"
            return store.Client(store.UnixTransport(sock, wait=5.0)).call("status", None)
        except Exception:  # no answer, whatever failed: never serving evidence
            return None


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


def check_producers(ops, plan):
    """Every designated account's producers must be tracked, so the designation
    list is complete (A3's one kept case; writer admission 9.2). The
    quiet-writer checks that sat beside it are replaced: new-release writers by
    the maintenance record (acquisition to `partial`), legacy writers by
    retirement and inhibition before `full` (#21 amendment 4, section 3)."""
    producers = ops.producers()
    if plan["designations"]:
        if not isinstance(producers, dict):
            raise Refused("blocked: no producer evidence for the designated accounts; nothing was changed")
        for account in plan["designations"]:
            if producers.get(account) != "tracked":
                raise Refused(f"blocked: account {account}: its producers are not all tracked "
                              f"({producers.get(account) if isinstance(producers.get(account), str) else 'unknown'}); "
                              "the designation list is never changed silently, and nothing was changed")


# --- the plan ----------------------------------------------------------------------

def make_plan(release, dest, root, designations, ops, spec_path=None, activation_path=None):
    """An activation plan: read-only. Refused if the release can't be staged,
    a target is blocked, the state already holds a new-format write, a
    bootstrap record, a maintenance record or a refusal sink, or the
    designation list is invalid."""
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
    for name, what in ((MAINT_FILE, "a maintenance record (writer admission is held by an operation)"),
                       (SINK_DIR, "a refusal sink (refused writes kept for an owner decision)")):
        if os.path.lexists(Path(root_id["path"]) / name):
            raise Refused(f"the chat state already holds {what}: no plan is made over it; it needs its own "
                          "operation's release, or an owner decision")
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
    """The plan, written exclusively, synced, and its directory entry synced too."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    fsync_dir(os.path.dirname(os.path.abspath(path)))
    return Path(path)


def load_plan(path):
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Refused(f"the activation plan can't be read ({e.__class__.__name__})") from None
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA or plan.get("digest") != plan_digest(plan):
        raise Refused("not an activation plan, or one altered after it was made")
    problem = plan_problem(plan)
    if problem:
        raise Refused(f"not a well-formed activation plan: {problem}")
    return plan


PLAN_FIELDS = {"operation": str, "made_at": str, "release": str, "release_path": str, "manifest_sha256": str,
               "source_commit": str, "dest": str, "staged_release": str, "root": str, "activation_sha256": str,
               "designations": list, "targets": list, "artifacts": list, "checkpoints": list, "state_root": dict,
               "transition": dict, "importers": dict, "state_snapshot": dict}


def plan_problem(plan):
    """What is malformed in a plan's shape (a digest proves only that it is
    unchanged since someone computed it, not that it is well formed)."""
    for field, kind in PLAN_FIELDS.items():
        if not isinstance(plan.get(field), kind):
            return f"its {field} is missing or of the wrong type"
    if not re.fullmatch(r"activate-[0-9a-f]{16}", plan["operation"]):
        return "its operation id is malformed"
    names = plan["designations"]
    if not all(isinstance(n, str) and ACCOUNT.fullmatch(n) for n in names) or names != sorted(set(names)):
        return "its designation list is not sorted distinct account names"
    root = plan["state_root"]
    if set(root) != {"path", "dev", "ino"} or not isinstance(root["path"], str) \
            or not all(type(root[k]) is int for k in ("dev", "ino")):
        return "its state root is malformed"
    if not all(isinstance(t, dict) and isinstance(t.get("name"), str) and isinstance(t.get("digest"), str)
               for t in plan["targets"]):
        return "its targets are malformed"
    if not all(isinstance(c, dict) and isinstance(c.get("name"), str) for c in plan["checkpoints"]):
        return "its checkpoints are malformed"
    for field in ("dest", "staged_release", "root", "release_path"):
        if not os.path.isabs(plan[field]):
            return f"its {field} is not an absolute path"
    return None


O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
FOLDERS = ("skill-folder", "import-location")


# --- pinned descriptors ----------------------------------------------------------------
# Every directory the controller changes or judges is opened once and then used
# only through its descriptor: a path swapped, renamed or replaced after it was
# checked is never written through (code review slot 2 on PR #22).

def ident(st):
    return [st.st_dev, st.st_ino]


def open_dir(path, expect=None, follow=False, what=None):
    """A descriptor of the directory at `path` (its last component never a
    symlink unless `follow`); with `expect`, refused unless it is that
    directory (device and inode)."""
    try:
        fd = os.open(path, os.O_RDONLY | O_DIRECTORY | (0 if follow else O_NOFOLLOW))
    except OSError as e:
        raise Refused(f"blocked: {what or shown(path)} can't be opened as a directory ({e.strerror or e}); "
                      "nothing more was changed") from None
    if expect is not None and ident(os.fstat(fd)) != list(expect):
        os.close(fd)
        raise Refused(f"blocked: {what or shown(path)} was moved or replaced since it was recorded; nothing more "
                      "was changed")
    return fd


def still_there(path, fd, follow=False):
    """`path` still names the directory `fd` holds."""
    try:
        st = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and ident(st) == ident(os.fstat(fd))


def read_fd(fd, cap=None):
    chunks, size = [], 0
    while True:
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if cap is not None and size > cap:
            raise Refused(f"a file is over {cap} bytes")


def write_fd(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def read_private_at(dir_fd, name, cap=None):
    """(bytes, stat) of a private regular file of this user, opened without
    following links relative to `dir_fd`; FileNotFoundError, or Refused."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as e:
        raise Refused(f"{name} can't be opened ({e.strerror or e})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise Refused(f"{name} is not a private regular file of this user")
        return read_fd(fd, cap), st
    finally:
        os.close(fd)


def write_private_at(dir_fd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    try:
        os.fchmod(fd, 0o600)
        write_fd(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def subdir(base_fd, parts, create=False):
    """A descriptor of base/parts..., each a real directory (created 0700 if
    `create`), never following a link."""
    fd = os.dup(base_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)  # the new entry is durable before anything is put in it (PR #22 review 4)
                except FileExistsError:
                    pass
            nxt = os.open(part, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


# --- the designation bootstrap record (#21's owner amendments 1 and 2) ----------------

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


def bootstrap_name():
    return load_activation()["bootstrap"]["file"]


def open_state_root(plan):
    """The chat state root the plan sealed, pinned: refused if missing, a link,
    or another directory (device and inode)."""
    root = plan["state_root"]
    if os.path.realpath(root["path"]) != root["path"]:
        raise Refused("blocked: the chat state root's path no longer resolves to itself; nothing more was changed")
    return open_dir(root["path"], [root["dev"], root["ino"]], what="the chat state root")


def fsync_dir(fd_or_path):
    if isinstance(fd_or_path, int):
        os.fsync(fd_or_path)
        return
    fd = os.open(fd_or_path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_bootstrap(journal, plan):
    """Publish the record and journal D; returns D, or Refused with everything
    retained.

    Everything happens through one pinned descriptor of the plan's state root.
    Each step is journaled: an intent naming the temporary file; that file
    created exclusively, written and synced, its inode journaled; link() to
    the final name, which never replaces a file; the directory synced. Then
    reconciliation (#21's APPROVE, spec addition 2): the final record is the
    very inode this operation's journal staged, holds exactly the plan's bytes
    and is durable. Only then is D journaled, with the record's change time (an
    inode number alone can be reused once the staged copy is gone). A record
    whose ownership this operation's own journal can't show is foreign: it is
    retained, and the activation stops."""
    op = plan["operation"]
    data = bootstrap_bytes(plan)
    d = sha256_bytes(data)
    final = bootstrap_name()
    sfd = open_state_root(plan)
    try:
        mine = [r for r in journal.records if r.get("op") == op]
        last = lambda kind: next((r for r in reversed(mine) if r["kind"] == kind), None)  # noqa: E731
        done = last("bootstrap")
        if done is not None:
            if done["digest"] != d:
                raise Refused("the journaled bootstrap digest is not this plan's; nothing more was changed")
            reconcile_bootstrap(sfd, data, tuple(done["inode"]), done["ctime_ns"])
            return d

        def lstat_at(name):
            try:
                return os.stat(name, dir_fd=sfd, follow_symlinks=False)
            except FileNotFoundError:
                return None
        published = last("bootstrap-published")
        if published is not None:
            inode = tuple(published["inode"])
            reconcile_bootstrap(sfd, data, inode)
        else:
            staged, inode = last("bootstrap-staged"), None
            if staged is not None:
                inode = tuple(staged["inode"])
                found = lstat_at(final)
                if found is not None and (found.st_dev, found.st_ino) != inode:
                    raise Refused(f"blocked: {final} is not the record this operation staged (another inode): it is "
                                  "retained, and this needs an owner decision")
                if found is None:
                    st = lstat_at(staged["tmp"])
                    if st is None or (st.st_dev, st.st_ino) != inode:
                        inode = None  # the staged copy is gone or replaced: stage again, under a new name
                    else:
                        os.link(staged["tmp"], final, src_dir_fd=sfd, dst_dir_fd=sfd)
            elif lstat_at(final) is not None:
                raise Refused(f"blocked: {final} exists, and this operation's journal never staged a record: it is "
                              "retained, never replaced, and this needs an owner decision")
            if inode is None:
                if lstat_at(final) is not None:
                    raise Refused(f"blocked: {final} appeared, and this operation didn't publish it: it is "
                                  "retained, and this needs an owner decision")
                attempt = sum(1 for r in mine if r["kind"] == "bootstrap-intent") + 1
                tmp = f".designation-bootstrap.{op}.{attempt}.tmp"
                journal.append({"op": op, "kind": "bootstrap-intent", "tmp": tmp, "digest": d})
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600, dir_fd=sfd)
                try:
                    write_fd(fd, data)
                    os.fsync(fd)
                    st = os.fstat(fd)
                finally:
                    os.close(fd)
                fsync_dir(sfd)  # the staged copy's name, before its record depends on it
                inode = (st.st_dev, st.st_ino)
                journal.append({"op": op, "kind": "bootstrap-staged", "tmp": tmp, "inode": list(inode)})
                os.link(tmp, final, src_dir_fd=sfd, dst_dir_fd=sfd)  # never replaces a file
            fsync_dir(sfd)
            reconcile_bootstrap(sfd, data, inode)
            root_still_there(plan, sfd)
            journal.append({"op": op, "kind": "bootstrap-published", "inode": list(inode)})
        debt = []
        staged_names = {r["tmp"]: tuple(r["inode"]) for r in journal.records
                        if r.get("op") == op and r["kind"] == "bootstrap-staged"}
        for r in [r for r in journal.records if r.get("op") == op and r["kind"] == "bootstrap-intent"]:
            st = lstat_at(r["tmp"])
            if st is None:
                continue
            if staged_names.get(r["tmp"]) == (st.st_dev, st.st_ino):
                with contextlib.suppress(OSError):
                    os.unlink(r["tmp"], dir_fd=sfd)  # this operation's own staged copy, once published: cleanup
            else:
                debt.append(r["tmp"])  # never journaled as ours: kept, and reported
        fsync_dir(sfd)
        ctime_ns = reconcile_bootstrap(sfd, data, inode)
        root_still_there(plan, sfd)  # D is journaled only while the path still names the root it was published in
        journal.append({"op": op, "kind": "bootstrap", "digest": d, "inode": list(inode), "ctime_ns": ctime_ns,
                        "cleanup_debt": debt})
        return d
    finally:
        os.close(sfd)


def root_still_there(plan, sfd):
    if not still_there(plan["state_root"]["path"], sfd):
        raise Refused("blocked: the chat state root was moved or replaced during publication; the record is retained, "
                      "and nothing more was changed")


def reconcile_bootstrap(sfd, data, inode, ctime_ns=None):
    """The published record, read through the pinned state root, is this
    operation's, exact and durable; returns its change time. Refused otherwise,
    with everything retained. `ctime_ns`, once journaled with D, must match too."""
    final = bootstrap_name()
    try:
        found, st = read_private_at(sfd, final, cap=1 << 16)
    except FileNotFoundError:
        raise Refused(f"blocked: {final} is missing after this operation published it; nothing more was "
                      "changed") from None
    if (st.st_dev, st.st_ino) != tuple(inode) or (ctime_ns is not None and st.st_ctime_ns != ctime_ns):
        raise Refused(f"blocked: {final} is not the file this operation published (another inode, or replaced "
                      "since); it is retained")
    if found != data:
        raise Refused(f"blocked: {final} doesn't hold this plan's exact record; it is retained")
    fd = os.open(final, os.O_RDONLY | O_NOFOLLOW, dir_fd=sfd)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(sfd)
    return st.st_ctime_ns


# --- the journal ----------------------------------------------------------------------

RECORD_FIELDS = {  # what each journal record must carry, by kind (shape checks on decoded JSON)
    "admitted": {"plan_sha256": str}, "backup": {"target": str, "folder": str, "digest": str},
    "bootstrap-intent": {"tmp": str, "digest": str}, "bootstrap-staged": {"tmp": str, "inode": list},
    "bootstrap-published": {"inode": list}, "bootstrap": {"digest": str, "inode": list, "ctime_ns": int},
    "binding": {"target": str}, "entry-binding": {"target": str, "rel": str, "tmp": str, "to": str},
    "entry-bound": {"target": str, "rel": str}, "bound": {"target": str},
    "entry-restoring": {"target": str, "rel": str, "tmp": str}, "entry-restored": {"target": str, "rel": str},
    "restored": {"target": str}, "release-seal-baseline": {"dirs": list}, "release-sealed": {"dirs": list},
    "maint-intent": {"step": dict}, "maint-progress": {"step": dict, "facts": dict},
    "maint-outcome": {"step": dict, "result": dict}, "full": {"boot": str}}


def record_problem(r):
    if not isinstance(r, dict) or not isinstance(r.get("kind"), str) or ("op" in r and not isinstance(r["op"], str)):
        return "a record is malformed"
    for field, kind in RECORD_FIELDS.get(r["kind"], {}).items():
        if not isinstance(r.get(field), kind) or isinstance(r.get(field), bool):
            return f"a {r['kind']} record's {field} is malformed"
    if r["kind"] in ("release-seal-baseline", "release-sealed") and (
            len(r["dirs"]) != len(SEAL_DIRS) or not all(
                isinstance(d, dict) and d.get("rel") == rel and all(type(d.get(k)) is int for k in ("dev", "ino", "mode",
                                                                                                    "uid"))
                and isinstance(d.get("access"), dict) for d, rel in zip(r["dirs"], SEAL_DIRS))):
        return f"a {r['kind']} record's directories are malformed"
    for field in ("inode", "container"):
        value = r.get(field)
        if value is not None and (not isinstance(value, list) or len(value) != 2
                                  or not all(type(v) is int for v in value)):
            return f"a {r['kind']} record's {field} is malformed"
    return None


def parse_journal(data):
    """The complete records (each ended by a newline) and the torn suffix after
    the last newline; Refused when a complete record is damaged."""
    cut = data.rfind(b"\n") + 1
    records = []
    for line in data[:cut].split(b"\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            raise Refused("the activation journal is damaged before its last record; nothing was changed") from None
        problem = record_problem(record)
        if problem:
            raise Refused(f"the activation journal is damaged: {problem}; nothing was changed")
        records.append(record)
    return records, data[cut:]


def make_durable_dirs(path):
    """Create each missing level of `path` (0700), syncing its parent after each new entry, so a power loss
    can't keep what depends on a directory while losing its name (PR #22 review 4, finding 1)."""
    path = Path(os.path.abspath(path))
    missing = []
    while not os.path.lexists(path):
        missing.append(path)
        path = path.parent
    for p in reversed(missing):
        try:
            os.mkdir(p, 0o700)
        except FileExistsError:
            continue
        fsync_dir(str(p.parent))


class Journal:
    """The activation journal in the private root, fenced: the root, the lock
    and the journal file are opened once, never through a link, and kept as
    descriptors; every append first checks that the root's path still names
    that directory and its names those files, so a renamed or replaced root
    can't split an operation across two journals, and a second activation is
    refused, never interleaved. A torn suffix (a record cut short by a crash)
    is kept privately, then truncated and synced, before anything is appended."""

    def __init__(self, root, crash=None):
        self.root, self.crash = Path(root), crash
        self.root_fd = self.lock_fd = self.fd = None
        try:
            make_durable_dirs(self.root)
            self.root_fd = open_dir(self.root, what="the activation root")
            st = os.fstat(self.root_fd)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise Refused("blocked: the activation root is not this user's own, or others can write it")
            self.ident = ident(st)
            new = [name for name in (LOCK, JOURNAL) if not self._exists(name)]
            self.lock_fd = self._file(LOCK, os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("blocked: another activation holds the journal lock; nothing was changed") from None
            self.fd = self._file(JOURNAL, os.O_RDWR | os.O_CREAT | os.O_APPEND)
            if new:
                fsync_dir(self.root_fd)  # the new lock and journal names, before the first record depends on them
            self.check_fence()
            self.records = self._load()
        except BaseException:
            self.close()
            raise

    def _exists(self, name):
        try:
            os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def _file(self, name, flags):
        try:
            fd = os.open(name, flags | O_NOFOLLOW, 0o600, dir_fd=self.root_fd)
        except OSError as e:
            raise Refused(f"blocked: the activation {name} can't be opened as a regular file ({e.strerror or e}); "
                          "nothing was changed") from None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            os.close(fd)
            raise Refused(f"blocked: the activation {name} is not this user's regular file; nothing was changed")
        return fd

    def check_fence(self):
        try:
            st = os.lstat(self.root)
            ok = stat.S_ISDIR(st.st_mode) and ident(st) == self.ident and all(
                ident(os.stat(name, dir_fd=self.root_fd, follow_symlinks=False)) == ident(os.fstat(fd))
                for name, fd in ((LOCK, self.lock_fd), (JOURNAL, self.fd)))
        except OSError:
            ok = False
        if not ok:
            raise Refused("blocked: the activation journal's root, lock or file was moved or replaced; nothing more "
                          "was changed")

    def _load(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        data = read_fd(self.fd)
        records, torn = parse_journal(data)
        if torn:
            kept = f"journal-torn-{len(data) - len(torn)}.bin"
            write_private_at(self.root_fd, kept, torn)
            os.ftruncate(self.fd, len(data) - len(torn))
            os.fsync(self.fd)
            fsync_dir(self.root_fd)
            self.records = records
            self.append({"kind": "journal-repaired", "dropped_bytes": len(torn), "kept": kept})
            return self.records
        return records

    def append(self, record):
        self.check_fence()
        record = dict(record, schema=JOURNAL_SCHEMA, at=now())
        write_fd(self.fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(self.fd)
        self.records.append(record)
        if self.crash:
            self.crash(record)

    def point(self, name):
        """A named place inside a maintenance step (delivery_store.Owner.point): nothing is journaled; a test's
        crash hook that asks for points (its `points` attribute) sees it as a record of kind maint-point:<name>."""
        if self.crash is not None and getattr(self.crash, "points", False):
            self.crash({"kind": f"maint-point:{name}"})

    def close(self):
        for fd in (self.fd, self.lock_fd, self.root_fd):
            if fd is not None:
                with contextlib.suppress(OSError):
                    if fd == self.lock_fd:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
        self.fd = self.lock_fd = self.root_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_journal(root):
    """The journal's complete records, read without the lock (status,
    post-check); a torn suffix is ignored, and repaired by the next writer."""
    try:
        fd = os.open(Path(root) / JOURNAL, os.O_RDONLY | O_NOFOLLOW)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise Refused(f"the activation journal can't be read ({e.strerror or e})") from None
    try:
        return parse_journal(read_fd(fd))[0]
    finally:
        os.close(fd)


def operation_state(records, op):
    mine = [r for r in records if r.get("op") == op]
    kinds = [r["kind"] for r in mine]
    return {"records": mine, "admitted": "admitted" in kinds, "selected": "selected" in kinds,
            "rolled_back": "rolled-back" in kinds, "released": "released" in kinds, "full": "full" in kinds,
            "bound": {r["target"] for r in mine if r["kind"] == "bound"},
            "backups": {r["target"]: r for r in mine if r["kind"] == "backup"},
            "digest": next((r["digest"] for r in mine if r["kind"] == "bootstrap"), None),
            "written": {(r["target"], r["rel"]): r["to"] for r in mine if r["kind"] == "entry-binding"}}


# --- writer admission (#21 amendment 4; docs/writer_admission.md 4, 6, 8, 9, 12) --------------
# The controller is the owning operation: it drives the release's own delivery_store.Owner
# (loaded from the verified wheel) under its fenced journal, and keeps its own decisions:
# retirement, inhibition and targets before `full`, the release, and the rollback's release.

MAINT_FILE, SINK_DIR = "maintenance.json", "refused.d"
MAINT_KIND = "plateia-activation"
BRIDGE_START = "bridge-start"
MAINT_RECORDS = ("maint-intent", "maint-progress", "maint-outcome")
COVERAGE = ("managed-target", "disabled", "never-writes")  # writer admission 8.2: what covers an unmanaged launcher


def step_key(step):
    return json.dumps({k: v for k, v in step.items() if k != "progress"}, sort_keys=True)


class OwnerFence:
    """delivery_store.Owner's fence for this operation: its fenced journal
    (amendment 4's APPROVE, clarification K4). Each intent, progress fact and
    outcome is a journal record, synced before the call returns; the pending
    step and the latest record evidence are derived from those records alone,
    so they survive across commands and crashes. Verifying checks the
    journal's fence and the sealed state root, and before `full` the
    controller's own checks. With no journal (records only) it is read-only:
    it verifies nothing, so it permits no mutation."""

    def __init__(self, journal, plan, full_check=None, records=None):
        self.journal, self.plan, self.full_check, self._records = journal, plan, full_check, records

    def records(self):
        records = self.journal.records if self.journal is not None else (self._records or [])
        return [r for r in records if r.get("op") == self.plan["operation"] and r["kind"] in MAINT_RECORDS]

    def verify(self, step):
        if self.journal is None:
            return False
        self.journal.check_fence()
        os.close(open_state_root(self.plan))  # the plan's sealed state root, still at its path
        if self.full_check is not None and (step.get("to") or {}).get("state") == "full" and "progress" not in step:
            self.full_check()  # immediately before the durable intent of `full` (writer admission 8.3)
        return True

    def _append(self, kind, **fields):
        self.journal.append({"op": self.plan["operation"], "kind": kind, **fields})

    def intent(self, step):
        self._append("maint-intent", step=dict(step), state_root=self.plan["state_root"]["path"])

    def progress(self, step, facts):
        self._append("maint-progress", step=json.loads(step_key(step)), facts=dict(facts))

    def outcome(self, step, result):
        self._append("maint-outcome", step=json.loads(step_key(step)), result=dict(result))

    def pending(self):
        records = self.records()
        intents = [i for i, r in enumerate(records) if r["kind"] == "maint-intent"]
        if not intents:
            return None
        start = intents[-1]
        step = records[start]["step"]
        key, facts = step_key(step), {}
        for r in records[start + 1:]:
            if step_key(r["step"]) != key:
                continue
            if r["kind"] == "maint-outcome":
                return None
            facts.update(r["facts"])
        return dict(step, progress=facts)

    def released(self):
        """Whether the last maintenance outcome journaled is this operation's landed release."""
        outcomes = [r for r in self.records() if r["kind"] == "maint-outcome"]
        return bool(outcomes) and outcomes[-1]["step"].get("step") == "release" \
            and outcomes[-1]["result"].get("landed") is True

    def evidence(self):
        found = None
        for r in self.records():
            if r["kind"] != "maint-outcome" or not r["result"].get("landed"):
                continue
            name = r["step"].get("step")
            if name in ("write-draining", "write-record") and isinstance(r["result"].get("evidence"), dict):
                found = r["result"]["evidence"]
            elif name == "release":
                found = None
        return dict(found) if found else None


class ControllerProbes:
    """What Owner asks of the host: the boot (the operator's evidence when it
    gives one, else the release's own read-only probe; unreadable is
    "unknown"), this process's identity, and whether a recorded process is
    gone (for the read-only reconcile)."""

    def __init__(self, ops, store):
        self.ops, self.store = ops, store

    def boot(self):
        read = self.ops.boot() if hasattr(self.ops, "boot") else self.store._boot_id()
        return read if isinstance(read, str) and read else "unknown"

    def identity(self, conn_id=None):
        return {"boot": self.boot(), "pid": os.getpid(), "start": None}

    def gone(self, writer):
        probe = getattr(self.ops, "process_gone", None)
        return probe(writer) if probe is not None else self.store.HostProbes().gone(writer)


class SealedRootFS:
    """Owner's file system effects, bound to the plan's sealed state root
    across the durable intent (PR #22 review 1, finding 3). Each mutation
    opens the root through its sealed realpath and identity (refused if it
    was moved or replaced) and acts through that descriptor, never through a
    path; a replacement or removal of the record first checks, through the
    same descriptor, that the record there is exactly the one the pending
    step journaled as its `from`. Reads are delegated unchanged."""

    def __init__(self, base, plan, fence, store):
        self.base, self.plan, self.fence, self.store = base, plan, fence, store
        self.before_release = None  # the release's eligibility, run again immediately before its unlink
        self.before_full = None  # full's checks, run again immediately before full replaces the record

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _name(self, path):
        path = Path(path)
        if str(path.parent) != self.plan["state_root"]["path"]:
            raise Refused(f"blocked: {path.name} is not in the sealed chat state root; nothing more was changed")
        return path.name

    @contextlib.contextmanager
    def _root(self):
        fd = open_state_root(self.plan)
        try:
            yield fd
        finally:
            os.close(fd)

    def _expected(self, fd, name):
        if name != MAINT_FILE:
            return
        want = (self.fence.pending() or {}).get("from")
        try:
            data, st = read_private_at(fd, name, cap=self.store.MAINT_MAX + 1)
            generation = json.loads(data).get("generation")
        except (FileNotFoundError, Refused, ValueError, AttributeError):
            data = None
        actual = None if data is None else {"generation": generation, "sha256": sha256_bytes(data),
                                            "ino": st.st_ino, "dev": st.st_dev, "ctime_ns": st.st_ctime_ns}
        if want is None or not self.store.same_evidence(want, actual):
            raise Refused("blocked: the maintenance record at the sealed root is not the one this step journaled; "
                          "it is never replaced or removed, and nothing more was changed")

    def create_excl(self, path, data):
        with self._root() as root:
            fd = os.open(self._name(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, 0o600, dir_fd=root)
            try:
                write_fd(fd, data)
                self.base.fsync_fd(fd)
            finally:
                os.close(fd)
            self.base.fsync_fd(root)  # its name, before Owner journals its identity

    def link(self, src, dst):
        with self._root() as root:
            os.link(self._name(src), self._name(dst), src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)

    def _still_bound(self, root, name):
        """Immediately before the effect, after any callback's I/O: the root's path still names the sealed
        root this descriptor holds, and the record is still the one the pending step expects (PR #22 review 3,
        finding 2)."""
        if not still_there(self.plan["state_root"]["path"], root):
            raise Refused("blocked: the chat state root was moved or replaced; nothing more was changed")
        self._expected(root, name)

    def replace(self, src, dst):
        with self._root() as root:
            self._expected(root, self._name(dst))
            if self._name(dst) == MAINT_FILE and self.before_full is not None \
                    and ((self.fence.pending() or {}).get("to") or {}).get("state") == "full":
                self.before_full()
            self._still_bound(root, self._name(dst))
            os.replace(self._name(src), self._name(dst), src_dir_fd=root, dst_dir_fd=root)

    def unlink(self, path):
        with self._root() as root:
            self._expected(root, self._name(path))
            if self._name(path) == MAINT_FILE and self.before_release is not None \
                    and (self.fence.pending() or {}).get("step") == "release":
                self.before_release()
            self._still_bound(root, self._name(path))
            os.unlink(self._name(path), dir_fd=root)

    def sync_dir(self, path):
        if str(path) != self.plan["state_root"]["path"]:
            return self.base.sync_dir(path)
        with self._root() as root:
            self.base.fsync_fd(root)

    def sync_file(self, path):
        with self._root() as root:
            fd = os.open(self._name(path), os.O_RDONLY | O_NOFOLLOW, dir_fd=root)
            try:
                self.base.fsync_fd(fd)
            finally:
                os.close(fd)


def maintenance(journal, plan, ops, full_check=None, records=None):
    """(the release's delivery_store, its Owner for this operation, the fence)."""
    store = load_release_module(plan, "delivery_store")
    fence = OwnerFence(journal, plan, full_check, records)
    clock = getattr(ops, "clock", None)  # tests: a fake clock bounds the drain's waits
    owner = store.Owner(store.Paths(plan["state_root"]["path"]), operation=plan["operation"], kind=MAINT_KIND,
                        plan_sha256=plan["digest"], fence=fence, probes=ControllerProbes(ops, store),
                        fs=SealedRootFS(store.FS_DEFAULT, plan, fence, store),
                        **({"mono": clock.mono, "sleep": clock.advance} if clock is not None else {}))
    if journal is not None:
        owner.point = journal.point
    return store, owner, fence


@contextlib.contextmanager
def owner_step(store, what):
    """An Owner primitive's refusal, as the controller's: nothing further is done."""
    try:
        yield
    except store.OwnerError as e:
        raise Refused(f"blocked: {what}: {e}; nothing more was changed") from None
    except OSError as e:  # an effect that couldn't complete: the pending intent is settled from disk next time
        raise Refused(f"blocked: {what}: {e.strerror or e}; nothing more was changed") from None


def held_problem(store, fence, plan, states):
    """(the record, why it is not this operation's own, exactly its last journaled write, in one of `states`;
    or None). Never writes anything."""
    rec = store.read_maintenance(store.Paths(plan["state_root"]["path"]))
    if not rec.present:
        return rec, "there is no maintenance record"
    if not rec.valid:
        return rec, f"the maintenance record is unknown ({rec.why})"
    f = rec.fields
    if (f["operation"], f["kind"], f["plan_sha256"]) != (plan["operation"], MAINT_KIND, plan["digest"]):
        return rec, "the maintenance record is another operation's"
    if not store.same_evidence(fence.evidence(), rec.evidence()):
        return rec, "the maintenance record is not this operation's last journaled write"
    if f["state"] not in states:
        return rec, f"the maintenance record is {f['state']}, not {' or '.join(states)}"
    return rec, None


def require_held(store, fence, plan, states, what):
    rec, problem = held_problem(store, fence, plan, states)
    if problem:
        raise Refused(f"blocked: {problem}, so {what}; nothing more was changed")
    return rec


def acquire(owner, store, probes):
    """Step 3 (writer admission 4.4, 9.1): the record `draining`, the drain, `partial`. On resume it continues
    from the record's state, and drains again in a new boot. Returns the record."""
    with owner_step(store, "acquiring maintenance"):
        rec = owner.acquire()  # settles a pending step first; another operation's or an unknown record refuses
        boot = probes.boot()
        if rec.state == "draining" or (rec.state == "partial" and rec.fields["boot"] != boot):
            if not landed(owner.drain(boot=boot)):
                raise Refused("blocked: the drain did not complete within its deadline (a writer's section is still "
                              "open); the record stays as it was and keeps refusing new sections; run activate "
                              "again, or roll back and release; nothing more was changed")
            rec = store.read_maintenance(owner.paths)
            if not (rec.valid and rec.fields["state"] == "partial" and rec.fields["boot"] == boot):
                raise Refused("blocked: the drain reported success, but the record is not partial in this boot; "
                              "nothing more was changed")
    return rec


def landed(result):
    """Whether an Owner advancement wrote its record: on its lock's deadline it returns landed and drained
    false, and writes nothing (PR #22 review 1, finding 1)."""
    return isinstance(result, dict) and result.get("landed") is True and result.get("drained", True) is True


def inhibition_problems(evidence):
    """Why the inhibition evidence does not cover the window (writer admission 8.2, 8.3; gate G-2)."""
    if not isinstance(evidence, dict):
        return ["no current inhibition evidence (gate G-2: the owner-authorized inventory of unmanaged launchers)"]
    problems = []
    if evidence.get("complete") is not True:
        problems.append("the inhibition evidence is marked incomplete (gate G-2)")
    launchers = evidence.get("launchers")
    if not isinstance(launchers, list) or not all(isinstance(x, dict) and isinstance(x.get("name"), str)
                                                  for x in launchers):
        return problems + ["the inhibition evidence's launchers are malformed"]
    for x in launchers:
        if x.get("coverage") not in COVERAGE:
            where = x.get("path") if isinstance(x.get("path"), str) else "an unknown path"
            problems.append(f"the unmanaged launcher {x['name']} ({where}) has no coverage")
    return problems


def targets_problems(journal_root_fd, mine, paths, layout, d):
    """Each target that this operation backed up, verified bound through its pinned directory, as the
    post-check verifies it."""
    problems = []
    for name, (t, path) in paths.items():
        backup = mine["backups"].get(name)
        if backup is None:
            problems.append(f"{name} is not bound by this operation")
            continue
        try:
            current, _ = observe_target(t, path, layout, backup.get("container"))
            want = desired_target(t, path, layout, load_backup(journal_root_fd, backup), d)
        except Refused as e:
            problems.append(f"{name}: {e}")
            continue
        problem = invocation_problem(t, current.get(""), layout, d) if t["kind"] == "launchagent" else None
        if summary_of(current) != summary_of(want) or problem:
            problems.append(f"{name} is not bound as planned" + (f" ({problem})" if problem else ""))
    return problems


def seal_problems(journal, plan):
    """The journaled seal, verified read-only (identities, modes, owners and access metadata), never resealed
    (PR #22 review 2, finding 4)."""
    try:
        seal_release(journal, plan, read_only=True)
    except Refused as e:
        return [f"the release seal: {e}"]
    return []


RETIREMENT = "retirement unverified"


def full_problems(journal, plan, ops, store, probes, layout, paths, d):
    """What `full` requires and doesn't have, in the current boot (writer admission 8.3): retirement (a known
    boot that differs from the binding's), complete inhibition, every target bound, the bootstrap record
    reconciled as this operation's, and the bridge stopped or this operation's own."""
    problems = []
    rec = store.read_maintenance(store.Paths(plan["state_root"]["path"]))
    bound, boot = (rec.fields or {}).get("bound_boot"), probes.boot()
    if store.known(boot) is None:
        problems.append("the boot can't be read: unknown")
    elif store.known(bound) is None:
        problems.append(f"{RETIREMENT}: the record holds no known boot of the binding")
    elif bound == boot:
        problems.append(f"{RETIREMENT}: the boot has not changed since the targets were bound, so a legacy writer "
                        "may still run (the owner's reboot, gate G-1)")
    problems += inhibition_problems(ops.inhibition() if hasattr(ops, "inhibition") else None)
    mine = operation_state(journal.records, plan["operation"])
    problems += targets_problems(journal.root_fd, mine, paths, layout, d)
    problems += seal_problems(journal, plan)
    if mine["digest"] != d:
        problems.append("the bootstrap digest is not journaled as this plan's")
    else:
        try:
            publish_bootstrap(journal, plan)  # journaled: reconciles only, writing nothing
        except Refused as e:
            problems.append(str(e))
    try:
        bridge_verdict(ops, plan)
    except Refused as e:
        problems.append(str(e))
    return problems


def full_binding(plan, d):
    return {"release_id": plan["release"], "bootstrap_digest": f"sha256:{d}", "permits": [BRIDGE_START]}


def take_full(journal, plan, ops, layout, paths, d):
    """Step 8 (writer admission 8.3, 9.1): after the owner's reboot, verify retirement, inhibition, targets and
    the bootstrap, then write `full`, which admits the bound bridge's start. A refusal of `full` is reported,
    never forced, and the record stays `partial`. With `full` already held, every target is verified again; a
    changed one writes the record back to `partial`, needing a new retirement (8.2)."""
    def check():
        found = full_problems(journal, plan, ops, store, probes, layout, paths, d)
        if found:
            raise Refused("; ".join(found))
    store, owner, fence = maintenance(journal, plan, ops, full_check=check)
    probes = ControllerProbes(ops, store)
    pending = fence.pending()
    if fence.released() or (pending is not None and pending.get("step") == "release"):
        return {"state": "releasing", "next": "release: it completes the release this operation began"}
    if not store.read_maintenance(owner.paths).present and pending is None:
        raise Refused("blocked: the maintenance record of this bound operation is missing; it is never acquired "
                      "again after binding: this needs an owner decision; nothing more was changed")
    with owner_step(store, "resuming maintenance"):
        rec = owner.acquire()
    boot = probes.boot()
    if rec.state == "full":
        changed = targets_problems(journal.root_fd, operation_state(journal.records, plan["operation"]), paths,
                                   layout, d) + seal_problems(journal, plan)
        if not changed:
            return {"state": "full", "next": "postcheck, then release"}
        with owner_step(store, "writing the record back to partial"):
            done = owner.advance("partial", boot=boot, bound_boot=boot, set_bound_boot=True)
        if not landed(done):
            return {"state": "full", "refused": "withdrawing full did not complete within its deadline: "
                                                + "; ".join(changed), "next": "activate again"}
        return {"state": "partial", "refused": "full is withdrawn: " + "; ".join(changed),
                "next": "an owner decision: a target changed while maintenance was full"}
    if store.known(boot) is None:
        return {"state": rec.state, "refused": "full is refused: the boot can't be read: unknown",
                "next": "activate again once the boot can be read"}
    rec = acquire(owner, store, probes)  # the drain, repeated in this boot (trivial after a reboot)
    found = full_problems(journal, plan, ops, store, probes, layout, paths, d)
    if found:
        only_retirement = all(p.startswith(RETIREMENT) for p in found)
        return {"state": rec.state, "refused": "full is refused: " + "; ".join(found),
                "next": "reboot" if only_retirement else "resolve these, then activate again"}
    owner.fs.before_full = check  # and once more after the durable intent, immediately before the replacement
    try:
        with owner_step(store, "writing full"):
            done = owner.advance("full", boot=boot, legacy="retired-and-inhibited", binding=full_binding(plan, d))
    except Refused as e:
        return {"state": "partial", "refused": f"full is refused: {e}", "next": "resolve this, then activate again"}
    if not landed(done):
        return {"state": "partial", "refused": "full is refused: writing full did not complete within its deadline "
                                               "(a writer's section is still open)", "next": "activate again"}
    rec, problem = held_problem(store, fence, plan, ("full",))
    if problem is None and (rec.fields["boot"] != boot or rec.fields["binding"] != full_binding(plan, d)
                            or rec.fields["legacy"] != "retired-and-inhibited"):
        problem = "the record written is not this boot's full binding"
    if problem:
        raise Refused(f"blocked: after writing full, {problem}; full is not journaled")
    journal.append({"op": plan["operation"], "kind": "full", "boot": boot})
    return {"state": "full", "next": "launchd's bound bridge starts its authority under full and serves read-only "
                                     "status; then postcheck, then release"}


def backup_problem(t_plan, t, path, captured):
    """Why a captured target is not exactly what the sealed plan observed, or None (code review 6112483129,
    finding 1): a LaunchAgent's exact bytes, a link's destination, and each captured file of a folder (or its
    absence) against the plan's recorded hashes."""
    observed = t_plan.get("observed") if isinstance(t_plan.get("observed"), dict) else {}
    entry = captured.get("")
    if t["kind"] == "launchagent":
        if not (isinstance(entry, dict) and "bytes" in entry) or sha256_bytes(entry["bytes"]) != observed.get("sha256"):
            return "its property list's bytes are not the ones the plan recorded"
        return None
    if t["kind"] == "symlink":
        if not (isinstance(entry, dict) and "link" in entry) or observed.get("type") != "symlink":
            return "it is not the link the plan recorded"
        where = Path(os.path.normpath(os.path.join(os.path.dirname(path), entry["link"])))
        return None if shown(where) == observed.get("target") else "its link doesn't point where the plan recorded"
    files = observed.get("files")
    prefix = "chat/" if t["kind"] == "skill-folder" else "chat/scripts/"
    if captured.get(".") != {"directory": True} or not isinstance(files, dict):
        return "it is not the directory the plan recorded"
    for rel, e in sorted(captured.items()):
        if rel == ".":
            continue
        have = None if e is None else sha256_bytes(e["bytes"]) if isinstance(e, dict) and "bytes" in e else "other"
        if have != files.get(prefix + rel):
            return f"{rel} is not as the plan recorded"
    return None


# --- the staged release ------------------------------------------------------------------

def stage_operation(plan):
    """The completed stage operation this plan admitted, from the stager's
    journal: (records, operation)."""
    records = stage_release.read_journal(Path(plan["dest"]) / stage_release.JOURNAL)
    found = [op for op in stage_release.operations(records).values()
             if op["complete"] and isinstance(op["binding"], dict)
             and (op["binding"].get("admission") or {}).get("plan_sha256") == plan["digest"]]
    if len(found) != 1:
        raise Refused("the release is not staged under this plan's admission; run stage --plan first")
    b = found[0]["binding"]
    if b.get("release") != plan["release"] or b.get("manifest_sha256") != plan["manifest_sha256"] \
            or os.path.abspath(b.get("release_dir", "")) != plan["staged_release"]:
        raise Refused("the staged release's recorded binding is not this plan's release")
    return records, found[0]


def verify_staged_release(plan):
    """The staged release verified as the stager verifies a completed operation
    (stage_release.verify_staged): the artifacts against the manifest hash, the
    environment against its recorded inventory, the interpreter and the import
    check. Refused, naming the problem, on any change since staging."""
    records, op = stage_operation(plan)
    b = op["binding"]
    begin = next((r for r in records if r.get("op") == op["id"] and r.get("kind") == "begin"), None)
    installed = [r for r in records if r.get("op") == op["id"] and r.get("kind") == "outcome"
                 and r.get("step") == "install-package" and r.get("result") == "ok"]
    if begin is None or not isinstance(begin.get("manifest"), dict) or not installed:
        raise Refused("the stager's journal doesn't hold this release's staging record")
    writer = stage_release.Writer(Path(plan["dest"]), build_release.live_roots())
    try:
        writer.load(records, op["id"])
        stage_release.verify_staged(writer, f"{stage_release.RELEASES}/{plan['release']}", b["manifest_sha256"],
                                    begin["manifest"], b["interpreter"], installed[-1].get("inventory_sha256"))
    except Refused as e:
        raise Refused(f"blocked: the staged release doesn't verify ({e}); nothing was changed") from None
    except (TypeError, KeyError, ValueError, AttributeError) as e:
        raise Refused(f"blocked: the stager's record of this release is malformed ({e.__class__.__name__}); "
                      "nothing was changed") from None
    finally:
        writer.close()


# --- the guarded seal (#21's owner amendment 3; its APPROVE's four additions) -----------

SEAL_DIRS = ("plateia_chat", "plateia_chat/scripts")  # under the staged site-packages; nothing else is sealed
SEAL_BASELINE, SEAL_MODE = 0o755, 0o555
DARWIN_ACL_EXTENDED = 0x00000100  # acl_type_t ACL_TYPE_EXTENDED
LINUX_ACL_XATTRS = ("system.posix_acl_access", "system.posix_acl_default")
LINUX_FS_IOC_GETFLAGS = 0x80086601
LINUX_RESTRICTIVE_FLAGS = 0x00000010 | 0x00000020  # FS_IMMUTABLE_FL, FS_APPEND_FL


def current_uid():
    return os.getuid()


def _darwin_acl(fd):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    get = libc.acl_get_fd_np
    get.restype, get.argtypes = ctypes.c_void_p, [ctypes.c_int, ctypes.c_int]
    libc.acl_free.argtypes = [ctypes.c_void_p]
    ctypes.set_errno(0)
    acl = get(fd, DARWIN_ACL_EXTENDED)
    if acl:
        libc.acl_free(acl)
        return "present"
    err = ctypes.get_errno()
    if err == errno.ENOENT:
        return "none"
    raise Refused(f"its ACL can't be read ({errno.errorcode.get(err, err)})")


def _linux_flags(fd):
    import fcntl as _fcntl
    try:
        raw = _fcntl.ioctl(fd, LINUX_FS_IOC_GETFLAGS, bytes(8))
    except OSError as e:
        if e.errno in (errno.ENOTTY, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EINVAL):
            return None  # this file system keeps no inode flags
        raise Refused(f"its flags can't be read ({e.strerror or e})") from None
    return int.from_bytes(raw[:4], sys.byteorder)


def access_metadata(fd):
    """A directory's access metadata, read through its descriptor: its ACL
    state and its flags. Refused when it can't be read, is ambiguous, or could
    defeat the seal or be altered by the mode change: any extended ACL (it can
    grant writes a 0555 mode doesn't, and on Linux the mode change would
    rewrite its mask), and any flag on macOS, or an immutable or append-only
    flag on Linux."""
    if sys.platform == "darwin":
        if _darwin_acl(fd) != "none":
            raise Refused("it has an ACL, which could grant writes the seal forbids")
        flags = os.fstat(fd).st_flags
        if flags:
            raise Refused(f"it has file flags ({flags:#x}), whose effect on the seal is not established")
        return {"acl": "none", "flags": flags}
    if sys.platform.startswith("linux"):
        try:
            names = os.listxattr(fd)
        except OSError as e:
            if e.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP):
                raise Refused(f"its extended attributes can't be read ({e.strerror or e})") from None
            names = []
        if any(n in LINUX_ACL_XATTRS for n in names):
            raise Refused("it has a POSIX ACL, which the mode change would alter and which could grant writes")
        flags = _linux_flags(fd)
        if flags is not None and flags & LINUX_RESTRICTIVE_FLAGS:
            raise Refused(f"it has immutable or append-only flags ({flags:#x})")
        return {"acl": "none", "flags": flags}
    raise Refused(f"its access metadata can't be read on {sys.platform}")


def dir_metadata(fd, rel):
    """(identity, mode, owner, access metadata) of one pinned directory;
    Refused, naming it, when any of it is unreadable or ambiguous."""
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or type(st.st_dev) is not int or type(st.st_ino) is not int \
                or st.st_ino <= 0:
            raise Refused("its identity is ambiguous")
        return {"rel": rel, "dev": st.st_dev, "ino": st.st_ino, "mode": stat.S_IMODE(st.st_mode),
                "uid": st.st_uid, "access": access_metadata(fd)}
    except Refused as e:
        raise Refused(f"blocked: the staged {rel} can't be sealed: {e}; nothing was changed") from None


@contextlib.contextmanager
def pinned_seal_dirs(plan):
    """The two directories the seal may change, pinned: the package directory
    is never reached through a link, and scripts/ is opened relative to it."""
    sites = sorted((Path(plan["staged_release"]) / "env").glob("lib/python*/site-packages"))
    if len(sites) != 1:
        raise Refused("the staged environment has no single site-packages")
    site = open_dir(sites[0], what="the staged site-packages")
    fds = []
    try:
        for rel in SEAL_DIRS:
            fds.append((rel, subdir(site, rel.split("/")), sites[0] / rel))
        yield fds
    finally:
        for _, fd, _ in fds:
            os.close(fd)
        os.close(site)


def seal_paths_hold(dirs):
    """The live staged paths still name the pinned seal directories (code review
    slot 3, finding 4): Refused, naming the first that doesn't."""
    for rel, fd, path in dirs:
        if not still_there(path, fd):
            raise Refused(f"blocked: the staged {rel} was moved or replaced during the seal (its path no longer names "
                          "the pinned directory); the seal is not journaled, and nothing more was changed")


def seal_problem(recorded, found):
    """Why `found` (now) is not the journaled seal of `recorded` (the
    originals): another directory, owner or access metadata, or a mode other
    than 0555."""
    for o, m in zip(recorded, found):
        if (o["dev"], o["ino"]) != (m["dev"], m["ino"]):
            return f"{o['rel']} is another directory now (replaced)"
        if (o["uid"], o["access"]) != (m["uid"], m["access"]):
            return f"{o['rel']}'s owner or access metadata changed"
        if m["mode"] != SEAL_MODE:
            return f"{o['rel']}'s mode is {m['mode']:#o}, not the sealed 0555"
    return None


def seal_release(journal, plan, read_only=False):
    """The guarded seal (amendment 3), or the verification of a seal already
    journaled. Both baselines are verified before either directory changes:
    mode exactly 0755, this user's, a definite identity, and access metadata
    that can't defeat the seal. The originals are journaled
    (release-seal-baseline) before the first fchmod; each directory is then
    set to 0555 through its pinned descriptor, nothing else changes, and
    release-sealed is journaled with the originals. A resume accepts 0555 only
    for this operation's journaled seal, with matching identities; a 0555 this
    operation didn't journal is unknown, and nothing is restored. A completed
    seal that drifted blocks, and is never sealed again silently."""
    op = plan["operation"]
    mine = [r for r in journal.records if r.get("op") == op]
    sealed = next((r for r in reversed(mine) if r["kind"] == "release-sealed"), None)
    intent = next((r for r in reversed(mine) if r["kind"] == "release-seal-baseline"), None)
    with pinned_seal_dirs(plan) as dirs:
        found = [dir_metadata(fd, rel) for rel, fd, _ in dirs]
        seal_paths_hold(dirs)
        if sealed is not None:
            problem = seal_problem(sealed["dirs"], found)
            if problem:
                raise Refused(f"blocked: the release's seal drifted ({problem}); it is never sealed again silently, "
                              "and nothing more was changed")
            return
        if read_only:
            raise Refused("blocked: a bridge of this release is running, but this operation never journaled its "
                          "seal; nothing was changed")
        if intent is not None:
            originals = intent["dirs"]
            for o, m in zip(originals, found):
                if (o["dev"], o["ino"], o["uid"], o["access"]) != (m["dev"], m["ino"], m["uid"], m["access"]) \
                        or m["mode"] not in (SEAL_BASELINE, SEAL_MODE):
                    raise Refused(f"blocked: {o['rel']} is not as this operation recorded it before sealing "
                                  "(unknown); nothing was changed")
        else:
            for m in found:  # both baselines, before either changes
                if m["mode"] != SEAL_BASELINE:
                    raise Refused(f"blocked: the staged {m['rel']}'s mode is {m['mode']:#o}, not 0755 (stricter, "
                                  "different, or a seal this operation never journaled: unknown); nothing was "
                                  "changed")
                if m["uid"] != current_uid():
                    raise Refused(f"blocked: the staged {m['rel']} is owned by another user; nothing was changed")
            originals = found
            journal.append({"op": op, "kind": "release-seal-baseline", "dirs": originals})
        for (rel, fd, _), m in zip(dirs, found):
            if m["mode"] != SEAL_MODE:
                seal_paths_hold(dirs)  # around each fchmod: the live paths still name the pinned directories
                os.fchmod(fd, SEAL_MODE)
                seal_paths_hold(dirs)
        problem = seal_problem(originals, [dir_metadata(fd, rel) for rel, fd, _ in dirs])
        if problem:
            raise Refused(f"blocked: the seal didn't take as recorded ({problem}); nothing more was changed")
        seal_paths_hold(dirs)  # before the seal is declared complete, and the bootstrap and binding go on
        journal.append({"op": op, "kind": "release-sealed", "dirs": originals})


def seal_report(plan, mine):
    """The post-check's view of the seal: "matches", or why not (read-only)."""
    sealed = next((r for r in reversed(mine["records"]) if r["kind"] == "release-sealed"), None)
    if sealed is None:
        return "not journaled by this operation"
    try:
        with pinned_seal_dirs(plan) as dirs:
            seal_paths_hold(dirs)
            problem = seal_problem(sealed["dirs"], [dir_metadata(fd, rel) for rel, fd, _ in dirs])
    except Refused as e:
        return f"unreadable: {e}"
    return f"drifted: {problem}" if problem else "matches"


def staged_layout(plan):
    """Where the staged release's commands, scripts and skill are (read after
    verify_staged_release)."""
    stage_operation(plan)
    rel = Path(plan["staged_release"])
    env = rel / "env"
    sites = sorted(env.glob("lib/python*/site-packages/plateia_chat/scripts"))
    if len(sites) != 1:
        raise Refused("the staged environment has no single plateia_chat/scripts")
    manifest = verified_manifest(plan)
    with zipfile.ZipFile(io.BytesIO(verified_artifact(plan, manifest, manifest["skill"]["artifact"]))) as z:
        skill_md = z.read(f"{manifest['skill']['name']}/SKILL.md")
    files = sorted(p.name for p in sites[0].iterdir() if p.is_file())
    return {"env": env, "bin": env / "bin", "scripts": sites[0], "skill": skill_md, "files": files}


def verified_manifest(plan):
    """The staged release's manifest, read once and accepted only if its bytes
    are the ones the plan pinned (the manifest hash)."""
    fd = open_dir(Path(plan["staged_release"]) / "artifacts", what="the staged artifacts")
    try:
        data = read_fd_at(fd, MANIFEST)
    finally:
        os.close(fd)
    if sha256_bytes(data) != plan["manifest_sha256"]:
        raise Refused("blocked: the staged manifest is not the one the plan pinned; nothing was changed")
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("artifacts"), list):
        raise Refused("blocked: the staged manifest is malformed; nothing was changed")
    return manifest


def verified_artifact(plan, manifest, name):
    """One staged artifact's bytes, read once and accepted only if they hash to
    the manifest's entry for it."""
    want = next((a.get("sha256") for a in manifest["artifacts"] if isinstance(a, dict) and a.get("name") == name),
                None)
    fd = open_dir(Path(plan["staged_release"]) / "artifacts", what="the staged artifacts")
    try:
        data = read_fd_at(fd, name)
    finally:
        os.close(fd)
    if want is None or sha256_bytes(data) != want:
        raise Refused(f"blocked: the staged artifact {name} is not the manifest's; nothing was changed")
    return data


def read_fd_at(dir_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | O_NOFOLLOW, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused(f"{name} is not a regular file")
        return read_fd(fd)
    finally:
        os.close(fd)


def load_release_module(plan, name):
    """A module of the pinned release's own chat code, executed from the
    hash-verified wheel artifact under a private name: never from an installed
    file that could have changed, never from a source checkout, never on
    sys.path."""
    manifest = verified_manifest(plan)
    wheel = verified_artifact(plan, manifest, manifest["package"]["artifact"])
    with zipfile.ZipFile(io.BytesIO(wheel)) as z:
        source = z.read(f"{manifest['package'].get('import_name', 'plateia_chat')}/scripts/{name}.py")
    module = types.ModuleType(f"plateia_activation_{name}")
    module.__file__ = f"<{manifest['release']} wheel>/{name}.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


# --- the seven targets ----------------------------------------------------------------------

def target_paths(spec_targets, skills):
    return {t["name"]: (t, stage_release.expand(t["path"], skills)) for t in spec_targets}


def container_of(t, path):
    """The directory a target's entries live in: a folder target is its own; a
    link or a LaunchAgent lives in its parent."""
    return path if t["kind"] in FOLDERS else path.parent


def entry_name(t, path, rel):
    return rel if t["kind"] in FOLDERS else path.name


def open_container(t, path, expect=None):
    """A descriptor of the target's directory: a folder target is never reached
    through a link; with `expect` (recorded at backup), refused unless it is
    that directory. None when it is missing and nothing was recorded."""
    folder = t["kind"] in FOLDERS
    try:
        fd = os.open(container_of(t, path), os.O_RDONLY | O_DIRECTORY | (O_NOFOLLOW if folder else 0))
    except FileNotFoundError:
        if expect is None:
            return None
        raise Refused(f"blocked: {t['name']}'s directory is missing; nothing more was changed") from None
    except OSError as e:
        if expect is None and folder:
            return "not-a-directory"
        raise Refused(f"blocked: {t['name']}'s directory can't be opened ({e.strerror or e}); nothing more was "
                      "changed") from None
    if expect is not None and ident(os.fstat(fd)) != list(expect):
        os.close(fd)
        raise Refused(f"blocked: {t['name']}'s directory was moved or replaced since it was backed up; nothing "
                      "more was changed")
    return fd


def entry_at(fd, name, link=False):
    """One entry, read relative to its pinned directory and never followed: a
    link's target, a regular file's (sha256, mode, bytes), None if missing, or
    what else it is."""
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if link:
        return {"link": os.readlink(name, dir_fd=fd)} if stat.S_ISLNK(st.st_mode) else {"not-a-link": True}
    if not stat.S_ISREG(st.st_mode):
        return {"not-a-file": os.readlink(name, dir_fd=fd) if stat.S_ISLNK(st.st_mode) else stat.S_IFMT(st.st_mode)}
    efd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | O_NOFOLLOW, dir_fd=fd)
    try:
        data = read_fd(efd)
        mode = os.fstat(efd).st_mode & 0o777
    finally:
        os.close(efd)
    return {"sha256": sha256_bytes(data), "mode": mode, "bytes": data}


def observe_target(t, path, layout, expect=None):
    """(state, container identity): the target's entries read through its
    pinned directory ("." is a folder target's own kind)."""
    fd = open_container(t, path, expect)
    folder = t["kind"] in FOLDERS
    if fd is None:
        return ({".": None} if folder else {"": None}), None
    if fd == "not-a-directory":
        return {".": {"not-a-directory": True}}, None
    try:
        if folder:
            names = ["SKILL.md"] if t["kind"] == "skill-folder" else layout["files"]
            state = {".": {"directory": True}, **{n: entry_at(fd, n) for n in names}}
        else:
            state = {"": entry_at(fd, path.name, link=t["kind"] == "symlink")}
        return state, ident(os.fstat(fd))
    finally:
        os.close(fd)


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
        old = (original or {}).get("")
        if not isinstance(old, dict) or "bytes" not in old:
            raise Refused(f"blocked: {t['name']} was not a regular file when it was backed up")
        try:
            data = plistlib.loads(old["bytes"])
        except (plistlib.InvalidFileException, ValueError):
            raise Refused(f"blocked: {t['name']} is not a readable property list") from None
        if not isinstance(data, dict):
            raise Refused(f"blocked: {t['name']} is not a property-list dictionary")
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


def invocation_problem(t, entry, layout, d):
    """For a bound LaunchAgent's entry: None when launchd would run exactly the
    desired invocation (its effective executable and its arguments)."""
    if not isinstance(entry, dict) or "bytes" not in entry:
        return "missing or not a regular file"
    try:
        plist = plistlib.loads(entry["bytes"])
    except (plistlib.InvalidFileException, ValueError):
        return "not a readable property list"
    if not isinstance(plist, dict):
        return "not a property-list dictionary"
    want = desired_invocation(t, layout, d)
    if plist.get("ProgramArguments") != want:
        return "its ProgramArguments are not the pinned release's invocation"
    if effective_executable(plist) != want[0]:
        return "launchd would execute something else (its Program key)"
    return None


def entry_digest(entry):
    return digest(None if entry is None else {k: v for k, v in entry.items() if k != "bytes"})


def summary_of(state):
    """A state's comparable digest, without the bytes."""
    return digest({k: (None if v is None else {kk: vv for kk, vv in v.items() if kk != "bytes"})
                   for k, v in sorted(state.items())})


FINAL_CHECK = contextvars.ContextVar("activate_release_final_check", default=None)


def write_entry_at(fd, name, entry, tmp):
    """One entry made `entry` relative to its pinned directory, atomically: a
    temporary created exclusively, synced, renamed into place; then the
    directory synced. Never follows a link. The caller's final check
    (FINAL_CHECK) runs at the final mutation boundary, after the temporary is
    prepared and immediately before the rename or removal (PR #22 review 2,
    finding 1); if it raises, the temporary is removed and nothing is
    replaced."""
    ready = FINAL_CHECK.get()

    def final():
        if ready is None:
            return
        try:
            ready()
        except BaseException:
            if entry is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp, dir_fd=fd)
            raise
    if entry is None:
        final()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name, dir_fd=fd)
    elif "link" in entry:
        os.symlink(entry["link"], tmp, dir_fd=fd)
        final()
        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    else:
        efd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW, entry["mode"], dir_fd=fd)
        try:
            write_fd(efd, entry["bytes"])
            os.fchmod(efd, entry["mode"])
            os.fsync(efd)
        finally:
            os.close(efd)
        final()
        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def clear_own_temporaries(journal, plan, t, path, backup):
    """A crash between creating an entry's temporary and renaming it leaves the
    temporary in the target's folder. Each one this operation journaled (its
    random name chosen and journaled before the exclusive create) is removed
    before writing resumes; nothing it didn't journal is touched."""
    names = [r["tmp"] for r in journal.records if r.get("op") == plan["operation"]
             and r["kind"] in ("entry-binding", "entry-restoring") and r["target"] == t["name"]]
    if not names:
        return
    fd = open_container(t, path, backup.get("container"))
    if not isinstance(fd, int):
        return
    try:
        for name in names:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def apply_entries(journal, plan, t, path, backup, writes, kind, guard=None):
    """Write each (rel, before, after) entry of one target through its pinned
    directory, journaled entry by entry (`kind` "binding" or "restoring").
    Each entry and its directory's path are checked against what was judged
    before the durable intent is journaled, and again after it, immediately
    before the write (code review slot 3, finding 3): a change in between
    refuses, and is kept. `guard` (the producers fenced, and for a rollback
    the current first-write evidence) runs immediately before every write.
    The directory's path is checked again after every write."""
    op = plan["operation"]
    follow = t["kind"] not in FOLDERS

    def unchanged(fd, name, before, rel):
        if not still_there(container_of(t, path), fd, follow=follow):
            raise Refused(f"blocked: {t['name']}'s directory was moved or replaced; nothing more was changed")
        if entry_digest(entry_at(fd, name, link=t["kind"] == "symlink")) != entry_digest(before):
            raise Refused(f"blocked: {t['name']} ({rel or name}) changed just before it was written: the change is "
                          "kept, and nothing more was changed")
    clear_own_temporaries(journal, plan, t, path, backup)
    fd = open_container(t, path, backup.get("container"))
    if not isinstance(fd, int):
        raise Refused(f"blocked: {t['name']}'s directory is missing; nothing more was changed")
    try:
        for rel, before, after in writes:
            name = entry_name(t, path, rel)
            unchanged(fd, name, before, rel)
            tmp = f".plateia-activation-{secrets.token_hex(6)}"
            journal.append({"op": op, "kind": f"entry-{kind}", "target": t["name"], "rel": rel, "tmp": tmp,
                            "from": entry_digest(before), "to": entry_digest(after)})
            if guard is not None:
                guard()
            unchanged(fd, name, before, rel)  # after the durable intent and the guard, before the temporary

            def ready(name=name, before=before, rel=rel):
                """At the final boundary, with the temporary prepared: the guard (the binding's, or rollback's
                eligibility with its first-write evidence) and the entry and its directory, again."""
                if guard is not None:
                    guard()
                unchanged(fd, name, before, rel)
            token = FINAL_CHECK.set(ready)
            try:
                write_entry_at(fd, name, after, tmp)
            finally:
                FINAL_CHECK.reset(token)
            journal.append({"op": op, "kind": "entry-bound" if kind == "binding" else "entry-restored",
                            "target": t["name"], "rel": rel, "digest": entry_digest(after)})
            if not still_there(container_of(t, path), fd, follow=follow):
                raise Refused(f"blocked: {t['name']}'s directory was moved or replaced during the write; nothing "
                              "more was changed")
    finally:
        os.close(fd)


def judge_entries(t, state, original, allowed_after, plan_name):
    """Each entry is as recorded before (original) or as this operation made or
    makes it (allowed_after: rel -> digests); anything else was changed by
    something else. Returns the rels whose current entry is not yet `target`."""
    if t["kind"] in FOLDERS and state.get(".") != {"directory": True}:
        raise Refused(f"blocked: {plan_name} is not a real directory; nothing was changed")
    for rel in set(state) | set(original):
        if rel == ".":
            continue
        current = entry_digest(state.get(rel))
        if current != entry_digest(original.get(rel)) and current not in allowed_after.get(rel, set()):
            raise Refused(f"blocked: {plan_name} ({rel or 'its entry'}) is neither as recorded before activation "
                          "nor as this operation wrote it: it was changed by something else; nothing more was "
                          "changed")


def backup_target(journal, op, t, state):
    """The target's original state, kept privately under the pinned root (D-27):
    mode 0600 files and an index. Returns the folder, relative to the root."""
    parts = [BACKUPS, op, re.sub(r"[^A-Za-z0-9._-]+", "_", t["name"])]
    fd = subdir(journal.root_fd, parts, create=True)
    try:
        index = {}
        for rel, entry in sorted(state.items()):
            if entry is None or "bytes" not in entry:
                index[rel] = entry
                continue
            name = f"entry-{len(index)}"
            write_private_at(fd, name, entry["bytes"])
            index[rel] = {"sha256": entry["sha256"], "mode": entry["mode"], "file": name}
        write_private_at(fd, "index.json", (json.dumps(index, sort_keys=True) + "\n").encode())
        fsync_dir(fd)
    finally:
        os.close(fd)
    return "/".join(parts)


def load_backup(root_fd, backup):
    """A journaled backup, read through the pinned root, and accepted only if it is exactly the state the
    journal recorded when it was taken (its immutable `digest`), not only consistent with its own mutable index
    (PR #22 review 3, finding 1). Refused otherwise, before anything uses it."""
    state = restore_state(root_fd, backup["folder"])
    if summary_of(state) != backup.get("digest"):
        raise Refused(f"blocked: the private backup {backup['folder']} doesn't match its journaled digest: it is "
                      "never used for reconciliation or restoration; nothing more was changed")
    return state


def restore_state(root_fd, folder):
    """A backed-up state, read through the pinned root and verified."""
    parts = folder.split("/")
    if parts[0] != BACKUPS or any(p in ("", ".", "..") for p in parts):
        raise Refused("the journal names a backup outside the private root")
    fd = subdir(root_fd, parts)
    try:
        raw, _ = read_private_at(fd, "index.json")
        try:
            index = json.loads(raw)
        except ValueError:
            index = None
        if not isinstance(index, dict):
            raise Refused(f"the private backup {folder} is malformed; nothing was restored")
        out = {}
        for rel, entry in index.items():
            if entry is None or (isinstance(entry, dict) and "file" not in entry):
                out[rel] = entry
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) \
                    or not isinstance(entry.get("sha256"), str) or type(entry.get("mode")) is not int:
                raise Refused(f"the private backup {folder} is malformed; nothing was restored")
            data, _ = read_private_at(fd, entry["file"])
            if sha256_bytes(data) != entry["sha256"]:
                raise Refused(f"the private backup {folder} doesn't verify; nothing was restored")
            out[rel] = {"sha256": entry["sha256"], "mode": entry["mode"], "bytes": data}
        return out
    finally:
        os.close(fd)


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
    """Seal, acquire maintenance, back up, publish the bootstrap and bind the
    seven targets, under the fenced journal (writer admission 9.1, steps 1 to
    6); run again after the owner's reboot, verify retirement and inhibition
    and write `full` (step 8). A resume takes the original plan for its own
    journaled operation only."""
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
        if mine["released"]:
            return {"state": "released", "operation": plan["operation"], "release": plan["release"]}
        if not mine["admitted"]:
            stage_release.admission(plan, plan["release_path"], spec_path, activation_path)
        elif [r for r in mine["records"] if r["kind"] == "admitted"][0]["plan_sha256"] != plan["digest"]:
            raise Refused("the journaled operation was admitted with other inputs; nothing was changed")
        verify_staged_release(plan)  # before every activation and resume: nothing changed since staging
        layout = staged_layout(plan)
        # This operation's own bridge (holding, or serving read-only status under `full`) permits the
        # maintenance transitions, but no target write; any other running bridge, or an unknown state, blocks
        # every change (amendment 4's APPROVE, K1).
        read_only = bridge_verdict(ops, plan) == "expected"
        if read_only and mine["digest"] is None:
            raise Refused("blocked: a bridge of this release is running, but this operation never journaled its "
                          "bootstrap; nothing was changed")
        skills = skills_root(spec_path)
        paths = target_paths(spec_targets, skills)
        d = sha256_bytes(bootstrap_bytes(plan))  # the plan alone determines D
        if mine["selected"]:
            ownership = take_full(journal, plan, ops, layout, paths, d)
            return dict(postcheck(plan_path, ops, spec_path), state="already selected", maintenance=ownership)
        if not read_only:
            check_producers(ops, plan)
        if not mine["admitted"]:
            journal.append({"op": plan["operation"], "kind": "admitted", "plan_sha256": plan["digest"],
                            "release": plan["release"], "targets": {t["name"]: t["digest"] for t in plan["targets"]}})
        pending = [t["name"] for t in plan["targets"] if t["name"] not in mine["backups"]]
        if pending:
            # Every original is backed up before anything is published or bound,
            # so a target still unbacked has not been touched: it must still be
            # exactly as the plan observed it, or nothing more happens.
            if read_only or any(r["kind"] in ("binding", "entry-binding", "bound", "bootstrap-intent")
                                for r in mine["records"]):
                raise Refused(f"blocked: {', '.join(pending)} has no backup, but this operation already went past "
                              "its backups; nothing was changed")
            now_targets = stage_release.make_plan(plan["release_path"], None, spec_path)["targets"]
            changed = [n["name"] for n, old in zip(now_targets, plan["targets"])
                       if n["name"] in pending and (n["digest"] != old["digest"] or n["problems"])]
            if changed:
                raise Refused(f"blocked: {', '.join(changed)} changed since the activation plan was made; "
                              "nothing more was changed")
        if mine["digest"] not in (None, d):
            raise Refused("the journaled bootstrap digest is not this plan's; nothing was changed")
        if not any(r["kind"].startswith("bootstrap") for r in mine["records"]):
            sfd = open_state_root(plan)  # read-only, before anything is acquired: a record nobody here published
            try:
                foreign = entry_at(sfd, bootstrap_name()) is not None
            finally:
                os.close(sfd)
            if foreign:
                raise Refused(f"blocked: {bootstrap_name()} exists, and this operation's journal never staged a "
                              "record: it is retained, never replaced, and this needs an owner decision")
        seal_release(journal, plan, read_only)  # step 2: verified, or guarded and journaled
        seal_release(journal, plan, read_only=True)  # and verified again across that durable record
        store, owner, fence = maintenance(journal, plan, ops)
        probes = ControllerProbes(ops, store)
        acquire(owner, store, probes)  # step 3: new-release writers drained and refused (`partial`)
        by_name = {t["name"]: t for t in plan["targets"]}
        for name in pending:  # step 4, admitted in `partial` (writer admission 6.2)
            t, path = paths[name]
            current, container = observe_target(t, path, layout)
            problem = backup_problem(by_name[name], t, path, current)
            if problem:
                raise Refused(f"blocked: {name} changed since the activation plan was made ({problem}): the change "
                              "is kept, and nothing more was changed")
            folder = backup_target(journal, plan["operation"], t, current)
            if summary_of(restore_state(journal.root_fd, folder)) != summary_of(current):
                raise Refused(f"blocked: {name}'s backup doesn't read back as captured; nothing more was changed")
            journal.append({"op": plan["operation"], "kind": "backup", "target": name, "folder": folder,
                            "digest": summary_of(current), "container": container})
        mine = operation_state(journal.records, plan["operation"])
        steps = []
        for t_plan in plan["targets"]:  # every target is judged before anything is published or written
            t, path = paths[t_plan["name"]]
            backup = mine["backups"][t["name"]]
            current, _ = observe_target(t, path, layout, backup.get("container"))
            original = load_backup(journal.root_fd, backup)
            desired = desired_target(t, path, layout, original, d)
            allowed = {rel: {entry_digest(e)} for rel, e in desired.items()}
            judge_entries(t, current, original, allowed, t["name"])
            writes = [(rel, current.get(rel), desired.get(rel)) for rel in sorted(set(current) | set(desired))
                      if rel != "." and entry_digest(current.get(rel)) != entry_digest(desired.get(rel))]
            if writes and read_only:
                raise Refused(f"blocked: {t['name']} isn't bound while a bridge runs; binding waits for it to stop; "
                              "nothing was changed")
            steps.append((t, path, backup, original, desired, writes))
        seal_release(journal, plan, read_only=True)  # still sealed as journaled, before anything is published
        if not read_only:
            publish_bootstrap(journal, plan)  # step 5, admitted in `partial`
        # The binding's boot, recorded before the first target write of this boot: retirement then needs a boot
        # change after every binding write (writer admission 8.1). A resume in a new boot records it again.
        boot = probes.boot()
        rec = require_held(store, fence, plan, ("partial",), "nothing is bound")
        if rec.fields["bound_boot"] != boot:
            with owner_step(store, "recording the binding's boot"):
                done = owner.advance("partial", boot=boot, bound_boot=boot, set_bound_boot=True)
            if not landed(done):
                raise Refused("blocked: recording the binding's boot did not complete within its deadline (a "
                              "writer's section is still open); nothing is bound; run activate again")
            rec = require_held(store, fence, plan, ("partial",), "nothing is bound")
            if rec.fields["bound_boot"] != boot:
                raise Refused("blocked: the record does not hold the binding's boot; nothing is bound")

        def binding_guard():
            """Before each write: no bridge started, the record still this
            operation's own and `partial` (which is what fences every
            new-release writer), and the release still sealed as journaled."""
            if bridge_verdict(ops, plan) != "stopped":
                raise Refused("blocked: a bridge started during binding; nothing more was changed")
            require_held(store, fence, plan, ("partial",), "nothing more is bound")
            seal_release(journal, plan, read_only=True)
        for t, path, backup, original, desired, writes in steps:  # step 6
            if writes:
                journal.append({"op": plan["operation"], "kind": "binding", "target": t["name"],
                                "from": summary_of(original), "to": summary_of(desired)})
                apply_entries(journal, plan, t, path, backup, writes, "binding", guard=binding_guard)
            if t["kind"] == "launchagent":
                bound, _ = observe_target(t, path, layout, backup.get("container"))
                problem = invocation_problem(t, bound.get(""), layout, d)
                if problem:
                    raise Refused(f"{t['name']} doesn't run the pinned release after binding: {problem}")
            if writes or t["name"] not in mine["bound"]:
                journal.append({"op": plan["operation"], "kind": "bound", "target": t["name"],
                                "digest": summary_of(desired)})
        change = skills_change(skills, layout, paths)
        journal.append({"op": plan["operation"], "kind": "selected", "release": plan["release"],
                        "skills_repository_change": change})
        return {"state": "selected", "operation": plan["operation"], "release": plan["release"],
                "bootstrap_digest": d, "skills_repository_change": change,
                "maintenance": {"state": "partial", "next": "reboot"},
                "next": "the owner reboots the machine (retirement, gate G-1); launchd then starts the bound bridge, "
                        "which waits in its hold; then run activate again to verify retirement and inhibition and "
                        "write full; then postcheck, then release. The activation is complete only once released"}


def serving_evidence(reply):
    """A live, well-formed answer to the read-only status request: the current
    authority instance (its epoch) serving."""
    epoch = reply.get("authority_epoch") if isinstance(reply, dict) else None
    return isinstance(reply, dict) and reply.get("v") == "outbox-authority/1" and reply.get("result") == "ok" \
        and type(epoch) is int and epoch >= 1 and isinstance(reply.get("status"), dict)


def postcheck(plan_path, ops, spec_path=None):
    """Read-only: verifies the staged release, recomputes the record and D from
    the sealed plan and compares them with the state-root record and every
    bound target (each LaunchAgent by its effective executable and exact
    arguments), checks the bridge and asks its authority, live, for its
    status. It never opens the outbox database. The activation is complete
    only when every check passes.

    The live answer ties the evidence to this activation: once the record
    exists, a bridge launched without its exact digest is UNKNOWN and never
    binds its socket, so an authority that answers was started for this D.

    It also reports the maintenance record (writer admission 6.4, 9.3): it
    must be this operation's own last journaled write, in `full`, binding
    this release, D and the bridge's start (or released by this operation).
    The record is reported beside the checks, not among them."""
    plan = load_plan(plan_path)
    records = read_journal(plan["root"])
    mine = operation_state(records, plan["operation"])
    data = bootstrap_bytes(plan)
    d = sha256_bytes(data)
    checks = [("bootstrap digest", "matches" if mine["digest"] == d else "not journaled as this plan's")]
    try:
        verify_staged_release(plan)
        checks.append(("staged release", "matches"))
    except Refused as e:
        checks.append(("staged release", f"differs: {e}"))
    try:
        layout = staged_layout(plan)
    except Refused as e:  # the targets can't be judged against a release that doesn't verify
        checks.append(("staged release layout", f"unreadable: {e}"))
        return {"operation": plan["operation"], "selected": mine["selected"], "checks": checks,
                "bridge": "not checked", "authority_serving": False, "authority_epoch": None,
                "first_writes": first_write_evidence(plan["state_root"]["path"], plan["state_root"]), "ok": False}
    # The record's two release fields against the pinned release itself (#21's owner amendment 2): the
    # staged release's own manifest hash and its directory's identity.
    staged = Path(plan["staged_release"])
    try:
        staged_sha = stage_release.drift_check.sha256(staged / "artifacts" / MANIFEST)
    except OSError:
        staged_sha = None
    checks.append(("release seal", seal_report(plan, mine)))
    fields = release_fields(plan)
    checks.append(("pinned release", "matches" if staged_sha == fields["release"] and staged.name == fields[
        "release_id"] else "the staged release's manifest or identity is not the record's"))
    try:
        sfd = open_state_root(plan)
        try:
            found, _ = read_private_at(sfd, bootstrap_name(), cap=1 << 16)
        finally:
            os.close(sfd)
        checks.append(("bootstrap record", "matches" if found == data else "differs from the plan's"))
    except (FileNotFoundError, Refused) as e:
        checks.append(("bootstrap record", f"missing or unreadable ({e.__class__.__name__})"))
    paths = target_paths(stage_release.load_spec(spec_path)[0]["targets"], skills_root(spec_path))
    root_fd = None
    try:
        root_fd = open_dir(plan["root"], what="the activation root")
        for name, (t, path) in paths.items():
            backup = mine["backups"].get(name)
            if backup is None:
                checks.append((name, "not bound by this operation"))
                continue
            try:
                current, _ = observe_target(t, path, layout, backup.get("container"))
                want = desired_target(t, path, layout, load_backup(root_fd, backup), d)
            except Refused as e:
                checks.append((name, f"differs: {e}"))
                continue
            same = summary_of(current) == summary_of(want)
            problem = invocation_problem(t, current.get(""), layout, d) if t["kind"] == "launchagent" else None
            checks.append((name, "bound" if same and not problem else "differs" + (f": {problem}" if problem else "")))
    except Refused as e:
        checks.append(("activation root", f"unreadable: {e}"))
    finally:
        if root_fd is not None:
            os.close(root_fd)
    try:
        bridge = bridge_verdict(ops, plan)
    except Refused as e:
        bridge = f"blocks any mutation: {e}"
    reply = ops.authority_status(plan) if bridge == "expected" else None
    serving = serving_evidence(reply)
    held = maintenance_report(plan, ops, records, d)
    ok = mine["selected"] and bridge == "expected" and serving and held["ok"] \
        and all(c[1] in ("bound", "matches") for c in checks)
    return {"operation": plan["operation"], "selected": mine["selected"], "checks": checks, "bridge": bridge,
            "authority_serving": serving, "authority_epoch": reply.get("authority_epoch") if serving else None,
            "maintenance": held,
            "first_writes": first_write_evidence(plan["state_root"]["path"], plan["state_root"]), "ok": ok}


def maintenance_report(plan, ops, records, d):
    """The record as the post-check judges it, read-only: {state, problem, refused_files, ok}."""
    mine = operation_state(records, plan["operation"])
    try:
        store, _, fence = maintenance(None, plan, ops, records=records)
    except Refused as e:
        return {"state": "not checked", "problem": str(e), "refused_files": None, "ok": False}
    paths = store.Paths(plan["state_root"]["path"])
    count = store.sink_count(paths)
    if mine["released"]:
        rec = store.read_maintenance(paths)
        return {"state": "released", "problem": None if not rec.present else "a record is present again",
                "refused_files": count, "ok": not rec.present}
    rec, problem = held_problem(store, fence, plan, ("full",))
    if problem is None and (rec.fields["binding"] != full_binding(plan, d)
                            or rec.fields["legacy"] != "retired-and-inhibited"):
        problem = "the record does not bind this release, D and the bridge's start, retired and inhibited"
    return {"state": rec.state, "problem": problem, "refused_files": count, "ok": problem is None}


def rollback(plan_path, ops, spec_path=None, crash=None):
    """Before the first new-format write only: restore the recorded targets
    exactly (D-27), entry by entry, which removes the launch option. The
    bootstrap record is left in place, and journaled. The first writes are
    judged in the plan's sealed state root alone (its realpath, device and
    inode): a missing, replaced or unreadable root is unknown, and refuses; a
    file in the refusal sink is a first write too (writer admission 12).
    After the first write, refused: the recovery is a reviewed repair or a
    roll-forward that keeps the newer state (D-74).

    Every new-release writer is fenced by the maintenance record, which must
    be this operation's own and held, checked before anything and again
    before every restoring write (amendment 4, section 3; code review
    6112483129, finding 2). The record is kept: `release` ends it once the
    boot has changed since the restore. This adds no automatic rollback and
    no rollback trigger."""
    plan = load_plan(plan_path)
    with Journal(plan["root"], crash) as journal:
        mine = operation_state(journal.records, plan["operation"])
        if not mine["admitted"]:
            raise Refused("this operation never began; there is nothing to roll back")
        if mine["rolled_back"]:
            return {"state": "already rolled back", "operation": plan["operation"]}
        if mine["released"]:
            raise Refused("rollback refused: this operation was released, so writers have been admitted since; the "
                          "recovery is a reviewed repair or a roll-forward; nothing was changed")
        bridge = ops.bridge_state() or {}
        if bridge.get("running") is not False:
            raise Refused("rollback refused for now: a bridge is running, or its state is unknown; it must be stopped "
                          "first; nothing was changed")
        store, owner, fence = maintenance(journal, plan, ops)
        if fence.pending() is not None:
            raise Refused("rollback refused for now: a maintenance step is pending in the journal; activate (or "
                          "release) settles it first; nothing was changed")

        def eligible():
            """Rollback stays eligible all the way through: checked before
            anything and again immediately before every restoring write. No
            bridge has started, the maintenance record is still this
            operation's own and held (so every new-release writer is fenced),
            and the plan's sealed state root still shows no first write,
            judged now."""
            if (ops.bridge_state() or {}).get("running") is not False:
                raise Refused("blocked: a bridge started, or its state became unknown, during rollback; nothing "
                              "more was changed")
            evidence = first_write_evidence(plan["state_root"]["path"], plan["state_root"])
            written = {k: v for k, v in evidence.items() if v != "absent"}
            if written:
                journal.append({"op": plan["operation"], "kind": "rollback-refused", "evidence": evidence})
                raise Refused("rollback refused: " + "; ".join(f"{k}: {v}" for k, v in sorted(written.items()))
                              + ". Old code can't read the new state, or nothing shows it wasn't written, so the "
                                "recovery is repair or roll-forward: a reviewed repair, or a roll-forward that keeps "
                                "the newer state (D-74). The recorded targets, backups, the state and any refused "
                                "writes are kept, and nothing more was changed")
            require_held(store, fence, plan, ("partial", "full"),
                         "new-release writers are not fenced and nothing is restored")
        eligible()
        layout = staged_layout(plan)
        paths = target_paths(stage_release.load_spec(spec_path)[0]["targets"], skills_root(spec_path))
        steps = []
        for t_plan in reversed(plan["targets"]):
            t, path = paths[t_plan["name"]]
            backup = mine["backups"].get(t["name"])
            if backup is None:
                continue
            original = load_backup(journal.root_fd, backup)
            current, _ = observe_target(t, path, layout, backup.get("container"))
            # Ownership evidence, entry by entry: what this operation journaled
            # writing, and what the staged release makes it now.
            allowed = {}
            for (target, rel), to in mine["written"].items():
                if target == t["name"]:
                    allowed.setdefault(rel, set()).add(to)
            if mine["digest"] is not None:
                for rel, e in desired_target(t, path, layout, original, mine["digest"]).items():
                    allowed.setdefault(rel, set()).add(entry_digest(e))
            judge_entries(t, current, original, allowed, t["name"])
            writes = [(rel, current.get(rel), original.get(rel)) for rel in sorted(set(current) | set(original))
                      if rel != "." and entry_digest(current.get(rel)) != entry_digest(original.get(rel))]
            steps.append((t, path, backup, writes))
        for t, path, backup, writes in steps:
            if writes:
                journal.append({"op": plan["operation"], "kind": "restoring", "target": t["name"]})
                apply_entries(journal, plan, t, path, backup, writes, "restoring", guard=eligible)
            journal.append({"op": plan["operation"], "kind": "restored", "target": t["name"]})
        try:
            sfd = open_state_root(plan)
            try:
                present = entry_at(sfd, bootstrap_name()) is not None
            finally:
                os.close(sfd)
        except Refused:
            present = None
        journal.append({"op": plan["operation"], "kind": "bootstrap-retained", "present": present,
                        "digest": mine["digest"]})
        journal.append({"op": plan["operation"], "kind": "rolled-back", "boot": ControllerProbes(ops, store).boot()})
        return {"state": "rolled-back", "operation": plan["operation"], "release": plan["release"],
                "bootstrap_record": "left in place",
                "maintenance": "held: a process started after the binding may hold new-release code, so the record "
                               "stays until the boot has changed since this restore; then release"}


def rollback_release_problems(journal, plan, ops, store, fence, spec_path):
    """What the release after a completed pre-write rollback verifies (amendment 4's APPROVE, K2): the exact
    restored targets, the retained record as this operation's own, a known boot change since the restore, and
    still no first write, the refusal sink included. A refused write meanwhile is kept for the owner (G-9)."""
    problems = []
    _, problem = held_problem(store, fence, plan, ("partial", "full"))
    if problem:
        problems.append(problem)
    bridge = ops.bridge_state() or {}
    if bridge.get("running") is None:
        problems.append(f"the bridge's state is unknown ({bridge.get('unknown', 'no evidence')})")
    elif bridge["running"] and bridge.get("release") == plan["release"]:
        problems.append("a bridge of the rolled-back release is running")
    mine = operation_state(journal.records, plan["operation"])
    layout = staged_layout(plan)
    paths = target_paths(stage_release.load_spec(spec_path)[0]["targets"], skills_root(spec_path))
    for name, (t, path) in paths.items():
        backup = mine["backups"].get(name)
        if backup is None:
            continue
        try:
            current, _ = observe_target(t, path, layout, backup.get("container"))
            same = summary_of(current) == summary_of(load_backup(journal.root_fd, backup))
        except Refused as e:
            problems.append(f"{name}: {e}")
            continue
        if not same:
            problems.append(f"{name} is not exactly as restored")
    restored = next((r.get("boot") for r in reversed(mine["records"]) if r["kind"] == "rolled-back"), None)
    now_boot = ControllerProbes(ops, store).boot()
    if store.known(restored) is None:
        problems.append("the boot at the restore is unknown")
    elif store.known(now_boot) is None:
        problems.append("the boot can't be read: unknown")
    elif restored == now_boot:
        problems.append("the boot has not changed since the restore: a process started after the binding may "
                        "still hold new-release code (the owner reboots first)")
    evidence = first_write_evidence(plan["state_root"]["path"], plan["state_root"])
    written = {k: v for k, v in evidence.items() if v != "absent"}
    if written:
        problems.append("a first write since the restore (" + "; ".join(f"{k}: {v}" for k, v in sorted(written.items()))
                        + "): it is kept, never deleted, for the owner's decision (G-9)")
    return problems


def release_problems(report):
    """What a post-check report says blocks a release after an activation."""
    problems = [f"{name}: {value}" for name, value in report["checks"] if value not in ("bound", "matches")]
    if report["bridge"] != "expected":
        problems.append(f"the bridge: {report['bridge']}")
    elif not report["authority_serving"]:
        problems.append("no live answer from the bridge's authority")
    if report["maintenance"]["problem"]:
        problems.append(report["maintenance"]["problem"])
    if not report["ok"] and not problems:
        problems.append("the post-check does not pass")
    return problems


def release(plan_path, ops, spec_path=None, crash=None):
    """End maintenance: unlink the record, the moment every writer is admitted
    again (writer admission 4.7). After a completed activation it is refused
    while any post-check fails; after a completed pre-write rollback it
    verifies what K2 names instead. A crash between the unlink and the journal
    resumes to a completed release; a record that is not provably this
    operation's own is never released."""
    plan = load_plan(plan_path)
    with Journal(plan["root"], crash) as journal:
        mine = operation_state(journal.records, plan["operation"])
        if not mine["admitted"]:
            raise Refused("this operation never began; there is nothing to release")
        if mine["released"]:
            return {"state": "already released", "operation": plan["operation"]}
        store, owner, fence = maintenance(journal, plan, ops)
        if fence.released() and not store.read_maintenance(owner.paths).present:  # journaled, then a crash
            journal.append({"op": plan["operation"], "kind": "released"})
            return {"state": "released", "operation": plan["operation"], "release": plan["release"],
                    "refused_files": store.sink_count(owner.paths)}
        pending = fence.pending()
        if pending is not None and pending.get("step") != "release":
            raise Refused("release refused for now: a maintenance step is pending in the journal; activate settles "
                          "it first; nothing was changed")
        if not (mine["rolled_back"] or mine["selected"]):
            raise Refused("release refused: the activation is not complete; finish it, or roll it back first; "
                          "nothing was changed")

        def eligible():
            """The post-check after an activation, or K2's checks after a rollback; refused with the record kept."""
            problems = rollback_release_problems(journal, plan, ops, store, fence, spec_path) if mine["rolled_back"] \
                else release_problems(postcheck(plan_path, ops, spec_path))
            if problems:
                raise Refused("release refused: " + "; ".join(problems) + ". The record stays and keeps every "
                              "writer refused; nothing more was changed")
        # A pending release whose unlink already landed only records its completion; one whose record is still
        # there repeats every check first (PR #22 review 1, finding 2). And the checks run once more after the
        # durable intent, immediately before the unlink (PR #22 review 2, finding 2).
        if pending is None or store.read_maintenance(owner.paths).present:
            eligible()
        owner.fs.before_release = eligible
        with owner_step(store, "releasing maintenance"):
            owner.release()
        journal.append({"op": plan["operation"], "kind": "released"})
        return {"state": "released", "operation": plan["operation"], "release": plan["release"],
                "refused_files": store.sink_count(owner.paths),
                "next": "writers are admitted again; the bridge resumes from its checkpoints and imports the "
                        "refusal sink"}


def status(root, plan_path=None, ops=None):
    """Each operation's state from the journal, read-only. With the plan, also
    its maintenance record and Owner's read-only reconcile (writer admission
    4.8): the record, whether it is exactly the journaled write, whether its
    acting process is gone, the refusal sink's count, any pending step and
    temporary files, and the next permitted step."""
    records = read_journal(root)
    out = []
    for op in dict.fromkeys(r["op"] for r in records if r.get("op")):
        s = operation_state(records, op)
        state = ("released" if s["released"] else
                 "rolled back (maintenance held until its release)" if s["rolled_back"] else
                 "full (complete only once postcheck passes and it is released)" if s["full"] else
                 "selected (maintenance partial: the owner's reboot, then activate again)" if s["selected"] else
                 f"unfinished (last: {s['records'][-1]['kind']})")
        out.append({"operation": op, "state": state})
    if plan_path is not None:
        plan = load_plan(plan_path)
        _, owner, _ = maintenance(None, plan, ops or LiveOps(), records=records)
        for entry in out:
            if entry["operation"] == plan["operation"]:
                entry["maintenance"] = owner.reconcile()
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
    lines += [f"  safe checkpoint (informational: maintenance refuses new-release writers, and retirement and "
              f"inhibition cover legacy ones; writer admission 9.2): {c['name']} ({c['kind']}), owner {c['owner']}"
              for c in plan["checkpoints"]]
    lines += [f"  preserved state: snapshot {plan['state_snapshot']['digest'][:12]}; no new-format write yet "
              f"({', '.join(sorted(plan['state_snapshot']['first_writes']))} absent)",
              "  migration point: the new bridge's first start under full (its bootstrap commit is the first "
              "outbox-db/1 write)",
              f"  designation list: {', '.join(plan['designations']) or 'explicitly empty'}",
              f"  bootstrap record digest D: sha256:{d} (the chat-bridge LaunchAgent's {BOOTSTRAP_OPTION})",
              "  writer admission (docs/writer_admission.md 9.1): activate acquires maintenance (draining, the "
              "drain, partial); from then until the release every new-release writer's post, acknowledgement and "
              "notice is refused into the refusal sink (exit 5), and kept",
              "  before activate: the owner stops the legacy bridge service",
              "  after activate reports the targets bound: the owner reboots the machine (retirement, gate G-1); "
              "launchd starts the bound bridge, which waits in its hold",
              "  inhibition evidence (gate G-2): the owner-authorized inventory of every unmanaged launcher and "
              "its coverage (a managed target, disabled for the window, or shown never to write), in the evidence "
              "file; full is refused while it is missing or incomplete",
              "  then activate again: retirement, inhibition, targets and the bootstrap verified, then full; the "
              "bound bridge's start commits the bootstrap and serves read-only status",
              "  postchecks: activate_release postcheck (record and D recomputed from this plan, every target "
              "bound, the LaunchAgents' effective executable, the maintenance record full and this operation's, "
              "the expected new bridge serving)",
              "  release: activate_release release, refused while any post-check fails; the unlink admits every "
              "writer again; the bridge catches up from its checkpoints and imports the refusal sink",
              "  watch during the window: the refusal sink's count (activate_release status --plan, pchat status)",
              "  the window against the chat server's history retention (gate G-6): the window is shorter, and no "
              "channel is created during it",
              "  permitted recovery: rollback restores the recorded targets only before the first new-format "
              "write (a refused write in the sink is one), leaving the bootstrap record in place and keeping the "
              "maintenance record until a release after a boot change since the restore; after a first write, a "
              "reviewed repair or a roll-forward keeping the newer state",
              "  skills repository: the expected change to the chat skill folder and chat/scripts is reported "
              "by activate; the owner commits it (D-27)"]
    lines += seal_plan_lines(plan)
    return "\n".join(lines)


def seal_plan_lines(plan):
    """The guarded seal in the operator plan (amendment 3, section 6): the
    exact staged paths and, once staged, their baselines as observed now
    (read-only). A live seal needs the owner's confirmation at that time."""
    sites = sorted((Path(plan["staged_release"]) / "env").glob("lib/python*/site-packages"))
    base = sites[0] if len(sites) == 1 else Path(plan["staged_release"]) / "env/lib/python<X.Y>/site-packages"
    lines = ["  guarded seal (a permission change: the owner confirms it at the time, against these paths and an "
             "independently verified baseline): 0755 -> 0555, mode bits only; rollback keeps it"]
    for rel in SEAL_DIRS:
        path = base / rel
        try:
            fd = open_dir(path, what=f"the staged {rel}")
            try:
                m = dir_metadata(fd, rel)
                seen = (f"mode {m['mode']:#o}, owner uid {m['uid']}, device {m['dev']} inode {m['ino']}, "
                        f"ACL {m['access']['acl']}, flags {m['access']['flags']}")
            finally:
                os.close(fd)
        except Refused as e:
            seen = f"not verifiable now ({e})"
        lines.append(f"    {shown(path)}: {seen}")
    return lines


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
    for name in ("stage", "activate", "postcheck", "release", "rollback", "operator-plan"):
        c = sub.add_parser(name)
        c.add_argument("--plan", required=True, type=Path)
        c.add_argument("--evidence", type=Path)
    s = sub.add_parser("status")
    s.add_argument("--root", required=True, type=Path)
    s.add_argument("--plan", type=Path)
    s.add_argument("--evidence", type=Path)
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
            result = activate(a.plan, LiveOps(a.evidence))
            print(json.dumps(result, indent=2, sort_keys=True))
            return 1 if (result.get("maintenance") or {}).get("refused") else 0
        elif a.cmd == "postcheck":
            report = postcheck(a.plan, LiveOps(a.evidence))
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        elif a.cmd == "release":
            print(json.dumps(release(a.plan, LiveOps(a.evidence)), indent=2, sort_keys=True))
        elif a.cmd == "rollback":
            print(json.dumps(rollback(a.plan, LiveOps(a.evidence)), indent=2, sort_keys=True))
        elif a.cmd == "operator-plan":
            print(render_operator_plan(load_plan(a.plan)))
        else:
            for op in status(a.root, a.plan, LiveOps(a.evidence)):
                print(f"{op['operation']}: {op['state']}")
                if "maintenance" in op:
                    print(json.dumps(op["maintenance"], indent=2, sort_keys=True))
        return 0
    except Refused as e:
        print(f"activate_release: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
