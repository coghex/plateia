"""The bridge under maintenance (docs/writer_admission.md sections 4.6, 6.3 and 6.4; design test 9, reduced as
#24's review requires): in `draining` (after the section in progress), `partial`, `full` without a binding, or an
unknown record, the bridge holds. It closes its connection, handles no message, moves no checkpoint, delivers,
imports and flushes nothing, starts no authority (so no outbox.db, WAL or socket is created), writes no status
snapshot, and reads the record again on a timer; a running authority keeps answering read-only requests. After the
release it resumes. The full-bound bootstrap start and the controller's parts of test 9 are #21's. Invented
accounts and channels only."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from admission_fakes import OPERATION, HeldCase, put_record, record_fields, snapshot  # noqa: E402
from outbox_fakes import CFG, Crash  # noqa: E402
from test_bridge import join, privmsg  # noqa: E402

STATES = ("draining", "partial", "full", "unknown")


class BridgeHoldCase(HeldCase):
    def setUp(self):
        super().setUp()
        self.bridge._HOLD_LOGGED[0] = None
        self.addCleanup(self.bridge._HOLD_LOGGED.__setitem__, 0, None)

    def hold_in(self, state):
        """Put the invented maintenance in `state` (through the owner, or by hand for an unknown record)."""
        if state == "unknown":
            put_record(self.paths, raw=b"{")
            return None
        return self.hold(state)

    def log_lines(self):
        path = self.bridge.BRIDGE_LOG
        return path.read_text().splitlines() if path.exists() else []

    def hold_lines(self):
        return [x for x in self.log_lines() if " hold: " in x]

    def no_database(self):
        for name in ("outbox.db", "outbox.db-wal", "outbox.db-shm", "outbox.sock", "outbox.authority.lock"):
            (self.chatlib.STATE_DIR / name).unlink(missing_ok=True)


class HoldTests(BridgeHoldCase):
    def test_a_section_starts_nothing_in_any_state_and_the_hold_is_logged_once_per_change(self):
        for state in STATES:
            with self.subTest(state=state):
                self.fresh()
                self.bridge._HOLD_LOGGED[0] = None
                self.hold_in(state)
                for _ in range(3):
                    with self.assertRaises(self.bridge.Hold) as caught:
                        with self.bridge.admitted("message"):
                            self.fail("the section started")
                    self.assertEqual(caught.exception.record.state, state)
                    self.assertIsNotNone(self.bridge.held_record())
                lines = self.hold_lines()
                self.assertEqual(len(lines), 1, "logged once for three checks of the same record")
                if state == "unknown":
                    self.assertIn("cannot be read", lines[0])
                else:
                    self.assertIn(f"held by {OPERATION}", lines[0])
                    self.assertIn(f"state {state}", lines[0])

    def test_a_change_of_state_is_logged_again_and_the_release_is_logged(self):
        owner = self.hold("draining")
        self.bridge.held_record()
        owner.drain()
        self.bridge.held_record()
        self.bridge.held_record()
        owner.release()
        self.assertIsNone(self.bridge.held_record())
        lines = self.hold_lines()
        self.assertEqual(len(lines), 3)
        self.assertIn("state draining", lines[0])
        self.assertIn("state partial", lines[1])
        self.assertIn("no maintenance record", lines[2])

    def test_no_record_admits_a_section(self):
        with self.bridge.admitted("message") as adm:
            self.assertTrue(adm.admitted)
        self.assertIsNone(self.bridge.held_record())

    def test_a_running_authority_is_kept_and_not_restarted_while_held(self):
        self.hold("partial")
        with mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority):
            self.assertIs(self.bridge.ensure_authority(), self.authority)

    def test_an_announcement_in_draining_is_kept_as_a_held_obligation_the_bridge_treats_as_queued(self):
        self.hold("draining")
        item = {"kind": "ring", "msgid": "m-1", "channel": "#alpha"}
        with mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority):
            kept = self.bridge.announce(CFG, item, "instruction delivered", "delivered")
        self.assertTrue(kept, "Held is a kind of Queued: durable, so announce() is satisfied")
        self.assertEqual(self.server.published, [])
        [row] = self.sink_rows()
        self.assertEqual((row["origin"], row["as"], row["reply_to"]), ("announce", "chatbridge", "m-1"))


class AuthorityStartTests(BridgeHoldCase):
    def test_the_start_is_refused_while_any_record_exists_and_creates_nothing(self):
        for state in STATES:
            with self.subTest(state=state):
                self.fresh()
                self.authority.close()
                self.no_database()
                self.hold_in(state)
                before = snapshot(self.chatlib.STATE_DIR)
                for how in ("start", "launch"):
                    authority = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, mono=self.clock.mono,
                                                  probes=self.probes)
                    with self.assertRaises(self.ds.Unavailable) as caught:
                        getattr(authority, how)()
                    self.assertIn("the authority does not start", str(caught.exception))
                    self.assertIsNone(authority.unavailable and authority.store.con)
                    self.assertFalse(authority.running)
                self.assertEqual(snapshot(self.chatlib.STATE_DIR), before)
                for name in ("outbox.db", "outbox.db-wal", "outbox.sock", "outbox.authority.lock"):
                    self.assertFalse((self.chatlib.STATE_DIR / name).exists(), name)

    def test_the_start_after_the_release_is_as_before(self):
        self.authority.close()
        self.no_database()
        owner = self.hold("partial")
        authority = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, mono=self.clock.mono,
                                      probes=self.probes)
        with self.assertRaises(self.ds.Unavailable):
            authority.start()
        owner.release()
        self.assertEqual(authority.start(), 1)
        self.assertTrue((self.chatlib.STATE_DIR / "outbox.db").exists())
        authority.close()

    def test_ensure_authority_under_a_record_starts_nothing_and_writes_no_snapshot(self):
        self.authority.close()
        self.no_database()
        self.hold("full")
        with mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", None):
            authority = self.bridge.ensure_authority()
            self.assertFalse(authority.running)
            self.assertIn("held by " + OPERATION, authority.unavailable[0])
        self.assertFalse((self.chatlib.STATE_DIR / "outbox-status.json").exists())
        for name in ("outbox.db", "outbox.db-wal", "outbox.sock"):
            self.assertFalse((self.chatlib.STATE_DIR / name).exists(), name)

    def test_case_9_reduced_in_partial_or_full_the_bridge_holds_and_creates_no_database_wal_or_socket(self):
        """The bridge's main loop, with a record in `partial` or in `full` and no binding: it reads the record, sleeps
        its recheck, and neither starts its authority nor connects. (`full` plus a matching binding admitting the
        start is #21's rework against PR #22.)"""
        for state in ("partial", "full"):
            with self.subTest(state=state):
                self.fresh()
                self.authority.close()
                self.no_database()
                self.hold(state)
                slept = []

                def sleep(seconds):
                    slept.append(seconds)
                    raise Crash("one pass of the hold is enough")
                with mock.patch.object(self.bridge.time, "sleep", sleep), \
                        mock.patch.object(self.bridge, "ensure_authority", side_effect=AssertionError("started")), \
                        mock.patch.object(self.chatlib, "login", side_effect=AssertionError("connected")), \
                        self.assertRaises(Crash):
                    self.bridge.main()
                self.assertEqual(slept, [self.ds.HOLD_RECHECK])
                for name in ("outbox.db", "outbox.db-wal", "outbox.db-shm", "outbox.sock", "outbox.authority.lock",
                             "outbox-status.json"):
                    self.assertFalse((self.chatlib.STATE_DIR / name).exists(), name)
                self.assertEqual(len(self.hold_lines()), 1)

    def test_the_main_loop_resumes_after_the_release(self):
        owner = self.hold("partial")
        self.authority.close()
        self.no_database()
        calls = []

        def sleep(seconds):
            calls.append("slept")
            owner.release()  # the owning operation releases while the bridge waits

        def ensure():
            calls.append("authority")

        def run(*args, **kw):
            calls.append("connected")
            raise Crash("stop")
        with mock.patch.object(self.bridge.time, "sleep", sleep), mock.patch.object(self.bridge, "ensure_authority",
                                                                                   ensure), \
                mock.patch.object(self.bridge, "run", run), mock.patch.object(self.chatlib, "load_config",
                                                                                lambda: CFG), \
                self.assertRaises(Crash):
            self.bridge.main()
        self.assertEqual(calls, ["slept", "authority", "connected"])


class StartupRaceTests(BridgeHoldCase):
    """The authority's start, and the cleanup of a start that fails, are one section (writer admission 4.2, 6.3): an
    owner cannot complete its drain while either still touches the database, its WAL or the socket. And closing a
    running authority is a mutation of the same files, so it too waits for a section, or for the release."""

    def fresh_authority(self):
        self.authority.close()
        self.no_database()
        return self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, mono=self.clock.mono, probes=self.probes)

    def test_a_start_that_fails_cleans_up_before_a_drain_can_complete(self):
        authority = self.fresh_authority()
        owner = self.owner()
        events = []
        real_close = self.ds.Store.close

        def run_in_thread():
            owner.acquire()  # maintenance begins while the start is in progress: no lock is needed to publish it
            events.append(("drain during the start", owner.drain(wait=0)["drained"]))
            raise RuntimeError("the thread cannot start")

        def store_close(store):
            events.append(("cleanup", self.record().state, owner.drain(wait=0)["drained"]))
            return real_close(store)
        authority.run_in_thread = run_in_thread
        with mock.patch.object(self.ds.Store, "close", store_close), self.assertRaises(RuntimeError):
            authority.launch()
        self.assertEqual(events, [("drain during the start", False), ("cleanup", "draining", False)],
                         "the cleanup ran while the start's section was still open, so the drain could not land")
        self.assertFalse(authority.running)
        self.assertTrue(authority.released)
        for name in ("outbox.db-wal", "outbox.sock"):
            self.assertFalse((self.chatlib.STATE_DIR / name).exists(), name)
        before = snapshot(self.chatlib.STATE_DIR)
        self.assertTrue(owner.drain(wait=0)["drained"], "once the start is over, the drain lands")
        self.assertEqual(self.record().state, "partial")
        after = snapshot(self.chatlib.STATE_DIR)
        self.assertEqual([k for k in after if before.get(k) != after[k]], ["maintenance.json"],
                         "and it is the only file that changed: no cleanup follows the drain")

    def test_a_start_whose_socket_bind_fails_is_cleaned_up_inside_the_section_too(self):
        authority = self.fresh_authority()
        owner = self.owner()
        seen = []

        def bind():
            owner.acquire()
            seen.append(owner.drain(wait=0)["drained"])
            raise OSError(98, "address in use (fake)")
        authority.bind = bind
        with self.assertRaises(OSError):
            authority.launch()
        self.assertEqual(seen, [False])
        self.assertTrue(authority.released)

    def test_closing_while_maintenance_is_held_is_deferred_and_touches_nothing(self):
        owner = self.hold("partial")
        db, wal = self.chatlib.STATE_DIR / "outbox.db", self.chatlib.STATE_DIR / "outbox.db-wal"
        before = (db.read_bytes(), wal.exists() and wal.read_bytes(), (db.stat().st_mtime_ns))
        self.authority.close()
        self.assertFalse(self.authority.released, "the connection and the lock are kept")
        self.assertFalse(self.authority.running)
        self.assertEqual((db.read_bytes(), wal.exists() and wal.read_bytes(), db.stat().st_mtime_ns), before,
                         "no checkpoint, no WAL removal while maintenance is held")
        owner.release()
        self.authority.close()
        self.assertTrue(self.authority.released)

    def test_closing_in_draining_with_no_open_section_is_deferred_but_inside_one_it_proceeds(self):
        self.hold("draining")
        self.authority.close()
        self.assertFalse(self.authority.released, "a new section does not start in draining")
        self.paths.maintenance.unlink()
        self.authority.close()
        self.assertTrue(self.authority.released)
        self.authority = self.new_authority()
        with self.ds.section(self.paths) as section:  # a section open before the record
            self.assertTrue(section.admitted)
            self.hold("draining")
            self.authority.close()
            self.assertTrue(self.authority.released, "it continues work already begun, under the section's lock")

    def test_ensure_authority_keeps_an_authority_whose_close_was_deferred_and_finishes_after_the_release(self):
        owner = self.hold("partial")
        self.authority.thread = None
        with mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority):
            self.authority.ready = False  # not running: the bridge would replace it
            self.assertIs(self.bridge.ensure_authority(), self.authority, "kept, not dropped with its connection open")
            self.assertFalse(self.authority.released)
            owner.release()
            started = self.bridge.ensure_authority()
            self.assertIsNot(started, self.authority)
            self.assertTrue(self.authority.released)
            started.close()


