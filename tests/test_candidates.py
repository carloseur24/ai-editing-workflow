from __future__ import annotations

import unittest

from fenixsoft.domain import candidates


MANIFEST = {"clips": {
    "01_back_cameras.mp4": {
        "duration_sec": 8.0,
        "windows": [
            {"id": "01c-a", "t": 3.6, "dur": 1.6, "score": 0.93},
            {"id": "01c-b", "t": 4.4, "dur": 1.6, "score": 0.88},
        ],
    },
}}


class CandidatesTest(unittest.TestCase):
    def test_ids_are_stable(self):
        self.assertEqual(candidates.ids(MANIFEST), ["01c-a", "01c-b"])

    def test_find_returns_clip_and_window(self):
        win = candidates.find(MANIFEST, "01c-b")
        self.assertIsNotNone(win)
        self.assertEqual(win["clip"], "01_back_cameras.mp4")
        self.assertEqual(win["t"], 4.4)
        self.assertEqual(win["clip_duration_sec"], 8.0)

    def test_unknown_id_is_none(self):
        self.assertIsNone(candidates.find(MANIFEST, "99c-z"))
        self.assertIsNone(candidates.find(None, "01c-a"))

    def test_missing_manifest_is_none(self):
        self.assertIsNone(candidates.load("/nonexistent/sku"))


if __name__ == "__main__":
    unittest.main()
