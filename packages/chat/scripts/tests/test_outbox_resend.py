"""A long post whose completion times out after the server committed it must
not be posted again on every outbox flush (#19). Each test drives chatlib.post,
pchat post, agentcli, the bridge's announce and flush_outbox against a fake
server that commits parts before answering, under _isolation: no IRC server,
cmux, network or real home. The clock is injected and every confirmation wait
is a fraction of a second. All names, channels, times and text are invented."""
import contextlib
import datetime
import importlib.machinery
import inspect
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
from test_bridge import bridge, chatlib, reset_state  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent
import agentcli  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("pchat_outbox", str(SCRIPTS / "pchat"))
_spec = importlib.util.spec_from_loader("pchat_outbox", _loader)
pchat = importlib.util.module_from_spec(_spec)
_loader.exec_module(pchat)
_isolation.check_bound(agentcli, pchat)

CFG = {"owner": "pat", "assistants": ["sam"],
       "accounts": {a: "x" for a in ("pat", "sam", "alp-solver-2", "alp-manager", "bet-solver-1", "chatbridge")},
       "projects": {"alpha": {"channel": "#alpha", "prefix": "#alp-", "manager": "alp-manager"},
                    "beta": {"channel": "#beta", "prefix": "#bet-", "manager": "bet-manager"}}}
TAG = "[question alpha-20261009-1]"
LONG = TAG + " " + "\n".join(f"point {i}: " + "lorem ipsum " * 25 for i in range(30))  # three 4 KB batches
T0 = 1791500000.0  # an invented moment in October 2026


class Crash(BaseException):
    """Stands in for the bridge being killed: no except clause in the code catches it."""


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class FakeServer:
    """Commits each part as soon as it is written, then answers the PING that
    follows it as `plan` says: ok, timeout, fail, fail-then-timeout, noise
    (unrelated traffic until the deadline), slow (traffic, then the answer),
    write-error (the write itself fails partway) or crash."""

    def __init__(self, clock, plan=(), multiline=True):
        self.clock, self.plan, self.multiline = clock, list(plan), multiline
        self.published, self.writes, self.logins = [], [], []
        self.close_error = False
        self.stall_after = None  # once this many messages are committed, no PING is answered

    def login(self, account, cfg=None, caps=()):
        self.logins.append(account)
        return FakeConn(self, account)

    def texts(self):
        return [m["text"] for m in self.published]


class FakeConn:
    def __init__(self, server, account):
        self.server, self.account, self.answers, self.batch = server, account, [], None
        self.caps = {"draft/multiline"} if server.multiline else set()
        self.cap_values = {"draft/multiline": "max-bytes=4096,max-lines=100"} if server.multiline else {}

    def _publish(self, channel, text):
        s = self.server
        s.published.append({"account": self.account, "channel": channel, "text": text,
                            "msgid": f"srv{len(s.published) + 1}", "at": s.clock()})

    def send(self, line):
        s = self.server
        if s.plan and s.plan[0] == "write-error" and line.startswith("BATCH -"):
            s.plan.pop(0)
            raise OSError("broken pipe")  # the batch's lines went out, its closing line did not
        s.writes.append(line)
        tags, rest = ("", line)
        if line.startswith("@"):
            tags, rest = line[1:].split(" ", 1)
        if rest.startswith("BATCH +"):
            self.batch = (rest.split()[3], [])
        elif rest.startswith("BATCH -") and self.batch:
            channel, lines = self.batch
            self._publish(channel, "".join(p if i == 0 or c else "\n" + p for i, (p, c) in enumerate(lines)))
            self.batch = None
        elif rest.startswith("PRIVMSG "):
            channel, text = rest[8:].split(" :", 1)
            if "batch=" in tags and self.batch:
                self.batch[1].append((text, "draft/multiline-concat" in tags))
            else:
                self._publish(channel, text)
        elif rest == "PING :round":
            if s.stall_after is not None and len(s.published) >= s.stall_after:
                self.answers.append("timeout")
            else:
                self.answers.append(s.plan.pop(0) if s.plan else "ok")

    def lines(self, timeout=None):
        answer = self.answers.pop(0) if self.answers else "ok"
        pong = {"command": "PONG", "params": ["irc", "round"], "tags": {}, "prefix": "irc"}
        if answer == "ok":
            yield pong
        elif answer == "slow":
            for _ in range(3):
                yield {"command": "NOTICE", "params": ["*", "unrelated"], "tags": {}, "prefix": "irc"}
            yield pong
        elif answer == "noise":
            while True:  # never the answer: only the absolute deadline ends this
                yield {"command": "NOTICE", "params": ["*", "unrelated"], "tags": {}, "prefix": "irc"}
        elif answer == "fail":
            yield {"command": "FAIL", "params": ["BATCH", "MULTILINE_MAX_BYTES", "too long"], "tags": {}, "prefix": "irc"}
            yield pong
        elif answer == "fail-then-timeout":
            yield {"command": "FAIL", "params": ["BATCH", "MULTILINE_MAX_BYTES", "too long"], "tags": {}, "prefix": "irc"}
            raise TimeoutError("timed out")
        elif answer == "crash":
            raise Crash()
        else:  # timeout
            raise TimeoutError("timed out")

    def close(self, reason=""):
        if self.server.close_error:
            raise OSError("connection reset while leaving")


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


