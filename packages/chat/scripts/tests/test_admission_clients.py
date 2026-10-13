"""Clients under maintenance (docs/writer_admission.md sections 4.5, 5 and 6.4; design tests 2, 5 and 13; the
review's corrections C2 and C3 and additions A3 and A5). A refused `pchat post`, `pchat ack` or notice is kept in
the refusal sink, exits 5 (a visible line for a notice), and every other file inside the design's boundary is
byte-identical; after release each is imported once and posted once, a post or notice with the delayed marker and
an acknowledgement as its original TAGMSG, with no text. The authority answers `maintenance` on every execution
path, and `maintenance` survives Client.call. Invented accounts, channels and operations only."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from admission_fakes import (OPERATION, HeldCase, changed, crash_at, exclusive_lock,  # noqa: E402
                             only_nonblocking_flocks, put_record, record_fields)
from outbox_fakes import CFG, CLIENT_PID, LONG, OTHER_PID, Crash  # noqa: E402


class RefusalTests(HeldCase):
    def refusals(self, state):
        """Hold the invented maintenance in `state`, then a post, an ack and a notice, with the boundary
        snapshotted around them. Returns (before, after) snapshots and the three results."""
        self.hold(state)
        before = self.state_snapshot()
        post = self.pchat("post", "#alpha", "[status] build green")
        ack = self.pchat("ack", "#alpha", "msg-1")
        notice = self.notice("build green too")
        return before, self.state_snapshot(), post, ack, notice

    def test_case_2_a_refusal_keeps_exactly_one_sink_file_and_changes_nothing_else_in_the_boundary(self):
        """C2: the assertion is limited to existing files inside the design's safety boundary; the sink and the
        admission lock are excluded, and so are identity state and agent-run state (pchat calls identities.remember
        before it posts: replaced here by a fake, as in every outbox test)."""
        for state in ("partial", "full"):
            with self.subTest(state=state):
                self.fresh()
                self.post("[status] earlier work")  # the database, its WAL and a delivered entry exist already
                before, after, post, ack, notice = self.refusals(state)
                created, removed, modified = changed(before, after)
                self.assertEqual(removed, [], "no existing file was removed")
                self.assertEqual(modified, [], "every existing file is byte-identical: outbox.db, its WAL, "
                                 "pending.json, outbox.jsonl, outbox.d/ and every channel log")
                self.assertEqual([c for c in created if not c.startswith("refused.d")], [])
                files = [c for c in created if c.endswith(".json")]
                self.assertEqual(len(files), 3, "one file per refused call")
                self.assertEqual(len(self.sink()), 3)
                self.assertEqual(self.sink_rows()[0]["v"], "outbox/2")

                self.assertEqual((post[0], ack[0]), (5, 5))
                for out in (post[1], ack[1], notice):
                    self.assertIn(f"held for maintenance by {OPERATION}", out)
                    self.assertIn("do not post it again", out)
                    self.assertIn("after the maintenance is released", out)
                self.assertIn("pchat agent: notice held:", notice, "a visible line, not silent")

    def test_a_refusal_publishes_nothing_to_the_chat_server(self):
        self.hold("partial")
        before = len(self.server.published)
        self.pchat("post", "#alpha", "[status] x")
        self.pchat("ack", "#alpha", "msg-1")
        self.notice()
        self.assertEqual(len(self.server.published), before, "nothing was sent")

    def test_the_authority_being_unavailable_makes_no_difference_to_a_refusal(self):
        self.hold("partial")
        with self.unavailable():
            code, out = self.pchat("post", "#alpha", "[status] x")
        self.assertEqual(code, 5, out)
        self.assertEqual(self.fallback_files(), [], "kept in the sink, not queued for the bridge's outbox.d")
        self.assertEqual(len(self.sink()), 1)

    def test_a_login_failure_during_maintenance_is_a_refusal_not_a_queue(self):
        self.hold("partial")
        self.server.refuse_login = {"alp-solver-2"}
        code, out = self.pchat("post", "#alpha", "[status] x")
        self.assertEqual(code, 5, out)
        self.assertEqual((len(self.sink()), self.fallback_files()), (1, []))

    def test_unknown_maintenance_refuses_visibly_and_keeps_the_work(self):
        put_record(self.paths, raw=b"{")
        code, out = self.pchat("post", "#alpha", "[status] x")
        self.assertEqual(code, 5, out)
        self.assertIn("the maintenance record cannot be read", out)
        self.assertEqual(len(self.sink()), 1)
        self.assertEqual(self.paths.maintenance.read_bytes(), b"{", "a writer never rewrites or removes it")

    def test_case_5_every_invalid_record_refuses_into_the_sink_and_the_authority_still_answers_status(self):
        good = json.dumps(record_fields(), sort_keys=True, separators=(",", ":")).encode()
        for label, raw in (("truncated", good[:30]), ("unknown v", good.replace(b"maintenance/1", b"maintenance/9")),
                           ("wrong type", good.replace(b'"generation":2', b'"generation":"2"'))):
            with self.subTest(label):
                put_record(self.paths, raw=raw)
                self.assertEqual(self.pchat("post", "#alpha", f"[status] {label}")[0], 5)
                reply = self.client().call("status", None)
                self.assertEqual(reply["result"], "ok")
                self.assertEqual(reply["status"]["maintenance"]["state"], "unknown")
                mutation = self.client().call("create", self.writer(), **self.create_args("c" * 32))
                self.assertEqual(mutation["result"], "maintenance")
                self.assertEqual(self.paths.maintenance.read_bytes(), raw)
        self.assertEqual(len(self.sink()), 3)

    def test_after_release_each_held_call_is_imported_once_and_posted_once_marked_delayed(self):
        owner = self.hold("partial")
        self.assertEqual(self.pchat("post", "#alpha", "[status] build green")[0], 5)
        self.assertEqual(self.pchat("ack", "#alpha", "msg-1")[0], 5)
        self.notice("notice kept")
        owner.release()
        for _ in range(3):  # a repeated flush adds nothing
            self.flush()
        self.assertEqual(self.sink(), [], "each file is removed once imported")
        published = self.server.published
        texts = [m["text"] for m in published if m["text"]]
        self.assertEqual(len(published), 3)
        self.assertEqual(sorted(t.split(" (delayed")[0] for t in texts),
                         ["[status] build green", "[status] notice kept"])
        for t in texts:
            self.assertRegex(t, r"\(delayed; written \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
        # C3: an acknowledgement keeps its original reply/react TAGMSG and gains no text message
        ack = next(m for m in published if m["text"] is None)
        self.assertIn("+draft/reply=msg-1", ack["tagmsg"])
        self.assertIn("+draft/react=ack", ack["tagmsg"])
        self.assertEqual([x for x in self.server.writes if "TAGMSG" in x[1]],
                         [("alp-solver-2", "@+draft/reply=msg-1;+draft/react=ack TAGMSG #alpha")])
        self.assertEqual(self.sql("SELECT state, delayed, origin FROM entries ORDER BY origin"),
                         [{"state": "done", "delayed": 1, "origin": "ack"},
                          {"state": "done", "delayed": 1, "origin": "notify"},
                          {"state": "done", "delayed": 1, "origin": "post"}])

    def test_the_import_keeps_the_obligation_across_a_crash_before_the_file_is_removed(self):
        """Once means an idempotent import plus delivery in a successful fixture; recovery stays at-least-once."""
        owner = self.hold("partial")
        self.pchat("post", "#alpha", "[status] kept")
        owner.release()
        name = self.sink()[0]
        real_unlink = self.ds.FS.unlink
        calls = []

        def crash(fs, path):
            if Path(path).name == name:
                calls.append(path)
                raise OSError(5, "the removal failed (fake)")
            return real_unlink(fs, path)
        with mock.patch.object(self.ds.FS, "unlink", crash):
            self.flush()
        self.assertEqual(self.sink(), [name], "the file survived its failed removal")
        self.flush()  # the receipt makes the second import a no-op, and the removal is retried
        self.assertEqual(self.sink(), [])
        self.assertEqual(len(self.server.published), 1, "one entry, delivered once")

    def test_a_refused_notice_is_a_distinct_origin_from_a_post(self):
        owner = self.hold("partial")
        self.notice("a notice")
        owner.release()
        self.flush()
        self.assertEqual(self.sql("SELECT origin FROM entries"), [{"origin": "notify"}])

    def test_a_silent_run_is_still_refused_below_every_caller_and_never_kept(self):
        self.hold("partial")
        with mock.patch.object(self.chatlib, "silence_refusal", lambda account: "a silent child run"):
            with self.assertRaises(self.chatlib.Refused):
                self.chatlib.post("#alpha", "[status] x", "alp-solver-2", CFG)
        self.assertEqual(self.sink(), [])


class StatusVisibilityTests(HeldCase):
    def test_pchat_status_shows_the_record_and_the_held_files_from_the_authority(self):
        from test_outbox_regressions import pchat
        self.hold("partial")
        self.pchat("post", "#alpha", "[status] one")
        self.notice()
        lines = "\n".join(pchat.outbox_status())
        self.assertIn(f"MAINTENANCE: held by {OPERATION} (plateia-activation), state partial, generation 2", lines)
        self.assertIn("2 refused call(s) kept in refused.d/", lines)
        self.assertIn("authority epoch", lines)

    def test_pchat_status_reads_the_record_and_counts_the_sink_itself_without_an_authority(self):
        from test_outbox_regressions import pchat
        self.hold("full")
        self.pchat("post", "#alpha", "[status] one")
        with self.unavailable():
            lines = "\n".join(pchat.outbox_status())
        self.assertIn("state full", lines)
        self.assertIn("1 refused call(s) kept in refused.d/", lines)
        self.assertIn("UNAVAILABLE", lines)

    def test_pchat_status_says_so_when_the_record_cannot_be_read(self):
        from test_outbox_regressions import pchat
        put_record(self.paths, raw=b"{")
        with self.unavailable():
            lines = "\n".join(pchat.outbox_status())
        self.assertIn("a maintenance record exists but cannot be read", lines)

    def test_an_unreadable_sink_is_distinct_from_an_empty_one(self):
        from test_outbox_regressions import pchat
        self.hold("partial")
        with self.unavailable():
            empty = "\n".join(pchat.outbox_status())
        self.assertIn("MAINTENANCE", empty)
        self.assertNotIn("could not be read", empty.split("MAINTENANCE", 1)[1].split("waiting for the bridge")[0])
        with mock.patch.object(self.ds, "sink_count", lambda paths, fs=None: None), self.unavailable():
            lines = "\n".join(pchat.outbox_status())
        self.assertIn("the refusal sink (refused.d/) could not be read", lines)
        self.assertNotIn("0 refused", lines)

    def test_without_maintenance_status_says_nothing_new(self):
        from test_outbox_regressions import pchat
        lines = "\n".join(pchat.outbox_status())
        self.assertNotIn("MAINTENANCE", lines)
        self.assertNotIn("refused", lines)


class CachedWriterTests(HeldCase):
    def test_case_13_a_host_that_imported_the_modules_before_the_record_reads_it_at_its_next_section(self):
        """A long-lived new-release host (an agent run) loaded chatlib before maintenance was held; it wakes and
        sends a notice: the record is read at that section's start (no cached admission), so the notice goes to the
        sink with a visible line. (A legacy host is not refused in partial, and what keeps `full` unreachable
        while one is present is #21's controller; this change does not claim it.)"""
        self.assertEqual(self.notice("before the record"), "")
        self.assertEqual(len(self.server.published), 1, "admitted: delivered")
        owner = self.hold("partial")
        line = self.notice("after the record")
        self.assertIn("notice held", line)
        self.assertEqual(len(self.sink()), 1)
        self.assertEqual(len(self.server.published), 1)
        owner.release()
        self.assertEqual(self.notice("after the release"), "", "admitted again at the next section")
        self.assertEqual(len(self.server.published), 2)

    def test_a_legacy_append_is_not_refused_in_partial_it_cannot_read_the_record(self):
        self.hold("partial")
        legacy = self.chatlib.STATE_DIR / "outbox.jsonl"
        legacy.write_text(json.dumps({"channel": "#alpha", "as": "alp-solver-2", "text": "[status] legacy"}) + "\n")
        self.assertTrue(legacy.exists())


class AdmissionTableTests(HeldCase):
    """Writer admission 4.5, on every execution path (A3): handle (client requests), the authority thread's job
    (_run_job) and run_tx's synchronous path. `maintenance` commits nothing."""

    def call(self, op, writer=None, **args):
        return self.client().call(op, writer or self.writer(), **args)

    def test_no_record_runs_everything(self):
        self.assertEqual(self.call("create", **self.create_args("a" * 32))["result"], "ok")
        self.assertEqual(self.call("attempt", id="a" * 32, n=0, generation=0, a1_nonce="n")["result"], "ok")

    def test_draining_refuses_only_a_request_that_would_create_a_new_entry(self):
        self.assertEqual(self.call("create", **self.create_args("a" * 32))["result"], "ok")  # a call in progress
        self.hold("draining")
        entry = {"channel": "#alpha", "as": "alp-solver-2", "text": "[status] y"}
        refused = self.call("create", **self.create_args("b" * 32))
        self.assertEqual(refused["result"], "maintenance")
        self.assertEqual(refused["maintenance"], {"operation": OPERATION, "kind": "plateia-activation",
                                                  "state": "draining", "generation": 1})
        self.assertEqual(self.call("queue", id="c" * 32, entry=entry)["result"], "maintenance")
        self.assertEqual(self.entry_ids(), ["a" * 32], "nothing was committed for either")
        # the continuations of the call that was already in progress
        self.assertEqual(self.call("create", **self.create_args("a" * 32))["result"], "replay")
        self.assertEqual(self.call("attempt", id="a" * 32, n=0, generation=0, a1_nonce="n")["result"], "ok")
        self.assertEqual(self.call("outcome", id="a" * 32, n=0, generation=1, a1_nonce="n", outcome="confirmed",
                                   final_at=self.clock())["result"], "ok")
        self.assertEqual(self.call("done", id="a" * 32)["result"], "ok")

    def test_committed_and_uncommitted_lost_create_replies_in_draining(self):
        """A lost create reply: if the create landed, the call's queue by the same X is a continuation and is
        admitted (it hands off an X that landed); if it did not, the queue would create, and is refused."""
        entry = {"channel": "#alpha", "as": "alp-solver-2", "text": "[status] z"}
        self.call("create", **self.create_args("a" * 32))  # landed; its reply was "lost"
        self.hold("draining")
        landed = self.call("queue", id="a" * 32, entry=entry)
        self.assertIn(landed["result"], ("ok", "replay"))
        self.assertEqual(self.entry_now("a" * 32)["owner_kind"], "outbox", "handed off")
        lost = self.call("queue", id="b" * 32, entry=entry)  # its create never landed
        self.assertEqual(lost["result"], "maintenance")
        self.assertEqual(self.entry_ids(), ["a" * 32])

    def test_a_wrong_writer_is_judged_by_the_guards_in_draining_and_refused_in_partial(self):
        self.call("create", **self.create_args("a" * 32))
        other = self.writer(OTHER_PID)
        owner = self.hold("draining")
        self.assertEqual(self.call("handoff", other, id="a" * 32)["result"], "conflict", "the same guard as ever")
        self.assertEqual(self.call("done", other, id="a" * 32)["result"], "conflict")
        owner.drain()
        for op, args in (("handoff", {}), ("done", {}), ("attempt", dict(n=0, generation=0, a1_nonce="n")),
                         ("outcome", dict(n=0, generation=1, a1_nonce="n", outcome="confirmed"))):
            with self.subTest(op=op):
                reply = self.call(op, other, id="a" * 32, **args)
                self.assertEqual(reply["result"], "maintenance")
        self.assertEqual(self.entry_now("a" * 32)["state"], "open")

    def test_partial_full_and_unknown_refuse_every_mutation_without_a_transaction(self):
        self.call("create", **self.create_args("a" * 32))
        owner = self.hold("partial")
        for label in ("partial", "full", "unknown"):
            if label == "full":
                owner.advance("full", boot="boot-B", legacy="retired-and-inhibited")
            if label == "unknown":
                self.paths.maintenance.write_bytes(b"{")
            for op, args in (("create", self.create_args("d" * 32)), ("queue", dict(id="a" * 32, entry={
                    "channel": "#alpha", "as": "alp-solver-2", "text": "x"})),
                    ("attempt", dict(id="a" * 32, n=0, generation=0, a1_nonce="n")),
                    ("outcome", dict(id="a" * 32, n=0, generation=1, a1_nonce="n", outcome="confirmed")),
                    ("retire", dict(id="a" * 32, n=0)), ("done", dict(id="a" * 32)),
                    ("handoff", dict(id="a" * 32)),
                    ("void", dict(id="a" * 32, attestation={"n": 0, "generation": 1, "a1_nonce": "n"}))):
                with self.subTest(state=label, op=op):
                    with mock.patch.object(self.authority.store, "tx",
                                           side_effect=AssertionError("a transaction began")):
                        reply = self.call(op, **args)
                    self.assertEqual(reply["result"], "maintenance")
                    self.assertEqual(reply["maintenance"]["state"], label)
        self.assertEqual(self.entry_ids(), ["a" * 32])

    def test_a_maintenance_reply_survives_client_call_and_is_not_an_unavailable_authority(self):
        self.hold("partial")
        reply = self.call("create", **self.create_args("a" * 32))
        self.assertIsNotNone(reply, "not converted to None, which is how an unavailable authority reads")
        self.assertEqual((reply["v"], reply["result"]), (self.ds.PROTOCOL, "maintenance"))
        self.assertIn("authority_epoch", reply)
        client = self.client()
        client.epoch = reply["authority_epoch"] + 5  # the reply is still validated: an older epoch is dropped
        self.assertIsNone(client.call("create", self.writer(), **self.create_args("a" * 32)))
        self.assertIn("maintenance", self.ds.RESULTS)

    def test_the_lock_held_exclusive_is_a_maintenance_answer_at_once_with_nothing_committed(self):
        with exclusive_lock(self.paths):
            reply = self.call("create", **self.create_args("a" * 32))
            status = self.call("status", None)
        self.assertEqual(reply["result"], "maintenance")
        self.assertEqual(status["result"], "ok", "read-only work takes no section")
        self.assertEqual(self.entry_ids(), [])

    def test_in_process_requests_run_in_draining_and_are_cancelled_after_on_both_paths(self):
        for path in ("synchronous", "authority thread"):
            with self.subTest(path=path):
                self.fresh()
                if path == "authority thread":
                    self.authority.run_in_thread()
                    self.assertIsNotNone(self.authority.thread)
                else:
                    self.assertIsNone(self.authority.thread)
                run = lambda: self.authority.run_tx(lambda s: s.status()["waiting"])  # noqa: E731
                self.assertEqual(run(), 0, "no record")
                with self.ds.section(self.paths) as section:  # a bridge section, open before the record begins
                    self.assertTrue(section.admitted)
                    owner = self.hold("draining")
                    self.assertEqual(run(), 0, "draining: a request of a section already open continues it")
                self.assertIs(run(), self.ds.CANCELLED, "draining, outside a section: it would start new work")
                owner.drain()
                self.assertIs(run(), self.ds.CANCELLED, "partial: not run, nothing begun")
                self.assertEqual(self.authority.run_tx(lambda s: s.status()["waiting"], readonly=True), 0,
                                 "a read-only request is answered in every state")
                owner.release()
                self.assertEqual(run(), 0)
                self.paths.maintenance.write_bytes(b"{")
                self.assertIs(run(), self.ds.CANCELLED, "unknown is held")
                os.unlink(self.paths.maintenance)

    def test_the_bridges_in_process_client_gets_no_answer_where_it_is_not_run(self):
        bridge_client = self.bridge.bridge_client(self.authority)
        me = self.probes.identity()
        with self.ds.section(self.paths):  # open before the record begins
            owner = self.hold("draining")
            self.assertIsNotNone(bridge_client.call("create", me, **self.create_args("a" * 32)),
                                 "draining: the bridge's own request, from a section already open, runs")
        self.assertIsNone(bridge_client.call("create", me, **self.create_args("c" * 32)),
                          "draining, from no open section: it would start new work")
        owner.drain()
        self.assertIsNone(bridge_client.call("create", me, **self.create_args("b" * 32)))
        self.assertEqual(bridge_client.call("status", None)["result"], "ok")
        self.assertEqual(bridge_client.call("query", me, id="a" * 32)["result"], "ok")

    def test_no_acquisition_in_the_store_ever_blocks(self):
        with only_nonblocking_flocks(self.ds) as seen:
            self.hold("partial")
            self.call("create", **self.create_args("a" * 32))
            self.call("status", None)
            self.pchat("post", "#alpha", "[status] x")
        self.assertTrue(seen)


class ReadOnlyTests(HeldCase):
    """A5: query and status change no database or WAL file and publish no snapshot, in every state."""

    def files(self):
        out = {}
        for name in ("outbox.db", "outbox.db-wal", "outbox.db-shm", "outbox-status.json"):
            p = self.chatlib.STATE_DIR / name
            out[name] = (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes()) if p.exists() else None
        return out

    def test_query_and_status_write_nothing_in_draining_partial_full_and_unknown(self):
        self.post("[status] data for the database")
        entry_id = self.entries()[0]["id"]
        writer = self.probes.view(CLIENT_PID).identity("127.0.0.1:1>s")
        owner = self.owner()
        owner.acquire()
        for state in ("draining", "partial", "full", "unknown"):
            if state == "partial":
                owner.drain()
            if state == "full":
                owner.advance("full", boot="boot-B", legacy="retired-and-inhibited")
            if state == "unknown":
                self.paths.maintenance.write_bytes(b"{")
            with self.subTest(state=state):
                before, all_before = self.files(), self.state_snapshot(skip=("refused.d",))
                for _ in range(3):
                    status = self.client().call("status", None)
                    query = self.client().call("query", writer, id=entry_id)
                    self.assertEqual((status["result"], query["result"]), ("ok", "ok"))
                    self.assertEqual(status["status"]["maintenance"]["state"], state)
                self.assertEqual(self.files(), before, "no database or WAL byte or timestamp changed")
                self.assertIsNone(before["outbox-status.json"], "and no snapshot was published")
                self.assertEqual(self.state_snapshot(skip=("refused.d",)), all_before)

    def test_they_run_in_a_deferred_transaction_never_a_write_transaction(self):
        """Writer admission 6.4: `status` and `query` must show they write no database page, or run in a deferred
        transaction. They do: the write transaction (BEGIN IMMEDIATE) is unavailable to them."""
        self.post("[status] data")
        entry_id = self.entry_ids()[0]
        writer = self.probes.view(CLIENT_PID).identity("127.0.0.1:1>s")
        for held in (None, "partial"):
            if held:
                self.hold(held)
            with mock.patch.object(self.authority.store, "tx", side_effect=AssertionError("a write transaction")):
                self.assertEqual(self.client().call("status", None)["result"], "ok")
                self.assertEqual(self.client().call("query", writer, id=entry_id)["result"], "ok")
                self.assertEqual(self.authority.run_tx(lambda s: s.status()["waiting"], readonly=True), 0)
            self.assertFalse(self.authority.store.con.in_transaction, "and none is left open")

    def test_status_reports_an_unreadable_sink_count_as_unknown_not_zero(self):
        self.hold("partial")
        self.assertEqual(self.client().call("status", None)["status"]["refused"], 0, "absent: zero")
        self.paths.refused.mkdir(exist_ok=True)
        (self.paths.refused / "00000000000000000001-aa-bb.json").write_text("held work")
        self.assertEqual(self.client().call("status", None)["status"]["refused"], 1)
        with mock.patch.object(self.authority.fs, "listdir", side_effect=PermissionError(13, "denied (fake)")):
            self.assertIsNone(self.client().call("status", None)["status"]["refused"])

    def test_status_counts_file_names_and_never_reads_held_work(self):
        self.hold("partial")
        self.paths.refused.mkdir(exist_ok=True)
        for i in range(3):
            (self.paths.refused / f"0000000000000000000{i}-aa-bb.json").write_text("held work")
        with mock.patch.object(self.authority.fs, "read", side_effect=AssertionError("read held work")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read held work")), \
                mock.patch.object(Path, "read_text", side_effect=AssertionError("read held work")):
            self.assertEqual(self.client().call("status", None)["status"]["refused"], 3)

    def test_status_with_no_maintenance_reports_none(self):
        status = self.client().call("status", None)["status"]
        self.assertIsNone(status["maintenance"])
        self.assertEqual(status["refused"], 0)


class OutstandingAttestationTests(HeldCase):
    """A3: a refusal that carries an outstanding attestation keeps it in the sink, and the import applies it
    under H1 exactly as for a fallback file: nothing already confirmed is ever resent."""

    def test_a_refused_outcome_is_carried_as_an_attestation_and_applied_at_the_import(self):
        real_exchange = self.chatlib._exchange

        def exchange_then_a_record_appears(conn, lines):
            result = real_exchange(conn, lines)
            if not self.paths.maintenance.exists():  # a fault let this call through: a record appears mid-call
                put_record(self.paths, record_fields(state="partial"))
            return result
        with mock.patch.object(self.chatlib, "_exchange", exchange_then_a_record_appears):
            code, out = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 5, out)
        self.assertEqual(len(self.server.published), 1, "part 1 was sent once, before the record")
        [row] = self.sink_rows()
        self.assertEqual(row["attestation"]["kind"], "outcome")
        self.assertEqual((row["attestation"]["outcome"], row["attestation"]["n"]), ("confirmed", 0))
        entry = self.entry_now(self.entry_ids()[0])
        self.assertEqual(entry["state"], "open")
        self.assertEqual([p["state"] for p in entry["parts"]], ["inflight", "unsent", "unsent"],
                         "the authority never recorded the outcome")
        os.unlink(self.paths.maintenance)  # the release
        for _ in range(3):
            self.flush()
        self.assertEqual(self.states(), ["confirmed", "confirmed", "confirmed"])
        self.assertEqual(self.entry()["state"], "done")
        texts = self.server.texts()
        self.assertEqual(len(texts), 3, "each of the three parts exactly once: part 1 never resent")
        self.assertEqual(self.sink(), [])

    def test_a_refused_attempt_leaves_the_confirmed_parts_alone_and_the_remainder_in_the_sink(self):
        real_attempt = self.ds.Client.call
        calls = {"attempt": 0}

        def call(client, op, writer, **args):
            if op == "attempt":
                calls["attempt"] += 1
                if calls["attempt"] == 2 and not self.paths.maintenance.exists():
                    put_record(self.paths, record_fields(state="partial"))
            return real_attempt(client, op, writer, **args)
        with mock.patch.object(self.ds.Client, "call", call):
            code, out = self.pchat("post", "#alpha", LONG)
        self.assertEqual(code, 5, out)
        self.assertEqual(len(self.server.published), 1, "part 1 only")
        self.assertEqual([p["state"] for p in self.entry_now(self.entry_ids()[0])["parts"]],
                         ["confirmed", "unsent", "unsent"])
        os.unlink(self.paths.maintenance)
        for _ in range(3):
            self.flush()
        self.assertEqual(self.states(), ["confirmed"] * 3)
        self.assertEqual(len(self.server.texts()), 3)


class OwnerAndWritersTests(HeldCase):
    """Design tests 3 and 4, with the writers around the owner's primitives. The controller's parts (the post-check,
    rollback, retirement and inhibition) are #21's."""

    def test_case_3_a_crash_of_the_owner_leaves_every_writer_refusing_and_reconcile_reports_it(self):
        probes = self.ds.FakeProbes(boot="boot-A", pid=7001)
        owner = self.owner(probes=probes)
        owner.acquire()
        crash_at(owner, "written")  # partial is durable; its outcome is not journaled
        with self.assertRaises(Crash):
            owner.drain()
        self.assertEqual(self.record().state, "partial", "the record stays at the last durable state")
        before = self.state_snapshot(skip=("refused.d",))
        for n in range(3):
            self.assertEqual(self.pchat("post", "#alpha", f"[status] {n}")[0], 5)
        self.assertEqual(len(self.sink()), 3, "the sink grows")
        self.assertEqual(self.state_snapshot(skip=("refused.d",)), before, "and nothing else changes")
        probes.world["gone"].add(7001)  # the crashed process
        report = self.owner(probes=probes).reconcile()
        self.assertEqual(report["record"], {"operation": OPERATION, "kind": "plateia-activation", "state": "partial",
                                            "generation": 2})
        self.assertIs(report["acting_process_gone"], True)
        self.assertEqual(report["refused_files"], 3)
        resumed = self.owner(probes=self.ds.FakeProbes(boot="boot-A", pid=7002))
        self.assertEqual(resumed.acquire().state, "partial")
        resumed.advance("full", boot="boot-B", legacy="retired-and-inhibited")  # and on to the same end state
        self.assertEqual((self.record().state, self.record().fields["generation"]), ("full", 3))

    def test_case_4_resume_changes_nothing_and_release_admits_writers_and_the_bridge_imports_the_sink(self):
        self.hold("full")
        self.assertEqual(self.pchat("post", "#alpha", "[status] held")[0], 5)
        before = self.state_snapshot()
        resumed = self.owner(probes=self.ds.FakeProbes(boot="boot-B", pid=7003))
        self.assertEqual(resumed.acquire().state, "full")
        self.assertEqual(self.state_snapshot(), before, "resume changes nothing but what the state requires")
        status = self.client().call("status", None)["status"]  # what the caller's post-check reads: still answerable
        self.assertEqual((status["maintenance"]["state"], status["refused"]), ("full", 1))
        resumed.release()
        self.assertFalse(self.paths.maintenance.exists())
        self.assertIsNone(self.journal.pending())
        self.assertEqual([s["step"] for s in self.journal.intents()][-1], "release")
        self.assertEqual([o["landed"] for o in self.journal.outcomes("release")], [True])
        self.assertEqual(self.pchat("post", "#alpha", "[status] direct")[0], 0, "the next post is admitted")
        self.assertEqual(self.server.texts(), ["[status] direct"], "posted directly, not marked delayed")
        self.flush()
        self.assertEqual(sorted(t.split(" (delayed")[0] for t in self.server.texts()),
                         ["[status] direct", "[status] held"])
        self.assertEqual(self.sink(), [])


if __name__ == "__main__":
    unittest.main()
