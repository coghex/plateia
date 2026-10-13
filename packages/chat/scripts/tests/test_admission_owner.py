"""The owning operation's primitives (docs/writer_admission.md sections 4.3, 4.4, 4.7, 4.8; design tests 3, 4
(primitives), 5, 6, 7 and 8, reduced as #24's review requires): acquire, drain, advance, release and a read-only
reconcile, each under a caller-supplied journal or fence. The caller's own decisions (verifying that legacy
writers are retired and inhibited, the post-check, rollback) are #21's and are not tested here: a fence is a
fake, and so is every boot. Invented operations, plans and boots on throwaway state directories."""
import fcntl
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import delivery_store as ds  # noqa: E402
from admission_fakes import (OPERATION, OTHER_OPERATION, PLAN, Journal, crash_at,  # noqa: E402
                             only_nonblocking_flocks, put_record, record_fields, shared_lock, snapshot)
from outbox_fakes import Clock, Crash, SimFS  # noqa: E402


class OwnerCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="owner-", dir=_isolation.HOME))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.paths = ds.Paths(self.root / "state")
        self.journal = Journal()
        self.clock = Clock()

    def owner(self, journal=None, operation=OPERATION, plan=PLAN, boot="boot-A", pid=7001, probes=None, fs=None):
        return ds.Owner(self.paths, operation=operation, kind="plateia-activation", plan_sha256=plan,
                        fence=journal or self.journal, probes=probes or ds.FakeProbes(boot=boot, pid=pid),
                        mono=self.clock.mono, sleep=self.clock.advance, fs=fs)

    def record(self):
        return ds.read_maintenance(self.paths)

    def fields(self):
        return dict(self.record().fields)

    def admitted(self):
        with ds.section(self.paths, wait=0) as adm:
            return adm.admitted

    def quiet_period(self):
        time.sleep(0.05)  # longer than the filesystem's timestamp granularity: a later change is a visible one

    def whole(self, owner=None):
        """Acquire, drain and advance to full: the operation's happy path."""
        owner = owner or self.owner()
        owner.acquire()
        owner.drain()
        owner.advance("full", boot="boot-B", legacy="retired-and-inhibited",
                      binding={"release_id": "rel-1", "bootstrap_digest": "sha256:" + "d" * 64,
                               "permits": ["bridge-start"]})
        return owner


