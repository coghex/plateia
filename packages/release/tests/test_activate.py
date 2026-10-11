"""Tests for packages/release/activate_release.py (PLT-14, #21; D-74, D-26,
D-27): the guarded activation of #19's release through one admitted format
transition, with the designation bootstrap and its launch digest (#21's owner
amendment of 2026-10-11 and its APPROVE).

Every test runs in an invented home (the staging tests' fixtures: an invented
skills tree, LaunchAgents, chat config and state), with #19's real release
built from an invented repository and staged offline under a private
destination. Services, processes and checkpoints are fakes (FakeOps); no real
installed tool, service, chat server or state is read or touched, and the
outbox database is never opened by the controller."""
import contextlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import _staging
import _support
from _support import make_repo

sys.path.insert(0, str(_support.RELEASE))
import activate_release  # noqa: E402
import build_release  # noqa: E402
import stage_release  # noqa: E402
from activate_release import Crash  # noqa: E402
from build_release import Refused  # noqa: E402

ACCOUNTS = ["alp-solver-2", "sam"]
BRIDGE_PLIST = "Library/LaunchAgents/com.coghex.chat-bridge.plist"
ROTATE_PLIST = "Library/LaunchAgents/com.coghex.log-rotate.plist"


class FakeOps:
    """What the controller reads about services, processes and checkpoints,
    invented: a bridge that is or isn't running (and which release it runs),
    the legacy writers with their checkpoint state, and each designated
    account's producers. It only answers reads: nothing here can start, stop,
    signal or restart anything."""

    def __init__(self, bridge=None, writers="default", producers="default"):
        """None for writers or producers means there is no evidence at all."""
        self.bridge = bridge or {"running": False, "release": None}
        self.writer_list = [
            {"name": "chat-bridge (old)", "kind": "bridge", "release": "skills-tree", "checkpoint": "acknowledged",
             "owner": "plat-manager"},
            {"name": "pchat flusher", "kind": "flusher", "release": "skills-tree", "checkpoint": "acknowledged",
             "owner": "plat-manager"},
            {"name": "alpha manager helper", "kind": "cached-producer", "release": "skills-tree",
             "checkpoint": "acknowledged", "owner": "alp-manager"}] if writers == "default" else writers
        self.producer_map = {a: "tracked" for a in ACCOUNTS} if producers == "default" else producers

    def bridge_state(self):
        return dict(self.bridge)

    def writers(self):
        return None if self.writer_list is None else [dict(w) for w in self.writer_list]

    def producers(self):
        return None if self.producer_map is None else dict(self.producer_map)


def ops(bridge=None, writers="default", producers="default"):
    return FakeOps(bridge, writers, producers)


class ActivationCase(unittest.TestCase):
    """A test-free base: each case gets a fresh invented home (fresh() makes
    another inside one test, for subTest loops)."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()
        repo, _ = make_repo(cls.sandbox.root)
        cls.release, _ = build_release.build(repo, cls.sandbox.root / "releases")  # #19's real release.json
        cls.manifest = build_release.verify(cls.release)

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.close()

    def setUp(self):
        self.base = self.sandbox.root / self.id().rsplit(".", 1)[-1]
        self.base.mkdir(parents=True)
        self.fake_bin, self.fake_log = _staging.fake_commands(self.base)
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("CHAT_STATE", "CHAT_CONFIG", "XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            if key in os.environ:
                saved = os.environ.pop(key)
                self.addCleanup(os.environ.__setitem__, key, saved)
        self.cases = 0
        self.fresh()

    def fresh(self, baseline=None):
        """A new invented home, staging destination and private root;
        `baseline` rewrites both LaunchAgents' invocation first."""
        self.cases += 1
        self.where = self.base / f"case-{self.cases}"
        self.home = self.where / "home"
        self.home.mkdir(parents=True)
        os.environ["HOME"] = str(self.home)
        self.skills = _staging.make_home(self.home)
        if baseline:
            for rel, script in ((BRIDGE_PLIST, "chat-bridge"), (ROTATE_PLIST, "rotate-logs")):
                path = self.home / rel
                data = plistlib.loads(path.read_bytes())
                data.pop("Program", None)
                data.update(baseline(str(self.skills / "chat/scripts" / script)))
                path.write_bytes(plistlib.dumps(data))
        self.spec = _staging.spec_for(_staging.provenance_for(self.skills, self.where / "provenance.json"),
                                      self.where / "staging.json")
        self.dest = self.where / "staging"
        self.root = self.where / "activation"
        self.state = self.home / ".local/state/chat"
        self.ops = ops()
        self.before = _staging.snapshot_tree(self.home)

    # --- helpers ---

    def plan(self, designations=ACCOUNTS):
        plan = activate_release.make_plan(self.release, self.dest, self.root, designations, self.ops,
                                          spec_path=self.spec)
        path = self.where / f"activation-plan-{plan['operation']}.json"
        activate_release.save_plan(plan, path)
        return plan, path

    def staged(self, path):
        return activate_release.stage_admitted(path, ops=self.ops, spec_path=self.spec)

    def activate(self, path, crash=None):
        return activate_release.activate(path, self.ops, spec_path=self.spec, crash=crash)

    def rollback(self, path, crash=None):
        return activate_release.rollback(path, self.ops, spec_path=self.spec, crash=crash)

    def postcheck(self, path):
        return activate_release.postcheck(path, self.ops, spec_path=self.spec)

    def prepared(self, designations=ACCOUNTS):
        plan, path = self.plan(designations)
        self.staged(path)
        return plan, path

    def activated(self, designations=ACCOUNTS):
        plan, path = self.prepared(designations)
        self.activate(path)
        return plan, path

    def journal(self):
        return activate_release.read_journal(self.root)

    def kinds(self):
        return [r["kind"] for r in self.journal()]

    def targets_snapshot(self):
        return {k: v for k, v in _staging.snapshot_tree(self.home).items() if not k.startswith(".local/state/chat")}

    def before_targets(self):
        return {k: v for k, v in self.before.items() if not k.startswith(".local/state/chat")}

    def record_path(self):
        return self.state / "designation-bootstrap.json"

    @staticmethod
    def crash_on(kind, nth=1):
        seen = []

        def hook(record):
            if record["kind"] == kind:
                seen.append(record)
                if len(seen) == nth:
                    raise Crash(f"killed after {kind}")
        return hook

    @staticmethod
    def crash_at(n):
        seen = []

        def hook(record):
            seen.append(record)
            if len(seen) == n:
                raise Crash(f"killed after journal record {n}")
        return hook

    def serving(self):
        """The new bridge's authority serving: its status snapshot, as it writes it."""
        _staging.write(self.state / "outbox-status.json",
                       json.dumps({"waiting": 0, "epoch": 1, "unavailable": None, "written_at": time.time() + 5}))

    def rewrite_plan(self, path, change):
        plan = json.loads(path.read_text())
        change(plan)
        path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")

    @contextlib.contextmanager
    def no_database(self):
        """The controller never opens the outbox database (any SQLite open fails the test)."""
        import sqlite3
        with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("a database was opened")):
            yield

    def assertChecksPass(self, report):
        self.assertEqual([c for c in report["checks"] if c[1] not in ("bound", "matches")], [])


