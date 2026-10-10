"""Tests for the chat bridge's recovery paths. Run:
    python3 -m unittest discover -s ~/.codex/skills/chat/scripts/tests
They use a temporary state directory and a fake cmux; no server is needed."""
import importlib.machinery
import importlib.util
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

STATE = str(_isolation.CHAT_STATE)
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import chatlib  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("chat_bridge", str(SCRIPTS / "chat-bridge"))
_spec = importlib.util.spec_from_loader("chat_bridge", _loader)
bridge = importlib.util.module_from_spec(_spec)
_loader.exec_module(bridge)
_isolation.check_bound(chatlib, bridge)
from outbox_fakes import OutboxCase, in_process_authority  # noqa: E402  (#19: the in-process outbox authority)

CFG = {"owner": "pat", "assistants": ["claude", "codex", "sam"],
       "accounts": {a: "x" for a in ("pat", "claude", "codex", "sam", "nov-manager", "nov-solver-1",
                                     "nov-solver-2", "nov-solver-3", "chatbridge")},
       "projects": {"nov": {"channel": "#nov", "prefix": "#nov-", "manager": "nov-manager"}}}
SID = "AAAA-SURFACE"


def reset_state():
    for f in Path(STATE).glob("**/*"):
        if f.is_file():
            f.unlink()
    (Path(STATE) / "logs").mkdir(parents=True, exist_ok=True)


def privmsg(sender, text, msgid, channel="#nov", reply_to=None, t="2026-10-02T00:00:00.000Z"):
    tags = {"account": sender, "msgid": msgid, "time": t}
    if reply_to:
        tags["+draft/reply"] = reply_to
    return {"tags": tags, "prefix": f"{sender}!u@h", "command": "PRIVMSG", "params": [channel, text]}


class FakePrompt:
    """A Claude prompt as cmux sees it, with switchable faults."""

    def __init__(self, mangle=False, enter_rc=0, submits=True):
        self.draft, self.transcript, self.lifecycle = "", "earlier output", "idle"
        self.mangle, self.enter_rc, self.submits = mangle, enter_rc, submits
        self.keys = []

    def __call__(self, *args):
        if args[:2] == ("rpc", "surface.input_state"):
            return 0, json.dumps({"surface_id": SID, "state": "draft" if self.draft else "empty",
                                  "lifecycle": self.lifecycle, "waiting_on_human": True}), ""
        if args[0] == "send":
            text = args[-1]
            if self.mangle:  # the middle changes, both ends survive
                mid = len(text) // 2
                text = text[:mid] + "XXXX" + text[mid + 4:]
            self.draft = text
            return 0, "", ""
        if args[0] == "read-screen":
            # wrap the draft like a terminal would
            body = "\n  ".join(self.draft[i:i + 50] for i in range(0, len(self.draft), 50))
            return 0, f"{self.transcript}\n{'─' * 20}\n❯ {body}\n{'─' * 20}\n  -- INSERT --", ""
        if args[0] == "send-key":
            self.keys.append(args[-1])
            if args[-1] == "ctrl+c":
                self.draft = ""
            elif args[-1] == "enter":
                if self.enter_rc:
                    return self.enter_rc, "", "error"
                if self.submits:
                    self.transcript += "\n> " + self.draft
                    self.draft, self.lifecycle = "", "running"
            return 0, "", ""
        return 0, "{}", ""


@mock.patch.object(bridge.time, "sleep", lambda s: None)
class TypingTests(unittest.TestCase):
    TEXT = "[chat] [#nov msgid=abc] <pat> " + "please fix the loader crash and merge it " * 3

    def run_with(self, fake):
        with mock.patch.object(bridge, "_cmux", fake):
            return bridge._type_into_prompt(SID, self.TEXT)

    def test_success_needs_submission_evidence(self):
        ok, detail = self.run_with(FakePrompt())
        self.assertTrue(ok, detail)

    def test_enter_failure_is_not_success(self):
        fake = FakePrompt(enter_rc=1)
        ok, detail = self.run_with(fake)
        self.assertFalse(ok)
        self.assertIn("Enter failed", detail)
        self.assertEqual(fake.draft, "", "failed draft should be cleared")

    def test_changed_middle_is_rejected_before_enter(self):
        fake = FakePrompt(mangle=True)
        ok, detail = self.run_with(fake)
        self.assertFalse(ok)
        self.assertNotIn("enter", fake.keys)
        self.assertEqual(fake.draft, "")

    def test_enter_without_submission_is_not_success(self):
        fake = FakePrompt(submits=False)
        ok, detail = self.run_with(fake)
        self.assertFalse(ok)
        self.assertIn("no evidence", detail)


