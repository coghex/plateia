"""Building a release: from a clean invented repository landed on its
origin/master, with every payload byte read from the recorded commit, a
manifest that binds the release's identity, and refusals that write no
release: a dirty tree, a commit not on origin/master, an output directory
inside the checkout or under the live tools, private data, and an existing
identity with different bytes."""
import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import _support
from _support import build_release, commit_file, git, make_repo, snapshot


class BuildCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.close()

    def setUp(self):
        self.where = self.sandbox.root / self.id().rsplit(".", 1)[-1]
        self.where.mkdir()
        self.repo, self.origin = make_repo(self.where)
        self.out = self.where / "releases"

    def build(self, rev="HEAD", out=None):
        return build_release.build(self.repo, out or self.out, rev)

    def refused(self, pattern, rev="HEAD", out=None):
        before = snapshot(self.out)
        with self.assertRaisesRegex(build_release.Refused, pattern):
            self.build(rev, out)
        self.assertEqual(snapshot(self.out), before, "a refused build changed the output directory")


class BuildTests(BuildCase):
    def test_a_clean_landed_commit_builds_and_verifies(self):
        target, new = self.build()
        self.assertTrue(new)
        manifest = build_release.verify(target)
        head = git(self.repo, "rev-parse", "HEAD").strip()
        self.assertEqual(manifest["source"]["commit"], head)
        self.assertEqual(manifest["release"], f"plateia-chat-0.1.0+g{head[:12]}")
        self.assertEqual(target.name, manifest["release"])
        self.assertEqual(sorted(p.name for p in target.iterdir()),
                         sorted([a["name"] for a in manifest["artifacts"]] + ["manifest.json"]))

    def test_the_manifest_binds_versions_interpreter_platforms_and_formats(self):
        manifest = build_release.verify(self.build()[0])
        self.assertEqual(manifest["requires_python"], ">=3.10")
        self.assertEqual(manifest["dependencies"]["interpreter"]["requires"], ">=3.10")
        self.assertEqual(manifest["dependencies"]["python_packages"], [])
        self.assertEqual([(m["name"], m["optional"]) for m in manifest["dependencies"]["host_modules"]],
                         [("weechat", True)])
        self.assertEqual((manifest["platforms"]["family"], manifest["platforms"]["systems"]),
                         ("posix", ["Darwin", "Linux"]))
        self.assertTrue(manifest["build_interpreter"]["implementation"])
        self.assertRegex(manifest["build_interpreter"]["version"], r"^3\.\d+\.\d+")
        self.assertEqual(manifest["package"]["commands"], ["chat-bridge", "pchat", "rotate-logs"])
        self.assertEqual(manifest["skill"]["compatible_packages"], {"plateia-chat": ["0.1.0"]})
        formats = {f["name"]: f for f in manifest["formats"]}
        # identities.register() writes the chat config, so it is declared writable
        self.assertEqual((formats["chat-config"]["read"], formats["chat-config"]["write"]),
                         (["chat-config/1"], "chat-config/1"))
        self.assertNotIn("api_version", json.dumps(manifest))
        for fmt in manifest["formats"]:
            with self.subTest(format=fmt["name"]):
                self.assertIn(fmt["kind"], ("wire", "state"))
                self.assertIsInstance(fmt["read"], list)
                self.assertTrue(fmt["read"] or fmt["write"], "a format the code neither reads nor writes")
        names = {f["name"] for f in manifest["formats"]}
        self.assertTrue({"irc-client", "irc-bridge", "identity-registry", "channel-log", "outbox",
                         "checkpoints", "chat-config", "receipt-experiment-marker"} <= names)
        builder = manifest["builder"]
        self.assertEqual(sorted(builder["files"]), sorted(build_release.BUILDER_FILES))

    def test_every_format_the_shipped_code_uses_is_declared(self):
        """Markers in the shipped code, each tied to the format entry that
        must declare it; a format the code starts using can't go unlisted."""
        markers = {
            r"weechat\.config_(get|set)": ("weechat-role-colors", True, True),
            r"\[\"launchctl\", \"print\"": ("launchd-query", True, False),
            r"\[\"ps\", ": ("process-table", True, False),
            r"signal\.SIGHUP": ("service-signal", False, True),
            r"CHAT_AGENT_ID=": ("agent-run-environment", True, True),
            r"atomic_json\(chatlib\.CONFIG_PATH": ("chat-config", True, True),
            r"CHATHISTORY": ("irc-bridge", True, True),
            r"urlopen": ("ntfy-push", False, True),
            r"\"identities\.json\"|'identities\.json'": ("identity-registry", True, True),
            r"checkpoints\.json": ("checkpoints", True, True),
            r"outbox\.jsonl": ("outbox", True, True),
            r"childrun/2": ("child-runs", True, True),
        }
        manifest = build_release.verify(self.build()[0])
        formats = {f["name"]: f for f in manifest["formats"]}
        code = "\n".join((_support.CHECKOUT / _support.SPEC["package"]["source_dir"] / f).read_text()
                          for f in _support.SPEC["package"]["files"])
        for marker, (name, reads, writes) in markers.items():
            with self.subTest(format=name):
                self.assertRegex(code, marker, "the marker no longer matches the shipped code")
                self.assertIn(name, formats)
                self.assertEqual(bool(formats[name]["read"]), reads)
                self.assertEqual(bool(formats[name]["write"]), writes)

    def test_payload_bytes_come_from_the_recorded_commit(self):
        target, _ = self.build()
        manifest = build_release.verify(target)
        wheel = target / manifest["package"]["artifact"]
        with zipfile.ZipFile(wheel) as z:
            for rel in _support.SPEC["package"]["files"]:
                with self.subTest(file=rel):
                    shipped = z.read(f"plateia_chat/scripts/{rel}")
                    committed = git(self.repo, "show", f"HEAD:packages/chat/scripts/{rel}").encode()
                    self.assertEqual(shipped, committed)
            mode = z.getinfo("plateia_chat/scripts/pchat").external_attr >> 16
            self.assertTrue(mode & 0o111, "pchat lost its executable bit")
        with zipfile.ZipFile(target / manifest["skill"]["artifact"]) as z:
            self.assertEqual(z.read("chat/SKILL.md"), (self.repo / "packages/chat/SKILL.md").read_bytes())

    def test_an_older_landed_commit_builds_from_its_own_bytes(self):
        old = git(self.repo, "rev-parse", "HEAD").strip()
        commit_file(self.repo, "packages/chat/scripts/rotate-logs", "#!/usr/bin/env python3\n# changed later\n")
        target, _ = self.build(old)
        manifest = build_release.verify(target)
        self.assertEqual(manifest["source"]["commit"], old)
        with zipfile.ZipFile(target / manifest["package"]["artifact"]) as z:
            self.assertEqual(z.read("plateia_chat/scripts/rotate-logs"),
                             (_support.CHECKOUT / "packages/chat/scripts/rotate-logs").read_bytes())

    def test_the_same_commit_rebuilds_byte_for_byte_and_changes_nothing(self):
        target, _ = self.build()
        before = snapshot(target)
        again, new = self.build()
        self.assertEqual((again, new), (target, False))
        self.assertEqual(snapshot(target), before)

    def test_the_inbox_core_is_not_released(self):
        commit_file(self.repo, "packages/routine_inbox/inbox.py", "VALUE = 1\n")
        target, _ = self.build()
        for artifact in target.iterdir():
            if artifact.suffix in (".whl", ".zip"):
                with zipfile.ZipFile(artifact) as z:
                    self.assertFalse([n for n in z.namelist() if "routine_inbox" in n or "/tests/" in n])

    def test_the_command_line_reports_and_exits(self):
        err, out = io.StringIO(), io.StringIO()
        from contextlib import redirect_stderr, redirect_stdout
        with redirect_stdout(out):
            self.assertEqual(build_release.main(["build", "--source", str(self.repo), "--out", str(self.out)]), 0)
        self.assertIn("built: plateia-chat-0.1.0+g", out.getvalue())
        (self.repo / "stray.txt").write_text("x\n")
        with redirect_stderr(err):
            self.assertEqual(build_release.main(["build", "--source", str(self.repo), "--out", str(self.out)]), 1)
        self.assertIn("refused: the source working tree is dirty", err.getvalue())


