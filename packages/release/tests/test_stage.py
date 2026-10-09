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

    def journal(self):
        return [json.loads(line) for line in (self.dest / "journal.jsonl").read_text().splitlines()]


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
        self.assertEqual({r["op"] for r in records}, {summary["operation"]})
        self.assertEqual(records[0]["kind"], "begin")
        steps = [(r["kind"], r["step"]) for r in records[1:]]
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

    def test_a_completed_stage_whose_environment_was_damaged_is_not_reported_staged(self):
        self.stage()
        (self.dest / "releases" / self.manifest["release"] / "env/bin/pchat").unlink()
        with self.assertRaisesRegex(Refused, r"recorded complete, but its staged release doesn't verify "
                                             r"\(the staged environment has no pchat command\)"):
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

    def test_a_retargeted_launchagent_program_blocks(self):
        _staging.plist(self.home / "Library/LaunchAgents/com.coghex.log-rotate.plist", "com.coghex.log-rotate",
                       self.where / "elsewhere/rotate-logs")
        self.before = _staging.snapshot_tree(self.home)
        self.assertNothingStaged(r"rotate-logs LaunchAgent: retargeted program: its ProgramArguments don't name "
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
        self.assertFalse(os.path.exists(self.dest / "journal.jsonl"))
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
        _staging.write(self.home / ".claude/skills/helper/run.sh",
                       'PYTHONPATH="$HOME/.codex/skills/chat/scripts" exec python3 "$@"\n', 0o755)
        plan = self.plan()
        self.assertEqual(plan["importers"]["unlisted"],
                         ["~/.claude/skills/helper/run.sh", "~/.codex/skills/notes-bot/scripts/digest"])
        self.assertIn("unlisted importer: ~/.codex/skills/notes-bot/scripts/digest (outside the known list; it "
                      "would stay on the old code)", stage_release.render_plan(plan))

    def test_data_files_are_not_opened(self):
        _staging.write(self.skills / "notes-bot/state.json", '{"sys.path.insert": "chat/scripts"}\n')
        opened = []
        real = Path.read_bytes

        def spy(path):
            opened.append(path)
            return real(path)
        with mock.patch.object(Path, "read_bytes", spy):
            importers = stage_release.find_importers(json.loads(self.spec.read_text()), self.skills)
        self.assertNotIn(self.skills / "notes-bot/state.json", opened)
        self.assertNotIn(self.home / ".local/state/chat/identities.json", opened)
        self.assertEqual(importers["unlisted"], [])

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
    def crash_at(self, n):
        seen = []

        def hook(record):
            seen.append(record)
            if len(seen) == n:
                raise stage_release.Crash(f"crash after record {n}")
        return hook

    def assertOneOperationStaged(self):
        records = self.journal()
        self.assertEqual(len({r["op"] for r in records}), 1, "a second operation was started")
        self.assertEqual(os.listdir(self.dest / "releases"), [self.manifest["release"]],
                         "a second release directory was created")
        release_dir = self.dest / "releases" / self.manifest["release"]
        build_release.verify(release_dir / "artifacts")
        self.assertEqual(stage_release.status(self.dest)[0]["state"], "complete")
        self.assertHomeUnchanged()
        self.assertNoServiceOrIdentityCall()

    def test_a_crash_after_any_journaled_intent_or_outcome_reconciles_to_one_operation(self):
        total = 1 + 2 * len(stage_release.STEPS)
        for n in range(1, total + 1):
            with self.subTest(crash_after=n):
                if self.dest.exists():
                    shutil.rmtree(self.dest)
                with self.assertRaises(stage_release.Crash):
                    self.stage(crash=self.crash_at(n))
                state = stage_release.status(self.dest)[0]["state"]
                staged = self.dest / "releases" / self.manifest["release"] / "staged.json"
                if n < total:
                    # Interrupted: never reported done, and nothing marked staged.
                    self.assertTrue(state.startswith("unfinished"), state)
                    self.assertFalse(staged.exists())
                else:
                    self.assertEqual(state, "complete")
                summary, _, _ = self.stage()
                self.assertIn(summary["state"], ("resumed and staged",) if n < total
                              else ("already staged, verified",))
                self.assertOneOperationStaged()

    def test_a_partial_copy_and_a_partial_environment_are_redone_in_place(self):
        for step, damage in (("copy-artifacts", self.partial_copy), ("create-environment", self.partial_env)):
            with self.subTest(step=step):
                if self.dest.exists():
                    shutil.rmtree(self.dest)

                def hook(record, step=step):
                    if record["kind"] == "intent" and record["step"] == step:
                        damage()
                        raise stage_release.Crash(step)
                with self.assertRaises(stage_release.Crash):
                    self.stage(crash=hook)
                self.assertTrue(stage_release.status(self.dest)[0]["state"].startswith("unfinished"))
                summary, _, _ = self.stage()
                self.assertEqual(summary["state"], "resumed and staged")
                self.assertOneOperationStaged()

    def partial_copy(self):
        artifacts = self.dest / "releases" / self.manifest["release"] / "artifacts"
        artifacts.mkdir()
        wheel = self.manifest["package"]["artifact"]
        (artifacts / wheel).write_bytes((self.release / wheel).read_bytes()[:100])
        (artifacts / f".{wheel}.tmp").write_bytes(b"half")

    def partial_env(self):
        env = self.dest / "releases" / self.manifest["release"] / "env"
        (env / "bin").mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = /invented\n")
        (env / "bin/python").write_text("not an interpreter\n")

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
