"""The outbox's file boundaries (docs/chat_outbox_state.md revision 8, section
9: tests 7, 9, 14, 22, 23 and 32-34): fallback files published and imported
without outbox.lock, the legacy outbox claimed and read without it, and every
export made durable. A filesystem model drops whatever was never synced, to
stand in for a power loss; a held flock stands in for a stopped old tool.
Invented data only, under _isolation."""
import fcntl
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from outbox_fakes import CLIENT_PID, OTHER_PID, T0, OutboxCase, SimFS  # noqa: E402


class Crash(BaseException):
    pass


class FilesCase(OutboxCase):
    def setUp(self):
        super().setUp()
        self.state = self.chatlib.STATE_DIR
        self.fs = SimFS(self.state)
        self.authority.close()
        self.authority = self.new_authority(fs=self.fs)
        p = mock.patch.object(self.ds, "FS_DEFAULT", self.fs)
        p.start()
        self.patches.append(p)
        self.p_lock = mock.patch.object(self.ds, "LOCK_WAIT", 0.05)
        self.p_lock.start()
        self.patches.append(self.p_lock)
        self.importer = self.ds.Importer(self.authority)

    def legacy(self, *rows, name="outbox.jsonl", raw=None):
        path = self.state / name
        with path.open("ab") as f:
            for row in rows:
                f.write((json.dumps(row) + "\n").encode())
            if raw:
                f.write(raw)
        self.fs.durable(path)
        return path

    def row(self, text, account="alp-solver-2", channel="#alpha"):
        return {"channel": channel, "as": account, "text": text, "at": "2026-10-09T00:00:00"}

    def published_texts(self):
        return [m["text"].split(" (delayed")[0] for m in self.server.published if m["text"]]

    @staticmethod
    def hold_lock(path):
        holder = open(path, "a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        return holder


class DurableClaimTests(FilesCase):
    """Test 7 (F3): power losses around the claim, its import and its unlink."""

    def test_a_power_loss_after_the_rename_before_the_directory_sync(self):
        self.legacy(self.row("one"), self.row("two"))
        real = self.fs.sync_dir
        calls = []

        def crash_once(path):
            if not calls:
                calls.append(path)
                raise Crash()
            return real(path)
        with mock.patch.object(self.fs, "sync_dir", crash_once), self.assertRaises(Crash):
            self.importer.claim_legacy()
        self.fs.power_loss()
        self.assertTrue((self.state / "outbox.jsonl").exists(), "the rename never reached the disk")
        for _ in range(2):
            self.flush()
        self.assertEqual(sorted(self.published_texts()), ["one", "two"])

    def test_a_power_loss_after_the_import_and_after_an_unsynced_unlink(self):
        self.legacy(self.row("one"), self.row("two"))
        self.flush()  # claimed, imported, sent; the claim retired at the barrier
        self.fs.power_loss()
        self.restart_bridge()
        self.authority.close()
        self.authority = self.new_authority(fs=self.fs)
        self.importer = self.ds.Importer(self.authority)
        for _ in range(2):
            self.flush()
        self.assertEqual(sorted(self.published_texts()), ["one", "two"], "no occurrence duplicated or lost")


class ImportTests(FilesCase):
    """Test 14: duplicates, changed files, unsupported rows, and the fallback file rules."""

    def test_identical_rows_are_distinct_occurrences(self):
        self.legacy(self.row("same"), self.row("same"))
        self.flush()
        self.assertEqual(self.published_texts(), ["same", "same"])

    def test_unsupported_rows_are_held_with_one_alert_for_their_claim_file(self):
        self.legacy(self.row("fine"), {"channel": "#alpha", "as": "x", "text": "t", "parts": []},
                    {"channel": "#alpha", "as": "x", "text": "t", "fallback": True}, raw=b"not json\n")
        self.flush()
        held = self.sql("SELECT * FROM held")
        self.assertEqual(len(held), 3)
        self.assertEqual(len([a for a in self.alerts() if a["kind"] == "held"]), 1)
        self.assertEqual(self.published_texts(), ["fine"])

    def test_a_changed_claim_prefix_is_held_and_never_merged(self):
        path = self.legacy(self.row("one"), name="outbox.claimed-1-a.jsonl")
        self.importer.import_legacy(["outbox.claimed-1-a.jsonl"])
        path.write_text(json.dumps(self.row("rewritten")) + "\n" + json.dumps(self.row("more")) + "\n")
        self.importer.import_legacy(["outbox.claimed-1-a.jsonl"])
        self.assertEqual(len(self.entries()), 1)
        self.assertTrue(path.exists(), "a changed claim is never unlinked")
        self.assertTrue(any(a["alert_key"].startswith("held:outbox.claimed-1-a.jsonl:") for a in self.alerts()))

    def fallback(self, entry_id, writer=None, **extra):
        row = dict(self.row("from a file"), id=entry_id, writer=writer, **extra)
        return self.ds.publish_fallback(self.fs, self.authority.paths, row, clock=self.clock)

    def test_the_fallback_file_rules(self):
        w = self.probes.view(CLIENT_PID).identity("127.0.0.1:9>x")
        # no such entry: created, the whole post once
        self.fallback("a" * 32)
        # an open direct entry of that writer: H1
        self.tx(lambda s: s.create("b" * 32, self.row("x"), [{"kind": "line", "lines": [["x", False]], "text": "x"}],
                                   w, "post"))
        self.fallback("b" * 32, writer=w)
        # another writer's entry: held
        self.tx(lambda s: s.create("c" * 32, self.row("y"), [{"kind": "line", "lines": [["y", False]], "text": "y"}],
                                   w, "post"))
        self.fallback("c" * 32, writer=self.probes.view(OTHER_PID).identity())
        # a done entry: a no-op
        self.tx(lambda s: s.con.execute("INSERT INTO entries (id, kind, account, channel, owner_kind, state)"
                                        " VALUES (?, 'post', 'x', '#alpha', 'outbox', 'done')", ("d" * 32,)))
        self.fallback("d" * 32)
        self.importer.import_fallback_files()
        got = {e["id"][0]: (e["owner_kind"], e["state"]) for e in self.entries()}
        self.assertEqual(got, {"a": ("outbox", "open"), "b": ("outbox", "open"), "c": ("direct", "open"),
                               "d": ("outbox", "done")})
        self.assertEqual(len(self.sql("SELECT * FROM held WHERE why LIKE '%H1%'")), 1)
        self.assertEqual(self.fallback_files(), [])

    def test_an_unreadable_published_file_is_held_with_one_alert(self):
        (self.state / "outbox.d").mkdir(exist_ok=True)
        bad = self.state / "outbox.d" / f"{int(T0 * 1e9):020d}-{'e' * 32}-bad.json"
        bad.write_text("{not json")
        self.importer.import_fallback_files()
        self.assertEqual(len(self.sql("SELECT * FROM held")), 1)
        self.assertEqual(len(self.alerts()), 1)
        self.assertFalse(bad.exists())


class ExportDurabilityTests(FilesCase):
    """Tests 9 (F5) and 22 (directory durability on recovery)."""

    def terminal_entry(self):
        self.server.plan = ["fail"]
        self.post("[status] refused")
        return self.entry()

    def test_x1_a_power_loss_before_the_sync_loses_the_line_and_x1_writes_it_again(self):
        e = self.terminal_entry()
        real = self.fs.durable

        def crash(path):
            raise Crash()
        with mock.patch.object(self.fs, "durable", crash), self.assertRaises(Crash):
            self.bridge.Flusher(self.authority).export_dead_letters()
        self.fs.power_loss()
        self.assertEqual(self.dead_letters(), [])
        self.bridge.Flusher(self.authority).export_dead_letters()
        self.assertEqual([d["id"] for d in self.dead_letters()], [e["id"]])
        del real

    def test_a_record_left_with_an_unsynced_directory_entry_is_synced_before_its_mark(self):
        e = self.terminal_entry()
        real_dir = self.fs.sync_dir

        def crash_on_dir(path):
            raise Crash()
        with mock.patch.object(self.fs, "sync_dir", crash_on_dir), self.assertRaises(Crash):
            self.bridge.Flusher(self.authority).export_dead_letters()  # created, appended, file synced: then crash
        self.bridge.Flusher(self.authority).export_dead_letters()  # finds it, syncs file AND directory, marks
        self.assertEqual(self.sql("SELECT dead_letter_exported_version v FROM entries")[0]["v"], 1)
        self.fs.power_loss()
        self.assertEqual([d["id"] for d in self.dead_letters()], [e["id"]], "the record survives the power loss")
        del real_dir

    def test_negative_control_without_the_directory_sync_the_record_is_lost(self):
        self.terminal_entry()
        with mock.patch.object(self.fs, "sync_dir", lambda path: None):
            self.bridge.Flusher(self.authority).export_dead_letters()
        self.fs.power_loss()
        self.assertEqual(self.dead_letters(), [], "the test can see the defect it guards against")

    def test_a_fallback_file_in_a_directory_another_process_made_survives_after_exit_3(self):
        synced = []
        real = self.fs.sync_dir
        self.server.refuse_login = {"alp-solver-2"}
        with self.unavailable(), mock.patch.object(self.fs, "sync_dir", lambda p: synced.append(Path(p)) or real(p)):
            self.assertEqual(self.post("[status] durable"), 3)
        self.assertIn(self.state, synced, "outbox.d's own entry is made durable, whoever created it")
        self.assertIn(self.state / "outbox.d", synced)
        self.fs.power_loss()
        self.assertEqual(len(self.fallback_files()), 1)
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] durable"])

    def test_the_pending_queue_and_its_ledger_are_durable(self):
        d = self.bridge.Deliveries()
        d.add_all([{"kind": "push", "msgid": "m1", "channel": "#alpha", "sender": "pat", "project": None,
                    "attempt": 0, "next_at": 0, "text": "x"}])
        d._record(d.items[0], True, "pushed")
        self.fs.power_loss()
        self.assertEqual(len(json.loads((self.state / "pending.json").read_text())), 1)
        self.assertEqual(len((self.state / "deliveries.jsonl").read_text().splitlines()), 1)

    def test_a_delivery_dead_letter_waits_for_outbox_lock_and_stays_pending(self):
        d = self.bridge.Deliveries()
        item = {"kind": "push", "msgid": "m2", "channel": "#alpha", "sender": "pat", "project": None,
                "attempt": 9, "next_at": 0, "text": "x"}
        d.add_all([item])
        holder = self.hold_lock(self.state / "outbox.lock")
        try:
            d._dead(item, "gave up", {})
        finally:
            holder.close()
        self.assertEqual(d.items, [item], "not acquired: the item stays pending, unchanged")
        d._dead(item, "gave up", {})
        self.assertEqual(d.items, [])
        self.assertEqual(len(self.dead_letters()), 1)


