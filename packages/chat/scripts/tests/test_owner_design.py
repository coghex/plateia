"""Hermetic owner-design coordination fixtures: no live messages or config."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401 -- install the existing live-state/network guard first
from test_bridge import bridge, privmsg, reset_state

deferred = bridge.owner_design

# The private file's contents (docs/chat_capture.md): an invented legacy scope and invented protected ids,
# written only inside the sandboxed state directory.
PRIVATE = {"legacy_scope": {"project": "alpha", "request": "alpha-20260101-1", "channel": "#alp-settings",
                            "worker": "alp-solver-7", "session_id": "legacy-session",
                            "surface_id": "legacy-surface"},
           "protected_workspaces": ["workspace-protected"],
           "protected_surfaces": ["surface-protected-1", "surface-protected-2"]}
S = PRIVATE["legacy_scope"]
LEGACY_TAG = "owner-design-deferred:" + S["request"]
CFG = {"owner": "pat", "assistants": ["sam"],
       "accounts": {n: "fixture" for n in ["pat", "sam", "alp-manager", S["worker"], "gam-manager"]},
       "identities": {S["worker"]: {"role": "solver", "project": S["project"]}},
       "projects": {S["project"]: {"channel": "#alpha", "prefix": "#alp-", "manager": "alp-manager"},
                    "gamma": {"channel": "#gamma", "prefix": "#gam-", "manager": "gam-manager"}}}
ACTOR = {k: S[k] for k in ["request", "channel", "session_id", "surface_id"]}
ACTOR.update(project=S["project"], role="solver", status="active")


def write_private(value=None):
    """Put the private file in the sandboxed state directory (the module only ever reads it)."""
    path = deferred.chatlib.STATE_DIR / deferred.PRIVATE_NAME
    path.write_text(json.dumps(PRIVATE if value is None else value))
    return path


class OwnerDesignTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        write_private()
        self.agents = {S["worker"]: copy.deepcopy(ACTOR)}
        self.temp = tempfile.TemporaryDirectory(prefix="owner-design-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.pm = Path(self.temp.name) / "pm"
        self.project = self.pm / S["project"]
        self.project.mkdir(parents=True)
        self.handoff = self.project / "handoff.md"
        self.handoff.write_text("# Manager\n## In flight\n- keep-flight\n## Owner and other work\n- keep-other\n## Queue\n- keep-queue\n")
        for patch in [mock.patch.object(bridge.identities, "read_registry", lambda: {"agents": self.agents}),
                      mock.patch.object(bridge, "_cmux", side_effect=AssertionError("no live cmux"))]:
            patch.start()
            self.addCleanup(patch.stop)
        self.record, self.deliveries, self.acks = bridge.Record(), bridge.Deliveries(), bridge.Acks()

    def post(self, mid="a", text=None, channel=None, reply=None, verified=True):
        msg = privmsg(S["worker"], text or f"[status {S['request']}] settings: decision signed off",
                      mid, channel=channel or S["channel"], reply_to=reply,
                      t="2026-10-12T02:17:25.000Z")
        if not verified:
            msg["tags"].pop("account")
        bridge.handle(msg, copy.deepcopy(CFG), self.record, self.deliveries, self.acks)

    def snapshot(self):
        return deferred.snapshot(S["project"])

    def test_repeated_posts_coalesce_without_ring_and_remain_logged(self):
        self.post("a")
        self.post("b")
        self.assertEqual(self.deliveries.items, [])
        self.assertEqual([e["msgid"] for e in self.snapshot()["pending"]], ["a", "b"])
        logged = [json.loads(l) for l in bridge.chatlib.channel_log(S["channel"]).read_text().splitlines()]
        self.assertEqual([e["msgid"] for e in logged], ["a", "b"])
        self.assertTrue(all(e["receipt_routing"]["manager_ring"] == "deferred" for e in logged))

    def test_replayed_and_consumed_posts_do_not_reopen_or_wake(self):
        self.post()
        self.post()
        self.assertEqual(len(self.snapshot()["pending"]), 1)
        deferred.acknowledge(self.snapshot(), self.pm)
        self.record = bridge.Record()
        self.post()
        self.assertIsNone(self.snapshot())
        self.assertEqual(self.deliveries.items, [])

    def test_explicit_requests_questions_done_replies_and_mentions_keep_routing(self):
        cases = [(f"[{kind} {S['request']}] needs coordination", None) for kind in ["request", "question", "blocked", "done"]]
        cases += [(f"[status {S['request']}] @alp-manager please act", None),
                  (f"[status {S['request']}] @sam please help", None),
                  (f"[status {S['request']}] explicit reply", "parent-post")]
        for i, (text, reply) in enumerate(cases):
            with self.subTest(text=text):
                self.post(str(i), text=text, reply=reply)
        self.assertEqual(len([x for x in self.deliveries.items if x["kind"] == "ring"]), len(cases))
        self.assertIsNone(self.snapshot())

    def test_other_requests_channels_projects_or_session_bindings_are_not_suppressed(self):
        self.post("other-request", text="[status alpha-other] update")
        self.post("other-channel", channel="#alp-other")
        self.post("other-project", channel="#gam-other")
        self.agents[S["worker"]]["session_id"] = "new-unapproved-session"
        self.post("new-session")
        self.assertEqual(len(self.deliveries.items), 4)
        self.assertIsNone(self.snapshot())

    def test_unverified_sender_cannot_create_pending_state(self):
        self.post(verified=False)
        self.assertIsNone(self.snapshot())
        self.assertEqual(self.deliveries.items, [])

    def test_storage_failure_retains_original_log_and_normal_delivery(self):
        with mock.patch.object(deferred, "_save", side_effect=OSError("fixture disk failure")):
            self.post()
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertTrue(bridge.chatlib.channel_log(S["channel"]).exists())
        self.assertIsNone(self.snapshot())

    def test_corrupt_state_is_preserved_and_normal_routing_remains(self):
        deferred.STATE.write_text("broken")
        self.post()
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertEqual(deferred.STATE.read_text(), "broken")

    def test_failure_to_append_channel_log_does_not_lose_pending_receipt(self):
        with mock.patch.object(self.record, "append", side_effect=OSError("fixture log failure")):
            with self.assertRaises(OSError):
                self.post()
        self.assertEqual(len(self.snapshot()["pending"]), 1)
        self.post()
        self.assertEqual(len(self.snapshot()["pending"]), 1)
        self.assertEqual(self.deliveries.items, [])

    def test_newer_arrival_survives_consumption_of_captured_range(self):
        self.post("a")
        captured = self.snapshot()
        self.post("b")
        deferred.acknowledge(captured, self.pm)
        self.assertEqual([e["msgid"] for e in self.snapshot()["pending"]], ["b"])
        self.assertIn("msgid=a", self.handoff.read_text())

    def test_handoff_failure_and_retry_do_not_lose_receipts(self):
        self.post()
        captured = self.snapshot()
        with mock.patch.object(deferred, "_atomic", side_effect=OSError("fixture handoff failure")):
            with self.assertRaises(OSError):
                deferred.acknowledge(captured, self.pm)
        self.assertIsNotNone(self.snapshot())
        deferred.acknowledge(captured, self.pm)
        self.assertIsNone(self.snapshot())
        self.assertIn("keep-other", self.handoff.read_text())

    def test_state_failure_after_handoff_is_idempotently_retried(self):
        self.post()
        captured = self.snapshot()
        with mock.patch.object(deferred, "_save", side_effect=OSError("fixture state failure")):
            with self.assertRaises(OSError):
                deferred.acknowledge(captured, self.pm)
        self.assertIsNotNone(self.snapshot())
        deferred.acknowledge(captured, self.pm)
        self.assertIsNone(self.snapshot())
        self.assertEqual(self.handoff.read_text().count(f"<!-- {LEGACY_TAG} -->"), 1)
