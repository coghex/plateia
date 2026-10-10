"""#19's acceptance 1-10, as amended, and design revision 8's posting
scenarios (section 9: tests 1, 2, 4, 6, 10, 11, 13 and 18): a long post whose
completion times out after the server committed it is never posted again
whole. Each test drives pchat post, pchat ack, agent notices and the bridge's
flush against a fake server and an in-process outbox authority, under
_isolation, with an injected clock. All names, channels, times and text are
invented."""
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from outbox_fakes import CFG, LONG, T0, OTHER_PID, Crash, OutboxCase  # noqa: E402
import test_bridge  # noqa: E402,F401  (puts the scripts on the path, under the sandbox)

import agentcli  # noqa: E402


class CommittedThenTimeoutTests(OutboxCase):
    """Acceptance 1 and 4: confirmed, confirmed, then committed and never confirmed."""

    def test_pchat_queues_only_the_unconfirmed_part_and_never_writes_it_again(self):
        self.server.plan = ["ok", "ok", "timeout"]
        code, said = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 3, said)
        self.assertEqual(len(self.server.published), 3)
        e = self.entry()
        self.assertEqual(self.states(e), ["confirmed", "confirmed", "uncertain"])
        self.assertEqual(e["owner_kind"], "outbox")  # the rest handed off (E3)
        self.assertEqual(e["text"], LONG)
        for _ in range(3):
            self.clock.advance(60)
            self.flush()
        self.assertEqual(len(self.server.published), 3, "no part is written again while its delivery is undecided")
        status = self.tx(lambda s: s.status())
        self.assertEqual(status["checking"], 1)  # visible as awaiting a delivery check

    def test_the_line_transport_behaves_the_same(self):
        self.server.multiline = False
        self.server.stall_after = 3
        code, said = self.pchat("post", "#alpha", "[status] one\ntwo\nthree\nfour")
        self.assertEqual(code, 3, said)
        self.assertEqual(self.states(), ["confirmed", "confirmed", "uncertain", "unsent"])
        for _ in range(3):
            self.flush()
        self.assertEqual(len(self.server.published), 3)

    def test_an_uncertain_part_never_proves_absence_by_a_timeout(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        self.cover(T0 - 3600, T0 + 10 * 86400)  # the record is complete, and holds no part 3
        self.clock.advance(3600)
        self.flush()
        self.assertEqual(len(self.server.published), 3, "missing finality never proves absence (N7)")


class ReconciledDeliveredTests(OutboxCase):
    """Acceptance 2: the record holds part 3 from that verified, designated account."""

    def test_a_designated_senders_logged_part_is_retired_without_writing(self):
        self.designate("alp-solver-2", T0 - 60)
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        part3 = self.server.published[2]
        self.see(part3)
        self.clock.advance(30)
        self.flush()
        e = self.entry()
        self.assertEqual(e["state"], "done")
        self.assertEqual(e["parts"][2]["msgid"], part3["msgid"])
        self.assertEqual(len(self.server.published), 3)

    def test_an_undesignated_account_stays_unknown_whatever_the_record_shows(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        self.see(self.server.published[2])
        self.flush()
        self.assertEqual(self.states(), ["confirmed", "confirmed", "uncertain"])


class ReconciledAbsentTests(OutboxCase):
    """Acceptance 3, as amended (AD-2): a non-refusal error reply with its PONG is
    finality; complete coverage and no matching message: written exactly once more."""

    def test_an_error_reply_with_finality_and_a_complete_record_resends_once(self):
        self.server.plan = ["ok", "ok", "error"]
        code, _ = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 3)
        part3 = self.server.published.pop()  # the server published nothing of part 3 after all
        self.assertEqual(self.states(), ["confirmed", "confirmed", "uncertain"])
        self.cover(T0 - 3600, T0 + 120)
        self.clock.advance(60)
        self.flush()
        self.assertEqual(self.entry()["state"], "done")
        self.assertEqual(len(self.server.published), 3)
        self.assertEqual(self.server.published[2]["text"], part3["text"], "the same part, unchanged")
        self.flush()
        self.assertEqual(len(self.server.published), 3, "parts 1 and 2 are never written again")

    def test_a_late_pong_without_an_error_is_confirmation(self):
        self.server.plan = ["ok", "ok", "late"]
        code, said = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.states(), ["confirmed"] * 3)
        self.flush()
        self.assertEqual(len(self.server.published), 3)

    def test_a_fail_is_a_refusal_dead_lettered_and_never_retried(self):
        self.server.plan = ["ok", "fail"]
        code, said = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 2, said)
        self.assertIn("1 of 3 parts were already posted", said)
        e = self.entry()
        self.assertEqual((e["state"], self.states(e)), ("terminal", ["confirmed", "refused", "unsent"]))
        self.flush()
        [dead] = self.dead_letters()
        self.assertEqual([p["state"] for p in dead["parts"]], ["confirmed", "refused", "unsent"])
        self.assertEqual(len(self.server.published), 2)

    def test_a_crash_or_broken_connection_never_proves_absence(self):
        for plan in (["ok", "crash"], ["ok", "timeout"]):
            with self.subTest(plan=plan):
                self.fresh()
                self.server.plan = list(plan)
                try:
                    self.pchat("post", "#alpha", LONG)
                except Crash:
                    pass
                self.cover(T0 - 3600, T0 + 86400)
                self.server.published[1:] = []
                self.clock.advance(3600)
                self.probes.world["gone"].add(4242)  # the crashed writer is gone: A8
                self.flush()
                self.assertEqual(len(self.server.published), 1)