class PausedLockHolderTests(FilesCase):
    """Tests 23 and 32: a stopped holder of outbox.lock delays only its own row
    and the deletion of claim files; every durable obligation flows."""

    def test_durable_obligations_behind_a_stopped_holder_flow_in_the_same_flush(self):
        self.server.refuse_login = {"sam"}
        with self.unavailable():
            self.assertEqual(self.post("[status] beta green", channel="#beta", account="sam"), 3)
        self.server.refuse_login = set()
        path = self.legacy(self.row("[status] alpha row"), raw=b'{"channel": "#alpha", "as": "pat", "te')  # half row
        holder = self.hold_lock(self.state / "outbox.lock")
        try:
            started = time.monotonic()
            self.flush()
            self.assertLess(time.monotonic() - started, 5, "the flush stays within its absolute deadlines")
            self.assertEqual(sorted(self.published_texts()), ["[status] alpha row", "[status] beta green"])
            [claim] = self.importer.claim_list()
            self.assertTrue((self.state / claim).exists(), "retirement waits for the lock")
            snap = json.loads((self.state / "outbox-status.json").read_text())
            self.assertIn("retire_and_export", snap["deferred"])
            # the stopped tool resumes: it finishes its row into the file it opened (now the claim)
            with (self.state / claim).open("ab") as f:
                f.write(b'xt": "[status] pat row", "at": "t"}\n')
            self.clock.advance(60)
            self.flush()
            self.assertIn("[status] pat row", self.published_texts())
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
        self.clock.advance(60)
        self.flush()
        self.assertEqual(self.importer.claim_list(), [], "retired once the lock was acquired after the claim")
        self.assertEqual(sorted(self.published_texts()), ["[status] alpha row", "[status] beta green",
                                                          "[status] pat row"])
        del path

    def test_a_holder_that_dies_leaves_its_half_row_held_at_retirement(self):
        self.legacy(self.row("[status] whole"), raw=b'{"channel": "#alpha", "as": "pat", "te')
        holder = self.hold_lock(self.state / "outbox.lock")
        self.flush()
        holder.close()  # it dies: the kernel releases its flock
        self.clock.advance(60)
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] whole"])
        self.assertEqual(len(self.sql("SELECT * FROM held")), 1)
        self.assertEqual(self.importer.claim_list(), [])

    def test_no_client_of_this_release_waits_for_outbox_lock(self):
        holder = self.hold_lock(self.state / "outbox.lock")
        try:
            self.server.refuse_login = {"alp-solver-2"}
            done = threading.Event()
            codes = []

            def run():
                with self.unavailable():
                    codes.append(self.post("[status] no wait"))
                done.set()
            threading.Thread(target=run, daemon=True).start()
            self.assertTrue(done.wait(5))
            self.assertEqual(codes, [3])
        finally:
            holder.close()

    def test_the_lock_is_never_broken_and_an_e7_and_its_alert_still_go_out(self):
        self.server.plan = ["ok", "timeout"]
        self.post("[status] one\n" + "y" * 5000)
        holder = self.hold_lock(self.state / "outbox.lock")
        try:
            self.clock.advance(self.ds.UNDECIDED_MAX + 1)
            deliveries = self.bridge.Deliveries()
            self.flush(deliveries)
            self.assertEqual(self.entry()["state"], "terminal")
            self.assertEqual(len([i for i in deliveries.items if i.get("alert_key")]), 1)
            self.assertEqual(self.dead_letters(), [], "X1 waits for the lock")
            with self.assertRaises(BlockingIOError):  # still ours: never stolen
                with open(self.state / "outbox.lock", "a") as other:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            holder.close()
        self.flush()
        self.assertEqual(len(self.dead_letters()), 1)