class AdmissionTests(ActivationCase):
    """A1, the review's first correction and third addition, as amended
    (amendment section 1): a plain preflight still refuses #19's release; only a
    plan bound to this release, these targets, this state snapshot and root,
    and activation.json's one transition admits it, and exactly #19's format
    delta plus the one bootstrap read input is admitted."""

    def test_a_plain_preflight_still_refuses_19s_release(self):
        report = stage_release.preflight(self.release, None, self.spec)
        refused = {line.split(":", 1)[0] for line in report["refused"]}
        self.assertTrue({"outbox", "delivery-records", "outbox-db", "outbox-authority", "boot-id",
                         "tcp-sockets", "designation-bootstrap"} <= refused, report["refused"])

    def test_a_bound_plan_admits_exactly_19s_delta_and_the_bootstrap_input(self):
        _, path = self.plan()
        summary, _, report = self.staged(path)
        self.assertEqual(summary["state"], "staged")
        self.assertEqual(report["refused"], [])
        admitted = sorted(r[1] for r in report["results"] if r[0] == "admitted")
        self.assertEqual(admitted, ["boot-id", "delivery-records", "designation-bootstrap", "outbox",
                                    "outbox-authority", "outbox-db", "tcp-sockets"])
        self.assertEqual(_staging.snapshot_tree(self.home), self.before)

    def test_the_transition_admits_no_other_format_change(self):
        spec = stage_release.load_spec(self.spec)[0]
        transition = activate_release.load_activation()["transition"]

        def refused(change):
            manifest = json.loads(json.dumps(self.manifest))
            change({f["name"]: f for f in manifest["formats"]}, manifest)
            return {r[1] for r in stage_release.compatibility(manifest, spec, transition) if r[0] == "refused"}
        self.assertEqual(refused(lambda f, m: None), set())
        self.assertIn("invented-format", refused(lambda f, m: m["formats"].append(
            {"name": "invented-format", "kind": "state", "read": ["invented/1"], "write": "invented/1"})))
        self.assertIn("checkpoints", refused(lambda f, m: f["checkpoints"].update(write="checkpoints/2")))
        self.assertIn("outbox", refused(lambda f, m: f["outbox"].update(write="outbox/3")))
        self.assertIn("designation-bootstrap", refused(lambda f, m: f["designation-bootstrap"].update(
            write="designation-bootstrap/1")))
        self.assertIn("designation-bootstrap", refused(lambda f, m: m["formats"].remove(f["designation-bootstrap"])))

    def test_an_admission_comes_only_from_a_validated_plan(self):
        with self.assertRaisesRegex(Refused, "must come from"):
            stage_release.stage(self.release, self.dest, None, None, self.spec, None,
                                {"transition": activate_release.load_activation()["transition"]})
        plan, _ = self.plan()
        with self.assertRaisesRegex(Refused, "not an activation plan"):
            stage_release.admission(dict(plan, schema="plateia-stage-plan/1"), self.release, self.spec)
        self.assertFalse(os.path.lexists(self.dest))

    def test_an_altered_or_mismatched_plan_admits_nothing(self):
        cases = {
            "tampered designation": lambda p: p["designations"].append("mallory"),
            "another release": lambda p: p.update(release="plateia-chat-9.9.9"),
            "another transition": lambda p: p.update(transition=dict(p["transition"], name="any-to-any")),
            "another state root": lambda p: p.update(state_root=dict(p["state_root"], ino=1)),
            "a changed target": lambda p: p["targets"][0].update(digest="0" * 64),
            "a changed state snapshot": lambda p: p["state_snapshot"].update(digest="0" * 64),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.fresh()
                _, path = self.plan()
                self.rewrite_plan(path, change)
                with self.assertRaises(Refused):
                    self.staged(path)
                with self.assertRaises(Refused):
                    self.activate(path)
                self.assertFalse(os.path.lexists(self.dest), "nothing was staged")
                self.assertEqual(_staging.snapshot_tree(self.home), self.before)

    def test_a_plan_whose_state_or_targets_changed_since_it_was_made_admits_nothing(self):
        for what in ("first write", "state root", "target"):
            with self.subTest(changed=what):
                self.fresh()
                _, path = self.plan()
                if what == "first write":
                    (self.state / "outbox.d").mkdir()
                elif what == "state root":
                    self.state.rename(self.state.with_name("chat-moved"))
                    shutil.copytree(self.state.with_name("chat-moved"), self.state)  # same path, another inode
                else:
                    (self.skills / "chat/scripts/chatlib.py").write_text("# changed after the plan\n")
                with self.assertRaises(Refused):
                    self.staged(path)
                self.assertFalse(os.path.lexists(self.dest))

    def test_a_plan_is_never_reused_for_another_operation(self):
        _, first = self.activated()
        with self.assertRaisesRegex(Refused, "already ran"):
            self.staged(first)
        self.assertEqual(self.activate(first)["state"], "already selected")  # its own operation: read-only
        self.fresh()
        _, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("bound", nth=2))
        self.rollback(path)
        with self.assertRaisesRegex(Refused, "rolled back"):
            self.activate(path)
        with self.assertRaisesRegex(Refused, "already ran"):
            self.staged(path)

    def test_a_second_operation_is_refused_while_another_is_selected_or_unfinished(self):
        for state in ("unfinished", "selected"):
            with self.subTest(other=state):
                self.fresh()
                _, path = self.prepared()
                if state == "unfinished":
                    with self.assertRaises(Crash):
                        self.activate(path, crash=self.crash_on("bootstrap"))
                else:
                    self.activate(path)
                plan = json.loads(path.read_text())
                plan["operation"] = "activate-0000000000000000"
                plan["digest"] = stage_release.plan_digest(plan)
                second = self.where / "second-plan.json"
                second.write_text(json.dumps(plan))
                held = _staging.snapshot_tree(self.home)
                with self.assertRaisesRegex(Refused, f"is {state}"):
                    self.activate(second)
                self.assertEqual(_staging.snapshot_tree(self.home), held)

    def test_no_plan_is_made_over_a_bootstrap_record_already_present(self):
        _staging.write(self.record_path(), "{}\n", 0o600)
        with self.assertRaisesRegex(Refused, "owner decision"):
            self.plan()