class OutboxTests(OutboxCase):
    """The outbox keeps every post it owes until it is sent (#19 revision 8: one
    SQLite authority; queued posts arrive as fallback files or legacy rows)."""

    def write_legacy(self, path, *rows):
        with path.open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def test_post_queued_during_flush_survives(self):
        self.write_legacy(Path(STATE) / "outbox.jsonl", {"channel": "#nov", "as": "claude", "text": "A", "at": "t"})
        real_login = self.server.login

        def login(account, *a, **k):  # a concurrent pchat queues B mid-flush, the authority not answering it
            if account == "claude" and not self.fallback_files():
                self.server.refuse_login.add("codex")
                with self.unavailable():
                    self.pchat("post", "#nov", "B", account="codex")
                self.server.refuse_login.discard("codex")
            return real_login(account, *a, **k)
        with mock.patch.object(chatlib, "login", login):
            self.flush()
        self.assertEqual([m["text"][0] for m in self.server.published], ["A"])
        self.assertEqual(len(self.fallback_files()), 1)
        self.flush()
        self.assertEqual([m["text"][0] for m in self.server.published], ["A", "B"])
        self.assertEqual(self.fallback_files(), [])

    def test_failures_are_requeued_and_crashed_claims_resumed(self):
        self.write_legacy(Path(STATE) / "outbox.claimed-1.jsonl",
                          {"channel": "#nov", "as": "claude", "text": "left by a crash", "at": "t"})
        self.write_legacy(Path(STATE) / "outbox.jsonl", {"channel": "#nov", "as": "codex", "text": "fails", "at": "t"})
        self.server.refuse_login = {"codex"}
        self.flush()
        self.assertTrue(self.server.published and self.server.published[0]["text"].startswith("left by a crash"))
        self.assertEqual([e["state"] for e in self.entries() if e["account"] == "codex"], ["open"])

    def test_tag_and_reply_survive_the_outbox(self):
        self.write_legacy(Path(STATE) / "outbox.jsonl",
                          {"channel": "#nov", "as": "claude", "text": "[status r-20261002-1] x",
                           "cont": "[status r-20261002-1]", "reply_to": "m1", "at": "t"})
        self.flush()
        [m] = self.server.published
        self.assertIn("+draft/reply=m1", m["tags"])
        self.assertTrue(m["text"].startswith("[status r-20261002-1] x"))


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()
        self.cmux = mock.patch.object(bridge, "_cmux", lambda *a: (0, "{}", ""))
        self.cmux.start()

    def tearDown(self):
        self.cmux.stop()

    def request(self, sender="pat", msgid="req1"):
        bridge.handle(privmsg(sender, "fix #41", msgid), CFG, self.r, self.d, self.a)
        ring = [i for i in self.d.items if i["kind"] == "ring"][0]
        ring.update(phase="accept", next_at=time.time() + 900)  # as if delivered
        return ring

    def test_owner_request_needs_acceptance(self):
        self.assertTrue(self.request()["needs_ack"])

    def test_worker_post_needs_no_acceptance(self):
        bridge.handle(privmsg("nov-solver-1", "started", "w1", channel="#nov-x"), CFG, self.r, self.d, self.a)
        self.assertFalse(self.d.items[0]["needs_ack"])

    def test_unrelated_manager_status_is_not_acceptance(self):
        self.request()
        bridge.handle(privmsg("nov-manager", "[status] something else", "s1"), CFG, self.r, self.d, self.a)
        self.assertEqual(len(self.d.items), 1)

    def test_reply_from_manager_accepts(self):
        self.request()
        bridge.handle(privmsg("nov-manager", "on it", "s2", channel="#nov-41", reply_to="req1"),
                      CFG, self.r, self.d, self.a)
        self.assertEqual(self.d.items, [])

    def test_reply_from_worker_does_not_accept(self):
        self.request()
        bridge.handle(privmsg("nov-solver-1", "on it", "s3", channel="#nov-41", reply_to="req1"),
                      CFG, self.r, self.d, self.a)
        self.assertIn("req1", [i["msgid"] for i in self.d.items])  # still open (the reply rings too)

    def test_ack_tagmsg_accepts(self):
        self.request()
        bridge.handle_tagmsg({"tags": {"account": "nov-manager", "msgid": "t1", "+draft/reply": "req1",
                                       "+draft/react": "ack"}, "prefix": "nov-manager!u@h",
                              "command": "TAGMSG", "params": ["#nov"]}, CFG, self.d, self.a)
        self.assertEqual(self.d.items, [])

    def test_unaccepted_goes_reminder_then_dead_letter_and_push(self):
        ring = self.request()
        with mock.patch.object(bridge, "ring_manager", lambda cfg, item: (True, "delivered")), \
                mock.patch.object(bridge, "push_owner", lambda cfg, item: (False, "offline")):
            for n in range(1, bridge.MAX_REMINDERS + 1):
                ring["next_at"] = 0
                self.d.process(CFG)          # accept window expired: reminder
                self.assertEqual(ring["reminders"], n)
                self.d.process(CFG)          # re-delivered: back to waiting for acceptance
                self.assertEqual(ring["phase"], "accept")
            ring["next_at"] = 0
            self.d.process(CFG)
        self.assertNotIn(ring, self.d.items)
        self.assertTrue(any(i["kind"] == "push" for i in self.d.items))
        dead = (Path(STATE) / "dead-letters.jsonl").read_text()
        self.assertIn("never accepted", dead)

    def test_retarget_on_new_manager_surface(self):
        pm = Path(STATE) / "pm"
        (pm / "nov").mkdir(parents=True)
        (pm / "nov" / "manager.json").write_text(json.dumps({"surface_id": "NEW"}))
        sent = []

        def fake(*args):
            if args[:2] == ("agent", "message"):
                sent.append(args[2])
                return 0, json.dumps({"id": "m2", "state": "queued"}), ""
            return 0, "{}", ""
        item = {"kind": "ring", "msgid": "x", "channel": "#nov", "sender": "nov-solver-1", "text": "t",
                "project": "nov", "attempt": 1, "surface": "OLD", "agent_msg_id": "m1"}
        with mock.patch.object(bridge, "PM_STATE", pm), mock.patch.object(bridge, "_cmux", fake):
            bridge.ring_manager(CFG, item)
        self.assertEqual(sent, ["NEW"])
        self.assertEqual(item["retargeted_from"], "OLD")


