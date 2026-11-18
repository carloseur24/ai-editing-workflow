from __future__ import annotations

import unittest

from fenixsoft.stages import scopes


class ScopesTest(unittest.TestCase):
    def test_parses_signalstats_metadata(self):
        text = """
[Parsed_metadata_1 @ 0x1] frame:0 pts:0 pts_time:0
[Parsed_metadata_1 @ 0x1] lavfi.signalstats.YMIN=16
[Parsed_metadata_1 @ 0x1] lavfi.signalstats.YAVG=118.45
[Parsed_metadata_1 @ 0x1] lavfi.signalstats.SATAVG=42.10
"""
        stats = scopes.parse_signalstats(text)
        self.assertEqual(stats["YMIN"], 16.0)
        self.assertEqual(stats["YAVG"], 118.45)
        self.assertEqual(stats["SATAVG"], 42.1)

    def test_ignores_unrelated_lines(self):
        self.assertEqual(scopes.parse_signalstats("hello world"), {})


if __name__ == "__main__":
    unittest.main()
