"""Hermetic multi-project ownership and durable routing regressions."""
import copy
import json
import unittest
from unittest import mock

import _isolation  # noqa: F401 -- install the existing live-state/network guard first
import test_owner_design as fixtures

bridge, deferred = fixtures.bridge, fixtures.deferred
ENABLED = deferred.ENABLED_PROJECTS  # the real constant, before any test narrows it


class OwnerLedTests(unittest.TestCase):
    def setUp(self):
        fixtures.OwnerDesignTests.setUp(self)
        self.cfg = copy.deepcopy(fixtures.CFG)
        self.worker = "gam-solver-3"
        self.scope = dict(project="gamma", request="owner-gamma-design",
                          channel="#gam-design", worker=self.worker, role="solver",
                          session_id="gamma-session", surface_id="gamma-surface")
        self.agents[self.worker] = dict(self.scope, status="active")
        self.cfg["accounts"][self.worker] = "fixture"
        self.cfg["identities"][self.worker] = dict(project="gamma", role="solver")
        self.gamma = self.pm / "gamma"
        self.gamma.mkdir()
        self.handoff = self.gamma / "handoff.md"
        self.handoff.write_text("# Manager\n## Owner and other work\n- keep\n## Queue\n- keep-queue\n")
        patch = mock.patch.object(deferred, "ENABLED_PROJECTS", frozenset({"gamma"}))
        patch.start()
        self.addCleanup(patch.stop)

    def send(self, sender, mid, text, reply=None, verified=True):
        msg = fixtures.privmsg(sender, text, mid, channel=self.scope["channel"], reply_to=reply)
        if not verified:
            msg["tags"].pop("account")
        bridge.handle(msg, self.cfg, self.record, self.deliveries, self.acks)

    def enroll(self, sender="pat", mid="owner-request", reply=None, verified=True, extra=""):
        self.send(sender, mid, f"[request {self.scope['request']}] @{self.worker} work with me {extra}", reply, verified)

    def status(self, mid="status", suffix="progress"):
        self.send(self.worker, mid, f"[status {self.scope['request']}] {suffix}")

    def snapshot(self):
        return deferred.snapshot("gamma")

    def test_manager_delegated_worker_keeps_ring(self):
        self.status()
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertIsNone(self.snapshot())
        self.assertFalse(deferred.OWNERSHIP.exists())

    def test_direct_owner_request_delivers_and_status_coalesces_durably(self):
        self.enroll()
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertEqual(self.deliveries.items[0]["kind"], "ring")
        self.assertEqual(self.deliveries.items[0].get("target"), self.worker)
        self.deliveries.items.clear()
        self.status("a")
        self.status("b")
        self.assertEqual(self.deliveries.items, [])
        self.assertEqual(len(self.snapshot()["pending"]), 2)
        self.assertEqual(self.snapshot()["pending"][0]["scope"]["worker"], self.worker)

    def test_other_authority_unverified_multi_recipient_and_reply_do_not_enroll(self):
        for sender in ["gam-manager", "sam"]:
            self.enroll(sender, sender)
        self.enroll(mid="unverified", verified=False)
        self.enroll(mid="multi", extra="@gam-manager")
        self.enroll(mid="reply", reply="parent")
        self.assertFalse(deferred.OWNERSHIP.exists())

    def test_retired_or_background_identity_does_not_enroll(self):
        self.agents[self.worker]["parent"] = "parent-worker"
        self.enroll()
        self.assertFalse(deferred.OWNERSHIP.exists())
        self.agents[self.worker].pop("parent")
        self.agents[self.worker]["status"] = "retired"
        self.enroll(mid="retired")
        self.assertFalse(deferred.OWNERSHIP.exists())

    def test_protected_group_or_held_surface_is_not_enrolled(self):
        self.agents[self.worker]["workspace_id"] = fixtures.PRIVATE["protected_workspaces"][0]
        self.enroll()
        self.assertFalse(deferred.OWNERSHIP.exists())
        self.agents[self.worker].pop("workspace_id")
        self.agents[self.worker]["surface_id"] = fixtures.PRIVATE["protected_surfaces"][0]
        self.enroll(mid="held")
        self.assertFalse(deferred.OWNERSHIP.exists())

    def test_explicit_requests_questions_blockers_completions_replies_mentions_deliver(self):
        self.enroll()
        self.deliveries.items.clear()
        for kind in ["request", "question", "blocked", "done"]:
            self.send(self.worker, kind, f"[{kind} {self.scope['request']}] act")
        self.send(self.worker, "reply", f"[status {self.scope['request']}] reply", reply="parent")
        self.status("mention", "@gam-manager act")
        self.assertEqual(len(self.deliveries.items), 6)
        self.assertIsNone(self.snapshot())

    def test_new_session_surface_request_or_channel_does_not_inherit_scope(self):
        self.enroll()
        self.deliveries.items.clear()
        for field in ["session_id", "surface_id", "request", "channel"]:
            original = self.agents[self.worker][field]
            self.agents[self.worker][field] = "new-binding"
            self.status(field)
            self.agents[self.worker][field] = original
        self.assertEqual(len(self.deliveries.items), 4)
        self.assertIsNone(self.snapshot())

    def test_storage_failure_or_corrupt_ownership_keeps_normal_routing(self):
        with mock.patch.object(deferred, "_atomic", side_effect=OSError("fixture disk")):
            self.enroll()
        self.deliveries.items.clear()
        self.status("disk")
        deferred.OWNERSHIP.write_text("broken")
        self.status("corrupt")
        self.assertEqual(len(self.deliveries.items), 2)
        self.assertEqual(deferred.OWNERSHIP.read_text(), "broken")

    def test_project_snapshot_preserves_other_projects_and_new_arrivals(self):
        self.enroll()
        self.status("gam-a")
        deferred.record(dict(msgid="legacy-a", at="fixture-at", text="legacy"))
        captured = self.snapshot()
        self.status("gam-b")
        deferred.acknowledge(captured, self.pm)
        self.assertEqual([e["msgid"] for e in self.snapshot()["pending"]], ["gam-b"])
        self.assertEqual([e["msgid"] for e in deferred.snapshot(fixtures.S["project"])["pending"]], ["legacy-a"])
        self.assertIn("msgid=gam-a", self.handoff.read_text())
        self.assertIn("keep-queue", self.handoff.read_text())

    def test_multiple_workers_and_retries_write_once_per_exact_run(self):
        self.enroll()
        self.status("first")
        other = dict(self.scope, worker="gam-solver-4", session_id="other-session", surface_id="other-surface")
        deferred.record(dict(msgid="other", at="fixture-at", text="other"), other)
        captured = self.snapshot()
        with mock.patch.object(deferred, "_atomic", side_effect=OSError("fixture handoff")):
            with self.assertRaises(OSError):
                deferred.acknowledge(captured, self.pm)
        self.assertIsNotNone(self.snapshot())
        with mock.patch.object(deferred, "_save", side_effect=OSError("fixture state")):
            with self.assertRaises(OSError):
                deferred.acknowledge(captured, self.pm)
        self.assertIsNotNone(self.snapshot())
        deferred.acknowledge(captured, self.pm)
        deferred.acknowledge(captured, self.pm)
        self.assertIsNone(self.snapshot())
        self.assertEqual(self.handoff.read_text().count("- OWNER-MANAGED"), 2)

    def test_legacy_pending_and_consumed_receipts_migrate_without_loss(self):
        old = dict(schema=deferred.LEGACY_SCHEMA, scope=fixtures.S, generation=2,
                   seen={"consumed": 1, "pending": 2},
                   pending=[dict(msgid="pending", at="fixture-at", text="old", generation=2)])
        deferred.STATE.write_text(json.dumps(old))
        self.enroll()
        self.status("new")
        self.assertEqual(json.loads(deferred.STATE.read_text())["schema"], deferred.SCHEMA)
        deferred.record(dict(msgid="consumed", at="fixture-at", text="old"))
        self.assertEqual(len(deferred.snapshot(fixtures.S["project"])["pending"]), 1)

    def test_same_rule_applies_to_other_enabled_projects_without_blanket_silencing(self):
        for project in sorted(ENABLED - {"gamma"}):
            with self.subTest(project=project), mock.patch.object(deferred, "ENABLED_PROJECTS", frozenset({project})):
                actor = dict(self.agents[self.worker], project=project)
                cfg = dict(owner="pat", projects={project: dict(prefix="#gam-")})
                entry = dict(verified=True, **{"from": "pat"}, msgid=project, at="fixture-at",
                             channel="#gam-design", text="[request owner-gamma-design] @gam-solver-3 work")
                self.assertTrue(deferred.enroll_owner_request(entry, project, {self.worker: actor}, {self.worker}, cfg))
                status = dict(verified=True, **{"from": self.worker}, role="solver", project=project,
                              msgid=project+"-status", at="fixture-at", text="[status owner-gamma-design] progress",
                              **{k:self.scope[k] for k in ["channel", "session_id", "surface_id"]})
                self.assertTrue(deferred.routine(status, project, {self.worker:actor}, set()))
                self.assertFalse(deferred.routine(status, project, {self.worker:actor}, {"manager"}))

    def test_nudge_keeps_generic_repository_and_tracker_changes(self):
        self.enroll()
        self.assertTrue(deferred.deferred_observation("sessions", "gam-solver-3 idle", "gamma", self.agents))
        self.assertFalse(deferred.deferred_observation("repo", "docs-wip: M docs/designs/render_2d_design.md", "gamma", self.agents))
        self.assertFalse(deferred.deferred_observation("tracker", "PR #418 changed", "gamma", self.agents))
