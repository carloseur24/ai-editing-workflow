from __future__ import annotations

import unittest

from fenixsoft.domain import shots as sm
from fenixsoft.stages.storyboard import build_storyboard

BRIEF = {
    "duration_sec": 9.0,
    "story": {"shots": [
        {"clip": "a.mp4", "in": 1.0, "dur": 5.5, "label": "hook",
         "spec": "GALAXY S25 ULTRA"},
        {"clip": "b.mp4", "in": 0.25, "dur": 3.5, "label": "detail"},
    ]},
}
DURATIONS = {"a.mp4": 10.0, "b.mp4": 8.0}


def rows(rows_list: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows_list}


class ShotTableTest(unittest.TestCase):
    def test_valid_table_passes_every_row(self):
        shots = sm.shots_from_brief(BRIEF)
        checks = rows(sm.validate_shots(shots, DURATIONS, 9.0))
        self.assertTrue(sm.shots_pass(checks.values()))
        self.assertTrue(checks["shot_clip"]["pass"])
        self.assertTrue(checks["shot_range"]["pass"])
        self.assertTrue(checks["shot_total"]["pass"])

    def test_timeline_is_cumulative_and_exact(self):
        line = sm.timeline(sm.shots_from_brief(BRIEF))
        self.assertEqual([s["t0"] for s in line], [0.0, 5.5])
        self.assertEqual([s["t1"] for s in line], [5.5, 9.0])

    def test_locked_plan_carries_the_exact_cuts(self):
        plan = sm.locked_plan(sm.shots_from_brief(BRIEF), "p", duration_sec=9.0)
        self.assertTrue(plan["locked"])
        self.assertEqual(plan["order_source"], "brief")
        self.assertEqual(plan["duration_sec"], 9.0)
        self.assertEqual([(c["clip"], c["in"], c["dur"]) for c in plan["cuts"]],
                         [("a.mp4", 1.0, 5.5), ("b.mp4", 0.25, 3.5)])
        self.assertEqual(plan["cuts"][1]["t0"], 5.5)
        self.assertEqual(plan["cuts"][0]["spec"], "GALAXY S25 ULTRA")

    def test_missing_clip_is_named(self):
        checks = rows(sm.validate_shots(
            sm.shots_from_brief(BRIEF), {"a.mp4": 10.0}, 9.0))
        self.assertFalse(checks["shot_clip"]["pass"])
        self.assertIn("b.mp4", checks["shot_clip"]["fix"])

    def test_in_plus_dur_past_the_source_fails(self):
        brief = {"duration_sec": 9.0, "story": {"shots": [
            {"clip": "b.mp4", "in": 6.0, "dur": 3.5}]}}
        checks = rows(sm.validate_shots(
            sm.shots_from_brief(brief), {"b.mp4": 8.0}, 9.0))
        self.assertFalse(checks["shot_range"]["pass"])
        self.assertIn("b.mp4", checks["shot_range"]["fix"])

    def test_negative_in_fails_with_a_named_schema_row(self):
        brief = {"duration_sec": 3.0, "story": {"shots": [
            {"clip": "a.mp4", "in": -1.0, "dur": 3.0}]}}
        checks = rows(sm.validate_shots(
            sm.shots_from_brief(brief), {"a.mp4": 10.0}, 3.0))
        # A negative in-point is named by field instead of surfacing later as a
        # range error in the renderer.
        self.assertFalse(checks["shot_schema"]["pass"])
        self.assertIn("in<0", checks["shot_schema"]["fix"])

    def test_sum_mismatch_fails(self):
        checks = rows(sm.validate_shots(
            sm.shots_from_brief(BRIEF), DURATIONS, 12.0))
        self.assertFalse(checks["shot_total"]["pass"])
        self.assertIn("12.00", checks["shot_total"].get("fix", ""))

    def test_min_cut_is_checked(self):
        brief = {"duration_sec": 3.0, "story": {"shots": [
            {"clip": "a.mp4", "in": 0.0, "dur": 0.4}]}}
        checks = rows(sm.validate_shots(
            sm.shots_from_brief(brief), {"a.mp4": 10.0}, 3.0, min_dur=1.2))
        self.assertFalse(checks["shot_min_cut"]["pass"])

    def test_brief_without_shots_returns_none(self):
        self.assertIsNone(sm.shots_from_brief({"duration_sec": 10}))
        self.assertIsNone(sm.shots_from_brief({"story": {}}))


