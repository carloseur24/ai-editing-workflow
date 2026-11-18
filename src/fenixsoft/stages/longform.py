#!/usr/bin/env python3
"""longform — assemble a chaptered timeline into one master.

This is the assembly mode: ordered segments (existing MP4s or raw clips with
in/out), one canvas/fps, optional global audio with a J/L offset, `xfade`
transitions, multicam angle expansion and chapter markers. It is not a
creative re-edit; it is how short pieces and interviews become a long-form
master without an NLE.

Spec (`input/<project>/longform.json`):

    {
      "width": 1920, "height": 1080, "fps": 30,
      "chapters": [
        {"title": "Cold open", "source": "input/p/clips/a.mp4",
         "in": 0, "out": 12},
        {"title": "Chapter 1", "source": "input/p/clips/b.mp4",
         "in": 3, "out": 40}
      ],
      "audio": "input/p/voice.wav", "audio_offset": 0.0,
      "transition": "fade", "transition_sec": 0.4,
      "multicam": {"angles": ["a.mp4", "b.mp4"], "cuts": [2, 3, 2],
                    "audio": "dialogue.wav", "audio_offset": -0.2},
      "fade_in": 0.5, "fade_out": 0.8
    }

Usage:
  fenix longform <project> [--spec PATH] [--out MP4] [--deliver] [--json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..context import (DEFAULT_CONFIG, OUTPUT_DIR, load_config, repo_path,
                       resolve_sku_dir)

DEFAULTS = {"width": 1920, "height": 1080, "fps": 30, "chapters": [],
            "audio": None, "audio_offset": 0.0, "fade_in": 0.0,
            "fade_out": 0.0, "transition": "fade", "transition_sec": 0.0,
            "multicam": None}

TRANSITIONS = {"fade", "wipeleft", "wiperight", "wipeup", "wipedown",
               "slideleft", "slideright", "slideup", "slidedown",
               "circleopen", "circleclose", "dissolve", "pixelize"}


def _resolve(value: str | None) -> Path | None:
    return repo_path(value) if value else None


def _load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[longform] missing spec: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[longform] invalid {path}: {exc}")
    spec = dict(DEFAULTS)
    spec.update(data)
    return spec


def expand_multicam(spec: dict) -> dict:
    """Expand a synced multicam block into chapters (angle switching)."""
    mc = spec.get("multicam")
    if not mc:
        return spec
    angles = mc.get("angles") or []
    cuts = mc.get("cuts") or []
    if not angles or not cuts:
        return spec
    start = float(mc.get("start", 0.0))
    chapters, cursor = [], start
    for i, dur in enumerate(cuts):
        dur = float(dur)
        chapters.append({"title": f"Angle {i + 1}",
                         "source": angles[i % len(angles)],
                         "in": round(cursor, 3), "out": round(cursor + dur, 3)})
        cursor += dur
    spec["chapters"] = chapters
    if mc.get("audio"):
        spec["audio"] = mc["audio"]
    if mc.get("audio_offset") is not None:
        spec["audio_offset"] = mc["audio_offset"]
    return spec


def total_duration(spec: dict) -> float:
    chapters = spec.get("chapters") or []
    total = sum(max(0.0, float(ch.get("out", 0)) - float(ch.get("in", 0)))
                for ch in chapters)
    d = max(0.0, float(spec.get("transition_sec", 0.0)))
    if d and len(chapters) > 1:
        total -= d * (len(chapters) - 1)
    return max(0.0, total)


def validate(spec: dict) -> list[str]:
    errors = []
    chapters = spec.get("chapters") or []
    if not chapters:
        errors.append("no chapters")
    for i, ch in enumerate(chapters):
        src = _resolve(ch.get("source"))
        if not src or not src.exists():
            errors.append(f"chapter {i}: source not found: {ch.get('source')}")
        if float(ch.get("out", 0)) <= float(ch.get("in", 0)):
            errors.append(f"chapter {i}: out must be > in")
    audio = _resolve(spec.get("audio"))
    if spec.get("audio") and (not audio or not audio.exists()):
        errors.append(f"audio not found: {spec.get('audio')}")
    if float(spec.get("fps", 0)) <= 0:
        errors.append("fps must be > 0")
    transition = spec.get("transition") or "fade"
    if transition not in TRANSITIONS:
        errors.append(f"unknown transition: {transition}")
    d = float(spec.get("transition_sec", 0))
    if d < 0:
        errors.append("transition_sec must be >= 0")
    return errors


def chapters_markdown(spec: dict) -> str:
    lines = ["# Chapters", ""]
    cursor = 0.0
    for i, ch in enumerate(spec.get("chapters") or []):
        dur = float(ch.get("out", 0)) - float(ch.get("in", 0))
        lines.append(f"| {i + 1} | {ch.get('title') or f'Chapter {i + 1}'} | "
                     f"{cursor:.2f} | {cursor + dur:.2f} |")
        cursor += dur
    return "\n".join(["| # | Title | In | Out |", "|---|---|---|---|"] + lines[2:]) + "\n"


def render(spec: dict, out: Path) -> dict:
    errors = validate(spec)
    if errors:
        sys.exit("[longform] invalid spec: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = int(spec["width"]), int(spec["height"])
    fps = int(spec["fps"])
    chapters = spec["chapters"]
    audio = _resolve(spec.get("audio"))
    trans = float(spec.get("transition_sec", 0.0))

    inputs: list[str] = []
    for ch in chapters:
        inputs += ["-i", str(_resolve(ch["source"]))]
    audio_idx = None
    if audio:
        inputs += ["-i", str(audio)]
        audio_idx = len(chapters)

    filters = []
    for i, ch in enumerate(chapters):
        start, end = float(ch["in"]), float(ch["out"])
        filters.append(
            f"[{i}:v]trim={start}:{end},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H},setsar=1,fps={fps}[v{i}]")
    if trans > 0 and len(chapters) > 1:
        current = "v0"
        durations = [float(ch["out"]) - float(ch["in"]) for ch in chapters]
        for i in range(1, len(chapters)):
            offset = sum(durations[:i]) - i * trans
            filters.append(
                f"[{current}][v{i}]xfade=transition={spec.get('transition', 'fade')}:"
                f"duration={trans:.3f}:offset={max(0.0, offset):.3f}[x{i}]")
            current = f"x{i}"
        filters.append(f"[{current}]null[vcat]")
    else:
        labels = "".join(f"[v{i}]" for i in range(len(chapters)))
        filters.append(f"{labels}concat=n={len(chapters)}:v=1:a=0[vcat]")

    total = total_duration(spec)
    fi, fo = float(spec.get("fade_in", 0)), float(spec.get("fade_out", 0))
    tail = []
    if fi > 0:
        tail.append(f"fade=t=in:st=0:d={fi:.3f}")
    if fo > 0:
        tail.append(f"fade=t=out:st={max(0, total - fo):.3f}:d={fo:.3f}")
    tail.append("format=yuv420p")
    filters.append("[vcat]" + ",".join(tail) + "[vout]")

    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-filter_complex", ";".join(filters), "-map", "[vout]"]
    if audio_idx is not None:
        # J/L at the assembly level: negative = audio leads the picture,
        # positive = audio trails it. Pad so a short VO never truncates.
        offset = float(spec.get("audio_offset", 0.0))
        if offset < 0:
            af = f"atrim=start={-offset:.3f},asetpts=PTS-STARTPTS,apad"
        elif offset > 0:
            ms = int(offset * 1000)
            af = f"adelay={ms}|{ms},apad"
        else:
            af = "apad"
        cmd += ["-map", f"{audio_idx}:a", "-af", af,
                "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]

    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"[longform] ffmpeg failed:\n{proc.stderr[-1500:]}")
    return {"path": str(out), "chapters": len(chapters),
            "duration_sec": round(total, 2),
            "elapsed_sec": round(time.time() - started, 1)}


def deliver(run: Path, out: Path) -> None:
    cfg = load_config(DEFAULT_CONFIG)
    deliv = cfg.get("delivery") or {}
    shelf = Path(deliv.get("dir", "")).expanduser() / run.name
    shelf.mkdir(parents=True, exist_ok=True)
    for item in (out, run / "longform.json", run / "manifest.json",
                 run / "chapters.md"):
        if item.exists():
            shutil.copy2(item, shelf / item.name)
    downloads = deliv.get("downloads")
    if downloads:
        dl = Path(downloads).expanduser()
        dl.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dl / f"{run.name}.mp4")
        print(f"[longform] downloads: {dl / (run.name + '.mp4')}")
    print(f"[longform] shelf: {shelf}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Assemble a chaptered master")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    spec_path = args.spec or (project_dir / "longform.json")
    spec = _load(spec_path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = OUTPUT_DIR / project_dir.name / "longform" / f"{project_dir.name}_{stamp}"
    out = args.out or (run / "master.mp4")
    data = render(spec, out)
    run.mkdir(parents=True, exist_ok=True)
    (run / "longform.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (run / "chapters.md").write_text(chapters_markdown(spec), encoding="utf-8")
    if args.deliver:
        deliver(run, out)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[longform] {data['path']} ({data['chapters']} chapters, "
              f"{data['duration_sec']}s, {data['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
