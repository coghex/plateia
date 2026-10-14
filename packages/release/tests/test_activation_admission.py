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
from pathlib import Path
from unittest import mock

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



class ReviewOneTests(AdmissionCase):
    """PR #22 review 1 (6154026430), the controller's three findings, each reproduced at 0c081e6 first."""

    def shared_section_after_the_redrain(self, hold):
        """A journal hook: once this run's re-drain has landed, a writer opens a section (L_adm shared) and keeps
        it open, so the next maintenance transition can't take the lock before its deadline."""
        def hook(record):
            if record["kind"] == "maint-outcome" and record["step"].get("step") == "write-record" \
                    and record["result"].get("landed") and "fd" not in hold:
                fd = os.open(self.state / "admission.lock", os.O_RDWR)
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                hold["fd"] = fd
        return hook

    def release_section(self, hold):
        if hold.get("fd") is not None:
            os.close(hold.pop("fd"))

    # 1. Every maintenance advancement must have landed before anything relies on it.
    def test_contention_at_the_binding_boot_stops_before_any_target_write(self):
        plan, path = self.prepared()
        with self.assertRaises(Crash):
            self.run_activate(path, crash=self.crash_on("entry-bound"))  # binding interrupted in boot-A
        self.ops.reboot()
        self.ops.clock = FakeClock()
        hold, written = {}, self.kinds().count("entry-bound")
        try:
            with self.assertRaisesRegex(Refused, "did not complete|binding's boot"):
                self.run_activate(path, crash=self.shared_section_after_the_redrain(hold))
        finally:
            self.release_section(hold)
        self.assertEqual(self.kinds().count("entry-bound"), written, "no target written")
        self.assertEqual(self.maintenance()["bound_boot"], "boot-A")
        self.assertEqual(self.run_activate(path)["state"], "selected")
        self.assertEqual(self.maintenance()["bound_boot"], "boot-B")
        self.assertIn("retirement unverified", self.run_activate(path)["maintenance"]["refused"],
                      "the resumed binding needs its own reboot")

    def test_contention_at_full_never_reports_or_journals_full(self):
        plan, path = self.bound()
        self.ops.reboot()
        self.ops.clock = FakeClock()
        hold = {}
        try:
            result = self.run_activate(path, crash=self.shared_section_after_the_redrain(hold))
        finally:
            self.release_section(hold)
        self.assertEqual(result["maintenance"]["state"], "partial")
        self.assertIn("did not complete", result["maintenance"]["refused"])
        self.assertNotIn("full", self.kinds())
        self.assertEqual(self.maintenance()["state"], "partial")
        self.assertEqual(self.run_activate(path)["maintenance"]["state"], "full", "without contention, full")

    # 2. An interrupted release repeats its checks unless its unlink already landed.
    def test_an_interrupted_release_repeats_the_post_check(self):
        plan, path = self.activated()
        self.serve(plan)
        with self.assertRaises(Crash):
            self.do_release(path, crash=self.crash_on("maint-intent"))
        (self.skills / "chat/SKILL.md").write_text((self.skills / "chat/SKILL.md").read_text() + "\nan edit\n")
        self.assertFalse(self.postcheck(path)["ok"])
        with self.assertRaisesRegex(Refused, "release refused"):
            self.do_release(path)
        self.assertEqual(self.maintenance()["state"], "full", "the record stays")

    def test_an_interrupted_rollback_release_repeats_its_checks(self):
        plan, path = self.bound()
        self.rollback(path)
        self.ops.reboot()
        with self.assertRaises(Crash):
            self.do_release(path, crash=self.crash_on("maint-intent"))
        _staging.write(self.state / "refused.d/1-invented.json", "{}\n", 0o600)
        with self.assertRaisesRegex(Refused, "release refused"):
            self.do_release(path)
        self.assertEqual(self.maintenance()["state"], "partial", "the record stays")
        self.assertTrue((self.state / "refused.d/1-invented.json").exists(), "kept, never deleted")

    # 3. Owner's file system effects stay bound to the sealed root and the expected record, across the intent.
    def test_a_root_swapped_at_the_draining_intent_publishes_nothing(self):
        plan, path = self.prepared()
        moved = self.state.with_name("chat-moved")

        def swap(record):
            if record["kind"] == "maint-intent" and record["step"].get("step") == "write-draining" \
                    and not moved.exists():
                self.state.rename(moved)
                self.state.mkdir()
        with self.assertRaises(Refused):
            self.run_activate(path, crash=swap)
        self.assertFalse((self.state / "maintenance.json").exists(), "nothing published in the replacement")
        self.assertFalse((moved / "maintenance.json").exists(), "nor in the moved root")
        self.assertFalse([r for r in self.journal() if r["kind"] == "maint-outcome"
                          and r["step"].get("step") == "write-draining"], "no outcome journaled")

    def test_a_foreign_record_at_the_release_intent_is_never_removed(self):
        plan, path = self.activated()
        self.serve(plan)
        foreign = json.dumps({"v": "maintenance/1", "operation": OTHER_OPERATION, "kind": "plateia-activation",
                              "plan_sha256": "e" * 64, "state": "partial", "generation": 2,
                              "acquired_boot": "boot-other", "boot": "boot-other", "bound_boot": None,
                              "legacy": "unverified", "binding": {"release_id": "", "bootstrap_digest": None,
                                                                  "permits": []}},
                             sort_keys=True, separators=(",", ":")) + "\n"

        def replace(record):
            if record["kind"] == "maint-intent" and record["step"].get("step") == "release":
                (self.state / "maintenance.json").unlink()
                _staging.write(self.state / "maintenance.json", foreign, 0o600)
        with self.assertRaises(Refused):
            self.do_release(path, crash=replace)
        self.assertEqual((self.state / "maintenance.json").read_text(), foreign, "the foreign record is kept")
        self.assertNotIn("released", self.kinds())



