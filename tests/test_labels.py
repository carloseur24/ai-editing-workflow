from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fenixsoft.context import load_config
from fenixsoft.domain import labels
from fenixsoft.domain import shots as shots_mod
from fenixsoft.render import render_video
from fenixsoft.stages import storyboard

SHOTS = [
    {"clip": "01_hook.mp4", "in": 0.0, "dur": 4.0,
     "label": "hook: box in hand", "spec": "GALAXY S25 ULTRA"},
    {"clip": "02_detail.mp4", "in": 0.0, "dur": 3.0,
     "label": "detail", "spec": "DYNAMIC AMOLED 2X", "text_zone": "lower"},
    {"clip": "03_unboxing.mp4", "in": 0.0, "dur": 2.5,
     "label": "unboxing", "spec": "512GB · SNAPDRAGON 8 ELITE"},
    {"clip": "04_camera.mp4", "in": 0.0, "dur": 5.0,
     "label": "camera", "spec": "200MP"},
    {"clip": "05_close.mp4", "in": 0.0, "dur": 5.5,
     "label": "close", "spec": "PRE-ORDER"},
]


class CardFromShotsTest(unittest.TestCase):
    def test_one_card_per_shot_inside_its_window(self):
        timeline = shots_mod.timeline(SHOTS)
        cards = labels.text_cards_from_shots(timeline, 20.0, load_config())
        self.assertEqual(len(cards), 5)
        for card, shot in zip(cards, timeline):
            self.assertGreaterEqual(card["start"], shot["t0"])
            self.assertLessEqual(card["end"], shot["t1"] + 1e-6)
            self.assertLess(card["start"], card["end"])
            self.assertEqual(card["text"], shot["spec"])
            self.assertEqual(card["sub"], shot["label"])

    def test_window_leaves_room_for_the_cut(self):
        cards = labels.text_cards_from_shots(
            shots_mod.timeline(SHOTS), 20.0, load_config())
        self.assertAlmostEqual(cards[0]["start"], 0.6)
        self.assertAlmostEqual(cards[0]["end"], 3.6)

    def test_card_does_not_cross_the_next_cut(self):
        cards = labels.text_cards_from_shots(
            shots_mod.timeline(SHOTS), 20.0, load_config())
        for i in range(len(cards) - 1):
            self.assertLess(cards[i]["end"], cards[i + 1]["start"])

    def test_zone_is_kept_for_the_renderer(self):
        cards = labels.text_cards_from_shots(
            shots_mod.timeline(SHOTS), 20.0, load_config())
        self.assertEqual(cards[1]["zone"], "lower")
        self.assertIsNone(cards[0]["zone"])

    def test_missing_label_and_spec_fall_back_to_the_clip_name(self):
        cards = labels.text_cards_from_shots(
            [{"clip": "03_unboxing_closeup.mp4", "in": 0, "dur": 2.0,
              "t0": 0.0}], 2.0, load_config())
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["text"], "Unboxing Closeup")
        self.assertEqual(cards[0]["sub"], "")

    def test_label_only_promotes_the_clause_to_the_big_line(self):
        cards = labels.text_cards_from_shots(
            [{"clip": "a.mp4", "in": 0, "dur": 2.0, "t0": 0.0,
              "label": "hook: box in hand"}], 2.0, load_config())
        self.assertEqual(cards[0]["text"], "box in hand")
        self.assertEqual(cards[0]["sub"], "")

    def test_short_shot_clamps_inside_the_shot(self):
        cards = labels.text_cards_from_shots(
            [{"clip": "a.mp4", "in": 0, "dur": 0.8, "t0": 3.0,
              "label": "quick", "spec": "FLASH"}], 3.8, load_config())
        self.assertEqual(len(cards), 1)
        self.assertGreater(cards[0]["start"], 3.0)
        self.assertLess(cards[0]["end"], 3.8)
        self.assertLess(cards[0]["start"], cards[0]["end"])

    def test_piece_duration_clamps_the_last_card(self):
        cards = labels.text_cards_from_shots(
            shots_mod.timeline(SHOTS), 19.0, load_config())
        self.assertTrue(cards)
        self.assertLessEqual(cards[-1]["end"], 19.0)

    def test_no_shots_yields_no_cards(self):
        self.assertEqual(labels.text_cards_from_shots([], 10.0, load_config()), [])

    def test_timing_can_come_from_config(self):
        cfg = load_config()
        cfg["labels"] = {"start_offset_sec": 0.2, "end_offset_sec": 0.2}
        cards = labels.text_cards_from_shots(
            shots_mod.timeline(SHOTS), 20.0, cfg)
        self.assertAlmostEqual(cards[0]["start"], 0.2)
        self.assertAlmostEqual(cards[0]["end"], 3.8)


class StoryboardCardsTest(unittest.TestCase):
    def test_storyboard_writes_one_card_per_locked_shot(self):
        project = "p2_cards_tmp"
        shots = [
            {"clip": "01_hook.mp4", "in": 0.0, "dur": 2.0,
             "label": "hook", "spec": "S25 ULTRA"},
            {"clip": "02_detail.mp4", "in": 0.0, "dur": 2.0,
             "label": "detail", "spec": "AMOLED"},
            {"clip": "03_close.mp4", "in": 0.0, "dur": 2.0,
             "label": "close", "spec": "PRE-ORDER"},
        ]
        brief = {"duration_sec": 6.0, "title": "T", "project_type": "product",
                 "format": {"aspect": "9:16", "width": 1080,
                            "height": 1920, "fps": 60},
                 "story": {"shots": shots}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            project_dir = input_dir / project
            project_dir.mkdir(parents=True)
            (project_dir / "brief.json").write_text(
                json.dumps(brief), encoding="utf-8")
            out = root / "output"
            with mock.patch.object(storyboard, "INPUT_DIR", input_dir), \
                    mock.patch.object(storyboard, "output_dir",
                                      lambda name: out / name), \
                    mock.patch.object(storyboard, "clip_durations",
                                      lambda _p, _b: {s["clip"]: 10.0
                                                      for s in shots}):
                storyboard.main([project])
            cards_path = out / project / "text_cards.json"
            self.assertTrue(cards_path.exists())
            cards = json.loads(cards_path.read_text(encoding="utf-8"))
            self.assertEqual(len(cards), 3)
            self.assertEqual([c["text"] for c in cards],
                             ["S25 ULTRA", "AMOLED", "PRE-ORDER"])
            self.assertEqual(cards[0]["start"], 0.6)

    def test_load_text_cards_prefers_the_authored_list(self):
        sku = {"text_cards": [{"text": "MINE", "start": 0, "end": 1}]}
        self.assertEqual(render_video.load_text_cards(sku, Path("/nonexistent")),
                         sku["text_cards"])

    def test_load_text_cards_reads_the_storyboard_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text_cards.json"
            path.write_text(json.dumps([{"text": "DERIVED", "start": 0,
                                         "end": 1}]), encoding="utf-8")
            with mock.patch.object(render_video, "artifact",
                                   lambda _sku, _name: path):
                cards = render_video.load_text_cards({}, Path(tmp))
            self.assertEqual(cards[0]["text"], "DERIVED")


if __name__ == "__main__":
    unittest.main()
