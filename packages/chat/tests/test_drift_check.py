"""The drift check on invented skills trees: it reports every change, missing
file, addition, type, mode and symlink-target change, skips exclusions, calls
an unreadable input an error rather than no drift, and leaves every tree's
bytes and modification times as it found them. A captured file may record an
uncommitted working copy over a named HEAD (#31, docs/plateia_design.md D-19):
a match is reported as that, never as a carried commit, and every later change
is still drift."""
import contextlib
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

CHAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHAT))
import drift_check  # noqa: E402

sys.path.insert(0, str(CHAT.parent / "release"))
import stage_release  # noqa: E402  (production staging consumes the same provenance and drift check)

FILES = {"core.py": (b"print('core')\n", "100644"), "tool": (b"#!/bin/sh\necho tool\n", "100755"),
         "tests/test_core.py": (b"import core\n", "100644")}
LINK = ("current", "core.py")  # an invented captured symlink and its target
EXCLUDE = ["__pycache__", "*.pyc", "bridge", "tests/fixtures"]


def snapshot(root):
    """Every path's bytes (or link target), mode and modification time."""
    found = {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(folder) / name
            st = path.lstat()
            try:
                body = (os.readlink(path) if stat.S_ISLNK(st.st_mode)
                        else path.read_bytes() if stat.S_ISREG(st.st_mode) else None)
            except PermissionError:
                body = "unreadable"
            found[path.relative_to(root).as_posix()] = (body, st.st_mode, st.st_mtime_ns)
    return found


class DriftCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name) / "skills"
        self.scripts = self.tree / "demo" / "scripts"
        for rel, (body, mode) in FILES.items():
            path = self.scripts / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o755 if mode == "100755" else 0o644)
        os.symlink(LINK[1], self.scripts / LINK[0])
        (self.scripts / "__pycache__").mkdir()
        (self.scripts / "__pycache__" / "core.cpython-314.pyc").write_bytes(b"\0")
        (self.scripts / "bridge").write_bytes(b"excluded\n")
        (self.scripts / "tests" / "fixtures").mkdir()
        (self.scripts / "tests" / "fixtures" / "sample.txt").write_bytes(b"excluded\n")
        captured = [{"baseline_path": f"demo/scripts/{rel}",
                     "baseline": {"type": "file", "mode": mode, "sha256": hashlib.sha256(body).hexdigest()},
                     "effective_sha256": hashlib.sha256(body).hexdigest()}
                    for rel, (body, mode) in FILES.items()]
        captured.append({"baseline_path": f"demo/scripts/{LINK[0]}", "baseline": {"type": "symlink", "target": LINK[1]}})
        self.provenance = {"baseline": "0" * 40, "captured": captured,
                           "monitored": {"root": "demo/scripts", "exclude": [{"pattern": p} for p in EXCLUDE]}}
        self.path = Path(tmp.name) / "provenance.json"
        self.path.write_text(json.dumps(self.provenance))

    def run_check(self):
        """(exit status, result) from the command line, asserting the tree is untouched."""
        before = snapshot(self.tree)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = drift_check.main(["--json", "--provenance", str(self.path), str(self.tree)])
        self.assertEqual(snapshot(self.tree), before, "the drift check changed the tree")
        return rc, json.loads(out.getvalue())

    def kinds(self, result):
        return sorted((d["path"], d["kind"]) for d in result["drift"])


