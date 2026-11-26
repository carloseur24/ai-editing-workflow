"""speech_coverage: does every span of speech sit under a caption?

Synthetic audio only. The fixture is three speech bursts (a 1 kHz tone, well
inside the 300-3000 Hz band) on silence; the middle burst stands in for a line
the captions missed. Boundaries sit on the 0.25 s hop, so a window is either
inside a burst or in silence; only the onset window mixes both, which is why
the measured gap starts one hop after the burst.
"""
from __future__ import annotations

import unittest

import numpy as np

from fenixsoft.gates.qa_check import speech_coverage

SR = 48000
TONE_HZ = 1000.0        # 48 samples/cycle at 48 kHz: 500 cycles per window
AMP = 0.5

BURST1 = (0.5, 3.5)      # captioned, long enough to calibrate
GAP = (5.0, 6.5)         # the uncaptioned burst
BURST3 = (7.75, 10.75)   # captioned
TOTAL = 11.5
CUES_ALL = [BURST1, GAP, BURST3]


def _blank(total: float = TOTAL) -> np.ndarray:
    return np.zeros(int(total * SR), dtype=np.float64)


def _tone(pcm: np.ndarray, t0: float, t1: float, freq: float = TONE_HZ,
          amp: float = AMP) -> np.ndarray:
    i0, i1 = int(round(t0 * SR)), int(round(t1 * SR))
    t = np.arange(i1 - i0) / SR
    pcm[i0:i1] += amp * np.sin(2 * np.pi * freq * t)
    return pcm


def _speech(spans, total: float = TOTAL) -> np.ndarray:
    pcm = _blank(total)
    for t0, t1 in spans:
        _tone(pcm, t0, t1)
    return pcm


class SpeechCoverageTest(unittest.TestCase):
    def test_uncaptioned_burst_is_one_gap(self):
        pcm = _speech([BURST1, GAP, BURST3])
        cover = speech_coverage(pcm, SR, [BURST1, BURST3], {})
        self.assertIsNone(cover["skipped"])
        self.assertGreaterEqual(cover["covered_windows"], 20)
        self.assertEqual(len(cover["gaps"]), 1)
        gap = cover["gaps"][0]
        # The onset window is the filter settling on a hard tone start, so
        # the run begins one hop late; the end lands on the burst edge.
        self.assertAlmostEqual(gap["start"], GAP[0], delta=0.3)
        self.assertAlmostEqual(gap["end"], GAP[1], delta=0.1)
        self.assertAlmostEqual(gap["end"] - gap["start"], 1.25, delta=0.15)
        self.assertGreaterEqual(cover["uncovered_s"], 1.0)
        self.assertFalse(cover["ok"])
        # The floor sits on the speech, ~-9 dBFS for this tone, never on 0.
        self.assertLess(cover["speech_floor_db"], -6.0)

    def test_fully_captioned_burst_has_no_gap(self):
        pcm = _speech([BURST1, GAP, BURST3])
        cover = speech_coverage(pcm, SR, CUES_ALL, {})
        self.assertEqual(cover["gaps"], [])
        self.assertEqual(cover["uncovered_s"], 0.0)
        self.assertTrue(cover["ok"])

    def test_music_bed_below_speech_is_not_a_gap(self):
        # A continuous music-like bed 20 dB under the voice, plus a broadband
        # noise floor. The bed lands near -29 dBFS: above the -40 dBFS
        # absolute floor that flags music on a long file, yet 20 dB under the
        # calibrated speech floor, so the uncaptioned silence stays quiet.
        pcm = _blank()
        _tone(pcm, 0.0, TOTAL, freq=660.0, amp=AMP * 10 ** (-20 / 20))
        pcm += np.random.default_rng(7).normal(0.0, 2e-3, pcm.size)
        _tone(pcm, *BURST1)
        _tone(pcm, *BURST3)
        cover = speech_coverage(pcm, SR, [BURST1, BURST3], {})
        self.assertEqual(cover["gaps"], [])
        self.assertEqual(cover["uncovered_s"], 0.0)
        self.assertTrue(cover["ok"])

    def test_short_swell_is_not_a_gap(self):
        pcm = _speech([BURST1, BURST3])
        _tone(pcm, 5.0, 5.4)          # 0.4 s shout between two lines
        cover = speech_coverage(pcm, SR, [BURST1, BURST3], {})
        self.assertEqual(cover["gaps"], [])
        self.assertTrue(cover["ok"])

    def test_run_shorter_than_sustain_is_not_a_gap(self):
        # 0.75 s of speech does fill two windows, so only min_sustain_s keeps
        # it out of the report; lowering the threshold must surface it.
        pcm = _speech([BURST1, BURST3])
        _tone(pcm, 5.25, 6.0)
        loose = {"qa": {"speech_coverage": {"min_sustain_s": 0.5}}}
        self.assertEqual(
            len(speech_coverage(pcm, SR, [BURST1, BURST3], loose)["gaps"]), 1)
        self.assertEqual(
            speech_coverage(pcm, SR, [BURST1, BURST3], {})["gaps"], [])

    def test_no_caption_stream_is_info_only(self):
        pcm = _speech([BURST1])
        cover = speech_coverage(pcm, SR, [], {})
        self.assertEqual(cover["skipped"],
                         "not enough captioned audio to calibrate")
        self.assertTrue(cover["ok"])
        self.assertEqual(cover["gaps"], [])
        self.assertEqual(cover["uncovered_s"], 0.0)
        self.assertIsNone(cover["speech_floor_db"])


if __name__ == "__main__":
    unittest.main()