class OutboxCase(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.clock = Clock()
        self.server = FakeServer(self.clock)
        # create=True and the getattr below only let this module run against the
        # pre-#19 scripts, so their failing-first run reaches the assertions.
        self.patches = [mock.patch.object(chatlib, "login", lambda *a, **k: self.server.login(*a, **k)),
                        mock.patch.object(chatlib, "_wallclock", self.clock, create=True),
                        mock.patch.object(chatlib, "PART_WAIT", 0.2, create=True),
                        mock.patch.object(chatlib, "silence_refusal", lambda account: None),
                        mock.patch.object(chatlib, "load_config", lambda: CFG),
                        mock.patch.object(bridge, "_now", self.clock, create=True)]
        for p in self.patches:
            p.start()
        self.coverage = getattr(bridge, "Coverage", dict)()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def serve(self, *plan, multiline=True):
        self.server.plan, self.server.multiline = list(plan), multiline

    def flush(self, module=None, deliveries=None, ack=None):
        flush = (module or bridge).flush_outbox
        if "coverage" in inspect.signature(flush).parameters:
            flush(CFG, coverage=self.coverage, deliveries=deliveries, ack=ack)
        else:  # the pre-#19 bridge
            flush(CFG, ack=ack)

    def entries(self):
        files = [chatlib.OUTBOX] + sorted(chatlib.STATE_DIR.glob("outbox.claimed-*.jsonl"))
        return [json.loads(line) for f in files if f.exists() for line in f.read_text().splitlines() if line.strip()]

    def states(self, entry=None):
        return [p["state"] for p in (entry or self.entries()[0])["parts"]]

    def dead(self):
        path = chatlib.STATE_DIR / "dead-letters.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def log_record(self, published, *, account=None, channel=None, verified=True, at=None, msgid=None):
        """What the bridge would have logged on reading `published` from the server."""
        bridge.Record().append({"at": iso(published["at"] if at is None else at),
                                "msgid": msgid or published["msgid"], "channel": channel or published["channel"],
                                "from": account or published["account"], "nick": account or published["account"],
                                "verified": verified, "text": published["text"]})

    def covered(self, since, through, channel="#alpha"):
        self.coverage.spans[channel] = {"since": since, "through": through, "mark": "msgid=x"}

    def pchat_post(self, channel, text, account="alp-solver-2", *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pchat, "who", lambda args, cfg, required=True: account), \
                mock.patch.object(pchat.identities, "remember", lambda *a, **k: None), \
                mock.patch.object(pchat.runstore, "silent_refusal", lambda *a: None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pchat.main(["post", channel, text, *extra])
        return code, out.getvalue() + err.getvalue()

    def queue_long(self, *plan, committed=3):
        """pchat post of LONG: the server commits each part written, and confirms what `plan` says."""
        self.serve(*plan)
        code, said = self.pchat_post("#alpha", LONG)
        self.assertEqual(code, 3, said)
        self.assertEqual(len(self.server.published), committed)
        return self.server.published[:]


class CommittedThenTimeoutTests(OutboxCase):
    def test_pchat_queues_only_the_unconfirmed_part(self):
        published = self.queue_long("ok", "ok", "timeout")
        [entry] = self.entries()
        self.assertEqual(self.states(entry), ["confirmed", "confirmed", "uncertain"])
        self.assertEqual([p["text"] for p in entry["parts"]], [m["text"] for m in published])
        self.assertEqual(entry["text"], LONG)  # still shown in full where pchat status lists it
        for _ in range(3):
            self.flush()
        self.assertEqual(len(self.server.published), 3, "no part is written again while its delivery is undecided")

    def test_delivered_part_is_found_in_the_record_and_retired_without_writing(self):
        published = self.queue_long("ok", "ok", "timeout")
        self.log_record(published[2])
        self.clock.t += 30
        with self.assertLogs_bridge() as said:
            self.flush()
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.server.published), 3)
        self.assertIn(published[2]["msgid"], said())

    def test_absent_part_is_written_exactly_once_more(self):
        part3 = self.queue_long("ok", "ok", "timeout")[2]
        self.server.published.pop()  # the server never committed part 3 after all
        self.clock.t += bridge.SETTLE + 60
        self.covered(T0 - 3600, self.clock.t)
        self.flush()
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.server.published), 3)
        self.assertEqual(self.server.published[2]["text"], part3["text"], "the same part, unchanged")
        self.flush()
        self.assertEqual(len(self.server.published), 3, "parts 1 and 2 are never written again")

    def test_undecided_part_waits_and_shows_in_status(self):
        self.queue_long("ok", "ok", "timeout")
        for _ in range(3):
            self.clock.t += 60
            self.flush()
        self.assertEqual(len(self.server.published), 3)
        self.assertEqual(chatlib.outbox_summary(), {"pending": 1, "checking": 1, "unsent": 0})
        self.assertIn("1 awaiting a delivery check", self.status())

    def test_bridge_flush_of_a_text_only_entry_no_longer_reposts_the_whole_post(self):
        """The defect as it was seen: a queued whole post committed, its completion timed out."""
        chatlib.outbox_append([{"channel": "#alpha", "as": "alp-solver-2", "text": LONG, "cont": TAG,
                                "at": "2026-10-09T09:00:00"}])
        self.server.stall_after = 3  # every part is committed; the answer after the last never comes
        for _ in range(3):
            self.flush()
            self.clock.t += 72
        self.assertEqual(len(self.server.published), 3, "on master every flush posted all three again")
        self.assertEqual(self.states(), ["confirmed", "confirmed", "uncertain"])

    def test_restart_continues_from_recorded_progress(self):
        self.queue_long("ok", "timeout", committed=2)
        self.server.published.pop()  # part 2 never arrived; part 3 was never written
        self.clock.t += bridge.SETTLE + 60
        self.covered(T0 - 3600, self.clock.t)
        loader = importlib.machinery.SourceFileLoader("chat_bridge_restarted", str(SCRIPTS / "chat-bridge"))
        fresh = importlib.util.module_from_spec(importlib.util.spec_from_loader("chat_bridge_restarted", loader))
        loader.exec_module(fresh)
        self.coverage = fresh.Coverage()  # read back from disk, as a restarted bridge would
        self.coverage.spans["#alpha"] = {"since": T0 - 3600, "through": self.clock.t, "mark": "msgid=x"}
        with mock.patch.object(fresh, "_now", self.clock):
            self.flush(module=fresh)
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.server.published), 3)
        part1 = [w for w in self.server.writes if "BATCH +p0" in w]
        self.assertEqual(len(part1), 1, "part 1 was written once, by pchat, never by the restarted bridge")

    def assertLogs_bridge(self):
        log = chatlib.STATE_DIR / "bridge.log"
        start = log.stat().st_size if log.exists() else 0

        @contextlib.contextmanager
        def watching():
            yield lambda: log.read_text()[start:] if log.exists() else ""
        return watching()

    def status(self):
        out = io.StringIO()
        with mock.patch("subprocess.run", lambda *a, **k: types.SimpleNamespace(stdout="")), \
                mock.patch.object(pchat, "unaccepted", lambda cfg, project=None: []), \
                contextlib.redirect_stdout(out):
            pchat.main(["status"])
        return out.getvalue()


