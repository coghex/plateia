"""The captured chat source matches its provenance record: every captured file
has the recorded hash and mode, a byte-for-byte file still hashes to the
baseline, a changed one lists its changes, and nothing else sits in the
captured tree unrecorded."""
import hashlib
import json
import os
import stat
import sys
import unittest
from pathlib import Path

CHAT = Path(__file__).resolve().parents[1]
REPO = CHAT.parents[1]
sys.path.insert(0, str(CHAT))
import drift_check  # noqa: E402

PROVENANCE = drift_check.load_provenance(CHAT / "provenance.json")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProvenanceTests(unittest.TestCase):
    def test_the_baseline_and_effective_source_are_named(self):
        self.assertRegex(PROVENANCE["baseline"], r"^[0-9a-f]{40}$")
        self.assertRegex(PROVENANCE["effective_source"], r"^[0-9a-f]{40}$")
        carried = PROVENANCE["carried_commits"]
        self.assertIsInstance(carried, list)
        if not carried:
            self.assertEqual(PROVENANCE["effective_source"], PROVENANCE["baseline"])

    def test_every_captured_file_has_its_recorded_hash_and_mode(self):
        for entry in PROVENANCE["captured"]:
            path = REPO / entry["path"]
            with self.subTest(path=entry["path"]):
                self.assertEqual(entry["path"], "packages/chat/scripts/" + entry["baseline_path"][len("chat/scripts/"):])
                self.assertEqual(sha256(path), entry["sha256"])
                executable = bool(path.stat().st_mode & stat.S_IXUSR)
                self.assertEqual(executable, entry["baseline"]["mode"] == "100755")

    def test_byte_for_byte_means_identical_and_changed_means_listed(self):
        kinds = {}
        for entry in PROVENANCE["captured"]:
            with self.subTest(path=entry["path"]):
                kinds[entry["baseline_path"]] = entry["capture"]
                if entry["capture"] == "byte-for-byte":
                    self.assertEqual(entry["changes"], [])
                    self.assertEqual(entry["sha256"], entry["effective_sha256"])
                else:
                    self.assertEqual(entry["capture"], "changed")
                    self.assertNotEqual(entry["sha256"], entry["effective_sha256"])
                    self.assertTrue(entry["changes"])
                    for change in entry["changes"]:
                        self.assertTrue(change["change"].strip() and change["reason"].strip())
        self.assertEqual(kinds["chat/scripts/receipt_experiment.py"], "byte-for-byte")  # D-69: verbatim

    def test_nothing_unrecorded_sits_in_the_captured_tree(self):
        recorded = {e["path"] for e in PROVENANCE["captured"]} | {e["path"] for e in PROVENANCE["plateia_files"]}
        present = set()
        for folder, dirs, files in os.walk(CHAT):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            present |= {(Path(folder) / f).relative_to(REPO).as_posix() for f in files if not f.endswith(".pyc")}
        self.assertEqual(present, recorded)

    def test_the_monitored_tree_excludes_only_named_paths(self):
        patterns = [e["pattern"] for e in PROVENANCE["monitored"]["exclude"]]
        for entry in PROVENANCE["monitored"]["exclude"]:
            self.assertTrue(entry["reason"].strip())
        for entry in PROVENANCE["captured"]:
            rel = entry["baseline_path"][len(PROVENANCE["monitored"]["root"]) + 1:]
            self.assertFalse(drift_check.excluded(rel, patterns), rel)


if __name__ == "__main__":
    unittest.main()
