"""Keep the agent-facing docs in sync with the code surface.

These are cheap string-containment checks, not prose quality checks. They catch
the common drift: a new command, config block or story framework lands in code
but never reaches the reference pages the agent reads.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from fenixsoft import cli
from fenixsoft.context import DEFAULT_CONFIG, REPO_ROOT
from fenixsoft.stages import storyboard

DOCS = REPO_ROOT / "docs"

STAGE_COMMANDS = {
    "storyboard": "storyboard",
    "script_lint": "lint",
    "beat_grid": "beats",
    "keyframes": "frames",
    "align": "align",
    "plan": "plan",
    "validate_plan": "validate",
    "sfx": "sfx",
    "motion": "motion",
    "track": "track",
    "comp": "comp",
    "fx": "fx",
    "grade": "grade",
    "scopes": "scopes",
    "longform": "longform",
    "tts": "tts",
}

OPS_COMMANDS = {
    "batch_run": "batch",
    "bundle": None,          # shared delivery planner, no fenix command
    "deliver": "deliver",
    "doctor": "doctor",
    "finish": "finish",
    "job": "job",
    "label": "label",
    "make": "make",
}

GATE_COMMANDS = {"qa_check": "qa", "eval_run": "eval"}


def _leaves(data: dict, prefix: str = ""):
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _leaves(value, full)
        else:
            yield full


class DocsCoverageTest(unittest.TestCase):
    def test_every_cli_command_is_documented(self):
        text = (DOCS / "reference/commands.md").read_text(encoding="utf-8")
        for command in cli.COMMANDS:
            with self.subTest(command=command):
                self.assertIn(f"fenix {command}", text)

    def test_every_config_leaf_is_documented(self):
        config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        text = (DOCS / "reference/config.md").read_text(encoding="utf-8")
        for full in _leaves(config):
            leaf = full.rsplit(".", 1)[-1]
            with self.subTest(key=full):
                self.assertTrue(full in text or leaf in text,
                                f"{full} is missing from config.md")

    def test_every_stage_is_reachable_or_documented(self):
        text = (DOCS / "reference/commands.md").read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "src/fenixsoft/stages").glob("*.py")):
            if path.stem == "__init__":
                continue
            with self.subTest(stage=path.stem):
                self.assertIn(path.stem, STAGE_COMMANDS,
                              f"new stage {path.stem}: add it to STAGE_COMMANDS "
                              f"and document its command")
            command = STAGE_COMMANDS[path.stem]
            self.assertIn(f"fenix {command}", text)

    def test_every_exposed_ops_and_gate_is_documented(self):
        text = (DOCS / "reference/commands.md").read_text(encoding="utf-8")
        modules = {
            "ops": (REPO_ROOT / "src/fenixsoft/ops", OPS_COMMANDS),
            "gates": (REPO_ROOT / "src/fenixsoft/gates", GATE_COMMANDS),
        }
        for label, (root, mapping) in modules.items():
            for path in sorted(root.glob("*.py")):
                if path.stem == "__init__":
                    continue
                with self.subTest(module=f"{label}/{path.stem}"):
                    self.assertIn(path.stem, mapping,
                                  f"new {label} module {path.stem}: add it to "
                                  f"the mapping and document its command")
                if mapping[path.stem] is None:
                    continue
                self.assertIn(f"fenix {mapping[path.stem]}", text)

    def test_every_story_framework_is_documented(self):
        text = (DOCS / "agent/storytelling.md").read_text(encoding="utf-8")
        for framework in storyboard.FRAMEWORKS:
            with self.subTest(framework=framework):
                self.assertIn(framework, text)

    def test_every_example_spec_is_documented(self):
        text = (DOCS / "reference/specs.md").read_text(encoding="utf-8")
        for path in sorted((DOCS / "examples").glob("*.json")):
            with self.subTest(spec=path.stem):
                self.assertIn(path.stem, text)


if __name__ == "__main__":
    unittest.main()
