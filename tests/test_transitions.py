from __future__ import annotations

import unittest

import numpy as np

from fenixsoft.render import transitions as tr


def frame(h: int = 4, w: int = 6, value: int = 0) -> np.ndarray:
    return np.full((h, w, 3), value, dtype=np.uint8)


class BlendTest(unittest.TestCase):
    def test_dissolve_endpoints_and_midpoint(self):
        a, b = frame(value=0), frame(value=200)
        self.assertTrue(np.array_equal(tr.blend_frames(a, b, 0.0), a))
        self.assertTrue(np.array_equal(tr.blend_frames(a, b, 1.0), b))
        mid = tr.blend_frames(a, b, 0.5)
        self.assertTrue(np.all(mid == 100))

    def test_progress_is_clamped(self):
        a, b = frame(value=10), frame(value=30)
        self.assertTrue(np.array_equal(tr.blend_frames(a, b, -2), a))
        self.assertTrue(np.array_equal(tr.blend_frames(a, b, 3), b))

    def test_fadeblack_dips_through_black(self):
        a, b = frame(value=120), frame(value=240)
        self.assertTrue(np.array_equal(tr.blend_frames(a, b, 0.0, "fadeblack"), a))
        self.assertTrue(np.array_equal(tr.blend_frames(a, b, 1.0, "fadeblack"), b))
        self.assertTrue(np.all(tr.blend_frames(a, b, 0.5, "fadeblack") == 0))
        quarter = tr.blend_frames(a, b, 0.25, "fadeblack")
        self.assertTrue(np.all(quarter == 60))

    def test_wipes_use_a_hard_edge(self):
        a, b = frame(h=4, w=8, value=0), frame(h=4, w=8, value=255)
        left = tr.blend_frames(a, b, 0.5, "wipeleft")
        # Incoming frame covers the right half; no blended values.
        self.assertTrue(np.all(left[:, :4] == 0))
        self.assertTrue(np.all(left[:, 4:] == 255))
        right = tr.blend_frames(a, b, 0.5, "wiperight")
        self.assertTrue(np.all(right[:, :4] == 255))
        self.assertTrue(np.all(right[:, 4:] == 0))
        up = tr.blend_frames(a, b, 0.5, "wipeup")
        self.assertTrue(np.all(up[:2] == 0))
        self.assertTrue(np.all(up[2:] == 255))
        down = tr.blend_frames(a, b, 0.5, "wipedown")
        self.assertTrue(np.all(down[:2] == 255))
        self.assertTrue(np.all(down[2:] == 0))

    def test_full_progress_is_the_incoming_frame_for_every_kind(self):
        a, b = frame(value=5), frame(value=250)
        for kind in tr.TRANSITIONS:
            self.assertTrue(np.array_equal(tr.blend_frames(a, b, 1.0, kind), b),
                            kind)

    def test_shape_mismatch_raises(self):
        with self.assertRaises(ValueError):
            tr.blend_frames(frame(4, 6), frame(4, 5), 0.5)

    def test_invalid_kind_raises(self):
        with self.assertRaises(ValueError):
            tr.blend_frames(frame(), frame(), 0.5, "spin")


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.cuts = [
            {"clip": "a.mp4", "in": 0, "dur": 4.0, "t0": 0.0},
            {"clip": "b.mp4", "in": 0, "dur": 3.0, "t0": 4.0},
            {"clip": "c.mp4", "in": 0, "dur": 2.0, "t0": 7.0},
        ]

    def test_first_boundary_is_skipped(self):
        plan = tr.transition_plan(self.cuts)
        self.assertEqual(len(plan), 2)
        self.assertEqual([p["at"] for p in plan], [4.0, 7.0])
        self.assertEqual([p["kind"] for p in plan], ["dissolve"] * 2)
        self.assertEqual(plan[0]["from_index"], 0)
        self.assertEqual(plan[0]["to_index"], 1)

    def test_default_kind_and_cut_override(self):
        cuts = [dict(c) for c in self.cuts]
        cuts[1]["transition"] = "cut"
        cuts[2]["transition"] = "wipeleft"
        plan = tr.transition_plan(cuts, default="fadeblack")
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["kind"], "wipeleft")
        self.assertEqual(plan[0]["at"], 7.0)

    def test_all_cut_plans_nothing(self):
        self.assertEqual(tr.transition_plan(self.cuts, default="cut"), [])

    def test_clamped_to_the_shorter_neighbour(self):
        cuts = [
            {"clip": "a.mp4", "in": 0, "dur": 4.0, "t0": 0.0},
            {"clip": "b.mp4", "in": 0, "dur": 0.4, "t0": 4.0},
            {"clip": "c.mp4", "in": 0, "dur": 4.0, "t0": 4.4},
        ]
        plan = tr.transition_plan(cuts, sec=0.35)
        # 0.4 s shot -> at most half of it on each side of the boundary.
        self.assertAlmostEqual(plan[0]["sec"], 0.2)
        cuts[1]["transition_sec"] = 0.02
        self.assertAlmostEqual(tr.transition_plan(cuts)[0]["sec"], 0.02)

    def test_invalid_kind_raises(self):
        cuts = [dict(c) for c in self.cuts]
        cuts[1]["transition"] = "dissolv"
        with self.assertRaises(ValueError):
            tr.transition_plan(cuts)

    def test_no_cuts(self):
        self.assertEqual(tr.transition_plan([]), [])

    def test_xfade_filter_maps_dissolve_to_fade(self):
        step = tr.xfade_filter("dissolve", 0.35, 5.5)
        self.assertIn("transition=fade:", step)
        self.assertIn("duration=0.350", step)
        self.assertIn("offset=5.500", step)
        with self.assertRaises(ValueError):
            tr.xfade_filter("spin", 0.35, 0)


if __name__ == "__main__":
    unittest.main()
