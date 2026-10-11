"""Building a release: from a clean invented repository landed on its
origin/master, with every payload byte read from the recorded commit, a
manifest that binds the release's identity, and refusals that write no
release: a dirty tree, a commit not on origin/master, an output directory
inside the checkout or under the live tools, private data, and an existing
identity with different bytes."""
import io
import json
import os
import re
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
            r"\['lsof', .*'-Ffn'\]": ("open-files", True, False),
            r"line\.startswith\('f'\)": ("open-files", True, False),
            r"signal\.SIGHUP": ("service-signal", False, True),
            r"CHAT_AGENT_ID=": ("agent-run-environment", True, True),
            r"atomic_json\(chatlib\.CONFIG_PATH": ("chat-config", True, True),
            r"CHATHISTORY": ("irc-bridge", True, True),
            r"urlopen": ("ntfy-push", False, True),
            r"\"identities\.json\"|'identities\.json'": ("identity-registry", True, True),
            r"checkpoints\.json": ("checkpoints", True, True),
            r"outbox\.jsonl": ("outbox", True, True),
            r"childrun/2": ("child-runs", True, True),
            # #19: the outbox authority, its fallback files, versioned dead letters and two host probes
            r"outbox\.db": ("outbox-db", True, True),
            r"outbox-authority/1": ("outbox-authority", True, True),
            r"\"outbox/2\"": ("outbox", True, True),
            r"\"supersedes\"": ("delivery-records", True, True),
            r"kern\.bootsessionuuid|random/boot_id": ("boot-id", True, False),
            r"-iTCP": ("tcp-sockets", True, False),
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

    def test_the_versions_the_shipped_code_embeds_are_the_declared_ones(self):
        """#19: outbox/2's fallback file, the outbox-authority frame and the
        outbox-db meta row each carry their version, and declare where."""
        manifest = build_release.verify(self.build()[0])
        formats = {f["name"]: f for f in manifest["formats"]}
        code = (_support.CHECKOUT / "packages/chat/scripts/delivery_store.py").read_text()
        for name, constant, field in (("outbox", 'FALLBACK_FORMAT = "outbox/2"', "v"),
                                      ("outbox-authority", 'PROTOCOL = "outbox-authority/1"', "v"),
                                      ("outbox-db", 'DB_FORMAT = "outbox-db/1"', "meta.schema")):
            with self.subTest(format=name):
                self.assertIn(constant, code)
                self.assertTrue(formats[name]["embedded_version"])
                self.assertEqual(formats[name]["field"], field)
                self.assertEqual(formats[name]["write"], constant.split('"')[1])
        self.assertEqual(formats["outbox"]["read"], ["outbox/1", "outbox/2"])
        self.assertIn("sqlite3", manifest["dependencies"]["interpreter"]["note"])

    def test_every_runtime_module_passes_the_unchanged_privacy_scan(self):
        """#19: a module named like chat state would make the build refuse the
        wheel; the new store is named so it does not, and the scan is not changed."""
        for f in _support.SPEC["package"]["files"]:
            with self.subTest(file=f):
                self.assertIsNone(build_release.STATE_NAMES.search(f"plateia_chat/scripts/{f}"))
        self.assertIsNotNone(build_release.STATE_NAMES.search("plateia_chat/scripts/outbox_store.py"),
                             "a negative control: a name starting with outbox would be refused")
        self.assertIn("delivery_store.py", _support.SPEC["package"]["files"])


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
                self.refused(r"under ~/\.codex(/skills)?, where the live chat tools live", out=out)
        self.assertEqual(list(real.iterdir()), [])

    def test_resolved_default_runtime_locations_are_refused(self):
        """Each default location is resolved on its own, and so is a symlink
        directly inside one; the destinations stay unchanged."""
        home = Path.home()
        live_state, live_scripts, live_logs = (self.where / n for n in ("live-state", "live-scripts", "live-logs"))
        for d in (live_state, live_scripts, live_logs):
            d.mkdir()
        (live_scripts / "pchat").write_text("#!/bin/sh\n")
        links = [(home / ".local/state/chat", live_state), (home / ".local/bin/pchat", live_scripts / "pchat"),
                 (home / ".local/state/ergo", live_logs)]
        # cleanups run last-first: the links go before their directories
        self.addCleanup(lambda: [p.rmdir() for p in (home / ".local/state", home / ".local/bin", home / ".local")])
        for link, target in links:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            self.addCleanup(link.unlink)
        before = {d: snapshot(d) for d in (live_state, live_scripts, live_logs)}
        for out, name in ((live_state / "releases", r"~/.local/state/chat"),
                          (live_scripts / "releases", r"~/.local/bin/pchat's target"),
                          (live_logs / "releases", r"~/.local/state/ergo")):
            with self.subTest(out=out.name, under=name):
                self.refused(rf"under {name}", out=out)
        self.assertEqual({d: snapshot(d) for d in before}, before)

    def test_nested_runtime_symlink_targets_are_refused(self):
        """A symlink deep inside a runtime tree protects its target too."""
        home = Path.home()
        external = self.where / "external-state"
        external.mkdir()
        (external / "manager.json").write_text("{}\n")
        deep = self.where / "external-logs"
        deep.mkdir()
        self.addCleanup(lambda: [p.rmdir() for p in (
            home / ".local/state/project-manager/alpha", home / ".local/state/project-manager",
            home / ".local/state/chat/logs", home / ".local/state/chat", home / ".local/state", home / ".local")])
        links = [(home / ".local/state/project-manager/alpha/manager.json", external / "manager.json"),
                 (home / ".local/state/chat/logs/archive", deep)]
        for link, target in links:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            self.addCleanup(link.unlink)
        before = {d: snapshot(d) for d in (external, deep)}
        for out, name in ((external / "releases", r"~/.local/state/project-manager/alpha/manager.json's target"),
                          (deep / "releases", r"~/.local/state/chat/logs/archive's target")):
            with self.subTest(out=out):
                self.refused(rf"under {name}", out=out)
        self.assertEqual({d: snapshot(d) for d in before}, before)

    def test_links_inside_a_linked_directory_and_link_cycles_are_handled(self):
        """A symlinked project directory's own links count, and a cycle of
        directory links ends the walk instead of looping."""
        home = Path.home()
        project, live = self.where / "project-state", self.where / "live-state"
        project.mkdir()
        live.mkdir()
        (live / "manager.json").write_text("{}\n")
        (project / "manager.json").symlink_to(live / "manager.json")
        (project / "loop").symlink_to(project)  # a cycle back to itself
        pm = home / ".local/state/project-manager"
        pm.mkdir(parents=True)
        self.addCleanup(lambda: [p.rmdir() for p in (pm, pm.parent, pm.parent.parent)])
        (pm / "alpha").symlink_to(project)
        self.addCleanup((pm / "alpha").unlink)
        before = snapshot(live)
        self.refused(r"under ~/.local/state/project-manager/alpha's target/manager.json's target",
                     out=live / "releases")
        self.assertEqual(snapshot(live), before)

    def test_links_inside_a_linked_files_directory_are_handled(self):
        """A file link's target directory is walked too: a command linked
        into a scripts directory whose module links elsewhere protects that
        module's directory."""
        home = Path.home()
        scripts, modules = self.where / "runtime-scripts", self.where / "runtime-modules"
        scripts.mkdir()
        modules.mkdir()
        (scripts / "pchat").write_text("#!/usr/bin/env python3\n")
        (modules / "chatlib.py").write_text("# invented\n")
        (scripts / "chatlib.py").symlink_to(modules / "chatlib.py")
        bin_dir = home / ".local/bin"
        bin_dir.mkdir(parents=True)
        self.addCleanup(lambda: [p.rmdir() for p in (bin_dir, bin_dir.parent)])
        (bin_dir / "pchat").symlink_to(scripts / "pchat")
        self.addCleanup((bin_dir / "pchat").unlink)
        before = snapshot(modules)
        self.refused(r"under ~/.local/bin/pchat's target/chatlib.py's target", out=modules / "releases")
        self.assertEqual(snapshot(modules), before)

    def test_a_live_location_inside_the_output_directory_is_refused(self):
        """The release and staging directories the build would create are
        checked, not only --out: configured chat state can be one of them."""
        head = git(self.repo, "rev-parse", "HEAD").strip()
        identity = f"plateia-chat-0.1.0+g{head[:12]}"
        for name in (identity, f".{identity}.partial-{os.getpid()}"):
            with self.subTest(directory=name):
                state = self.out / name
                with mock.patch.dict(os.environ, {"CHAT_STATE": str(state)}):
                    self.refused(rf"the release directory {re.escape(name)} is under CHAT_STATE")
                self.assertFalse(state.exists())
                self.assertIsNone(snapshot(self.out))

    def test_adversarial_symlink_variants_are_refused(self):
        """A chain of links, a relative link, a hook under ~/.claude, and an
        output path that is itself a link to a protected target."""
        home = Path.home()
        chained, relative, hooks = (self.where / n for n in ("chained-live", "relative-live", "hook-live"))
        for d in (chained, relative, hooks):
            d.mkdir()
        (hooks / "on-start.sh").write_text("#!/bin/sh\n")
        hop = self.where / "hop"
        hop.symlink_to(chained)
        state = home / ".local/state/chat"
        state.mkdir(parents=True)
        (home / ".claude/hooks").mkdir(parents=True)
        self.addCleanup(lambda: [p.rmdir() for p in (home / ".claude/hooks", home / ".claude", state,
                                                       state.parent, state.parent.parent)])
        links = [(state / "chain", hop), (state / "relative", Path(os.path.relpath(relative, state))),
                 (home / ".claude/hooks/on-start.sh", hooks / "on-start.sh")]
        for link, target in links:
            link.symlink_to(target)
            self.addCleanup(link.unlink)
        (self.where / "innocent-looking").symlink_to(relative)
        before = {d: snapshot(d) for d in (chained, relative, hooks)}
        for out, name in ((chained / "releases", r"~/.local/state/chat/chain's target"),
                          (relative / "releases", r"~/.local/state/chat/relative's target"),
                          (self.where / "innocent-looking" / "releases", r"~/.local/state/chat/relative's target"),
                          (hooks / "releases", r"~/.claude/hooks/on-start.sh's target")):
            with self.subTest(out=out):
                self.refused(rf"under {name}", out=out)
        self.assertEqual({d: snapshot(d) for d in before}, before)

    @unittest.skipIf(os.geteuid() == 0, "root reads unreadable directories")
    def test_an_unreadable_runtime_tree_is_refused(self):
        locked = Path.home() / ".local/state/chat/locked"
        locked.mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(lambda: [p.rmdir() for p in (locked, locked.parent, locked.parent.parent,
                                                       locked.parent.parent.parent)])
        self.addCleanup(locked.chmod, 0o755)
        self.refused(r"cannot check ~/.local/state for symlinks .*refusing rather than risk")

    def test_too_many_entries_to_check_is_refused(self):
        tree = Path.home() / ".config/chat"
        tree.mkdir(parents=True)
        for n in range(5):
            (tree / f"f{n}").write_text("x\n")
        self.addCleanup(lambda: [p.unlink() for p in tree.iterdir()] and None or
                        [tree.rmdir(), tree.parent.rmdir()])
        with mock.patch.object(build_release, "WALK_LIMIT", 3):
            self.refused(r"more than 3 entries to check for symlinks")

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


