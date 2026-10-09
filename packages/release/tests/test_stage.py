"""Staging (PLT-12) on invented homes only: plan, preflight and stage against
fixtures holding every managed target, with fake service and identity
commands. Staging never runs against the real home directory: each test
points HOME, CHAT_STATE and CHAT_CONFIG at its own invented home."""
import contextlib
import fcntl
import io
import json
import os
import plistlib
import re
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

import _staging
import _support
from _support import build_release, make_repo

import stage_release  # noqa: E402  (on the path through _support)
from stage_release import Refused


class StageCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()
        repo, _ = make_repo(cls.sandbox.root)
        cls.release, _ = build_release.build(repo, cls.sandbox.root / "releases")
        cls.manifest = build_release.verify(cls.release)

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.close()

    def setUp(self):
        self.where = self.sandbox.root / self.id().rsplit(".", 1)[-1]
        self.home = self.where / "home"
        self.home.mkdir(parents=True)
        self.skills = _staging.make_home(self.home)
        self.spec = _staging.spec_for(_staging.provenance_for(self.skills, self.where / "provenance.json"),
                                      self.where / "staging.json")
        self.fake_bin, self.fake_log = _staging.fake_commands(self.where)
        env = {"HOME": str(self.home), "PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("CHAT_STATE", "CHAT_CONFIG", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            if key in os.environ:
                saved = os.environ.pop(key)
                self.addCleanup(os.environ.__setitem__, key, saved)
        self.dest = self.where / "staging"
        stage_release.CALLS.clear()
        self.before = _staging.snapshot_tree(self.home)

    def stage(self, release=None, dest=None, python=None, plan=None, crash=None):
        return stage_release.stage(release or self.release, dest or self.dest, python, plan, self.spec, crash)

    def plan(self, release=None):
        return stage_release.make_plan(release or self.release, self.dest, self.spec)

    def preflight(self, release=None, python=None):
        return stage_release.preflight(release or self.release, python, self.spec)

    def assertHomeUnchanged(self):
        self.assertEqual(_staging.snapshot_tree(self.home), self.before,
                         "a managed target, setting, state, queue or service definition changed")

    def assertNothingStaged(self, pattern, **kw):
        with self.assertRaisesRegex(Refused, pattern):
            self.stage(**kw)
        self.assertFalse(os.path.lexists(self.dest), "a blocked run created the staging destination")
        self.assertHomeUnchanged()

    def assertNoServiceOrIdentityCall(self):
        self.assertFalse(self.fake_log.exists(), "a fake service or identity command was run")
        for call in stage_release.CALLS:
            with self.subTest(call=call[:3]):
                self.assertNotRegex(" ".join(call), r"launchctl|systemctl|install-identities|merge-worker|"
                                                    r"drain|bootstrap|kickstart")
                head = Path(call[0])
                staged_python = head.name == "python" and head.parent.parent.name == "env"
                self.assertTrue(staged_python or os.path.realpath(head) == os.path.realpath(sys.executable),
                                f"unexpected command {call[0]}")

    def release_copy(self):
        copy = self.where / "release-copy" / self.release.name
        if copy.parent.exists():
            shutil.rmtree(copy.parent)
        shutil.copytree(self.release, copy)
        return copy

    def rewrite_manifest(self, release, change):
        path = release / "manifest.json"
        manifest = json.loads(path.read_text())
        change(manifest)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    def fake_python(self, implementation="CPython", version="3.12.4", system=None):
        system = system or __import__("platform").system()
        report = json.dumps({"implementation": implementation, "version": version, "system": system,
                             "prefix": "/invented"})
        return _staging.write(self.where / f"fake-python-{implementation}-{version}-{system}",
                              f"#!/bin/sh\necho '{report}'\n", 0o755)

    def crash_at(self, n):
        seen = []

        def hook(record):
            seen.append(record)
            if len(seen) == n:
                raise stage_release.Crash(f"crash after record {n}")
        return hook

    def crash_when(self, predicate):
        def hook(record):
            if predicate(record):
                raise stage_release.Crash("stopped")
        return hook

    def journal(self):
        return [json.loads(line) for line in (self.dest / "journal.jsonl").read_text().splitlines()]

    def writer(self):
        """A writer on the destination that knows what the journal's operation created."""
        writer = stage_release.Writer(self.dest, build_release.live_roots())
        self.addCleanup(writer.close)
        writer.load(self.journal(), self.journal()[1]["op"])
        return writer


class HappyPathTests(StageCase):
    def test_a_full_stage_plans_stages_verifies_and_changes_nothing_live(self):
        summary, plan, report = self.stage()
        self.assertEqual(summary["state"], "staged")
        names = [t["name"] for t in plan["targets"]]
        self.assertEqual(names, ["pchat", "chat-bridge LaunchAgent", "rotate-logs LaunchAgent",
                                 "WeeChat role_colors autoload link", "chat skill folder", "Claude chat skill link",
                                 "chat/scripts import location"])
        self.assertTrue(all(t["status"] == "ok" and t["observed"]["type"] != "missing" for t in plan["targets"]))
        self.assertEqual([(k["name"], k["found"]) for k in plan["importers"]["known"]],
                         [(n, True) for n in ("launch-worker", "childrun", "report", "reconcile", "modelclass")])
        self.assertEqual(plan["importers"]["unlisted"], [])
        self.assertTrue(plan["importers"]["complete"])
        release_dir = self.dest / "releases" / self.manifest["release"]
        self.assertEqual(build_release.verify(release_dir / "artifacts")["release"], self.manifest["release"])
        self.assertTrue((release_dir / "env/bin/pchat").exists())
        staged = json.loads((release_dir / "staged.json").read_text())
        self.assertEqual((staged["release"], staged["selected"]), (self.manifest["release"], False))
        records = self.journal()
        self.assertEqual(records[0]["kind"], "journal")
        self.assertEqual({r["op"] for r in records[1:]}, {summary["operation"]})
        records = records[1:]
        self.assertEqual(records[0]["kind"], "begin")
        steps = [(r["kind"], r["step"]) for r in records[1:] if r["kind"] in ("intent", "outcome")]
        self.assertEqual(steps, [(k, s) for s in stage_release.STEPS for k in ("intent", "outcome")])
        self.assertTrue(all(r["result"] == "ok" for r in records if r["kind"] == "outcome"))
        self.assertEqual(records[0]["manifest"]["release"], self.manifest["release"])
        self.assertEqual(len(records[0]["targets"]), 7)
        self.assertEqual(stage_release.status(self.dest)[0]["state"], "complete")
        self.assertHomeUnchanged()
        self.assertNoServiceOrIdentityCall()

    def test_the_plan_records_type_link_hash_ownership_and_replacement(self):
        plan = self.plan()
        by = {t["name"]: t for t in plan["targets"]}
        self.assertEqual(by["pchat"]["observed"]["target"], "~/.codex/skills/chat/scripts/pchat")
        self.assertRegex(by["pchat"]["observed"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(by["chat-bridge LaunchAgent"]["observed"]["label"], "com.coghex.chat-bridge")
        self.assertEqual(by["Claude chat skill link"]["observed"]["target"], "~/.codex/skills/chat")
        self.assertEqual(len(by["chat skill folder"]["observed"]["files"]), len(_staging.CAPTURED))
        for t in plan["targets"]:
            with self.subTest(target=t["name"]):
                self.assertTrue(t["path"].startswith("~/"))
                self.assertTrue(t["ownership"] and t["proposed"] and t["expected"])
        self.assertHomeUnchanged()

    def test_repeating_a_completed_stage_verifies_and_starts_nothing_new(self):
        first, _, _ = self.stage()
        count = len(self.journal())
        again, _, _ = self.stage()
        self.assertEqual((again["operation"], again["state"]), (first["operation"], "already staged, verified"))
        self.assertEqual(len(self.journal()), count)
        self.assertEqual(os.listdir(self.dest / "releases"), [self.manifest["release"]])

    def test_a_changed_environment_is_refused_before_any_of_its_code_runs(self):
        """On a completed retry, a modified initializer, an added startup
        hook (.pth or sitecustomize), a replaced command, a cleared executable bit or an edited
        pyvenv.cfg is refused by the static check, before the environment's
        interpreter runs: the registry the planted code would write stays
        byte-identical."""
        self.stage()
        env = self.dest / "releases" / self.manifest["release"] / "env"
        site = next(env.glob("lib/python*/site-packages"))
        registry = self.home / ".local/state/chat/identities.json"
        payload = f"open({str(registry)!r}, 'w').write('overwritten')\n"
        init, pth, pchat = site / "plateia_chat/__init__.py", site / "zz-hook.pth", env / "bin/pchat"
        custom = site / "sitecustomize.py"
        cases = ((lambda: init.write_text(init.read_text() + payload), "lib/python"),
                 (lambda: pth.write_text("import os; " + payload), "lib/python"),
                 (lambda: custom.write_text(payload), "lib/python"),
                 (lambda: pchat.write_text(pchat.read_text() + payload), "bin/pchat"),
                 (lambda: pchat.chmod(0o644), "bin/pchat"),
                 (lambda: (env / "pyvenv.cfg").write_text("home = /invented\n"), "pyvenv.cfg"))
        for damage, where in cases:
            with self.subTest(changed=where):
                saved = {p: (p.read_bytes(), p.stat().st_mode) for p in (init, pchat, env / "pyvenv.cfg")}
                stage_release.CALLS.clear()
                damage()
                try:
                    with self.assertRaisesRegex(Refused, rf"doesn't verify \(the staged environment differs from "
                                                         rf"what this operation built \({re.escape(where)}"):
                        self.stage()
                    self.assertEqual(stage_release.CALLS[1:], [], "the environment's code ran")  # only the probe
                finally:
                    for p, (data, mode) in saved.items():
                        p.write_bytes(data)
                        p.chmod(mode)
                    pth.unlink(missing_ok=True)
                    custom.unlink(missing_ok=True)
                self.assertHomeUnchanged()
        self.assertEqual(self.stage()[0]["state"], "already staged, verified")

    def test_the_installed_payload_and_commands_must_match_the_wheel(self):
        """The checks behind the inventory: with the inventory re-recorded to
        match a change, the installed files are still compared with the
        verified wheel and RECORD, and each command with its entry point."""
        self.stage()
        release_dir = self.dest / "releases" / self.manifest["release"]
        env = release_dir / "env"
        site = next(env.glob("lib/python*/site-packages"))
        pchat, chatlib = env / "bin/pchat", site / "plateia_chat/scripts/chatlib.py"
        lines = pchat.read_text().splitlines()
        launcher = lines[:3] if lines[0] == "#!/bin/sh" else lines[:1]  # pip's long-path launcher is 3 lines
        cases = ((lambda: pchat.write_text("\n".join(launcher) + "\nraise RuntimeError('replaced')\n"),
                  r"the staged pchat command doesn't call the wheel's entry point"),
                 (lambda: pchat.write_text(pchat.read_text() + "import os\n"),
                  r"the installed \.\./\.\./\.\./bin/pchat no longer matches the package's RECORD"),
                 (lambda: chatlib.write_text(chatlib.read_text() + "# edited\n"),
                  r"the installed plateia_chat/scripts/chatlib\.py differs from the verified wheel"),
                 (lambda: pchat.chmod(0o644), r"the staged pchat command is not executable"),
                 (lambda: (site / "plateia_chat/sitecustomize.py").write_text("x = 1\n"),
                  r"holds a file its RECORD doesn't list \(plateia_chat/sitecustomize\.py\)"))
        for damage, pattern in cases:
            with self.subTest(case=pattern[:30]):
                saved = {p: (p.read_bytes(), p.stat().st_mode) for p in (pchat, chatlib)}
                damage()
                with self.writer().pinned(f"releases/{self.manifest['release']}/env") as fd:
                    found = stage_release.inventory_at(fd)
                data = (json.dumps(found, indent=1, sort_keys=True) + "\n").encode()
                (release_dir / "env-inventory.json").write_bytes(data)
                try:
                    with self.assertRaisesRegex(Refused, pattern):
                        stage_release.verify_static(self.writer(), f"releases/{self.manifest['release']}",
                                                    self.journal()[1]["binding"]["manifest_sha256"], self.manifest,
                                                    __import__("hashlib").sha256(data).hexdigest())
                finally:
                    for p, (content, mode) in saved.items():
                        p.write_bytes(content)
                        p.chmod(mode)
                    (site / "plateia_chat/sitecustomize.py").unlink(missing_ok=True)

    def test_a_completed_stage_whose_environment_was_damaged_is_not_reported_staged(self):
        self.stage()
        (self.dest / "releases" / self.manifest["release"] / "env/bin/pchat").unlink()
        with self.assertRaisesRegex(Refused, r"recorded complete, but its staged release doesn't verify "
                                             r"\(the staged environment differs from what this operation built "
                                             r"\(bin/pchat\)\)"):
            self.stage()

    def test_both_console_script_forms_name_their_interpreter(self):
        """A shebang, or the /bin/sh launcher pip writes when the path is too
        long for one (as on Linux runners)."""
        env_python = "/invented/staging/releases/r/env/bin/python"
        self.assertEqual(stage_release.interpreter_of([f"#!{env_python}", "import sys"]), env_python)
        self.assertEqual(stage_release.interpreter_of(
            ["#!/bin/sh", f"'''exec' \"{env_python}\" \"$0\" \"$@\"", "' '''", "import sys"]), env_python)
        self.assertEqual(stage_release.interpreter_of(  # pip quotes the path only when it holds a space
            ["#!/bin/sh", f"'''exec' {env_python} \"$0\" \"$@\"", "' '''", "import sys"]), env_python)
        for lines in (["import sys"], ["#!/bin/sh", "exec python3"], []):
            with self.subTest(lines=lines):
                self.assertIsNone(stage_release.interpreter_of(lines))

    def test_the_api_check_is_not_applicable_never_a_pass(self):
        report = self.preflight()
        self.assertIn(["not applicable", "api",
                       "the manifest declares no API version (D-23: it joins from PLT-10)"], report["results"])
        self.assertFalse(any(r[1] == "api" and r[0] == "pass" for r in report["results"]))
        declared = self.release_copy()
        self.rewrite_manifest(declared, lambda m: m.update(api_version="chat-api/1"))
        self.assertIn("api: the manifest declares an API version, and no consumer compatibility matrix exists "
                      "yet to check it against", self.preflight(declared)["refused"])


class BlockingTests(StageCase):
    def test_unknown_ownership_blocks(self):
        plist = self.home / "Library/LaunchAgents/com.coghex.chat-bridge.plist"
        _staging.plist(plist, "org.example.someone-else", self.skills / "chat/scripts/chat-bridge")
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"chat-bridge LaunchAgent: unknown ownership: its Label is not "
                                 r"com\.coghex\.chat-bridge")

    def test_a_regular_file_where_a_link_belongs_blocks(self):
        pchat = self.home / ".local/bin/pchat"
        pchat.unlink()
        _staging.write(pchat, "#!/bin/sh\n", 0o755)
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"pchat: unknown ownership: a regular file, not the expected symlink")

    def test_an_unknown_file_in_the_skill_folder_blocks(self):
        _staging.write(self.skills / "chat/scripts/local_patch.py", "# a private patch\n")
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"chat skill folder: unknown ownership: an entry the provenance doesn't list: "
                                 r"chat/scripts/local_patch\.py")

    def test_a_changed_target_blocks(self):
        with open(self.skills / "chat/scripts/chatlib.py", "a") as stream:
            stream.write("# a local edit\n")
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"chat skill folder: changed content \(changed\): chat/scripts/chatlib\.py")

    def test_a_retargeted_link_blocks(self):
        pchat = self.home / ".local/bin/pchat"
        pchat.unlink()
        _staging.write(self.where / "elsewhere/pchat", "#!/bin/sh\n", 0o755)
        pchat.symlink_to(self.where / "elsewhere/pchat")
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"pchat: retargeted link: it points to .*/elsewhere/pchat, not "
                                 r"~/\.codex/skills/chat/scripts/pchat")

    def test_a_launchagent_must_run_the_script_itself(self):
        script = self.skills / "chat/scripts/chat-bridge"
        path = self.home / "Library/LaunchAgents/com.coghex.chat-bridge.plist"
        cases = (({"ProgramArguments": ["/bin/echo", str(script)]}, "echo before the script"),
                 ({"Program": "/bin/echo"}, "a Program override"),
                 ({"ProgramArguments": ["/usr/bin/python3", str(script), "--replay-all"]}, "an extra argument"),
                 ({"ProgramArguments": ["/usr/bin/python3", "-c", str(script)]}, "the script as code"))
        for extra, why in cases:
            with self.subTest(why=why):
                _staging.plist(path, "com.coghex.chat-bridge", script, extra)
                self.before = _staging.snapshot_tree(self.home)
                self.assertNothingStaged(r"chat-bridge LaunchAgent: retargeted program: it doesn't run "
                                         r"~/\.codex/skills/chat/scripts/chat-bridge")
        _staging.plist(path, "com.coghex.chat-bridge", script, {"ProgramArguments": [str(script)]})
        by = {t["name"]: t for t in self.plan()["targets"]}
        self.assertEqual((by["chat-bridge LaunchAgent"]["status"], by["chat-bridge LaunchAgent"]["observed"]
                          ["invocation"]), ("ok", "direct"))

    def test_a_retargeted_launchagent_program_blocks(self):
        _staging.plist(self.home / "Library/LaunchAgents/com.coghex.log-rotate.plist", "com.coghex.log-rotate",
                       self.where / "elsewhere/rotate-logs")
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"rotate-logs LaunchAgent: retargeted program: it doesn't run "
                                 r"~/\.codex/skills/chat/scripts/rotate-logs")

    def test_a_missing_target_blocks(self):
        (self.home / ".local/share/weechat/python/autoload/role_colors.py").unlink()
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"WeeChat role_colors autoload link: missing: "
                                 r"~/\.local/share/weechat/python/autoload/role_colors\.py")

    def test_a_target_changed_after_the_plan_blocks(self):
        saved = self.where / "plan.json"
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(stage_release, "SPEC_PATH", self.spec):
                stage_release.main(["plan", "--release", str(self.release), "--dest", str(self.dest),
                                    "--out", str(saved)])
        plist = self.home / "Library/LaunchAgents/com.coghex.chat-bridge.plist"
        _staging.plist(plist, "com.coghex.chat-bridge", self.skills / "chat/scripts/chat-bridge",
                       {"KeepAlive": False})  # still owned and pointed right, but not what was planned
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"blocked: chat-bridge LaunchAgent changed since the plan was made", plan=saved)

    def test_a_target_changed_between_the_plan_and_the_recheck_blocks(self):
        real = stage_release.preflight

        def preflight_then_edit(*args, **kw):
            report = real(*args, **kw)  # after the plan, before the recheck under the lock
            with open(self.skills / "chat/scripts/runstore.py", "a") as stream:
                stream.write("# edited mid-run\n")
            self.before = _staging.snapshot_tree(self.home)
            return report
        with mock.patch.object(stage_release, "preflight", preflight_then_edit):
            with self.assertRaisesRegex(Refused, r"blocked: chat skill folder, chat/scripts import location changed "
                                                 r"before staging could record it; nothing was staged"):
                self.stage()
        self.assertEqual(stage_release.status(self.dest), [])  # no operation was begun
        self.assertFalse(os.path.exists(self.dest / "releases"))
        self.assertHomeUnchanged()

    def test_an_interpreter_that_doesnt_match_blocks(self):
        cases = ((self.fake_python(version="3.9.18"), r"CPython 3\.9\.18 .* does not meet requires_python >=3\.10"),
                 (self.fake_python(implementation="PyPy"), r"PyPy 3\.12\.4 .* is not the CPython the manifest "
                                                           r"requires"),
                 (self.fake_python(system="Plan9"), r"CPython 3\.12\.4 on Plan9 .* is not on a platform the manifest supports "
                                                    r"\(Darwin, Linux\)"))
        for python, pattern in cases:
            with self.subTest(python=python.name):
                self.assertNothingStaged(rf"preflight refused: interpreter: {pattern}", python=python)

    def test_child_runs_behind_symlinks_are_checked(self):
        pm = self.home / ".local/state/project-manager"
        elsewhere = self.where / "project-state"
        _staging.write(elsewhere / "beta/runs/run-20261002T120000Z-0123456789ab/state.json",
                       json.dumps({"schema": "childrun/99"}))
        (pm / "beta").symlink_to(elsewhere / "beta")  # a symlinked project
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"child-runs: the existing state holds a child-runs version the release doesn't "
                                 r"read")
        (pm / "beta").unlink()
        run_link = next(pm.glob("alpha/runs")) / "run-20261003T120000Z-ba9876543210"
        run_link.symlink_to(elsewhere / "beta/runs/run-20261002T120000Z-0123456789ab")  # a symlinked run
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"child-runs: the existing state holds a child-runs version the release doesn't "
                                 r"read")

    @unittest.skipIf(os.geteuid() == 0, "root reads unreadable directories")
    def test_an_inventory_that_cant_be_listed_blocks(self):
        runs = next((self.home / ".local/state/project-manager").glob("*/runs"))
        experiments = self.home / ".local/state/chat/experiments"
        for folder, pattern in ((runs, r"child-runs: state version evidence unreadable: "
                                       r"~/\.local/state/project-manager/alpha/runs: can't be listed"),
                                (experiments, r"receipt-experiment-marker: state version evidence unreadable: "
                                              r"~/\.local/state/chat/experiments: can't be listed")):
            with self.subTest(folder=folder.name):
                folder.chmod(0)
                self.before = _staging.snapshot_tree(self.home)  # as it can be seen while locked
                try:
                    self.assertNothingStaged(rf"preflight refused: .*{pattern}")
                finally:
                    folder.chmod(0o755)

    def test_an_unsupported_or_unreadable_state_version_blocks(self):
        state = self.home / ".local/state/chat"
        registry = state / "identities.json"
        runs = next((self.home / ".local/state/project-manager").glob("*/runs/*/state.json"))
        cases = ((registry, '{"version": 2}', r"identity-registry: the existing state holds a identity-registry "
                                              r"version the release doesn't read \(it reads 1\)"),
                 (registry, "{not json", r"identity-registry: state version evidence unreadable: "
                                         r"~/\.local/state/chat/identities\.json: not valid JSON"),
                 (registry, '{"counters": {}}', r"identity-registry: state version evidence unreadable: .*: "
                                                r"no version field"),
                 (runs, '{"schema": "childrun/3"}', r"child-runs: the existing state holds a child-runs version"),
                 (state / "experiments/review-start-receipts-v2.enabled", "",
                  r"receipt-experiment-marker: the existing state holds"))
        for path, text, pattern in cases:
            with self.subTest(case=pattern[:30]):
                original = path.read_bytes() if path.exists() else None
                path.write_text(text)
                self.before = _staging.snapshot_tree(self.home)
                self.assertNothingStaged(rf"preflight refused: .*{pattern}")
                path.write_bytes(original) if original is not None else path.unlink()
        (self.home / ".config/chat/config.json").unlink()
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"chat-config file: ~/\.config/chat/config\.json: missing")

    def test_an_unsupported_wire_version_blocks(self):
        release = self.release_copy()

        def bump(manifest):
            next(f for f in manifest["formats"] if f["name"] == "irc-client")["read"] = ["chat-irc/2"]
        self.rewrite_manifest(release, bump)
        self.assertNothingStaged(r"irc-client: unsupported version: the live tools use chat-irc/1; the release "
                                 r"reads chat-irc/2", release=release)

    def test_missing_malformed_or_undeclared_compatibility_blocks(self):
        cases = ((lambda m: m["formats"].pop(next(i for i, f in enumerate(m["formats"]) if f["name"] == "outbox")),
                  r"outbox: the manifest declares no outbox format"),
                 (lambda m: next(f for f in m["formats"] if f["name"] == "checkpoints").update(read="checkpoints/1"),
                  r"checkpoints: the manifest's read and write versions are malformed"),
                 (lambda m: m["formats"].append({"name": "card-move", "kind": "wire", "read": ["card-move/1"],
                                                 "write": "card-move/1"}),
                  r"card-move: the manifest declares card-move, which the live tools don't use"),
                 (lambda m: m["platforms"].pop("systems"), r"interpreter: the manifest declares no platform systems"))
        for change, pattern in cases:
            with self.subTest(case=pattern[:30]):
                release = self.release_copy()
                self.rewrite_manifest(release, change)
                self.assertNothingStaged(rf"preflight refused: .*{pattern}", release=release)

    def test_a_tampered_missing_or_unpinned_release_blocks(self):
        wheel = self.manifest["package"]["artifact"]
        def flip(path):
            data = bytearray(path.read_bytes())
            data[len(data) // 2] ^= 0xFF
            path.write_bytes(bytes(data))
        cases = ((lambda r: flip(r / wheel),
                  rf"artifact {re.escape(wheel)} does not match the manifest"),
                 (lambda r: (r / wheel).unlink(), rf"artifact {re.escape(wheel)} is missing"),
                 (lambda r: self.rewrite_manifest(r, lambda m: m["source"].pop("commit")),
                  r"pins no source commit"),
                 (lambda r: self.rewrite_manifest(r, lambda m: m.pop("requires_python")),
                  r"pins no interpreter requirement"))
        for change, pattern in cases:
            with self.subTest(case=pattern[:30]):
                release = self.release_copy()
                change(release)
                self.assertNothingStaged(pattern, release=release)


class ImporterTests(StageCase):
    def test_an_unlisted_importer_is_named(self):
        _staging.write(self.skills / "notes-bot/scripts/digest", "#!/usr/bin/env python3\nimport os, sys\n"
                       "sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'chat', 'scripts'))\n",
                       0o755)
        _staging.write(self.skills / "reports/scripts/weekly", "#!/usr/bin/env python3\nimport sys\n"
                       "from pathlib import Path\nsys.path = [str(Path(__file__).resolve().parents[2] / 'chat' / "
                       "'scripts')] + sys.path\n", 0o755)
        _staging.write(self.home / ".claude/skills/helper/run.sh",
                       'PYTHONPATH="$HOME/.codex/skills/chat/scripts" exec python3 "$@"\n', 0o755)
        plan = self.plan()
        self.assertEqual(plan["importers"]["unlisted"],
                         ["~/.claude/skills/helper/run.sh", "~/.codex/skills/notes-bot/scripts/digest",
                          "~/.codex/skills/reports/scripts/weekly"])
        self.assertIn("unlisted importer: ~/.codex/skills/notes-bot/scripts/digest (outside the known list; it "
                      "would stay on the old code)", stage_release.render_plan(plan))

    def test_an_importer_expression_over_several_lines_is_found(self):
        _staging.write(self.skills / "tidy/scripts/tidy", "#!/usr/bin/env python3\nimport sys\n"
                       "from pathlib import Path\n\nsys.path.insert(\n    0,\n"
                       "    str(\n        Path(__file__).resolve().parents[2]\n        / \"chat\"\n"
                       "        / \"scripts\"\n    ),\n)\n", 0o755)
        importers = self.plan()["importers"]
        self.assertEqual(importers["unlisted"], ["~/.codex/skills/tidy/scripts/tidy"])
        self.assertTrue(importers["complete"])

    def test_importers_behind_symlinks_are_found_once_and_cycles_end(self):
        helper = self.where / "external-helper"
        _staging.write(helper / "scripts/tool", "#!/usr/bin/env python3\nimport sys\n"
                       "sys.path.insert(0, '/x/.codex/skills/chat/scripts')\n", 0o755)
        _staging.write(self.where / "loose/sync.py", "import sys\nsys.path.append('../chat/scripts')\n")
        (self.skills / "helper").symlink_to(helper)                      # a symlinked skill folder
        (self.skills / "unrelated/scripts/sync.py").symlink_to(self.where / "loose/sync.py")  # a symlinked script
        (self.skills / "unrelated/loop").symlink_to(self.skills)        # a cycle
        (self.home / ".claude/skills/project-manager").symlink_to(self.skills / "project-manager")  # an alias
        importers = self.plan()["importers"]
        self.assertTrue(importers["complete"])
        self.assertEqual(importers["unlisted"], ["~/.codex/skills/helper/scripts/tool",
                                                 "~/.codex/skills/unrelated/scripts/sync.py"])
        self.assertTrue(all(k["found"] for k in importers["known"]))

    def opened_by_discovery(self):
        opened, real = [], stage_release.imports_chat_scripts

        def spy(path):
            opened.append(Path(path))
            return real(path)
        with mock.patch.object(stage_release, "imports_chat_scripts", spy):
            importers = stage_release.find_importers(json.loads(self.spec.read_text()), self.skills)
        return importers, opened

    def test_data_files_are_not_opened(self):
        _staging.write(self.skills / "notes-bot/state.json", '{"sys.path.insert": "chat/scripts"}\n')
        importers, opened = self.opened_by_discovery()
        self.assertNotIn(self.skills / "notes-bot/state.json", opened)
        self.assertEqual(importers["unlisted"], [])
        self.assertTrue(importers["complete"])

    def test_aliases_into_private_data_are_never_opened(self):
        config = self.home / ".config/chat/config.json"
        (self.skills / "unrelated/scripts/settings.py").symlink_to(config)
        (self.skills / "state-view").symlink_to(self.home / ".local/state")
        (self.home / ".claude/skills/history").symlink_to(self.home / ".claude")  # outside its skills folder
        importers, opened = self.opened_by_discovery()
        resolved = {os.path.realpath(p) for p in opened}
        self.assertNotIn(os.path.realpath(config), resolved)
        self.assertFalse(any(r.startswith(os.path.realpath(self.home / ".local/state")) for r in resolved))
        self.assertFalse(importers["complete"])
        excluded = [x for r in importers["roots"] for x in r["excluded"]]
        self.assertEqual(sorted(x.split(" (")[0] for x in excluded),
                         ["~/.claude/skills/history", "~/.codex/skills/state-view",
                          "~/.codex/skills/unrelated/scripts/settings.py"])
        self.assertNotIn(_staging.SECRETS[0], json.dumps(importers))

    def test_a_declared_root_aliased_into_private_data_exempts_nothing(self):
        """Round 5's fixture: ~/.claude/skills is a link to ~/.claude, the
        managed chat link preserved through it, and a script name there
        links to the private settings file. The settings file is never
        opened, the alias is named as excluded and the inventory is
        incomplete; the same holds for a root linked to the whole home
        folder (one that contains private data) and one linked inside
        ~/.claude elsewhere than its skills folder."""
        claude = self.home / ".claude"
        settings = _staging.write(claude / "settings.json",
                                  f'{{"token": "{_staging.SECRETS[0]}", "sys.path.insert": "chat/scripts"}}\n')
        settings.chmod(0o755)  # looks like a script, so only the exclusion keeps it closed
        (claude / "skills/chat").rename(claude / "chat")  # still a link to the skills tree's chat folder
        shutil.rmtree(claude / "skills")
        for target in (claude, self.home, claude / "projects"):
            with self.subTest(alias=target.name):
                target.mkdir(exist_ok=True)
                if os.path.lexists(claude / "skills"):
                    (claude / "skills").unlink()
                (claude / "skills").symlink_to(target)
                if not os.path.lexists(target / "chat"):
                    (target / "chat").symlink_to(self.skills / "chat")  # the managed link, kept through the alias
                for script in (target / "settings.py", self.skills / "unrelated/scripts/settings.py"):
                    if not os.path.lexists(script):
                        script.symlink_to(settings)
                importers, opened = self.opened_by_discovery()
                self.assertNotIn(os.path.realpath(settings), {os.path.realpath(p) for p in opened})
                self.assertFalse(importers["complete"])
                root = next(r for r in importers["roots"] if r["path"] == "~/.claude/skills")
                self.assertEqual(root["status"], "incomplete")
                self.assertRegex(root["excluded"][0], r"^~/\.claude/skills \(a declared root that (resolves to|is "
                                                      r"an alias resolving to) .*private data; not searched")
                self.assertEqual(importers["unlisted"], [])
                self.assertNotIn(_staging.SECRETS[0], json.dumps(importers))
                plan = self.plan()
                self.assertFalse(plan["importers"]["complete"])
                self.assertEqual([t["name"] for t in plan["targets"] if t["problems"]], [])
                self.assertNotIn(_staging.SECRETS[0], stage_release.render_plan(plan))

    def test_the_skills_root_itself_aliased_to_claude_or_the_home_exempts_nothing(self):
        """The declared skills root ({skills}) resolving to ~/.claude, then to
        the invented home, each with an executable-looking alias to the
        private settings file beside it: the settings file is never opened,
        the root is named as excluded, and the inventory is incomplete."""
        claude = self.home / ".claude"
        settings = _staging.write(claude / "settings.json",
                                  f'{{"token": "{_staging.SECRETS[0]}", "sys.path.insert": "chat/scripts"}}\n')
        settings.chmod(0o755)
        self.skills.rename(self.where / "skills-elsewhere")
        for target in (claude, self.home):
            with self.subTest(alias=target.name):
                if os.path.lexists(self.skills):
                    self.skills.unlink()
                self.skills.symlink_to(target)
                if not os.path.lexists(target / "settings.py"):
                    (target / "settings.py").symlink_to(settings)
                importers, opened = self.opened_by_discovery()
                self.assertNotIn(os.path.realpath(settings), {os.path.realpath(p) for p in opened})
                self.assertFalse(importers["complete"])
                root = next(r for r in importers["roots"] if r["path"] == "~/.codex/skills")
                self.assertEqual(root["status"], "incomplete")
                self.assertRegex(root["excluded"][0], r"^~/\.codex/skills \(a declared root that resolves to "
                                                      r".*private data; not searched")
                self.assertNotIn(_staging.SECRETS[0], json.dumps(importers))

    def test_a_large_script_is_searched_and_one_over_the_cap_is_named(self):
        big = self.skills / "bulk/scripts/generated.py"
        _staging.write(big, "# generated\n" + ("x = 1\n" * 400_000) +
                       "import sys\nsys.path.insert(0, '../chat/scripts')\n")
        self.assertGreater(big.stat().st_size, 2_400_000)
        importers = self.plan()["importers"]
        self.assertEqual(importers["unlisted"], ["~/.codex/skills/bulk/scripts/generated.py"])
        self.assertTrue(importers["complete"])
        with mock.patch.object(stage_release, "IMPORTER_LIMIT", 1 << 20):
            importers = self.plan()["importers"]
        self.assertFalse(importers["complete"])
        self.assertIn("~/.codex/skills/bulk/scripts/generated.py (over 1 MiB; not searched)",
                      importers["roots"][0]["unreadable"])

    def test_a_missing_known_importer_is_reported(self):
        (self.skills / "project-manager/scripts/report").unlink()
        known = {k["name"]: k["found"] for k in self.plan()["importers"]["known"]}
        self.assertFalse(known["report"])
        self.assertTrue(known["childrun"])

    @unittest.skipIf(os.geteuid() == 0, "root reads unreadable directories")
    def test_an_unreadable_root_makes_the_inventory_incomplete(self):
        locked = self.skills / "unrelated"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        importers = self.plan()["importers"]
        self.assertFalse(importers["complete"])
        root = importers["roots"][0]
        self.assertEqual((root["path"], root["status"]), ("~/.codex/skills", "incomplete"))
        self.assertIn("~/.codex/skills/unrelated", root["unreadable"])


class RecoveryTests(StageCase):
    def assertOneOperationStaged(self):
        records = self.journal()
        self.assertEqual(len({r["op"] for r in records} - {None}), 1, "a second operation was started")
        self.assertEqual(os.listdir(self.dest / "releases"), [self.manifest["release"]],
                         "a second release directory was created")
        release_dir = self.dest / "releases" / self.manifest["release"]
        build_release.verify(release_dir / "artifacts")
        self.assertEqual(stage_release.status(self.dest)[0]["state"], "complete")
        self.assertHomeUnchanged()
        self.assertNoServiceOrIdentityCall()

    def test_a_crash_after_any_journaled_intent_or_outcome_reconciles_to_one_operation(self):
        """Stopped right after each journal record in turn (the header, the
        operation, every intent and outcome, and every record of a file or
        directory created), then run again with the same inputs."""
        n = 0
        while True:
            n += 1
            with self.subTest(crash_after=n):
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                try:
                    self.stage(crash=self.crash_at(n))
                except stage_release.Crash:
                    pass
                else:
                    break  # n is past the last record: the run completed
                records = self.journal()
                ops = stage_release.status(self.dest)
                staged = self.dest / "releases" / self.manifest["release"] / "staged.json"
                complete = bool(ops) and ops[0]["state"] == "complete"
                if not complete:
                    # Interrupted: never reported done. staged.json can exist only once the
                    # complete step wrote it; the journal's outcome is what completes it.
                    self.assertTrue(not ops or ops[0]["state"].startswith("unfinished"), ops)
                    wrote_staged = any(r["kind"] == "created" and r["path"].endswith("/staged.json")
                                       for r in records)
                    self.assertEqual(staged.exists(), wrote_staged)
                summary, _, _ = self.stage()
                begun = any(r["kind"] == "begin" for r in records)
                self.assertEqual(summary["state"], "already staged, verified" if complete
                                 else "resumed and staged" if begun else "staged")
                self.assertOneOperationStaged()
        self.assertGreater(n, 25, "fewer journal records than expected")

    def test_recovery_never_runs_an_environment_it_finds_changed(self):
        """Interrupted after the install, or after verification, then the
        environment tampered with: altered package code, a .pth startup hook,
        a sitecustomize hook, or a changed pyvenv.cfg, each planted to
        overwrite the invented identity registry. Recovery never runs the
        environment it finds: it removes the directory this operation
        created and builds it again, and the registry stays byte-identical."""
        registry = self.home / ".local/state/chat/identities.json"
        payload = f"open({str(registry)!r}, 'w').write('overwritten')\n"
        env = self.dest / "releases" / self.manifest["release"] / "env"

        def site():
            return next(env.glob("lib/python*/site-packages"))
        damage = {"package code": lambda: (site() / "plateia_chat/__init__.py").write_text(payload),
                  ".pth hook": lambda: (site() / "zz-hook.pth").write_text("import os; " + payload),
                  "sitecustomize": lambda: (site() / "sitecustomize.py").write_text(payload),
                  "pyvenv.cfg": lambda: (env / "pyvenv.cfg").write_text("home = /invented\n")}
        for after in ("install-package", "verify-environment"):
            for name, plant in damage.items():
                with self.subTest(after=after, damage=name):
                    if self.dest.exists():
                        shutil.rmtree(self.dest)
                    with self.assertRaises(stage_release.Crash):
                        self.stage(crash=self.crash_when(lambda r, after=after: r["kind"] == "outcome"
                                                         and r.get("step") == after))
                    plant()
                    stage_release.CALLS.clear()
                    summary, _, _ = self.stage()
                    self.assertEqual(summary["state"], "resumed and staged")
                    venv_at = next(i for i, c in enumerate(stage_release.CALLS) if "venv" in c)
                    self.assertFalse(any(Path(c[0]).parent.parent == env for c in stage_release.CALLS[:venv_at]),
                                     "the environment found on disk ran before it was rebuilt")
                    self.assertFalse((site() / "zz-hook.pth").exists() or (site() / "sitecustomize.py").exists())
                    self.assertHomeUnchanged()

    def test_a_partial_copy_and_a_partial_environment_are_redone_in_place(self):
        """Interrupted mid-copy (one artifact written and recorded, the next
        temporary file journaled) and mid-venv (the environment directory
        made and half filled), a rerun redoes both in place."""
        release_dir = self.dest / "releases" / self.manifest["release"]
        wheel = self.manifest["package"]["artifact"]

        def mid_copy(record):
            if record["kind"] == "creating" and wheel in record["path"]:
                (release_dir / "artifacts/manifest.json").write_bytes(b"{")  # cut short, in its own file
                raise stage_release.Crash("mid-copy")

        def mid_venv(record):
            if record["kind"] == "created" and record["path"].endswith("/env"):
                env = release_dir / "env"
                (env / "bin").mkdir()
                (env / "pyvenv.cfg").write_text("home = /invented\n")
                (env / "bin/python").write_text("not an interpreter\n")
                raise stage_release.Crash("mid-venv")
        for why, hook in (("copy", mid_copy), ("venv", mid_venv)):
            with self.subTest(interrupted=why):
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                with self.assertRaises(stage_release.Crash):
                    self.stage(crash=hook)
                self.assertTrue(stage_release.status(self.dest)[0]["state"].startswith("unfinished"))
                summary, _, _ = self.stage()
                self.assertEqual(summary["state"], "resumed and staged")
                self.assertOneOperationStaged()

    def test_a_file_at_a_journaled_temporary_name_without_creation_evidence_is_left(self):
        names = []

        def hook(record):
            if record["kind"] == "creating":
                names.append(record["path"])
                raise stage_release.Crash("before the exclusive create")
        with self.assertRaises(stage_release.Crash):
            self.stage(crash=hook)
        foreign = self.dest / names[0]
        foreign.write_text("someone else's file\n")
        with self.assertRaisesRegex(Refused, r"is not a file the journal records this operation created"):
            self.stage()
        self.assertEqual(foreign.read_text(), "someone else's file\n")
        self.assertTrue(stage_release.status(self.dest)[0]["state"].startswith("unfinished"))

    def test_an_entry_staging_didnt_create_is_never_deleted(self):
        """A stray file in the artifacts directory that this operation didn't
        create is refused, not removed."""
        with self.assertRaises(stage_release.Crash):
            self.stage(crash=self.crash_when(lambda r: r["kind"] == "created" and r["path"].endswith("/artifacts")))
        stray = self.dest / "releases" / self.manifest["release"] / "artifacts/notes.txt"
        stray.write_text("someone else's\n")
        with self.assertRaisesRegex(Refused, r"notes\.txt is not a file the journal records this operation "
                                             r"created; staging never writes through, replaces or deletes an entry "
                                             r"it didn't create"):
            self.stage()
        self.assertEqual(stray.read_text(), "someone else's\n")

    def test_a_retry_with_different_inputs_reports_the_conflict(self):
        with self.assertRaises(stage_release.Crash):
            self.stage(crash=self.crash_at(4))
        other = self.where / "other-python"
        other.symlink_to(sys.executable)
        count = len(self.journal())
        with self.assertRaisesRegex(Refused, r"operation stage-[0-9a-f]{16} \(plateia-chat-[^)]*\) is unfinished "
                                             r"with different inputs \(interpreter\); repeat it with its own "
                                             r"inputs, nothing was changed"):
            self.stage(python=other)
        self.assertEqual(len(self.journal()), count)
        self.assertTrue(stage_release.status(self.dest)[0]["state"].startswith("unfinished"))

    def test_a_torn_final_journal_line_is_ignored_and_the_operation_resumes(self):
        with self.assertRaises(stage_release.Crash):
            self.stage(crash=self.crash_at(6))
        with open(self.dest / "journal.jsonl", "a") as stream:
            stream.write('{"op": "stage-torn", "kind": "inte')
        summary, _, _ = self.stage()
        self.assertEqual(summary["state"], "resumed and staged")
        self.assertTrue((self.dest / "journal.jsonl").read_bytes().endswith(b"\n"))
        self.assertNotIn(b"stage-torn", (self.dest / "journal.jsonl").read_bytes())
        self.assertEqual([op["state"] for op in stage_release.status(self.dest)], ["complete"])
        again, _, _ = self.stage()
        self.assertEqual(again["state"], "already staged, verified")
        resume = next(r for r in self.journal() if r["kind"] == "resume")
        self.assertEqual(resume["found"]["torn_bytes_dropped"], len('{"op": "stage-torn", "kind": "inte'))


class ConcurrencyTests(StageCase):
    def test_a_second_run_while_the_journal_is_locked_is_refused(self):
        self.dest.mkdir()
        with open(self.dest / "journal.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Refused, r"another staging run holds the journal lock; refusing to "
                                                 r"interleave"):
                self.stage()
        self.assertEqual(sorted(os.listdir(self.dest)), ["journal.lock"])
        self.assertHomeUnchanged()


class DestinationTests(StageCase):
    def test_a_release_identity_that_escapes_the_destination_is_refused(self):
        escape = str(self.home / ".local/state/chat/staged-here")
        for identity in (escape, "../escaped", "plateia-chat-0.1.0+g000000000000", "x/y"):
            with self.subTest(identity=identity):
                release = self.release_copy()
                self.rewrite_manifest(release, lambda m: m.update(release=identity))
                self.assertNothingStaged(r"manifest: the manifest can't be staged: its release identity is not "
                                         r"the package's name and version as one plain path component",
                                         release=release)
                self.assertFalse(os.path.lexists(escape))
                self.assertFalse(os.path.lexists(self.where / "escaped"))

    def test_a_destination_through_a_link_into_a_managed_target_is_refused(self):
        (self.where / "innocent").symlink_to(self.skills / "chat/scripts")
        for dest, pattern in ((self.where / "innocent" / "staging", r"under ~/\.codex"),
                              (self.home / ".local/bin/staging", r"under ~/\.local/bin"),
                              (_support.CHECKOUT / "staging-here", r"inside the source checkout")):
            with self.subTest(dest=dest):
                with self.assertRaisesRegex(Refused, rf"the staging destination is {pattern}|{pattern}"):
                    self.stage(dest=dest)
                self.assertFalse(os.path.lexists(dest))
        self.assertHomeUnchanged()

    def test_an_unrelated_existing_destination_is_refused(self):
        self.dest.mkdir()
        (self.dest / "notes.txt").write_text("someone else's\n")
        with self.assertRaisesRegex(Refused, r"holds entries staging didn't create \(notes\.txt\); left unchanged"):
            self.stage()
        (self.dest / "notes.txt").unlink()
        (self.dest / "releases" / self.manifest["release"]).mkdir(parents=True)
        with self.assertRaisesRegex(Refused, r"releases without a journal"):
            self.stage()
        self.assertEqual(os.listdir(self.dest / "releases" / self.manifest["release"]), [])


    def test_an_ancestor_swapped_after_the_guard_cant_place_the_root_in_protected_state(self):
        """Round 6: the guard passes, then an ancestor of the destination is
        swapped for a link into the invented chat state before the root is
        created: after check_destination returns (the pin's own recheck
        refuses it), or after the pin's recheck (its walk, one name at a time
        without following links, refuses it). The root, and a missing
        parent, are created only through pinned descriptors, so nothing is
        created inside protected state."""
        state = self.home / ".local/state/chat"
        real_check, real_reason = stage_release.check_destination, stage_release.protected_reason
        for when, pattern in (("after the guard", r"is under ~/\.local/state, where the live chat tools live"),
                              ("after the pin's recheck", r"changed after it was checked: a directory on the way to "
                                                          r"it is now a link or not a directory; nothing was created")):
            for dest in (self.where / "outer" / "new-staging", self.where / "outer" / "missing" / "new-staging"):
                with self.subTest(when=when, dest=str(dest.relative_to(self.where))):
                    swapped = self.where / "outer"
                    for leftover in (swapped, Path(f"{swapped}-moved")):
                        if os.path.lexists(leftover):
                            leftover.unlink() if leftover.is_symlink() else shutil.rmtree(leftover)
                    swapped.mkdir()
                    done = []

                    def swap():
                        if not done:
                            os.rename(swapped, f"{swapped}-moved")
                            os.symlink(state, swapped)
                            done.append(True)

                    def check_then_swap(path, identity):
                        root = real_check(path, identity)
                        swap()
                        return root

                    def reason_then_swap(path, bases):
                        result = real_reason(path, bases)
                        if str(path).endswith("new-staging"):
                            swap()
                        return result
                    patch = (mock.patch.object(stage_release, "check_destination", check_then_swap)
                             if when == "after the guard" else
                             mock.patch.object(stage_release, "protected_reason", reason_then_swap))
                    with patch:
                        with self.assertRaisesRegex(Refused, pattern):
                            self.stage(dest=dest)
                    self.assertTrue(done, "the swap never happened")
                    self.assertFalse(os.path.lexists(state / "new-staging"))
                    self.assertFalse(os.path.lexists(state / "missing"))
                    self.assertHomeUnchanged()

    def test_status_never_follows_a_link_at_the_journal(self):
        self.dest.mkdir()
        (self.dest / "journal.jsonl").symlink_to(self.home / ".local/state/chat/identities.json")
        with self.assertRaisesRegex(Refused, r"not a journal staging can read without following a link"):
            stage_release.status(self.dest)


class EnvironmentIntegrityTests(StageCase):
    """Round 6 and the audit: nothing from the staged environment runs
    unless it was statically checked, through pinned descriptors, against
    trusted evidence just before (the inventory taken right after venv, for
    pip; the inventory journaled at install, for the probe), and what a
    process leaves behind is checked again before it is trusted."""

    def registry(self):
        return self.home / ".local/state/chat/identities.json"

    def payload(self):
        return f"open({str(self.registry())!r}, 'w').write('overwritten')\n"

    def env(self):
        return self.dest / "releases" / self.manifest["release"] / "env"

    def site(self):
        return next(self.env().glob("lib/python*/site-packages"))

    def test_a_change_after_venv_is_refused_before_pip_runs(self):
        hook = _staging.write(self.where / "external/hook.pth", "import os; " + self.payload())
        plants = {
            ".pth link": lambda: (self.site() / "zz-hook.pth").symlink_to(hook),
            ".pth file": lambda: (self.site() / "zz-hook.pth").write_text("import os; " + self.payload()),
            "sitecustomize": lambda: (self.site() / "sitecustomize.py").write_text(self.payload()),
            "pyvenv.cfg": lambda: (self.env() / "pyvenv.cfg").write_text("home = /invented\n"),
            "interpreter link": lambda: ((self.env() / "bin/python").unlink(),
                                         (self.env() / "bin/python").symlink_to(self.where / "elsewhere-python")),
        }
        for name, plant in plants.items():
            with self.subTest(change=name):
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                stage_release.CALLS.clear()

                def between(record, plant=plant):
                    if record["kind"] == "outcome" and record.get("step") == "create-environment":
                        plant()
                with self.assertRaisesRegex(Refused, r"install-package failed: the environment changed after venv "
                                                     r"built it \((lib/python|pyvenv|bin/python).*\); pip was not run"):
                    self.stage(crash=between)
                self.assertFalse(any("pip" in c for c in stage_release.CALLS), "pip ran")
                self.assertEqual(stage_release.status(self.dest)[0]["state"],
                                 "unfinished (last: outcome install-package: failed)")
                self.assertHomeUnchanged()

    def test_what_pip_leaves_beyond_its_record_is_never_trusted(self):
        real = stage_release.run
        for name, plant in (("unlisted hook", lambda: (self.site() / "zz-hook.pth").write_text("import os\n")),
                            ("venv file changed", lambda: (self.env() / "pyvenv.cfg").write_text("home = /x\n"))):
            with self.subTest(change=name):
                if self.dest.exists():
                    shutil.rmtree(self.dest)

                def boundary(argv, plant=plant, **kw):
                    result = real(argv, **kw)
                    if "pip" in [str(a) for a in argv]:
                        plant()
                    return result
                with mock.patch.object(stage_release, "run", boundary):
                    with self.assertRaisesRegex(Refused, r"install-package failed: (the environment holds .*RECORD "
                                                         r"doesn't list|pip changed or removed pyvenv\.cfg)"):
                        self.stage()
                records = self.journal()
                self.assertFalse(any(r["kind"] == "outcome" and r.get("step") == "install-package"
                                     and r["result"] == "ok" for r in records))
                self.assertFalse((self.env().parent / "env-inventory.json").exists())

    def test_the_probe_runs_only_between_static_checks(self):
        """A change to the environment, or a swapped directory, around the
        probe's run is refused and the operation never recorded complete."""
        real = stage_release.run
        rel_dir = self.dest / "releases" / self.manifest["release"]
        for name, act, pattern in (
                ("hook added during the probe", lambda: (self.site() / "zz-hook.pth").write_text("import os\n"),
                 r"the staged environment changed while its probe ran \(lib/python.*zz-hook\.pth\)"),
                ("environment swapped during the probe",
                 lambda: (os.rename(self.env(), f"{self.env()}-moved"),
                          os.symlink(self.home / ".local/state/chat", self.env())),
                 r".*/env changed while the environment probe ran: .*so the environment probe may have run other "
                 r"code; refused")):
            with self.subTest(change=name):
                if self.dest.exists():
                    shutil.rmtree(self.dest)

                def boundary(argv, act=act, **kw):
                    text = " ".join(str(a) for a in argv)
                    if "-c" in [str(a) for a in argv] and str(rel_dir) in text:
                        act()
                    return real(argv, **kw)
                with mock.patch.object(stage_release, "run", boundary):
                    with self.assertRaisesRegex(Refused, r"verify-environment failed: " + pattern):
                        self.stage()
                self.assertEqual(stage_release.status(self.dest)[0]["state"],
                                 "unfinished (last: outcome verify-environment: failed)")
                if os.path.islink(self.env()):
                    os.unlink(self.env())
                    os.rename(f"{self.env()}-moved", self.env())


class AliasMatrixTests(StageCase):
    """Every write site staging has, each aliased in turn: a symlink and a
    hardlink to protected state (the invented identity registry, or the
    invented chat-config directory for a directory site), a directory staging
    didn't create, and traversal through the release identity, an artifact
    name and the plan's output path. Each is refused, and every file in the
    invented home stays byte-identical."""

    def registry(self):
        return self.home / ".local/state/chat/identities.json"

    def plant(self, path, kind, directory):
        if os.path.lexists(path):
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        if kind == "symlink":
            path.symlink_to(self.home / ".config/chat" if directory else self.registry())
        elif kind == "hardlink":
            os.link(self.registry(), path)
        else:  # a directory staging didn't create
            path.mkdir()

    def sites(self):
        """(write site, a predicate on the record after which to stop, whether
        the site is a directory). None means a fresh destination."""
        rel = f"releases/{self.manifest['release']}"
        created = lambda suffix: lambda r: r["kind"] == "created" and r["path"].endswith(suffix)  # noqa: E731
        return [("journal.jsonl", None, False),
                ("journal.lock", None, False),
                ("releases", lambda r: r["kind"] == "begin", True),
                (rel, created("releases"), True),
                (f"{rel}/artifacts", created(self.manifest["release"]), True),
                (f"{rel}/artifacts/manifest.json", created("/artifacts"), False),
                ("temporary file", lambda r: r["kind"] == "creating", False),
                (f"{rel}/env", lambda r: r["kind"] == "outcome" and r["step"] == "copy-artifacts", True),
                (f"{rel}/env-inventory.json", lambda r: r["kind"] == "intent" and r["step"] == "install-package",
                 False),
                (f"{rel}/staged.json", lambda r: r["kind"] == "intent" and r["step"] == "complete", False)]

    def test_symlink_hardlink_and_foreign_aliases_at_every_write_site(self):
        for site, stop, directory in self.sites():
            for kind in ("symlink", "dir") if directory else ("symlink", "hardlink"):
                with self.subTest(site=site, alias=kind):
                    if self.dest.exists():
                        shutil.rmtree(self.dest)
                    temp = []
                    if stop is None:
                        self.dest.mkdir()
                    else:
                        def hook(record, stop=stop):
                            if record["kind"] == "creating":
                                temp.append(record["path"])
                            if stop(record):
                                raise stage_release.Crash(site)
                        with self.assertRaises(stage_release.Crash):
                            self.stage(crash=hook)
                    path = self.dest / (temp[-1] if site == "temporary file" else site)
                    self.plant(path, kind, directory)
                    registry_before = self.registry().read_bytes()
                    with self.assertRaises(Refused) as caught:
                        self.stage()
                    self.assertNotIn("Traceback", str(caught.exception))
                    self.assertEqual(self.registry().read_bytes(), registry_before)
                    self.assertHomeUnchanged()

    def test_traversal_through_the_identity_artifact_names_and_plan_output(self):
        for change in (lambda m: m.update(release="../escaped"),
                       lambda m: m.update(release=str(self.home / ".local/state/chat/staged-here")),
                       lambda m: m["artifacts"][0].update(name="../../escaped.whl")):
            with self.subTest(change=change):
                release = self.release_copy()
                self.rewrite_manifest(release, change)
                with self.assertRaisesRegex(Refused, r"can't be staged|malformed artifact entry"):
                    self.stage(release=release)
                self.assertFalse(os.path.lexists(self.dest))
                self.assertFalse(os.path.lexists(self.where / "escaped"))
                self.assertFalse(os.path.lexists(self.home / ".local/state/chat/staged-here"))
                self.assertHomeUnchanged()
        registry = self.registry()
        (self.where / "out-link.json").symlink_to(registry)
        os.link(registry, self.where / "out-hard.json")
        _staging.write(self.where / "out-existing.json", "an earlier plan\n")
        traversal = self.where / "staging" / ".." / "home" / ".local/state/chat" / "plan.json"
        for out, pattern in ((self.where / "out-link.json", r"already exists|under ~/\.local/state"),
                             (self.where / "out-hard.json", r"already exists"),
                             (self.where / "out-existing.json", r"already exists"),
                             (traversal, r"under ~/\.local/state"),
                             (self.home / ".local/bin/plan.json", r"under ~/\.local/bin")):
            with self.subTest(out=out.name):
                before = registry.read_bytes()
                code, text = PrivacyAndIsolationTests.outputs(self, "plan", "--release", str(self.release),
                                                              "--out", str(out))
                self.assertEqual(code, 1, text)
                self.assertRegex(text, pattern)
                self.assertEqual(registry.read_bytes(), before)
        self.assertEqual((self.where / "out-existing.json").read_text(), "an earlier plan\n")
        self.assertFalse(os.path.lexists(self.home / ".local/state/chat/plan.json"))
        self.assertHomeUnchanged()

    def test_a_parent_swapped_for_a_link_mid_write_is_never_written_through(self):
        """A directory swapped for a symlink into protected state right after
        it was checked, at the journal boundary before the write: the write
        stays in the pinned original directory, and the next one is refused."""
        state = self.home / ".local/state/chat"
        rel = f"releases/{self.manifest['release']}"
        cases = (("artifacts", lambda r: r["kind"] == "creating" and "/artifacts/" in r["path"]),
                 ("release directory", lambda r: r["kind"] == "creating" and "/artifacts/" in r["path"]),
                 ("env", lambda r: r["kind"] == "created" and r["path"].endswith("/env")))
        for what, when in cases:
            with self.subTest(swapped=what):
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                target = self.dest / {"artifacts": f"{rel}/artifacts", "release directory": rel,
                                      "env": f"{rel}/env"}[what]
                swapped = []

                def hook(record, when=when, target=target):
                    if not swapped and when(record):
                        target.rename(target.with_name(target.name + ".moved-away"))
                        target.symlink_to(state)
                        swapped.append(record)
                with self.assertRaisesRegex(Refused, r"is not a directory staging created|is no longer the "
                                                     r"directory this operation created|doesn't resolve under the "
                                                     r"staging destination|is under ~/\.local/state|is no longer the "
                                                     r"directory staging created and checked"):
                    self.stage(crash=hook)
                self.assertFalse(any(state.glob("*.whl")) or any(state.glob("*.zip")) or
                                 (state / "manifest.json").exists() or (state / "bin").exists())
                self.assertTrue(swapped)
                self.assertHomeUnchanged()

    def test_the_writer_writes_through_its_pinned_directory_not_the_path(self):
        """Below the path checks: a directory swapped for a link to protected
        state after the writer pinned it still receives the write, and the
        protected directory doesn't."""
        root, protected = self.where / "pin-root", self.where / "pin-protected"
        root.mkdir(mode=0o755)
        protected.mkdir()
        writer = stage_release.Writer(root, [])
        self.addCleanup(writer.close)
        swapped = []

        def record(r):
            if r["kind"] == "creating" and not swapped:  # after the parent is pinned, before the create
                (root / "a").rename(root / "a-moved")
                (root / "a").symlink_to(protected)
                swapped.append(r)
        writer.record = record
        writer.mkdir("a")
        writer.write("a/x.txt", b"pinned\n")
        self.assertEqual(os.listdir(protected), [])
        self.assertEqual((root / "a-moved/x.txt").read_bytes(), b"pinned\n")
        with self.assertRaisesRegex(Refused, r"is not a directory staging created|doesn't resolve"):
            writer.write("a/y.txt", b"refused\n")
        self.assertEqual(os.listdir(protected), [])

    def test_a_replaced_directory_staging_created_is_refused(self):
        with self.assertRaises(stage_release.Crash):
            self.stage(crash=self.crash_when(lambda r: r["kind"] == "created" and r["path"] == "releases"))
        (self.dest / "releases").rename(self.where / "old-releases")  # kept, so its inode can't be reused
        (self.dest / "releases").mkdir()  # same name, another inode
        with self.assertRaisesRegex(Refused, r"releases is a directory staging didn't create"):
            self.stage()

    def test_a_journal_staging_didnt_create_is_left_byte_identical(self):
        for text in (b"my own notes\n", b"my own notes", b'{"schema": "something-else/1"}\n'):
            with self.subTest(text=text):
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                self.dest.mkdir()
                (self.dest / "journal.jsonl").write_bytes(text)
                with self.assertRaisesRegex(Refused, r"has no lock beside it, so staging didn't create it"):
                    self.stage()
                self.assertEqual((self.dest / "journal.jsonl").read_bytes(), text)
                self.assertEqual(os.listdir(self.dest), ["journal.jsonl"])
                self.assertFalse((self.dest / "releases").exists())


class ProcessSwapTests(StageCase):
    """D-71: venv and pip write by path while they run, so a same-user
    process that swaps a directory during one of them can redirect its
    writes, and these tests don't claim it can't. At the process boundary,
    each directory on the environment's path is swapped for a link to
    protected state (the invented chat state) just as the real venv or pip
    starts. Staging must refuse, name the changed directory, and record no
    completion for that step or any later one."""

    def swap_during(self, process, swapped):
        real = stage_release.run

        def boundary(argv, **kw):
            text = " ".join(str(a) for a in argv)
            if (" -m venv " in text) if process == "venv" else (" -m pip " in text):
                os.rename(swapped, f"{swapped}-moved")
                os.symlink(self.home / ".local/state/chat", swapped)
            return real(argv, **kw)
        return mock.patch.object(stage_release, "run", boundary)

    def check_swap_during(self, process, step):
        """For each directory on the environment's path, swapped while
        `process` runs and still swapped at the check after it: refused,
        naming that directory; the step recorded failed and no step at or
        after it recorded complete; no later step started (for venv, pip
        never ran); the operation left unfinished. A retry refuses the
        conflicting directory without writing: the journal keeps its one
        operation byte for byte, and nothing in the home changes."""
        rel_dir = self.dest / "releases" / self.manifest["release"]
        for swapped in (self.dest, rel_dir, rel_dir / "env"):
            with self.subTest(process=process, swapped=swapped.name):
                for leftover in (self.dest, Path(f"{self.dest}-moved")):
                    if os.path.lexists(leftover):
                        leftover.unlink() if leftover.is_symlink() else shutil.rmtree(leftover)
                stage_release.CALLS.clear()
                with self.swap_during(process, swapped):
                    with self.assertRaises(Refused) as caught:
                        self.stage()
                message = str(caught.exception)
                self.assertIn(f"{step} failed: {stage_release.shown(swapped)} changed while {process} ran",
                              message)
                self.assertIn("may have landed elsewhere (D-71)", message)
                journal = (Path(f"{self.dest}-moved") if swapped == self.dest else self.dest) / "journal.jsonl"
                records = stage_release.read_journal(journal)
                outcomes = [r for r in records if r["kind"] == "outcome"]
                self.assertEqual([r["result"] for r in outcomes if r["step"] == step], ["failed"])
                late = [r for r in outcomes if r["result"] == "ok" and STEP_ORDER[r["step"]] >= STEP_ORDER[step]]
                self.assertEqual(late, [], "a step at or after the swap was recorded complete")
                started = [r["step"] for r in records if r["kind"] == "intent"]
                self.assertEqual(started[-1], step, "a later step started")
                if process == "venv":
                    self.assertFalse(any("pip" in call for call in stage_release.CALLS), "pip ran after the swap")
                ops = stage_release.status(journal.parent)
                self.assertEqual([o["state"] for o in ops], [f"unfinished (last: outcome {step}: failed)"])
                self.assertFalse(os.path.lexists(rel_dir / "staged.json"))
                # Nothing here asserts that no write landed: during the swap D-71 doesn't require that.

                journal_bytes, home_now = journal.read_bytes(), _staging.snapshot_tree(self.home)
                with self.assertRaises(Refused) as retry:
                    self.stage()
                self.assertIn(stage_release.shown(swapped), str(retry.exception))  # the conflicting directory
                self.assertEqual(journal.read_bytes(), journal_bytes, "the retry wrote to the journal")
                self.assertEqual([o["operation"] for o in stage_release.status(journal.parent)],
                                 [o["operation"] for o in ops])
                self.assertEqual(_staging.snapshot_tree(self.home), home_now, "the retry changed the home")
                os.unlink(swapped)
                if swapped != self.dest:
                    shutil.rmtree(self.dest)

    def test_a_directory_swapped_while_venv_runs_is_named_and_the_step_never_completes(self):
        self.check_swap_during("venv", "create-environment")

    def test_a_directory_swapped_while_pip_runs_is_named_and_the_step_never_completes(self):
        self.check_swap_during("pip", "install-package")


STEP_ORDER = {name: i for i, name in enumerate(stage_release.STEPS)}


class PrivacyAndIsolationTests(StageCase):
    def outputs(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.object(stage_release, "SPEC_PATH", self.spec):
                code = stage_release.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def assertNoSecret(self, text):
        for secret in _staging.SECRETS:
            self.assertNotIn(secret, text)

    def test_plan_preflight_stage_status_and_journal_carry_no_private_value(self):
        release, dest = str(self.release), str(self.dest)
        for argv in (["plan", "--release", release, "--json"], ["plan", "--release", release],
                     ["preflight", "--release", release, "--json"], ["stage", "--release", release, "--dest", dest],
                     ["status", "--dest", dest]):
            with self.subTest(argv=argv[:2]):
                code, text = self.outputs(*argv)
                self.assertEqual(code, 0, text)
                self.assertNoSecret(text)
        journal = (self.dest / "journal.jsonl").read_text()
        self.assertNoSecret(journal)
        self.assertNoSecret(json.dumps(stage_release.make_plan(self.release, self.dest, self.spec)))
        self.assertHomeUnchanged()

    def test_a_failing_preflight_names_its_reason_without_the_value(self):
        registry = self.home / ".local/state/chat/identities.json"
        registry.write_text(json.dumps({"version": _staging.SECRETS[1], "agents": {"x": _staging.SECRETS[0]}}))
        _staging.write(self.home / ".local/state/project-manager/alpha/runs/run-x/state.json",
                       '{"schema": "' + _staging.SECRETS[2] + '"')
        self.before = _staging.snapshot_tree(self.home)
        code, text = self.outputs("stage", "--release", str(self.release), "--dest", str(self.dest))
        self.assertEqual(code, 1)
        self.assertIn("identity-registry: the existing state holds a identity-registry version the release doesn't "
                      "read", text)
        self.assertIn("child-runs: state version evidence unreadable", text)
        self.assertNoSecret(text)
        self.assertFalse(self.dest.exists())
        self.assertHomeUnchanged()

    def test_every_external_command_goes_through_the_recorded_boundary(self):
        source = (_support.RELEASE / "stage_release.py").read_text()
        self.assertEqual(source.count("subprocess.run("), 1)
        self.assertNotRegex(source, r"os\.system|os\.exec|os\.spawn|Popen|os\.kill|launchctl\b(?! print)")
        self.stage()
        kinds = sorted({("probe" if "-I" in c and "plateia_chat" not in c[-1] else "env-probe" if "-I" in c
                         else "venv" if "venv" in c else "pip" if "pip" in c else "other") for c in
                        stage_release.CALLS})
        self.assertEqual(kinds, ["env-probe", "pip", "probe", "venv"])
        self.assertNoServiceOrIdentityCall()
        self.assertHomeUnchanged()


if __name__ == "__main__":
    unittest.main()
