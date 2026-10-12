"""Draining, paused clients and lock ordering with real threads (docs/writer_admission.md sections 4.5, 7.1 and
7.2; design tests 1 and 10, the review's addition A2, and the ordering and deadlock schedule). The authority runs
on its own thread and every client reaches it over a socketpair it adopts (the sandbox refuses real socket
connects), so no client and no bridge flush ever touches the database from another thread. Every `L_adm`
acquisition must be LOCK_NB, and the authority's thread must never sleep: it answers `maintenance` at once.
Invented accounts, channels and operations only."""
import queue
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from admission_fakes import HeldCase, exclusive_lock, only_nonblocking_flocks  # noqa: E402
from outbox_fakes import CFG, CLIENT_PID, LONG, Crash  # noqa: E402

BOUND = 20  # real seconds any thread may take to finish


class PairTransport:
    """A client's transport to the authority's thread: each request is a new socketpair the authority adopts, as
    its accept path adopts a connection, and one frame each way."""

    def __init__(self, case, pid=CLIENT_PID):
        self.case, self.pid = case, pid

    def exchange(self, request):
        case, ds = self.case, self.case.ds
        mine, theirs = socket.socketpair()
        case.adopts.put((theirs, (case.uid, self.pid)))
        case.authority._wake()
        try:
            mine.settimeout(BOUND)
            mine.sendall(ds.encode_frame(request, ds.FRAME_MAX))
            buf = b""
            while True:
                data = mine.recv(65536)
                if not data:
                    return None
                buf += data
                reply, _ = ds.decode_frame(buf, ds.REPLY_MAX)
                if reply is not None:
                    return reply
        finally:
            mine.close()


class ThreadedCase(HeldCase):
    def setUp(self):
        super().setUp()
        import os
        self.uid = os.getuid()
        self.adopts = queue.Queue()
        self.authority_flocks = []
        self.authority_sleeps = []
        authority = self.authority

        def loop():  # the authority thread's loop, adopting what the transports queue (adopt is its thread's work)
            while not authority.stopping:
                while True:
                    try:
                        item = self.adopts.get_nowait()
                    except queue.Empty:
                        break
                    authority.adopt(*item)
                try:
                    authority.serve_once(0.01)
                except Exception as err:  # as the real loop: the thread stays up
                    authority.log(f"loop error {err!r}")
        authority._loop = loop
        authority.run_in_thread()
        self.threads = []

    def client(self):
        if not self.available:
            return self.ds.Client(None, clock=self.clock)
        return self.ds.Client(PairTransport(self), clock=self.clock)

    def spawn(self, fn, *args):
        result = {}

        def run():
            try:
                result["value"] = fn(*args)
            except Crash:
                result["killed"] = True
            except BaseException as err:  # noqa: BLE001
                result["error"] = err
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.threads.append(thread)
        return result, thread

    def tearDown(self):
        for t in self.threads:
            t.join(BOUND)
        super().tearDown()

    def post_as_a_thread(self, text):
        return self.spawn(lambda: self.chatlib.post("#alpha", text, "alp-solver-2", CFG))

    def join(self, thread):
        thread.join(BOUND)
        self.assertFalse(thread.is_alive(), "a thread did not finish within its bound")


class Pause:
    """Stop a call at a named place, once, until the test lets it go; others pass untouched."""

    def __init__(self, case, target, name, match):
        self.at, self.go = threading.Event(), threading.Event()
        owner, self.original = target, getattr(target, name)

        def paused(*args, **kw):
            if match(*args, **kw) and not self.at.is_set():
                self.at.set()
                if not self.go.wait(BOUND):
                    raise AssertionError("never released")
                if getattr(self, "kill", False):
                    raise Crash(name)
            return self.original(*args, **kw)
        patch = mock.patch.object(owner, name, paused)
        patch.start()
        case.addCleanup(patch.stop)

    def wait(self, test):
        test.assertTrue(self.at.wait(BOUND), "the call never reached the place")

    def release(self, kill=False):
        self.kill = kill
        self.go.set()