# The declarative content of release.json that #19 leaves exactly as it was:
# everything but package.files, the interpreter note and the outbox,
# delivery-records, outbox-db, outbox-authority, boot-id and tcp-sockets
# format entries (the second to fifth amendments of #19, acceptance 11).
UNCHANGED_DECLARATIVE = "f2895cb0c6145ded6b845e214ab21cc06ae5f68426d73cb71bfecadb66602f81"
CHANGED_BY_19 = {"outbox", "delivery-records", "outbox-db", "outbox-authority", "boot-id", "tcp-sockets"}
GENERATED = ("release", "source", "builder", "build_interpreter", "artifacts")


class UnchangedTests(BuildCase):
    """Acceptance 11, as corrected: release.json's and the built manifest's
    declarative content is unchanged apart from what #19 declares; the fields
    the builder generates from the commit, the payload and the build
    environment are not compared."""

    @staticmethod
    def declarative(spec):
        spec = json.loads(json.dumps(spec))
        spec["package"].pop("files")
        spec["dependencies"]["interpreter"].pop("note")
        spec["formats"] = [f for f in spec["formats"] if f["name"] not in CHANGED_BY_19]
        return spec

    def test_release_json_is_unchanged_apart_from_19s_declarations(self):
        import hashlib
        digest = hashlib.sha256(json.dumps(self.declarative(_support.SPEC), sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, UNCHANGED_DECLARATIVE)

    def test_any_other_declarative_change_is_caught(self):
        import hashlib
        spec = json.loads(json.dumps(_support.SPEC))
        next(f for f in spec["formats"] if f["name"] == "checkpoints")["write"] = "checkpoints/2"
        digest = hashlib.sha256(json.dumps(self.declarative(spec), sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(digest, UNCHANGED_DECLARATIVE)

    def test_manifests_of_two_commits_differ_only_in_generated_fields(self):
        def stripped(manifest):
            manifest = {k: v for k, v in manifest.items() if k not in GENERATED}
            manifest["package"] = {k: v for k, v in manifest["package"].items() if k not in ("version", "artifact")}
            manifest["skill"] = {k: v for k, v in manifest["skill"].items() if k not in ("version", "artifact")}
            return manifest
        first = build_release.verify(self.build()[0])
        commit_file(self.repo, "packages/chat/scripts/rotate-logs", "#!/usr/bin/env python3\n# a later commit\n")
        second = build_release.verify(self.build()[0])
        self.assertNotEqual(first["source"]["commit"], second["source"]["commit"])
        self.assertEqual(stripped(first), stripped(second))


if __name__ == "__main__":
    unittest.main()
