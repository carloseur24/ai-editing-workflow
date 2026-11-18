#!/usr/bin/env python3
"""
quality.py — shared clip-quality measurements.

One measurement, one definition, used by the plan validator (pre-render), the
renderer (in-point floor), the keyframe scanner (candidates.json) and QA
(rendered-frame identity).

Sharpness is Laplacian variance on the greyscale frame; "ratio" is the frame's
score divided by the clip's median score, so a dark clip is not punished for
being dark — only for using a soft moment of it. Luma and motion are sampled in
the same pass so scoring a candidate never re-decodes the clip.

The demo measured on 2026-11-15: clip medians 41.4 / 44.4 / 26.7 / 15.5 / 11.5,
and three of the sixteen planned in-points sat below 0.60x of their own median
(worst 0.33x). That is the failure this module exists to catch.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from ..context import CACHE_DIR

CACHE = CACHE_DIR / "clip_quality"
SCHEMA = "v3"


def _gray(frame: np.ndarray, size: int | None = None) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if size:
        gray = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    return gray


def frame_sharpness(path: Path, t: float) -> float:
    cap = cv2.VideoCapture(str(path))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            return 0.0
        return float(cv2.Laplacian(_gray(frame), cv2.CV_64F).var())
    finally:
        cap.release()


def scan_clip(path: Path, step: float = 0.2) -> dict:
    """Sample the clip every `step` seconds: sharpness, luma and motion."""
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps if fps else 0.0
        values: list[tuple[float, float]] = []
        luma: list[tuple[float, float]] = []
        motion: list[tuple[float, float]] = []
        prev: np.ndarray | None = None
        t = 0.0
        while t <= duration + 1e-6:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                break
            gray = _gray(frame)
            small = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)
            stamp = round(t, 3)
            values.append((stamp, round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2)))
            luma.append((stamp, round(float(small.mean()), 1)))
            if prev is None:
                motion.append((stamp, 0.0))
            else:
                motion.append((stamp, round(float(np.abs(
                    small.astype(np.float32) - prev.astype(np.float32)).mean()), 2)))
            prev = small
            t += step
    finally:
        cap.release()
    if not values:
        return {"path": str(path), "duration_sec": 0.0, "median": 0.0,
                "max": 0.0, "samples": [], "luma": [], "motion": []}
    scores = [s for _, s in values]
    return {
        "path": str(path),
        "duration_sec": round(duration, 3),
        "median": round(float(np.median(scores)), 2),
        "max": round(float(max(scores)), 2),
        "samples": values,
        "luma": luma,
        "motion": motion,
    }


def cached_scan(path: Path, step: float = 0.2) -> dict:
    """`scan_clip`, memoised on path + mtime + size + schema."""
    st = path.stat()
    key = f"{path.name}-{int(st.st_mtime)}-{st.st_size}-{step}-{SCHEMA}"
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE / f"{key}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    data = scan_clip(path, step=step)
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def ratio(path: Path, t: float, median: float | None = None) -> dict:
    prof = cached_scan(path)
    med = median if median is not None else prof["median"]
    score = frame_sharpness(path, t)
    return {"t": round(t, 3), "sharpness": round(score, 2),
            "median": med, "ratio": round(score / med, 3) if med else 0.0}


def window_stats(prof: dict, t0: float, dur: float,
                 floor: float = 0.60) -> dict:
    """Sustained sharpness of a whole shot, not just its first frame.

    Scoring only the in-point rewarded cuts that are sharp for one frame and
    soft for the other 1.4 s. These are the profile of the entire window:
    `mean_ratio` is the sustained quality, `min_ratio` its worst sampled
    frame, `soft_frac` the share of the shot below `floor` and `mean_motion`
    how much the frame moves (a moving shot reads softer at the same ratio).
    """
    med = float(prof.get("median") or 0.0)
    stamp_ok = lambda t: t0 - 1e-6 <= t <= t0 + dur + 1e-6  # noqa: E731
    samples = [(t, s) for t, s in prof.get("samples") or [] if stamp_ok(t)]
    motion = [m for t, m in prof.get("motion") or [] if stamp_ok(t)]
    if not samples:  # shorter than the sampling step: use the nearest sample
        all_samples = prof.get("samples") or []
        if not all_samples or not med:
            return {"n": 0, "mean_ratio": 0.0, "min_ratio": 0.0,
                    "soft_frac": 0.0, "mean_motion": 0.0}
        mid = t0 + dur / 2.0
        samples = [min(all_samples, key=lambda item: abs(item[0] - mid))]
    ratios = [score / med for _, score in samples] if med else [0.0]
    return {
        "n": len(ratios),
        "mean_ratio": round(float(np.mean(ratios)), 3),
        "min_ratio": round(float(min(ratios)), 3),
        "soft_frac": round(sum(r < floor for r in ratios) / len(ratios), 3),
        "mean_motion": round(float(np.mean(motion)), 2) if motion else 0.0,
    }


# ------------------------------------------------------- clip identity / qa
def normalized_clip_path(src: Path, width: int, height: int, fps: int,
                         grade: dict | None = None) -> Path:
    """Where `render_video.normalize_clip` caches this clip (key must match)."""
    st = src.stat()
    gtag = (f"|g{grade.get('lightness_alpha')}_{grade.get('lightness_beta')}"
            if grade else "")
    key = hashlib.sha1(
        f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|"
        f"{width}x{height}@{fps}{gtag}".encode()
    ).hexdigest()[:16]
    return CACHE_DIR / "clips" / f"{src.stem}_{key}.mp4"


def ensure_normalized_clip(src: Path, width: int, height: int, fps: int,
                           grade: dict | None = None) -> Path:
    """Rebuild the deterministic normalised clip if the cache was pruned."""
    out = normalized_clip_path(src, width, height, fps, grade)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase:"
          f"flags=lanczos,crop={width}:{height},setsar=1,fps={fps}")
    if grade:
        alpha = float(grade.get("lightness_alpha", 1.06))
        beta = float(grade.get("lightness_beta", 3)) / 255.0
        vf += f",eq=contrast={alpha:.4f}:brightness={beta:.4f}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf,
                    "-an", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "16", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


def frame_crop_gray(path: Path, t: float, size: int = 96,
                    crop: tuple[float, float] = (0.22, 0.60),
                    x_crop: tuple[float, float] = (0.06, 0.94)) -> np.ndarray | None:
    """Grey crop of the footage band (skips the header and bottom text stack)."""
    cap = cv2.VideoCapture(str(path))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            return None
        h, w = frame.shape[:2]
        y0, y1 = int(h * crop[0]), int(h * crop[1])
        x0, x1 = int(w * x_crop[0]), int(w * x_crop[1])
        return cv2.resize(frame[y0:y1, x0:x1], (size, size),
                          interpolation=cv2.INTER_AREA).mean(axis=2)
    finally:
        cap.release()


def frame_similarity(a: np.ndarray, b: np.ndarray) -> dict:
    """MAD (0 = identical) and normalised correlation (1 = identical) on crops."""
    af = a.astype(np.float32)
    bf = b.astype(np.float32)
    mad = float(np.abs(af - bf).mean()) / 255.0
    if af.std() < 1e-6 or bf.std() < 1e-6:
        corr = 0.0
    else:
        corr = float(np.corrcoef(af.ravel(), bf.ravel())[0, 1])
    return {"mad": round(mad, 4), "corr": round(corr, 4)}
