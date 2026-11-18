from __future__ import annotations

import unittest

from fenixsoft.context import load_config
from fenixsoft.domain import safe_zones
from fenixsoft.domain import text_cards as tc
from fenixsoft.render.motion import plate

CFG = {
    "canvas": {"width": 1080, "height": 1920, "fps": 60},
    "safe_zones": {
        "reels_universal": {"title_y": 118, "sub_y": 194, "caption_y": 1445},
        "status_story": {"title_y": 118, "sub_y": 194, "caption_y": 1485},
    },
}


class ZoneResolveTest(unittest.TestCase):
    def test_header_and_lower_read_the_safe_zone(self):
        self.assertEqual(safe_zones.zone_y("header", CFG,
                                           mode="reels_universal"), 118)
        self.assertEqual(safe_zones.zone_y("lower", CFG,
                                           mode="reels_universal"), 1445)
        self.assertEqual(safe_zones.zone_y("lower", CFG,
                                           mode="status_story"), 1485)

    def test_center_uses_the_canvas(self):
        self.assertEqual(safe_zones.zone_y("center", CFG, size=86),
                         (1920 - 86) // 2)

    def test_unknown_or_missing_zone_is_none(self):
        self.assertIsNone(safe_zones.zone_y(None, CFG))
        self.assertIsNone(safe_zones.zone_y("somewhere", CFG))
        self.assertIsNone(safe_zones.zone_y(True, CFG))

    def test_describe_lists_every_band(self):
        bands = safe_zones.describe(CFG, mode="reels_universal")
        self.assertEqual(bands["header"], 118)
        self.assertEqual(bands["lower"], 1445)
        self.assertIn("center", bands)


class TextCardZoneTest(unittest.TestCase):
    def resolver(self, zone, size):
        return safe_zones.zone_y(zone, CFG, mode="reels_universal", size=size)

    def test_zone_sets_y_without_an_explicit_coordinate(self):
        cards = tc.normalize([{"text": "A", "sub": "B", "start": 0, "end": 2,
                              "zone": "lower"}],
                            zone_resolver=self.resolver)
        self.assertEqual(cards[0]["y"], 1445)
        self.assertFalse(cards[0]["explicit_y"])
        self.assertEqual(cards[0]["zone"], "lower")

    def test_explicit_y_wins_over_the_zone(self):
        cards = tc.normalize([{"text": "A", "start": 0, "end": 2,
                              "zone": "lower", "y": 300}],
                            zone_resolver=self.resolver)
        self.assertEqual(cards[0]["y"], 300)
        self.assertTrue(cards[0]["explicit_y"])

    def test_a_shot_zone_only_moves_cards_without_their_own_placement(self):
        cards = tc.normalize([{"text": "A", "start": 0, "end": 2},
                              {"text": "B", "start": 0, "end": 2, "y": 300},
                              {"text": "C", "start": 0, "end": 2,
                               "zone": "header"}],
                             zone_resolver=self.resolver)
        self.assertEqual(tc.resolve_y(cards[0], "lower", cfg=CFG,
                                      mode="reels_universal"), 1445)
        self.assertEqual(tc.resolve_y(cards[1], "lower", cfg=CFG,
                                      mode="reels_universal"), 300)
        self.assertEqual(tc.resolve_y(cards[2], "lower", cfg=CFG,
                                      mode="reels_universal"), 118)

    def test_without_a_resolver_the_card_keeps_the_default_y(self):
        cards = tc.normalize([{"text": "A", "start": 0, "end": 2,
                              "zone": "lower"}])
        self.assertEqual(cards[0]["y"], tc.DEFAULTS["y"])
        self.assertEqual(cards[0]["zone"], "lower")

    def test_config_has_the_documented_zones(self):
        cfg = load_config()
        for mode in ("reels_universal", "status_story"):
            self.assertIn("title_y", cfg["safe_zones"][mode])
            self.assertIn("caption_y", cfg["safe_zones"][mode])


class MotionZoneTest(unittest.TestCase):
    def test_zone_replaces_y_and_stacks_within_a_band(self):
        spec = {"text": [
            {"text": "TITLE", "zone": "lower", "size": 60},
            {"text": "SPEC", "zone": "lower", "size": 34},
        ]}
        warnings = plate.resolve_text_zones(spec, CFG)
        self.assertEqual(warnings, [])
        self.assertEqual(spec["text"][0]["y"], 1445)
        self.assertEqual(spec["text"][1]["y"], 1445 + int(round(60 * 1.35)))

    def test_explicit_y_is_untouched(self):
        spec = {"text": [{"text": "TITLE", "zone": "lower", "y": 250,
                          "size": 60}]}
        plate.resolve_text_zones(spec, CFG)
        self.assertEqual(spec["text"][0]["y"], 250)

    def test_unknown_zone_warns_and_leaves_y_absent(self):
        spec = {"text": [{"text": "TITLE", "zone": "banner", "size": 60}]}
        warnings = plate.resolve_text_zones(spec, CFG)
        # an unknown zone is normalised to None and simply ignored
        self.assertEqual(warnings, [])
        self.assertNotIn("y", spec["text"][0])


if __name__ == "__main__":
    unittest.main()