class BootstrapPublicationTests(ActivationCase):
    """The amendment's section 2 and the APPROVE's spec addition 2: the record's
    exact bytes come from the sealed plan; it is published without replacing
    anything, reconciled as this operation's own, and only then is D journaled
    and the LaunchAgent bound with exactly one option."""

    def test_the_record_comes_from_the_sealed_plan_and_its_digest_is_the_launch_option(self):
        plan, path = self.activated()
        data = self.record_path().read_bytes()
        self.assertEqual(data, activate_release.bootstrap_bytes(plan))
        self.assertEqual(self.record_path().stat().st_mode & 0o777, 0o600)
        record = json.loads(data)
        self.assertEqual(record, {
            "v": "designation-bootstrap/1", "operation": plan["operation"], "release": plan["manifest_sha256"],
            "release_id": plan["release"], "state_root": plan["state_root"], "accounts": sorted(ACCOUNTS),
            "plan_sha256": plan["digest"], "time": plan["made_at"]})
        st = os.stat(self.state)
        self.assertEqual((record["state_root"]["dev"], record["state_root"]["ino"]), (st.st_dev, st.st_ino))
        d = activate_release.sha256_bytes(data)
        bin_dir = Path(plan["staged_release"]) / "env/bin"
        bridge = plistlib.loads((self.home / BRIDGE_PLIST).read_bytes())
        self.assertEqual(bridge["ProgramArguments"], [str(bin_dir / "chat-bridge"), "--activation-bootstrap",
                                                      f"sha256:{d}"])
        rotate = plistlib.loads((self.home / ROTATE_PLIST).read_bytes())
        self.assertEqual(rotate["ProgramArguments"], [str(bin_dir / "rotate-logs")], "no option for log rotation")
        kinds = self.kinds()
        self.assertEqual(kinds.count("bootstrap"), 1)
        self.assertLess(kinds.index("bootstrap-published"), kinds.index("bootstrap"))
        self.assertLess(kinds.index("bootstrap"), kinds.index("binding"), "D is journaled before any binding")
        self.assertEqual(next(r for r in self.journal() if r["kind"] == "bootstrap")["digest"], d)
        self.assertEqual([n for n in os.listdir(self.state) if n.endswith(".tmp")], [],
                         "this operation's own staged copy was removed once published")

    def test_an_explicit_empty_list_is_published(self):
        self.ops = ops(producers=None)
        self.activated(designations=[])
        self.assertEqual(json.loads(self.record_path().read_bytes())["accounts"], [])

    def test_a_crash_at_each_publication_step_resumes_to_the_same_record(self):
        for kind in ("bootstrap-intent", "bootstrap-staged", "bootstrap-published", "bootstrap"):
            with self.subTest(crash_after=kind):
                self.fresh()
                plan, path = self.prepared()
                with self.assertRaises(Crash):
                    self.activate(path, crash=self.crash_on(kind))
                inode_before = os.lstat(self.record_path()).st_ino if os.path.lexists(self.record_path()) else None
                self.assertEqual(self.activate(path)["state"], "selected")
                self.assertEqual(self.record_path().read_bytes(), activate_release.bootstrap_bytes(plan))
                if inode_before is not None:
                    self.assertEqual(os.lstat(self.record_path()).st_ino, inode_before, "never replaced")
                self.assertEqual(self.kinds().count("bootstrap"), 1)
                self.assertChecksPass(self.postcheck(path))

    def test_a_crash_after_the_link_before_the_directory_sync(self):
        plan, path = self.prepared()
        real, calls = activate_release.fsync_dir, []

        def fsync_dir(p):
            if Path(p) == self.state:
                calls.append(p)
                if len(calls) == 1:
                    raise Crash("killed after the link, before the directory sync")
            return real(p)
        with mock.patch.object(activate_release, "fsync_dir", fsync_dir), self.assertRaises(Crash):
            self.activate(path)
        self.assertTrue(self.record_path().exists(), "linked")
        self.assertNotIn("bootstrap", self.kinds(), "D is not journaled before the reconciliation")
        staged = next(r for r in self.journal() if r["kind"] == "bootstrap-staged")
        self.activate(path)
        st = os.lstat(self.record_path())
        self.assertEqual([st.st_dev, st.st_ino], staged["inode"], "the very file this operation staged")
        self.assertEqual(self.kinds().count("bootstrap"), 1)

    def test_an_unrecorded_staged_copy_is_kept_as_cleanup_debt(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("bootstrap-intent"))
        intent = next(r for r in self.journal() if r["kind"] == "bootstrap-intent")
        stray = self.state / intent["tmp"]
        _staging.write(stray, activate_release.bootstrap_bytes(plan).decode(), 0o600)  # as if made before the kill
        self.activate(path)
        done = next(r for r in self.journal() if r["kind"] == "bootstrap")
        self.assertEqual(done["cleanup_debt"], [intent["tmp"]])
        self.assertTrue(stray.exists(), "kept, not deleted")
        self.assertNotEqual(os.lstat(self.record_path()).st_ino, os.lstat(stray).st_ino,
                            "matching bytes never prove ownership: a new copy was staged and published")

    def test_a_record_this_operation_did_not_publish_is_retained_and_stops_it(self):
        def same_bytes_new_inode(plan):
            time.sleep(0.05)  # past the file system's timestamp granularity
            data = self.record_path().read_bytes()
            self.record_path().unlink()  # a file system may hand the replacement the same inode number
            _staging.write(self.record_path(), data.decode(), 0o600)

        def rewritten_in_place(plan):
            time.sleep(0.05)
            data = self.record_path().read_bytes()
            with open(self.record_path(), "r+b") as f:  # the same inode, the same bytes, written again
                f.write(data)

        def appears(plan):
            _staging.write(self.record_path(), activate_release.bootstrap_bytes(plan).decode(), 0o600)
        cases = {"published by someone else first": (None, appears),
                 "published by someone else after it was staged": ("bootstrap-staged", appears),
                 "replaced after publication": ("bootstrap-published", same_bytes_new_inode),
                 "replaced after D was journaled": ("bootstrap", same_bytes_new_inode),
                 "rewritten in place after D was journaled": ("bootstrap", rewritten_in_place)}
        for name, (kind, change) in cases.items():
            with self.subTest(case=name):
                self.fresh()
                plan, path = self.prepared()
                if kind:
                    with self.assertRaises(Crash):
                        self.activate(path, crash=self.crash_on(kind))
                change(plan)
                held = _staging.snapshot_tree(self.state)
                targets = self.targets_snapshot()
                with self.assertRaisesRegex(Refused, "retained"):
                    self.activate(path)
                self.assertEqual(_staging.snapshot_tree(self.state), held, "retained, nothing replaced")
                self.assertEqual(self.targets_snapshot(), targets, "nothing more was bound")


