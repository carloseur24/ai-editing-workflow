#!/usr/bin/env python3
"""track — track a patch through a clip and emit normalized coordinates.

`fenix comp` reads the result to drive an overlay's position (screen replacement,
label tracking, tracked text). Template matching is the v1; it is deterministic,
CPU-only and good enough for a locked-off phone screen or a moving product.

Spec (`input/<project>/track.json`):

    {
      "input": "input/p/clips/a.mp4",
      "start": 0.0,
      "duration": 5.0,
      "template": {"x": 0.40, "y": 0.35, "w": 0.20, "h": 0.30},
      "search": 0.18,
      "smooth": 0.6,
      "threshold": 0.45
    }

Output `output/<project>/track/track.json`:
`{fps, width, height, start, points:[{t,x,y,nx,ny,score}]}`.

Usage:
  fenix track <project> [--spec PATH] [--out JSON] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from ..context import DEFAULT_CONFIG, OUTPUT_DIR, load_config, repo_path, \
    resolve_sku_dir

DEFAULTS = {
    "start": 0.0,
    "duration": 5.0,
    "template": {"x": 0.40, "y": 0.35, "w": 0.20, "h": 0.30},
    "search": 0.18,
    "smooth": 0.6,
    "threshold": 0.45,
}


def _resolve(value: str | None) -> Path | None:
    return repo_path(value) if value else None


def _load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[track] missing spec: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[track] invalid {path}: {exc}")
    spec = dict(DEFAULTS)
    spec.update(data)
    spec["template"] = {**DEFAULTS["template"], **(data.get("template") or {})}
    return spec


def validate(spec: dict) -> list[str]:
    errors = []
    src = _resolve(spec.get("input"))
    if not src or not src.exists():
        errors.append(f"input not found: {spec.get('input')}")
    if float(spec.get("duration", 0)) <= 0:
        errors.append("duration must be > 0")
    t = spec.get("template") or {}
    if not (0 < float(t.get("w", 0)) <= 1 and 0 < float(t.get("h", 0)) <= 1):
        errors.append("template.w/h must be between 0 and 1")
    return errors


def track(spec: dict) -> dict:
    errors = validate(spec)
    if errors:
        sys.exit("[track] invalid spec: " + "; ".join(errors))
    src = _resolve(spec["input"])
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        sys.exit(f"[track] cannot open {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    start = float(spec.get("start", 0))
    duration = float(spec.get("duration", 5))
    tpl = spec["template"]
    x = int(float(tpl["x"]) * W)
    y = int(float(tpl["y"]) * H)
    tw = max(8, int(float(tpl["w"]) * W))
    th = max(8, int(float(tpl["h"]) * H))
    search = max(0.05, min(0.5, float(spec.get("search", 0.18))))
    smooth = max(0.0, min(0.95, float(spec.get("smooth", 0.6))))
    threshold = float(spec.get("threshold", 0.45))

    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    ok, first = cap.read()
    if not ok:
        sys.exit("[track] could not read the first frame")
    first_gray = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    template = first_gray[y:y + th, x:x + tw].copy()
    cx, cy = x + tw / 2.0, y + th / 2.0
    points = []
    frames = int(round(duration * fps))
    for i in range(frames):
        if i:
            ok, frame = cap.read()
            if not ok:
                break
        else:
            frame = first
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sw = max(tw + 16, int(tw * (1 + search * 2)))
        sh = max(th + 16, int(th * (1 + search * 2)))
        sx = int(max(0, min(W - sw, cx - sw / 2)))
        sy = int(max(0, min(H - sh, cy - sh / 2)))
        roi = gray[sy:sy + sh, sx:sx + sw]
        score = 0.0
        nx, ny = cx, cy
        if roi.shape[0] >= th and roi.shape[1] >= tw:
            res = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            score = float(maxv)
            if score >= threshold:
                nx = sx + maxloc[0] + tw / 2.0
                ny = sy + maxloc[1] + th / 2.0
        if score >= threshold:
            cx = smooth * cx + (1 - smooth) * nx
            cy = smooth * cy + (1 - smooth) * ny
        points.append({"t": round(i / fps, 4),
                       "x": round(cx, 2), "y": round(cy, 2),
                       "nx": round(cx / W, 4), "ny": round(cy / H, 4),
                       "score": round(score, 3)})
    cap.release()
    return {"input": str(src), "fps": round(fps, 3), "width": W, "height": H,
            "start": start, "duration": round(len(points) / fps, 3),
            "template": {"x": x, "y": y, "w": tw, "h": th},
            "points": points}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Track a patch through a clip")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    spec = _load(args.spec or (project_dir / "track.json"))
    started = time.time()
    data = track(spec)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or (OUTPUT_DIR / project_dir.name / "track"
                       / f"track_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    data["elapsed_sec"] = round(time.time() - started, 1)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[track] {len(data['points'])} points -> {out} "
              f"({data['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
