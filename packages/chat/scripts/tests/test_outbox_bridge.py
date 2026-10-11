"""The bridge's side of the outbox (docs/chat_outbox_state.md revision 8,
section 9: tests 16, 17, 20, 29 and 36): coverage and indexing on its loop,
bounded setup, its own announcements (the fallback file, E9 after a restart),
and the authority thread itself. Fakes only: a scripted connection, fake
sockets, an injected clock and invented data."""
import contextlib
import fcntl
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from outbox_fakes import CFG, CLIENT_PID, LONG, T0, Crash, OutboxCase, iso  # noqa: E402


class BridgeCase(OutboxCase):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", self.authority),
                  mock.patch.object(self.ds, "LOCK_WAIT", 0.05)):
            p.start()
            self.patches.append(p)

    def restart_bridge(self):
        super().restart_bridge()
        self.bridge.OUTBOX_AUTHORITY = self.authority


class CoverageTests(BridgeCase):
    """Test 16: anything that may have let a message pass unread stops coverage."""

    def span(self, ch="#alpha"):
        rows = self.sql("SELECT * FROM coverage WHERE channel = ?", ch)
        return rows[0] if rows else None

    def test_begin_sync_and_each_way_of_stopping(self):
        cov = self.bridge.Coverage(self.authority)
        cov.begin("#alpha", None, None)
        cov.sync(T0 + 10)
        self.assertEqual(self.span()["through"], T0 + 10)
        cov.lost("#alpha")  # KICK or PART
        cov.sync(T0 + 20)
        self.assertEqual(self.span()["through"], T0 + 10, "a lost channel is not extended")
        cov.begin("#alpha", "msgid=old", "msgid=new")  # a catch-up from another checkpoint: a new span
        self.assertIsNone(self.span()["through"])
        cov.connected()  # a disconnect or a restart: nothing is complete until a catch-up
        cov.sync(T0 + 30)
        self.assertIsNone(self.span()["through"])

    def test_a_catch_up_from_the_stored_mark_carries_the_span_over(self):
        cov = self.bridge.Coverage(self.authority)
        cov.begin("#alpha", None, "msgid=a")
        cov.sync(T0 + 10)
        since = self.span()["since"]
        self.clock.advance(100)
        cov.connected()
        cov.begin("#alpha", "msgid=a", "msgid=b")
        self.assertEqual((self.span()["since"], self.span()["through"]), (since, T0 + 10))

    def test_a_failed_index_stops_coverage_and_holds_the_checkpoint(self):
        from test_bridge import FakeServer as ScriptServer, join, privmsg
        cfgfile = self.chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        cov = self.bridge.Coverage(self.authority)
        server = ScriptServer([join("#alpha"), privmsg("pat", "hello", "m1", channel="#alpha",
                                                       t="2026-10-02T10:00:00Z")])
        with mock.patch.object(self.chatlib, "CONFIG_PATH", cfgfile), \
                mock.patch.object(self.chatlib, "login", lambda *a, **k: server), \
                mock.patch.object(self.bridge, "index_message", lambda entry, authority=None: False), \
                self.assertRaises(self.chatlib.ChatError):
            self.bridge.run(CFG, *state, coverage=cov)
        self.assertNotIn("#alpha", cov.live)
        mark = state[3].marks.get("#alpha")  # a new channel's checkpoint is its join boundary (slot 4, finding 2)
        self.assertTrue(mark.startswith("timestamp="), "the checkpoint stays before the failed message")
        self.assertNotEqual(mark, "msgid=m1")

    def test_the_bridges_loop_indexes_verified_messages(self):
        from test_bridge import FakeServer as ScriptServer, join, privmsg
        cfgfile = self.chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        joined = join("#alpha")
        joined["tags"]["time"] = "2026-10-02T09:59:00.000Z"  # the server's time: coverage begins (V2)
        server = ScriptServer([joined, privmsg("pat", "hello", "m1", channel="#alpha",
                                               t="2026-10-02T10:00:00Z")])
        with mock.patch.object(self.chatlib, "CONFIG_PATH", cfgfile), \
                mock.patch.object(self.chatlib, "login", lambda *a, **k: server), \
                mock.patch.object(self.bridge, "_cmux", lambda *a: (0, "{}", "")), \
                self.assertRaises(self.chatlib.ChatError):
            self.bridge.run(CFG, *state, coverage=self.bridge.Coverage(self.authority))
        self.assertEqual([m["msgid"] for m in self.sql("SELECT * FROM messages")], ["m1"])
        self.assertIsNotNone(self.span())


class CatchUpIndexFailureTests(BridgeCase):
    """Code review slot 1, finding 1: a message whose index fails during a
    catch-up invalidates that catch-up; its checkpoint stays before the
    message until a replay indexes it, and no coverage is restored meanwhile."""

    class OneShot:
        """One scripted connection that resumes where a read left off, then drops."""

        def __init__(self, script):
            self.script, self.sent = list(script), []

        def send(self, line):
            self.sent.append(line)

        def lines(self, timeout=None):
            import chatlib
            while self.script:
                yield self.script.pop(0)
            raise chatlib.ChatError("connection dropped")

    def run_script(self, script, state, cov, failing=(), clock=None):
        cfgfile = self.chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        server = self.OneShot(script)
        real = self.bridge.index_message

        def index(entry, authority=None):
            return False if entry.get("msgid") in failing else real(entry, authority)
        patches = [mock.patch.object(self.chatlib, "CONFIG_PATH", cfgfile),
                   mock.patch.object(self.chatlib, "login", lambda *a, **k: server),
                   mock.patch.object(self.bridge, "_cmux", lambda *a: (0, "{}", "")),
                   mock.patch.object(self.bridge, "index_message", index)]
        if clock:
            patches.append(mock.patch.object(self.bridge.time, "time", clock))
        for p in patches:
            p.start()
        try:
            with self.assertRaises(self.chatlib.ChatError):
                self.bridge.run(CFG, *state, coverage=cov)
        finally:
            for p in reversed(patches):
                p.stop()
        return server

    def setup_channel(self):
        from test_bridge import privmsg
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        self.bridge.handle(privmsg("pat", "before", "m0", channel="#alpha", t="2026-10-02T09:00:00Z"), CFG,
                           *state[:3])
        return state, self.bridge.Coverage(self.authority)

    def test_a_failed_index_mid_catch_up_never_restores_completeness_or_moves_the_checkpoint(self):
        from test_bridge import batch, in_batch, join, privmsg
        state, cov = self.setup_channel()
        self.run_script([join("#alpha"), batch("b1", "#alpha"),
                         in_batch("b1", privmsg("sam", "missed", "m1", channel="#alpha", t="2026-10-02T10:00:00Z")),
                         batch("b1", None)], state, cov, failing={"m1"})
        self.assertNotIn("#alpha", cov.live)
        self.assertEqual(self.sql("SELECT * FROM coverage"), [], "no span may begin from an invalid catch-up")
        self.assertEqual(state[3].marks.get("#alpha"), "msgid=m0", "the checkpoint stays before the message")
        self.server.plan = ["error"]  # an uncertain attempt with finality, in that channel
        self.post("missed", account="sam")
        self.server.published.clear()
        self.clock.advance(60)
        self.flush()
        self.assertEqual(self.states(), ["uncertain"], "absence is never proved without a complete record")

    def test_a_successful_replay_restores_completeness_from_the_preserved_checkpoint(self):
        from test_bridge import batch, in_batch, join, privmsg
        state, cov = self.setup_channel()
        ticks = iter(range(10 ** 6))
        clock = lambda: 1_000_000.0 + 20 * next(ticks)  # noqa: E731  (each check is 20 s later: LIST is due)
        missed = privmsg("sam", "missed", "m1", channel="#alpha", t="2026-10-02T10:00:00Z")
        server = self.run_script([join("#alpha"), batch("b1", "#alpha"), in_batch("b1", dict(missed)),
                                  batch("b1", None)], state, cov, failing={"m1"}, clock=clock)
        asked = [x for x in server.sent if x.startswith("CHATHISTORY")]
        self.assertEqual(asked, ["CHATHISTORY AFTER #alpha msgid=m0 1000"] * 2,
                         "the join's catch-up, then a replay from the preserved mark at the next LIST")
        self.run_script([join("#alpha"), batch("b2", "#alpha"),
                         in_batch("b2", privmsg("sam", "missed", "m1", channel="#alpha", t="2026-10-02T10:00:00Z")),
                         batch("b2", None)], state, cov)
        self.assertIn("#alpha", cov.live)
        self.assertEqual(state[3].marks.get("#alpha"), "msgid=m1")


class ServerEvidenceTests(CatchUpIndexFailureTests):
    """Code review slot 4, findings 1 and 2 (self-audit: server fields, and
    first-time paths). Only the server's own time, msgid and account are
    evidence: a message without them is a failed index, which stops coverage.
    A brand-new channel gets a durable replay boundary when it is joined, so a
    failed first index is replayed, on this connection and after a reconnect,
    and never skipped."""

    def test_a_late_message_without_server_time_never_proves_absence(self):
        from test_bridge import batch, join, privmsg
        state, cov = self.setup_channel()  # a logged channel, with its checkpoint
        self.run_script([join("#alpha"), batch("b0", "#alpha"), batch("b0", None)], state, cov)  # covered from now
        self.clock.advance(10)
        self.server.plan = ["error"]  # published, then an error reply: uncertain, with finality
        self.post("[status] late")
        self.assertEqual(self.states(), ["uncertain"])
        late = privmsg("alp-solver-2", "[status] late", "m1", channel="#alpha")
        del late["tags"]["time"]  # received late, and without its server time
        self.run_script([join("#alpha"), batch("b1", "#alpha"), batch("b1", None), late], state, cov)
        self.assertEqual(self.sql("SELECT since FROM coverage")[0]["since"], T0, "carried over the reconnect")
        cov.sync(self.clock() + 1000)  # a sync after it: only channels still complete are carried
        self.server.published.clear()
        self.clock.advance(60)
        self.flush()
        self.assertEqual(self.states(), ["uncertain"], "absence is never proved from a local receipt time")
        self.assertEqual(self.server.published, [], "nothing is sent again")
        self.assertEqual(self.sql("SELECT * FROM messages"), [], "local receipt time is not evidence")

    def test_a_failed_first_index_without_a_checkpoint_is_replayed_and_never_skipped(self):
        from test_bridge import batch, in_batch, join, privmsg
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        cov = self.bridge.Coverage(self.authority)
        ticks = iter(range(10 ** 6))
        clock = lambda: 1_000_000.0 + 20 * next(ticks)  # noqa: E731  (each check is 20 s later: LIST is due)
        first = privmsg("sam", "first", "m1", channel="#new", t="2026-10-02T10:00:00.000Z")
        joined = join("#new")
        joined["tags"]["time"] = "2026-10-02T09:59:00.000Z"
        server = self.run_script([joined, dict(first, tags=dict(first["tags"]))], state, cov,
                                 failing={"m1"}, clock=clock)
        asked = [x for x in server.sent if x.startswith("CHATHISTORY")]
        self.assertEqual(len(asked), 1, "replayed on the same connection")
        self.assertTrue(asked[0].startswith("CHATHISTORY AFTER #new timestamp="), asked)
        self.assertNotIn("#new", cov.live, "coverage stops at the failure")
        self.assertEqual(self.sql("SELECT through FROM coverage WHERE channel = '#new'")[0]["through"], None)
        state = state[:3] + (self.bridge.Checkpoints(),)  # a reconnect, after a restart: from the durable file
        server = self.run_script([joined, batch("b2", "#new"), in_batch("b2", dict(first, tags=dict(first["tags"]))),
                                  batch("b2", None)], state, cov)
        [again] = [x for x in server.sent if x.startswith("CHATHISTORY")]
        self.assertEqual(again, asked[0], "the reconnect asks from the same boundary, not after the failed message")
        self.assertEqual([m["msgid"] for m in self.sql("SELECT msgid FROM messages")], ["m1"], "indexed at last")


