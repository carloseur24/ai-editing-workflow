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
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..context import (DEFAULT_CONFIG, load_config, repo_path,
                       resolve_sku_dir, stage_dir)
from ..domain import encode, runs

DEFAULTS = {"width": 1080, "height": 1920, "fps": 60, "duration": 10,
            "layers": [], "fade_in": 0.0, "fade_out": 0.0}


def _px(value, total: int) -> float:
    value = float(value)
    return value * total if 0.0 <= value <= 1.0 else value


def _resolve(value: str | None, project: str | None = None) -> Path | None:
    """A repo-relative path, or a stable ref such as ``@render/latest``.

    Stable refs are resolved against the project's run index and latest stage
    output, so a comp spec does not have to carry a timestamp that changes on
    every render. An unresolvable ref returns the expected path so ``validate``
    can name it; ``None`` means the field was empty.
    """
    if not value:
        return None
    if runs.is_stable_ref(value):
        if not project:
            return None
        resolved = runs.resolve_stable_ref(value, project)
        return resolved if resolved else Path(str(value))
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
    project = spec.get("_project")
    errors: list[str] = []
    base = _resolve(spec.get("base"), project)
    if not base or not base.exists():
        errors.append(f"base not found: {spec.get('base')}")
    for i, layer in enumerate(spec.get("layers") or []):
        src = _resolve(layer.get("src"), project)
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
    project = spec.get("_project")

    inputs: list[str] = ["-i", str(_resolve(spec["base"], project))]
    images = {".png", ".jpg", ".jpeg", ".webp"}
    for layer in layers:
        src = _resolve(layer["src"], project)
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
        start = float(layer.get("start", 0))
        end = float(layer.get("end", dur))
        filters.append(_layer_chain(layer, i + 1, i, W, H, start, end))
        move = layer.get("move") or {}
        start_pos = move.get("from") or [layer.get("x", 0.5),
                                         layer.get("y", 0.5)]
        end_pos = move.get("to") or [layer.get("x", 0.5), layer.get("y", 0.5)]
        track_path = _resolve(layer.get("track"), project)
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


def _layer_chain(layer: dict, input_index: int, out_index: int,
                 W: int, H: int, start: float, end: float,
                 *, trim: bool = True) -> str:
    """The scale/mask/key/opacity chain for one layer.

    ``trim=True`` is the video path (source interval + timed start). The stills
    preview seeks each input itself, so it reuses the pixel chain without the
    trim/setpts pair — one definition, no drift between preview and render.
    """
    scale = float(layer.get("scale", 1.0))
    if trim:
        chain = (f"[{input_index}:v]trim={start}:{end},setpts=PTS-STARTPTS,"
                 f"scale=iw*{scale:.4f}:ih*{scale:.4f},setsar=1")
    else:
        chain = f"[{input_index}:v]scale=iw*{scale:.4f}:ih*{scale:.4f},setsar=1"
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
    if trim:
        chain += f",setpts=PTS+{start}/TB"
    return chain + f"[l{out_index}]"


def _file_fingerprint(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"path": str(path), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns}


def inputs_fingerprint(spec: dict) -> dict:
    """Resolved base/layer/track files with size + mtime: what reuse compares."""
    project = spec.get("_project")
    return {
        "base": _file_fingerprint(_resolve(spec.get("base"), project)),
        "layers": [_file_fingerprint(_resolve(layer.get("src"), project))
                   for layer in spec.get("layers") or []],
        "tracks": [_file_fingerprint(_resolve(layer.get("track"), project))
                   for layer in spec.get("layers") or []],
    }


