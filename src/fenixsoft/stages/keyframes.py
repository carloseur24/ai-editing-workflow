#!/usr/bin/env python3
"""
keyframes — vision keyframes + the scored candidate manifest.

The harness asks the agent to inspect keyframes and reject blurry/dark takes,
but nothing produced those frames. This stage writes:

  input/<sku>/keyframes/<clip>_t012.00.jpg      (360 px contact frames)
  input/<sku>/keyframes/keyframe_report.json    (luma + Laplacian sharpness)
  input/<sku>/candidates.json                   (scored windows with IDs)

`candidates.json` is the guardrail that removes guessed timestamps from the
edit: the agent picks an ID (`{"clip": "...", "candidate": "01c-a"}`) and the
renderer/validator resolve the exact in-point from the measured window. An
explicit `in`/`dur` still works and is measured against the same table.

Window score = (sharpness / clip median) × exp(−motion / 25), so a sharp but
violently moving frame does not outrank a sharp, settled one. Dark samples
(mean luma < 35) are rejected outright.

Usage:
  fenix frames <sku> [--fps 1] [--width 360] [--candidates-step 0.25]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from ..context import DEFAULT_CONFIG, load_config, resolve_sku_dir
from ..domain.quality import cached_scan

LETTERS = "abcdefghij"


def _window_durations(cfg: dict) -> list[float]:
    min_cut = float(cfg.get("min_cut_sec", 1.2))
    target = float(cfg.get("target_duration_sec", 28.0))
    cuts = max(1, int(cfg.get("cuts_per_video", 16)))
    avg = target / cuts
    return sorted({round(min_cut, 2), round(avg, 2),
                   round(min(3.6, avg * 1.45), 2), round(min(4.2, avg * 2.0), 2)})


def build_candidates(clips: list[Path], cfg: dict, step: float = 0.25,
                     per_clip: int = 6) -> dict:
    durs = _window_durations(cfg)
    floor = float((cfg.get("quality") or {}).get("in_point_floor", 0.60))
    clips_out: dict[str, dict] = {}
    for clip in clips:
        prof = cached_scan(clip, step=step)
        if not prof["samples"] or not prof["duration_sec"]:
            continue
        median = prof["median"] or 1.0
        luma = dict(prof["luma"])
        motion = dict(prof["motion"])
        sample_by_t = dict(prof["samples"])
        end = prof["duration_sec"] - 0.02
        scored: list[dict] = []
        for t, sharp in prof["samples"]:
            ratio = sharp / median
            if luma.get(t, 0.0) < 35.0 or ratio < floor:
                continue
            fitting = [d for d in durs if t + d <= end]
            if not fitting:
                continue
            dur = max(fitting)          # the longest shot this in-point can host
            inside = [s for ts, s in sample_by_t.items() if t <= ts <= t + dur]
            window_ratio = (sum(inside) / len(inside) / median) if inside else ratio
            score = (min(ratio, window_ratio)
                     * math.exp(-motion.get(t, 0.0) / 25.0))
            scored.append({
                "t": round(t, 2), "dur": dur,
                "sharpness": round(sharp, 2),
                "ratio": round(ratio, 3),
                "window_ratio": round(window_ratio, 3),
                "motion": motion.get(t, 0.0),
                "luma": luma.get(t, 0.0),
                "score": round(score, 3),
            })
        scored.sort(key=lambda w: w["score"], reverse=True)
        kept: list[dict] = []
        for w in scored:
            if any(abs(w["t"] - k["t"]) < 0.6 for k in kept):
                continue
            kept.append(w)
            if len(kept) >= per_clip:
                break
        prefix = clip.stem.split("_")[0]
        windows = [{"id": f"{prefix}c-{LETTERS[i]}", **w}
                   for i, w in enumerate(kept)]
        clips_out[clip.name] = {
            "duration_sec": prof["duration_sec"],
            "median_sharpness": median,
            "windows": windows,
        }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step": step,
        "score_formula": "(sharpness / clip median) * exp(-motion / 25)",
        "window_durations": durs,
        "clips": clips_out,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Extract keyframes for vision QA")
    ap.add_argument("sku_dir", help="SKU name (or input/<sku> path)")
    ap.add_argument("--fps", type=float, default=1.0,
                    help="keyframe sample rate (default: 1 frame per second)")
    ap.add_argument("--width", type=int, default=360,
                    help="width of the exported JPEGs (default: 360)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output dir (default: <sku_dir>/keyframes)")
    ap.add_argument("--candidates-step", type=float, default=0.25,
                    help="scoring pass step in seconds (default 0.25 = 4 fps)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--no-candidates", action="store_true")
    args = ap.parse_args(argv)

    sku_dir = resolve_sku_dir(args.sku_dir)
    clips = sorted((sku_dir / "clips").glob("*.mp4"))
    if not clips:
        sys.exit(f"[ERROR] No .mp4 clips in {sku_dir / 'clips'}")
    out = args.out or (sku_dir / "keyframes")
    out.mkdir(parents=True, exist_ok=True)

    report: dict[str, dict] = {}
    for clip in clips:
        cap = cv2.VideoCapture(str(clip))
        if not cap.isOpened():
            print(f"[keyframes] WARNING: cannot open {clip.name}")
            continue
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / max(0.01, args.fps))))
        frames: list[dict] = []
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step == 0:
                t = idx / fps
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                h, w = frame.shape[:2]
                nh = max(1, int(args.width * h / w))
                small = cv2.resize(frame, (args.width, nh),
                                   interpolation=cv2.INTER_AREA)
                name = f"{clip.stem}_t{t:06.2f}.jpg"
                cv2.imwrite(str(out / name), small,
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
                frames.append({
                    "t": round(t, 2),
                    "file": name,
                    "luma": round(float(gray.mean()), 1),
                    "sharpness": round(float(cv2.Laplacian(
                        gray, cv2.CV_64F).var()), 1),
                })
            idx += 1
        cap.release()
        duration = idx / fps if fps else 0.0
        sharp = [f["sharpness"] for f in frames]
        report[clip.name] = {
            "duration_sec": round(duration, 2),
            "mean_sharpness": round(float(np.mean(sharp)), 1) if sharp else 0.0,
            "min_sharpness": round(float(np.min(sharp)), 1) if sharp else 0.0,
            "dark_frames": [f["file"] for f in frames if f["luma"] < 35.0],
            "frames": frames,
        }
        print(f"[keyframes] {clip.name}: {len(frames)} frames, "
              f"{duration:.2f}s, mean sharpness {report[clip.name]['mean_sharpness']}")

    report_path = out / "keyframe_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    total = sum(len(v["frames"]) for v in report.values())
    print(f"[keyframes] wrote {total} frames + {report_path}")

    # ---- scored candidate manifest (the IDs the agent is allowed to pick)
    if not args.no_candidates:
        cfg = load_config(args.config)
        cand = build_candidates(clips, cfg, step=args.candidates_step)
        cand_path = sku_dir / "candidates.json"
        cand_path.write_text(json.dumps(cand, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        n = sum(len(c["windows"]) for c in cand["clips"].values())
        print(f"[candidates] {n} windows across {len(cand['clips'])} clips "
              f"-> {cand_path}")
        for name, c in cand["clips"].items():
            top = c["windows"][0] if c["windows"] else None
            if top:
                print(f"[candidates]   {name:<26} best {top['id']} @ "
                      f"{top['t']:.2f}s score {top['score']:.2f} "
                      f"({top['ratio']:.2f}x median)")

    print("[keyframes] next: pick candidate IDs per narration line and run "
          f"'fenix plan {sku_dir.name}'")


if __name__ == "__main__":
    main()
