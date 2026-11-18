"""Production-line separation: project config overlay, job display, freshness.

These tests cover the boundary that the P0-P2 work introduced:

    machinery  config/pipeline.json (+ gitignored local)
    intent     input/<project>/config.json
    media      output/<project>/ (immutable runs)
    product    the delivery shelf

The overlay is sparse and only project keys move; a project must be able to
override a nested global key and to remove an inherited preset.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fenixsoft import context
from fenixsoft.domain import config_slices
from fenixsoft.ops import job, make
from fenixsoft.stages import sfx


class ProjectOverlayTest(unittest.TestCase):
    def _setup(self, root: Path) -> tuple[Path, Path, Path]:
        base = root / "pipeline.json"
        local = root / "pipeline.local.json"
        project = root / "input" / "piece"
        project.mkdir(parents=True)
        base.write_text(json.dumps({
            "target_duration_sec": 28.0,
            "cuts_per_video": 16,
            "quality": {"in_point_floor": 0.6, "beat_policy": "music"},
            "audio": {"music_bed_lufs": -28.0,
                      "music_only": {"bed_lufs": -18.0}},
            "delivery": {"dir": "output/delivery"},
        }), encoding="utf-8")
        local.write_text(json.dumps({
            "delivery": {"dir": "/mnt/c/shelf"},
            "quality": {"caption_warn_cps": 19},
        }), encoding="utf-8")
        (project / "config.json").write_text(json.dumps({
            "target_duration_sec": 33.0,
            "quality": {"in_point_floor": 0.25, "beat_policy": "picture"},
            "audio": {"music_only": None},
        }), encoding="utf-8")
        return base, local, project

    def test_precedence_and_nested_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, local, project = self._setup(Path(tmp))
            with mock.patch.object(context, "DEFAULT_CONFIG", base), \
                    mock.patch.object(context, "LOCAL_CONFIG", local):
                cfg = context.load_config(project=project)
        # project > local > base
        self.assertEqual(cfg["target_duration_sec"], 33.0)
        self.assertEqual(cfg["quality"]["in_point_floor"], 0.25)
        self.assertEqual(cfg["quality"]["beat_policy"], "picture")
        # local keys that the project does not touch survive
        self.assertEqual(cfg["delivery"]["dir"], "/mnt/c/shelf")
        self.assertEqual(cfg["quality"]["caption_warn_cps"], 19)
        # a null in the project overlay removes an inherited preset
        self.assertNotIn("music_only", cfg["audio"])

    def test_explicit_config_is_a_base_not_a_project_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, local, project = self._setup(root)
            other = root / "other.json"
            other.write_text(json.dumps({
                "target_duration_sec": 20.0,
                "cuts_per_video": 4,
                "quality": {"in_point_floor": 0.9, "beat_policy": "music"},
                "audio": {"music_only": {"bed_lufs": -20.0}},
            }), encoding="utf-8")
            with mock.patch.object(context, "DEFAULT_CONFIG", base), \
                    mock.patch.object(context, "LOCAL_CONFIG", local):
                cfg = context.load_config(other, project=project)
        # explicit --config is the base: local is skipped...
        self.assertNotIn("delivery", cfg)
        self.assertNotIn("caption_warn_cps", cfg["quality"])
        # ...but the project overlay still wins over it
        self.assertEqual(cfg["target_duration_sec"], 33.0)
        self.assertEqual(cfg["quality"]["in_point_floor"], 0.25)
        self.assertEqual(cfg["cuts_per_video"], 4)
        self.assertNotIn("music_only", cfg["audio"])

    def test_override_count_counts_leaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, project = self._setup(Path(tmp))
            overlay = context.project_overrides(project)
        self.assertEqual(context.count_overrides(overlay), 4)
        self.assertEqual(context.project_overrides(None), {})


class JobConfigCardTest(unittest.TestCase):
    def test_card_shows_project_config_and_effective_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "input" / "piece"
            project.mkdir(parents=True)
            (project / "sku.json").write_text(json.dumps({
                "title": "piece",
                "script_text": "hola",
                "voiceover": None,
                "duration_sec": 33,
                "text_cards": [{"text": "A", "start": 0.0, "end": 1.0}],
            }), encoding="utf-8")
            (project / "config.json").write_text(json.dumps({
                "target_duration_sec": 33.0,
                "cuts_per_video": 5,
                "quality": {"in_point_floor": 0.25,
                            "beat_policy": "picture"},
                "transitions": {"enabled": True, "default": "dissolve",
                                "sec": 0.2},
            }), encoding="utf-8")
            with mock.patch.object(job, "resolve_sku_dir", return_value=project), \
                    mock.patch.object(job.runs, "latest_run",
                                      return_value=None):
                card = job.build_card("piece")
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    job._print_human(card)

        self.assertTrue(card["config"]["exists"])
        self.assertEqual(card["config"]["overrides"], 7)
        self.assertEqual(card["params"]["duration_sec"], 33.0)
        self.assertEqual(card["params"]["cuts"], 5)
        self.assertEqual(card["params"]["beat_policy"], "picture")
        self.assertEqual(card["params"]["transitions"], "dissolve 0.20s")
        self.assertIn("native sku.text_cards (1 cards)", card["params"]["text"])
        text = captured.getvalue()
        self.assertIn("config.json (7 overrides)", text)
        self.assertIn("33.0s · 5 cuts · beat picture · in-point floor 0.25",
                      text)


class MakeFreshnessTest(unittest.TestCase):
    def test_project_config_change_invalidates_a_stage_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            base = {"target_duration_sec": 28.0, "cuts_per_video": 16,
                    "bpm": 118}
            make.write_config_stamp(out, base, "plan")
            self.assertTrue(make.config_state(out, base, "plan"))
            project_cfg = context.deep_merge(base, {
                "target_duration_sec": 33.0,
                "cuts_per_video": 5,
            })
            self.assertFalse(make.config_state(out, project_cfg, "plan"))

    def test_picture_and_audio_slices_react_to_their_own_keys(self):
        base = context.load_config()
        stored = config_slices.render_slices(base)
        picture_cfg = context.deep_merge(base, {"target_duration_sec": 33.0})
        audio_cfg = context.deep_merge(base, {"audio": {"music_bed_lufs": -18.0}})
        self.assertTrue(config_slices.picture_changed(picture_cfg, stored))
        self.assertFalse(config_slices.audio_changed(picture_cfg, stored))
        self.assertTrue(config_slices.audio_changed(audio_cfg, stored))
        self.assertFalse(config_slices.picture_changed(audio_cfg, stored))


class CueFloorTest(unittest.TestCase):
    def test_short_piece_scales_the_floor(self):
        self.assertEqual(sfx.cue_floor(6.0), 3)
        self.assertEqual(sfx.cue_floor(28.0), 8)
        self.assertEqual(sfx.cue_floor(33.0), 8)
        self.assertEqual(sfx.cue_floor(6.0, configured=2), 2)

    def test_evaluate_accepts_three_cues_for_six_seconds(self):
        cues = [
            {"layer": "impact", "role": "impact_sub", "t_sec": 0.0},
            {"layer": "tactile", "role": "click_tactile", "t_sec": 1.0},
            {"layer": "anticipation", "role": "riser_tension", "t_sec": 2.0},
        ]
        rows = sfx.evaluate(cues, -6.0, -18.0, {
            "duration_sec": 6.0, "min_cues": 8, "music_bed_lufs": -18.0,
            "jcut_min_ms": 150, "sub_spacing_sec": 1.5,
        })
        cue_row = next(r for r in rows if r["id"] == "sd_cues")
        self.assertTrue(cue_row["pass"])
        self.assertIn("duration-scaled", cue_row["threshold"])


class PostFlagsTest(unittest.TestCase):
    def test_post_plan_honors_project_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "motion.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(make, "_motion_decision",
                                   return_value=(True, "stale")):
                rows = {r["stage"]: r for r in make.post_decisions(
                    project, {"post": {"motion": False, "comp": False}})}
        self.assertFalse(rows["motion"]["run"])
        self.assertFalse(rows["comp"]["run"])
        self.assertIn("post.motion = false", rows["motion"]["why"])
        self.assertIn("post.comp = false", rows["comp"]["why"])
        # The dry-run must not claim a comp delivery that will not happen.
        self.assertFalse(rows["deliver"]["run"])
        self.assertIn("edit master is the product", rows["deliver"]["why"])
        self.assertIn("fenix finish", rows["deliver"]["why"])

    def test_post_decisions_deliver_row_when_comp_is_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "motion.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(make, "_motion_decision",
                                   return_value=(False, "plate current")):
                rows = {r["stage"]: r for r in make.post_decisions(
                    project, {"post": {"motion": True, "comp": True}})}
                no_finish = {r["stage"]: r for r in make.post_decisions(
                    project, {"post": {"motion": True, "comp": True}},
                    deliver=False)}
        self.assertTrue(rows["deliver"]["run"])
        self.assertIn("post QA", rows["deliver"]["why"])
        self.assertFalse(no_finish["deliver"]["run"])
        self.assertIn("--no-finish", no_finish["deliver"]["why"])

    def test_run_post_comp_does_no_post_work_when_comp_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            with mock.patch.object(make, "_step") as step, \
                    mock.patch.object(make.runs,
                                      "resolve_stable_ref") as stable:
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    ok = make.run_post_comp(
                        project, {"post": {"motion": False, "comp": False}},
                        deliver_result=True)
        self.assertTrue(ok)
        step.assert_not_called()
        stable.assert_not_called()
        text = captured.getvalue()
        self.assertIn("deliver SKIP", text)
        self.assertIn("fenix finish", text)


if __name__ == "__main__":
    unittest.main()
