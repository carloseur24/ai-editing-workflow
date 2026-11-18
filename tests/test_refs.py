from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fenixsoft.domain import runs

SKU = "refsku"
RUN = f"{SKU}_20261118-000000"


def build_root(root: Path) -> dict[str, Path]:
    run_dir = root / "output" / SKU / RUN
    run_dir.mkdir(parents=True)
    video = run_dir / f"{RUN}_final.mp4"
    video.write_bytes(b"video")
    plan = run_dir / "edit_plan.json"
    plan.write_text("{}", encoding="utf-8")
    (root / "output" / SKU / "runs.json").write_text(json.dumps({
        "sku": SKU,
        "runs": [{"run": RUN, "video": str(video)}],
    }), encoding="utf-8")

    motion = root / "output" / SKU / "motion" / f"{SKU}_20261118-000001"
    motion.mkdir(parents=True)
    plate = motion / "plate.mov"
    plate.write_bytes(b"plate")
    (motion / "manifest.json").write_text("{}", encoding="utf-8")

    track = root / "output" / SKU / "track"
    track.mkdir(parents=True)
    track_json = track / "track_20261118.json"
    track_json.write_text("{}", encoding="utf-8")
    return {"video": video, "plan": plan, "plate": plate,
            "track": track_json}


class StableRefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = build_root(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_latest_resolves_the_newest_video(self):
        got = runs.resolve_stable_ref("@render/latest", SKU, self.root)
        self.assertEqual(got, self.paths["video"])

    def test_render_latest_can_name_a_file_inside_the_run(self):
        got = runs.resolve_stable_ref("@render/latest/edit_plan.json",
                                      SKU, self.root)
        self.assertEqual(got, self.paths["plan"])

    def test_motion_latest_prefers_the_plate(self):
        self.assertEqual(
            runs.resolve_stable_ref("@motion/latest", SKU, self.root),
            self.paths["plate"])
        self.assertEqual(
            runs.resolve_stable_ref("@motion/latest/plate.mov", SKU,
                                    self.root),
            self.paths["plate"])

    def test_track_latest_resolves_the_json(self):
        self.assertEqual(
            runs.resolve_stable_ref("@track/latest", SKU, self.root),
            self.paths["track"])

    def test_explicit_paths_are_not_stable_refs(self):
        self.assertFalse(runs.is_stable_ref("input/a/clips/a.mp4"))
        self.assertIsNone(
            runs.resolve_stable_ref("input/a/clips/a.mp4", SKU, self.root))

    def test_missing_stage_and_bad_form_return_none(self):
        self.assertIsNone(
            runs.resolve_stable_ref("@comp/latest", SKU, self.root))
        self.assertIsNone(runs.resolve_stable_ref("@render", SKU, self.root))
        self.assertIsNone(
            runs.resolve_stable_ref("@render/latest/nope.json", SKU, self.root))


if __name__ == "__main__":
    unittest.main()
