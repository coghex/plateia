"""Owner-led routing in plateia: the private file, fail-closed routing, #29's owner identity rule, replay,
the bridge's evidence and writer admission (#31). Invented accounts, projects and ids only; the private file is
written only inside the sandboxed state directory, and its values are never real.

The captured tests (test_owner_design, test_owner_led) show the routing itself; these show what plateia adds."""
import copy
import fcntl
import json
import os
import signal
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
import test_admission_bridge as admission  # noqa: E402
import test_owner_design as fixtures  # noqa: E402
import test_owner_led as led  # noqa: E402
from admission_fakes import put_record, snapshot  # noqa: E402
from outbox_fakes import CFG as OUTBOX_CFG  # noqa: E402
from test_bridge import join, privmsg  # noqa: E402

bridge, deferred, chatlib = fixtures.bridge, fixtures.deferred, fixtures.deferred.chatlib
PRIVATE_PATH = lambda: chatlib.STATE_DIR / deferred.PRIVATE_NAME  # noqa: E731
OWNER_FILES = lambda: (deferred.OWNERSHIP, deferred.STATE, Path(str(deferred.STATE) + ".lock"))  # noqa: E731


def owner_led_bytes():
    return {p.name: (p.read_bytes() if p.exists() else None) for p in OWNER_FILES()}


def good(**over):
    value = copy.deepcopy(fixtures.PRIVATE)
    value.update(over)
    return value


class Base(unittest.TestCase):
    """The captured tests' gamma fixture (an enrolled-project worker) plus helpers."""
    send, enroll, status, snapshot = (led.OwnerLedTests.send, led.OwnerLedTests.enroll,
                                      led.OwnerLedTests.status, led.OwnerLedTests.snapshot)

    def setUp(self):
        led.OwnerLedTests.setUp(self)
        self.flag = chatlib.STATE_DIR / "experiment.enabled"
        patch = mock.patch.object(bridge.receipt_experiment, "FLAG_PATH", self.flag)
        patch.start()
        self.addCleanup(patch.stop)

    def experiment(self, on):
        if on:
            self.flag.write_text(bridge.receipt_experiment.EXPERIMENT + "\n")
        else:
            self.flag.unlink(missing_ok=True)

    def logged(self):
        path = chatlib.channel_log(self.scope["channel"])
        return [json.loads(line) for line in path.read_text().splitlines()]

    def rings(self):
        return [i for i in self.deliveries.items if i["kind"] == "ring"]

    def break_private(self, how):
        if how == "missing":
            PRIVATE_PATH().unlink(missing_ok=True)
        else:
            PRIVATE_PATH().write_text("{not json")


