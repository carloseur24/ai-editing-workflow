from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fenixsoft.domain import labeling
from fenixsoft.ops import label


class SlugAndNameTest(unittest.TestCase):
    def test_slugify_folds_accents_and_punctuation(self):
        self.assertEqual(labeling.slugify("iPhone 14 · Azul — 128 GB"), "iphone-14-azul-128-gb")
        self.assertEqual(labeling.slugify("  ¿Caja?  "), "caja")
        self.assertEqual(labeling.slugify("A//B__C"), "a-b-c")

    def test_target_name_keeps_source_suffix(self):
        self.assertEqual(labeling.target_name("Galaxy S25 Ultra / Caja", ".mp4"),
                         "galaxy-s25-ultra-caja.mp4")
        self.assertEqual(labeling.target_name("ya-tiene.mp4", ".mp4"), "ya-tiene.mp4")

    def test_validate_rejects_windows_hazards(self):
        self.assertTrue(labeling.validate_filename("CON.mp4"))
        self.assertTrue(labeling.validate_filename("bad:name.mp4"))
        self.assertTrue(labeling.validate_filename("trailing."))
        self.assertTrue(labeling.validate_filename(""))
        self.assertEqual(labeling.validate_filename("galaxy-s25-ultra-caja.mp4"), [])

    def test_shot_fractions_are_inside_the_buckets(self):
        self.assertEqual(labeling.shot_fractions(4), [0.125, 0.375, 0.625, 0.875])


class RenamePlanTest(unittest.TestCase):
    def test_collision_with_existing_file_is_an_error(self):
        plan, errors = labeling.build_rename_plan(
            [{"file": "a.mp4", "name": "taken"}], ["a.mp4", "taken.mp4"])
        self.assertEqual(plan, [])
        self.assertTrue(errors and "already exists" in errors[0])

    def test_two_entries_cannot_share_a_target(self):
        plan, errors = labeling.build_rename_plan(
            [{"file": "a.mp4", "name": "mismo"}, {"file": "b.mp4", "name": "Mismo"}],
            ["a.mp4", "b.mp4"])
        self.assertEqual(len(plan), 1)
        self.assertTrue(errors and "duplicate target" in errors[0])

    def test_sources_may_free_a_name_for_each_other(self):
        plan, errors = labeling.build_rename_plan(
            [{"file": "a.mp4", "name": "b"}, {"file": "b.mp4", "name": "a"}],
            ["a.mp4", "b.mp4"])
        self.assertEqual(errors, [])
        self.assertEqual({row["name"] for row in plan}, {"a.mp4", "b.mp4"})

    def test_empty_name_is_skipped(self):
        plan, errors = labeling.build_rename_plan(
            [{"file": "a.mp4", "name": ""}], ["a.mp4"])
        self.assertEqual((plan, errors), ([], []))


class DuplicateTest(unittest.TestCase):
    def test_identical_fingerprints_match(self):
        a = {"index": 1, "kind": "video", "duration": 6.0, "hashes": [1, 2, 3, 4]}
        b = {"index": 2, "kind": "video", "duration": 6.0, "hashes": [1, 2, 3, 4]}
        self.assertEqual(labeling.duplicate_pairs([a, b]), [(1, 2)])

    def test_one_different_frame_breaks_the_match(self):
        a = {"index": 1, "kind": "video", "duration": 6.0, "hashes": [1, 2, 3, 4]}
        b = {"index": 2, "kind": "video", "duration": 6.0, "hashes": [1, 2, 0, 4]}
        self.assertEqual(labeling.duplicate_pairs([a, b], threshold=0), [])

    def test_duration_gap_prevents_false_positives(self):
        a = {"index": 1, "kind": "video", "duration": 6.0, "hashes": [1, 2, 3, 4]}
        b = {"index": 2, "kind": "video", "duration": 28.0, "hashes": [1, 2, 3, 4]}
        self.assertEqual(labeling.duplicate_pairs([a, b]), [])

    def test_duplicate_root_prefers_the_older_capture(self):
        items = [{"index": 1, "mtime": 200}, {"index": 2, "mtime": 100},
                 {"index": 3, "mtime": 300}]
        self.assertEqual(labeling.duplicate_roots(items, [(1, 2), (2, 3)]), {1: 2, 3: 2})


class ApplyUndoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.work = self.folder / label.WORK_DIR
        self.work.mkdir()
        for name in ("a.mp4", "b.mp4", "c.jpg"):
            (self.folder / name).write_bytes(b"x")
        scan = {
            "files": [
                {"index": 1, "file": "a.mp4", "kind": "video"},
                {"index": 2, "file": "b.mp4", "kind": "video"},
                {"index": 3, "file": "c.jpg", "kind": "image"},
            ]
        }
        (self.work / "scan.json").write_text(json.dumps(scan))
        (self.work / "names.tsv").write_text("01\tGalaxy S25 Ultra caja\n02\t\n03\tFoto\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _args(self, **overrides):
        base = dict(folder=str(self.folder), work=str(self.work), names=None,
                    config=None, dry_run=False, exact=False, json=False)
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_apply_renames_and_writes_manifest(self):
        self.assertEqual(label.apply(self._args()), 0)
        names = sorted(p.name for p in self.folder.iterdir())
        self.assertIn("galaxy-s25-ultra-caja.mp4", names)
        self.assertIn("foto.jpg", names)
        self.assertIn("b.mp4", names)  # empty name -> untouched
        manifests = list(self.work.glob("renames-*.csv"))
        self.assertEqual(len(manifests), 1)
        with manifests[0].open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["file"], "a.mp4")
        self.assertEqual(rows[0]["name"], "galaxy-s25-ultra-caja.mp4")
        scan = json.loads((self.work / "scan.json").read_text())
        self.assertEqual(scan["files"][0]["file"], "galaxy-s25-ultra-caja.mp4")

    def test_apply_is_dry_with_dry_run(self):
        self.assertEqual(label.apply(self._args(dry_run=True)), 0)
        self.assertTrue((self.folder / "a.mp4").exists())
        self.assertFalse((self.folder / "galaxy-s25-ultra-caja.mp4").exists())

    def test_undo_restores_original_names(self):
        label.apply(self._args())
        self.assertEqual(label.undo(SimpleNamespace(
            folder=str(self.folder), work=str(self.work), manifest=None)), 0)
        names = sorted(p.name for p in self.folder.iterdir() if p.name != label.WORK_DIR)
        self.assertEqual(names, ["a.mp4", "b.mp4", "c.jpg"])

    def test_collision_aborts_without_partial_renames(self):
        (self.folder / "ocupado.mp4").write_bytes(b"x")
        (self.work / "names.tsv").write_text("01\tocupado\n")
        with self.assertRaises(SystemExit):
            label.apply(self._args())
        self.assertTrue((self.folder / "a.mp4").exists())


if __name__ == "__main__":
    unittest.main()
