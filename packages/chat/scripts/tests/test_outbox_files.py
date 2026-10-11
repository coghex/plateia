"""The outbox's file boundaries (docs/chat_outbox_state.md revision 8, section
9: tests 7, 9, 14, 22, 23 and 32-34): fallback files published and imported
without outbox.lock, the legacy outbox claimed and read without it, and every
export made durable. A filesystem model drops whatever was never synced, to
stand in for a power loss; a held flock stands in for a stopped old tool.
Invented data only, under _isolation."""
import fcntl
import hashlib
import json
import os
import sqlite3
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


class CleanupAfterPublicationTests(FilesCase):
    """Code review slot 6, finding 1: once the fallback file and its
    directories are durably synced, a failed removal of its tmp/ copy is
    cleanup only. The call is queued (exit 3), never "not queued", and the
    bridge delivers it once. A failure before publication stays a failure."""

    def test_a_cleanup_failure_after_publication_is_still_queued_and_delivered_once(self):
        real, failed = self.fs.unlink, []

        def unlink(path):
            if Path(path).parent == self.state / "outbox.d" / "tmp":
                failed.append(path)
                raise PermissionError(13, "permission denied (fake)")
            return real(path)
        with self.unavailable(), mock.patch.object(self.fs, "unlink", unlink):
            code, out = self.pchat("post", "#alpha", "[status] queued once")
        self.assertEqual(code, 3, out)
        self.assertEqual(len(failed), 1, "the cleanup was tried")
        self.assertEqual(len(self.fallback_files()), 1, "published")
        self.assertEqual(len(list((self.state / "outbox.d" / "tmp").iterdir())), 1, "its staging copy is kept")
        self.flush()
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] queued once"], "delivered once")
        self.assertEqual(list((self.state / "outbox.d" / "tmp").iterdir()), [], "cleaned up later, once linked")

    def test_a_failure_before_publication_is_still_a_failure(self):
        with self.unavailable(), mock.patch.object(self.fs, "link", side_effect=OSError(28, "no space (fake)")):
            code, out = self.pchat("post", "#alpha", "[status] not queued")
        self.assertNotEqual(code, 3, out)
        self.assertEqual(self.fallback_files(), [])


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


class UnreadableFileTests(FilesCase):
    """Code review slot 1, finding 2: a file the importer cannot read or sync is
    kept, with an alert, and never stops another entry's attempt."""

    def test_an_unreadable_fallback_file_never_blocks_other_entries(self):
        import os
        self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row("[status] stuck"), id="u" * 32),
                                 clock=self.clock)
        [bad] = self.fallback_files()
        os.chmod(bad, 0)
        self.server.refuse_login = {"sam"}
        self.post("[status] other", channel="#beta", account="sam")
        self.server.refuse_login = set()
        try:
            deliveries = self.bridge.Deliveries()
            self.flush(deliveries)
            self.flush(deliveries)
            self.assertEqual(self.published_texts(), ["[status] other"], "the other entry flows")
            self.assertTrue(bad.exists(), "the obligation is kept")
            alerts = [a for a in self.alerts() if a["ref"] == bad.name]
            self.assertEqual(len(alerts), 1)
            self.assertEqual(len([i for i in deliveries.items if i.get("alert_key") == alerts[0]["alert_key"]]), 1)
        finally:
            os.chmod(bad, 0o600)
        self.flush()
        self.assertEqual(sorted(self.published_texts()), ["[status] other", "[status] stuck"])

    def test_an_unreadable_legacy_claim_never_blocks_other_entries(self):
        import os
        claim = self.legacy(self.row("[status] old"), name="outbox.claimed-5-z.jsonl")
        os.chmod(claim, 0)
        self.server.refuse_login = {"sam"}
        self.post("[status] other", channel="#beta", account="sam")
        self.server.refuse_login = set()
        try:
            self.flush()
            self.assertEqual(self.published_texts(), ["[status] other"])
            self.assertEqual(len([a for a in self.alerts() if a["ref"] == claim.name]), 1)
        finally:
            os.chmod(claim, 0o600)
        self.flush()
        self.assertEqual(sorted(self.published_texts()), ["[status] old", "[status] other"])


