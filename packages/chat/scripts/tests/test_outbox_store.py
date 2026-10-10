"""The outbox authority's own rules (docs/chat_outbox_state.md revision 8,
section 9: tests 3, 4, 5, 15, 19, 24, 27, 30 and 35): attribution, finality,
fragments, message accounting on every part, collection, alert identity,
A12 and attested outcomes. Each builds attempts and messages straight in a
sandboxed database, on an injected clock, with invented data only."""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from outbox_fakes import CLIENT_PID, OTHER_PID, T0, FakeServer, OutboxCase  # noqa: E402

ACCT, CH = "alp-solver-2", "#alpha"


class StoreCase(OutboxCase):
    """Helpers that make an entry and its attempts as a writer would."""

    seq = 0

    def writer(self, pid=CLIENT_PID, port=1):
        return self.probes.view(pid).identity(f"127.0.0.1:{port}>127.0.0.1:6667")

    def entry_with(self, texts, *, multi=False, pid=CLIENT_PID, account=ACCT, channel=CH):
        StoreCase.seq += 1
        entry_id = f"e{StoreCase.seq:031d}"
        w = self.writer(pid)
        parts = []
        for t in texts:
            lines = [[x, False] for x in t.split("\n")] if multi else [[t, False]]
            parts.append({"kind": "multiline" if multi else "line", "lines": lines, "text": t})
        self.tx(lambda s: s.create(entry_id, {"channel": channel, "as": account, "text": "\n".join(texts)},
                                   parts, w, "post"))
        return entry_id, w

    def attempt(self, entry_id, n, w, *, at=None, outcome=None, final=None, nonce=None):
        """A1 at `at`, then, if given, the outcome with `final` as its PONG time."""
        nonce = nonce or f"nonce-{entry_id}-{n}"
        if at is not None:
            self.clock.t = at
        gen = self.tx(lambda s: s.con.execute("SELECT generation FROM parts WHERE entry_id = ? AND n = ?",
                                              (entry_id, n)).fetchone()[0])
        _, att = self.tx(lambda s: s.attempt(entry_id, n, gen, nonce, w))
        if outcome:
            self.tx(lambda s: s.outcome(entry_id, n, att["generation"], nonce, w, outcome, final))
        return att["generation"], nonce

    def message(self, text, at, msgid=None, account=ACCT, channel=CH):
        StoreCase.seq += 1
        msgid = msgid or f"m{StoreCase.seq}"
        self.tx(lambda s: s.index(msgid, channel, account, text, at))
        return msgid

    def reconcile(self):
        out = []
        for key in self.tx(lambda s: s.reconcile_keys()):
            out += self.tx(lambda s, key=key: s.reconcile(*key))
        return out

    def part_state(self, entry_id, n=0):
        return self.sql("SELECT state FROM parts WHERE entry_id = ? AND n = ?", entry_id, n)[0]["state"]