class PrivateFileTests(Base):
    def put(self, value):
        PRIVATE_PATH().write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())

    def invalid(self, value):
        self.put(value)
        with self.assertRaises(deferred.Unavailable) as caught:
            deferred.read_private()
        self.assertEqual(caught.exception.reason, "invalid")

    def test_a_valid_file_gives_the_scope_and_the_protected_lists(self):
        private = deferred.read_private()
        self.assertEqual(private.legacy, fixtures.S)
        self.assertEqual(private.workspaces, frozenset(fixtures.PRIVATE["protected_workspaces"]))
        self.assertEqual(private.surfaces, frozenset(fixtures.PRIVATE["protected_surfaces"]))

    def test_empty_protected_lists_are_valid_and_protect_nothing(self):
        self.put(good(protected_workspaces=[], protected_surfaces=[]))
        self.assertEqual(deferred.read_private().workspaces | deferred.read_private().surfaces, frozenset())
        self.agents[self.worker]["workspace_id"] = fixtures.PRIVATE["protected_workspaces"][0]
        self.agents[self.worker]["surface_id"] = fixtures.PRIVATE["protected_surfaces"][0]
        self.scope.update(surface_id=fixtures.PRIVATE["protected_surfaces"][0])
        self.enroll()
        self.assertTrue(deferred.OWNERSHIP.exists(), "nothing is protected, so the worker enrolls")

    def test_a_missing_file_is_missing(self):
        PRIVATE_PATH().unlink()
        with self.assertRaises(deferred.Unavailable) as caught:
            deferred.read_private()
        self.assertEqual(caught.exception.reason, "missing")

    def test_every_invalid_case_is_invalid(self):
        scope = fixtures.S
        cases = {
            "not UTF-8": b"\xff\xfe{}",
            "not JSON": b"{nope",
            "empty": b"",
            "an array": json.dumps([]).encode(),
            "a string": json.dumps("x").encode(),
            "duplicate key": b'{"legacy_scope": {}, "legacy_scope": {}, "protected_workspaces": [], "protected_surfaces": []}',
            "too large": b"{" + b" " * 70000 + b"}",
            "missing legacy_scope": {k: v for k, v in fixtures.PRIVATE.items() if k != "legacy_scope"},
            "missing protected_surfaces": {k: v for k, v in fixtures.PRIVATE.items() if k != "protected_surfaces"},
            "extra top-level field": good(extra="x"),
            "extra scope field": good(legacy_scope=dict(scope, role="solver")),
            "missing scope field": good(legacy_scope={k: v for k, v in scope.items() if k != "worker"}),
            "scope not an object": good(legacy_scope=[]),
            "scope field not a string": good(legacy_scope=dict(scope, request=7)),
            "scope field empty": good(legacy_scope=dict(scope, channel="")),
            "scope field too long": good(legacy_scope=dict(scope, surface_id="s" * 256)),
            "workspaces not an array": good(protected_workspaces="workspace-protected"),
            "surfaces not an array": good(protected_surfaces={"a": "b"}),
            "workspace entry not a string": good(protected_workspaces=["w", 3]),
            "workspace entry empty": good(protected_workspaces=[""]),
            "surface entry too long": good(protected_surfaces=["s" * 256]),
        }
        for name, value in cases.items():
            with self.subTest(name):
                self.invalid(value)

    def test_the_longest_allowed_values_are_valid(self):
        self.put(good(legacy_scope=dict(fixtures.S, surface_id="s" * 255), protected_workspaces=["w" * 255]))
        self.assertEqual(len(deferred.read_private().legacy["surface_id"]), 255)

    def test_an_unreadable_file_is_invalid(self):
        PRIVATE_PATH().chmod(0)
        self.addCleanup(PRIVATE_PATH().chmod, 0o600)
        if os.access(PRIVATE_PATH(), os.R_OK):
            self.skipTest("the permission bits do not stop this user from reading")
        with self.assertRaises(deferred.Unavailable) as caught:
            deferred.read_private()
        self.assertEqual(caught.exception.reason, "invalid")

    def test_a_path_that_is_not_a_regular_file_is_invalid_and_never_blocks(self):
        def alarm(*_):
            raise AssertionError("the read blocked")
        previous = signal.signal(signal.SIGALRM, alarm)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        for kind in ("fifo", "directory", "symlink"):
            with self.subTest(kind):
                PRIVATE_PATH().unlink(missing_ok=True)
                if kind == "fifo":
                    os.mkfifo(PRIVATE_PATH())
                elif kind == "directory":
                    PRIVATE_PATH().mkdir()
                else:
                    real = chatlib.STATE_DIR / "elsewhere.json"
                    real.write_text(json.dumps(fixtures.PRIVATE))
                    PRIVATE_PATH().symlink_to(real)
                signal.alarm(5)
                try:
                    with self.assertRaises(deferred.Unavailable) as caught:
                        deferred.read_private()
                finally:
                    signal.alarm(0)
                self.assertEqual(caught.exception.reason, "invalid")
                if kind == "directory":
                    PRIVATE_PATH().rmdir()

    def test_the_reason_never_carries_the_files_content(self):
        for raw in (b"{secret-not-json", b"\xff\xfe secret", json.dumps(good(extra="secret-value")).encode()):
            with self.subTest(raw=raw[:8]):
                self.put(raw)
                with self.assertRaises(deferred.Unavailable) as caught:
                    deferred.read_private()
                self.assertIn(caught.exception.reason, ("missing", "invalid"))
                self.assertNotIn("secret", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)

    def test_nothing_is_derived_from_the_file_at_import(self):
        for name in ("SCOPE", "TAG", "PROTECTED_WORKSPACES", "PROTECTED_SURFACES"):
            self.assertFalse(hasattr(deferred, name), name)

    def test_the_module_only_reads_the_file(self):
        before = PRIVATE_PATH().read_bytes(), PRIVATE_PATH().stat().st_mtime_ns
        self.enroll()
        self.status()
        deferred.acknowledge(self.snapshot(), self.pm)
        self.assertEqual((PRIVATE_PATH().read_bytes(), PRIVATE_PATH().stat().st_mtime_ns), before)
        PRIVATE_PATH().unlink()
        self.enroll(mid="again")
        self.assertFalse(PRIVATE_PATH().exists(), "the module never creates it")