class JoinWithoutServerTimeTests(CatchUpIndexFailureTests):
    """Round A follow-up (class: local substitutes): a replay boundary or
    coverage is never derived from local time. A JOIN without a readable server
    time gives its channel no coverage, and a boundary from before anything it
    can hold, so a failed first index is never skipped, however far ahead the
    local clock runs."""

    EPOCH = "CHATHISTORY AFTER #new timestamp=1970-01-01T00:00:00.000Z 1000"

    def test_a_failed_first_index_after_an_untimed_join_is_never_skipped_and_proves_nothing(self):
        from test_bridge import batch, in_batch, join, privmsg
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        cov = self.bridge.Coverage(self.authority)
        ticks = iter(range(10 ** 6))
        ahead = lambda: 4_000_000_000.0 + 20 * next(ticks)  # noqa: E731  (a local clock far ahead of the server)
        first = privmsg("sam", "first", "m1", channel="#new", t="2026-10-02T10:00:00.000Z")
        with mock.patch.object(self.bridge.time, "time", ahead):
            server = self.run_script([join("#new"), dict(first, tags=dict(first["tags"]))], state, cov,
                                     failing={"m1"})
        self.assertEqual([x for x in server.sent if x.startswith("CHATHISTORY")], [self.EPOCH])
        self.assertEqual(self.sql("SELECT * FROM coverage WHERE channel = '#new'"), [], "no coverage from local time")
        self.assertNotIn("#new", cov.live)
        state = state[:3] + (self.bridge.Checkpoints(),)  # a reconnect, after a restart
        with mock.patch.object(self.bridge.time, "time", ahead):
            server = self.run_script([join("#new"), batch("b2", "#new"),
                                      in_batch("b2", dict(first, tags=dict(first["tags"]))), batch("b2", None)],
                                     state, cov)
        self.assertEqual([x for x in server.sent if x.startswith("CHATHISTORY")], [self.EPOCH])
        self.assertEqual([m["msgid"] for m in self.sql("SELECT msgid FROM messages")], ["m1"], "indexed at last")

    def test_a_timed_join_takes_its_boundary_from_the_server_alone(self):
        from test_bridge import join
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        cov = self.bridge.Coverage(self.authority)
        timed = join("#new")
        timed["tags"]["time"] = "2026-10-02T10:00:00.000Z"
        with mock.patch.object(self.bridge.time, "time", lambda: 4_000_000_000.0):
            self.run_script([timed], state, cov)
        self.assertEqual(state[3].marks["#new"], "timestamp=2026-10-02T09:59:55.000Z", "A seconds before the join")
        self.assertEqual(len(self.sql("SELECT * FROM coverage WHERE channel = '#new'")), 1)


class NoThread:
    """A thread the platform cannot start."""

    def __init__(self, *a, **k):
        pass

    def start(self):
        raise RuntimeError("can't start new thread (fake)")


