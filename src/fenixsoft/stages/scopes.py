#!/usr/bin/env python3
"""scopes — histogram, vectorscope, waveform and signal stats.

Reads one frame (mid-clip or a requested time) and writes reviewable PNGs plus
the measured luma/chroma means. This is the QC companion to `fenix grade`.

Spec (`input/<project>/scopes.json`) or flags:

    {"input": "output/p/run/final.mp4", "time": null,
     "audio": true, "out": null}

Usage:
  fenix scopes <project> --input PATH [--time SEC] [--no-audio] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from ..context import OUTPUT_DIR, repo_path, resolve_sku_dir


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)], capture_output=True, text=True).stdout
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"duration": float((data.get("format") or {}).get("duration") or 0),
            "width": video.get("width"), "height": video.get("height"),
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


def parse_signalstats(text: str) -> dict:
    stats = {}
    for line in text.splitlines():
        m = re.search(r"lavfi\.signalstats\.(\w+)=([0-9.]+)", line)
        if m:
            stats[m.group(1)] = round(float(m.group(2)), 2)
    return stats


def run(src: Path, out: Path, t: float | None = None,
        with_audio: bool = True) -> dict:
    info = _probe(src)
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{t:.3f}" if t is not None else f"{info.get('duration', 0) / 2:.3f}"

    jobs = [
        ("histogram.png", ["-ss", stamp, "-i", str(src), "-frames:v", "1",
                           "-vf", "histogram=display_mode=stack:scale_height=20",
                           str(out / "histogram.png")]),
        ("vectorscope.png", ["-ss", stamp, "-i", str(src), "-frames:v", "1",
                             "-vf", "vectorscope=mode=color3:envelope=instant",
                             str(out / "vectorscope.png")]),
        ("waveform.png", ["-ss", stamp, "-i", str(src), "-frames:v", "1",
                          "-vf", "waveform=filter=lowpass:scale=0.5",
                          str(out / "waveform.png")]),
    ]
    produced = {}
    for name, cmd in jobs:
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", *cmd],
                              capture_output=True)
        produced[name] = proc.returncode == 0 and (out / name).exists()

    stats_proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", stamp, "-i", str(src),
         "-frames:v", "1", "-vf", "signalstats,metadata=print:file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    stats = parse_signalstats(stats_proc.stderr + stats_proc.stdout)

    if with_audio and info.get("audio"):
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-lavfi",
             "showspectrumpic=s=1280x720:legend=1", str(out / "spectrum.png")],
            capture_output=True)
        produced["spectrum.png"] = proc.returncode == 0 and \
            (out / "spectrum.png").exists()

    return {"input": str(src), "time": stamp, "info": info,
            "files": produced, "stats": stats}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Histogram, vectorscope, waveform")
    parser.add_argument("project")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--time", type=float, default=None)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    spec_path = args.spec or (project_dir / "scopes.json")
    spec = {}
    if spec_path.exists():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    target = args.input or (Path(spec["input"]) if spec.get("input") else None)
    if not target:
        sys.exit("[scopes] pass --input or put input in scopes.json")
    src = repo_path(str(target)) if not Path(target).is_absolute() else Path(target)
    if not src.exists():
        sys.exit(f"[scopes] input not found: {src}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = OUTPUT_DIR / project_dir.name / "scopes" / f"scopes_{stamp}"
    data = run(src, out, args.time, not args.no_audio)
    data["elapsed_sec"] = 0.0
    (out / "scopes.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[scopes] {out}")
    print(f"[scopes] {data['stats']}")


if __name__ == "__main__":
    main()
