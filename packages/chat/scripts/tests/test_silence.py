"""A silent child run's posts are refused in Python, at every shared path:
pchat post, the pchat agent run notices, and chatlib's own post and outbox
(so a direct call cannot go around pchat). Fakes only."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import agentcli  # noqa: E402
import binding  # noqa: E402
import chatlib  # noqa: E402
import runstore  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("pchat_cli", str(SCRIPTS / "pchat"))
_spec = importlib.util.spec_from_loader("pchat_cli", _loader)
pchat = importlib.util.module_from_spec(_spec)
_loader.exec_module(pchat)
_isolation.check_bound(agentcli, binding, chatlib, runstore, pchat)

P = "beta"
PARENT = {"project": P, "role": "manager", "name": "bet-manager", "brand": "claude", "provider_session_id": "m1",
          "pid": 100, "pid_start": "Mon Oct  6 10:00:00 2026", "surface_id": "M", "workspace_id": None,
          "manager_generation": "g"}
CHILD = dict(PARENT, role="solver", name="bet-solver-3", brand="codex", provider_session_id="c1", pid=200,
             surface_id="C", manager_generation=None)
POLICY = {"publish": "none", "notify": "none", "on_failure": "record", "wake_adapter": "none"}


class SilenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=_isolation.HOME)
        root = Path(self.tmp.name)
        self.posts, self.queued, self.logins = [], [], []
        self.patches = [mock.patch.object(runstore, "PM_STATE", root / "pm"),
                        mock.patch.object(binding, "CHAT_STATE", root / "chat"),
                        mock.patch.object(chatlib, "OUTBOX", root / "outbox.jsonl"),
                        mock.patch.object(chatlib, "_OUTBOX_LOCK", root / "outbox.lock"),
                        mock.patch.object(chatlib, "login", self.login),
                        mock.patch.object(chatlib, "load_config", lambda: {}),
                        mock.patch.object(pchat, "who", lambda args, cfg, required=True: "bet-solver-3"),
                        mock.patch.object(pchat.identities, "remember", lambda *a, **k: None),
                        mock.patch.dict(os.environ, {}, clear=False)]
        for p in self.patches:
            p.start()
        (root / "chat").mkdir()
        self.registry({"bet-solver-3": {"keys": ["codex:c1"]}})
        for var in ("CHAT_RUN_ID", "CMUX_SURFACE_ID"):
            os.environ.pop(var, None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def registry(self, agents):
        (binding.CHAT_STATE / "identities.json").write_text(json.dumps({"agents": agents}))

    def login(self, account, cfg=None, caps=()):
        """Reaching login means the post was allowed: record it, then stop."""
        self.logins.append(account)
        raise chatlib.ChatError("test: no server")

    def run_(self, publish="none", child=CHILD):
        run_id = runstore.new_run_id()
        runstore.append(P, run_id, "created", "bet-manager", {
            "run_id": run_id, "project": P, "task": "backlog", "request_id": "r-1", "role": "solver",
            "policy": dict(POLICY, publish=publish), "parent": PARENT, "deadline": "2099-01-01T00:00:00Z"},
            create=True)
        with runstore.locked(P, run_id):
            runstore.append(P, run_id, "launched", "bet-manager",
                            {"launch": {"kind": "launched", "surface_id": "C"}, "child": None})
            if child:
                runstore.append(P, run_id, "claimed", child["name"], {"child": child})
        return run_id

    def finish(self, run_id):
        with runstore.locked(P, run_id):
            runstore.append(P, run_id, "finished", "bet-solver-3",
                            {"status": "ok", "result": {"path": "x", "sha256": "0" * 64, "bytes": 1, "status": "ok"}})

    def post(self):
        err = io.StringIO()
        with mock.patch.object(chatlib, "post", lambda *a, **k: self.posts.append(a) or 1), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = pchat.main(["post", "#bet-issue-5", "starting", "--type", "status", "--re", "r-1"])
        return code, err.getvalue()


class PchatTests(SilenceCase):
    """pchat in a process without verifiable session evidence (the sandbox has
    no cmux or ps): only the invocation's own environment can silence it."""

    def test_the_runs_environment_refuses_and_nothing_is_queued(self):
        run_id = self.run_(child=None)
        os.environ["CHAT_RUN_ID"] = run_id
        code, err = self.post()
        self.assertEqual(code, 4)
        self.assertIn(run_id, err)
        self.assertEqual((self.posts, chatlib.OUTBOX.exists()), ([], False))

    def test_an_account_name_or_an_old_key_alone_never_silences(self):
        self.run_()  # bet-solver-3 is the bound child, and the registry still lists its old session key
        self.registry({"bet-solver-3": {"keys": ["codex:c1", "codex:c2"]}})  # /clear: old and new keys kept
        self.assertEqual(self.post()[0], 0, "another invocation of the same account is not refused")
        self.assertEqual(len(self.posts), 1)

    def test_silence_outlives_the_finish_with_no_timer_until_release(self):
        run_id = self.run_()
        os.environ["CHAT_RUN_ID"] = run_id
        self.finish(run_id)
        with mock.patch.object(runstore.time, "time", return_value=time.time() + 86400):
            self.assertEqual(self.post()[0], 4, "a delayed routine done post is still refused")
        with runstore.locked(P, run_id):
            runstore.append(P, run_id, "released", "bet-manager", {"reason": "worker reused for other work"})
        self.assertEqual(self.post()[0], 0)
        os.environ.pop("CHAT_RUN_ID")
        self.run_(publish="request")
        self.assertEqual(self.post()[0], 0)
        self.assertEqual(len(self.posts), 2)

    def test_an_unreadable_run_named_by_the_environment_refuses_but_unrelated_ones_do_not(self):
        run_id = self.run_(child=dict(CHILD, name="bet-solver-9"))
        (runstore.PM_STATE / P / "runs" / run_id / "journal.jsonl").write_text("garbage\n")
        self.assertEqual(self.post()[0], 0)
        os.environ["CHAT_RUN_ID"] = run_id
        self.assertEqual(self.post()[0], 4)


