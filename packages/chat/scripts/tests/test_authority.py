"""Cited delegation: `pchat post --authority MSGID` is accepted only when the
cited post is in the record, verified, and from the owner or an assistant, and
a refused citation posts nothing. Plateia's own test; invented record only."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import chatlib  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("pchat_cli", str(SCRIPTS / "pchat"))
_spec = importlib.util.spec_from_loader("pchat_cli", _loader)
pchat = importlib.util.module_from_spec(_spec)
_loader.exec_module(pchat)
_isolation.check_bound(chatlib, pchat)

CFG = {"owner": "pat", "assistants": ["sam"],
       "accounts": {"pat": "x", "sam": "x", "alp-manager": "x", "bet-manager": "x"}}
RECORD = [
    {"msgid": "m-owner", "from": "pat", "verified": True, "channel": "#alpha", "text": "go ahead"},
    {"msgid": "m-assistant", "from": "sam", "verified": True, "channel": "#alpha", "text": "approved"},
    {"msgid": "m-unverified", "from": "pat", "verified": False, "channel": "#alpha", "text": "go ahead"},
    {"msgid": "m-manager", "from": "alp-manager", "verified": True, "channel": "#alpha", "text": "do it"},
]


class AuthorityCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=_isolation.HOME)
        self.addCleanup(tmp.cleanup)
        logs = Path(tmp.name) / "logs"
        logs.mkdir()
        (logs / "alpha.jsonl").write_text("not json m-owner\n" + "".join(json.dumps(e) + "\n" for e in RECORD))
        self.posts = []
        patches = [mock.patch.object(chatlib, "LOG_DIR", logs),
                   mock.patch.object(chatlib, "load_config", lambda: json.loads(json.dumps(CFG))),
                   mock.patch.object(chatlib, "post", self.post),
                   mock.patch.object(chatlib.delivery_store, "publish_fallback", self.fail_queue),
                   mock.patch.object(chatlib, "client_factory", self.fail_queue),
                   mock.patch.object(pchat, "who", lambda args, cfg, required=True: "bet-manager"),
                   mock.patch.object(pchat.identities, "remember", lambda *a, **k: None),
                   mock.patch.object(pchat.runstore, "silent_refusal", lambda *a, **k: None)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def post(self, channel, text, account, cfg=None, cont="", reply_to=None):
        self.posts.append((channel, text, account))
        return 1

    def fail_queue(self, *args, **kwargs):
        raise AssertionError(f"nothing may be queued: {args} {kwargs}")

    def pchat(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pchat.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class CitedAuthorityTests(AuthorityCase):
    def test_the_owners_and_an_assistants_verified_posts_authorize(self):
        for msgid, sender in (("m-owner", "pat"), ("m-assistant", "sam")):
            with self.subTest(msgid=msgid):
                entry, why = chatlib.cited_authority(msgid, CFG)
                self.assertEqual((entry["from"], why), (sender, ""))

    def test_everything_else_is_refused_with_its_reason(self):
        for msgid, reason in (("m-missing", "no post with msgid m-missing in the record"),
                              ("m-unverified", "the cited post is not from a verified account"),
                              ("m-manager", "the cited post is from alp-manager, who has no owner authority")):
            with self.subTest(msgid=msgid):
                self.assertEqual(chatlib.cited_authority(msgid, CFG), (None, reason))

    def test_authority_follows_the_config_not_a_fixed_name(self):
        other = dict(CFG, owner="robin", assistants=[])
        self.assertIsNone(chatlib.cited_authority("m-owner", other)[0])
        self.assertIsNone(chatlib.cited_authority("m-assistant", other)[0])


class PostAuthorityTests(AuthorityCase):
    def test_a_valid_citation_is_appended_to_the_post(self):
        rc, out, err = self.pchat("post", "#beta", "please", "start", "--type", "request", "--re", "bet-1",
                                  "--authority", "m-owner")
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(self.posts, [("#beta", "[request bet-1] please start — authority: msgid=m-owner",
                                       "bet-manager")])
        self.assertEqual(out, "posted 1 line(s) to #beta as bet-manager\n")

    def test_a_refused_citation_exits_2_and_posts_nothing(self):
        for msgid, reason in (("m-missing", "no post with msgid m-missing in the record"),
                              ("m-unverified", "the cited post is not from a verified account"),
                              ("m-manager", "the cited post is from alp-manager, who has no owner authority")):
            with self.subTest(msgid=msgid):
                rc, out, err = self.pchat("post", "#beta", "please start", "--authority", msgid)
                self.assertEqual((rc, out), (2, ""))
                self.assertEqual(err, f"pchat: --authority {msgid}: {reason}\n")
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()
