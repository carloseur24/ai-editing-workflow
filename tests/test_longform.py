from __future__ import annotations

import unittest

from fenixsoft.stages import longform

SRC = "tests/fixtures/placeholder.mp4"


def spec():
    return {"width": 1080, "height": 1920, "fps": 30,
            "chapters": [{"title": "A", "source": SRC, "in": 0, "out": 2},
                         {"title": "B", "source": SRC, "in": 1, "out": 2.5}]}


class LongformTest(unittest.TestCase):
    def test_total_duration_sums_chapters(self):
        self.assertAlmostEqual(longform.total_duration(spec()), 3.5, places=3)

    def test_valid_spec(self):
        self.assertEqual(longform.validate(spec()), [])

    def test_out_must_be_greater_than_in(self):
        s = spec()
        s["chapters"][0]["out"] = 0
        self.assertTrue(any("out must be > in" in e
                            for e in longform.validate(s)))

    def test_missing_source_is_rejected(self):
        s = spec()
        s["chapters"][0]["source"] = "nope.mp4"
        self.assertTrue(any("source not found" in e
                            for e in longform.validate(s)))

    def test_multicam_expands_into_angle_chapters(self):
        s = {"multicam": {"angles": ["a.mp4", "b.mp4"],
                          "cuts": [1.0, 2.0], "audio": "vo.wav",
                          "audio_offset": -0.2}}
        out = longform.expand_multicam(s)
        self.assertEqual(len(out["chapters"]), 2)
        self.assertEqual(out["chapters"][0]["source"], "a.mp4")
        self.assertEqual(out["chapters"][1]["source"], "b.mp4")
        self.assertAlmostEqual(out["chapters"][1]["in"], 1.0, places=3)
        self.assertEqual(out["audio"], "vo.wav")
        self.assertAlmostEqual(out["audio_offset"], -0.2, places=3)

    def test_transition_shortens_the_master(self):
        s = spec()
        s["transition_sec"] = 0.5
        self.assertAlmostEqual(longform.total_duration(s), 3.0, places=3)

    def test_unknown_transition_is_rejected(self):
        s = spec()
        s["transition"] = "starwipe"
        self.assertTrue(any("unknown transition" in e
                            for e in longform.validate(s)))

    def test_chapters_markdown_has_timestamps(self):
        md = longform.chapters_markdown(spec())
        self.assertIn("| A |", md)
        self.assertIn("| B |", md)
        self.assertIn("2.00", md)


if __name__ == "__main__":
    unittest.main()