class UniqueAttributionTests(StoreCase):
    """Test 3: identical texts within and across entries; the UNIQUE constraints; a crash between components."""

    def test_identical_texts_each_get_a_distinct_message(self):
        self.designate(ACCT, T0 - 100)
        a, wa = self.entry_with(["same", "same"])
        b, wb = self.entry_with(["same"], pid=OTHER_PID)
        self.attempt(a, 0, wa, at=T0, outcome="confirmed", final=T0 + 1)
        self.attempt(a, 1, wa, at=T0 + 2, outcome="closed")
        self.attempt(b, 0, wb, at=T0 + 3, outcome="closed")
        for t in (T0 + 0.5, T0 + 2.5, T0 + 3.5):
            self.message("same", t)
        verdicts = self.reconcile()
        self.assertEqual(sorted(v for v, _ in verdicts), ["attributed", "delivered", "delivered"])
        rows = self.sql("SELECT * FROM attributions")
        self.assertEqual(len({r["msgid"] for r in rows}), 3)
        self.assertEqual(len({r["attempt_id"] for r in rows}), 3)

    def test_the_unique_constraints_refuse_a_second_use(self):
        a, w = self.entry_with(["x"])
        self.attempt(a, 0, w, at=T0, outcome="confirmed", final=T0 + 1)
        m = self.message("x", T0 + 0.5)
        [att] = self.attempts()
        self.tx(lambda s: s.con.execute("INSERT INTO attributions VALUES (?, ?, 0)", (m, att["id"])))
        with self.assertRaises(sqlite3.IntegrityError):
            self.tx(lambda s: s.con.execute("INSERT INTO attributions VALUES (?, ?, 0)", (m, 999)))
        with self.assertRaises(sqlite3.IntegrityError):
            self.tx(lambda s: s.con.execute("INSERT INTO attributions VALUES ('other', ?, 0)", (att["id"],)))

    def test_a_crash_between_components_decides_the_rest_the_same_way_later(self):
        self.designate(ACCT, T0 - 100)
        a, w = self.entry_with(["one"])
        b, w2 = self.entry_with(["two"], pid=OTHER_PID)
        self.attempt(a, 0, w, at=T0, outcome="closed")
        self.attempt(b, 0, w2, at=T0 + 1, outcome="closed")
        self.message("one", T0 + 0.5)
        self.message("two", T0 + 1.5)
        keys = self.tx(lambda s: s.reconcile_keys())
        self.tx(lambda s: s.reconcile(*keys[0]))  # the first component's transaction commits; then a crash

        class Crash(Exception):
            pass
        with self.assertRaises(Crash):
            def crashing(s):
                s.reconcile(*keys[1])
                raise Crash()
            self.tx(crashing)  # rolled back: nothing of it remains
        self.reconcile()
        self.assertEqual(len(self.sql("SELECT * FROM attributions")), 2)
        self.assertEqual({self.part_state(a), self.part_state(b)}, {"confirmed"})


class FinalityTests(StoreCase):
    """Test 4: what gives finality, and what never does."""

    def exchange(self, *plan, broken=False):
        server = FakeServer(self.clock, plan)
        conn = server.login(ACCT)
        if broken:
            conn.closed = True
        return self.chatlib._exchange(conn, ["PRIVMSG #alpha :x"])

    def test_an_error_reply_and_a_late_pong_give_finality(self):
        outcome, final, _, _ = self.exchange("error")
        self.assertEqual(outcome, "closed")
        self.assertIsNotNone(final)
        outcome, final, _, drained = self.exchange("late")
        self.assertEqual((outcome, drained), ("confirmed", True))
        self.assertIsNotNone(final)
        outcome, final, _, _ = self.exchange("late-error")
        self.assertEqual(outcome, "closed")
        self.assertIsNotNone(final)

    def test_a_bare_timeout_and_a_write_error_give_none(self):
        self.assertEqual(self.exchange("timeout")[:2], ("closed", None))
        self.assertEqual(self.exchange(broken=True)[:2], ("closed", None))

    def test_a_fail_during_the_drain_is_a_refusal(self):
        self.assertEqual(self.exchange("late-fail")[0], "refused")

    def test_a_gone_writer_or_a_closed_connection_ends_without_finality_and_r2_never_applies(self):
        for kind in ("writer_gone", "connection_closed"):
            with self.subTest(kind=kind):
                a, w = self.entry_with([f"x {kind}"])
                self.attempt(a, 0, w, at=T0)
                [att] = self.attempts(entry_id=a)
                self.tx(lambda s: s.end_attempts([(att["id"], kind, T0 + 1)]))
                self.cover(T0 - 100, T0 + 10 ** 6)
                self.reconcile()
                [att] = self.attempts(entry_id=a)
                self.assertEqual((att["state"], att["final_at"]), ("ended", None))
                self.assertEqual(self.part_state(a), "uncertain")


