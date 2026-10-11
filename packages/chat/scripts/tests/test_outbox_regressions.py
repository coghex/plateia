"""Failing-first regressions for #19: each reproduces a defect itself, through
the public paths (pchat post and the bridge's outbox flush), against a fake
server that commits parts before answering. They run unchanged on the code
before the fix, where they fail, and on the fix, where they pass: the Harness
picks the flush and the queueing path each version has. Invented data only,
under _isolation: no server, cmux, network or real home."""
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
from outbox_fakes import CFG, LONG, Clock, FakeServer  # noqa: E402
from test_bridge import bridge, chatlib, reset_state  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("pchat_regressions", str(SCRIPTS / "pchat"))
_spec = importlib.util.spec_from_loader("pchat_regressions", _loader)
pchat = importlib.util.module_from_spec(_spec)
_loader.exec_module(pchat)
_isolation.check_bound(pchat)

try:
    import delivery_store  # noqa: E402  (the fix: one SQLite authority)
except ImportError:
    delivery_store = None


class Harness:
    """The fix runs an in-process authority and points every client at it; the
    code before the fix has none, and flushes the outbox file directly."""

    def __init__(self, clock):
        self.clock, self.authority, self.patches = clock, None, []
        if delivery_store:
            probes = delivery_store.FakeProbes()
            self.authority = delivery_store.Authority(chatlib.STATE_DIR, clock=clock, mono=clock.mono, probes=probes)
            self.authority.start()
            self.patches.append(mock.patch.object(chatlib, "client_factory", self.client))
            self.patches.append(mock.patch.object(delivery_store, "PROBES", probes))
            self.patches += [mock.patch.object(delivery_store, name, value) for name, value in
                             (("PART_WAIT", 0.05), ("DRAIN_WAIT", 0.05), ("LOCK_WAIT", 0.2))]
        for p in self.patches:
            p.start()
        self.available = True

    def client(self):
        if not self.available:
            return delivery_store.Client(None, clock=self.clock)
        return delivery_store.Client(delivery_store.LocalTransport(self.authority), clock=self.clock)

    def flush(self):
        if self.authority:
            bridge.flush_outbox(CFG, authority=self.authority)
        else:
            bridge.flush_outbox(CFG)

    @contextlib.contextmanager
    def confirmation_record_fails_once(self):
        """The durable record of a confirmed part fails once: before the fix the
        part's reservation, with the fix the authority's answer to its outcome."""
        if self.authority:
            real, calls = self.authority.handle, []

            def handle(request, **kw):
                if request.get("op") == "outcome" and not calls:
                    calls.append(request)
                    return {"v": delivery_store.PROTOCOL, "request_id": request.get("request_id"),
                            "result": "error", "authority_epoch": self.authority.epoch}
                return real(request, **kw)
            with mock.patch.object(self.authority, "handle", handle):
                yield
            return
        state = {"failed": False}
        real = getattr(chatlib, "reserve", None)

        def reserve(*a, **k):
            if not state["failed"]:
                state["failed"] = True
                raise OSError("disk full (fake)")
            return real(*a, **k) if real else None
        with mock.patch.object(chatlib, "reserve", reserve, create=True):
            yield

    @contextlib.contextmanager
    def authority_unavailable(self):
        self.available = False
        try:
            yield
        finally:
            self.available = True

    def close(self):
        for p in reversed(self.patches):
            p.stop()
        if self.authority:
            self.authority.close()


