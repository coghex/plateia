"""Shared fixtures for the release tests: an invented plateia repository that
carries the actual captured chat code, an `origin` it has landed on, and a
git environment that ignores the machine's own git configuration. Nothing
here touches the network, the real home directory or this checkout."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
CHECKOUT = RELEASE.parents[1]
sys.path.insert(0, str(RELEASE))
import build_release  # noqa: E402

SPEC = json.loads((RELEASE / "release.json").read_text(encoding="utf-8"))
PAYLOAD = ([f"{SPEC['package']['source_dir']}/{f}" for f in SPEC["package"]["files"]]
           + SPEC["package"]["metadata_files"] + [SPEC["skill"]["source"]])

_ENV_KEYS = ("HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
             "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")


class Sandbox:
    """An invented home and git identity for the duration of a test class."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="plateia-release-")
        self.root = Path(os.path.realpath(self.tmp.name))
        self.home = self.root / "home"
        self.home.mkdir()
        self.saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        os.environ.update(HOME=str(self.home), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                          GIT_AUTHOR_NAME="Pat Example", GIT_AUTHOR_EMAIL="pat@example.invalid",
                          GIT_COMMITTER_NAME="Pat Example", GIT_COMMITTER_EMAIL="pat@example.invalid",
                          GIT_AUTHOR_DATE="2026-10-01T12:00:00Z", GIT_COMMITTER_DATE="2026-10-01T12:00:00Z")

    def close(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_repo(where, name="plateia"):
    """A clean invented repository holding the captured runtime code, landed on
    its origin's master. Returns (checkout, origin)."""
    origin = where / f"{name}-origin.git"
    repo = where / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    for rel in PAYLOAD:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CHECKOUT / rel, target)  # contents only: a read-only checkout stays editable here
        target.chmod(0o755 if os.access(CHECKOUT / rel, os.X_OK) else 0o644)
    (repo / "README.md").write_text("An invented plateia checkout for the release tests.\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "invented capture")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "master")
    git(repo, "fetch", "-q", "origin")
    return repo, origin


def commit_file(repo, rel, text, push=True):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    git(repo, "add", rel)
    git(repo, "commit", "-q", "-m", f"change {rel}")
    if push:
        git(repo, "push", "-q", "origin", "master")
        git(repo, "fetch", "-q", "origin")
    return git(repo, "rev-parse", "HEAD").strip()


def snapshot(root):
    """Every file's bytes and modification time under root."""
    root = Path(root)
    if not root.exists():
        return None
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def rezip(data, replace):
    """A copy of zip bytes with some members' contents replaced."""
    import io
    members = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            members[name] = (replace.get(name, z.read(name)), bool((z.getinfo(name).external_attr >> 16) & 0o111))
    return build_release.zip_bytes(members)
