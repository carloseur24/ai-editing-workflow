from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fenixsoft.gates.qa_check import _pill_rgb

FFMPEG = shutil.which("ffmpeg")


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class PillSampleTest(unittest.TestCase):
    """Regression: an odd crop width used to return None on yuv420p video.

    FFmpeg rounds an odd crop to the chroma grid, so the byte count came back
    short and `_pill_rgb` reported "could not sample the price pill".
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.tmp.name) / "pill.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=0x059669:s=640x480:d=2:r=30",
             "-pix_fmt", "yuv420p", str(cls.video)],
            check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_odd_width_box_still_samples(self):
        # x1 - x0 - 24 = 21 -> odd; the crop must round to 20, not fail.
        rgb = _pill_rgb(self.video, [40, 40, 85, 80])
        self.assertIsNotNone(rgb)

    def test_median_is_emerald(self):
        r, g, b = _pill_rgb(self.video, [40, 40, 84, 80])
        self.assertGreater(g, 100, (r, g, b))
        self.assertLess(r, 60, (r, g, b))
        self.assertLess(b, 140, (r, g, b))


if __name__ == "__main__":
    unittest.main()
