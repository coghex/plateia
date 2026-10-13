"""Tests for #21's owner amendment 4 (2026-10-13; APPROVE 6151464851): PR #22's
remaining blockers (code review 6112483129) and the activation controller's
side of writer admission (docs/writer_admission.md sections 6, 8, 9.2, 10, 12;
#24's delivery_store.Owner).

Every test runs in an invented home with #19's real release built from an
invented repository and staged offline, as test_activate does. Services,
processes, boots, checkpoints and inhibition evidence are fakes (FakeOps): no
real service, process table, boot id or state is read."""
import fcntl
import json
import os
import shutil
import sys
import threading
import unittest

import _staging
import _support

sys.path.insert(0, str(_support.RELEASE))
import activate_release  # noqa: E402
import stage_release  # noqa: E402
from activate_release import Crash  # noqa: E402
from build_release import Refused  # noqa: E402
from test_activate import ActivationCase, BRIDGE_PLIST  # noqa: E402


def edit_at(kind, nth, edit):
    """A journal hook that edits a target (instead of crashing) when the `nth` record of `kind` is journaled."""
    seen = []

    def hook(record):
        if record["kind"] == kind:
            seen.append(record)
            if len(seen) == nth:
                edit()
    return hook


class BackupValidationTests(ActivationCase):
    """Code review 6112483129, finding 1: each captured backup's exact bytes and links are validated against
    the sealed plan before it is accepted. A target changed between the plan's check and its capture, or between
    two captures, is refused with its edit kept; nothing is published or bound, and a rollback never restores
    an altered backup."""

    def edits(self):
        chatlib = self.skills / "chat/scripts/chatlib.py"
        skill = self.skills / "chat/SKILL.md"
        role = self.home / ".local/share/weechat/python/autoload/role_colors.py"

        def append(path, text):
            def edit():
                with open(path, "a") as f:
                    f.write(text)
            return edit

        def relink():
            role.unlink()
            role.symlink_to(self.where / "invented-role-colors.py")

        def agent():
            data = (self.home / BRIDGE_PLIST).read_bytes()
            (self.home / BRIDGE_PLIST).write_bytes(data.replace(b"</dict>", b"<key>Nice</key><integer>5</integer>"
                                                                            b"</dict>", 1))
        # (what is edited, the backup record it is edited at, the edit, the path that keeps the edit)
        return [("chat/scripts import location", ("backup", 1), append(chatlib, "# an owner's edit\n"), chatlib),
                ("chat-bridge LaunchAgent", ("backup", 1), agent, self.home / BRIDGE_PLIST),
                ("chat skill folder", ("backup", 2), append(skill, "\nAn owner's note.\n"), skill),
                ("WeeChat role_colors autoload link", ("backup", 3), relink, role)]

    def test_a_target_changed_before_its_capture_is_refused_and_kept(self):
        for case in range(len(self.edits())):
            self.fresh()
            name, (kind, nth), edit, kept = self.edits()[case]  # this case's invented home
            with self.subTest(target=name):
                plan, path = self.prepared()
                edited = []

                def do():
                    edit()
                    edited.append(os.readlink(kept) if kept.is_symlink() else kept.read_bytes())
                with self.assertRaisesRegex(Refused, name):
                    self.activate(path, crash=edit_at(kind, nth, do))
                self.assertEqual(edited, [os.readlink(kept) if kept.is_symlink() else kept.read_bytes()],
                                 "the edit is kept")
                kinds = [r["kind"] for r in self.journal()]
                self.assertNotIn("bootstrap-intent", kinds, "nothing was published")
                self.assertFalse(any(k in ("binding", "entry-binding") for k in kinds), "nothing was bound")
                self.assertNotIn(name, [r["target"] for r in self.journal() if r["kind"] == "backup"],
                                 "the altered capture was never accepted as its backup")
                self.assertFalse(self.record_path().exists())


OTHER_OPERATION = "activate-9f8e7d6c5b4a3921"


