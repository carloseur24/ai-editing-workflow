from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from fenixsoft.stages import track


def make_video(path: Path, frames: int = 20) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             30, (160, 120))
    for i in range(frames):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        x = 20 + i * 3
        cv2.rectangle(frame, (x, 50), (x + 20, 70), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


class TrackTest(unittest.TestCase):
    def test_tracks_a_moving_square(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "move.mp4"
            make_video(src)
            spec = {"input": str(src), "start": 0, "duration": 0.6,
                    "template": {"x": 20 / 160, "y": 50 / 120,
                                 "w": 20 / 160, "h": 20 / 120},
                    "smooth": 0.0, "threshold": 0.3}
            data = track.track(spec)
            self.assertGreater(len(data["points"]), 10)
            self.assertGreater(data["points"][-1]["x"],
                               data["points"][0]["x"] + 30)
            self.assertGreater(data["points"][-1]["score"], 0.5)

    def test_missing_input_is_rejected(self):
        self.assertTrue(any("input not found" in e
                            for e in track.validate({"input": "nope.mp4"})))

    def test_template_must_be_a_fraction(self):
        errors = track.validate({"input": "tests/fixtures/placeholder.mp4",
                                 "template": {"w": 2, "h": 0}})
        self.assertTrue(any("template.w/h" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