class ReviewTwoTests(AdmissionCase):
    """PR #22 review 2 (6154707381), the controller's three findings, each reproduced at 604aaba first."""

    def at_temporary(self, match, act):
        """Patch write_fd: once the temporary of the entry whose bytes `match` has been written, `act` runs: the
        window between preparing the temporary and the rename."""
        real, done = activate_release.write_fd, []

        def write_fd(fd, data):
            real(fd, data)
            if not done and match(data):
                done.append(1)
                act()
        return mock.patch.object(activate_release, "write_fd", write_fd), done

    def chatlib(self):
        return self.skills / "chat/scripts/chatlib.py"

    # 1. The final mutation boundary: after the temporary is prepared, immediately before the replacement.
    def test_an_edit_while_the_temporary_is_prepared_is_kept_when_binding_and_restoring(self):
        edit = b"# the owner's edit, made while the temporary was prepared\n"
        for phase in ("binding", "restoring"):
            with self.subTest(phase=phase):
                self.fresh()
                original = self.chatlib().read_bytes()
                if phase == "binding":
                    plan, path = self.prepared()
                    match = lambda data: b"plateia handoff layer" in data and b"chatlib.py" in data  # noqa: E731
                else:
                    plan, path = self.bound()
                    match = lambda data: data == original  # noqa: E731
                def append_edit():
                    with open(self.chatlib(), "ab") as f:
                        f.write(edit)
                patch, done = self.at_temporary(match, append_edit)
                with patch, self.assertRaisesRegex(Refused, "changed just before it was written"):
                    self.run_activate(path) if phase == "binding" else self.rollback(path)
                self.assertTrue(done)
                self.assertTrue(self.chatlib().read_bytes().endswith(edit), "the edit is kept")

    def test_a_refused_write_while_a_restore_is_prepared_stops_that_restore(self):
        original = self.chatlib().read_bytes()
        plan, path = self.bound()
        bound = self.chatlib().read_bytes()
        patch, done = self.at_temporary(lambda data: data == original, lambda: _staging.write(
            self.state / "refused.d/1-invented.json", "{}\n", 0o600))
        with patch, self.assertRaisesRegex(Refused, "repair or roll-forward"):
            self.rollback(path)
        self.assertTrue(done)
        self.assertEqual(self.chatlib().read_bytes(), bound, "not restored after the first write")

    # 2. Release eligibility, checked again after the durable intent, immediately before the unlink.
    def test_a_target_changed_at_the_release_intent_keeps_maintenance(self):
        plan, path = self.activated()
        self.serve(plan)
        skill = self.skills / "chat/SKILL.md"

        def edit(record):
            if record["kind"] == "maint-intent" and record["step"].get("step") == "release":
                skill.write_text(skill.read_text() + "\nan edit at the intent\n")
        with self.assertRaisesRegex(Refused, "release refused"):
            self.do_release(path, crash=edit)
        self.assertEqual(self.maintenance()["state"], "full", "maintenance is kept")
        self.assertNotIn("released", self.kinds())

    def test_a_sink_write_at_the_rollback_release_intent_keeps_maintenance(self):
        plan, path = self.bound()
        self.rollback(path)
        self.ops.reboot()

        def sink(record):
            if record["kind"] == "maint-intent" and record["step"].get("step") == "release":
                _staging.write(self.state / "refused.d/1-invented.json", "{}\n", 0o600)
        with self.assertRaisesRegex(Refused, "release refused"):
            self.do_release(path, crash=sink)
        self.assertEqual(self.maintenance()["state"], "partial", "maintenance is kept")
        self.assertTrue((self.state / "refused.d/1-invented.json").exists(), "kept, never deleted")

    def test_a_target_changed_at_the_full_intent_never_reaches_full(self):
        """The same class, for full: its checks run again after the durable intent, before the replacement."""
        plan, path = self.bound()
        self.ops.reboot()
        skill = self.skills / "chat/SKILL.md"

        def edit(record):
            if record["kind"] == "maint-intent" and (record["step"].get("to") or {}).get("state") == "full":
                skill.write_text(skill.read_text() + "\nan edit at the full intent\n")
        refused = self.run_activate(path, crash=edit)["maintenance"]
        self.assertIn("full is refused", refused["refused"])
        self.assertEqual(self.maintenance()["state"], "partial")
        self.assertNotIn("full", self.kinds())

    # 4. The journaled seal is verified before a selected operation advances to full.
    def test_seal_drift_after_binding_refuses_full_and_is_never_resealed(self):
        plan, path = self.bound()
        site = next((Path(plan["staged_release"]) / "env").glob("lib/python*/site-packages"))
        scripts = site / "plateia_chat/scripts"
        scripts.chmod(0o755)
        self.addCleanup(scripts.chmod, 0o755)
        self.ops.reboot()
        refused = self.run_activate(path)["maintenance"]
        self.assertIn("seal", refused["refused"])
        self.assertEqual(self.maintenance()["state"], "partial")
        self.assertNotIn("full", self.kinds())
        self.assertEqual(scripts.stat().st_mode & 0o777, 0o755, "never resealed")