class FragmentTests(StoreCase):
    """Test 5 (F1): no fragment model; any unexplained same-account message in a
    multi-line part's window blocks R2 and R1. A forced earlier part does not."""

    LINES = "north\n\nsouth"

    def setup_y(self):
        self.cover(T0 - 100, T0 + 1000)
        y, w = self.entry_with([self.LINES], multi=True)
        self.attempt(y, 0, w, at=T0, outcome="closed", final=T0 + 2)  # ended with finality
        return y

    def test_each_unexplained_fragment_blocks_absence(self):
        for fragment in ("north\n", "south", "\n", "south\nnorth", "northsouth", "unrelated text"):
            with self.subTest(fragment=fragment):
                self.fresh()
                y = self.setup_y()
                self.message(fragment, T0 + 1)
                self.reconcile()
                self.assertEqual(self.part_state(y), "uncertain")

    def test_with_nothing_unexplained_it_is_absent(self):
        y = self.setup_y()
        self.reconcile()
        self.assertEqual(self.part_state(y), "unsent")

    def test_a_forced_earlier_part_of_the_same_post_does_not_block(self):
        self.cover(T0 - 100, T0 + 1000)
        e, w = self.entry_with(["intro", self.LINES], multi=True)
        self.attempt(e, 0, w, at=T0, outcome="confirmed", final=T0 + 1)
        self.message("intro", T0 + 0.5)
        self.attempt(e, 1, w, at=T0 + 1.5, outcome="closed", final=T0 + 3)
        self.reconcile()
        self.assertEqual(self.part_state(e, 1), "unsent")

    def test_an_unconfirmed_multi_line_attempt_blocks_r1_inside_its_window(self):
        self.designate(ACCT, T0 - 100)
        self.setup_y()
        z, wz = self.entry_with(["south"], pid=OTHER_PID)
        self.attempt(z, 0, wz, at=T0 + 0.5, outcome="closed")
        self.message("south", T0 + 1)
        self.reconcile()
        self.assertEqual(self.part_state(z), "uncertain", "Y might have published any text")


class AccountingTests(StoreCase):
    """Test 30 (design round 4, finding 3): R2 condition 5 applies to every part."""

    def check(self, multi):
        text = "deploy\ndone" if multi else "deploy done"
        self.cover(T0 - 100, T0 + 1000)
        y, w = self.entry_with([text], multi=multi)
        self.attempt(y, 0, w, at=T0, outcome="closed", final=T0 + 2)
        m = self.message("beta status", T0 + 1)
        self.reconcile()
        self.assertEqual(self.part_state(y), "uncertain", "one unexplained message: UNKNOWN, never resent")
        k, wk = self.entry_with(["beta status"], pid=OTHER_PID)
        self.attempt(k, 0, wk, at=T0 + 0.5, outcome="confirmed", final=T0 + 1.5)  # now it is forced to k
        self.reconcile()
        self.assertEqual(self.part_state(y), "unsent", "accounted for: absent, resent once")
        del m

    def test_a_single_line_part(self):
        self.check(multi=False)

    def test_a_multi_line_part_as_a_control(self):
        self.check(multi=True)

    def test_an_attributed_message_is_accounted_for_too(self):
        self.cover(T0 - 100, T0 + 1000)
        y, w = self.entry_with(["deploy done"])
        self.attempt(y, 0, w, at=T0, outcome="closed", final=T0 + 2)
        m = self.message("beta status", T0 + 1)
        self.tx(lambda s: s.con.execute("INSERT INTO attributions VALUES (?, 12345, 0)", (m,)))
        self.reconcile()
        self.assertEqual(self.part_state(y), "unsent")


