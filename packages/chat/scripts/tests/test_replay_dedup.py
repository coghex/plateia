"""Message-ID deduplication after acceptance recovery: a reply logged just
before a crash still accepts its request when it is replayed, and neither it
nor the request it accepted is logged twice or wakes anyone again, within one
bridge run or after a restart, including a restart after a catch-up longer
than 5,000 messages. Plateia's own test of the captured bridge; invented
record only."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (first: sandbox home and live-state guard)
from test_bridge import CFG, STATE, FakeServer, batch, bridge, chatlib, in_batch, join, privmsg, reset_state


def logged(msgid, channel="#nov"):
    path = bridge.chatlib.channel_log(channel)
    lines = path.read_text().splitlines() if path.exists() else []
    return [entry for entry in map(json.loads, lines) if entry.get("msgid") == msgid]


class ReplayDedupTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.d, self.r, self.a = bridge.Deliveries(), bridge.Record(), bridge.Acks()
        rings = mock.patch.object(bridge, "_cmux", lambda *a: (0, "{}", ""))
        rings.start()
        self.addCleanup(rings.stop)

    def request(self):
        bridge.handle(privmsg("pat", "fix #41", "req1"), CFG, self.r, self.d, self.a)
        self.assertEqual([i["msgid"] for i in self.d.items], ["req1"])
        self.d.items[0].update(phase="accept", next_at=time.time() + 900)

    def test_a_reply_logged_before_a_crash_accepts_once_and_is_not_logged_again(self):
        self.request()
        reply = privmsg("nov-manager", "on it", "rep1", reply_to="req1")
        self.r.append({"at": "t", "msgid": "rep1", "channel": "#nov", "from": "nov-manager",
                       "reply_to": "req1"})  # logged; the crash came before acceptance
        for _ in range(2):
            bridge.handle(reply, CFG, self.r, self.d, self.a, replayed=True)
            self.assertEqual(self.d.items, [])
            self.assertEqual(len(logged("rep1")), 1)

    def test_after_a_restart_replayed_history_is_deduplicated_by_msgid(self):
        self.request()
        bridge.handle(privmsg("nov-manager", "on it", "rep1", reply_to="req1"), CFG, self.r, self.d, self.a)
        self.assertEqual(self.d.items, [])
        # a fresh bridge process reads the record from disk and replays the same history
        d, r, a = bridge.Deliveries(), bridge.Record(), bridge.Acks()
        for msg in (privmsg("pat", "fix #41", "req1"), privmsg("nov-manager", "on it", "rep1", reply_to="req1")):
            bridge.handle(msg, CFG, r, d, a, replayed=True)
        self.assertEqual(d.items, [])
        self.assertEqual((len(logged("req1")), len(logged("rep1"))), (1, 1))
        self.assertTrue(Path(bridge.chatlib.channel_log("#nov")).exists())

    def test_a_new_msgid_is_still_handled(self):
        self.request()
        bridge.handle(privmsg("pat", "and #42", "req2"), CFG, self.r, self.d, self.a)
        self.assertEqual([i["msgid"] for i in self.d.items], ["req1", "req2"])
        self.assertEqual(len(logged("req2")), 1)


def unverified(text, msgid, t):
    """A history line from an unauthenticated nick: logged, never wakes anyone."""
    return {"tags": {"msgid": msgid, "time": t}, "prefix": "visitor!u@h", "command": "PRIVMSG",
            "params": ["#nov", text]}


class LongCatchUpRestartTests(unittest.TestCase):
    """A catch-up cut short leaves the checkpoint where it was, so after a
    restart everything since then is replayed; deduplication has to cover all
    of it, not only the newest 5,000 logged lines."""
    PAGES = 6

    def setUp(self):
        reset_state()
        cfgfile = Path(STATE) / "config.json"
        cfgfile.write_text("{}")
        for patch in (mock.patch.object(chatlib, "CONFIG_PATH", cfgfile),
                      mock.patch.object(bridge, "_cmux", lambda *a: (0, "{}", ""))):
            patch.start()
            self.addCleanup(patch.stop)
        self.start()
        bridge.handle(privmsg("pat", "before the outage", "m0", t="2026-10-02T09:00:00Z"), CFG, *self.state[:3])

    def start(self):
        """A fresh bridge process: every piece of state is read back from disk."""
        self.state = (bridge.Record(), bridge.Deliveries(), bridge.Acks(), bridge.Checkpoints())

    def history(self):
        """PAGES full pages since m0: the owner's request and the manager's
        accepting reply first, then filler."""
        lines = [privmsg("pat", "fix #41", "req1", t="2026-10-02T10:00:00Z"),
                 privmsg("nov-manager", "on it", "rep1", reply_to="req1", t="2026-10-02T10:00:01Z")]
        lines += [unverified(f"note {n}", f"h{n}", "2026-10-02T10:01:00Z")
                  for n in range(self.PAGES * bridge.HISTORY_PAGE - len(lines))]
        script = [join()]
        for page in range(self.PAGES):
            chunk = lines[page * bridge.HISTORY_PAGE:(page + 1) * bridge.HISTORY_PAGE]
            script += [batch(f"p{page}")] + [in_batch(f"p{page}", m) for m in chunk] + [batch(f"p{page}", None)]
        return script  # every page full, then the connection drops mid catch-up

    def connect(self, script):
        server = FakeServer(script)
        with mock.patch.object(chatlib, "login", lambda *a, **k: server):
            with self.assertRaises(chatlib.ChatError):
                bridge.run(CFG, *self.state)

    def test_a_restart_after_a_long_cut_short_catch_up_logs_and_wakes_nothing_twice(self):
        self.connect(self.history())
        self.assertEqual([i["msgid"] for i in self.state[1].items if i["msgid"] == "req1"], [])
        self.assertEqual(self.state[3].get("#nov"), "msgid=m0")  # unfinished: the checkpoint stays
        self.start()
        self.connect(self.history())
        self.assertEqual((len(logged("req1")), len(logged("rep1")), len(logged("h0"))), (1, 1, 1))
        self.assertEqual([i["msgid"] for i in self.state[1].items if i["msgid"] in ("req1", "rep1")], [])


if __name__ == "__main__":
    unittest.main()
