from __future__ import annotations

import re
import unittest
from pathlib import Path

from fenixsoft.render.hf_overlay import (STATE_FPS, _ffconcat, _html,
                                         build_states)

CARDS = [
    {"text": "UN IPHONE 14", "s": 0.0, "e": 1.4, "size": 46, "x": 300,
     "y": 1506, "w": 380, "h": 62, "gap": 11,
     "words": [{"w": "UN", "s": 0.0, "e": 0.143},
               {"w": "IPHONE", "s": 0.143, "e": 0.302},
               {"w": "14", "s": 0.302, "e": 0.762}]},
    {"text": "POR 330", "s": 1.4, "e": 2.8, "size": 46, "x": 340,
     "y": 1506, "w": 300, "h": 62, "gap": 11,
     "words": [{"w": "POR", "s": 1.4, "e": 1.9},
               {"w": "330", "s": 1.9, "e": 2.8}]},
]

LAYOUT = {
    "canvas": {"width": 1080, "height": 1920},
    "cx": 507,
    "zone": {"x_min": 65, "x_max": 950, "price_y": 1310,
             "badge_y": 254},
    "title": {"text": 'iPhone 14 "Pro" & co', "x": 250, "y": 118,
              "size": 58, "width": 512},
    "subtitle": {"text": "Azul", "x": 300, "y": 194, "size": 34, "width": 200},
    "badges": [],
    "price": {"text": "$330", "x0": 300, "y0": 1310, "x1": 700, "y1": 1391,
              "size": 54, "radius": 24},
    "footer": {"text": "@fenixsoft", "x": 300, "y": 1585, "size": 28,
               "width": 300},
    "typography": {"badge_size": 26},
}


class StateTimelineTest(unittest.TestCase):
    def setUp(self):
        self.states = build_states(CARDS, 2.8)

    def test_states_cover_the_timeline_contiguously(self):
        self.assertEqual(self.states[0]["start"], 0.0)
        self.assertEqual(self.states[-1]["end"], 2.8)
        for prev, nxt in zip(self.states, self.states[1:]):
            self.assertAlmostEqual(prev["end"], nxt["start"], places=4)

    def test_card_and_word_indices_are_valid(self):
        for state in self.states:
            if state["card"] is None:
                continue
            card = CARDS[state["card"]]
            self.assertLess(state["active"], len(card["words"]))
            self.assertEqual(len(state["words"]), len(card["words"]))

    def test_card_spring_starts_below_one_and_settles(self):
        card0 = [s for s in self.states if s["card"] == 0]
        self.assertLess(card0[0]["in"], 1.0)
        self.assertAlmostEqual(card0[-1]["in"], 1.0, places=2)

    def test_state_count_is_much_smaller_than_frames(self):
        # 2.8 s at 60 fps is 168 frames; the state cache must be smaller
        self.assertLess(len(self.states), 120)

    def test_empty_gap_gets_its_own_state(self):
        states = build_states([CARDS[1]], 3.5)
        before = [s for s in states if s["start"] < 1.4 - 1e-6]
        self.assertTrue(before)
        self.assertTrue(all(s["card"] is None for s in before))
        first_card = next(s for s in states if s["card"] == 0)
        self.assertAlmostEqual(first_card["start"], 1.4, places=2)

    def test_ffconcat_durations_sum_to_the_duration(self):
        pngs = [Path(f"/tmp/frame_{i:06d}.png") for i in range(len(self.states))]
        text = _ffconcat(pngs, [s["start"] for s in self.states], 2.8)
        durations = [float(x) for x in re.findall(r"duration ([0-9.]+)", text)]
        self.assertAlmostEqual(sum(durations), 2.8, places=2)
        self.assertEqual(text.count("file '"), len(pngs) + 1)

    def test_state_fps_matches_the_composition(self):
        self.assertEqual(STATE_FPS, 30)


class HtmlEscapingTest(unittest.TestCase):
    def test_text_is_html_escaped_not_json_quoted(self):
        html = _html(LAYOUT, CARDS, build_states(CARDS, 2.8))
        self.assertIn("&quot;Pro&quot;", html)
        self.assertIn("&amp; co", html)
        self.assertNotIn('"Pro"', html)

    def test_composition_declares_the_canvas_and_state_fps(self):
        html = _html(LAYOUT, CARDS, build_states(CARDS, 2.8))
        self.assertIn('data-width="1080"', html)
        self.assertIn('data-height="1920"', html)
        self.assertIn(f'data-fps="{STATE_FPS}"', html)
        self.assertIn("background:transparent", html)


if __name__ == "__main__":
    unittest.main()
