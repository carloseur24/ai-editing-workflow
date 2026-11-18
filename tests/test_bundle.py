from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fenixsoft.ops import bundle, deliver

PROJECT = "p2_tmp_demo"
RUN = f"{PROJECT}_20260101-120000"


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class BundlePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.run_dir = root / "output" / PROJECT / RUN
        write(self.run_dir / f"{RUN}_final.mp4", "mp4")
        write(self.run_dir / "contact_sheet.png", "png")
        write(self.run_dir / "qa_report.json", '{"pass": true}')
        write(self.run_dir / "shot_order.json", '{"locked": true}')
        write(self.run_dir / "script_vo.md", "no voiceover")
        write(self.run_dir / "edit_plan.json",
              json.dumps({"duration_sec": 8.0, "cuts": []}))
        self.root = root
        self.cfg = {"delivery": {
            "dir": str(root / "shelf"),
            "downloads": str(root / "downloads"),
            "copy": ["*_final.mp4", "contact_sheet.png", "qa_report.json"],
        }}

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_carries_script_sheet_and_shot_order(self):
        plan = bundle.bundle_plan(PROJECT, self.run_dir, self.cfg)
        names = {Path(p["dest"]).name for p in plan if p["kind"] == "shelf"}
        self.assertIn("script_vo.md", names)
        self.assertIn("contact_sheet.png", names)
        self.assertIn("shot_order.json", names)
        self.assertIn(f"{RUN}_final.mp4", names)
        self.assertIn("qa_report.json", names)

    def test_every_shelf_dest_is_inside_the_run_folder(self):
        plan = bundle.bundle_plan(PROJECT, self.run_dir, self.cfg)
        shelf = self.root / "shelf" / RUN
        for item in plan:
            if item["kind"] == "shelf":
                self.assertEqual(Path(item["dest"]).parent, shelf)

    def test_downloads_are_flat_master_and_contact_sheet(self):
        plan = bundle.bundle_plan(PROJECT, self.run_dir, self.cfg)
        downloads = {Path(p["dest"]).name
                     for p in plan if p["kind"] == "downloads"}
        self.assertEqual(downloads, {
            f"{RUN}_final.mp4", f"{RUN}_contact_sheet.png"})

    def test_missing_master_plans_nothing(self):
        (self.run_dir / f"{RUN}_final.mp4").unlink()
        self.assertEqual(bundle.bundle_plan(PROJECT, self.run_dir, self.cfg), [])

    def test_comp_master_is_detected_without_a_final_mp4(self):
        (self.run_dir / f"{RUN}_final.mp4").unlink()
        (self.run_dir / "comp.mp4").write_text("comp", encoding="utf-8")
        plan = bundle.bundle_plan(PROJECT, self.run_dir, self.cfg)
        self.assertIn("comp.mp4", {Path(p["dest"]).name for p in plan})

    def test_explicit_master_can_live_outside_the_run(self):
        outside = write(self.root / "master" / "graded.mp4", "graded")
        (self.run_dir / f"{RUN}_final.mp4").unlink()
        plan = bundle.bundle_plan(PROJECT, self.run_dir, self.cfg,
                                  master=outside)
        self.assertIn("graded.mp4", {Path(p["dest"]).name for p in plan})

    def test_extra_artifact_is_added(self):
        note = write(self.root / "no_vo.md", "note")
        plan = bundle.bundle_plan(PROJECT, self.run_dir, self.cfg,
                                  extras=[note])
        self.assertIn("no_vo.md", {Path(p["dest"]).name for p in plan})


class BundleReadmeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = {"delivery": {"dir": str(self.root / "shelf"),
                                "downloads": str(self.root / "downloads")}}
        self.run_dir = self.root / RUN
        self.run_dir.mkdir()
        self.shots = [
            {"clip": "01_hook.mp4", "dur": 4.0, "t0": 0.0,
             "label": "hook", "spec": "GALAXY S25 ULTRA"},
            {"clip": "02_unbox.mp4", "dur": 4.0, "t0": 4.0,
             "label": "unboxing", "spec": "IN THE BOX"},
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def test_readme_has_the_shot_table_and_no_vo_note(self):
        plan = [{"src": self.run_dir / f"{RUN}_final.mp4",
                 "dest": self.root / "shelf" / RUN / f"{RUN}_final.mp4",
                 "kind": "shelf"}]
        text = bundle.write_bundle_readme(
            PROJECT, self.run_dir, plan, self.cfg,
            no_voice=True, shots=self.shots)
        self.assertIn(f"# {PROJECT}", text)
        self.assertIn("| 1 | hook | GALAXY S25 ULTRA | `01_hook.mp4` |", text)
        self.assertIn("| 2 | unboxing | IN THE BOX |", text)
        self.assertIn("no voiceover", text)
        self.assertIn("not a QA failure", text)
        self.assertIn(f"{RUN}_final.mp4", text)
        self.assertIn("Duration: 8.0 s", text)

    def test_readme_reads_the_run_plan_when_shots_are_omitted(self):
        (self.run_dir / "edit_plan.json").write_text(json.dumps({
            "duration_sec": 6.0,
            "cuts": [{"clip": "a.mp4", "dur": 6.0, "t0": 0.0,
                      "text": "opening"}],
        }), encoding="utf-8")
        text = bundle.write_bundle_readme(
            PROJECT, self.run_dir, [], self.cfg)
        self.assertIn("| 1 | opening |", text)
        self.assertIn("Duration: 6.0 s", text)
        self.assertIn("voiceover + ducked music bed", text)

    def test_readme_falls_back_to_a_stage_manifest_for_duration(self):
        (self.run_dir / "manifest.json").write_text(json.dumps({
            "duration_sec": 4.5, "path": "comp.mp4"}),
            encoding="utf-8")
        text = bundle.write_bundle_readme(
            PROJECT, self.run_dir, [], self.cfg)
        self.assertIn("Duration: 4.5 s", text)


class DeliverIntegrationTest(unittest.TestCase):
    def test_deliver_builds_the_bundle_and_readme(self):
        project = "p2_deliver_tmp"
        run_name = f"{project}_20260101-120000"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "output" / project / run_name
            write(run / f"{run_name}_final.mp4", "mp4")
            write(run / "contact_sheet.png", "png")
            write(run / "qa_report.json", '{"pass": true}')
            write(run / "shot_order.json", '{"locked": true}')
            write(run / "script_vo.md", "no voiceover")
            write(run / "edit_plan.json", json.dumps({
                "duration_sec": 6.0,
                "cuts": [{"clip": "a.mp4", "dur": 6.0, "t0": 0.0,
                          "text": "opening"}]}))
            cfg = {"delivery": {
                "dir": str(root / "shelf"),
                "downloads": str(root / "downloads"),
                "copy": ["*_final.mp4", "contact_sheet.png", "qa_report.json",
                         "edit_plan.json", "*.md"],
                "copy_to_downloads": True,
                "prune_local_media": False,
            }}
            cfg_path = write(root / "config.json", json.dumps(cfg))
            with mock.patch.object(deliver.runs, "register_run"):
                deliver.main([str(run), "--config", str(cfg_path), "--no-prune"])
                # Second delivery is a pure copy; nothing is pruned or lost.
                deliver.main([str(run), "--config", str(cfg_path), "--no-prune"])

            shelf = root / "shelf" / run_name
            names = {p.name for p in shelf.iterdir()}
            for expected in (f"{run_name}_final.mp4", "contact_sheet.png",
                             "shot_order.json", "edit_plan.json",
                             "script_vo.md", "qa_report.json",
                             "delivery_readme.md"):
                self.assertIn(expected, names)
            record = json.loads((run / "delivery.json").read_text(
                encoding="utf-8"))
            self.assertIn("delivery_readme.md", record["files"])
            self.assertEqual(record["skipped"], [])
            self.assertTrue(record["copied"])
            downloads = {p.name for p in (root / "downloads").iterdir()}
            self.assertEqual(downloads, {
                f"{run_name}_final.mp4", f"{run_name}_contact_sheet.png"})


class BundleApplyTest(unittest.TestCase):
    def test_apply_copies_and_skips_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = write(root / "run" / "note.md", "hello")
            missing = root / "run" / "optional.md"
            plan = [
                {"src": src, "dest": root / "new" / "deep" / "note.md",
                 "kind": "shelf"},
                {"src": missing, "dest": root / "new" / "optional.md",
                 "kind": "shelf"},
            ]
            result = bundle.apply_bundle(plan)
            copied = root / "new" / "deep" / "note.md"
            self.assertTrue(copied.exists())
            self.assertEqual(copied.read_text(encoding="utf-8"), "hello")
            self.assertEqual(result["copied"], [str(copied)])
            self.assertEqual(result["skipped"], [str(missing)])


if __name__ == "__main__":
    unittest.main()
