from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from fenixsoft.context import load_config
from fenixsoft.render.hf_overlay import (_has_header, _html, build_states,
                                         verify_png_alpha)
from fenixsoft.render.render_video import FontBook, build_overlay


def overlay_for(sku: dict) -> dict:
    cfg = load_config()
    # The checked-in config parks the price/badge rows off canvas for one job;
    # this suite tests the drawing code, so pin the documented safe zone.
    cfg["safe_zones"]["reels_universal"].update(
        {"badge_y": 254, "price_y": 1310})
    return build_overlay(cfg, sku, FontBook())


class EmptyOverlayTest(unittest.TestCase):
    def test_absent_fields_draw_no_pixels(self):
        overlay = overlay_for({"safe_zone_mode": "reels_universal"})
        self.assertEqual(int(overlay["alpha"].max()), 0)
        self.assertEqual(overlay["layout"]["title"]["size"], 0)
        self.assertEqual(overlay["layout"]["footer"]["width"], 0)
        self.assertEqual(overlay["layout"]["price"]["size"], 0)
        self.assertEqual(overlay["layout"]["badges"], [])

    def test_empty_strings_are_treated_as_absent(self):
        overlay = overlay_for({"title": "", "subtitle": "  ",
                               "footer": "", "price_label": "",
                               "badges": ["", "  "],
                               "safe_zone_mode": "reels_universal"})
        self.assertEqual(int(overlay["alpha"].max()), 0)

    def test_a_title_alone_draws_and_no_price_pill_appears(self):
        overlay = overlay_for({"title": "ONLY TITLE",
                               "safe_zone_mode": "reels_universal"})
        self.assertGreater(int(overlay["alpha"].max()), 0)
        self.assertEqual(overlay["layout"]["price"]["size"], 0)
        self.assertEqual(overlay["layout"]["price"]["x1"], 0)

    def test_a_price_alone_keeps_the_bottom_stack_check(self):
        overlay = overlay_for({"price_label": "$10",
                               "safe_zone_mode": "reels_universal"})
        self.assertGreater(int(overlay["alpha"].max()), 0)
        self.assertGreater(overlay["layout"]["price"]["x1"],
                           overlay["layout"]["price"]["x0"])


class CaptionsOnlyOverlayTest(unittest.TestCase):
    def setUp(self):
        self.layout = overlay_for({"safe_zone_mode": "reels_universal"})["layout"]

    def test_header_is_reported_as_absent(self):
        self.assertFalse(_has_header(self.layout))

    def test_html_has_no_stray_header_nodes(self):
        cards = [{"text": "HOLA", "s": 0.0, "e": 1.0, "size": 46, "x": 300,
                  "y": 1445, "w": 300, "h": 60, "gap": 10,
                  "words": [{"w": "HOLA", "s": 0.0, "e": 1.0}]}]
        html = _html(self.layout, cards, build_states(cards, 1.5))
        self.assertIn('id="captions"', html)
        self.assertNotIn('id="title"', html)
        self.assertNotIn('id="rule"', html)
        self.assertNotIn('id="price"', html)

    def test_alpha_check_accepts_a_captions_only_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "state.png"
            img = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle([340, 1445, 740, 1505], fill=(17, 24, 39, 255))
            img.save(png)
            caption_only = verify_png_alpha(png, header_expected=False)
            self.assertTrue(caption_only["ok"], caption_only)
            self.assertFalse(caption_only["header_expected"])
            # The same frame must still fail when a header was promised.
            self.assertFalse(verify_png_alpha(png, header_expected=True)["ok"])

    def test_html_still_declares_the_header_when_one_exists(self):
        layout = overlay_for({"title": "T", "subtitle": "S",
                              "safe_zone_mode": "reels_universal"})["layout"]
        cards = [{"text": "HOLA", "s": 0.0, "e": 1.0, "size": 46, "x": 300,
                  "y": 1445, "w": 300, "h": 60, "gap": 10,
                  "words": [{"w": "HOLA", "s": 0.0, "e": 1.0}]}]
        html = _html(layout, cards, build_states(cards, 1.5))
        self.assertIn('id="title"', html)
        self.assertIn('id="rule"', html)


if __name__ == "__main__":
    unittest.main()
