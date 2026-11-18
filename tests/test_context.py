from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fenixsoft import context


class ResolveSkuTest(unittest.TestCase):
    def test_accepts_sku_name(self):
        path = context.resolve_sku_dir("demo_iphone14_blue")
        self.assertEqual(path.name, "demo_iphone14_blue")
        self.assertTrue((path / "sku.json").exists())

    def test_accepts_relative_path(self):
        path = context.resolve_sku_dir("input/demo_iphone14_blue")
        self.assertEqual(path.name, "demo_iphone14_blue")

    def test_rejects_unknown_with_actionable_message(self):
        with self.assertRaises(SystemExit) as ctx:
            context.resolve_sku_dir("does_not_exist")
        self.assertIn("known SKUs", str(ctx.exception))

    def test_temp_input_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mysku").mkdir()
            with mock.patch.object(context, "INPUT_DIR", root):
                self.assertEqual(context.resolve_sku_dir("mysku").name, "mysku")
                self.assertEqual(context.known_skus(), [])


class OutputPathTest(unittest.TestCase):
    def test_output_dir_uses_sku_name(self):
        self.assertEqual(context.output_dir("/x/input/iphone14_blue").name,
                         "iphone14_blue")
        self.assertEqual(context.artifact("/x/input/iphone14_blue",
                                          "beat_grid.json").name,
                         "beat_grid.json")


class ConfigTest(unittest.TestCase):
    def test_default_config_loads(self):
        cfg = context.load_config()
        self.assertEqual(cfg["canvas"]["width"], 1080)
        self.assertEqual(cfg["canvas"]["fps"], 60)

    def test_invalid_json_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                context.load_config(path)
            self.assertIn("invalid JSON", str(ctx.exception))

    def test_repo_path_resolves_assets(self):
        self.assertEqual(context.repo_path("assets/music/x.wav"),
                         context.REPO_ROOT / "assets" / "music" / "x.wav")
        absolute = Path("/tmp/x.wav")
        self.assertEqual(context.repo_path(absolute), absolute)


class LocalConfigOverlayTest(unittest.TestCase):
    def _paths(self, tmp: str) -> tuple[Path, Path]:
        root = Path(tmp)
        base = root / "pipeline.json"
        local = root / "pipeline.local.json"
        base.write_text(json.dumps({
            "canvas": {"width": 1080, "fps": 60},
            "delivery": {"dir": "output/delivery", "prune_local_media": True},
        }), encoding="utf-8")
        local.write_text(json.dumps({
            "delivery": {"dir": "/mnt/c/shelf"},
            "music": {"file": "assets/music/bed.wav"},
        }), encoding="utf-8")
        return base, local

    def test_local_overlay_is_deep_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, local = self._paths(tmp)
            with mock.patch.object(context, "DEFAULT_CONFIG", base), \
                    mock.patch.object(context, "LOCAL_CONFIG", local):
                cfg = context.load_config()
        self.assertEqual(cfg["delivery"]["dir"], "/mnt/c/shelf")
        self.assertTrue(cfg["delivery"]["prune_local_media"])
        self.assertEqual(cfg["music"]["file"], "assets/music/bed.wav")
        self.assertEqual(cfg["canvas"]["width"], 1080)

    def test_explicit_config_is_loaded_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, local = self._paths(tmp)
            other = base.with_name("other.json")
            other.write_text(json.dumps({
                "delivery": {"dir": "elsewhere"},
            }), encoding="utf-8")
            with mock.patch.object(context, "DEFAULT_CONFIG", base), \
                    mock.patch.object(context, "LOCAL_CONFIG", local):
                cfg = context.load_config(other)
        self.assertEqual(cfg["delivery"]["dir"], "elsewhere")
        self.assertNotIn("music", cfg)

    def test_missing_local_overlay_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, _ = self._paths(tmp)
            with mock.patch.object(context, "DEFAULT_CONFIG", base), \
                    mock.patch.object(context, "LOCAL_CONFIG",
                                      Path(tmp) / "absent.json"):
                cfg = context.load_config()
        self.assertEqual(cfg["canvas"]["fps"], 60)


class RelativeAssetPathTest(unittest.TestCase):
    def test_delivery_dir_resolves_against_repo(self):
        from fenixsoft.ops import deliver
        self.assertEqual(deliver.delivery_dir(
            {"delivery": {"dir": "output/delivery"}}, None),
            context.REPO_ROOT / "output" / "delivery")

    def test_sfx_library_resolves_against_repo(self):
        from fenixsoft.stages import sfx
        _, library = sfx.load_manifest({"sfx": {"library_dir": "assets/sfx"}})
        self.assertEqual(library, context.REPO_ROOT / "assets" / "sfx")


if __name__ == "__main__":
    unittest.main()