class MalformedFallbackTests(FilesCase):
    """Code review slot 2, finding 3 (and self-audit class A): a fallback file is
    validated whole before any part of it is used. An invalid one is held once,
    with one alert, and removed like an imported file (section 6.2); a request
    that fails keeps its file, with an alert; nothing else waits for either."""

    def write(self, i, row):
        name = f"{int((T0 + i) * 1e9):020d}-{row.get('id') if isinstance(row, dict) else 'x'}-{i:08x}.json"
        path = self.state / "outbox.d" / name
        path.write_bytes((json.dumps(row) + "\n").encode())
        self.fs.durable(path)
        return path

    def queued(self, text="[status] other"):
        self.server.refuse_login = {"sam"}
        self.post(text, channel="#beta", account="sam")
        self.server.refuse_login = set()

    def test_malformed_fields_anywhere_are_held_once_and_block_nothing(self):
        base = dict(self.row("[status] odd"), v="outbox/2")
        att = {"kind": "void", "n": 0, "generation": 1, "a1_nonce": "x"}
        bad = [dict(base, id="a" * 32, attestation="bad"),
               dict(base, id="b" * 32, attestation=dict(att, n="0")),
               dict(base, id="c" * 32, attestation=dict(att, kind="outcome", outcome="confirmed", final_at="soon")),
               dict(base, id="d" * 32, attestation=dict(att, writer=5)),
               dict(base, id="e" * 32, attestation=dict(att, kind="other")),
               dict(base, id="f" * 32, writer="x"),
               dict(base, id="g" * 32, writer={"boot": "test-boot", "pid": "x", "start": "start-1"}),
               dict(base, id=7),
               dict(base, id="h" * 32, **{"as": {"x": 1}}),
               dict(base, id="i" * 32, cont=5),
               dict(base, id="j" * 32, reply_to=["m"]),
               dict(base, id="k" * 32, text=None, ack=5),
               dict(base, id="l" * 32, origin=["post"]),
               dict(base, id="m" * 32, channel="#al pha"),
               dict(base, id="n" * 32, at={"t": 1}),
               ["a", "list"]]
        paths = [self.write(i, row) for i, row in enumerate(bad)]
        self.write(len(bad), dict(base, id="z" * 32, text="[status] fine"))
        self.queued()
        self.flush()
        self.assertEqual(sorted(self.published_texts()), ["[status] fine", "[status] other"])
        self.assertEqual(self.fallback_files(), [], "each invalid file is held, then removed like an imported one")
        held = self.sql("SELECT file FROM held")
        self.assertEqual(sorted(h["file"] for h in held), sorted(p.name for p in paths))
        self.assertEqual(sorted(a["ref"] for a in self.alerts()), sorted(p.name for p in paths))
        self.flush()
        self.assertEqual(len(self.sql("SELECT * FROM held")), len(bad))

    def test_an_import_request_that_fails_keeps_its_file_with_an_alert_and_blocks_nothing(self):
        failing = self.write(0, dict(self.row("[status] later"), v="outbox/2", id="a" * 32))
        self.write(1, dict(self.row("[status] fine"), v="outbox/2", id="b" * 32))
        self.queued()
        real = self.ds.Store.import_fallback

        def flaky(store, name, sha, row):
            if name == failing.name:
                raise sqlite3.OperationalError("database disk image is malformed (fake)")
            return real(store, name, sha, row)
        with mock.patch.object(self.ds.Store, "import_fallback", flaky):
            self.flush()
        self.assertEqual(sorted(self.published_texts()), ["[status] fine", "[status] other"])
        self.assertEqual(self.fallback_files(), [failing], "kept: its obligation is not lost")
        self.assertEqual([a["ref"] for a in self.alerts()], [failing.name])
        self.flush()
        self.assertEqual(sorted(self.published_texts()), ["[status] fine", "[status] later", "[status] other"])

    def test_a_malformed_legacy_row_is_held_and_blocks_nothing(self):
        bad = [dict(self.row("x"), **{"as": {"x": 1}}), dict(self.row("x"), cont=5),
               dict(self.row("x"), reply_to={"m": 1}), dict(self.row("x"), at=7),
               dict(self.row("x"), channel="#a b"), dict(self.row("x"), channel=["#alpha"]),
               {"channel": "#alpha", "as": "alp-solver-2", "ack": "m1", "text": 5}]
        self.legacy(*bad, self.row("[status] good"))
        self.flush()
        self.assertEqual(self.published_texts(), ["[status] good"])
        self.assertEqual(len(self.sql("SELECT * FROM held")), len(bad))


