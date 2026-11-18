from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fenixsoft.domain import encode

CFG = {
    "encoding": {
        "codec": "libx264", "preset": "medium", "profile": "main",
        "level": "4.2", "pix_fmt": "yuv420p", "crf": 18,
        "maxrate": "3800k", "bufsize": "7600k", "fallback_crf": 21,
        "fallback_maxrate": "2500k", "audio_codec": "aac",
        "audio_bitrate": "128k", "audio_sample_rate": 48000,
        "max_whatsapp_bytes": 1024, "faststart": True,
    }
}


def flags(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None


class SharedEncodingTest(unittest.TestCase):
    def test_defaults_fill_a_partial_block(self):
        enc = encode.settings({"encoding": {"crf": 20}})
        self.assertEqual(enc["crf"], 20)
        self.assertEqual(enc["profile"], "main")
        self.assertEqual(enc["maxrate"], "3800k")

    def test_video_args_come_from_the_config_block(self):
        args = encode.video_args(CFG)
        self.assertEqual(flags(args, "-c:v"), "libx264")
        self.assertEqual(flags(args, "-preset"), "medium")
        self.assertEqual(flags(args, "-profile:v"), "main")
        self.assertEqual(flags(args, "-level"), "4.2")
        self.assertEqual(flags(args, "-crf"), "18")
        self.assertEqual(flags(args, "-maxrate"), "3800k")
        self.assertEqual(flags(args, "-bufsize"), "7600k")
        self.assertIn("+faststart", args)

    def test_draft_flips_preset_and_crf(self):
        args = encode.video_args(CFG, draft=True)
        self.assertEqual(flags(args, "-preset"), "veryfast")
        self.assertEqual(flags(args, "-crf"), "23")

    def test_fallback_args_use_the_budget_values_and_copy_audio(self):
        args = encode.fallback_args(CFG)
        self.assertEqual(flags(args, "-crf"), "21")
        self.assertEqual(flags(args, "-maxrate"), "2500k")
        self.assertEqual(flags(args, "-c:a"), "copy")

    def test_audio_is_copied_unless_a_filter_ran(self):
        self.assertEqual(encode.audio_args(CFG), ["-c:a", "copy"])
        re_encoded = encode.audio_args(CFG, copy_audio=False)
        self.assertEqual(flags(re_encoded, "-c:a"), "aac")
        self.assertEqual(flags(re_encoded, "-b:a"), "128k")

    def test_over_budget_compares_against_the_configured_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.mp4"
            path.write_bytes(b"x" * 2048)
            self.assertTrue(encode.over_budget(path, CFG))
            bigger = {"encoding": {"max_whatsapp_bytes": 4096}}
            self.assertFalse(encode.over_budget(path, bigger))


if __name__ == "__main__":
    unittest.main()