class RoutingTests(unittest.TestCase):
    """Who gets woken: the manager hears posts that go up the chain; the owner
    and assistants reach a named agent directly, without the manager."""

    def setUp(self):
        reset_state()
        agents = {"nov-solver-1": {"status": "active", "surface_id": "S1", "brand": "claude", "project": "nov"},
                  "nov-solver-2": {"status": "active", "surface_id": "S2", "brand": "codex", "project": "nov"},
                  "nov-solver-3": {"status": "retired", "surface_id": "S3", "brand": "claude", "project": "nov"}}
        (Path(STATE) / "identities.json").write_text(json.dumps({"agents": agents}))
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()

    def route(self, sender, text, channel="#nov-41"):
        bridge.handle(privmsg(sender, text, f"m{len(self.d.items)}", channel=channel), CFG, self.r, self.d, self.a)
        return sorted((i["kind"], i.get("target", "manager" if i["kind"] == "ring" else "owner"))
                      for i in self.d.items)

    def test_unaddressed_worker_post_rings_manager(self):
        self.assertEqual(self.route("nov-solver-1", "[status] PR #45 opened"), [("ring", "manager")])

    def test_worker_asking_owner_pushes_and_rings_manager(self):
        self.assertEqual(self.route("nov-solver-1", "@pat which loader?"),
                         [("push", "owner"), ("ring", "manager")])

    def test_worker_leading_address_form_rings_manager(self):
        self.assertEqual(self.route("nov-solver-1", "pat: which loader?"),
                         [("push", "owner"), ("ring", "manager")])

    def test_worker_asking_assistant_rings_manager(self):
        self.assertEqual(self.route("nov-solver-1", "@sam is #44 still wanted?"), [("ring", "manager")])

    def test_worker_to_worker_rings_nobody(self):
        self.assertEqual(self.route("nov-solver-1", "@nov-solver-2 I rebased main"), [])

    def test_assistant_to_agent_bypasses_manager(self):
        self.assertEqual(self.route("sam", "@nov-solver-1 stop work on #45"), [("ring", "nov-solver-1")])
        self.assertFalse(self.d.items[0]["needs_ack"])

    def test_owner_to_two_agents_and_manager(self):
        self.assertEqual(self.route("pat", "@nov-solver-1 @nov-solver-2 @nov-manager hold"),
                         [("ring", "manager"), ("ring", "nov-solver-1"), ("ring", "nov-solver-2")])

    def test_owner_to_agent_without_live_session_falls_back_to_manager(self):
        self.assertEqual(self.route("pat", "@nov-solver-3 resume #33"), [("ring", "manager")])
        self.assertTrue(self.d.items[0]["needs_ack"])

    def test_owner_unaddressed_rings_manager_for_acceptance(self):
        self.assertEqual(self.route("pat", "fix #41 and merge"), [("ring", "manager")])
        self.assertTrue(self.d.items[0]["needs_ack"])

    def test_assistant_asking_owner_does_not_ring_manager(self):
        self.assertEqual(self.route("sam", "@pat decide #2778"), [("push", "owner")])

    def test_manager_reply_does_not_close_a_direct_delivery(self):
        self.route("pat", "@nov-solver-1 stop")
        bridge.handle(privmsg("nov-manager", "noted", "r1", reply_to="m0"), CFG, self.r, self.d, self.a)
        self.assertEqual([i.get("target") for i in self.d.items], ["nov-solver-1"])

    def test_direct_delivery_follows_a_moved_agent(self):
        self.route("sam", "@nov-solver-1 stop")
        sent = []

        def fake(*args):
            if args[:2] == ("agent", "message"):
                sent.append(args[2])
                return 0, json.dumps({"id": "a1", "state": "delivered"}), ""
            return 0, "{}", ""
        reg = json.loads((Path(STATE) / "identities.json").read_text())
        reg["agents"]["nov-solver-1"]["surface_id"] = "S9"
        (Path(STATE) / "identities.json").write_text(json.dumps(reg))
        with mock.patch.object(bridge, "_cmux", fake):
            self.d.process(CFG)
        self.assertEqual(sent, ["S9"])
        self.assertEqual(self.d.items, [])


class InterruptRoutingTests(RoutingTests):
    def item(self):
        return self.d.items[-1]

    def test_owner_double_bang_interrupts_directly(self):
        self.assertEqual(self.route("pat", "!! @nov-solver-1 hold #2699"), [("ring", "nov-solver-1")])
        self.assertTrue(self.item()["interrupt"] and self.item()["needs_ack"])

    def test_manager_may_interrupt_its_own_agent(self):
        self.assertEqual(self.route("nov-manager", "[interrupt r-20261002-1] @nov-solver-1 hold"),
                         [("ring", "nov-solver-1")])

    def test_manager_plain_mention_is_not_delivered(self):
        self.assertEqual(self.route("nov-manager", "@nov-solver-1 fyi"), [])

    def test_worker_cannot_interrupt(self):
        self.assertEqual(self.route("nov-solver-2", "!! @nov-solver-1 stop"), [])

    def test_only_the_interrupted_agent_can_acknowledge(self):
        self.route("pat", "!! @nov-solver-1 hold")
        ring = self.item()
        ring.update(phase="accept", next_at=time.time() + 600)
        bridge.handle(privmsg("nov-manager", "noted", "r1", reply_to=ring["msgid"]), CFG, self.r, self.d, self.a)
        self.assertIn(ring, self.d.items)
        bridge.handle(privmsg("nov-solver-1", "[status] holding", "r2", reply_to=ring["msgid"]),
                      CFG, self.r, self.d, self.a)
        self.assertNotIn(ring, self.d.items)

    def test_unacknowledged_interrupt_alerts_without_interrupting_again(self):
        self.route("pat", "!! @nov-solver-1 hold")
        ring = self.item()
        ring.update(phase="accept", next_at=0)
        with mock.patch.object(bridge, "ring_agent", lambda *a: self.fail("must not re-interrupt")):
            self.d.process(CFG)
        self.assertNotIn(ring, self.d.items)
        self.assertTrue(any("has not acknowledged" in i.get("text", "") for i in self.d.items))


class FakeAgent:
    """A Claude (vim mode) or Codex session as the live tests showed it: Esc
    leaves vim insert mode before it interrupts; a turn stopped before any
    output gives its prompt back as a draft; cmux's lifecycle stays "running"."""

    def __init__(self, brand="claude", running=True, mode="insert", restores=False, dialog=False,
                 background=False, browsing=False):
        self.brand, self.running, self.mode, self.restores, self.dialog = brand, running, mode, restores, dialog
        self.background, self.browsing = background, browsing
        self.draft, self.transcript, self.keys = "", ["❯ /delta:autosolve 2699" if brand == "claude" else "› task"], []

    def screen(self):
        if self.brand == "codex":
            box = self.draft or "Ask Codex to do anything"
            status = (["  2 background terminals running · /ps to view · /stop to close"]
                      if self.background else [])
            footer = (["  Browsing transcript · ↑↓/jk scroll · ←→/hl prompts · ctrl+t details · ↵ rewind · esc back"]
                      if self.browsing else [])
            return "\n".join(self.transcript + status + [f"› {box}", "", "  GPT high · ~/work"] + footer)
        footer = ("-- INSERT -- " if self.mode == "insert" else "") + "⏵⏵ bypass permissions on"
        if self.running and self.mode == "normal":
            footer += " · esc to interrupt"
        return "\n".join(self.transcript + ["─" * 20, f"❯ {self.draft}", "─" * 20, "  " + footer])

    def __call__(self, *args):
        if args[:2] == ("rpc", "surface.input_state"):
            state = "dialog" if self.dialog else ("draft" if self.draft else "empty")
            return 0, json.dumps({"surface_id": "S", "state": state, "lifecycle": "running",
                                  "waiting_on_human": self.dialog}), ""
        if args[0] == "read-screen":
            return 0, self.screen(), ""
        if args[0] == "send-key":
            key = args[-1]
            self.keys.append(key)
            if key == "escape":
                if self.brand == "claude" and self.mode == "insert":
                    self.mode = "normal"
                elif self.running:
                    self.running = False
                    if self.restores:
                        self.draft = self.transcript.pop()[2:]
                    else:
                        self.transcript.append("■ Conversation interrupted" if self.brand == "codex"
                                               else "  ⎿  Interrupted · What should Claude do instead?")
            elif key == "ctrl+c":
                self.draft = ""
            elif key == "enter" and self.draft:
                self.transcript.append(("❯ " if self.brand == "claude" else "› ") + self.draft)
                self.draft, self.running = "", True
            return 0, "", ""
        if args[0] == "send":
            text = args[-1]
            self.keys.append("type:" + text[:12])
            if self.brand == "claude" and self.mode == "normal":
                if text == "i":
                    self.mode = "insert"
                return 0, "", ""  # other keys are vim commands, not text
            self.draft += text
            return 0, "", ""
        return 0, "{}", ""


