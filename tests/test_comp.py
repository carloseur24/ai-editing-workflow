from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fenixsoft.stages import comp

BASE = "tests/fixtures/placeholder.mp4"
LAYER = "assets/motion/brain.png"

CFG = {"encoding": {"codec": "libx264", "preset": "veryfast", "profile": "main",
                    "level": "4.2", "pix_fmt": "yuv420p", "crf": 18,
                    "maxrate": "3800k", "bufsize": "7600k",
                    "fallback_crf": 21, "fallback_maxrate": "2500k",
                    "audio_codec": "aac", "audio_bitrate": "128k",
                    "audio_sample_rate": 48000,
                    "max_whatsapp_bytes": 16252928, "faststart": True}}


def flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def spec(**over):
    data = {"base": BASE, "width": 1080, "height": 1920, "fps": 60,
            "duration": 2, "layers": [
                {"src": LAYER, "start": 0, "end": 2,
                 "x": 0.5, "y": 0.5,
                 "move": {"from": [0.5, 0.55], "to": [0.5, 0.45]}}]}
    data.update(over)
    return data


class CompTest(unittest.TestCase):
    def test_px_normalises_fractions(self):
        self.assertEqual(comp._px(0.5, 1080), 540)
        self.assertEqual(comp._px(200, 1080), 200)

    def test_valid_spec(self):
        self.assertEqual(comp.validate(spec()), [])

    def test_graph_has_overlay_and_move(self):
        inputs, graph = comp._graph(spec())
        self.assertIn("overlay=", graph)
        self.assertIn("trim=0.0:2.0", graph)
        self.assertIn("min(max((t-0.000)", graph)
        self.assertEqual(inputs.count("-i"), 2)

    def test_chroma_key_and_rect_mask(self):
        s = spec()
        s["layers"][0]["key"] = {"color": "#00ff00"}
        s["layers"][0]["mask"] = {"type": "rect", "x": 0.1, "y": 0.1,
                                  "w": 0.8, "h": 0.8}
        _, graph = comp._graph(s)
        self.assertIn("chromakey=color=0x00ff00", graph)
        self.assertIn("crop=864:1536", graph)

    def test_ellipse_mask_is_supported(self):
        s = spec()
        s["layers"][0]["mask"] = {"type": "ellipse", "x": 0.5, "y": 0.5,
                                  "w": 0.6, "h": 0.6}
        self.assertEqual(comp.validate(s), [])
        _, graph = comp._graph(s)
        self.assertIn("geq=", graph)
        self.assertIn("alpha(X,Y)", graph)

    def test_track_expression_is_piecewise(self):
        expr = comp._track_expr([{"t": 0, "nx": 0.2},
                                 {"t": 1, "nx": 0.8}], "nx", 100)
        self.assertIn("if(lt(t,1.000)", expr)
        self.assertIn("20.00", expr)
        self.assertIn("80.00", expr)

    def test_unknown_mask_is_rejected(self):
        s = spec()
        s["layers"][0]["mask"] = {"type": "lasso"}
        self.assertTrue(any("mask type" in e for e in comp.validate(s)))

    def test_missing_base_is_rejected(self):
        self.assertTrue(any("base not found" in e
                            for e in comp.validate(spec(base="nope.mp4"))))


class CompEncodingTest(unittest.TestCase):
    def test_command_uses_the_shared_encoding_block(self):
        cmd = comp.build_command(spec(), Path("/tmp/out.mp4"), CFG)
        self.assertEqual(flag(cmd, "-c:v"), "libx264")
        self.assertEqual(flag(cmd, "-preset"), "veryfast")
        self.assertEqual(flag(cmd, "-profile:v"), "main")
        self.assertEqual(flag(cmd, "-level"), "4.2")
        self.assertEqual(flag(cmd, "-crf"), "18")
        self.assertEqual(flag(cmd, "-maxrate"), "3800k")
        self.assertEqual(flag(cmd, "-bufsize"), "7600k")
        self.assertIn("+faststart", cmd)

    def test_audio_is_copied_when_no_filter_is_applied(self):
        cmd = comp.build_command(spec(), Path("/tmp/out.mp4"), CFG)
        self.assertEqual(flag(cmd, "-c:a"), "copy")

    def test_budget_decision_reads_the_configured_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "comp.mp4"
            out.write_bytes(b"x" * 4096)
            from fenixsoft.domain import encode
            self.assertTrue(encode.over_budget(
                out, {"encoding": {"max_whatsapp_bytes": 1024}}))
            self.assertFalse(encode.over_budget(
                out, {"encoding": {"max_whatsapp_bytes": 8192}}))


