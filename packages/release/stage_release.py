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
import datetime
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
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
JOURNAL, LOCK, RELEASES, MARKER, STAGED = "journal.jsonl", "journal.lock", "releases", ".operation", "staged.json"
STEPS = ("create-release-dir", "copy-artifacts", "create-environment", "install-package",
         "verify-environment", "complete")
IMPORTER_LIMIT = 2 << 20  # larger files aren't scripts; they are skipped, not read
SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".zsh")  # with the executable bit: the files searched

# A path-bound importer manipulates the import path (or PYTHONPATH) and names
# the chat/scripts location, as "chat" / "scripts", os.path.join(..., "chat",
# "scripts") or a "chat/scripts" string.
SYS_PATH = re.compile(r"sys\.path\s*(?:\.\s*(?:insert|append|extend)\b|\[|\+=)|\bPYTHONPATH\b")
CHAT_SCRIPTS = re.compile(r"""["']chat["']\s*[/,]\s*["']scripts["']|["'][^"'\n]*\bchat/scripts\b""")

PROBE = ("import json, platform, sys; print(json.dumps({'implementation': platform.python_implementation(), "
         "'version': platform.python_version(), 'system': platform.system(), 'prefix': sys.prefix}))")
ENV_PROBE = r"""
import json, platform, sys
from importlib import metadata
import plateia_chat
print(json.dumps({"implementation": platform.python_implementation(), "version": platform.python_version(),
                  "system": platform.system(), "prefix": sys.prefix, "module": plateia_chat.__file__,
                  "dist_version": metadata.version("plateia-chat"), "path": sys.path}))
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


def sha256_file(path):
    return drift_check.sha256(Path(path))


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
    found = sha256_file(path)
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
        observed["sha256"] = sha256_file(expected)
    elif not expected.is_dir() or expected.is_symlink():
        return observed, f"missing: the link destination {shown(expected)} is not a directory"
    return observed, None


def observe_launchagent(t, path, skills, provenance):
    if path.is_symlink() or not path.is_file():
        return {"type": "symlink" if path.is_symlink() else "other"}, \
            "unknown ownership: not a regular property-list file"
    observed = {"type": "file", "sha256": sha256_file(path)}
    try:
        with open(path, "rb") as stream:
            plist = plistlib.load(stream)
    except (plistlib.InvalidFileException, ValueError, OSError):
        return observed, "unknown ownership: not a readable property list"
    # Only the label and program arguments are looked at: never the
    # environment, log paths or anything else the file holds.
    if not isinstance(plist, dict) or plist.get("Label") != t["label"]:
        return observed, f"unknown ownership: its Label is not {t['label']}"
    observed["label"] = t["label"]
    program = expand(t["program"], skills)
    arguments = plist.get("ProgramArguments")
    if not (isinstance(arguments, list) and any(isinstance(a, str) and Path(os.path.normpath(a)) == program
                                                for a in arguments)):
        return observed, f"retargeted program: its ProgramArguments don't name {shown(program)}"
    observed["program"] = shown(program)
    problem = content_problem(t["content"], skills, provenance)
    return observed, problem


def folder_inventory(skills, provenance, prefix=""):
    """The drift check's view of the chat folder, as (observed, problems):
    every captured file's hash, the excluded entries counted, and every
    missing, changed or unknown entry as a problem."""
    result = drift_check.check(skills, provenance)
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
            files[rel] = sha256_file(skills / rel)
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


def find_importers(spec, skills):
    """Path-bound importers of chat/scripts under the declared source roots.
    Only scripts are opened (a Python or shell suffix, or the executable
    bit), never data files, and links aren't followed; the chat folder
    itself is skipped. A root or script that can't be read makes the
    inventory incomplete rather than clean."""
    cfg = spec["importers"]
    managed = skills / "chat"
    known = {expand(k["path"], skills): k["name"] for k in cfg["known"]}
    found, roots = {}, []
    for text in cfg["roots"]:
        root = expand(text, skills)
        entry = {"path": shown(root), "status": "complete", "unreadable": []}
        roots.append(entry)
        if not os.path.lexists(root):
            entry["status"] = "absent"
            continue
        if root.is_symlink() or not root.is_dir():
            entry["status"] = "incomplete"
            entry["unreadable"].append(f"{shown(root)} is not a directory")
            continue

        def failed(error, entry=entry):
            entry["unreadable"].append(shown(error.filename or root))
        for folder, dirs, files in os.walk(root, onerror=failed):
            dirs[:] = sorted(d for d in dirs if d not in cfg["skip_dirs"] and Path(folder, d) != managed
                             and not Path(folder, d).is_symlink())
            for name in sorted(files):
                path = Path(folder, name)
                try:
                    if path.is_symlink() or not path.is_file() or path.stat().st_size > IMPORTER_LIMIT:
                        continue
                    if not (path.suffix in SCRIPT_SUFFIXES or path.stat().st_mode & 0o111):
                        continue
                    data = path.read_bytes()
                except OSError:
                    entry["unreadable"].append(shown(path))
                    continue
                if b"\0" in data[:8192]:
                    continue
                text_ = data.decode("utf-8", errors="ignore")
                if SYS_PATH.search(text_) and CHAT_SCRIPTS.search(text_):
                    found[path] = known.get(path)
        if entry["unreadable"]:
            entry["status"] = "incomplete"
    return {"roots": roots,
            "known": [{"name": name, "path": shown(path), "found": path in found}
                      for path, name in known.items()],
            "unlisted": sorted(shown(p) for p, name in found.items() if name is None),
            "complete": all(r["status"] != "incomplete" for r in roots)}


def make_plan(release, dest=None, spec_path=None):
    """The plan for staging `release`: read-only."""
    spec, skills, provenance = load_spec(spec_path)
    manifest = build_release.verify(release)
    targets = [observe(t, skills, provenance) for t in spec["targets"]]
    staged = (Path(os.path.abspath(dest)) / RELEASES / manifest["release"]) if dest else None
    plan = {"schema": PLAN_SCHEMA, "release": manifest["release"],
            "manifest_sha256": sha256_file(Path(release) / MANIFEST),
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
    for path in sorted((Path.home() / ".local/state/project-manager").glob("*/runs/*/state.json")):
        value, problem = read_field(path, "schema")
        if problem:
            problems.append(f"{shown(path)}: {problem}")
        else:
            runs.add(value)
    found["child-runs"] = (sorted(runs), "; ".join(problems[:3]) or None)
    markers = sorted(p.name[:-len(".enabled")] for p in (state / "experiments").glob("review-start-receipts-v*.enabled"))
    found["receipt-experiment-marker"] = (markers, None)
    return found


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


# --- journal -----------------------------------------------------------------

class Journal:
    """The locked, durable stage journal: one JSON record per line, each
    flushed and fsynced before the step it announces runs."""

    def __init__(self, root, crash=None):
        self.root, self.path, self.crash = root, root / JOURNAL, crash
        self.lock = open(root / LOCK, "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise Refused("another staging run holds the journal lock; refusing to interleave") from None
        self.records = read_journal(self.path)

    def close(self):
        self.lock.close()

    def append(self, record):
        record = dict(record, schema=JOURNAL_SCHEMA, seq=len(self.records) + 1,
                      at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        new = not self.path.exists()
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            fsync_dir(self.root)
        self.records.append(record)
        if self.crash:
            self.crash(record)
        return record


def read_journal(path):
    """The journal's records. A torn final line (a crash mid-write) is
    ignored; any other unreadable line is a refusal."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for n, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
            assert isinstance(record, dict) and record.get("schema") == JOURNAL_SCHEMA
        except (ValueError, AssertionError):
            if n == len(lines):
                break
            raise Refused(f"the journal {shown(path)} is unreadable at line {n}; nothing was changed") from None
        records.append(record)
    return records


