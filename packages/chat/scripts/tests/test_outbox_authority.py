"""The bridge-only authority and its request protocol (docs/chat_outbox_state.md
revision 8, section 9: tests 25, 26, 28 and 31). The authority's own selector
loop serves socketpair ends: the sandbox refuses every socket connect, and a
socketpair connects nothing. Peer credentials, processes and clocks are faked.
Invented data only."""
import json
import os
import socket
import sqlite3
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from outbox_fakes import CLIENT_PID, T0, OutboxCase  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent


class Conn:
    """The client end of a socketpair the authority serves."""

    def __init__(self, case, creds=None):
        self.case = case
        self.mine, theirs = socket.socketpair()
        self.peer = case.authority.adopt(theirs, creds or (os.getuid(), CLIENT_PID))

    def send(self, request, cut=None):
        data = self.case.ds.encode_frame(request, self.case.ds.FRAME_MAX)
        self.mine.sendall(data if cut is None else data[:cut])

    def reply(self, turns=5):
        self.mine.settimeout(0.05)
        buf = b""
        for _ in range(turns):
            self.case.authority.serve_once(0.01)
            try:
                chunk = self.mine.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                return None
            buf += chunk
            obj, _ = self.case.ds.decode_frame(buf, self.case.ds.REPLY_MAX)
            if obj is not None:
                return obj
        return None

    def closed_by_authority(self):
        self.mine.settimeout(0.05)
        try:
            return self.mine.recv(1) == b""
        except socket.timeout:
            return False

    def close(self):
        self.mine.close()


class AuthorityCase(OutboxCase):
    def setUp(self):
        super().setUp()
        self.conns = []

    def tearDown(self):
        for c in self.conns:
            c.close()
        super().tearDown()

    def conn(self, creds=None):
        c = Conn(self, creds)
        self.conns.append(c)
        return c

    def request(self, op, writer=None, **args):
        return {"v": self.ds.PROTOCOL, "op": op, "request_id": f"r{time.monotonic_ns()}",
                "writer": writer or self.probes.view(CLIENT_PID).identity("127.0.0.1:1>s"), **args}

    def create(self, entry_id, text="[status] x", writer=None):
        return self.request("create", writer, id=entry_id, entry={"channel": "#alpha", "as": "alp-solver-2",
                                                                    "text": text},
                            parts=[{"kind": "line", "lines": [[text, False]], "text": text}])


