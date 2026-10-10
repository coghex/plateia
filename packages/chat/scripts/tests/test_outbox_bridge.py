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
        self.assertIsNone(state[3].marks.get("#alpha"), "the checkpoint stays before the failed message")

    def test_the_bridges_loop_indexes_verified_messages(self):
        from test_bridge import FakeServer as ScriptServer, join, privmsg
        cfgfile = self.chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        state = (self.bridge.Record(), self.bridge.Deliveries(), self.bridge.Acks(), self.bridge.Checkpoints())
        server = ScriptServer([join("#alpha"), privmsg("pat", "hello", "m1", channel="#alpha",
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