class ImportRotationTests(FilesCase):
    """Code review slot 2, finding 4 (class D): retained failures rotate within
    the bounded budget, so a later file is never starved by them."""

    def test_retained_failures_never_starve_a_later_file(self):
        for i in range(3):
            self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row(f"[status] stuck {i}"), id=f"{i}" * 32),
                                     clock=lambda i=i: T0 + i)
        stuck = self.fallback_files()
        self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row("[status] later"), id="9" * 32),
                                 clock=lambda: T0 + 10)
        for p in stuck:
            os.chmod(p, 0)
        try:
            with mock.patch.object(self.ds, "IMPORT_MAX", 2):
                for _ in range(3):
                    self.flush()
            self.assertEqual(self.published_texts(), ["[status] later"])
            self.assertEqual({a["ref"] for a in self.alerts()}, {p.name for p in stuck}, "each one tried")
        finally:
            for p in stuck:
                os.chmod(p, 0o600)
        self.flush()
        self.assertEqual(sorted(self.published_texts()), ["[status] later"] + [f"[status] stuck {i}" for i in range(3)])


class ListingFailureTests(FilesCase):
    """Code review slot 3, finding 1: a listing of outbox.d that fails is never
    read as an empty directory. It completes no import pass and leaves
    import_epoch where it was, so a durable handoff keeps its H1 window; other
    entries are delivered meanwhile."""

    def epoch(self):
        return int(self.sql("SELECT value FROM meta WHERE name = 'import_epoch'")[0]["value"])

    def queued(self, text="[status] other"):
        self.server.refuse_login = {"sam"}
        self.post(text, channel="#beta", account="sam")
        self.server.refuse_login = set()

    def test_the_same_handoff_imports_once_the_directory_can_be_read_again(self):
        w = self.probes.view(CLIENT_PID).identity("127.0.0.1:1>127.0.0.1:6667")
        text = "[status] handed"
        self.tx(lambda s: s.create("h" * 32, {"channel": "#alpha", "as": "alp-solver-2", "text": text},
                                   [{"kind": "line", "lines": [[text, False]], "text": text}], w, "post"))
        self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row(text), id="h" * 32, writer=w),
                                 clock=self.clock)  # the writer's durable handoff (H1), then it is gone
        self.probes.world["gone"].add(CLIENT_PID)
        self.queued()
        before = self.epoch()
        fallback = self.state / "outbox.d"
        os.chmod(fallback, 0)
        try:
            for _ in range(3):
                self.flush()
            self.assertEqual(self.epoch(), before, "no pass completed on a listing that failed")
            self.assertEqual(self.entry("h" * 32)["state"], "abandoned", "E8, before its handoff could be read")
            self.assertEqual(self.published_texts(), ["[status] other"], "other entries are delivered meanwhile")
        finally:
            os.chmod(fallback, 0o700)
        self.flush()
        self.assertEqual(self.sql("SELECT * FROM held"), [], "H1's window is intact")
        self.assertEqual(sorted(self.published_texts()), ["[status] handed", "[status] other"])
        self.assertEqual(self.entry("h" * 32)["state"], "done")
        self.assertEqual(self.fallback_files(), [])

    def test_a_listing_that_fails_after_the_import_completes_no_pass(self):
        real, calls = self.fs.listdir, []

        def listdir(path):
            if Path(path) == self.authority.paths.fallback:
                calls.append(path)
                if len(calls) % 2 == 0:  # the second listing of each flush: the one that ends a pass
                    raise PermissionError(13, "permission denied (fake)")
            return real(path)
        self.queued()
        before = self.epoch()
        with mock.patch.object(self.fs, "listdir", listdir):
            for _ in range(3):
                self.flush()
        self.assertEqual(self.epoch(), before)
        self.assertEqual(self.published_texts(), ["[status] other"])
        self.flush()
        self.assertEqual(self.epoch(), before + 1, "the pass completes once the directory can be listed")


