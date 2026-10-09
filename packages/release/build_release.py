#!/usr/bin/env python3
"""Build and verify immutable releases of plateia's shared chat code (PLT-11).

  python3 packages/release/build_release.py build --source REPO [--commit REV] --out DIR
  python3 packages/release/build_release.py verify RELEASE_DIR

`build` packages one commit of a plateia repository: a wheel for the shared
chat code, a skill archive carrying its SKILL.md, and manifest.json binding
them. Every payload byte is read from that commit with git, never from a
working tree, so the manifest's source commit is exactly what ships. The
packaging glue (this file, the entry points, release.json) is the builder's
own and is identified separately in the manifest.

It refuses, writing no release, when the source working tree is dirty, when
the commit is not reachable from the source's `origin/master` (checked
offline against the local ref, which only advances by landing on master),
when the output directory is inside the source checkout (after resolving
symlinks) or under the live chat tools' locations, when an artifact would
carry private data, or when a release of the same identity already exists
with different bytes. Rebuilding a release byte-for-byte is accepted and
changes nothing.

`verify` checks a release directory against its manifest and never writes
to it: it refuses, naming the failing input, a missing or extra artifact,
an artifact whose bytes or metadata differ from the manifest, an unpinned
manifest (no interpreter requirement, no source commit), and a skill whose
declared compatible package versions exclude the release's package.

Exit status: 0 success, 1 refused, 2 bad usage. Standard library only; no
network access.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC_PATH = HERE / "release.json"
INIT_PATH = HERE / "plateia_chat_init.py"
BUILDER_FILES = ("build_release.py", "plateia_chat_init.py", "release.json")
MANIFEST = "manifest.json"
SCHEMA = "plateia-release-manifest/1"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # fixed, so the same commit always builds the same bytes
SHA = re.compile(r"^[0-9a-f]{40}$")

# Where the live chat tools keep code, settings, state, hooks and services:
# no release is ever written there (requirement 8). Relative to the home
# directory: the broad roots (including the agents' own settings, hooks and
# plugin caches under ~/.codex and ~/.claude), then each concrete runtime location inside or beside them,
# because each may be a symlink to somewhere else and is resolved on its
# own. The chat code's CHAT_STATE and CHAT_CONFIG are added at run time.
LIVE_ROOTS = (".codex", ".claude", ".codex/skills", ".config/chat", ".config/cmux", ".local/state", ".local/bin",
              "Library/LaunchAgents",
              ".codex/skills/chat", ".codex/skills/chat/scripts", ".local/state/chat",
              ".local/state/project-manager", ".local/state/ergo", ".local/share/weechat", ".config/weechat",
              ".weechat")
WALK_LIMIT = 1_000_000  # entries checked for symlinks before the guard gives up (and refuses)

# Patterns no artifact may carry (requirement 5). Invented placeholders that
# the captured code uses in docstrings and examples are allowed.
HOME_PATH = re.compile(r"/(?:Users|home)/(?!someone\b)[A-Za-z0-9._-]+")
CREDENTIALS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"""(?i)["']?(?:password|passwd|secret|token)["']?\s*[:=]\s*["'](?![<{$])[^"'\s]{6,}["']"""),
    re.compile(r"https://ntfy\.sh/(?!<)[A-Za-z0-9_-]+"),
]
STATE_NAMES = re.compile(r"(?:^|/)(?:[^/]+\.jsonl|identities\.json|checkpoints\.json|pending\.json|config\.json|"
                         r"manager\.json|outbox[^/]*|bridge\.log|[^/]*\.enabled)$")


class Refused(Exception):
    """The build or verification refused; the message names the cause."""


# --- git, read-only --------------------------------------------------------

def git(repo, *args, binary=False):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env)
    if r.returncode:
        raise Refused(f"git {' '.join(args[:2])} failed: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout if binary else r.stdout.decode()


def source_state(source, rev):
    """(checkout root, commit) after the clean-tree and reachability checks."""
    try:
        top = Path(git(source, "rev-parse", "--show-toplevel").strip())
    except Refused:
        raise Refused(f"the source {source} is not a git checkout") from None
    try:
        commit = git(top, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()
    except Refused:
        commit = ""
    if not SHA.match(commit):
        raise Refused(f"the source revision {rev!r} is not a commit")
    dirty = git(top, "status", "--porcelain=v1", "--untracked-files=normal").splitlines()
    if dirty:
        paths = ", ".join(line[3:] for line in dirty[:5]) + (", ..." if len(dirty) > 5 else "")
        raise Refused(f"the source working tree is dirty ({len(dirty)} path(s): {paths}); "
                      "commit or remove the changes first")
    r = subprocess.run(["git", "-C", str(top), "rev-parse", "--verify", "--quiet", "refs/remotes/origin/master"],
                       capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if r.returncode:
        raise Refused("the source has no origin/master to check the commit against")
    if subprocess.run(["git", "-C", str(top), "merge-base", "--is-ancestor", commit, "refs/remotes/origin/master"],
                      capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).returncode:
        raise Refused(f"commit {commit[:12]} is not reachable from origin/master; "
                      "only code that has landed on master is released")
    return top, commit


def read_blob(top, commit, path):
    """(bytes, executable) of `path` at `commit`."""
    entry = git(top, "ls-tree", commit, "--", path).split()
    if len(entry) < 4 or entry[1] != "blob":
        raise Refused(f"commit {commit[:12]} has no file {path}")
    return git(top, "cat-file", "blob", entry[2], binary=True), entry[0] == "100755"


def builder_identity():
    files = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in BUILDER_FILES}
    revision, dirty = None, None
    try:
        root = Path(git(HERE, "rev-parse", "--show-toplevel").strip())
        revision = git(root, "rev-parse", "HEAD").strip()
        dirty = bool(git(root, "status", "--porcelain=v1", "--", str(HERE.relative_to(root))).strip())
    except (Refused, ValueError, OSError):
        pass
    return {"revision": revision, "dirty": dirty, "files": files}


# --- artifacts ---------------------------------------------------------------

def sha256(data):
    return hashlib.sha256(data).hexdigest()


def zip_bytes(members):
    """A deterministic zip of {name: (bytes, executable)}, in name order."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(members):
            data, executable = members[name]
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o100755 if executable else 0o100644) << 16)
            info.create_system = 3
            z.writestr(info, data)
    return out.getvalue()


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def build_wheel(spec, version, payload):
    pkg = spec["package"]
    dist = f"{pkg['import_name']}-{version}"
    info = f"{dist}.dist-info"
    members = {f"{pkg['import_name']}/__init__.py": (INIT_PATH.read_bytes(), False)}
    for name, (data, executable) in payload.items():
        members[name] = (data, executable)
    members[f"{info}/METADATA"] = ("\n".join([
        "Metadata-Version: 2.1", f"Name: {pkg['name']}", f"Version: {version}", f"Summary: {pkg['summary']}",
        f"Requires-Python: {spec['requires_python']}", "Platform: POSIX", ""]).encode(), False)
    members[f"{info}/WHEEL"] = ("\n".join([
        "Wheel-Version: 1.0", "Generator: plateia build_release", "Root-Is-Purelib: true",
        "Tag: py3-none-any", ""]).encode(), False)
    members[f"{info}/entry_points.txt"] = ("[console_scripts]\n" + "".join(
        f"{command} = {pkg['import_name']}:{function}\n"
        for command, function in sorted(pkg["commands"].items()))).encode(), False
    rows = io.StringIO()
    writer = csv.writer(rows, lineterminator="\n")
    for name in sorted(members):
        writer.writerow([name, record_hash(members[name][0]), len(members[name][0])])
    writer.writerow([f"{info}/RECORD", "", ""])
    members[f"{info}/RECORD"] = (rows.getvalue().encode(), False)
    return f"{dist}-py3-none-any.whl", zip_bytes(members)


def skill_declaration(spec, version):
    skill = spec["skill"]
    return {"schema": "plateia-skill/1", "name": skill["name"], "version": version,
            "compatible_packages": skill["compatible_packages"]}


def build_skill(spec, version, skill_md, declaration):
    name = spec["skill"]["name"]
    members = {f"{name}/SKILL.md": (skill_md, False),
               f"{name}/skill.json": ((json.dumps(declaration, indent=2, sort_keys=True) + "\n").encode(), False)}
    return f"plateia-skill-{name}-{version}.zip", zip_bytes(members)


def private_findings(name, data):
    """What in one artifact looks private: (member, reason) pairs."""
    found = []
    members = {}
    if name.endswith((".whl", ".zip")):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for member in z.namelist():
                members[member] = z.read(member)
    else:
        members[name] = data
    for member, body in members.items():
        if STATE_NAMES.search(member):
            found.append((member, "a chat-state or configuration file"))
        text = body.decode("utf-8", errors="replace")
        if HOME_PATH.search(text):
            found.append((member, "a home-directory path"))
        if any(p.search(text) for p in CREDENTIALS):
            found.append((member, "a credential pattern"))
    return found


def public_version(version):
    return version.split("+", 1)[0]


# --- build -------------------------------------------------------------------

def live_roots():
    """(name, path) for every live location. Each named location counts, and
    so does the target of every symlink anywhere inside one (a directory's
    target, or a file's target directory, as for ~/.local/bin/pchat or a
    project's manager.json): the trees are walked without following links.
    A tree that can't be read completely, or is too large to walk, is a
    refusal rather than a guess."""
    home = Path.home()
    roots = [(f"~/{rel}", home / rel) for rel in LIVE_ROOTS]
    if os.environ.get("CHAT_STATE"):
        roots.append(("CHAT_STATE", Path(os.environ["CHAT_STATE"])))
    if os.environ.get("CHAT_CONFIG"):
        roots.append(("CHAT_CONFIG's directory", Path(os.environ["CHAT_CONFIG"]).parent))
    linked, walked, seen = [], set(), 0
    # Every root, then every protected link target (a directory, or a file's
    # directory), is walked once: links inside a target count too. `walked`
    # holds resolved paths, so a cycle of links ends.
    pending = [(name, os.path.realpath(root)) for name, root in roots]
    while pending:
        name, real = pending.pop(0)
        if not os.path.isdir(real) or any(real == w or real.startswith(w + os.sep) for w in walked):
            continue
        walked.add(real)

        def unreadable(error, name=name):
            raise Refused(f"cannot check {name} for symlinks ({error.strerror}); "
                          "refusing rather than risk writing into a live location")
        for folder, dirs, files in os.walk(real, onerror=unreadable):
            for entry in dirs + files:
                seen += 1
                if seen > WALK_LIMIT:
                    raise Refused(f"the live locations hold more than {WALK_LIMIT} entries to check for "
                                  "symlinks; refusing rather than risk writing into a live location")
                path = os.path.join(folder, entry)
                if os.path.islink(path):
                    target = Path(os.path.realpath(path))
                    label = f"{name}/{os.path.relpath(path, real)}'s target"
                    protected = target if target.is_dir() else target.parent
                    linked.append((label, protected))
                    pending.append((label, str(protected)))
    return roots + linked


def check_output_dir(out, top, names=()):
    """The output directory, resolved, after refusing one inside the source
    checkout or under a live location. Each directory the build would write
    (the output directory, and `names` inside it: the release and its
    staging directory) is checked. Both sides of every comparison are
    resolved, and the unresolved spellings are checked too, so neither a
    symlinked output path nor a symlinked live location gets through."""
    given = Path(os.path.abspath(out))
    out = Path(os.path.realpath(out))
    inside = lambda path, root: path == root or root in path.parents  # noqa: E731
    top_real = Path(os.path.realpath(top))
    if inside(out, top_real) or inside(given, Path(os.path.abspath(top))):
        raise Refused("the output directory is inside the source checkout (after resolving symlinks)")
    for name, root in live_roots():
        for what, extra in [("the output directory", None)] + [(f"the release directory {n}", n) for n in names]:
            for path, base in ((out, Path(os.path.realpath(root))), (given, Path(os.path.abspath(root)))):
                if inside(path / extra if extra else path, base):
                    raise Refused(f"{what} is under {name}, where the live chat tools live")
    return out


def assemble(top, commit, spec):
    """{file name: bytes} for the release, plus its identity."""
    version = f"{spec['package']['version']}+g{commit[:12]}"
    skill_version = f"{spec['skill']['version']}+g{commit[:12]}"
    pkg = spec["package"]
    payload = {}
    for name in pkg["files"]:
        payload[f"{pkg['import_name']}/scripts/{name}"] = read_blob(top, commit, f"{pkg['source_dir']}/{name}")
    for path in pkg["metadata_files"]:
        payload[f"{pkg['import_name']}/{Path(path).name}"] = read_blob(top, commit, path)
    wheel_name, wheel = build_wheel(spec, version, payload)
    declaration = skill_declaration(spec, skill_version)
    skill_name, skill = build_skill(spec, skill_version, read_blob(top, commit, spec["skill"]["source"])[0],
                                    declaration)
    files = {wheel_name: wheel, skill_name: skill}
    for name, data in files.items():
        findings = private_findings(name, data)
        if findings:
            raise Refused(f"artifact {name} would carry private data: "
                          + "; ".join(f"{m}: {why}" for m, why in findings[:5]))
    identity = f"{pkg['name']}-{version}"
    manifest = {
        "schema": SCHEMA,
        "release": identity,
        "source": {"repository": "coghex/plateia", "commit": commit, "eligibility": "reachable from origin/master"},
        "builder": builder_identity(),
        "build_interpreter": {"implementation": platform.python_implementation(),
                              "version": platform.python_version()},
        "requires_python": spec["requires_python"],
        "platforms": spec["platforms"],
        "dependencies": spec["dependencies"],
        "package": {"name": pkg["name"], "version": version, "artifact": wheel_name,
                    "commands": sorted(pkg["commands"])},
        "skill": {"name": spec["skill"]["name"], "version": skill_version, "artifact": skill_name,
                  "compatible_packages": spec["skill"]["compatible_packages"]},
        "formats": spec["formats"],
        "artifacts": [{"name": n, "sha256": sha256(d), "size": len(d)} for n, d in sorted(files.items())],
    }
    files[MANIFEST] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return identity, files


def build(source, out, rev="HEAD", spec_path=SPEC_PATH):
    """Build one release; returns (release directory, whether it was new)."""
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    top, commit = source_state(source, rev)
    identity, files = assemble(top, commit, spec)  # writes nothing
    staging_name = f".{identity}.partial-{os.getpid()}"
    # The release and staging directories are checked too: a live location
    # can sit inside an allowed output directory, not only above it.
    out = check_output_dir(out, top, (identity, staging_name))
    target = out / identity
    if target.exists() or target.is_symlink():
        if not target.is_dir() or target.is_symlink():
            raise Refused(f"{identity} exists and is not a release directory; left unchanged")
        present = {p.name for p in target.iterdir()}
        if present != set(files) or any((target / n).read_bytes() != d for n, d in files.items()):
            raise Refused(f"release {identity} already exists with different contents; "
                          "a release identity is never rebound, and the existing one was left unchanged")
        return target, False
    out.mkdir(parents=True, exist_ok=True)
    staging = out / staging_name
    staging.mkdir()
    try:
        for name, data in files.items():
            (staging / name).write_bytes(data)
        verify(staging)
        os.rename(staging, target)
    except BaseException:
        for p in staging.iterdir():
            p.unlink()
        staging.rmdir()
        raise
    return target, True


# --- verify ------------------------------------------------------------------

def verify(release):
    """Check a release directory against its manifest; returns the manifest."""
    release = Path(release)
    try:
        manifest = json.loads((release / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refused(f"{MANIFEST} is missing") from None
    except (OSError, ValueError) as e:
        raise Refused(f"{MANIFEST} is unreadable: {e}") from None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        raise Refused(f"{MANIFEST} is not a {SCHEMA} manifest")
    commit = (manifest.get("source") or {}).get("commit") if isinstance(manifest.get("source"), dict) else None
    if not isinstance(commit, str) or not SHA.match(commit):
        raise Refused(f"{MANIFEST} pins no source commit (source.commit)")
    requires = manifest.get("requires_python")
    interpreter = (manifest.get("dependencies") or {}).get("interpreter") \
        if isinstance(manifest.get("dependencies"), dict) else None
    if not isinstance(requires, str) or not requires.strip() \
            or not isinstance(interpreter, dict) or not interpreter.get("requires"):
        raise Refused(f"{MANIFEST} pins no interpreter requirement (requires_python, dependencies.interpreter)")
    for field in ("release", "package", "skill", "artifacts", "platforms", "formats"):
        if not manifest.get(field):
            raise Refused(f"{MANIFEST} has no {field}")

    recorded = {}
    for entry in manifest["artifacts"]:
        if not (isinstance(entry, dict) and isinstance(entry.get("name"), str) and "/" not in entry["name"]
                and isinstance(entry.get("sha256"), str)):
            raise Refused(f"{MANIFEST} has a malformed artifact entry: {entry!r}")
        recorded[entry["name"]] = entry
    present = {p.name for p in release.iterdir()} - {MANIFEST}
    for name in sorted(set(recorded) - present):
        raise Refused(f"artifact {name} is missing")
    for name in sorted(present - set(recorded)):
        raise Refused(f"{name} is not an artifact the manifest names")
    data = {}
    for name, entry in sorted(recorded.items()):
        data[name] = (release / name).read_bytes()
        found = sha256(data[name])
        if found != entry["sha256"] or len(data[name]) != entry.get("size"):
            raise Refused(f"artifact {name} does not match the manifest "
                          f"(sha256 {entry['sha256'][:12]} recorded, {found[:12]} found)")

    pkg, skill = manifest["package"], manifest["skill"]
    for what, entry in (("package", pkg), ("skill", skill)):
        if not isinstance(entry, dict) or entry.get("artifact") not in recorded or not entry.get("version"):
            raise Refused(f"{MANIFEST}'s {what} names no artifact or version")
    metadata = wheel_metadata(pkg["artifact"], data[pkg["artifact"]])
    if (metadata.get("Name"), metadata.get("Version")) != (pkg.get("name"), pkg["version"]):
        raise Refused(f"artifact {pkg['artifact']} is {metadata.get('Name')} {metadata.get('Version')}, "
                      f"but the manifest says {pkg.get('name')} {pkg['version']}")
    if metadata.get("Requires-Python") != requires:
        raise Refused(f"artifact {pkg['artifact']} requires Python {metadata.get('Requires-Python')}, "
                      f"but the manifest says {requires}")
    declaration = skill_metadata(skill["artifact"], data[skill["artifact"]], skill.get("name"))
    if (declaration["name"], declaration["version"]) != (skill.get("name"), skill["version"]):
        raise Refused(f"artifact {skill['artifact']} declares skill {declaration['name']} {declaration['version']}, "
                      f"but the manifest says {skill.get('name')} {skill['version']}")
    if declaration["compatible_packages"] != skill.get("compatible_packages"):
        raise Refused(f"artifact {skill['artifact']}'s compatibility declaration differs from the manifest's")
    accepted = declaration["compatible_packages"].get(pkg["name"], [])
    if public_version(pkg["version"]) not in accepted:
        raise Refused(f"skill {declaration['name']} {declaration['version']} is compatible with "
                      f"{pkg['name']} {', '.join(accepted) or 'no version'}, "
                      f"but the release carries {pkg['name']} {pkg['version']}")
    return manifest


def wheel_metadata(name, data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            records = [m for m in z.namelist() if m.endswith(".dist-info/RECORD")]
            if len(records) != 1:
                raise Refused(f"artifact {name} is not a wheel with one RECORD")
            for row in csv.reader(io.StringIO(z.read(records[0]).decode())):
                if row and row[1]:
                    if record_hash(z.read(row[0])) != row[1]:
                        raise Refused(f"artifact {name}: {row[0]} does not match the wheel's RECORD")
            text = z.read(records[0].replace("RECORD", "METADATA")).decode()
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as e:
        raise Refused(f"artifact {name} is not a readable wheel: {e}") from None
    return dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)


def skill_metadata(name, data, skill_name):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.read(f"{skill_name}/SKILL.md")
            declaration = json.loads(z.read(f"{skill_name}/skill.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise Refused(f"artifact {name} has no readable SKILL.md and skill.json: {e}") from None
    ok = (isinstance(declaration, dict) and isinstance(declaration.get("name"), str)
          and isinstance(declaration.get("version"), str)
          and isinstance(declaration.get("compatible_packages"), dict) and declaration["compatible_packages"]
          and all(isinstance(v, list) and v and all(isinstance(x, str) for x in v)
                  for v in declaration["compatible_packages"].values()))
    if not ok:
        raise Refused(f"artifact {name}'s skill.json has no valid name, version and compatible_packages")
    return declaration


# --- command line ------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_release", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build one release from a commit")
    b.add_argument("--source", required=True, type=Path, help="a plateia checkout")
    b.add_argument("--commit", default="HEAD", help="the source revision (default HEAD)")
    b.add_argument("--out", required=True, type=Path, help="output directory, outside the source checkout")
    v = sub.add_parser("verify", help="verify a release directory")
    v.add_argument("release", type=Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "build":
            target, new = build(a.source, a.out, a.commit)
            manifest = verify(target)
            print(f"{'built' if new else 'already built, identical'}: {manifest['release']} "
                  f"from {manifest['source']['commit'][:12]} ({len(manifest['artifacts'])} artifacts)")
        else:
            manifest = verify(a.release)
            print(f"verified: {manifest['release']} from {manifest['source']['commit'][:12]}; "
                  f"{len(manifest['artifacts'])} artifacts match the manifest")
    except Refused as e:
        print(f"build_release: refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