class DrainingTests(ThreadedCase):
    def test_case_1_a_call_in_progress_finishes_while_new_calls_are_refused_and_the_drain_waits_for_it(self):
        chatlib = self.chatlib
        a_paused = Pause(self, chatlib._Call, "send_parts", lambda call, *a, **k: call.row["text"] == "A text")
        c_paused = Pause(self, chatlib, "fix_parts", lambda text, cont, conn: text == "C text")
        a, a_thread = self.post_as_a_thread("A text")        # created its entry, and has not attempted a part
        a_paused.wait(self)
        c, c_thread = self.post_as_a_thread("C text")        # its section is open, and it has created nothing
        c_paused.wait(self)

        owner = self.owner()
        owner.acquire()                                      # draining
        b_code, b_out = self.pchat("post", "#alpha", "B text")  # a new call: refused at its section's start
        self.assertEqual(b_code, 5, b_out)
        self.assertEqual(len(self.sink()), 1)

        with only_nonblocking_flocks(self.ds):
            first = owner.drain(wait=2.0)
            self.assertEqual((first["drained"], first["landed"]), (False, False))
            self.assertGreater(first["attempts"], 3)
            self.assertEqual(self.record().state, "draining", "partial only after the sections end")

            c_paused.release()                               # C's create reaches the authority: a new entry
            self.join(c_thread)
            self.assertIsInstance(c.get("error"), chatlib.Held, "refused into the sink though its section was open")
            self.assertEqual(len(self.sink()), 2)
            self.assertFalse(owner.drain(wait=1.0)["drained"], "A's section is still open")

            a_paused.release()                               # A's attempt, outcome and done are all admitted
            self.join(a_thread)
            self.assertEqual(a.get("value"), 1, a)
            second = owner.drain(wait=2.0)
        self.assertEqual((second["drained"], second["landed"]), (True, True))
        self.assertEqual(self.record().state, "partial")
        self.assertEqual(self.server.texts(), ["A text"], "the one call in progress finished, once")
        self.assertEqual(self.entry_ids().__len__(), 1)
        self.assertEqual(self.entry_now(self.entry_ids()[0])["state"], "done")
        ids = {r["id"] for r in self.sink_rows()}
        self.assertEqual(len(ids), 2, "B's and C's calls, each with its own entry id X")
        self.assertTrue(all(i != self.entry_ids()[0] for i in ids))
        outcomes = self.journal.outcomes("write-record")
        self.assertEqual([o["drained"] for o in outcomes], [False, False, True],
                         "the journal shows every drain, and its tries")
        self.assertTrue(all(o["attempts"] > 1 for o in outcomes[:2]))

    def test_case_10_a_client_paused_after_its_committed_attempt_reply_finishes_and_each_part_is_posted_once(self):
        paused = Pause(self, self.chatlib._Call, "record", lambda call, *a, **k: call.row["text"] == LONG)
        a, a_thread = self.post_as_a_thread(LONG)
        paused.wait(self)                                    # part 1's bytes are sent; its outcome is not recorded
        self.assertEqual(len(self.server.published), 1)
        owner = self.owner()
        owner.acquire()
        with only_nonblocking_flocks(self.ds):
            self.assertFalse(owner.drain(wait=2.0)["drained"], "the drain does not complete while it is paused")
            # the authority keeps serving every other client (I-11): reads, and refusals, are answered at once
            self.assertEqual(self.client().call("status", None)["status"]["maintenance"]["state"], "draining")
            self.assertEqual(self.pchat("post", "#alpha", "[status] meanwhile")[0], 5)
            paused.release()
            self.join(a_thread)
            self.assertTrue(owner.drain(wait=2.0)["drained"])
        self.assertEqual(a.get("value"), 30, a)  # the lines it sent: three batches
        entry = self.entry_now(self.entry_ids()[0])
        self.assertEqual([p["state"] for p in entry["parts"]], ["confirmed"] * 3)
        self.assertEqual(entry["state"], "done")
        self.assertEqual(len(self.server.published), 3, "each part confirmed and posted once")
        self.assertEqual([x["state"] for x in self.rows("SELECT state FROM attempts ORDER BY n")], ["confirmed"] * 3)

    def test_case_10_killed_instead_the_drain_completes_and_nothing_is_resent_before_the_check(self):
        paused = Pause(self, self.chatlib._Call, "record", lambda call, *a, **k: call.row["text"] == LONG)
        a, a_thread = self.post_as_a_thread(LONG)
        paused.wait(self)
        owner = self.owner()
        owner.acquire()
        self.assertFalse(owner.drain(wait=1.0)["drained"])
        paused.release(kill=True)                            # the process dies: the kernel releases its flock
        self.join(a_thread)
        self.assertTrue(a.get("killed"))
        self.assertTrue(owner.drain(wait=2.0)["drained"], "the dead client no longer holds the lock")
        owner.release()
        self.probes.world["gone"].add(CLIENT_PID)            # the bridge's probe: that writer is gone
        for _ in range(2):
            self.flush()
        attempts = self.rows("SELECT n, state, end_kind FROM attempts")
        self.assertEqual([(x["n"], x["state"], x["end_kind"]) for x in attempts], [(0, "ended", "writer_gone")],
                         "ended by A8, and to be decided against the record")
        self.assertEqual(len(self.server.published), 1, "part 1 only: nothing was resent before that decision")
        self.assertEqual([p["state"] for p in self.entry_now(self.entry_ids()[0])["parts"]][:1], ["uncertain"])


