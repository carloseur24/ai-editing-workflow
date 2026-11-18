from __future__ import annotations

import unittest

from fenixsoft.domain.voice import (explicit_duration, is_music_only,
                                    voice_name)
from fenixsoft.gates.qa_check import VO_CHECK_IDS, strip_vo_checks
from fenixsoft.render.render_video import audio_filter_graph

CFG = {"audio": {"duck_threshold": 0.03, "duck_ratio": 6,
                 "duck_attack_ms": 8, "duck_release_ms": 350}}


class DetectionTest(unittest.TestCase):
    def test_missing_key_keeps_the_legacy_voice_default(self):
        self.assertFalse(is_music_only({}))
        self.assertEqual(voice_name({}), "voice.mp3")

    def test_explicit_none_and_none_string_are_music_only(self):
        self.assertTrue(is_music_only({"voiceover": None}))
        self.assertTrue(is_music_only({"voiceover": "none"}))
        self.assertTrue(is_music_only({"voiceover": "MUSIC"}))
        self.assertTrue(is_music_only({"voiceover": ""}))

    def test_a_real_file_is_not_music_only(self):
        self.assertFalse(is_music_only({"voiceover": "voice.mp3"}))

    def test_explicit_duration_precedence(self):
        cfg = {"target_duration_sec": 28}
        self.assertEqual(explicit_duration(
            {"duration_sec": 33}, {"duration_sec": 30}, cfg), 33)
        self.assertEqual(explicit_duration(
            {"duration_sec": None}, {"duration_sec": 30}, cfg), 30)
        self.assertEqual(explicit_duration({}, {}, cfg), 28)
        self.assertEqual(explicit_duration({}, {}, cfg, fallback=12), 12)


class AudioGraphTest(unittest.TestCase):
    def test_voice_graph_ducks_the_bed_under_three_inputs(self):
        graph = audio_filter_graph(CFG, 10.0, voice=True)
        self.assertIn("amix=inputs=3", graph)
        self.assertIn("sidechaincompress", graph)
        self.assertIn("[1:a]", graph)

    def test_music_only_graph_has_no_voice_or_sidechain(self):
        graph = audio_filter_graph(CFG, 10.0, voice=False)
        self.assertIn("amix=inputs=2", graph)
        self.assertNotIn("sidechaincompress", graph)
        self.assertNotIn("[2:a]", graph)
        self.assertIn("atrim=0:10.000", graph)


class QaSkipTest(unittest.TestCase):
    def _checks(self):
        return [{"id": cid, "ok": True, "severity": "error"}
                for cid in ("v_resolution", "d_matches_vo", "c_timings",
                            "c_caption_text", "a_lufs")]

    def test_music_only_drops_the_voice_and_caption_rows(self):
        kept, skipped = strip_vo_checks(self._checks(), True)
        ids = {c["id"] for c in kept}
        self.assertNotIn("c_timings", ids)
        self.assertNotIn("c_caption_text", ids)
        self.assertIn("d_matches_vo", ids)     # duration still checked
        self.assertIn("a_lufs", ids)
        self.assertIn("a_music_only", ids)
        self.assertEqual(set(skipped), {"c_timings", "c_caption_text"})

    def test_native_piece_keeps_every_row(self):
        checks = self._checks()
        kept, skipped = strip_vo_checks(checks, False)
        self.assertEqual(kept, checks)
        self.assertEqual(skipped, [])

    def test_skip_list_is_not_empty(self):
        self.assertTrue(VO_CHECK_IDS)


if __name__ == "__main__":
    unittest.main()