class BoundedDrainTests(FilesCase):
    """Code review slot 5's re-check (unbounded drains): staging cleanup and the
    legacy import are bounded per flush and rotate, so neither can keep a flush
    from the rest of its work however much is waiting; all of it is done in time."""

    def test_staging_cleanup_is_bounded_per_flush_and_reaches_every_file(self):
        staging = self.state / "outbox.d" / "tmp"
        for i in range(5):
            (staging / f"test-boot.{CLIENT_PID}.start-1.{i:04x}.tmp").write_bytes(b"{}")
        self.probes.world["gone"].add(CLIENT_PID)
        left = []
        with mock.patch.object(self.ds, "IMPORT_MAX", 2):
            for _ in range(3):
                self.importer.clean_staging()
                left.append(len(list(staging.iterdir())))
        self.assertEqual(left, [3, 1, 0])

    def test_a_large_claim_is_imported_one_bounded_batch_per_flush(self):
        self.legacy(*[self.row(f"[status] row {i}") for i in range(5)], name="outbox.claimed-1-a.jsonl")
        counts = []
        with mock.patch.object(self.ds, "LINES_MAX", 2):
            for _ in range(3):
                self.importer.import_legacy(self.importer.claim_list())
                counts.append(len(self.entries()))
        self.assertEqual(counts, [2, 4, 5])


class ChangedFallbackTests(FilesCase):
    """Design test 47 (code review slot 8, finding 2): a fallback file whose
    removal failed after its import, and whose content is then changed outside
    this design, is held with its exact bytes, and removed only after that
    hold's transaction commits. A failed or cancelled hold keeps the file. The
    original obligation is unchanged."""

    def imported_but_kept(self):
        self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row("[status] original"), id="o" * 32),
                                 clock=self.clock)
        [path] = self.fallback_files()
        real = self.fs.unlink

        def unlink(p):
            if Path(p) == path:
                raise PermissionError(13, "permission denied (fake)")
            return real(p)
        with mock.patch.object(self.fs, "unlink", unlink):
            self.flush()
        self.assertTrue(path.exists(), "imported, but its removal failed")
        return path

    def test_the_changed_bytes_are_held_before_the_file_is_removed(self):
        for changed in (b'{"v": "outbox/2", "text": "changed outside the design"}\n', b"\xff\xfe not text \x00"):
            with self.subTest(changed=changed[:12]):
                self.fresh()
                path = self.imported_but_kept()
                path.write_bytes(changed)
                self.fs.durable(path)
                self.flush()
                self.assertFalse(path.exists(), "removed once the hold committed")
                held = self.sql("SELECT raw, why FROM held WHERE file = ?", path.name)
                self.assertEqual([bytes(h["raw"]) if isinstance(h["raw"], (bytes, memoryview)) else h["raw"]
                                  for h in held], [changed], "the exact changed bytes")
                import hashlib
                key = f"held:{path.name}:{hashlib.sha256(changed).hexdigest()}"
                self.assertEqual(len([a for a in self.alerts() if a["alert_key"] == key]), 1, "one alert")
                self.assertEqual(self.published_texts(), ["[status] original"], "the original obligation, once")
                self.flush()
                self.assertEqual(len(self.sql("SELECT * FROM held")), 1)

    def test_a_power_loss_between_the_hold_and_the_removal(self):
        path = self.imported_but_kept()
        changed = b"changed outside the design"
        path.write_bytes(changed)
        self.fs.durable(path)
        real = self.fs.unlink

        def unlink(p):
            if Path(p) == path:
                raise Crash()  # the hold has committed; the removal never happens
            return real(p)
        with mock.patch.object(self.fs, "unlink", unlink), self.assertRaises(Crash):
            self.importer.import_fallback_files()
        self.fs.power_loss()
        self.restart_bridge()
        self.authority.close()
        self.authority = self.new_authority(fs=self.fs)
        self.importer = self.ds.Importer(self.authority)
        for _ in range(2):
            self.flush()
        self.assertFalse(path.exists(), "removed after the restart")
        held = self.sql("SELECT raw FROM held WHERE file = ?", path.name)
        self.assertEqual([bytes(h["raw"]) if not isinstance(h["raw"], str) else h["raw"] for h in held], [changed],
                         "held once, with its exact bytes")
        self.assertEqual(self.published_texts(), ["[status] original"], "the original obligation, once")

    def test_a_changed_file_keeps_import_fair_and_its_pass_accounting(self):
        """Test 47's last clause: fairness and import_epoch behave as in test 34."""
        path = self.imported_but_kept()
        path.write_bytes(b"changed")
        self.fs.durable(path)
        for n in range(3):
            self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row(f"[status] later {n}"), id=f"{n}" * 32),
                                     clock=self.clock)
        epoch = int(self.tx(lambda s: s.meta("import_epoch")) or 0)
        with mock.patch.object(self.ds, "IMPORT_MAX", 2):
            self.importer.import_fallback_files()
            self.assertEqual(len(self.fallback_files()), 2, "two per flush, the oldest (the changed file) first")
            self.importer.import_fallback_files()
        self.assertEqual(self.fallback_files(), [])
        self.assertEqual(sorted(e["text"] for e in self.entries()),
                         ["[status] later 0", "[status] later 1", "[status] later 2", "[status] original"])
        self.assertEqual(int(self.tx(lambda s: s.meta("import_epoch"))), epoch + 1, "one pass, completed once")
        self.assertEqual(len(self.sql("SELECT * FROM held")), 1)

    def test_a_failed_or_cancelled_hold_keeps_the_file(self):
        for how in ("fails", "cancelled"):
            with self.subTest(how=how):
                self.fresh()
                path = self.imported_but_kept()
                path.write_bytes(b"changed")
                self.fs.durable(path)
                real = self.authority.run_tx
                calls = []

                def run_tx(fn, how=how):
                    if "hold_changed" in getattr(fn, "__code__", None).co_names:
                        calls.append(how)
                        if how == "fails":
                            raise sqlite3.OperationalError("disk I/O error (fake)")
                        return self.ds.CANCELLED
                    return real(fn)
                with mock.patch.object(self.authority, "run_tx", run_tx):
                    self.importer.import_fallback_files()
                self.assertEqual(calls, [how])
                self.assertTrue(path.exists(), "kept: its changed content is not lost")
                self.assertEqual(path.read_bytes(), b"changed")
                self.importer.import_fallback_files()  # a later flush, with the hold working
                self.assertFalse(path.exists())
                self.assertEqual(len(self.sql("SELECT * FROM held")), 1)


