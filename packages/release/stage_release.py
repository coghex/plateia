#!/usr/bin/env python3
"""Stage a built release without changing the live chat tools (PLT-12, D-26).

  python3 packages/release/stage_release.py plan --release DIR [--dest ROOT] [--json] [--out PLAN]
  python3 packages/release/stage_release.py preflight --release DIR [--python PY] [--json]
  python3 packages/release/stage_release.py stage --release DIR --dest ROOT [--python PY] [--plan PLAN]
  python3 packages/release/stage_release.py status --dest ROOT

`plan` lists every managed target (staging.json) with its current type, link
destination or content hash, the evidence of who owns it and its proposed
replacement, and every path-bound importer of the chat/scripts location under
the declared source roots. Expected types, links and labels come from
staging.json and expected file contents from the capture provenance, never
from what is observed. A target blocks when its ownership is unknown, its
content is unexpected, its link is retargeted or it is missing. An importer
outside the known list is named. Plan reads no configuration, credentials,
messages or chat state, and prints none.

`preflight` refuses a release, naming the reason, when its manifest doesn't
verify (a missing or tampered artifact, an unpinned input), when the
interpreter doesn't meet its Python or platform requirement, or when the
live wire and state versions don't match what it reads and writes. It reads
the existing state only for embedded version fields, and the chat config
only to see that it parses; values are never printed or recorded. Until a
manifest declares an API version, the API check is "not applicable".

`stage` runs both, then puts the release's artifacts and a private Python
environment (the chosen interpreter's venv and its bundled pip, offline) in
ROOT/releases/<release>/, under a locked, durable journal at ROOT/journal.jsonl.
Every step's intent is journaled before it runs and its outcome after. A
repeated or interrupted run reconciles the same operation from the journal and
the disk, and verifies the artifacts and environment before reporting it
complete. Staging selects nothing; it never replaces, edits or deletes a
managed target, writes settings, provisions identities or touches a service.
ROOT must be outside this checkout and every live location (build_release's
guard). `status` reads the journal and reports each operation.

Exit status: 0 success, 1 refused or blocked, 2 bad usage. Standard library
only; no network access.
"""
from __future__ import annotations

import argparse
import ast
import base64
import collections
import contextlib
import csv
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
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(CHECKOUT / "packages" / "chat"))
import build_release  # noqa: E402
import drift_check  # noqa: E402
from build_release import MANIFEST, Refused  # noqa: E402

SPEC_PATH = HERE / "staging.json"
PLAN_SCHEMA = "plateia-stage-plan/1"
JOURNAL_SCHEMA = "plateia-stage-journal/1"
STAGED_SCHEMA = "plateia-staged-release/1"
JOURNAL, LOCK, RELEASES, STAGED, INVENTORY = ("journal.jsonl", "journal.lock", "releases", "staged.json",
                                              "env-inventory.json")
STEPS = ("create-release-dir", "copy-artifacts", "create-environment", "install-package",
         "verify-environment", "complete")
IMPORTER_LIMIT = 64 << 20  # a script larger than this is named as unsearched, and the inventory incomplete
IMPORTER_WALK_LIMIT = 200_000  # entries searched for importers before the inventory is called incomplete
SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".zsh")  # with the executable bit: the files searched

# A path-bound importer changes the import path (assigning, extending or
# inserting into sys.path, site.addsitedir, or PYTHONPATH) and names
# the chat/scripts location, as "chat" / "scripts", os.path.join(..., "chat",
# "scripts") or a "chat/scripts" string.
SYS_PATH = re.compile(r"sys\.path\s*(?:\.\s*(?:insert|append|extend|__setitem__|__iadd__)\b|\[|\+=|=(?!=))"
                      r"|\bsite\.addsitedir\b|\bPYTHONPATH\b")
CHAT_SCRIPTS = re.compile(r"""["']chat["']\s*[/,]\s*["']scripts["']|["'][^"'\n]*\bchat/scripts\b""")

PROBE = ("import json, platform, sys; print(json.dumps({'implementation': platform.python_implementation(), "
         "'version': platform.python_version(), 'system': platform.system(), 'prefix': sys.prefix}))")
# The import check runs the operation's chosen interpreter (checked at
# preflight, outside the environment), never the environment's own: with -I
# -S, so no site, .pth or sitecustomize, and / as its working directory. The
# package's sources and dist-info metadata arrive on stdin, already read
# through pinned descriptors and checked against the verified wheel and the
# journaled inventory, and a minimal in-memory finder imports plateia_chat
# from those bytes. Only that interpreter and those bytes run, so a swap of
# the environment can't put other code in the way, and an import-time error
# under the chosen interpreter still surfaces. What it proves live is that
# the verified package imports under the chosen interpreter; the
# environment's own startup (bin/python, pyvenv.cfg, site processing) is
# never run, only checked statically (environment_facts).
IMPORT_PROBE = r"""
import base64, importlib.abc, importlib.machinery, importlib.util, json, pathlib, platform, re, sys
from importlib import metadata
given = json.load(sys.stdin)
site, sources = given["site"], {k: base64.b64decode(v) for k, v in given["sources"].items()}
info = {k: base64.b64decode(v).decode("utf-8") for k, v in given["metadata"].items()}
shadowed = importlib.machinery.PathFinder.find_spec("plateia_chat") is not None


class Source(importlib.abc.Loader):
    def __init__(self, rel):
        self.rel = rel

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        exec(compile(sources[self.rel], module.__spec__.origin, "exec", dont_inherit=True), module.__dict__)


class Located(pathlib.PurePosixPath):
    def exists(self):  # files are listed from the verified RECORD; the disk is never consulted
        return True


class Dist(metadata.Distribution):
    def read_text(self, filename):
        return info.get(filename)

    def locate_file(self, path):
        return Located(site) / str(path)


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] != "plateia_chat":
            return None
        base = name.replace(".", "/")
        for rel, package in ((base + "/__init__.py", True), (base + ".py", False)):
            if rel in sources:
                spec = importlib.util.spec_from_loader(name, Source(rel), origin=site + "/" + rel,
                                                       is_package=package)
                spec.has_location = True
                return spec
        return None

    def find_distributions(self, context=metadata.DistributionFinder.Context()):
        if context.name is None or re.sub(r"[-_.]+", "-", context.name).lower() == "plateia-chat":
            yield Dist()


sys.meta_path.insert(0, Finder())
found = {"implementation": platform.python_implementation(), "version": platform.python_version(),
         "system": platform.system(), "path": list(sys.path), "shadowed": shadowed}
try:
    import plateia_chat
    dist = metadata.distribution("plateia-chat")
    found.update(module=plateia_chat.__file__, dist_version=dist.version,
                 record=[[str(f), f"{f.hash.mode}={f.hash.value}" if f.hash else None] for f in dist.files or []])
except Exception as e:
    found["error"] = f"{type(e).__name__}: {e}"[:300]
print(json.dumps(found))
"""

CALLS = []  # every external command this process ran: the process boundary tests inspect


class Crash(BaseException):
    """Raised by a test's crash hook to stop a run where it stands."""


def run(argv, **kw):
    CALLS.append([str(a) for a in argv])
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=600, **kw)


