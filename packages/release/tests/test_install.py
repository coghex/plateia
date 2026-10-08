"""A built release installs into a fresh venv outside any checkout with the
venv's own pip, offline (`--no-index`), and runs from there: `pchat --help`
and `rotate-logs --help` succeed, the bridge's code loads without starting,
weechat's absence is supported, and every shared-chat module loads from the
venv. Nothing loads from this checkout or the invented source repository,
nothing installed puts either on the import path, and pip writes nothing to
the invented home."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import _support
from _support import build_release, make_repo

# Run inside the venv: import or run what is named, then report every loaded
# module's file and the import path, as JSON on the last line of stdout.
PROBE = r"""
import atexit, json, sys
def report():
    files = sorted({m.__file__ for m in list(sys.modules.values()) if getattr(m, "__file__", None)})
    print("\nPROBE " + json.dumps({"files": files + extra, "path": sys.path}))
extra = []
atexit.register(report)
import plateia_chat
what = sys.argv[1]
if what == "bridge":
    bridge = plateia_chat.load("chat-bridge")
    colors = plateia_chat.load("role_colors.py")
    extra += [bridge.__file__, colors.__file__]
    assert callable(bridge.main) and colors.weechat is None
    print("bridge loaded, not started; weechat absent")
else:
    sys.argv = [what, "--help"]
    {"pchat": plateia_chat.pchat, "rotate-logs": plateia_chat.rotate_logs}[what]()
