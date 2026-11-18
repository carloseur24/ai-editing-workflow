from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fenixsoft.domain import preview as pv
from fenixsoft.domain import runs


def cut(index: int, t0: float, dur: float, clip: str = "a.mp4") -> dict:
    return {"index": index, "t0": t0, "t1": t0 + dur, "dur": dur,
            "clip": clip, "in": 2.0, "line": index}


CUTS = [cut(0, 0.0, 4.0), cut(1, 4.0, 6.0, "b.mp4")]
CARDS = [
    {"text": "TITLE", "sub": "SPEC", "start": 1.0, "end": 3.0,
     "enter_sec": 0.45, "size": 86},
    {"text": "END", "sub": "", "start": 7.0, "end": 9.0,
     "enter_sec": 0.30, "size": 86},
]


class SamplePlanTest(unittest.TestCase):
    def test_one_still_per_cut_midpoint(self):
        samples = pv.sample_plan(CUTS, [], duration=10.0)
        self.assertEqual(len(samples), 2)
        self.assertEqual([s["t"] for s in samples], [2.0, 7.0])
        self.assertEqual([s["kind"] for s in samples], ["cut", "cut"])

    def test_source_time_is_relative_to_the_cut(self):
        sample = pv.sample_plan([CUTS[1]], [], duration=10.0)[0]
        # cut 1 starts at 4.0 from source in=2.0; midpoint 7.0 -> 5.0
        self.assertEqual(sample["t_src"], 5.0)

    def test_card_samples_use_the_first_visible_frame(self):
        samples = pv.sample_plan(CUTS, CARDS, duration=10.0)
        cards = [s for s in samples if s["kind"] == "card"]
        self.assertEqual(len(cards), 2)
        # start + enter_sec, not the raw start where alpha is zero
        self.assertAlmostEqual(cards[0]["t"], 1.45, places=3)
        self.assertAlmostEqual(cards[1]["t"], 7.30, places=3)

    def test_a_card_on_a_cut_midpoint_is_deduplicated(self):
        cards = [{"text": "ON CUT", "start": 1.8, "end": 2.5,
                  "enter_sec": 0.0}]
        samples = pv.sample_plan(CUTS, cards, duration=10.0)
        self.assertEqual(len([s for s in samples if abs(s["t"] - 2.0) < 0.05]),
                         1)
        kept = next(s for s in samples if abs(s["t"] - 2.0) < 0.05)
        self.assertEqual(kept["kind"], "cut")

    def test_max_stills_keeps_every_cut_first(self):
        cuts = [cut(i, i * 2.0, 2.0) for i in range(6)]
        cards = [{"text": f"C{i}", "start": i * 2.0 + 0.5,
                  "end": i * 2.0 + 1.5, "enter_sec": 0.2} for i in range(6)]
        samples = pv.sample_plan(cuts, cards, duration=12.0, max_stills=8)
        self.assertEqual(len(samples), 8)
        self.assertEqual(sum(1 for s in samples if s["kind"] == "cut"), 6)

    def test_empty_cuts_produce_no_samples(self):
        self.assertEqual(pv.sample_plan([], CARDS), [])

    def test_cut_at_covers_the_last_frame(self):
        self.assertIs(pv.cut_at(CUTS, 4.0), CUTS[1])
        self.assertIs(pv.cut_at(CUTS, 99.0), CUTS[-1])
        self.assertIs(pv.cut_at(CUTS, 0.0), CUTS[0])


class TimestampedDirTest(unittest.TestCase):
    def test_same_second_gets_a_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "preview"
            first = runs.new_timestamped_dir(base)
            second = runs.new_timestamped_dir(base)
            self.assertTrue(first.is_dir())
            self.assertNotEqual(first, second)
            self.assertTrue(second.name.endswith("-2"))

    def test_preview_dir_is_not_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = runs.new_timestamped_dir(Path(tmp) / "preview")
            self.assertFalse(runs.is_run_dir(path))


class EvidencePathTest(unittest.TestCase):
    cfg = {"delivery": {"dir": "/tmp/shelf", "downloads": "/tmp/downloads"}}

    def test_a_local_run_keeps_its_report_next_to_the_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "sku_20260101-000000" / "sku_final.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"x")
            self.assertEqual(runs.evidence_path(video, self.cfg),
                             video.parent / "qa_report.json")

    def test_a_delivered_master_never_writes_the_shelf(self):
        video = Path("/tmp/shelf/sku_20260101-000000/sku_final.mp4")
        path = runs.evidence_path(video, self.cfg)
        self.assertNotIn(str(path), str(Path("/tmp/shelf")))
        self.assertIn("output", str(path))
        self.assertIn("qa", str(path))

    def test_a_delivered_master_uses_the_local_run_when_it_exists(self):
        from fenixsoft.context import OUTPUT_DIR
        run = "sku_20260101-000000"
        local = OUTPUT_DIR / "sku" / run
        local.mkdir(parents=True, exist_ok=True)
        try:
            expected = local / "qa_report.json"
            self.assertEqual(
                runs.evidence_path(Path("/tmp/shelf") / run / "sku_final.mp4",
                                   self.cfg),
                expected)
            self.assertTrue(runs.is_on_delivery_shelf(
                Path("/tmp/shelf") / run / "sku_final.mp4", self.cfg))
            self.assertFalse(runs.is_on_delivery_shelf(
                Path("/tmp/elsewhere") / run / "sku_final.mp4", self.cfg))
        finally:
            (local / "qa_report.json").unlink(missing_ok=True)
            local.rmdir()

    def test_delivery_roots_resolve_relative_paths(self):
        from fenixsoft.context import REPO_ROOT
        roots = runs.delivery_roots({"delivery": {"dir": "output/delivery"}})
        self.assertEqual(roots, [(REPO_ROOT / "output/delivery").resolve()])


class PreviewManifestShapeTest(unittest.TestCase):
    def test_manifest_is_json_serializable(self):
        manifest = {"kind": "render", "frames": pv.sample_plan(CUTS, CARDS),
                     "music_only": False}
        json.dumps(manifest)


if __name__ == "__main__":
    unittest.main()
