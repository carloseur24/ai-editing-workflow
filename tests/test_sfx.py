from __future__ import annotations

import unittest

from fenixsoft.stages import sfx


def _cuts() -> list[dict]:
    return [
        {"t0": 0.0, "dur": 2.0, "clip": "01.mp4", "kind": "hook"},
        {"t0": 2.0, "dur": 2.0, "clip": "02.mp4", "kind": "proof"},
        {"t0": 4.0, "dur": 2.0, "clip": "03.mp4", "kind": "proof"},
        {"t0": 6.0, "dur": 2.0, "clip": "04.mp4", "kind": "proof"},
        {"t0": 8.0, "dur": 2.0, "clip": "05.mp4", "kind": "cta"},
    ]


def _words() -> list[dict]:
    return [
        {"w": "330", "s": 0.8, "e": 1.0},
        {"w": "100%", "s": 5.6, "e": 5.9},
        {"w": "GARANTÍA", "s": 7.2, "e": 7.5},
        {"w": "C-29", "s": 8.5, "e": 8.7},
    ]


def _policy() -> dict:
    return {
        "vacuum_ms": 120,
        "jcut_min_ms": 150,
        "sub_spacing_sec": 1.5,
        "music_bed_lufs": -28.0,
        "duration_sec": 10.0,
    }


def _enrich(cues: list[dict]) -> list[dict]:
    for cue in cues:
        if cue["layer"] == "env":
            continue
        cue.setdefault("start_sec", round(max(0.0, cue["t_sec"] - 0.2), 3))
        cue.setdefault("sample_sec", 0.5)
        cue.setdefault("onset_lead_ms", -200.0)
        cue.setdefault("onset_sec", 0.05)
        cue.setdefault("anchor_sec", 0.05)
    return cues


class CueSheetTest(unittest.TestCase):
    def test_layers_and_keywords(self):
        cues = sfx.plan_cues(_cuts(), _words(), 10.0, "assets/music/x.wav",
                             -28.0, _policy())
        layers = {cue["layer"] for cue in cues}
        self.assertTrue({"env", "anticipation", "tactile", "impact",
                         "accent"}.issubset(layers))

        def has(role: str, t: float) -> bool:
            return any(cue["role"] == role and abs(cue["t_sec"] - t) < 0.01
                       for cue in cues)

        self.assertTrue(has("impact_sub", 0.0), "opening sub")
        self.assertTrue(has("impact_sub", 5.6), "100% keyword sub")
        self.assertTrue(has("impact_sub", 8.0), "final lock sub")
        self.assertTrue(has("chime_lock", 7.38), "warranty chime lands late")
        self.assertTrue(has("click_tactile", 7.2), "warranty click")
        self.assertFalse(has("whoosh_air", 8.0), "final uses a riser, not a whoosh")
        self.assertTrue(has("riser_tension", 8.0))

    def test_ascii_token_normalises_accents_and_punctuation(self):
        self.assertEqual(sfx._ascii_token("GARANTÍA"), "GARANTIA")
        self.assertEqual(sfx._ascii_token("100%"), "100")
        self.assertEqual(sfx._ascii_token("E-SIM"), "ESIM")
        self.assertEqual(sfx._ascii_token("C-29"), "C29")

    def test_bus_gate_passes_a_balanced_sheet(self):
        cues = _enrich(sfx.plan_cues(_cuts(), _words(), 10.0,
                                     "assets/music/x.wav", -28.0, _policy()))
        checks = sfx.evaluate(cues, -3.0, -28.0, _policy())
        self.assertTrue(all(c["pass"] for c in checks),
                        [c for c in checks if not c["pass"]])

    def test_sub_gap_gate_catches_stacked_hits(self):
        cues = _enrich(sfx.plan_cues(_cuts(), _words(), 10.0,
                                     "assets/music/x.wav", -28.0, _policy()))
        cues.append({"id": "extra-sub", "t_sec": 4.3, "layer": "impact",
                     "role": "impact_sub", "major": True,
                     "start_sec": 4.1, "sample_sec": 0.5,
                     "onset_lead_ms": -200.0})
        checks = {c["id"]: c for c in
                  sfx.evaluate(cues, -3.0, -28.0, _policy())}
        self.assertFalse(checks["sd_sub_gap"]["pass"])

    def test_jcut_gate_catches_a_late_riser(self):
        cues = _enrich(sfx.plan_cues(_cuts(), _words(), 10.0,
                                     "assets/music/x.wav", -28.0, _policy()))
        for cue in cues:
            if cue["layer"] == "anticipation":
                cue["onset_lead_ms"] = 0.0
                break
        checks = {c["id"]: c for c in
                  sfx.evaluate(cues, -3.0, -28.0, _policy())}
        self.assertFalse(checks["sd_jcut"]["pass"])

    def test_dry_run_ignores_unmeasured_audio_gates(self):
        # evaluate() itself is audio-aware; build() drops the two audio rows
        # when there is no waveform. This test locks the IDs that the dry-run
        # filter depends on.
        cues = _enrich(sfx.plan_cues(_cuts(), _words(), 10.0,
                                     "assets/music/x.wav", -28.0, _policy()))
        ids = {c["id"] for c in sfx.evaluate(cues, 0.0, 0.0, _policy())}
        self.assertIn("sd_peak", ids)
        self.assertIn("sd_bed", ids)


if __name__ == "__main__":
    unittest.main()
