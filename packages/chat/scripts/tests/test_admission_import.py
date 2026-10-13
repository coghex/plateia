"""The two import sources (docs/writer_admission.md section 5; the review's addition A4): after release the bridge
imports the refusal sink refused.d/ as it imports outbox.d/, by entry id under H1, idempotently. The sink keeps its
own source identity (a file is known by its directory and name), bounded import fairness holds across both
sources, and an incomplete or unreadable listing never advances the import pass or invalidates a retained
handoff (import_epoch). Invented accounts, channels and entry ids only."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _isolation  # noqa: E402,F401  (first: sandbox home and live-state guard)
from admission_fakes import HeldCase  # noqa: E402
from outbox_fakes import CLIENT_PID, T0  # noqa: E402

SINK = "refused.d/"
WRITER = {"boot": "test-boot", "pid": CLIENT_PID, "start": "start-1", "conn": None}


class ImportCase(HeldCase):
    def setUp(self):
        super().setUp()
        self.importer = self.ds.Importer(self.authority)

    def row(self, entry_id, text="[status] x", **more):
        return dict({"id": entry_id, "channel": "#alpha", "as": "alp-solver-2", "text": text, "cont": "",
                     "reply_to": None, "at": "2026-10-09T00:00:00", "writer": WRITER, "origin": "post",
                     "epoch_seen": 0, "attestation": None}, **more)

    def put(self, directory, name, row=None, raw=None):
        """A published fallback file under outbox.d/ or refused.d/, by hand, with the name given."""
        d = self.paths.refused if directory == "sink" else self.paths.fallback
        d.mkdir(parents=True, exist_ok=True)
        data = raw if raw is not None else (json.dumps(dict({"v": "outbox/2"}, **row), sort_keys=True) + "\n").encode()
        (d / name).write_bytes(data)
        return d / name

    def publish(self, directory, row, t):
        return self.ds.publish_fallback(self.ds.FS(), self.paths, row, clock=lambda: T0 + t,
                                        sink=directory == "sink")

    @staticmethod
    def name(n, entry_id, tag="aaaaaaaa"):
        return f"{n:020d}-{entry_id}-{tag}.json"

    def receipts(self):
        return {r["name"]: r for r in self.rows("SELECT * FROM imports ORDER BY name")}

    def held(self):
        return self.rows("SELECT file, why FROM held ORDER BY file")

    def epoch(self):
        return int(self.rows("SELECT value FROM meta WHERE name = 'import_epoch'")[0]["value"])

    def files(self, directory):
        d = self.paths.refused if directory == "sink" else self.paths.fallback
        return sorted(n for n in os.listdir(d) if self.ds.is_published_name(n)) if d.exists() else []


class SourceIdentityTests(ImportCase):
    def test_a_sink_file_is_imported_after_release_keyed_by_its_source_and_name(self):
        name = self.name(1, "a" * 32)
        self.put("sink", name, self.row("a" * 32))
        self.assertEqual(self.importer.import_fallback_files(), 1)
        self.assertEqual(self.files("sink"), [], "removed once imported")
        self.assertEqual(list(self.receipts()), [SINK + name], "its receipt names its source")
        self.assertEqual([e["origin"] for e in self.rows("SELECT * FROM entries")], ["post"])
        self.assertEqual(self.rows("SELECT owner_kind, delayed FROM entries"), [{"owner_kind": "outbox", "delayed": 1}])

    def test_the_two_files_of_one_entry_id_are_one_obligation_and_two_receipts(self):
        for first, second in (("fallback", "sink"), ("sink", "fallback")):
            with self.subTest(first=first):
                self.fresh()
                self.importer = self.ds.Importer(self.authority)
                self.put(first, self.name(1, "a" * 32, "11111111"), self.row("a" * 32))
                self.put(second, self.name(2, "a" * 32, "22222222"), self.row("a" * 32))
                self.assertEqual(self.importer.import_fallback_files(), 2)
                self.assertEqual(len(self.rows("SELECT id FROM entries")), 1, "deduplicated by entry id")
                self.assertEqual(sorted(r["name"] for r in self.receipts().values()),
                                 sorted([self.name(1 if first == "fallback" else 2, "a" * 32,
                                                   "11111111" if first == "fallback" else "22222222"),
                                         SINK + self.name(2 if first == "fallback" else 1, "a" * 32,
                                                          "22222222" if first == "fallback" else "11111111")]))
                self.assertEqual((self.files("fallback"), self.files("sink")), ([], []))
                self.assertEqual(self.held(), [])

    def test_identical_basenames_in_the_two_directories_are_two_files_with_two_receipts(self):
        same = self.name(1, "a" * 32)
        self.put("fallback", same, self.row("a" * 32, "[status] from outbox.d"))
        self.put("sink", same, self.row("a" * 32, "[status] from the sink, other bytes"))
        self.assertEqual(self.importer.import_fallback_files(), 2)
        self.assertEqual(sorted(self.receipts()), sorted([same, SINK + same]))
        self.assertEqual(self.held(), [], "the second is not 'changed after import began': it is another file")
        self.assertEqual((self.files("fallback"), self.files("sink")), ([], []))
        [entry] = self.rows("SELECT text FROM entries")
        self.assertEqual(entry["text"], "[status] from outbox.d", "the first applied wins; the entry is one")

    def test_identical_basenames_with_different_entry_ids_are_two_obligations(self):
        same = self.name(1, "x" * 32)
        self.put("fallback", same, self.row("a" * 32, "[status] one"))
        self.put("sink", same, self.row("b" * 32, "[status] two"))
        self.importer.import_fallback_files()
        self.assertEqual(sorted(r["id"] for r in self.rows("SELECT id FROM entries")), ["a" * 32, "b" * 32])

    def test_repeated_imports_change_nothing_and_a_file_that_reappears_is_removed_by_its_receipt(self):
        name = self.name(1, "a" * 32)
        path = self.put("sink", name, self.row("a" * 32))
        raw = path.read_bytes()
        self.importer.import_fallback_files()
        before = (self.rows("SELECT * FROM entries"), self.receipts())
        for _ in range(3):
            self.assertEqual(self.importer.import_fallback_files(), 0)
        self.put("sink", name, raw=raw)  # a failed removal left it, or it was linked again: same bytes
        self.assertEqual(self.importer.import_fallback_files(), 1)
        self.assertEqual(self.files("sink"), [], "removed on its receipt")
        self.assertEqual(self.rows("SELECT * FROM entries"), before[0], "no second obligation")
        self.assertEqual({k: v["state"] for k, v in self.receipts().items()}, {SINK + name: "closed"})

    def test_a_sink_file_rewritten_after_its_import_is_held_by_its_bytes_and_kept(self):
        name = self.name(1, "a" * 32)
        self.put("sink", name, self.row("a" * 32))
        self.importer.import_fallback_files()
        self.put("sink", name, self.row("a" * 32, "[status] rewritten"))
        self.importer.import_fallback_files()
        self.assertEqual([h["file"] for h in self.held()], [SINK + name])
        self.assertIn("changed after import began", self.held()[0]["why"])
        self.assertEqual(self.files("sink"), [], "its exact bytes are held, so it is removed")

    def test_an_invalid_sink_file_is_held_under_its_source_and_loses_nothing(self):
        name = self.name(1, "a" * 32)
        self.put("sink", name, raw=b'{"v": "outbox/2", "id": 7}\n')
        self.importer.import_fallback_files()
        self.assertEqual([(h["file"], h["why"]) for h in self.held()], [(SINK + name, "invalid fallback file")])
        self.assertEqual(self.rows("SELECT id FROM entries"), [])
        self.assertEqual(self.alerts()[0]["alert_key"], f"held:{SINK}{name}")


class ListingTests(ImportCase):
    """An incomplete or unreadable listing never advances the pass, and so never invalidates a retained
    handoff: H1 reopens an abandoned entry only while import_epoch <= its abandon_epoch + 1."""

    def abandoned(self, entry_id="a" * 32):
        """An entry its writer abandoned (E8) at epoch 0, whose handoff a file still carries."""
        self.tx(lambda s: s.create(entry_id, {"channel": "#alpha", "as": "alp-solver-2", "text": "[status] x"},
                                   [{"kind": "line", "lines": [["[status] x", False]], "text": "[status] x"}],
                                   WRITER, "post"))
        self.tx(lambda s: s.con.execute("UPDATE entries SET state = 'abandoned', abandon_epoch = 0 WHERE id = ?",
                                        (entry_id,)))

    def unreadable(self, directory):
        real = self.authority.fs.listdir
        target = str(self.paths.refused if directory == "sink" else self.paths.fallback)

        def listdir(path):
            if str(path) == target:
                raise PermissionError(13, "denied (fake)")
            return real(path)
        return mock.patch.object(self.authority.fs, "listdir", listdir)

    def test_an_unreadable_sink_never_completes_a_pass_and_the_retained_handoff_survives(self):
        self.abandoned()
        name = self.name(1, "a" * 32)
        self.put("sink", name, self.row("a" * 32))
        with self.unreadable("sink"):
            for _ in range(4):  # each flush would complete a pass, if outbox.d alone were enough
                self.importer.import_fallback_files()
        self.assertEqual(self.epoch(), 0, "no pass completed while a source could not be listed")
        self.assertEqual(self.files("sink"), [name])
        self.assertEqual(self.alerts()[0]["alert_key"], "held:refused.d:unreadable")
        self.importer.import_fallback_files()  # the listing works again
        self.assertEqual(self.held(), [], "H1's guard still holds: epoch 0 <= abandon_epoch + 1")
        self.assertEqual(self.rows("SELECT state, owner_kind FROM entries"), [{"state": "open", "owner_kind": "outbox"}])
        self.assertEqual(self.epoch(), 1, "and the pass completes, once")

    def test_an_unreadable_outbox_d_has_the_same_effect_and_the_sink_is_still_imported(self):
        self.put("sink", self.name(1, "a" * 32), self.row("a" * 32))
        with self.unreadable("fallback"):
            for _ in range(3):
                self.importer.import_fallback_files()
        self.assertEqual(self.epoch(), 0)
        self.assertEqual(self.files("sink"), [], "the readable source went on")
        self.assertEqual(len(self.rows("SELECT id FROM entries")), 1)

    def test_a_listing_that_fails_after_the_files_were_imported_completes_no_pass(self):
        self.put("sink", self.name(1, "a" * 32), self.row("a" * 32))
        real = self.authority.fs.listdir
        calls = {"sink": 0}

        def listdir(path):
            if str(path) == str(self.paths.refused):
                calls["sink"] += 1
                if calls["sink"] == 2:  # the re-listing that decides whether the pass is complete
                    raise PermissionError(13, "denied (fake)")
            return real(path)
        with mock.patch.object(self.authority.fs, "listdir", listdir):
            self.importer.import_fallback_files()
        self.assertEqual(self.epoch(), 0, "an incomplete listing is not an empty directory")
        self.importer.import_fallback_files()
        self.assertEqual(self.epoch(), 1)

    def test_a_pass_completes_only_when_every_file_it_listed_is_gone_from_both_sources(self):
        for i in range(3):
            self.put("sink", self.name(i, f"{i}" * 32), self.row(f"{i}" * 32))
        self.put("fallback", self.name(9, "f" * 32), self.row("f" * 32))
        with mock.patch.object(self.ds, "IMPORT_MAX", 2):
            epochs = []
            for _ in range(4):
                self.importer.import_fallback_files()
                epochs.append(self.epoch())
        # incomplete while a listed file remains; complete when the last went; and, as in #19, an empty
        # listing completes a pass at every flush (which is why an unreadable one must never read as empty)
        self.assertEqual(epochs, [0, 1, 2, 3])
        self.assertEqual((self.files("fallback"), self.files("sink")), ([], []))


class FairnessTests(ImportCase):
    def test_at_most_import_max_files_per_flush_oldest_name_first_across_both_sources(self):
        names = []
        for i in range(8):
            directory = "sink" if i % 2 else "fallback"
            names.append((directory, self.publish(directory, self.row(f"{i:032d}"), t=i)))
        with mock.patch.object(self.ds, "IMPORT_MAX", 3):
            first = self.importer.import_fallback_files()
            self.assertEqual(first, 3)
            left = {d: self.files(d) for d in ("fallback", "sink")}
            self.assertEqual(sum(len(v) for v in left.values()), 5)
            for directory, name in names[:3]:
                self.assertNotIn(name, left[directory], "the three oldest names, whichever source holds them")
            for directory, name in names[3:]:
                self.assertIn(name, left[directory])
            self.assertEqual(self.importer.import_fallback_files(), 3)
            self.assertEqual(self.importer.import_fallback_files(), 2)
        self.assertEqual(len(self.rows("SELECT id FROM entries")), 8)

    def test_one_unreadable_file_is_kept_and_blocks_no_other_in_either_source(self):
        bad = self.publish("sink", self.row("b" * 32), t=1)
        self.publish("fallback", self.row("a" * 32), t=2)
        self.publish("sink", self.row("c" * 32), t=3)
        real = self.authority.fs.read

        def read(path):
            if Path(path).name == bad:
                raise OSError(5, "input/output error (fake)")
            return real(path)
        with mock.patch.object(self.authority.fs, "read", read):
            self.assertEqual(self.importer.import_fallback_files(), 2)
        self.assertEqual((self.files("fallback"), self.files("sink")), ([], [bad]))
        self.assertEqual({a["alert_key"] for a in self.alerts()}, {f"held:{SINK}{bad}:unreadable"})
        self.assertEqual(self.importer.kept, [SINK + bad])
        self.importer.import_fallback_files()  # tried again at a later flush, and imported when it can be read
        self.assertEqual(self.files("sink"), [])


class StagingTests(ImportCase):
    def test_the_sinks_tmp_directory_is_cleaned_like_outbox_ds(self):
        self.paths.refused_staging.mkdir(parents=True, exist_ok=True)
        self.paths.staging.mkdir(parents=True, exist_ok=True)
        for directory in (self.paths.staging, self.paths.refused_staging):
            (directory / "x.5555.x.abcdef.tmp").write_bytes(b"a writer that is gone")
            (directory / "x.6666.x.abcdef.tmp").write_bytes(b"a writer still running")
        self.probes.world["gone"].add(5555)
        self.importer.clean_staging()
        for directory in (self.paths.staging, self.paths.refused_staging):
            self.assertEqual(sorted(os.listdir(directory)), ["x.6666.x.abcdef.tmp"], str(directory))

    def test_a_published_sink_file_leaves_no_tmp_copy(self):
        self.publish("sink", self.row("a" * 32), t=1)
        self.assertEqual(os.listdir(self.paths.refused_staging), [])
        self.importer.clean_staging()
        self.assertEqual(os.listdir(self.paths.refused_staging), [])


if __name__ == "__main__":
    unittest.main()
