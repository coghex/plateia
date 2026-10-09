"""Verifying a release refuses, naming the failing input, and never changes
the release it reads: a missing or extra artifact, an artifact changed by one
byte, a manifest without an interpreter requirement or a source commit,
artifact metadata that disagrees with the manifest, and a skill whose
declared compatible package versions exclude the release's package."""
import hashlib
import re
import json
import shutil
import unittest
import zipfile

import _support
from _support import build_release, make_repo, rezip, snapshot


class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = _support.Sandbox()
        repo, _ = make_repo(cls.sandbox.root)
        cls.built, _ = build_release.build(repo, cls.sandbox.root / "releases")
        cls.manifest = json.loads((cls.built / "manifest.json").read_text())
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.close()

    def setUp(self):
        VerifyTests.n += 1
        self.release = self.sandbox.root / f"copy-{self.n}" / self.built.name
        shutil.copytree(self.built, self.release)
        self.wheel = self.release / self.manifest["package"]["artifact"]
        self.skill = self.release / self.manifest["skill"]["artifact"]

    def edit_manifest(self, change):
        manifest = json.loads((self.release / "manifest.json").read_text())
        change(manifest)
        (self.release / "manifest.json").write_text(json.dumps(manifest))

    def rehash(self, name):
        """Record an artifact's new bytes in the manifest, so only the check
        under test can fail."""
        data = (self.release / name).read_bytes()
        def change(m):
            entry = next(a for a in m["artifacts"] if a["name"] == name)
            entry.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        self.edit_manifest(change)

    def refused(self, pattern):
        before = snapshot(self.release)
        with self.assertRaisesRegex(build_release.Refused, pattern):
            build_release.verify(self.release)
        self.assertEqual(snapshot(self.release), before, "verification changed its input")

    def test_the_built_release_verifies(self):
        self.assertEqual(build_release.verify(self.release)["release"], self.built.name)

    def test_a_missing_artifact_is_named(self):
        self.wheel.unlink()
        self.refused(rf"artifact {re.escape(self.wheel.name)} is missing")

    def test_an_artifact_changed_by_one_byte_is_named(self):
        data = bytearray(self.skill.read_bytes())
        data[len(data) // 2] ^= 1
        self.skill.write_bytes(bytes(data))
        self.refused(rf"artifact {re.escape(self.skill.name)} does not match the manifest \(sha256 [0-9a-f]{{12}} recorded")

    def test_an_extra_file_is_named(self):
        (self.release / "notes.txt").write_text("x\n")
        self.refused(r"notes.txt is not an artifact the manifest names")

    def test_a_missing_or_unreadable_manifest_is_refused(self):
        (self.release / "manifest.json").write_text("{")
        self.refused(r"manifest.json is unreadable")
        (self.release / "manifest.json").unlink()
        self.refused(r"manifest.json is missing")

    def test_a_manifest_without_an_interpreter_requirement_is_refused(self):
        for change in (lambda m: m.pop("requires_python"), lambda m: m.update(requires_python=" "),
                       lambda m: m["dependencies"].pop("interpreter"),
                       lambda m: m["dependencies"]["interpreter"].pop("requires")):
            with self.subTest():
                shutil.copy2(self.built / "manifest.json", self.release / "manifest.json")
                self.edit_manifest(change)
                self.refused(r"pins no interpreter requirement")

    def test_a_manifest_without_a_source_commit_is_refused(self):
        for change in (lambda m: m["source"].pop("commit"), lambda m: m["source"].update(commit="48b901a"),
                       lambda m: m.pop("source")):
            with self.subTest():
                shutil.copy2(self.built / "manifest.json", self.release / "manifest.json")
                self.edit_manifest(change)
                self.refused(r"pins no source commit")

    def test_a_package_version_that_disagrees_with_the_wheel_is_refused(self):
        self.edit_manifest(lambda m: m["package"].update(version="0.2.0+g000000000000"))
        self.refused(r"is plateia-chat 0.1.0\+g[0-9a-f]{12}, but the manifest says plateia-chat 0.2.0")

    def test_a_wheel_whose_contents_disagree_with_its_record_is_refused(self):
        name = "plateia_chat/scripts/pchat"
        with zipfile.ZipFile(self.wheel) as z:
            changed = z.read(name) + b"# changed\n"
        self.wheel.write_bytes(rezip(self.wheel.read_bytes(), {name: changed}))
        self.rehash(self.wheel.name)
        self.refused(rf"{name} does not match the wheel's RECORD")

    def test_a_skill_that_declares_an_incompatible_package_version_is_refused(self):
        bad = {"plateia-chat": ["0.2.0"]}
        with zipfile.ZipFile(self.skill) as z:
            declaration = json.loads(z.read("chat/skill.json"))
        declaration["compatible_packages"] = bad
        self.skill.write_bytes(rezip(self.skill.read_bytes(),
                                     {"chat/skill.json": json.dumps(declaration).encode()}))
        self.rehash(self.skill.name)
        self.edit_manifest(lambda m: m["skill"].update(compatible_packages=bad))
        self.refused(r"skill chat 0.1.0\+g[0-9a-f]{12} is compatible with plateia-chat 0.2.0, "
                     r"but the release carries plateia-chat 0.1.0\+g")

    def test_a_skill_declaration_that_disagrees_with_the_manifest_is_refused(self):
        self.edit_manifest(lambda m: m["skill"].update(compatible_packages={"plateia-chat": ["0.1.0", "0.2.0"]}))
        self.refused(r"compatibility declaration differs from the manifest's")

    def test_a_missing_or_malformed_skill_declaration_is_refused(self):
        for body, pattern in ((None, r"has no readable SKILL.md and skill.json"),
                              (b"{", r"has no readable SKILL.md and skill.json"),
                              (b'{"name": "chat", "version": "x", "compatible_packages": {}}',
                               r"skill.json has no valid name, version and compatible_packages"),
                              (b'{"name": "chat", "version": "x", "compatible_packages": {"plateia-chat": "0.1.0"}}',
                               r"skill.json has no valid name, version and compatible_packages")):
            with self.subTest(body=body):
                shutil.copy2(self.built / self.skill.name, self.skill)
                shutil.copy2(self.built / "manifest.json", self.release / "manifest.json")
                if body is None:
                    with zipfile.ZipFile(self.skill) as z:
                        members = {n: (z.read(n), False) for n in z.namelist() if n != "chat/skill.json"}
                    self.skill.write_bytes(build_release.zip_bytes(members))
                else:
                    self.skill.write_bytes(rezip(self.skill.read_bytes(), {"chat/skill.json": body}))
                self.rehash(self.skill.name)
                self.refused(pattern)

    def test_the_command_line_names_the_cause(self):
        import io
        from contextlib import redirect_stderr
        self.wheel.unlink()
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(build_release.main(["verify", str(self.release)]), 1)
        self.assertIn(f"refused: artifact {self.wheel.name} is missing", err.getvalue())


if __name__ == "__main__":
    unittest.main()
