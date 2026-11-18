"""``fenix`` — the single command surface for the pipeline.

Each stage keeps its own argparse parser; this module only routes the first
argument to it. Run ``fenix`` with no arguments for the full map.
"""
from __future__ import annotations

import importlib
import sys

from . import __version__

COMMANDS: dict[str, tuple[str, str]] = {
    "storyboard": ("fenixsoft.stages.storyboard", "brief -> timed storyboard"),
    "doctor": ("fenixsoft.ops.doctor", "check environment, paths and inputs"),
    "job": ("fenixsoft.ops.job", "one card: mode, state, skills, next command"),
    "lint": ("fenixsoft.stages.script_lint", "check script_text before spending TTS"),
    "beats": ("fenixsoft.stages.beat_grid", "measure the music bed tempo/phase"),
    "frames": ("fenixsoft.stages.keyframes", "keyframe review + candidates.json"),
    "align": ("fenixsoft.stages.align", "trim voiceover, align words, narration lines"),
    "plan": ("fenixsoft.stages.plan", "narration lines x candidates -> edit_plan.json"),
    "validate": ("fenixsoft.stages.validate_plan", "pre-render gate for edit_plan.json"),
    "sfx": ("fenixsoft.stages.sfx", "cue sheet -> layered sound design bus"),
    "render": ("fenixsoft.render.render_video", "render a new timestamped run"),
    "motion": ("fenixsoft.stages.motion", "render a motion-graphics plate"),
    "track": ("fenixsoft.stages.track", "track a patch -> comp coordinates"),
    "comp": ("fenixsoft.stages.comp", "composite keyed/alpha layers over a base"),
    "fx": ("fenixsoft.stages.fx", "particles, glitch and shatter"),
    "grade": ("fenixsoft.stages.grade", "LUT, primaries and shot matching"),
    "longform": ("fenixsoft.stages.longform", "assemble a chaptered master"),
    "tts": ("fenixsoft.stages.tts", "generate a voiceover (ElevenLabs/OpenAI)"),
    "scopes": ("fenixsoft.stages.scopes", "histogram, vectorscope, waveform"),
    "qa": ("fenixsoft.gates.qa_check", "delivery gate for a render"),
    "eval": ("fenixsoft.gates.eval_run", "regression against eval/<sku>_baseline.json"),
    "deliver": ("fenixsoft.ops.deliver", "copy a finished run to the delivery shelf"),
    "finish": ("fenixsoft.ops.finish", "strict QA -> Windows master -> prune WSL"),
    "make": ("fenixsoft.ops.make", "whole chain, skipping fresh stages"),
    "batch": ("fenixsoft.ops.batch_run", "align -> render -> qa for several SKUs"),
    "runs": ("fenixsoft.domain.runs", "list the runs of a SKU"),
    "label": ("fenixsoft.ops.label", "scan a footage folder -> grids + safe rename"),
    "test": ("__tests__", "run the fast unit tests"),
}

ORDER = ["storyboard", "doctor", "job", "lint", "beats", "frames", "align",
         "plan", "validate", "sfx", "render", "motion", "track", "comp",
         "fx", "grade", "scopes", "longform", "tts", "qa", "eval", "deliver",
         "finish", "make", "batch", "runs", "label", "test"]


def usage() -> str:
    width = max(len(name) for name in ORDER)
    rows = "\n".join(f"  {name:<{width}}  {COMMANDS[name][1]}" for name in ORDER)
    return f"""fenix {__version__} — video editing & motion design pipeline

Usage:
  fenix <command> [args...]
  fenix <command> --help

Commands:
{rows}

Outputs:
  input/<sku>/                 source media + brief/specs (you edit these)
  output/<sku>/*.json          derived stage artifacts (tools write these)
  output/<sku>/<stamp>/        one immutable directory per edit render
  output/<sku>/{{motion,track,comp,fx,grade,scopes,longform}}/
                                post-production outputs + manifests

Run 'fenix doctor' to print the resolved paths and check the environment.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0
    if args[0] in {"-V", "--version"}:
        print(__version__)
        return 0

    command, rest = args[0], args[1:]
    if command not in COMMANDS:
        sys.stderr.write(f"fenix: unknown command '{command}'\n\n{usage()}")
        return 2
    if command == "test":
        return _run_tests()

    module_name = COMMANDS[command][0]
    module = importlib.import_module(module_name)
    module.main(rest)
    return 0


def _run_tests() -> int:
    import unittest

    from .context import REPO_ROOT

    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    suite = unittest.defaultTestLoader.discover(
        str(REPO_ROOT / "tests"), top_level_dir=root)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1
