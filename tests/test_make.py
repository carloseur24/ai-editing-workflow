from __future__ import annotations

import contextlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fenixsoft.context import DEFAULT_CONFIG
from fenixsoft.domain import shots as shots_mod
from fenixsoft.ops import finish
from fenixsoft.ops import job as job_ops
from fenixsoft.ops.job import build_card
from fenixsoft.ops.make import (_align_current, config_digest, config_state,
                                is_fresh, locked_plan_state, write_config_stamp)
from fenixsoft.stages import plan as plan_stage
from fenixsoft.domain.config_slices import (audio_changed, picture_changed,
                                            render_slices)


class FreshnessTest(unittest.TestCase):
    def test_missing_output_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            src = Path(tmp) / "src.json"
            src.write_text("{}", encoding="utf-8")
            self.assertFalse(is_fresh(out, [src]))

    def test_output_newer_than_input_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            src = Path(tmp) / "src.json"
            src.write_text("{}", encoding="utf-8")
            time.sleep(0.01)
            out.write_text("{}", encoding="utf-8")
            self.assertTrue(is_fresh(out, [src]))

    def test_newer_input_makes_output_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.json"
            src = Path(tmp) / "src.json"
            out.write_text("{}", encoding="utf-8")
            time.sleep(0.01)
            src.write_text("{}", encoding="utf-8")
            self.assertFalse(is_fresh(out, [src]))


