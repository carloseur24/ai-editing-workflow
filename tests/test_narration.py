from __future__ import annotations

import unittest

from fenixsoft.domain.narration import build_lines, script_units


def words(text: str, step: float = 0.3) -> list[dict]:
    return [{"w": w, "s": round(i * step, 3), "e": round(i * step + 0.25, 3)}
            for i, w in enumerate(text.split())]


class ScriptUnitsTest(unittest.TestCase):
    def test_splits_sentences_and_clauses(self):
        units = script_units("Uno dos, tres cuatro. Cinco seis.")
        self.assertEqual(units, ["Uno dos,", "tres cuatro.", "Cinco seis."])

    def test_empty_script(self):
        self.assertEqual(script_units("   "), [])


class BuildLinesTest(unittest.TestCase):
    def test_every_unit_gets_a_monotonic_span(self):
        script = "Uno dos, tres cuatro. Cinco seis."
        result = build_lines(script, words(script), duration=1.8)
        self.assertEqual(result["units"], 3)
        self.assertEqual([line["text"] for line in result["lines"]],
                         ["Uno dos,", "tres cuatro.", "Cinco seis."])
        spans = [(line["t0"], line["t1"]) for line in result["lines"]]
        self.assertEqual(spans, sorted(spans))
        self.assertAlmostEqual(result["lines"][-1]["t1"], 1.8, places=3)
        for line in result["lines"]:
            self.assertGreater(line["t1"], line["t0"])
            self.assertIn(line["kind"], {"hook", "proof", "trust", "cta"})

    def test_unmatched_units_are_interpolated(self):
        result = build_lines("Uno dos. Texto que no se dijo.",
                             words("uno dos"), duration=2.0)
        self.assertEqual(len(result["lines"]), 2)
        self.assertIsNotNone(result["lines"][1]["t0"])
        self.assertGreaterEqual(result["lines"][1]["t0"],
                                result["lines"][0]["t0"])

    def test_no_words_returns_empty(self):
        result = build_lines("Uno dos.", [], duration=1.0)
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["method"], "none")


if __name__ == "__main__":
    unittest.main()
