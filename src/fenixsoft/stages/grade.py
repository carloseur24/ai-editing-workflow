#!/usr/bin/env python3
"""grade — LUT, primary correction, shot matching and keyed secondaries.

Spec (`input/<project>/grade.json`):

    {
      "input": "output/p/run/final.mp4",
      "lut": "assets/luts/editorial.cube",
      "eq": {"contrast": 1.05, "saturation": 1.08, "gamma": 1.0,
              "brightness": 0.0},
      "temperature": 0.0,
      "shot_match": {"reference": "assets/stills/hero.png",
                      "reference_frame": 0, "sample_frame": 5,
                      "strength": 0.6},
      "secondaries": [{"color": "#1E90FF", "similarity": 0.3,
                       "blend": 0.1, "hue": 140, "saturation": 1.2}],
      "fade_in": 0.0, "fade_out": 0.0
    }

Shot matching measures the mean RGB of the reference and of the input, then
applies a per-channel gain (blended by `strength`, clamped 0.7–1.4).
Secondaries hue/saturation-shift only the pixels near each key colour.

Usage:
  fenix grade <project> [--spec PATH] [--out MP4] [--deliver] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..context import (DEFAULT_CONFIG, load_config, repo_path,
                       resolve_sku_dir, stage_dir)
from ..domain import encode

DEFAULTS = {
    "lut": None,
    "eq": {"contrast": 1.0, "saturation": 1.0, "gamma": 1.0,
           "brightness": 0.0},
    "temperature": 0.0,
    "shot_match": None,
    "secondaries": [],
    "fade_in": 0.0,
    "fade_out": 0.0,
}


def _resolve(value: str | None) -> Path | None:
    return repo_path(value) if value else None


def _load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[grade] missing spec: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[grade] invalid {path}: {exc}")
    spec = dict(DEFAULTS)
    spec.update(data)
    spec["eq"] = {**DEFAULTS["eq"], **(data.get("eq") or {})}
    return spec


def mean_rgb(path: Path, t: float = 0.0) -> tuple[float, float, float] | None:
    proc = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    if proc.returncode != 0 or len(proc.stdout) < 3:
        return None
    return tuple(float(v) for v in proc.stdout[:3])


def gains(src: tuple[float, float, float],
          ref: tuple[float, float, float], strength: float) -> tuple[float, float, float]:
    out = []
    for a, b in zip(src, ref):
        a = max(1.0, a)
        g = (max(1.0, b) / a) ** max(0.0, min(1.0, strength))
        out.append(round(max(0.7, min(1.4, g)), 4))
    return tuple(out)  # type: ignore[return-value]


def validate(spec: dict) -> list[str]:
    errors = []
    src = _resolve(spec.get("input"))
    if not src or not src.exists():
        errors.append(f"input not found: {spec.get('input')}")
    lut = _resolve(spec.get("lut"))
    if spec.get("lut") and (not lut or not lut.exists()):
        errors.append(f"lut not found: {spec.get('lut')}")
    match = spec.get("shot_match")
    if match:
        ref = _resolve(match.get("reference"))
        if not ref or not ref.exists():
            errors.append(f"shot_match.reference not found: "
                          f"{match.get('reference')}")
    for i, sec in enumerate(spec.get("secondaries") or []):
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", str(sec.get("color", ""))):
            errors.append(f"secondary {i}: color must be #RRGGBB")
    return errors


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def filter_chain(spec: dict, duration: float | None = None) -> tuple[str, dict]:
    parts: list[str] = []
    notes: dict = {}
    lut = _resolve(spec.get("lut"))
    if lut and lut.exists():
        parts.append(f"lut3d=file='{lut.as_posix()}':interp=tetrahedral")
        notes["lut"] = lut.name
    eq = spec.get("eq") or {}
    parts.append(
        "eq=contrast={:.3f}:saturation={:.3f}:gamma={:.3f}:brightness={:.3f}"
        .format(float(eq.get("contrast", 1.0)), float(eq.get("saturation", 1.0)),
                float(eq.get("gamma", 1.0)), float(eq.get("brightness", 0.0))))
    temp = max(-1.0, min(1.0, float(spec.get("temperature", 0.0))))
    if temp:
        parts.append(f"colorbalance=rs={temp * 0.2:.3f}:bs={-temp * 0.2:.3f}")
        notes["temperature"] = temp

    match = spec.get("shot_match")
    if match:
        ref = _resolve(match.get("reference"))
        src = _resolve(spec.get("input"))
        if ref and src:
            ref_mean = mean_rgb(ref, float(match.get("reference_frame", 0)))
            src_mean = mean_rgb(src, float(match.get("sample_frame", 5)))
            if ref_mean and src_mean:
                g = gains(src_mean, ref_mean, float(match.get("strength", 0.6)))
                parts.append(
                    "colorchannelmixer=rr={:.4f}:gg={:.4f}:bb={:.4f}"
                    .format(*g))
                notes["shot_match"] = {"reference": ref.name,
                                       "source_mean": [round(v, 1) for v in src_mean],
                                       "reference_mean": [round(v, 1) for v in ref_mean],
                                       "gains": list(g)}
    fi = float(spec.get("fade_in", 0))
    fo = float(spec.get("fade_out", 0))
    if fi > 0:
        parts.append(f"fade=t=in:st=0:d={fi:.3f}")
    if fo > 0 and duration and duration > fo:
        parts.append(f"fade=t=out:st={duration - fo:.3f}:d={fo:.3f}")
    parts.append("format=yuv420p")
    return ",".join(parts), notes


def secondary_graph(spec: dict) -> tuple[str, int]:
    """Hue-shift only the pixels near each key colour, then chain."""
    seconds = spec.get("secondaries") or []
    if not seconds:
        return "", 0
    graph = ["[0:v]split=2[b0][s0]"]
    for i, sec in enumerate(seconds):
        color = str(sec.get("color", "#FF0000")).lstrip("#")
        sim = float(sec.get("similarity", 0.15))
        blend = float(sec.get("blend", 0.05))
        hue = float(sec.get("hue", 0.0))
        sat = float(sec.get("saturation", 1.0))
        graph.append(
            f"[s{i}]format=rgba,colorkey=0x{color}:{sim}:{blend},"
            f"hue=h={hue}:s={sat},format=rgba[sh{i}]")
        graph.append(f"[b{i}][sh{i}]overlay=format=auto[g{i}]")
        if i + 1 < len(seconds):
            graph.append(f"[g{i}]split=2[b{i + 1}][s{i + 1}]")
    return ";".join(graph), len(seconds)


def render(spec: dict, out: Path, cfg: dict | None = None) -> dict:
    errors = validate(spec)
    if errors:
        sys.exit("[grade] invalid spec: " + "; ".join(errors))
    cfg = cfg or load_config(DEFAULT_CONFIG)
    src = _resolve(spec["input"])
    out.parent.mkdir(parents=True, exist_ok=True)
    chain, notes = filter_chain(spec, _duration(src))
    pre, n_sec = secondary_graph(spec)
    if n_sec:
        graph = f"{pre};[g{n_sec - 1}]{chain}[vout]"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
               "-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
               *encode.video_args(cfg), *encode.audio_args(cfg),
               str(out)]
        notes["secondaries"] = n_sec
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
               "-vf", chain, *encode.video_args(cfg),
               *encode.audio_args(cfg), str(out)]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"[grade] ffmpeg failed:\n{proc.stderr[-1200:]}")

    # before/after stills at the match sample frame
    sample = float((spec.get("shot_match") or {}).get("sample_frame", 1.0))
    for label, source in (("before", src), ("after", out)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{sample:.3f}",
                        "-i", str(source), "-frames:v", "1",
                        str(out.parent / f"{label}.png")])
    return {"path": str(out), "elapsed_sec": round(time.time() - started, 1),
            "filters": notes}


def deliver(run: Path, out: Path) -> None:
    cfg = load_config(DEFAULT_CONFIG)
    deliv = cfg.get("delivery") or {}
    shelf = Path(deliv.get("dir", "")).expanduser() / run.name
    shelf.mkdir(parents=True, exist_ok=True)
    for item in (out, run / "grade.json", run / "manifest.json"):
        shutil.copy2(item, shelf / item.name)
    downloads = deliv.get("downloads")
    if downloads:
        dl = Path(downloads).expanduser()
        dl.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dl / f"{run.name}.mp4")
        print(f"[grade] downloads: {dl / (run.name + '.mp4')}")
    print(f"[grade] shelf: {shelf}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Grade a clip: LUT, primaries, match")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    cfg = load_config(DEFAULT_CONFIG, project=project_dir)
    spec_path = args.spec or (project_dir / "grade.json")
    spec = _load(spec_path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = stage_dir(project_dir, "grade") / f"{project_dir.name}_{stamp}"
    out = args.out or (run / "graded.mp4")
    data = render(spec, out, cfg)
    run.mkdir(parents=True, exist_ok=True)
    (run / "grade.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if args.deliver:
        deliver(run, out)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[grade] {data['path']} ({data['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
