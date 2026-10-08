"""receipt_experiment.start_kind() on its own, with no bridge: the explicit
receipt type only for the exact reviewer-start record while the experiment's
marker holds exactly its expected content, and the ordinary status type for
every other record or marker. Plateia's own test (D-69); invented data only."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402  (first: sandbox home and live-state guard)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import receipt_experiment  # noqa: E402
_isolation.check_bound(receipt_experiment)

START = {"project": "alpha", "role": "reviewer", "parent": "alp-solver-1", "request": "alpha-req-1",
         "task": "PR #57"}
MARKER = (receipt_experiment.EXPERIMENT + "\n").encode()
BOUND_FLAG = receipt_experiment.FLAG_PATH  # as bound at import, before any test patches it


class StartKindTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=_isolation.HOME)
        self.addCleanup(tmp.cleanup)
        self.flag = Path(tmp.name) / "experiments" / (receipt_experiment.EXPERIMENT + ".enabled")
        patch = mock.patch.object(receipt_experiment, "FLAG_PATH", self.flag)
        patch.start()
        self.addCleanup(patch.stop)

    def mark(self, content=MARKER):
        self.flag.parent.mkdir(parents=True, exist_ok=True)
        self.flag.write_bytes(content)

    def test_the_exact_reviewer_start_with_the_marker_is_a_receipt(self):
        self.mark()
        record = dict(START)
        self.assertEqual(receipt_experiment.start_kind(record), "receipt-start")
        self.assertEqual(record, START)  # read, never changed

    def test_extra_fields_on_the_record_do_not_matter(self):
        self.mark()
        self.assertEqual(receipt_experiment.start_kind(dict(START, name="alp-reviewer-4", brand="codex")),
                         "receipt-start")

    def test_a_missing_marker_leaves_status(self):
        self.assertFalse(self.flag.exists())
        self.assertEqual(receipt_experiment.start_kind(dict(START)), "status")

    def test_a_malformed_marker_leaves_status(self):
        for content in (b"", MARKER.rstrip(b"\n"), MARKER + b"\n", MARKER + b"on", b" " + MARKER,
                        MARKER.upper(), b"review-start-receipts-v2\n", b"yes\n", b"\xff\xfe\n"):
            with self.subTest(content=content):
                self.mark(content)
                self.assertEqual(receipt_experiment.start_kind(dict(START)), "status")

    def test_an_unreadable_marker_leaves_status(self):
        self.flag.mkdir(parents=True)  # opening a directory fails like any unreadable file
        self.assertEqual(receipt_experiment.start_kind(dict(START)), "status")

    def test_any_other_record_is_status(self):
        self.mark()
        changes = [("role", "solver"), ("role", None), ("project", ""), ("project", None), ("project", 7),
                   ("parent", ""), ("parent", None), ("parent", ["alp-solver-1"]),
                   ("request", ""), ("request", "Alpha-Req-1"), ("request", "alpha req"), ("request", None),
                   ("task", "PR #0"), ("task", "PR #057"), ("task", "PR #57 and more"), ("task", "pr #57"),
                   ("task", "issue #57"), ("task", None)]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                self.assertEqual(receipt_experiment.start_kind(dict(START, **{key: value})), "status")
        for key in START:
            with self.subTest(missing=key):
                record = {k: v for k, v in START.items() if k != key}
                self.assertEqual(receipt_experiment.start_kind(record), "status")

    def test_the_marker_lives_under_the_chat_state(self):
        self.assertEqual(BOUND_FLAG, Path(os.environ["CHAT_STATE"]) / "experiments" / "review-start-receipts-v1.enabled")


if __name__ == "__main__":
    unittest.main()
