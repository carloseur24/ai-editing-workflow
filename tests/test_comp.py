from __future__ import annotations

import unittest

from fenixsoft.stages import comp

BASE = "tests/fixtures/placeholder.mp4"
LAYER = "assets/motion/brain.png"


def spec(**over):
    data = {"base": BASE, "width": 1080, "height": 1920, "fps": 60,
            "duration": 2, "layers": [
                {"src": LAYER, "start": 0, "end": 2,
                 "x": 0.5, "y": 0.5,
                 "move": {"from": [0.5, 0.55], "to": [0.5, 0.45]}}]}
    data.update(over)
    return data


class CompTest(unittest.TestCase):
    def test_px_normalises_fractions(self):
        self.assertEqual(comp._px(0.5, 1080), 540)
        self.assertEqual(comp._px(200, 1080), 200)

    def test_valid_spec(self):
        self.assertEqual(comp.validate(spec()), [])

    def test_graph_has_overlay_and_move(self):
        inputs, graph = comp._graph(spec())
        self.assertIn("overlay=", graph)
        self.assertIn("trim=0.0:2.0", graph)
        self.assertIn("min(max((t-0.000)", graph)
        self.assertEqual(inputs.count("-i"), 2)

    def test_chroma_key_and_rect_mask(self):
        s = spec()
        s["layers"][0]["key"] = {"color": "#00ff00"}
        s["layers"][0]["mask"] = {"type": "rect", "x": 0.1, "y": 0.1,
                                  "w": 0.8, "h": 0.8}
        _, graph = comp._graph(s)
        self.assertIn("chromakey=color=0x00ff00", graph)
        self.assertIn("crop=864:1536", graph)

    def test_ellipse_mask_is_supported(self):
        s = spec()
        s["layers"][0]["mask"] = {"type": "ellipse", "x": 0.5, "y": 0.5,
                                  "w": 0.6, "h": 0.6}
        self.assertEqual(comp.validate(s), [])
        _, graph = comp._graph(s)
        self.assertIn("geq=", graph)
        self.assertIn("alpha(X,Y)", graph)

    def test_track_expression_is_piecewise(self):
        expr = comp._track_expr([{"t": 0, "nx": 0.2},
                                 {"t": 1, "nx": 0.8}], "nx", 100)
        self.assertIn("if(lt(t,1.000)", expr)
        self.assertIn("20.00", expr)
        self.assertIn("80.00", expr)

    def test_unknown_mask_is_rejected(self):
        s = spec()
        s["layers"][0]["mask"] = {"type": "lasso"}
        self.assertTrue(any("mask type" in e for e in comp.validate(s)))

    def test_missing_base_is_rejected(self):
        self.assertTrue(any("base not found" in e
                            for e in comp.validate(spec(base="nope.mp4"))))


if __name__ == "__main__":
    unittest.main()