class ReviewThreeTests(AdmissionCase):
    """PR #22 review 3 (6157690404), the controller's two findings, each reproduced at e603ef0 first."""

    def backup_dir(self, plan, target):
        return self.root / "backups" / plan["operation"] / activate_release.re.sub(r"[^A-Za-z0-9._-]+", "_", target)

    # 1. A loaded backup must match its journaled digest, not only its own (mutable) index.
    def test_a_changed_backup_index_or_a_coordinated_payload_and_hash_change_is_refused(self):
        for change in ("the pchat link destination in its index", "a payload and its index hash together"):
            with self.subTest(change=change):
                self.fresh()
                plan, path = self.activated()
                if change.startswith("the pchat"):
                    index = self.backup_dir(plan, "pchat") / "index.json"
                    data = json.loads(index.read_text())
                    data[""] = {"link": str(self.where / "invented-elsewhere/pchat")}
                else:
                    folder = self.backup_dir(plan, "chat/scripts import location")
                    index = folder / "index.json"
                    data = json.loads(index.read_text())
                    entry = data["chatlib.py"]
                    payload = b"# an invented replacement payload\n"
                    (folder / entry["file"]).write_bytes(payload)
                    entry["sha256"] = activate_release.sha256_bytes(payload)
                index.write_text(json.dumps(data, sort_keys=True) + "\n")
                bound = self.targets_snapshot()
                with self.assertRaisesRegex(Refused, "journaled digest"):
                    self.rollback(path)
                self.assertEqual(self.targets_snapshot(), bound, "nothing was restored from the altered backup")
                self.assertNotIn("rolled-back", self.kinds())

    # 2. The record and the root are checked again after the final callbacks, immediately before the effect.
    def foreign_record(self):
        return json.dumps({"v": "maintenance/1", "operation": OTHER_OPERATION, "kind": "plateia-activation",
                           "plan_sha256": "e" * 64, "state": "partial", "generation": 2,
                           "acquired_boot": "boot-other", "boot": "boot-other", "bound_boot": None,
                           "legacy": "unverified", "binding": {"release_id": "", "bootstrap_digest": None,
                                                               "permits": []}},
                          sort_keys=True, separators=(",", ":")) + "\n"

    def swap_in_foreign(self):
        (self.state / "maintenance.json").unlink()
        _staging.write(self.state / "maintenance.json", self.foreign_record(), 0o600)

    def test_a_foreign_record_swapped_in_during_the_release_final_check_is_kept(self):
        plan, path = self.activated()
        self.serve(plan)
        after_intent, real = [], activate_release.first_write_evidence

        def hook(record):
            if record["kind"] == "maint-intent" and record["step"].get("step") == "release":
                after_intent.append(1)

        def evidence(*a, **k):
            if after_intent and after_intent[0] == 1:
                after_intent[0] = 2
                self.swap_in_foreign()
            return real(*a, **k)
        with mock.patch.object(activate_release, "first_write_evidence", evidence), self.assertRaises(Refused):
            self.do_release(path, crash=hook)
        self.assertEqual(after_intent, [2], "swapped during the final first-write evidence check")
        self.assertEqual((self.state / "maintenance.json").read_text(), self.foreign_record(), "kept")
        self.assertNotIn("released", self.kinds())

    def test_a_foreign_record_swapped_in_during_the_full_final_bridge_query_is_kept(self):
        plan, path = self.bound()
        self.ops.reboot()
        after_intent, real = [], self.ops.bridge_state

        def hook(record):
            if record["kind"] == "maint-intent" and (record["step"].get("to") or {}).get("state") == "full":
                after_intent.append(1)

        def bridge_state():
            if after_intent and after_intent[0] == 1:
                after_intent[0] = 2
                self.swap_in_foreign()
            return real()
        self.ops.bridge_state = bridge_state
        result = self.run_activate(path, crash=hook)
        self.assertEqual(after_intent, [2], "swapped during the final bridge-state query")
        self.assertIn("refused", result["maintenance"])
        self.assertEqual((self.state / "maintenance.json").read_text(), self.foreign_record(), "not overwritten")
        self.assertNotIn("full", self.kinds())