class RegressionCase(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.clock = Clock()
        self.server = FakeServer(self.clock)
        self.patches = [mock.patch.object(chatlib, "login", lambda *a, **k: self.server.login(*a, **k)),
                        mock.patch.object(chatlib, "_wallclock", self.clock, create=True),
                        mock.patch.object(chatlib, "PART_WAIT", 0.05, create=True),
                        mock.patch.object(chatlib, "silence_refusal", lambda account: None),
                        mock.patch.object(chatlib, "load_config", lambda: CFG),
                        mock.patch.object(bridge, "_now", self.clock, create=True)]
        for p in self.patches:
            p.start()
        self.harness = Harness(self.clock)

    def tearDown(self):
        self.harness.close()
        for p in reversed(self.patches):
            p.stop()

    def pchat_post(self, channel, text, account="alp-solver-2"):
        out = io.StringIO()
        with mock.patch.object(pchat, "who", lambda args, cfg, required=True: account), \
                mock.patch.object(pchat.identities, "remember", lambda *a, **k: None), \
                mock.patch.object(pchat.runstore, "silent_refusal", lambda *a: None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = pchat.main(["post", channel, text])
        return code, out.getvalue()


class RepostTests(RegressionCase):
    """#19's Background: a long post whose last completion times out after the
    server committed every part is queued whole and posted again at every flush."""

    def check_never_reposted(self, multiline):
        self.server.multiline = multiline
        self.server.stall_after = 3 if multiline else None
        if not multiline:  # a line-split post: stall once its last line is in
            self.server.stall_after = len(chatlib.split_text(LONG, "[question alpha-20261009-1]"))
        code, said = self.pchat_post("#alpha", LONG)
        self.assertEqual(code, 3, said)
        committed = len(self.server.published)
        self.assertGreater(committed, 1)
        for _ in range(3):
            self.clock.advance(60)
            self.harness.flush()
        self.assertEqual(len(self.server.published), committed,
                         "no part the server committed is posted again by a flush")

    def test_a_committed_multiline_post_is_never_reposted_whole(self):
        self.check_never_reposted(multiline=True)

    def test_a_committed_line_split_post_is_never_reposted_whole(self):
        self.check_never_reposted(multiline=False)


class StorageFailureTests(RegressionCase):
    """PR #20's fifth review, finding 2: a failure to record a confirmed part
    escaped without the parts, and the whole text was queued and posted again."""

    def test_a_failed_confirmation_record_never_requeues_the_whole_post(self):
        self.server.plan = ["ok", "ok", "ok"]
        with self.harness.confirmation_record_fails_once():
            code, said = self.pchat_post("#alpha", LONG)
        self.assertIn(code, (0, 3), said)
        for _ in range(3):
            self.clock.advance(60)
            self.harness.flush()
        texts = [t.split(" (delayed")[0] for t in self.server.texts()]
        self.assertEqual(len(texts), len(set(texts)), "a part the server committed is never written again")


class IndependentFlowTests(RegressionCase):
    """#19 requirement 6 with the third amendment's C2: a process stopped while
    holding outbox.lock never keeps another account's durable queued entry from
    its attempt in the same flush."""

    def test_a_stopped_outbox_lock_holder_does_not_block_another_accounts_queued_entry(self):
        self.server.refuse_login = {"bet-solver-1"}
        with self.harness.authority_unavailable():
            code, said = self.pchat_post("#beta", "[status] beta build green", account="bet-solver-1")
        self.assertEqual(code, 3, said)  # queued durably, as #19 requires
        self.server.refuse_login = set()
        holder = open(chatlib.STATE_DIR / "outbox.lock", "a")
        fcntl.flock(holder, fcntl.LOCK_EX)  # a process stopped while holding the lock
        flushed = threading.Event()
        errors = []

        def run():
            try:
                self.harness.flush()
            except BaseException as err:  # reported below, never lost in the thread
                errors.append(err)
            flushed.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            self.assertTrue(flushed.wait(5), "the flush waited for the stopped lock holder")
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
            worker.join(10)
        self.assertEqual(errors, [])
        texts = [m["text"] for m in self.server.by("bet-solver-1")]
        self.assertEqual(len(texts), 1, texts)
        self.assertTrue(texts[0].startswith("[status] beta build green"), texts)


if __name__ == "__main__":
    unittest.main()