class FailClosedTests(Base):
    """Missing or invalid, the hook enrolls nothing and binds no scope, for every post it evaluates."""

    def enrolled_run(self):
        self.enroll()
        self.status("deferred")
        self.deliveries.items.clear()
        return owner_led_bytes()

    def test_owner_posts_are_delivered_normally_and_enroll_nothing(self):
        for how in ("missing", "invalid"):
            with self.subTest(how):
                self.setUp()
                self.break_private(how)
                self.enroll()
                self.assertEqual([r["target"] for r in self.rings()], [self.worker])
                self.assertFalse(deferred.OWNERSHIP.exists())
                self.assertIsNone(self.snapshot())

    def test_a_post_that_would_match_an_existing_record_gets_ordinary_delivery_and_the_record_is_untouched(self):
        for how in ("missing", "invalid"):
            for legacy_record in (False, True):
                with self.subTest(how=how, legacy_record=legacy_record):
                    self.setUp()
                    if legacy_record:
                        self.enroll()
                        deferred.STATE.write_text(json.dumps(dict(
                            schema=deferred.LEGACY_SCHEMA, scope=fixtures.S, generation=1, seen={"old": 1},
                            pending=[dict(msgid="old", at="fixture-at", text="old", generation=1)])))
                        deferred.OWNERSHIP.chmod(0o600)
                        self.deliveries.items.clear()
                    else:
                        self.enrolled_run()
                    before = owner_led_bytes()
                    self.break_private(how)
                    with mock.patch.object(deferred, "ENABLED_PROJECTS", frozenset({"gamma", "alpha"})):
                        self.status("after-1")  # matches the existing gamma record
                        self.post("legacy-after")  # the legacy worker's status, too
                    self.assertEqual(len(self.rings()), 2, "both get the manager ring they would get today")
                    self.assertEqual(owner_led_bytes(), before, "no record changed, and none was created")
                    legacy_log = chatlib.channel_log(fixtures.S["channel"]).read_text().splitlines()
                    for entry in (self.logged()[-1], json.loads(legacy_log[-1])):
                        self.assertEqual(entry["receipt_routing"],
                                         {"owner_led": "off", "owner_led_reason": how})

    post = fixtures.OwnerDesignTests.post

    def test_the_legacy_record_is_preserved_not_migrated_while_the_file_is_missing(self):
        old = json.dumps(dict(schema=deferred.LEGACY_SCHEMA, scope=fixtures.S, generation=1, seen={"old": 1},
                              pending=[dict(msgid="old", at="fixture-at", text="old", generation=1)]))
        deferred.STATE.write_text(old)
        PRIVATE_PATH().unlink()
        with self.assertRaises(ValueError):
            deferred.snapshot(fixtures.S["project"])
        self.assertEqual(deferred.STATE.read_text(), old)

    def test_the_evidence_has_two_fixed_tokens_with_the_experiment_on_and_off(self):
        for how in ("missing", "invalid"):
            for on in (True, False):
                with self.subTest(how=how, experiment=on):
                    self.setUp()
                    self.experiment(on)
                    self.break_private(how)
                    self.status("evidence")
                    routing = self.logged()[-1]["receipt_routing"]
                    expected = {"owner_led": "off", "owner_led_reason": how}
                    if on:
                        expected.update(experiment=bridge.receipt_experiment.EXPERIMENT,
                                        classification="existing_delivery", manager_ring="unchanged")
                    self.assertEqual(routing, expected)

    def test_posts_the_hook_does_not_evaluate_gain_no_owner_led_evidence(self):
        for on in (True, False):
            with self.subTest(experiment=on):
                self.setUp()
                self.experiment(on)
                self.break_private("missing")
                for mid, text, reply in (("q", f"[question {self.scope['request']}] ?", None),
                                         ("r", f"[status {self.scope['request']}] reply", "parent"),
                                         ("n", f"[status {self.scope['request']}] @gam-manager hi", None),
                                         ("p", "plain chat", None)):
                    self.send(self.worker, mid, text, reply=reply)
                self.send("sam", "assistant", f"[request {self.scope['request']}] @{self.worker} go")
                self.send(self.worker, "unverified", f"[status {self.scope['request']}] x", verified=False)
                for entry in self.logged():
                    self.assertNotIn("owner_led", json.dumps(entry.get("receipt_routing", {})), entry["msgid"])

    def test_a_review_start_is_still_suppressed_on_its_own_and_gets_no_owner_led_evidence(self):
        import test_receipt_experiment as rx
        self.experiment(True)
        self.break_private("missing")
        self.cfg = copy.deepcopy(rx.CFG)
        self.agents.clear()
        self.agents.update(copy.deepcopy(rx.AGENTS))
        message = privmsg("nov-reviewer-8", rx.TEXT, "receipt", channel=rx.CHANNEL)
        bridge.handle(message, self.cfg, self.record, self.deliveries, self.acks)
        self.assertEqual(self.deliveries.items, [])
        entry = json.loads(chatlib.channel_log(rx.CHANNEL).read_text().splitlines()[0])
        self.assertEqual(entry["receipt_routing"]["classification"], "routine_review_start")
        self.assertNotIn("owner_led", entry["receipt_routing"])

    def test_a_change_of_the_file_takes_effect_on_the_next_post_without_a_reimport(self):
        self.enroll()
        self.status("one")
        self.assertEqual(len(self.snapshot()["pending"]), 1)
        self.deliveries.items.clear()
        PRIVATE_PATH().unlink()
        self.status("two")
        self.assertEqual(len(self.rings()), 1, "removed: ordinary delivery on the very next post")
        self.assertEqual(len(self.snapshot()["pending"]), 1)
        self.break_private("invalid")
        self.status("three")
        self.assertEqual(len(self.rings()), 2)
        PRIVATE_PATH().write_text(json.dumps(fixtures.PRIVATE))
        self.status("four")
        self.assertEqual(len(self.rings()), 2, "created again: deferred again")
        self.assertEqual([e["msgid"] for e in self.snapshot()["pending"]], ["one", "four"])
        self.assertEqual([e["receipt_routing"].get("owner_led_reason") for e in self.logged() if e["msgid"] != "owner-request"],
                         [None, "missing", "invalid", None])

    def test_the_example_file_is_never_read(self):
        example = Path(__file__).resolve().parents[2] / "owner-led-private.json.example"
        self.assertTrue(example.is_file())
        PRIVATE_PATH().unlink()
        (chatlib.STATE_DIR / "owner-led-private.json.example").write_text(example.read_text())
        self.enroll()
        self.assertFalse(deferred.OWNERSHIP.exists())

    def test_the_shipped_example_is_valid_and_invented(self):
        example = Path(__file__).resolve().parents[2] / "owner-led-private.json.example"
        PRIVATE_PATH().write_text(example.read_text())
        private = deferred.read_private()
        self.assertEqual(set(private.legacy), set(deferred.SCOPE_FIELDS))
        self.assertTrue(private.workspaces and private.surfaces)


