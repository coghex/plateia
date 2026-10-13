"""The maintenance record's reader and the section helper (docs/writer_admission.md sections 4.2 and 4.3):
no file means admitted, a valid record means held in its state, and anything else is unknown, which is held.
A reader never writes the record; the only file the chat package creates is the empty admission lock. Invented
operations and boots only, on throwaway state directories under _isolation."""
import fcntl
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import delivery_store as ds  # noqa: E402
from admission_fakes import (OPERATION, changed, exclusive_lock, only_nonblocking_flocks, put_record,  # noqa: E402
                             record_fields, shared_lock, snapshot)
from outbox_fakes import Clock  # noqa: E402


class StateCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="admission-", dir=_isolation.HOME))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.paths = ds.Paths(self.root / "state")
        self.paths.state.mkdir()


class ReaderTests(StateCase):
    def test_no_file_is_no_record_and_reading_creates_nothing(self):
        before = snapshot(self.paths.state)
        record = ds.read_maintenance(self.paths)
        self.assertEqual((record.present, record.valid, record.state, record.describe()), (False, False, "none", None))
        self.assertEqual(snapshot(self.paths.state), before)

    def test_a_missing_state_directory_is_no_record(self):
        record = ds.read_maintenance(ds.Paths(self.root / "never-made"))
        self.assertFalse(record.present)
        self.assertFalse((self.root / "never-made").exists())

    def test_a_valid_record_is_held_in_its_state_with_its_evidence(self):
        raw = put_record(self.paths, record_fields(state="draining", generation=1))
        record = ds.read_maintenance(self.paths)
        self.assertEqual((record.present, record.valid, record.state), (True, True, "draining"))
        self.assertEqual(record.describe(), {"operation": OPERATION, "kind": "plateia-activation",
                                             "state": "draining", "generation": 1})
        evidence = record.evidence()
        self.assertEqual(evidence["generation"], 1)
        self.assertEqual(evidence["sha256"], __import__("hashlib").sha256(raw).hexdigest())
        st = os.stat(self.paths.maintenance)
        self.assertEqual((evidence["ino"], evidence["ctime_ns"]), (st.st_ino, st.st_ctime_ns))

    def test_canonical_json_with_or_without_one_trailing_newline_is_valid(self):
        for newline in (True, False):
            with self.subTest(newline=newline):
                put_record(self.paths, newline=newline)
                self.assertTrue(ds.read_maintenance(self.paths).valid)

    def test_every_state_reads_as_held(self):
        for state in ds.MAINT_STATES:
            with self.subTest(state=state):
                put_record(self.paths, record_fields(state=state))
                record = ds.read_maintenance(self.paths)
                self.assertEqual((record.present, record.state), (True, state))

    def test_informational_fields_are_accepted_and_nothing_else_is(self):
        put_record(self.paths, record_fields(pid=4242, pid_start="start-1"))
        self.assertTrue(ds.read_maintenance(self.paths).valid)
        put_record(self.paths, record_fields(created_at=1.0))
        self.assertEqual(ds.read_maintenance(self.paths).state, "unknown")

    def unknown(self, label, **kw):
        try:
            raw_before = self.paths.maintenance.read_bytes()
        except OSError:  # a directory, a fifo, mode 000, a dangling link: nothing to compare
            raw_before = None
        record = ds.read_maintenance(self.paths, **({"uid": kw.pop("uid")} if "uid" in kw else {}))
        self.assertEqual((record.present, record.valid, record.state), (True, False, "unknown"), label)
        self.assertEqual(record.describe()["state"], "unknown")
        self.assertIsNone(record.evidence())
        if raw_before is not None:
            self.assertEqual(self.paths.maintenance.read_bytes(), raw_before, "a reader never rewrites it")
        return record

    def test_case_5_every_invalid_record_is_unknown_and_unchanged(self):
        """Design test 5: truncated JSON, an unknown v, a wrong field type, a symlink, another owner's file,
        mode 000 (and more): every one is unknown, which is held, and its bytes are untouched."""
        good = json.dumps(record_fields(), sort_keys=True, separators=(",", ":")).encode()
        cases = {
            "truncated JSON": good[:40],
            "empty": b"",
            "not UTF-8": b"\xff\xfe\x00",
            "not an object": b"[1]\n",
            "unknown v": json.dumps(record_fields(v="maintenance/2"), sort_keys=True, separators=(",", ":")).encode(),
            "unknown state": json.dumps(record_fields(state="releasing"), sort_keys=True,
                                        separators=(",", ":")).encode(),
            "generation as a string": json.dumps(record_fields(generation="2"), sort_keys=True,
                                                 separators=(",", ":")).encode(),
            "generation as a bool": json.dumps(record_fields(generation=True), sort_keys=True,
                                               separators=(",", ":")).encode(),
            "generation zero": json.dumps(record_fields(generation=0), sort_keys=True, separators=(",", ":")).encode(),
            "boot as a number": json.dumps(record_fields(boot=5), sort_keys=True, separators=(",", ":")).encode(),
            "unknown legacy": json.dumps(record_fields(legacy="maybe"), sort_keys=True,
                                         separators=(",", ":")).encode(),
            "binding without permits": json.dumps(record_fields(binding={"release_id": "r",
                                                                         "bootstrap_digest": None}),
                                                  sort_keys=True, separators=(",", ":")).encode(),
            "an unknown field": json.dumps(dict(record_fields(), extra=1), sort_keys=True,
                                           separators=(",", ":")).encode(),
            "a missing field": json.dumps({k: v for k, v in record_fields().items() if k != "plan_sha256"},
                                          sort_keys=True, separators=(",", ":")).encode(),
            "not canonical (spaces)": json.dumps(record_fields(), sort_keys=True).encode(),
            "not canonical (unsorted)": json.dumps(record_fields(), separators=(",", ":")).encode(),
            "two newlines": good + b"\n\n",
            "an invalid operation": json.dumps(record_fields(operation="has space"), sort_keys=True,
                                               separators=(",", ":")).encode(),
        }
        for label, raw in cases.items():
            with self.subTest(label):
                put_record(self.paths, raw=raw)
                self.unknown(label)

    def test_a_record_over_64_kib_is_unknown(self):
        put_record(self.paths, raw=b"{" + b" " * (ds.MAINT_MAX + 10) + b"}")
        self.unknown("over 64 KiB")

    def test_a_symlink_is_unknown_and_never_followed(self):
        target = self.root / "elsewhere.json"
        target.write_bytes(json.dumps(record_fields(), sort_keys=True, separators=(",", ":")).encode())
        os.symlink(target, self.paths.maintenance)
        self.assertEqual(ds.read_maintenance(self.paths).state, "unknown")
        os.unlink(self.paths.maintenance)
        os.symlink(self.root / "dangling", self.paths.maintenance)
        self.assertEqual(ds.read_maintenance(self.paths).state, "unknown", "a dangling link is held, not absent")

    def test_another_users_file_is_unknown(self):
        put_record(self.paths)
        self.unknown("another owner", uid=os.getuid() + 1)

    def test_a_file_that_is_not_private_is_unknown(self):
        for mode in (0o644, 0o660, 0o606):
            with self.subTest(mode=oct(mode)):
                put_record(self.paths, mode=mode)
                self.unknown("not private")

    @unittest.skipIf(os.geteuid() == 0, "root reads a mode 000 file")
    def test_mode_000_is_unknown(self):
        put_record(self.paths, mode=0)
        self.unknown("mode 000")
        os.chmod(self.paths.maintenance, 0o600)

    def test_a_directory_is_unknown(self):
        self.paths.maintenance.mkdir()
        self.assertEqual(ds.read_maintenance(self.paths).state, "unknown")

    def test_a_fifo_is_unknown_and_never_waited_on(self):
        os.mkfifo(self.paths.maintenance)
        self.assertEqual(ds.read_maintenance(self.paths).state, "unknown")

    def test_a_state_directory_that_is_a_file_is_unknown_not_absent(self):
        gone = ds.Paths(self.root / "afile" / "state")
        (self.root / "afile").write_text("not a directory")
        self.assertEqual(ds.read_maintenance(gone).state, "unknown")


