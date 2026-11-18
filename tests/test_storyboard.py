from __future__ import annotations

import unittest

from fenixsoft.stages.storyboard import (build_storyboard, missing_fields,
                                         questionnaire)


class StoryboardTest(unittest.TestCase):
    def test_beats_are_allocated_across_the_duration(self):
        brief = {"duration_sec": 10, "story": {"beats": [
            {"purpose": "hook", "weight": 1},
            {"purpose": "close", "weight": 3},
        ]}}
        story = build_storyboard(brief)
        self.assertEqual(len(story["scenes"]), 2)
        self.assertAlmostEqual(story["scenes"][0]["seconds"], 2.5, places=3)
        self.assertAlmostEqual(story["scenes"][1]["seconds"], 7.5, places=3)
        self.assertAlmostEqual(story["scenes"][-1]["t1"], 10.0, places=3)

    def test_default_framework_is_used_without_beats(self):
        story = build_storyboard({"duration_sec": 20})
        self.assertEqual(story["framework"]["id"], "hook_retain_reward")
        self.assertEqual(len(story["scenes"]), 5)
        self.assertEqual([s["purpose"] for s in story["scenes"]],
                         ["hook", "problem", "mechanism", "payoff", "cta"])
        self.assertAlmostEqual(sum(s["seconds"] for s in story["scenes"]),
                               20.0, places=2)

    def test_framework_selects_its_beat_structure(self):
        story = build_storyboard({"duration_sec": 12, "story": {
            "framework": "aida"}})
        self.assertEqual(story["framework"]["id"], "aida")
        self.assertEqual(len(story["scenes"]), 4)
        self.assertEqual([s["purpose"] for s in story["scenes"]],
                         ["attention", "interest", "desire", "action"])
        self.assertAlmostEqual(sum(s["seconds"] for s in story["scenes"]),
                               12.0, places=2)

    def test_project_type_selects_the_default_framework(self):
        brand = build_storyboard({"duration_sec": 10,
                                 "project_type": "brand"})
        self.assertEqual(brand["framework"]["id"], "storybrand")
        explainer = build_storyboard({"duration_sec": 10,
                                     "project_type": "explainer"})
        self.assertEqual(explainer["framework"]["id"], "explainer")
        longform = build_storyboard({"duration_sec": 60,
                                    "project_type": "longform"})
        self.assertEqual(longform["framework"]["id"], "save_the_cat")

    def test_unknown_framework_falls_back(self):
        story = build_storyboard({"duration_sec": 10, "story": {
            "framework": "nope"}})
        self.assertEqual(story["framework"]["id"], "hook_retain_reward")

    def test_missing_facts_are_named(self):
        missing = missing_fields({"title": "x", "duration_sec": 10})
        self.assertTrue(any(m.startswith("format.aspect") for m in missing))
        self.assertTrue(any(m.startswith("story.hook") for m in missing))

    def test_questionnaire_covers_every_section(self):
        text = questionnaire()
        for section in ("Intent", "Format", "Story", "Assets",
                        "Motion & style", "Delivery"):
            self.assertIn(f"[{section}]", text)


if __name__ == "__main__":
    unittest.main()