class SelectorTests(ThreadedCase):
    """A2: ADMIT_WAIT never sleeps or spins on the authority's selector thread."""

    def test_an_exclusive_holder_alongside_status_query_and_many_clients(self):
        self.post("[status] data")
        entry_id = self.entry_ids()[0]
        real_sleep, calls = time.sleep, []

        def sleep(seconds):
            if threading.current_thread().name == "outbox-authority":
                self.authority_sleeps.append(seconds)
            return real_sleep(seconds)
        replies = {}

        def ask(label, op, **args):
            replies[label] = self.client().call(op, self.writer(), **args)
        with mock.patch.object(self.ds.time, "sleep", sleep), only_nonblocking_flocks(self.ds, calls):
            started = time.monotonic()
            with exclusive_lock(self.paths):
                workers = [threading.Thread(target=ask, args=("status", "status")),
                           threading.Thread(target=ask, args=("query", "query"), kwargs={"id": entry_id})]
                workers += [threading.Thread(target=ask, args=(f"create{i}", "create"),
                                             kwargs=self.create_args(f"{i:032d}")) for i in range(6)]
                for w in workers:
                    w.start()
                for w in workers:
                    w.join(BOUND)
                    self.assertFalse(w.is_alive())
            elapsed = time.monotonic() - started
        self.assertEqual(replies["status"]["result"], "ok")
        self.assertEqual(replies["query"]["result"], "ok")
        for i in range(6):
            self.assertEqual(replies[f"create{i}"]["result"], "maintenance", "answered at once, nothing committed")
        self.assertLess(elapsed, self.ds.ADMIT_WAIT, "answered well within ADMIT_WAIT")
        self.assertEqual(self.authority_sleeps, [], "the selector thread never slept")
        on_selector = [op for name, op in calls if name == "outbox-authority"]
        self.assertEqual(len(on_selector), 6 * 2 - 6, "one non-blocking try per mutation (a miss holds nothing to "
                         "release), nothing for the two read-only requests, and no spin")
        self.assertEqual(self.entry_ids(), [entry_id], "none of the six created anything")
        # and with the holder gone, the same requests run
        self.assertEqual(self.client().call("create", self.writer(), **self.create_args("e" * 32))["result"], "ok")

    def test_an_exclusive_holder_never_stalls_the_authority_for_a_second_client(self):
        with exclusive_lock(self.paths):
            started = time.monotonic()
            for _ in range(3):
                self.assertEqual(self.client().call("status", None)["result"], "ok")
            self.assertLess(time.monotonic() - started, 5)


class ScheduleTests(ThreadedCase):
    def test_ordering_and_deadlock_schedule(self):
        """An open client section, a bridge flush step causing in-process transactions, and the owner's acquisition
        run concurrently. Every thread finishes within its bound, no acquisition of L_adm ever blocks, and the
        authority answers clients throughout."""
        with self.unavailable():  # something for the flush to do: a queued call, which the bridge will import
            self.assertEqual(self.pchat("post", "#alpha", "[status] queued before")[0], 3)
        stop, outcomes = threading.Event(), {"flushes": 0, "holds": 0, "errors": []}

        def flush_loop():
            while not stop.is_set():
                try:
                    self.bridge.flush_outbox(CFG, authority=self.authority)
                    outcomes["flushes"] += 1
                except self.bridge.Hold:
                    outcomes["holds"] += 1
                except Exception as err:  # noqa: BLE001
                    outcomes["errors"].append(err)
                time.sleep(0.01)

        paused = Pause(self, self.chatlib._Call, "send_parts", lambda call, *a, **k: call.row["text"] == "A text")
        with only_nonblocking_flocks(self.ds):
            a, a_thread = self.post_as_a_thread("A text")
            paused.wait(self)
            flusher, flush_thread = self.spawn(flush_loop)
            deadline = time.monotonic() + BOUND
            while outcomes["flushes"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(outcomes["flushes"], 1, "the bridge flushed while a client section was open")

            owner = self.owner()
            owner.acquire()                                  # draining: the bridge's next section holds
            while outcomes["holds"] < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(outcomes["holds"], 1, "a flush step found maintenance held and started nothing")
            for _ in range(5):                               # the authority answers throughout
                self.assertEqual(self.client().call("status", None)["status"]["maintenance"]["state"], "draining")
            self.assertFalse(owner.drain(wait=1.0)["drained"], "waiting for the open section, holding nothing")

            paused.release()
            self.join(a_thread)
            self.assertEqual(a.get("value"), 1, a)
            self.assertTrue(owner.drain(wait=2.0)["drained"])
            owner.advance("full", boot="boot-B", legacy="retired-and-inhibited")
            owner.release()
            flushed = outcomes["flushes"]
            while outcomes["flushes"] < flushed + 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            stop.set()
            self.join(flush_thread)
        self.assertGreaterEqual(outcomes["flushes"], flushed + 2, "and after the release the flush runs again")
        self.assertEqual(outcomes["errors"], [])
        self.assertIn("[status] queued before", [t.split(" (delayed")[0] for t in self.server.texts()])
        self.assertEqual(self.server.texts().count("A text"), 1)


if __name__ == "__main__":
    unittest.main()