class PausedClientTests(AuthorityCase):
    """Test 25 (design round 4, finding 2): a client stopped at any point never
    stalls the authority, and no transaction waits for it."""

    def test_half_a_frame_commits_nothing_and_closes_at_req_wait(self):
        c = self.conn()
        c.send(self.create("a" * 32), cut=10)
        self.authority.serve_once(0)
        # meanwhile the bridge's own request runs at once
        self.assertEqual(self.tx(lambda s: s.status())["waiting"], 0)
        self.clock.advance(self.ds.REQ_WAIT + 0.1)
        self.authority.serve_once(0)
        self.assertTrue(c.closed_by_authority())
        self.assertEqual(self.entries(), [], "nothing committed for an incomplete frame")

    def test_a_whole_frame_commits_and_its_unread_reply_is_recovered_by_replay(self):
        c = self.conn()
        req = self.create("b" * 32)
        c.send(req)
        self.authority.serve_once(0)
        self.assertEqual(len(self.entries()), 1, "its TX ran, and committed before any reply")
        c.close()  # it never read its reply
        again = self.conn()
        again.send(dict(req, request_id="again"))
        self.assertEqual(again.reply()["result"], "replay")

    def test_a_client_stopped_after_its_a1_reply_keeps_its_writing_attempt(self):
        c = self.conn()
        c.send(self.create("c" * 32))
        c.reply()
        c2 = self.conn()
        c2.send(self.request("attempt", id="c" * 32, n=0, generation=0, a1_nonce="n"))
        self.assertEqual(c2.reply()["result"], "ok")
        for _ in range(3):
            self.clock.advance(3600)
            self.flush()
        self.assertEqual([a["state"] for a in self.attempts()], ["writing"], "never ended, voided or retried by time")

    def test_another_accounts_entry_is_delivered_while_a_client_is_stopped_mid_request(self):
        self.server.refuse_login = {"sam"}
        self.post("[status] beta green", channel="#beta", account="sam")
        self.server.refuse_login = set()
        c = self.conn()
        c.send(self.create("d" * 32), cut=7)
        self.authority.serve_once(0)
        self.flush()
        self.assertEqual([m["text"].split(" (delayed")[0] for m in self.server.published], ["[status] beta green"])

    def test_floods_are_closed_or_busy_at_once_with_no_transaction(self):
        with mock.patch.object(self.ds, "CONN_MAX", 2):
            self.conn(), self.conn()
            extra = Conn(self)
            self.conns.append(extra)
            self.assertIsNone(extra.peer)
            self.assertTrue(extra.closed_by_authority())
        for c in self.conns:
            c.close()
        self.conns.clear()
        for peer in list(self.authority.peers.values()):
            self.authority._drop(peer)
        with mock.patch.object(self.ds, "QUEUE_MAX", 1):
            a, b = self.conn(), self.conn()
            a.send(self.create("e" * 32))
            b.send(self.create("f" * 32))
            self.authority.serve_once(0)
            replies = [a.reply(), b.reply()]
        self.assertIn("busy", [r and r["result"] for r in replies])
        self.assertEqual(len(self.entries()), 1, "the busy one ran no transaction")

    def test_bridge_requests_still_run_at_every_other_turn(self):
        ran = []
        job = self.ds._Job(lambda s: ran.append(self.authority.turn) or True)
        self.authority.jobs.append(job)
        for i in range(3):
            c = self.conn()
            c.send(self.create(f"{i}" * 32))
        self.authority.serve_once(0)
        self.assertEqual(ran, [1], "the bridge's request ran in the first turn, beside one client request")
        self.assertEqual(len(self.entries()), 1)

    def test_an_in_process_request_still_queued_at_auth_wait_is_cancelled_and_never_runs(self):
        ran = []
        self.authority.thread = threading.Thread(target=lambda: None)  # a loop that never serves
        try:
            with mock.patch.object(self.ds, "AUTH_WAIT", 0.05), \
                    mock.patch.object(self.authority, "mono", time.monotonic):
                result = self.authority.run_tx(lambda s: ran.append(1))
        finally:
            self.authority.thread = None
        self.assertIs(result, self.ds.CANCELLED)
        self.authority.serve_once(0)
        self.assertEqual(ran, [])

    def test_negative_control_a_client_holding_a_database_transaction_stalls_other_writers(self):
        db = _isolation.HOME / "control.db"
        holder = sqlite3.connect(str(db), isolation_level=None, timeout=0.05)
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("CREATE TABLE t (x)")
        holder.execute("BEGIN IMMEDIATE")  # the stopped client
        other = sqlite3.connect(str(db), isolation_level=None, timeout=0.05)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("BEGIN IMMEDIATE")
        finally:
            holder.close()
            other.close()


class RequestCrashTests(AuthorityCase):
    """Test 26: each op's reply lost after its commit, then a replay; error,
    conflict, restarts and an older epoch's reply."""

    def test_every_op_replays_to_the_same_state(self):
        w = self.probes.view(CLIENT_PID).identity("c1")
        reqs = [self.create("a" * 32, writer=w),
                self.request("attempt", w, id="a" * 32, n=0, generation=0, a1_nonce="n1"),
                self.request("outcome", w, id="a" * 32, n=0, generation=1, a1_nonce="n1", outcome="confirmed",
                             final_at=T0),
                self.request("done", w, id="a" * 32),
                self.request("queue", w, id="b" * 32, entry={"channel": "#alpha", "as": "x", "text": "q"})]
        for req in reqs:
            with self.subTest(op=req["op"]):
                first = self.authority.handle(req)
                self.assertEqual(first["result"], "ok")
                before = self.entries()
                second = self.authority.handle(dict(req, request_id="replayed"))
                self.assertEqual(second["result"], "replay")
                self.assertEqual(self.entries(), before)

    def test_handoff_void_and_retire_replay_too(self):
        w = self.probes.view(CLIENT_PID).identity("c1")
        self.authority.handle(self.create("a" * 32, writer=w))
        self.authority.handle(self.request("attempt", w, id="a" * 32, n=0, generation=0, a1_nonce="n1"))
        att = {"kind": "void", "n": 0, "generation": 1, "a1_nonce": "n1"}
        for req in (self.request("void", w, id="a" * 32, attestation=att),
                    self.request("handoff", w, id="a" * 32)):
            self.assertEqual(self.authority.handle(req)["result"], "ok")
            self.assertEqual(self.authority.handle(dict(req, request_id="x"))["result"], "replay")

    def test_an_error_reply_is_treated_as_missing_and_a_conflict_changes_nothing(self):
        client = self.client()
        with mock.patch.object(self.authority, "handle", lambda r, **k: {
                "v": self.ds.PROTOCOL, "request_id": r["request_id"], "result": "error", "authority_epoch": 1}):
            self.assertIsNone(client.call("query", {}, id="x"))
        w = self.probes.view(CLIENT_PID).identity("c1")
        reply = self.authority.handle(self.request("attempt", w, id="nope", n=0, generation=0, a1_nonce="n"))
        self.assertEqual(reply["result"], "conflict")
        self.assertEqual(self.attempts(), [])

    def test_a_restart_between_two_requests_of_one_call_makes_the_client_query(self):
        """Section 5.6, case 13: the outcome is retried until the new authority answers."""
        client = self.client()
        w = self.probes.view(CLIENT_PID).identity("c1")
        self.assertEqual(client.call("query", w, id="x")["authority_epoch"], 1)
        self.restart_bridge()
        self.assertEqual(client.call("query", w, id="x")["authority_epoch"], 2)
        self.assertTrue(client.restarted)

    def test_an_older_epochs_reply_is_ignored(self):
        client = self.client()
        client.epoch = 5
        self.assertIsNone(client.call("query", {}, id="x"))

    def test_no_reply_is_ever_written_before_its_commit(self):
        order = []
        real_tx = self.authority.store.tx

        import contextlib

        @contextlib.contextmanager
        def tx():
            with real_tx() as con:
                yield con
            order.append("commit")
        real_send = self.authority._send
        with mock.patch.object(self.authority.store, "tx", tx), \
                mock.patch.object(self.authority, "_send", lambda peer, reply: order.append("reply")
                                  or real_send(peer, reply)):
            c = self.conn()
            c.send(self.create("z" * 32))
            c.reply()
        self.assertEqual(order[:2], ["commit", "reply"])


