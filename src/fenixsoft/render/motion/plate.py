"""Spec-driven motion plate renderer.

A plate is one self-contained motion-graphics shot: background, one SDF object
(brain/sphere/torus/blob), optional text layers, grain and audio. It can render
to MP4 (with background) or MOV ProRes 4444 (transparent, for compositing).

Spec (`input/<project>/motion.json`) is documented in `docs/pipeline.md`.
"""
from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...context import FONTS_DIR, REPO_ROOT
from ...domain import safe_zones
from .sdf import PRESETS

DEFAULTS = {
    "width": 1080,
    "height": 1920,
    "fps": 60,
    "duration_sec": 6.0,
    "background": "#0c0f0e",
    "grain": 0.0,
    "object": {
        "preset": "brain",
        "size": 860,
        "center": [0.5, 0.5],
        "scale": 1.0,
        "rotate": {"rx": [-28.0, 0.0], "ry": [-42.0, 318.0], "rz": [12.0, -12.0]},
        "light": {"angle": [-60.0, -145.0]},
        "texture_top": "assets/motion/brain.png",
        "texture_side": "assets/motion/brain_side.png",
        "mesh": 280,
        "raymarch_steps": 20,
    },
    "text": [],
    "audio": None,
}


# --------------------------------------------------------------------- easing
def _clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _ease(t):
    t = _clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _pair(value, t, ease=True):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        p = _ease(t) if ease else _clamp(t)
        return float(value[0]) + (float(value[1]) - float(value[0])) * p
    return float(value)


