from __future__ import annotations

import unittest

from fenixsoft.domain import brief as brief_mod
from fenixsoft.domain import shots as shots_mod
from fenixsoft.stages.storyboard import build_storyboard


class BeatSchemaTest(unittest.TestCase):
    def test_plain_strings_are_accepted(self):
        beats, errors = brief_mod.normalize_beats(
            ["hook: the box in hand", "proof", "close: one action"])
        self.assertEqual(errors, [])
        self.assertEqual([b["purpose"] for b in beats],
                         ["hook", "beat_1", "close"])
        self.assertEqual(beats[0]["text"], "the box in hand")
        self.assertEqual(beats[1]["text"], "proof")
        self.assertEqual(beats[0]["weight"], 1.0)

    def test_objects_keep_their_fields_and_numeric_weight(self):
        beats, errors = brief_mod.normalize_beats(
            [{"purpose": "hook", "weight": "0.2", "text": "box",
              "visual": "zoom"}])
        self.assertEqual(errors, [])
        self.assertEqual(beats[0]["weight"], 0.2)
        self.assertEqual(beats[0]["visual"], "zoom")

    def test_a_non_numeric_weight_names_the_entry(self):
        _, errors = brief_mod.normalize_beats([{"purpose": "hook",
                                                "weight": "heavy"}])
        self.assertEqual(len(errors), 1)
        self.assertIn("story.beats[0].weight", errors[0])

    def test_a_zero_weight_is_rejected(self):
        _, errors = brief_mod.normalize_beats([{"weight": 0}])
        self.assertTrue(any("> 0" in e for e in errors))

    def test_a_number_entry_is_rejected_clearly(self):
        _, errors = brief_mod.normalize_beats([42])
        self.assertIn("must be an object or a string", errors[0])

    def test_empty_beats_mean_use_the_framework(self):
        self.assertEqual(brief_mod.normalize_beats([]), ([], []))
        self.assertEqual(brief_mod.normalize_beats(None), ([], []))

    def test_storyboard_builds_scenes_from_string_beats(self):
        story = build_storyboard({"duration_sec": 10, "story": {
            "beats": ["hook: open", "proof: detail", "close: cta"]}})
        self.assertEqual(len(story["scenes"]), 3)
        self.assertEqual([s["purpose"] for s in story["scenes"]],
                         ["hook", "proof", "close"])
        self.assertAlmostEqual(sum(s["seconds"] for s in story["scenes"]),
                               10.0, places=2)

    def test_storyboard_exits_with_the_schema_message(self):
        with self.assertRaises(SystemExit) as raised:
            build_storyboard({"duration_sec": 10,
                              "story": {"beats": [{"weight": "x"}]}})
        self.assertIn("brief.story.beats", str(raised.exception))


SHOT = {"clip": "a.mp4", "in": 1.0, "dur": 2.0}


class ShotSchemaTest(unittest.TestCase):
    def rows(self, shots, durations=None, duration=3.0):
        return {r["id"]: r for r in shots_mod.validate_shots(
            shots_mod.shots_from_brief({"story": {"shots": shots}}),
            durations if durations is not None else {"a.mp4": 10.0}, duration)}

    def test_zero_and_negative_durations_are_schema_failures(self):
        for bad in (0, -2):
            with self.subTest(dur=bad):
                rows = self.rows([{**SHOT, "dur": bad}])
                self.assertFalse(rows["shot_schema"]["pass"])
                self.assertIn("dur<=0", rows["shot_schema"]["fix"])

    def test_a_missing_duration_is_named(self):
        item = {"clip": "a.mp4", "in": 0.0}
        rows = self.rows([item])
        self.assertFalse(rows["shot_schema"]["pass"])
        self.assertIn("dur", rows["shot_schema"]["fix"])

    def test_a_non_numeric_duration_is_named(self):
        rows = self.rows([{**SHOT, "dur": "long"}])
        self.assertFalse(rows["shot_schema"]["pass"])
        self.assertIn("dur", rows["shot_schema"]["fix"])

    def test_a_numeric_string_is_accepted(self):
        rows = self.rows([{**SHOT, "dur": "2.0"}])
        self.assertTrue(rows["shot_schema"]["pass"])

    def test_a_negative_in_point_fails_the_schema(self):
        rows = self.rows([{**SHOT, "in": -1.0}])
        self.assertFalse(rows["shot_schema"]["pass"])
        self.assertIn("in<0", rows["shot_schema"]["fix"])

    def test_a_valid_table_still_passes(self):
        rows = self.rows([SHOT, {"clip": "a.mp4", "in": 4.0, "dur": 1.0}])
        self.assertTrue(all(r["pass"] for r in rows.values()))


class ShotTextZoneTest(unittest.TestCase):
    def test_text_zone_travels_from_brief_to_the_locked_plan(self):
        brief = {"story": {"shots": [
            {"clip": "a.mp4", "in": 0.0, "dur": 2.0, "text_zone": "lower"},
        ]}}
        shots = shots_mod.shots_from_brief(brief)
        self.assertEqual(shots[0]["text_zone"], "lower")
        plan = shots_mod.locked_plan(shots, "p", duration_sec=2.0)
        self.assertEqual(plan["cuts"][0]["text_zone"], "lower")

    def test_changing_the_zone_releases_a_matching_plan(self):
        brief = {"story": {"shots": [
            {"clip": "a.mp4", "in": 0.0, "dur": 2.0}]}}
        plan = shots_mod.locked_plan(shots_mod.shots_from_brief(brief), "p",
                                     duration_sec=2.0)
        self.assertTrue(shots_mod.plan_cuts_match(
            plan, shots_mod.shots_from_brief(brief)))
        moved = {"story": {"shots": [
            {"clip": "a.mp4", "in": 0.0, "dur": 2.0,
             "text_zone": "lower"}]}}
        self.assertFalse(shots_mod.plan_cuts_match(
            plan, shots_mod.shots_from_brief(moved)))


if __name__ == "__main__":
    unittest.main()