class CompStableRefTest(unittest.TestCase):
    def test_stable_ref_needs_a_project(self):
        self.assertIsNone(comp._resolve("@render/latest"))

    def test_stable_ref_without_a_match_keeps_a_named_path(self):
        resolved = comp._resolve("@render/latest", "sku_that_has_no_runs")
        self.assertEqual(str(resolved), "@render/latest")

    def test_explicit_paths_still_resolve_relative_to_the_repo(self):
        self.assertEqual(comp._resolve("assets/motion/brain.png"),
                         comp.repo_path("assets/motion/brain.png"))


class CompFingerprintTest(unittest.TestCase):
    def _fixture(self, tmp: Path) -> dict:
        base = tmp / "base.mp4"
        base.write_bytes(b"base")
        layer = tmp / "layer.png"
        layer.write_bytes(b"layer")
        return {"base": str(base), "width": 1080, "height": 1920, "fps": 60,
                "duration": 2, "layers": [{"src": str(layer), "start": 0,
                                           "end": 2}]}

    def test_fingerprint_is_stable_for_unchanged_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture(Path(tmp))
            self.assertEqual(comp.spec_fingerprint(dict(data)),
                             comp.spec_fingerprint(dict(data)))

    def test_a_changed_layer_invalidates_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture(Path(tmp))
            before = comp.spec_fingerprint(data)
            Path(data["layers"][0]["src"]).write_bytes(b"layeredited")
            self.assertNotEqual(before, comp.spec_fingerprint(data))

    def test_a_spec_edit_invalidates_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self._fixture(Path(tmp))
            before = comp.spec_fingerprint(data)
            data["layers"][0]["opacity"] = 0.5
            self.assertNotEqual(before, comp.spec_fingerprint(data))


class CompPreviewTest(unittest.TestCase):
    def test_position_interpolates_a_linear_move(self):
        layer = {"x": 0.5, "y": 0.5, "start": 0, "end": 4,
                 "move": {"from": [0.5, 0.55], "to": [0.5, 0.45]}}
        x, y = comp._position_at(layer, 2.0, 1000, 2000)
        self.assertAlmostEqual(x, 500.0, places=3)
        self.assertAlmostEqual(y, 1000.0, places=3)
        _, y0 = comp._position_at(layer, 0.0, 1000, 2000)
        _, y1 = comp._position_at(layer, 4.0, 1000, 2000)
        self.assertAlmostEqual(y0, 1100.0, places=3)
        self.assertAlmostEqual(y1, 900.0, places=3)

    def test_position_uses_track_points_when_present(self):
        points = [{"t": 0, "nx": 0.2, "ny": 0.4},
                  {"t": 2, "nx": 0.6, "ny": 0.8}]
        x, y = comp._position_at({"x": 0.5, "y": 0.5}, 1.0, 1000, 2000,
                                 points)
        self.assertAlmostEqual(x, 400.0, places=3)
        self.assertAlmostEqual(y, 1200.0, places=3)

    def test_still_command_seeks_and_composites_only_active_layers(self):
        s = spec()
        s["layers"].append({"src": LAYER, "start": 1.5, "end": 2.0,
                            "x": 0.25, "y": 0.25})
        cmd = comp.build_still_command(s, 1.6, Path("/tmp/still.png"))
        self.assertIn("-ss", cmd)
        self.assertIn("1.600", cmd)
        self.assertIn("-frames:v", cmd)
        joined = " ".join(cmd)
        self.assertIn("overlay=", joined)
        self.assertNotIn("fade=", joined)
        # base + both layers active at 1.6 (0-2 and 1.5-2.0)
        self.assertEqual(joined.count("-i "), 3)

    def test_base_cut_times_read_the_sibling_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "run" / "run_final.mp4"
            base.parent.mkdir(parents=True)
            base.write_bytes(b"x")
            (base.parent / "edit_plan.json").write_text(
                '{"cuts": [{"t0": 0, "dur": 2}, {"t0": 2, "dur": 4}]}',
                encoding="utf-8")
            rows = comp.base_cut_times({"base": str(base), "duration": 6})
        self.assertEqual([r["t"] for r in rows], [1.0, 4.0])
        self.assertEqual([r["kind"] for r in rows], ["cut", "cut"])

    def test_base_cut_times_fall_back_to_samples(self):
        rows = comp.base_cut_times({"base": None, "duration": 10}, limit=5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["kind"], "sample")
        self.assertAlmostEqual(rows[0]["t"], 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