def _resolve(path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


# -------------------------------------------------------------------- textures
def _prep_tex(path: Path | None, size: int = 512):
    if not path or not path.exists():
        return None
    rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if rgba is None:
        return None
    if rgba.shape[2] == 4:
        rgba = cv2.cvtColor(rgba, cv2.COLOR_BGRA2BGR)
    r = cv2.resize(rgba, (size, size), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(r, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    smooth = cv2.GaussianBlur(gray, (9, 9), 0)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=5) * 1.4
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=5) * 1.4
    return gray, gx, gy


# --------------------------------------------------------------------- object
def render_object(obj: dict, rx: float, ry: float, rz: float,
                  light_deg: float, tex_top=None, tex_side=None):
    preset = str(obj.get("preset", "brain"))
    sdf = PRESETS.get(preset)
    if sdf is None:
        raise SystemExit(f"[motion] unknown preset '{preset}'; "
                         f"choose from {', '.join(PRESETS)}")
    n = int(obj.get("mesh", 280))
    steps = int(obj.get("raymarch_steps", 20))

    rx, ry, rz = map(math.radians, (rx, ry, rz))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], dtype=np.float32)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], dtype=np.float32)
    R = Rz @ Ry @ Rx
    R_inv = R.T

    u = np.linspace(-1.12, 1.12, n, dtype=np.float32)
    gx, gy = np.meshgrid(u, -u)
    ox = R_inv[0, 0] * gx + R_inv[0, 1] * gy + R_inv[0, 2] * 2.2
    oy = R_inv[1, 0] * gx + R_inv[1, 1] * gy + R_inv[1, 2] * 2.2
    oz = R_inv[2, 0] * gx + R_inv[2, 1] * gy + R_inv[2, 2] * 2.2
    dx, dy, dz = -R_inv[0, 2], -R_inv[1, 2], -R_inv[2, 2]

    b_dot = ox * dx + oy * dy + oz * dz
    c_val = ox * ox + oy * oy + oz * oz - 1.22 * 1.22
    inside = (b_dot * b_dot - c_val) > 0
    t = np.where(inside,
                 np.maximum(0.0, -b_dot - np.sqrt(np.maximum(
                     b_dot * b_dot - c_val, 0.0))), 4.0)
    min_d = np.full_like(t, 1.0)
    for _ in range(steps):
        px, py, pz = ox + t * dx, oy + t * dy, oz + t * dz
        d = np.where(inside & (t < 3.6), sdf(px, py, pz), 1.0)
        min_d = np.minimum(min_d, d)
        t = t + d * 0.92

    px, py, pz = ox + t * dx, oy + t * dy, oz + t * dz
    eps = 0.015
    nx = sdf(px + eps, py, pz) - sdf(px - eps, py, pz)
    ny = sdf(px, py + eps, pz) - sdf(px, py - eps, pz)
    nz = sdf(px, py, pz + eps) - sdf(px, py, pz - eps)
    nlen = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-6
    nx, ny, nz = nx / nlen, ny / nlen, nz / nlen

    if tex_top is not None and tex_side is not None:
        u_top = np.clip((px / 0.82 * 0.45 + 0.5) * 511.0, 0, 511)
        v_top = np.clip((-py / 0.90 * 0.45 + 0.5) * 511.0, 0, 511)
        u_side = np.clip((py / 0.90 * 0.45 + 0.5) * 511.0, 0, 511)
        v_side = np.clip((-pz / 0.72 * 0.45 + 0.5) * 511.0, 0, 511)
        top = cv2.remap(tex_top[0], u_top, v_top, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT)
        tgx = cv2.remap(tex_top[1], u_top, v_top, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT)
        tgy = cv2.remap(tex_top[2], u_top, v_top, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT)
        side = cv2.remap(tex_side[0], u_side, v_side, cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REFLECT)
        sgx = cv2.remap(tex_side[1], u_side, v_side, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT)
        sgy = cv2.remap(tex_side[2], u_side, v_side, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT)
        w_top = np.clip(np.abs(nz) ** 1.3, 0.0, 1.0)
        albedo = top * w_top + side * (1.0 - w_top)
        dgx = tgx * w_top + sgx * (1.0 - w_top)
        dgy = tgy * w_top + sgy * (1.0 - w_top)
    else:
        albedo = np.full_like(nx, 0.78, dtype=np.float32)
        dgx = np.zeros_like(nx)
        dgy = np.zeros_like(nx)

    nx_p = nx + dgx * 0.14
    ny_p = ny + dgy * 0.14
    nz_p = nz
    plen = np.sqrt(nx_p ** 2 + ny_p ** 2 + nz_p ** 2) + 1e-6
    nx_p, ny_p, nz_p = nx_p / plen, ny_p / plen, nz_p / plen
    wnx = R[0, 0] * nx_p + R[0, 1] * ny_p + R[0, 2] * nz_p
    wny = R[1, 0] * nx_p + R[1, 1] * ny_p + R[1, 2] * nz_p
    wnz = R[2, 0] * nx_p + R[2, 1] * ny_p + R[2, 2] * nz_p

    l_rad = math.radians(light_deg)
    lx, ly, lz = math.cos(l_rad) * 0.72, -math.sin(l_rad) * 0.72, 0.68
    ll = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ll, ly / ll, lz / ll
    diff = np.clip(wnx * lx + wny * ly + wnz * lz, 0.0, 1.0)
    fill = np.clip(wnz * 0.85 + wny * 0.15, 0.0, 1.0) * 0.52
    rim = ((1.0 - np.clip(wnz, 0.0, 1.0)) ** 2.5) * 0.14
    shade = np.clip(albedo * (0.56 + 0.44 * diff + fill) + rim, 0.0, 1.0)
    val = (shade * 242.0).astype(np.uint8)
    alpha = np.where(inside, np.clip((0.024 - min_d) / 0.024, 0.0, 1.0), 0.0)

    rgba = np.zeros((n, n, 4), dtype=np.uint8)
    rgba[:, :, 0] = val
    rgba[:, :, 1] = val
    rgba[:, :, 2] = val
    rgba[:, :, 3] = (alpha * 255).astype(np.uint8)
    return cv2.resize(rgba, (int(obj.get("size", 860)),
                             int(obj.get("size", 860))),
                      interpolation=cv2.INTER_LINEAR)


