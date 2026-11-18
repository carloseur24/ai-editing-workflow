#!/usr/bin/env python3
"""fx — particles, glitch and shatter over a clip.

Frame-accurate, deterministic effects (seeded), streamed to FFmpeg. This is the
place for texture and impact effects that do not belong in the cut or the grade.

Spec (`input/<project>/fx.json`):

    {
      "input": "input/p/clips/a.mp4",
      "width": 1080, "height": 1920, "fps": 60,
      "start": 0.0, "duration": 3.0,
      "effects": [
        {"type": "glitch", "start": 0.4, "end": 1.0, "intensity": 0.5},
        {"type": "particles", "start": 0, "end": 3, "count": 60,
         "color": "#10B981", "size": 4, "speed": 120, "gravity": 200,
         "seed": 7},
        {"type": "shatter", "start": 1.8, "end": 2.4, "tiles": 8,
         "intensity": 0.7}
      ]
    }

Usage:
  fenix fx <project> [--spec PATH] [--out MP4] [--deliver] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from ..context import (DEFAULT_CONFIG, OUTPUT_DIR, load_config, repo_path,
                       resolve_sku_dir)

DEFAULTS = {"width": 1080, "height": 1920, "fps": 60, "start": 0.0,
            "duration": 3.0, "effects": []}
TYPES = {"glitch", "particles", "shatter"}


def _resolve(value: str | None) -> Path | None:
    return repo_path(value) if value else None


def _load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[fx] missing spec: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[fx] invalid {path}: {exc}")
    spec = dict(DEFAULTS)
    spec.update(data)
    return spec


def _hex(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def validate(spec: dict) -> list[str]:
    errors = []
    src = _resolve(spec.get("input"))
    if not src or not src.exists():
        errors.append(f"input not found: {spec.get('input')}")
    if float(spec.get("duration", 0)) <= 0:
        errors.append("duration must be > 0")
    for i, eff in enumerate(spec.get("effects") or []):
        if eff.get("type") not in TYPES:
            errors.append(f"effect {i}: unknown type {eff.get('type')}")
        if float(eff.get("end", 0)) <= float(eff.get("start", 0)):
            errors.append(f"effect {i}: end must be > start")
    return errors


def _active(eff: dict, t: float) -> bool:
    return float(eff.get("start", 0)) <= t <= float(eff.get("end", 1e9))


def _make_particles(eff: dict, W: int, H: int) -> list[dict]:
    rng = np.random.default_rng(int(eff.get("seed", 7)))
    count = int(eff.get("count", 50))
    speed = float(eff.get("speed", 100))
    life = max(0.2, float(eff.get("life", 2.0)))
    return [{"x": rng.uniform(0, W), "y": rng.uniform(0, H),
             "vx": rng.uniform(-speed, speed),
             "vy": rng.uniform(-speed, speed * 0.2),
             "phase": rng.uniform(0, life), "life": life,
             "size": max(1, int(eff.get("size", 3) * rng.uniform(0.6, 1.4)))}
            for _ in range(count)]


def particle_positions(particles: list[dict], eff: dict, t: float,
                       W: int, H: int) -> list[tuple[int, int, int]]:
    g = float(eff.get("gravity", 0))
    out = []
    for p in particles:
        te = (t + p["phase"]) % p["life"]
        x = (p["x"] + p["vx"] * te) % (W + 40) - 20
        y = (p["y"] + p["vy"] * te + 0.5 * g * te * te) % (H + 40) - 20
        alpha = int(255 * max(0.0, 1.0 - te / p["life"]))
        out.append((int(x), int(y), alpha, p["size"]))
    return out


def _glitch(frame: np.ndarray, eff: dict, idx: int) -> np.ndarray:
    H, W = frame.shape[:2]
    rng = np.random.default_rng(int(eff.get("seed", 3)) + idx)
    k = max(1, int(float(eff.get("intensity", 0.4)) * 12))
    out = frame.copy()
    for _ in range(k):
        y0 = int(rng.integers(0, max(1, H - 8)))
        h = int(rng.integers(4, max(6, H // 8)))
        shift = int(rng.integers(-1, 2) * float(eff.get("intensity", 0.4)) * W * 0.08)
        out[y0:y0 + h] = np.roll(out[y0:y0 + h], shift, axis=1)
    if eff.get("channels", True):
        b, g, r = cv2.split(out)
        s = int(float(eff.get("intensity", 0.4)) * 12)
        out = cv2.merge([np.roll(b, s, axis=1), g, np.roll(r, -s, axis=1)])
    return out


def _shatter(frame: np.ndarray, eff: dict, t: float) -> np.ndarray:
    H, W = frame.shape[:2]
    tiles = max(2, int(eff.get("tiles", 8)))
    start, end = float(eff.get("start", 0)), float(eff.get("end", 1))
    p = max(0.0, min(1.0, (t - start) / max(1e-6, end - start)))
    ease = 1 - (1 - p) ** 3
    power = float(eff.get("intensity", 0.6)) * W * 0.12 * ease
    out = np.zeros_like(frame)
    tw, th = max(1, W // tiles), max(1, H // tiles)
    for iy in range(tiles):
        for ix in range(tiles):
            y0, y1 = iy * th, min(H, (iy + 1) * th)
            x0, x1 = ix * tw, min(W, (ix + 1) * tw)
            dx = (ix - tiles / 2 + 0.5) / (tiles / 2) * power
            dy = (iy - tiles / 2 + 0.5) / (tiles / 2) * power
            sx, sy = int(x0 + dx), int(y0 + dy)
            dx0, dy0 = max(0, sx), max(0, sy)
            dx1, dy1 = min(W, sx + (x1 - x0)), min(H, sy + (y1 - y0))
            if dx1 <= dx0 or dy1 <= dy0:
                continue
            out[dy0:dy1, dx0:dx1] = frame[y0:y0 + (dy1 - dy0),
                                          x0:x0 + (dx1 - dx0)]
    return out


def render(spec: dict, out: Path) -> dict:
    errors = validate(spec)
    if errors:
        sys.exit("[fx] invalid spec: " + "; ".join(errors))
    src = _resolve(spec["input"])
    W, H, fps = int(spec["width"]), int(spec["height"]), int(spec["fps"])
    start, duration = float(spec["start"]), float(spec["duration"])
    frames = int(round(duration * fps))
    out.parent.mkdir(parents=True, exist_ok=True)

    particles = {}
    for i, eff in enumerate(spec.get("effects") or []):
        if eff["type"] == "particles":
            particles[i] = _make_particles(eff, W, H)

    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-",
           "-map", "1:v", "-map", "0:a?", "-c:v", "libx264",
           "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    cap = cv2.VideoCapture(str(src))
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    started = time.time()
    try:
        for i in range(frames):
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
            t = i / fps
            for j, eff in enumerate(spec.get("effects") or []):
                if not _active(eff, t):
                    continue
                if eff["type"] == "glitch":
                    frame = _glitch(frame, eff, i)
                elif eff["type"] == "shatter":
                    frame = _shatter(frame, eff, t)
                elif eff["type"] == "particles":
                    overlay = frame.copy()
                    color = _hex(eff.get("color", "#FFFFFF"))[::-1]
                    for x, y, a, size in particle_positions(
                            particles[j], eff, t, W, H):
                        cv2.circle(overlay, (x, y), size, color, -1)
                        cv2.circle(overlay, (x, y), max(1, size // 2),
                                   tuple(min(255, c + 40) for c in color), -1)
                    frame = cv2.addWeighted(overlay, 0.55, frame, 0.45, 0)
            proc.stdin.write(frame.tobytes())
    finally:
        cap.release()
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"[fx] ffmpeg failed:\n{err[-1200:]}")
    return {"path": str(out), "frames": frames, "duration_sec": duration,
            "effects": [e["type"] for e in spec.get("effects") or []],
            "elapsed_sec": round(time.time() - started, 1)}


def deliver(run: Path, out: Path) -> None:
    cfg = load_config(DEFAULT_CONFIG)
    deliv = cfg.get("delivery") or {}
    shelf = Path(deliv.get("dir", "")).expanduser() / run.name
    shelf.mkdir(parents=True, exist_ok=True)
    for item in (out, run / "fx.json", run / "manifest.json"):
        shutil.copy2(item, shelf / item.name)
    downloads = deliv.get("downloads")
    if downloads:
        dl = Path(downloads).expanduser()
        dl.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, dl / f"{run.name}.mp4")
        print(f"[fx] downloads: {dl / (run.name + '.mp4')}")
    print(f"[fx] shelf: {shelf}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Particles, glitch and shatter")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    spec = _load(args.spec or (project_dir / "fx.json"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = OUTPUT_DIR / project_dir.name / "fx" / f"{project_dir.name}_{stamp}"
    out = args.out or (run / "fx.mp4")
    data = render(spec, out)
    run.mkdir(parents=True, exist_ok=True)
    (run / "fx.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if args.deliver:
        deliver(run, out)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[fx] {data['path']} ({data['frames']} frames, "
              f"{data['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