class ProtectedTests(Base):
    def recorded_scope(self, surface):
        """A record for the worker as if enrolled, written by hand so that only the protected list can refuse it
        (enrollment itself refuses a protected surface)."""
        scope = dict(self.scope, surface_id=surface,
                     authority=dict(kind="verified_owner_request", owner="pat", msgid="m0", at="fixture-at"))
        deferred.OWNERSHIP.write_text(json.dumps({"schema": "owner-led-scopes-v1", "scopes": [scope]}))
        self.agents[self.worker] = dict(scope, status="active")
        self.scope["surface_id"] = surface

    def test_a_protected_workspace_refuses_binding(self):
        self.enroll()
        self.deliveries.items.clear()
        self.status("control")
        self.assertEqual(self.deliveries.items, [], "control: bound while the workspace is not protected")
        self.agents[self.worker]["workspace_id"] = fixtures.PRIVATE["protected_workspaces"][0]
        self.status("protected")
        self.assertEqual(len(self.rings()), 1)
        self.assertEqual(len(self.snapshot()["pending"]), 1)

    def test_a_protected_surface_refuses_binding(self):
        self.recorded_scope("surface-ordinary")
        self.status("control")
        self.assertEqual(self.deliveries.items, [], "control: bound while the surface is not protected")
        deferred.OWNERSHIP.unlink()
        self.recorded_scope(fixtures.PRIVATE["protected_surfaces"][1])
        self.status("protected")
        self.assertEqual(len(self.rings()), 1)
        self.assertEqual(len(self.snapshot()["pending"]), 1)

    def test_a_protected_actor_cannot_be_bound_through_the_legacy_scope_either(self):
        legacy = fixtures.S
        self.agents[legacy["worker"]]["workspace_id"] = fixtures.PRIVATE["protected_workspaces"][0]
        self.post("legacy-protected")
        self.assertEqual(len(self.rings()), 1)
        self.assertIsNone(deferred.snapshot(legacy["project"]))

    post = fixtures.OwnerDesignTests.post

    def test_the_protected_lists_come_only_from_the_file(self):
        PRIVATE_PATH().write_text(json.dumps(good(protected_workspaces=["another-workspace"])))
        self.agents[self.worker]["workspace_id"] = fixtures.PRIVATE["protected_workspaces"][0]
        self.enroll()
        self.assertTrue(deferred.OWNERSHIP.exists(), "only the listed workspace is protected")