class UnavailableTests(AuthorityCase):
    """Test 28: the material change. With the authority unavailable in any way, a
    call sends no byte without a committed attempt reply, and exits 3 only once
    its fallback file is durable; the bridge delivers it once on its return."""

    def transport(self, how):
        case = self

        class T:
            def exchange(self, request):
                if how == "no socket":
                    raise FileNotFoundError("no outbox.sock")
                if how == "refused":
                    raise ConnectionRefusedError()
                if how == "timeout":
                    raise socket.timeout()
                if how in ("busy", "error", "refused-version"):
                    return {"v": case.ds.PROTOCOL, "request_id": request["request_id"], "authority_epoch": 1,
                            "result": {"refused-version": "refused"}.get(how, how)}
                if how == "wrong peer":  # the authority closes the connection unread: EOF
                    return None
                raise AssertionError(how)
        return T()

    def test_each_way_of_being_unavailable_queues_the_whole_call_once(self):
        ways = ("no socket", "refused", "wrong peer", "refused-version", "busy", "error", "timeout")
        for how in ways:
            with self.subTest(how=how):
                self.fresh()
                factory = lambda: self.ds.Client(self.transport(how), clock=self.clock)  # noqa: E731
                with mock.patch.object(self.chatlib, "client_factory", factory):
                    code, said = self.pchat("post", "#alpha", "[status] queued while away")
                self.assertEqual(code, 3, said)
                self.assertEqual(self.server.published, [], "no byte without a committed attempt reply")
                self.assertEqual(len(self.fallback_files()), 1)
                self.flush()
                self.flush()
                self.assertEqual([m["text"].split(" (delayed")[0] for m in self.server.published],
                                 ["[status] queued while away"])

    def test_pchat_ack_and_an_agent_notice_queue_the_same_way(self):
        import agentcli
        import contextlib
        import io
        factory = lambda: self.ds.Client(None, clock=self.clock)  # noqa: E731
        with mock.patch.object(self.chatlib, "client_factory", factory):
            self.assertEqual(self.pchat("ack", "#alpha", "m1", account="alp-manager")[0], 3)
            with contextlib.redirect_stderr(io.StringIO()):
                agentcli.notify({"channel": "#alpha", "name": "alp-solver-2"}, "start")
        self.assertEqual(len(self.fallback_files()), 2)
        self.assertEqual(self.server.published, [])
        self.flush()
        self.assertEqual(len(self.server.published), 2)

    def test_mid_post_the_remainder_is_handed_off_and_confirmed_parts_never_resent(self):
        real = self.authority.handle
        calls = {"n": 0}

        def flaky(request, **kw):
            if request.get("op") == "attempt" and request.get("n") == 1:
                return None  # the authority stops answering after part 0
            if request.get("op") == "handoff":
                return None
            return real(request, **kw)
        with mock.patch.object(self.authority, "handle", flaky):
            code, _ = self.pchat("post", "#alpha", "[status] a\n" + "b " * 2500)
        self.assertEqual(code, 3)
        self.flush()
        texts = [m["text"] for m in self.server.published]
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(self.entry()["state"], "done")
        del calls

    def test_a_socket_path_over_the_limit_queues_without_connecting(self):
        long_path = _isolation.HOME / ("x" * 200) / "outbox.sock"
        transport = self.ds.UnixTransport(long_path)
        with mock.patch.object(self.ds.socket, "socket", side_effect=AssertionError("connected")):
            self.assertIsNone(transport.exchange({"v": self.ds.PROTOCOL}))


