from __future__ import annotations

import unittest

from fenixsoft.render.motion import plate


class MotionSpecTest(unittest.TestCase):
    def test_unknown_preset_is_rejected(self):
        errors = plate.validate({"width": 64, "height": 64, "fps": 24,
                                 "duration_sec": 1,
                                 "object": {"preset": "dragon"}})
        self.assertTrue(any("preset" in e for e in errors))

    def test_valid_spec_passes(self):
        errors = plate.validate({"width": 64, "height": 64, "fps": 24,
                                 "duration_sec": 1,
                                 "object": {"preset": "sphere"}})
        self.assertEqual(errors, [])

    def test_pair_interpolates_and_holds_scalars(self):
        self.assertAlmostEqual(plate._pair([0, 10], 0.5), 5.0, places=5)
        self.assertAlmostEqual(plate._pair(7.0, 0.5), 7.0, places=5)

    def test_one_frame_has_rgba_shape_and_ink(self):
        spec = {
            "width": 64, "height": 64, "fps": 24, "duration_sec": 1,
            "background": "#000000",
            "object": {"preset": "sphere", "size": 64, "mesh": 24,
                       "raymarch_steps": 4, "center": [0.5, 0.5]},
            "text": [], "grain": 0.0,
        }
        frame = plate.render_frame(spec, 0.5)
        self.assertEqual(frame.shape, (64, 64, 4))
        self.assertEqual(frame.dtype.name, "uint8")
        self.assertGreater(int(frame[:, :, 3].max()), 0)

    def test_transparent_background_starts_clear(self):
        spec = {
            "width": 32, "height": 32, "fps": 24, "duration_sec": 1,
            "background": None,
            "object": {"preset": "sphere", "size": 16, "mesh": 12,
                       "raymarch_steps": 2, "center": [-1.0, -1.0]},
            "text": [], "grain": 0.0,
        }
        frame = plate.render_frame(spec, 0.1)
        self.assertEqual(int(frame[0, 0, 3]), 0)


if __name__ == "__main__":
    unittest.main()