def spec_fingerprint(spec: dict) -> str:
    """Hash of the spec itself (stable refs kept verbatim) + input files.

    A new render of ``@render/latest`` changes the resolved file mtime, so the
    input half refuses a stale reuse even if the spec text is unchanged.
    """
    payload = {k: v for k, v in spec.items() if not k.startswith("_")}
    raw = json.dumps({"spec": payload, "inputs": inputs_fingerprint(spec)},
                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_reusable(project: str, fingerprint: str) -> tuple[Path, Path, dict] | None:
    """Newest earlier comp whose spec + inputs match ``fingerprint``."""
    base = stage_dir(project, "comp")
    if not base.exists():
        return None
    manifests = sorted(base.glob("*/manifest.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    for manifest in manifests:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("fingerprint") != fingerprint:
            continue
        path = Path(str(data.get("path") or ""))
        if path.exists():
            return manifest.parent, path, data
    return None


def build_command(spec: dict, out: Path, cfg: dict) -> list[str]:
    """The comp ffmpeg command, using the shared encoding contract.

    Audio is copied from the base: the graph only touches video, so a second
    AAC generation added nothing and cost a re-encode. The caller retries with
    ``copy_audio=False`` if the container rejects the copy.
    """
    inputs, graph = _graph(spec)
    return [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
        *encode.video_args(cfg),
        *encode.audio_args(cfg, copy_audio=True),
        "-shortest", str(out),
    ]


def _encode(spec: dict, out: Path, cfg: dict) -> tuple[dict, str]:
    cmd = build_command(spec, out, cfg)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Some bases carry an audio codec MP4 will not accept with -c:a copy.
        retry = [a for a in cmd]
        idx = retry.index("-c:a")
        retry[idx:idx + 2] = encode.audio_args(cfg, copy_audio=False)
        proc = subprocess.run(retry, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"[comp] ffmpeg failed:\n{proc.stderr[-1200:]}")
        return {"audio": "re-encoded"}, "re-encoded"
    return {"audio": "copy"}, "copy"


def enforce_budget(out: Path, cfg: dict) -> dict:
    """Re-encode above ``encoding.max_whatsapp_bytes`` (same fallback as render)."""
    limit = encode.budget_bytes(cfg)
    before = out.stat().st_size if out.exists() else 0
    if before <= limit:
        return {"fallback": False, "bytes": before, "max_bytes": limit}
    tmp = out.with_name(out.stem + "_fallback" + out.suffix)
    proc = subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(out),
        *encode.fallback_args(cfg), str(tmp),
    ], capture_output=True, text=True)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"[comp] budget fallback failed:\n{proc.stderr[-1200:]}")
    tmp.replace(out)
    return {"fallback": True, "bytes_before": before,
            "bytes": out.stat().st_size, "max_bytes": limit}


