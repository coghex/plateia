"""Message-ID deduplication after acceptance recovery: a reply logged just
before a crash still accepts its request when it is replayed, and neither it
nor the request it accepted is logged twice or wakes anyone again, within one
bridge run or after a restart. Plateia's own test of the captured bridge;
invented record only."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (first: sandbox home and live-state guard)
from test_bridge import CFG, bridge, privmsg, reset_state


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


if __name__ == "__main__":
    unittest.main()