class IdentityRuleTests(Base):
    """#29's rule: the owner is a server-verified account that is the configured owner or an exact entry of
    `owner_accounts`; the accounts are one owner."""

    def setUp(self):
        super().setUp()
        self.cfg["owner_accounts"] = ["pat-two"]
        self.cfg["accounts"]["pat-two"] = "fixture"

    def scopes(self):
        return json.loads(deferred.OWNERSHIP.read_text())["scopes"]

    def test_a_verified_second_account_enrolls_and_routes_as_the_configured_owner_does(self):
        self.enroll("pat-two")
        self.assertEqual([r["target"] for r in self.rings()], [self.worker])
        authority = self.scopes()[0]["authority"]
        self.assertEqual((authority["owner"], authority["account"]), ("pat", "pat-two"))
        self.assertEqual(self.logged()[0]["from"], "pat-two", "the raw sender account stays as evidence")
        self.deliveries.items.clear()
        self.status("a")
        self.assertEqual(self.deliveries.items, [])
        self.assertEqual(len(self.snapshot()["pending"]), 1)

    def test_the_configured_owner_gets_the_same_record_it_always_did(self):
        self.enroll("pat")
        authority = self.scopes()[0]["authority"]
        self.assertEqual(set(authority), {"kind", "owner", "msgid", "at"})
        self.assertEqual(authority["owner"], "pat")

    def test_without_owner_accounts_routing_is_unchanged_and_a_second_account_is_nobody(self):
        del self.cfg["owner_accounts"]
        self.enroll("pat-two")
        self.assertFalse(deferred.OWNERSHIP.exists())
        self.enroll("pat", mid="owner")
        self.assertEqual(set(self.scopes()[0]["authority"]), {"kind", "owner", "msgid", "at"})
        self.status("a")
        self.assertEqual(len(self.snapshot()["pending"]), 1)

    def test_an_unverified_post_whose_nick_is_the_owner_is_not_the_owner(self):
        for sender in ("pat", "pat-two"):
            with self.subTest(sender):
                self.enroll(sender, mid=f"unverified-{sender}", verified=False)
                self.assertFalse(deferred.OWNERSHIP.exists())
        entry = dict(verified=False, msgid="m", at="t", channel=self.scope["channel"], text="[request r] @w go",
                     **{"from": "pat"})
        self.assertFalse(deferred.enroll_owner_request(entry, "gamma", self.agents, {self.worker}, self.cfg))
        entry["verified"] = "yes"  # only an affirmative True counts
        self.assertFalse(deferred.enroll_owner_request(entry, "gamma", self.agents, {self.worker}, self.cfg))

    def test_an_unlisted_account_an_assistant_and_a_lookalike_do_not_enroll(self):
        self.cfg["accounts"].update({"pat-three": "fixture", "pat-twoo": "fixture"})
        for sender in ("pat-three", "sam", "pat-twoo", "Pat-two"):
            self.enroll(sender, mid=sender)
        self.assertFalse(deferred.OWNERSHIP.exists())

    def test_a_malformed_owner_accounts_list_leaves_only_the_configured_owner(self):
        for bad in ("pat-two", ["pat-two", 5], ["pat two"], ["pat-two", "sam"], {"a": "b"}):
            with self.subTest(bad=bad):
                self.setUp()
                self.cfg["owner_accounts"] = bad
                self.cfg["accounts"]["pat-two"] = "fixture"
                self.enroll("pat-two")
                self.assertFalse(deferred.OWNERSHIP.exists(), "rejected wholesale, so the second account is nobody")
                self.enroll("pat", mid="owner")
                self.assertEqual(self.scopes()[0]["authority"]["owner"], "pat")
                self.assertEqual(len(self.scopes()), 1)

    def test_two_owner_accounts_on_one_request_make_one_enrollment_and_one_receipt_stream(self):
        self.enroll("pat", mid="first")
        self.enroll("pat-two", mid="second")
        self.assertEqual(len(self.scopes()), 1)
        self.status("a")
        self.status("b")
        self.assertEqual([e["msgid"] for e in self.snapshot()["pending"]], ["a", "b"])

    def test_an_owner_post_delivered_again_enrolls_and_records_nothing_twice(self):
        self.enroll("pat-two", mid="owner-post")
        self.status("receipt")
        before = owner_led_bytes()
        deliveries = len(self.deliveries.items)
        # the same two posts again: in this process, after a restart, and as a replayed catch-up
        for label, record, replayed in (("same record", self.record, False), ("restart", bridge.Record(), False),
                                        ("replay", bridge.Record(), True)):
            with self.subTest(label):
                for sender, mid, text in (("pat-two", "owner-post",
                                           f"[request {self.scope['request']}] @{self.worker} work with me "),
                                          (self.worker, "receipt", f"[status {self.scope['request']}] progress")):
                    msg = privmsg(sender, text, mid, channel=self.scope["channel"])
                    bridge.handle(msg, self.cfg, record, self.deliveries, self.acks, replayed=replayed)
                self.assertEqual(owner_led_bytes(), before)
                self.assertEqual(len(self.deliveries.items), deliveries)
        self.assertEqual(len(self.scopes()), 1)
        self.assertEqual(len(self.snapshot()["pending"]), 1)