class AlignFingerprintTest(unittest.TestCase):
    def _sku(self, tmp: Path, script: str = "hola mundo") -> Path:
        (tmp / "sku.json").write_text(json.dumps(
            {"script_text": script, "voiceover": "voice.mp3",
             "display_merge": {"ES SIM": "E-SIM"}}), encoding="utf-8")
        (tmp / "voice.mp3").write_bytes(b"fake voice")
        return tmp

    def _timings(self, tmp: Path, **over):
        import hashlib
        payload = {
            "source": {
                "voice": "voice.mp3",
                "voice_sha256": hashlib.sha256(b"fake voice").hexdigest(),
                "script_sha256": hashlib.sha256("hola mundo".encode()).hexdigest(),
                "display_merge": {"ES SIM": "E-SIM"},
            },
            "words": [],
        }
        payload.update(over)
        (tmp / "word_timings.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_matching_fingerprint_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._sku(Path(tmp))
            self._timings(d)
            self.assertTrue(_align_current(d))

    def test_price_only_edit_stays_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._sku(Path(tmp))
            self._timings(d)
            sku = json.loads((d / "sku.json").read_text())
            sku["price_label"] = "$999"
            (d / "sku.json").write_text(json.dumps(sku), encoding="utf-8")
            self.assertTrue(_align_current(d))

    def test_script_edit_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._sku(Path(tmp), script="otro guion")
            self._timings(d)
            self.assertFalse(_align_current(d))

    def test_legacy_artifact_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._sku(Path(tmp))
            (d / "word_timings.json").write_text(
                json.dumps({"words": []}), encoding="utf-8")
            self.assertIsNone(_align_current(d))


class FinishQaReuseTest(unittest.TestCase):
    def _run(self, tmp: Path, **report) -> Path:
        video = tmp / "run_final.mp4"
        video.write_bytes(b"not really a video")
        stat = video.stat()
        payload = {
            "pass": True,
            "strict": True,
            "video_bytes": stat.st_size,
            "video_mtime_ns": stat.st_mtime_ns,
        }
        payload.update(report)
        (tmp / "qa_report.json").write_text(json.dumps(payload), encoding="utf-8")
        return video

    def test_matching_strict_pass_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = self._run(Path(tmp))
            self.assertTrue(finish.qa_is_current(video))

    def test_changed_bytes_invalidate_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = self._run(Path(tmp))
            video.write_bytes(b"changed")
            self.assertFalse(finish.qa_is_current(video))

    def test_non_strict_or_failed_report_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(finish.qa_is_current(self._run(Path(tmp), strict=False)))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(finish.qa_is_current(
                self._run(Path(tmp), **{"pass": False})))

    def test_legacy_report_without_fingerprint_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "run_final.mp4"
            video.write_bytes(b"x")
            (Path(tmp) / "qa_report.json").write_text(
                json.dumps({"pass": True, "strict": True}), encoding="utf-8")
            self.assertFalse(finish.qa_is_current(video))


class ConfigSliceTest(unittest.TestCase):
    def test_only_the_stage_slice_invalidates_a_stamp(self):
        cfg = {"music": {"file": "a.wav"}, "audio": {"target_lufs": -14.0},
               "delivery": {"dir": "/tmp/out"}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_config_stamp(out, cfg, "beats")
            self.assertTrue(config_state(out, cfg, "beats"))
            cfg2 = dict(cfg, delivery={"dir": "/tmp/other"})
            self.assertTrue(config_state(out, cfg2, "beats"))
            cfg3 = dict(cfg, music={"file": "b.wav"})
            self.assertFalse(config_state(out, cfg3, "beats"))

    def test_stage_without_config_deps_has_no_digest(self):
        self.assertIsNone(config_digest({}, "validate"))


class RenderSliceTest(unittest.TestCase):
    cfg = {
        "canvas": {"width": 1080, "height": 1920, "fps": 60},
        "audio": {"target_lufs": -14.0},
        "music": {"file": "a.wav"},
        "grade": {"mode": "baked"},
        "delivery": {"dir": "/tmp/out"},
    }

    def test_same_config_is_unchanged(self):
        stored = render_slices(self.cfg)
        self.assertFalse(picture_changed(self.cfg, stored))
        self.assertFalse(audio_changed(self.cfg, stored))

    def test_delivery_edit_does_not_change_the_render(self):
        stored = render_slices(self.cfg)
        edited = dict(self.cfg, delivery={"dir": "/tmp/other"})
        self.assertFalse(picture_changed(edited, stored))
        self.assertFalse(audio_changed(edited, stored))

    def test_audio_edit_is_audio_only(self):
        stored = render_slices(self.cfg)
        edited = dict(self.cfg, audio={"target_lufs": -12.0})
        self.assertFalse(picture_changed(edited, stored))
        self.assertTrue(audio_changed(edited, stored))

    def test_grade_edit_is_a_picture_change(self):
        stored = render_slices(self.cfg)
        edited = dict(self.cfg, grade={"mode": "per_frame"})
        self.assertTrue(picture_changed(edited, stored))

    def test_legacy_plan_returns_none(self):
        self.assertIsNone(picture_changed(self.cfg, None))
        self.assertIsNone(audio_changed(self.cfg, {}))


class JobCardTest(unittest.TestCase):
    def test_new_project_points_at_intake(self):
        card = build_card("project_that_does_not_exist_98765")
        self.assertEqual(card["mode"], "new")
        self.assertFalse(card["ready"])
        self.assertIn("storyboard", card["next"][0]["command"])
        self.assertEqual(len(card["rules"]), 5)

    def test_incomplete_project_lists_missing_inputs(self):
        card = build_card("example_iphone14")
        self.assertEqual(card["mode"], "edit")
        self.assertFalse(card["ready"])
        self.assertIn("voice.mp3", card["missing"])
        self.assertIn("clips/*.mp4", card["missing"])

    def _fixture(self, root: Path, *, music: Path) -> tuple[Path, Path]:
        sku = root / "input" / "fixture"
        (sku / "clips").mkdir(parents=True)
        (sku / "sku.json").write_text(json.dumps(
            {"script_text": "hola mundo"}), encoding="utf-8")
        (sku / "voice.mp3").write_bytes(b"fake voice")
        (sku / "clips" / "a.mp4").write_bytes(b"fake clip")
        (sku / "brief.json").write_text("{}", encoding="utf-8")
        cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        cfg["music"]["file"] = str(music)
        config_path = root / "pipeline.json"
        config_path.write_text(json.dumps(cfg), encoding="utf-8")
        return sku, config_path

    def test_ready_project_has_stage_and_render_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            music = root / "bed.wav"
            music.write_bytes(b"fake music")
            sku, config_path = self._fixture(root, music=music)
            with mock.patch.object(job_ops, "resolve_sku_dir",
                                   return_value=sku), \
                    mock.patch.object(job_ops.runs, "latest_run",
                                      return_value=None):
                card = build_card("fixture", config_path)
        self.assertEqual(card["mode"], "edit")
        self.assertTrue(card["ready"])
        self.assertEqual(len(card["stages"]), 7)
        self.assertEqual(card["render"]["mode"], "full")
        self.assertTrue(card["skills"])
        self.assertIn("fenix make fixture",
                      " ".join(a["command"] for a in card["next"]))

    def test_missing_music_is_an_asset_gap_not_a_project_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sku, config_path = self._fixture(
                root, music=root / "assets" / "music" / "absent.wav")
            with mock.patch.object(job_ops, "resolve_sku_dir",
                                   return_value=sku), \
                    mock.patch.object(job_ops.runs, "latest_run",
                                      return_value=None):
                card = build_card("fixture", config_path)
        self.assertFalse(card["ready"])
        self.assertEqual(card["missing"], [])
        self.assertEqual(card["asset_gaps"],
                         [str(root / "assets" / "music" / "absent.wav")])
        action = next(a for a in card["next"] if a["label"] == "add assets")
        self.assertIn("not input/", action["command"])

    def test_card_is_json_serializable(self):
        json.dumps(build_card("demo_iphone14_blue"))


SHOT_BRIEF = {
    "title": "fixture", "project_type": "edit",
    "format": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 60},
    "duration_sec": 9.0, "deliverables": ["mp4"],
    "story": {"shots": [
        {"clip": "a.mp4", "in": 1.0, "dur": 5.5, "label": "hook"},
        {"clip": "b.mp4", "in": 0.25, "dur": 3.5, "label": "detail"},
    ]},
}


class OrderLockTest(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        sku = root / "input" / "fixture"
        (sku / "clips").mkdir(parents=True)
        for name in ("a.mp4", "b.mp4"):
            (sku / "clips" / name).write_bytes(b"fake clip")
        (sku / "sku.json").write_text(json.dumps(
            {"voiceover": None, "duration_sec": 9.0}), encoding="utf-8")
        (sku / "brief.json").write_text(json.dumps(SHOT_BRIEF),
                                        encoding="utf-8")
        return sku

    def test_plan_writes_the_brief_order_as_a_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            out = Path(tmp) / "out"
            cfg = {"min_cut_sec": 1.2, "target_duration_sec": 9.0,
                   "shots": {"source_tolerance_sec": 0.02,
                             "sum_tolerance_sec": 0.25}}
            with mock.patch.object(plan_stage, "probe_duration",
                                   side_effect=lambda p: 10.0), \
                    mock.patch.object(plan_stage, "artifact",
                                      side_effect=lambda s, n: out / n):
                result = plan_stage.plan(sku, cfg, dry_run=False)
            self.assertTrue(result["pass"])
            data = json.loads((sku / "edit_plan.json").read_text())
            self.assertTrue(data["locked"])
            self.assertEqual(data["order_source"], "brief")
            self.assertEqual([(c["clip"], c["in"], c["dur"])
                              for c in data["cuts"]],
                             [("a.mp4", 1.0, 5.5), ("b.mp4", 0.25, 3.5)])
            self.assertEqual(data["cuts"][1]["t0"], 5.5)
            self.assertTrue((out / "plan_edit.json").exists())

    def test_a_locked_plan_is_not_replanned_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            plan = shots_mod.locked_plan(
                shots_mod.shots_from_brief(SHOT_BRIEF), "fixture",
                duration_sec=9.0)
            (sku / "edit_plan.json").write_text(json.dumps(plan),
                                                encoding="utf-8")
            (sku / "brief.json").write_text(json.dumps({"duration_sec": 9.0}),
                                            encoding="utf-8")
            result = plan_stage.plan(sku, {"target_duration_sec": 9.0},
                                     dry_run=False)
            self.assertTrue(result["skipped_locked"])
            self.assertTrue(json.loads(
                (sku / "edit_plan.json").read_text())["locked"])

    def test_matching_shots_keep_the_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            (sku / "edit_plan.json").write_text(json.dumps(
                shots_mod.locked_plan(
                    shots_mod.shots_from_brief(SHOT_BRIEF), "fixture",
                    duration_sec=9.0)), encoding="utf-8")
            keep, why = locked_plan_state(sku)
            self.assertTrue(keep)
            self.assertIn("story.shots", why)

    def test_an_edited_brief_releases_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            original = shots_mod.locked_plan(
                shots_mod.shots_from_brief(SHOT_BRIEF), "fixture",
                duration_sec=9.0)
            (sku / "edit_plan.json").write_text(json.dumps(original),
                                                encoding="utf-8")
            edited = json.loads(json.dumps(SHOT_BRIEF))
            edited["story"]["shots"][0]["dur"] = 4.0
            (sku / "brief.json").write_text(json.dumps(edited),
                                            encoding="utf-8")
            keep, why = locked_plan_state(sku)
            self.assertFalse(keep)
            self.assertIn("changed", why)

    def test_music_only_skips_align(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
            from fenixsoft.ops import make
            run, why = make.stage_decision("align", sku, cfg)
            self.assertFalse(run)
            self.assertIn("music-only", why)

    def test_job_card_shows_the_order_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sku = self._project(root)
            music = root / "bed.wav"
            music.write_bytes(b"fake music")
            cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
            cfg["music"]["file"] = str(music)
            config_path = root / "pipeline.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            with mock.patch.object(job_ops, "resolve_sku_dir",
                                   return_value=sku), \
                    mock.patch.object(job_ops.runs, "latest_run",
                                      return_value=None), \
                    mock.patch.object(job_ops, "probe_duration",
                                      side_effect=lambda p: 10.0):
                card = build_card("fixture", config_path)
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    job_ops._print_human(card)
        self.assertEqual(card["mode"], "edit")
        self.assertTrue(card["order"]["locked"])
        self.assertEqual(card["order"]["source"], "brief")
        self.assertEqual(len(card["order"]["shots"]), 2)
        self.assertFalse(card["order"]["order_confirmed"])
        self.assertNotIn("voice.mp3", card["missing"])
        text = buffer.getvalue()
        self.assertIn("order", text)
        self.assertIn("locked by brief", text)
        self.assertIn("a.mp4", text)
        self.assertIn("NOT confirmed", text)

    def test_job_card_confirms_a_matching_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sku = self._project(root)
            (sku / "edit_plan.json").write_text(json.dumps(
                shots_mod.locked_plan(
                    shots_mod.shots_from_brief(SHOT_BRIEF), "fixture",
                    duration_sec=9.0)), encoding="utf-8")
            cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
            config_path = root / "pipeline.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            with mock.patch.object(job_ops, "resolve_sku_dir",
                                   return_value=sku), \
                    mock.patch.object(job_ops.runs, "latest_run",
                                      return_value=None), \
                    mock.patch.object(job_ops, "probe_duration",
                                      side_effect=lambda p: 10.0):
                card = build_card("fixture", config_path)
        self.assertTrue(card["order"]["order_confirmed"])


MANUAL_PLAN = {
    "sku": "fixture",
    "generated_by": "storyboard timings (corrected order: hook, detail)",
    "narration_lines": 2,
    "cuts": [
        {"line": 0, "text": "hook", "kind": "shot", "clip": "a.mp4",
         "in": 1.0, "dur": 5.0, "t0": 0.0},
        {"line": 1, "text": "detail", "kind": "shot", "clip": "b.mp4",
         "in": 0.25, "dur": 3.5, "t0": 5.0},
    ],
}


class ManualPlanTest(unittest.TestCase):
    """A hand-authored cut list is the delivered order until --force."""

    def _project(self, root: Path, *, with_shots: bool = True) -> Path:
        sku = root / "input" / "fixture"
        (sku / "clips").mkdir(parents=True)
        for name in ("a.mp4", "b.mp4"):
            (sku / "clips" / name).write_bytes(b"fake clip")
        (sku / "sku.json").write_text(json.dumps(
            {"voiceover": None, "duration_sec": 9.0}), encoding="utf-8")
        brief = SHOT_BRIEF if with_shots else {"duration_sec": 9.0}
        (sku / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
        (sku / "edit_plan.json").write_text(json.dumps(MANUAL_PLAN),
                                            encoding="utf-8")
        return sku

    def _cfg(self) -> dict:
        cfg = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        cfg.update({"min_cut_sec": 1.2, "target_duration_sec": 9.0})
        return cfg

    def test_plan_keeps_a_hand_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            before = (sku / "edit_plan.json").read_text()
            result = plan_stage.plan(sku, self._cfg(), dry_run=False)
            after = (sku / "edit_plan.json").read_text()
        self.assertTrue(result["skipped_manual"])
        self.assertTrue(result["locked"])
        self.assertTrue(result["manual"])
        self.assertEqual(result["cuts_per_video"], 2)
        self.assertEqual(result["total_sec"], 8.5)
        self.assertEqual(before, after)
        self.assertIn("storyboard timings",
                      result["generated_by"])

    def test_force_replaces_a_hand_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            cfg = self._cfg()
            with mock.patch.object(plan_stage, "probe_duration",
                                   side_effect=lambda p: 10.0), \
                    mock.patch.object(plan_stage, "artifact",
                                      side_effect=lambda s, n:
                                      Path(tmp) / n):
                result = plan_stage.plan(sku, cfg, dry_run=False, force=True)
            data = json.loads((sku / "edit_plan.json").read_text())
            prev = json.loads((sku / "edit_plan.prev.json").read_text())
        self.assertTrue(result["pass"])
        self.assertTrue(data["locked"])
        self.assertEqual(data["generated_by"], "fenix plan")
        self.assertEqual(data["order_source"], "brief")
        self.assertEqual(data["cuts"][0]["dur"], 5.5)
        self.assertEqual(prev["generated_by"], MANUAL_PLAN["generated_by"])

    def test_make_keeps_a_hand_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp))
            cfg = self._cfg()
            from fenixsoft.ops import make
            keep, why = locked_plan_state(sku)
            plan_run, plan_why = make.stage_decision("plan", sku, cfg)
            frames_run, frames_why = make.stage_decision("frames", sku, cfg)
        self.assertTrue(keep)
        self.assertIn("manual", why)
        self.assertFalse(plan_run)
        self.assertIn("manual", plan_why)
        self.assertFalse(frames_run)
        self.assertIn("explicit in/dur", frames_why)

    def test_job_reports_a_hand_locked_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sku = self._project(root)
            cfg = self._cfg()
            music = root / "bed.wav"
            music.write_bytes(b"fake music")
            cfg["music"]["file"] = str(music)
            config_path = root / "pipeline.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            with mock.patch.object(job_ops, "resolve_sku_dir",
                                   return_value=sku), \
                    mock.patch.object(job_ops.runs, "latest_run",
                                      return_value=None), \
                    mock.patch.object(job_ops, "probe_duration",
                                      side_effect=lambda p: 10.0):
                card = build_card("fixture", config_path)
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    job_ops._print_human(card)
        order = card["order"]
        self.assertTrue(order["locked"])
        self.assertTrue(order["manual"])
        self.assertEqual(order["source"], "plan")
        self.assertTrue(order["order_confirmed"])
        self.assertEqual(order["total_sec"], 8.5)
        self.assertEqual(order["checks"], [])
        text = buffer.getvalue()
        self.assertIn("locked plan (manual)", text)
        self.assertIn("a.mp4", text)
        self.assertNotIn("NOT confirmed", text)
        self.assertNotIn("brief FAILS", text)

    def test_planner_output_is_still_replaceable(self):
        with tempfile.TemporaryDirectory() as tmp:
            sku = self._project(Path(tmp), with_shots=False)
            generated = {**MANUAL_PLAN, "generated_by": "fenix plan"}
            (sku / "edit_plan.json").write_text(json.dumps(generated),
                                                encoding="utf-8")
            keep, why = locked_plan_state(sku)
        self.assertFalse(keep)
        self.assertEqual(why, "not locked")

    def test_make_force_passes_force_to_the_planner(self):
        from fenixsoft.ops.make import stage_argv
        self.assertEqual(stage_argv("plan", Path("/p"), force=True),
                         ["/p", "--force"])
        self.assertEqual(stage_argv("plan", Path("/p")), ["/p"])
        self.assertEqual(stage_argv("align", Path("/p"), force=True), ["/p"])


class PostChainTest(unittest.TestCase):
    def test_post_plan_always_runs_comp_and_post_qa(self):
        from fenixsoft.ops import make
        with tempfile.TemporaryDirectory() as tmp:
            sku = Path(tmp)
            (sku / "sku.json").write_text("{}", encoding="utf-8")
            rows = {r["stage"]: r for r in make.post_plan(sku, {})}
        self.assertTrue(rows["comp"]["run"])
        self.assertIn("--reuse", rows["comp"]["why"])
        self.assertTrue(rows["post qa"]["run"])
        self.assertFalse(rows["motion"]["run"])
        self.assertIn("no motion.json", rows["motion"]["why"])

    def test_a_changed_motion_spec_schedules_the_plate(self):
        from fenixsoft.ops import make
        with tempfile.TemporaryDirectory() as tmp:
            sku = Path(tmp)
            (sku / "motion.json").write_text("{}", encoding="utf-8")
            run, why = make._motion_decision(sku)
        self.assertTrue(run)
        self.assertIn("motion.json", why)


if __name__ == "__main__":
    unittest.main()