@mock.patch.object(bridge.time, "sleep", lambda s: None)
class InterruptKeystrokeTests(unittest.TestCase):
    def interrupt(self, agent):
        item = {"kind": "ring", "target": "alp-solver-4", "interrupt": True, "msgid": "m1",
                "channel": "#alp-x", "sender": "pat", "text": "!! @alp-solver-4 hold #2699"}
        with mock.patch.object(bridge, "_cmux", agent):
            ok, detail = bridge._interrupt(item, "S", agent.brand, "alp-solver-4")
        return ok, detail, item

    def test_running_claude_in_insert_mode_needs_two_escapes(self):
        agent = FakeAgent()
        ok, detail, item = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertEqual(agent.keys[:3], ["escape", "escape", "type:i"])
        self.assertIn("hold #2699", agent.transcript[-1])
        self.assertIn("stopped_at", item)

    def test_restored_prompt_is_cleared_before_typing(self):
        agent = FakeAgent(restores=True)
        ok, detail, _ = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertIn("ctrl+c", agent.keys)
        self.assertTrue(agent.transcript[-1].startswith("❯ [chat interrupt]"))

    def test_idle_claude_gets_no_escape(self):  # a second Esc on an idle prompt opens rewind
        agent = FakeAgent(running=False, mode="normal")
        ok, detail, item = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertNotIn("escape", agent.keys)
        self.assertNotIn("stopped_at", item)

    def test_dialog_is_held_untouched(self):
        agent = FakeAgent(dialog=True)
        ok, detail, _ = self.interrupt(agent)
        self.assertIs(ok, bridge.WAITING)
        self.assertTrue(detail.startswith("held"))
        self.assertEqual(agent.keys, [])

    def test_someones_draft_is_held_untouched(self):
        agent = FakeAgent()
        agent.draft = "half-typed by the owner"
        ok, detail, _ = self.interrupt(agent)
        self.assertIs(ok, bridge.WAITING)
        self.assertEqual((agent.keys, agent.draft), ([], "half-typed by the owner"))

    def test_running_codex_needs_one_escape(self):
        agent = FakeAgent(brand="codex")
        ok, detail, _ = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertEqual(agent.keys.count("escape"), 1)
        self.assertTrue(agent.transcript[-1].startswith("› [chat interrupt]"))

    def test_codex_stale_running_after_an_interrupt_is_idle(self):
        agent = FakeAgent(brand="codex")
        agent.running = False
        agent.transcript.append("■ Conversation interrupted")
        ok, detail, _ = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertNotIn("escape", agent.keys)

    def test_codex_interrupted_with_background_terminals_gets_no_escape(self):
        agent = FakeAgent(brand="codex", running=False, background=True)
        agent.transcript.append("■ Conversation interrupted - use /feedback if something went wrong")
        ok, detail, item = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertNotIn("escape", agent.keys)
        self.assertNotIn("stopped_at", item)
        self.assertTrue(agent.background)
        self.assertTrue(agent.transcript[-1].startswith("› [chat interrupt]"))

    def test_running_codex_with_background_terminals_gets_one_escape(self):
        agent = FakeAgent(brand="codex", background=True)
        ok, detail, item = self.interrupt(agent)
        self.assertTrue(ok, detail)
        self.assertEqual(agent.keys.count("escape"), 1)
        self.assertIn("stopped_at", item)
        self.assertTrue(agent.background)

    def test_codex_transcript_browser_is_held_without_keys(self):
        agent = FakeAgent(brand="codex", running=False, background=True, browsing=True)
        ok, detail, _ = self.interrupt(agent)
        self.assertIs(ok, bridge.WAITING)
        self.assertIn("browsing", detail)
        self.assertEqual(agent.keys, [])

    def test_plain_codex_delivery_holds_transcript_browser_without_keys(self):
        agent = FakeAgent(brand="codex", running=False, background=True, browsing=True)
        item = {"msgid": "m1", "channel": "#alp-x", "sender": "pat", "text": "read the existing handoff"}
        with mock.patch.object(bridge, "_cmux", agent):
            ok, detail = bridge._ring_surface({}, item, "S", "codex", "alp-solver-4")
        self.assertIs(ok, bridge.WAITING)
        self.assertIn("browsing", detail)
        self.assertEqual(agent.keys, [])


class CodexInterruptedScreenTests(unittest.TestCase):
    def screen(self, events, status="2 background terminals running · /ps to view · /stop to close"):
        return "\n".join(["› task", *events, "", status, "", "› Ask Codex to do anything", "", "GPT high"])

    def test_captured_interrupt_with_background_status(self):
        screen = (Path(__file__).parent / "fixtures" / "codex-interrupted-background.txt").read_text()
        self.assertTrue(bridge._codex_interrupted(screen))

    def test_one_background_terminal(self):
        self.assertTrue(bridge._codex_interrupted(self.screen(
            ["■ Conversation interrupted"], "1 background terminal running · /ps to view · /stop to close")))

    def test_old_interrupt_followed_by_new_output_is_not_idle(self):
        self.assertFalse(bridge._codex_interrupted(self.screen(
            ["■ Conversation interrupted", "• Working on the next request"])))

    def test_unknown_status_is_not_ignored(self):
        self.assertFalse(bridge._codex_interrupted(self.screen(
            ["■ Conversation interrupted"], "2 background tasks require permission")))

    def test_background_status_without_interrupt_is_not_idle(self):
        self.assertFalse(bridge._codex_interrupted(self.screen(["• Working"])))

    def test_browsing_old_interruption_is_not_idle_composer(self):
        screen = self.screen(["■ Conversation interrupted"]) + "\nBrowsing transcript · ↵ rewind · esc back"
        self.assertFalse(bridge._codex_interrupted(screen))