class CollectionTests(StoreCase):
    """Tests 15 and 19: collection by dependency, then delayed reconciliation."""

    def test_messages_near_unresolved_attempts_are_kept_and_attributions_go_with_their_messages(self):
        a, w = self.entry_with(["kept"])
        self.attempt(a, 0, w, at=T0, outcome="closed")  # unresolved, open window
        self.message("kept", T0 + 1)
        b, wb = self.entry_with(["old"], pid=OTHER_PID)
        self.attempt(b, 0, wb, at=T0 - 10 * 86400, outcome="confirmed", final=T0 - 10 * 86400 + 1)
        old = self.message("old", T0 - 10 * 86400 + 0.5, account="bet-solver-1", channel="#beta")
        self.tx(lambda s: s.con.execute("INSERT INTO attributions VALUES (?, 777, 0)", (old,)))
        self.clock.t = T0 + 2 * 86400
        self.tx(lambda s: s.collect())
        msgids = {r["msgid"] for r in self.sql("SELECT msgid FROM messages")}
        self.assertNotIn(old, msgids)
        self.assertEqual(len(msgids), 1)
        self.assertEqual(self.sql("SELECT * FROM attributions"), [])

    def test_terminal_rows_stay_until_their_current_version_is_exported(self):
        a, w = self.entry_with(["dead end"])
        self.attempt(a, 0, w, at=T0, outcome="refused", final=T0 + 1)
        self.clock.t = T0 + 3 * 86400
        self.tx(lambda s: s.collect())
        self.assertEqual(len(self.attempts(entry_id=a)), 1)
        self.tx(lambda s: s.con.execute("UPDATE entries SET dead_letter_exported_version = export_version"))
        self.tx(lambda s: s.collect())
        self.assertEqual(self.attempts(entry_id=a), [])
        self.assertEqual(len(self.sql("SELECT * FROM entries WHERE id = ?", a)), 1, "entry rows are tombstones")

    def test_messages_kept_for_an_unresolved_attempt_never_stop_newer_ones_being_collected(self):
        """Self-audit, class D: each collection examines a bounded window that
        rotates past what it must keep, so retention never starves."""
        a, w = self.entry_with(["kept"])
        self.attempt(a, 0, w, at=T0 - 10 * 86400, outcome="closed")  # an open window from ten days ago
        for i in range(3):
            self.message(f"kept {i}", T0 - 9 * 86400 + i)
        other = self.message("other", T0 - 8 * 86400, account="bet-solver-1", channel="#beta")
        self.clock.t = T0
        for _ in range(3):
            self.tx(lambda s: s.collect(limit=2))
        msgids = {r["msgid"] for r in self.sql("SELECT msgid FROM messages")}
        self.assertNotIn(other, msgids)
        self.assertEqual(len(msgids), 3, "the protected ones are kept")

    def test_open_window_dead_attempts_are_kept(self):
        a, w = self.entry_with(["stuck"])
        self.attempt(a, 0, w, at=T0, outcome="closed")
        self.clock.t = T0 + 2 * 86400
        self.tx(lambda s: s.expire())
        self.tx(lambda s: s.con.execute("UPDATE entries SET dead_letter_exported_version = export_version"))
        self.tx(lambda s: s.con.execute("UPDATE alerts SET exported_at = 1"))
        self.tx(lambda s: s.collect())
        self.assertEqual([x["state"] for x in self.attempts(entry_id=a)], ["dead"])

    def test_collection_then_delayed_reconciliation_decides_the_same(self):
        self.cover(T0 - 100, T0 + 10 ** 6)
        k, wk = self.entry_with(["same"], pid=OTHER_PID)
        self.attempt(k, 0, wk, at=T0, outcome="confirmed", final=T0 + 10)
        self.message("same", T0 + 1)  # K's message: before U's window, inside K's own
        u, wu = self.entry_with(["same"])
        self.attempt(u, 0, wu, at=T0 + 8, outcome="closed", final=T0 + 9)
        self.clock.t = T0 + self.ds.GC_MARGIN + 3600
        self.tx(lambda s: s.collect())
        self.assertEqual(len(self.sql("SELECT * FROM messages")), 1, "K's message is kept")
        self.assertEqual(len(self.attempts(entry_id=k)), 1)
        self.reconcile()
        self.assertEqual(self.part_state(u), "unsent", "the same verdict as without collection")


