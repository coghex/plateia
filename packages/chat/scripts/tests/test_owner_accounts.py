"""Owner accounts (#29, staged slice 1): the optional `owner_accounts` list makes each listed, server-verified
account the owner for the bridge's owner checks, with one owner push per message. Three offline scenarios (the
inbox core's four are in routine_inbox/tests/test_owner_accounts.py) on invented data: owner `pat`, second owner
account `quill`, assistant `sam`, manager `alpha-manager`, worker `alp-solver-2`, project `alpha`. Subcases cover
the variations. Owner-led routing is not part of this tree."""
import importlib.machinery
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)

STATE = str(_isolation.CHAT_STATE)
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import chatlib  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("chat_bridge_owner_accounts", str(SCRIPTS / "chat-bridge"))
_spec = importlib.util.spec_from_loader("chat_bridge_owner_accounts", _loader)
bridge = importlib.util.module_from_spec(_spec)
_loader.exec_module(bridge)
_isolation.check_bound(chatlib, bridge)

# `quill` has no password entry and no identity: the second account is recognized from the list alone.
BASE = {"owner": "pat", "assistants": ["sam"],
        "accounts": {a: "x" for a in ("pat", "sam", "alpha-manager", "alp-solver-2", "chatbridge")},
        "projects": {"alpha": {"channel": "#alpha", "prefix": "#alp-", "manager": "alpha-manager"}}}
TWO = dict(BASE, owner_accounts=["quill"])


def privmsg(sender, text, msgid, channel="#alp-1", account=True):
    tags = {"msgid": msgid, "time": "2026-10-13T00:00:00.000Z"}
    if account:
        tags["account"] = sender
    return {"tags": tags, "prefix": f"{sender}!u@h", "command": "PRIVMSG", "params": [channel, text]}


class OwnerIdentityTests(unittest.TestCase):
    def test_exact_verified_listed_accounts_only_and_a_malformed_list_grants_nothing(self):
        self.exact_accounts()
        self.citations()
        self.malformed_lists()

    def exact_accounts(self):
        for account, verified, expected in (
                ("pat", True, "pat"), ("quill", True, "pat"),  # either account names the one canonical owner
                ("quill", False, None), ("quill", None, None), ("quill", "yes", None),  # affirmative only
                ("quill2", True, None), ("qui", True, None), ("Quill", True, None), ("pat2", True, None),
                (None, True, None), ("sam", True, None), ("mallory", True, None)):
            with self.subTest(account=account, verified=verified):
                self.assertEqual(chatlib.owner_identity(TWO, account, verified), expected)

    def citations(self):
        record = {"q-ok": {"from": "quill", "verified": True},
                  "q-unverified": {"from": "quill", "verified": False},
                  "q-lookalike": {"from": "quill2", "verified": True},
                  "q-nick-only": {"from": "guest123", "nick": "quill", "verified": False},
                  "q-unlisted": {"from": "mallory", "verified": True},
                  "s-assistant": {"from": "sam", "verified": True}}
        with mock.patch.object(chatlib, "find_message", lambda msgid: record.get(msgid)):
            for msgid in ("q-ok", "s-assistant"):  # the assistant keeps its delegated authority
                with self.subTest(msgid=msgid):
                    entry, why = chatlib.cited_authority(msgid, TWO)
                    self.assertEqual((entry, why), (record[msgid], ""))  # the raw account is kept as evidence
            for msgid in ("q-unverified", "q-lookalike", "q-nick-only", "q-unlisted"):
                with self.subTest(msgid=msgid):
                    self.assertIsNone(chatlib.cited_authority(msgid, TWO)[0])
            # without the key the second account has no authority at all
            self.assertIsNone(chatlib.cited_authority("q-ok", BASE)[0])

    def malformed_lists(self):
        for value in ("quill", "pat,quill", ("quill",), {"quill": 1}, ["quill", 7], ["quill", None], [["quill"]],
                      ["quill "], ["qui ll"], [""], ["quill", "sam"], 7, True, None):
            with self.subTest(value=value):
                cfg = dict(BASE, owner_accounts=value)
                self.assertEqual(chatlib.owner_accounts(cfg), {"pat"})
                self.assertIsNone(chatlib.owner_identity(cfg, "quill", True))
        for value in ([], ["pat"]):
            with self.subTest(value=value):
                self.assertEqual(chatlib.owner_accounts(dict(BASE, owner_accounts=value)), {"pat"})
        self.assertEqual(chatlib.owner_accounts(BASE), {"pat"})
        self.assertEqual(chatlib.authority_accounts(BASE), {"pat", "sam"})
        self.assertEqual(chatlib.owner_accounts(dict(BASE, owner_accounts=["quill", "quill", "pat"])),
                         {"pat", "quill"})  # duplicates add nothing
        self.assertEqual(chatlib.authority_accounts(TWO), {"pat", "quill", "sam"})
        # no usable owner, no owner accounts: the list alone grants nothing
        self.assertEqual(chatlib.owner_accounts({"assistants": ["sam"], "owner_accounts": ["quill"]}), frozenset())