class CodexDeliveryTests(unittest.TestCase):
    """cmux agent messages never reach Codex, so a direct post is typed into an
    idle, empty Codex composer."""
    SCREEN = "  Worked for 6m 6s\n \n› {box}\n \n  GPT-6.1-Sol high · ~/work/nov\n  ? for shortcuts"

    def item(self):
        return {"kind": "ring", "target": "nov-solver-2", "msgid": "m1", "channel": "#nov-41",
                "sender": "sam", "text": "stop work on #45", "project": "nov", "attempt": 1}

    def fake(self, lifecycle="idle"):
        st = {"draft": "", "lifecycle": lifecycle, "above": "", "calls": []}

        def run(*args):
            st["calls"].append(args[0])
            if args[:2] == ("rpc", "surface.input_state"):
                return 0, json.dumps({"surface_id": "S2", "state": "draft" if st["draft"] else "empty",
                                      "lifecycle": st["lifecycle"]}), ""
            if args[0] == "send":
                st["draft"] = args[-1]
            elif args[0] == "send-key" and args[-1] == "enter":
                st["above"], st["draft"], st["lifecycle"] = "› " + st["draft"], "", "running"
            elif args[0] == "read-screen":
                box = st["draft"] or "Ask Codex to do anything"
                return 0, st["above"] + "\n" + self.SCREEN.format(box=box), ""
            return 0, "{}", ""
        return st, run

    @mock.patch.object(bridge.time, "sleep", lambda s: None)
    def test_typed_into_idle_codex(self):
        st, run = self.fake()
        with mock.patch.object(bridge, "agent_surface", lambda n: ("S2", "codex")), \
                mock.patch.object(bridge, "_cmux", run):
            ok, detail = bridge.ring_agent(CFG, self.item())
        self.assertTrue(ok, detail)
        self.assertNotIn("agent", st["calls"])

    @mock.patch.object(bridge.time, "sleep", lambda s: None)
    def test_busy_codex_gets_it_after_its_next_tool_call(self):
        # Codex takes a prompt submitted while it works "after next tool call", without stopping.
        st, run = self.fake(lifecycle="running")
        with mock.patch.object(bridge, "agent_surface", lambda n: ("S2", "codex")), \
                mock.patch.object(bridge, "_cmux", run):
            ok, detail = bridge.ring_agent(CFG, self.item())
        self.assertTrue(ok, detail)
        self.assertNotIn("escape", [c for c in st["calls"]])

    def test_a_draft_in_the_codex_prompt_waits(self):
        st, run = self.fake()
        st["draft"] = "someone typing"
        with mock.patch.object(bridge, "agent_surface", lambda n: ("S2", "codex")), \
                mock.patch.object(bridge, "_cmux", run):
            ok, detail = bridge.ring_agent(CFG, self.item())
        self.assertIs(ok, bridge.WAITING)
        self.assertNotIn("send", st["calls"])


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_cursor_never_moves_backward(self):
        log = chatlib.channel_log("#nov")
        rows = [("new", "2026-10-02T02:00:00Z"), ("old", "2026-10-02T01:00:00Z")]  # live, then replayed
        log.write_text("".join(json.dumps({"msgid": m, "at": t}) + "\n" for m, t in rows))
        self.assertEqual(bridge.Record.cursor("#nov"), "msgid=new")

    def test_full_page_requests_the_next(self):
        pager = bridge.HistoryPager(page=3)
        pager.start("b1", "#nov")
        for i in range(3):
            pager.item("b1", {"tags": {"msgid": f"m{i}"}})
        self.assertEqual(pager.end("b1"), "CHATHISTORY AFTER #nov msgid=m2 3")
        pager.start("b2", "#nov")
        pager.item("b2", {"tags": {"msgid": "m3"}})
        self.assertIsNone(pager.end("b2"))


class FakeServer:
    """One scripted connection: yields its lines, then drops."""

    def __init__(self, script):
        self.script, self.sent = script, []

    def send(self, line):
        self.sent.append(line)

    def lines(self, timeout=None):
        yield from self.script
        raise chatlib.ChatError("connection dropped")


def join(channel="#nov"):
    return {"tags": {}, "prefix": "chatbridge!u@h", "command": "JOIN", "params": [channel]}


def batch(ref, channel="#nov"):
    return {"tags": {}, "prefix": "irc", "command": "BATCH",
            "params": [f"+{ref}", "chathistory", channel] if channel else [f"-{ref}"]}


def in_batch(ref, msg):
    msg["tags"]["batch"] = ref
    return msg


class CatchUpRecoveryTests(unittest.TestCase):
    """A catch-up cut short must resume where completeness ends."""

    def setUp(self):
        reset_state()
        cfgfile = Path(STATE) / "config.json"
        cfgfile.write_text("{}")
        orig = bridge.HistoryPager
        self.authority = in_process_authority()  # #19: a live message is indexed before its checkpoint moves
        self.patches = [mock.patch.object(chatlib, "CONFIG_PATH", cfgfile),
                        mock.patch.object(bridge, "HISTORY_PAGE", 2),
                        mock.patch.object(bridge, "HistoryPager", lambda: orig(page=2)),
                        mock.patch.object(bridge, "OUTBOX_AUTHORITY", self.authority.__enter__())]
        for p in self.patches:
            p.start()
        self.state = (bridge.Record(), bridge.Deliveries(), bridge.Acks(), bridge.Checkpoints())
        bridge.handle(privmsg("pat", "before the outage", "m0", t="2026-10-02T09:00:00Z"), CFG, *self.state[:3])

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.authority.__exit__(None, None, None)

    def connect(self, script):
        server = FakeServer(script)
        with mock.patch.object(chatlib, "login", lambda *a, **k: server):
            with self.assertRaises(chatlib.ChatError):
                bridge.run(CFG, *self.state)
        return [line for line in server.sent if line.startswith("CHATHISTORY")]

    def test_interrupted_catch_up_resumes_from_checkpoint(self):
        sent = self.connect([join(), batch("b1"),
                             in_batch("b1", privmsg("pat", "gap 1", "m1", t="2026-10-02T10:00:00Z")),
                             in_batch("b1", privmsg("pat", "gap 2", "m2", t="2026-10-02T10:30:00Z")),
                             privmsg("pat", "live during catch-up", "m9", t="2026-10-02T12:05:00Z"),
                             batch("b1", None)])  # page full: asks for more, then the connection drops
        self.assertEqual(sent, ["CHATHISTORY AFTER #nov msgid=m0 2", "CHATHISTORY AFTER #nov msgid=m2 2"])
        self.assertEqual(self.connect([join()]), ["CHATHISTORY AFTER #nov msgid=m0 2"])

    def test_finished_catch_up_then_live_messages_advance_checkpoint(self):
        self.connect([join(), batch("b1"),
                      in_batch("b1", privmsg("pat", "gap 1", "m1", t="2026-10-02T10:00:00Z")),
                      privmsg("pat", "live", "m9", t="2026-10-02T12:05:00Z"),
                      batch("b1", None)])   # short page: caught up, through m9
        self.assertEqual(self.connect([join()]), ["CHATHISTORY AFTER #nov msgid=m9 2"])
        self.connect([join(), batch("b2"), batch("b2", None),
                      privmsg("pat", "later", "m10", t="2026-10-02T12:10:00Z")])
        self.assertEqual(self.connect([join()]), ["CHATHISTORY AFTER #nov msgid=m10 2"])


