"""Release artifacts carry no private settings or data: every artifact, and
every member of every archive, is opened and fails on a home-directory path,
a credential pattern or a chat-state or configuration file. The scan itself
is shown to catch each of those, and to allow the invented placeholders and
generic ~/... references the captured code uses."""
import json
import unittest
import zipfile

import _support
from _support import build_release, make_repo


class PrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()
        repo, _ = make_repo(cls.sandbox.root)
        cls.release, _ = build_release.build(repo, cls.sandbox.root / "releases")

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.close()

    def test_every_artifact_and_member_is_clean(self):
        opened = 0
        for artifact in sorted(self.release.iterdir()):
            with self.subTest(artifact=artifact.name):
                data = artifact.read_bytes()
                self.assertEqual(build_release.private_findings(artifact.name, data), [])
                if artifact.suffix in (".whl", ".zip"):
                    with zipfile.ZipFile(artifact) as z:
                        opened += len(z.namelist())
        self.assertGreater(opened, 10)

    def test_the_manifest_names_no_local_path(self):
        text = (self.release / "manifest.json").read_text()
        self.assertNotRegex(text, r"/(Users|home)/")
        self.assertNotIn(str(self.sandbox.root), text)
        self.assertNotIn(str(_support.CHECKOUT), text)

    def test_the_scan_catches_each_kind_of_private_data(self):
        cases = {
            "a home-directory path": [b"LOG = '/Users/realperson/.local/state/chat'\n",
                                      b"cd /home/someoneelse/work\n"],
            "a credential pattern": [b'"password": "hunter2hunter2"\n', b"-----BEGIN OPENSSH PRIVATE KEY-----\n",
                                     b"token = 'ghp_" + b"x" * 36 + b"'\n", b"https://ntfy.sh/my-private-topic\n"],
        }
        for why, bodies in cases.items():
            for body in bodies:
                with self.subTest(body=body):
                    wheel = build_release.zip_bytes({"plateia_chat/scripts/x.py": (body, False)})
                    self.assertEqual(build_release.private_findings("a.whl", wheel),
                                     [("plateia_chat/scripts/x.py", why)])
        for member in ("chat/logs/alpha.jsonl", "identities.json", "checkpoints.json", "pending.json",
                       ".config/chat/config.json", "outbox.jsonl", "bridge.log", "manager.json",
                       "experiments/review-start-receipts-v1.enabled"):
            with self.subTest(member=member):
                archive = build_release.zip_bytes({member: (b"{}", False)})
                self.assertIn((member, "a chat-state or configuration file"),
                              build_release.private_findings("a.zip", archive))

    def test_invented_placeholders_and_generic_paths_are_allowed(self):
        body = json.dumps({"path": "/Users/someone/.local/share/claude", "config": "~/.config/chat/config.json",
                           "accounts": {"<account>": "<password>"}, "ntfy": "https://ntfy.sh/<topic>"}).encode()
        self.assertEqual(build_release.private_findings("a.whl", build_release.zip_bytes(
            {"plateia_chat/scripts/x.py": (body, False)})), [])


if __name__ == "__main__":
    unittest.main()
