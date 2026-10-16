#!/usr/bin/env python3
"""Report drift between the skills repository's working tree and the baseline
plateia captured the shared chat code from (docs/plateia_design.md D-19, D-23).

  python3 packages/chat/drift_check.py [--provenance FILE] [--json] SKILLS_TREE

SKILLS_TREE is the skills repository's root. The check looks only under the
provenance's monitored directory, skipping its listed exclusions, and reports
every captured file that changed, went missing or changed type, mode or symlink
target, and every other path that was added. It compares symlink targets
without following them.

A captured entry may also record a `working_copy`: the bytes of a file that was
uncommitted in the skills repository over a named HEAD when it was captured
(docs/plateia_design.md D-19, the owner's 2026-10-14 exception for #31). Its
sha256 is kept apart from the baseline's and from the sanitized file's. An entry
with no baseline at all (a file the baseline never had) must record one. A live
file that matches the recorded working copy is reported as `working copy`, never
as a carried commit; any other bytes, even the baseline's or a carried commit's,
are reported as changed from it, naming its hash.

It is read-only: it lists, stats, reads and resolves links, never writes to
the tree, and reports differences instead of fixing them. Paths are printed
relative to SKILLS_TREE.

Exit status: 0 no drift, 1 drift, 2 the check could not be completed (a
missing tree, an unreadable input or a bad provenance file). An error is
never reported as "no drift".
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath

PROVENANCE = Path(__file__).resolve().parent / "provenance.json"


class BadProvenance(ValueError):
    pass


def load_provenance(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BadProvenance(f"cannot read provenance {path}: {e}") from None
    monitored = data.get("monitored") if isinstance(data, dict) else None
    if not isinstance(monitored, dict) or not isinstance(monitored.get("root"), str) or not monitored["root"]:
        raise BadProvenance("provenance has no monitored root")
    if not all(isinstance(e, dict) and isinstance(e.get("pattern"), str) for e in monitored.get("exclude", [])):
        raise BadProvenance("provenance exclusions must each name a pattern")
    captured = data.get("captured")
    if not isinstance(captured, list) or not captured:
        raise BadProvenance("provenance lists no captured files")
    prefix = monitored["root"].rstrip("/") + "/"
    for entry in captured:
        base = entry.get("baseline") if isinstance(entry, dict) else None
        working = entry.get("working_copy") if isinstance(entry, dict) else None
        if (not isinstance(entry.get("baseline_path"), str) or not entry["baseline_path"].startswith(prefix)
                or (working is not None and not _working_copy_ok(working))
                or (base is None and working is None)  # nothing to hold the file to
                or (base is not None and (not isinstance(base, dict)
                                          or base.get("type") not in ("file", "symlink")
                                          or (base["type"] == "file" and not (isinstance(base.get("sha256"), str)
                                                                              and base.get("mode") in MODES))
                                          or (base["type"] == "symlink" and not isinstance(base.get("target"), str))))
                or (base is not None and base["type"] == "symlink" and working is not None)):
            raise BadProvenance(f"malformed captured entry: {entry!r}")
    return data


MODES = ("100644", "100755")


def _working_copy_ok(working) -> bool:
    return (isinstance(working, dict) and isinstance(working.get("head"), str)
            and len(working["head"]) == 40 and all(c in "0123456789abcdef" for c in working["head"])
            and isinstance(working.get("sha256"), str) and len(working["sha256"]) == 64
            and all(c in "0123456789abcdef" for c in working["sha256"])
            and working.get("mode") in MODES)


def excluded(rel: str, patterns: list[str]) -> bool:
    """A pattern with no slash matches any path segment (as .gitignore does);
    one with a slash matches the path, or a directory above it, from the
    monitored root."""
    parts = PurePosixPath(rel).parts
    for pattern in patterns:
        if "/" in pattern:
            p = pattern.strip("/")
            if any(fnmatch.fnmatchcase("/".join(parts[:i]), p) for i in range(1, len(parts) + 1)):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def mode_of(st: os.stat_result) -> str:
    return "100755" if st.st_mode & stat.S_IXUSR else "100644"


def describe(path: Path, st: os.stat_result) -> str:
    if stat.S_ISLNK(st.st_mode):
        return f"symlink to {os.readlink(path)}"
    return "directory" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "special file"


def check(tree: Path, provenance: dict, hasher=None) -> dict:
    """The drift report for `tree`. `hasher(path, rel)`, when given, hashes
    each captured file in place of sha256(path), so a caller can refuse to
    read a file (raising OSError) that this check would otherwise open."""
    tree = Path(tree)
    root_rel = provenance["monitored"]["root"].strip("/")
    patterns = [e["pattern"] for e in provenance["monitored"].get("exclude", [])]
    monitored = tree / root_rel
    drift, errors = [], []
    result = {"baseline": provenance.get("baseline"), "monitored": root_rel,
              "captured": len(provenance["captured"]), "drift": drift, "errors": errors}
    heads = sorted({e["working_copy"]["head"] for e in provenance["captured"] if e.get("working_copy")})
    if heads:
        result["working_copy_heads"] = heads
    if not tree.is_dir():
        errors.append({"path": ".", "error": "the skills tree is not a directory"})
        return result
    if not monitored.is_dir() or monitored.is_symlink():
        errors.append({"path": root_rel, "error": "the monitored directory is missing or not a directory"})
        return result

    expected = {}
    for entry in provenance["captured"]:
        expected[entry["baseline_path"]] = entry
    for rel, entry in sorted(expected.items()):
        base, working, path = entry["baseline"], entry.get("working_copy"), tree / rel
        gone = f"baseline {base['type']} is gone" if base else "recorded working-copy file is gone"
        try:
            st = path.lstat()
        except FileNotFoundError:
            drift.append({"path": rel, "kind": "missing", "detail": gone})
            continue
        except OSError as e:
            errors.append({"path": rel, "error": e.strerror or str(e)})
            continue
        try:
            if base and base["type"] == "symlink":
                if not stat.S_ISLNK(st.st_mode):
                    drift.append({"path": rel, "kind": "type changed",
                                  "detail": f"symlink to {base['target']} -> {describe(path, st)}"})
                elif os.readlink(path) != base["target"]:
                    drift.append({"path": rel, "kind": "symlink target changed",
                                  "detail": f"{base['target']} -> {os.readlink(path)}"})
                continue
            if not stat.S_ISREG(st.st_mode):
                drift.append({"path": rel, "kind": "type changed", "detail": f"file -> {describe(path, st)}"})
                continue
            digest = sha256(path) if hasher is None else hasher(path, rel)
        except OSError as e:
            errors.append({"path": rel, "error": e.strerror or str(e)})
            continue
        held = working is not None and digest == working["sha256"]
        if working is not None:
            # A recorded working copy is the only content that explains this file. Equal to it, it is reported
            # as such (nothing at all when it also equals the baseline); anything else, including the baseline's
            # or a carried commit's bytes, is a change from it and is never "no drift" or a carried match.
            if held and (base is None or digest != base["sha256"]):
                drift.append({"path": rel, "kind": "working copy", "source_sha256": working["sha256"],
                              "detail": f"matches recorded working copy over HEAD {working['head'][:7]} "
                                        f"(sha256 {digest[:12]})"})
            elif not held:
                drift.append({"path": rel, "kind": "changed", "source_sha256": working["sha256"],
                              "detail": f"sha256 {working['sha256'][:12]} -> {digest[:12]}; differs from the "
                                        f"recorded working copy over HEAD {working['head'][:7]}"})
        elif digest != base["sha256"]:
            carried = (entry.get("effective_sha256") not in (None, base["sha256"])
                       and digest == entry["effective_sha256"])
            source = str(entry.get("source_commit") or "")[:7]
            drift.append({"path": rel, "kind": "changed",
                          "detail": (f"matches carried commit {source}".rstrip() if carried
                                     else f"sha256 {base['sha256'][:12]} -> {digest[:12]}")})
        want = working["mode"] if held or base is None else base["mode"]
        if mode_of(st) != want:
            drift.append({"path": rel, "kind": "mode changed", "detail": f"{want} -> {mode_of(st)}"})

    pending = [monitored]
    while pending:
        top = pending.pop()
        try:
            with os.scandir(top) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError as e:
            errors.append({"path": Path(top).relative_to(tree).as_posix(), "error": e.strerror or str(e)})
            continue
        for item in entries:
            path = Path(item.path)
            if excluded(path.relative_to(monitored).as_posix(), patterns):
                continue
            rel = path.relative_to(tree).as_posix()
            try:
                is_dir = item.is_dir(follow_symlinks=False)
            except OSError as e:
                errors.append({"path": rel, "error": e.strerror or str(e)})
                continue
            if is_dir:
                pending.append(path)
            elif rel not in expected:
                try:
                    what = describe(path, path.lstat())
                except OSError as e:
                    errors.append({"path": rel, "error": e.strerror or str(e)})
                    continue
                drift.append({"path": rel, "kind": "added", "detail": what})
    drift.sort(key=lambda d: (d["path"], d["kind"]))
    errors.sort(key=lambda e: e["path"])
    return result


def render(result: dict) -> str:
    lines = [f"drift check: {result['captured']} captured files under {result['monitored']}/ "
             f"against baseline {str(result['baseline'])[:12]}"]
    for head in result.get("working_copy_heads", []):
        lines.append(f"  working-copy sources are uncommitted bytes over skills HEAD {head[:12]}")
    lines += [f"  {d['kind']:22} {d['path']}  ({d['detail']})" for d in result["drift"]]
    lines += [f"  error                  {e['path']}  ({e['error']})" for e in result["errors"]]
    if result["errors"]:
        lines.append("incomplete: the check could not read everything; this is not a no-drift result")
    elif result["drift"]:
        lines.append(f"drift: {len(result['drift'])} difference(s) from the baseline; nothing was changed")
    else:
        lines.append("no drift: every captured file matches the baseline and nothing was added")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="drift_check", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree", type=Path, help="the skills repository's root")
    ap.add_argument("--provenance", type=Path, default=PROVENANCE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        provenance = load_provenance(a.provenance)
    except BadProvenance as e:
        print(f"drift_check: {e}", file=sys.stderr)
        return 2
    result = check(a.tree, provenance)
    print(json.dumps(result, indent=2) if a.json else render(result))
    return 2 if result["errors"] else 1 if result["drift"] else 0


if __name__ == "__main__":
    sys.exit(main())