class ScriptedConnection:
    """One scripted chat-server connection: its lines, then the connection drops. `during` runs between lines."""

    def __init__(self, script):
        self.script, self.sent, self.closed = list(script), [], False

    def send(self, line):
        self.sent.append(line)

    def shutdown(self):
        self.closed = True

    def close(self, reason=""):
        self.closed = True

    def lines(self, timeout=None):
        while self.script:  # what is read is consumed: a later call goes on where this one left off
            item = self.script.pop(0)
            if callable(item):
                item()
                continue
            if isinstance(item, BaseException):
                raise item
            yield item
        from chatlib import ChatError
        raise ChatError("connection dropped")


class RunLoopTests(BridgeHoldCase):
    def run_script(self, script, expect):
        cfgfile = self.chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        self.state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        self.conn = ScriptedConnection(script)
        with mock.patch.object(self.chatlib, "CONFIG_PATH", cfgfile), \
                mock.patch.object(self.chatlib, "login", lambda *a, **k: self.conn), \
                mock.patch.object(self.bridge, "_cmux", lambda *a: (0, "{}", "")), \
                mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority), \
                self.assertRaises(expect):
            self.bridge.run(CFG, *self.state, coverage=self.bridge.Coverage(self.authority))

    def logged(self, channel="#alpha"):
        path = self.chatlib.channel_log(channel)
        return [json.loads(x)["msgid"] for x in path.read_text().splitlines()] if path.exists() else []

    def test_without_a_record_the_loop_handles_messages_as_ever(self):
        self.run_script([join("#alpha"), privmsg("pat", "hello", "m1", channel="#alpha")], self.chatlib.ChatError)
        self.assertEqual(self.logged(), ["m1"])

    def test_with_a_record_the_first_section_holds_and_the_connection_is_closed_with_nothing_written(self):
        for first in ("a join", "a message"):
            with self.subTest(first=first):
                self.fresh()
                self.hold("partial")
                before = snapshot(self.chatlib.STATE_DIR)
                script = [join("#alpha")] if first == "a join" else [privmsg("pat", "hello", "m1", channel="#alpha")]
                self.run_script(script, self.bridge.Hold)
                self.assertTrue(self.conn.closed, "it closed its chat-server connection")
                self.assertEqual(self.logged(), [], "no message was logged")
                self.assertEqual(self.state[3].marks, {}, "no checkpoint moved")
                self.assertEqual(snapshot(self.chatlib.STATE_DIR, skip=("bridge.log", "config.json")),
                                 {k: v for k, v in before.items() if k not in ("bridge.log", "config.json")})

    def test_the_section_in_progress_finishes_and_the_next_does_not_start(self):
        record_appears = lambda: put_record(self.paths, record_fields(state="draining", generation=1))  # noqa: E731
        self.run_script([join("#alpha"), privmsg("pat", "first", "m1", channel="#alpha"), record_appears,
                         privmsg("pat", "second", "m2", channel="#alpha")], self.bridge.Hold)
        self.assertEqual(self.logged(), ["m1"], "the message handled before the record is kept; the next is not")
        self.assertTrue(self.conn.closed)

    def test_after_the_release_the_catch_up_covers_the_interval_from_the_checkpoint(self):
        owner = self.hold("partial")
        self.run_script([join("#alpha"), privmsg("pat", "held", "m1", channel="#alpha")], self.bridge.Hold)
        self.assertEqual(self.logged(), [])
        owner.release()
        cur = self.state[3].get("#alpha")
        self.assertIsNone(cur, "no checkpoint was written, so the catch-up asks for the whole channel's history "
                          "from where the log was complete (here, nothing)")

    def test_an_idle_pass_with_a_record_holds_before_the_delivery_pass(self):
        self.hold("partial")
        with mock.patch.object(self.bridge.Deliveries, "process", side_effect=AssertionError("a delivery pass ran")):
            self.run_script([TimeoutError("idle")], self.bridge.Hold)
        self.assertTrue(self.conn.closed)

    def test_an_idle_pass_without_a_record_runs_the_delivery_pass(self):
        ran = []
        with mock.patch.object(self.bridge.Deliveries, "process", lambda self_, cfg: ran.append(1)):
            self.run_script([TimeoutError("idle")], self.chatlib.ChatError)
        self.assertEqual(ran, [1])


