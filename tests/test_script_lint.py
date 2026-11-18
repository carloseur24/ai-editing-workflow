from __future__ import annotations

import unittest

from fenixsoft.stages.script_lint import build_rows, find_phonetic_issues


class PhoneticsTest(unittest.TestCase):
    def test_mangles_are_grouped_by_kind(self):
        issues = find_phonetic_issues("iPhone 14 por $330, 77% batería")
        kinds = {i["kind"] for i in issues}
        self.assertIn("brand", kinds)
        self.assertIn("percent", kinds)
        self.assertIn("currency", kinds)

    def test_number_range_is_flagged(self):
        issues = find_phonetic_issues("un 14x330 imperdible")
        self.assertTrue(any(i["kind"] == "number" for i in issues))


class BudgetTest(unittest.TestCase):
    def test_short_script_passes_the_word_budget(self):
        rows = {r["id"]: r for r in build_rows(
            " ".join(["palabra"] * 60), {}, 28.0, 162.0, 1.12, 1.30)}
        self.assertTrue(rows["s_words"]["pass"])
        self.assertTrue(rows["s_tempo"]["pass"])
        self.assertTrue(rows["s_duration"]["pass"])

    def test_over_budget_script_fails_with_a_fix(self):
        rows = {r["id"]: r for r in build_rows(
            " ".join(["palabra"] * 120), {}, 28.0, 162.0, 1.12, 1.30)}
        self.assertFalse(rows["s_words"]["pass"])
        self.assertFalse(rows["s_tempo"]["pass"])
        self.assertIn("cut", rows["s_words"]["fix"])


if __name__ == "__main__":
    unittest.main()