class BridgeRoutingCase(unittest.TestCase):
    def setUp(self):
        self.count = 0

    def fresh(self):
        for f in Path(STATE).glob("**/*"):
            if f.is_file():
                f.unlink()
        (Path(STATE) / "logs").mkdir(parents=True, exist_ok=True)
        agents = {"alp-solver-2": {"status": "active", "surface_id": "S2", "brand": "codex", "project": "alpha"}}
        (Path(STATE) / "identities.json").write_text(json.dumps({"agents": agents}))
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()

    def route(self, cfg, sender, text, account=True):
        """The deliveries one post queues: (kind, target) pairs, and the queued items."""
        self.fresh()
        self.count += 1
        bridge.handle(privmsg(sender, text, f"m{self.count}", account=account), cfg, self.r, self.d, self.a)
        return sorted((i["kind"], i.get("target", "manager" if i["kind"] == "ring" else "owner"))
                      for i in self.d.items), self.d.items


class DirectAndWaitingRoutingTests(BridgeRoutingCase):
    def test_the_second_account_routes_directly_and_is_taken_by_a_manager_waiting_on_the_owner(self):
        self.direct_routing()
        self.waiting_owner()

    def direct_routing(self):
        for text, expected, needs_ack in (
                ("@alp-solver-2 @alpha-manager hold", [("ring", "alp-solver-2"), ("ring", "manager")], True),
                ("fix it and merge", [("ring", "manager")], True),
                ("@alp-solver-2 stop", [("ring", "alp-solver-2")], False)):
            with self.subTest(text=text):
                kinds, items = self.route(TWO, "quill", text)
                self.assertEqual(kinds, expected)  # one set of rings; never a push to the owner for its own post
                self.assertEqual({i["needs_ack"] for i in items if i["kind"] == "ring" and "target" not in i},
                                 {needs_ack} if needs_ack else set())
                self.assertTrue(all(i["sender"] == "quill" for i in items))  # the raw account stays the evidence
        with self.subTest(case="over the manager's head"):
            kinds, _ = self.route(TWO, "alp-solver-2", "@quill which approach?")
            self.assertEqual(kinds, [("push", "owner"), ("ring", "manager")])
        with self.subTest(case="not an owner account"):
            for sender in ("quill2", "mallory"):
                self.assertEqual(self.route(TWO, sender, "@alp-solver-2 stop")[0], [])
        with self.subTest(case="key absent: unchanged"):
            self.assertEqual(self.route(BASE, "quill", "@alp-solver-2 stop")[0], [])
            self.assertEqual(self.route(BASE, "alp-solver-2", "@pat @quill which?")[0],
                             [("push", "owner"), ("ring", "manager")])  # quill has no entry: only pat is named

    def waiting_owner(self):
        typed = []

        def cmux(*args):
            if args[:2] == ("agent", "message"):
                return 0, json.dumps({"id": "a1", "state": "queued"}), ""
            return 0, "{}", ""
        for sender, account, expect in (("quill", True, True), ("pat", True, True), ("sam", True, True),
                                        ("quill2", True, False), ("mallory", True, False)):
            with self.subTest(sender=sender):
                self.fresh()
                item = {"msgid": "m9", "channel": "#alp-1", "sender": sender, "text": "approved", "kind": "ring",
                        "project": "alpha", "attempt": 0, "next_at": 0}
                typed.clear()
                with mock.patch.object(bridge, "_cmux", cmux), \
                        mock.patch.object(bridge, "_input_state", lambda s: {"waiting_on_human": True}), \
                        mock.patch.object(bridge, "_type_into_prompt",
                                          lambda s, text, *a: (typed.append(text) or True, "typed")):
                    ok, _ = bridge._ring_surface(TWO, item, "SURF-1", "claude", "manager")
                self.assertEqual((ok is True, bool(typed)), (expect, expect))


class MentionPushTests(BridgeRoutingCase):
    def test_one_push_however_many_owner_accounts_a_post_names_and_none_for_an_owner(self):
        for text in ("@pat @quill need a decision", "@quill need a decision", "quill: need a decision",
                     "@quill @pat @quill again"):
            with self.subTest(text=text):
                kinds, _ = self.route(TWO, "alp-solver-2", text)
                self.assertEqual(kinds.count(("push", "owner")), 1)
        for sender, text in (("quill", "@pat please look"), ("pat", "@quill fyi"), ("pat", "@pat @quill note"),
                             ("quill", "@quill @pat note")):
            with self.subTest(sender=sender, text=text):
                kinds, _ = self.route(TWO, sender, text)
                self.assertEqual(kinds, [])  # no mention-driven push from any owner account, even naming the other
        with self.subTest(case="unverified senders and lookalikes"):
            self.assertEqual(self.route(TWO, "quill", "@pat hi", account=False)[0], [])  # a guest named quill
            self.assertEqual(self.route(TWO, "quill2", "@pat hi")[0].count(("push", "owner")), 1)  # an ordinary sender
        with self.subTest(case="the push is a distinct item from a failure or stuck notice"):
            _, items = self.route(TWO, "alp-solver-2", "@quill need a decision")
            self.assertEqual([(i["kind"], i["sender"]) for i in items if i["kind"] == "push"],
                             [("push", "alp-solver-2")])
        with self.subTest(case="key absent: today's single push"):
            self.assertEqual(self.route(BASE, "alp-solver-2", "@pat need you")[0].count(("push", "owner")), 1)
            self.assertNotIn(("push", "owner"), self.route(BASE, "pat", "@pat note")[0])


if __name__ == "__main__":
    unittest.main()