class CrashTests(OutboxCase):
    def test_crash_between_parts_never_resends_confirmed_or_mid_write_parts(self):
        chatlib.outbox_append([{"channel": "#alpha", "as": "alp-solver-2", "text": LONG, "cont": TAG, "at": "t"}])
        self.serve("ok", "crash")
        with self.assertRaises(Crash):
            self.flush()
        [entry] = self.entries()
        self.assertEqual(self.states(entry), ["confirmed", "writing", "unsent"])
        self.serve()
        for _ in range(2):
            self.flush()
        self.assertEqual(self.states(), ["confirmed", "uncertain", "unsent"])
        self.assertEqual(len(self.server.published), 2, "part 2 may be in the channel: not resent blindly")
        self.assertEqual(len(self.entries()), 1, "nothing lost, nothing doubled")

    def test_completion_recorded_before_the_claimed_file_went_away(self):
        parts = [{"kind": "line", "lines": [["done", False]], "text": "done", "state": "confirmed",
                  "written_at": T0}]
        (chatlib.STATE_DIR / "outbox.claimed-1.jsonl").write_text(
            json.dumps({"channel": "#alpha", "as": "alp-solver-2", "text": "done", "id": "e1", "parts": parts}) + "\n")
        self.flush()
        self.assertEqual((self.entries(), self.server.writes), ([], []))

    def test_a_kept_entry_is_owed_once_across_flushes_and_crashes(self):
        chatlib.outbox_append([{"channel": "#alpha", "as": "alp-solver-2", "text": "first", "at": "t"},
                               {"channel": "#alpha", "as": "alp-solver-2", "text": "second", "at": "t"}])
        self.serve("ok", "crash")  # the first entry completes, then the bridge dies on the second
        with self.assertRaises(Crash):
            self.flush()
        self.assertEqual([e["text"] for e in self.entries()], ["second"])
        self.serve("timeout")
        self.flush()
        self.flush()
        self.assertEqual(len(self.entries()), 1, "a kept entry is never owed from two places")
        self.assertEqual(self.server.texts().count(self.server.published[0]["text"]), 1, "the first is not resent")

    def test_failure_to_record_progress_stops_writing(self):
        chatlib.outbox_append([{"channel": "#alpha", "as": "alp-solver-2", "text": LONG, "cont": TAG, "at": "t"}])
        self.serve("ok", "ok", "ok")
        calls = []

        def failing_save(self_):
            calls.append(1)
            if len(calls) == 2:  # recording part 1's confirmation fails
                raise OSError("disk full")
            return original(self_)
        original = bridge._Claimed.save
        with mock.patch.object(bridge._Claimed, "save", failing_save):
            with contextlib.suppress(OSError):
                self.flush()
        self.assertEqual(len(self.server.published), 1, "no part is written after a record failed")
        self.assertEqual(self.states()[1:], ["unsent", "unsent"])
        self.assertIn(self.states()[0], ("writing", "confirmed"), "part 1 is never recorded as unsent")