class LegacyClaimIntegrityTests(FilesCase):
    """Code review slot 9, findings 3 and 4 (design section 6.3, I2 and I3): a
    closed claim's removal is replayed only while its bytes are the ones its
    import committed; a rewritten one is held with its exact bytes, alerted, and
    kept. Every unterminated last line is held as unreadable at the final read,
    even when it happens to be valid JSON."""

    NAME = "outbox.claimed-9-z.jsonl"

    def final_import(self):
        self.importer.import_legacy([self.NAME], barrier_at=T0 - 1)  # first seen, after this barrier
        self.clock.advance(1)
        self.importer.import_legacy([self.NAME], barrier_at=self.clock())  # the final read, and I3

    def closed_but_kept(self):
        path = self.legacy(self.row("[status] one"), name=self.NAME)
        real = self.fs.unlink

        def unlink(p):
            if Path(p) == path:
                raise PermissionError(13, "permission denied (fake)")
            return real(p)
        with mock.patch.object(self.fs, "unlink", unlink):
            self.final_import()
        self.assertEqual(self.sql("SELECT state FROM imports WHERE name = ?", self.NAME)[0]["state"], "closed")
        self.assertTrue(path.exists(), "closed, but its removal failed")
        return path

    def test_a_rewritten_closed_claim_is_held_with_its_bytes_and_kept(self):
        path = self.closed_but_kept()
        changed = (json.dumps(self.row("[status] rewritten")) + "\n").encode()
        path.write_bytes(changed)
        self.fs.durable(path)
        for _ in range(2):
            self.importer.import_legacy([self.NAME], barrier_at=self.clock())
        self.assertTrue(path.exists(), "a changed claim is never deleted")
        held = self.sql("SELECT raw FROM held WHERE file = ?", self.NAME)
        self.assertEqual([bytes(h["raw"]) if not isinstance(h["raw"], str) else h["raw"] for h in held], [changed])
        key = f"held:{self.NAME}:{hashlib.sha256(changed).hexdigest()}"
        self.assertEqual(len([a for a in self.alerts() if a["alert_key"] == key]), 1, "one alert")
        self.assertEqual([e["text"] for e in self.entries()], ["[status] one"], "never merged")

    def test_an_unchanged_closed_claim_is_still_removed(self):
        path = self.closed_but_kept()
        self.importer.import_legacy([self.NAME], barrier_at=self.clock())
        self.assertFalse(path.exists())
        self.assertEqual(self.sql("SELECT * FROM held"), [])

    def rewritten_after_commit(self, path, changed):
        """A run_tx that rewrites `path` just after the transaction it ran commits."""
        real = self.authority.run_tx

        def run_tx(fn):
            out = real(fn)
            if path.exists() and path.read_bytes() != changed and "hold_changed" not in fn.__code__.co_names:
                path.write_bytes(changed)
            return out
        return mock.patch.object(self.authority, "run_tx", run_tx)

    def test_a_claim_rewritten_between_its_final_import_and_its_removal_is_held(self):
        path = self.legacy(self.row("[status] one"), name=self.NAME)
        self.importer.import_legacy([self.NAME], barrier_at=T0 - 1)
        self.clock.advance(1)
        changed = b"rewritten between the import and the removal"
        with self.rewritten_after_commit(path, changed):
            self.importer.import_legacy([self.NAME], barrier_at=self.clock())
        self.assertTrue(path.exists(), "not removed: its bytes are not the ones committed")
        self.importer.import_legacy([self.NAME], barrier_at=self.clock())
        self.assertTrue(path.exists())
        held = self.sql("SELECT raw FROM held WHERE file = ?", self.NAME)
        self.assertEqual([bytes(h["raw"]) if not isinstance(h["raw"], str) else h["raw"] for h in held], [changed])

    def test_a_fallback_file_rewritten_between_its_import_and_its_removal_is_held(self):
        self.ds.publish_fallback(self.fs, self.authority.paths, dict(self.row("[status] original"), id="o" * 32),
                                 clock=self.clock)
        [path] = self.fallback_files()
        changed = b"rewritten between the import and the removal"
        with self.rewritten_after_commit(path, changed):
            self.importer.import_fallback_files()
        self.assertTrue(path.exists(), "not removed: its bytes are not the ones committed")
        self.importer.import_fallback_files()
        self.assertFalse(path.exists(), "removed once its exact bytes are held (F2(b))")
        held = self.sql("SELECT raw FROM held WHERE file = ?", path.name)
        self.assertEqual([bytes(h["raw"]) if not isinstance(h["raw"], str) else h["raw"] for h in held], [changed])

    def test_an_unterminated_last_line_is_held_even_when_it_is_valid_json(self):
        for tail in (json.dumps(self.row("[status] no newline")).encode(), b"{not json"):
            with self.subTest(tail=tail[:12]):
                self.fresh()
                path = self.legacy(self.row("[status] whole"), name=self.NAME, raw=tail)
                self.final_import()
                self.assertEqual([e["text"] for e in self.entries()], ["[status] whole"], "nothing deliverable from it")
                held = self.sql("SELECT ordinal, raw, why FROM held WHERE file = ?", self.NAME)
                self.assertEqual([(h["ordinal"], h["raw"]) for h in held], [(1, tail.decode(errors="replace"))])
                self.assertIn("unterminated", held[0]["why"])
                self.assertFalse(path.exists(), "the database holds it: the claim is retired")


