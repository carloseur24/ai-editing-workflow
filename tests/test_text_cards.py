from __future__ import annotations

import unittest

import numpy as np

from fenixsoft.context import load_config
from fenixsoft.domain import text_cards as tc
from fenixsoft.render.render_video import (FontBook, TextCardRenderer,
                                           build_overlay)

CARD = {"text": "GALAXY S25 ULTRA", "sub": "512GB · SNAPDRAGON 8 ELITE",
        "start": 1.0, "end": 3.0, "enter_sec": 0.4, "exit_sec": 0.3,
        "rise_px": 20, "y": 250, "size": 80, "sub_size": 32}


class CardTimingTest(unittest.TestCase):
    def test_defaults_fill_missing_fields(self):
        cards = tc.normalize([{"text": "A", "sub": "B", "start": 0, "end": 2}])
        self.assertEqual(cards[0]["size"], tc.DEFAULTS["size"])
        self.assertEqual(cards[0]["sub_color"], tc.DEFAULTS["sub_color"])
        self.assertEqual(cards[0]["y"], tc.DEFAULTS["y"])

    def test_per_card_values_win(self):
        cards = tc.normalize([CARD])
        self.assertEqual(cards[0]["size"], 80)
        self.assertEqual(cards[0]["sub_size"], 32)
        self.assertEqual(cards[0]["rise_px"], 20)

    def test_empty_and_inverted_cards_are_dropped(self):
        cards = tc.normalize([
            {"text": "", "sub": "", "start": 0, "end": 1},
            {"text": "A", "start": 2, "end": 1},
            "not a dict",
        ])
        self.assertEqual(cards, [])

    def test_sub_only_card_takes_the_sub_size(self):
        cards = tc.normalize([{"sub": "ONLY SPEC", "start": 0, "end": 1}])
        self.assertEqual(cards[0]["size"], tc.DEFAULTS["sub_size"])

    def test_alpha_ramps_in_and_out(self):
        card = tc.normalize([CARD])[0]
        self.assertEqual(tc.alpha_at(card, 0.5), (0.0, 0))
        alpha, rise = tc.alpha_at(card, 1.0)
        self.assertAlmostEqual(alpha, 0.0, places=3)
        self.assertEqual(rise, 20)
        alpha, rise = tc.alpha_at(card, 1.4)
        self.assertAlmostEqual(alpha, 1.0, places=3)
        self.assertEqual(rise, 0)
        self.assertGreater(tc.alpha_at(card, 2.0)[0], 0.9)
        self.assertLess(tc.alpha_at(card, 2.95)[0], 0.5)
        self.assertEqual(tc.alpha_at(card, 3.1), (0.0, 0))

    def test_active_cards_follow_the_window(self):
        cards = tc.normalize([CARD])
        self.assertEqual(tc.active_at(cards, 0.4), [])
        self.assertEqual(len(tc.active_at(cards, 2.0)), 1)

    def test_sub_line_sits_below_the_title(self):
        card = tc.normalize([CARD])[0]
        self.assertEqual(tc.sub_y(card), 250 + 80 + 24)

    def test_audit_lists_every_card_window(self):
        audit = tc.audit(tc.normalize([CARD]))
        self.assertEqual(audit["count"], 1)
        self.assertEqual(audit["total_seconds"], 2.0)


class CardRendererTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.fonts = FontBook()
        self.overlay = build_overlay(self.cfg, {"safe_zone_mode":
                                                "reels_universal"},
                                     self.fonts)

    def _renderer(self):
        return TextCardRenderer(self.cfg, self.overlay, self.fonts, [CARD])

    def test_card_paints_pixels_inside_its_window(self):
        canvas = np.zeros((1920, 1080, 3), dtype=np.uint8)
        drawn = self._renderer().draw(canvas, 2.0)
        self.assertEqual(drawn, 1)
        self.assertGreater(int(canvas.sum()), 0)

    def test_card_paints_nothing_outside_its_window(self):
        canvas = np.zeros((1920, 1080, 3), dtype=np.uint8)
        self.assertEqual(self._renderer().draw(canvas, 0.2), 0)
        self.assertEqual(int(canvas.sum()), 0)

    def test_patch_is_cached_between_frames(self):
        renderer = self._renderer()
        canvas = np.zeros((1920, 1080, 3), dtype=np.uint8)
        renderer.draw(canvas, 2.0)
        self.assertEqual(len(renderer.cache), 1)
        renderer.draw(canvas, 2.1)
        self.assertEqual(len(renderer.cache), 1)

    def test_audit_reports_the_card(self):
        self.assertEqual(self._renderer().audit()["count"], 1)


if __name__ == "__main__":
    unittest.main()