class IndependentDeliveryTests(OutboxCase):
    def test_other_entries_go_out_while_one_awaits_its_check(self):
        self.queue_long("ok", "ok", "timeout")
        chatlib.outbox_append([
            {"channel": "#beta", "as": "bet-solver-1", "text": "[status beta-20261009-2] unrelated", "at": "t"},
            {"channel": "#alpha", "as": "alp-solver-2", "text": "[status alpha-20261009-1] same account", "at": "t"},
            {"channel": "#alpha", "as": "sam", "text": "keeps failing", "at": "t"}])
        failing = self.server.login

        def login(account, cfg=None, caps=()):
            if account == "sam":
                raise OSError("connection refused")
            return failing(account, cfg, caps)
        self.server.login = login
        self.flush()
        texts = [t.split(" (delayed")[0] for t in self.server.texts()]
        self.assertIn("[status beta-20261009-2] unrelated", texts)
        self.assertIn("[status alpha-20261009-1] same account", texts)
        left = self.entries()
        self.assertEqual(sorted(e["as"] for e in left), ["alp-solver-2", "sam"])
        self.assertEqual(next(e for e in left if e["as"] == "sam").get("parts"), None,
                         "nothing was written: the whole post stays queued, as before")


class TransportTests(OutboxCase):
    def post(self, text, **kw):
        return chatlib.post("#alpha", text, "alp-solver-2", CFG, cont=TAG, **kw)

    def test_unrelated_traffic_does_not_extend_the_wait(self):
        self.serve("noise")
        with self.assertRaises(chatlib.PostIncomplete) as err:
            self.post("[question alpha-20261009-1] short")
        self.assertEqual([p["state"] for p in err.exception.parts], ["uncertain"])

    def test_slow_but_confirmed_post_succeeds_without_queueing(self):
        self.serve("slow", "slow", "slow")
        code, said = self.pchat_post("#alpha", LONG)
        self.assertEqual(code, 0, said)
        self.assertFalse(chatlib.OUTBOX.exists())

    def test_partial_write_failure_is_uncertain_not_unsent(self):
        self.serve("ok", "write-error")
        with self.assertRaises(chatlib.PostIncomplete) as err:
            self.post(LONG)
        self.assertEqual([p["state"] for p in err.exception.parts], ["confirmed", "uncertain", "unsent"])

    def test_refusal_stands_through_a_later_timeout(self):
        self.serve("ok", "fail-then-timeout")
        with self.assertRaises(chatlib.Refused) as err:
            self.post(LONG)
        self.assertEqual([p["state"] for p in err.exception.parts], ["confirmed", "refused", "unsent"])

    def test_cleanup_failure_after_confirmation_keeps_the_success(self):
        self.server.close_error = True
        self.serve("ok", "ok", "ok")
        self.assertGreater(self.post(LONG), 0)

    def test_line_transport_uncertain_middle_then_unsent(self):
        self.serve("ok", "timeout", multiline=False)
        with self.assertRaises(chatlib.PostIncomplete) as err:
            self.post("[question alpha-20261009-1] one\ntwo\nthree\nfour")
        self.assertEqual([p["state"] for p in err.exception.parts], ["confirmed", "uncertain", "unsent", "unsent"])
        self.assertEqual([p["text"] for p in err.exception.parts][1], f"… {TAG} two")

    def test_a_fixed_multiline_part_is_never_resplit_for_a_server_without_multiline(self):
        self.queue_long("ok", "timeout", committed=2)
        self.server.published.pop()
        self.clock.t += bridge.SETTLE + 60
        self.covered(T0 - 3600, self.clock.t)
        self.serve(multiline=False)
        self.flush()
        self.assertEqual(len(self.server.published), 1, "kept unsent rather than split into lines")
        self.assertEqual(self.states(), ["confirmed", "unsent", "unsent"])
        self.serve()
        self.flush()
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.server.published), 3)