class PlanMatchTest(unittest.TestCase):
    def test_matching_plan_is_recognised(self):
        shots = sm.shots_from_brief(BRIEF)
        plan = sm.locked_plan(shots, "p", duration_sec=9.0)
        self.assertTrue(sm.plan_cuts_match(plan, shots))

    def test_edited_duration_breaks_the_match(self):
        shots = sm.shots_from_brief(BRIEF)
        plan = sm.locked_plan(shots, "p", duration_sec=9.0)
        plan["cuts"][0]["dur"] = 4.0
        self.assertFalse(sm.plan_cuts_match(plan, shots))

    def test_order_state_requires_a_locked_plan(self):
        shots = sm.shots_from_brief(BRIEF)
        state = sm.order_state(BRIEF, None, DURATIONS, 9.0)
        self.assertTrue(state["locked"])
        self.assertEqual(state["source"], "brief")
        self.assertFalse(state["order_confirmed"])
        plan = sm.locked_plan(shots, "p", duration_sec=9.0)
        state = sm.order_state(BRIEF, plan, DURATIONS, 9.0)
        self.assertTrue(state["order_confirmed"])

    def test_order_state_reports_a_broken_table(self):
        state = sm.order_state(BRIEF, None, {"a.mp4": 10.0}, 12.0)
        self.assertFalse(state["locked"])
        self.assertFalse(state["order_confirmed"])


MANUAL_PLAN = {
    "sku": "p",
    "generated_by": "storyboard timings (corrected order: a, b)",
    "narration_lines": 2,
    "cuts": [
        {"line": 0, "clip": "a.mp4", "in": 1.0, "dur": 5.5},
        {"line": 1, "clip": "b.mp4", "in": 0.25, "dur": 3.5},
    ],
}


class ManualPlanTest(unittest.TestCase):
    def test_plan_without_a_planner_stamp_is_manual(self):
        self.assertTrue(sm.is_manual_plan(MANUAL_PLAN))
        self.assertTrue(sm.is_manual_plan(
            {"cuts": MANUAL_PLAN["cuts"]}))       # no generated_by at all

    def test_fenix_plan_output_is_not_manual(self):
        self.assertFalse(sm.is_manual_plan(
            {"generated_by": "fenix plan", "narration_lines": 2,
             "cuts": MANUAL_PLAN["cuts"]}))
        self.assertFalse(sm.is_manual_plan(
            sm.locked_plan(sm.shots_from_brief(BRIEF), "p", duration_sec=9.0)))

    def test_legacy_planner_output_is_not_manual(self):
        self.assertFalse(sm.is_manual_plan(
            {"generated_by": "plan_edit.py", "cuts": MANUAL_PLAN["cuts"]}))

    def test_empty_and_locked_plans_are_not_manual(self):
        self.assertFalse(sm.is_manual_plan({}))
        self.assertFalse(sm.is_manual_plan({"generated_by": "hand",
                                            "cuts": []}))
        self.assertFalse(sm.is_manual_plan(
            {**MANUAL_PLAN, "locked": True}))


class StoryboardFromShotsTest(unittest.TestCase):
    def test_scenes_follow_the_shot_table(self):
        story = build_storyboard(BRIEF, durations=DURATIONS)
        self.assertEqual(len(story["scenes"]), 2)
        self.assertEqual(story["scenes"][0]["clip"], "a.mp4")
        self.assertEqual(story["scenes"][1]["t0"], 5.5)
        self.assertTrue(story["order"]["locked"])
        self.assertTrue(story["order"]["pass"])

    def test_order_checks_fail_with_a_bad_clip(self):
        story = build_storyboard(BRIEF, durations={"a.mp4": 10.0})
        self.assertFalse(story["order"]["pass"])

    def test_shot_table_replaces_the_hook_missing_fact(self):
        brief = dict(BRIEF, title="t", project_type="edit",
                     format={"aspect": "9:16", "width": 1080,
                             "height": 1920, "fps": 60},
                     deliverables=["mp4"])
        missing = build_storyboard(brief, durations=DURATIONS)["missing"]
        self.assertFalse(any(m.startswith("story.hook") for m in missing))


if __name__ == "__main__":
    unittest.main()