class RollbackFenceTests(ActivationCase):
    """Code review 6112483129, finding 2: rollback no longer exempts writers that report the new release. Its
    quiet-writer check is replaced by the maintenance record (amendment 4, section 3): the record must be this
    operation's and still held, which is what fences every new-release writer. While it isn't, an
    unacknowledged new-release writer is unfenced, and rollback restores nothing."""

    def maintenance(self):
        return self.state / "maintenance.json"

    def unfence(self, how):
        record = self.maintenance()
        if how == "no record":
            record.unlink(missing_ok=True)
        elif how == "an unknown record":
            _staging.write(record, "{", 0o600)
        else:
            fields = {"v": "maintenance/1", "operation": OTHER_OPERATION, "kind": "plateia-activation",
                      "plan_sha256": "e" * 64, "state": "partial", "generation": 2, "acquired_boot": "boot-other",
                      "boot": "boot-other", "bound_boot": None, "legacy": "unverified",
                      "binding": {"release_id": "", "bootstrap_digest": None, "permits": []}}
            record.unlink(missing_ok=True)
            _staging.write(record, activate_release.json.dumps(fields, sort_keys=True, separators=(",", ":")) + "\n",
                           0o600)

    def test_an_unacknowledged_new_release_writer_blocks_rollback_while_it_is_unfenced(self):
        for how in ("no record", "an unknown record", "another operation's record"):
            with self.subTest(record=how):
                self.fresh()
                plan, path = self.activated()
                self.ops.writer_list = (self.ops.writer_list or []) + [
                    {"name": "pchat flusher (new release)", "kind": "flusher", "release": plan["release"],
                     "checkpoint": None, "owner": "alpha-manager"}]
                self.unfence(how)
                record = self.maintenance().read_bytes() if self.maintenance().exists() else None
                bound = self.targets_snapshot()
                with self.assertRaisesRegex(Refused, "maintenance record"):
                    self.rollback(path)
                self.assertEqual(self.targets_snapshot(), bound, "nothing was restored")
                self.assertEqual(self.maintenance().read_bytes() if self.maintenance().exists() else None, record,
                                 "a record that is not this operation's is never touched")



class AdmissionCase(ActivationCase):
    """Helpers over the invented home's maintenance record and the controller's commands, run directly (the
    shared activate helper's reboot is left to each test)."""

    def run_activate(self, path, crash=None):
        return activate_release.activate(path, self.ops, spec_path=self.spec, crash=crash)

    def do_release(self, path, crash=None):
        return activate_release.release(path, self.ops, spec_path=self.spec, crash=crash)

    def maintenance(self):
        p = self.state / "maintenance.json"
        return json.loads(p.read_text()) if p.exists() else None

    def bound(self):
        """Selected (targets bound, record partial), before the owner's reboot."""
        plan, path = self.prepared()
        result = self.run_activate(path)
        self.assertEqual((result["state"], result["maintenance"]["next"]), ("selected", "reboot"))
        return plan, path

    def serve(self, plan):
        self.ops.bridge = {"running": True, "release": plan["release"]}
        self.serving()

    def no_new_format_files(self):
        for name in ("outbox.db", "outbox.db-wal", "outbox.sock", "outbox.d", "refused.d"):
            self.assertFalse((self.state / name).exists(), f"the controller never makes {name}")


