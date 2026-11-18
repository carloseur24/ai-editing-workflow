from __future__ import annotations

import unittest

from fenixsoft.stages import grade


class GradeTest(unittest.TestCase):
    def test_full_strength_gain_matches_means(self):
        self.assertEqual(grade.gains((100, 100, 100), (110, 90, 100), 1.0),
                         (1.1, 0.9, 1.0))

    def test_strength_blends_towards_neutral(self):
        g = grade.gains((100, 100, 100), (110, 90, 100), 0.5)
        self.assertAlmostEqual(g[0], 1.1 ** 0.5, places=3)
        self.assertAlmostEqual(g[1], 0.9 ** 0.5, places=3)

    def test_gain_is_clamped(self):
        g = grade.gains((10, 10, 10), (255, 255, 255), 1.0)
        self.assertEqual(g, (1.4, 1.4, 1.4))

    def test_filter_chain_has_eq_and_temperature(self):
        chain, notes = grade.filter_chain({
            "input": "tests/fixtures/placeholder.mp4",
            "eq": {"contrast": 1.2, "saturation": 0.9},
            "temperature": 0.5, "fade_in": 0.2})
        self.assertIn("eq=contrast=1.200", chain)
        self.assertIn("colorbalance=", chain)
        self.assertIn("fade=t=in", chain)
        self.assertEqual(notes["temperature"], 0.5)

    def test_secondary_graph_keys_and_shifts(self):
        graph, n = grade.secondary_graph({"secondaries": [
            {"color": "#FF0000", "hue": 120, "saturation": 1.2}]})
        self.assertEqual(n, 1)
        self.assertIn("colorkey=0xFF0000", graph)
        self.assertIn("hue=h=120.0", graph)

    def test_secondary_color_must_be_hex(self):
        errors = grade.validate({
            "input": "tests/fixtures/placeholder.mp4",
            "secondaries": [{"color": "red"}]})
        self.assertTrue(any("color must be" in e for e in errors))

    def test_missing_input_is_rejected(self):
        self.assertTrue(any("input not found" in e
                            for e in grade.validate({"input": "nope.mp4"})))


if __name__ == "__main__":
    unittest.main()
