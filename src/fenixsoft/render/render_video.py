#!/usr/bin/env python3
"""
render — deterministic 1080x1920 @ 60 fps Fenixsoft renderer.

What changed vs. the v3 renderer (and why):
  * Source clips are normalised to 1080x1920 @ project fps once and cached.
    v3 read one frame per output frame, so 30 fps footage played at 2x speed.
  * Frames are streamed straight into x264 over a rawvideo pipe. v3 wrote a
    lossy mp4v intermediate and encoded it a second time.
  * Cuts are beat-snapped, drawn from rotating clip in-points, and every cut
    gets a 0.16 s punch-in that settles 1.12x -> 1.0x.
  * Music bed + per-cut air-swish SFX are actually mixed (v3 ignored the
    audio block in the config), ducked under the voice, then two-pass
    loudness-normalised to -14 LUFS.
  * Every text element is auto-fitted to its safe zone and the layout is
    asserted before a single frame is encoded.
  * H.264 level is 4.2: 1080x1920@60 is 8160 MBs and 489,600 MB/s, which is
    outside level 3.1/4.0 and is rejected by strict decoders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..context import (CACHE_DIR, DEFAULT_CONFIG, FONTS_DIR, MUSIC_DIR,
                       artifact, load_config, repo_path, resolve_sku_dir)
from ..domain import runs
from ..domain.config_slices import render_slices
from ..domain.quality import (cached_scan, frame_sharpness,
                              normalized_clip_path, window_stats)
from ..stages.beat_grid import cached_measure, load_grid, nearest_beat, write_grid

CACHE = CACHE_DIR
FONT_DIR = FONTS_DIR

QUALITY_DEFAULTS = {
    "in_point_floor": 0.60,
    "beat_offset_warn_ms": 60.0,
    "caption_warn_cps": 20.0,
    "caption_fail_cps": 25.0,
}


def quality_cfg(cfg: dict) -> dict:
    q = dict(QUALITY_DEFAULTS)
    q.update(cfg.get("quality") or {})
    return q


def beat_grid_for(cfg: dict, sku_dir: Path) -> dict | None:
    """Measured grid from `output/<sku>/beat_grid.json`, measuring once if absent.

    The config's `bpm` is a label, not a measurement: the demo track is
    118.97 BPM while the config said 118, which drifts 176 ms by the 16th cut.
    Returns None only when the music file or librosa is unavailable.
    """
    grid = load_grid(sku_dir)
    if grid:
        return grid
    music_cfg = cfg.get("music") or {}
    music = repo_path(music_cfg.get("file") or "")
    if not music.exists():
        return None
    try:
        grid = cached_measure(music, config_bpm=music_cfg.get("bpm") or cfg.get("bpm"))
        write_grid(sku_dir, grid)
    except (SystemExit, Exception) as exc:  # librosa missing -> fall back
        print(f"[render] WARNING: beat grid unavailable ({exc}); "
              f"falling back to the config BPM")
        return None
    print(f"[render] measured beat grid: {grid['bpm']:.2f} BPM "
          f"(config says {cfg.get('bpm')})")
    return grid


# --------------------------------------------------------------------- utils
def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, **kw)


def probe(path: Path) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames:format=duration",
        "-of", "json", str(path),
    ])
    data = json.loads(out)
    st = (data.get("streams") or [{}])[0]
    num, _, den = str(st.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "width": int(st.get("width") or 0),
        "height": int(st.get("height") or 0),
        "fps": fps,
        "duration": float(data.get("format", {}).get("duration") or 0.0),
    }


def probe_loudness(path: Path) -> dict:
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ], capture_output=True, text=True)
    text = proc.stderr
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError(f"loudness measurement failed for {path}")
    return json.loads(text[start:end + 1])


# ------------------------------------------------------------------ clips
def normalize_clip(src: Path, width: int, height: int, fps: int,
                   grade: dict | None = None) -> Path:
    """Transcode a raw clip to exact canvas geometry / CFR so frames map 1:1.

    The colour grade is baked in here (one-time cost) instead of running a LAB
    conversion on every rendered frame.
    """
    out = normalized_clip_path(src, width, height, fps, grade)
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={width}:{height},setsar=1,fps={fps}")
    if grade:
        alpha = float(grade.get("lightness_alpha", 1.06))
        beta = float(grade.get("lightness_beta", 3)) / 255.0
        vf += f",eq=contrast={alpha:.4f}:brightness={beta:.4f}"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", str(out)])
    return out


class ClipReader:
    """Sequential reader with a single seek per cut."""

    def __init__(self, path: Path, fps: int):
        self.path = path
        self.fps = fps
        self.cap = cv2.VideoCapture(str(path))
        self.frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT)) or int(1e9)
        self.duration = self.frames / fps

    def seek(self, seconds: float) -> None:
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, int(max(0.0, seconds) * self.fps))

    def read(self) -> np.ndarray:
        ok, frame = self.cap.read()
        if not ok:                      # loop to the top of the clip
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError(f"cannot decode frames from {self.path}")
        return frame

    def release(self) -> None:
        self.cap.release()


# ------------------------------------------------------------------- fonts
def resolve_font(name: str) -> Path | None:
    for cand in (FONT_DIR / name,
                 Path("/usr/share/fonts/truetype/inter") / name,
                 Path("/usr/share/fonts/truetype/msttcorefonts") / name):
        if cand.exists():
            return cand
    try:
        found = subprocess.check_output(["fc-match", "-f", "%{file}", name],
                                        stderr=subprocess.DEVNULL).decode().strip()
        if found:
            return Path(found)
    except Exception:  # noqa: BLE001
        pass
    return None


class FontBook:
    def __init__(self):
        self.files = {name: resolve_font(name)
                      for name in ("Inter-Black.ttf", "Inter-Bold.ttf",
                                   "Inter-SemiBold.ttf")}
        self.cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))

    def at(self, name: str, size: int) -> ImageFont.FreeTypeFont:
        key = (name, size)
        if key not in self.cache:
            path = self.files.get(name)
            if path and path.exists():
                self.cache[key] = ImageFont.truetype(str(path), size)
            else:
                self.cache[key] = ImageFont.load_default()
        return self.cache[key]

    def measure(self, text: str, font) -> tuple[int, int, tuple]:
        b = self._scratch.textbbox((0, 0), text, font=font)
        return b[2] - b[0], b[3] - b[1], b

    def fit(self, text: str, name: str, start: int, max_width: int,
            min_size: int) -> tuple[ImageFont.FreeTypeFont, int]:
        size = start
        font = self.at(name, size)
        while size > min_size:
            w, _, _ = self.measure(text, font)
            if w <= max_width:
                break
            size -= 2
            font = self.at(name, size)
        return font, size


# ------------------------------------------------------------------ easing
def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def ease_out_sine(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return math.sin(t * math.pi / 2)


# ----------------------------------------------------------------- overlay
def draw_ink_pill(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str,
                  font, bg: str, fg: str, pad_x: int, pad_y: int,
                  radius: int) -> tuple[int, int, int, int]:
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    pw, ph = tw + pad_x * 2, th + pad_y * 2
    x0 = int(cx - pw // 2)
    x1, y1 = x0 + pw, y + ph
    draw.rounded_rectangle([x0, y, x1, y1], radius=radius, fill=bg)
    draw.text((x0 + (pw - tw) // 2 - b[0], y + (ph - th) // 2 - b[1]),
              text, font=font, fill=fg)
    return x0, y, x1, y1


def badge_width(text: str, font) -> int:
    b = ImageFont.FreeTypeFont.getbbox(font, text)
    return 20 + 28 + 12 + (b[2] - b[0]) + 20


def draw_badge(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font,
               circle_d: int = 28) -> tuple[int, int]:
    b = draw.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    pad_x, pad_y = 20, 12
    pw = pad_x + circle_d + 12 + tw + pad_x
    ph = max(circle_d, th) + pad_y * 2
    draw.rounded_rectangle([x, y, x + pw, y + ph], radius=ph // 2, fill="#FFFFFF")
    cx, cy, r = x + pad_x + circle_d // 2, y + ph // 2, circle_d // 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#059669")
    draw.line([(cx - 6, cy + 1), (cx - 1, cy + 6), (cx + 7, cy - 5)],
              fill="#FFFFFF", width=3)
    draw.text((x + pad_x + circle_d + 12 - b[0], y + (ph - th) // 2 - b[1]),
              text, font=font, fill="#111827")
    return pw, ph


def build_overlay(cfg: dict, sku: dict, fonts: FontBook) -> dict:
    W, H = cfg["canvas"]["width"], cfg["canvas"]["height"]
    typo = cfg["typography"]
    mode = sku.get("safe_zone_mode", "reels_universal")
    sz = dict(cfg["safe_zones"][mode])
    # move the whole bottom stack down by a fraction of its own height; this
    # keeps the price/caption/footer spacing and a deliberate margin below it
    shift_pct = float((cfg.get("layout") or {}).get("bottom_stack_shift_pct", 0.0))
    block_h = sz["footer_y"] + typo["footer_size"] - sz["price_y"]
    shift = int(round(block_h * shift_pct))
    if shift:
        sz["price_y"] += shift
        sz["caption_y"] += shift
        sz["footer_y"] += shift
    zone_w = sz["x_max"] - sz["x_min"]
    # the visual axis is the canvas centre; the safe zone only bounds width
    cx = W // 2
    warnings: list[str] = []
    # geometry actually drawn, so alternative renderers (HyperFrames) can
    # reproduce the locked layout from one source of truth
    layout: dict = {
        "canvas": {"width": W, "height": H},
        "zone": dict(sz),
        "cx": cx,
        "badges": [],
        "typography": dict(typo),
    }

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    f_sub = fonts.at("Inter-Bold.ttf", typo["subtitle_size"])
    f_badge = fonts.at("Inter-Bold.ttf", typo["badge_size"])
    f_footer = fonts.at("Inter-Bold.ttf", typo["footer_size"])

    # ---- title / subtitle (auto-fit inside the safe zone)
    title = sku.get("title", "")
    subtitle = sku.get("subtitle", "")
    f_title, size = fonts.fit(title, "Inter-Black.ttf",
                              typo["title_size"], zone_w, typo["min_size"])
    if size < typo["title_size"]:
        warnings.append(f"title shrunk to {size}px to fit {zone_w}px")
    tw, _, tb = fonts.measure(title, f_title)
    tx = cx - tw // 2 - tb[0]
    layout["title"] = {"text": title, "x": int(tx), "y": int(sz["title_y"]),
                       "size": int(size), "width": int(tw)}
    draw.text((tx + 2, sz["title_y"] + 2), title, font=f_title,
              fill=(0, 0, 0, 200))
    draw.text((tx, sz["title_y"]), title, font=f_title, fill="#FFFFFF")

    f_sub, size = fonts.fit(subtitle, "Inter-Bold.ttf",
                            typo["subtitle_size"], zone_w, typo["min_size"])
    if size < typo["subtitle_size"]:
        warnings.append(f"subtitle shrunk to {size}px")
    sw, _, sb = fonts.measure(subtitle, f_sub)
    layout["subtitle"] = {"text": subtitle, "x": int(cx - sw // 2 - sb[0]),
                          "y": int(sz["sub_y"]), "size": int(size),
                          "width": int(sw)}
    draw.text((cx - sw // 2 - sb[0] + 2, sz["sub_y"] + 2), subtitle,
              font=f_sub, fill=(0, 0, 0, 190))
    draw.text((cx - sw // 2 - sb[0], sz["sub_y"]), subtitle,
              font=f_sub, fill="#6EE7B7")

    # ---- badges: rows centred on the canvas axis, wrapping inside the zone
    badges = sku.get("badges", [])[:3]
    widths = [badge_width(b, f_badge) for b in badges]
    rows: list[list[tuple[str, int]]] = []
    cur: list[tuple[str, int]] = []
    for badge, bw in zip(badges, widths):
        if cur and sum(w for _, w in cur) + 14 + bw > zone_w:
            rows.append(cur)
            cur = []
        cur.append((badge, bw))
    if cur:
        rows.append(cur)

    by = sz["badge_y"]
    row_h = 0
    for row in rows:
        total = sum(w for _, w in row) + 14 * (len(row) - 1)
        bx = cx - total // 2
        for badge, bw in row:
            _, bh = draw_badge(draw, bx, by, badge, f_badge)
            layout["badges"].append({"text": badge, "x": int(bx), "y": int(by),
                                     "w": int(bw), "h": int(bh)})
            row_h = max(row_h, bh)
            bx += bw + 14
        by += row_h + 10
    if by > sz["price_y"] - 12:
        warnings.append(f"badge block reaches y={by}, "
                        f"too close to price at y={sz['price_y']}")

    # ---- price pill
    f_price, size = fonts.fit(sku["price_label"], "Inter-Black.ttf",
                              typo["price_size"], zone_w - 76, typo["min_size"])
    if size < typo["price_size"]:
        warnings.append(f"price shrunk to {size}px")
    pbox = draw_ink_pill(draw, cx, sz["price_y"], sku["price_label"], f_price,
                         cfg["colors"]["emerald_primary"], "#FFFFFF",
                         pad_x=38, pad_y=16, radius=24)
    layout["price"] = {"text": sku["price_label"], "x0": int(pbox[0]),
                       "y0": int(pbox[1]), "x1": int(pbox[2]),
                       "y1": int(pbox[3]), "size": int(size), "radius": 24,
                       "pad_x": 38, "pad_y": 16}

    # ---- footer
    footer = sku.get("footer", "@fenixsoft • Mercado Social Local C-29")
    f_footer, size = fonts.fit(footer, "Inter-Bold.ttf", typo["footer_size"],
                               zone_w, typo["min_size"])
    fw, _, fb = fonts.measure(footer, f_footer)
    layout["footer"] = {"text": footer, "x": int(cx - fw // 2 - fb[0]),
                        "y": int(sz["footer_y"]), "size": int(size),
                        "width": int(fw)}
    draw.text((cx - fw // 2 - fb[0] + 2, sz["footer_y"] + 2), footer,
              font=f_footer, fill=(0, 0, 0, 210))
    draw.text((cx - fw // 2 - fb[0], sz["footer_y"]), footer,
              font=f_footer, fill="#FFFFFF")

    # ---- layout assertions (bottom stack must not collide)
    caption_gap = sz["caption_y"] - pbox[3]
    if caption_gap < 10:
        warnings.append(f"price pill bottom y={pbox[3]} overlaps captions "
                        f"at y={sz['caption_y']}")
    if pbox[0] < sz["x_min"] or pbox[2] > sz["x_max"]:
        warnings.append("price pill exceeds safe-zone width")

    rgba = np.array(img)
    return {
        "rgb": rgba[:, :, :3].astype(np.uint8),
        "alpha": rgba[:, :, 3].astype(np.uint8),
        "warnings": warnings,
        "mode": mode,
        "zone": sz,
        "cx": cx,
        "layout": layout,
    }


# ----------------------------------------------------------------- captions
# A caption must never *end* on these: "EN MARACAY TE" reads like a cut-off
# sentence. Breaking after a content word keeps every card self-contained.
_NEVER_END = {
    "A", "AL", "ALGO", "ANTE", "BAJO", "CON", "COMO", "CUANDO", "DE", "DEL",
    "DESDE", "DONDE", "E", "EL", "EN", "ENTRE", "ES", "ESA", "ESAS", "ESE",
    "ESO", "ESOS", "ESTA", "ESTAS", "ESTE", "ESTOS", "HASTA", "LA", "LAS",
    "LE", "LES", "LO", "LOS", "MAS", "ME", "MI", "MIS", "MUY", "MÁS", "NI",
    "NO", "NOS", "O", "PARA", "PERO", "POR", "QUE", "SE", "SIN", "SOBRE",
    "SON", "SU", "SUS", "TAN", "TE", "TODO", "TODA", "TODOS", "TODAS", "TU",
    "TUS", "UN", "UNA", "UNOS", "UNAS", "Y", "YA", "×",
}
# ...and prefer not to *start* a card on these (conjunctions read better when
# they close the previous phrase, but the penalty is soft because it is often
# unavoidable inside a 3-word budget).
_NEVER_START = {"COMO", "CUANDO", "E", "NI", "O", "PERO", "QUE", "SI", "Y", "×"}
# "3 / MESES", "128 / GIGAS" and "77 / %" must never be split across cards.
_NUMBER_UNITS = {"GIGAS", "GB", "MES", "MESES", "AÑO", "AÑOS", "DÍA", "DÍAS",
                 "DÓLARES", "DÓLAR", "USDT", "CIENTO", "%"}
# ...and these nouns keep the number that follows them ("EL DÍA / 1").
_NUMBER_HOSTS = {"DÍA", "DÍAS", "MES", "MESES", "AÑO", "AÑOS", "SEMANA",
                 "SEMANAS", "HORA", "HORAS", "GRADO", "GRADOS"}


def _fold(token: str) -> str:
    """Uppercase with diacritics removed so 'QUÉ' still matches 'QUE'.

    The ASR writes accents ("QUÉ", "MÁS"); hand-written stopword sets never
    list every accented variant, which used to let cards end on "POR QUÉ".
    """
    return "".join(c for c in unicodedata.normalize("NFD", token.upper())
                   if not unicodedata.combining(c))


def _is_stop(token: str) -> bool:
    f = _fold(token)
    return f in _NEVER_END or f in _NEVER_START


def group_words(words: list[dict], max_words: int,
                max_chars: int, min_display: float = 0.45) -> list[dict]:
    """Dynamic-programming caption chunker.

    Greedy chunking + one stopword pass still leaves dangling function words
    ("ESTÉTICOS EN", "VERIFICA CON") because moving one word can overflow the
    next chunk. A tiny DP over <=3-word chunks scores every segmentation at
    once and picks the one with the fewest sentence-fragment breaks.
    """
    n = len(words)
    if not n:
        return []

    # prefix length of "word " tokens so a chunk's rendered width is O(1)
    plen = [0] * (n + 1)
    for i, w in enumerate(words):
        plen[i + 1] = plen[i] + len(w["w"]) + 1   # +1 for the joining space

    inf = float("inf")
    dp = [inf] * (n + 1)
    back = [0] * (n + 1)
    dp[n] = 0.0
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, min(n, i + max_words) + 1):
            chars = plen[j] - plen[i] - 1
            if chars > max_chars and j > i + 1:
                break
            k = j - i
            cost = 0.0
            cost += 5.0 if k == 1 else (0.4 if k == 2 else 0.0)
            if j < n and _fold(words[j - 1]["w"]) in _NEVER_END:
                cost += 60.0
            if _fold(words[i]["w"]) in _NEVER_START:
                cost += 6.0
            # Very short last words ("EL DÍA 1 TE") look like a typo; nudge
            # the break to the next content word when possible.
            if j < n and k > 1 and len(words[j - 1]["w"]) <= 2:
                cost += 1.5
            # Keep numbers with their unit ("3 MESES", "128 GIGAS").
            if (j < n and words[j - 1]["w"].isdigit()
                    and words[j]["w"] in _NUMBER_UNITS):
                cost += 50.0
            if (i > 0 and words[i]["w"] in _NUMBER_UNITS
                    and words[i - 1]["w"].isdigit()):
                cost += 50.0
                        # "IPHONE / 14 POR 330" — a number belongs with the noun before it
            # unless that noun is a stopword ("SON 128 GIGAS" stays split).
            if (j < n and words[j]["w"].isdigit()
                    and words[j - 1]["w"].isalpha()
                    and not _is_stop(words[j - 1]["w"])):
                cost += 50.0
            # Comma clauses are weaker than sentence ends: prefer cutting
            # there, but do not force it ("AL 77% PERO ESTÁ" is survivable).
            for _s in range(i, j - 1):
                if words[_s].get("cs"):
                    cost += 30.0
            if j < n and words[j - 1].get("cs"):
                cost -= 2.0
            # "GRADO / B", "VERSIÓN / B": a lone letter is a grade or a version
            # marker, never a phrase of its own. Breaking before it strands the
            # next card with "B TIENE DETALLES". Conjunctions ("Y", "E", "O")
            # are single letters too but already handled above — without this
            # exclusion the rule refuses to break before "Y BATERÍA AL 77%"
            # and forces "ESTÉTICOS EN LA" instead.
            _nw = words[j]["w"] if j < n else ""
            if (len(_nw) == 1 and _nw.isalpha() and not _is_stop(_nw)):
                cost += 80.0
            if (k == 1 and len(words[i]["w"]) == 1 and words[i]["w"].isalpha()
                    and not _is_stop(words[i]["w"])):
                cost += 120.0
            # "EL DÍA / 1 TE LLEVAS" — keep the count with its noun.
            if (j < n and words[j]["w"].isdigit()
                    and words[j - 1]["w"] in _NUMBER_HOSTS):
                cost += 50.0
            # Sentence ends (from Whisper's own segmentation, carried on the
            # last word as `sn`) are the strongest signal: a card that spans
            # one reads as two sentences jammed together ("ES GRADO B TIENE").
            for _s in range(i, j - 1):
                if words[_s].get("sn"):
                    cost += 100.0
            if j < n and words[j - 1].get("sn"):
                cost -= 12.0
            total = cost + dp[j]
            if total < dp[i]:
                dp[i], back[i] = total, j

    chunks: list[dict] = []
    i = 0
    while i < n:
        j = back[i] if back[i] > i else min(n, i + max_words)
        seg = words[i:j]
        chunks.append({"words": seg, "s": seg[0]["s"], "e": seg[-1]["e"],
                       "text": " ".join(w["w"] for w in seg)})
        i = j
    return merge_short_chunks(chunks, max_words, max_chars, min_display)


def merge_short_chunks(chunks: list[dict], max_words: int, max_chars: int,
                       min_display: float = 0.45) -> list[dict]:
    """Merge a card that would flash past into its neighbour.

    The ASR occasionally places a two-word card 0.24 s before the next one
    ("TE LLEVAS" / "UN MES DE GARANTÍA" at 37.7 CPS). The DP chunker cannot see
    display time, so this pass enforces it: any card on screen for less than
    `min_display` is merged forward (else backward) while the result stays
    within the character budget and at most two words over the word budget.
    The extra words are the price of legibility; 26 chars still fits the pill.
    """
    out = [dict(c) for c in chunks]
    i = 0
    for _guard in range(500):
        if i >= len(out):
            break
        span = _display_span(out, i)
        if span >= min_display:
            i += 1
            continue
        merged = False
        if i + 1 < len(out):
            cand = _merge(out[i], out[i + 1])
            if _fits(cand, max_words + 2, max_chars):
                out[i] = cand
                del out[i + 1]
                merged = True
        if not merged and i > 0:
            cand = _merge(out[i - 1], out[i])
            if _fits(cand, max_words + 2, max_chars):
                out[i - 1] = cand
                del out[i]
                i = max(0, i - 1)
                merged = True
        if not merged:
            i += 1
    return out


def _merge(a: dict, b: dict) -> dict:
    merged = a["words"] + b["words"]
    return {"words": merged, "s": a["s"], "e": b["e"],
            "text": " ".join(w["w"] for w in merged)}


def _fits(chunk: dict, max_words: int, max_chars: int) -> bool:
    return len(chunk["words"]) <= max_words and len(chunk["text"]) <= max_chars


def _display_span(chunks: list[dict], i: int, hold_s: float = 0.45) -> float:
    c = chunks[i]
    end = chunks[i + 1]["s"] if i + 1 < len(chunks) else c["e"] + hold_s
    return end - c["s"]


def build_caption_audit(chunks: list[dict], cfg: dict,
                        cap: "CaptionRenderer", hold_s: float) -> dict:
    """Every caption card as TEXT: CPS, display span, rendered width.

    Measured over the span the card is actually on screen, which is what
    `chunk_at` computes: min(next chunk start, last word end + hold). Reading
    the frames to find a too-fast card costs a vision call and is unreliable;
    this is exact and free.
    """
    warn = float((cfg.get("quality") or {}).get("caption_warn_cps", 20.0))
    cards: list[dict] = []
    scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    for i, c in enumerate(chunks):
        end = c["e"]
        if i + 1 < len(chunks):
            end = min(chunks[i + 1]["s"], c["e"] + hold_s)
        span = max(0.01, end - c["s"])
        font, size = cap.fonts.fit(c["text"], "Inter-Black.ttf",
                                   cap.cap["font_size"], cap.max_text_w,
                                   cap.typo["min_size"])
        b = scratch.textbbox((0, 0), c["text"], font=font)
        cards.append({
            "index": i,
            "text": c["text"],
            "words": len(c["words"]),
            "t0": round(c["s"], 3),
            "t1": round(end, 3),
            "display_sec": round(span, 3),
            "chars": len(c["text"]),
            "cps": round(len(c["text"]) / span, 1),
            "font_px": size,
            "width_px": int(b[2] - b[0]),
        })
    cps = [c["cps"] for c in cards] or [0.0]
    return {
        "cards": cards,
        "mean_cps": round(float(sum(cps) / len(cps)), 2),
        "max_cps": round(float(max(cps)), 2),
        "cards_over_20cps": int(sum(1 for v in cps if v > 20.0)),
        "cards_over_warn": int(sum(1 for v in cps if v > warn)),
        "warn_cps": warn,
        "definition": "CPS = characters incl. spaces / min(next card start, "
                      "last word end + hold_last_gap_ms)",
    }


def chunk_at(chunks: list[dict], t: float, hold_s: float) -> dict | None:
    for i, c in enumerate(chunks):
        end = c["e"]
        if i + 1 < len(chunks):
            end = min(chunks[i + 1]["s"], c["e"] + hold_s)
        if c["s"] <= t <= end:
            return c
    return None


def active_word_index(chunk: dict, t: float) -> int:
    idx = 0
    for i, w in enumerate(chunk["words"]):
        if w["s"] <= t:
            idx = i
        else:
            break
    return idx


class CaptionRenderer:
    """Renders only the pill patch (not the full 1080x1920 canvas) and caches it."""

    def __init__(self, cfg: dict, overlay: dict, fonts: FontBook):
        self.cfg = cfg
        self.sz = overlay["zone"]
        self.cx = overlay["cx"]
        self.fonts = fonts
        self.typo = cfg["typography"]
        self.cap = cfg["kinetic_captions"]
        self.colors = cfg["colors"]
        self.max_text_w = (self.sz["x_max"] - self.sz["x_min"]) - 60
        self.cache: dict[tuple, tuple] = {}
        self.warnings: list[str] = []

    def patch(self, text: str, words: list[dict], active_idx: int,
              font_size: int, y_offset: int
              ) -> tuple[np.ndarray, np.ndarray, int, int]:
        """Render one caption card with the spoken word highlighted.

        Returns a BGR patch (the canvas is OpenCV BGR). The old version passed
        a PIL RGB array straight into blend_region(), which swapped red and
        blue: the dark pill actually shipped as brown (37,22,13).
        """
        key = (text, active_idx, font_size, y_offset)
        if key in self.cache:
            return self.cache[key]
        font = self.fonts.at("Inter-Black.ttf", font_size)
        scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        b = scratch.textbbox((0, 0), text, font=font)
        tw, th = b[2] - b[0], b[3] - b[1]
        pad_x, pad_y, radius = 30, 14, 20
        pw, ph = tw + pad_x * 2, th + pad_y * 2
        img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, pw - 1, ph - 1], radius=radius,
                            fill=self.colors["pill_dark"])
        base_y = pad_y - b[1]
        for i, w in enumerate(words):
            prefix = (" ".join(x["w"] for x in words[:i]) + " ") if i else ""
            x = pad_x - b[0] + scratch.textlength(prefix, font=font)
            wb = scratch.textbbox((0, 0), w["w"], font=font)
            fill = (self.colors["emerald_flash"] if i == active_idx
                    else self.colors["text_white"])
            d.text((x - wb[0], base_y), w["w"], font=font, fill=fill)
        arr = np.array(img)
        bgr = arr[:, :, 2::-1].copy()            # RGB -> BGR for the canvas
        x0 = int(self.cx - pw // 2)
        y0 = int(self.sz["caption_y"] + y_offset)
        out = (bgr, arr[:, :, 3].astype(np.uint8), x0, y0)
        self.cache[key] = out
        return out

    def layout_cards(self, chunks: list[dict], hold: float = 0.6) -> list[dict]:
        """Every card's exact box, font size and word times for an HTML layer.

        Same fit/measure calls as `draw()`, so the HyperFrames overlay and the
        native renderer cannot drift apart.
        """
        scratch = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        out: list[dict] = []
        for i, chunk in enumerate(chunks):
            end = chunk["e"]
            if i + 1 < len(chunks):
                end = min(chunks[i + 1]["s"], chunk["e"] + hold)
            font, size = self.fonts.fit(chunk["text"], "Inter-Black.ttf",
                                        self.cap["font_size"], self.max_text_w,
                                        self.typo["min_size"])
            b = scratch.textbbox((0, 0), chunk["text"], font=font)
            tw, th = b[2] - b[0], b[3] - b[1]
            pw, ph = tw + 30 * 2, th + 14 * 2
            space = float(scratch.textlength("A A", font=font)
                          - scratch.textlength("AA", font=font))
            out.append({
                "text": chunk["text"],
                "s": round(float(chunk["s"]), 3),
                "e": round(float(end), 3),
                "size": int(size),
                "x": int(self.cx - pw // 2),
                "y": int(self.sz["caption_y"]),
                "w": int(pw),
                "h": int(ph),
                "gap": round(space, 2),
                "words": [{"w": w["w"], "s": w["s"], "e": w["e"]}
                          for w in chunk["words"]],
            })
        return out

    def draw(self, canvas: np.ndarray, chunk: dict, t: float) -> None:
        idx = active_word_index(chunk, t)

        enter = ease_out_cubic((t - chunk["s"]) / (self.cap["phrase_enter_ms"] / 1000.0))
        y_off = int((1.0 - enter) * 12)

        # keep the pill inside the safe zone at the configured size; shrink if needed
        font, size = self.fonts.fit(chunk["text"], "Inter-Black.ttf",
                                    self.cap["font_size"], self.max_text_w,
                                    self.typo["min_size"])
        if size < self.cap["font_size"] and len(self.warnings) < 3:
            self.warnings.append(f"caption shrunk to {size}px: {chunk['text']}")

        bgr, alpha, x0, y0 = self.patch(chunk["text"], chunk["words"], idx,
                                        size, y_off)
        # The card slides in at full opacity. Fading it in over 100 ms leaves
        # the first ~5 frames at 60 fps semi-transparent, which reads as a
        # washed-out caption on scrubbed frames and in the contact sheet.
        blend_region(canvas, bgr, alpha, x0, y0)


# ------------------------------------------------------------------ blend
class OverlayCompositor:
    """Composites the static overlay fast.

    Rows whose alpha is identical across the width use one SIMD-optimised
    cv2.addWeighted call; only rows that contain glyphs need per-pixel blending.
    The per-row source products are precomputed once: a static overlay must not
    re-cast the same arrays on all 1,700 frames.
    """

    def __init__(self, overlay: dict):
        self.rgb = overlay["rgb"][:, :, ::-1]          # BGR
        self.alpha = overlay["alpha"]
        const = np.all(self.alpha == self.alpha[:, :1], axis=1)
        self.runs: list[tuple[int, int, tuple]] = []
        start = 0
        for y in range(1, len(const) + 1):
            if y == len(const) or const[y] != const[start]:
                if const[start]:
                    # constant alpha across the row: one weighted band, or skip
                    payload = ("const", float(self.alpha[start, 0]) / 255.0)
                else:
                    a = self.alpha[start:y, :, None].astype(np.uint16)
                    payload = ("blend",
                               (255 - a),
                               self.rgb[start:y].astype(np.uint16) * a)
                self.runs.append((start, y, payload))
                start = y

    def apply(self, canvas: np.ndarray) -> None:
        for y0, y1, payload in self.runs:
            if y1 <= y0:
                continue
            if payload[0] == "const":
                a = payload[1]
                if a <= 0.0:
                    continue
                band = cv2.addWeighted(canvas[y0:y1], 1.0 - a,
                                       self.rgb[y0:y1], a, 0.0)
                canvas[y0:y1] = band
            else:
                _, inv, src_a = payload
                roi = canvas[y0:y1].astype(np.uint16)
                out = (roi * inv + src_a + 127) >> 8
                canvas[y0:y1] = out.astype(np.uint8)


def blend_region(canvas: np.ndarray, rgb: np.ndarray, alpha: np.ndarray,
                 x0: int, y0: int) -> None:
    H, W = rgb.shape[:2]
    ch, cw = canvas.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    ex, ey = min(W, cw - x0), min(H, ch - y0)
    if ex <= sx0 or ey <= sy0:
        return
    roi = canvas[y0 + sy0:y0 + ey, x0 + sx0:x0 + ex].astype(np.uint16)
    a = alpha[sy0:ey, sx0:ex, None].astype(np.uint16)
    src = rgb[sy0:ey, sx0:ex].astype(np.uint16)
    out = (roi * (255 - a) + src * a + 127) >> 8
    canvas[y0 + sy0:y0 + ey, x0 + sx0:x0 + ex] = out.astype(np.uint8)


# ------------------------------------------------------------------- cuts
def score_clip_positions(path: Path, fps: int, slot: float,
                         samples: int = 8) -> list[tuple[float, float]]:
    """Score candidate in-points by detail + sharpness so cuts avoid dead frames.

    A uniform white box face scores low (low std, few edges); a phone body with
    reflections and camera rings scores high.
    """
    reader = ClipReader(path, fps)
    dur = reader.duration
    usable = max(0.0, dur - slot - 0.25)
    out: list[tuple[float, float]] = []
    for i in range(samples):
        t = 0.1 + usable * i / max(1, samples - 1)
        reader.seek(t)
        frame = reader.read()
        h, w = frame.shape[:2]
        crop = frame[int(h * 0.18):int(h * 0.82), int(w * 0.10):int(w * 0.90)]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        detail = float(gray.std())
        mean = float(gray.mean())
        penalty = 1.0 if 35.0 <= mean <= 215.0 else 0.35
        out.append((round(t, 3), round((detail + min(sharp / 8.0, 60.0)) * penalty, 2)))
    reader.release()
    return out


def plan_cuts(clips: list[Path], durations: list[float], total: float,
              cfg: dict, seed: str,
              scores: dict[str, list[tuple[float, float]]],
              grid: dict | None = None) -> list[dict]:
    n = int(cfg.get("cuts_per_video", 16))
    beat = 60.0 / float(cfg.get("bpm", 118))
    snap = bool(cfg.get("cut_snap_to_beat", True))
    min_cut = max(0.3, float(cfg.get("min_cut_sec", 1.2)))
    # never plan more cuts than the timeline can hold
    n = max(1, min(n, int(total / min_cut)))

    beats = [b for b in (grid or {}).get("beats", []) if 0.05 < b < total - 0.05]
    bounds = [0.0]
    for k in range(1, n):
        t = total * k / n
        if snap:
            if beats:
                # snap to the MEASURED grid, not the nominal BPM constant
                t = min(beats, key=lambda b: abs(b - t))
            else:
                t = round(t / beat) * beat
        t = min(max(t, bounds[-1] + 0.2), total - 0.2)
        bounds.append(t)
    bounds.append(total)

    rng = random.Random(seed)
    order = list(range(len(clips)))
    rng.shuffle(order)
    use_count = {i: 0 for i in range(len(clips))}
    used_points: dict[int, set[float]] = {i: set() for i in range(len(clips))}
    segs: list[dict] = []
    prev_clip = -1
    for i in range(n):
        t0, t1 = bounds[i], bounds[i + 1]
        slot = max(0.2, t1 - t0)
        candidates = [c for c in order if c != prev_clip] or order
        clip_idx = min(candidates, key=lambda c: (use_count[c], order.index(c)))
        cand = [(t, s) for t, s in scores.get(clips[clip_idx].name, [])
                if t + slot <= durations[clip_idx] - 0.02]
        fresh = [c for c in cand if c[0] not in used_points[clip_idx]]
        cand = fresh or cand or [(0.0, 0.0)]
        cand.sort(key=lambda x: -x[1])
        top = cand[:3]
        in_point = top[rng.randrange(len(top))][0]
        used_points[clip_idx].add(in_point)
        segs.append({
            "index": i, "t0": round(t0, 3), "t1": round(t1, 3),
            "clip": clips[clip_idx].name, "clip_path": clips[clip_idx],
            "in": round(in_point, 3), "dur": round(slot, 3),
            "score": next((s for t, s in cand if t == in_point), None),
        })
        use_count[clip_idx] += 1
        prev_clip = clip_idx
    return segs


# ------------------------------------------------------------------- audio
def static_gain_to_lufs(src: Path, dst: Path, target_lufs: float) -> float:
    meas = probe_loudness(src)
    gain = target_lufs - float(meas["input_i"])
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-af", f"volume={gain:.2f}dB", "-ar", "48000", "-ac", "2", str(dst)])
    return gain


def sfx_artifacts(cfg: dict, sku_dir: Path, total: float,
                  warn: bool = True) -> dict | None:
    """Fresh cue-sheet artifacts from `fenix sfx`, or None for the legacy path.

    The cue sheet is only trusted when its `edit_plan_sha256` matches the plan
    the renderer is about to execute. A stale sheet is a warning, not a silent
    substitution: the renderer falls back to the one-whoosh-per-cut bed.
    """
    sfx_cfg = cfg.get("sfx") or {}
    if str(sfx_cfg.get("mode") or "cue_sheet") != "cue_sheet":
        return None
    cue_path = artifact(sku_dir, sfx_cfg.get("cue_sheet_file",
                                             "sfx_cue_sheet.json"))
    bus_path = artifact(sku_dir, sfx_cfg.get("bus_file", "sfx_bus.wav"))
    bed_path = artifact(sku_dir, sfx_cfg.get("music_bed_file", "music_bed.wav"))
    plan_path = sku_dir / "edit_plan.json"
    if not (cue_path.exists() and bus_path.exists() and plan_path.exists()):
        return None
    try:
        cue = json.loads(cue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    if cue.get("source", {}).get("edit_plan_sha256") != digest:
        if warn:
            print("[render] WARNING: sfx_cue_sheet.json is stale for this "
                  "edit_plan.json; using the legacy whoosh bed. Re-run "
                  "`fenix sfx <sku>`.")
        return None
    if abs(float(cue.get("duration_sec") or 0.0) - total) > 0.1:
        if warn:
            print("[render] WARNING: sfx_cue_sheet.json duration does not "
                  "match the render; using the legacy whoosh bed.")
        return None
    return {
        "cue": cue,
        "cue_path": cue_path,
        "bus": bus_path,
        "bed": bed_path if bed_path.exists() else None,
    }


def copy_sfx_evidence(cfg: dict, sku_dir: Path, run_dir: Path,
                      total: float) -> None:
    """Keep the cue sheet + report with the run only when they were consumed."""
    if not sfx_artifacts(cfg, sku_dir, total, warn=False):
        return
    sfx_cfg = cfg.get("sfx") or {}
    names = [sfx_cfg.get("cue_sheet_file") or "sfx_cue_sheet.json",
             "sfx_report.json"]
    for name in names:
        src = artifact(sku_dir, name)
        if src.exists():
            shutil.copy2(src, run_dir / name)


def build_sfx_bed(cfg: dict, cuts: list[dict], total: float, out: Path) -> None:
    """Place one air-swish per cut so its transient lands exactly on the cut.

    A whoosh is a rising sweep: its loudest point sits ~0.6-0.7 s after the
    file starts. Starting it at the cut would make the punch feel late, so we
    detect the peak and pre-roll the sample by that amount.
    """
    sfx_path = repo_path(cfg.get("sfx", {}).get("file") or "")
    if not sfx_path.exists():
        write_silence(out, total)
        return
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(sfx_path),
                          "-t", "1.4", "-f", "f32le", "-ac", "1", "-ar", "48000",
                          "-"], capture_output=True, check=True).stdout
    one = np.frombuffer(raw, dtype=np.float32).copy()
    if not len(one):
        write_silence(out, total)
        return
    gain = 10 ** (float(cfg["audio"].get("sfx_gain_db", -13.0)) / 20.0)
    one *= gain
    fade = int(float(cfg["audio"].get("sfx_fade_sec", 0.3)) * 48000)
    if fade and len(one) > 2 * fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        one[:max(1, fade // 20)] *= ramp[:max(1, fade // 20)]
        one[-fade:] *= ramp[::-1]

    peak_time = 0.0
    if cfg.get("sfx", {}).get("align_peak_to_cut", True):
        peak_time = float(np.argmax(np.abs(one[:int(1.2 * 48000)]))) / 48000

    pre_roll = int(max(0.0, peak_time - 0.02) * 48000)
    bed = np.zeros(int(total * 48000) + len(one) + pre_roll, dtype=np.float32)
    for cut in cuts:
        at = max(0, int(cut["t0"] * 48000) - pre_roll)
        bed[at:at + len(one)] += one
    bed = np.clip(bed, -1.0, 1.0)
    write_wav_f32(out, bed[:int(total * 48000)], 48000)


def write_silence(out: Path, total: float) -> None:
    write_wav_f32(out, np.zeros(int(total * 48000), dtype=np.float32), 48000)


def write_wav_f32(path: Path, samples: np.ndarray, sr: int) -> None:
    run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr),
         "-ac", "1", "-i", "-", "-c:a", "pcm_s16le", str(path)],
        input=samples.astype(np.float32).tobytes())


def build_audio(cfg: dict, sku_dir: Path, total: float, cuts: list[dict],
                tmp: Path) -> tuple[Path, dict]:
    audio = cfg["audio"]
    vo = sku_dir / "voice_28s.wav"
    if not vo.exists():
        sys.exit(f"[ERROR] Missing {vo}; run 'fenix align {sku_dir.name}' first")

    music_cfg = cfg.get("music", {})
    music_path = repo_path(music_cfg.get("file") or "")
    if not music_path.exists():
        pool = sorted(MUSIC_DIR.glob("*"))
        pool = [p for p in pool if p.suffix.lower() in (".wav", ".mp3", ".m4a")]
        music_path = pool[0] if pool else None

    notes: dict = {"music": None, "sfx": None}
    assets = sfx_artifacts(cfg, sku_dir, total)

    # music bed: prefer the cue-sheet stage's already-ducked/vacuumed bed.
    if assets and assets.get("bed"):
        bed = tmp / "_music_bed_lvl.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(assets["bed"]),
             "-t", f"{total:.3f}", "-vn", "-ar", "48000", "-ac", "2",
             str(bed)])
        policy = assets["cue"].get("policy") or {}
        notes["music"] = {"file": assets["bed"].name, "processed": True,
                          "bed_lufs": policy.get("music_bed_lufs"),
                          "duck_db": policy.get("duck_db"),
                          "vacuum_ms": policy.get("vacuum_ms")}
    elif music_path and music_path.exists():
        bed = tmp / "_music_bed.wav"
        run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i",
             str(music_path), "-t", f"{total:.3f}", "-vn",
             "-ar", "48000", "-ac", "2", str(bed)])
        gain = static_gain_to_lufs(bed, tmp / "_music_bed_lvl.wav",
                                   float(audio.get("music_bed_lufs", -28.0)))
        fade_in = float(audio.get("music_fade_in_sec", 0.25))
        fade_out = float(audio.get("music_fade_out_sec", 0.7))
        faded = tmp / "_music_bed_faded.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp / "_music_bed_lvl.wav"),
             "-af", f"afade=t=in:st=0:d={fade_in:.2f},"
                    f"afade=t=out:st={max(0.0, total - fade_out):.2f}:d={fade_out:.2f}",
             "-ar", "48000", "-ac", "2", str(faded)])
        faded.replace(tmp / "_music_bed_lvl.wav")
        notes["music"] = {"file": music_path.name,
                          "gain_db": round(gain, 2),
                          "bed_lufs": audio.get("music_bed_lufs", -28.0)}
    else:
        write_silence(tmp / "_music_bed_lvl.wav", total)

    sfx_bed = tmp / "_sfx_bed.wav"
    if assets:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(assets["bus"]),
             "-t", f"{total:.3f}", "-ar", "48000", "-ac", "2",
             "-c:a", "pcm_s16le", str(sfx_bed)])
        summary = assets["cue"].get("summary") or {}
        notes["sfx"] = {"mode": "cue_sheet",
                        "file": assets["bus"].name,
                        "cue_sheet": str(assets["cue_path"]),
                        "events": summary.get("foreground_cues"),
                        "by_layer": summary.get("by_layer")}
    else:
        build_sfx_bed(cfg, cuts, total, sfx_bed)
        notes["sfx"] = {"mode": "legacy",
                        "file": cfg.get("sfx", {}).get("file"),
                        "gain_db": audio.get("sfx_gain_db", -13.0),
                        "events": len(cuts)}

    duck = (f"sidechaincompress=threshold={audio.get('duck_threshold', 0.03)}:"
            f"ratio={audio.get('duck_ratio', 6)}:"
            f"attack={audio.get('duck_attack_ms', 8)}:"
            f"release={audio.get('duck_release_ms', 350)}")

    raw_mix = tmp / "_mix_raw.wav"
    graph = (
        "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "asplit=2[vo][key];"
        "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[mus];"
        f"[mus][key]{duck}[duck];"
        "[2:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[sfx];"
        "[duck][vo][sfx]amix=inputs=3:duration=longest:normalize=0,"
        f"atrim=0:{total:.3f},asetpts=N/SR/TB[out]"
    )
    run(["ffmpeg", "-y", "-v", "error",
         "-i", str(tmp / "_music_bed_lvl.wav"),
         "-i", str(vo),
         "-i", str(sfx_bed),
         "-filter_complex", graph, "-map", "[out]",
         "-ar", "48000", "-ac", "2", str(raw_mix)])

    meas = probe_loudness(raw_mix)
    final = tmp / "_mix.wav"
    ln = (f"loudnorm=I={audio.get('target_lufs', -14)}:"
          f"TP={audio.get('true_peak_db', -1.5)}:LRA={audio.get('lra', 11)}:"
          f"measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
          f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
          f"offset={meas['target_offset']}:linear=true")
    ceiling = float(audio.get("limiter_ceiling", 0.82))
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw_mix),
         "-af", f"{ln},alimiter=limit={ceiling:.3f}:level=0,aresample=48000",
         "-ar", "48000", "-ac", "2", str(final)])

    notes["mix_input_lufs"] = float(meas["input_i"])
    notes["mix_input_tp"] = float(meas["input_tp"])
    notes["target_lufs"] = audio.get("target_lufs", -14)
    return final, notes


# ------------------------------------------------------------------- render
def load_cut_override(sku_dir: Path, clips: list[Path],
                      durations: list[float], total: float,
                      cfg: dict | None = None,
                      allow_soft_in: bool = False) -> tuple[list[dict] | None,
                                                            list[str]]:
    """Read an optional vision-locked cut list from `input/<sku>/edit_plan.json`.

    Shape (only `cuts` is required):

        {"cuts": [
            {"clip": "01_back_cameras.mp4", "in": 2.40, "dur": 1.52},
            {"clip": "02_screen_faceid.mp4", "in": 3.35, "dur": 2.03}
        ]}

    This is the hook for the agent's vision pass: once it has picked the
    sharpest angles, the renderer must not re-plan or re-order them. Cuts are
    laid out back-to-back from 0 s; if the list is shorter than the voiceover
    the last shot is extended so the video still ends with the audio.
    """
    cfg = cfg or {}
    path = sku_dir / "edit_plan.json"
    if not path.exists():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, []
    raw = data.get("cuts")
    if not raw:
        return None, []
    by_name = {c.name: (c, d) for c, d in zip(clips, durations)}
    default_dur = total / max(1, len(raw))
    from ..domain import candidates as cand_mod
    cand = cand_mod.load(sku_dir)
    lengths: list[float] = []
    for i, item in enumerate(raw):
        if item.get("dur") is not None:
            lengths.append(float(item["dur"]))
        elif item.get("t1") is not None:
            lengths.append(float(item["t1"]) - float(item.get("t0", 0.0)))
        elif item.get("candidate"):
            # a candidate ID fixes the in-point, not the shot length; the
            # renderer's slot is still the voiceover-proportional default
            lengths.append(default_dur)
        else:
            lengths.append(default_dur)
        if lengths[-1] <= 0:
            raise SystemExit(f"[ERROR] edit_plan.json cut {i}: duration must be > 0")
    if sum(lengths) > total:
        raise SystemExit(
            f"[ERROR] edit_plan.json cuts total {sum(lengths):.2f}s > "
            f"voiceover {total:.2f}s")

    cuts: list[dict] = []
    notes: list[str] = []
    cursor = 0.0
    for i, (item, length) in enumerate(zip(raw, lengths)):
        name = str(item.get("clip") or "")
        cid = item.get("candidate")
        cand_in = None
        if cid:
            win = cand_mod.find(cand, str(cid))
            if win is None:
                raise SystemExit(
                    f"[ERROR] edit_plan.json cut {i}: candidate {cid!r} is not in "
                    f"{sku_dir / 'candidates.json'}")
            cand_in = float(win["t"])
            if not name:
                name = win["clip"]
        if name not in by_name:
            raise SystemExit(f"[ERROR] edit_plan.json cut {i}: unknown clip {name!r}")
        clip_path, clip_dur = by_name[name]
        slot = length
        if i == len(raw) - 1 and cursor + slot < total - 0.01:
            slot = total - cursor          # extend the final shot
        if slot > clip_dur + 0.02:
            raise SystemExit(
                f"[ERROR] edit_plan.json cut {i}: {slot:.2f}s is longer than "
                f"{name} ({clip_dur:.2f}s)")
        in_pt = (cand_in if item.get("in") is None and cand_in is not None
                 else max(0.0, float(item.get("in", cand_in or 0.0))))
        if in_pt + slot > clip_dur:
            notes.append(
                f"cut {i} ({name}) in={in_pt:.2f}+{slot:.2f}s exceeds the "
                f"{clip_dur:.2f}s source; clamped to "
                f"{max(0.0, clip_dur - slot):.2f}s")
            in_pt = max(0.0, clip_dur - slot)
        cuts.append({"index": i, "t0": round(cursor, 3),
                     "t1": round(cursor + slot, 3), "clip": name,
                     "clip_path": clip_path, "in": round(in_pt, 3),
                     "dur": round(slot, 3), "score": None,
                     "candidate": cid, "line": item.get("line")})
        cursor += slot
    if not cuts or cuts[0]["t0"] > 0.01:
        raise SystemExit("[ERROR] edit_plan.json produced no usable cuts")
    return cuts, notes


def enforce_cut_quality(cuts: list[dict], cfg: dict, sku_dir: Path,
                        grid: dict | None, allow_soft_in: bool) -> list[str]:
    """Score every in-point and cut offset; refuse soft in-points by default.

    Applied to both the vision-locked list and the auto-planned one, so the
    floor is a property of the renderer rather than of the agent's diligence.
    """
    q = quality_cfg(cfg)
    notes: list[str] = []
    medians: dict[str, float] = {}
    soft: list[dict] = []
    soft_windows: list[dict] = []
    for c in cuts:
        if c["clip"] not in medians:
            medians[c["clip"]] = cached_scan(c["clip_path"])["median"] or 0.0
        med = medians[c["clip"]]
        score = frame_sharpness(c["clip_path"], c["in"])
        c["sharpness"] = round(score, 2)
        c["sharpness_ratio"] = round(score / med, 3) if med else 0.0
        profile = cached_scan(c["clip_path"])
        c["window"] = window_stats(profile, c["in"], c["dur"],
                                   float(q["in_point_floor"]))
        # a shot can open sharp and stay soft: the whole window has to hold up
        if c["window"]["n"] >= 3 and (
                c["window"]["mean_ratio"] < float(q["in_point_floor"])
                or c["window"]["soft_frac"] > 0.30):
            soft_windows.append(c)
        elif c["sharpness_ratio"] < float(q["in_point_floor"]):
            prof = cached_scan(c["clip_path"])
            fitting = [(t, s) for t, s in prof["samples"]
                       if t + c["dur"] <= prof["duration_sec"] - 0.02]
            c["suggested_in"] = (round(max(fitting, key=lambda x: x[1])[0], 1)
                                 if fitting else 0.0)
            soft.append(c)

    if grid and grid.get("beats"):
        for c in cuts:
            _, off = nearest_beat(c["t0"], grid["beats"])
            c["beat_offset_ms"] = off
        worst = max(abs(c["beat_offset_ms"]) for c in cuts)
        if worst > float(q["beat_offset_warn_ms"]):
            notes.append(
                f"cuts are up to {worst:.0f} ms off the measured beat grid "
                f"({grid['bpm']:.2f} BPM); snap to output/{sku_dir.name}/"
                f"beat_grid.json for a tighter edit")

    if soft_windows and not allow_soft_in:
        detail = "; ".join(
            f"cut {c['index']} ({c['clip']} @ {c['in']:.2f}s) averages "
            f"{c['window']['mean_ratio']:.2f}x over {c['dur']:.2f}s "
            f"({c['window']['soft_frac']:.0%} soft)" for c in soft_windows[:6])
        raise SystemExit(
            f"[ERROR] sustained-quality floor: {len(soft_windows)} cut(s) are "
            f"soft over most of the shot, not just at the in-point.\n"
            f"        {detail}\n"
            f"        Fix input/{sku_dir.name}/edit_plan.json, or pass "
            f"--allow-soft-in to ship anyway.")
    if soft and not allow_soft_in:
        detail = "; ".join(
            f"cut {c['index']} ({c['clip']} @ {c['in']:.2f}s) is "
            f"{c['sharpness_ratio']:.2f}x of clip median, try in="
            f"{c.get('suggested_in')}" for c in soft[:6])
        raise SystemExit(
            f"[ERROR] in-point quality floor: {len(soft)} cut(s) below "
            f"{q['in_point_floor']:.2f}x of their clip median sharpness.\n"
            f"        {detail}\n"
            f"        Fix input/{sku_dir.name}/edit_plan.json, or pass "
            f"--allow-soft-in to ship anyway.")
    return notes


def render(cfg: dict, sku_dir: Path, out_mp4: Path,
           allow_soft_in: bool = False, hf: bool = False,
           hf_workers: int = 4, draft: bool = False) -> dict:
    t_start = time.time()
    stage_start = t_start
    stage_times: dict[str, float] = {}

    def mark(name: str) -> None:
        nonlocal stage_start
        now = time.time()
        stage_times[name] = round(now - stage_start, 1)
        stage_start = now

    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    timings_path = sku_dir / "word_timings.json"
    if not timings_path.exists():
        sys.exit(f"[ERROR] Missing {timings_path}; run `fenix align <sku>` first")
    timings = json.loads(timings_path.read_text(encoding="utf-8"))
    words = timings.get("words", [])

    clips = sorted((sku_dir / "clips").glob("*.mp4"))
    if not clips:
        sys.exit(f"[ERROR] No .mp4 clips found in {sku_dir / 'clips'}")

    W, H, FPS = cfg["canvas"]["width"], cfg["canvas"]["height"], cfg["canvas"]["fps"]
    target = float(cfg["target_duration_sec"])
    # Never truncate speech: the finished video follows the voiceover duration.
    total = float(timings.get("duration_sec") or target)
    total = max(2.0, total)

    print(f"[render] normalising {len(clips)} clip(s) to {W}x{H}@{FPS} ...")
    grade = cfg.get("grade") if cfg.get("grade", {}).get("mode", "baked") == "baked" else None
    norm = [normalize_clip(c, W, H, FPS, grade=grade) for c in clips]
    durations = [probe(p)["duration"] for p in norm]
    mark("normalize")

    grid = beat_grid_for(cfg, sku_dir)
    soft_ok = allow_soft_in or bool(sku.get("allow_soft_in"))
    cuts, notes = load_cut_override(sku_dir, clips, durations, total, cfg=cfg,
                                    allow_soft_in=soft_ok)
    if cuts:
        print(f"[render] using vision-locked input/edit_plan.json "
              f"({len(cuts)} cuts)")
    else:
        print("[render] scoring candidate in-points ...")
        slot_est = total / max(1, int(cfg.get("cuts_per_video", 16)))
        scores = {c.name: score_clip_positions(n, FPS, slot_est)
                  for c, n in zip(clips, norm)}
        cuts = plan_cuts(clips, durations, total, cfg, seed=sku_dir.name,
                         scores=scores, grid=grid)
        notes = []
    notes += enforce_cut_quality(cuts, cfg, sku_dir, grid, soft_ok)
    mark("plan")
    fonts = FontBook()
    overlay = build_overlay(cfg, sku, fonts)
    compositor = OverlayCompositor(overlay)
    cap = CaptionRenderer(cfg, overlay, fonts)
    kc = cfg["kinetic_captions"]
    chunks = group_words(words, int(kc["max_words_per_chunk"]),
                         int(kc["max_chars_per_chunk"]),
                         float(kc.get("min_display_ms", 450)) / 1000.0)
    hold = float(kc.get("hold_last_gap_ms", 600)) / 1000.0

    warnings = list(overlay["warnings"]) + list(cap.warnings) + notes
    max_extra = float((cfg.get("voiceover") or {}).get("max_extra_sec", 2.0))
    if total > target + max_extra:
        warnings.append(f"voiceover is {total:.2f}s, over the {target:.1f}s target "
                        f"+{max_extra:.1f}s allowance (shorten the script)")
    elif total > target + 0.15:
        print(f"[render] {total:.2f}s: over the {target:.1f}s target but inside "
              f"the +{max_extra:.1f}s allowance (last phrase kept intact)")
    if not words:
        warnings.append("no word timings: captions disabled")
    caption_audit = build_caption_audit(chunks, cfg, cap, hold)
    qcfg = quality_cfg(cfg)
    if caption_audit["max_cps"] > qcfg["caption_fail_cps"] \
            and not sku.get("allow_fast_captions"):
        worst = max(caption_audit["cards"], key=lambda c: c["cps"])
        warnings.append(
            f"caption #{worst['index']} '{worst['text']}' shows for "
            f"{worst['display_sec']:.2f}s ({worst['cps']:.1f} CPS, "
            f"fail {qcfg['caption_fail_cps']:.0f}); merge it or cut a word")

    tmp = Path(tempfile.mkdtemp(prefix="fenixsoft_"))
    try:
        print("[render] building audio mix (music + ducked VO + SFX) ...")
        mix, audio_notes = build_audio(cfg, sku_dir, total, cuts, tmp)
        mark("audio")

        # Optional HyperFrames text layer: a transparent WebM rendered by a
        # deterministic frame server, composited in the same encode pass.
        hf_notes: dict = {}
        hf_path: Path | None = None
        if hf:
            from . import hf_overlay
            cards = cap.layout_cards(chunks, hold)
            info = hf_overlay.ensure_overlay(cfg, overlay["layout"], cards,
                                             total, workers=hf_workers)
            hf_path = Path(info["path"]) if info.get("path") else None
            hf_notes = {k: v for k, v in info.items() if k != "path"}

        enc = cfg["encoding"]
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
            "-i", str(mix),
        ]
        if hf_path is not None:
            # Two overlay transports:
            #   .ffconcat -> the state renderer's PNG list (concat demuxer)
            #   .webm     -> VP9 with sidecar alpha (libvpx-vp9 must precede -i,
            #                the native decoder ignores the alpha plane)
            if str(hf_path).endswith(".ffconcat"):
                # State PNGs carry straight RGBA. Blend in RGB and let the
                # default RGB->YUV conversion run, exactly like the native
                # path: measured pill rgb(3,149,105) vs native rgb(3,148,104)
                # against #059669. An explicit BT.709 conversion shifted it to
                # rgb(14,167,104) on this pipeline.
                cmd += ["-f", "concat", "-safe", "0", "-i", str(hf_path)]
                overlay_filter = (
                    "[0:v]format=rgb24[b];[2:v]format=rgba[a];"
                    "[b][a]overlay=0:0:format=rgb,format=yuv420p[v]")
                color_args: list[str] = []
            else:
                # Legacy VP9 transport: libvpx-vp9 must precede -i or the
                # sidecar alpha is dropped; VP9 is BT.709-tagged.
                cmd += ["-c:v", "libvpx-vp9", "-i", str(hf_path)]
                overlay_filter = (
                    "[0:v]format=rgb24[b];[2:v]format=rgba[a];"
                    "[b][a]overlay=0:0:eof_action=pass:format=rgb,"
                    "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[v]")
                color_args = ["-colorspace", "bt709",
                              "-color_primaries", "bt709", "-color_trc", "bt709"]
            cmd += ["-filter_complex", overlay_filter,
                    "-map", "[v]", "-map", "1:a", *color_args]
        cmd += [
            "-c:v", enc["codec"],
            "-preset", "veryfast" if draft else enc.get("preset", "medium"),
            "-profile:v", enc["profile"], "-level", str(enc["level"]),
            "-pix_fmt", enc["pix_fmt"],
            "-crf", "23" if draft else str(enc["crf"]),
            "-maxrate", str(enc["maxrate"]), "-bufsize", str(enc["bufsize"]),
            "-c:a", enc.get("audio_codec", "aac"),
            "-b:a", str(enc.get("audio_bitrate", "128k")),
            "-ar", str(enc.get("audio_sample_rate", 48000)),
            "-movflags", "+faststart" if enc.get("faststart", True) else "frag_keyframe",
            "-shortest", str(out_mp4),
        ]
        log = tempfile.TemporaryFile()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
        assert proc.stdin is not None

        # static overlay composited with the band-optimised path
        norm_by_name = {c.name: n for c, n in zip(clips, norm)}
        readers: dict[int, ClipReader] = {}
        cut_idx = 0
        frames_total = int(round(total * FPS))
        punch = cfg.get("punch", {})
        grade_cfg = cfg.get("grade", {})
        per_frame_grade = grade_cfg.get("mode", "baked") == "per_frame"

        try:
            for f_idx in range(frames_total):
                t_now = f_idx / FPS
                while cut_idx + 1 < len(cuts) and t_now >= cuts[cut_idx + 1]["t0"]:
                    cut_idx += 1
                cut = cuts[cut_idx]

                ri = cut_idx % len(cuts)
                reader = readers.get(ri)
                if reader is None:
                    for old in readers.values():
                        old.release()
                    readers.clear()
                    # Honour the clip chosen by plan_cuts()/edit_plan.json.
                    # Using cut_idx % len(norm) silently swapped clips while
                    # keeping another clip's scored in-point.
                    reader = ClipReader(
                        norm_by_name.get(cut["clip"], norm[cut_idx % len(norm)]), FPS)
                    reader.seek(cut["in"])
                    readers[ri] = reader

                frame = reader.read()
                if frame.shape[1] != W or frame.shape[0] != H:
                    frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)

                # 0.16 s punch-in: 1.12x -> 1.0x, anchored on the cut
                if punch.get("enabled", True):
                    p = ease_out_cubic((t_now - cut["t0"]) / float(punch["duration_sec"]))
                    z = 1.0 + (float(punch["scale"]) - 1.0) * (1.0 - p)
                    if z > 1.001:
                        zw, zh = int(W / z), int(H / z)
                        zx, zy = (W - zw) // 2, (H - zh) // 2
                        frame = cv2.resize(frame[zy:zy + zh, zx:zx + zw], (W, H),
                                           interpolation=cv2.INTER_LINEAR)

                if per_frame_grade:
                    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                    l_ch, a_ch, b_ch = cv2.split(lab)
                    l_ch = cv2.convertScaleAbs(
                        l_ch, alpha=float(grade_cfg.get("lightness_alpha", 1.06)),
                        beta=float(grade_cfg.get("lightness_beta", 3)))
                    frame = cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]),
                                         cv2.COLOR_LAB2BGR)

                if not hf_path:
                    compositor.apply(frame)
                    active = chunk_at(chunks, t_now, hold)
                    if active:
                        cap.draw(frame, active, t_now)

                proc.stdin.write(memoryview(frame))

                if f_idx and f_idx % (FPS * 5) == 0:
                    print(f"[render] {t_now:5.1f}s / {total:.1f}s")
        finally:
            for reader in readers.values():
                reader.release()
            proc.stdin.close()
            proc.wait()
            log.seek(0)
            err = log.read().decode("utf-8", "replace").strip()
            log.close()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg encode failed:\n{err}")
        mark("encode")

        size_mb = out_mp4.stat().st_size / (1024 * 1024)
        max_bytes = int(enc["max_whatsapp_bytes"])
        if out_mp4.stat().st_size > max_bytes:
            print(f"[render] {size_mb:.2f} MB over WhatsApp budget; re-encoding ...")
            tmp2 = tmp / "_fallback.mp4"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(out_mp4),
                 "-c:v", enc["codec"], "-profile:v", enc["profile"],
                 "-level", str(enc["level"]), "-pix_fmt", enc["pix_fmt"],
                 "-crf", str(enc["fallback_crf"]),
                 "-maxrate", str(enc["fallback_maxrate"]),
                 "-bufsize", str(enc["bufsize"]),
                 "-c:a", "copy", "-movflags", "+faststart", str(tmp2)])
            tmp2.replace(out_mp4)
            size_mb = out_mp4.stat().st_size / (1024 * 1024)

        # ---- QA frames + contact sheet
        qa_dir = out_mp4.parent / "qa_frames"
        if qa_dir.exists():
            shutil.rmtree(qa_dir)
        qa_dir.mkdir(parents=True, exist_ok=True)
        # AGENTS.md QA contract: 0.5 s, 10 s, 20 s and the final second, plus
        # the first six cut midpoints so every clip/angle gets eyeballed.
        probe_times = {0.5, round(max(0.0, total - 1.0), 2)}
        probe_times |= {t for t in (10.0, 20.0) if t < total - 0.05}
        cut_times = {round(min(total - 0.05, c["t0"] + c["dur"] / 2), 2)
                     for c in cuts[:6]}
        # The sheet must represent the whole edit, not just the first 10 s:
        # eight evenly spaced samples across the runtime.
        sheet_times = [round(total * (i + 0.5) / 8, 2) for i in range(8)]
        times = sorted(probe_times | cut_times | set(sheet_times))

        def _extract(ts: float) -> None:
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}",
                 "-i", str(out_mp4), "-frames:v", "1",
                 str(qa_dir / f"frame_{ts:05.2f}s.png")])

        if not draft:
            with ThreadPoolExecutor(max_workers=min(4, len(times))) as pool:
                list(pool.map(_extract, times))
        else:
            # Draft: only the eight sheet samples, no per-cut QA stills.
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(_extract, sheet_times))
        mark("qa_frames")
        sheet_frames = [qa_dir / f"frame_{ts:05.2f}s.png" for ts in sheet_times]
        if sheet_frames:
            inputs = []
            for f in sheet_frames:
                inputs += ["-i", str(f)]
            filt = "".join(f"[{i}:v]scale=270:480,setsar=1[v{i}];"
                           for i in range(len(sheet_frames)))
            filt += "".join(f"[v{i}]" for i in range(len(sheet_frames)))
            filt += f"xstack=inputs={len(sheet_frames)}:layout=" + \
                    "|".join(f"{(i % 4) * 270}_{(i // 4) * 480}"
                             for i in range(len(sheet_frames))) + "[out]"
            run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filt,
                 "-map", "[out]", "-frames:v", "1",
                 str(out_mp4.parent / "contact_sheet.png")])
        mark("contact_sheet")

        shutil.copy2(timings_path, out_mp4.parent / "word_timings.json")
        (out_mp4.parent / "caption_audit.json").write_text(
            json.dumps({"sku": sku_dir.name, **caption_audit},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        ratios = [c.get("sharpness_ratio", 0.0) for c in cuts] or [0.0]
        shots = [c["window"] for c in cuts if c.get("window")]
        shot_means = [w["mean_ratio"] for w in shots] or [0.0]
        offsets = [abs(c["beat_offset_ms"]) for c in cuts
                   if c.get("beat_offset_ms") is not None]
        plan = {
            "sku": sku_dir.name,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "output": str(out_mp4),
            "size_mb": round(size_mb, 3),
            "draft": draft,
            "config_slices": render_slices(cfg),
            "timings": {**stage_times, "total": round(time.time() - t_start, 1)},
            "duration_sec": round(total, 3),
            "canvas": f"{W}x{H}@{FPS}",
            "bpm": cfg.get("bpm"),
            "beat": ({"bpm": grid["bpm"], "beat_sec": grid["beat_sec"],
                      "offset": grid["offset"], "source": grid.get("source"),
                      "max_cut_offset_ms": round(max(offsets), 1) if offsets else None,
                      "mean_cut_offset_ms": (round(sum(offsets) / len(offsets), 1)
                                             if offsets else None)}
                     if grid else None),
            "quality": {
                "in_point_floor": qcfg["in_point_floor"],
                "in_point_min_ratio": round(min(ratios), 3),
                "in_point_mean_ratio": round(sum(ratios) / len(ratios), 3),
                "window_min_ratio": round(min(shot_means), 3),
                "window_mean_ratio": round(sum(shot_means) / len(shot_means), 3),
                "window_soft_frac": round(
                    sum(w["soft_frac"] for w in shots) / len(shots), 3)
                if shots else 0.0,
                "caption_mean_cps": caption_audit["mean_cps"],
                "caption_max_cps": caption_audit["max_cps"],
                "hyperframes": 1 if hf_path else 0,
            },
            "cuts": [{k: v for k, v in c.items() if k != "clip_path"} for c in cuts],
            "audio": audio_notes,
            "alignment": {
                "method": timings.get("method"),
                "words": len(words),
                "tempo": timings.get("tempo"),
                "script_similarity": timings.get("script_similarity"),
            },
            "safe_zone": overlay["mode"],
            "overlay_engine": "hyperframes" if hf_path else "native",
            "hyperframes": hf_notes,
            "price_box": [overlay["layout"]["price"]["x0"],
                          overlay["layout"]["price"]["y0"],
                          overlay["layout"]["price"]["x1"],
                          overlay["layout"]["price"]["y1"]],
            "warnings": warnings,
            "elapsed_sec": round(time.time() - t_start, 1),
        }
        plan["run"] = out_mp4.parent.name
        (out_mp4.parent / "edit_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        copy_sfx_evidence(cfg, sku_dir, out_mp4.parent, total)

        # Versioned run index: never overwrite the previous render.
        runs.register_run(sku_dir.name, out_mp4.parent, out_mp4, {
            "duration_sec": round(total, 3),
            "size_mb": round(size_mb, 3),
            "cuts": len(cuts),
            "in_point_min_ratio": plan["quality"]["in_point_min_ratio"],
            "beat_max_offset_ms": plan["beat"]["max_cut_offset_ms"] if plan["beat"] else None,
            "caption_max_cps": caption_audit["max_cps"],
            "hyperframes": 1 if hf_path else 0,
            "warnings": len(warnings),
        })

        print(f"[render] done: {out_mp4} ({size_mb:.2f} MB, {total:.2f}s, "
              f"{time.time() - t_start:.1f}s wall)")
        if stage_times:
            print("[render] stages: " + ", ".join(
                f"{name} {seconds:.1f}s" for name, seconds in stage_times.items()))
        for w in warnings:
            print(f"[render] WARNING: {w}")
        return plan
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remix(cfg: dict, sku_dir: Path, out_mp4: Path,
          source_mp4: Path | None = None) -> None:
    """Rebuild only the audio mix into a NEW run directory (-c:v copy).

    Useful while tuning music/SFX: seconds instead of a full re-render. The
    previous run is copied, never modified, so history stays intact.
    """
    src = source_mp4 or out_mp4
    plan_path = src.parent / "edit_plan.json"
    if not src.exists() or not plan_path.exists():
        sys.exit("[ERROR] --remix needs an existing render and edit_plan.json")
    if src.resolve() != out_mp4.resolve():
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out_mp4)
        for extra in ("caption_audit.json", "contact_sheet.png",
                      "word_timings.json"):
            p = src.parent / extra
            if p.exists():
                shutil.copy2(p, out_mp4.parent / extra)
        qa = src.parent / "qa_frames"
        if qa.exists():
            shutil.copytree(qa, out_mp4.parent / "qa_frames", dirs_exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    total = float(plan["duration_sec"])
    cuts = plan["cuts"]
    tmp = Path(tempfile.mkdtemp(prefix="fenixsoft_remix_"))
    try:
        mix, notes = build_audio(cfg, sku_dir, total, cuts, tmp)
        tmp_out = tmp / "remix.mp4"
        enc = cfg["encoding"]
        run(["ffmpeg", "-y", "-v", "error", "-i", str(out_mp4), "-i", str(mix),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", enc.get("audio_codec", "aac"),
             "-b:a", str(enc.get("audio_bitrate", "128k")),
             "-ar", str(enc.get("audio_sample_rate", 48000)),
             "-movflags", "+faststart", "-shortest", str(tmp_out)])
        tmp_out.replace(out_mp4)
        plan["audio"] = notes
        plan["remixed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        plan["size_mb"] = round(out_mp4.stat().st_size / (1024 * 1024), 3)
        plan["run"] = out_mp4.parent.name
        plan["remixed_from"] = src.parent.name
        (out_mp4.parent / "edit_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        copy_sfx_evidence(cfg, sku_dir, out_mp4.parent, total)
        runs.register_run(sku_dir.name, out_mp4.parent, out_mp4, {
            "duration_sec": plan.get("duration_sec"),
            "size_mb": plan["size_mb"],
            "remix_of": src.parent.name,
        })
        print(f"[remix] audio rebuilt -> {out_mp4} "
              f"({plan['size_mb']:.2f} MB) {notes}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render a Fenixsoft SKU video")
    parser.add_argument("sku_dir", help="SKU name (or input/<sku> path)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=None,
                        help="explicit output MP4 (default: a new timestamped "
                             "run directory under output/<sku>/)")
    parser.add_argument("--remix", action="store_true",
                        help="rebuild only the audio mix into a new run")
    parser.add_argument("--from", dest="from_run", type=Path, default=None,
                        help="run directory to copy the video from (--remix)")
    parser.add_argument("--allow-soft-in", action="store_true",
                        help="ship in-points below the sharpness floor (recorded "
                             "as a QA warning)")
    parser.add_argument("--hf-overlay", action="store_true",
                        help="render the text/caption layer with HyperFrames "
                             "(transparent WebM) instead of the native compositor")
    parser.add_argument("--hf-workers", type=int, default=4,
                        help="Chrome workers for the HyperFrames overlay (4 is "
                             "safe in 8 GB WSL; auto pins to 1)")
    parser.add_argument("--draft", action="store_true",
                        help="veryfast/crf23 encode and only the eight contact-"
                             "sheet stills; for review, not delivery")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    sku_dir = resolve_sku_dir(args.sku_dir)

    if args.remix:
        if args.out:
            out = args.out
            source = args.from_run
            if source is None or source.is_dir():
                source = runs.run_video(args.from_run) if args.from_run else None
        else:
            source = (runs.run_video(args.from_run) if args.from_run
                      else (runs.latest_run(sku_dir.name) or {}).get("video"))
            if not source:
                sys.exit(f"[ERROR] --remix: no earlier run of {sku_dir.name}; "
                         f"render once first")
            source = Path(source)
            run_dir = runs.new_run_dir(sku_dir.name)
            out = run_dir / f"{run_dir.name}_final.mp4"
        remix(cfg, sku_dir, out, source_mp4=Path(source) if source else None)
        return

    run_dir = None
    if args.out:
        out = args.out
    else:
        run_dir = runs.new_run_dir(sku_dir.name)
        out = run_dir / f"{run_dir.name}_final.mp4"
        print(f"[render] new run: {run_dir}")
    try:
        render(cfg, sku_dir, out, allow_soft_in=args.allow_soft_in,
               hf=args.hf_overlay, hf_workers=args.hf_workers,
               draft=args.draft)
    except BaseException:
        # a refused render must not leave an empty "run" behind: the directory
        # was created by this process and nothing else writes into it
        if run_dir is not None and run_dir.exists() and \
                not runs.run_video(run_dir).exists():
            shutil.rmtree(run_dir, ignore_errors=True)
            print(f"[render] removed incomplete run {run_dir.name}")
        raise


if __name__ == "__main__":
    main()