class PublicationTests(FilesCase):
    """Test 33: unfinished, duplicate and failing publications."""

    def test_a_writer_stopped_before_its_link_is_never_imported_and_delays_nothing(self):
        staging = self.state / "outbox.d" / "tmp"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / f"test-boot.{CLIENT_PID}.start-1.abc.tmp").write_text(json.dumps(dict(self.row("x"), id="f" * 32)))
        self.server.refuse_login = {"sam"}
        with self.unavailable():
            self.post("[status] other", account="sam")
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] other"])
        self.assertTrue((staging / f"test-boot.{CLIENT_PID}.start-1.abc.tmp").exists(), "a stopped writer's: kept")
        self.probes.world["gone"].add(CLIENT_PID)
        self.flush()
        self.assertEqual(list(staging.iterdir()), [], "a gone writer's: removed")

    def test_a_power_loss_after_the_link_before_the_directory_sync(self):
        real = self.fs.sync_dir
        state = {"n": 0}

        def crash_after_link(path):
            if Path(path).name == "outbox.d" and state["n"] >= 1:
                raise Crash()
            state["n"] += 1
            return real(path)
        self.server.refuse_login = {"alp-solver-2"}
        with self.unavailable(), mock.patch.object(self.fs, "sync_dir", crash_after_link), \
                self.assertRaises(Crash):
            self.post("[status] lost")  # never reported queued
        self.fs.power_loss()
        self.assertEqual(self.fallback_files(), [], "the name never reached the disk; nothing was reported")

    def test_two_files_for_one_entry_are_one_obligation(self):
        row = dict(self.row("[status] twice"), id="9" * 32)
        for _ in range(2):
            self.ds.publish_fallback(self.fs, self.authority.paths, row, clock=self.clock)
        self.assertEqual(len(self.fallback_files()), 2)
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] twice"])

    def test_a_taken_name_is_never_replaced(self):
        row = dict(self.row("[status] mine"), id="8" * 32)
        with mock.patch.object(self.ds.secrets, "token_hex", side_effect=["aa" * 6, "bb" * 4, "aa" * 6, "bb" * 4,
                                                                           "cc" * 4]):
            first = self.ds.publish_fallback(self.fs, self.authority.paths, row, clock=self.clock)
            second = self.ds.publish_fallback(self.fs, self.authority.paths, dict(row, text="[status] other"),
                                              clock=self.clock)
        self.assertNotEqual(first, second)
        self.assertIn("mine", (self.state / "outbox.d" / first).read_text())

    def test_without_hard_links_publication_fails_and_nothing_is_reported_queued(self):
        self.fs.fail.add("link")
        self.server.refuse_login = {"alp-solver-2"}
        with self.unavailable():
            code, said = self.pchat("post", "#alpha", "[status] x")
        self.assertEqual(code, 2, said)
        self.assertIn("cannot be written", said)


