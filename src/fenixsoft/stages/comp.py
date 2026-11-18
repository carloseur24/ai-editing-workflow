#!/usr/bin/env python3
"""comp — compositing/VFX over a base clip.

One base, N layers, one FFmpeg graph:
  * alpha MOV/PNG layers or chroma-keyed footage
  * rectangular or elliptical masks
  * animated placement (linear move or `fenix track` points) and scale/opacity
  * fade in/out

Spec (`input/<project>/comp.json`), paths relative to the repo root:

    {
      "base": "input/p/clips/base.mp4",
      "width": 1080, "height": 1920, "fps": 60, "duration": 10,
      "layers": [
        {"src": "output/p/motion/.../plate.mov",
         "start": 1.0, "end": 5.0,
         "x": 0.5, "y": 0.5, "scale": 1.0, "opacity": 1.0,
         "move": {"from": [0.5, 0.55], "to": [0.5, 0.45]},
         "track": "output/p/track/track_...json",
         "mask": {"type": "rect|ellipse", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
         "key": {"color": "#00ff00", "similarity": 0.12, "blend": 0.05}}
      ],
      "fade_in": 0.3, "fade_out": 0.4
    }

Usage:
  fenix comp <project> [--spec PATH] [--out MP4] [--deliver] [--json]
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

DEFAULTS = {"width": 1080, "height": 1920, "fps": 60, "duration": 10,
            "layers": [], "fade_in": 0.0, "fade_out": 0.0}


def _px(value, total: int) -> float:
    value = float(value)
    return value * total if 0.0 <= value <= 1.0 else value


def _resolve(value: str | None) -> Path | None:
    if not value:
        return None
    return repo_path(value)


def _load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[comp] missing spec: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[comp] invalid {path}: {exc}")
    spec = dict(DEFAULTS)
    spec.update(data)
    return spec


def validate(spec: dict) -> list[str]:
    errors: list[str] = []
    base = _resolve(spec.get("base"))
    if not base or not base.exists():
        errors.append(f"base not found: {spec.get('base')}")
    for i, layer in enumerate(spec.get("layers") or []):
        src = _resolve(layer.get("src"))
        if not src or not src.exists():
            errors.append(f"layer {i}: src not found: {layer.get('src')}")
        start = float(layer.get("start", 0))
        end = float(layer.get("end", spec.get("duration", 0)))
        if end <= start:
            errors.append(f"layer {i}: end must be > start")
        mask = layer.get("mask")
        if mask and mask.get("type") not in (None, "rect", "ellipse"):
            errors.append(f"layer {i}: mask type must be 'rect' or 'ellipse'")
    if float(spec.get("duration", 0)) <= 0:
        errors.append("duration must be > 0")
    return errors


def _hex(value: str) -> str:
    v = value.lstrip("#")
    return "0x" + (v * 2 if len(v) == 3 else v)


def _track_expr(points: list[dict], key: str, size: int) -> str | None:
    """Piecewise-linear FFmpeg expression from tracked normalized points."""
    pts = [(float(p["t"]), float(p[key]) * size) for p in points
           if key in p and "t" in p]
    if not pts:
        return None
    # FFmpeg's expression stack chokes on 100+ nested ifs; 32 control points
    # with linear interpolation is visually identical at 30/60 fps.
    if len(pts) > 32:
        step = max(1, len(pts) // 32)
        last = pts[-1]
        pts = pts[::step]
        if pts[-1][0] != last[0]:
            pts.append(last)
    expr = f"{pts[-1][1]:.2f}"
    for i in range(len(pts) - 1, 0, -1):
        t0, x0 = pts[i - 1]
        t1, x1 = pts[i]
        dt = max(1e-3, t1 - t0)
        expr = (f"if(lt(t,{t1:.3f}),{x0:.2f}+({x1:.2f}-{x0:.2f})*"
                f"max(0,min(1,(t-{t0:.3f})/{dt:.3f})),{expr})")
    return expr


def _graph(spec: dict) -> tuple[list[str], str]:
    W, H = int(spec["width"]), int(spec["height"])
    fps = int(spec["fps"])
    dur = float(spec["duration"])
    layers = spec.get("layers") or []

    inputs: list[str] = ["-i", str(_resolve(spec["base"]))]
    images = {".png", ".jpg", ".jpeg", ".webp"}
    for layer in layers:
        src = _resolve(layer["src"])
        if src.suffix.lower() in images:
            inputs += ["-loop", "1", "-framerate", str(fps), "-i", str(src)]
        else:
            inputs += ["-i", str(src)]

    filters = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={W}:{H},setsar=1,fps={fps},trim=0:{dur},"
        f"setpts=PTS-STARTPTS[base]"
    ]
    current = "base"
    for i, layer in enumerate(layers):
        idx = i + 1
        start = float(layer.get("start", 0))
        end = float(layer.get("end", dur))
        scale = float(layer.get("scale", 1.0))
        chain = (f"[{idx}:v]trim={start}:{end},setpts=PTS-STARTPTS,"
                 f"scale=iw*{scale:.4f}:ih*{scale:.4f},setsar=1")
        mask = layer.get("mask")
        if mask:
            mx = int(_px(mask.get("x", 0), W))
            my = int(_px(mask.get("y", 0), H))
            mw = int(_px(mask.get("w", 1), W))
            mh = int(_px(mask.get("h", 1), H))
            chain += f",crop={max(2, mw)}:{max(2, mh)}:{max(0, mx)}:{max(0, my)}"
        key = layer.get("key")
        if key:
            chain += (f",chromakey=color={_hex(key.get('color', '#00ff00'))}:"
                      f"similarity={float(key.get('similarity', 0.12))}:"
                      f"blend={float(key.get('blend', 0.05))}")
        chain += ",format=rgba"
        mask = layer.get("mask") or {}
        if mask.get("type") == "ellipse":
            mx = _px(mask.get("x", 0.5), W)
            my = _px(mask.get("y", 0.5), H)
            rx = max(2.0, _px(mask.get("w", 0.6), W) / 2.0)
            ry = max(2.0, _px(mask.get("h", 0.6), H) / 2.0)
            chain += (f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                      f"a='if(lte((X-{mx:.1f})^2/{rx:.1f}^2+(Y-{my:.1f})^2/"
                      f"{ry:.1f}^2,1),alpha(X,Y),0)'")
        opacity = float(layer.get("opacity", 1.0))
        if opacity < 1.0:
            chain += f",colorchannelmixer=aa={opacity:.3f}"
        chain += f",setpts=PTS+{start}/TB[l{i}]"
        filters.append(chain)

        move = layer.get("move") or {}
        start_pos = move.get("from") or [layer.get("x", 0.5),
                                         layer.get("y", 0.5)]
        end_pos = move.get("to") or [layer.get("x", 0.5), layer.get("y", 0.5)]
        track_path = _resolve(layer.get("track"))
        if track_path and track_path.exists():
            data = json.loads(track_path.read_text(encoding="utf-8"))
            ex = _track_expr(data.get("points") or [], "nx", W)
            ey = _track_expr(data.get("points") or [], "ny", H)
            ex = f"({ex}-overlay_w/2)" if ex else "0"
            ey = f"({ey}-overlay_h/2)" if ey else "0"
        else:
            x0, y0 = _px(start_pos[0], W), _px(start_pos[1], H)
            x1, y1 = _px(end_pos[0], W), _px(end_pos[1], H)
            span = max(1e-6, end - start)
            ex = (f"({x0:.2f}+({x1 - x0:.2f})*"
                  f"min(max((t-{start:.3f})/{span:.3f},0),1)-overlay_w/2)")
            ey = (f"({y0:.2f}+({y1 - y0:.2f})*"
                  f"min(max((t-{start:.3f})/{span:.3f},0),1)-overlay_h/2)")
        filters.append(
            f"[{current}][l{i}]overlay=x='{ex}':y='{ey}':"
            f"enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[v{i}]")
        current = f"v{i}"

    fade_in = float(spec.get("fade_in", 0))
    fade_out = float(spec.get("fade_out", 0))
    tail = []
    if fade_in > 0:
        tail.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        tail.append(f"fade=t=out:st={max(0, dur - fade_out):.3f}:d={fade_out:.3f}")
    tail.append("format=yuv420p")
    filters.append(f"[{current}]" + ",".join(tail) + "[vout]")
    return inputs, ";".join(filters)


def render(spec: dict, out: Path) -> dict:
    errors = validate(spec)
    if errors:
        sys.exit("[comp] invalid spec: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs, graph = _graph(spec)
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", "-shortest", str(out)]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"[comp] ffmpeg failed:\n{proc.stderr[-1200:]}")
    return {"path": str(out), "duration_sec": spec["duration"],
            "layers": len(spec.get("layers") or []),
            "elapsed_sec": round(time.time() - started, 1)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Composite layers over a base clip")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    spec_path = args.spec or (project_dir / "comp.json")
    spec = _load(spec_path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = OUTPUT_DIR / project_dir.name / "comp" / f"{project_dir.name}_{stamp}"
    out = args.out or (run / "comp.mp4")
    data = render(spec, out)
    run.mkdir(parents=True, exist_ok=True)
    (run / "comp.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    if args.deliver:
        cfg = load_config(DEFAULT_CONFIG)
        deliv = cfg.get("delivery") or {}
        shelf = Path(deliv.get("dir", "")).expanduser() / run.name
        shelf.mkdir(parents=True, exist_ok=True)
        for item in (out, run / "comp.json", run / "manifest.json"):
            shutil.copy2(item, shelf / item.name)
        downloads = deliv.get("downloads")
        if downloads:
            dl = Path(downloads).expanduser()
            dl.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out, dl / f"{run.name}.mp4")
            print(f"[comp] downloads: {dl / (run.name + '.mp4')}")
        print(f"[comp] shelf: {shelf}")

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[comp] {data['path']} ({data['layers']} layers, "
              f"{data['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