class LaunchAgentTests(ActivationCase):
    """The APPROVE's correction: the binding and the post-check verify launchd's
    effective executable, not only ProgramArguments; both accepted baseline
    forms, with or without a Program key, bind to the pinned release and
    restore exactly on an eligible reversal."""

    FORMS = {
        "the script alone": lambda script: {"ProgramArguments": [script]},
        "an interpreter and the script": lambda script: {"ProgramArguments": ["/usr/bin/python3", script]},
        "the script, with Program": lambda script: {"Program": script, "ProgramArguments": [script]},
        "an interpreter, with Program": lambda script: {"Program": "/usr/bin/python3",
                                                        "ProgramArguments": ["/usr/bin/python3", script]},
    }

    def test_each_accepted_baseline_form_binds_to_the_pinned_release_and_restores_exactly(self):
        for name, form in self.FORMS.items():
            with self.subTest(baseline=name):
                self.fresh(baseline=form)
                originals = {rel: (self.home / rel).read_bytes() for rel in (BRIDGE_PLIST, ROTATE_PLIST)}
                plan, path = self.activated()
                bin_dir = Path(plan["staged_release"]) / "env/bin"
                for rel, command in ((BRIDGE_PLIST, "chat-bridge"), (ROTATE_PLIST, "rotate-logs")):
                    bound = plistlib.loads((self.home / rel).read_bytes())
                    self.assertNotIn("Program", bound)
                    self.assertEqual(activate_release.effective_executable(bound), str(bin_dir / command))
                    self.assertEqual(bound["EnvironmentVariables"], plistlib.loads(originals[rel])[
                        "EnvironmentVariables"], "nothing else in the LaunchAgent changes")
                self.serving()
                self.ops.bridge = {"running": True, "release": plan["release"]}
                self.assertTrue(self.postcheck(path)["ok"])
                self.ops.bridge = {"running": False, "release": None}
                self.rollback(path)
                for rel, data in originals.items():
                    self.assertEqual((self.home / rel).read_bytes(), data, "restored exactly")

    def test_a_program_key_that_overrides_the_bound_executable_fails_the_postcheck(self):
        plan, path = self.activated()
        self.serving()
        self.ops.bridge = {"running": True, "release": plan["release"]}
        self.assertTrue(self.postcheck(path)["ok"])
        bound = plistlib.loads((self.home / BRIDGE_PLIST).read_bytes())
        bound["Program"] = str(self.skills / "chat/scripts/chat-bridge")
        (self.home / BRIDGE_PLIST).write_bytes(plistlib.dumps(bound))
        report = self.postcheck(path)
        self.assertFalse(report["ok"])
        self.assertIn("Program key", dict(report["checks"])["chat-bridge LaunchAgent"])


