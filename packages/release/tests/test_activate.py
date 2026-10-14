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
import atexit
import contextlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
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
    the legacy writers with their checkpoint state, each designated account's
    producers, the boot (a fake boot id; reboot() changes it) and the owner's
    inhibition evidence (complete, no unmanaged launcher, unless a test says
    otherwise). It only answers reads: nothing here can start, stop, signal or
    restart anything."""

    boot_id = "boot-A"
    inhibition_evidence = {"complete": True, "launchers": []}

    def boot(self):
        return self.boot_id

    def reboot(self):
        """The owner's reboot (writer admission 8.1; gate G-1), invented: a new boot id."""
        self.boot_id = f"boot-{chr(ord(self.boot_id[-1]) + 1)}"

    def inhibition(self):
        return None if self.inhibition_evidence is None else dict(self.inhibition_evidence)

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

    def authority_status(self, plan):
        """A live answer from the bridge's authority over its socket, as the
        status request returns it; None when nothing answers."""
        return dict(self.live) if getattr(self, "live", None) else None


def ops(bridge=None, writers="default", producers="default"):
    return FakeOps(bridge, writers, producers)


_BUILT = {}


def built_release():
    """#19's real release (its release.json), built once per test process from an invented repository and
    shared by every activation test class: tests only read it (each stages its own copy), and a build per class
    cost about four seconds each on CI."""
    if not _BUILT:
        tmp = tempfile.TemporaryDirectory(prefix="plateia-activation-release-")
        atexit.register(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        repo, _ = make_repo(root)
        release, _ = build_release.build(repo, root / "releases")
        _BUILT.update(release=release, manifest=build_release.verify(release))
    return _BUILT["release"], _BUILT["manifest"]


class ActivationCase(unittest.TestCase):
    """A test-free base: each case gets a fresh invented home (fresh() makes
    another inside one test, for subTest loops)."""

    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()
        cls.release, cls.manifest = built_release()

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
        """activate as the published operator plan runs it (writer admission 9.1): when it reports the targets
        bound and full waiting for retirement, with no bridge running, the invented host reboots (the owner's
        step) and activate runs again to write full. The first run's result is returned, with the second's
        maintenance outcome. One crash hook spans both runs."""
        result = activate_release.activate(path, self.ops, spec_path=self.spec, crash=crash)
        if (result.get("maintenance") or {}).get("next") == "reboot" and self.ops.bridge.get("running") is False:
            self.ops.reboot()
            again = activate_release.activate(path, self.ops, spec_path=self.spec, crash=crash)
            result = dict(result, maintenance=again.get("maintenance"))
        return result

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

    def serving(self, epoch=2):
        """The new bridge's authority serving: its status snapshot, as it writes
        it, and a live answer to the read-only status request."""
        _staging.write(self.state / "outbox-status.json",
                       json.dumps({"waiting": 0, "epoch": epoch, "unavailable": None, "written_at": time.time() + 5}))
        self.ops.live = {"v": "outbox-authority/1", "result": "ok", "authority_epoch": epoch,
                         "status": {"waiting": 0, "checking": 0, "held": 0, "suspended": [], "dead": 0, "epoch": epoch}}

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
        """Exactly the one transition: #19's delta, designation-bootstrap, maintenance (amendment 4) and #27's
        request-line, a library format no running tool uses (#21 amendment 6)."""
        _, path = self.plan()
        summary, _, report = self.staged(path)
        self.assertEqual(summary["state"], "staged")
        self.assertEqual(report["refused"], [])
        admitted = sorted(r[1] for r in report["results"] if r[0] == "admitted")
        self.assertEqual(admitted, ["boot-id", "delivery-records", "designation-bootstrap", "maintenance", "outbox",
                                    "outbox-authority", "outbox-db", "request-line", "tcp-sockets"])
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
            # the first sync of the chat-state directory once the record is linked: the post-link boundary
            if isinstance(p, int) and os.path.samestat(os.fstat(p), os.stat(self.state)) \
                    and self.record_path().exists():
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

    def test_a_crash_after_every_kind_of_journal_record_resumes_to_the_same_bindings(self):
        """A crash after the first and after the last record of every kind the
        activation journals: every distinct boundary, each from both ends.
        (Repeats of one kind, such as one entry write after another, cross the
        same boundary; an interruption between two of them has its own test.)"""
        self.activated()
        kinds = [r["kind"] for r in self.journal()]
        self.assertGreater(len(set(kinds)), 10)
        points = sorted({kinds.index(k) + 1 for k in kinds} | {len(kinds) - kinds[::-1].index(k) for k in kinds})
        for n in points:
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
        real, calls = activate_release.write_entry_at, []

        def write_entry_at(fd, name, entry, tmp):
            calls.append(name)
            real(fd, name, entry, tmp)
            if len(calls) == 3:
                raise Crash("killed after the write, before it was journaled")
        with mock.patch.object(activate_release, "write_entry_at", write_entry_at), self.assertRaises(Crash):
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
                with self.assertRaisesRegex(Refused, "changed by something else|not a real directory|can't be opened"):
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
    """A3, as #21's owner amendment 4 (2026-10-13) leaves it: the quiet-writer
    checks are replaced by writer admission (test_activation_admission: the
    record fences new-release writers; retirement and inhibition cover legacy
    ones before full). Its kept case: every designated account's producers
    must be tracked before anything changes, and the list is never changed."""

    def test_an_untracked_producer_blocks_before_any_change(self):
        cases = {"an untracked producer": ops(producers={"alp-solver-2": "tracked", "sam": "untracked"}),
                 "no producer evidence": ops(producers=None)}
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
        self.assertIn("selected", self.kinds())

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
                         {".local/state/chat/designation-bootstrap.json", ".local/state/chat/maintenance.json",
                          ".local/state/chat/admission.lock"},
                         "and writer admission's record and lock (#21 amendment 4)")
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