class EvidenceTests(Base):
    """What the bridge records and does, with the review-start experiment on and off."""

    def test_an_owner_led_receipt_is_suppressed_with_the_experiment_on_and_off(self):
        for on in (True, False):
            with self.subTest(experiment=on):
                self.setUp()
                self.experiment(on)
                self.enroll()
                self.deliveries.items.clear()
                self.status("a")
                self.assertEqual(self.deliveries.items, [])
                self.assertEqual(self.logged()[-1]["receipt_routing"],
                                 {"classification": "owner_design_deferred", "manager_ring": "deferred",
                                  "request": self.scope["request"]})

    def test_an_ordinary_status_keeps_the_experiments_evidence_and_gains_nothing_owner_led(self):
        self.experiment(True)
        self.status("plain")  # nobody enrolled the worker, and the file is valid
        self.assertEqual(self.logged()[-1]["receipt_routing"],
                         {"experiment": bridge.receipt_experiment.EXPERIMENT,
                          "classification": "existing_delivery", "manager_ring": "unchanged"})
        self.assertEqual(len(self.rings()), 1)

    def test_a_hook_failure_keeps_ordinary_delivery_and_accurate_evidence(self):
        for on in (True, False):
            for failure in (OSError("fixture"), ValueError("fixture"), TypeError("fixture")):
                with self.subTest(experiment=on, failure=type(failure).__name__):
                    self.setUp()
                    self.experiment(on)
                    self.enroll()
                    self.deliveries.items.clear()
                    with mock.patch.object(deferred, "record", side_effect=failure):
                        self.status("fails")
                    self.assertEqual(len(self.rings()), 1, "the manager still hears it")
                    routing = self.logged()[-1].get("receipt_routing")
                    if on:
                        self.assertEqual(routing, {"experiment": bridge.receipt_experiment.EXPERIMENT,
                                                   "classification": "existing_delivery",
                                                   "manager_ring": "unchanged"})
                    else:
                        self.assertIsNone(routing, "the failed hook claims nothing")
                    self.assertIsNone(self.snapshot())

    def test_the_evidence_names_no_exception_text(self):
        with mock.patch.object(deferred, "record", side_effect=OSError("secret-detail")):
            self.enroll()
            self.status("fails")
        self.assertNotIn("secret-detail", json.dumps(self.logged()))
        self.assertIn("OSError", bridge.BRIDGE_LOG.read_text())
        self.assertNotIn("secret-detail", bridge.BRIDGE_LOG.read_text())


