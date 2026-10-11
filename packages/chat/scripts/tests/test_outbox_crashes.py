"""A crash at every boundary (docs/chat_outbox_state.md revision 8, section 9,
test 12): one case per row of section 5.5's three tables. A row whose case
lives in another module is listed with it, and a test below checks that each
of those exists; the rest are here. A crash is a BaseException no handler
catches, raised just before a transition commits (inside its transaction,
so it rolls back) or just after; then the crashed process is gone, and the
bridge recovers. Invented data only."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from outbox_fakes import CLIENT_PID, LONG, T0, Crash, OutboxCase  # noqa: E402

# Section 5.5's rows -> the tests (module.Class.method) that cover them, besides this module's own.
ELSEWHERE = {
    "Q0 TX landed, its reply lost": "test_outbox_resend.FailureBeforeE1Tests.test_a_q0_commit_whose_reply_is_lost_plus_its_fallback_file_is_one_entry",
    "Q0 fallback file: create, write, fsync": "test_outbox_files.PublicationTests.test_a_writer_stopped_before_its_link_is_never_imported_and_delays_nothing",
    "the file's link()": "test_outbox_files.PublicationTests.test_a_power_loss_after_the_link_before_the_directory_sync",
    "the fsync of outbox.d/ and the state directory": "test_outbox_files.ExportDurabilityTests.test_a_fallback_file_in_a_directory_another_process_made_survives_after_exit_3",
    "a fallback file in an outbox.d/ another process made": "test_outbox_files.ExportDurabilityTests.test_a_fallback_file_in_a_directory_another_process_made_survives_after_exit_3",
    "E1 landed, its reply lost": "test_outbox_resend.FailureBeforeE1Tests.test_an_e1_commit_whose_reply_is_lost_is_handed_off_not_duplicated",
    "A1 landed, its reply lost; P alive": "test_outbox_store.AttestationTests.test_a_void_from_writing_returns_the_part_to_unsent",
    "A1 landed, its reply lost; P closes; A8c first": "test_outbox_store.AttestationTests.test_a_lost_a1_reply_then_a_closed_connection_then_the_handoff_delivers_once",
    "an outcome landed, its reply lost": "test_outbox_authority.RequestCrashTests.test_every_op_replays_to_the_same_state",
    "A3 + E6": "test_outbox_resend.ReconciledAbsentTests.test_a_fail_is_a_refusal_dead_lettered_and_never_retried",
    "A11 (ack)": "test_outbox_resend.AcknowledgementTests.test_a_restart_between_the_failure_and_the_retry_changes_nothing",
    "a client sends part of a frame": "test_outbox_authority.PausedClientTests.test_half_a_frame_commits_nothing_and_closes_at_req_wait",
    "the reply is written": "test_outbox_authority.PausedClientTests.test_a_whole_frame_commits_and_its_unread_reply_is_recovered_by_replay",
    "a client after a restart": "test_outbox_authority.RequestCrashTests.test_a_restart_between_two_requests_of_one_call_makes_the_client_query",
    "a fallback file with an attestation, then the client dies": "test_outbox_resend.StorageFailureTests.test_with_the_handoff_unanswered_too_the_fallback_file_carries_the_outcome",
    "a late outcome on a terminal entry": "test_outbox_resend.LateOutcomeTests.test_a_late_outcome_exports_a_superseding_version",
    "E9": "test_outbox_bridge.AnnounceTests.test_a_crash_after_e1_and_before_a1_is_adopted_after_the_restart_and_sent_once",
    "IF: the durable sync, its TX, the unlink": "test_outbox_files.ImportBoundaryTests.test_crashes_around_each_step_neither_lose_nor_duplicate",
    "two fallback files for the same X": "test_outbox_files.PublicationTests.test_two_files_for_one_entry_are_one_obligation",
    "I1 rename, and its directory fsync": "test_outbox_files.DurableClaimTests.test_a_power_loss_after_the_rename_before_the_directory_sync",
    "a claim file left by an earlier pass or the old tools": "test_bridge.OutboxTests.test_failures_are_requeued_and_crashed_claims_resumed",
    "I2": "test_outbox_files.ImportBoundaryTests.test_a_legacy_claim_read_in_several_requests_keeps_its_occurrence_ids",
    "I2 reading a line with no newline yet": "test_outbox_files.PausedLockHolderTests.test_durable_obligations_behind_a_stopped_holder_flow_in_the_same_flush",
    "I3: the barrier acquisition": "test_outbox_files.ImportBoundaryTests.test_a_claim_is_never_unlinked_before_a_barrier_after_its_first_import",
    "I3 unlink, then directory fsync": "test_outbox_files.DurableClaimTests.test_a_power_loss_after_the_import_and_after_an_unsynced_unlink",
    "H1, in IF": "test_outbox_files.ImportBoundaryTests.test_a_handoff_published_before_an_e8_mid_pass_is_applied_and_nothing_collected_early",
    "import_epoch + 1": "test_outbox_files.ImportBoundaryTests.test_import_epoch_grows_only_when_a_whole_listing_is_imported",
    "V1": "test_outbox_bridge.CoverageTests.test_a_failed_index_stops_coverage_and_holds_the_checkpoint",
    "V2, V3; the checkpoint file write; a bridge restart": "test_outbox_bridge.CoverageTests.test_begin_sync_and_each_way_of_stopping",
    "announce's fallback file": "test_outbox_bridge.AnnounceTests.test_with_nothing_durable_the_flag_stays_unset_and_a_repeat_is_one_entry",
    "announce's E1 committed, A1 not": "test_outbox_bridge.AnnounceTests.test_a_crash_after_e1_and_before_a1_is_adopted_after_the_restart_and_sent_once",
    "Deliveries saves held_announced": "test_outbox_bridge.AnnounceTests.test_deliveries_saves_held_announced_only_after_the_announcement_is_durable",
    "X1 append": "test_outbox_files.ExportDurabilityTests.test_x1_a_power_loss_before_the_sync_loses_the_line_and_x1_writes_it_again",
    "X1 creates dead-letters.jsonl, then crashes before the directory fsync": "test_outbox_files.ExportDurabilityTests.test_a_record_left_with_an_unsynced_directory_entry_is_synced_before_its_mark",
    "Deliveries._dead append": "test_outbox_files.ExportDurabilityTests.test_a_delivery_dead_letter_waits_for_outbox_lock_and_stays_pending",
    "X2 steps; a ledger line never directory-synced": "test_outbox_store.AlertTests.test_an_expired_entry_raises_one_row_and_one_pending_item",
    "the TX that raises an alert row": "test_outbox_store.AlertTests.test_a_crash_after_the_pending_item_and_before_the_mark_still_gives_one",
    "a Deliveries save or ledger append": "test_outbox_files.ExportDurabilityTests.test_the_pending_queue_and_its_ledger_are_durable",
}


class CoverageMapTests(unittest.TestCase):
    def test_every_row_covered_elsewhere_names_a_test_that_exists(self):
        import importlib
        for row, name in ELSEWHERE.items():
            with self.subTest(row=row):
                module, cls, method = name.split(".")
                self.assertTrue(hasattr(getattr(importlib.import_module(module), cls), method), name)


class CrashCase(OutboxCase):
    def crash_in(self, method, *, after=False):
        """A crash in the authority's `method`: before its commit (inside the TX), or just after it."""
        real = getattr(self.ds.Store, method)

        def wrapped(store, *a, **kw):
            if not after:
                raise Crash()
            result = real(store, *a, **kw)
            wrapped.done = True
            return result
        wrapped.done = False
        if after:  # raise once the transaction has committed: in the authority's handle, after COMMIT
            real_handle = self.authority.handle

            def handle(request, **kw):
                reply = real_handle(request, **kw)
                if wrapped.done:
                    wrapped.done = False
                    raise Crash()
                return reply
            return [mock.patch.object(self.ds.Store, method, wrapped), mock.patch.object(self.authority, "handle",
                                                                                         handle)]
        return [mock.patch.object(self.ds.Store, method, wrapped)]

    def run_crashing(self, patches, fn):
        for p in patches:
            p.start()
        try:
            with self.assertRaises(Crash):
                fn()
        finally:
            for p in reversed(patches):
                p.stop()

    def gone(self, pid=CLIENT_PID):
        self.probes.world["gone"].add(pid)

    def published_once(self):
        texts = [m["text"] for m in self.server.published]
        self.assertEqual(len(texts), len(set(texts)), "nothing published twice")
        return texts