class AlertTests(StoreCase):
    """Test 24 (design round 3, finding 3): exactly one alert per event."""

    def test_an_expired_entry_raises_one_row_and_one_pending_item(self):
        a, w = self.entry_with(["late"])
        self.attempt(a, 0, w, at=T0, outcome="closed")
        self.clock.t = T0 + self.ds.UNDECIDED_MAX + 1
        self.tx(lambda s: s.expire())
        self.tx(lambda s: s.expire())  # E7 again: the entry is terminal; nothing new
        self.assertEqual([x["alert_key"] for x in self.alerts()], [f"outbox:{a}"])
        deliveries = self.bridge.Deliveries()
        self.flush(deliveries)
        deliveries.items.clear()  # as if the push went out and left pending, its ledger line written
        self.bridge.Deliveries._record(deliveries, {"kind": "push", "msgid": "x", "channel": "-", "project": None,
                                                    "attempt": 1, "alert_key": f"outbox:{a}"}, True, "pushed")
        self.tx(lambda s: s.con.execute("UPDATE alerts SET exported_at = NULL"))  # a crash before X2's mark
        self.flush(deliveries)
        self.assertEqual(deliveries.items, [], "found in the ledger: not enqueued again")
        self.assertIsNotNone(self.alerts()[0]["exported_at"])

    def test_a_crash_after_the_pending_item_and_before_the_mark_still_gives_one(self):
        a, w = self.entry_with(["late"])
        self.attempt(a, 0, w, at=T0, outcome="closed")
        self.clock.t = T0 + self.ds.UNDECIDED_MAX + 1
        self.tx(lambda s: s.expire())
        deliveries = self.bridge.Deliveries()
        self.flush(deliveries)
        self.tx(lambda s: s.con.execute("UPDATE alerts SET exported_at = NULL"))
        self.flush(deliveries)
        self.assertEqual(len([i for i in deliveries.items if i.get("alert_key") == f"outbox:{a}"]), 1)

    def test_suspensions_raise_one_row_each_with_the_next_sequence(self):
        self.designate(ACCT, T0 - 100)
        self.message("stray", T0)
        self.message("stray again", T0 + 1)  # already suspended: nothing new
        self.designate(ACCT, T0 + 2)  # the owner lifts the suspension
        self.message("stray once more", T0 + 3)
        self.assertEqual([x["alert_key"] for x in self.alerts()],
                         [f"suspended:{ACCT}:1", f"suspended:{ACCT}:2"])

    def test_a_replayed_index_suspends_nothing_new(self):
        self.designate(ACCT, T0 - 100)
        self.message("stray", T0, msgid="same")
        self.message("stray", T0, msgid="same")
        self.assertEqual(len(self.alerts()), 1)