class PostcheckTests(ActivationCase):
    """The post-check is read-only and never opens the outbox database; the
    activation is complete only with the record, D, every target, and the
    expected new bridge serving (review correction 2; amendment section 2)."""

    def test_complete_only_with_the_expected_new_bridge_serving(self):
        plan, path = self.activated()
        before = _staging.snapshot_tree(self.home)
        with self.no_database():
            self.assertFalse(self.postcheck(path)["ok"], "the bridge is not running yet")
            self.ops.bridge = {"running": True, "release": plan["release"]}
            self.assertFalse(self.postcheck(path)["ok"], "its authority isn't serving")
            self.serving()
            report = self.postcheck(path)
        self.assertTrue(report["ok"], report)
        self.assertChecksPass(report)
        self.assertEqual({k: v for k, v in _staging.snapshot_tree(self.home).items()
                          if not k.endswith("outbox-status.json")}, before, "read-only")

    def append_to_staged_manifest(self):
        manifest = Path(json.loads(next(self.where.glob("activation-plan-*.json")).read_text())["staged_release"])
        manifest = manifest / "artifacts/manifest.json"
        manifest.chmod(0o644)
        with open(manifest, "a") as f:
            f.write("\n")

    def rewrite_agent(self, change):
        data = plistlib.loads((self.home / BRIDGE_PLIST).read_bytes())
        data["ProgramArguments"] = change(data["ProgramArguments"])
        (self.home / BRIDGE_PLIST).write_bytes(plistlib.dumps(data))

    def test_a_record_or_invocation_mismatch_fails_the_postcheck(self):
        changes = {
            "a changed record": lambda: self.record_path().write_bytes(b'{"v":"designation-bootstrap/1"}\n'),
            "a missing record": lambda: self.record_path().unlink(),
            "another launch digest": lambda: self.rewrite_agent(lambda a: a[:2] + ["sha256:" + "0" * 64]),
            "a staged manifest that is not the record's hash": lambda: self.append_to_staged_manifest(),
            "an extra option": lambda: self.rewrite_agent(lambda a: a + ["--more"]),
        }
        for name, change in changes.items():
            with self.subTest(case=name):
                self.fresh()
                plan, path = self.activated()
                self.serving()
                self.ops.bridge = {"running": True, "release": plan["release"]}
                change()
                self.assertFalse(self.postcheck(path)["ok"])

    def test_an_unexpected_or_unknown_bridge_blocks_any_mutation(self):
        for bridge in ({"running": True, "release": "skills-tree"}, {"running": True, "release": "plateia-chat-0.0.1"},
                       {"running": None, "release": None, "unknown": "invented"}):
            with self.subTest(bridge=bridge):
                self.fresh()
                plan, path = self.prepared()
                self.ops.bridge = bridge
                with self.assertRaisesRegex(Refused, "blocked"):
                    self.activate(path)
                self.assertEqual(_staging.snapshot_tree(self.home), self.before)

    def test_the_expected_new_bridge_allows_only_a_read_only_reconcile(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("bound", nth=7))
        self.ops.bridge = {"running": True, "release": plan["release"]}
        bound = _staging.snapshot_tree(self.home)
        self.assertEqual(self.activate(path)["state"], "selected", "reconciled from what is already bound")
        self.assertEqual(_staging.snapshot_tree(self.home), bound, "without writing anything")
        self.fresh()
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("bound", nth=2))
        self.ops.bridge = {"running": True, "release": plan["release"]}
        bound = _staging.snapshot_tree(self.home)
        with self.assertRaisesRegex(Refused, "while a bridge runs"):
            self.activate(path)
        self.assertEqual(_staging.snapshot_tree(self.home), bound)