def render(spec: dict, out: Path, cfg: dict | None = None) -> dict:
    errors = validate(spec)
    if errors:
        sys.exit("[comp] invalid spec: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = cfg or load_config(DEFAULT_CONFIG)
    started = time.time()
    encode_note, audio_mode = _encode(spec, out, cfg)
    budget = enforce_budget(out, cfg)
    return {"path": str(out), "duration_sec": spec["duration"],
            "layers": len(spec.get("layers") or []),
            "elapsed_sec": round(time.time() - started, 1),
            "encoding": {**encode.settings(cfg), **encode_note},
            "audio_mode": audio_mode,
            "budget": budget}


def _points_at(points: list[dict], key: str, t: float, size: int) -> float | None:
    """Piecewise-linear tracked coordinate at ``t`` (same shape as _track_expr)."""
    pts = [(float(p["t"]), float(p[key]) * size) for p in points
           if key in p and "t" in p]
    if not pts:
        return None
    pts.sort(key=lambda p: p[0])
    if t <= pts[0][0]:
        return pts[0][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t <= t1:
            span = max(1e-6, t1 - t0)
            return v0 + (v1 - v0) * max(0.0, min(1.0, (t - t0) / span))
    return pts[-1][1]


def _position_at(layer: dict, t: float, W: int, H: int,
                 track_points: list[dict] | None = None) -> tuple[float, float]:
    """Layer centre in pixels at absolute time ``t`` (preview: numeric, no expr)."""
    if track_points:
        nx = _points_at(track_points, "nx", t, W)
        ny = _points_at(track_points, "ny", t, H)
        if nx is not None and ny is not None:
            return nx, ny
    move = layer.get("move") or {}
    start_pos = move.get("from") or [layer.get("x", 0.5),
                                     layer.get("y", 0.5)]
    end_pos = move.get("to") or [layer.get("x", 0.5), layer.get("y", 0.5)]
    start = float(layer.get("start", 0))
    end = float(layer.get("end", t))
    p = max(0.0, min(1.0, (t - start) / max(1e-6, end - start)))
    x0, y0 = _px(start_pos[0], W), _px(start_pos[1], H)
    x1, y1 = _px(end_pos[0], W), _px(end_pos[1], H)
    return x0 + (x1 - x0) * p, y0 + (y1 - y0) * p


def build_still_command(spec: dict, t: float, out: Path) -> list[str]:
    """One ffmpeg call: base frame + each active layer composited at ``t``.

    Uses the same pixel chain as :func:`_graph` (scale/mask/key/opacity) and the
    same position maths, but seeks the inputs instead of filtering the whole
    timeline, so a review still costs one seek rather than a full encode.
    """
    W, H = int(spec["width"]), int(spec["height"])
    dur = float(spec["duration"])
    project = spec.get("_project")
    images = {".png", ".jpg", ".jpeg", ".webp"}
    active: list[tuple[dict, Path, list[dict] | None]] = []
    for layer in spec.get("layers") or []:
        start = float(layer.get("start", 0))
        end = float(layer.get("end", dur))
        if not (start <= t <= end):
            continue
        src = _resolve(layer["src"], project)
        if src is None or not src.exists():
            continue
        track_path = _resolve(layer.get("track"), project)
        points = None
        if track_path and track_path.exists():
            try:
                points = json.loads(track_path.read_text(encoding="utf-8")) \
                    .get("points") or []
            except (OSError, json.JSONDecodeError):
                points = None
        active.append((layer, src, points))
    base = _resolve(spec["base"], project)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}",
           "-i", str(base)]
    for layer, src, _ in active:
        if src.suffix.lower() not in images:
            local = max(0.0, t - float(layer.get("start", 0)))
            cmd += ["-ss", f"{local:.3f}"]
        cmd += ["-i", str(src)]
    filters = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={W}:{H},setsar=1,format=yuv420p[base]"
    ]
    current = "base"
    for i, (layer, _src, points) in enumerate(active):
        start = float(layer.get("start", 0))
        end = float(layer.get("end", dur))
        filters.append(_layer_chain(layer, i + 1, i, W, H, start, end,
                                    trim=False))
        cx, cy = _position_at(layer, t, W, H, points)
        filters.append(
            f"[{current}][l{i}]overlay=x='{cx:.2f}-overlay_w/2':"
            f"y='{cy:.2f}-overlay_h/2':eof_action=pass[v{i}]")
        current = f"v{i}"
    filters.append(f"[{current}]format=yuv420p[vout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]",
            "-frames:v", "1", str(out)]
    return cmd


def base_cut_times(spec: dict, *, limit: int = 12) -> list[dict]:
    """Cut midpoints of the comp's base edit plan (or evenly spaced samples)."""
    dur = float(spec.get("duration", 0))
    base = _resolve(spec.get("base"), spec.get("_project"))
    plan_path = base.parent / "edit_plan.json" if base else None
    plan: dict = {}
    if plan_path and plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            plan = {}
    cuts = [c for c in (plan.get("cuts") or [])
            if c.get("dur") and c.get("t0") is not None]
    rows: list[dict] = []
    for i, cut in enumerate(cuts[:limit]):
        t = float(cut["t0"]) + float(cut["dur"]) / 2.0
        rows.append({"kind": "cut", "cut": i, "t": round(t, 3),
                     "label": f"cut {i} midpoint ({cut.get('clip')})"})
    if rows:
        return rows
    n = max(1, min(limit, 5))
    return [{"kind": "sample", "cut": i,
             "t": round(dur * (i + 0.5) / n, 3),
             "label": f"sample {i}"} for i in range(n)]


