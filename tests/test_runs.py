from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fenixsoft.domain import runs


class RunNamingTest(unittest.TestCase):
    def test_sku_of_run(self):
        self.assertEqual(runs.sku_of_run("demo_iphone14_blue_20261115-185938"),
                         "demo_iphone14_blue")
        self.assertEqual(runs.sku_of_run("demo_iphone14_blue_20261115-185938-2"),
                         "demo_iphone14_blue")

    def test_run_video_prefers_final_mp4(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "sku_20260101-000000"
            run_dir.mkdir()
            expected = run_dir / f"{run_dir.name}_final.mp4"
            self.assertEqual(runs.run_video(run_dir), expected)
            expected.write_bytes(b"x")
            self.assertEqual(runs.run_video(run_dir), expected)


class RunIndexTest(unittest.TestCase):
    def test_new_run_dir_never_collides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            when = datetime(2026, 1, 1, 12, 0, 0)
            first = runs.new_run_dir("sku", root=root, when=when)
            second = runs.new_run_dir("sku", root=root, when=when)
            self.assertEqual(first.name, "sku_20260101-120000")
            self.assertEqual(second.name, "sku_20260101-120000-2")
            self.assertTrue(first.is_dir() and second.is_dir())

    def test_register_run_updates_index_and_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = runs.new_run_dir("sku", root=root)
            video = run_dir / f"{run_dir.name}_final.mp4"
            video.write_bytes(b"x")
            runs.register_run("sku", run_dir, video, root=root)
            index = json.loads((root / "output" / "sku" / "runs.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual(index["runs"][0]["run"], run_dir.name)
            latest = json.loads((root / "output" / "sku" / "latest.json")
                                .read_text(encoding="utf-8"))
            self.assertEqual(latest["run"], run_dir.name)

    def test_legacy_flat_render_is_not_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "output" / "sku"
            base.mkdir(parents=True)
            (base / "sku_final.mp4").write_bytes(b"x")
            self.assertEqual(runs.list_runs("sku", root=root), [])


if __name__ == "__main__":
    unittest.main()