class SectionTests(StateCase):
    def test_no_record_admits_and_creates_only_the_empty_lock(self):
        before = snapshot(self.paths.state)
        with ds.section(self.paths) as adm:
            self.assertTrue(adm.admitted)
            self.assertTrue(adm.locked)
        created, removed, modified = changed(before, snapshot(self.paths.state))
        self.assertEqual((created, removed, modified), (["admission.lock"], [], []))
        self.assertEqual(self.paths.admission_lock.read_bytes(), b"", "content-free")
        self.assertFalse(self.paths.maintenance.exists(), "a writer never writes the record")

    def test_the_section_makes_a_missing_state_tree_for_a_first_call(self):
        paths = ds.Paths(self.root / "new" / "state")
        with ds.section(paths) as adm:
            self.assertTrue(adm.admitted)
        self.assertTrue(paths.admission_lock.exists())

    def test_a_record_is_not_admitted_and_is_read_while_the_lock_is_held(self):
        put_record(self.paths, record_fields(state="draining", generation=1))
        with ds.section(self.paths) as adm:
            self.assertEqual((adm.locked, adm.admitted, adm.record.state), (True, False, "draining"))
            # the lock is really held shared: an exclusive try fails, a second shared one does not
            fd = os.open(self.paths.admission_lock, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            finally:
                os.close(fd)

    def test_the_lock_is_released_when_the_section_ends_even_by_an_exception(self):
        with self.assertRaises(RuntimeError):
            with ds.section(self.paths):
                raise RuntimeError("the call failed")
        with exclusive_lock(self.paths):
            pass

    def test_an_unknown_record_is_not_admitted(self):
        put_record(self.paths, raw=b"{")
        with ds.section(self.paths) as adm:
            self.assertEqual((adm.locked, adm.admitted, adm.record.state), (True, False, "unknown"))

    def test_a_lock_held_exclusive_is_a_miss_read_as_held_never_a_wait_on_it(self):
        clock = Clock()
        with only_nonblocking_flocks(ds) as seen, exclusive_lock(self.paths):
            with ds.section(self.paths, clock=clock.mono, sleep=clock.advance) as adm:
                self.assertEqual((adm.locked, adm.admitted), (False, False))
                self.assertEqual(adm.record.state, "unknown")
                self.assertIn("busy", adm.record.why)
        self.assertGreater(len(seen), 2, "retried without blocking")
        self.assertTrue(all(op & fcntl.LOCK_NB for op in seen))
        self.assertAlmostEqual(clock.mono_t - 1000.0, ds.ADMIT_WAIT, delta=ds.ADMIT_POLL + 0.01,
                               msg="a miss gives up at ADMIT_WAIT, by a fake clock that never really slept")

    def test_wait_zero_tries_once_and_never_sleeps(self):
        slept = []
        with exclusive_lock(self.paths):
            with ds.section(self.paths, wait=0, sleep=slept.append) as adm:
                self.assertFalse(adm.locked)
        self.assertEqual(slept, [])

    def test_a_lock_released_during_the_retry_is_taken(self):
        clock = Clock()
        calls = []
        self.paths.admission_lock.write_bytes(b"")
        fd = os.open(self.paths.admission_lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def sleep(seconds):
            calls.append(seconds)
            clock.advance(seconds)
            if len(calls) == 3:
                os.close(fd)  # the owner's one durable write is over
        with ds.section(self.paths, clock=clock.mono, sleep=sleep) as adm:
            self.assertTrue(adm.admitted)
        self.assertEqual(len(calls), 3)

    def test_two_sections_hold_two_shared_locks_that_never_wait_for_one_another(self):
        with only_nonblocking_flocks(ds):
            with ds.section(self.paths) as a, ds.section(self.paths) as b:
                self.assertTrue(a.admitted and b.admitted)

    def test_a_shared_holder_never_stops_a_section(self):
        with shared_lock(self.paths):
            with ds.section(self.paths, wait=0) as adm:
                self.assertTrue(adm.admitted)

    def test_a_lock_that_cannot_be_created_is_a_storage_error_not_held_maintenance(self):
        blocker = self.root / "blocker"
        blocker.write_text("a file where a directory should be")
        with self.assertRaises(ds.StorageError):
            with ds.section(ds.Paths(blocker / "state")):
                self.fail("no section")

    @unittest.skipIf(os.geteuid() == 0, "root opens a mode 000 file")
    def test_a_lock_that_exists_and_cannot_be_opened_fails_closed(self):
        self.paths.admission_lock.write_bytes(b"")
        os.chmod(self.paths.admission_lock, 0)
        try:
            with ds.section(self.paths) as adm:
                self.assertEqual((adm.locked, adm.admitted), (False, False))
        finally:
            os.chmod(self.paths.admission_lock, 0o600)

    def test_a_lock_that_is_a_symlink_fails_closed(self):
        target = self.root / "elsewhere.lock"
        target.write_bytes(b"")
        os.symlink(target, self.paths.admission_lock)
        with ds.section(self.paths) as adm:
            self.assertEqual((adm.locked, adm.admitted), (False, False))


class SinkTests(StateCase):
    def test_the_count_is_by_file_name_and_an_unreadable_directory_is_not_zero(self):
        self.assertEqual(ds.sink_count(self.paths), 0, "a missing sink is empty")
        self.paths.refused.mkdir()
        (self.paths.refused / "tmp").mkdir()
        for name in ("0000000000000000001-aa-x.json", "0000000000000000002-bb-y.json", ".hidden.json", "note.txt"):
            (self.paths.refused / name).write_text("not read")
        self.assertEqual(ds.sink_count(self.paths), 2, "published names only, never their content")

        class Broken(ds.FS):
            def listdir(self, path):
                raise PermissionError(13, "denied (fake)")
        self.assertIsNone(ds.sink_count(self.paths, Broken()), "unreadable is None, distinct from zero")

    def test_a_refusal_is_the_same_file_as_a_fallback_file_in_another_directory(self):
        row = {"id": "a" * 32, "channel": "#alpha", "as": "alp-solver-2", "text": "[status] x", "cont": "",
               "reply_to": None, "at": "2026-10-09T00:00:00", "writer": {"boot": "b", "pid": 4242, "start": "s",
                                                                       "conn": None}, "origin": "post",
               "epoch_seen": 0, "attestation": None}
        sink = ds.publish_fallback(ds.FS(), self.paths, row, sink=True)
        plain = ds.publish_fallback(ds.FS(), self.paths, row)
        self.assertTrue(ds.is_published_name(sink))
        self.assertEqual(json.loads((self.paths.refused / sink).read_text()),
                         json.loads((self.paths.fallback / plain).read_text()))
        self.assertTrue(ds.valid_fallback(json.loads((self.paths.refused / sink).read_text())))
        self.assertEqual(sorted(os.listdir(self.paths.refused_staging)), [], "published, so no tmp file is left")


if __name__ == "__main__":
    unittest.main()