class RefusalTests(BuildCase):
    def test_a_dirty_tree_is_refused(self):
        (self.repo / "packages/chat/scripts/chatlib.py").write_text("# edited\n")
        self.refused(r"working tree is dirty \(1 path\(s\): packages/chat/scripts/chatlib.py\)")
        git(self.repo, "checkout", "--", "packages/chat/scripts/chatlib.py")
        (self.repo / "untracked.txt").write_text("x\n")
        self.refused(r"working tree is dirty \(1 path\(s\): untracked.txt\)")

    def test_a_commit_not_on_origin_master_is_refused(self):
        commit_file(self.repo, "notes.txt", "local only\n", push=False)
        self.refused(r"commit [0-9a-f]{12} is not reachable from origin/master")

    def test_a_source_without_origin_master_is_refused(self):
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/master")
        self.refused(r"no origin/master")

    def test_a_revision_that_is_not_a_commit_is_refused(self):
        self.refused(r"not a commit", rev="no-such-branch")

    def test_an_output_directory_inside_the_checkout_is_refused(self):
        self.refused(r"inside the source checkout", out=self.repo / "releases")
        self.assertFalse((self.repo / "releases").exists())

    def test_an_outside_looking_path_resolving_inside_the_checkout_is_refused(self):
        (self.where / "elsewhere").symlink_to(self.repo / "packages")
        self.refused(r"inside the source checkout", out=self.where / "elsewhere" / "out")
        self.assertFalse((self.repo / "packages" / "out").exists())

    def test_an_output_directory_under_the_live_tools_is_refused(self):
        for rel in build_release.LIVE_ROOTS:
            with self.subTest(root=rel):
                self.refused(r"where the live chat tools live", out=Path.home() / rel / "releases")
                self.assertFalse((Path.home() / rel).exists())

    def test_a_symlinked_live_location_is_refused_either_way(self):
        real = self.where / "skills-elsewhere"
        real.mkdir()
        link = Path.home() / ".codex" / "skills"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real)
        self.addCleanup(link.parent.rmdir)
        self.addCleanup(link.unlink)
        for out in (link / "releases", real / "releases"):
            with self.subTest(out=out):
                self.refused(r"under ~/.codex/skills, where the live chat tools live", out=out)
        self.assertEqual(list(real.iterdir()), [])

    def test_configured_chat_state_and_config_locations_are_refused(self):
        state, config = self.where / "chat-state", self.where / "chat-config"
        state.mkdir()
        config.mkdir()
        (self.where / "state-link").symlink_to(state)
        with mock.patch.dict(os.environ, {"CHAT_STATE": str(state), "CHAT_CONFIG": str(config / "config.json")}):
            for out, name in ((state / "releases", "CHAT_STATE"), (self.where / "state-link" / "r", "CHAT_STATE"),
                              (config / "releases", "CHAT_CONFIG's directory")):
                with self.subTest(out=out):
                    self.refused(rf"under {name}, where the live chat tools live", out=out)
        self.assertEqual((list(state.iterdir()), list(config.iterdir())), ([], []))

    def test_private_data_in_the_payload_is_refused(self):
        for text, why in (("PATH = '/Users/realperson/work'\n", "a home-directory path"),
                          ("TOKEN = 'ghp_" + "a" * 36 + "'\n", "a credential pattern")):
            with self.subTest(why=why):
                commit_file(self.repo, "packages/chat/scripts/runstore.py", text)
                self.refused(rf"would carry private data: plateia_chat/scripts/runstore.py: {why}")

    def test_an_existing_identity_is_never_rebound(self):
        target, _ = self.build()
        manifest = json.loads((target / "manifest.json").read_text())
        wheel = target / manifest["package"]["artifact"]
        wheel.write_bytes(wheel.read_bytes() + b"\0")
        before = snapshot(target)
        self.refused(r"already exists with different contents")
        self.assertEqual(snapshot(target), before, "the existing release was changed")
        (target / "extra.txt").write_text("x\n")
        self.refused(r"already exists with different contents")

    def test_a_refused_build_leaves_no_partial_release(self):
        (self.repo / "stray.txt").write_text("x\n")
        self.refused(r"dirty")
        self.assertFalse(self.out.exists())
        os.remove(self.repo / "stray.txt")
        self.build()
        self.assertEqual([p.name for p in self.out.iterdir() if p.name.startswith(".")], [])


if __name__ == "__main__":
    unittest.main()