def fd_path(fd):
    """The path a directory descriptor names (Linux /proc, or macOS F_GETPATH): for the trace only."""
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return fcntl.fcntl(fd, 50, b"\0" * 1024).split(b"\0")[0].decode()  # F_GETPATH


class ExtensionOneTests(AdmissionCase):
    """PR #22 review 4 (6158063208), extension round 1, the controller's finding, reproduced at 38cb21a first."""

    def trace(self):
        """Patch the file system calls to log, in order: each new directory entry (its parent directory's path
        and identity), each fsync (the identity synced) and each journal append."""
        events, real_mkdir, real_open, real_fsync = [], os.mkdir, os.open, os.fsync
        real_append = activate_release.Journal.append

        def parent(path, dir_fd):
            if dir_fd is not None:
                st, where = os.fstat(dir_fd), fd_path(dir_fd)
            else:
                where = os.path.dirname(os.path.abspath(path))
                st = os.stat(where)
            return (st.st_dev, st.st_ino), os.path.realpath(where)

        def mkdir(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            events.append(("create", *parent(path, dir_fd), os.path.basename(path)))

        def open_(path, flags, mode=0o777, *, dir_fd=None):
            existed = True
            if flags & os.O_CREAT:
                try:
                    os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
                except FileNotFoundError:
                    existed = False
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            if not existed:
                events.append(("create", *parent(path, dir_fd), os.path.basename(path)))
            return fd

        def fsync(fd):
            real_fsync(fd)
            st = os.fstat(fd)
            events.append(("fsync", (st.st_dev, st.st_ino)))

        def append(journal, record):
            events.append(("append", record["kind"]))
            return real_append(journal, record)
        return events, [mock.patch.object(activate_release.os, "mkdir", mkdir),
                        mock.patch.object(activate_release.os, "open", open_),
                        mock.patch.object(activate_release.os, "fsync", fsync),
                        mock.patch.object(activate_release.Journal, "append", append)]

    def test_every_new_entry_in_the_activation_root_is_synced_before_what_depends_on_it(self):
        plan, path = self.prepared()
        events, patches = self.trace()
        for p in patches:
            p.start()
        try:
            self.assertEqual(self.run_activate(path)["state"], "selected")
        finally:
            for p in reversed(patches):
                p.stop()
        root = os.path.realpath(self.root)
        mine = [i for i, e in enumerate(events) if e[0] == "create"
                and (e[2] == os.path.dirname(root) and e[3] == os.path.basename(root) or e[2].startswith(root))]
        self.assertGreater(len(mine), 10, "the root, its lock and journal, and the backup hierarchy")
        unsynced = []
        for i in mine:
            _, parent, where, name = events[i]
            nxt = next((j for j in range(i + 1, len(events)) if events[j][0] == "append"), len(events))
            if not any(events[j] == ("fsync", parent) for j in range(i + 1, nxt)):
                unsynced.append(f"{where}/{name}")
        self.assertEqual(unsynced, [], "each new entry's parent is synced before the next journal record")



class ExtensionTwoTests(AdmissionCase):
    """PR #22 extension review 1 (6158798456), the controller's two findings, reproduced at 13e9194 first."""

    trace = ExtensionOneTests.trace

    def traced(self, path):
        events, patches = self.trace()
        for p in patches:
            p.start()
        try:
            return self.run_activate(path), events
        finally:
            for p in reversed(patches):
                p.stop()

    @staticmethod
    def ident(path):
        st = os.stat(path)
        return st.st_dev, st.st_ino

    def synced_before(self, events, parent, kind):
        """Whether `parent` was synced before the first journal record of `kind`."""
        end = next((i for i, e in enumerate(events) if e == ("append", kind)), len(events))
        return ("fsync", parent) in events[:end]

    # 1. An entry left by an interrupted creation is synced before what depends on it, even though it exists.
    def test_an_activation_root_left_unsynced_is_synced_before_the_first_record(self):
        plan, path = self.prepared()
        os.mkdir(self.root, 0o700)  # an earlier run made it and died before syncing its parent
        result, events = self.traced(path)
        self.assertEqual(result["state"], "selected")
        first = next(e[1] for e in events if e[0] == "append")
        self.assertTrue(self.synced_before(events, self.ident(self.root.parent), first),
                        "the root's parent is synced before the first journal record")

    def test_backup_directories_left_unsynced_are_synced_before_their_backup_record(self):
        plan, path = self.prepared()
        leaf = self.root / "backups" / plan["operation"] / "pchat"
        os.makedirs(leaf, 0o700)  # an earlier run made them and died before syncing their parents
        result, events = self.traced(path)
        self.assertEqual(result["state"], "selected")
        for parent in (self.root, self.root / "backups", self.root / "backups" / plan["operation"]):
            with self.subTest(parent=parent.name):
                self.assertTrue(self.synced_before(events, self.ident(parent), "backup"),
                                f"{parent.name} is synced before the first backup record")

    # 4. The producers precondition holds when full is written, not only when binding began.
    def test_full_needs_every_designated_account_still_tracked(self):
        for case, producers in (("no producer evidence", None),
                                ("an untracked designated account", {"alp-solver-2": "tracked", "sam": "untracked"})):
            with self.subTest(case=case):
                self.fresh()
                plan, path = self.bound()
                self.ops.producer_map = producers
                self.ops.reboot()
                refused = self.run_activate(path)["maintenance"]
                self.assertIn("producer", refused.get("refused", ""))
                self.assertEqual(self.maintenance()["state"], "partial")
                self.assertNotIn("full", self.kinds())


if __name__ == "__main__":
    unittest.main()