class ImportBoundaryTests(FilesCase):
    """Test 34: crashes at each import step, fairness, passes, and legacy claims in several reads."""

    published = 0

    def publish(self, n):
        for _ in range(n):
            i = self.published
            self.published += 1
            self.clock.advance(0.001)
            self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row(f"[status] {i}"), id=f"{i:032d}"),
                                     clock=self.clock)

    def test_crashes_around_each_step_neither_lose_nor_duplicate(self):
        self.publish(1)
        for step in ("unlink", "sync_dir"):
            with self.subTest(step=step):
                with mock.patch.object(self.fs, step, side_effect=Crash()), self.assertRaises(Crash):
                    self.importer.import_fallback_files()
                self.fs.power_loss()
        self.importer.import_fallback_files()
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] 0"])

    def test_bounded_and_fair_oldest_first_with_a_steady_inflow(self):
        self.publish(5)
        with mock.patch.object(self.ds, "IMPORT_MAX", 2):
            seen = []
            for _ in range(6):
                self.importer.import_fallback_files()
                seen.append(len(self.entries()))
                self.publish(1)
        self.assertEqual(seen[:3], [2, 4, 6], "two per flush, the oldest first")
        ids = [e["id"] for e in self.entries()]
        self.assertEqual(ids[:5], [f"{i:032d}" for i in range(5)])

    def test_import_epoch_grows_only_when_a_whole_listing_is_imported(self):
        self.publish(3)
        with mock.patch.object(self.ds, "IMPORT_MAX", 2):
            self.importer.import_fallback_files()
            self.assertEqual(self.tx(lambda s: s.meta("import_epoch")), "0")
            self.importer.import_fallback_files()
            self.assertEqual(self.tx(lambda s: s.meta("import_epoch")), "1")

    def test_a_handoff_published_before_an_e8_mid_pass_is_applied_and_nothing_collected_early(self):
        """Test 8 (F4): reopenable progress across a pass that spans flushes."""
        w = self.probes.view(CLIENT_PID).identity("c")
        parts = [{"kind": "line", "lines": [[t, False]], "text": t} for t in ("p0", "p1")]
        self.tx(lambda s: s.create("h" * 32, self.row("p0\np1"), parts, w, "post"))
        _, att = self.tx(lambda s: s.attempt("h" * 32, 0, 0, "n0", w))
        self.tx(lambda s: s.outcome("h" * 32, 0, att["generation"], "n0", w, "confirmed", T0))
        self.publish(3)  # other files: the pass will span two flushes
        with mock.patch.object(self.ds, "IMPORT_MAX", 2):
            self.importer.import_fallback_files()  # the pass's listing misses the handoff file below
            self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row("p0\np1"), id="h" * 32, writer=w),
                                     clock=self.clock)
            self.probes.world["gone"].add(CLIENT_PID)
            self.bridge.Flusher(self.authority).abandon_or_adopt()  # E8, mid-pass
            self.assertEqual(self.entry("h" * 32)["state"], "abandoned")
            self.tx(lambda s: s.collect())
            self.assertEqual(len(self.attempts(entry_id="h" * 32)), 1, "not collected while H1 may still apply")
            for _ in range(3):
                self.importer.import_fallback_files()
        e = self.entry("h" * 32)
        self.assertEqual((e["state"], e["owner_kind"], self.states(e)), ("open", "outbox", ["confirmed", "unsent"]))

    def test_a_legacy_claim_read_in_several_requests_keeps_its_occurrence_ids(self):
        rows = [self.row(f"[status] {i}") for i in range(5)]
        self.legacy(*rows, name="outbox.claimed-7-x.jsonl")
        with mock.patch.object(self.ds, "LINES_MAX", 2):
            self.importer.import_legacy(["outbox.claimed-7-x.jsonl"])
        once = sorted(e["id"] for e in self.entries())
        self.assertEqual(len(once), 5)
        self.importer.import_legacy(["outbox.claimed-7-x.jsonl"])
        self.assertEqual(sorted(e["id"] for e in self.entries()), once)

    def test_a_claim_is_never_unlinked_before_a_barrier_after_its_first_import(self):
        self.legacy(self.row("[status] x"), name="outbox.claimed-8-y.jsonl")
        self.importer.import_legacy(["outbox.claimed-8-y.jsonl"], barrier_at=T0 - 1)  # a barrier from before
        self.assertTrue((self.state / "outbox.claimed-8-y.jsonl").exists())
        self.clock.advance(1)
        self.importer.import_legacy(["outbox.claimed-8-y.jsonl"], barrier_at=self.clock())
        self.assertFalse((self.state / "outbox.claimed-8-y.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
