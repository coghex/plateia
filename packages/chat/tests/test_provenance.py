"""The captured chat source matches its provenance record: every captured file
has the recorded hash and mode, a byte-for-byte file still hashes to its
source commit's content, a changed one lists its changes, a carried commit
names the files it changed, and nothing else sits in the captured tree
unrecorded."""
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
        else:
            self.assertEqual(PROVENANCE["effective_source"], carried[-1]["commit"])

    def test_each_file_names_its_source_commit(self):
        base = PROVENANCE["baseline"]
        carried = {c["commit"]: set(c["files"]) for c in PROVENANCE["carried_commits"]}
        for commit in carried:
            self.assertRegex(commit, r"^[0-9a-f]{40}$")
        changed_by_carry = set().union(*carried.values()) if carried else set()
        for entry in PROVENANCE["captured"]:
            with self.subTest(path=entry["path"]):
                source = entry["source_commit"]
                if source == base:
                    self.assertNotIn(entry["baseline_path"], changed_by_carry)
                    self.assertEqual(entry["effective_sha256"], entry["baseline"]["sha256"])
                else:
                    self.assertIn(entry["baseline_path"], carried[source])
                    self.assertNotEqual(entry["effective_sha256"], entry["baseline"]["sha256"])

    def test_the_carried_timing_link_is_kept_and_recorded_as_external(self):
        entry = next(e for e in PROVENANCE["captured"] if e["baseline_path"] == "chat/SKILL.md")
        link = "../project-manager/references/timing-reservation.md"
        self.assertIn(f"]({link})", (REPO / entry["path"]).read_text(encoding="utf-8"))
        self.assertIn(link, [r["reference"] for r in entry["external_references"]])
        self.assertFalse((REPO / entry["path"]).parent.joinpath(link).exists())

    def test_every_captured_file_has_its_recorded_hash_and_mode(self):
        for entry in PROVENANCE["captured"]:
            path = REPO / entry["path"]
            with self.subTest(path=entry["path"]):
                self.assertEqual(entry["path"], "packages/" + entry["baseline_path"])
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