class IndependentDeliveryTests(OutboxCase):
    """Acceptance 8: another account's entry for #beta is delivered in the same flush."""

    def test_another_accounts_entry_flows_while_one_awaits_its_check(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        self.server.refuse_login = {"bet-solver-1"}
        code, _ = self.pchat("post", "#beta", "[status] beta green", account="bet-solver-1")
        self.assertEqual(code, 3)
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual([m["text"].split(" (delayed")[0] for m in self.server.by("bet-solver-1")],
                         ["[status] beta green"])
        [first] = [e for e in self.entries() if e["account"] == "alp-solver-2"]
        self.assertEqual(self.states(first), ["confirmed", "confirmed", "uncertain"])

    def test_a_refused_and_a_failing_entry_never_stop_the_others(self):
        self.server.refuse_login = {"alp-solver-2", "bet-solver-1"}
        self.post("[status] first", account="alp-solver-2")
        self.post("[status] second", channel="#beta", account="bet-solver-1")
        self.server.refuse_login = {"alp-solver-2"}  # still failing at the flush
        self.flush()
        self.assertEqual(len(self.server.by("bet-solver-1")), 1)


class RestartTests(OutboxCase):
    """Acceptance 6 and 7: a fresh bridge, or one killed between parts, continues
    from the recorded progress; confirmed parts are never rewritten."""

    def test_a_restarted_bridge_continues_from_the_recorded_progress(self):
        self.server.plan = ["ok", "ok", "error"]
        self.pchat("post", "#alpha", LONG)
        self.server.published.pop()
        self.restart_bridge()
        self.cover(T0 - 3600, T0 + 600)
        self.clock.advance(60)
        self.flush()
        self.assertEqual(self.entry()["state"], "done")
        self.assertEqual(len(self.server.published), 3)

    def test_a_flush_killed_after_writing_a_part_recovers_from_the_database(self):
        self.server.refuse_login = {"alp-solver-2"}
        self.post(LONG)
        self.server.refuse_login = set()
        self.server.plan = ["ok", "crash"]  # part 2 is written; the bridge dies before recording it
        with self.assertRaises(Crash):
            self.flush()
        self.assertEqual(len(self.server.published), 2)
        self.restart_bridge()  # the old bridge process is gone: its attempt ends without finality (A8)
        for _ in range(2):
            self.flush()
        self.assertEqual(self.states(), ["confirmed", "uncertain", "unsent"])
        self.assertEqual(len(self.server.published), 2, "part 1 is never rewritten, part 2 is never resent blindly")


class UnknownForADayTests(OutboxCase):
    """Acceptance 10: an uncertain part with no evidence either way, on an injected clock."""

    def test_after_24_hours_it_is_dead_lettered_with_its_progress_and_one_alert(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        deliveries = self.bridge.Deliveries()
        self.clock.advance(self.ds.UNDECIDED_MAX - 60)
        self.flush(deliveries)
        self.assertEqual(self.entry()["state"], "open")
        self.clock.advance(120)
        self.flush(deliveries)
        self.flush(deliveries)
        e = self.entry()
        self.assertEqual(e["state"], "terminal")
        [dead] = self.dead_letters()
        self.assertEqual([p["state"] for p in dead["parts"]], ["confirmed", "confirmed", "uncertain"])
        self.assertEqual([p["text"] for p in dead["parts"]], [m["text"] for m in self.server.published])
        pushes = [i for i in deliveries.items if i.get("alert_key") == f"outbox:{e['id']}"]
        self.assertEqual(len(pushes), 1)
        self.assertNotIn("lorem", pushes[0]["text"], "the alert is content-free")
        self.assertEqual(len(self.server.published), 3, "never resent")


class LegacyFlushTests(OutboxCase):
    """Acceptance 5 and 9: the bridge's own flush of a legacy text-only row."""

    def write_legacy(self, *rows):
        import json
        path = self.chatlib.STATE_DIR / "outbox.jsonl"
        with path.open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def test_a_legacy_post_whose_final_confirmation_times_out_is_not_reposted(self):
        self.write_legacy({"channel": "#alpha", "as": "alp-solver-2", "text": LONG, "at": "2026-10-09T00:00:00"})
        self.server.stall_after = 3
        for _ in range(4):
            self.clock.advance(60)
            self.flush()
        self.assertEqual(len(self.server.published), 3)
        self.assertTrue(self.server.published[2]["text"].endswith("(delayed; written 2026-10-09T00:00:00Z)"))

    def test_legacy_posts_and_acks_go_out_as_before(self):
        self.write_legacy({"channel": "#alpha", "as": "alp-solver-2", "text": "[status] hi", "cont": "[status]",
                           "reply_to": "m1", "at": "t"},
                          {"channel": "#alpha", "as": "alp-manager", "ack": "m9", "at": "t"})
        self.flush()
        [msg] = [m for m in self.server.published if m["text"]]
        self.assertTrue(msg["text"].startswith("[status] hi (delayed; written t"))
        self.assertIn("+draft/reply=m1", msg["tags"])
        acks = [m for m in self.server.published if m.get("tagmsg")]
        self.assertEqual(len(acks), 1)
        self.assertIn("+draft/reply=m9", acks[0]["tagmsg"])
        self.assertTrue(all(e["state"] == "done" for e in self.entries()))


class UnchangedPathTests(OutboxCase):
    """Test 18: a slow but confirmed post, exit codes and messages."""

    def test_a_confirmed_post_exits_0_and_queues_nothing(self):
        code, said = self.pchat("post", "#alpha", "[status] all good")
        self.assertEqual((code, said.strip()), (0, "posted 1 line(s) to #alpha as alp-solver-2"))
        self.assertEqual(self.entry()["state"], "done")
        self.assertEqual(self.fallback_files(), [])

    def test_a_server_that_cannot_be_reached_queues_the_whole_post_once(self):
        self.server.refuse_login = {"alp-solver-2"}
        code, said = self.pchat("post", "#alpha", "[status] later")
        self.assertEqual(code, 3, said)
        self.assertIn("queued", said)
        self.server.refuse_login = set()
        self.flush()
        self.flush()
        self.assertEqual([m["text"].split(" (delayed")[0] for m in self.server.published], ["[status] later"])

    def test_ack_exits_0_and_is_one_tagmsg(self):
        code, said = self.pchat("ack", "#alpha", "m1", account="alp-manager")
        self.assertEqual(code, 0, said)
        self.assertEqual(len([m for m in self.server.published if m.get("tagmsg")]), 1)


class AcknowledgementTests(OutboxCase):
    """Test 6 (F2): a failed ack, A11 and a retry that succeeds."""

    def test_a_timed_out_ack_is_retired_and_retried_by_the_bridge(self):
        self.server.plan = ["timeout"]
        code, _ = self.pchat("ack", "#alpha", "m1", account="alp-manager")
        self.assertEqual(code, 3)
        a = self.attempts()[0]
        self.assertEqual((a["state"], a["is_ack"]), ("ended", 1))
        self.flush()
        self.assertEqual([x["state"] for x in self.attempts()], ["retired", "confirmed"])
        self.assertEqual(self.entry()["state"], "done")
        self.assertEqual(len([m for m in self.server.published if m.get("tagmsg")]), 2, "a repeated ack is harmless")

    def test_a_restart_between_the_failure_and_the_retry_changes_nothing(self):
        self.server.plan = ["timeout"]
        self.pchat("ack", "#alpha", "m1", account="alp-manager")
        self.restart_bridge()
        self.flush()
        self.assertEqual(self.entry()["state"], "done")


class StorageFailureTests(OutboxCase):
    """Test 1.2: A2 fails after part 1 of 3, through pchat, an agent notice and
    the bridge's announcement. Nothing is requeued whole, and part 1 is never rewritten."""

    def fail_outcomes(self, times, ops=("outcome",)):
        real, left = self.authority.handle, [times]

        def handle(request, **kw):
            if request.get("op") in ops and left[0]:
                left[0] -= 1
                return {"v": self.ds.PROTOCOL, "request_id": request.get("request_id"), "result": "error",
                        "authority_epoch": self.authority.epoch}
            return real(request, **kw)
        return mock.patch.object(self.authority, "handle", handle)

    def test_pchat_retries_then_hands_off_the_rest(self):
        with self.fail_outcomes(1):
            code, said = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 3, said)
        self.assertEqual(self.states(), ["confirmed", "unsent", "unsent"])
        self.flush()
        self.assertEqual(len(self.server.published), 3)
        self.assertEqual(len({m["text"] for m in self.server.published}), 3)

    def test_an_outcome_never_recorded_travels_as_an_attested_outcome(self):
        with self.fail_outcomes(1000):
            code, said = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 3, said)
        self.assertEqual(self.fallback_files(), [], "the handoff was acknowledged: no file")
        self.assertEqual(self.states(), ["confirmed", "unsent", "unsent"], "the attested A2 applied in E3's TX")
        self.flush()
        self.assertEqual(len(self.server.published), 3)

    def test_with_the_handoff_unanswered_too_the_fallback_file_carries_the_outcome(self):
        with self.fail_outcomes(1000, ops=("outcome", "handoff")):
            code, said = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 3, said)
        self.assertEqual(len(self.fallback_files()), 1)
        self.flush()
        self.assertEqual(self.states()[0], "confirmed", "the attested A2 applied before A8")
        self.assertEqual(len(self.server.published), 3)

    def test_an_agent_notice_does_the_same(self):
        with self.fail_outcomes(1), contextlib.redirect_stderr(io.StringIO()):
            agentcli.notify({"channel": "#alpha", "request": "r-1", "name": "alp-solver-2"}, LONG)
        self.flush()
        self.assertEqual(len({m["text"] for m in self.server.published}), len(self.server.published))
        self.assertEqual(self.entry()["origin"], "notify")