def render_preview(spec: dict, cfg: dict | None = None) -> dict:
    """Stills of the comp at the base cut midpoints (no full encode)."""
    errors = validate(spec)
    if errors:
        sys.exit("[comp] invalid spec: " + "; ".join(errors))
    project = spec.get("_project") or ""
    cfg = cfg or load_config(DEFAULT_CONFIG)
    pc = {"max_stills": 12, "tile_width": 270, "tile_height": 480,
          "columns": 4}
    pc.update(cfg.get("preview") or {})
    from ..render.preview import contact_sheet
    base_dir = stage_dir(project, "preview") if project else Path("preview")
    out_dir = runs.new_timestamped_dir(base_dir / "comp")
    started = time.time()
    samples = base_cut_times(spec, limit=int(pc.get("max_stills", 12)))
    frames: list[Path] = []
    rows: list[dict] = []
    for i, sample in enumerate(samples):
        name = f"frame_{i:02d}_{float(sample['t']):05.2f}s.png"
        path = out_dir / name
        proc = subprocess.run(build_still_command(spec, float(sample["t"]),
                                                  path),
                              capture_output=True, text=True)
        if proc.returncode != 0 or not path.exists():
            print(f"[comp] preview: skipped t={sample['t']}: "
                  f"{proc.stderr.strip().splitlines()[-1] if proc.stderr else 'ffmpeg failed'}")
            continue
        frames.append(path)
        active = [j for j, layer in enumerate(spec.get("layers") or [])
                  if float(layer.get("start", 0)) <= float(sample["t"])
                  <= float(layer.get("end", spec["duration"]))]
        rows.append({**sample, "file": name, "layers": active})
    sheet = contact_sheet(frames, out_dir / "contact_sheet.png",
                          cols=int(pc.get("columns", 4)),
                          tile=(int(pc.get("tile_width", 270)),
                                int(pc.get("tile_height", 480))))
    manifest = {
        "kind": "comp",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dir": str(out_dir),
        "base": str(_resolve(spec.get("base"), project) or ""),
        "duration_sec": spec.get("duration"),
        "layers": len(spec.get("layers") or []),
        "frames": rows,
        "contact_sheet": str(sheet) if sheet else None,
        "elapsed_sec": round(time.time() - started, 2),
        "note": "stills only: layer placement at each base cut midpoint; "
                "global fades are not previewed",
    }
    (out_dir / "preview.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def deliver_run(run: Path, out: Path, cfg: dict) -> dict:
    """Deliver one comp run through the shared bundle (media + spec + manifest).

    The stage → ops import is deliberate and delivery-only: the planner lives
    in ``ops/bundle.py`` so an edit render, a comp and a grade ship the same
    evidence set. It is imported inside the function so importing ``comp`` for
    a render does not pull the delivery layer in.
    """
    from ..ops import bundle as bundle_mod
    project = run.name.rsplit("_", 1)[0]
    plan = bundle_mod.bundle_plan(project, run, cfg, master=out)
    if not plan:
        sys.exit(f"[comp] nothing to deliver in {run} "
                 "(configure delivery.dir or delivery.downloads)")

    readme_path = run / "delivery_readme.md"
    no_voice = False
    try:
        base = Path(json.loads((run / "comp.json").read_text(
            encoding="utf-8")).get("base") or "")
        if not base.is_absolute():
            from ..context import repo_path
            base = repo_path(str(base))
        base_plan = json.loads((base.parent / "edit_plan.json").read_text(
            encoding="utf-8"))
        no_voice = bool(base_plan.get("music_only"))
    except (OSError, ValueError, TypeError):
        no_voice = False
    readme_path.write_text(
        bundle_mod.write_bundle_readme(project, run, plan, cfg,
                                       no_voice=no_voice),
        encoding="utf-8")
    shelf_root = next((Path(row["dest"]).parent for row in plan
                       if row["kind"] == "shelf"), None)
    if shelf_root is not None:
        readme_dest = shelf_root / readme_path.name
        if all(Path(row["dest"]) != readme_dest for row in plan):
            plan.append({"src": readme_path, "dest": readme_dest,
                         "kind": "shelf"})

    result = bundle_mod.apply_bundle(plan)
    for warning in result["skipped"]:
        print(f"[comp] WARNING: missing source skipped: {warning}")

    files = []
    for row in plan:
        src, dest = Path(row["src"]), Path(row["dest"])
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            sys.exit(f"[comp] copy verification failed: {dest}")
        if row["kind"] == "shelf":
            files.append(dest.name)
        else:
            print(f"[comp] downloads: {dest}")

    record = {"run": run.name,
              "delivered_to": str(shelf_root) if shelf_root else None,
              "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "files": files,
              "copied": result["copied"],
              "skipped": result["skipped"],
              "downloads_to": str(Path(next(
                  row["dest"] for row in plan
                  if row["kind"] == "downloads")).parent) if any(
                      row["kind"] == "downloads" for row in plan) else None,
              "master": str(shelf_root / out.name) if shelf_root else str(out)}
    (run / "delivery.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[comp] shelf: {shelf_root or '(downloads only)'}")
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Composite layers over a base clip")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--reuse", action="store_true",
                        help="copy the newest comp with the same spec + inputs "
                             "instead of re-encoding")
    parser.add_argument("--force", action="store_true",
                        help="re-encode even when --deliver could reuse the "
                             "newest matching comp")
    parser.add_argument("--preview", action="store_true",
                        help="stills at the base cut midpoints with the layers "
                             "composited; no encode, no run")
    parser.add_argument("--deliver", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    cfg = load_config(DEFAULT_CONFIG, project=project_dir)
    spec_path = args.spec or (project_dir / "comp.json")
    spec = _load(spec_path)
    spec["_project"] = project_dir.name

    if args.preview:
        manifest = render_preview(spec, cfg)
        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            print(f"[comp] preview: {len(manifest['frames'])} still(s) in "
                  f"{manifest['elapsed_sec']}s -> {manifest['dir']}")
            if manifest.get("contact_sheet"):
                print(f"[comp] sheet: {manifest['contact_sheet']}")
            print("[comp] previews are review artifacts: not in runs.json, "
                  "not a delivery master")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = stage_dir(project_dir, "comp") / f"{project_dir.name}_{stamp}"
    out = args.out or (run / "comp.mp4")
    fingerprint = spec_fingerprint(spec)
    # A delivery is a copy job: reuse the newest comp whose spec and resolved
    # inputs are unchanged unless --force asks for a fresh encode.
    reuse = args.reuse or (args.deliver and not args.force)

    data: dict | None = None
    if reuse:
        found = find_reusable(project_dir.name, fingerprint)
        if found:
            source_run, source_out, source_data = found
            if args.deliver and args.out is None:
                # Delivering an unchanged comp must not manufacture another
                # identical run directory: deliver the run that already holds
                # the media (a copy job, no ffmpeg).
                print(f"[comp] delivering existing {source_run.name} "
                      f"(spec + inputs unchanged)")
                deliver_run(source_run, source_out, cfg)
                if args.json:
                    print(json.dumps(source_data, indent=2))
                else:
                    print(f"[comp] {source_out} ({source_data.get('layers', 0)} "
                          f"layers, reused, 0.0s)")
                return
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_out, out)
            data = {
                "path": str(out),
                "duration_sec": source_data.get("duration_sec", spec["duration"]),
                "layers": source_data.get("layers", len(spec.get("layers") or [])),
                "elapsed_sec": 0.0,
                "reused_from": source_run.name,
                "encoding": source_data.get("encoding")
                or encode.settings(cfg),
                "audio_mode": source_data.get("audio_mode"),
                "budget": source_data.get("budget"),
            }
            print(f"[comp] reused {source_run.name} (spec + inputs unchanged)")
    if data is None:
        if reuse:
            print("[comp] reuse: no matching comp; rendering a new one")
        data = render(spec, out, cfg)
    data["fingerprint"] = fingerprint
    data["inputs"] = inputs_fingerprint(spec)

    run.mkdir(parents=True, exist_ok=True)
    public_spec = {k: v for k, v in spec.items() if not k.startswith("_")}
    (run / "comp.json").write_text(json.dumps(public_spec, indent=2),
                                   encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps(data, indent=2),
                                       encoding="utf-8")

    if args.deliver:
        deliver_run(run, out, cfg)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        mode = ("reused" if data.get("reused_from") else "rendered")
        budget = data.get("budget") or {}
        print(f"[comp] {data['path']} ({data['layers']} layers, {mode}, "
              f"{data['elapsed_sec']}s"
              + (", budget fallback" if budget.get("fallback") else "") + ")")


if __name__ == "__main__":
    main()