class LifecycleTests(OwnerCase):
    def test_acquire_drain_full_release_with_every_step_journaled_around_its_effect(self):
        owner = self.owner()
        rec = owner.acquire()
        self.assertEqual((rec.state, rec.fields["generation"]), ("draining", 1))
        self.assertEqual((rec.fields["acquired_boot"], rec.fields["boot"], rec.fields["legacy"]),
                         ("boot-A", "boot-A", "unverified"))
        self.assertTrue(self.paths.admission_lock.exists())
        self.assertFalse(self.admitted())
        result = owner.drain()
        self.assertEqual((result["landed"], result["drained"]), (True, True))
        self.assertEqual((self.record().state, self.fields()["generation"]), ("partial", 2))
        owner.advance("full", boot="boot-B", legacy="retired-and-inhibited", bound_boot="boot-A",
                      set_bound_boot=True, binding={"release_id": "rel-1", "bootstrap_digest": "sha256:" + "d" * 64,
                                                    "permits": ["bridge-start"]})
        f = self.fields()
        self.assertEqual((f["state"], f["generation"], f["boot"], f["acquired_boot"], f["bound_boot"], f["legacy"],
                          f["binding"]["permits"]), ("full", 3, "boot-B", "boot-A", "boot-A",
                                                     "retired-and-inhibited", ["bridge-start"]))
        owner.release()
        self.assertFalse(self.paths.maintenance.exists())
        self.assertTrue(self.paths.admission_lock.exists(), "release is the record's unlink; the lock file stays")
        self.assertTrue(self.admitted())
        # every step: verified, then an intent, then its effect, then an outcome; nothing is left pending
        self.assertEqual([k for k, _, _ in self.journal.records if k != "progress"], ["intent", "outcome"] * 5)
        # a record write journals the file it will publish before it publishes, and the published record after
        self.assertEqual([[k for k in r] for r in self.journal.progresses("write-record")],
                         [["tmp"], ["published"]] * 2)
        self.assertEqual(self.journal.progresses("write-draining")[1]["published"],
                         self.journal.outcomes("write-draining")[0]["evidence"])
        self.assertEqual([s["step"] for s in self.journal.intents()],
                         ["create-lock", "write-draining", "write-record", "write-record", "release"])
        self.assertEqual(self.journal.verified, [s["step"] for s in self.journal.intents()])
        self.assertIsNone(self.journal.pending())
        self.assertIsNone(self.journal.evidence(), "a released record leaves no evidence of a live one")

    def test_the_outcomes_report_generation_bytes_inode_and_change_time_for_the_caller_to_journal(self):
        owner = self.owner()
        owner.acquire()
        owner.drain()
        for outcome in (self.journal.outcomes("write-draining")[0], self.journal.outcomes("write-record")[0]):
            evidence = outcome["evidence"]
            self.assertEqual(set(evidence), {"generation", "sha256", "ino", "dev", "ctime_ns"})
        self.assertEqual(self.journal.evidence(), self.record().evidence(), "the last journaled write is the record")
        step = self.journal.intents("write-record")[0]
        self.assertEqual(step["from"], self.journal.outcomes("write-draining")[0]["evidence"])
        self.assertEqual(step["to"]["generation"], 2)
        self.assertEqual(step["to"]["sha256"], self.record().evidence()["sha256"], "the exact bytes it wrote")
        self.assertEqual((step["operation"], step["plan_sha256"]), (OPERATION, PLAN))

    def test_the_record_is_a_private_canonical_file_written_complete_at_the_final_name(self):
        owner = self.owner()
        owner.acquire()
        st = os.stat(self.paths.maintenance)
        self.assertEqual((st.st_mode & 0o777, st.st_uid), (0o600, os.getuid()))
        self.assertTrue(self.record().valid)
        self.assertEqual([n for n in os.listdir(self.paths.state) if n.endswith(".tmp")], [], "no temp file is left")
        owner.drain()
        self.assertEqual([n for n in os.listdir(self.paths.state) if n.endswith(".tmp")], [])
        self.assertEqual(self.fields()["pid"], 7001, "the acting process, for display and reconcile only")

    def test_draining_is_linked_and_never_replaces_a_file(self):
        put_record(self.paths, record_fields(operation=OTHER_OPERATION, state="draining", generation=1))
        before = snapshot(self.paths.state)
        with self.assertRaises(ds.NotOwner):
            self.owner().acquire()
        self.assertEqual(snapshot(self.paths.state), before, "nothing changes")
        self.assertEqual(self.journal.records, [])

    def test_a_record_that_appears_between_the_check_and_the_link_is_not_replaced(self):
        owner = self.owner()

        def foreign(name):
            if name == "tmp-created":
                put_record(self.paths, record_fields(operation=OTHER_OPERATION, state="draining", generation=1))
        owner.point = foreign
        with self.assertRaises(FileExistsError):
            owner.acquire()
        self.assertEqual(self.fields()["operation"], OTHER_OPERATION, "link() never replaces a file")
        self.assertIsNotNone(self.journal.pending(), "the intent was journaled, with no outcome")
        with self.assertRaises(ds.NotOwner):  # a resume finds neither the prior (absent) nor its own write
            self.owner().acquire()
        self.assertEqual(self.fields()["operation"], OTHER_OPERATION, "the replacement is never adopted")

    def test_illegal_transitions_are_refused(self):
        owner = self.owner()
        owner.acquire()
        before = snapshot(self.paths.state)
        with self.assertRaises(ds.OwnerError):
            owner.advance("full")  # not from draining
        with self.assertRaises(ds.OwnerError):
            owner.advance("draining")
        self.assertEqual(snapshot(self.paths.state), before)

    def test_full_is_refused_when_the_boot_cannot_be_read(self):
        owner = self.owner()
        owner.acquire()
        owner.drain()
        before = snapshot(self.paths.state)
        owner.probes = ds.FakeProbes(boot="unknown", pid=7001)
        with self.assertRaises(ds.OwnerError) as caught:
            owner.advance("full")
        self.assertIn("unknown", str(caught.exception))
        self.assertEqual(snapshot(self.paths.state), before)

    def test_leaving_full_for_partial_resets_legacy_coverage(self):
        owner = self.whole()
        owner.advance("partial", boot="boot-B")
        f = self.fields()
        self.assertEqual((f["state"], f["legacy"]), ("partial", "unverified"))

    def test_the_drain_takes_the_lock_exclusive_only_for_the_one_durable_write(self):
        owner = self.owner()
        owner.acquire()
        seen = {}

        def point(name):
            if name == "locked":
                fd = os.open(self.paths.admission_lock, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    seen["held at the write"] = True
                finally:
                    os.close(fd)
            if name == "written":
                seen["state at the end of the write"] = self.record().state
        owner.point = point
        owner.drain()
        self.assertEqual(seen, {"held at the write": True, "state at the end of the write": "partial"})
        with shared_lock(self.paths):  # and released: a section is admitted at once
            pass

    def test_a_section_open_keeps_the_drain_incomplete_and_it_holds_nothing_and_never_blocks(self):
        owner = self.owner()
        owner.acquire()
        with only_nonblocking_flocks(ds) as seen:
            with shared_lock(self.paths):
                before = snapshot(self.paths.state)
                result = owner.drain(wait=1.0)
                self.assertEqual((result["landed"], result["drained"]), (False, False))
                self.assertGreater(result["attempts"], 3)
                self.assertEqual(snapshot(self.paths.state), before, "the record stays draining, unchanged")
            self.assertEqual(self.record().state, "draining")
            self.assertTrue(all(op & fcntl.LOCK_NB for op in seen))
        self.assertEqual(self.journal.outcomes("write-record")[0]["attempts"], result["attempts"],
                         "the journal shows the drain's attempts")
        # the deadline only bounds the owner's wait: the next try, with the section over, drains
        self.assertTrue(owner.drain()["drained"])
        self.assertEqual(self.record().state, "partial")

    def test_the_wait_is_the_fake_clock_never_a_real_sleep(self):
        owner = self.owner()
        owner.acquire()
        start = time.monotonic()
        with shared_lock(self.paths):
            owner.drain(wait=ds.DRAIN_MAX)
        self.assertLess(time.monotonic() - start, 5, "300 s of fake time")
        self.assertGreaterEqual(self.clock.mono_t - 1000.0, ds.DRAIN_MAX - ds.ADMIT_POLL)


class WrongOperationTests(OwnerCase):
    def test_case_6_a_second_operation_is_refused_everywhere_and_changes_nothing(self):
        first = self.owner()
        first.acquire()
        first.drain()
        before, bytes_before = snapshot(self.paths.state), self.paths.maintenance.read_bytes()
        other_journal = Journal()
        other = self.owner(journal=other_journal, operation=OTHER_OPERATION, plan="q" * 64)
        for attempt in (other.acquire, other.drain, lambda: other.advance("full", boot="boot-B"), other.release):
            with self.subTest(attempt=getattr(attempt, "__name__", "advance")):
                with self.assertRaises(ds.NotOwner):
                    attempt()
        self.assertEqual(snapshot(self.paths.state), before)
        self.assertEqual(self.paths.maintenance.read_bytes(), bytes_before)
        self.assertEqual(self.fields()["generation"], 2)
        self.assertEqual(other_journal.intents(), [], "it journaled no intent")
        first.advance("full", boot="boot-B", legacy="retired-and-inhibited")  # the first can still resume
        self.assertEqual(self.fields()["generation"], 3)

    def test_the_same_operation_under_another_plan_is_not_the_owner(self):
        self.owner().acquire()
        with self.assertRaises(ds.NotOwner):
            self.owner(plan="z" * 64).acquire()

    def test_a_lost_journal_cannot_release_or_adopt_its_own_record(self):
        self.owner().acquire()
        before = snapshot(self.paths.state)
        fresh = self.owner(journal=Journal())
        for attempt in (fresh.acquire, fresh.drain, fresh.release):
            with self.assertRaises(ds.NotOwner):
                attempt()
        self.assertEqual(snapshot(self.paths.state), before, "repair is an owner decision")
        self.assertFalse(self.admitted(), "and every writer keeps refusing")


class EvidenceTests(OwnerCase):
    """A record whose bytes, inode or change time differ from the operation's last journaled write is not the
    operation's own: it is unknown to the operation, never adopted (4.3)."""

    def refuses_all(self, label):
        before = snapshot(self.paths.state)
        owner = self.owner()
        for attempt in (owner.drain, lambda: owner.advance("full", boot="boot-B"), owner.release, owner.acquire):
            with self.subTest(label, attempt=getattr(attempt, "__name__", "advance")):
                with self.assertRaises(ds.NotOwner):
                    attempt()
        self.assertEqual(snapshot(self.paths.state), before)
        self.assertEqual(self.journal.pending(), None, "it journaled no intent for a record it cannot prove")

    def test_other_bytes_with_the_same_operation_are_not_the_operations_write(self):
        self.owner().acquire()
        put_record(self.paths, record_fields(state="draining", generation=1, boot="boot-X"))
        self.refuses_all("other bytes")

    def test_the_same_bytes_in_a_new_inode_are_not_the_operations_write(self):
        self.owner().acquire()
        raw = self.paths.maintenance.read_bytes()
        replacement = self.paths.state / "elsewhere"
        replacement.write_bytes(raw)
        os.chmod(replacement, 0o600)
        os.replace(replacement, self.paths.maintenance)
        self.assertEqual(self.paths.maintenance.read_bytes(), raw)
        self.refuses_all("another inode")

    def test_the_same_bytes_and_inode_with_a_later_change_time_are_not_the_operations_write(self):
        self.owner().acquire()
        self.quiet_period()
        os.chmod(self.paths.maintenance, 0o600)
        os.utime(self.paths.maintenance)
        self.refuses_all("a later change time")

    def test_an_unknown_record_is_never_adopted_or_repaired(self):
        self.owner().acquire()
        self.paths.maintenance.write_bytes(b"{")
        self.refuses_all("an unknown record")
        self.assertEqual(self.paths.maintenance.read_bytes(), b"{")

    def test_a_deleted_record_is_not_released_or_resumed(self):
        self.owner().acquire()
        os.unlink(self.paths.maintenance)
        owner = self.owner()
        for attempt in (owner.drain, owner.release):
            with self.assertRaises(ds.NotOwner):
                attempt()

    def test_a_journal_that_kept_no_dev_still_matches_on_the_other_four(self):
        owner = self.owner()
        owner.acquire()
        self.journal.last.pop("dev")
        owner.drain()  # matches generation, bytes, inode and change time
        self.assertEqual(self.record().state, "partial")


class FenceTests(OwnerCase):
    def test_a_failed_or_unknown_fence_permits_nothing(self):
        for label, verifies in (("false", False), ("unknown", None), ("error", RuntimeError("journal root moved"))):
            with self.subTest(label):
                shutil.rmtree(self.paths.state, ignore_errors=True)
                self.journal = Journal()
                self.journal.verifies = verifies
                owner = self.owner()
                with self.assertRaises(ds.FenceFailed):
                    owner.acquire()
                self.assertFalse(self.paths.state.exists() and os.listdir(self.paths.state), "nothing was created")
                self.assertEqual(self.journal.records, [], "and nothing journaled")
                self.journal.verifies = True  # the fence recovers; this owner, which saw it fail, still refuses
                with self.assertRaises(ds.FenceFailed):
                    owner.acquire()
                self.assertFalse(self.paths.state.exists() and os.listdir(self.paths.state))
                self.owner().acquire()  # a new owner, with a fence that holds, can
                self.assertEqual(self.record().state, "draining")

    def test_a_fence_that_fails_midway_permits_no_further_mutation(self):
        owner = self.owner()
        owner.acquire()
        self.journal.verifies = False
        before = snapshot(self.paths.state)
        for attempt in (owner.drain, owner.release):
            with self.assertRaises(ds.FenceFailed):
                attempt()
        self.journal.verifies = True
        with self.assertRaises(ds.FenceFailed):
            owner.drain()
        self.assertEqual(snapshot(self.paths.state), before)

    def test_an_intent_that_cannot_be_journaled_stops_before_the_effect(self):
        owner = self.owner()
        owner.acquire()
        self.journal.fail_intent = OSError(28, "no space (fake)")
        before = snapshot(self.paths.state)
        with self.assertRaises(ds.FenceFailed):
            owner.drain()
        self.assertEqual(snapshot(self.paths.state), before, "no write without its intent")

    def test_an_outcome_that_cannot_be_journaled_leaves_the_effect_and_a_pending_intent(self):
        owner = self.owner()
        self.journal.fail_outcome = None
        owner.acquire()
        self.journal.fail_outcome = OSError(5, "input/output error (fake)")
        with self.assertRaises(ds.OutcomeNotRecorded) as caught:
            owner.drain()
        self.assertTrue(caught.exception.result["landed"])
        self.assertEqual(self.record().state, "partial", "the effect happened")
        self.assertEqual(self.journal.pending()["step"], "write-record")
        with self.assertRaises(ds.FenceFailed):
            owner.release()  # this owner saw its fence fail
        # a resume, with the journal back, adopts what is on disk: the journaled bytes, not a rewrite
        self.journal.fail_outcome = None
        ino = os.stat(self.paths.maintenance).st_ino
        resumed = self.owner()
        rec = resumed.acquire()
        self.assertEqual((rec.state, rec.fields["generation"]), ("partial", 2))
        self.assertEqual(os.stat(self.paths.maintenance).st_ino, ino, "not rewritten")
        self.assertIsNone(self.journal.pending())
        self.assertEqual(self.journal.evidence(), self.record().evidence())
        self.assertEqual(len(self.journal.intents("write-record")), 1)


class CrashTests(OwnerCase):
    """Design tests 3 and 8: a crash at every record write and journal append of acquisition, full and release."""

    def run_to_end(self, owner):
        owner.acquire()
        if self.record().state == "draining":
            owner.drain()
        if self.record().state == "partial" and self.fields()["generation"] == 2:
            owner.advance("full", boot="boot-B", legacy="retired-and-inhibited",
                          binding={"release_id": "rel-1", "bootstrap_digest": None, "permits": []})

    def end_state(self):
        f = self.fields()
        return f["state"], f["generation"], f["boot"], f["legacy"]

    def test_a_crash_at_any_point_of_acquisition_leaves_no_torn_record_and_resumes_to_the_same_end(self):
        clean = self.owner()
        self.run_to_end(clean)
        expected = self.end_state()
        for point in ("intent", "tmp-created", "linked", "effect", "locked", "renamed", "written"):
            for rounds in range(2):  # a crash in the first step reached, then again after a resume
                with self.subTest(point=point, rounds=rounds):
                    shutil.rmtree(self.paths.state, ignore_errors=True)
                    self.journal = Journal()
                    for _ in range(rounds + 1):
                        crashing = self.owner()
                        crash_at(crashing, point)
                        try:
                            self.run_to_end(crashing)
                        except Crash:
                            pass
                        rec = self.record()
                        self.assertTrue(rec.valid or not rec.present, "no final name ever holds a torn record")
                        if rec.present:
                            self.assertFalse(self.admitted(), "every writer keeps refusing while a record exists")
                    self.run_to_end(self.owner())  # the resume
                    self.assertEqual(self.end_state(), expected)
                    self.assertIsNone(self.journal.pending())
                    self.assertEqual(self.journal.evidence(), self.record().evidence())
                    self.assertEqual([n for n in os.listdir(self.paths.state) if n.endswith(".tmp")], [],
                                     "a journaled temporary file is removed on resume")

    def test_a_crash_after_the_temporary_record_leaves_it_journaled_and_unpublished(self):
        owner = self.owner()
        crash_at(owner, "tmp-created")
        with self.assertRaises(Crash):
            owner.acquire()
        self.assertFalse(self.paths.maintenance.exists())
        tmp = self.journal.pending()["tmp"]
        self.assertTrue((self.paths.state / tmp).exists())
        self.assertTrue(self.admitted(), "nothing is published yet: no record, so writers are admitted")
        rec = self.owner().acquire()
        self.assertEqual(rec.fields["generation"], 1)
        self.assertFalse((self.paths.state / tmp).exists())
        self.assertEqual(self.journal.outcomes("write-draining")[0], {"landed": False, "resumed": True})

    def test_a_crash_after_the_link_resumes_by_adopting_the_record_without_writing_it_again(self):
        owner = self.owner()
        crash_at(owner, "linked")
        with self.assertRaises(Crash):
            owner.acquire()
        self.assertEqual(self.record().state, "draining", "complete at the final name")
        ino = os.stat(self.paths.maintenance).st_ino
        rec = self.owner().acquire()
        self.assertEqual((rec.state, os.stat(self.paths.maintenance).st_ino), ("draining", ino))
        self.assertEqual(len(self.journal.intents("write-draining")), 1)
        self.assertEqual(self.journal.outcomes("write-draining")[0]["landed"], True)

    def test_a_crash_holding_the_exclusive_lock_before_partial_is_durable_releases_the_lock_and_redrains(self):
        owner = self.owner()
        owner.acquire()
        crash_at(owner, "locked")
        with self.assertRaises(Crash):
            owner.drain()
        self.assertEqual(self.record().state, "draining")
        with shared_lock(self.paths):  # the lock went with the process
            pass
        self.owner().drain()
        self.assertEqual(self.record().state, "partial")

    def test_partial_never_appears_without_a_completed_drain(self):
        owner = self.owner()
        owner.acquire()
        with shared_lock(self.paths):
            owner.drain(wait=0.5)
            self.assertEqual(self.record().state, "draining")
        crash_at(owner := self.owner(), "written")
        with self.assertRaises(Crash):
            owner.drain()
        self.assertEqual(self.record().state, "partial", "written under the exclusive lock, so the drain completed")
        rec = self.owner().acquire()
        self.assertEqual((rec.state, rec.fields["generation"]), ("partial", 2))

    def test_a_crash_after_the_release_unlink_is_a_completed_release(self):
        owner = self.whole()
        crash_at(owner, "unlinked")
        with self.assertRaises(Crash):
            owner.release()
        self.assertFalse(self.paths.maintenance.exists())
        self.assertTrue(self.admitted(), "the unlink is the moment of release")
        self.assertEqual(self.journal.pending()["step"], "release")
        done = self.owner().release()
        self.assertEqual(done["landed"], True)
        self.assertIsNone(self.journal.pending())
        self.assertEqual(self.journal.outcomes("release")[0]["landed"], True)

    def test_a_crash_before_the_release_unlink_resumes_and_releases(self):
        owner = self.whole()
        crash_at(owner, "intent")
        with self.assertRaises(Crash):
            owner.release()
        self.assertTrue(self.paths.maintenance.exists())
        self.assertFalse(self.admitted())
        self.owner().release()
        self.assertFalse(self.paths.maintenance.exists())
        self.assertEqual([o["landed"] for o in self.journal.outcomes("release")], [False, True],
                         "the first intent found nothing done, and the retry released")

    def test_case_3_after_the_release_unlink_admission_resumes_even_if_the_outcome_was_not_journaled(self):
        owner = self.whole()
        self.journal.fail_outcome = OSError(5, "input/output error (fake)")
        with self.assertRaises(ds.OutcomeNotRecorded):
            owner.release()
        self.assertFalse(self.paths.maintenance.exists())
        self.assertTrue(self.admitted(), "writers are admitted: not refused for want of a journal append")
        self.assertEqual(self.journal.pending()["step"], "release")
        self.journal.fail_outcome = None
        self.assertEqual(self.owner().release()["landed"], True, "recovered from what is on disk")
        self.assertIsNone(self.journal.pending())

    def test_a_replaced_record_after_a_crash_is_never_adopted(self):
        owner = self.owner()
        owner.acquire()
        crash_at(owner, "effect")  # the write landed; its outcome did not
        with self.assertRaises(Crash):
            owner.drain()
        put_record(self.paths, record_fields(state="partial", generation=2, boot="boot-X"))
        before = snapshot(self.paths.state)
        with self.assertRaises(ds.NotOwner):
            self.owner().acquire()
        self.assertEqual(snapshot(self.paths.state), before, "different bytes: nothing adopted, rewritten or removed")
        self.assertEqual(self.journal.pending()["step"], "write-record", "still pending")

    def test_a_temporary_file_the_operation_did_not_journal_is_kept_and_reported(self):
        stray = self.paths.state / ".maintenance.json.0000000000aa.tmp"
        self.paths.state.mkdir(parents=True)
        stray.write_bytes(b"someone else's")
        owner = self.owner()
        crash_at(owner, "tmp-created")
        with self.assertRaises(Crash):
            owner.acquire()
        journaled = self.journal.pending()["tmp"]
        report = self.owner().reconcile()
        self.assertEqual((report["journaled_temp_files"], report["unjournaled_temp_files"]),
                         ([journaled], [stray.name]))
        self.owner().acquire()
        self.assertTrue(stray.exists(), "kept")
        self.assertFalse((self.paths.state / journaled).exists(), "the journaled one is removed on resume")
        self.assertEqual(self.owner().reconcile()["unjournaled_temp_files"], [stray.name], "and still reported")


class RecoveryIdentityTests(OwnerCase):
    """Recovery adopts a landed write only if it is the file the operation published: the identity of its temporary
    file, journaled before it was published, and, once journaled, the published record's evidence with its change
    time. Identical bytes in another file are a replacement, never adopted."""

    def crashed(self, how, point):
        """A run that dies at `point` of acquisition (`how` == "acquire") or of the drain; returns the pending step."""
        owner = self.owner()
        if how == "drain":
            owner.acquire()
        crash_at(owner, point, nth=2 if how == "acquire" and point in ("intent", "effect") else 1)
        with self.assertRaises(Crash):
            owner.acquire() if how == "acquire" else owner.drain()
        return self.journal.pending()

    def replace_with_identical_bytes(self):
        raw = self.paths.maintenance.read_bytes()
        other = self.paths.state / "elsewhere"
        other.write_bytes(raw)
        os.chmod(other, 0o600)
        os.replace(other, self.paths.maintenance)
        self.assertEqual(self.paths.maintenance.read_bytes(), raw)
        return raw

    PLAN_POINTS = (("acquire", "linked"), ("acquire", "published"), ("acquire", "effect"),
                   ("drain", "renamed"), ("drain", "written"), ("drain", "published"), ("drain", "effect"))

    def test_identical_bytes_in_a_new_inode_are_a_replacement_at_every_point_after_the_write(self):
        for how, point in self.PLAN_POINTS:
            with self.subTest(how=how, point=point):
                shutil.rmtree(self.paths.state, ignore_errors=True)
                self.journal = Journal()
                self.crashed(how, point)
                raw = self.replace_with_identical_bytes()
                ino, evidence = os.stat(self.paths.maintenance).st_ino, self.journal.evidence()
                resumed = self.owner()
                for attempt in (resumed.acquire, self.owner().release, self.owner().drain):
                    with self.assertRaises(ds.NotOwner):
                        attempt()
                self.assertEqual(self.paths.maintenance.read_bytes(), raw, "not rewritten, adopted or removed")
                self.assertEqual(os.stat(self.paths.maintenance).st_ino, ino)
                self.assertIsNotNone(self.journal.pending(), "the intent stays pending: no outcome was journaled")
                self.assertEqual(self.journal.evidence(), evidence, "the replacement's inode and change time were "
                                 "never journaled as the operation's own")
                self.assertFalse(self.admitted(), "and every writer keeps refusing")

    def test_the_operations_own_file_is_adopted_at_every_point(self):
        for how, point in self.PLAN_POINTS:
            with self.subTest(how=how, point=point):
                shutil.rmtree(self.paths.state, ignore_errors=True)
                self.journal = Journal()
                self.crashed(how, point)
                ino = os.stat(self.paths.maintenance).st_ino
                rec = self.owner().acquire()
                self.assertEqual(os.stat(self.paths.maintenance).st_ino, ino, "not rewritten")
                self.assertEqual(self.journal.evidence(), rec.evidence())
                self.assertIsNone(self.journal.pending())

    def test_a_changed_change_time_after_publication_was_journaled_is_not_adopted(self):
        for how in ("acquire", "drain"):
            with self.subTest(how=how):
                shutil.rmtree(self.paths.state, ignore_errors=True)
                self.journal = Journal()
                step = self.crashed(how, "effect")  # published and journaled; the outcome is not
                self.assertIn("published", step["progress"])
                self.quiet_period()
                os.chmod(self.paths.maintenance, 0o600)  # same bytes, same inode, a later change time
                os.utime(self.paths.maintenance)
                before = snapshot(self.paths.state)
                with self.assertRaises(ds.NotOwner):
                    self.owner().acquire()
                self.assertEqual(snapshot(self.paths.state), before)
                self.assertIsNotNone(self.journal.pending())

    def test_a_record_with_the_journaled_bytes_is_refused_when_no_temporary_file_was_journaled(self):
        owner = self.owner()
        crash_at(owner, "intent", nth=2)  # the record's intent is journaled; nothing was created or published
        with self.assertRaises(Crash):
            owner.acquire()
        step = self.journal.pending()
        self.assertNotIn("progress", step)
        put_record(self.paths, record_fields(state="draining", generation=1, boot="boot-A"))  # anything at the name
        with self.assertRaises(ds.NotOwner):
            self.owner().acquire()

    def test_the_temporary_files_identity_is_journaled_before_it_is_published(self):
        owner = self.owner()
        crash_at(owner, "tmp-journaled")
        with self.assertRaises(Crash):
            owner.acquire()
        step = self.journal.pending()
        st = os.stat(self.paths.state / step["tmp"])
        self.assertEqual(step["progress"], {"tmp": {"ino": st.st_ino, "dev": st.st_dev}})
        self.assertFalse(self.paths.maintenance.exists(), "journaled first, published after")

    def test_progress_that_cannot_be_journaled_publishes_nothing(self):
        owner = self.owner()
        self.journal.fail_progress = OSError(5, "input/output error (fake)")
        with self.assertRaises(ds.FenceFailed):
            owner.acquire()
        self.assertFalse(self.paths.maintenance.exists(), "an unjournaled identity is never published")
        with self.assertRaises(ds.FenceFailed):
            owner.acquire()


class RecordingFS(SimFS):
    """The filesystem model that drops what was never synced, noting each sync in a shared list of events."""

    def __init__(self, root, events=None):
        if events is not None:
            self.events = events  # kept across the power loss, which builds the model again
        super().__init__(root)

    def sync_dir(self, path):
        self.events.append("sync_dir")
        super().sync_dir(path)

    def sync_file(self, path):
        self.events.append("sync_file")
        super().sync_file(path)


class DurableRecoveryTests(OwnerCase):
    """A recovery makes its decision durable, the file and the state directory, before it journals the outcome, so a
    power loss cannot undo an effect already recorded as complete. The original crash comes before the normal sync."""

    def setUp(self):
        super().setUp()
        self.paths.state.mkdir()
        self.events = []
        self.fs = RecordingFS(self.paths.state, self.events)
        real = self.journal.outcome

        def outcome(step, result):
            self.events.append("outcome")
            return real(step, result)
        self.journal.outcome = outcome

    def resume(self, how):
        del self.events[:]
        owner = self.owner(fs=self.fs)
        {"acquire": owner.acquire, "release": owner.release}[how]()
        self.assertIn("outcome", self.events)
        first = self.events.index("outcome")
        self.assertIn("sync_dir", self.events[:first], "the state directory is synced before the outcome is journaled")

    def test_a_recovered_link_is_durable_before_its_outcome(self):
        owner = self.owner(fs=self.fs)
        crash_at(owner, "linked")
        with self.assertRaises(Crash):
            owner.acquire()
        self.resume("acquire")
        self.fs.power_loss()
        self.assertEqual(self.record().state, "draining", "the recovered record survives a power loss")
        self.assertEqual(self.record().evidence()["generation"], 1)

    def test_a_recovered_rename_is_durable_before_its_outcome(self):
        owner = self.owner(fs=self.fs)
        owner.acquire()
        crash_at(owner, "renamed")
        with self.assertRaises(Crash):
            owner.drain()
        self.resume("acquire")
        self.fs.power_loss()
        self.assertEqual((self.record().state, self.fields()["generation"]), ("partial", 2))

    def test_a_recovered_release_is_durable_before_its_outcome(self):
        owner = self.whole(self.owner(fs=self.fs))
        crash_at(owner, "unlinked")
        with self.assertRaises(Crash):
            owner.release()
        self.resume("release")
        self.fs.power_loss()
        self.assertFalse(self.paths.maintenance.exists(), "the release is not undone by a power loss")
        self.assertTrue(self.admitted())

    def test_a_recovered_temporary_files_removal_is_durable_before_its_outcome(self):
        owner = self.owner(fs=self.fs)
        crash_at(owner, "tmp-journaled")
        with self.assertRaises(Crash):
            owner.acquire()
        tmp = self.journal.pending()["tmp"]
        self.fs.sync_dir(self.paths.state)  # it is a name the model saw durably
        self.resume("acquire")
        self.fs.power_loss()
        self.assertFalse((self.paths.state / tmp).exists())
        self.assertEqual(self.record().state, "draining", "and the retry then wrote the record")

    def test_a_sync_that_fails_keeps_the_intent_pending_and_journals_no_outcome(self):
        owner = self.owner(fs=self.fs)
        crash_at(owner, "linked")
        with self.assertRaises(Crash):
            owner.acquire()
        real = self.fs.sync_dir

        def broken(path):
            raise OSError(5, "input/output error (fake)")
        self.fs.sync_dir = broken
        with self.assertRaises(OSError):
            self.owner(fs=self.fs).acquire()
        self.assertIsNotNone(self.journal.pending(), "not durable, so not recorded as done")
        self.assertEqual(self.journal.outcomes("write-draining"), [])
        self.fs.sync_dir = real
        self.assertEqual(self.owner(fs=self.fs).acquire().state, "draining")
        self.assertIsNone(self.journal.pending())


class RebootTests(OwnerCase):
    def test_case_7_a_reboot_is_never_a_release_and_resume_drains_again_recording_the_new_boot(self):
        owner = self.owner(boot="boot-A")
        owner.acquire()
        owner.drain()
        owner.advance("partial", set_bound_boot=True, bound_boot="boot-A")
        bytes_before = self.paths.maintenance.read_bytes()
        # boot-B: the record persists, and a section of any writer refuses from its first
        self.assertFalse(self.admitted())
        self.assertEqual(self.paths.maintenance.read_bytes(), bytes_before)
        after = self.owner(boot="boot-B")
        rec = after.acquire()  # resume: the same operation, verified as the record's owner
        self.assertEqual(rec.state, "partial")
        after.drain(boot="boot-B")
        f = self.fields()
        self.assertEqual((f["state"], f["boot"], f["acquired_boot"], f["bound_boot"]),
                         ("partial", "boot-B", "boot-A", "boot-A"))
        self.assertEqual(f["generation"], 4)

    def test_case_7_with_the_boot_unreadable_full_is_refused_as_unknown(self):
        self.owner(boot="boot-A").acquire()
        owner = self.owner(boot="unknown")
        owner.drain()  # a drain does not claim legacy writers are gone
        before = snapshot(self.paths.state)
        with self.assertRaises(ds.OwnerError):
            owner.advance("full", legacy="retired-and-inhibited")
        self.assertEqual(snapshot(self.paths.state), before)
        self.assertEqual(self.record().state, "partial")


class ReconcileTests(OwnerCase):
    def test_reconcile_reports_and_writes_and_cleans_nothing(self):
        owner = self.owner()
        crash_at(owner, "tmp-created")
        with self.assertRaises(Crash):
            owner.acquire()
        (self.paths.refused).mkdir()
        (self.paths.refused / "00000000000000000001-aa-bb.json").write_text("held work, not read")
        (self.paths.state / ".maintenance.json.0000000000cc.tmp").write_bytes(b"stray")
        before = snapshot(self.paths.state)
        before_mtimes = {p: os.stat(self.paths.state / p).st_mtime_ns for p in os.listdir(self.paths.state)}
        report = self.owner().reconcile()
        self.assertEqual(snapshot(self.paths.state), before, "no byte, file or directory changed")
        self.assertEqual({p: os.stat(self.paths.state / p).st_mtime_ns for p in os.listdir(self.paths.state)},
                         before_mtimes)
        self.assertEqual((report["record"], report["refused_files"]), (None, 1))
        self.assertEqual(report["pending"]["step"], "write-draining")
        self.assertIn("settle", report["next"])

    def test_reconcile_of_a_live_record(self):
        owner = self.owner(pid=7001)
        owner.acquire()
        owner.drain()
        self.quiet_period()
        report = owner.reconcile()
        f = report["fields"]
        self.assertEqual((report["record"]["state"], report["matches_journal"], f["boot"], f["legacy"],
                          f["binding"]["permits"]), ("partial", True, "boot-A", "unverified", []))
        self.assertIs(report["acting_process_gone"], False)
        self.assertEqual(report["next"], "advance to full or release")

    def test_the_acting_process_is_reported_gone_by_boot_pid_and_start_as_the_probes_decide(self):
        probes = ds.FakeProbes(boot="boot-A", pid=7001)
        owner = self.owner(probes=probes)
        owner.acquire()
        self.assertIs(owner.reconcile()["acting_process_gone"], False)
        probes.world["gone"].add(7001)
        self.assertIs(owner.reconcile()["acting_process_gone"], True)
        reborn = self.owner(probes=ds.FakeProbes(boot="boot-B", pid=7001))
        self.assertIs(reborn.reconcile()["acting_process_gone"], True, "another boot: gone")

    def test_reconcile_reports_a_record_that_is_not_the_journaled_write_and_an_unknown_one(self):
        owner = self.owner()
        owner.acquire()
        put_record(self.paths, record_fields(state="draining", generation=1, boot="boot-X"))
        self.assertIs(owner.reconcile()["matches_journal"], False)
        self.paths.maintenance.write_bytes(b"{")
        report = owner.reconcile()
        self.assertEqual(report["record"]["state"], "unknown")
        self.assertIn("owner decision", report["next"])
        self.assertEqual(self.paths.maintenance.read_bytes(), b"{")

    def test_reconcile_after_a_release(self):
        owner = self.whole()
        owner.release()
        report = owner.reconcile()
        self.assertEqual((report["record"], report["next"]), (None, "acquire"))


class OnlyTheOwnerWritesTests(unittest.TestCase):
    """C1: the primitives write the record for the fenced owning operation; ordinary writers only read it."""

    def test_no_ordinary_writer_names_the_owner_or_writes_the_record(self):
        for name in ("chatlib.py", "chat-bridge", "pchat", "agentcli.py", "identities.py", "runstore.py"):
            text = (SCRIPTS / name).read_text()
            with self.subTest(name):
                self.assertNotIn("Owner(", text)
                self.assertNotIn("ds.Owner", text)
                self.assertNotIn("delivery_store.Owner", text)
                for write in ("paths.maintenance, ", ".maintenance.write", "maintenance.json\", \"w", "unlink(paths.maint"):
                    self.assertNotIn(write, text)

    def test_the_store_writes_the_record_only_inside_owner(self):
        text = (SCRIPTS / "delivery_store.py").read_text()
        owner_start = text.index("class Owner:")
        owner_end = text.index("# --- the database (sections 2.1 and 2.2)")
        outside = text[:owner_start] + text[owner_end:]
        for line in outside.splitlines():
            if "paths.maintenance" in line or "self.paths.maintenance" in line:
                code = line.split("#")[0]
                self.assertNotRegex(code, r"\.(link|replace|rename|unlink|create_excl|write_bytes|write_text)\(.*maintenance",
                                    f"only Owner may write the record: {line.strip()}")
        self.assertIn("read_maintenance(self.paths)", text)


if __name__ == "__main__":
    unittest.main()
