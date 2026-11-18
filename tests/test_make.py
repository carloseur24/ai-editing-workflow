from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fenixsoft.context import DEFAULT_CONFIG
from fenixsoft.ops import finish
from fenixsoft.ops import job as job_ops
from fenixsoft.ops.job import build_card
from fenixsoft.ops.make import (_align_current, config_digest, config_state,
                                is_fresh, write_config_stamp)
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


if __name__ == "__main__":
    unittest.main()