class StartupCase(BridgeCase):
    """The bridge's own start on the sandboxed state: the case's authority is
    closed first, so the bridge's ensure_authority takes the lock itself."""

    def setUp(self):
        super().setUp()
        self.authority.close()
        for p in (mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", None),
                  mock.patch.object(self.bridge, "log", lambda m: None)):
            p.start()
            self.patches.append(p)

    def tearDown(self):
        if self.bridge.OUTBOX_AUTHORITY is not None:
            self.bridge.OUTBOX_AUTHORITY.close()
        super().tearDown()

    def lock_free(self):
        with open(self.chatlib.STATE_DIR / "outbox.authority.lock", "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(f, fcntl.LOCK_UN)
            return True

    @staticmethod
    def open_fds():
        return len(os.listdir("/dev/fd"))

    def assert_nothing_held(self, authority):
        self.assertFalse(authority.running)
        self.assertIsNone(authority.store.con, "the database is closed")
        self.assertIsNone(authority.listener, "no socket is left bound")
        self.assertTrue(self.lock_free(), "the authority lock is released")
        self.assertIsNotNone(authority.unavailable)

    def assert_running_under_the_lock(self, authority):
        self.assertTrue(authority.running)
        self.assertFalse(self.lock_free(), "it serves only under the authority lock")
        self.assertIsNotNone(authority.thread)


class FailedBindTests(StartupCase):
    """Code review slot 1, finding 3: a start whose bind fails is retried, whole, at the next pass."""

    def test_a_failed_bind_is_retried_and_the_thread_started_at_the_next_pass(self):
        attempts, threads = [], []

        def bind(authority_self):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("address in use (fake)")
            return False
        real = self.ds.Authority.run_in_thread

        def run_in_thread(authority_self):
            threads.append(1)
            return real(authority_self)
        with mock.patch.object(self.ds.Authority, "bind", bind), \
                mock.patch.object(self.ds.Authority, "run_in_thread", run_in_thread):
            self.bridge.ensure_authority()
            second = self.bridge.ensure_authority()
            self.assertEqual((len(attempts), len(threads)), (2, 1))
            self.assertIsNone(second.unavailable)
            self.assert_running_under_the_lock(second)


class StartupCleanupTests(StartupCase):
    """Code review slot 2, finding 2 (class B): a start is all or nothing. A
    failure at any step (opening, the database files' durability, the bind, the
    thread) leaves nothing open and the lock free; running is true only after a
    complete start; the next pass starts again from the lock."""

    def test_a_failed_sync_of_the_database_files_releases_everything(self):
        real, fails = self.ds.FS.durable, []

        def durable(fs, path):
            if Path(path).name.startswith("outbox.db") and not fails:
                fails.append(path)
                raise OSError(5, "sync failed (fake)")
            return real(fs, path)
        with mock.patch.object(self.ds.FS, "durable", durable), \
                mock.patch.object(self.ds.Authority, "bind", lambda a: False):
            first = self.bridge.ensure_authority()
            self.assert_nothing_held(first)
            second = self.bridge.ensure_authority()
            self.assert_running_under_the_lock(second)
        self.assertEqual(len(fails), 1)

    def test_a_failed_bind_or_thread_start_releases_everything(self):
        for step in ("bind", "thread"):
            with self.subTest(step=step):
                def bind(a, step=step):
                    if step == "bind":
                        raise OSError(48, "address in use (fake)")
                    return False
                fds = self.open_fds()
                with mock.patch.object(self.ds.Authority, "bind", bind), \
                        mock.patch.object(self.ds.threading, "Thread", NoThread if step == "thread"
                                          else self.ds.threading.Thread):
                    first = self.bridge.ensure_authority()
                self.assert_nothing_held(first)
                self.assertEqual(self.open_fds(), fds, "no file, socket or pipe of the failed start is left open")
                with mock.patch.object(self.ds.Authority, "bind", lambda a: False):
                    second = self.bridge.ensure_authority()
                self.assert_running_under_the_lock(second)
                second.close()
                self.bridge.OUTBOX_AUTHORITY = None

    def test_a_dead_authority_thread_is_not_running_and_is_replaced(self):
        with mock.patch.object(self.ds.Authority, "bind", lambda a: False):
            first = self.bridge.ensure_authority()
            self.assert_running_under_the_lock(first)
            first.stopping = True  # its loop ends, as if the thread had died
            first.thread.join(5)
            self.assertFalse(first.running)
            second = self.bridge.ensure_authority()
            self.assert_running_under_the_lock(second)
            self.assertIsNot(first, second)


class FlushContainmentTests(BridgeCase):
    """Self-audit, class A: a failure in one step of a flush, or on one item of a
    step, never stops the steps after it or the items beside it (R6)."""

    def queued(self, text="[status] other"):
        self.server.refuse_login = {"sam"}
        self.post(text, channel="#beta", account="sam")
        self.server.refuse_login = set()

    def published(self):
        return [m["text"].split(" (delayed")[0] for m in self.server.published if m["text"]]

    def status_written(self):
        path = self.authority.paths.status
        return path.exists() and json.loads(path.read_text()).get("written_at") == self.clock()

    def test_each_failing_step_leaves_the_others_running(self):
        flusher = ("retry_pending", "end_gone_attempts", "abandon_or_adopt", "reconcile", "export_dead_letters",
                   "export_alerts")
        importer = ("clean_staging", "import_fallback_files", "claim_legacy", "import_legacy")
        store = ("expire", "collect")
        for step in flusher + importer + store:
            with self.subTest(step=step):
                self.fresh()
                self.queued()
                target = (self.bridge.Flusher if step in flusher else self.ds.Importer if step in importer
                          else self.ds.Store)

                def boom(*a, **k):
                    raise sqlite3.OperationalError("disk I/O error (fake)")
                with mock.patch.object(target, step, boom), mock.patch.object(self.bridge, "log", lambda m: None):
                    self.flush()
                self.assertEqual(self.published(), ["[status] other"])
                self.assertTrue(self.status_written())

    def test_one_failing_reconciliation_key_leaves_the_others_decided(self):
        self.queued("[status] first")
        self.queued("[status] second")
        real, calls = self.ds.Store.reconcile, []

        def flaky(store, account, channel, text, log=lambda m: None):
            calls.append(text)
            if len(calls) == 1:
                raise RecursionError("maximum recursion depth exceeded (fake)")
            return real(store, account, channel, text, log=log)
        with mock.patch.object(self.ds.Store, "reconcile", flaky), \
                mock.patch.object(self.ds.Store, "reconcile_keys", lambda s: [("sam", "#beta", "k1"), ("sam", "#beta", "k2")]), \
                mock.patch.object(self.bridge, "log", lambda m: None):
            self.flush()
        self.assertEqual(calls, ["k1", "k2"])
        self.assertEqual(sorted(self.published()), ["[status] first", "[status] second"])

    def test_a_writer_with_an_impossible_pid_never_stops_a_flush(self):
        self.server.plan = ["ok", "crash"]
        with contextlib.suppress(Crash):
            self.pchat("post", "#alpha", LONG)
        [a] = self.attempts(state="writing")
        w = dict(json.loads(a["writer"]), boot="boot-a", pid=2 ** 40, start="start-a")
        self.tx(lambda s: s.con.execute("UPDATE attempts SET writer = ?", (self.ds.jdump(w),)))
        self.tx(lambda s: s.con.execute("UPDATE entries SET owner_writer = ?", (self.ds.jdump(w),)))
        self.queued()
        self.server.published.clear()
        host = self.ds.HostProbes()
        with mock.patch.object(self.ds, "_boot_id", lambda: "boot-a"), \
                mock.patch.object(self.ds, "_process_start", lambda pid: "start-a"), \
                mock.patch.object(self.ds, "_tcp_ports", lambda pid: None):
            self.bridge._FLUSHERS.clear()
            self.bridge.flush_outbox(CFG, authority=self.authority, probes=host)
        self.assertEqual(self.published(), ["[status] other"])
        self.assertEqual(len(self.attempts(state="writing")), 1, "an identity that proves nothing ends nothing")

    def test_an_export_that_cannot_be_written_stays_due_and_blocks_nothing(self):
        self.server.plan = ["fail"]
        self.post("[status] refused")  # terminal: its dead letter is due
        self.queued()
        self.server.published.clear()
        self.server.plan = []
        dead = self.chatlib.STATE_DIR / "dead-letters.jsonl"
        dead.mkdir()  # the sink cannot be written
        try:
            with mock.patch.object(self.bridge, "log", lambda m: None):
                self.flush()
        finally:
            dead.rmdir()
        self.assertEqual(self.published(), ["[status] other"])
        self.assertTrue(self.status_written())
        self.flush()
        self.assertEqual(len(self.dead_letters()), 1, "exported at the next flush")


class DeliveryContainmentTests(BridgeCase):
    """Self-audit, class A, on the pending deliveries: one item that cannot be
    dead-lettered or recorded stays pending and blocks no other item; an item
    leaves only after its ledger line is durable (section 3.6)."""

    def items(self):
        first = {"kind": "push", "msgid": "m1", "channel": "#alpha", "sender": "sam", "project": None,
                 "attempt": len(self.bridge.BACKOFF), "next_at": 0, "text": "one"}
        return [first, dict(first, msgid="m2", attempt=0, text="two")]

    def process(self, deliveries):
        outcomes = {"m1": (False, "no route (fake)"), "m2": (True, "pushed (fake)")}
        with mock.patch.object(self.bridge, "push_owner", lambda cfg, item: outcomes[item["msgid"]]), \
                mock.patch.object(self.bridge, "log", lambda m: None):
            deliveries.process(CFG)

    def test_a_dead_letter_that_cannot_be_written_blocks_nothing(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = self.items()
        self.bridge.DEAD_LETTERS.mkdir(parents=True)
        self.addCleanup(self.bridge.DEAD_LETTERS.rmdir)
        self.process(deliveries)
        self.assertEqual([i["msgid"] for i in deliveries.items], ["m1"], "m1 stays pending; m2 is done")

    def test_an_item_that_fails_in_any_way_blocks_no_other(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = self.items()

        def push(cfg, item):
            if item["msgid"] == "m1":
                raise RuntimeError("cmux answered something unexpected (fake)")
            return True, "pushed (fake)"
        with mock.patch.object(self.bridge, "push_owner", push), mock.patch.object(self.bridge, "log", lambda m: None):
            deliveries.process(CFG)
        self.assertEqual([i["msgid"] for i in deliveries.items], ["m1"], "m1 stays pending; m2 is done")

    def test_an_item_leaves_only_once_its_ledger_line_is_durable(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = self.items()[:1]
        real = self.bridge.Deliveries._record

        def record(d, item, ok, detail):
            if detail.startswith("dead-lettered"):  # the dead letter is durable; its ledger line cannot be written
                raise OSError(28, "no space left on device (fake)")
            return real(d, item, ok, detail)
        with mock.patch.object(self.bridge.Deliveries, "_record", record), contextlib.suppress(OSError):
            self.process(deliveries)
        self.assertEqual([i["msgid"] for i in deliveries.items], ["m1"])
        self.assertEqual(len(self.dead_letters()), 1)
        self.process(deliveries)
        self.assertEqual(deliveries.items, [], "it leaves at the next pass, its dead letter not duplicated")
        self.assertEqual(len(self.dead_letters()), 1)


class IndexTimeTests(BridgeCase):
    """Self-audit, class C: a verified message whose server time cannot be read
    is not indexed, so it stops the channel's coverage instead of passing as seen."""

    def test_server_fields_missing_or_malformed_are_never_replaced_by_local_ones(self):
        """Code review slot 4's re-check, through handle(): no server time, a
        time without its zone, no msgid, or an empty account is never evidence."""
        from test_bridge import privmsg
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        cases = {"no time": lambda m: m["tags"].pop("time"),
                 "no zone": lambda m: m["tags"].update(time="2026-10-02T10:00:00"),
                 "no msgid": lambda m: m["tags"].pop("msgid")}
        for name, spoil in cases.items():
            with self.subTest(name=name):
                m = privmsg("sam", f"hi {name}", f"m-{name}", channel="#alpha", t="2026-10-02T10:00:00.000Z")
                spoil(m)
                self.assertIs(self.bridge.handle(m, CFG, *state), False, "a failed index: coverage stops")
        m = privmsg("sam", "hi", "m-empty", channel="#alpha", t="2026-10-02T10:00:00.000Z")
        m["tags"]["account"] = ""
        self.bridge.handle(m, CFG, *state)
        self.assertEqual(self.sql("SELECT * FROM messages"), [], "no message is indexed under a local substitute")

    def test_an_unreadable_time_is_a_failed_index(self):
        for at in ("not a time", None, ""):
            with self.subTest(at=at):
                entry = {"verified": True, "msgid": f"m-{at}", "channel": "#alpha", "from": "sam", "text": "hi",
                         "at": at}
                self.assertFalse(self.bridge.index_message(entry, self.authority))
        self.assertEqual(self.sql("SELECT * FROM messages"), [])


class FakeSock:
    """A socket that answers nothing useful: only unrelated lines, forever."""

    def __init__(self, script=()):
        self.script = list(script)
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def sendall(self, data):
        pass

    def recv(self, n):
        if self.script:
            return self.script.pop(0).encode()
        time.sleep(0.01)
        return b":irc NOTICE * :unrelated traffic\r\n"

    def close(self):
        pass

    def shutdown(self, how):
        pass


class BoundedSetupTests(BridgeCase):
    """Test 20: every setup phase fails at its absolute deadline, whatever unrelated traffic arrives."""

    def connect(self, script, account="alp-solver-2", wait=0.2):
        with mock.patch.object(self.chatlib.socket, "create_connection", lambda *a, **k: FakeSock(script)):
            return self.chatlib.Connection(account, "x", cfg=CFG, caps=("draft/multiline",), wait=wait)

    def test_each_login_phase_times_out_at_its_deadline(self):
        phases = {
            "capability negotiation": [],
            "SASL": [":irc CAP * LS :sasl draft/multiline\r\n", ":irc CAP * ACK :sasl draft/multiline\r\n"],
            "the welcome": [":irc CAP * LS :sasl\r\n", ":irc CAP * ACK :sasl\r\n", "AUTHENTICATE +\r\n",
                            ":irc 903 x :ok\r\n"],
        }
        for phase, script in phases.items():
            with self.subTest(phase=phase):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    self.connect(script)
                self.assertLess(time.monotonic() - started, 2)

    def test_channel_creation_times_out_at_its_deadline(self):
        class Conn:
            deadline = None
            buf = b""

            def __init__(self):
                self.sock = FakeSock([":irc 353 x = #alpha :someone\r\n", ":irc 366 x #alpha :end\r\n"])

            def send(self, line):
                pass
            lines = self.chatlib.Connection.lines
        started = time.monotonic()
        with self.assertRaises((TimeoutError, self.chatlib.ChatError)):
            self.chatlib.ensure_channel(Conn(), "#alpha", wait=0.2)
        self.assertLess(time.monotonic() - started, 2)

    def test_another_entry_in_the_same_flush_still_gets_its_attempt(self):
        self.server.refuse_login = {"alp-solver-2", "sam"}
        self.post("[status] stuck")
        self.post("[status] other", channel="#beta", account="sam")
        self.server.refuse_login = set()
        real = self.server.login

        def login(account, *a, **k):
            if account == "alp-solver-2":
                raise TimeoutError("setup deadline passed")
            return real(account, *a, **k)
        with mock.patch.object(self.chatlib, "login", login):
            self.flush()
        self.assertEqual([m["account"] for m in self.server.published], ["sam"])


class DeliveredAnnouncementTests(BridgeCase):
    """Code review slot 6, finding 2 (C5): an interrupt's "delivered"
    announcement that nothing durable recorded stays pending and is raised
    again at later passes until it is durable; the interrupt is never repeated,
    and acceptance and dead-lettering keep working."""

    def item(self):
        return {"kind": "ring", "msgid": "m-int", "channel": "#alpha", "sender": "pat", "project": "alpha",
                "target": "alp-solver-2", "phase": "deliver", "interrupt": True, "needs_ack": True, "attempt": 0,
                "next_at": 0, "text": "!! stop and read #alpha"}

    def passes(self, deliveries, n, *, durable):
        rings = []

        def ring(cfg, item):
            rings.append(item["msgid"])
            return True, "delivered (fake)"
        with mock.patch.object(self.bridge, "ring_agent", ring), mock.patch.object(self.bridge, "log", lambda m: None):
            for _ in range(n):
                if durable:
                    deliveries.process(CFG)
                else:
                    with mock.patch.object(self.bridge, "OUTBOX_AUTHORITY", None), \
                            mock.patch.object(self.ds, "publish_fallback", side_effect=OSError(28, "no space (fake)")):
                        deliveries.process(CFG)
        return rings

    def announcements(self):
        return [m for m in self.server.published if m["text"] and "instruction delivered" in m["text"]]

    def test_an_unrecorded_announcement_is_raised_again_until_durable_without_a_second_interrupt(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = [self.item()]
        rings = self.passes(deliveries, 3, durable=False)
        self.assertEqual(rings, ["m-int"], "the interrupt is delivered once")
        self.assertEqual(self.announcements(), [], "nothing could be recorded yet")
        self.assertEqual(deliveries.items[0]["phase"], "accept", "it still waits for its acknowledgement")
        rings += self.passes(deliveries, 2, durable=True)
        self.assertEqual(rings, ["m-int"], "never interrupted again")
        self.assertEqual(len(self.announcements()), 1, "announced once, at recovery")
        self.assertEqual([i["phase"] for i in deliveries.items], ["accept"])
        saved = self.bridge.Deliveries()  # what a restart reads back
        self.assertEqual([i["msgid"] for i in saved.items], ["m-int"])

    def test_an_announcement_that_raises_after_delivery_is_owed_and_the_interrupt_rung_once(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = [self.item()]
        real, raised = self.bridge.announce, []

        def announce(cfg, item, text, reason):
            if not raised:
                raised.append(reason)
                raise OSError(5, "the authority's reply could not be read (fake)")
            return real(cfg, item, text, reason)
        with mock.patch.object(self.bridge, "announce", announce):
            rings = self.passes(deliveries, 3, durable=True)
        self.assertEqual(raised, ["delivered"])
        self.assertEqual(rings, ["m-int"], "the interrupt is rung exactly once")
        self.assertEqual(deliveries.items[0]["phase"], "accept")
        self.assertEqual(len(self.announcements()), 1, "the owed announcement is made durable at a later pass")

    def ledger(self):
        path = self.bridge.DELIVERIES
        rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
        return [r for r in rows if r.get("msgid") == "m-int" and r.get("ok") is True]

    def test_no_failure_after_delivery_ever_rings_the_interrupt_again(self):
        """Code review slot 7: once ring_agent has delivered the interrupt, a
        failing ledger line, announcement or state save leaves it delivered;
        only that owed work is retried, across passes and a restart."""
        real_record, real_announce, real_save = (self.bridge.Deliveries._record, self.bridge.announce,
                                                 self.bridge.Deliveries._save)
        for failure in ("ledger raises", "announcement raises", "announcement fails", "save raises"):
            with self.subTest(failure=failure):
                self.fresh()
                failed = []

                def record(d, item, ok, detail):
                    if failure == "ledger raises" and ok is True and not failed:
                        failed.append(detail)
                        raise OSError(28, "no space left on device (fake)")
                    return real_record(d, item, ok, detail)

                def announce(cfg, item, text, reason):
                    if failure.startswith("announcement") and not failed:
                        failed.append(reason)
                        if failure == "announcement raises":
                            raise OSError(5, "the authority's reply could not be read (fake)")
                        return False
                    return real_announce(cfg, item, text, reason)

                def save(d):
                    if failure == "save raises" and any(i.get("phase") == "accept" for i in d.items) and not failed:
                        failed.append("save")
                        raise OSError(28, "no space left on device (fake)")
                    return real_save(d)
                deliveries = self.bridge.Deliveries()
                deliveries.items = [self.item()]
                deliveries._save()  # as queued: "deliver", durably
                with mock.patch.object(self.bridge.Deliveries, "_record", record), \
                        mock.patch.object(self.bridge, "announce", announce), \
                        mock.patch.object(self.bridge.Deliveries, "_save", save):
                    rings = self.passes(deliveries, 3, durable=True)
                    rings += self.passes(self.bridge.Deliveries(), 2, durable=True)  # after a restart
                self.assertEqual(len(failed), 1, "the failure was injected once")
                self.assertEqual(rings, ["m-int"], "the interrupt is rung exactly once")
                self.assertEqual(len(self.ledger()), 1, "its ledger line is written once")
                self.assertEqual(len(self.announcements()), 1, "its announcement is made once")
                self.assertEqual([i["phase"] for i in self.bridge.Deliveries().items], ["accept"], "saved as delivered")

    def test_a_failure_before_delivery_is_retried_as_before(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = [self.item()]
        calls = []

        def ring(cfg, item):
            calls.append(item["msgid"])
            if len(calls) == 1:
                raise OSError(61, "cmux did not answer (fake)")  # nothing delivered: it is rung again
            return True, "delivered (fake)"
        with mock.patch.object(self.bridge, "ring_agent", ring), mock.patch.object(self.bridge, "log", lambda m: None):
            for _ in range(3):
                deliveries.process(CFG)
        self.assertEqual(calls, ["m-int", "m-int"], "rung again only because the first was never delivered")
        self.assertEqual(len(self.announcements()), 1)

    def test_an_acknowledgement_before_recovery_keeps_the_announcement_owed(self):
        deliveries = self.bridge.Deliveries()
        deliveries.items = [self.item()]
        self.passes(deliveries, 1, durable=False)
        deliveries.accept("m-int", "alp-solver-2", "reply", CFG)
        self.assertEqual(self.announcements(), [])
        rings = self.passes(deliveries, 2, durable=True)
        self.assertEqual(rings, [], "an accepted interrupt is never rung again")
        self.assertEqual(len(self.announcements()), 1)
        self.assertEqual(deliveries.items, [], "it leaves once its announcement is durable")


class InterruptCase(BridgeCase):
    """An interrupt for alp-solver-2 through the real interrupt path (owner
    amendment 6), with a fake agent screen and keys (test_bridge.FakeAgent),
    no real waits, and the bridge's own announcements on the fake server."""

    def setUp(self):
        super().setUp()
        from test_bridge import FakeAgent
        self.FakeAgent = FakeAgent
        for p in (mock.patch.object(self.bridge, "agent_surface", lambda name: ("S", "claude")),
                  mock.patch.object(self.bridge.time, "sleep", lambda s: None),
                  mock.patch.object(self.bridge, "log", lambda m: None)):
            p.start()
            self.patches.append(p)

    def item(self):
        return {"kind": "ring", "msgid": "m-int", "channel": "#alpha", "sender": "pat", "project": "alpha",
                "target": "alp-solver-2", "phase": "deliver", "interrupt": True, "needs_ack": True, "attempt": 0,
                "next_at": 0, "text": "!! stop and read #alpha"}

    def queued(self):
        d = self.bridge.Deliveries()
        d.items = [self.item()]
        d._save()  # durably queued, as handle() leaves it
        return d

    def passes(self, agent, n=1, deliveries=None):
        d = deliveries if deliveries is not None else self.bridge.Deliveries()
        with mock.patch.object(self.bridge, "_cmux", agent):
            for _ in range(n):
                d.process(CFG)
        return d

    def on_disk(self):
        return self.bridge.Deliveries().items

    def interrupts(self, *agents):
        return sum(a.keys.count("enter") for a in agents)

    def announcements(self):
        return [m for m in self.server.published if m["text"] and "instruction delivered" in m["text"]]

    def ledger(self):
        path = self.bridge.DELIVERIES
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.is_file() else []

    def alerts(self, items=None):
        """The owner alerts about this interrupt: pushes still pending, and pushes already sent (the ledger)."""
        pending = [i for i in (items or []) if i["kind"] == "push" and i.get("msgid") == "m-int"]
        sent = [r for r in self.ledger() if r.get("kind") == "push" and r.get("msgid") == "m-int" and r.get("ok")]
        return pending + sent


class CrashingAgent:
    """A fake agent that is killed (Crash) at its n-th agent-affecting command,
    or at a read-only call when n is "read"."""

    def __init__(self, agent, n):
        self.agent, self.n, self.acts = agent, n, 0
        self.keys = agent.keys

    def __call__(self, *args):
        if args[0] in ("send", "send-key"):
            if self.n == self.acts:
                raise Crash()
            self.acts += 1
        elif self.n == "read" or (isinstance(self.n, tuple) and self.acts >= self.n[1]):
            raise Crash()  # a read before the intent; or one after the submission, before its evidence is saved
        return self.agent(*args)


class InterruptIntentTests(InterruptCase):
    """Design tests 41 and 42 (owner amendment 6; the scope review's correction
    and first addition): durable intent before the first agent-affecting
    command, the preparatory Escape included; with no intent, no action; and
    from the intent on, no crash ever leads to a second interrupt."""

    def test_the_intent_is_durable_before_the_first_command(self):
        seen = []
        agent = self.FakeAgent()

        def watching(*args):
            if args[0] in ("send", "send-key") and not seen:
                seen.append([i.get("phase") for i in self.on_disk()])
            return agent(*args)
        watching.keys = agent.keys
        self.queued()
        self.passes(watching)
        self.assertEqual(seen, [["intent"]], "intent is on disk before the preparatory Escape")
        self.assertEqual(self.interrupts(agent), 1)

    def test_without_a_durable_intent_nothing_is_done(self):
        agent = self.FakeAgent()
        real_save, failed = self.bridge.Deliveries._save, []

        def save(d):
            if any(i.get("phase") == "intent" for i in d.items) and not failed:
                failed.append(1)
                raise OSError(28, "no space left on device (fake)")
            return real_save(d)
        d = self.queued()
        with mock.patch.object(self.bridge.Deliveries, "_save", save):
            self.passes(agent, deliveries=d)
        self.assertEqual(agent.keys, [], "no command without the intent")
        self.assertEqual([i["phase"] for i in self.on_disk()], ["deliver"])
        d.items[0]["next_at"] = 0
        self.passes(agent, deliveries=d)
        self.assertEqual(self.interrupts(agent), 1, "tried again once the intent can be recorded")

    def test_a_crash_at_any_boundary_after_the_intent_never_interrupts_again(self):
        for n in (0, 1, 2, 3, 4, ("read after", 5)):  # the vim Escape, the stop, the "i", the typing, the Enter
            with self.subTest(killed_at_command=n):
                self.fresh()
                first = self.FakeAgent()
                self.queued()
                with self.assertRaises(Crash):
                    self.passes(CrashingAgent(first, n))
                second = self.FakeAgent()
                after = self.passes(second, 3)  # a restart: the state on disk
                self.assertEqual(second.keys, [], "never rung again")
                self.assertEqual([i["phase"] for i in after.items if i["kind"] == "ring"], ["held"])
                self.assertEqual(len(self.alerts(after.items)), 1, "one content-free owner alert")

    def test_a_crash_after_the_evidence_never_interrupts_again(self):
        """Test 42's last boundary: killed after the submission's ledger line, before anything else."""
        calls = []

        def announce(*a):
            calls.append(1)
            if len(calls) == 1:
                raise Crash()
            return True
        first = self.FakeAgent()
        self.queued()
        with mock.patch.object(self.bridge, "announce", announce), self.assertRaises(Crash):
            self.passes(first)
        second = self.FakeAgent()
        with mock.patch.object(self.bridge, "announce", announce):
            after = self.passes(second, 2)
        self.assertEqual(self.interrupts(first, second), 1, "never rung again")
        self.assertEqual([i["phase"] for i in after.items if i["kind"] == "ring"], ["accept"], "by its ledger line")
        self.assertEqual(len(calls), 2, "the owed announcement, made once after the restart")

    def test_a_crash_before_the_intent_is_retried_as_today(self):
        self.queued()
        with self.assertRaises(Crash):
            self.passes(CrashingAgent(self.FakeAgent(), "read"))
        agent = self.FakeAgent()
        self.passes(agent)
        self.assertEqual(self.interrupts(agent), 1, "nothing was done: it is delivered after the restart")


class InterruptOutcomeTests(InterruptCase):
    """Design tests 43 to 45, with the scope review's two additions: save,
    ledger and announcement failures after a proved submission; a proved
    no-action, a partial step, and a delivered interrupt kept distinct; and
    proof that is not proof."""

    def failing_saves(self, persistent):
        real, failed = self.bridge.Deliveries._save, []

        def save(d):
            if any(i.get("phase") in ("accept", "settled") for i in d.items) and (persistent or not failed):
                failed.append(1)
                raise OSError(28, "no space left on device (fake)")
            return real(d)
        return save

    def test_save_failures_after_delivery_never_interrupt_again(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                self.fresh()
                first = self.FakeAgent()
                d = self.queued()
                with mock.patch.object(self.bridge.Deliveries, "_save", self.failing_saves(persistent)):
                    self.passes(first, 1 if not persistent else 3, deliveries=d)
                # the bridge dies at once, with the delivered state never saved
                second = self.FakeAgent()
                after = self.passes(second, 2)
                self.assertEqual(self.interrupts(first, second), 1, "rung exactly once")
                self.assertEqual([i["phase"] for i in after.items], ["accept"], "reconciled by its ledger line")
                self.assertEqual(len(self.announcements()), 1)

    def test_with_no_surviving_proof_it_is_held_alerted_once_and_settled_by_the_acknowledgement(self):
        real_record = self.bridge.Deliveries._record

        def record(d, item, ok, detail):
            if ok is True:
                raise OSError(28, "no space left on device (fake)")
            return real_record(d, item, ok, detail)
        first = self.FakeAgent()
        d = self.queued()
        with mock.patch.object(self.bridge.Deliveries, "_save", self.failing_saves(True)), \
                mock.patch.object(self.bridge.Deliveries, "_record", record):
            self.passes(first, 2, deliveries=d)
        second = self.FakeAgent()
        after = self.passes(second, 2)
        self.assertEqual(self.interrupts(first, second), 1)
        [held] = [i for i in after.items if i["kind"] == "ring"]
        self.assertEqual(held["phase"], "held")
        self.assertEqual(len(self.alerts(after.items)), 1)
        after.accept("m-int", "alp-solver-2", "reply", CFG)
        self.assertEqual([i for i in self.on_disk() if i["kind"] == "ring"], [], "settled by the acknowledgement")

    def no_delivery_line(self):
        real = self.bridge.Deliveries._record

        def record(d, item, ok, detail):
            if ok is True and "submitted" in str(detail):
                raise OSError(28, "no space left on device (fake)")
            return real(d, item, ok, detail)
        return record

    def test_an_acknowledgement_while_work_is_owed_then_a_restart(self):
        for save_fails in (False, True):
            with self.subTest(acknowledgement_save_fails=save_fails):
                self.fresh()
                agent = self.FakeAgent()
                d = self.queued()
                with mock.patch.object(self.bridge, "announce", lambda *a: False), \
                        mock.patch.object(self.bridge.Deliveries, "_record", self.no_delivery_line()):
                    self.passes(agent, deliveries=d)
                    acks = self.bridge.Acks()
                    with mock.patch.object(self.bridge.Deliveries, "_save",
                                           self.failing_saves(True) if save_fails else self.bridge.Deliveries._save):
                        self.bridge.accepted(CFG, d, acks, msgid="m-ack", reply_to="m-int", sender="alp-solver-2",
                                             channel="#alpha", at="2026-10-09T00:00:00Z", how="reply")
                # restart at once
                second = self.FakeAgent()
                after = self.passes(second, 2)
                self.assertEqual(self.interrupts(agent, second), 1, "no further interrupt")
                self.assertEqual(len(self.announcements()), 1, "the owed announcement is made, once")
                delivered = [r for r in self.ledger() if r.get("intent") and r.get("ok") is True
                             and "submitted" in str(r.get("detail"))]
                self.assertEqual(len(delivered), 1, "the owed ledger line is written once, with its original identity")
                self.assertEqual([i for i in after.items if i["kind"] == "ring"], [], "kept until its work was durable")

    def test_a_proved_no_action_is_retried_and_a_partial_step_is_held(self):
        with mock.patch.object(self.bridge, "_stop_turn", lambda s, b: (None, "not working")), \
                mock.patch.object(self.bridge, "_type_into_prompt",
                                  lambda *a, **k: (False, "prompt not empty; not typing")):
            d = self.queued()
            agent = self.FakeAgent()
            self.passes(agent, deliveries=d)
        self.assertEqual([i["phase"] for i in self.on_disk()], ["deliver"], "proved no-action: tried again")
        self.assertEqual(agent.keys, [])
        for how in ("typing mismatch", "enter acts but fails", "failure after the escape"):
            with self.subTest(how=how):
                self.fresh()
                agent = self.FakeAgent()
                base = agent

                def acting(*args, how=how):
                    if how == "typing mismatch" and args[0] == "send" and len(args[-1]) > 1:
                        return base(*args[:-1], args[-1] + " (garbled)")
                    if how == "enter acts but fails" and args[0] == "send-key" and args[-1] == "enter":
                        base(*args)
                        return 1, "", "cmux: send-key failed (fake)"
                    if how == "failure after the escape" and args[0] == "send-key" and base.keys.count("escape") == 1:
                        raise OSError(5, "cmux died (fake)")
                    return base(*args)
                acting.keys = agent.keys
                d = self.queued()
                self.passes(acting, deliveries=d)
                self.assertEqual([i["phase"] for i in self.on_disk() if i["kind"] == "ring"], ["held"])
                again = self.FakeAgent()
                self.passes(again, 2, deliveries=d)
                for i in d.items:
                    i["next_at"] = 0  # its acknowledgement wait is over
                self.passes(again, 2, deliveries=d)
                self.assertEqual(again.keys, [], "a partial or unclear step is never repeated")
                self.assertEqual([i for i in d.items if i["kind"] == "ring"], [])
                [dl] = [r for r in self.dead_letters() if r.get("msgid") == "m-int"]
                self.assertIn("outcome unknown; not repeated", dl["last_detail"], "dead-lettered truthfully")

    def test_proof_that_is_not_proof_leaves_it_held(self):
        intent = {"phase": "intent", "intent_id": "i" * 32, "intent_surface": "S", "intent_session": None}
        good = {"msgid": "m-int", "ok": True, "intent": "i" * 32, "target": "alp-solver-2", "surface": "S",
                "session": None}
        cases = {"another intent": dict(good, intent="j" * 32), "another target": dict(good, target="sam"),
                 "another surface": dict(good, surface="T"), "no intent": {k: v for k, v in good.items()
                                                                            if k != "intent"},
                 "not ok": dict(good, ok=False), "unreadable": None, "exact": good,
                 "a reminder's line": {"msgid": "m-int", "kind": "ring", "ok": True, "detail": "reminder 1 of 2"}}
        for name, line in cases.items():
            with self.subTest(case=name):
                self.fresh()
                d = self.bridge.Deliveries()
                d.items = [dict(self.item(), **intent)]
                d._save()
                if line is None:
                    self.bridge.DELIVERIES.mkdir(parents=True)  # an unreadable ledger
                else:
                    self.bridge.DELIVERIES.write_text(json.dumps(line) + "\n")
                try:
                    agent = self.FakeAgent()
                    after = self.passes(agent, 2)
                finally:
                    if self.bridge.DELIVERIES.is_dir():
                        self.bridge.DELIVERIES.rmdir()
                self.assertEqual(agent.keys, [], "never rung again")
                want = "accept" if name == "exact" else "held"
                self.assertEqual([i["phase"] for i in after.items if i["kind"] == "ring"], [want])


class AcknowledgementRecoveryTests(InterruptCase):
    """Code review slot 9, findings 1 and 2: an acknowledgement already in
    acks.jsonl settles its interrupt after a restart for EVERY recipient of one
    message, and recovery that could not read the file is tried again at a
    later pass, keeping the valid records around a malformed line."""

    def delivered(self, target):
        return dict(self.item(), target=target, phase="accept", attempt=1, next_at=self.bridge.time.time() + 600,
                    intent_id=f"{target}-intent", intent_surface="S", intent_session=None, delivered_announced=True)

    def expire_and_pass(self, d):
        for i in d.items:
            i["next_at"] = 0
        self.passes(self.FakeAgent(), 2, deliveries=d)

    def unacknowledged_dead_letters(self):
        return [r for r in self.dead_letters() if r.get("msgid") == "m-int" and r.get("kind") == "ring"]

    def test_every_recipient_of_one_interrupt_is_recovered_after_a_failed_settling_save(self):
        d = self.bridge.Deliveries()
        d.items = [self.delivered("alp-solver-2"), self.delivered("alp-solver-3")]
        d._save()
        acks = self.bridge.Acks()

        def save(_d):
            raise OSError(28, "no space left on device (fake)")
        with mock.patch.object(self.bridge.Deliveries, "_save", save):
            for n, target in enumerate(("alp-solver-2", "alp-solver-3")):
                self.bridge.accepted(CFG, d, acks, msgid=f"m-ack-{n}", reply_to="m-int", sender=target,
                                     channel="#alpha", at="2026-10-09T00:00:00Z", how="reply")
        after = self.bridge.Deliveries()  # a restart: neither settling was saved
        self.assertEqual(sorted(i["target"] for i in after.items), ["alp-solver-2", "alp-solver-3"])
        self.passes(self.FakeAgent(), deliveries=after)
        self.expire_and_pass(after)
        self.assertEqual([i for i in after.items if i["kind"] == "ring"], [], "both recipients settled")
        self.assertEqual(self.unacknowledged_dead_letters(), [], "no false unacknowledged dead letter")

    def test_a_failed_read_is_retried_at_a_later_pass_and_a_malformed_line_is_skipped(self):
        d = self.bridge.Deliveries()
        d.items = [self.delivered("alp-solver-2")]
        d._save()
        self.bridge.ACKS.mkdir(parents=True)  # the first read fails
        try:
            after = self.bridge.Deliveries()
            self.passes(self.FakeAgent(), deliveries=after)
            self.assertEqual([i["phase"] for i in after.items], ["accept"], "not yet recovered")
        finally:
            self.bridge.ACKS.rmdir()
        ack = {"at": "2026-10-09T00:00:00Z", "msgid": "m-ack", "reply_to": "m-int", "by": "alp-solver-2",
               "channel": "#alpha", "project": "alpha", "how": "reply"}
        self.bridge.ACKS.write_text('{"half a line\n' + json.dumps(ack) + "\n")
        self.passes(self.FakeAgent(), deliveries=after)  # a later pass of the same process
        self.expire_and_pass(after)
        self.assertEqual([i for i in after.items if i["kind"] == "ring"], [], "recovered at the later pass")
        self.assertEqual(self.unacknowledged_dead_letters(), [])


class ReplayCase(InterruptCase):
    """An interrupt message for alp-solver-2, ingested and replayed through handle()."""

    def message(self, text="!! @alp-solver-2 stop and read #alpha"):
        from test_bridge import privmsg
        return privmsg("pat", text, "m-int", channel="#alpha", t="2026-10-09T10:00:00.000Z")

    def ingest_unlogged(self, msg):
        """handle() with the message's log append failing: queued, never logged."""
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        with mock.patch.object(self.bridge.Record, "append", side_effect=OSError(28, "no space left (fake)")):
            with self.assertRaises(OSError):
                self.bridge.handle(msg, CFG, *state)
        return state[1]

    def replay(self, msg):
        """A restart, then the same server message replayed."""
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        self.bridge.handle(msg, CFG, *state, replayed=True)
        return state[1]

    def rings(self, d, target="alp-solver-2"):
        return [i for i in d.items if i["kind"] == "ring" and i.get("target") == target]


class ReplayedInterruptTests(ReplayCase):
    """Code review slot 10, finding 1: the bridge queues a message's deliveries
    durably before it logs the message, so a failed log (or a crash between the
    two) means the same server message is handled again on replay. An interrupt
    keeps ONE obligation per message and recipient: the replay adds nothing, and
    the existing item keeps its identity, intent, phase and owed work, whatever
    its outcome so far. Another recipient of the same message stays independent."""

    def test_a_replayed_interrupt_keeps_one_obligation_whatever_its_outcome(self):
        for outcome in ("deliver", "possibly acted", "held", "accept", "settled"):
            with self.subTest(outcome=outcome):
                self.fresh()
                d = self.ingest_unlogged(self.message())
                [item] = self.rings(d)
                first = self.FakeAgent()
                if outcome == "possibly acted":  # killed at the Enter: still in intent on disk
                    with self.assertRaises(Crash):
                        self.passes(CrashingAgent(first, 4), deliveries=d)
                elif outcome == "held":
                    with self.assertRaises(Crash):
                        self.passes(CrashingAgent(first, 4), deliveries=d)
                    self.passes(self.FakeAgent(), deliveries=self.bridge.Deliveries())
                elif outcome in ("accept", "settled"):
                    self.passes(first, deliveries=d)
                    if outcome == "settled":
                        self.bridge.accepted(CFG, d, self.bridge.Acks(), msgid="m-ack", reply_to="m-int",
                                             sender="alp-solver-2", channel="#alpha",
                                             at="2026-10-09T10:01:00Z", how="reply")
                before = [{k: i.get(k) for k in ("intent_id", "phase")} for i in self.rings(self.bridge.Deliveries())]
                after = self.replay(self.message())
                self.assertEqual([{k: i.get(k) for k in ("intent_id", "phase")} for i in self.rings(after)], before,
                                 "the replay adds nothing and changes nothing")
                second = self.FakeAgent()
                self.passes(second, 3, deliveries=after)
                if outcome == "deliver":  # never rung before: rung once now
                    self.assertEqual(self.interrupts(second), 1)
                else:
                    self.assertEqual(second.keys, [], "no second interrupt, and no partial step either")
                self.assertLessEqual(self.interrupts(first, second), 1, "never submitted twice")

    def test_another_recipient_of_the_same_message_stays_independent(self):
        d = self.ingest_unlogged(self.message("!! @alp-solver-2 stop and read #alpha"))
        self.passes(self.FakeAgent(), deliveries=d)
        after = self.replay(self.message("!! @alp-solver-2 @alp-manager stop and read #alpha"))
        self.assertEqual(len(self.rings(after)), 1, "the existing obligation is kept")
        self.assertEqual(len(self.rings(after, "alp-manager")), 1, "another recipient gets its own")

    def test_an_ordinary_wake_up_keeps_its_baseline(self):
        """Not an interrupt: a replay may duplicate a wake-up, never lose one (as documented)."""
        d = self.ingest_unlogged(self.message("@alp-manager status of #alpha?"))
        self.assertEqual(len(self.rings(d, None)), 1)
        after = self.replay(self.message("@alp-manager status of #alpha?"))
        self.assertEqual(len(self.rings(after, None)), 2, "queued again, as before")

    def test_a_replay_with_an_unreadable_ledger_is_never_rung(self):
        """No pending item, and the ledger that would show an earlier obligation
        cannot be read: possibly a repeat, so it is held, never rung (a pause over
        a duplicate), with one owner alert."""
        d = self.ingest_unlogged(self.message())
        agent = self.FakeAgent()
        self.passes(agent, deliveries=d)
        self.bridge.accepted(CFG, d, self.bridge.Acks(), msgid="m-ack", reply_to="m-int", sender="alp-solver-2",
                             channel="#alpha", at="2026-10-09T10:01:00Z", how="reply")
        ledger = self.bridge.DELIVERIES.read_text()
        self.bridge.DELIVERIES.unlink()
        self.bridge.DELIVERIES.mkdir()
        try:
            after = self.replay(self.message())
            self.passes(agent, 2, deliveries=after)
        finally:
            self.bridge.DELIVERIES.rmdir()
            self.bridge.DELIVERIES.write_text(ledger)
        self.assertEqual(self.interrupts(agent), 1, "never rung again")
        self.assertEqual([i["phase"] for i in self.rings(after)], ["held"])


class QueuedOnlyWhenSavedTests(ReplayCase):
    """Code review slot 11, finding 1: an interrupt is queued only once its save
    has succeeded. A failed save leaves nothing of it in memory (nor an alert
    raised for it), and handle() raises before the message is logged, so the
    same bridge replays the message and queues it again: after a restart,
    exactly one interrupt is queued, and none is lost."""

    def failing_saves(self, n):
        real, calls = self.bridge.Deliveries._save, []

        def save(d):
            calls.append(1)
            if len(calls) <= n:
                raise OSError(28, "no space left on device (fake)")
            return real(d)
        return mock.patch.object(self.bridge.Deliveries, "_save", save)

    def test_a_failed_queue_save_then_a_same_instance_replay_then_a_crash(self):
        for failures in (1, 2):
            with self.subTest(failed_saves=failures):
                self.fresh()
                state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
                with self.failing_saves(failures):
                    for _ in range(failures):
                        with self.assertRaises(OSError):
                            self.bridge.handle(self.message(), CFG, *state)
                        self.assertEqual(state[1].items, [], "nothing kept in memory")
                    self.bridge.handle(self.message(), CFG, *state, replayed=True)  # the same bridge
                logged = self.chatlib.channel_log("#alpha").read_text().splitlines()
                self.assertEqual(len([x for x in logged if '"m-int"' in x]), 1, "logged once, after it was queued")
                after = self.bridge.Deliveries()  # a crash before any delivery pass
                self.assertEqual(len(self.rings(after)), 1, "exactly one interrupt, durably queued")
                agent = self.FakeAgent()
                self.passes(agent, 2, deliveries=after)
                self.assertEqual(self.interrupts(agent), 1)

    def test_a_failed_save_of_a_held_replay_leaves_no_alert_behind(self):
        d = self.bridge.Deliveries()
        self.bridge.DELIVERIES.mkdir(parents=True)  # unreadable ledger: the replay would be held, with an alert
        try:
            with self.failing_saves(1), self.assertRaises(OSError):
                d.add_all([dict(self.item(), msgid="m-int")])
        finally:
            self.bridge.DELIVERIES.rmdir()
        self.assertEqual(d.items, [], "neither the item nor its alert is kept")


class AcknowledgementBeforeHistoryTests(ReplayCase):
    """Code review slot 11, finding 2: a live acknowledgement can arrive before
    the instruction it acknowledges is replayed from history. Queueing a new
    interrupt reopens acknowledgement recovery, so the next pass settles it by
    its recorded acknowledgement: never rung, no false dead letter, whether the
    ledger could be read at ingestion or not (held)."""

    def test_an_acknowledgement_before_its_instruction_is_replayed(self):
        from test_bridge import privmsg
        for ledger in ("readable", "unreadable"):
            with self.subTest(ledger=ledger):
                self.fresh()
                state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
                d = state[1]
                agent = self.FakeAgent()
                self.passes(agent, deliveries=d)  # the first scan: nothing pending
                self.bridge.handle(privmsg("alp-solver-2", "[status] stopped as asked", "m-ack", channel="#alpha",
                                           reply_to="m-int", t="2026-10-09T10:02:00.000Z"), CFG, *state)
                if ledger == "unreadable":
                    self.bridge.DELIVERIES.mkdir(parents=True)
                try:
                    self.bridge.handle(self.message(), CFG, *state, replayed=True)  # the instruction, from history
                finally:
                    if self.bridge.DELIVERIES.is_dir():
                        self.bridge.DELIVERIES.rmdir()
                self.passes(agent, 2, deliveries=d)
                for i in d.items:
                    i["next_at"] = 0
                self.passes(agent, 2, deliveries=d)
                self.assertEqual(agent.keys, [], "never rung")
                self.assertEqual(self.rings(d), [], "settled by its acknowledgement")
                self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [],
                                 "no false dead letter")


class AcknowledgementDurabilityTests(ReplayCase):
    """The final round's bounded check: an acknowledgement counts as recorded
    (and a replay of it as a duplicate) only once its line is written to
    acks.jsonl, so a failed append is written again when the message is
    replayed."""

    def test_a_failed_acknowledgement_append_is_written_on_replay(self):
        from test_bridge import privmsg
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        ack = privmsg("alp-solver-2", "[status] stopped as asked", "m-ack", channel="#alpha", reply_to="m-int",
                      t="2026-10-09T10:02:00.000Z")
        real, calls = self.bridge.delivery_store.durable_append, []

        def append(fs, path, line):
            if Path(path) == self.bridge.ACKS and not calls:
                calls.append(1)
                raise OSError(28, "no space left on device (fake)")
            return real(fs, path, line)
        with mock.patch.object(self.bridge.delivery_store, "durable_append", append):
            with self.assertRaises(OSError):
                self.bridge.handle(ack, CFG, *state)
            self.bridge.handle(ack, CFG, *state, replayed=True)  # the same bridge
            self.bridge.handle(ack, CFG, *state, replayed=True)  # and once more: a duplicate now
        lines = self.bridge.ACKS.read_text().splitlines() if self.bridge.ACKS.exists() else []
        self.assertEqual([json.loads(x)["msgid"] for x in lines], ["m-ack"], "recorded once, when written")


class UnreadablePendingTests(ReplayCase):
    """Code review slot 12, finding 1: only a MISSING pending.json means an empty
    queue. A read or parse failure leaves the state unloaded: nothing replaces
    the saved file, nothing new is queued (handle() raises before logging, so
    the message is replayed later), no agent is touched, and each pass tries
    the load again; once it succeeds the saved intent is recovered as held."""

    def possibly_acted(self):
        """The instruction queued but not yet logged (the crash window between the two), then
        the bridge killed at its Enter: an intent on disk, and a message the restart replays."""
        d = self.ingest_unlogged(self.message())
        first = self.FakeAgent()
        with self.assertRaises(Crash):
            self.passes(CrashingAgent(first, 4), deliveries=d)
        self.assertEqual([i["phase"] for i in self.rings(self.bridge.Deliveries())], ["intent"])
        return first

    def unreadable(self, how, original):
        real = Path.read_text
        pending = self.bridge.PENDING

        def read_text(path, *a, **k):
            if Path(path) == pending:
                if how == "read error":
                    raise OSError(5, "input/output error (fake)")
                return original.decode()[: len(original) // 2]  # a torn read: not JSON
            return real(path, *a, **k)
        return mock.patch.object(Path, "read_text", read_text)

    def test_a_possibly_acted_intent_survives_an_unreadable_pending_state(self):
        for how in ("read error", "parse error"):
            with self.subTest(how=how):
                self.fresh()
                first = self.possibly_acted()
                original = self.bridge.PENDING.read_bytes()
                second = self.FakeAgent()
                with self.unreadable(how, original):
                    d = self.bridge.Deliveries()  # a restart that cannot read its state
                    state = (self.bridge.Record(), d, self.bridge.Acks())
                    with self.assertRaises(Exception):
                        self.bridge.handle(self.message(), CFG, *state, replayed=True)  # the exact instruction
                    self.passes(second, 3, deliveries=d)
                    self.assertEqual(self.bridge.PENDING.read_bytes(), original, "the saved state is intact")
                logged = self.chatlib.channel_log("#alpha")
                self.assertNotIn('"m-int"', logged.read_text() if logged.exists() else "", "left for replay")
                self.passes(second, 2, deliveries=d)  # readable again: the same bridge loads it
                self.bridge.handle(self.message(), CFG, *state, replayed=True)  # the replay, now handled
                self.passes(second, 2, deliveries=d)
                self.assertEqual(second.keys, [], "no new interrupt, no partial step")
                self.assertEqual(self.interrupts(first, second), 0, "never submitted")
                self.assertEqual([i["phase"] for i in self.rings(d)], ["held"], "the original intent, held")

    def test_a_missing_pending_file_is_an_empty_queue(self):
        """The baseline (a control): no file at all is an empty queue, and a new interrupt is delivered."""
        self.assertFalse(self.bridge.PENDING.exists())
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        self.assertEqual(state[1].items, [])
        self.bridge.handle(self.message(), CFG, *state)
        agent = self.FakeAgent()
        self.passes(agent, deliveries=state[1])
        self.assertEqual(self.interrupts(agent), 1)


class AcknowledgementPowerLossTests(InterruptCase):
    """Code review slot 12, finding 2: an acknowledgement is durable (file, then
    directory) before it counts as recorded. Its settling save fails, the
    storage loses everything unsynced, the bridge restarts: the interrupt is
    settled by that acknowledgement, with its owed work kept, and is never rung
    or falsely dead-lettered."""

    def test_an_acknowledgement_survives_a_failed_settling_save_and_a_power_loss(self):
        from outbox_fakes import SimFS
        for owed in (False, True):
            with self.subTest(announcement_owed=owed):
                self.fresh()
                fs = SimFS(self.chatlib.STATE_DIR)
                p = mock.patch.object(self.bridge.delivery_store, "FS_DEFAULT", fs)
                p.start()
                self.addCleanup(p.stop)
                item = dict(self.item(), phase="accept", attempt=1, next_at=self.bridge.time.time() + 600,
                            intent_id="i" * 32, intent_surface="S", intent_session=None, delivered_announced=True)
                if owed:
                    item.update(delivered_announced=False, delivered_text="interrupt → alp-solver-2: it was not "
                                "working; instruction delivered; waiting for its acknowledgement")
                d = self.bridge.Deliveries()
                d.items = [item]
                d._save()
                agent = self.FakeAgent()
                with mock.patch.object(self.bridge, "announce", lambda *a: False):
                    self.passes(agent, deliveries=d)
                acks = self.bridge.Acks()

                def save(dd):
                    raise OSError(28, "no space left on device (fake)")
                with mock.patch.object(self.bridge.Deliveries, "_save", save):
                    self.bridge.accepted(CFG, d, acks, msgid="m-ack", reply_to="m-int", sender="alp-solver-2",
                                         channel="#alpha", at="2026-10-09T10:01:00Z", how="reply")
                fs.power_loss()  # whatever was never synced is gone
                p.stop()
                after = self.bridge.Deliveries()  # the restart
                self.passes(agent, 2, deliveries=after)
                for i in after.items:
                    i["next_at"] = 0
                self.passes(agent, 2, deliveries=after)
                self.assertEqual(agent.keys, [], "never rung")
                self.assertEqual([i for i in after.items if i["kind"] == "ring"], [], "settled")
                self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [],
                                 "no false unacknowledged dead letter")
                self.assertEqual(len(self.announcements()), 1 if owed else 0, "owed work kept and done")


class UnreadableAcknowledgementsTests(ReplayCase):
    """Code review CODE13: while acks.jsonl cannot be read, an acknowledgement
    already saved there cannot be checked. The interrupts it could settle are
    deferred (no Escape or Enter, no dead letter, no removal; an elapsed
    deadline is no substitute for the check), independent work goes on, and
    once the file can be read they are settled by it."""

    def unreadable_acks(self):
        real, acks = Path.read_text, self.bridge.ACKS

        def read_text(path, *a, **k):
            if Path(path) == acks:
                raise OSError(5, "input/output error (fake)")
            return real(path, *a, **k)
        return mock.patch.object(Path, "read_text", read_text)

    def saved_ack(self):
        from test_bridge import privmsg
        return privmsg("alp-solver-2", "[status] stopped as asked", "m-ack", channel="#alpha", reply_to="m-int",
                       t="2026-10-09T10:02:00.000Z")

    def an_owner_push(self, d):
        d.items.append({"kind": "push", "msgid": "m-other", "channel": "#beta", "sender": "pat", "project": "beta",
                        "attempt": 0, "next_at": 0, "text": "@sam a question for you"})

    def pushed(self):
        return [r for r in self.ledger() if r.get("kind") == "push" and r.get("msgid") == "m-other" and r.get("ok")]

    def test_a_ready_interrupt_is_deferred_until_its_saved_acknowledgement_can_be_read(self):
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        d = state[1]
        agent = self.FakeAgent()
        self.passes(agent, deliveries=d)  # the first scan: nothing pending
        self.bridge.handle(self.saved_ack(), CFG, *state)  # the acknowledgement, before its instruction
        self.bridge.handle(self.message(), CFG, *state, replayed=True)  # the instruction, from history
        self.an_owner_push(d)
        with self.unreadable_acks():
            self.passes(agent, 3, deliveries=d)
            self.assertEqual(agent.keys, [], "no Escape, no Enter while the acknowledgement cannot be checked")
            self.assertEqual([i["phase"] for i in self.rings(d)], ["deliver"], "the original obligation, kept")
            self.assertEqual(len(self.pushed()), 1, "independent work goes on")
        self.passes(agent, 2, deliveries=d)  # readable again
        self.assertEqual(agent.keys, [], "settled by its acknowledgement, never rung")
        self.assertEqual(self.rings(d), [])
        self.assertEqual(len([r for r in self.ledger() if str(r.get("detail")).startswith("accepted by")]), 1)

    def test_an_expired_interrupt_is_not_dead_lettered_while_its_acknowledgement_cannot_be_read(self):
        for owed in (False, True):
            with self.subTest(announcement_owed=owed):
                self.fresh()
                item = dict(self.item(), phase="accept", attempt=1, next_at=0, intent_id="i" * 32,
                            intent_surface="S", intent_session=None, delivered_announced=True)
                if owed:
                    item.update(delivered_announced=False, delivered_text="interrupt → alp-solver-2: it was not "
                                "working; instruction delivered; waiting for its acknowledgement")
                d = self.bridge.Deliveries()
                d.items = [item]
                d._save()
                self.bridge.Acks().add("m-ack", {"at": "2026-10-09T10:02:00Z", "msgid": "m-ack", "reply_to": "m-int",
                                                 "by": "alp-solver-2", "channel": "#alpha", "project": "alpha",
                                                 "how": "reply"})
                after = self.bridge.Deliveries()  # a restart
                self.an_owner_push(after)
                agent = self.FakeAgent()
                with self.unreadable_acks(), mock.patch.object(self.bridge, "announce", lambda *a: False):
                    self.passes(agent, 3, deliveries=after)  # its deadline has passed
                    self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [],
                                     "no unacknowledged dead letter")
                    self.assertEqual([i["phase"] for i in self.rings(after)], ["accept"], "not removed")
                    self.assertEqual(len(self.pushed()), 1, "independent work goes on")
                self.passes(agent, 2, deliveries=after)  # readable again
                self.assertEqual(self.rings(after), [], "settled")
                self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [])
                self.assertEqual(agent.keys, [])
                self.assertEqual(len(self.announcements()), 1 if owed else 0, "owed work kept and done")

    def test_an_expired_interrupt_with_no_acknowledgement_is_dead_lettered_as_before(self):
        """A control: the file can be read, and holds no acknowledgement for it."""
        d = self.bridge.Deliveries()
        d.items = [dict(self.item(), phase="accept", attempt=1, next_at=0, intent_id="i" * 32, intent_surface="S",
                        intent_session=None, delivered_announced=True)]
        d._save()
        self.bridge.ACKS.write_text("")
        self.passes(self.FakeAgent(), 2, deliveries=self.bridge.Deliveries())
        [dl] = [r for r in self.dead_letters() if r.get("kind") == "ring"]
        self.assertEqual(dl["last_detail"], "interrupted, but never acknowledged")

    def test_with_no_acknowledgement_file_a_ready_interrupt_is_delivered(self):
        """A control: no acks.jsonl at all is no acknowledgement, never an unreadable one."""
        self.assertFalse(self.bridge.ACKS.exists())
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        self.bridge.handle(self.message(), CFG, *state)
        agent = self.FakeAgent()
        self.passes(agent, deliveries=state[1])
        self.assertEqual(self.interrupts(agent), 1)


def inspection_fails(*paths, how="stat"):
    """A recovery file that cannot be inspected. "stat": stat() fails, so Path.exists() reports False
    (as on Python 3.14, which suppresses the error) and the read raises PermissionError. "read": it
    exists, and reading it fails."""
    targets = {Path(p) for p in paths}
    real_exists, real_read = Path.exists, Path.read_text

    def exists(path, *a, **k):
        if Path(path) in targets and how == "stat":
            return False
        return real_exists(path, *a, **k)

    def read_text(path, *a, **k):
        if Path(path) in targets:
            raise PermissionError(13, "permission denied (fake)") if how == "stat" else OSError(5, "I/O error (fake)")
        return real_read(path, *a, **k)
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(Path, "exists", exists))
    stack.enter_context(mock.patch.object(Path, "read_text", read_text))
    return stack


class RecoveryInputTests(ReplayCase):
    """Code review CODE14, finding 1, and the bounded systematic check: for the
    bridge's recovery files (pending state, acks.jsonl, the delivery ledger),
    only FileNotFoundError is absence. A file that cannot be inspected (stat
    failing, which Path.exists() hides) or read is unknown: nothing is done on
    the strength of it (no key, no dead letter, no removal, no repeated
    obligation, no record or settling as if it were empty), it is tried again,
    and independent work goes on. The map is in docs/chat_outbox_test_matrix.md."""

    def an_owner_push(self, d):
        d.items.append({"kind": "push", "msgid": "m-other", "channel": "#beta", "sender": "pat", "project": "beta",
                        "attempt": 0, "next_at": 0, "text": "@sam a question for you"})

    def pushed(self):
        return [r for r in self.ledger() if r.get("kind") == "push" and r.get("msgid") == "m-other" and r.get("ok")]

    def saved_ack(self):
        self.bridge.Acks().add("m-ack", {"at": "2026-10-09T10:02:00Z", "msgid": "m-ack", "reply_to": "m-int",
                                         "by": "alp-solver-2", "channel": "#alpha", "project": "alpha",
                                         "how": "reply"})

    # acks.jsonl, read by acknowledgement recovery
    def test_acknowledgement_recovery_with_a_stat_failure_defers_a_ready_interrupt(self):
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks())
        d = state[1]
        agent = self.FakeAgent()
        self.passes(agent, deliveries=d)
        self.saved_ack()  # recorded before its instruction is replayed
        self.bridge.handle(self.message(), CFG, *state, replayed=True)
        self.an_owner_push(d)
        with inspection_fails(self.bridge.ACKS):
            self.passes(agent, 3, deliveries=d)
            self.assertEqual(agent.keys, [], "no Escape, no Enter while unchecked")
            self.assertEqual([i["phase"] for i in self.rings(d)], ["deliver"], "kept")
            self.assertEqual(len(self.pushed()), 1, "independent work goes on")
        self.passes(agent, 2, deliveries=d)
        self.assertEqual((agent.keys, self.rings(d)), ([], []), "settled by its acknowledgement, never rung")

    def test_acknowledgement_recovery_with_a_stat_failure_keeps_an_expired_interrupt(self):
        item = dict(self.item(), phase="accept", attempt=1, next_at=0, intent_id="i" * 32, intent_surface="S",
                    intent_session=None, delivered_announced=False, delivered_text="interrupt → alp-solver-2: it "
                    "was not working; instruction delivered; waiting for its acknowledgement")
        d = self.bridge.Deliveries()
        d.items = [item]
        d._save()
        self.saved_ack()
        after = self.bridge.Deliveries()
        agent = self.FakeAgent()
        with inspection_fails(self.bridge.ACKS), mock.patch.object(self.bridge, "announce", lambda *a: False):
            self.passes(agent, 3, deliveries=after)
            self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [], "no false dead letter")
            self.assertEqual([i["phase"] for i in self.rings(after)], ["accept"], "not removed")
        self.passes(agent, 2, deliveries=after)
        self.assertEqual(self.rings(after), [], "settled")
        self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [])
        self.assertEqual(len(self.announcements()), 1, "its owed announcement, made once")

    # the delivery ledger, read for an earlier interrupt on replay
    def test_a_replay_with_a_ledger_stat_failure_is_never_rung(self):
        d = self.ingest_unlogged(self.message())
        agent = self.FakeAgent()
        self.passes(agent, deliveries=d)
        self.bridge.accepted(CFG, d, self.bridge.Acks(), msgid="m-ack", reply_to="m-int", sender="alp-solver-2",
                             channel="#alpha", at="2026-10-09T10:01:00Z", how="reply")
        self.assertEqual(self.rings(d), [], "settled and removed: only the ledger shows it")
        with inspection_fails(self.bridge.DELIVERIES):
            after = self.replay(self.message())
            self.assertEqual([i["phase"] for i in self.rings(after)], ["held"], "a possible repeat is held")
            self.passes(agent, 2, deliveries=after)
        self.passes(agent, 2, deliveries=after)
        self.assertEqual(self.interrupts(agent), 1, "never rung again")
        self.assertEqual(self.rings(after), [], "settled by the acknowledgement already recorded for it")
        self.assertEqual([r for r in self.dead_letters() if r.get("kind") == "ring"], [])

    # the delivery ledger, read for an interrupt's proof (already conservative: a control)
    def test_proof_with_a_ledger_stat_failure_is_no_proof_until_it_can_be_read(self):
        d = self.bridge.Deliveries()
        d.items = [dict(self.item(), phase="intent", intent_id="i" * 32, intent_surface="S", intent_session=None,
                        intent_at=0)]
        d._save()
        self.bridge.DELIVERIES.write_text(json.dumps({"msgid": "m-int", "kind": "ring", "ok": True, "intent": "i" * 32,
                                                      "target": "alp-solver-2", "surface": "S", "session": None})
                                          + "\n")
        agent = self.FakeAgent()
        after = self.bridge.Deliveries()
        with inspection_fails(self.bridge.DELIVERIES):
            self.passes(agent, 2, deliveries=after)
            self.assertEqual([i["phase"] for i in self.rings(after)], ["held"])
        self.passes(agent, deliveries=after)
        self.assertEqual([i["phase"] for i in self.rings(after)], ["accept"], "reconciled once it can be read")
        self.assertEqual(agent.keys, [])

    # the delivery ledger, read to export an alert once
    def test_alert_export_with_a_ledger_stat_failure_queues_no_second_push(self):
        for how in ("stat", "read"):
            with self.subTest(how=how):
                self.fresh()
                self.alert_export_unknown(how)

    def alert_export_unknown(self, how):
        key = "held:outbox.claimed-1-a.jsonl"
        self.tx(lambda s: s.raise_alert(key, "held", "outbox.claimed-1-a.jsonl", "rows are held (fake)"))
        self.bridge.DELIVERIES.write_text(json.dumps({"kind": "push", "msgid": key, "alert_key": key, "ok": True})
                                          + "\n")  # pushed before; its export was never marked
        d = self.bridge.Deliveries()
        flusher = self.bridge.Flusher(self.authority)
        with inspection_fails(self.bridge.DELIVERIES, how=how):
            flusher.export_alerts(d)
        self.assertEqual([i for i in d.items if i.get("alert_key") == key], [], "no second push")
        self.assertIsNone(self.sql("SELECT exported_at FROM alerts WHERE alert_key = ?", key)[0]["exported_at"])
        flusher.export_alerts(d)
        self.assertEqual([i for i in d.items if i.get("alert_key") == key], [])
        self.assertIsNotNone(self.sql("SELECT exported_at FROM alerts WHERE alert_key = ?", key)[0]["exported_at"])

    # acks.jsonl, read for the acknowledgement dedup set
    def test_an_unreadable_acknowledgement_set_never_records_or_settles_as_if_empty(self):
        from test_bridge import privmsg
        for how in ("stat", "read"):
            with self.subTest(how=how):
                self.fresh()
                d = self.bridge.Deliveries()
                d.items = [dict(self.item(), phase="accept", attempt=1, next_at=self.bridge.time.time() + 600,
                                intent_id="i" * 32, intent_surface="S", intent_session=None,
                                delivered_announced=True)]
                d._save()
                self.saved_ack()  # recorded; its settling was never saved
                ack = privmsg("alp-solver-2", "[status] stopped as asked", "m-ack", channel="#alpha",
                              reply_to="m-int", t="2026-10-09T10:02:00.000Z")
                with inspection_fails(self.bridge.ACKS, how=how):
                    acks = self.bridge.Acks()  # a restart that cannot read its set
                    state = (self.bridge.Record(), self.bridge.Deliveries(), acks)
                    with self.assertRaises(OSError):
                        self.bridge.handle(ack, CFG, *state, replayed=True)
                    self.assertEqual([i["phase"] for i in self.rings(state[1])], ["accept"], "not settled")
                self.assertEqual(len(self.bridge.ACKS.read_text().splitlines()), 1, "not recorded again")
                self.bridge.handle(ack, CFG, *state, replayed=True)  # readable again: the replay
                self.assertEqual(self.rings(state[1]), [], "settled")
                self.assertEqual(len(self.bridge.ACKS.read_text().splitlines()), 1, "recorded once")

    # the existing torn and malformed line rule, for every JSONL reader (controls)
    def test_every_recovery_reader_skips_a_torn_line_and_keeps_the_rest(self):
        key = "held:outbox.claimed-1-a.jsonl"
        lines = ['{"torn', "[1, 2]",
                 json.dumps({"msgid": "m-int", "kind": "ring", "ok": True, "intent": "i" * 32,
                             "target": "alp-solver-2", "surface": "S", "session": None}),
                 json.dumps({"kind": "push", "msgid": key, "alert_key": key, "ok": True})]
        self.bridge.DELIVERIES.write_text("\n".join(lines) + "\n")
        self.bridge.ACKS.write_text('{"torn\n' + json.dumps({"msgid": "m-ack", "reply_to": "m-int",
                                                              "by": "alp-solver-2"}) + "\n")
        d = self.bridge.Deliveries()
        item = dict(self.item(), phase="intent", intent_id="i" * 32, intent_surface="S", intent_session=None)
        self.assertTrue(d._proof(item), "proof")
        self.assertTrue(d._earlier_interrupt(dict(self.item())), "replay detection")
        self.assertTrue(d.ledger_has(key), "alert export")
        acks = self.bridge.Acks()
        self.assertIn("m-ack", acks.seen, "the acknowledgement set")
        self.assertFalse(acks.add("m-ack", {"msgid": "m-ack"}), "a duplicate, not recorded again")

    # pending.json (already read directly: a control)
    def test_pending_state_with_a_stat_failure_stays_unloaded(self):
        d = self.ingest_unlogged(self.message())
        original = self.bridge.PENDING.read_bytes()
        with inspection_fails(self.bridge.PENDING):
            after = self.bridge.Deliveries()
            self.assertIsNotNone(after.load_error)
            self.passes(self.FakeAgent(), 2, deliveries=after)
        self.assertEqual(self.bridge.PENDING.read_bytes(), original)
        del d


class LiveAcknowledgementRetryTests(InterruptCase):
    """Code review slot 10, finding 2: a live acknowledgement is durable in
    acks.jsonl, but settling it fails in the ledger. The SAME Deliveries instance
    settles it at a later pass once the ledger works, with its owed work kept:
    no replay, no second interrupt, no second acknowledgement, no false dead
    letter."""

    def delivered(self, **extra):
        item = dict(self.item(), phase="accept", attempt=1, next_at=self.bridge.time.time() + 600,
                    intent_id="i" * 32, intent_surface="S", intent_session=None, delivered_announced=True)
        item.update(extra)
        return item

    def test_a_live_acknowledgement_is_settled_after_a_transient_ledger_failure(self):
        for owed in (False, True):
            with self.subTest(announcement_owed=owed):
                self.fresh()
                d = self.bridge.Deliveries()
                text = "interrupt → alp-solver-2: it was not working; instruction delivered; waiting for its acknowledgement"
                d.items = [self.delivered(**({"delivered_announced": False, "delivered_text": text} if owed else {}))]
                d._save()
                agent = self.FakeAgent()
                with mock.patch.object(self.bridge, "announce", lambda *a: False):
                    self.passes(agent, deliveries=d)  # the first scan is complete
                real = self.bridge.Deliveries._record

                def record(dd, item, ok, detail):
                    if str(detail).startswith("accepted by"):
                        raise OSError(5, "input/output error (fake)")
                    return real(dd, item, ok, detail)
                acks = self.bridge.Acks()
                with mock.patch.object(self.bridge.Deliveries, "_record", record):
                    with self.assertRaises(OSError):
                        self.bridge.accepted(CFG, d, acks, msgid="m-ack", reply_to="m-int", sender="alp-solver-2",
                                             channel="#alpha", at="2026-10-09T10:01:00Z", how="reply")
                    with mock.patch.object(self.bridge, "announce", lambda *a: False):
                        self.passes(agent, deliveries=d)  # the ledger still fails
                self.passes(agent, deliveries=d)  # the ledger works again: same instance, no replay
                for i in d.items:
                    i["next_at"] = 0
                self.passes(agent, 2, deliveries=d)
                self.assertEqual([i for i in d.items if i["kind"] == "ring"], [], "settled")
                self.assertEqual([r for r in self.dead_letters() if r.get("msgid") == "m-int"], [],
                                 "no false unacknowledged dead letter")
                self.assertEqual(agent.keys, [], "never rung")
                self.assertEqual(len([r for r in self.ledger() if str(r.get("detail")).startswith("accepted by")]), 1)
                self.assertEqual(len(self.bridge.ACKS.read_text().splitlines()), 1, "one acknowledgement")
                self.assertEqual(len(self.announcements()), 1 if owed else 0, "owed work kept and done")


class AlertIdentityTests(InterruptCase):
    """The final round's re-check: an owner alert's dead-letter identity carries
    its recipient and the alert itself, so the alerts for two recipients of one
    message (or two alerts for one) are never collapsed into one record."""

    def test_two_recipients_alerts_are_each_dead_lettered(self):
        d = self.bridge.Deliveries()
        for target in ("alp-solver-2", "alp-solver-3"):
            d._alert(dict(self.item(), target=target), f"delivery to {target} failed (fake)")
        d._alert(dict(self.item(), target="alp-solver-3"), "alp-solver-3 has not taken a post (fake)")
        with mock.patch.object(self.bridge, "push_owner", lambda cfg, item: (False, "unreachable (fake)")):
            for _ in range(len(self.bridge.BACKOFF) + 2):
                for i in d.items:
                    i["next_at"] = 0
                self.passes(self.FakeAgent(), deliveries=d)
        self.assertEqual(d.items, [])
        pushes = [r for r in self.dead_letters() if r.get("msgid") == "m-int" and r.get("kind") == "push"]
        self.assertEqual(len(pushes), 3, "one dead-letter record per alert")


class OwedAnnouncementExpiryTests(InterruptCase):
    """Design test 46 (code review slot 8, finding 1): an announcement still
    owed when the acknowledgement wait expires survives the dead letter and a
    restart, and is made once, with no second interrupt."""

    def test_an_owed_announcement_survives_the_expiry_and_a_restart(self):
        agent = self.FakeAgent()
        d = self.queued()
        with mock.patch.object(self.bridge, "announce", lambda *a: False):
            self.passes(agent, deliveries=d)
            d.items[0]["next_at"] = 0  # the acknowledgement wait is over
            self.passes(agent, deliveries=d)
        self.assertEqual(len([r for r in self.dead_letters() if r.get("msgid") == "m-int"]), 1, "dead-lettered")
        self.assertEqual(self.announcements(), [])
        second = self.FakeAgent()
        after = self.passes(second, 2)  # a restart, with announcing working again
        self.assertEqual(len(self.announcements()), 1, "the owed announcement is made, once")
        self.assertEqual(self.interrupts(agent, second), 1, "never rung again")
        self.assertEqual([i for i in after.items if i["kind"] == "ring"], [])


class AnnounceTests(BridgeCase):
    """Tests 29 and 36: the bridge's own announcement, its fallback file, and E9."""

    item = {"kind": "ring", "msgid": "m-held", "channel": "#alpha", "target": "alp-solver-2", "project": "alpha"}

    def announce(self, reason="held"):
        return self.bridge.announce(CFG, self.item, "interrupt → alp-solver-2: held", reason)

    def test_with_its_requests_failing_it_publishes_a_fallback_file_without_outbox_lock(self):
        import fcntl
        holder = open(self.chatlib.STATE_DIR / "outbox.lock", "a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            self.server.refuse_login = {"chatbridge"}
            with mock.patch.object(self.bridge, "bridge_client", lambda authority=None: self.ds.Client(None)):
                self.assertTrue(self.announce(), "durable: the flag may be saved")
            self.assertEqual(len(self.fallback_files()), 1)
            self.server.refuse_login = set()
            self.flush()
            self.flush()
        finally:
            holder.close()
        texts = [m["text"] for m in self.server.published]
        self.assertEqual(len(texts), 1)
        self.assertIn("chatbridge: interrupt", texts[0])

    def test_with_nothing_durable_the_flag_stays_unset_and_a_repeat_is_one_entry(self):
        self.server.refuse_login = {"chatbridge"}
        with mock.patch.object(self.bridge, "bridge_client", lambda authority=None: self.ds.Client(None)), \
                mock.patch.object(self.ds, "publish_fallback", side_effect=OSError("disk full")):
            self.assertFalse(self.announce())
        self.assertTrue(self.announce())  # raised again at the next pass: queued by Q0 now
        self.assertTrue(self.announce())  # and again: the same X, one obligation
        self.server.refuse_login = set()
        self.flush()
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(len(self.server.published), 1)

    def test_deliveries_saves_held_announced_only_after_the_announcement_is_durable(self):
        d = self.bridge.Deliveries()
        item = dict(self.item, attempt=0, next_at=0, sender="pat", text="!! stop", interrupt=True, phase="deliver")
        d.items.append(item)
        with mock.patch.object(self.bridge, "ring_agent", lambda cfg, it: (None, "held: waiting on a prompt")), \
                mock.patch.object(self.bridge, "announce", lambda *a: False):
            d.process(CFG)
        self.assertNotIn("held_announced", d.items[0])
        with mock.patch.object(self.bridge, "ring_agent", lambda cfg, it: (None, "held: waiting on a prompt")), \
                mock.patch.object(self.bridge, "announce", lambda *a: True):
            d.items[0]["next_at"] = 0
            d.process(CFG)
        self.assertTrue(d.items[0]["held_announced"])

    def test_a_crash_after_e1_and_before_a1_is_adopted_after_the_restart_and_sent_once(self):
        """Section 5.6, case 15 (correction (e))."""
        real = self.authority._dispatch

        def crash_at_a1(request, bridge):
            if request.get("op") == "attempt":
                raise Crash()  # the bridge is killed: A1's transaction rolls back, E1's stands
            return real(request, bridge)
        with mock.patch.object(self.ds, "PROBES", self.probes), \
                mock.patch.object(self.authority, "_dispatch", crash_at_a1), self.assertRaises(Crash):
            self.announce()
        [e] = self.entries()
        self.assertEqual((e["state"], e["owner_kind"], e["origin"]), ("open", "direct", "announce"))
        self.restart_bridge()
        self.flush()
        e = self.entry()
        self.assertEqual(e["state"], "done", "E9 adopted it; existence alone suppressed nothing")
        self.assertEqual(len(self.server.published), 1)
        with mock.patch.object(self.ds, "PROBES", self.probes):
            self.assertTrue(self.announce())  # the repeat finds the obligation
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(len(self.server.published), 1)

    def test_an_ordinary_post_killed_after_e1_is_still_abandoned(self):
        real = self.authority.handle

        def crash_at_a1(request, **kw):
            if request.get("op") == "attempt":
                raise Crash()
            return real(request, **kw)
        with mock.patch.object(self.authority, "handle", crash_at_a1), self.assertRaises(Crash):
            self.pchat("post", "#alpha", "[status] killed")
        self.probes.world["gone"].add(CLIENT_PID)
        self.flush()
        self.assertEqual(self.entry()["state"], "abandoned", "D1: its remainder is not adopted")
        self.assertEqual(self.server.published, [])


class AuthorityThreadTests(BridgeCase):
    """The authority thread: in-process requests from another thread, and a client, served together."""

    def test_the_thread_serves_a_client_and_the_bridges_requests(self):
        self.authority.run_in_thread()
        try:
            mine, theirs = socket.socketpair()
            done = threading.Event()
            self.authority.mutex.acquire()
            try:
                self.authority.adopt(theirs, (os.getuid(), CLIENT_PID))
            finally:
                self.authority.mutex.release()
            self.authority._wake()
            w = self.probes.view(CLIENT_PID).identity("c")
            req = {"v": self.ds.PROTOCOL, "op": "queue", "request_id": "r1", "writer": w, "id": "q" * 32,
                   "entry": {"channel": "#alpha", "as": "alp-solver-2", "text": "x"}}
            mine.sendall(self.ds.encode_frame(req, self.ds.FRAME_MAX))
            mine.settimeout(5)
            buf = b""
            while True:
                buf += mine.recv(65536)
                reply, _ = self.ds.decode_frame(buf, self.ds.REPLY_MAX)
                if reply is not None:
                    break
            self.assertEqual(reply["result"], "ok")
            count = self.authority.run_tx(lambda s: s.con.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
            self.assertEqual(count, 1)
            done.set()
            mine.close()
        finally:
            self.authority.stopping = True
            self.authority._wake()
            self.authority.thread.join(5)
            self.authority.thread = None
            self.authority.stopping = False


if __name__ == "__main__":
    unittest.main()