class AdmissionTests(admission.BridgeHoldCase, Base):
    """Owner-led state stays under writer admission (docs/writer_admission.md 3.1, 3.2 and 4.2)."""

    def setUp(self):
        admission.BridgeHoldCase.setUp(self)
        self.owner_fixture()

    def owner_fixture(self):
        """The gamma fixture on top of this case's authority: it must not wipe the state directory again."""
        with mock.patch.object(fixtures, "reset_state", lambda: None):
            Base.setUp(self)

    def run_script(self, script, expect):
        cfg = copy.deepcopy(OUTBOX_CFG)
        cfg["accounts"]["alp-solver-2"] = "x"
        cfg["identities"] = {"alp-solver-2": {"role": "solver", "project": "alpha"}}
        cfgfile = chatlib.STATE_DIR / "config.json"
        cfgfile.write_text("{}")
        self.state = (bridge.Record(), bridge.Deliveries(), bridge.Acks(), bridge.Checkpoints())
        self.conn = admission.ScriptedConnection(script)
        with mock.patch.object(chatlib, "CONFIG_PATH", cfgfile), \
                mock.patch.object(chatlib, "login", lambda *a, **k: self.conn), \
                mock.patch.object(bridge, "OUTBOX_AUTHORITY", self.authority), \
                mock.patch.object(deferred, "ENABLED_PROJECTS", frozenset({"alpha"})), \
                self.assertRaises(expect):
            bridge.run(cfg, *self.state, coverage=bridge.Coverage(self.authority))

    def worker_agents(self):
        agents = {"alp-solver-2": dict(project="alpha", role="solver", status="active", request="alpha-20260105-1",
                                       channel="#alp-design", session_id="alp-session", surface_id="alp-surface")}
        return mock.patch.object(bridge.identities, "read_registry", lambda: {"agents": agents})

    def owner_post(self):
        return [join("#alp-design"),
                privmsg("pat", "[request alpha-20260105-1] @alp-solver-2 work with me", "owner-1",
                        channel="#alp-design")]

    def test_an_owner_post_under_held_maintenance_creates_and_changes_no_owner_led_file(self):
        owner = self.hold("partial")
        before = snapshot(chatlib.STATE_DIR, skip=("bridge.log", "config.json"))
        with self.worker_agents():
            self.run_script(self.owner_post(), bridge.Hold)
        for path in OWNER_FILES():
            self.assertFalse(path.exists(), path.name)
        self.assertEqual(snapshot(chatlib.STATE_DIR, skip=("bridge.log", "config.json")), before)
        self.assertEqual(self.state[1].items, [])
        owner.release()
        with self.worker_agents():
            self.run_script(self.owner_post(), chatlib.ChatError)
        self.assertEqual(json.loads(deferred.OWNERSHIP.read_text())["scopes"][0]["worker"], "alp-solver-2")
        self.assertEqual([i["target"] for i in self.state[1].items if i["kind"] == "ring"], ["alp-solver-2"])

    def test_acknowledge_under_held_or_unknown_maintenance_changes_nothing_and_says_so(self):
        for state in ("partial", "full", "unknown"):
            with self.subTest(state):
                self.fresh()
                self.owner_fixture()
                self.enroll()
                self.status("a")
                captured = self.snapshot()
                handoff = self.handoff.read_bytes()
                owner = self.hold_in(state)
                before = (owner_led_bytes(), self.handoff.read_bytes())
                with self.assertRaises(deferred.MaintenanceHeld):
                    deferred.acknowledge(captured, self.pm)
                self.assertEqual((owner_led_bytes(), self.handoff.read_bytes()), before)
                self.assertEqual(self.handoff.read_bytes(), handoff)
                self.assertIsNotNone(self.snapshot(), "the receipts stay pending")
                if owner is None:
                    self.paths.maintenance.unlink()
                else:
                    owner.release()
                deferred.acknowledge(captured, self.pm)
                self.assertIsNone(self.snapshot())
                self.assertEqual(self.handoff.read_text().count("- OWNER-MANAGED"), 1)

    def test_acknowledge_writes_only_inside_its_own_section(self):
        self.enroll()
        self.status("a")
        captured = self.snapshot()
        seen = []
        real = deferred._atomic

        def atomic(path, text, mode=0o600):
            fd = os.open(self.paths.admission_lock, os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen.append((path.name, "not held"))
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except BlockingIOError:
                    seen.append((path.name, "held shared"))
            finally:
                os.close(fd)
            return real(path, text, mode)
        with mock.patch.object(deferred, "_atomic", atomic):
            deferred.acknowledge(captured, self.pm)
        self.assertEqual(seen, [("handoff.md", "held shared"), (deferred.STATE.name, "held shared")])

    def test_acknowledge_without_the_private_file_changes_nothing(self):
        self.enroll()
        self.status("a")
        captured = self.snapshot()
        before = (owner_led_bytes(), self.handoff.read_bytes())
        PRIVATE_PATH().unlink()
        with self.assertRaises(deferred.Unavailable):
            deferred.acknowledge(captured, self.pm)
        self.assertEqual((owner_led_bytes(), self.handoff.read_bytes()), before)

    def test_report_lists_each_deferred_run_without_relaying_choices(self):
        self.enroll()
        self.status("a")
        self.status("b")
        lines = deferred.report(self.snapshot())
        self.assertIn(f"owner-managed {self.scope['request']}: 2 deferred design receipt(s) in "
                      f"{self.scope['channel']}, msgid=a through msgid=b", lines[0])
        self.assertIn("do not relay choices", lines[1])
        self.assertIn("progress", lines[2])


if __name__ == "__main__":
    unittest.main()