class AnnounceStorageFailureTests(StorageFailureTests):
    def test_the_bridges_announcement_does_the_same(self):
        item = {"kind": "ring", "msgid": "m-x", "channel": "#alpha", "target": "alp-solver-2"}
        with self.fail_outcomes(1), \
                mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority), \
                mock.patch.object(self.ds, "PROBES", self.probes):
            self.assertTrue(self.bridge.announce(CFG, item, "x\n" + "y " * 3000, "held"))
        self.flush()
        texts = [m["text"] for m in self.server.published]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(self.entry()["state"], "done")


class StatusTests(OutboxCase):
    """Test 18 and R8: pchat status, from the authority or, while it is unavailable, its snapshot."""

    def test_status_counts_waiting_checking_and_files_awaiting_import(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        self.server.refuse_login = {"sam"}
        with self.unavailable():
            self.post("[status] later", account="sam")
        from test_outbox_regressions import pchat
        text = "\n".join(pchat.outbox_status())
        self.assertIn("1 awaiting a delivery check", text)
        self.assertIn("1 fallback files", text)
        self.flush()
        with self.unavailable():
            text = "\n".join(pchat.outbox_status())
        self.assertIn("UNAVAILABLE", text)
        self.assertIn("snapshot", text)


class FailureBeforeE1Tests(OutboxCase):
    """Test 11 (F7) and the ambiguous creation commits: each queues the whole post once."""

    def test_a_failed_login_queues_by_q0_with_no_fallback_file(self):
        self.server.refuse_login = {"alp-solver-2"}
        self.assertEqual(self.post("[status] x"), 3)
        self.assertEqual(self.fallback_files(), [], "an acknowledged Q0 needs no file")
        e = self.entry()
        self.assertEqual((e["owner_kind"], e["parts"]), ("outbox", []))

    def test_with_the_authority_unavailable_the_fallback_file_carries_it(self):
        self.server.refuse_login = {"alp-solver-2"}
        with self.unavailable():
            self.assertEqual(self.post("[status] x"), 3)
        self.assertEqual(len(self.fallback_files()), 1)
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(len(self.server.published), 1)
        self.assertEqual(self.fallback_files(), [])

    def test_an_e1_commit_whose_reply_is_lost_is_handed_off_not_duplicated(self):
        real = self.authority.handle

        def handle(request, **kw):
            reply = real(request, **kw)
            if request.get("op") == "create":
                return None  # landed, but the reply is lost
            return reply
        with mock.patch.object(self.authority, "handle", handle):
            self.assertEqual(self.post("[status] once"), 3)
        [e] = self.entries()
        self.assertEqual(e["owner_kind"], "outbox")
        self.flush()
        self.assertEqual(len(self.server.published), 1)

    def test_a_q0_commit_whose_reply_is_lost_plus_its_fallback_file_is_one_entry(self):
        self.server.refuse_login = {"alp-solver-2"}
        real = self.authority.handle

        def handle(request, **kw):
            reply = real(request, **kw)
            return None if request.get("op") == "queue" else reply
        with mock.patch.object(self.authority, "handle", handle):
            self.assertEqual(self.post("[status] once"), 3)
        self.assertEqual(len(self.fallback_files()), 1)
        self.server.refuse_login = set()
        for _ in range(2):
            self.flush()
            self.restart_bridge()
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(len(self.server.published), 1)

    def test_two_calls_with_identical_text_are_two_entries(self):
        self.server.refuse_login = {"alp-solver-2"}
        self.post("[status] same")
        self.post("[status] same")
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(len(self.server.published), 2)


class PausedWriterTests(OutboxCase):
    """Test 13: a paused writer is never ended or retried; gone-ness is proved
    only by a missing pid, a changed start time or a changed boot id."""

    def paused_attempt(self):
        self.server.plan = ["ok", "crash"]  # stands in for the writer stopping after its A1, mid-part
        with contextlib.suppress(Crash):
            self.pchat("post", "#alpha", LONG)
        self.assertEqual(self.attempts(state="writing")[0]["n"], 1)

    def test_a_paused_writer_is_never_ended_or_retried(self):
        self.paused_attempt()
        for _ in range(3):
            self.clock.advance(600)
            self.flush()
        self.assertEqual(len(self.attempts(state="writing")), 1)
        self.assertEqual(len(self.server.published), 2)

    def test_each_proof_of_a_gone_writer_ends_it_without_finality(self):
        for how in ("pid", "start", "boot"):
            with self.subTest(how=how):
                self.fresh()
                self.paused_attempt()
                [a] = self.attempts(state="writing")
                if how == "pid":
                    self.probes.world["gone"].add(4242)
                elif how == "start":
                    self.probes.world["starts"][4242] = "start-2"
                else:
                    self.tx(lambda s: s.con.execute("UPDATE attempts SET writer = replace(writer, 'test-boot',"
                                                    " 'old-boot')"))
                self.flush()
                [b] = self.attempts(id=a["id"])
                self.assertEqual((b["state"], b["end_kind"], b["final_at"]), ("ended", "writer_gone", None))

    def test_missing_recorded_identity_proves_nothing(self):
        """Code review slot 2, finding 1: a boot id or start time the writer could
        not record is unknown, never different; its live attempt is not ended, its
        entry is not abandoned, and its own outcome still applies."""
        for how in ("start missing", "boot unknown", "both"):
            with self.subTest(how=how):
                self.fresh()
                self.paused_attempt()
                [a] = self.attempts(state="writing")
                w = json.loads(a["writer"])
                if how != "boot unknown":
                    w["start"] = None
                if how != "start missing":
                    w["boot"] = "unknown"
                self.tx(lambda s: s.con.execute("UPDATE attempts SET writer = ?", (self.ds.jdump(w),)))
                self.tx(lambda s: s.con.execute("UPDATE entries SET owner_writer = ?", (self.ds.jdump(w),)))
                for _ in range(2):
                    self.clock.advance(600)
                    self.flush()
                [b] = self.attempts(id=a["id"])
                self.assertEqual(b["state"], "writing", "a live writer is never ended on missing evidence")
                e = self.entry()
                self.assertEqual((e["state"], e["owner_kind"]), ("open", "direct"), "nor its entry abandoned")
                self.assertEqual(self.tx(lambda s: s.outcome(e["id"], b["n"], b["generation"], b["a1_nonce"], w,
                                                             "confirmed", T0 + 5)), "ok")

    def test_unreadable_probes_prove_nothing(self):
        self.paused_attempt()
        self.probes.world["unreadable"] = True
        self.flush()
        self.assertEqual(len(self.attempts(state="writing")), 1)

    def test_e7_makes_it_terminal_after_24_hours_but_leaves_it_writing(self):
        self.paused_attempt()
        self.clock.advance(self.ds.UNDECIDED_MAX + 60)
        self.flush()
        self.assertEqual(self.entry()["state"], "terminal")
        self.assertEqual(len(self.attempts(state="writing")), 1)


class HostProbeTests(unittest.TestCase):
    """Code review slot 2, finding 1, on the host probes (self-audit, classes A
    and C): only a boot id or start time known on both sides is compared, each
    read from one source per platform; nothing unreadable or malformed proves a
    writer gone or its connection closed, and nothing raises. The host
    interfaces are faked: no process table, kernel or socket table is read."""

    BOOT, START = "boot-a", "start-a"

    def probes(self, boot=BOOT, start=START):
        import delivery_store as ds
        for p in (mock.patch.object(ds, "_boot_id", lambda: boot),
                  mock.patch.object(ds, "_process_start", lambda pid: start),
                  mock.patch.object(ds, "_tcp_ports", lambda pid: {1})):
            p.start()
            self.addCleanup(p.stop)
        return ds.HostProbes()

    def test_only_values_known_on_both_sides_are_compared(self):
        me = os.getpid()
        for host_boot, writer, want in (
                (self.BOOT, {"boot": "unknown", "pid": me, "start": self.START}, False),
                (self.BOOT, {"boot": None, "pid": me, "start": self.START}, False),
                (self.BOOT, {"pid": me, "start": self.START}, False),
                (self.BOOT, {"boot": self.BOOT, "pid": me, "start": None}, None),
                (self.BOOT, {"boot": self.BOOT, "pid": me}, None),
                (self.BOOT, {"boot": "unknown", "pid": me, "start": None}, None),
                (None, {"boot": "boot-b", "pid": me, "start": self.START}, False),
                (self.BOOT, {"boot": "boot-b", "pid": me, "start": self.START}, True),
                (self.BOOT, {"boot": self.BOOT, "pid": me, "start": "start-b"}, True)):
            with self.subTest(host_boot=host_boot, writer=writer):
                self.assertIs(self.probes(boot=host_boot).gone(writer), want)

    def test_malformed_identities_prove_nothing_and_raise_nothing(self):
        p = self.probes()
        for pid in (2 ** 40, 0, -1, True, "12", None, 1.5):
            with self.subTest(pid=pid):
                self.assertIsNone(p.gone({"boot": self.BOOT, "pid": pid, "start": self.START}))
        me = {"boot": self.BOOT, "pid": os.getpid(), "start": self.START}
        for conn in (5, None, "", "garbage", ["x"], "127.0.0.1:99999999999999999999>s"):
            with self.subTest(conn=conn):
                self.assertIsNone(p.conn_closed(dict(me, conn=conn)))
        self.assertIs(p.conn_closed(dict(me, conn="127.0.0.1:2>s")), True)
        self.assertIs(p.conn_closed(dict(me, conn="127.0.0.1:1>s")), False)

    def test_a_socket_table_that_cannot_be_read_proves_nothing(self):
        import delivery_store as ds
        for rc, err in ((1, "lsof: WARNING: can't stat() a file system\n"), (2, "")):
            ran = lambda *a, rc=rc, err=err, **k: subprocess.CompletedProcess(a, rc, "", err)  # noqa: E731
            with self.subTest(rc=rc), mock.patch.object(ds, "HAS_PROC", False, create=True), \
                    mock.patch.object(ds.subprocess, "run", ran):
                self.assertIsNone(ds._tcp_ports(4194305))
        none = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "")  # noqa: E731
        with mock.patch.object(ds, "HAS_PROC", False, create=True), mock.patch.object(ds.subprocess, "run", none):
            self.assertEqual(ds._tcp_ports(4194305), set(), "no TCP socket at all: each of its connections closed")

    def test_a_start_time_is_read_from_one_source_per_platform(self):
        import delivery_store as ds
        ps = lambda *a, **k: subprocess.CompletedProcess(a, 0, "Sat Oct 10 10:00:00 2026\n", "")  # noqa: E731
        with mock.patch.object(ds, "HAS_PROC", True, create=True), mock.patch.object(ds.subprocess, "run", ps):
            self.assertIsNone(ds._process_start(4194305), "where /proc is the source, an unreadable entry is unknown")
        with mock.patch.object(ds, "HAS_PROC", False, create=True), mock.patch.object(ds.subprocess, "run", ps):
            self.assertEqual(ds._process_start(4194305), "Sat Oct 10 10:00:00 2026")


