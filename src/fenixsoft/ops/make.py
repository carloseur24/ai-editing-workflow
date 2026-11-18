#!/usr/bin/env python3
"""make — one command from source material to a verified master.

The individual stages stay available, but a normal video job should not need
twenty hand-run commands. ``make`` walks the chain, skips every stage whose
inputs have not changed, rebuilds only the audio when the picture is current,
and stops at the first failure.

    fenix make <project>              # stale stages -> render/remix -> QA -> finish
    fenix make <project> --dry-run    # print the plan, run nothing
    fenix make <project> --force      # rerun every stage
    fenix make <project> --no-finish  # stop after QA, keep the local media
    fenix make <project> --remix      # force the audio-only path

Freshness is mtime based and intentionally simple: an artifact newer than every
input is trusted. ``--force`` is the escape hatch when a file was edited back in
time or copied from another machine.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from ..context import (DEFAULT_CONFIG, SFX_DIR, load_config, output_dir,
                       repo_path, resolve_sku_dir)
from ..domain import runs
from ..domain.config_slices import audio_changed, picture_changed

STAGES = {
    "storyboard": "fenixsoft.stages.storyboard",
    "beats": "fenixsoft.stages.beat_grid",
    "frames": "fenixsoft.stages.keyframes",
    "align": "fenixsoft.stages.align",
    "plan": "fenixsoft.stages.plan",
    "validate": "fenixsoft.stages.validate_plan",
    "sfx": "fenixsoft.stages.sfx",
    "render": "fenixsoft.render.render_video",
    "qa": "fenixsoft.gates.qa_check",
}

# A stage depends on the config sections it actually reads, not the whole file.
# A docs/delivery edit must not invalidate frames or align.
CONFIG_DEPS: dict[str, list[str]] = {
    "storyboard": ["canvas", "target_duration_sec", "bpm", "script",
                   "typography", "safe_zones"],
    "beats": ["music", "bpm"],
    "frames": ["min_cut_sec", "target_duration_sec", "cuts_per_video", "quality"],
    "align": ["alignment", "voiceover"],
    "plan": ["bpm", "cuts_per_video", "min_cut_sec", "cut_snap_to_beat",
             "target_duration_sec", "kinetic_captions"],
    "sfx": ["sfx", "audio", "music"],
}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _newest(paths: list[Path]) -> float:
    return max((_mtime(p) for p in paths), default=-1.0)


def is_fresh(output: Path, inputs: list[Path]) -> bool:
    """True when ``output`` exists and is at least as new as every input."""
    if not output.exists():
        return False
    return _mtime(output) >= _newest(inputs)


def config_digest(cfg: dict, stage: str) -> str | None:
    """Hash only the config sections a stage reads (``None`` = no deps)."""
    keys = CONFIG_DEPS.get(stage)
    if not keys:
        return None
    payload = {key: cfg.get(key) for key in keys}
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stamp_path(out: Path, stage: str) -> Path:
    return out / ".state" / f"{stage}.json"


def config_state(out: Path, cfg: dict, stage: str) -> bool | None:
    """Compare the stage's last-run config slice with the current one.

    ``None`` means no stamp exists yet (artifact predates this harness); the
    caller falls back to mtime instead of guessing.
    """
    digest = config_digest(cfg, stage)
    if digest is None:
        return None
    path = _stamp_path(out, stage)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("config_sha256") == digest


def write_config_stamp(out: Path, cfg: dict, stage: str) -> None:
    digest = config_digest(cfg, stage)
    if digest is None:
        return
    path = _stamp_path(out, stage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "stage": stage,
        "keys": CONFIG_DEPS[stage],
        "config_sha256": digest,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _voice(sku_dir: Path) -> Path:
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    return sku_dir / (sku.get("voiceover") or "voice.mp3")


def _align_current(sku_dir: Path) -> bool | None:
    """Compare word_timings against the voice + script it was made from.

    ``None`` means the artifact predates the fingerprint (fall back to mtime).
    A price/title edit must not trigger a 15 s realignment.
    """
    path = sku_dir / "word_timings.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    source = data.get("source")
    if not isinstance(source, dict):
        return None
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    voice = sku_dir / (sku.get("voiceover") or "voice.mp3")
    script = (sku.get("script_text") or "").encode("utf-8")
    return bool(
        source.get("voice_sha256")
        == hashlib.sha256(voice.read_bytes()).hexdigest()
        and source.get("script_sha256") == hashlib.sha256(script).hexdigest()
        and source.get("display_merge", {}) == sku.get("display_merge", {})
    )


def _step(name: str, argv: list[str]) -> bool:
    """Run a stage in-process so the whole chain costs one Python start."""
    started = time.monotonic()
    print(f"[make] run   {name} {' '.join(argv)}")
    module = importlib.import_module(STAGES[name])
    try:
        module.main(argv)
    except SystemExit as exc:                     # stages exit with a message
        if exc.code not in (0, None):
            print(f"[make] FAIL  {name} after {time.monotonic() - started:.0f}s")
            return False
    except Exception as exc:                      # keep the chain readable
        print(f"[make] FAIL  {name}: {type(exc).__name__}: {exc}")
        return False
    print(f"[make] ok    {name} ({time.monotonic() - started:.0f}s)")
    return True


def _should_run(name: str, sku_dir: Path, cfg: dict, force: bool,
                config_path: Path = DEFAULT_CONFIG) -> tuple[bool, str]:
    out = output_dir(sku_dir)
    clips = sorted((sku_dir / "clips").glob("*.mp4"))
    music = (cfg.get("music") or {}).get("file")
    music_path = [repo_path(music)] if music else []

    checks: dict[str, tuple[Path | None, list[Path]]] = {
        "storyboard": (out / "storyboard.json", [sku_dir / "brief.json"]),
        "beats": (out / "beat_grid.json", [*music_path]),
        "frames": (sku_dir / "candidates.json", [*clips]),
        "align": (sku_dir / "word_timings.json",
                  [_voice(sku_dir), sku_dir / "sku.json"]),
        "plan": (sku_dir / "edit_plan.json", [
            sku_dir / "candidates.json", sku_dir / "word_timings.json",
            out / "narration_lines.json", sku_dir / "picks.json"]),
        "validate": (None, []),                # cheap gate: always run
        "sfx": (out / "sfx_cue_sheet.json", [
            sku_dir / "edit_plan.json", sku_dir / "word_timings.json",
            SFX_DIR / "library.json", *music_path]),
    }
    if name == "storyboard" and not (sku_dir / "brief.json").exists():
        return False, "no brief.json (not an edit project)"
    if name == "align" and not force:
        current = _align_current(sku_dir)
        if current is True:
            return False, "word_timings.json matches voice + script"
        if current is False:
            return True, "voice or script changed"
    output, inputs = checks[name]
    if output is None:
        return True, "pre-render gate"
    if force:
        return True, "forced"
    if not output.exists():
        return True, f"{output.name} missing"
    if not is_fresh(output, inputs):
        newest = max(inputs, key=_mtime).name if inputs else "an input"
        return True, f"{output.name} is older than {newest}"
    state = config_state(out, cfg, name)
    if state is False:
        keys = ", ".join(CONFIG_DEPS.get(name, []))
        return True, f"config changed ({keys})"
    newest = max(inputs, key=_mtime).name if inputs else "inputs"
    return False, f"{output.name} is newer than {newest}"


def _render_freshness(sku_dir: Path, cfg: dict,
                      config_path: Path = DEFAULT_CONFIG) -> tuple[str, list[str]]:
    """Decide between ``skip``, ``remix`` and ``full`` for the picture stage.

    A render is stale when any picture input is newer than the run. If only the
    audio inputs moved, the fast path is ``--remix`` (video stream copied).
    """
    latest = runs.latest_run(sku_dir.name)
    if not latest:
        return "full", ["no previous run"]

    video = Path(latest.get("video") or "")
    run_dir = Path(latest.get("dir") or video.parent)
    plan_path = run_dir / "edit_plan.json"
    if not plan_path.exists():
        plan_path = video.parent / "edit_plan.json"
    run_ts = 0.0
    plan: dict = {}
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        stamps = [plan.get("remixed_at"), plan.get("generated_at")]
        run_ts = max(datetime.fromisoformat(s).timestamp()
                     for s in stamps if s)
    except (OSError, ValueError, json.JSONDecodeError):
        try:
            run_ts = datetime.fromisoformat(
                latest.get("created_at") or "").timestamp()
        except ValueError:
            run_ts = 0.0
    if not run_ts:
        return "full", ["run timestamp missing"]

    out = output_dir(sku_dir)
    clips = sorted((sku_dir / "clips").glob("*.mp4"))
    slices = plan.get("config_slices")
    picture_cfg = picture_changed(cfg, slices)
    audio_cfg = audio_changed(cfg, slices)

    picture = [sku_dir / "edit_plan.json", sku_dir / "sku.json", *clips]
    audio = [sku_dir / "word_timings.json", out / "sfx_cue_sheet.json",
             out / "sfx_bus.wav", out / "music_bed.wav",
             _voice(sku_dir)]
    music = (cfg.get("music") or {}).get("file")
    if music:
        audio.append(repo_path(music))
    # Legacy runs have no slice fingerprint: fall back to the config mtime.
    if picture_cfg is None:
        picture.append(config_path)
    if audio_cfg is None:
        audio.append(config_path)

    stale_picture = [p.name for p in picture if _mtime(p) > run_ts]
    stale_audio = [p.name for p in audio if _mtime(p) > run_ts]
    if picture_cfg:
        return "full", ["config.picture changed"]
    if stale_picture:
        return "full", stale_picture
    if audio_cfg:
        return "remix", ["config.audio changed"]
    if stale_audio:
        return "remix", stale_audio
    return "skip", ["picture and audio unchanged"]


STAGE_ORDER = ["storyboard", "beats", "frames", "align", "plan", "validate", "sfx"]


def stage_decision(name: str, sku_dir: Path, cfg: dict, force: bool = False,
                   config_path: Path = DEFAULT_CONFIG) -> tuple[bool, str]:
    """Public stage-state view for ``fenix job`` and status consumers."""
    return _should_run(name, sku_dir, cfg, force, config_path)


def render_decision(sku_dir: Path, cfg: dict,
                    config_path: Path = DEFAULT_CONFIG, *,
                    remix: bool = False, force: bool = False,
                    draft: bool = False) -> tuple[str, list[str]]:
    """Render mode with the CLI overrides applied (skip/remix/full)."""
    mode, why = _render_freshness(sku_dir, cfg, config_path)
    if remix:
        return "remix", ["forced --remix"]
    if force:
        return "full", ["forced"]
    if draft and mode == "skip":
        return "full", ["draft requested"]
    return mode, why


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the whole chain, skipping stages that are up to date")
    parser.add_argument("project", help="project/SKU name")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and exit")
    parser.add_argument("--force", action="store_true",
                        help="rerun every stage even when the artifacts are fresh")
    parser.add_argument("--remix", action="store_true",
                        help="force the audio-only render path")
    parser.add_argument("--no-finish", action="store_true",
                        help="stop after QA instead of delivering to Windows")
    parser.add_argument("--no-qa", action="store_true",
                        help="skip the QA gate (delivery will be refused)")
    parser.add_argument("--draft", action="store_true",
                        help="faster encode for review (not a delivery master)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    sku_dir = resolve_sku_dir(args.project)
    print(f"[make] {sku_dir.name}")

    todo: list[str] = []
    order = ["storyboard", "beats", "frames", "align", "plan", "validate", "sfx"]
    for name in order:
        run, why = _should_run(name, sku_dir, cfg, args.force, args.config)
        print(f"[make]       {name:10} {'RUN ' if run else 'SKIP'}  {why}")
        if run:
            todo.append(name)

    render_mode, render_why = _render_freshness(sku_dir, cfg, args.config)
    if args.remix:
        render_mode, render_why = "remix", ["forced --remix"]
    elif args.force:
        render_mode, render_why = "full", ["forced"]
    elif args.draft and render_mode == "skip":
        render_mode, render_why = "full", ["draft requested"]
    print(f"[make]       {'render':10} {render_mode.upper():5}  "
          f"{'; '.join(render_why[:3])}")

    if args.dry_run:
        return 0

    started = time.monotonic()
    for name in todo:
        if not _step(name, [str(sku_dir)]):
            return 1
        write_config_stamp(output_dir(sku_dir), cfg, name)

    # A stage that just ran may have changed the picture inputs (edit_plan,
    # captions, SFX). Re-decide after the loop so a plan change is a full
    # render, never a remix of the old picture.
    if todo and not args.remix and not args.force:
        render_mode, render_why = _render_freshness(sku_dir, cfg, args.config)
        if args.draft and render_mode == "skip":
            render_mode, render_why = "full", ["draft requested"]
        print(f"[make]       {'render':10} {render_mode.upper():5}  "
              f"{'; '.join(render_why[:3])}")

    render_ran = False
    if render_mode != "skip":
        render_argv = [str(sku_dir)]
        if render_mode == "remix":
            render_argv.append("--remix")
        if args.draft:
            render_argv.append("--draft")
        if not _step("render", render_argv):
            return 1
        render_ran = True

    if args.no_qa:
        print("[make] qa skipped; delivery is blocked until QA passes")
        return 0
    if args.draft:
        print("[make] draft rendered for review; QA and delivery skipped")
        return 0
    if not render_ran:
        latest = runs.latest_run(sku_dir.name) or {}
        from .finish import qa_is_current
        if qa_is_current(Path(latest.get("video") or "")):
            print("[make]       qa         SKIP  strict PASS current")
        elif not _step("qa", ["--latest", sku_dir.name, "--strict"]):
            return 1
    elif not _step("qa", ["--latest", sku_dir.name, "--strict"]):
        return 1

    if not args.no_finish and not args.draft:
        print(f"[make] run   finish {sku_dir.name}")
        try:
            from . import finish
            finish.main([sku_dir.name, "--config", str(args.config)])
        except SystemExit as exc:
            if exc.code not in (0, None):
                return 1

    print(f"[make] done  {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