class AttestationTests(StoreCase):
    """Tests 27 and 35 (D4): A12 applies only on the writer's own nonce-bound
    statement, from writing or from an end by A8 or A8c, and never after A5."""

    def setup_lost_reply(self):
        a, w = self.entry_with(["one part"])
        gen, nonce = self.attempt(a, 0, w, at=T0)
        return a, w, gen, nonce

    def void(self, a, w, gen, nonce, **change):
        att = {"kind": "void", "id": a, "n": 0, "generation": gen, "a1_nonce": nonce, "writer": w}
        att.update(change)
        return self.tx(lambda s: s.apply_attestation(att))

    def test_a_void_from_writing_returns_the_part_to_unsent(self):
        a, w, gen, nonce = self.setup_lost_reply()
        self.assertTrue(self.void(a, w, gen, nonce))
        self.assertEqual((self.part_state(a), self.attempts(entry_id=a)[0]["state"]), ("unsent", "void"))

    def test_wrong_nonce_writer_or_generation_change_nothing(self):
        a, w, gen, nonce = self.setup_lost_reply()
        self.assertFalse(self.void(a, w, gen, "guess"))
        self.assertFalse(self.void(a, self.writer(OTHER_PID), gen, nonce))
        self.assertFalse(self.void(a, w, gen + 1, nonce))
        self.assertEqual(self.part_state(a), "inflight")

    def test_recovery_first_a8c_then_the_void_still_applies(self):
        for kind in ("connection_closed", "writer_gone"):
            with self.subTest(kind=kind):
                self.fresh()
                a, w, gen, nonce = self.setup_lost_reply()
                [att] = self.attempts(entry_id=a)
                self.tx(lambda s: s.end_attempts([(att["id"], kind, T0 + 1)]))
                self.assertEqual(self.part_state(a), "uncertain")
                self.assertTrue(self.void(a, w, gen, nonce))
                self.assertEqual(self.part_state(a), "unsent")

    def test_never_after_a5_or_a_decision_or_on_a_terminal_entry(self):
        a, w, gen, nonce = self.setup_lost_reply()
        self.tx(lambda s: s.outcome(a, 0, gen, nonce, w, "closed", None))  # A5: the writer wrote, then closed
        self.assertFalse(self.void(a, w, gen, nonce))
        for state in ("delivered", "absent", "dead"):
            with self.subTest(state=state):
                b, wb, gb, nb = self.setup_lost_reply()
                [att] = self.attempts(entry_id=b)
                self.tx(lambda s: s.con.execute("UPDATE attempts SET state = ? WHERE id = ?", (state, att["id"])))
                self.assertFalse(self.void(b, wb, gb, nb))
        c, wc, gc, nc = self.setup_lost_reply()
        [att] = self.attempts(entry_id=c)
        self.tx(lambda s: s.end_attempts([(att["id"], "writer_gone", T0 + 1)]))
        self.tx(lambda s: s.con.execute("INSERT INTO attributions VALUES ('mm', ?, 0)", (att["id"],)))
        self.assertFalse(self.void(c, wc, gc, nc), "an attribution names it")
        d, wd, gd, nd = self.setup_lost_reply()
        self.tx(lambda s: s.con.execute("UPDATE entries SET state = 'terminal' WHERE id = ?", (d,)))
        self.assertFalse(self.void(d, wd, gd, nd))

    def test_an_attested_outcome_applies_only_to_an_attempt_still_writing(self):
        a, w, gen, nonce = self.setup_lost_reply()
        att = {"kind": "outcome", "id": a, "n": 0, "generation": gen, "a1_nonce": nonce, "writer": w,
               "outcome": "confirmed", "final_at": T0 + 1}
        [row] = self.attempts(entry_id=a)
        self.tx(lambda s: s.end_attempts([(row["id"], "writer_gone", T0 + 1)]))
        self.assertFalse(self.tx(lambda s: s.apply_attestation(att)))
        b, wb, gb, nb = self.setup_lost_reply()
        self.assertTrue(self.tx(lambda s: s.apply_attestation(dict(att, id=b, writer=wb, generation=gb,
                                                                  a1_nonce=nb))))
        self.assertEqual(self.part_state(b), "confirmed")

    def test_a_lost_a1_reply_then_a_closed_connection_then_the_handoff_delivers_once(self):
        """Section 5.6 case 14, through pchat and the bridge."""
        real = self.authority.handle

        def handle(request, **kw):
            reply = real(request, **kw)
            return None if request.get("op") == "attempt" and request.get("n") == 1 else reply
        from unittest import mock
        conn_ids = []
        original_login = self.server.login

        def login(*a, **k):
            conn = original_login(*a, **k)
            conn_ids.append(conn.conn_id())
            return conn
        with mock.patch.object(self.authority, "handle", handle), \
                mock.patch.object(self.ds, "publish_fallback", wraps=self.ds.publish_fallback), \
                mock.patch.object(self.chatlib, "login", login):
            real_handoff = self.authority.handle
            with mock.patch.object(self.authority, "handle",
                                   lambda r, **kw: None if r.get("op") == "handoff" else real_handoff(r, **kw)):
                code, said = self.pchat("post", "#alpha", "[status] part one\n" + "x" * 5000)
        self.assertEqual(code, 3, said)
        self.assertEqual(len(self.fallback_files()), 1)
        [writing] = self.attempts(state="writing")
        self.probes.world["closed"].add(conn_ids[0])  # the client closed its connection: A8c runs first
        self.bridge._FLUSHERS.clear()
        fl = self.bridge.Flusher(self.authority)
        fl.end_gone_attempts()
        self.assertEqual(self.attempts(id=writing["id"])[0]["state"], "ended")
        self.flush()  # now the handoff file is imported: A12 from ended
        self.assertEqual(self.attempts(id=writing["id"])[0]["state"], "void")
        self.assertEqual(self.entry()["state"], "done")
        texts = [m["text"] for m in self.server.published]
        self.assertEqual(len(texts), len(set(texts)), "part 1 once, and part 0 never resent")


if __name__ == "__main__":
    unittest.main()