class EvidenceTests(OutboxCase):
    """What counts as the record showing a part, and what shows it absent."""

    def setUp(self):
        super().setUp()
        self.published = self.queue_long("ok", "ok", "timeout")
        self.part3 = self.published[2]
        self.clock.t += 30

    def assertUndecided(self):
        before = len(self.server.published)
        self.flush()
        self.assertEqual(self.states(), ["confirmed", "confirmed", "uncertain"])
        self.assertEqual(len(self.server.published), before)

    def test_wrong_account_unverified_wrong_channel_and_too_old_are_not_evidence(self):
        self.log_record(self.part3, account="alp-manager", msgid="w1")
        self.log_record(self.part3, verified=False, msgid="w2")
        self.log_record(self.part3, channel="#beta", msgid="w3")
        self.log_record(self.part3, at=self.part3["at"] - 3600, msgid="w4")
        self.assertUndecided()

    def test_absence_needs_the_whole_interval(self):
        self.server.published.pop()
        self.clock.t += bridge.SETTLE + 60
        self.covered(T0 + 10, self.clock.t)  # began after the part was written
        self.assertUndecided()
        self.covered(T0 - 3600, T0 + 60)  # ends before the settle time
        self.assertUndecided()
        self.covered(T0 - 3600, self.clock.t)
        self.flush()
        self.assertEqual(self.entries(), [])

    def test_a_msgid_is_never_evidence_for_two_parts(self):
        self.log_record(self.part3)
        other = {**self.entries()[0], "id": "e2", "as": "alp-solver-2"}
        claimed = sorted(chatlib.STATE_DIR.glob("outbox.claimed-*.jsonl")) or [chatlib.OUTBOX]
        with claimed[-1].open("a") as f:
            f.write(json.dumps(other) + "\n")
        self.flush()
        left = self.entries()
        self.assertEqual(len(left), 1, "one message confirms one of the two identical parts, not both")
        self.assertEqual(self.states(left[0]), ["confirmed", "confirmed", "uncertain"])