"""


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()
        root = cls.sandbox.root
        cls.repo, _ = make_repo(root)
        cls.release, _ = build_release.build(cls.repo, root / "releases")
        cls.manifest = build_release.verify(cls.release)
        cls.venv = root / "env"
        cls.work = root / "elsewhere"
        cls.work.mkdir()
        cls.chat_home = root / "home"
        cls.env = {"PATH": f"{cls.venv / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(cls.chat_home),
                   "CHAT_CONFIG": str(cls.chat_home / ".config/chat/config.json"),
                   "CHAT_STATE": str(cls.chat_home / ".local/state/chat"), "LC_ALL": "C.UTF-8",
                   "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_NO_INDEX": "1",
                   "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CONFIG_FILE": os.devnull}
        cls.home_before = sorted(p.relative_to(cls.chat_home).as_posix() for p in cls.chat_home.rglob("*"))
        cls.run_ok([sys.executable, "-m", "venv", str(cls.venv)])
        cls.pip = cls.run_ok([str(cls.venv / "bin/python"), "-m", "pip", "install", "--no-index", "--no-deps",
                              str(cls.release / cls.manifest["package"]["artifact"])])

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.close()

    @classmethod
    def run_ok(cls, argv):
        r = subprocess.run(argv, cwd=cls.work, env=cls.env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            raise AssertionError(f"{argv[0]} exited {r.returncode}: {r.stdout}{r.stderr}")
        return r

    def site_packages(self):
        return Path(subprocess.run([str(self.venv / "bin/python"), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                   env=self.env, cwd=self.work, capture_output=True, text=True, check=True).stdout.strip())

    def probe(self, what):
        r = self.run_ok([str(self.venv / "bin/python"), "-c", PROBE, what])
        line = next(l for l in reversed(r.stdout.splitlines()) if l.startswith("PROBE "))
        return r.stdout, json.loads(line[len("PROBE "):])

    def offenders(self, report):
        """Every loaded file or import-path entry inside a checkout, or a
        shared-chat module that did not load from the venv; empty is good."""
        site = os.path.realpath(self.site_packages())
        checkouts = [os.path.realpath(_support.CHECKOUT), os.path.realpath(self.repo)]
        inside = lambda f, root: f == root or f.startswith(root + os.sep)  # noqa: E731
        found = []
        chat = 0
        for f in map(os.path.realpath, report["files"]):
            if any(inside(f, c) for c in checkouts):
                found.append(f"loaded from a checkout: {f}")
            if "plateia_chat" in f or Path(f).stem in ("chatlib", "identities", "binding", "runstore",
                                                         "receipt_experiment", "agentcli"):
                chat += 1
                if not inside(f, site):
                    found.append(f"shared-chat module outside the venv: {f}")
        for entry in report["path"]:
            real = os.path.realpath(entry) if entry else os.path.realpath(self.work)
            if any(inside(real, c) for c in checkouts):
                found.append(f"checkout on the import path: {entry}")
        if not chat:
            found.append("no shared-chat module loaded at all")
        return found

    def assert_nothing_from_a_checkout(self, report):
        self.assertEqual(self.offenders(report), [])

    def test_the_check_catches_a_checkout_import(self):
        scripts = _support.CHECKOUT / "packages/chat/scripts"
        report = {"files": [str(scripts / "chatlib.py")], "path": [str(scripts)]}
        self.assertEqual(len(self.offenders(report)), 3)
        self.assertEqual(self.offenders({"files": [], "path": []}), ["no shared-chat module loaded at all"])

    def test_pchat_help_runs_from_the_venv(self):
        r = self.run_ok([str(self.venv / "bin/pchat"), "--help"])
        self.assertIn("usage: pchat", r.stdout)
        out, report = self.probe("pchat")
        self.assertIn("usage: pchat", out)
        self.assert_nothing_from_a_checkout(report)

    def test_rotate_logs_help_runs_from_the_venv(self):
        r = self.run_ok([str(self.venv / "bin/rotate-logs"), "--help"])
        self.assertIn("usage: rotate-logs", r.stdout)
        out, report = self.probe("rotate-logs")
        self.assertIn("usage: rotate-logs", out)
        self.assert_nothing_from_a_checkout(report)

    def test_the_bridge_loads_from_the_venv_without_starting(self):
        self.assertTrue((self.venv / "bin/chat-bridge").exists())
        out, report = self.probe("bridge")
        self.assertIn("bridge loaded, not started; weechat absent", out)
        self.assert_nothing_from_a_checkout(report)
        self.assertFalse((self.chat_home / ".local/state/chat").exists(), "loading the bridge wrote chat state")

    def test_the_install_is_the_wheel_alone_and_adds_nothing_to_the_import_path(self):
        site = self.site_packages()
        self.assertEqual(sorted(p.name for p in site.glob("*.pth") if "plateia" in p.name or "chat" in p.name), [])
        dist = next(site.glob("plateia_chat-*.dist-info"))
        installed = [row.split(",")[0] for row in (dist / "RECORD").read_text().splitlines() if row]
        for path in installed:
            with self.subTest(path=path):
                self.assertTrue(path.startswith(("plateia_chat/", f"{dist.name}/", "../../../bin/"))
                                or path.startswith("../../bin/"), path)
                self.assertFalse(path.endswith(".pth"), path)
        self.assertFalse((dist / "direct_url.json").exists() and
                         json.loads((dist / "direct_url.json").read_text()).get("dir_info", {}).get("editable"))
        commands = sorted(Path(p).name for p in installed if "/bin/" in p)
        self.assertEqual(commands, ["chat-bridge", "pchat", "rotate-logs"])

    def test_pip_ran_offline_and_wrote_nothing_to_the_home(self):
        self.assertIn("Successfully installed plateia-chat-0.1.0+g", self.pip.stdout)
        after = sorted(p.relative_to(self.chat_home).as_posix() for p in self.chat_home.rglob("*"))
        self.assertEqual(after, self.home_before)

    def test_the_exact_interpreter_is_recorded_for_this_environment(self):
        r = self.run_ok([str(self.venv / "bin/python"), "-c",
                         "import platform, sys; print(platform.python_implementation(), platform.python_version())"])
        implementation, version = r.stdout.split()
        self.assertEqual((implementation, version), (__import__("platform").python_implementation(),
                                                     __import__("platform").python_version()))
        self.assertGreaterEqual(tuple(map(int, version.split(".")[:2])), (3, 10))
        print(f"\n  install environment: {implementation} {version}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