def multiline(ref, sender, lines, msgid, channel="#nov", parent=None, reply_to=None):
    """A draft/multiline batch as Ergo relays it: tags on the opening line only."""
    tags = {"account": sender, "msgid": msgid, "time": "2026-10-02T12:00:00.000Z"}
    if parent:
        tags["batch"] = parent
    if reply_to:
        tags["+draft/reply"] = reply_to
    out = [{"tags": tags, "prefix": f"{sender}!u@h", "command": "BATCH",
            "params": [f"+{ref}", "draft/multiline", channel]}]
    for text, concat in lines:
        t = {"batch": ref, **({"draft/multiline-concat": ""} if concat else {})}
        out.append({"tags": t, "prefix": f"{sender}!u@h", "command": "PRIVMSG", "params": [channel, text]})
    out.append({"tags": {"batch": parent} if parent else {}, "prefix": "irc", "command": "BATCH",
                "params": [f"-{ref}"]})
    return out


class MultilineTests(CatchUpRecoveryTests):
    """One post, however long, is one logged message with one wake-up."""
    LINES = [("[question r-20261002-1] which loader?", False), (" It crashes on GLB.", True),
             ("Second paragraph.", False)]
    TEXT = "[question r-20261002-1] which loader? It crashes on GLB.\nSecond paragraph."

    def setUp(self):
        super().setUp()
        self.d = self.state[1]
        self.d.items.clear()

    def logged(self, channel="#nov"):
        return [json.loads(line) for line in chatlib.channel_log(channel).read_text().splitlines()]

    def test_live_batch_is_one_message_and_one_ring(self):
        self.connect([join("#nov-41")] + multiline("ml", "sam", self.LINES, "post1", channel="#nov-41"))
        [entry] = self.logged("#nov-41")
        self.assertEqual((entry["msgid"], entry["text"], entry["verified"]), ("post1", self.TEXT, True))
        rings = [i for i in self.d.items if i["kind"] == "ring"]
        self.assertEqual([(r["msgid"], r["needs_ack"]) for r in rings], [("post1", True)])

    def test_batch_replayed_inside_history_is_one_message(self):
        sent = self.connect([join(), batch("h1")]
                            + multiline("ml", "sam", self.LINES, "post2", parent="h1")
                            + [batch("h1", None)])
        entry = self.logged()[-1]
        self.assertEqual((entry["msgid"], entry["text"], entry.get("replayed")), ("post2", self.TEXT, True))
        self.assertEqual(sent, ["CHATHISTORY AFTER #nov msgid=m0 2"])  # one item: no second page

    def test_manager_reply_as_a_batch_accepts(self):
        bridge.handle(privmsg("pat", "fix #41", "req1"), CFG, *self.state[:3])
        self.d.items[0].update(phase="accept", next_at=time.time() + 900)
        self.connect([join()] + multiline("ml", "nov-manager", [("on it", False), ("plan: …", False)],
                                          "rep1", reply_to="req1"))
        self.assertEqual([i for i in self.d.items if i["msgid"] == "req1"], [])


class AcceptanceByTypeTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()

    def needs_ack(self, sender, text):
        bridge.handle(privmsg(sender, text, f"m{len(self.d.items)}"), CFG, self.r, self.d, self.a)
        return self.d.items[-1]["needs_ack"]

    def test_status_and_done_need_no_acceptance(self):
        self.assertFalse(self.needs_ack("sam", "[status r-20261002-1] sam: drainer incident cleared"))
        self.assertFalse(self.needs_ack("sam", "[done r-20261002-1] sam: janitor ran"))

    def test_actionable_types_and_untyped_need_acceptance(self):
        for text in ("[request r-20261002-1] fix #41", "[question r-20261002-1] merge #2778?",
                     "[answer r-20261002-1] yes", "[decision r-20261002-1] ship it", "fix #41 please"):
            self.assertTrue(self.needs_ack("sam", text), text)


CFG2 = {**CFG, "accounts": {**CFG["accounts"], "alp-manager": "x", "alp-solver-1": "x"},
        "projects": {**CFG["projects"], "alp": {"channel": "#alp", "prefix": "#alp-", "manager": "alp-manager"}}}