# ----------------------------------------------------------------------- text
def _draw_text(rgba: np.ndarray, layer: dict, t: float) -> None:
    start = float(layer.get("start", 0.0))
    end = float(layer.get("end", 1e9))
    if t < start or t > end:
        return
    text = str(layer.get("text", ""))
    if not text:
        return
    size = int(layer.get("size", 64))
    font_name = layer.get("font", "Inter-Black.ttf")
    font_path = FONTS_DIR / font_name
    font = (ImageFont.truetype(str(font_path), size)
            if font_path.exists() else ImageFont.load_default())
    color = layer.get("color", "#FFFFFF")
    enter = float(layer.get("enter_sec", 0.35))
    exit_ = float(layer.get("exit_sec", 0.30))
    p_in = _ease((t - start) / max(1e-6, enter))
    p_out = _ease((end - t) / max(1e-6, exit_))
    alpha = min(p_in, p_out)
    rise = (1.0 - p_in) * float(layer.get("rise_px", 24))

    img = Image.fromarray(rgba, "RGBA")
    draw = ImageDraw.Draw(img)
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    x = layer.get("x")
    y = layer.get("y")
    x = (rgba.shape[1] / 2 - tw / 2) if x is None else (float(x) - tw / 2)
    y = (rgba.shape[0] / 2 - th / 2) if y is None else float(y)
    y += rise
    col = _hex_rgba(color, alpha)
    if layer.get("shadow", True):
        draw.text((x + 2, y + 2), text, font=font,
                  fill=(0, 0, 0, int(200 * alpha)))
    draw.text((x, y), text, font=font, fill=col)
    rgba[:] = np.array(img)


def _hex_rgba(value: str, alpha: float = 1.0):
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) + (int(255 * alpha),)


# -------------------------------------------------------------------- compose
def resolve_text_zones(spec: dict, cfg: dict, mode: str | None = None) -> list[str]:
    """Replace ``zone`` with the band's pixel ``y`` (explicit ``y`` still wins).

    Zones are resolved once at load time so the executed spec — and therefore
    the run's ``motion.json`` snapshot — carries the exact coordinates used.
    Layers that share a zone stack downward in file order (title, then spec), so
    a ``lower`` title and its ``lower`` spec do not land on the same line.
    """
    warnings: list[str] = []
    preset = mode or spec.get("safe_zone_mode") or "reels_universal"
    stacking: dict[str, int] = {}
    for i, layer in enumerate(spec.get("text") or []):
        if not isinstance(layer, dict):
            continue
        zone = safe_zones.normalize_zone(layer.get("zone"))
        if not zone or layer.get("y") is not None:
            continue
        size = int(layer.get("size", 64))
        y = safe_zones.zone_y(zone, cfg, mode=preset, size=size)
        if y is None:
            warnings.append(f"text[{i}]: zone {zone!r} has no y in "
                            f"safe_zones.{preset}")
            continue
        if zone in stacking:
            y = max(y, stacking[zone])
        layer["y"] = y
        stacking[zone] = y + int(round(size * 1.35))
    return warnings