class PingTests(BridgeHoldCase):
    """The outbox PING and its PONG: the coverage update the PONG causes is a write to the authority, so it is one
    section of the bridge. Maintenance beginning while a PING is outstanding holds the bridge there."""

    class Script(ScriptedConnection):
        def __init__(self, case, record_appears):
            super().__init__([])
            self.case, self.phase, self.record_appears = case, 0, record_appears

        def lines(self, timeout=None):
            self.phase += 1
            if self.phase == 1:
                joined = join("#alpha")
                joined["tags"]["time"] = "2026-10-02T09:59:00.000Z"  # the server's time: coverage begins
                yield joined
                raise TimeoutError("idle")  # the loop's pass, and then its PING
            if self.phase == 2:
                self.record_appears()
                ping = next(x for x in reversed(self.sent) if x.startswith("PING :outbox-sync-"))
                yield {"tags": {}, "prefix": "irc", "command": "PONG", "params": ["irc", ping.split(":", 1)[1]]}
            from chatlib import ChatError
            raise ChatError("connection dropped")

    def run_with(self, record_appears, expect):
        cfgfile = self.chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        self.conn = self.Script(self, record_appears)
        ticks = iter(range(10 ** 6))
        with mock.patch.object(self.chatlib, "CONFIG_PATH", cfgfile), \
                mock.patch.object(self.chatlib, "login", lambda *a, **k: self.conn), \
                mock.patch.object(self.bridge, "_cmux", lambda *a: (0, "{}", "")), \
                mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority), \
                mock.patch.object(self.bridge.time, "time", lambda: 1.8e9 + 10 * next(ticks)), \
                self.assertRaises(expect):
            self.bridge.run(CFG, *state, coverage=self.bridge.Coverage(self.authority))

    def through(self):
        return self.rows("SELECT through FROM coverage WHERE channel = '#alpha'")[0]["through"]

    def test_without_maintenance_the_pong_extends_coverage(self):
        self.run_with(lambda: None, self.chatlib.ChatError)
        self.assertIsNotNone(self.through())

    def test_maintenance_beginning_while_a_ping_is_outstanding_holds_the_bridge_at_its_pong(self):
        records = []

        def appears():
            records.append(self.hold("draining"))
        passes = []
        real_process = self.bridge.Deliveries.process

        def process(deliveries, cfg):
            passes.append(self.record().state if self.record().present else "none")
            return real_process(deliveries, cfg)
        with mock.patch.object(self.bridge.Coverage, "sync", side_effect=AssertionError("coverage.sync started")), \
                mock.patch.object(self.bridge.Deliveries, "process", process):
            self.run_with(appears, self.bridge.Hold)
        self.assertEqual(passes, ["none"], "the only delivery pass ran before the record: the hold came at the PONG")
        self.assertIsNone(self.through(), "no coverage update was made after the record began")
        self.assertTrue(self.conn.closed)
        self.assertEqual(self.record().state, "draining", "and the owner was not delayed: the bridge held at once")

    def test_a_coverage_update_outside_any_section_is_cancelled_in_draining_whatever_calls_it(self):
        """The authority's rule, not the call site's: in draining an in-process request runs only for a caller that
        holds a section open, so no bridge write can slip in between the PING and the pass that holds."""
        coverage = self.bridge.Coverage(self.authority)
        coverage.begin("#alpha", None, "msgid=m0")
        self.hold("draining")
        coverage.sync(self.clock())
        self.assertIsNone(self.through())
        index = self.bridge.index_message({"verified": True, "msgid": "m9", "channel": "#alpha", "from": "pat",
                                           "text": "hello", "at": "2026-10-02T10:00:00+00:00"}, self.authority)
        self.assertFalse(index, "an index outside a section fails too")
        self.assertEqual(self.rows("SELECT msgid FROM messages"), [])


