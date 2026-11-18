from __future__ import annotations

import unittest

from fenixsoft.domain.quality import beat_policy, picture_driven
from fenixsoft.stages.beat_grid import bpm_drift_pct, bpm_warning


class BeatPolicyTest(unittest.TestCase):
    def test_music_is_the_default(self):
        self.assertEqual(beat_policy({}), "music")
        self.assertEqual(beat_policy({"quality": {}}), "music")
        self.assertEqual(beat_policy({"quality": {"beat_policy": "music"}}),
                         "music")
        self.assertFalse(picture_driven({}))

    def test_picture_is_recognised_case_insensitively(self):
        self.assertTrue(picture_driven(
            {"quality": {"beat_policy": " Picture "}}))
        self.assertEqual(beat_policy({"quality": {"beat_policy": "picture"}}),
                         "picture")

    def test_an_unknown_policy_falls_back_to_music(self):
        self.assertEqual(beat_policy({"quality": {"beat_policy": "vibes"}}),
                         "music")
        self.assertFalse(picture_driven(
            {"quality": {"beat_policy": "vibes"}}))


class BpmDriftTest(unittest.TestCase):
    def test_drift_is_a_percentage_of_the_requested_tempo(self):
        self.assertEqual(bpm_drift_pct(120.0, 120.0), 0.0)
        self.assertEqual(bpm_drift_pct(121.2, 120.0), 1.0)
        self.assertAlmostEqual(bpm_drift_pct(118.97, 120.0), 0.858, places=3)

    def test_invalid_input_returns_none(self):
        self.assertIsNone(bpm_drift_pct(0, 0))
        self.assertIsNone(bpm_drift_pct("x", 120))

    def test_a_drift_past_tolerance_warns(self):
        grid = {"bpm": 122.0, "config_bpm": 120.0, "bpm_drift_pct": 1.67}
        warning = bpm_warning(grid, tolerance=1.5)
        self.assertIsNotNone(warning)
        self.assertIn("122.00 BPM", warning)
        self.assertIn("120.0 BPM", warning)
        self.assertIn("tolerance 1.50%", warning)

    def test_a_drift_inside_tolerance_is_quiet(self):
        grid = {"bpm": 120.5, "config_bpm": 120.0, "bpm_drift_pct": 0.42}
        self.assertIsNone(bpm_warning(grid, tolerance=1.5))

    def test_a_grid_without_a_requested_bpm_is_quiet(self):
        self.assertIsNone(bpm_warning({"bpm": 120.0}, tolerance=1.5))


if __name__ == "__main__":
    unittest.main()