class SharedPathTests(SilenceCase):
    """chatlib itself: what any caller reaches, pchat or not."""

    def test_a_direct_post_from_the_silent_invocation_is_refused_below_every_caller(self):
        os.environ["CHAT_RUN_ID"] = self.run_()
        with self.assertRaises(chatlib.Refused):
            chatlib.post("#bet-issue-5", "done", "bet-solver-3", {})
        with self.assertRaises(chatlib.Refused):
            chatlib.post("#bet-issue-5", "done", "any-account", {})  # the invocation, whatever it claims
        self.assertEqual(self.logins, [])

    def test_the_silent_invocation_never_queues_a_post_but_keeps_acks(self):
        os.environ["CHAT_RUN_ID"] = self.run_()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            chatlib.outbox_append([{"channel": "#c", "as": "bet-solver-3", "text": "done"},
                                   {"channel": "#c", "as": "bet-solver-3", "ack": "m1"}])
        queued = [json.loads(line) for line in chatlib.OUTBOX.read_text().splitlines()]
        self.assertEqual([(q.get("text"), q.get("ack")) for q in queued], [(None, "m1")])
        self.assertIn("not queued", err.getvalue())

    def test_a_pre_run_outbox_entry_keeps_its_delivery(self):
        """Queued before the run by the same account, flushed later by the bridge (no run environment):
        it is that earlier job's post, not this run's, and goes out as it would have."""
        chatlib.outbox_append([{"channel": "#c", "as": "bet-solver-3", "text": "written before the run"}])
        self.run_()  # the account is now a silent run's child
        [entry] = [json.loads(line) for line in chatlib.OUTBOX.read_text().splitlines()]
        with self.assertRaises(chatlib.ChatError) as e:  # reached the (fake) server: allowed
            chatlib.post(entry["channel"], entry["text"], entry["as"], {})
        self.assertNotIsInstance(e.exception, chatlib.Refused)
        self.assertEqual(self.logins, ["bet-solver-3"])


class AgentWrapperTests(SilenceCase):
    def test_wrapper_notices_inside_a_silent_run_are_withheld_not_queued(self):
        os.environ["CHAT_RUN_ID"] = self.run_(child=None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            agentcli.notify({"channel": "#bet-issue-5", "request": "r-1", "name": "bet-reviewer-9"}, "start PR #5")
        self.assertIn("notice withheld", err.getvalue())
        self.assertEqual((self.logins, chatlib.OUTBOX.exists()), ([], False))
        os.environ.pop("CHAT_RUN_ID")
        with contextlib.redirect_stderr(io.StringIO()):
            agentcli.notify({"channel": "#bet-issue-5", "request": "r-1", "name": "bet-reviewer-9"}, "start PR #5")
        self.assertEqual(self.logins, ["bet-reviewer-9"])  # allowed: it reached the (fake) server


if __name__ == "__main__":
    unittest.main()