class DriftTests(DriftCase):
    def test_an_identical_tree_has_no_drift(self):
        rc, result = self.run_check()
        self.assertEqual((rc, result["drift"], result["errors"]), (0, [], []))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            drift_check.main(["--provenance", str(self.path), str(self.tree)])
        self.assertIn("no drift", out.getvalue())

    def test_a_changed_file_is_reported(self):
        (self.scripts / "core.py").write_bytes(b"print('edited')\n")
        rc, result = self.run_check()
        self.assertEqual((rc, self.kinds(result)), (1, [("demo/scripts/core.py", "changed")]))

    def test_a_file_matching_its_carried_commit_says_so(self):
        carried = b"print('carried')\n"
        entry = self.provenance["captured"][0]
        entry.update(source_commit="1" * 40, effective_sha256=hashlib.sha256(carried).hexdigest())
        self.path.write_text(json.dumps(self.provenance))
        (self.scripts / "core.py").write_bytes(carried)
        rc, result = self.run_check()
        self.assertEqual((rc, self.kinds(result)), (1, [("demo/scripts/core.py", "changed")]))
        self.assertEqual(result["drift"][0]["detail"], "matches carried commit 1111111")
        (self.scripts / "core.py").write_bytes(b"print('other')\n")
        self.assertTrue(self.run_check()[1]["drift"][0]["detail"].startswith("sha256 "))

    def test_a_missing_file_is_reported(self):
        (self.scripts / "tests" / "test_core.py").unlink()
        rc, result = self.run_check()
        self.assertEqual((rc, self.kinds(result)), (1, [("demo/scripts/tests/test_core.py", "missing")]))

    def test_an_added_file_is_reported_and_exclusions_are_not(self):
        (self.scripts / "tests" / "test_new.py").write_bytes(b"pass\n")
        (self.scripts / "extra").mkdir()
        (self.scripts / "extra" / "helper.py").write_bytes(b"pass\n")
        os.symlink("elsewhere", self.scripts / "new-link")
        (self.scripts / "tests" / "fixtures" / "another.txt").write_bytes(b"excluded\n")
        (self.scripts / "stray.pyc").write_bytes(b"\0")
        rc, result = self.run_check()
        self.assertEqual((rc, self.kinds(result)), (1, [("demo/scripts/extra/helper.py", "added"),
                                                         ("demo/scripts/new-link", "added"),
                                                         ("demo/scripts/tests/test_new.py", "added")]))
        self.assertEqual(next(d for d in result["drift"] if d["path"].endswith("new-link"))["detail"],
                         "symlink to elsewhere")

    def test_a_changed_symlink_target_is_reported_without_following_it(self):
        (self.scripts / LINK[0]).unlink()
        os.symlink("tool", self.scripts / LINK[0])
        rc, result = self.run_check()
        self.assertEqual((rc, self.kinds(result)), (1, [("demo/scripts/current", "symlink target changed")]))
        self.assertEqual(result["drift"][0]["detail"], "core.py -> tool")
        # the same target text reports nothing, even if what it points at changed
        (self.scripts / LINK[0]).unlink()
        os.symlink(LINK[1], self.scripts / LINK[0])
        (self.scripts / "core.py").write_bytes(b"print('edited')\n")
        self.assertEqual(self.kinds(self.run_check()[1]), [("demo/scripts/core.py", "changed")])

    def test_type_and_mode_changes_are_reported(self):
        (self.scripts / "core.py").unlink()
        os.symlink("tool", self.scripts / "core.py")
        (self.scripts / LINK[0]).unlink()
        (self.scripts / LINK[0]).write_bytes(b"now a file\n")
        (self.scripts / "tool").chmod(0o644)
        rc, result = self.run_check()
        self.assertEqual((rc, self.kinds(result)), (1, [("demo/scripts/core.py", "type changed"),
                                                         ("demo/scripts/current", "type changed"),
                                                         ("demo/scripts/tool", "mode changed")]))

    def test_every_difference_is_reported_at_once(self):
        (self.scripts / "core.py").write_bytes(b"print('edited')\n")
        (self.scripts / "tool").unlink()
        (self.scripts / "added.py").write_bytes(b"pass\n")
        (self.scripts / LINK[0]).unlink()
        os.symlink("tool", self.scripts / LINK[0])
        rc, result = self.run_check()
        self.assertEqual(self.kinds(result), [("demo/scripts/added.py", "added"), ("demo/scripts/core.py", "changed"),
                                              ("demo/scripts/current", "symlink target changed"),
                                              ("demo/scripts/tool", "missing")])

    @unittest.skipIf(os.geteuid() == 0, "root reads unreadable files")
    def test_an_unreadable_file_is_an_error_not_no_drift(self):
        (self.scripts / "core.py").chmod(0)
        self.addCleanup((self.scripts / "core.py").chmod, 0o644)
        rc, result = self.run_check()
        self.assertEqual(rc, 2)
        self.assertEqual([e["path"] for e in result["errors"]], ["demo/scripts/core.py"])

    @unittest.skipIf(os.geteuid() == 0, "root lists unreadable directories")
    def test_an_unlistable_directory_is_an_error(self):
        (self.scripts / "tests").chmod(0)
        self.addCleanup((self.scripts / "tests").chmod, 0o755)
        rc, result = self.run_check()
        self.assertEqual(rc, 2)
        self.assertIn("demo/scripts/tests", [e["path"] for e in result["errors"]])

    def test_a_missing_tree_or_monitored_directory_is_an_error(self):
        for tree in (self.tree / "absent", self.tree):
            if tree == self.tree:
                (self.tree / "demo" / "scripts").rename(self.tree / "demo" / "moved")
            with self.subTest(tree=tree.name):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = drift_check.main(["--provenance", str(self.path), str(tree)])
                self.assertEqual(rc, 2)
                self.assertIn("not a no-drift result", out.getvalue())

    def test_a_bad_provenance_file_is_refused(self):
        for body in ("{", "[]", json.dumps({"monitored": {"root": "demo/scripts"}, "captured": []}),
                     json.dumps(dict(self.provenance, captured=[{"baseline_path": "elsewhere/x",
                                                                 "baseline": {"type": "file", "mode": "100644",
                                                                              "sha256": "0"}}]))):
            with self.subTest(body=body[:30]):
                self.path.write_text(body)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(drift_check.main(["--provenance", str(self.path), str(self.tree)]), 2)
                self.assertIn("drift_check:", err.getvalue())

    def test_paths_are_reported_relative_to_the_tree(self):
        (self.scripts / "core.py").write_bytes(b"print('edited')\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            drift_check.main(["--provenance", str(self.path), str(self.tree)])
        self.assertNotIn(str(self.tree), out.getvalue())
        self.assertIn("demo/scripts/core.py", out.getvalue())


HEAD = "2" * 40  # an invented skills-repository commit


def digest(body):
    return hashlib.sha256(body).hexdigest()


class WorkingCopyTests(DriftCase):
    """Entries that record uncommitted bytes over HEAD: four that match the tree, one the tree has moved past
    (like a file that gained other edits since), a baseline file with an ordinary edit, and a new file."""

    SOURCES = {"core.py": b"print('core, working copy')\n", "owner.py": b"owner = 1\n",
               "tests/test_owner.py": b"import owner\n", "tests/test_more.py": b"import owner\n# more\n",
               "tool": b"#!/bin/sh\necho recorded source\n"}
    NEW = ("owner.py", "tests/test_owner.py", "tests/test_more.py")

    def setUp(self):
        super().setUp()
        for rel, body in self.SOURCES.items():
            path = self.scripts / rel
            path.write_bytes(body)
            path.chmod(0o755 if rel == "tool" else 0o644)
        (self.scripts / "tool").write_bytes(b"#!/bin/sh\necho live, later\n")  # moved past its recorded source
        (self.scripts / "tests" / "test_core.py").write_bytes(b"import core  # edited\n")  # an ordinary edit
        (self.scripts / "extra.py").write_bytes(b"pass\n")  # a new file nothing records
        entries = {e["baseline_path"]: e for e in self.provenance["captured"]}
        for rel in ("core.py", "tool"):
            entries[f"demo/scripts/{rel}"]["working_copy"] = self.record(rel)
        for rel in self.NEW:
            self.provenance["captured"].append({"baseline_path": f"demo/scripts/{rel}", "baseline": None,
                                                "working_copy": self.record(rel)})
        self.save()

    def record(self, rel, **over):
        return dict({"head": HEAD, "sha256": digest(self.SOURCES[rel]),
                     "mode": "100755" if rel == "tool" else "100644"}, **over)

    def save(self):
        self.path.write_text(json.dumps(self.provenance))

    def entry(self, rel):
        return next(e for e in self.provenance["captured"] if e["baseline_path"] == f"demo/scripts/{rel}")

    def drift(self, result, rel):
        return next(d for d in result["drift"] if d["path"] == f"demo/scripts/{rel}")

    def test_the_outcomes_in_text_and_json(self):
        rc, result = self.run_check()
        self.assertEqual(rc, 1, "drift remains, with no read error")
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.kinds(result), [
            ("demo/scripts/core.py", "working copy"), ("demo/scripts/extra.py", "added"),
            ("demo/scripts/owner.py", "working copy"), ("demo/scripts/tests/test_core.py", "changed"),
            ("demo/scripts/tests/test_more.py", "working copy"), ("demo/scripts/tests/test_owner.py", "working copy"),
            ("demo/scripts/tool", "changed")])
        self.assertEqual(result["working_copy_heads"], [HEAD])
        for rel in ("core.py", "owner.py", "tests/test_owner.py", "tests/test_more.py"):
            item = self.drift(result, rel)
            self.assertEqual(item["source_sha256"], digest(self.SOURCES[rel]))
            self.assertEqual(item["detail"], f"matches recorded working copy over HEAD {HEAD[:7]} "
                                             f"(sha256 {digest(self.SOURCES[rel])[:12]})")
            self.assertNotIn("carried", item["detail"])
        moved = self.drift(result, "tool")
        self.assertEqual(moved["source_sha256"], digest(self.SOURCES["tool"]))
        self.assertIn(digest(self.SOURCES["tool"])[:12], moved["detail"], "it names the recorded source's hash")
        self.assertNotIn("matches", moved["detail"])
        self.assertNotIn("source_sha256", self.drift(result, "tests/test_core.py"))
        self.assertEqual(self.drift(result, "extra.py")["detail"], "file")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(drift_check.main(["--provenance", str(self.path), str(self.tree)]), 1)
        text = out.getvalue()
        self.assertIn(f"uncommitted bytes over skills HEAD {HEAD[:12]}", text)
        for rel in self.SOURCES:
            self.assertIn(digest(self.SOURCES[rel])[:12], text)
        self.assertEqual(len([l for l in text.splitlines() if l.strip().startswith("working copy")]), 4)
        self.assertNotIn("carried commit", text)

    def test_a_match_is_never_a_carried_commit_even_when_one_is_recorded(self):
        carried = b"print('carried')\n"
        entry = self.entry("core.py")
        entry.update(source_commit="1" * 40, effective_sha256=digest(carried))
        self.save()
        rc, result = self.run_check()
        self.assertEqual(self.drift(result, "core.py")["kind"], "working copy")
        (self.scripts / "core.py").write_bytes(carried)
        self.assertEqual(self.drift(self.run_check()[1], "core.py")["detail"], "matches carried commit 1111111")

    def test_a_later_change_is_still_drift_for_a_baseline_file_and_a_new_one(self):
        for rel in ("core.py", "owner.py"):
            with self.subTest(rel):
                (self.scripts / rel).write_bytes(b"edited again\n")
                item = self.drift(self.run_check()[1], rel)
                self.assertEqual(item["kind"], "changed")
                self.assertIn(digest(self.SOURCES[rel])[:12], item["detail"])
                self.assertEqual(item["source_sha256"], digest(self.SOURCES[rel]))
                self.assertNotIn("matches", item["detail"])
                (self.scripts / rel).write_bytes(self.SOURCES[rel])
        self.assertEqual(self.drift(self.run_check()[1], "owner.py")["kind"], "working copy")

    def test_a_file_back_at_its_baseline_is_no_drift(self):
        (self.scripts / "core.py").write_bytes(FILES["core.py"][0])
        self.assertNotIn("demo/scripts/core.py", [d["path"] for d in self.run_check()[1]["drift"]])

    def test_a_missing_file_is_reported_for_both_kinds(self):
        for rel, detail in (("core.py", "baseline file is gone"), ("owner.py", "recorded working-copy file is gone")):
            with self.subTest(rel):
                (self.scripts / rel).unlink()
                item = self.drift(self.run_check()[1], rel)
                self.assertEqual((item["kind"], item["detail"]), ("missing", detail))
                (self.scripts / rel).write_bytes(self.SOURCES[rel])

    def test_a_type_change_is_reported_for_both_kinds(self):
        for rel in ("core.py", "owner.py"):
            with self.subTest(rel):
                (self.scripts / rel).unlink()
                os.symlink("tool", self.scripts / rel)
                item = self.drift(self.run_check()[1], rel)
                self.assertEqual((item["kind"], item["detail"]), ("type changed", "file -> symlink to tool"))
                (self.scripts / rel).unlink()
                (self.scripts / rel).write_bytes(self.SOURCES[rel])

    def test_a_mode_change_is_reported_against_the_recorded_mode(self):
        (self.scripts / "owner.py").chmod(0o755)  # recorded 100644
        (self.scripts / "core.py").chmod(0o755)
        (self.scripts / "tool").write_bytes(self.SOURCES["tool"])
        (self.scripts / "tool").chmod(0o644)  # recorded 100755
        result = self.run_check()[1]
        modes = {d["path"]: d["detail"] for d in result["drift"] if d["kind"] == "mode changed"}
        self.assertEqual(modes, {"demo/scripts/owner.py": "100644 -> 100755", "demo/scripts/core.py": "100644 -> 100755",
                                 "demo/scripts/tool": "100755 -> 100644"})
        self.assertIn(("demo/scripts/tool", "working copy"), self.kinds(result))

    @unittest.skipIf(os.geteuid() == 0, "root reads unreadable files")
    def test_an_unreadable_new_file_is_an_error_not_no_drift(self):
        (self.scripts / "owner.py").chmod(0)
        self.addCleanup((self.scripts / "owner.py").chmod, 0o644)
        rc, result = self.run_check()
        self.assertEqual((rc, [e["path"] for e in result["errors"]]), (2, ["demo/scripts/owner.py"]))

    def test_no_baseline_content_is_needed_or_invented(self):
        for rel in self.NEW:
            self.assertIsNone(self.entry(rel)["baseline"])
        self.assertEqual(len(drift_check.load_provenance(self.path)["captured"]), len(FILES) + 1 + len(self.NEW))

    def test_a_malformed_record_is_refused(self):
        def refuse(change):
            provenance = json.loads(json.dumps(self.provenance))
            change(provenance)
            self.path.write_text(json.dumps(provenance))
            with self.assertRaises(drift_check.BadProvenance):
                drift_check.load_provenance(self.path)
        entry = lambda p, rel: next(e for e in p["captured"] if e["baseline_path"].endswith(rel))  # noqa: E731
        refuse(lambda p: entry(p, "owner.py").pop("working_copy"))  # no baseline and no working copy
        for field, value in (("head", "2" * 7), ("head", "G" * 40), ("head", "A" * 40), ("sha256", "0" * 63),
                             ("sha256", None), ("mode", "100600"), ("mode", None)):
            refuse(lambda p, f=field, v=value: entry(p, "owner.py")["working_copy"].update({f: v}))
        refuse(lambda p: entry(p, "owner.py").update(working_copy=[]))
        refuse(lambda p: entry(p, "current").update(working_copy=self.record("core.py")))  # a symlink baseline

    def test_production_staging_still_reads_the_same_record(self):
        # a live file at the recorded working-copy bytes is not the baseline and not a carried commit, so
        # staging's content check refuses it; at the baseline it passes; baseline-less entries cannot raise
        provenance = drift_check.load_provenance(self.path)
        self.assertIn("matches neither the baseline nor a carried commit",
                      stage_release.content_problem("demo/scripts/core.py", self.tree, provenance))
        (self.scripts / "core.py").write_bytes(FILES["core.py"][0])
        self.assertIsNone(stage_release.content_problem("demo/scripts/core.py", self.tree, provenance))
        observed, problems = stage_release.folder_inventory(self.tree, provenance)
        self.assertIn("changed content (working copy): demo/scripts/owner.py", problems)
        self.assertIn("unknown ownership: an entry the provenance doesn't list: demo/scripts/extra.py", problems)
        self.assertNotIn("unknown ownership: an entry the provenance doesn't list: demo/scripts/owner.py", problems)
        self.assertIn("demo/scripts/owner.py", observed["files"])


class ExclusionTests(unittest.TestCase):
    def test_segment_and_path_patterns(self):
        self.assertTrue(drift_check.excluded("__pycache__/x.pyc", EXCLUDE))
        self.assertTrue(drift_check.excluded("tests/__pycache__/y.pyc", EXCLUDE))
        self.assertTrue(drift_check.excluded("tests/fixtures/a/b.txt", EXCLUDE))
        self.assertTrue(drift_check.excluded("bridge", EXCLUDE))
        self.assertFalse(drift_check.excluded("fixtures/a.txt", EXCLUDE))
        self.assertFalse(drift_check.excluded("bridge.py", EXCLUDE))
        self.assertFalse(drift_check.excluded("tests/test_core.py", EXCLUDE))


if __name__ == "__main__":
    unittest.main()