def operations(records):
    """{operation id: summary} in journal order."""
    ops = {}
    for r in records:
        op = ops.setdefault(r["op"], {"id": r["op"], "binding": None, "outcomes": {}, "intents": {},
                                      "complete": False, "last": None})
        if r["kind"] in ("begin", "resume"):
            op["binding"] = r.get("binding", op["binding"])
            op["last"] = r["kind"]
        elif r["kind"] == "intent":
            op["intents"][r["step"]] = r["attempt"]
            op["last"] = f"intent {r['step']}"
        elif r["kind"] == "outcome":
            op["outcomes"][r["step"]] = r["result"]
            op["last"] = f"outcome {r['step']}: {r['result']}"
            if r["step"] == "complete" and r["result"] == "ok":
                op["complete"] = True
    return ops


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path, data):
    temp = path.with_name(f".{path.name}.tmp")
    with open(temp, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fsync_dir(path.parent)


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
        raise Refused(str(e).replace("output directory", "staging destination")
                      .replace("release directory", "staging path")) from None
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


LAUNCHER = re.compile(r"""^'''exec' "([^"]+)" "\$0" "\$@"$""")


def interpreter_of(lines):
    """The interpreter a console script runs: its shebang's, or, when pip
    wrote the /bin/sh launcher it uses for a path too long for a shebang
    (Linux allows 127 bytes), the one that launcher execs."""
    if not lines or not lines[0].startswith("#!"):
        return None
    words = lines[0][2:].split()
    if words == ["/bin/sh"]:
        m = LAUNCHER.match(lines[1]) if len(lines) > 1 else None
        return m.group(1) if m else None
    return words[0] if words else None


def verify_staged(release_dir, manifest_sha, manifest, interpreter):
    """Check a staged release directory's artifacts and environment; returns
    the environment probe or raises Refused naming the problem."""
    artifacts = release_dir / "artifacts"
    staged = build_release.verify(artifacts)
    if sha256_file(artifacts / MANIFEST) != manifest_sha:
        raise Refused("the staged manifest differs from the release's")
    env = release_dir / "env"
    python = env / "bin" / "python"
    if not python.exists():
        raise Refused("the staged environment has no interpreter")
    r = run([python, "-I", "-c", ENV_PROBE], env=child_env(), cwd=str(release_dir))
    try:
        found = json.loads(r.stdout.strip().splitlines()[-1])
        assert all(isinstance(found.get(k), str) for k in ("implementation", "version", "system", "prefix",
                                                            "module", "dist_version"))
        assert isinstance(found.get("path"), list)
    except (IndexError, ValueError, AssertionError, AttributeError):
        raise Refused("the staged environment can't import plateia_chat") from None
    def inside(path):
        # A venv's bin/python is a link to the base interpreter, so the
        # path as written counts as well as the resolved one.
        return any(p == base or p.startswith(base + os.sep)
                   for p, base in ((os.path.normpath(path), os.path.normpath(env)),
                                   (os.path.realpath(path), os.path.realpath(env))))
    if not inside(found["prefix"]) or not inside(found["module"]):
        raise Refused("the staged environment loads plateia_chat from outside itself")
    if found["dist_version"] != staged["package"]["version"]:
        raise Refused(f"the staged environment holds plateia-chat {found['dist_version']}, "
                      f"not {staged['package']['version']}")
    if any(isinstance(e, str) and e and os.path.realpath(e).startswith(str(CHECKOUT) + os.sep)
           for e in found["path"]):
        raise Refused("the staged environment has this checkout on its import path")
    for command in staged["package"]["commands"]:
        script = env / "bin" / command
        try:
            lines = script.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        if not lines:
            raise Refused(f"the staged environment has no {command} command")
        runs = interpreter_of(lines)
        if not runs or not inside(runs):
            raise Refused(f"the staged {command} command doesn't run the staged interpreter")
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
    identity = manifest["release"]
    root = check_destination(dest, identity)
    root.mkdir(parents=True, exist_ok=True)
    journal = Journal(root, crash)
    try:
        return run_operation(journal, root, release, manifest, plan, report, python, spec_path), plan, report
    finally:
        journal.close()


def run_operation(journal, root, release, manifest, plan, report, python, spec_path):
    identity = manifest["release"]
    release_dir = root / RELEASES / identity
    # The targets are checked again, under the lock, immediately before the
    # operation records them.
    spec, skills, provenance = load_spec(spec_path)
    recheck = [observe(t, skills, provenance) for t in spec["targets"]]
    changed = [new["name"] for old, new in zip(plan["targets"], recheck) if old["digest"] != new["digest"]]
    if changed or any(t["problems"] for t in recheck):
        raise Refused(f"blocked: {', '.join(changed) or 'a target'} changed before staging could record it; "
                      "nothing was staged")
    binding = {"release": identity, "manifest_sha256": plan["manifest_sha256"],
               "destination": str(root), "release_dir": str(release_dir),
               "interpreter": {k: report["interpreter"][k] for k in ("path", "implementation", "version", "system")},
               "targets": {t["name"]: t["digest"] for t in recheck}}
    op_id = "stage-" + digest(binding)[:16]
    ops = operations(journal.records)
    if op_id in ops and ops[op_id]["complete"]:
        try:
            verify_staged(release_dir, binding["manifest_sha256"], manifest, binding["interpreter"])
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
        if os.path.lexists(release_dir):
            raise Refused(f"{shown(release_dir)} already exists and no operation in the journal created it; "
                          "left unchanged")
        journal.append({"op": op_id, "kind": "begin", "binding": binding, "manifest": manifest,
                        "targets": recheck, "importers": plan["importers"],
                        "compatibility": report["results"]})
    else:
        journal.append({"op": op_id, "kind": "resume",
                        "found": {"last": ops[op_id]["last"], "release_dir_exists": release_dir.exists()}})
    attempt = 1 + max([r.get("attempt", 0) for r in journal.records if r["op"] == op_id] or [0])

    def step(name, action):
        journal.append({"op": op_id, "kind": "intent", "step": name, "attempt": attempt})
        try:
            try:
                detail = action()
            except OSError as e:
                raise Refused(f"{e.strerror or e.__class__.__name__}"
                              + (f" ({shown(e.filename)})" if e.filename else "")) from None
        except Refused as e:
            journal.append({"op": op_id, "kind": "outcome", "step": name, "attempt": attempt,
                            "result": "failed", "detail": str(e)})
            raise Refused(f"operation {op_id} is unfinished: {name} failed: {e}") from None
        journal.append({"op": op_id, "kind": "outcome", "step": name, "attempt": attempt, "result": "ok",
                        "detail": detail})

    def create_release_dir():
        (root / RELEASES).mkdir(exist_ok=True)
        if release_dir.exists():
            mark = release_dir / MARKER
            owner = mark.read_text().strip() if mark.is_file() else None
            if owner != op_id and (owner is not None or any(release_dir.iterdir())):
                raise Refused(f"{shown(release_dir)} exists and this operation didn't create it")
        else:
            release_dir.mkdir()
            fsync_dir(root / RELEASES)
        write_atomic(release_dir / MARKER, (op_id + "\n").encode())
        return "created" if not resumed else "present and owned by this operation"

    def copy_artifacts():
        artifacts = release_dir / "artifacts"
        artifacts.mkdir(exist_ok=True)
        wanted = [MANIFEST] + [a["name"] for a in manifest["artifacts"]]
        for stray in sorted(set(os.listdir(artifacts)) - set(wanted)):
            (artifacts / stray).unlink()  # a partial copy this operation left
        for name in wanted:
            data = (release / name).read_bytes()
            write_atomic(artifacts / name, data)
        build_release.verify(artifacts)
        if sha256_file(artifacts / MANIFEST) != binding["manifest_sha256"]:
            raise Refused("the release's manifest changed while it was copied")
        return f"{len(wanted)} files copied and verified"

    env = release_dir / "env"

    def create_environment():
        if os.path.lexists(env):
            shutil.rmtree(env)  # a partial environment this operation left
        r = run([python, "-m", "venv", env], env=child_env(), cwd=str(release_dir))
        if r.returncode:
            raise Refused(f"venv exited {r.returncode}")
        return "venv created with the chosen interpreter and its bundled pip"

    def install_package():
        wheel = release_dir / "artifacts" / manifest["package"]["artifact"]
        r = run([env / "bin" / "python", "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
                 "--disable-pip-version-check", wheel], env=child_env(), cwd=str(release_dir))
        if r.returncode:
            raise Refused(f"pip exited {r.returncode}")
        return f"{manifest['package']['name']} {manifest['package']['version']} installed offline"

    def verify_environment():
        found = verify_staged(release_dir, binding["manifest_sha256"], manifest, binding["interpreter"])
        return f"artifacts verify; plateia-chat {found['dist_version']} loads from the environment on " \
               f"{found['implementation']} {found['version']}"

    def complete():
        write_atomic(release_dir / STAGED, (json.dumps(
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
                build_release.check_output_dir(a.out.parent, CHECKOUT)
                a.out.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