def child_env():
    """The environment for the interpreter, venv and pip: no PYTHONPATH, no user
    site, no pip index, cache or configuration, so nothing leaks in and pip
    writes nothing to the home directory."""
    env = {"PATH": os.defpath, "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "PIP_NO_INDEX": "1", "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
           "PIP_CONFIG_FILE": os.devnull}
    for key in ("HOME", "TMPDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def shown(path):
    """A path for output: under the home directory, written as ~/..."""
    path, home = str(path), str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def guarded_bytes(path, inside=None):
    """The bytes of a managed or captured file, read only when it is a
    regular file with a single link and, with `inside` as (root, rel), when
    it is reached from root with no alias on the way. A hard link or an
    alias may be private data under another name, and a path can't show
    which, so such a file is never opened: OSError says why. The file opened
    must still be the one checked, with one link, before anything is read."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EINVAL, "not a regular file; not read")
    if st.st_nlink > 1:
        raise OSError(errno.EMLINK, f"a file with {st.st_nlink} hard links, which may be private data under another "
                                    "name; not read")
    if inside is not None and os.path.realpath(path) != os.path.join(os.path.realpath(inside[0]), inside[1]):
        raise OSError(errno.ELOOP, "reached through an alias, which may lead to private data; not read")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        now = os.fstat(stream.fileno())
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino) or now.st_nlink > 1:
            raise OSError(errno.EAGAIN, "changed or gained a hard link while it was checked; not read")
        return stream.read()


def sha256_file(path, inside=None):
    return hashlib.sha256(guarded_bytes(path, inside)).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_spec(path=None):
    spec = json.loads(Path(path or SPEC_PATH).read_text(encoding="utf-8"))
    skills = Path(os.path.expanduser(spec["skills_root"]))
    provenance = Path(spec["provenance"])
    if not provenance.is_absolute():
        provenance = CHECKOUT / provenance
    return spec, skills, drift_check.load_provenance(provenance)


def expand(text, skills):
    return Path(os.path.expanduser(text.replace("{skills}", str(skills))))


def link_destination(path):
    return Path(os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path))))


# --- plan --------------------------------------------------------------------

def content_problem(rel, skills, provenance):
    """None when the skills-tree file `rel` holds a content the provenance
    records (its baseline, or a carried commit's), else the reason."""
    entry = next((e for e in provenance["captured"] if e["baseline_path"] == rel), None)
    if entry is None:
        return f"the provenance records no {rel}"
    path = skills / rel
    if not path.is_file() or path.is_symlink():
        return f"missing: {shown(path)} is not a file"
    try:
        found = sha256_file(path, (skills, rel))
    except OSError as e:
        return f"unknown ownership: {shown(path)} can't be read ({e.strerror or e})"
    if found not in (entry["baseline"]["sha256"], entry.get("effective_sha256")):
        return f"changed content: {shown(path)} matches neither the baseline nor a carried commit"
    return None


def observe_symlink(t, path, skills, provenance):
    expected = expand(t["target"], skills)
    if not path.is_symlink():
        return {"type": "file" if path.is_file() else "directory" if path.is_dir() else "other"}, \
            f"unknown ownership: a {'regular file' if path.is_file() else 'directory' if path.is_dir() else 'non-link'}, not the expected symlink"
    destination = link_destination(path)
    observed = {"type": "symlink", "target": shown(destination)}
    if destination != expected:
        return observed, f"retargeted link: it points to {shown(destination)}, not {shown(expected)}"
    if t.get("content"):
        problem = content_problem(t["content"], skills, provenance)
        if problem:
            return observed, problem
        try:
            observed["sha256"] = sha256_file(expected, (skills, t["content"]))
        except OSError as e:
            return observed, f"unknown ownership: {shown(expected)} can't be read ({e.strerror or e})"
    elif not expected.is_dir() or expected.is_symlink():
        return observed, f"missing: the link destination {shown(expected)} is not a directory"
    return observed, None


def observe_launchagent(t, path, skills, provenance):
    if path.is_symlink() or not path.is_file():
        return {"type": "symlink" if path.is_symlink() else "other"}, \
            "unknown ownership: not a regular property-list file"
    try:
        data = guarded_bytes(path)
    except OSError as e:
        return {"type": "file"}, f"unknown ownership: {shown(path)} can't be read ({e.strerror or e})"
    observed = {"type": "file", "sha256": hashlib.sha256(data).hexdigest()}
    try:
        plist = plistlib.loads(data)
    except (plistlib.InvalidFileException, ValueError):
        return observed, "unknown ownership: not a readable property list"
    # Only the label and program arguments are looked at: never the
    # environment, log paths or anything else the file holds.
    if not isinstance(plist, dict) or plist.get("Label") != t["label"]:
        return observed, f"unknown ownership: its Label is not {t['label']}"
    observed["label"] = t["label"]
    program = expand(t["program"], skills)
    invocation = launch_invocation(plist, program)
    if invocation is None:
        return observed, (f"retargeted program: it doesn't run {shown(program)} (Program and ProgramArguments "
                          "must be the script itself, or a Python interpreter and the script, and nothing else)")
    observed["program"] = shown(program)
    observed["invocation"] = invocation
    problem = content_problem(t["content"], skills, provenance)
    return observed, problem


PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")


def launch_invocation(plist, program):
    """How a LaunchAgent runs `program`: "direct" when launchd executes the
    script itself, "interpreter" when a Python interpreter runs it as its
    only argument; None for anything else. launchd executes Program when it
    is set, else ProgramArguments[0], and passes ProgramArguments as argv."""
    arguments = plist.get("ProgramArguments")
    executable = plist.get("Program")
    if not (isinstance(arguments, list) and arguments and all(isinstance(a, str) for a in arguments)):
        return None
    if executable is None:
        executable = arguments[0]
    if not isinstance(executable, str):
        return None
    same = lambda a: Path(os.path.normpath(a)) == program  # noqa: E731
    if same(executable) and len(arguments) == 1:
        return "direct"
    if PYTHON.fullmatch(os.path.basename(executable)) and len(arguments) == 2 and same(arguments[1]) \
            and PYTHON.fullmatch(os.path.basename(arguments[0])):
        return "interpreter"
    return None


def folder_inventory(skills, provenance, prefix=""):
    """The drift check's view of the chat folder, as (observed, problems):
    every captured file's hash, the excluded entries counted, and every
    missing, changed or unknown entry as a problem."""
    # Every captured file the drift check hashes is read through
    # guarded_bytes, so a hard link or alias into private data is never opened.
    result = drift_check.check(skills, provenance, lambda path, rel: sha256_file(path, (skills, rel)))
    problems = []
    for e in result["errors"]:
        if e["path"].startswith(prefix):
            problems.append(f"unknown ownership: {e['path']} can't be read ({e['error']})")
    for d in result["drift"]:
        if not d["path"].startswith(prefix):
            continue
        if d["kind"] == "changed" and d["detail"].startswith("matches carried commit"):
            continue
        reason = {"missing": "missing", "added": "unknown ownership: an entry the provenance doesn't list"}.get(
            d["kind"], f"changed content ({d['kind']})")
        problems.append(f"{reason}: {d['path']}")
    files = {}
    for entry in provenance["captured"]:
        rel = entry["baseline_path"]
        if rel.startswith(prefix) and (skills / rel).is_file() and not (skills / rel).is_symlink():
            try:
                files[rel] = sha256_file(skills / rel, (skills, rel))
            except OSError:
                continue  # the drift check already named it as unreadable
    return {"type": "directory", "files": files, "digest": digest(files)}, problems


def observe(t, skills, provenance):
    """One managed target as the plan records it."""
    path = expand(t["path"], skills)
    record = {"name": t["name"], "kind": t["kind"], "path": shown(path), "proposed": t["proposed"]}
    expected = {"symlink": lambda: {"type": "symlink", "target": shown(expand(t["target"], skills))},
                "launchagent": lambda: {"type": "file", "label": t["label"],
                                        "program": shown(expand(t["program"], skills))},
                "skill-folder": lambda: {"type": "directory", "contents": "the provenance's captured files"},
                "import-location": lambda: {"type": "directory",
                                            "contents": "the provenance's captured chat/scripts files"}}[t["kind"]]()
    if t.get("content"):
        expected["content"] = f"{t['content']} as the provenance records it"
    record["expected"] = expected
    problems = []
    if not os.path.lexists(path):
        observed, problems = {"type": "missing"}, [f"missing: {shown(path)}"]
    elif t["kind"] == "symlink":
        observed, problem = observe_symlink(t, path, skills, provenance)
        problems = [problem] if problem else []
    elif t["kind"] == "launchagent":
        observed, problem = observe_launchagent(t, path, skills, provenance)
        problems = [problem] if problem else []
    elif path.is_symlink() or not path.is_dir():
        observed, problems = {"type": "not a directory"}, ["unknown ownership: not a real directory"]
    else:
        prefix = "chat/" if t["kind"] == "skill-folder" else "chat/scripts/"
        observed, problems = folder_inventory(skills, provenance, prefix)
    record["observed"] = observed
    record["ownership"] = ownership(t, skills) if not problems else "not established: " + problems[0]
    record["status"] = "blocked" if problems else "ok"
    record["problems"] = problems
    record["digest"] = digest(observed)
    return record


def ownership(t, skills):
    if t["kind"] == "symlink" and t.get("content"):
        return (f"a symlink to the skills tree's {t['content']}, whose content matches the capture provenance")
    if t["kind"] == "symlink":
        return f"a symlink to {shown(expand(t['target'], skills))}"
    if t["kind"] == "launchagent":
        return (f"Label {t['label']}; its ProgramArguments name the skills tree's {t['content']}, whose content "
                "matches the capture provenance")
    if t["kind"] == "skill-folder":
        return "every captured file matches the provenance (baseline or carried commit); no unknown entries"
    return "a directory in the chat skill folder; its captured modules match the provenance"


def private_bases(skills):
    """Where private data lives, which importer discovery never opens or
    walks into, even through an alias: chat state and config, other
    settings and state, and everything under ~/.codex and ~/.claude except
    their skills folders (sessions, history, settings).

    A skills folder is exempt only where it really is: a declared root
    that reaches its own location with no alias, or one aliased to such a
    root. A root that resolves to, contains or lands elsewhere inside
    private data (~/.claude/skills linked to ~/.claude, say) exempts
    nothing; it is returned among the dropped roots, with the reason, and
    never searched."""
    home = Path.home()
    always = [chat_state(), chat_config().parent]
    broad = [home / ".config", home / ".local/state", home / ".local/share", home / "Library", home / ".ssh",
             home / ".gnupg", home / ".aws", home / ".netrc", home / ".codex", home / ".claude"]
    real = lambda paths: [os.path.realpath(p) for p in paths]  # noqa: E731
    always, broad = real(always), real(broad)
    lexical_home, real_home = os.path.normpath(os.path.abspath(home)), os.path.realpath(home)

    def own_place(root):
        """Its location, when the root reaches it with no alias on the way from the home folder."""
        lexical = os.path.normpath(os.path.abspath(root))
        if not lexical.startswith(lexical_home + os.sep):
            return None
        place = os.path.join(real_home, os.path.relpath(lexical, lexical_home))
        return place if os.path.realpath(root) == place else None

    roots = [skills, home / ".claude/skills"]
    canonical = {own_place(r) for r in roots} - {None}
    allowed, dropped = [], {}
    for root in roots:
        target = os.path.realpath(root)
        inside = [b for b in always + broad if target.startswith(b + os.sep)]
        if any(target == b or b.startswith(target + os.sep) for b in always + broad):
            dropped[str(root)] = f"resolves to {shown(target)}, which is or holds private data"
        elif inside and (target not in canonical or any(target.startswith(b + os.sep) for b in always)):
            dropped[str(root)] = f"is an alias resolving to {shown(target)}, inside private data"
        else:
            allowed.append(target)
    return always, allowed, broad, dropped


def private_target(path, bases):
    always, allowed, broad, _ = bases
    real = os.path.realpath(path)
    under = lambda base: real == base or real.startswith(base + os.sep)  # noqa: E731
    if any(under(b) for b in always):
        return True
    if any(under(b) for b in allowed):
        return False
    return any(under(b) for b in broad)


IMPORTER_WINDOW = 16  # physical lines searched together, so an expression split over lines is matched


def without_comment(line):
    """A line with a trailing # comment dropped, outside quotes, so an
    expression with a comment between "chat" and "scripts" still matches.
    Searched as well as the line itself, never instead of it."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == "\\":
                continue
            if ch == quote and line[i - 1] != "\\":
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return line[:i] + "\n"
    return line


def imports_chat_scripts(path, checked=None):
    """Whether a script both changes the import path and names chat/scripts.
    It is read line by line, so a large script is searched whole, and each
    search covers the last IMPORTER_WINDOW lines joined, so an expression
    spread over several lines (a parenthesized sys.path.insert(...) with
    / "chat" and / "scripts" on lines of their own) still matches. None for
    a file that turns out to be binary. With `checked` (the stat discovery
    checked), nothing is read unless the opened file is that same file and
    still has a single link."""
    path_change = names_location = False
    window = collections.deque(maxlen=IMPORTER_WINDOW)
    bare = collections.deque(maxlen=IMPORTER_WINDOW)  # the same lines with their comments dropped
    with open(path, "rb") as stream:
        if checked is not None:
            now = os.fstat(stream.fileno())
            if (now.st_dev, now.st_ino) != (checked.st_dev, checked.st_ino) or now.st_nlink > 1:
                raise OSError(f"{shown(path)} changed or gained a hard link after it was checked; not read")
        head = stream.read(8192)
        if b"\0" in head:
            return None
        stream.seek(0)
        for raw in stream:
            window.append(raw.decode("utf-8", errors="ignore"))
            bare.append(without_comment(window[-1]))
            text, stripped = "".join(window), "".join(bare)
            path_change = path_change or bool(SYS_PATH.search(text) or SYS_PATH.search(stripped))
            names_location = names_location or bool(CHAT_SCRIPTS.search(text) or CHAT_SCRIPTS.search(stripped))
            if path_change and names_location:
                return True
    return False


def find_importers(spec, skills):
    """Path-bound importers of chat/scripts under the declared source roots.

    Symlinked folders and scripts inside a root are followed, each resolved
    folder walked once (so a cycle ends) and each resolved file counted
    once, under the first path that reaches it. Only scripts are opened (a
    Python or shell suffix, or the executable bit), never data files, and
    nothing that resolves into private data (private_bases): such an alias
    is named as excluded, as is a declared root aliased into private data,
    which is not searched at all, and a file with more than one hard link,
    which may be private data under another name (a path can't show it).
    The managed chat folder is skipped, however it is reached. A root,
    folder or script that can't be read, a script over the size cap, an
    excluded alias, or more entries than the walk allows makes the
    inventory incomplete rather than clean."""
    cfg = spec["importers"]
    managed = os.path.realpath(skills / "chat")
    private = private_bases(skills)
    known = {os.path.realpath(expand(k["path"], skills)): (k["name"], expand(k["path"], skills))
             for k in cfg["known"]}
    found, roots, walked, seen = {}, [], set(), 0
    for text in cfg["roots"]:
        root = expand(text, skills)
        entry = {"path": shown(root), "status": "complete", "unreadable": [], "excluded": []}
        roots.append(entry)
        if not os.path.lexists(root):
            entry["status"] = "absent"
            continue
        if str(root) in private[3]:  # a declared root aliased into private data: nothing exempt, nothing searched
            entry["excluded"].append(f"{shown(root)} (a declared root that {private[3][str(root)]}; not "
                                     "searched, and nothing under it exempt)")
            entry["status"] = "incomplete"
            continue
        pending = [root]
        while pending:
            folder = pending.pop()
            real = os.path.realpath(folder)
            if real == managed or real in walked:
                continue
            if private_target(folder, private):
                entry["excluded"].append(f"{shown(folder)} (resolves into private data; not searched)")
                continue
            walked.add(real)
            try:
                with os.scandir(folder) as listing_:
                    items = sorted(listing_, key=lambda e: e.name)
            except OSError:
                entry["unreadable"].append(shown(folder))
                continue
            for item in items:
                seen += 1
                if seen > IMPORTER_WALK_LIMIT:
                    entry["unreadable"].append(f"more than {IMPORTER_WALK_LIMIT} entries")
                    pending = []
                    break
                path = Path(item.path)
                try:
                    if item.is_dir():  # follows a link, deliberately
                        if item.name not in cfg["skip_dirs"]:
                            pending.append(path)
                        continue
                    if not item.is_file():
                        continue
                    st = path.stat()
                    if not (path.suffix in SCRIPT_SUFFIXES or st.st_mode & 0o111):
                        continue
                    target = os.path.realpath(path)
                    if target in found or target.startswith(managed + os.sep):
                        continue
                    if private_target(path, private):
                        entry["excluded"].append(f"{shown(path)} (resolves into private data; not opened)")
                        continue
                    if st.st_nlink > 1:
                        entry["excluded"].append(f"{shown(path)} (a file with {st.st_nlink} hard links, which may be "
                                                 "private data under another name; not opened)")
                        continue
                    if st.st_size > IMPORTER_LIMIT:
                        entry["unreadable"].append(f"{shown(path)} (over {IMPORTER_LIMIT >> 20} MiB; not searched)")
                        continue
                    hit = imports_chat_scripts(path, st)
                except OSError:
                    entry["unreadable"].append(shown(path))
                    continue
                if hit:
                    found[target] = path
        if entry["unreadable"] or entry["excluded"]:
            entry["status"] = "incomplete"
    return {"roots": roots,
            "known": [{"name": name, "path": shown(path), "found": real in found}
                      for real, (name, path) in known.items()],
            "unlisted": sorted(shown(p) for real, p in found.items() if real not in known),
            "complete": all(r["status"] != "incomplete" for r in roots)}


def make_plan(release, dest=None, spec_path=None):
    """The plan for staging `release`: read-only."""
    spec, skills, provenance = load_spec(spec_path)
    manifest = build_release.verify(release)
    targets = [observe(t, skills, provenance) for t in spec["targets"]]
    staged = (Path(os.path.abspath(dest)) / RELEASES / manifest["release"]) if dest else None
    plan = {"schema": PLAN_SCHEMA, "release": manifest["release"],
            "manifest_sha256": drift_check.sha256(Path(release) / MANIFEST),
            "staged_release": shown(staged) if staged else None,
            "targets": targets, "importers": find_importers(spec, skills),
            "blocked": [f"{t['name']}: {p}" for t in targets for p in t["problems"]],
            "note": "plan only: nothing is selected or replaced; the proposed replacements are PLT-14's"}
    return plan


def render_plan(plan):
    lines = [f"plan for {plan['release']} (manifest {plan['manifest_sha256'][:12]})"]
    if plan["staged_release"]:
        lines.append(f"  staged release would be {plan['staged_release']}")
    for t in plan["targets"]:
        o = t["observed"]
        facts = [o["type"]]
        if o.get("target"):
            facts.append(f"to {o['target']}")
        if o.get("sha256"):
            facts.append(f"sha256 {o['sha256'][:12]}")
        if o.get("digest"):
            facts.append(f"{len(o['files'])} captured files, digest {o['digest'][:12]}")
        lines.append(f"  [{t['status']}] {t['name']}: {t['path']} ({', '.join(facts)})")
        lines.append(f"      owner: {t['ownership']}")
        lines.append(f"      proposed: {t['proposed']}")
        lines += [f"      blocks: {p}" for p in t["problems"][1:]]
    imp = plan["importers"]
    for r in imp["roots"]:
        lines.append(f"  importer root {r['path']}: {r['status']}"
                     + (f" ({len(r['unreadable'])} unreadable: {', '.join(r['unreadable'][:3])})"
                        if r["unreadable"] else ""))
        lines += [f"      excluded: {x}" for x in r.get("excluded", [])]
    for k in imp["known"]:
        lines.append(f"  known importer {k['name']}: {k['path']} ({'found' if k['found'] else 'not found'})")
    for u in imp["unlisted"]:
        lines.append(f"  unlisted importer: {u} (outside the known list; it would stay on the old code)")
    if not imp["complete"]:
        lines.append("  importer inventory incomplete: some roots couldn't be read completely")
    lines.append(f"blocked: {len(plan['blocked'])} target problem(s)" if plan["blocked"]
                 else "no target blocks staging")
    return "\n".join(lines)


# --- preflight ---------------------------------------------------------------

def satisfies(version, requirement):
    """Whether a dotted version meets a requirement like '>=3.10' or
    '>=3.10, <4'; None when the requirement can't be read."""
    def parts(text):
        return tuple(int(x) for x in text.split("."))
    try:
        have = parts(re.match(r"\d+(?:\.\d+)*", version).group(0))
    except (AttributeError, ValueError):
        return None
    ok = True
    for clause in requirement.split(","):
        m = re.fullmatch(r"\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*", clause)
        if not m:
            return None
        want = parts(m.group(2))
        size = max(len(want), len(have))
        a, b = have + (0,) * (size - len(have)), want + (0,) * (size - len(want))
        if m.group(1) == "==":
            a = have[:len(want)] + (0,) * (size - len(want))
        ok &= {">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b, ">": a > b, "<": a < b}[m.group(1)]
    return ok


def probe_interpreter(python):
    try:
        r = run([python, "-I", "-c", PROBE], env=child_env(), cwd="/")
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused(f"interpreter: {shown(python)} can't be run ({getattr(e, 'strerror', None) or e})") from None
    try:
        found = json.loads(r.stdout.strip().splitlines()[-1])
        assert isinstance(found, dict) and all(isinstance(found.get(k), str)
                                               for k in ("implementation", "version", "system"))
    except (IndexError, ValueError, AssertionError):
        raise Refused(f"interpreter: {shown(python)} didn't report its implementation, version and platform") \
            from None
    return found


def interpreter_result(manifest, found, python):
    requires = manifest["requires_python"]
    impl = (manifest["dependencies"].get("interpreter") or {}).get("implementation")
    systems = (manifest.get("platforms") or {}).get("systems") if isinstance(manifest.get("platforms"), dict) else None
    what = f"{found['implementation']} {found['version']} on {found['system']} ({shown(python)})"
    if not (isinstance(systems, list) and systems and all(isinstance(s, str) for s in systems)):
        return ("refused", "interpreter", "the manifest declares no platform systems")
    met = satisfies(found["version"], requires)
    if met is None:
        return ("refused", "interpreter", f"the manifest's requires_python {requires!r} can't be read")
    if impl and found["implementation"] != impl:
        return ("refused", "interpreter", f"{what} is not the {impl} the manifest requires")
    if not met:
        return ("refused", "interpreter", f"{what} does not meet requires_python {requires}")
    if found["system"] not in systems:
        return ("refused", "interpreter", f"{what} is not on a platform the manifest supports "
                                           f"({', '.join(systems)})")
    return ("pass", "interpreter", f"{what} meets requires_python {requires} on {found['system']}")


def chat_state():
    return Path(os.environ.get("CHAT_STATE") or Path.home() / ".local/state/chat")


def chat_config():
    return Path(os.environ.get("CHAT_CONFIG") or Path.home() / ".config/chat/config.json")


def read_field(path, field):
    """(value, None) from a JSON object file's one field, or (None, reason).
    Nothing else in the file is kept, and the reason never quotes it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"
    except ValueError:
        return None, "not valid JSON"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    if field is None:
        return True, None
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None, f"no {field} field"
    return str(value), None


def observed_versions():
    """{format: (versions found, problem or None)} for the state formats that
    embed a version, read from the existing state."""
    state = chat_state()
    found = {}
    value, problem = read_field(state / "identities.json", "version")
    found["identity-registry"] = ([value] if value else [], f"{shown(state / 'identities.json')}: {problem}"
                                  if problem else None)
    runs, problems = set(), []
    for project in listing(Path.home() / ".local/state/project-manager", problems):
        # Symlinked projects and runs are followed: their snapshots count too.
        for run_dir in listing(project / "runs", problems) if project.is_dir() else ():
            if not run_dir.is_dir():
                continue
            value, problem = read_field(run_dir / "state.json", "schema")
            if problem == "missing":
                continue  # a run still being created has no snapshot yet
            if problem:
                problems.append(f"{shown(run_dir / 'state.json')}: {problem}")
            else:
                runs.add(value)
    found["child-runs"] = (sorted(runs), "; ".join(problems[:3]) or None)
    problems = []
    markers = sorted(p.name[:-len(".enabled")] for p in listing(state / "experiments", problems)
                     if re.fullmatch(r"review-start-receipts-v[^/]*\.enabled", p.name))
    found["receipt-experiment-marker"] = (markers, "; ".join(problems) or None)
    return found


def listing(folder, problems):
    """A directory's entries, sorted. A missing directory has none; one that
    can't be listed is a problem, never an empty inventory."""
    try:
        with os.scandir(folder) as entries:
            return sorted(Path(e.path) for e in entries)
    except FileNotFoundError:
        return []
    except OSError as e:
        problems.append(f"{shown(folder)}: can't be listed ({e.strerror or e.__class__.__name__})")
        return []


def compatibility(manifest, spec):
    """(result, check, detail) for each wire and state format the live tools use."""
    results = []
    live = {k: v for k, v in spec["live_versions"].items() if k != "note"}
    formats = manifest.get("formats")
    if not isinstance(formats, list) or not all(isinstance(f, dict) and isinstance(f.get("name"), str)
                                                for f in formats):
        return [("refused", "formats", "the manifest's format inventory is malformed")]
    declared = {f["name"]: f for f in formats}
    observed = observed_versions()
    for name, version in sorted(live.items()):
        f = declared.get(name)
        if f is None:
            results.append(("refused", name, f"the manifest declares no {name} format; the live tools use {version}"))
            continue
        reads, writes = f.get("read"), f.get("write")
        if not isinstance(reads, list) or not all(isinstance(r, str) for r in reads) \
                or not (writes is None or isinstance(writes, str)) or (not reads and writes is None):
            results.append(("refused", name, "the manifest's read and write versions are malformed"))
            continue
        if reads and version not in reads:
            results.append(("refused", name, f"unsupported version: the live tools use {version}; "
                                             f"the release reads {', '.join(reads)}"))
            continue
        if writes is not None and writes != version:
            results.append(("refused", name, f"unsupported version: the release writes {writes}, "
                                             f"which the live tools ({version}) don't read"))
            continue
        if name in observed:
            values, problem = observed[name]
            if problem:
                results.append(("refused", name, f"state version evidence unreadable: {problem}"))
                continue
            if any(v not in reads for v in values):
                results.append(("refused", name, f"the existing state holds a {name} version the release "
                                                 f"doesn't read (it reads {', '.join(reads)})"))
                continue
            seen = f"existing state: {', '.join(values)}" if values else "none in the existing state"
            results.append(("pass", name, f"{version}; {seen}"))
            continue
        results.append(("pass", name, f"{version} ({f.get('kind', 'format')})"))
    for name in sorted(set(declared) - set(live)):
        results.append(("refused", name, f"the manifest declares {name}, which the live tools don't use: "
                                         "no compatibility evidence"))
    _, problem = read_field(chat_config(), None)
    results.append(("refused", "chat-config file", f"{shown(chat_config())}: {problem}") if problem
                   else ("pass", "chat-config file", f"{shown(chat_config())} parses (values not read)"))
    return results


IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def identity_problem(manifest):
    """Why the manifest's release identity or artifact names can't name
    files under the staging root, or None: each must be one plain path
    component, and the identity must be the package's name and version."""
    identity, pkg = manifest.get("release"), manifest.get("package") or {}
    if not (isinstance(identity, str) and IDENTITY.fullmatch(identity)) \
            or identity != f"{pkg.get('name')}-{pkg.get('version')}":
        return "its release identity is not the package's name and version as one plain path component"
    names = [a.get("name") for a in manifest["artifacts"]]
    if not all(isinstance(n, str) and IDENTITY.fullmatch(n) and n != MANIFEST for n in names) \
            or len(set(names)) != len(names):
        return "an artifact name is not one plain, distinct path component"
    return None


def api_result(manifest):
    if "api_version" not in manifest and "api" not in manifest:
        return ("not applicable", "api", "the manifest declares no API version (D-23: it joins from PLT-10)")
    return ("refused", "api", "the manifest declares an API version, and no consumer compatibility matrix "
                              "exists yet to check it against")


def preflight(release, python=None, spec_path=None):
    """{"results": [...], "refused": [...], "interpreter": probe}: read-only."""
    spec = json.loads(Path(spec_path or SPEC_PATH).read_text(encoding="utf-8"))
    python = str(python or sys.executable)
    results = []
    try:
        manifest = build_release.verify(release)
        problem = identity_problem(manifest)
        if problem:
            raise Refused(f"the manifest can't be staged: {problem}")
        results.append(("pass", "manifest", f"{manifest['release']}: {len(manifest['artifacts'])} artifacts "
                                            "match, source commit and interpreter pinned"))
    except Refused as e:
        return {"results": [("refused", "manifest", str(e))], "refused": [f"manifest: {e}"], "interpreter": None}
    found = None
    try:
        found = probe_interpreter(python)
        results.append(interpreter_result(manifest, found, python))
    except Refused as e:
        results.append(("refused", "interpreter", str(e).split(": ", 1)[-1]))
    results += compatibility(manifest, spec)
    results.append(api_result(manifest))
    return {"results": [list(r) for r in results],
            "refused": [f"{check}: {detail}" for result, check, detail in results if result == "refused"],
            "interpreter": dict(found, path=python) if found else None}


def render_preflight(report):
    lines = [f"  {r:15} {c}: {d}" for r, c, d in report["results"]]
    lines.append(f"refused: {len(report['refused'])} check(s)" if report["refused"] else "preflight passed")
    return "\n".join(["preflight"] + lines)


# --- the safe-write layer ----------------------------------------------------

SAFE_NAME = re.compile(r"[A-Za-z0-9._+-]+")


def protected_bases(live):
    """build_release's protected locations, each as (name, resolved, given),
    with this checkout first."""
    return [("this checkout", Path(os.path.realpath(CHECKOUT)), Path(os.path.abspath(CHECKOUT)))] + \
        [(name, Path(os.path.realpath(root)), Path(os.path.abspath(root))) for name, root in live]


def protected_reason(path, bases):
    """Why `path` may not be written, or None: inside this checkout or under a
    protected location, comparing both the resolved and the given spelling."""
    given, real = Path(os.path.abspath(path)), Path(os.path.realpath(path))
    inside = lambda p, base: p == base or base in p.parents  # noqa: E731
    for name, resolved, spelled in bases:
        if inside(real, resolved) or inside(given, spelled):
            return "inside this checkout" if name == "this checkout" else \
                f"under {name}, where the live chat tools live"
    return None


def write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def pin_directory(path, protected, create=False):
    """(descriptor, resolved path) for the directory at `path`.

    The path is resolved once and checked against the protected locations.
    Then that resolved path is opened from / one name at a time with
    O_DIRECTORY|O_NOFOLLOW, each relative to the one before. A directory
    swapped for a link after the check makes the open fail instead of
    following it. With `create`, a missing name is created relative to its
    pinned parent, so neither the root nor a missing parent can be created
    anywhere but where the guard looked."""
    real = os.path.realpath(path)
    reason = protected_reason(real, protected)
    if reason:
        raise Refused(f"{shown(path)} is {reason}; left unchanged")
    fd = os.open("/", DIR_FLAGS)
    try:
        for name in Path(real).parts[1:]:
            try:
                child = os.open(name, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise Refused(f"{shown(path)} doesn't exist") from None
                os.mkdir(name, 0o755, dir_fd=fd)
                try:
                    child = os.open(name, DIR_FLAGS, dir_fd=fd)
                except OSError:
                    raise Refused(f"{shown(path)} changed while it was being created; stopped") from None
            except OSError:
                raise Refused(f"{shown(path)} changed after it was checked: a directory on the way to it is now a "
                              "link or not a directory; nothing was created through it") from None
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd, real


def open_inside(fd, inner, flags=os.O_RDONLY):
    """Open `inner` (a relative path) below the directory `fd`, one name at
    a time, never following a link."""
    parts = inner.split("/")
    current = os.dup(fd)
    try:
        for name in parts[:-1]:
            child = os.open(name, DIR_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        return os.open(parts[-1], flags | os.O_NOFOLLOW, dir_fd=current)
    finally:
        os.close(current)


def read_inside(fd, inner):
    """The bytes of the regular file `inner` below the directory `fd`, read
    through descriptors only."""
    try:
        out = open_inside(fd, inner)
    except OSError:
        raise Refused(f"{inner} can't be read without following a link") from None
    try:
        if not stat.S_ISREG(os.fstat(out).st_mode):
            raise Refused(f"{inner} is not a regular file")
        chunks = []
        while chunk := os.read(out, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(out)


def inventory_at(fd, prefix=""):
    """Every entry below the directory `fd`, read through descriptors and
    never following a link: a directory, a link's target, or a file's sha256
    and permission bits."""
    out = {}
    with os.scandir(fd) as listing_:
        names = sorted(e.name for e in listing_)
    for name in names:
        rel = prefix + name
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            out[rel] = ["link", os.readlink(name, dir_fd=fd)]
        elif stat.S_ISDIR(st.st_mode):
            out[rel] = ["dir"]
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            try:
                out.update(inventory_at(child, rel + "/"))
            finally:
                os.close(child)
        elif stat.S_ISREG(st.st_mode):
            data = read_inside(fd, name)
            out[rel] = ["file", hashlib.sha256(data).hexdigest(), stat.S_IMODE(st.st_mode)]
        else:
            out[rel] = ["other"]
    return out


class Writer:
    """The one way staging changes the filesystem, `plan --out` included.

    - A path is plain components (letters, digits, `._+-`; never `.` or
      `..`) under a root build_release's guard accepted.
    - The root is held open, and every directory on the way is opened
      relative to the one above it with O_DIRECTORY|O_NOFOLLOW, then checked
      by its inode against the journal's record that staging created it
      (owned by this user, on the root's device). Every create, rename and
      delete happens relative to those pinned descriptors, so swapping a
      directory for a link after it was checked can't redirect a write
      staging makes itself. venv and pip write by path while they run:
      path_intact checks around them, and D-71 bounds what that covers.
    - The final path and its parent resolve under the root, and outside this
      checkout and every protected location.
    - Files are created with O_CREAT|O_EXCL|O_NOFOLLOW under a fresh
      temporary name, journaled first, their inode journaled right after,
      then renamed into place.
    - Nothing is replaced, truncated or deleted unless the journal records
      that this operation created it, and it is still that entry: same
      inode, singly linked, this owner. Anything else is refused."""

    def __init__(self, root, live, create=False):
        """Pin the root (pin_directory), creating it and any missing parent
        only through pinned descriptors when `create`."""
        self.root, self.protected = Path(root), protected_bases(live)
        self.uid = os.getuid()
        self.root_fd, self.real = pin_directory(self.root, self.protected, create)
        st = os.fstat(self.root_fd)
        problem = ("is not a directory this user owns" if st.st_uid != self.uid
                   else "is writable by other users" if st.st_mode & 0o022 else None)
        if problem:
            os.close(self.root_fd)
            raise Refused(f"{shown(self.root)} {problem}; left unchanged")
        self.dev, self.root_inode = st.st_dev, (st.st_dev, st.st_ino)
        self.dirs, self.mine = {}, {}
        self.record = None

    def close(self):
        os.close(self.root_fd)

    def load(self, records, op_id):
        """What the journal says staging created, by inode: directories by
        any operation here, files by this one."""
        for r in records:
            if r.get("kind") == "created":
                inode = tuple(r["inode"])
                if r["type"] == "dir":
                    self.dirs[r["path"]] = inode
                if r.get("op") == op_id:
                    self.mine[r["path"]] = inode

    def resolve(self, rel):
        """Check rel's names and that it resolves under the root and outside
        every protected location; returns its path (for messages and the
        processes that need one)."""
        parts = rel.split("/")
        if any(not SAFE_NAME.fullmatch(p) or p in (".", "..") for p in parts):
            raise Refused(f"{rel!r} is not a plain path under the staging destination")
        path = self.root.joinpath(*parts)
        parent = os.path.realpath(path.parent)
        if os.path.lexists(path.parent) and parent != os.path.normpath(os.path.join(self.real, *parts[:-1])):
            raise Refused(f"{shown(path)} doesn't resolve under the staging destination")
        for candidate in (parent, os.path.join(parent, parts[-1])):
            reason = protected_reason(candidate, self.protected)
            if reason:
                raise Refused(f"{shown(path)} is {reason}; left unchanged")
        return path

    def foreign(self, path, why):
        raise Refused(f"{shown(path)} {why}; staging never writes through, replaces or deletes an entry it "
                      "didn't create, and left it unchanged")

    def open_dir(self, parent_fd, name, rel):
        """A directory staging created, opened relative to its pinned parent
        and checked by inode; the caller closes it."""
        try:
            fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            raise
        except OSError:
            self.foreign(self.root / rel, "is not a directory staging created")
        st = os.fstat(fd)
        if st.st_uid != self.uid or st.st_dev != self.dev or self.dirs.get(rel) != (st.st_dev, st.st_ino):
            os.close(fd)
            self.foreign(self.root / rel, "is a directory staging didn't create")
        return fd

    def parent(self, rel):
        """(pinned descriptor of rel's directory, rel's last name). Raises
        FileNotFoundError when a directory on the way is missing."""
        self.resolve(rel)
        parts = rel.split("/")
        fd = os.dup(self.root_fd)
        try:
            for i, name in enumerate(parts[:-1], 1):
                child = self.open_dir(fd, name, "/".join(parts[:i]))
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd, parts[-1]

    def entry(self, fd, name):
        try:
            return os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def owned(self, rel, st):
        return (self.mine.get(rel) == (st.st_dev, st.st_ino) and st.st_uid == self.uid and st.st_dev == self.dev
                and not stat.S_ISLNK(st.st_mode) and (stat.S_ISDIR(st.st_mode) or st.st_nlink == 1))

    def lstat(self, rel):
        """The entry at rel, or None when it (or a directory on the way) is
        missing; refuses an alias on the way to it."""
        try:
            fd, name = self.parent(rel)
        except FileNotFoundError:
            return None
        try:
            return self.entry(fd, name)
        finally:
            os.close(fd)

    def inspect(self, rel):
        """Refuse an existing entry at rel that this operation (or, for a
        directory, staging) didn't create; walking stops at a missing one."""
        try:
            fd, name = self.parent(rel)  # every directory on the way is checked
        except FileNotFoundError:
            return
        try:
            st = self.entry(fd, name)
            if st is None:
                return
            if stat.S_ISDIR(st.st_mode):
                os.close(self.open_dir(fd, name, rel))
            elif not self.owned(rel, st):
                self.foreign(self.root / rel, "is not a file this operation created")
        finally:
            os.close(fd)

    def note(self, rel, st, kind):
        inode = (st.st_dev, st.st_ino)
        self.record({"kind": "created", "path": rel, "type": kind, "inode": list(inode)})
        self.mine[rel] = inode
        if kind == "dir":
            self.dirs[rel] = inode

    def mkdir(self, rel):
        fd, name = self.parent(rel)
        try:
            if self.entry(fd, name) is not None:
                os.close(self.open_dir(fd, name, rel))
                return self.root / rel
            os.mkdir(name, 0o755, dir_fd=fd)
            made = os.open(name, DIR_FLAGS, dir_fd=fd)
            try:
                self.note(rel, os.fstat(made), "dir")
            finally:
                os.close(made)
            os.fsync(fd)
        finally:
            os.close(fd)
        return self.root / rel

    def write(self, rel, data):
        """Write a file this operation owns: a new one, or a replacement for
        one it created; never through or over anything else."""
        fd, name = self.parent(rel)
        try:
            st = self.entry(fd, name)
            if st is not None and not (stat.S_ISREG(st.st_mode) and self.owned(rel, st)):
                self.foreign(self.root / rel, "exists and is not a file this operation created")
            head = rel.rpartition("/")[0]
            temp = f".{name}.{secrets.token_hex(6)}.tmp"
            temp_rel = (head + "/" if head else "") + temp
            self.resolve(temp_rel)
            self.record({"kind": "creating", "path": temp_rel})  # journaled before it exists
            out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            try:
                self.note(temp_rel, os.fstat(out), "temp")  # its inode: the evidence that lets recovery remove it
                write_all(out, data)
                os.fsync(out)
                made = os.fstat(out)
            finally:
                os.close(out)
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
            self.note(rel, made, "file")
            os.fsync(fd)
        finally:
            os.close(fd)
        return self.root / rel

    def create(self, rel, data):
        """Create a new file exclusively (plan --out): never an existing one."""
        fd, name = self.parent(rel)
        try:
            try:
                out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
            except FileExistsError:
                raise Refused(f"{shown(self.root / rel)} already exists; staging writes only new files, so choose "
                              "a new path") from None
            try:
                write_all(out, data)
                os.fsync(out)
            finally:
                os.close(out)
            os.fsync(fd)
        finally:
            os.close(fd)
        return self.root / rel

    def remove(self, rel):
        """Delete a file the journal records this operation created, by its
        inode, that is still that singly linked file. A journaled intent to
        create a name is not evidence: anything at such a name without a
        creation record is left untouched."""
        fd, name = self.parent(rel)
        try:
            st = self.entry(fd, name)
            if st is None or not (stat.S_ISREG(st.st_mode) and self.owned(rel, st)):
                self.foreign(self.root / rel, "is not a file the journal records this operation created")
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)

    def remove_tree(self, rel):
        """Delete a directory this operation created, with what it put
        there (the environment its own venv run filled), through pinned
        descriptors. Links inside are removed, never followed."""
        fd, name = self.parent(rel)
        try:
            st = self.entry(fd, name)
            if st is None or not (stat.S_ISDIR(st.st_mode) and self.owned(rel, st)):
                self.foreign(self.root / rel, "is not a directory this operation created")
            remove_tree_at(fd, name, (st.st_dev, st.st_ino))
            os.fsync(fd)
        finally:
            os.close(fd)
        self.mine.pop(rel, None)
        self.dirs.pop(rel, None)

    @contextlib.contextmanager
    def pinned(self, rel):
        """A descriptor for rel, a directory staging created, reached through
        pinned descriptors and checked by inode; closed afterwards."""
        fd, name = self.parent(rel)
        try:
            child = self.open_dir(fd, name, rel)
        finally:
            os.close(fd)
        try:
            yield child
        finally:
            os.close(child)

    def read(self, rel):
        """The bytes of a file this operation wrote, read through pinned
        descriptors: still the same singly linked inode the journal records."""
        fd, name = self.parent(rel)
        try:
            try:
                out = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                self.foreign(self.root / rel, "can't be read as a file this operation created")
            try:
                st = os.fstat(out)
                if not (stat.S_ISREG(st.st_mode) and self.owned(rel, st)):
                    self.foreign(self.root / rel, "is not a file this operation created")
                chunks = []
                while chunk := os.read(out, 1 << 20):
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                os.close(out)
        finally:
            os.close(fd)

    def same_dir(self, rel):
        """Refuse unless rel is still the directory this operation created."""
        fd, name = self.parent(rel)
        try:
            st = self.entry(fd, name)
            if st is None or not (stat.S_ISDIR(st.st_mode) and self.owned(rel, st)):
                self.foreign(self.root / rel, "is no longer the directory this operation created")
        finally:
            os.close(fd)

    def path_intact(self, rel, process, after=False):
        """Checked immediately before and after a process that writes into
        rel by its path (venv, pip): that path must still lead, name by name,
        to the directories staging created and pinned, the root and each one
        below it by its inode, none of them a link. The first that differs
        is named. A process writes by path while it runs, so a same-user
        process that swaps one of these directories during it can redirect
        its writes before this check sees the swap (D-71): staging then
        refuses, and the step's outcome is recorded failed, never ok."""
        parts = rel.split("/")
        chain = [(self.root, self.root_inode)] + [(self.root.joinpath(*parts[:i]), self.dirs.get("/".join(parts[:i])))
                                                 for i in range(1, len(parts) + 1)]
        for path, inode in chain:
            try:
                st = os.lstat(path)
            except OSError:
                st = None
            if st is None or not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != inode:
                if after:
                    effect = (f"so {process}'s writes through it may have landed elsewhere (D-71)"
                              if process in ("venv", "pip") else "so what it checked may no longer be what is there")
                    raise Refused(f"{shown(path)} changed while {process} ran: it is no longer the directory "
                                  f"staging created and checked, {effect}; refused, and the step isn't recorded "
                                  "complete")
                raise Refused(f"{shown(path)} is no longer the directory staging created and checked; {process} "
                              "was not run")
        self.same_dir(rel)


def remove_tree_at(parent_fd, name, inode):
    """Remove a directory and everything in it relative to its parent's
    descriptor, never following a link; the directory must still be inode."""
    fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != inode:
            raise Refused("a directory changed while it was being removed; stopped")
        with os.scandir(fd) as entries:
            items = [(e.name, e.is_dir(follow_symlinks=False)) for e in entries]
        for child, is_dir in items:
            if is_dir:
                remove_tree_at(fd, child, (lambda s: (s.st_dev, s.st_ino))(
                    os.stat(child, dir_fd=fd, follow_symlinks=False)))
            else:
                os.unlink(child, dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent_fd)


# --- journal -----------------------------------------------------------------

class Journal:
    """The locked, durable stage journal: one JSON record per line, each
    fsynced before the step it announces runs. Its first record names the
    journal's and the lock's own inodes, so a later run can tell the journal
    staging created from anything else at that name."""

    def __init__(self, writer, crash=None):
        self.writer, self.crash = writer, crash
        self.path, lock_path = writer.resolve(JOURNAL), writer.resolve(LOCK)
        self.root_fd = writer.root_fd
        if writer.entry(self.root_fd, JOURNAL) is not None and writer.entry(self.root_fd, LOCK) is None:
            raise Refused(f"{shown(self.path)} has no lock beside it, so staging didn't create it; left unchanged")
        self.lock = self.open_lock(lock_path)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.lock)
            raise Refused("another staging run holds the journal lock; refusing to interleave") from None
        try:
            self.fd, self.records, self.repaired = self.open_journal()
        except BaseException:
            os.close(self.lock)
            raise

    def check(self, fd, path):
        st = os.fstat(fd)
        w = self.writer
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != w.uid or st.st_dev != w.dev:
            os.close(fd)
            w.foreign(path, "is not a singly linked file this user owns on the destination's device")
        return st

    def open_lock(self, path):
        """The lock is only ever opened read-only once it exists: nothing is
        written to it."""
        st = self.writer.entry(self.root_fd, LOCK)
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise Refused(f"{shown(path)} is a symlink; staging never follows a link under its destination")
        try:
            fd = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.root_fd)
        except FileNotFoundError:
            fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.root_fd)
            os.fsync(self.root_fd)
        self.lock_inode = list((lambda st: (st.st_dev, st.st_ino))(self.check(fd, path)))
        return fd

    def open_journal(self):
        st = self.writer.entry(self.root_fd, JOURNAL)
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise Refused(f"{shown(self.path)} is a symlink; staging never follows a link under its destination")
        try:
            fd = os.open(JOURNAL, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW, dir_fd=self.root_fd)
        except FileNotFoundError:
            fd = os.open(JOURNAL, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                         dir_fd=self.root_fd)
            st = os.fstat(fd)
            header = {"op": None, "kind": "journal", "inode": [st.st_dev, st.st_ino], "lock": self.lock_inode}
            self.fd, self.records = fd, []
            self.write_record(header)
            os.fsync(self.root_fd)
            return fd, self.records, 0
        st = self.check(fd, self.path)
        data = os.pread(fd, st.st_size, 0)
        try:
            records, torn = parse_journal(data, self.path)
            header = records[0] if records else {}
            if header.get("kind") != "journal" or header.get("inode") != [st.st_dev, st.st_ino] \
                    or header.get("lock") != self.lock_inode:
                raise Refused(f"{shown(self.path)} is not the journal staging created here (its first record "
                              "doesn't name this journal and lock); left unchanged")
        except Refused:
            os.close(fd)
            raise
        if torn:
            os.ftruncate(fd, len(data) - torn)  # only a recognizable torn suffix, in staging's own journal
            os.fsync(fd)
        return fd, records, torn

    def close(self):
        os.close(self.fd)
        os.close(self.lock)

    def write_record(self, record):
        record = dict(record, schema=JOURNAL_SCHEMA, seq=len(self.records) + 1,
                      at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        write_all(self.fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(self.fd)
        self.records.append(record)
        return record

    def append(self, record):
        record = self.write_record(record)
        if self.crash:
            self.crash(record)
        return record


def parse_journal(data, path):
    """(records, bytes of a torn suffix). Only a final fragment with no
    newline that starts like one of staging's records counts as torn (a
    crash mid-write); any other unreadable line, or a journal whose first
    record isn't staging's header, is a refusal."""
    lines = data.split(b"\n")
    tail = lines.pop()  # after the last newline: empty unless the final write was cut short
    records = []
    for n, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
            assert isinstance(record, dict) and record.get("schema") == JOURNAL_SCHEMA
        except (ValueError, AssertionError):
            raise Refused(f"{shown(path)} is not a staging journal, or is damaged at line {n}; "
                          "left unchanged") from None
        records.append(record)
    if tail and not (tail.startswith(b'{"') and records):
        raise Refused(f"{shown(path)} ends with text that is not a cut-short staging record; left unchanged")
    if records and records[0].get("kind") != "journal":
        raise Refused(f"{shown(path)} is not a staging journal; left unchanged")
    return records, len(tail)


def read_journal(path):
    """The journal's records, read-only (status): a torn final fragment is
    ignored, not repaired."""
    path = Path(path)
    if not os.path.lexists(path.parent):
        return []
    fd, _ = pin_directory(path.parent, [])  # read-only: through descriptors, never a link
    try:
        try:
            data = read_inside(fd, path.name)
        except Refused:
            if not os.path.lexists(path):
                return []
            raise Refused(f"{shown(path)} is not a journal staging can read without following a link") from None
    finally:
        os.close(fd)
    return parse_journal(data, path)[0]


def operations(records):
    """{operation id: summary} in journal order."""
    ops = {}
    for r in records:
        if r.get("op") is None or r["kind"] not in ("begin", "resume", "intent", "outcome"):
            continue
        op = ops.setdefault(r["op"], {"id": r["op"], "binding": None, "outcomes": {}, "intents": {},
                                      "complete": False, "last": None})
        if r["kind"] in ("begin", "resume"):
            op["binding"] = r.get("binding", op["binding"])
            op["last"] = r["kind"]
        elif r["kind"] == "intent":
            op["intents"][r["step"]] = r["attempt"]
            op["last"] = f"intent {r['step']}"
        else:
            op["outcomes"][r["step"]] = r["result"]
            op["last"] = f"outcome {r['step']}: {r['result']}"
            if r["step"] == "complete" and r["result"] == "ok":
                op["complete"] = True
    return ops


# --- stage -------------------------------------------------------------------

def check_destination(dest, identity):
    """The resolved staging root, after build_release's guard: the root and
    everything staging writes under it stay outside this checkout and every
    live location, after resolving symlinks."""
    names = (JOURNAL, LOCK, RELEASES, f"{RELEASES}/{identity}", f"{RELEASES}/{identity}/env",
             f"{RELEASES}/{identity}/artifacts")
    try:
        root = build_release.check_output_dir(dest, CHECKOUT, names)
    except Refused as e:
        text = str(e).replace("release directory", "staging path")
        named = f"the staging destination {shown(dest)}"
        raise Refused(text.replace("the output directory", named).replace("output directory", named)) from None
    if os.path.lexists(root) and not root.is_dir():
        raise Refused(f"the staging destination {shown(root)} exists and is not a directory; left unchanged")
    if root.is_dir():
        present = set(os.listdir(root))
        unknown = sorted(present - {JOURNAL, LOCK, RELEASES})
        if RELEASES in present and JOURNAL not in present:
            unknown.append(f"{RELEASES} without a journal")
        if unknown:
            raise Refused(f"the staging destination {shown(root)} holds entries staging didn't create "
                          f"({', '.join(unknown[:3])}); left unchanged")
    return root


def check_root_contents(writer):
    """check_destination's content check again, on the pinned root."""
    present = set(os.listdir(writer.root_fd))
    unknown = sorted(present - {JOURNAL, LOCK, RELEASES})
    if RELEASES in present and JOURNAL not in present:
        unknown.append(f"{RELEASES} without a journal")
    if unknown:
        raise Refused(f"the staging destination {shown(writer.root)} holds entries staging didn't create "
                      f"({', '.join(unknown[:3])}); left unchanged")


LAUNCHER = re.compile(r"""^'''exec' (?:"([^"]+)"|(\S+)) "\$0" "\$@"$""")  # quoted only with spaces


ARGV0 = "sys.argv[0]"


def launcher_problem(text, module, attr):
    """None when a console script is the launcher pip writes for the entry
    point module:attr, judged by its structure (ast.parse runs nothing),
    never by its RECORD hash: after the shebang (and, for a long path, the
    /bin/sh exec preamble, a bare string to Python), only `import re` and
    `import sys`, the one import of the entry point, and an
    `if __name__ == "__main__":` block that at most rewrites sys.argv[0]
    from itself and then exits with the entry point's result. Otherwise
    the reason."""
    try:
        body = ast.parse(text).body
    except (SyntaxError, ValueError):
        return "it isn't valid Python"
    if text.startswith("#!/bin/sh\n"):
        if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            return "its /bin/sh preamble isn't pip's"
        body = body[1:]
    imports = []
    while body and isinstance(body[0], ast.Import):
        imports += [(a.name, a.asname) for a in body.pop(0).names]
    if ("sys", None) not in imports or not set(imports) <= {("re", None), ("sys", None)}:
        return "it imports something other than re and sys before the entry point"
    first = body.pop(0) if body else None
    if not (isinstance(first, ast.ImportFrom) and first.module == module and not first.level
            and [(a.name, a.asname) for a in first.names] == [(attr, None)]):
        return f"it doesn't import the entry point {module}:{attr} next"
    guard = body[0] if len(body) == 1 else None
    if not (isinstance(guard, ast.If) and not guard.orelse and ast.unparse(guard.test) in
            ("__name__ == '__main__'", '__name__ == "__main__"')):
        return "it holds more than the import and one __main__ block"
    *rewrites, last = guard.body
    if ast.unparse(last) != f"sys.exit({attr}())":
        return f"its __main__ block doesn't end by exiting with {attr}()"
    if not all(argv0_rewrite(r) for r in rewrites):
        return "its __main__ block does more than rewrite sys.argv[0]"
    return None


def argv0_rewrite(node):
    """An assignment to sys.argv[0], or an if/elif over such assignments,
    computed only from sys.argv[0], constants, slices, its endswith and
    removesuffix, and re.sub: what pip's launchers (across its versions) do
    to drop a -script.pyw or .exe suffix."""
    if isinstance(node, ast.If):
        return safe_expression(node.test) and all(argv0_rewrite(n) for n in node.body + node.orelse)
    return (isinstance(node, ast.Assign) and len(node.targets) == 1 and ast.unparse(node.targets[0]) == ARGV0
            and safe_expression(node.value))


def safe_expression(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = ast.unparse(n.func)
            if not (name == "re.sub" or (name in (f"{ARGV0}.endswith", f"{ARGV0}.removesuffix"))):
                return False
        elif isinstance(n, ast.Name) and n.id not in ("sys", "re"):
            return False
        elif isinstance(n, ast.Attribute) and n.attr not in ("argv", "sub", "endswith", "removesuffix"):
            return False
        elif not isinstance(n, (ast.Call, ast.Name, ast.Attribute, ast.Subscript, ast.Slice, ast.Constant,
                                ast.UnaryOp, ast.USub, ast.Load, ast.Compare, ast.Eq)):
            return False
    return True


def interpreter_of(lines):
    """The interpreter a console script runs: its shebang's, or, when pip
    wrote the /bin/sh launcher it uses for a path too long for a shebang
    (Linux allows 127 bytes), the one that launcher execs."""
    if not lines or not lines[0].startswith("#!"):
        return None
    words = lines[0][2:].split()
    if words == ["/bin/sh"]:
        m = LAUNCHER.match(lines[1]) if len(lines) > 1 else None
        return (m.group(1) or m.group(2)) if m else None
    return words[0] if words else None


def wheel_contents(wheel):
    """({member: RECORD hash} for the wheel's hashed files, {command: entry
    point}, its dist-info directory) from a wheel that already verified
    against its manifest."""
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        record = next(n for n in names if n.endswith(".dist-info/RECORD"))
        files = {row[0]: row[1] for row in csv.reader(io.StringIO(z.read(record).decode())) if row and row[1]}
        points = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        text = z.read(points).decode() if points else ""
    entry_points, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[console_scripts]" and "=" in line:
            name, _, target = line.partition("=")
            entry_points[name.strip()] = target.strip()
    return files, entry_points, record.rsplit("/", 1)[0]


def pip_additions(site, wheel):
    """{path: kind} for every entry pip may add to the environment when it
    installs the verified wheel, derived from the wheel alone and never from
    what pip leaves (its RECORD included): each member at its place in
    site-packages, the dist-info files pip writes itself (INSTALLER,
    REQUESTED, direct_url.json and the rewritten RECORD), a command in bin/
    for each console script the wheel declares, and each directory holding
    one. pip runs with --no-compile, so no bytecode is expected."""
    _, entry_points, dist_info = wheel_contents(io.BytesIO(wheel))
    with zipfile.ZipFile(io.BytesIO(wheel)) as z:
        members = [n for n in z.namelist() if not n.endswith("/")]
    files = [f"bin/{command}" for command in entry_points]
    for member in members + [f"{dist_info}/{n}" for n in ("INSTALLER", "REQUESTED", "direct_url.json", "RECORD")]:
        located = os.path.normpath(os.path.join(site, member))
        if located.startswith("..") or os.path.isabs(located) or ".data/" in member:
            raise Refused(f"the verified wheel holds {member}, which pip would place outside site-packages")
        if "/" not in member and (member.endswith(".pth") or member in STARTUP_HOOKS):
            raise Refused(f"the verified wheel holds {member}, a startup hook")
        files.append(located)
    permitted = {}
    for rel in files:
        permitted[rel] = "file"
        parent = os.path.dirname(rel)
        while parent:
            permitted.setdefault(parent, "dir")
            parent = os.path.dirname(parent)
    return permitted


def hex_to_record(hexdigest):
    """A file's sha256 hex digest as a RECORD hash."""
    return "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(hexdigest)).rstrip(b"=").decode()


def first_difference(actual, expected):
    changed = sorted(set(actual) ^ set(expected) | {k for k in actual if actual[k] != expected.get(k)})
    return changed[0] + (f" and {len(changed) - 1} more" if len(changed) > 1 else "") if changed else None


def site_packages(inventory):
    sites = [k for k, v in inventory.items() if v == ["dir"] and re.fullmatch(r"lib/python[^/]+/site-packages", k)]
    if len(sites) != 1:
        raise Refused("the staged environment has no single site-packages")
    return sites[0]


def check_installed(inventory, env_fd, env, wheel, package):
    """What can be checked from one pinned snapshot of the environment,
    without running anything: every file the verified wheel hashes is
    installed with its bytes; every file the installed RECORD hashes
    matches, and RECORD names nothing outside the environment; the package
    directory holds nothing RECORD doesn't list; and each command is a
    regular executable file, listed in RECORD, that runs the staged
    interpreter and calls the wheel's entry point. Returns the paths RECORD
    lists, relative to the environment."""
    site = site_packages(inventory)
    wheel_files, entry_points, dist_info = wheel_contents(io.BytesIO(wheel))

    def file_hash(rel):
        entry = inventory.get(rel)
        return hex_to_record(entry[1]) if entry and entry[0] == "file" else None
    for member, digest_ in sorted(wheel_files.items()):
        if file_hash(f"{site}/{member}") != digest_:
            raise Refused(f"the installed {member} differs from the verified wheel")
    try:
        record = read_inside(env_fd, f"{site}/{dist_info}/RECORD")
    except Refused:
        raise Refused("the installed package has no RECORD") from None
    if file_hash(f"{site}/{dist_info}/RECORD") != build_release.record_hash(record):
        raise Refused("the installed RECORD changed while it was read")
    listed = {}
    for row in csv.reader(io.StringIO(record.decode("utf-8"))):
        if not row:
            continue
        located = os.path.normpath(os.path.join(site, row[0]))
        if located.startswith("..") or os.path.isabs(located):
            raise Refused(f"the installed RECORD names {row[0]}, outside the environment")
        listed[located] = (row[1] if len(row) > 1 else "", row[0])
    for command in package["commands"]:
        rel = f"bin/{command}"
        entry = inventory.get(rel)
        if not entry or entry[0] != "file":
            raise Refused(f"the staged environment has no {command} command")
        data = read_inside(env_fd, rel)
        if hashlib.sha256(data).hexdigest() != entry[1]:
            raise Refused(f"the staged {command} command changed while it was read")
        text = data.decode("utf-8", errors="replace")
        runs = interpreter_of(text.splitlines())
        if not runs or os.path.normpath(runs) != os.path.normpath(Path(env) / "bin" / os.path.basename(runs)):
            raise Refused(f"the staged {command} command doesn't run the staged interpreter")
        if not entry[2] & 0o100:
            raise Refused(f"the staged {command} command is not executable")
        module, _, attr = entry_points.get(command, "").partition(":")
        if not attr or f"from {module} import {attr}" not in text:
            raise Refused(f"the staged {command} command doesn't call the wheel's entry point "
                          f"({entry_points.get(command, 'none declared')})")
        if rel not in listed:
            raise Refused(f"the staged {command} command isn't in the installed package's RECORD")
    for located, (expected, named) in sorted(listed.items()):
        if expected and file_hash(located) != expected:
            raise Refused(f"the installed {named} no longer matches the package's RECORD")
    for command in package["commands"]:
        module, _, attr = entry_points[command].partition(":")
        problem = launcher_problem(read_inside(env_fd, f"bin/{command}").decode("utf-8", errors="replace"),
                                   module, attr)
        if problem:
            raise Refused(f"the staged {command} command isn't the launcher pip writes for the wheel's entry point "
                          f"({entry_points[command]}): {problem}")
    for rel, entry in sorted(inventory.items()):
        if rel.startswith(f"{site}/plateia_chat/") and entry[0] != "dir" and rel not in listed:
            raise Refused(f"the installed package holds a file its RECORD doesn't list "
                          f"({os.path.relpath(rel, site)})")
    return set(listed)


def verified_artifacts(writer, artifacts_rel, manifest_sha, manifest):
    """The staged manifest and artifacts, read through pinned descriptors:
    the manifest's bytes are the release's (by sha256) and say what the
    verified manifest says, the directory holds exactly its artifacts, and
    each matches its recorded sha256 and size. Returns {name: bytes}."""
    data = {MANIFEST: writer.read(f"{artifacts_rel}/{MANIFEST}")}
    if hashlib.sha256(data[MANIFEST]).hexdigest() != manifest_sha:
        raise Refused("the staged manifest differs from the release's")
    try:
        staged = json.loads(data[MANIFEST])
    except ValueError:
        raise Refused("the staged manifest is unreadable") from None
    if staged != manifest:
        raise Refused("the staged manifest differs from the verified one")
    with writer.pinned(artifacts_rel) as fd:
        present = set(os.listdir(fd))
    expected = {MANIFEST} | {a["name"] for a in manifest["artifacts"]}
    if present != expected:
        raise Refused(f"the staged artifacts directory holds {sorted(present ^ expected)[0]}, which the manifest "
                      "doesn't list or is missing")
    for entry in manifest["artifacts"]:
        data[entry["name"]] = writer.read(f"{artifacts_rel}/{entry['name']}")
        found = data[entry["name"]]
        if hashlib.sha256(found).hexdigest() != entry["sha256"] or len(found) != entry.get("size"):
            raise Refused(f"the staged artifact {entry['name']} does not match the manifest")
    return data


def verify_static(writer, rel_dir, manifest_sha, manifest, inventory_sha):
    """Everything that can be checked without running the environment's
    code, checked before it runs, through pinned descriptors only: the
    artifacts against the manifest, the environment entry by entry against
    the inventory recorded when this operation built it (startup hooks such
    as .pth files and sitecustomize, bytecode, the interpreter link and
    pyvenv.cfg included), the installed package against the verified wheel
    and its own RECORD, and each command (check_installed)."""
    artifacts = verified_artifacts(writer, f"{rel_dir}/artifacts", manifest_sha, manifest)
    try:
        recorded = writer.read(f"{rel_dir}/{INVENTORY}")
    except (Refused, FileNotFoundError):
        raise Refused("the staged environment has no recorded inventory") from None
    if hashlib.sha256(recorded).hexdigest() != inventory_sha:
        raise Refused("the staged environment's inventory differs from the one the journal recorded")
    expected = json.loads(recorded)
    with writer.pinned(f"{rel_dir}/env") as fd:
        actual = inventory_at(fd)
        changed = first_difference(actual, expected)
        if changed:
            raise Refused(f"the staged environment differs from what this operation built ({changed})")
        check_installed(actual, fd, writer.root / rel_dir / "env", artifacts[manifest["package"]["artifact"]],
                        manifest["package"])
    return expected, artifacts[manifest["package"]["artifact"]]


STARTUP_HOOKS = ("sitecustomize.py", "usercustomize.py")


def environment_facts(expected, env_fd, env, interpreter):
    """What the environment's own startup would establish, derived without
    running it from the trusted inventory and files read through the pinned
    environment: bin/python leads, through links inside the environment, to
    the operation's chosen interpreter; pyvenv.cfg names that interpreter's
    directory and version and keeps the system site-packages out, so the
    prefix is the environment and its one site-packages is purelib; no
    sitecustomize or usercustomize is anywhere in it; and the import path's
    additions are the site-packages folder plus the directory lines of its
    .pth files (each venv built, pinned by the inventory, and read here).
    These are static checks only: they don't show that bin/python starts,
    or that its venv detection and site processing (the .pth files' import
    lines included) run as expected. Returns (prefix, purelib relative to the environment, additions to the
    import path, the .pth files)."""
    rel, hops = "bin/python", 0
    while expected.get(rel, [None])[0] == "link" and hops < 8:
        target, hops = expected[rel][1], hops + 1
        if os.path.isabs(target):
            rel = target
            break
        rel = os.path.normpath(os.path.join(os.path.dirname(rel), target))
    if not os.path.isabs(rel) or os.path.realpath(rel) != os.path.realpath(interpreter["path"]):
        raise Refused(f"the staged environment's bin/python doesn't lead to the operation's interpreter "
                      f"({shown(interpreter['path'])})")
    entry = expected.get("pyvenv.cfg")
    data = read_inside(env_fd, "pyvenv.cfg") if entry and entry[0] == "file" else None
    if data is None or hashlib.sha256(data).hexdigest() != entry[1]:
        raise Refused("the staged environment's pyvenv.cfg is missing or changed while it was read")
    cfg = dict(line.split("=", 1) for line in data.decode("utf-8", errors="replace").splitlines() if "=" in line)
    cfg = {k.strip(): v.strip() for k, v in cfg.items()}
    if cfg.get("include-system-site-packages") != "false":
        raise Refused("the staged environment's pyvenv.cfg doesn't keep the system site-packages out")
    if cfg.get("version") != interpreter["version"] or \
            os.path.realpath(cfg.get("home", "")) != os.path.realpath(os.path.dirname(rel)):
        raise Refused("the staged environment's pyvenv.cfg doesn't name the operation's interpreter")
    major_minor = ".".join(interpreter["version"].split(".")[:2])
    site = site_packages(expected)
    if site != f"lib/python{major_minor}/site-packages":
        raise Refused(f"the staged environment's site-packages ({site}) isn't the chosen interpreter's")
    hooks = sorted(k for k in expected if os.path.basename(k) in STARTUP_HOOKS)
    if hooks:
        raise Refused(f"the staged environment holds a startup hook ({hooks[0]})")
    additions, pth = [os.path.join(env, site)], []
    for rel_pth in sorted(k for k in expected if os.path.dirname(k) == site and k.endswith(".pth")):
        if expected[rel_pth][0] != "file":
            raise Refused(f"the staged environment's {rel_pth} is not a file")
        text = read_inside(env_fd, rel_pth)
        if hashlib.sha256(text).hexdigest() != expected[rel_pth][1]:
            raise Refused(f"the staged environment's {rel_pth} changed while it was read")
        pth.append(rel_pth)
        for line in text.decode("utf-8", errors="replace").splitlines():
            if line.strip() and not line.startswith(("#", "import ", "import\t")):
                additions.append(os.path.normpath(os.path.join(env, site, line.rstrip())))
    return str(env), os.path.join(env, site), additions, pth


def import_payload(expected, env_fd, site, wheel):
    """The package's sources and dist-info metadata for the import check,
    read through the pinned environment: each source and the metadata file
    must hash as the verified wheel says, and the installed RECORD as the
    journaled inventory says."""
    wheel_files, _, dist_info = wheel_contents(io.BytesIO(wheel))
    sources, info = {}, {}
    for member, want in sorted(wheel_files.items()):
        is_source = member.startswith("plateia_chat/") and member.endswith(".py")
        if not is_source and member not in (f"{dist_info}/METADATA", f"{dist_info}/entry_points.txt"):
            continue
        data = read_inside(env_fd, f"{site}/{member}")
        if build_release.record_hash(data) != want:
            raise Refused(f"the installed {member} differs from the verified wheel")
        if is_source:
            sources[member] = base64.b64encode(data).decode()
        else:
            info[os.path.basename(member)] = base64.b64encode(data).decode()
    record = read_inside(env_fd, f"{site}/{dist_info}/RECORD")
    entry = expected.get(f"{site}/{dist_info}/RECORD")
    if not entry or hashlib.sha256(record).hexdigest() != entry[1]:
        raise Refused("the installed RECORD differs from the journaled inventory")
    info["RECORD"] = base64.b64encode(record).decode()
    return {"sources": sources, "metadata": info}


def verify_staged(writer, rel_dir, manifest_sha, manifest, interpreter, inventory_sha):
    """Check a staged release without running anything from its
    environment: statically (verify_static, environment_facts), then by
    importing plateia_chat in the operation's chosen interpreter from the
    environment's own files, read through pinned descriptors and
    hash-checked (IMPORT_PROBE), then statically again. The environment's
    path is checked against the pinned directories just before and after the
    import check, and its contents compared with the trusted inventory
    afterwards: a change around it is refused and never reported verified.
    Returns what was found, or raises Refused naming the problem."""
    expected, wheel = verify_static(writer, rel_dir, manifest_sha, manifest, inventory_sha)
    env_rel = f"{rel_dir}/env"
    env = writer.root / env_rel
    with writer.pinned(env_rel) as fd:
        prefix, purelib, additions, _ = environment_facts(expected, fd, env, interpreter)
        site = site_packages(expected)
        payload = import_payload(expected, fd, site, wheel)
    payload["site"] = str(env / site)
    python = interpreter["path"]
    writer.path_intact(env_rel, "the import check")
    try:
        r = run([python, "-I", "-S", "-B", "-c", IMPORT_PROBE], env=child_env(), cwd=os.sep,
                input=json.dumps(payload))
    finally:
        writer.path_intact(env_rel, "the import check", after=True)
        with writer.pinned(env_rel) as fd:
            changed = first_difference(inventory_at(fd), expected)
        if changed:
            raise Refused(f"the staged environment changed while the import check ran ({changed})")
    try:
        found = json.loads(r.stdout.strip().splitlines()[-1])
        assert all(isinstance(found.get(k), str) for k in ("implementation", "version", "system"))
        assert isinstance(found.get("path"), list)
    except (IndexError, ValueError, AssertionError, AttributeError):
        raise Refused("the chosen interpreter didn't report the import check") from None
    if found.get("error") or not isinstance(found.get("module"), str) or \
            not isinstance(found.get("dist_version"), str):
        raise Refused(f"the staged environment can't import plateia_chat ({found.get('error', 'no module')})")
    if found["shadowed"]:
        raise Refused("the chosen interpreter's own library holds a plateia_chat, which would shadow the staged one")
    recorded = dict(tuple(row) for row in found.get("record") or [])
    for member, data in payload["sources"].items():
        if recorded.get(member) != build_release.record_hash(base64.b64decode(data)):
            raise Refused(f"the installed package's metadata doesn't match the imported {member}")
    found.update(prefix=prefix, purelib=purelib, path=found["path"] + additions)

    def inside(path):
        return any(p == base or p.startswith(base + os.sep)
                   for p, base in ((os.path.normpath(path), os.path.normpath(env)),
                                   (os.path.realpath(path), os.path.realpath(env))))
    if not inside(found["prefix"]) or not inside(found["module"]):
        raise Refused("the staged environment loads plateia_chat from outside itself")
    if found["dist_version"] != manifest["package"]["version"]:
        raise Refused(f"the staged environment holds plateia-chat {found['dist_version']}, "
                      f"not {manifest['package']['version']}")
    if any(isinstance(e, str) and e and os.path.realpath(e).startswith(str(CHECKOUT) + os.sep)
           for e in found["path"]):
        raise Refused("the staged environment has this checkout on its import path")
    result = interpreter_result(manifest, found, str(python))
    if result[0] != "pass":
        raise Refused(f"the staged environment's {result[2]}")
    if found["version"] != interpreter["version"] or found["implementation"] != interpreter["implementation"]:
        raise Refused("the staged environment's interpreter is not the one the operation chose")
    return found


def stage(release, dest, python=None, plan_path=None, spec_path=None, crash=None):
    """Stage `release` under `dest`; returns (summary, plan, preflight report)."""
    release = Path(release)
    python = str(python or sys.executable)
    plan = make_plan(release, dest, spec_path)
    if plan_path:
        saved = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        if saved.get("schema") != PLAN_SCHEMA or saved.get("manifest_sha256") != plan["manifest_sha256"]:
            raise Refused("the saved plan is not a plan for this release")
        for old, new in zip(saved["targets"], plan["targets"]):
            if old.get("digest") != new["digest"] or old.get("name") != new["name"]:
                raise Refused(f"blocked: {new['name']} changed since the plan was made; nothing was staged")
    if plan["blocked"]:
        raise Refused("blocked: " + "; ".join(plan["blocked"]) + "; nothing was staged")
    report = preflight(release, python, spec_path)
    if report["refused"]:
        raise Refused("preflight refused: " + "; ".join(report["refused"]) + "; nothing was staged")
    manifest = build_release.verify(release)
    if identity_problem(manifest):
        raise Refused(f"the manifest can't be staged: {identity_problem(manifest)}; nothing was staged")
    root = check_destination(dest, manifest["release"])
    # The root and any missing parent are created through pinned descriptors.
    writer = Writer(root, build_release.live_roots(), create=True)
    try:
        check_root_contents(writer)
        journal = Journal(writer, crash)
        try:
            return run_operation(journal, writer, release, manifest, plan, report, python, spec_path), plan, report
        finally:
            journal.close()
    finally:
        writer.close()


def run_operation(journal, writer, release, manifest, plan, report, python, spec_path):
    identity = manifest["release"]
    rel_dir = f"{RELEASES}/{identity}"
    release_dir = writer.root / RELEASES / identity
    artifacts_rel, env_rel = f"{rel_dir}/artifacts", f"{rel_dir}/env"
    names = [MANIFEST] + [a["name"] for a in manifest["artifacts"]]
    # The targets are checked again, under the lock, immediately before the
    # operation records them.
    spec, skills, provenance = load_spec(spec_path)
    recheck = [observe(t, skills, provenance) for t in spec["targets"]]
    changed = [new["name"] for old, new in zip(plan["targets"], recheck) if old["digest"] != new["digest"]]
    if changed or any(t["problems"] for t in recheck):
        raise Refused(f"blocked: {', '.join(changed) or 'a target'} changed before staging could record it; "
                      "nothing was staged")
    binding = {"release": identity, "manifest_sha256": plan["manifest_sha256"],
               "destination": str(writer.root), "release_dir": str(release_dir),
               "interpreter": {k: report["interpreter"][k] for k in ("path", "implementation", "version", "system")},
               "targets": {t["name"]: t["digest"] for t in recheck}}
    op_id = "stage-" + digest(binding)[:16]
    ops = operations(journal.records)
    writer.load(journal.records, op_id)
    writer.record = lambda r: journal.append(dict(r, op=op_id))
    for rel in [RELEASES, rel_dir, artifacts_rel, env_rel, f"{rel_dir}/{INVENTORY}", f"{rel_dir}/{STAGED}"] \
            + [f"{artifacts_rel}/{n}" for n in names]:
        writer.inspect(rel)  # an alias anywhere staging will touch is refused before anything changes

    def inventory_sha():
        found = [r for r in journal.records if r.get("op") == op_id and r["kind"] == "outcome"
                 and r["step"] == "install-package" and r["result"] == "ok"]
        return found[-1].get("inventory_sha256") if found else None

    if op_id in ops and ops[op_id]["complete"]:
        try:
            verify_staged(writer, rel_dir, binding["manifest_sha256"], manifest, binding["interpreter"],
                          inventory_sha())
        except Refused as e:
            raise Refused(f"operation {op_id} is recorded complete, but its staged release doesn't verify "
                          f"({e}); it is not reported staged, and nothing was changed") from None
        return {"operation": op_id, "state": "already staged, verified", "release_dir": shown(release_dir)}
    for other in ops.values():
        if other["id"] == op_id or other["binding"] is None:
            continue
        b = other["binding"]
        same_place = b.get("release_dir") == binding["release_dir"]
        if not other["complete"] or same_place:
            differs = sorted(k for k in binding if b.get(k) != binding[k])
            raise Refused(f"operation {other['id']} ({b.get('release')}) is "
                          f"{'unfinished' if not other['complete'] else 'already staged there'} with different "
                          f"inputs ({', '.join(differs)}); repeat it with its own inputs, nothing was changed")
    resumed = op_id in ops
    if not resumed:
        if writer.lstat(rel_dir) is not None:
            raise Refused(f"{shown(release_dir)} already exists and no operation in the journal created it; "
                          "left unchanged")
        journal.append({"op": op_id, "kind": "begin", "binding": binding, "manifest": manifest,
                        "targets": recheck, "importers": plan["importers"],
                        "compatibility": report["results"]})
    else:
        journal.append({"op": op_id, "kind": "resume",
                        "found": {"last": ops[op_id]["last"], "release_dir_exists": writer.lstat(rel_dir) is not None,
                                  "torn_bytes_dropped": journal.repaired}})
    attempt = 1 + max([r.get("attempt", 0) for r in journal.records if r.get("op") == op_id] or [0])

    def step(name, action):
        journal.append({"op": op_id, "kind": "intent", "step": name, "attempt": attempt})
        extra = {}
        try:
            try:
                detail = action()
                if isinstance(detail, tuple):
                    detail, extra = detail
            except OSError as e:
                raise Refused(f"{e.strerror or e.__class__.__name__}"
                              + (f" ({shown(e.filename)})" if e.filename else "")) from None
        except Refused as e:
            journal.append({"op": op_id, "kind": "outcome", "step": name, "attempt": attempt,
                            "result": "failed", "detail": str(e)})
            raise Refused(f"operation {op_id} is unfinished: {name} failed: {e}") from None
        journal.append(dict({"op": op_id, "kind": "outcome", "step": name, "attempt": attempt, "result": "ok",
                             "detail": detail}, **extra))

    def create_release_dir():
        writer.mkdir(RELEASES)
        writer.mkdir(rel_dir)
        return "created" if not resumed else "present and created by this operation"

    def copy_artifacts():
        writer.mkdir(artifacts_rel)
        with writer.pinned(artifacts_rel) as fd:
            strays = sorted(set(os.listdir(fd)) - set(names))
        for stray in strays:
            writer.remove(f"{artifacts_rel}/{stray}")  # only what this operation created
        for name in names:
            writer.write(f"{artifacts_rel}/{name}", (release / name).read_bytes())
        verified_artifacts(writer, artifacts_rel, binding["manifest_sha256"], manifest)  # the copies, read back pinned
        return f"{len(names)} files copied and verified"

    env = release_dir / "env"
    evidence = {}  # the environment venv built, inventoried through pinned descriptors right after it ran

    def create_environment():
        if writer.lstat(env_rel) is not None:
            writer.remove_tree(env_rel)  # an environment found on disk is never run: removed and built again
        writer.mkdir(env_rel)
        writer.path_intact(env_rel, "venv")
        try:
            r = run([python, "-m", "venv", env], env=child_env(), cwd=os.sep)
        finally:
            writer.path_intact(env_rel, "venv", after=True)  # a swap during it is named, whatever venv did
        if r.returncode:
            raise Refused(f"venv exited {r.returncode}")
        with writer.pinned(env_rel) as fd:
            evidence["venv"] = inventory_at(fd)
        data = json.dumps(evidence["venv"], sort_keys=True).encode()
        return ("venv created with the chosen interpreter and its bundled pip; inventoried",
                {"venv_inventory_sha256": hashlib.sha256(data).hexdigest()})

    def install_package():
        wheel_name = manifest["package"]["artifact"]
        wheel_bytes = verified_artifacts(writer, artifacts_rel, binding["manifest_sha256"], manifest)[wheel_name]
        if "venv" not in evidence:
            raise Refused("no environment was built in this run; pip was not run")
        # The environment's own interpreter and pip are about to run: it must
        # still be exactly what venv built (a .pth hook or link added since,
        # say, would run inside pip).
        writer.path_intact(env_rel, "pip")
        with writer.pinned(env_rel) as fd:
            changed = first_difference(inventory_at(fd), evidence["venv"])
        if changed:
            raise Refused(f"the environment changed after venv built it ({changed}); pip was not run")
        try:
            r = run([env / "bin" / "python", "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
                     "--no-compile", "--disable-pip-version-check", release_dir / "artifacts" / wheel_name],
                    env=child_env(), cwd=os.sep)
        finally:
            writer.path_intact(env_rel, "pip", after=True)
        if r.returncode:
            raise Refused(f"pip exited {r.returncode}")
        # What pip left becomes the trusted inventory only if it is what venv
        # built plus entries the verified wheel accounts for (pip_additions),
        # each matching the wheel. The installed RECORD is checked, but never
        # authorises an entry: pip's output could have been rewritten.
        with writer.pinned(env_rel) as fd:
            after = inventory_at(fd)
            before = evidence["venv"]
            permitted = pip_additions(site_packages(after), wheel_bytes)
            for rel in sorted(set(before) | set(after)):
                if rel in before and after.get(rel) != before[rel]:
                    raise Refused(f"pip changed or removed {rel}, which venv built; not recorded as installed")
                if rel not in before and (rel not in permitted or after[rel][0] != permitted[rel]):
                    raise Refused(f"the environment holds {rel}, which the verified wheel doesn't account for; "
                                  "not recorded as installed")
            listed = check_installed(after, fd, env, wheel_bytes, manifest["package"])
        beyond = sorted(set(listed) - set(permitted))
        if beyond:
            raise Refused(f"the installed RECORD names {beyond[0]}, which the verified wheel doesn't account for; "
                          "not recorded as installed")
        data = (json.dumps(after, indent=1, sort_keys=True) + "\n").encode()
        writer.write(f"{rel_dir}/{INVENTORY}", data)
        return (f"{manifest['package']['name']} {manifest['package']['version']} installed offline; "
                "environment inventoried", {"inventory_sha256": hashlib.sha256(data).hexdigest()})

    def verify_environment():
        found = verify_staged(writer, rel_dir, binding["manifest_sha256"], manifest, binding["interpreter"],
                              inventory_sha())
        return f"artifacts verify; plateia-chat {found['dist_version']} imports from the environment's verified " \
               f"files on {found['implementation']} {found['version']}"

    def complete():
        # Checked again at the completion boundary: the environment's path
        # against the pinned directories, and the artifacts and environment
        # against the trusted evidence. A change since verify-environment is
        # refused, and the operation is never recorded complete.
        writer.path_intact(env_rel, "the completion step")
        verify_static(writer, rel_dir, binding["manifest_sha256"], manifest, inventory_sha())
        writer.write(f"{rel_dir}/{STAGED}", (json.dumps(
            {"schema": STAGED_SCHEMA, "operation": op_id, "release": identity,
             "manifest_sha256": binding["manifest_sha256"], "interpreter": binding["interpreter"],
             "selected": False}, indent=2, sort_keys=True) + "\n").encode())
        return "staged, not selected"

    for name, action in zip(STEPS, (create_release_dir, copy_artifacts, create_environment, install_package,
                                    verify_environment, complete)):
        step(name, action)
    return {"operation": op_id, "state": "resumed and staged" if resumed else "staged",
            "release_dir": shown(release_dir)}


def status(dest):
    """Each journaled operation's state: read-only."""
    root = Path(dest)
    out = []
    for op in operations(read_journal(root / JOURNAL)).values():
        b = op["binding"] or {}
        out.append({"operation": op["id"], "release": b.get("release"),
                    "state": "complete" if op["complete"] else f"unfinished (last: {op['last']})",
                    "selected": False})
    return out


# --- command line ------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="stage_release", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="list the managed targets and importers; read-only")
    p.add_argument("--release", required=True, type=Path)
    p.add_argument("--dest", type=Path)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path, help="also save the plan here, for stage --plan")
    f = sub.add_parser("preflight", help="check a release against this machine; read-only")
    f.add_argument("--release", required=True, type=Path)
    f.add_argument("--python", help="the interpreter to stage with (default: this one)")
    f.add_argument("--json", action="store_true")
    s = sub.add_parser("stage", help="stage a release under a journal")
    s.add_argument("--release", required=True, type=Path)
    s.add_argument("--dest", required=True, type=Path)
    s.add_argument("--python")
    s.add_argument("--plan", type=Path, help="a saved plan the targets must still match")
    t = sub.add_parser("status", help="report the journal's operations; read-only")
    t.add_argument("--dest", required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            plan = make_plan(a.release, a.dest)
            if a.out:
                out = Path(os.path.abspath(a.out))
                writer = Writer(out.parent, build_release.live_roots())
                try:
                    writer.create(out.name, (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode())
                finally:
                    writer.close()
            print(json.dumps(plan, indent=2, sort_keys=True) if a.json else render_plan(plan))
            return 1 if plan["blocked"] else 0
        if a.cmd == "preflight":
            report = preflight(a.release, a.python)
            print(json.dumps(report, indent=2, sort_keys=True) if a.json else render_preflight(report))
            return 1 if report["refused"] else 0
        if a.cmd == "stage":
            summary, plan, report = stage(a.release, a.dest, a.python, a.plan)
            print(render_plan(plan))
            print(render_preflight(report))
            print(f"{summary['state']}: {plan['release']} in {summary['release_dir']} "
                  f"(operation {summary['operation']}); nothing was selected")
            return 0
        for op in status(a.dest):
            print(f"{op['operation']}: {op['release']}: {op['state']}; selected: no")
        return 0
    except Refused as e:
        print(f"stage_release: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