class LateOutcomeTests(OutboxCase):
    """Test 10 (F6): a paused writer resumes after E7's export: version 2 supersedes version 1."""

    def test_a_late_outcome_exports_a_superseding_version(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        e = self.entry()
        [a] = self.attempts(n=2)
        self.tx(lambda s: s.con.execute("UPDATE attempts SET state = 'writing', end_kind = NULL, ended_at = NULL"
                                        " WHERE id = ?", (a["id"],)))  # as if its A5 had not landed: paused
        self.tx(lambda s: s.con.execute("UPDATE parts SET state = 'inflight' WHERE entry_id = ? AND n = 2",
                                        (e["id"],)))
        self.clock.advance(self.ds.UNDECIDED_MAX + 60)
        self.flush()
        self.assertEqual([d["version"] for d in self.dead_letters()], [1])
        writer = __import__("json").loads(a["writer"])
        self.tx(lambda s: s.outcome(e["id"], 2, a["generation"], a["a1_nonce"], writer, "confirmed", T0 + 5))
        self.flush()
        versions = [(d["version"], d["supersedes"]) for d in self.dead_letters()]
        self.assertEqual(versions, [(1, None), (2, 1)])
        self.assertEqual(self.entry()["state"], "terminal", "it never becomes retryable")


class SameFlushTests(OutboxCase):
    """Test 1.1 (round-5 finding 1): a confirmed part and an uncertain part of one
    text, and another entry's delivered part of that text: one flush resolves them
    all, from current state, with no resend."""

    def test_one_flush_resolves_every_obligation_of_a_text(self):
        self.designate("alp-solver-2", T0 - 60)
        text = "[status] same words"
        self.server.plan = ["ok"]
        self.post(text)                                  # A: confirmed, msgid unknown
        self.server.plan = ["timeout"]
        with self.as_process(OTHER_PID):
            self.post(text)                              # B: committed, then uncertain
        for m in self.server.published:
            self.see(m)
        self.flush()
        self.assertEqual(len(self.server.published), 2)
        self.assertTrue(all(e["state"] == "done" for e in self.entries()))
        self.assertEqual(len(self.sql("SELECT * FROM attributions")), 2, "each message counted once")


class DirectPostDuringFlushTests(OutboxCase):
    """Test 1.3 (round-5 finding 3, section 5.6 case 1)."""

    def test_a_direct_posts_message_never_satisfies_an_older_part_too(self):
        text = "[status] build green"
        self.server.plan = ["error"]  # U: ended with finality, never published
        self.post(text)
        self.server.published.clear()
        self.cover(T0 - 3600, T0 + 3600)
        self.clock.advance(1)
        # D: the same text posted directly; its A1 commits and its message is indexed while it awaits the PONG
        d_entry = "d" * 32
        writer = self.probes.view(OTHER_PID).identity("127.0.0.1:1>127.0.0.1:6667")
        self.tx(lambda s: s.create(d_entry, {"channel": "#alpha", "as": "alp-solver-2", "text": text},
                                   [{"kind": "multiline", "lines": [[text, False]], "text": text}], writer, "post"))
        status, att = self.tx(lambda s: s.attempt(d_entry, 0, 0, "nd", writer))
        self.see({"msgid": "srv-d", "channel": "#alpha", "account": "alp-solver-2", "text": text,
                  "at": self.clock() + 1})
        self.flush()
        u = [e for e in self.entries() if e["id"] != d_entry][0]
        self.assertEqual(u["parts"][0]["state"], "uncertain", "R2 cannot rule U absent while D is writing")
        self.tx(lambda s: s.outcome(d_entry, 0, att["generation"], "nd", writer, "confirmed", self.clock() + 2))
        self.cover(T0 - 3600, T0 + 7200)
        self.flush()
        u = [e for e in self.entries() if e["id"] != d_entry][0]
        self.assertEqual(u["state"], "done")
        self.assertEqual(len(self.server.published), 1, "U was resent once; D's message counted for D only")


class OutageTests(OutboxCase):
    """Test 1.4: a 26-hour outage keeps the evidence an unresolved attempt needs."""

    def test_evidence_survives_a_26_hour_outage(self):
        self.server.plan = ["ok", "ok", "timeout"]
        self.pchat("post", "#alpha", LONG)
        for m in self.server.published:
            self.see(m)
        self.clock.advance(26 * 3600)
        self.tx(lambda s: s.collect())
        self.assertEqual(len(self.sql("SELECT * FROM messages")), 3)


class UntrackedSubstitutionTests(OutboxCase):
    """Test 2: an identical untracked message."""

    def test_undesignated_it_leaves_the_part_unknown_until_its_dead_letter(self):
        self.server.plan = ["timeout"]
        self.post("[status] ping")
        self.server.published.clear()
        self.see({"msgid": "untracked", "channel": "#alpha", "account": "alp-solver-2", "text": "[status] ping",
                  "at": T0 + 1})
        self.flush()
        self.assertEqual(self.states(), ["uncertain"])

    def test_designated_an_unexplained_message_suspends_once_with_one_alert(self):
        self.designate("alp-solver-2", T0 - 60)
        for i in range(2):
            self.see({"msgid": f"x{i}", "channel": "#alpha", "account": "alp-solver-2", "text": f"stray {i}",
                      "at": T0 + i})
        [acct] = self.sql("SELECT * FROM accounts")
        self.assertIsNotNone(acct["suspended_at"])
        self.assertEqual([a["alert_key"] for a in self.alerts()], ["suspended:alp-solver-2:1"])


if __name__ == "__main__":
    unittest.main()