class ResumeTests(ActivationCase):
    """A6 and the review's first correction: a crash at any boundary resumes,
    for the original plan's own journaled operation only, to the same result;
    a concurrent upgrader and a target changed or swapped by anything else
    prevent action; releases still referenced are kept."""

    def test_a_crash_after_every_journal_record_resumes_to_the_same_bindings(self):
        self.activated()
        records = len(self.journal())
        self.assertGreater(records, 20)
        for n in range(1, records + 1):
            with self.subTest(crash_after_record=n):
                self.fresh()
                plan, path = self.prepared()
                with self.assertRaises(Crash):
                    self.activate(path, crash=self.crash_at(n))
                self.assertIn(self.activate(path)["state"], ("selected", "already selected"))
                self.assertChecksPass(self.postcheck(path))
                journal = self.journal()
                self.assertEqual(sorted(r["target"] for r in journal if r["kind"] == "backup"),
                                 sorted(t["name"] for t in plan["targets"]), "each original backed up once")
                self.assertEqual([r["kind"] for r in journal].count("bootstrap"), 1)

    def test_a_crash_inside_a_target_write_resumes(self):
        plan, path = self.prepared()
        real, calls = activate_release.write_target, []

        def write_target(t, p, state):
            calls.append(t["name"])
            real(t, p, state)
            if len(calls) == 3:
                raise Crash("killed after the write, before it was journaled")
        with mock.patch.object(activate_release, "write_target", write_target), self.assertRaises(Crash):
            self.activate(path)
        self.activate(path)
        self.assertChecksPass(self.postcheck(path))

    def test_a_changed_plan_cannot_resume_the_operation(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("bound", nth=2))
        held = _staging.snapshot_tree(self.home)
        self.rewrite_plan(path, lambda p: p["designations"].append("mallory"))
        with self.assertRaisesRegex(Refused, "altered"):
            self.activate(path)
        self.assertEqual(_staging.snapshot_tree(self.home), held)

    def test_a_concurrent_upgrader_is_refused(self):
        plan, path = self.prepared()
        with activate_release.Journal(self.root):
            with self.assertRaisesRegex(Refused, "another activation holds"):
                self.activate(path)
            with self.assertRaisesRegex(Refused, "another activation holds"):
                self.rollback(path)
        self.assertEqual(_staging.snapshot_tree(self.home), self.before)

    def test_a_target_changed_or_swapped_mid_activation_blocks(self):
        for how in ("changed", "swapped for a symlink"):
            with self.subTest(how=how):
                self.fresh()
                plan, path = self.prepared()
                with self.assertRaises(Crash):
                    self.activate(path, crash=self.crash_on("bound", nth=2))
                scripts = self.skills / "chat/scripts"
                if how == "changed":
                    (scripts / "chatlib.py").write_text("# changed by something else\n")
                else:
                    shutil.copytree(scripts, self.where / "elsewhere", symlinks=True)
                    shutil.rmtree(scripts)
                    scripts.symlink_to(self.where / "elsewhere")
                held = _staging.snapshot_tree(self.home)
                with self.assertRaisesRegex(Refused, "changed by something else|not a real directory"):
                    self.activate(path)
                self.assertEqual(_staging.snapshot_tree(self.home), held)

    def test_the_staged_release_and_every_backup_are_kept(self):
        plan, path = self.activated()
        self.rollback(path)
        self.assertTrue(Path(plan["staged_release"]).is_dir(), "a release still referenced is retained")
        backups = self.root / "backups" / plan["operation"]
        self.assertEqual(len(list(backups.iterdir())), 7)
        for f in backups.rglob("*"):
            if f.is_file():
                self.assertEqual(f.stat().st_mode & 0o777, 0o600, f"{f.name} is private")