class WriterSideTests(CrashCase):
    """Section 5.5, writer side (P, or B as a flusher)."""

    def test_login_or_setup(self):
        self.server.refuse_login = {"alp-solver-2"}
        self.assertEqual(self.post("[status] x"), 3)  # P still running: Q0 on X
        self.assertEqual(self.server.published, [])
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(len(self.published_once()), 1)

    def test_q0_tx_before_and_after(self):
        self.server.refuse_login = {"alp-solver-2"}
        self.run_crashing(self.crash_in("queue"), lambda: self.pchat("post", "#alpha", "[status] before"))
        self.assertEqual(self.entries(), [], "before: nothing durable, as today")
        self.run_crashing(self.crash_in("queue", after=True), lambda: self.pchat("post", "#alpha", "[status] after"))
        self.assertEqual([e["owner_kind"] for e in self.entries()], ["outbox"])
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(len(self.published_once()), 1)

    def test_e1_before_and_after(self):
        self.run_crashing(self.crash_in("create"), lambda: self.pchat("post", "#alpha", "[status] before"))
        self.assertEqual(self.entries(), [])
        self.run_crashing(self.crash_in("create", after=True), lambda: self.pchat("post", "#alpha", "[status] after"))
        [e] = self.entries()
        self.assertEqual((e["owner_kind"], self.states(e)), ("direct", ["unsent"]))
        self.gone()
        self.flush()
        self.assertEqual(self.entry()["state"], "abandoned", "the writer is gone: E8, nothing unknown")
        self.assertEqual(self.server.published, [])

    def test_a1_before_and_after(self):
        self.run_crashing(self.crash_in("attempt"), lambda: self.pchat("post", "#alpha", "[status] before"))
        self.assertEqual(self.attempts(), [])
        self.gone()
        self.flush()
        self.assertEqual(self.server.published, [])
        self.fresh()
        self.run_crashing(self.crash_in("attempt", after=True), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.assertEqual([a["state"] for a in self.attempts()], ["writing"])
        self.gone()
        self.flush()
        self.assertEqual([(a["state"], a["final_at"]) for a in self.attempts()], [("ended", None)])
        self.assertEqual(self.states(), ["uncertain"], "no finality: decided only by R1 or E7")

    def test_sending_waiting_and_closing_leave_it_writing_until_a8(self):
        for plan in (["crash"], ["late-crash"]):
            with self.subTest(plan=plan):
                self.fresh()
                self.server.plan = list(plan)
                with self.assertRaises(Crash):
                    self.pchat("post", "#alpha", "[status] mid-write")
                self.assertEqual([a["state"] for a in self.attempts()], ["writing"])
                self.gone()
                self.flush()
                self.assertEqual(self.states(), ["uncertain"])
                self.assertEqual(len(self.server.published), 1, "never resent blindly")

    def test_a2_before_and_after(self):
        self.run_crashing(self.crash_in("outcome"), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.gone()
        self.flush()
        self.assertEqual(self.states(), ["uncertain"], "before A2: A8, uncertain, never unsent")
        self.fresh()
        self.run_crashing(self.crash_in("outcome", after=True), lambda: self.pchat("post", "#alpha", "[status] y"))
        self.assertEqual(self.states(), ["confirmed"])

    def test_a4_before_and_after(self):
        self.server.plan = ["403"]
        self.run_crashing(self.crash_in("outcome"), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.gone()
        self.flush()
        self.assertEqual(self.states(), ["uncertain"], "before A4: UNKNOWN, then E7")
        self.fresh()
        self.server.plan = ["403"]
        self.run_crashing(self.crash_in("outcome", after=True), lambda: self.pchat("post", "#alpha", "[status] y"))
        self.assertEqual(self.states(), ["unsent"])
        self.gone()
        self.flush()
        self.assertEqual(self.entry()["state"], "abandoned", "unsent, and the owner is gone: E8")

    def test_a5_before_and_after(self):
        self.server.plan = ["timeout"]
        self.run_crashing(self.crash_in("outcome"), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.gone()
        self.flush()
        self.assertEqual([(a["state"], a["end_kind"]) for a in self.attempts()], [("ended", "writer_gone")])
        self.fresh()
        self.server.plan = ["error"]
        self.run_crashing(self.crash_in("outcome", after=True), lambda: self.pchat("post", "#alpha", "[status] y"))
        [a] = self.attempts()
        self.assertEqual((a["state"], a["end_kind"]), ("ended", "closed"))
        self.assertIsNotNone(a["final_at"], "with the finality observed")

    def test_e3_before_and_after(self):
        self.server.plan = ["ok", "timeout"]
        self.run_crashing(self.crash_in("handoff"), lambda: self.pchat("post", "#alpha", LONG))
        self.assertEqual(self.entry()["owner_kind"], "direct")
        self.gone()
        self.flush()
        self.assertEqual(self.entry()["state"], "abandoned", "no durable handoff: E8; the remainder is not adopted")
        self.fresh()
        self.server.plan = ["ok", "timeout"]
        self.run_crashing(self.crash_in("handoff", after=True), lambda: self.pchat("post", "#alpha", LONG))
        self.assertEqual(self.entry()["owner_kind"], "outbox")

    def test_the_handoffs_fallback_file_once_published_is_applied_by_h1(self):
        self.server.plan = ["ok", "timeout"]
        with mock.patch.object(self.authority, "handle",
                               lambda r, real=self.authority.handle, **kw: None if r["op"] == "handoff"
                               else real(r, **kw)):
            self.assertEqual(self.pchat("post", "#alpha", LONG)[0], 3)
        self.gone()
        self.flush()
        e = self.entry()
        self.assertEqual((e["state"], e["owner_kind"]), ("open", "outbox"))

    def test_e5_before_and_after(self):
        self.run_crashing(self.crash_in("done"), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.assertEqual(self.entry()["state"], "open")
        self.gone()
        self.flush()
        self.assertEqual(self.entry()["state"], "abandoned", "all parts confirmed: nothing more is owed or sent")
        self.assertEqual(len(self.server.published), 1)
        self.fresh()
        self.run_crashing(self.crash_in("done", after=True), lambda: self.pchat("post", "#alpha", "[status] y"))
        self.assertEqual(self.entry()["state"], "done")


class AuthoritySideTests(CrashCase):
    """Section 5.5, requests and the authority."""

    def test_the_authority_crashing_mid_transaction_rolls_it_back(self):
        self.run_crashing(self.crash_in("create"), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.restart_bridge()
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.post("[status] again"), 0, "the next epoch answers")

    def test_the_start_transaction(self):
        epoch = self.authority.epoch
        self.authority.close()
        with mock.patch.object(self.ds.Store, "set_meta", side_effect=Crash()), self.assertRaises(Crash):
            self.new_authority()
        self.authority = self.new_authority()
        self.assertEqual(self.authority.epoch, epoch + 1, "the crashed start changed no epoch")


class BridgeSideTests(CrashCase):
    """Section 5.5, bridge side."""

    def queued(self, text="[status] queued"):
        self.server.refuse_login = {"alp-solver-2"}
        self.post(text)
        self.server.refuse_login = set()

    def test_e4_before_and_after(self):
        self.queued()
        with mock.patch.object(self.ds.Store, "fix_parts", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()
        self.assertEqual(self.states(), [])
        self.flush()
        self.assertEqual(self.states(), ["confirmed"])
        self.assertEqual(len(self.published_once()), 1)

    def test_a8_before_and_after(self):
        self.server.plan = ["crash"]
        with self.assertRaises(Crash):
            self.pchat("post", "#alpha", "[status] x")
        self.gone()
        with mock.patch.object(self.ds.Store, "end_attempts", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()
        self.assertEqual([a["state"] for a in self.attempts()], ["writing"])
        self.flush()
        self.assertEqual([a["state"] for a in self.attempts()], ["ended"])

    def test_r1_r2_before_and_after(self):
        self.server.plan = ["error"]
        self.post("[status] x")
        self.server.published.clear()
        self.cover(T0 - 100, T0 + 10 ** 6)
        with mock.patch.object(self.ds.Store, "_r2", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()
        self.assertEqual(self.states(), ["uncertain"], "unchanged: decided again")
        self.flush()
        self.assertEqual(self.entry()["state"], "done")
        self.assertEqual(len(self.published_once()), 1)

    def test_e7_before_and_after(self):
        self.server.plan = ["timeout"]
        self.post("[status] x")
        self.clock.advance(self.ds.UNDECIDED_MAX + 1)
        with mock.patch.object(self.ds.Store, "expire", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()
        self.assertEqual(self.entry()["state"], "open")
        self.flush()
        self.assertEqual(self.entry()["state"], "terminal")
        self.assertEqual(len(self.dead_letters()), 1)

    def test_e8_before_and_after(self):
        self.run_crashing(self.crash_in("create", after=True), lambda: self.pchat("post", "#alpha", "[status] x"))
        self.gone()
        with mock.patch.object(self.ds.Store, "abandon_or_adopt", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()
        self.assertEqual(self.entry()["state"], "open")
        self.flush()
        e = self.entry()
        self.assertEqual(e["state"], "abandoned")
        self.assertIsNotNone(e["abandon_epoch"])

    def test_x1_mark(self):
        self.server.plan = ["fail"]
        self.post("[status] x")
        real = self.ds.Store.entry_view
        with mock.patch.object(self.bridge.Flusher, "export_alerts", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()  # the record durable, the mark set; then the crash
        self.flush()
        self.assertEqual(len(self.dead_letters()), 1, "found present, never appended twice")
        del real

    def test_g1_before_and_after(self):
        self.server.plan = ["fail"]
        self.post("[status] x")
        self.flush()
        self.clock.advance(10 * 86400)
        with mock.patch.object(self.ds.Store, "collect", side_effect=Crash()), self.assertRaises(Crash):
            self.flush()
        self.assertEqual(len(self.attempts()), 1)
        self.flush()
        self.assertEqual(self.attempts(), [])
        self.assertEqual(len(self.entries()), 1)


if __name__ == "__main__":
    unittest.main()
