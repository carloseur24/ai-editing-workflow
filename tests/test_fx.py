from __future__ import annotations

import unittest

import numpy as np

from fenixsoft.stages import fx

SRC = "tests/fixtures/placeholder.mp4"


def spec(**over):
    data = {"input": SRC, "width": 320, "height": 480, "fps": 30,
            "start": 0, "duration": 1,
            "effects": [{"type": "particles", "start": 0, "end": 1,
                         "count": 20, "seed": 4}]}
    data.update(over)
    return data


class FxTest(unittest.TestCase):
    def test_valid_spec(self):
        self.assertEqual(fx.validate(spec()), [])

    def test_unknown_effect_is_rejected(self):
        s = spec(effects=[{"type": "laser", "start": 0, "end": 1}])
        self.assertTrue(any("unknown type" in e for e in fx.validate(s)))

    def test_particles_are_deterministic(self):
        a = fx._make_particles({"count": 10, "seed": 9}, 320, 480)
        b = fx._make_particles({"count": 10, "seed": 9}, 320, 480)
        self.assertEqual(a, b)

    def test_particle_positions_are_stable_at_a_time(self):
        parts = fx._make_particles({"count": 10, "seed": 9}, 320, 480)
        p1 = fx.particle_positions(parts, {"gravity": 100}, 0.5, 320, 480)
        p2 = fx.particle_positions(parts, {"gravity": 100}, 0.5, 320, 480)
        self.assertEqual(p1, p2)
        self.assertEqual(len(p1), 10)

    def test_glitch_changes_pixels(self):
        frame = np.random.default_rng(0).integers(
            0, 255, (120, 160, 3), dtype=np.uint8)
        out = fx._glitch(frame, {"intensity": 0.8, "seed": 1}, 3)
        self.assertEqual(out.shape, frame.shape)
        self.assertFalse(np.array_equal(out, frame))


if __name__ == "__main__":
    unittest.main()