class QuietWriterTests(ActivationCase):
    """A3 and the review's sixth addition: every legacy writer, running or
    cached, must be at an acknowledged checkpoint, and every designated
    account's producers tracked, before anything changes. A delivered request
    is not an acknowledgement, unknown is never quiet, and nothing is
    interrupted, restarted or killed, nor the list changed."""

    def test_anything_short_of_acknowledged_quiet_blocks_before_any_change(self):
        writers = ops().writer_list
        cases = {
            "a delivered but unacknowledged request": ops(writers=[dict(writers[1], checkpoint="requested")]),
            "a cached producer not yet quiet": ops(writers=[dict(writers[2], checkpoint="requested")]),
            "an unknown checkpoint": ops(writers=[dict(writers[0], checkpoint=None)]),
            "an unknown kind of writer": ops(writers=[dict(writers[0], kind="mystery")]),
            "no checkpoint evidence": ops(writers=None),
            "an untracked producer": ops(producers={"alp-solver-2": "tracked", "sam": "untracked"}),
            "no producer evidence": ops(producers=None),
        }
        for name, fake in cases.items():
            with self.subTest(case=name):
                self.fresh()
                plan, path = self.prepared()
                self.ops = fake
                with self.assertRaisesRegex(Refused, "blocked"):
                    self.activate(path)
                self.assertEqual(_staging.snapshot_tree(self.home), self.before, "nothing was changed")
                self.assertNotIn("bootstrap-intent", self.kinds())
                self.assertEqual(json.loads(path.read_text())["designations"], sorted(ACCOUNTS),
                                 "the list is never changed")
        self.assertFalse(self.fake_log.exists(), "no service command was run")

    def test_an_explicit_empty_list_needs_no_producer_evidence(self):
        self.ops = ops(producers=None)
        self.activated(designations=[])
        self.assertEqual(self.kinds()[-1], "selected")

    def test_a_writer_of_the_new_release_is_not_a_legacy_writer(self):
        plan, path = self.prepared()
        self.ops.writer_list.append({"name": "new helper", "kind": "helper", "release": plan["release"],
                                     "checkpoint": None, "owner": "plat-manager"})
        self.assertEqual(self.activate(path)["state"], "selected")


class RollbackLimitTests(ActivationCase):
    """A7, the review's fifth addition, and the amendment's section 4: before
    any new-format write, the rollback restores the recorded skills-tree
    targets exactly (D-27), removing the launch option and leaving the
    bootstrap record in place; after the first outbox-db/1, outbox/2 or
    delivery-records/2 write, or with unreadable evidence, it is refused,
    whatever the journal last recorded, the evidence is kept, and the
    controller names repair or roll-forward. It never opens the outbox
    database."""

    def test_a_rollback_before_any_new_format_write_restores_the_targets_exactly(self):
        plan, path = self.activated()
        self.assertNotEqual(self.targets_snapshot(), self.before_targets())
        record = self.record_path().read_bytes()
        with self.no_database():
            summary = self.rollback(path)
        self.assertEqual(summary["state"], "rolled-back")
        self.assertEqual(self.targets_snapshot(), self.before_targets(), "the recorded targets, exactly")
        self.assertEqual(self.record_path().read_bytes(), record, "the bootstrap record is left in place")
        retained = next(r for r in self.journal() if r["kind"] == "bootstrap-retained")
        self.assertTrue(retained["present"])
        self.assertEqual(self.rollback(path)["state"], "already rolled back")

    def test_a_rollback_after_a_first_new_format_write_is_refused(self):
        writes = {
            "outbox-db/1": lambda: (self.state / "outbox.db").write_bytes(b"SQLite format 3\x00 invented"),
            "outbox-db/1, its WAL alone": lambda: (self.state / "outbox.db-wal").write_bytes(b"invented"),
            "outbox/2": lambda: _staging.write(self.state / "outbox.d" / "fallback-invented.json",
                                               '{"v": "outbox/2"}\n'),
            "delivery-records/2": lambda: _staging.write(
                self.state / "dead-letters.jsonl",
                '{"id": "d-1", "state": "delivered"}\n{"id": "p-1", "version": 2, "supersedes": 1}\n'),
            "an unreadable delivery record line": lambda: _staging.write(
                self.state / "dead-letters.jsonl", '{"id": "d-1", "state": "delivered"}\n{"id": "p-1", "ver\n'),
            "unreadable delivery records": lambda: (self.state / "dead-letters.jsonl").chmod(0),
        }
        for boundary, write in writes.items():
            with self.subTest(boundary=boundary):
                self.fresh()
                plan, path = self.activated()
                bound = self.targets_snapshot()
                if boundary == "unreadable delivery records":
                    state = _staging.snapshot_tree(self.state)
                    write()
                else:
                    write()
                    state = _staging.snapshot_tree(self.state)
                with self.no_database(), self.assertRaisesRegex(Refused, r"repair or roll-forward"):
                    self.rollback(path)
                (self.state / "dead-letters.jsonl").chmod(0o644)
                self.assertEqual(self.targets_snapshot(), bound, "nothing was rolled back")
                self.assertEqual(_staging.snapshot_tree(self.state), state, "the evidence is kept")

    def test_a_crash_before_the_controller_acknowledged_still_refuses_after_a_first_write(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("bound", nth=3))
        (self.state / "outbox.db").write_bytes(b"SQLite format 3\x00 the bridge's first commit (invented)")
        self.assertNotIn("selected", self.kinds())
        with self.assertRaisesRegex(Refused, r"repair or roll-forward"):
            self.rollback(path)

    def test_a_running_or_unknown_bridge_defers_the_rollback(self):
        plan, path = self.activated()
        for bridge in ({"running": True, "release": plan["release"]}, {"running": None}):
            with self.subTest(bridge=bridge):
                self.ops.bridge = bridge
                held = _staging.snapshot_tree(self.home)
                with self.assertRaisesRegex(Refused, "refused for now"):
                    self.rollback(path)
                self.assertEqual(_staging.snapshot_tree(self.home), held)

    def test_a_target_changed_by_something_else_blocks_the_rollback(self):
        plan, path = self.activated()
        os.unlink(self.home / ".local/bin/pchat")
        os.symlink("/invented/elsewhere", self.home / ".local/bin/pchat")
        held = _staging.snapshot_tree(self.home)
        with self.assertRaisesRegex(Refused, "changed by something else"):
            self.rollback(path)
        self.assertEqual(_staging.snapshot_tree(self.home), held)