class RepeatedTextTests(OutboxCase):
    def test_identical_parts_need_distinct_messages(self):
        self.serve("ok", "ok", "timeout", multiline=False)
        code, _ = self.pchat_post("#alpha", "[question alpha-20261009-1] first\nsame\nsame")
        self.assertEqual(code, 3)
        p = self.server.published
        self.assertEqual(p[1]["text"], p[2]["text"])
        self.log_record(p[1])  # only part 2's message is in the record
        self.flush()
        self.assertEqual(self.states(), ["confirmed", "confirmed", "uncertain"], "part 2's message is not part 3's")
        self.log_record(p[2])
        self.flush()
        self.assertEqual(self.entries(), [])

    def test_identical_parts_absent_when_complete(self):
        self.serve("ok", "ok", "timeout", multiline=False)
        self.pchat_post("#alpha", "[question alpha-20261009-1] first\nsame\nsame")
        p = self.server.published
        self.log_record(p[1])
        self.server.published.pop()
        self.clock.t += bridge.SETTLE + 60
        self.covered(T0 - 3600, self.clock.t)
        self.flush()
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.server.published), 3, "part 3 written once more, part 2 not")


class RefusalTests(OutboxCase):
    def test_refusal_through_pchat_records_what_was_published(self):
        self.serve("ok", "fail")
        code, said = self.pchat_post("#alpha", LONG)
        self.assertEqual(code, 2)
        self.assertIn("1 of 3 parts were already posted", said)
        self.assertEqual(self.entries(), [])
        [dead] = self.dead()
        self.assertEqual([p["state"] for p in dead["parts"]], ["confirmed", "refused", "unsent"])
        self.assertTrue(all(p["text"] for p in dead["parts"]))
        self.assertIn("1 of 3 parts posted, 2 not", self.status())

    def test_refusal_in_the_bridge_dead_letters_with_parts_and_requeues_nothing(self):
        chatlib.outbox_append([{"channel": "#alpha", "as": "alp-solver-2", "text": LONG, "cont": TAG, "at": "t"}])
        self.serve("ok", "ok", "fail")
        self.flush()
        self.assertEqual(self.entries(), [])
        [dead] = self.dead()
        self.assertEqual([p["state"] for p in dead["parts"]], ["confirmed", "confirmed", "refused"])

    def status(self):
        return CommittedThenTimeoutTests.status(self)


class UnchangedPathTests(OutboxCase):
    def test_old_format_and_ack_entries_go_out_as_before(self):
        chatlib.outbox_append([{"channel": "#alpha", "as": "alp-solver-2", "text": "[status alpha-1] x", "at": "t"},
                               {"channel": "#alpha", "as": "alp-manager", "ack": "srv9", "at": "t"}])
        acks = []
        self.flush(ack=lambda *a: acks.append(a))
        self.assertEqual(self.server.texts(), ["[status alpha-1] x (delayed; written tZ)"])
        self.assertEqual(acks, [("#alpha", "srv9", "alp-manager", CFG)])
        self.assertEqual(self.entries(), [])

    def test_failure_before_any_write_keeps_the_whole_post(self):
        def refused(*a, **k):
            raise ConnectionRefusedError("chat is down")
        self.server.login = refused
        code, _ = self.pchat_post("#alpha", LONG)
        self.assertEqual(code, 3)
        [entry] = self.entries()
        self.assertEqual((entry["text"], "parts" in entry), (LONG, False))


class OtherCallerTests(OutboxCase):
    def test_agent_notices_queue_only_the_remainder(self):
        self.serve("ok", "timeout")
        with mock.patch.object(agentcli.runstore, "silent_refusal", lambda *a: None):
            agentcli.notify({"channel": "#alpha", "name": "alp-solver-2", "request": "alpha-20261009-1"},
                            LONG.split(" ", 2)[2])
        self.assertEqual(self.states(), ["confirmed", "uncertain", "unsent"])

    def test_bridge_announcements_queue_only_the_remainder(self):
        self.serve("timeout")
        bridge.announce(CFG, {"channel": "#alpha", "msgid": "srv1"}, "interrupt delivered")
        [entry] = self.entries()
        self.assertEqual((entry["as"], self.states(entry)), ("chatbridge", ["uncertain"]))

    def test_a_silent_run_never_queues_a_remainder_either(self):
        entry = {"channel": "#alpha", "as": "alp-solver-2", "text": "x", "id": "e1",
                 "parts": [{"kind": "line", "lines": [["x", False]], "text": "x", "state": "uncertain"}]}
        with mock.patch.object(chatlib, "silence_refusal", lambda account: "a silent run"), \
                contextlib.redirect_stderr(io.StringIO()):
            chatlib.outbox_append([entry])
        self.assertEqual(self.entries(), [])