class FlushTests(BridgeHoldCase):
    def seed(self):
        """A queued call in outbox.d/ and another in the refusal sink, as invented rows."""
        rows = []
        for sink, text in ((False, "[status] queued before"), (True, "[status] kept for maintenance")):
            row = {"id": ("1" if not sink else "2") * 32, "channel": "#alpha", "as": "alp-solver-2", "text": text,
                   "cont": "", "reply_to": None, "at": "2026-10-09T00:00:00",
                   "writer": {"boot": "x", "pid": 4242, "start": "s", "conn": None}, "origin": "post",
                   "epoch_seen": 0, "attestation": None}
            self.ds.publish_fallback(self.ds.FS(), self.paths, row, clock=self.clock, sink=sink)
            rows.append(row)
        return rows

    def test_a_held_flush_imports_delivers_and_writes_nothing(self):
        self.seed()
        for state in STATES:
            with self.subTest(state=state):
                self.fresh()
                self.seed()
                self.hold_in(state)
                before = snapshot(self.chatlib.STATE_DIR, skip=("bridge.log",))
                with mock.patch.object(self.ds.Importer, "import_fallback_files",
                                       side_effect=AssertionError("imported")), \
                        mock.patch.object(self.ds.Importer, "clean_staging", side_effect=AssertionError("cleaned")), \
                        self.assertRaises(self.bridge.Hold):
                    self.flush()
                self.assertEqual(snapshot(self.chatlib.STATE_DIR, skip=("bridge.log",)), before,
                                 "no import, claim, delivery, export, collection or status snapshot")
                self.assertEqual(self.server.published, [])
                self.assertFalse((self.chatlib.STATE_DIR / "outbox-status.json").exists())

    def test_after_the_release_both_sources_are_imported_and_delivered_once(self):
        self.seed()
        owner = self.hold("partial")
        with self.assertRaises(self.bridge.Hold):
            self.flush()
        owner.release()
        for _ in range(3):
            self.flush()
        texts = sorted(t.split(" (delayed")[0] for t in self.server.texts())
        self.assertEqual(texts, ["[status] kept for maintenance", "[status] queued before"])
        self.assertEqual(self.sink(), [])
        self.assertEqual(self.fallback_files(), [])
        self.assertTrue((self.chatlib.STATE_DIR / "outbox-status.json").exists(), "the first pass after release")

    def test_the_step_in_progress_finishes_and_no_later_step_starts(self):
        ran = []
        real = self.bridge.Flusher.retry_pending

        def retry_pending(flusher):
            ran.append("attestation retry")
            put_record(self.paths, record_fields(state="draining", generation=1))
            return real(flusher)
        with mock.patch.object(self.bridge.Flusher, "retry_pending", retry_pending), \
                mock.patch.object(self.ds.Importer, "clean_staging", side_effect=AssertionError("started")), \
                self.assertRaises(self.bridge.Hold):
            self.flush()
        self.assertEqual(ran, ["attestation retry"])

    def test_delivery_takes_one_section_per_entry_and_holds_between_entries(self):
        for text in ("[status] one", "[status] two"):
            with self.unavailable():
                self.pchat("post", "#alpha", text)
        flusher = self.bridge.Flusher(self.authority)
        flusher.importer.import_fallback_files()
        delivered = []
        real = self.chatlib.deliver_entry

        def deliver(*a, **k):
            result = real(*a, **k)
            delivered.append(result[0])
            put_record(self.paths, record_fields(state="draining", generation=1))  # appears after the first entry
            return result
        with mock.patch.object(self.chatlib, "deliver_entry", deliver), self.assertRaises(self.bridge.Hold):
            flusher.deliver(CFG)
        self.assertEqual(delivered, ["done"], "the entry in progress finished; the next was not started")
        self.assertEqual(len(self.server.published), 1)

    def test_a_status_snapshot_is_never_written_under_a_record(self):
        for state in STATES:
            with self.subTest(state=state):
                self.fresh()
                self.hold_in(state)
                flusher = self.bridge.Flusher(self.authority)
                with self.assertRaises(self.bridge.Hold):
                    flusher.write_status()
                self.bridge.write_unavailable_status(self.authority.paths, "a reason", self.clock())
                self.assertFalse(self.authority.paths.status.exists())

    def test_the_status_snapshot_is_written_when_nothing_is_held(self):
        flusher = self.bridge.Flusher(self.authority)
        flusher.write_status()
        self.assertTrue(self.authority.paths.status.exists())
        self.authority.paths.status.unlink()
        self.bridge.write_unavailable_status(self.authority.paths, "a reason", self.clock())
        self.assertEqual(json.loads(self.authority.paths.status.read_text())["unavailable"]["reason"], "a reason")


if __name__ == "__main__":
    unittest.main()
