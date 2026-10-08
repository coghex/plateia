#!/usr/bin/env python3
"""Report drift between the skills repository's working tree and the baseline
plateia captured the shared chat code from (docs/plateia_design.md D-19, D-23).

  python3 packages/chat/drift_check.py [--provenance FILE] [--json] SKILLS_TREE

SKILLS_TREE is the skills repository's root. The check looks only under the
provenance's monitored directory, skipping its listed exclusions, and reports
every captured file that changed, went missing or changed type, mode or symlink
target, and every other path that was added. It compares symlink targets
without following them.

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
        if (not isinstance(base, dict) or not isinstance(entry.get("baseline_path"), str)
                or not entry["baseline_path"].startswith(prefix)
                or base.get("type") not in ("file", "symlink")
                or (base["type"] == "file" and not (isinstance(base.get("sha256"), str)
                                                    and base.get("mode") in ("100644", "100755")))
                or (base["type"] == "symlink" and not isinstance(base.get("target"), str))):
            raise BadProvenance(f"malformed captured entry: {entry!r}")
    return data


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


def check(tree: Path, provenance: dict) -> dict:
    tree = Path(tree)
    root_rel = provenance["monitored"]["root"].strip("/")
    patterns = [e["pattern"] for e in provenance["monitored"].get("exclude", [])]
    monitored = tree / root_rel
    drift, errors = [], []
    result = {"baseline": provenance.get("baseline"), "monitored": root_rel,
              "captured": len(provenance["captured"]), "drift": drift, "errors": errors}
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
        base, path = entry["baseline"], tree / rel
        try:
            st = path.lstat()
        except FileNotFoundError:
            drift.append({"path": rel, "kind": "missing", "detail": f"baseline {base['type']} is gone"})
            continue
        except OSError as e:
            errors.append({"path": rel, "error": e.strerror or str(e)})
            continue
        try:
            if base["type"] == "symlink":
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
            digest = sha256(path)
        except OSError as e:
            errors.append({"path": rel, "error": e.strerror or str(e)})
            continue
        if digest != base["sha256"]:
            carried = entry.get("effective_sha256") not in (None, base["sha256"]) and digest == entry["effective_sha256"]
            source = str(entry.get("source_commit") or "")[:7]
            drift.append({"path": rel, "kind": "changed",
                          "detail": (f"matches carried commit {source}".rstrip() if carried
                                     else f"sha256 {base['sha256'][:12]} -> {digest[:12]}")})
        if mode_of(st) != base["mode"]:
            drift.append({"path": rel, "kind": "mode changed", "detail": f"{base['mode']} -> {mode_of(st)}"})

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