class OneOpenerTests(AuthorityCase):
    """Test 31."""

    def test_a_second_connection_gets_busy_at_once_while_the_authority_runs(self):
        other = sqlite3.connect(str(self.authority.paths.db), timeout=0.05, isolation_level=None)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("SELECT * FROM entries").fetchall()
        finally:
            other.close()

    def test_a_second_bridge_cannot_take_the_authority_lock(self):
        second = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, probes=self.probes)
        with self.assertRaises(self.ds.Unavailable):
            second.start()
        self.assertIsNone(second.store.con)

    def test_a_foreign_holder_at_startup_fails_the_start_within_open_wait(self):
        self.authority.close()
        foreign = sqlite3.connect(str(self.authority.paths.db), isolation_level=None)
        foreign.execute("BEGIN EXCLUSIVE")
        try:
            with mock.patch.object(self.ds, "OPEN_WAIT", 0.1):
                a = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, probes=self.probes)
                started = time.monotonic()
                with self.assertRaises(self.ds.Unavailable):
                    a.start()
                self.assertLess(time.monotonic() - started, 2)
        finally:
            foreign.close()
        self.authority = self.new_authority()  # its lock untouched; the next start succeeds

    def test_a_database_made_by_other_code_is_refused(self):
        self.authority.close()
        db = self.authority.paths.db
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        other = sqlite3.connect(str(db))
        other.execute("CREATE TABLE something (x)")
        other.commit()
        other.close()
        a = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, probes=self.probes)
        with self.assertRaises(self.ds.Unavailable):
            a.start()
        a.close()
        Path(db).unlink()
        self.authority = self.new_authority()

    def test_a_file_that_is_not_a_database_is_refused_and_the_lock_released(self):
        self.authority.close()
        db = self.authority.paths.db
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        Path(db).write_bytes(b"not a database at all, just bytes" * 100)
        a = self.ds.Authority(self.chatlib.STATE_DIR, clock=self.clock, probes=self.probes)
        with self.assertRaises(self.ds.Unavailable):
            a.start()
        self.assertIsNone(a.lock_file)
        Path(db).unlink()
        self.authority = self.new_authority()  # the same process can start it once the file is gone

    def test_the_modes_read_back_exclusive_and_wal_and_no_autocommit_is_used(self):
        con = self.authority.store.con
        self.assertEqual(con.execute("PRAGMA locking_mode").fetchone()[0].lower(), "exclusive")
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
        self.assertIsNone(con.isolation_level)
        self.assertNotIn(".autocommit", (SCRIPTS / "delivery_store.py").read_text())

    def test_no_client_module_opens_the_database(self):
        for name in ("chatlib.py", "pchat", "agentcli.py"):
            self.assertNotIn("sqlite3", (SCRIPTS / name).read_text(), name)
        opened = []
        active = [True]

        def hook(event, args):
            if active[0] and event == "sqlite3.connect":
                opened.append(args)
        sys.addaudithook(hook)
        try:
            factory = lambda: self.ds.Client(None, clock=self.clock)  # noqa: E731
            with mock.patch.object(self.chatlib, "client_factory", factory):
                self.pchat("post", "#alpha", "[status] x")
                self.pchat("ack", "#alpha", "m1")
        finally:
            active[0] = False
        self.assertEqual(opened, [])

    def test_a_peer_of_another_uid_is_closed_unread(self):
        c = Conn(self, creds=(os.getuid() + 1, CLIENT_PID))
        self.conns.append(c)
        self.assertIsNone(c.peer)
        self.assertTrue(c.closed_by_authority())

    def test_a_request_that_does_not_speak_for_its_sender_is_refused(self):
        c = self.conn(creds=(os.getuid(), 999))
        c.send(self.create("p" * 32))
        self.assertEqual(c.reply()["result"], "refused")


if __name__ == "__main__":
    unittest.main()