class SlotTwoFindingTests(ActivationCase):
    """Code review slot 2 on PR #22 (6110706975): each finding, reproduced on
    invented fixtures before its fix."""

    # 1. Rollback judges the sealed state root, not whatever is at its path.
    def test_a_replaced_missing_or_unreadable_state_root_refuses_rollback(self):
        for how in ("replaced", "missing", "unreadable"):
            with self.subTest(root=how):
                self.fresh()
                plan, path = self.activated()
                (self.state / "outbox.db").write_bytes(b"SQLite format 3\x00 invented first write")
                moved = self.state.with_name("chat-moved")
                self.state.rename(moved)
                if how == "replaced":
                    self.state.mkdir()
                elif how == "unreadable":
                    self.state.mkdir()
                    self.state.chmod(0)
                bound = self.targets_snapshot() if how != "unreadable" else None
                try:
                    with self.assertRaisesRegex(Refused, "repair or roll-forward"):
                        self.rollback(path)
                finally:
                    if self.state.exists():
                        self.state.chmod(0o700)
                if bound is not None:
                    self.assertEqual(self.targets_snapshot(), bound, "nothing was rolled back")

    # 2. A target swapped after it was checked is never written through.
    def test_a_folder_or_parent_swapped_mid_binding_is_never_written_through(self):
        for target, folder in (("chat/scripts import location", "chat/scripts"), ("pchat", "../../.local/bin")):
            with self.subTest(target=target):
                self.fresh()
                plan, path = self.prepared()
                real = (self.skills / folder).resolve()
                elsewhere = self.where / "elsewhere"

                def swap(record):
                    if record["kind"] == "binding" and record["target"] == target and not elsewhere.exists():
                        shutil.copytree(real, elsewhere, symlinks=True)
                        shutil.rmtree(real)
                        real.symlink_to(elsewhere)
                with self.assertRaises(Refused):
                    self.activate(path, crash=swap)
                copy = _staging.snapshot_tree(elsewhere)
                self.assertNotIn("selected", self.kinds())
                with self.assertRaises(Refused):
                    self.activate(path)
                self.assertEqual(_staging.snapshot_tree(elsewhere), copy, "nothing written into the other folder")

    # 3. The journal is fenced: its root, lock and file are pinned.
    def test_a_renamed_or_replaced_journal_root_cannot_split_the_operation(self):
        self.root.mkdir(parents=True)
        first = activate_release.Journal(self.root)
        try:
            self.root.rename(self.root.with_name("activation-moved"))
            self.root.mkdir()
            second = activate_release.Journal(self.root)
            second.close()
            with self.assertRaisesRegex(Refused, "journal"):
                first.append({"op": "activate-0000000000000000", "kind": "admitted"})
            self.assertEqual(activate_release.read_journal(self.root), [], "nothing reached the new root's journal")
        finally:
            first.close()

    def test_a_symlinked_journal_root_lock_or_file_is_refused(self):
        other = self.where / "other"
        other.mkdir()
        for what in ("root", "lock", "journal"):
            with self.subTest(substituted=what):
                root = self.where / f"root-{what}"
                if what == "root":
                    root.symlink_to(other)
                else:
                    root.mkdir()
                    name = activate_release.LOCK if what == "lock" else activate_release.JOURNAL
                    (root / name).symlink_to(other / name)
                with self.assertRaises(Refused):
                    activate_release.Journal(root).close()
                self.assertEqual(os.listdir(other), [])

    # 4. The staged release is verified against its recorded inventory, not trusted.
    def test_a_changed_staged_release_blocks_activation_and_fails_the_postcheck(self):
        def tamper(plan):
            target = Path(plan["staged_release"]) / "env/bin/chat-bridge"
            target.chmod(0o755)
            with open(target, "a") as f:
                f.write("# changed after staging\n")
        plan, path = self.prepared()
        tamper(plan)
        with self.assertRaisesRegex(Refused, "staged"):
            self.activate(path)
        self.assertEqual(self.targets_snapshot(), self.before_targets())
        self.fresh()
        plan, path = self.activated()
        self.serving()
        self.ops.bridge = {"running": True, "release": plan["release"]}
        self.assertTrue(self.postcheck(path)["ok"])
        tamper(plan)
        self.assertFalse(self.postcheck(path)["ok"])

    # 5. Every writer the sealed plan names needs current evidence.
    # 6. A folder interrupted between two entries resumes, and rolls back.
    def test_an_interruption_between_script_replacements_resumes_and_rolls_back(self):
        for then in ("resume", "rollback"):
            with self.subTest(then=then):
                self.fresh()
                plan, path = self.prepared()
                real_replace, calls = os.replace, []

                def replace(src, dst, **kw):
                    real_replace(src, dst, **kw)
                    if str(dst).endswith(".py"):
                        calls.append(dst)
                        if len(calls) == 2:
                            raise Crash("killed between two script replacements")
                with mock.patch.object(activate_release.os, "replace", replace), self.assertRaises(Crash):
                    self.activate(path)
                if then == "resume":
                    self.assertEqual(self.activate(path)["state"], "selected")
                    self.assertChecksPass(self.postcheck(path))
                else:
                    self.rollback(path)
                    self.assertEqual(self.targets_snapshot(), self.before_targets())

    # 7. Only a live answer from the current authority proves it serves.
    def test_an_old_status_snapshot_proves_nothing(self):
        plan, path = self.activated()
        self.serving()
        self.ops.bridge = {"running": True, "release": plan["release"]}
        self.assertTrue(self.postcheck(path)["ok"])
        self.ops.live = None  # the bridge restarted into UNKNOWN: its old snapshot stays
        report = self.postcheck(path)
        self.assertFalse(report["ok"])
        self.assertFalse(report["authority_serving"])
        self.ops.live = {"v": "outbox-authority/1", "result": "refused", "authority_epoch": 3}
        self.assertFalse(self.postcheck(path)["ok"], "a refusal is not serving")

    # 9. A torn journal suffix is repaired under the lock before appending.
    def test_a_torn_journal_suffix_is_repaired_before_appending(self):
        self.root.mkdir(parents=True)
        complete = json.dumps({"op": "activate-0000000000000000", "kind": "admitted", "plan_sha256": "0" * 64,
                               "schema": "x", "at": "t"})
        (self.root / activate_release.JOURNAL).write_text(complete + "\n" + '{"op": "activate-00', encoding="utf-8")
        with activate_release.Journal(self.root) as journal:
            journal.append({"op": "activate-0000000000000000", "kind": "selected"})
        kinds = [r["kind"] for r in activate_release.read_journal(self.root)]
        self.assertEqual(kinds, ["admitted", "journal-repaired", "selected"])
        kept = list(self.root.glob("journal-torn-*"))
        self.assertEqual(len(kept), 1, "the dropped fragment is kept privately")
        self.assertEqual(kept[0].read_bytes(), b'{"op": "activate-00')

    def test_a_crash_during_a_journal_write_resumes(self):
        plan, path = self.prepared()
        real_write, writes = os.write, []

        def write(fd, data):
            data = bytes(data)
            if data.endswith(b"\n") and b'"kind": "binding"' in data:
                writes.append(data)
                if len(writes) == 2:
                    real_write(fd, data[: len(data) // 2])
                    raise Crash("killed during a journal write")
            return real_write(fd, data)
        with mock.patch.object(activate_release.os, "write", write), self.assertRaises(Crash):
            self.activate(path)
        self.assertEqual(self.activate(path)["state"], "selected")
        self.assertChecksPass(self.postcheck(path))


class SweepTests(ActivationCase):
    """The same classes as slot 2's findings, swept across the controller:
    crash windows, stale or absent evidence, and decoded shapes."""

    def test_a_crash_between_a_temporary_and_its_rename_leaves_nothing_behind(self):
        plan, path = self.prepared()
        real_replace, calls = os.replace, []

        scripts_stat = os.stat(self.skills / "chat/scripts")

        def replace(src, dst, **kw):
            if "dst_dir_fd" in kw and os.path.samestat(os.fstat(kw["dst_dir_fd"]), scripts_stat):
                calls.append(dst)
                if len(calls) == 1:
                    raise Crash("killed after the temporary was written, before its rename")
            return real_replace(src, dst, **kw)
        with mock.patch.object(activate_release.os, "replace", replace), self.assertRaises(Crash):
            self.activate(path)
        scripts = self.skills / "chat/scripts"
        self.assertTrue([n for n in os.listdir(scripts) if n.startswith(".plateia-activation-")])
        self.assertEqual(self.activate(path)["state"], "selected")
        self.assertEqual([n for n in os.listdir(scripts) if n.startswith(".plateia-activation-")], [])
        self.assertChecksPass(self.postcheck(path))

    def test_checkpoint_evidence_must_be_current(self):
        evidence = self.where / "evidence.json"
        live = activate_release.LiveOps(evidence)
        writers = [{"name": "pchat flusher", "kind": "flusher", "checkpoint": "acknowledged"}]
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        for name, observed, expect in (("undated", None, None),
                                       ("stale", (now - __import__("datetime").timedelta(hours=2)).isoformat(), None),
                                       ("current", now.isoformat(), writers)):
            with self.subTest(evidence=name):
                data = {"writers": writers, "producers": {}}
                if observed:
                    data["observed_at"] = observed
                evidence.write_text(json.dumps(data))
                self.assertEqual(live.writers(), expect)

    def test_a_failed_service_query_is_unknown_not_stopped(self):
        live = activate_release.LiveOps()
        for code, err, running in ((113, "Could not find service", False), (1, "Operation not permitted", None),
                                   (5, "", None)):
            with self.subTest(code=code), mock.patch.object(activate_release.subprocess, "run", return_value=mock.Mock(
                    returncode=code, stdout="", stderr=err)):
                self.assertIs(live.bridge_state()["running"], running)

    def test_the_status_client_comes_from_the_verified_wheel(self):
        plan, path = self.prepared()
        installed = next((Path(plan["staged_release"]) / "env").glob(
            "lib/python*/site-packages/plateia_chat/scripts/delivery_store.py"))
        installed.chmod(0o644)
        installed.write_text("raise SystemExit('the installed copy must never run')\n")
        module = activate_release.load_release_module(plan, "delivery_store")
        self.assertEqual(module.PROTOCOL, "outbox-authority/1")
        self.assertTrue(module.__file__.startswith("<"))
        artifacts = Path(plan["staged_release"]) / "artifacts"
        wheel = next(artifacts.glob("*.whl"))
        wheel.chmod(0o644)
        with open(wheel, "ab") as f:
            f.write(b"\0")
        with self.assertRaisesRegex(Refused, "not the manifest's"):
            activate_release.load_release_module(plan, "delivery_store")

    def test_malformed_plans_and_journals_are_refused_not_crashed(self):
        plan, path = self.plan()
        for name, change in (("designations not names", lambda p: p.update(designations=[1])),
                             ("a state root of the wrong shape", lambda p: p.update(state_root={"path": 1})),
                             ("targets not records", lambda p: p.update(targets=["pchat"])),
                             ("a relative root", lambda p: p.update(root="activation"))):
            with self.subTest(plan=name):
                bad = dict(json.loads(path.read_text()))
                change(bad)
                bad["digest"] = stage_release.plan_digest(bad)  # anyone can recompute a digest
                bad_path = self.where / "bad-plan.json"
                bad_path.write_text(json.dumps(bad))
                with self.assertRaisesRegex(Refused, "well-formed"):
                    activate_release.load_plan(bad_path)
        self.root.mkdir(parents=True)
        for line in ('{"kind": 7}', '{"op": "x", "kind": "backup", "target": "pchat"}', '[1, 2]'):
            with self.subTest(journal=line):
                (self.root / activate_release.JOURNAL).write_text(line + "\n")
                with self.assertRaisesRegex(Refused, "damaged"):
                    activate_release.read_journal(self.root)


BYTECODE_PROBE = """
import importlib.machinery, json, sys
attempts = []
real = importlib.machinery.SourceFileLoader.set_data
def spy(self, path, data, *, _mode=0o666):
    attempts.append(path)
    return real(self, path, data, _mode=_mode)
importlib.machinery.SourceFileLoader.set_data = spy
assert sys.dont_write_bytecode is False and sys.pycache_prefix is None, "bytecode must be enabled"
import plateia_chat
sys.path.insert(0, str(plateia_chat.SCRIPTS))
import chatlib, identities, delivery_store
print(json.dumps({"attempts": attempts, "dont_write_bytecode": sys.dont_write_bytecode,
                  "pycache_prefix": sys.pycache_prefix}))
"""


class GuardedSealTests(ActivationCase):
    """Owner amendment 3 (6111420402), section 7, and its APPROVE's four spec
    additions (6111462237): the guarded seal of exactly two staged directories."""

    def seal_dirs(self, plan):
        site = next((Path(plan["staged_release"]) / "env").glob("lib/python*/site-packages"))
        return [site / "plateia_chat", site / "plateia_chat/scripts"]

    @staticmethod
    def mode(p):
        return os.stat(p).st_mode & 0o777

    def seal_records(self):
        return [r for r in self.journal() if r["kind"] in ("release-seal-baseline", "release-sealed")]

    def test_a_seal_from_0755_journals_the_originals_and_changes_nothing_else(self):
        plan, path = self.prepared()
        dirs = self.seal_dirs(plan)
        parents = [dirs[0].parent, dirs[0].parent.parent, dirs[0].parent.parent.parent,
                   Path(plan["staged_release"]) / "env"]
        before = {p: (os.stat(p).st_mode, os.stat(p).st_uid) for p in parents}
        owners = [os.stat(p).st_uid for p in dirs]
        self.activate(path)
        self.assertEqual([self.mode(p) for p in dirs], [0o555, 0o555])
        self.assertEqual([os.stat(p).st_uid for p in dirs], owners, "ownership unchanged")
        self.assertEqual({p: (os.stat(p).st_mode, os.stat(p).st_uid) for p in parents}, before, "parents unchanged")
        kinds = self.kinds()
        self.assertLess(kinds.index("release-seal-baseline"), kinds.index("release-sealed"))
        self.assertLess(kinds.index("release-sealed"), kinds.index("binding"), "sealed before any target is bound")
        sealed = next(r for r in self.journal() if r["kind"] == "release-sealed")
        self.assertEqual([d["mode"] for d in sealed["dirs"]], [0o755, 0o755], "the originals are journaled")
        self.assertEqual([[d["dev"], d["ino"]] for d in sealed["dirs"]],
                         [[os.stat(p).st_dev, os.stat(p).st_ino] for p in dirs])
        for d, p in zip(sealed["dirs"], dirs):
            fd = os.open(p, os.O_RDONLY)
            try:
                self.assertEqual(activate_release.access_metadata(fd), d["access"], "ACL and flags unchanged")
            finally:
                os.close(fd)

    def test_every_other_baseline_refuses_before_either_directory_changes(self):
        def second_only(change):
            """Make only the second directory (scripts/) invalid: the first stays 0755."""
            return lambda dirs: change(dirs[1])
        cases = {
            "a stricter mode (0700)": (second_only(lambda p: p.chmod(0o700)), None),
            "a stricter mode (0555, unjournaled)": (second_only(lambda p: p.chmod(0o555)), None),
            "a different mode (0775)": (second_only(lambda p: p.chmod(0o775)), None),
            "a different mode (0750)": (second_only(lambda p: p.chmod(0o750)), None),
            "another owner": (None, ("current_uid", lambda: os.getuid() + 1)),
            "an ACL that could grant writes": (None, "acl"),
            "an unreadable ACL": (None, "unreadable"),
            "an ambiguous identity": (None, "identity"),
        }
        real_access = activate_release.access_metadata
        for name, (change, patch) in cases.items():
            with self.subTest(baseline=name):
                self.fresh()
                plan, path = self.prepared()
                dirs = self.seal_dirs(plan)
                if change:
                    change(dirs)
                calls = []

                def access(fd):
                    calls.append(fd)
                    if len(calls) == 2 and patch == "acl":
                        raise Refused("it has an ACL, which could grant writes the seal forbids")
                    if len(calls) == 2 and patch == "unreadable":
                        raise Refused("its ACL can't be read (EIO)")
                    return real_access(fd)
                patches = [mock.patch.object(activate_release, "access_metadata", access)]
                if isinstance(patch, tuple):
                    patches.append(mock.patch.object(activate_release, patch[0], patch[1]))
                if patch == "identity":
                    real_fstat, second = os.fstat, os.stat(dirs[1])

                    def fstat(fd):
                        st = real_fstat(fd)
                        if (st.st_dev, st.st_ino) == (second.st_dev, second.st_ino):
                            return os.stat_result((st.st_mode, 0, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
                                                   st.st_size, 0, 0, 0))
                        return st
                    patches.append(mock.patch.object(activate_release.os, "fstat", fstat))
                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)
                    with self.assertRaisesRegex(Refused, "blocked"):
                        self.activate(path)
                self.assertEqual(self.mode(dirs[0]), 0o755, "the first directory is unchanged")
                if not change:
                    self.assertEqual(self.mode(dirs[1]), 0o755)
                self.assertEqual(self.seal_records(), [], "nothing was journaled as sealed")
                self.assertEqual(self.targets_snapshot(), self.before_targets(), "nothing else changed")

    @unittest.skipUnless(sys.platform == "darwin", "a real extended ACL is set here with macOS chmod +a")
    def test_a_real_acl_that_defeats_the_seal_is_refused(self):
        plan, path = self.prepared()
        dirs = self.seal_dirs(plan)
        user = __import__("pwd").getpwuid(os.getuid()).pw_name
        subprocess.run(["/bin/chmod", "+a", f"{user} allow add_file,add_subdirectory", str(dirs[1])], check=True)
        probe = dirs[1].parent.parent / "acl-probe"
        probe.mkdir()
        try:  # the effective access a 0555 mode would not stop
            subprocess.run(["/bin/chmod", "+a", f"{user} allow add_file", str(probe)], check=True)
            probe.chmod(0o555)
            (probe / "written").write_text("an ACL let this through a 0555 directory\n")
        finally:
            probe.chmod(0o755)
            shutil.rmtree(probe)
        with self.assertRaisesRegex(Refused, "ACL"):
            self.activate(path)
        self.assertEqual([self.mode(p) for p in dirs], [0o755, 0o755])
        self.assertEqual(self.seal_records(), [])

    def test_resume_accepts_only_this_operations_journaled_seal(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.activate(path, crash=self.crash_on("release-sealed"))
        self.assertEqual(self.activate(path)["state"], "selected")
        self.assertEqual(self.kinds().count("release-sealed"), 1)
        self.fresh()
        plan, path = self.prepared()
        for p in self.seal_dirs(plan):
            p.chmod(0o555)  # a 0555 this operation never journaled
        with self.assertRaisesRegex(Refused, "unknown"):
            self.activate(path)
        self.assertEqual([self.mode(p) for p in self.seal_dirs(plan)], [0o555, 0o555], "nothing is restored")
        self.assertEqual(self.targets_snapshot(), self.before_targets())

    def test_a_crash_at_each_fchmod_and_at_the_journal_boundary_resumes(self):
        for point in ("before the first fchmod", "after the first fchmod", "after the second fchmod",
                      "after release-sealed"):
            with self.subTest(crash=point):
                self.fresh()
                plan, path = self.prepared()
                real, calls = os.fchmod, []

                def fchmod(fd, mode):
                    if mode != 0o555:  # the private backups' own 0600: not the seal
                        return real(fd, mode)
                    calls.append(fd)
                    if point == "before the first fchmod" and len(calls) == 1:
                        raise Crash(point)
                    real(fd, mode)
                    if (point, len(calls)) in (("after the first fchmod", 1), ("after the second fchmod", 2)):
                        raise Crash(point)
                crash = self.crash_on("release-sealed") if point == "after release-sealed" else None
                with mock.patch.object(activate_release.os, "fchmod", fchmod), self.assertRaises(Crash):
                    self.activate(path, crash=crash)
                self.assertEqual([r["kind"] for r in self.seal_records()][:1], ["release-seal-baseline"],
                                 "the originals were journaled before the first change")
                self.assertEqual(self.activate(path)["state"], "selected")
                self.assertEqual([self.mode(p) for p in self.seal_dirs(plan)], [0o555, 0o555])
                sealed = [r for r in self.seal_records() if r["kind"] == "release-sealed"]
                self.assertEqual(len(sealed), 1)
                self.assertEqual([d["mode"] for d in sealed[0]["dirs"]], [0o755, 0o755], "originals retained")

    def test_a_crash_mid_seal_then_a_replaced_directory_is_unknown(self):
        plan, path = self.prepared()
        real, calls = os.fchmod, []

        def fchmod(fd, mode):
            real(fd, mode)
            if mode == 0o555:
                raise Crash("after the first fchmod")
        with mock.patch.object(activate_release.os, "fchmod", fchmod), self.assertRaises(Crash):
            self.activate(path)
        first, scripts = self.seal_dirs(plan)
        first.chmod(0o755)
        scripts.rename(first / "scripts-old")
        shutil.copytree(first / "scripts-old", scripts)
        shutil.rmtree(first / "scripts-old")
        first.chmod(0o555)
        with self.assertRaisesRegex(Refused, "unknown"):
            self.activate(path)
        self.assertEqual(self.mode(scripts), 0o755, "nothing was sealed or restored")

    def test_a_drifted_seal_blocks_and_is_never_resealed(self):
        def mode_only(dirs):
            dirs[1].chmod(0o755)

        def replaced_with_identical_contents(dirs):
            dirs[0].chmod(0o755)
            dirs[1].rename(dirs[0] / "scripts-old")
            shutil.copytree(dirs[0] / "scripts-old", dirs[1])
            (dirs[0] / "scripts-old").chmod(0o755)
            shutil.rmtree(dirs[0] / "scripts-old")
            dirs[1].chmod(0o555)
            dirs[0].chmod(0o555)
        for name, drift in (("mode only", mode_only), ("replaced with identical contents",
                                                       replaced_with_identical_contents)):
            with self.subTest(drift=name):
                self.fresh()
                plan, path = self.activated()
                self.serving()
                self.ops.bridge = {"running": True, "release": plan["release"]}
                self.assertTrue(self.postcheck(path)["ok"])
                drift(self.seal_dirs(plan))
                report = self.postcheck(path)
                self.assertFalse(report["ok"])
                self.assertTrue(dict(report["checks"])["release seal"].startswith("drifted"))
                self.assertEqual(dict(report["checks"])["staged release"], "matches",
                                 "the inventory alone can't see it: directories are recorded as [\"dir\"]")
                self.fresh()
                plan, path = self.prepared()
                with self.assertRaises(Crash):
                    self.activate(path, crash=self.crash_on("release-sealed"))
                drift(self.seal_dirs(plan))
                modes = [self.mode(p) for p in self.seal_dirs(plan)]
                with self.assertRaisesRegex(Refused, "drifted"):
                    self.activate(path)
                self.assertEqual([self.mode(p) for p in self.seal_dirs(plan)], modes, "never sealed again silently")

    def test_rollback_keeps_the_seal(self):
        plan, path = self.activated()
        self.rollback(path)
        self.assertEqual([self.mode(p) for p in self.seal_dirs(plan)], [0o555, 0o555])
        self.assertEqual(self.targets_snapshot(), self.before_targets())

    def bytecode_probe(self, plan):
        """Run the staged interpreter with bytecode deliberately enabled: no -B,
        no PYTHONDONTWRITEBYTECODE (which CI sets), no cache prefix."""
        python = Path(plan["staged_release"]) / "env/bin/python"
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        done = subprocess.run([str(python), "-I", "-c", BYTECODE_PROBE], capture_output=True, text=True, env=env,
                              timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        found = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertFalse(found["dont_write_bytecode"])
        self.assertIsNone(found["pycache_prefix"])
        return found["attempts"]

    def pycache(self, plan):
        return [p for d in self.seal_dirs(plan) for p in d.glob("__pycache__")]

    def test_real_use_with_bytecode_enabled_writes_nothing_and_the_strict_postcheck_passes(self):
        # The control: an unsealed staged release does get bytecode, so the proof below is not vacuous.
        plan, path = self.prepared()
        attempts = self.bytecode_probe(plan)
        self.assertEqual({Path(a).parent.parent for a in attempts} & set(self.seal_dirs(plan)),
                         set(self.seal_dirs(plan)))
        self.assertEqual(len(self.pycache(plan)), 2, "without the seal, both directories get __pycache__")
        with self.assertRaisesRegex(Refused, "doesn't verify"):
            activate_release.verify_staged_release(plan)
        # Sealed: the same use attempts to write caches in both directories, and none appears.
        self.fresh()
        plan, path = self.activated()
        attempts = self.bytecode_probe(plan)
        self.assertEqual({Path(a).parent.parent for a in attempts} & set(self.seal_dirs(plan)),
                         set(self.seal_dirs(plan)), "imports tried to write caches in both sealed directories")
        bound = subprocess.run([str(self.home / ".local/bin/pchat"), "--help"], capture_output=True, text=True,
                               env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"}, timeout=60)
        self.assertEqual(bound.returncode, 0, bound.stderr)
        self.assertEqual(self.pycache(plan), [], "no cache appeared")
        self.serving()
        self.ops.bridge = {"running": True, "release": plan["release"]}
        report = self.postcheck(path)
        self.assertTrue(report["ok"], report)
        self.assertEqual(dict(report["checks"])["staged release"], "matches", "strict verification, unchanged")


class CodeThreeTests(ActivationCase):
    """Code review slot 3 on PR #22 (6112012412): the controller's three
    findings, each reproduced at c229c83 before its fix."""

    # 1. Rollback stays eligible all the way through: fenced producers, current first-write evidence.
    def test_a_first_write_or_a_waking_producer_during_rollback_refuses_before_the_next_restore(self):
        for name, when in (("a first write before the first restore", "restoring"),
                           ("a first write between two restores", "entry-restored"),
                           ("the maintenance record no longer this operation's", "restoring")):
            with self.subTest(case=name):
                self.fresh()
                plan, path = self.activated()
                seen = []

                def late(record):
                    if record["kind"] == when and not seen:
                        seen.append(record)
                        if "record" in name:  # what fences new-release writers (amendment 4, section 3)
                            (self.state / "maintenance.json").unlink()
                        else:
                            (self.state / "outbox.d").mkdir()
                with self.assertRaisesRegex(Refused, "repair or roll-forward" if "write" in name else "blocked"):
                    self.rollback(path, crash=late)
                self.assertNotIn("rolled-back", self.kinds())
                if "write" in name:
                    self.assertTrue((self.state / "outbox.d").is_dir(), "the evidence is kept")
                restored = [r for r in self.journal() if r["kind"] == "entry-restored"]
                self.assertEqual(len(restored), 0 if when == "restoring" else 1, "nothing more was restored")

    # 3. The entry and its directory are checked again after the durable intent, just before the write.
    def test_an_edit_at_the_intent_boundary_refuses_and_is_kept(self):
        edit = "# the owner's edit, made at the intent boundary\n"
        for phase in ("binding", "restoring"):
            with self.subTest(phase=phase):
                self.fresh()
                if phase == "binding":
                    plan, path = self.prepared()
                else:
                    plan, path = self.activated()
                target = self.skills / "chat/scripts/chatlib.py"

                def at_intent(record):
                    if record["kind"] == f"entry-{phase}" and record["rel"] == "chatlib.py":
                        target.write_text(edit)
                with self.assertRaisesRegex(Refused, "changed"):
                    (self.activate if phase == "binding" else self.rollback)(path, crash=at_intent)
                self.assertEqual(target.read_text(), edit, "the owner's edit is kept")

    # 4. The live staged paths still name the pinned seal directories.
    def test_a_seal_directory_swapped_mid_operation_refuses_and_is_not_journaled_sealed(self):
        for when in ("after the journaled baseline", "between the two fchmods"):
            with self.subTest(swap=when):
                self.fresh()
                plan, path = self.prepared()
                site = next((Path(plan["staged_release"]) / "env").glob("lib/python*/site-packages"))
                pkg, scripts = site / "plateia_chat", site / "plateia_chat/scripts"

                def swap():
                    mode = pkg.stat().st_mode & 0o777
                    pkg.chmod(0o755)
                    scripts.rename(pkg / "scripts-displaced")
                    shutil.copytree(pkg / "scripts-displaced", scripts)  # identical contents, another directory
                    scripts.chmod(0o755)
                    pkg.chmod(mode)
                crash, patches = None, []
                if when == "after the journaled baseline":
                    def crash(record):
                        if record["kind"] == "release-seal-baseline":
                            swap()
                else:
                    real, calls = os.fchmod, []

                    def fchmod(fd, mode):
                        real(fd, mode)
                        if mode == 0o555:
                            calls.append(fd)
                            if len(calls) == 1:
                                swap()
                    patches.append(mock.patch.object(activate_release.os, "fchmod", fchmod))
                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)
                    with self.assertRaisesRegex(Refused, "blocked"):
                        self.activate(path, crash=crash)
                self.assertNotIn("release-sealed", self.kinds())
                self.assertNotIn("binding", self.kinds())
                self.assertEqual(scripts.stat().st_mode & 0o777, 0o755, "the live scripts/ was never sealed")


class CodeThreeSweepTests(ActivationCase):
    """The same classes as slot 3's findings, swept: a check that doesn't hold
    across a durable step, and validation that doesn't reach the act."""

    def test_a_seal_directory_swapped_just_after_release_sealed_stops_before_any_binding(self):
        plan, path = self.prepared()
        site = next((Path(plan["staged_release"]) / "env").glob("lib/python*/site-packages"))
        pkg, scripts = site / "plateia_chat", site / "plateia_chat/scripts"

        def swap(record):
            if record["kind"] == "release-sealed":
                pkg.chmod(0o755)
                scripts.rename(pkg / "scripts-displaced")
                shutil.copytree(pkg / "scripts-displaced", scripts)
                pkg.chmod(0o555)
        with self.assertRaisesRegex(Refused, "blocked"):
            self.activate(path, crash=swap)
        self.assertNotIn("bootstrap-intent", self.kinds())
        self.assertNotIn("binding", self.kinds())

    def test_the_record_no_longer_held_during_binding_stops_the_next_write(self):
        """Amendment 4: the binding guard's quiet-writer check is replaced by the record check (writer
        admission 6.1, 9.2)."""
        plan, path = self.prepared()
        seen = []

        def wake(record):
            if record["kind"] == "entry-bound" and not seen:
                seen.append(record)
                (self.state / "maintenance.json").unlink()
        with self.assertRaisesRegex(Refused, "blocked"):
            self.activate(path, crash=wake)
        self.assertEqual(self.kinds().count("entry-bound"), 1, "nothing more was written")

    def test_the_state_root_moved_during_publication_stops_before_d(self):
        plan, path = self.prepared()

        def move(record):
            if record["kind"] == "bootstrap-staged":
                self.state.rename(self.state.with_name("chat-moved"))
                self.state.mkdir()
        with self.assertRaisesRegex(Refused, "blocked"):
            self.activate(path, crash=move)
        self.assertNotIn("bootstrap", self.kinds(), "D is never journaled for a root that moved")


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