class StagingIdentityTests(FilesCase):
    """Self-audit, classes A and C: a staging file whose writer identity in its
    name is incomplete or odd is kept while its pid lives, and never stops the flush."""

    def test_an_incomplete_writer_identity_never_proves_its_writer_gone(self):
        staging = self.state / "outbox.d" / "tmp"
        names = [f"test-boot.{CLIENT_PID}.x.0a1b.tmp", f"test-boot.{CLIENT_PID}.None.0a1c.tmp",
                 f"unknown.{CLIENT_PID}.start-1.0a1d.tmp", f"x.{CLIENT_PID}.x.0a1e.tmp"]
        odd = "test-boot.\u00b2\u00b3.x.0a1f.tmp"  # digits to str.isdigit, but not a number
        for n in names + [odd]:
            (staging / n).write_bytes(b"{}")
        self.importer.clean_staging()
        self.assertEqual(sorted(p.name for p in staging.iterdir()), sorted(names + [odd]))
        self.probes.world["gone"].add(CLIENT_PID)
        self.importer.clean_staging()
        self.assertEqual([p.name for p in staging.iterdir()], [odd])


class ChangedClaimTests(FilesCase):
    """Self-audit, classes A and D: a claim changed outside this design is held
    once (one held row, one alert) however many flushes see it, and never unlinked."""

    def test_a_changed_claim_is_held_once(self):
        claim = self.legacy(self.row("[status] one"), name="outbox.claimed-1-a.jsonl")
        self.flush()  # its line imported; not yet quiet, so not retired
        data = claim.read_bytes()
        claim.write_bytes(b" " + data[1:])  # its imported prefix rewritten
        self.fs.durable(claim)
        for _ in range(3):
            self.flush()
        self.assertEqual(len(self.sql("SELECT * FROM held WHERE file = ?", claim.name)), 1)
        self.assertEqual(len([a for a in self.alerts() if a["ref"] == claim.name]), 1)
        self.assertTrue(claim.exists())