class DelegationTests(unittest.TestCase):
    """A manager delegating to another project cites the owner's post; the
    bridge checks the citation against the record."""

    def setUp(self):
        reset_state()
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()
        for sender, mid in (("pat", "orig1"), ("nov-solver-1", "work1")):
            bridge.handle(privmsg(sender, "fix the shared loader in alpha too, through merge", mid,
                                  channel="#nov-41"), CFG2, self.r, self.d, self.a)
        self.d.items.clear()

    def delegate(self, sender="nov-manager", cite="orig1"):
        text = f"[request nov-20261002-7] port the loader fix (endpoint: merged); report in #nov-41 — authority: msgid={cite}"
        bridge.handle(privmsg(sender, text, "dlg1", channel="#alp"), CFG2, self.r, self.d, self.a)
        return next(i for i in self.d.items if i["project"] == "alp")

    def test_verified_delegation_needs_acceptance_and_quotes_the_original(self):
        ring = self.delegate()
        self.assertTrue(ring["needs_ack"])
        self.assertIn('authority verified: <pat>', ring["authority"])
        self.assertIn("fix the shared loader in alpha", bridge._wake_text(ring))

    def test_citing_a_workers_post_is_not_authority(self):
        ring = self.delegate(cite="work1")
        self.assertFalse(ring["needs_ack"])
        self.assertIn("NOT verified", ring["authority"])
        self.assertIn("nov-solver-1", ring["authority"])

    def test_citing_a_missing_post_is_not_authority(self):
        ring = self.delegate(cite="nosuch")
        self.assertIn("no post with msgid nosuch", ring["authority"])

    def test_a_worker_citing_authority_gets_no_check(self):
        ring = self.delegate(sender="nov-solver-1")
        self.assertNotIn("authority", ring)
        self.assertFalse(ring["needs_ack"])

    def test_receiving_manager_accepts_it(self):
        self.delegate()
        bridge.handle(privmsg("alp-manager", "on it", "acc1", channel="#alp-loader", reply_to="dlg1"),
                      CFG2, self.r, self.d, self.a)
        self.assertEqual([i for i in self.d.items if i["msgid"] == "dlg1"], [])

    def pchat(self, *argv):
        loader = importlib.machinery.SourceFileLoader("pchat_cli", str(SCRIPTS / "pchat"))
        spec = importlib.util.spec_from_loader("pchat_cli", loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        posted = []
        with mock.patch.object(chatlib, "load_config", lambda: CFG2), \
                mock.patch.object(chatlib, "post", lambda ch, text, *a, **k: posted.append(text) or 1), \
                mock.patch.dict(os.environ, {"CMUX_SURFACE_ID": "", "CHAT_AGENT_ID": ""}):
            code = cli.main(["post", "#alp", "--as", "nov-manager", *argv])
        return code, posted

    def test_pchat_appends_a_checked_citation(self):
        code, posted = self.pchat("--authority", "orig1", "port the loader fix")
        self.assertEqual((code, posted), (0, ["port the loader fix — authority: msgid=orig1"]))

    def test_pchat_refuses_a_bad_citation(self):
        code, posted = self.pchat("--authority", "work1", "port the loader fix")
        self.assertEqual((code, posted), (2, []))


class CrashReplayTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()

    def test_reply_logged_before_crash_is_accepted_on_replay(self):
        bridge.handle(privmsg("pat", "fix #41", "req1"), CFG, self.r, self.d, self.a)
        self.d.items[0].update(phase="accept", next_at=time.time() + 900)
        reply = privmsg("nov-manager", "on it", "rep1", reply_to="req1")
        self.r.append({"at": "t", "msgid": "rep1", "channel": "#nov", "from": "nov-manager",
                       "reply_to": "req1"})  # logged; the crash came before acceptance
        with mock.patch.object(bridge, "_cmux", lambda *a: (0, "{}", "")):
            bridge.handle(reply, CFG, self.r, self.d, self.a, replayed=True)
        self.assertEqual(self.d.items, [])


class WaitingDeliveryTests(unittest.TestCase):
    """cmux holding a message is waiting, not failing; every dead letter alerts."""

    def setUp(self):
        reset_state()
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()
        self.pushes = []

    def post(self, sender="pat"):
        bridge.handle(privmsg(sender, "fix #41", "req1"), CFG, self.r, self.d, self.a)
        return next(i for i in self.d.items if i["kind"] == "ring")

    def run_until(self, outcome, *, elapsed=0, rounds=20):
        def push(cfg, item):
            self.pushes.append(item["text"])
            return True, "pushed"
        with mock.patch.object(bridge, "ring_manager", lambda cfg, item: outcome), \
                mock.patch.object(bridge, "push_owner", push):
            for _ in range(rounds):
                for i in self.d.items:
                    i["next_at"] = 0
                    if "waiting_since" in i:
                        i["waiting_since"] = time.time() - elapsed
                self.d.process(CFG)

    def dead(self):
        path = Path(STATE) / "dead-letters.jsonl"
        return path.read_text().splitlines() if path.exists() else []

    def test_queued_is_followed_not_dead_lettered(self):
        ring = self.post()
        self.run_until((bridge.WAITING, "queued at manager AAAA"))
        self.assertIn(ring, self.d.items)
        self.assertEqual((ring["attempt"], self.dead(), self.pushes), (0, [], []))

    def test_owner_post_queued_30_min_alerts_once(self):
        self.post()
        self.run_until((bridge.WAITING, "queued at manager AAAA"), elapsed=bridge.STUCK_ALERT + 1)
        self.assertEqual(len(self.pushes), 1)
        self.assertIn("has not taken a post from pat", self.pushes[0])

    def test_worker_post_queued_30_min_does_not_alert(self):
        self.post(sender="nov-solver-1")
        self.run_until((bridge.WAITING, "queued at manager AAAA"), elapsed=bridge.STUCK_ALERT + 1)
        self.assertEqual(self.pushes, [])

    def test_waiting_a_day_dead_letters_and_alerts(self):
        self.post(sender="nov-solver-1")
        self.run_until((bridge.WAITING, "queued at manager AAAA"), elapsed=bridge.MAX_WAIT + 1)
        self.assertEqual(len(self.dead()), 1)
        self.assertTrue(any("still waiting after 24 h" in p for p in self.pushes), self.pushes)

    def test_failed_delivery_dead_letters_and_alerts_whoever_sent_it(self):
        self.post(sender="nov-solver-1")
        self.run_until((False, "no manager record for nov"))
        self.assertEqual(len(self.dead()), 1)
        self.assertEqual(self.pushes, ["delivery to the nov manager failed: no manager record for nov"])

    def test_dead_lettered_push_does_not_alert_again(self):
        self.d.add_all([{"kind": "push", "msgid": "x", "channel": "#nov", "sender": "pat", "text": "t",
                         "project": None, "attempt": 0, "next_at": 0}])
        with mock.patch.object(bridge, "push_owner", lambda cfg, item: (False, "offline")):
            for _ in range(10):
                for i in self.d.items:
                    i["next_at"] = 0
                self.d.process(CFG)
        self.assertEqual((self.d.items, len(self.dead())), ([], 1))

    def test_message_gone_from_inbox_is_a_failure_and_resent(self):
        pm = Path(STATE) / "pm"
        (pm / "nov").mkdir(parents=True, exist_ok=True)
        (pm / "nov" / "manager.json").write_text(json.dumps({"surface_id": "S"}))
        calls = []

        def fake(*args):
            calls.append(args)
            return 0, json.dumps({"messages": []}), ""
        item = {"kind": "ring", "msgid": "x", "channel": "#nov", "sender": "pat", "text": "t",
                "project": "nov", "attempt": 1, "surface": "S", "agent_msg_id": "m1"}
        with mock.patch.object(bridge, "PM_STATE", pm), mock.patch.object(bridge, "_cmux", fake):
            ok, detail = bridge.ring_manager(CFG, item)
        self.assertIs(ok, False)
        self.assertNotIn("agent_msg_id", item)
        self.assertIn("--limit", calls[0])  # not just the newest 50


class SendMultilineTests(unittest.TestCase):
    """pchat's side: a post goes as one batch that the bridge rejoins exactly."""

    def roundtrip(self, batch):
        opener = {"tags": {"msgid": "x"}, "prefix": "a!u@h", "params": ["+b", "draft/multiline", "#nov"]}
        lines = [{"tags": {"draft/multiline-concat": ""} if concat else {}, "params": ["#nov", piece]}
                 for piece, concat in batch]
        return bridge.join_multiline(opener, lines)["params"][1]

    def test_long_multi_line_post_is_one_batch_that_rejoins_exactly(self):
        text = "[request r-20261002-1] " + "word " * 150 + "end\n\nsecond paragraph"
        [batch] = chatlib.multiline_batches(text, "[request r-20261002-1]")
        self.assertTrue(all(len(p.encode()) <= chatlib.MAX_TEXT for p, _ in batch))
        self.assertEqual(self.roundtrip(batch), text)

    def test_over_the_limit_continues_in_a_tagged_second_message(self):
        text = "\n".join(f"line {i} " + "x" * 300 for i in range(20))
        batches = chatlib.multiline_batches(text, "[status r-20261002-1]", max_bytes=4096)
        self.assertEqual(len(batches), 2)
        self.assertTrue(all(sum(len(p.encode()) + 1 for p, _ in b) <= 4096 for b in batches))
        self.assertTrue(batches[1][0][0].startswith("… [status r-20261002-1] line"))
        self.assertFalse(batches[1][0][1], "a batch can't open with a continuation")

    def post_with(self, replies=()):
        sent = []

        class Conn:
            caps, cap_values = {"draft/multiline"}, {"draft/multiline": "max-bytes=4096,max-lines=100"}

            def send(self, line):
                sent.append(line)

            def lines(self, timeout=None):
                yield from ({"command": "FAIL", "params": list(r)} for r in replies)
                yield {"command": "PONG", "params": ["round"]}

            def close(self, reason=""):
                pass
        with mock.patch.object(chatlib, "login", lambda *a, **k: Conn()), in_process_authority():
            chatlib.post("#nov-41", "[answer r-20261002-1] yes\nsee #44", "sam", reply_to="m1")
        return sent

    def test_post_sends_one_batch_with_the_reply_on_its_opening_line(self):
        sent = self.post_with()
        self.assertEqual(sent[:4], ["@+draft/reply=m1 BATCH +p0 draft/multiline #nov-41",
                                    "@batch=p0 PRIVMSG #nov-41 :[answer r-20261002-1] yes",
                                    "@batch=p0 PRIVMSG #nov-41 :see #44", "BATCH -p0"])

    def test_refusal_is_not_queued(self):
        with self.assertRaises(chatlib.Refused):
            self.post_with(replies=[("BATCH", "MULTILINE_MAX_BYTES", "4096", "too long")])


class LogRotationTests(unittest.TestCase):
    """Under launchd, stdout is a handle opened on the log once; the bridge must
    write and rotate its own file instead, so both copies stay bounded."""

    def test_rotation_stays_bounded_with_an_inherited_stdout(self):
        reset_state()
        log = Path(STATE) / "bridge.log"
        held = open(Path(STATE) / "bridge.stderr.log", "a")  # what launchd would hold
        with mock.patch.object(bridge, "BRIDGE_LOG", log), mock.patch.object(bridge, "LOG_LIMIT", 2000), \
                mock.patch.object(sys, "stdout", held):
            for i in range(500):
                bridge.log(f"line {i} " + "x" * 40)
        held.close()
        self.assertTrue(log.exists())
        self.assertLessEqual(log.stat().st_size, 2000 + 100)
        self.assertLessEqual(log.with_suffix(".log.1").stat().st_size, 2000 + 100)
        self.assertIn("line 499", log.read_text())
        self.assertEqual((Path(STATE) / "bridge.stderr.log").stat().st_size, 0)


class EvidenceTests(unittest.TestCase):
    """A post citing a /tmp file keeps a copy, so the reference outlives a reboot."""

    def setUp(self):
        reset_state()
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()

    def kept(self):
        path = Path(STATE) / "evidence.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def post(self, text, msgid="e1"):
        bridge.handle(privmsg("zet-manager", text, msgid, channel="#zet-x"), CFG, self.r, self.d, self.a)

    def test_a_cited_file_is_kept_and_mapped(self):
        f = self.tmp / "issue2-body-v4.md"
        f.write_text("signed-off draft\n")
        self.post(f"[status r-1] signed off draft v4 (snapshot: {f}). Scope is the core.")
        [rec] = self.kept()
        self.assertEqual((rec["cited"], rec["msgid"]), (str(f), "e1"))
        self.assertEqual(Path(rec["stored"]).read_text(), "signed-off draft\n")
        f.unlink()  # the reboot
        self.assertTrue(Path(rec["stored"]).exists())

    def test_the_same_content_is_stored_once(self):
        f = self.tmp / "verdict.json"
        f.write_text("{}")
        self.post(f"see {f}", "e1")
        self.post(f"again {f}.", "e2")
        stored = {r["stored"] for r in self.kept()}
        self.assertEqual(len(stored), 1)
        self.assertEqual(len(self.kept()), 2)

    def test_missing_files_directories_and_oversize(self):
        big = self.tmp / "capture.bin"
        big.write_bytes(b"x" * 50)
        with mock.patch.object(bridge, "EVIDENCE_MAX", 10):
            self.post(f"gone: /tmp/no-such-file-xyz.md; dir: {self.tmp}; big: {big}")
        [rec] = self.kept()
        self.assertEqual((rec["cited"], rec["stored"]), (str(big), None))


class SplitTests(unittest.TestCase):
    def test_every_fragment_carries_the_tag(self):
        lines = chatlib.split_text("[done r-20261002-1] first\nsecond line\n" + "word " * 200,
                                   "[done r-20261002-1]")
        self.assertTrue(all("r-20261002-1" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