def render_frame(spec: dict, t: float) -> np.ndarray:
    W, H = int(spec["width"]), int(spec["height"])
    obj = spec.get("object") or {}
    bg = spec.get("background")
    if bg in (None, "transparent"):
        frame = np.zeros((H, W, 4), dtype=np.uint8)
    else:
        frame = np.zeros((H, W, 4), dtype=np.uint8)
        if isinstance(bg, str) and bg.startswith("#"):
            frame[:, :, :3] = _hex_rgba(bg)[:3]            # RGB throughout
        else:
            path = _resolve(bg)
            img = cv2.imread(str(path)) if path and path.exists() else None
            if img is None:
                raise SystemExit(f"[motion] background not found: {bg}")
            img = cv2.cvtColor(cv2.resize(img, (W, H),
                                          interpolation=cv2.INTER_LANCZOS4),
                               cv2.COLOR_BGR2RGB)
            frame[:, :, :3] = img
        frame[:, :, 3] = 255

    duration = float(spec.get("duration_sec", 6.0))
    p = _clamp(t / max(1e-6, duration))
    rot = obj.get("rotate") or {}
    rx = _pair(rot.get("rx", 0.0), p)
    ry = _pair(rot.get("ry", 0.0), p)
    rz = _pair(rot.get("rz", 0.0), p)
    light = _pair((obj.get("light") or {}).get("angle", -75.0), p)

    top = _prep_tex(_resolve(obj.get("texture_top")))
    side = _prep_tex(_resolve(obj.get("texture_side")))
    sprite = render_object(obj, rx, ry, rz, light, top, side)
    scale = float(obj.get("scale", 1.0))
    if scale != 1.0:
        sh, sw = sprite.shape[:2]
        sprite = cv2.resize(sprite, (max(2, int(sw * scale)),
                                     max(2, int(sh * scale))),
                            interpolation=cv2.INTER_AREA)
    cx, cy = obj.get("center", [0.5, 0.5])
    cx = int(float(cx) * W) if float(cx) <= 1.0 else int(float(cx))
    cy = int(float(cy) * H) if float(cy) <= 1.0 else int(float(cy))
    x0 = cx - sprite.shape[1] // 2
    y0 = cy - sprite.shape[0] // 2
    sx0, sy0 = max(0, -x0), max(0, -y0)
    ex = min(sprite.shape[1], W - x0)
    ey = min(sprite.shape[0], H - y0)
    if ex > sx0 and ey > sy0:
        dst = frame[y0 + sy0:y0 + ey, x0 + sx0:x0 + ex]
        src = sprite[sy0:ey, sx0:ex]
        a = src[:, :, 3:4].astype(np.float32) / 255.0
        dst[:, :, :3] = (dst[:, :, :3].astype(np.float32) * (1 - a)
                         + src[:, :, :3].astype(np.float32) * a).astype(np.uint8)
        dst[:, :, 3] = np.maximum(dst[:, :, 3], src[:, :, 3])

    for layer in spec.get("text") or []:
        _draw_text(frame, layer, t)

    grain = float(spec.get("grain", 0.0))
    if grain > 0:
        rng = np.random.default_rng(7 + int(t * 24))
        noise = rng.normal(0.0, grain * 255.0, (H, W, 1)).astype(np.float32)
        frame[:, :, :3] = np.clip(frame[:, :, :3].astype(np.float32) + noise,
                                  0, 255).astype(np.uint8)
    return frame


# --------------------------------------------------------------------- render
def validate(spec: dict) -> list[str]:
    errors = []
    for key in ("width", "height", "fps", "duration_sec"):
        if not spec.get(key):
            errors.append(f"missing {key}")
    preset = (spec.get("object") or {}).get("preset", "brain")
    if preset not in PRESETS:
        errors.append(f"unknown preset '{preset}'")
    if int(spec.get("duration_sec", 0)) <= 0:
        errors.append("duration_sec must be > 0")
    return errors


def render(spec: dict, out: Path, still_at: float | None = None,
           seconds: float | None = None) -> dict:
    errors = validate(spec)
    if errors:
        raise SystemExit("[motion] invalid spec: " + "; ".join(errors))
    out.parent.mkdir(parents=True, exist_ok=True)
    W, H = int(spec["width"]), int(spec["height"])
    fps = int(spec["fps"])
    duration = min(float(spec["duration_sec"]), seconds or 1e9)
    alpha = spec.get("background") in (None, "transparent")

    if still_at is not None:
        frame = render_frame(spec, float(still_at))
        if alpha:
            Image.fromarray(frame, "RGBA").save(out)
        else:
            Image.fromarray(frame[:, :, :3], "RGB").save(out)
        return {"path": str(out), "still": True}

    frames = int(round(duration * fps))
    if alpha:
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
               "-r", str(fps), "-i", "-",
               "-c:v", "prores_ks", "-profile:v", "4444",
               "-pix_fmt", "yuva444p10le", str(out)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(fps), "-i", "-"]
        audio = _resolve(spec.get("audio"))
        if audio and audio.exists():
            cmd += ["-i", str(audio)]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if audio and audio.exists():
            cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
        cmd += [str(out)]

    started = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(frames):
            t = i / fps
            frame = render_frame(spec, t)
            proc.stdin.write(frame.tobytes() if alpha else
                             frame[:, :, :3].tobytes())
            if i and i % (fps * 2) == 0:
                print(f"[motion] {t:5.1f}s / {duration:.1f}s")
    finally:
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"[motion] ffmpeg failed:\n{err}")
    return {"path": str(out), "seconds": round(duration, 3),
            "frames": frames, "elapsed_sec": round(time.time() - started, 1),
            "alpha": alpha}