class TargetTests(ActivationCase):
    """A2 and D-26: every one of the seven targets resolves to the one pinned,
    staged release, and the chat/scripts import location hands off to it
    without a source checkout on the import path."""

    def test_the_seven_targets_resolve_to_the_pinned_release(self):
        plan, path = self.activated()
        rel = Path(plan["staged_release"])
        bin_dir = rel / "env/bin"
        self.assertEqual(os.readlink(self.home / ".local/bin/pchat"), str(bin_dir / "pchat"))
        role = Path(os.readlink(self.home / ".local/share/weechat/python/autoload/role_colors.py"))
        self.assertTrue(role.is_relative_to(rel) and role.name == "role_colors.py")
        self.assertEqual(os.readlink(self.home / ".claude/skills/chat"), str(self.skills / "chat"))
        self.assertEqual((self.skills / "chat/SKILL.md").read_bytes(),
                         (_support.CHECKOUT / "packages/chat/SKILL.md").read_bytes())
        for name in ("chatlib.py", "delivery_store.py", "pchat", "chat-bridge"):
            self.assertIn(str(rel), (self.skills / "chat/scripts" / name).read_text())
        change = next(r for r in self.journal() if r["kind"] == "selected")["skills_repository_change"]
        self.assertIn("chat/SKILL.md", {c["path"] for c in change})
        self.assertIn("chat/scripts/chatlib.py", {c["path"] for c in change})
        self.assertTrue(all(c["path"].startswith("chat/") for c in change))

    def test_a_module_imported_by_path_loads_from_the_release_alone(self):
        plan, path = self.activated()
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import chatlib, identities; "
                "print(chatlib.__file__); print(identities.__file__); print('\\n'.join(sys.path))")
        out = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(self.skills / "chat/scripts")],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        for f in lines[:2]:
            self.assertTrue(Path(f).is_relative_to(plan["staged_release"]), f)
        self.assertFalse(any(p.startswith(str(_support.CHECKOUT)) for p in lines[2:] if p),
                         "no source checkout on the import path")


class PreservationTests(ActivationCase):
    """A4: activating changes nothing in the chat state but the one new
    bootstrap record, and no configuration, credential or project state; no
    private value reaches the journal or the results."""

    def test_the_state_config_and_project_state_are_preserved(self):
        plan, path = self.activated()
        after = _staging.snapshot_tree(self.home)
        changed = {k for k in set(after) | set(self.before) if after.get(k) != self.before.get(k)}
        self.assertEqual({k for k in changed if k.startswith(".local/state/chat")},
                         {".local/state/chat/designation-bootstrap.json"})
        self.assertFalse({k for k in changed if k.startswith((".config/", ".local/state/project-manager",
                                                              ".local/state/ergo"))})
        text = (self.root / "activation-journal.jsonl").read_text() + json.dumps(self.postcheck(path), default=str)
        for secret in _staging.SECRETS:
            self.assertNotIn(secret, text)
        record = json.loads(self.record_path().read_text())
        self.assertEqual(set(record), {"v", "operation", "release", "release_id", "state_root", "accounts",
                                       "plan_sha256", "time"}, "no config or credential is migrated")
        for secret in _staging.SECRETS:
            self.assertNotIn(secret, self.record_path().read_text())


class OperatorPlanTests(ActivationCase):
    """A9: the private operator plan names everything an operator publishes."""

    def test_the_operator_plan_names_every_required_item(self):
        plan, path = self.plan()
        text = activate_release.render_operator_plan(plan)
        for item in [plan["source_commit"], plan["release"], plan["manifest_sha256"], "private environment",
                     "private activation root", "chat state root", "migration point", "designation list",
                     "postchecks", "permitted recovery", "skills repository", "safe checkpoint",
                     "preserved state", "affected helper", "--activation-bootstrap",
                     "sha256:" + activate_release.sha256_bytes(activate_release.bootstrap_bytes(plan))] \
                + [a["sha256"] for a in plan["artifacts"]] + [t["name"] for t in plan["targets"]] \
                + [c["owner"] for c in plan["checkpoints"]] + ACCOUNTS:
            self.assertIn(item, text)
        for secret in _staging.SECRETS:
            self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