class UndecidedForADayTests(OutboxCase):
    def test_dead_lettered_after_a_day_with_a_content_free_alert(self):
        self.queue_long("ok", "ok", "timeout")
        deliveries = bridge.Deliveries()
        self.clock.t += bridge.UNDECIDED_MAX - 60
        self.flush(deliveries=deliveries)
        self.assertEqual((len(self.entries()), self.dead()), (1, []))
        self.clock.t += 120
        self.flush(deliveries=deliveries)
        self.assertEqual(self.entries(), [])
        self.assertEqual(len(self.server.published), 3, "never written again without proof of absence")
        [dead] = self.dead()
        self.assertEqual([p["state"] for p in dead["parts"]], ["confirmed", "confirmed", "uncertain"])
        self.assertTrue(dead["parts"][2]["text"] and dead["parts"][2]["written_at"])
        [push] = [i for i in deliveries.items if i["kind"] == "push"]
        self.assertNotIn("lorem", push["text"])
        self.assertIn("alp-solver-2", push["text"])


class CoverageTests(unittest.TestCase):
    """The record is complete over an interval only through a finished catch-up and a sync."""

    def setUp(self):
        reset_state()
        cfgfile = Path(_isolation.CHAT_STATE) / "config.json"
        cfgfile.write_text("{}")
        orig = bridge.HistoryPager
        self.patches = [mock.patch.object(chatlib, "CONFIG_PATH", cfgfile),
                        mock.patch.object(bridge, "HISTORY_PAGE", 2),
                        mock.patch.object(bridge, "HistoryPager", lambda: orig(page=2))]
        for p in self.patches:
            p.start()
        self.state = (bridge.Record(), bridge.Deliveries(), bridge.Acks(), bridge.Checkpoints())
        self.coverage = bridge.Coverage()
        from test_bridge import privmsg
        self.privmsg = privmsg
        bridge.handle(privmsg("pat", "before", "m0", channel="#alpha", t="2026-10-09T09:00:00Z"),
                      CFG, *self.state[:3])

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def connect(self, script):
        from test_bridge import FakeServer as ScriptedServer
        server = ScriptedServer(script)
        with mock.patch.object(chatlib, "login", lambda *a, **k: server):
            with self.assertRaises(chatlib.ChatError):
                bridge.run(CFG, *self.state, self.coverage)

    def test_interrupted_page_establishes_nothing_and_a_finished_one_does(self):
        from test_bridge import batch, in_batch, join
        self.connect([join("#alpha"), batch("b1", "#alpha"),
                      in_batch("b1", self.privmsg("pat", "gap 1", "m1", channel="#alpha", t="2026-10-09T10:00:00Z")),
                      in_batch("b1", self.privmsg("pat", "gap 2", "m2", channel="#alpha", t="2026-10-09T10:01:00Z")),
                      batch("b1", None)])  # a full page, then the connection drops
        self.coverage.sync({"#alpha"}, T0)
        self.assertFalse(self.coverage.covers("#alpha", 0, 0))
        self.connect([join("#alpha"), batch("b2", "#alpha"), batch("b2", None)])  # finished
        since = self.coverage.spans["#alpha"]["since"]
        self.assertIsNone(self.coverage.spans["#alpha"]["through"], "complete, but not through any time yet")
        self.coverage.sync({"#alpha"}, since + 100)
        self.assertTrue(self.coverage.covers("#alpha", since, since + 100))
        self.assertFalse(self.coverage.covers("#alpha", since - 1, since + 100))

    def test_a_resumed_catch_up_keeps_the_interval_and_a_seeded_one_does_not(self):
        from test_bridge import batch, join
        self.connect([join("#alpha"), batch("b1", "#alpha"), batch("b1", None)])
        since = self.coverage.spans["#alpha"]["since"]
        self.coverage.sync({"#alpha"}, since + 10)
        self.connect([join("#alpha"), batch("b2", "#alpha"), batch("b2", None)])  # from the same checkpoint
        self.assertEqual(self.coverage.spans["#alpha"]["since"], since)
        bridge.CHECKPOINTS.unlink()  # lost: the next catch-up starts from a seeded checkpoint
        self.coverage.spans["#alpha"]["mark"] = "msgid=elsewhere"
        self.state = (*self.state[:3], bridge.Checkpoints())
        self.connect([join("#alpha"), batch("b3", "#alpha"), batch("b3", None)])
        self.assertGreaterEqual(self.coverage.spans["#alpha"]["since"], since)
        self.assertIsNone(self.coverage.spans["#alpha"]["through"])


if __name__ == "__main__":
    unittest.main()
