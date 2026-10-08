"""Hermetic routing fixtures: no IRC, cmux, provider, or private config access."""
import copy
import json
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (first: sandbox home and live-state guard)
from test_bridge import STATE, bridge, privmsg, reset_state

experiment = bridge.receipt_experiment
REQUEST = "nova-20261005-2"
CHANNEL = "#nov-issue-51-53"
TEXT = f"[receipt-start {REQUEST}] start PR #57 — launched by nov-solver-15"
CFG = {
    "owner": "pat", "assistants": ["sam"],
    "accounts": {name: "fixture" for name in ["pat", "sam", "nov-manager", "nov-solver-15",
                                              "nov-reviewer-8", "gam-manager", "gam-reviewer-1"]},
    "identities": {"nov-reviewer-8": {"role": "reviewer", "project": "nova"},
                   "gam-reviewer-1": {"role": "reviewer", "project": "gamma"}},
    "projects": {
        "nova": {"channel": "#nova", "prefix": "#nov-", "manager": "nov-manager"},
        "gamma": {"channel": "#gamma", "prefix": "#gam-", "manager": "gam-manager"},
    },
}
AGENTS = {
    "nov-manager": {"role": "manager", "project": "nova", "status": "active",
                     "surface_id": "fixture-manager", "brand": "claude"},
    "nov-reviewer-8": {"role": "reviewer", "project": "nova", "keys": ["background:fixture"],
                        "parent": "nov-solver-15", "request": REQUEST, "task": "PR #57", "channel": CHANNEL},
    "nov-solver-15": {"role": "solver", "project": "nova", "status": "active",
                       "surface_id": "fixture-surface", "request": REQUEST, "channel": CHANNEL},
}


class ReceiptRoutingTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.flag = Path(STATE) / "experiment.enabled"
        self.flag.write_text(experiment.EXPERIMENT + "\n")
        self.agents = copy.deepcopy(AGENTS)
        self.cfg = copy.deepcopy(CFG)
        self.patches = [mock.patch.object(experiment, "FLAG_PATH", self.flag),
                        mock.patch.object(bridge.identities, "read_registry", lambda: {"agents": self.agents}),
                        mock.patch.object(bridge, "_cmux", side_effect=AssertionError("offline fixture"))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.deliveries, self.record, self.acks = bridge.Deliveries(), bridge.Record(), bridge.Acks()

    def send(self, text=TEXT, sender="nov-reviewer-8", msgid="receipt", channel=CHANNEL, **kwargs):
        message = privmsg(sender, text, msgid, channel=channel, reply_to=kwargs.pop("reply_to", None))
        if kwargs.pop("unverified", False):
            message["tags"].pop("account")
        bridge.handle(message, self.cfg, self.record, self.deliveries, self.acks, **kwargs)
        return message

    def logged(self):
        return [json.loads(line) for line in bridge.chatlib.channel_log(CHANNEL).read_text().splitlines()]

    def test_typed_routine_start_logs_receipt_without_manager_ring(self):
        self.send()
        self.assertEqual(self.deliveries.items, [])
        entry = self.logged()[0]
        self.assertEqual(entry["text"], TEXT)
        self.assertEqual(entry["receipt_routing"]["manager_ring"], "suppressed")

    def test_duplicate_event_is_logged_once_without_a_ring(self):
        self.send()
        self.send(replayed=True)
        self.assertEqual(len(self.logged()), 1)
        self.assertEqual(self.deliveries.items, [])

    def test_disable_immediately_restores_routing_in_same_bridge(self):
        self.send(msgid="first")
        self.flag.unlink()
        self.send(msgid="second")
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertNotIn("receipt_routing", self.logged()[1])

    def test_malformed_or_unreadable_marker_fails_open(self):
        self.flag.write_text("true\n")
        self.send(msgid="malformed")
        with mock.patch.object(Path, "open", side_effect=PermissionError("fixture")):
            self.assertFalse(experiment.enabled())
        self.assertEqual(len(self.deliveries.items), 1)

    def test_owner_and_assistant_inputs_always_deliver_and_need_acceptance(self):
        for n, sender in enumerate(["pat", "sam"]):
            self.send(sender=sender, msgid=str(n))
        self.assertEqual(len(self.deliveries.items), 2)
        self.assertTrue(all(item["needs_ack"] for item in self.deliveries.items))

    def test_owner_pause_interrupt_is_preserved(self):
        self.send("[interrupt pause] @nov-manager STOP: keep all project work paused", sender="pat")
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertEqual(self.deliveries.items[0]["target"], "nov-manager")
        self.assertTrue(self.deliveries.items[0]["interrupt"])
        self.assertTrue(self.deliveries.items[0]["needs_ack"])

    def test_blockers_terminal_results_dependency_release_and_ambiguous_status_deliver(self):
        texts = [
            f"[blocked {REQUEST}] reviewer failed", f"[question {REQUEST}] owner decision needed",
            f"[done {REQUEST}] PR #57 APPROVED at current head",
            f"[status {REQUEST}] PR #57 completed — output: fixture",
            f"[status {REQUEST}] dependency merged; next issue can start",
            f"[blocked {REQUEST}] no progress from reviewer",
            f"[status {REQUEST}] CI build-test success at current head",
            TEXT + "; blocker needs a decision", TEXT + "\nSTOP", TEXT.replace("receipt-start", "status"),
        ]
        for n, text in enumerate(texts):
            self.send(text, msgid=str(n))
        self.assertEqual(len(self.deliveries.items), len(texts))
        self.assertTrue(all(entry["receipt_routing"]["manager_ring"] == "unchanged" for entry in self.logged()))

    def test_missing_or_stale_ownership_and_malformed_metadata_fail_open(self):
        cases = [
            ("nov-solver-15", "status", "retired"), ("nov-solver-15", "surface_id", None),
            ("nov-solver-15", "request", "another-request"), ("nov-solver-15", "channel", "#nov-other"),
            ("nov-reviewer-8", "keys", []), ("nov-reviewer-8", "keys", None),
            ("nov-reviewer-8", "parent", None), ("nov-reviewer-8", "task", "PR #0"),
            ("nov-reviewer-8", "request", None), ("nov-reviewer-8", "project", "gamma"),
        ]
        for n, (name, field, value) in enumerate(cases):
            self.agents = copy.deepcopy(AGENTS)
            self.agents[name][field] = value
            self.send(msgid=str(n))
        self.assertEqual(len(self.deliveries.items), len(cases))

    def test_reply_retains_normal_delivery(self):
        self.send(reply_to="owner-request")
        self.assertEqual(len(self.deliveries.items), 1)

    def test_unverified_sender_never_acquires_routing_authority(self):
        self.send(unverified=True)
        self.assertEqual(self.deliveries.items, [])  # unchanged unauthenticated behavior
        self.assertNotIn("receipt_routing", self.logged()[0])

    def test_gamma_ambiguous_receipt_retains_delivery(self):
        self.send(sender="gam-reviewer-1", channel="#gam-assets")
        self.assertEqual(self.deliveries.items[0]["project"], "gamma")
        entry = json.loads(bridge.chatlib.channel_log("#gam-assets").read_text())
        self.assertEqual(entry["receipt_routing"]["manager_ring"], "unchanged")

    def test_producer_explicit_type_requires_opt_in_and_project_identity(self):
        record = self.agents["nov-reviewer-8"]
        self.assertEqual(experiment.start_kind(record), "receipt-start")
        self.assertEqual(experiment.start_kind(dict(record, project="gamma")), "receipt-start")
        self.assertEqual(experiment.start_kind(dict(record, project=None)), "status")
        self.assertEqual(experiment.start_kind(dict(record, task="ambiguous")), "status")
        self.flag.unlink()
        self.assertEqual(experiment.start_kind(record), "status")

    def project_fixture(self, project, prefix):
        channel, request = f"#{prefix}-issue-1", f"{project}-20261005-1"
        reviewer, parent, manager = f"{prefix}-reviewer-100", f"{prefix}-solver-100", f"{prefix}-manager"
        self.cfg["projects"][project] = {"channel": f"#{project}", "prefix": f"#{prefix}-", "manager": manager}
        for name in [reviewer, parent, manager]:
            self.cfg["accounts"][name] = "fixture"
        self.cfg["identities"][reviewer] = {"role": "reviewer", "project": project}
        self.agents[reviewer] = {"role": "reviewer", "project": project, "keys": ["background:fixture"],
                                 "parent": parent, "request": request, "task": "PR #1", "channel": channel}
        self.agents[parent] = {"role": "solver", "project": project, "status": "active",
                               "surface_id": "fixture-parent", "request": request, "channel": channel}
        self.agents[manager] = {"role": "manager", "project": project, "status": "active",
                                "surface_id": "fixture-manager", "brand": "claude"}
        text = f"[receipt-start {request}] start PR #1 — launched by {parent}"
        return channel, reviewer, parent, manager, request, text

    PROJECTS = [("nova", "nov"), ("gamma", "gam"), ("delta", "del"),
                ("alpha", "alp"), ("epsilon", "eps"), ("zeta", "zet")]

    def test_exact_typed_starts_suppress_only_manager_ring_across_projects(self):
        for project, prefix in self.PROJECTS:
            with self.subTest(project=project):
                channel, reviewer, _, _, _, text = self.project_fixture(project, prefix)
                self.send(text, reviewer, msgid=project, channel=channel)
                entry = json.loads(bridge.chatlib.channel_log(channel).read_text())
                self.assertEqual(entry["text"], text)
                self.assertEqual(entry["receipt_routing"]["manager_ring"], "suppressed")
        self.assertEqual(self.deliveries.items, [])

    def test_disabled_behavior_unchanged_across_projects(self):
        self.flag.unlink()
        for project, prefix in self.PROJECTS:
            channel, reviewer, _, _, _, text = self.project_fixture(project, prefix)
            self.send(text.replace("receipt-start", "status"), reviewer, msgid=project, channel=channel)
            entry = json.loads(bridge.chatlib.channel_log(channel).read_text())
            self.assertNotIn("receipt_routing", entry)
            self.assertEqual(experiment.start_kind(self.agents[reviewer]), "status")
        self.assertEqual(len(self.deliveries.items), len(self.PROJECTS))

    def test_cross_project_parent_fails_open(self):
        channel, reviewer, parent, _, _, text = self.project_fixture("delta", "del")
        self.agents[parent]["project"] = "gamma"
        self.send(text, reviewer, channel=channel)
        self.assertEqual(len(self.deliveries.items), 1)
        self.assertEqual(self.deliveries.items[0]["project"], "delta")

    def test_owner_stop_blocker_terminal_approval_ci_and_dependency_across_projects(self):
        total = 0
        for project, prefix in self.PROJECTS:
            channel, reviewer, _, manager, request, text = self.project_fixture(project, prefix)
            messages = [("pat", text), ("sam", f"[decision {request}] owner approves images"),
                        ("pat", f"[interrupt {request}] @{manager} STOP; keep all work paused"),
                        (reviewer, f"[blocked {request}] review failed"),
                        (reviewer, f"[done {request}] PR #1 approved at current head"),
                        (reviewer, f"[status {request}] PR #1 completed — output: fixture"),
                        (reviewer, f"[status {request}] dependency merged; next stage ready"),
                        (reviewer, f"[status {request}] CI build-test passed"),
                        (reviewer, text + "\nblocker needs owner"),
                        (reviewer, f"[blocked {request}] no progress; watchdog warning")]
            for n, (sender, message) in enumerate(messages):
                self.send(message, sender, msgid=f"{project}-{n}", channel=channel)
                total += 1
        self.assertEqual(len(self.deliveries.items), total)
        interrupts = [item for item in self.deliveries.items if item.get("interrupt")]
        self.assertEqual(len(interrupts), len(self.PROJECTS))
        self.assertTrue(all(item["needs_ack"] for item in interrupts))