class RetirementTests(AdmissionCase):
    """Design tests 7, 11 and 13, the controller's parts: full needs a known boot that differs from the
    binding's; a legacy writer is never refused (nothing claims otherwise) and keeps full unreachable until
    the boot changes; a boot change is never a release."""

    def test_full_needs_a_boot_change_since_binding_and_a_legacy_append_is_not_refused(self):
        plan, path = self.bound()
        record = self.maintenance()
        self.assertEqual((record["state"], record["bound_boot"], record["legacy"]), ("partial", "boot-A", "unverified"))
        legacy = self.state / "outbox.jsonl"
        with open(legacy, "a") as f:  # a baseline-format client appending, as legacy code does
            f.write(json.dumps({"channel": "#alpha", "as": "sam", "text": "[status] legacy", "at": "x"}) + "\n")
        held = legacy.read_bytes()
        again = self.run_activate(path)  # no reboot
        self.assertEqual(again["state"], "already selected")
        self.assertIn("retirement unverified", again["maintenance"]["refused"])
        self.assertEqual(self.maintenance()["state"], "partial")
        self.assertEqual(legacy.read_bytes(), held, "the legacy append is not refused, moved or rewritten")
        self.ops.reboot()
        self.assertEqual(self.run_activate(path)["maintenance"]["state"], "full")
        self.assertEqual(self.maintenance()["legacy"], "retired-and-inhibited")
        self.no_new_format_files()

    def test_an_unreadable_boot_refuses_full_as_unknown(self):
        plan, path = self.bound()
        self.ops.boot_id = None
        refused = self.run_activate(path)["maintenance"]
        self.assertEqual(refused["state"], "partial")
        self.assertIn("unknown", refused["refused"])
        self.assertEqual(self.maintenance()["state"], "partial")

    def test_a_reboot_mid_activation_drains_again_and_is_never_a_release(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.run_activate(path, crash=self.crash_on("backup"))
        self.assertEqual(self.maintenance()["boot"], "boot-A")
        self.ops.reboot()
        result = self.run_activate(path)
        self.assertEqual(result["state"], "selected")
        record = self.maintenance()
        self.assertEqual((record["state"], record["acquired_boot"], record["boot"], record["bound_boot"]),
                         ("partial", "boot-A", "boot-B", "boot-B"), "drained again and bound in the new boot")
        self.assertIn("retirement unverified", self.run_activate(path)["maintenance"]["refused"],
                      "the binding's boot is this boot: another reboot is needed")


class InhibitionTests(AdmissionCase):
    """Design test 12: an unmanaged launcher without coverage, or incomplete or missing inhibition evidence,
    refuses full and names it; nothing is bound or written by that refusal, and partial continues."""

    def test_missing_coverage_refuses_full_and_names_it(self):
        copy = {"name": "com.invented.chat-copy", "path": "~/Library/LaunchAgents/com.invented.chat-copy.plist "
                                                           "(~/tools/chat-copy/pchat, a copy of the baseline)"}
        for case, evidence, named in (
                ("an unmanaged launcher", {"complete": True, "launchers": [dict(copy, coverage=None)]},
                 "com.invented.chat-copy"),
                ("incomplete evidence", {"complete": False, "launchers": []}, "incomplete"),
                ("no evidence", None, "no current inhibition evidence")):
            with self.subTest(case=case):
                self.fresh()
                plan, path = self.bound()
                self.ops.reboot()
                self.ops.inhibition_evidence = evidence
                targets, kinds = self.targets_snapshot(), self.kinds()
                refused = self.run_activate(path)["maintenance"]
                self.assertIn(named, refused["refused"])
                self.assertEqual(self.maintenance()["state"], "partial")
                self.assertEqual(self.targets_snapshot(), targets, "nothing bound by the refusal")
                self.assertFalse(any(k in ("binding", "entry-binding", "full") for k in self.kinds()[len(kinds):]))
                self.no_new_format_files()
        self.ops.inhibition_evidence = {"complete": True, "launchers": [dict(copy, coverage="disabled")]}
        self.assertEqual(self.run_activate(path)["maintenance"]["state"], "full", "covered, full is reached")


class FullBindingTests(AdmissionCase):
    """Design test 9, the controller's part, and K1: full binds this release, D and the bridge's start; the
    operation's own bridge (holding, or serving read-only) permits the maintenance transitions and release but
    no target write."""

    def test_full_binds_the_release_d_and_the_bridge_start(self):
        plan, path = self.bound()
        self.no_new_format_files()
        self.ops.reboot()
        self.ops.bridge = {"running": True, "release": plan["release"]}  # launchd's bound bridge, in its hold
        targets = self.targets_snapshot()
        self.assertEqual(self.run_activate(path)["maintenance"]["state"], "full", "permitted while it holds (K1)")
        self.assertEqual(self.targets_snapshot(), targets, "no target written")
        d = activate_release.sha256_bytes(activate_release.bootstrap_bytes(plan))
        record = self.maintenance()
        self.assertEqual((record["operation"], record["state"], record["bound_boot"], record["boot"]),
                         (plan["operation"], "full", "boot-A", "boot-B"))
        self.assertEqual(record["binding"], {"release_id": plan["release"], "bootstrap_digest": f"sha256:{d}",
                                             "permits": ["bridge-start"]})
        self.no_new_format_files()
        self.serving()
        report = self.postcheck(path)
        self.assertTrue(report["ok"], report)
        self.assertEqual(self.do_release(path)["state"], "released", "the release, while it serves (K1)")


class ReleaseTests(AdmissionCase):
    """Design test 4: release is refused while any post-check fails; after it the record is gone and the
    journal holds its intent and outcome; a crash between the unlink and the journal resumes to a completed
    release."""

    def test_release_is_refused_while_the_post_check_fails_then_completes(self):
        plan, path = self.activated()
        for why, setup in (("the bridge is not running", lambda: None),
                           ("no live answer", lambda: setattr(self.ops, "bridge",
                                                              {"running": True, "release": plan["release"]})),
                           ("a target changed", lambda: (self.serving(), (self.skills / "chat/SKILL.md").write_text(
                               (self.skills / "chat/SKILL.md").read_text() + "\nan edit\n")))):
            with self.subTest(why=why):
                setup()
                with self.assertRaisesRegex(Refused, "release refused"):
                    self.do_release(path)
                self.assertEqual(self.maintenance()["state"], "full", "the record stays")
        self.fresh()
        plan, path = self.activated()
        self.serve(plan)
        with self.assertRaises(Crash):
            self.do_release(path, crash=self.crash_on("maint-outcome"))  # the release's own outcome
        self.assertIsNone(self.maintenance(), "unlinked: the moment of release")
        self.assertNotIn("released", self.kinds())
        self.assertEqual(self.do_release(path)["state"], "released")
        self.assertEqual(self.do_release(path)["state"], "already released")
        self.assertEqual(self.kinds().count("released"), 1)
        self.assertEqual(self.run_activate(path)["state"], "released")
        unlinked = Crash  # a crash after the unlink, before its outcome is journaled
        self.fresh()
        plan, path = self.activated()
        self.serve(plan)
        hook = self.point_hook("unlinked")
        with self.assertRaises(unlinked):
            self.do_release(path, crash=hook)
        self.assertIsNone(self.maintenance())
        self.assertEqual(self.do_release(path)["state"], "released", "settled from what is on disk")

    @staticmethod
    def point_hook(point, nth=1):
        seen = []

        def hook(record):
            if record["kind"] == f"maint-point:{point}":
                seen.append(record)
                if len(seen) == nth:
                    raise Crash(f"killed at {point}")
        hook.points = True
        return hook


class OwnerFenceTests(AdmissionCase):
    """K4: the fence keeps intent, progress, outcome, the pending step and the latest record evidence in the
    journal across commands; crash settlement verifies the published file's identity and durability, and never
    adopts matching bytes or a matching operation alone: an identical-byte replacement is refused before and
    after the outcome is journaled, and left untouched."""

    def replace_with_identical_bytes(self):
        record = self.state / "maintenance.json"
        data, ino = record.read_bytes(), record.stat().st_ino
        spare = self.state / "spare-inode"  # keeps the old inode alive, so the replacement gets another
        os.link(record, spare)
        record.unlink()
        _staging.write(record, data.decode(), 0o600)
        spare.unlink()
        self.assertNotEqual(record.stat().st_ino, ino)
        return data

    def test_an_identical_byte_replacement_is_never_adopted(self):
        for when, hook in (("before the outcome is journaled", ReleaseTests.point_hook("linked")),
                           ("after the outcome is journaled", self.crash_on("maint-outcome", nth=2))):
            with self.subTest(when=when):
                self.fresh()
                plan, path = self.prepared()
                with self.assertRaises(Crash):
                    self.run_activate(path, crash=hook)
                fence = activate_release.OwnerFence(None, plan, records=self.journal())
                self.assertEqual(fence.pending() is not None, when.startswith("before"),
                                 "the pending step is read back from the journal alone")
                data = self.replace_with_identical_bytes()
                targets = self.targets_snapshot()
                with self.assertRaisesRegex(Refused, "never adopted|not the file|differs from the operation"):
                    self.run_activate(path)
                self.assertEqual((self.state / "maintenance.json").read_bytes(), data, "left as it is")
                self.assertEqual(self.targets_snapshot(), targets)
                self.assertNotIn("backup", self.kinds(), "nothing went further")

    def test_the_fence_is_durable_journal_records(self):
        plan, path = self.activated()
        records = [r for r in self.journal() if r["kind"].startswith("maint-")]
        steps = [r["step"]["step"] for r in records if r["kind"] == "maint-intent"]
        self.assertEqual(steps[:2], ["create-lock", "write-draining"])
        self.assertIn("write-record", steps)
        published = [r["facts"] for r in records if r["kind"] == "maint-progress" and "published" in r["facts"]]
        record = self.state / "maintenance.json"
        st = record.stat()
        self.assertEqual({k: published[-1]["published"][k] for k in ("ino", "ctime_ns")},
                         {"ino": st.st_ino, "ctime_ns": st.st_ctime_ns}, "the latest evidence, journaled")
        fence = activate_release.OwnerFence(None, plan, records=self.journal())
        self.assertIsNone(fence.pending())
        self.assertEqual(fence.evidence()["sha256"], activate_release.sha256_bytes(record.read_bytes()))
        self.assertFalse(fence.verify({"step": "write-record"}), "read-only: it permits nothing")


class CrashResumeTests(AdmissionCase):
    """Design tests 3 and 8, the controller's parts: a crash at each acquisition, full and release journal
    point resumes to the same end state, and no final name ever holds a torn record."""

    def flow(self, path, plan, hook=None):
        self.activate(path, crash=hook)
        self.serve(plan)
        result = self.do_release(path, crash=hook)
        self.ops.bridge = {"running": False, "release": None}
        return result

    def test_a_crash_at_each_maintenance_point_resumes_to_a_released_operation(self):
        cases = [("point", "tmp-journaled", 1), ("point", "linked", 1), ("maint-outcome", None, 2),
                 ("point", "locked", 1), ("point", "renamed", 1), ("point", "published", 3),
                 ("maint-intent", None, 4), ("point", "renamed", 3), ("maint-outcome", None, 5),
                 ("point", "unlinked", 1)]
        for kind, point, nth in cases:
            with self.subTest(crash=f"{point or kind} #{nth}"):
                self.fresh()
                plan, path = self.prepared()
                hook = ReleaseTests.point_hook(point, nth) if kind == "point" else self.crash_on(kind, nth)
                with self.assertRaises(Crash):
                    self.flow(path, plan, hook)
                record = self.state / "maintenance.json"
                if record.exists():
                    json.loads(record.read_text())  # never torn
                self.ops.bridge = {"running": False, "release": None}
                self.activate(path)  # resumes whatever it reached; after a begun release it hands over to release
                self.serve(plan)
                self.assertIn(self.do_release(path)["state"], ("released", "already released"))
                self.assertIsNone(self.maintenance())
                self.assertEqual(self.kinds().count("released"), 1)
                self.assertTrue(self.postcheck(path)["ok"])
                self.assertFalse([n for n in os.listdir(self.state) if n.startswith(".maintenance.json.")],
                                 "every journaled temporary removed")


class RollbackReleaseTests(AdmissionCase):
    """Design test 4's rollback variant and K2: before any first write the targets are restored and the record
    is kept until the boot changes since the restore, then released; a refused write meanwhile is a first write,
    reported and kept, and blocks this release."""

    def test_the_record_is_kept_until_the_boot_changes_then_released(self):
        plan, path = self.bound()
        result = self.rollback(path)
        self.assertEqual(result["state"], "rolled-back")
        self.assertEqual(self.targets_snapshot(), self.before_targets())
        self.assertEqual(self.maintenance()["state"], "partial", "kept")
        with self.assertRaisesRegex(Refused, "boot has not changed since the restore"):
            self.do_release(path)
        self.ops.reboot()
        self.ops.bridge = {"running": True, "release": "skills-tree"}  # the restored legacy bridge
        self.assertEqual(self.do_release(path)["state"], "released")
        self.assertIsNone(self.maintenance())

    def test_what_blocks_the_rollback_release(self):
        cases = {
            "a refused write in the sink": lambda plan: _staging.write(
                self.state / "refused.d/1-invented.json", "{}\n", 0o600),
            "a target changed since the restore": lambda plan: (self.skills / "chat/SKILL.md").write_text("changed"),
            "a bridge of the rolled-back release": lambda plan: setattr(
                self.ops, "bridge", {"running": True, "release": plan["release"]}),
            "an unknown bridge": lambda plan: setattr(self.ops, "bridge", {"running": None, "release": None}),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.fresh()
                plan, path = self.bound()
                self.rollback(path)
                self.ops.reboot()
                change(plan)
                with self.assertRaisesRegex(Refused, "release refused"):
                    self.do_release(path)
                self.assertEqual(self.maintenance()["state"], "partial", "the record stays")
                if "sink" in name:
                    self.assertTrue((self.state / "refused.d/1-invented.json").exists(), "kept, never deleted")

    def test_a_sink_file_before_the_restore_refuses_the_rollback_and_is_kept(self):
        plan, path = self.bound()
        _staging.write(self.state / "refused.d/1-invented.json", "{}\n", 0o600)
        targets = self.targets_snapshot()
        with self.assertRaisesRegex(Refused, "repair or roll-forward"):
            self.rollback(path)
        self.assertEqual(self.targets_snapshot(), targets)
        self.assertTrue((self.state / "refused.d/1-invented.json").exists())
        self.assertIn("refused.d", json.dumps(next(r for r in self.journal() if r["kind"] == "rollback-refused")))


class PlanTests(AdmissionCase):
    """plan refuses a state that already holds a maintenance record or a refusal sink (writer admission 9.1)."""

    def test_no_plan_over_a_record_or_a_sink(self):
        for name, make in (("a record", lambda: _staging.write(self.state / "maintenance.json", "{}\n", 0o600)),
                           ("a sink", lambda: (self.state / "refused.d").mkdir(parents=True))):
            with self.subTest(case=name):
                self.fresh()
                make()
                with self.assertRaisesRegex(Refused, "maintenance record|refusal sink|refused.d"):
                    self.plan()


class FakeClock:
    """The drain's clock: its sleep advances it, so no wait is real; `on_advance` runs at each advance."""

    def __init__(self):
        self.t, self.on_advance = 0.0, None

    def mono(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds
        if self.on_advance:
            self.on_advance(self.t)


class OrderingTests(AdmissionCase):
    """The ordering and deadlock schedule (Requirement 10) with the controller in the loop: an open section
    (a client's L_adm shared) keeps the drain from completing without blocking anything; every wait is bounded
    by the fake clock; the drain completes after the section ends, or reports at its deadline."""

    def section(self):
        self.state.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.state / "admission.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return fd

    def run_bounded(self, fn):
        out = {}

        def body():
            try:
                out["result"] = fn()
            except BaseException as e:  # reported to the test
                out["error"] = e
        t = threading.Thread(target=body)
        t.start()
        t.join(120)
        self.assertFalse(t.is_alive(), "the controller never blocks on L_adm")
        return out

    def test_an_open_section_holds_the_drain_until_it_ends_or_its_deadline(self):
        plan, path = self.prepared()
        self.ops.clock = FakeClock()
        fd = self.section()
        out = self.run_bounded(lambda: self.run_activate(path))
        self.assertRegex(str(out.get("error")), "drain did not complete")
        self.assertEqual(self.maintenance()["state"], "draining", "it stays draining, refusing new sections")
        self.assertGreaterEqual(self.ops.clock.t, 300.0, "it reported at DRAIN_MAX")
        self.assertNotIn("backup", self.kinds())
        self.ops.clock = FakeClock()
        self.ops.clock.on_advance = lambda t: t >= 1.0 and fd is not None and os.close(fd)
        out = self.run_bounded(lambda: self.run_activate(path))
        self.assertEqual(out.get("result", {}).get("state"), "selected", out)
        attempts = [r["result"]["attempts"] for r in self.journal()
                    if r["kind"] == "maint-outcome" and r["result"].get("drained")]
        self.assertGreater(attempts[0], 1, "the drain tried again until the section ended")


class StatusTests(AdmissionCase):
    """status reports the record and Owner's read-only reconcile."""

    def test_status_reports_the_record_and_the_reconcile(self):
        plan, path = self.activated()
        before = _staging.snapshot_tree(self.state)
        [entry] = activate_release.status(self.root, path, self.ops)
        self.assertIn("full", entry["state"])
        m = entry["maintenance"]
        self.assertEqual((m["record"]["state"], m["record"]["operation"], m["matches_journal"]),
                         ("full", plan["operation"], True))
        self.assertEqual(m["next"], "post-check, then release")
        self.assertEqual(m["refused_files"], 0, "no refused write")
        self.assertEqual(_staging.snapshot_tree(self.state), before, "read-only")
        text = activate_release.render_operator_plan(plan)
        for item in ("stops the legacy bridge", "reboots the machine", "inhibition evidence", "release:",
                     "refusal sink's count", "history retention"):
            self.assertIn(item, text)


if __name__ == "__main__":
    unittest.main()