class StatusReadTests(FilesCase):
    """Self-audit, class A: pchat status, which every outbox alert points to,
    reports what it cannot read instead of failing on it."""

    def test_status_reads_the_highest_dead_letter_version_of_each_post(self):
        """Code review slot 4, finding 3: a late confirmation's version 2
        supersedes version 1; legacy records stay; the file is never rewritten."""
        import subprocess
        dead = self.state / "dead-letters.jsonl"
        rows = [{"kind": "ring", "msgid": "r1", "channel": "#alpha", "dl_key": "delivery:ring:r1:"},
                {"kind": "post", "id": "e" * 32, "version": 1, "supersedes": None, "channel": "#alpha",
                 "as": "alp-solver-2", "parts": [{"n": 0, "state": "confirmed"}, {"n": 1, "state": "uncertain"}],
                 "last_detail": "undecided for 24 h"},
                {"kind": "post", "id": "e" * 32, "version": 2, "supersedes": 1, "channel": "#alpha",
                 "as": "alp-solver-2", "parts": [{"n": 0, "state": "confirmed"}, {"n": 1, "state": "confirmed"}],
                 "last_detail": "undecided for 24 h"}]
        dead.write_text("".join(json.dumps(r) + "\n" for r in rows))
        before = dead.read_bytes()
        idle = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")  # noqa: E731  (no service is asked)
        with mock.patch.object(subprocess, "run", idle):
            code, out = self.pchat("status")
        self.assertEqual(code, 0, out)
        self.assertIn("dead letters: 2", out)
        posts = [line for line in out.splitlines() if "post to #alpha" in line]
        self.assertEqual(len(posts), 1, out)
        self.assertIn("2 of 2 parts posted", posts[0])
        self.assertEqual(dead.read_bytes(), before, "the stored history is never rewritten")

    def test_status_reports_unreadable_files_and_an_odd_snapshot(self):
        from test_outbox_regressions import pchat
        claim = self.legacy(self.row("[status] old"), name="outbox.claimed-5-z.jsonl")
        (self.state / "outbox-status.json").write_text("[1, 2]")
        fallback = self.state / "outbox.d"
        os.chmod(claim, 0)
        os.chmod(fallback, 0)
        try:
            with self.unavailable():
                lines = pchat.outbox_status()
        finally:
            os.chmod(claim, 0o600)
            os.chmod(fallback, 0o700)
        text = "\n".join(lines)
        self.assertIn("UNAVAILABLE", text)
        self.assertIn("could not be read", text)


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
            for _ in range(3):  # one bounded request per call (slot 5's re-check): 2, 2, then 1 line
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
