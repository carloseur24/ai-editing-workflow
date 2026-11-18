#!/usr/bin/env python3
"""
hf_overlay.py — HyperFrames text layer, state-cached (v3, motion upgrade).

The text layer only changes at discrete moments, so we render one PNG per unique
visual state and hold it for its real duration via an ffconcat list. This version
adds a real animation pass over the old static layer:

  * title      — masked left-to-right reveal + rise
  * subtitle   — delayed rise/fade
  * badges     — staggered spring-in
  * price      — spring pop + a single light sweep
  * footer     — late rise/fade
  * rule       — emerald accent line that draws in
  * captions   — card spring, per-word stagger, active word emerald + scale,
                 short exit; no fade-in wash

Geometry comes from render_video.build_overlay()/layout_cards(), so the locked
centred layout, the 20%-down bottom stack and the no-scrim rule stay identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from ..context import (ASSETS_DIR, CACHE_DIR, DEFAULT_CONFIG, FONTS_DIR,
                       resolve_sku_dir)

HF_VERSION = "0.9.55"
STATE_FPS = 30           # animation sampling rate for state derivation
TEMPLATE_VERSION = 5     # bump when the HTML/JS changes (cache key)
CACHE = CACHE_DIR / "hf_overlay"

INTRO_SEC = 1.65         # header/price/footer animation window
CARD_IN = 0.24
WORD_STAGGER = 0.035
WORD_IN = 0.20
CARD_OUT = 0.14


# --------------------------------------------------------------------- easing
def _clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return max(a, min(b, x))


def _ease(p: float) -> float:
    p = _clamp(p)
    return 1.0 - (1.0 - p) ** 3


def _spring(p: float) -> float:
    """Damped overshoot that settles at 1.0 (0 at p=0)."""
    p = max(0.0, p)
    if p >= 1.0:
        return 1.0
    return 1.0 - math.exp(-6.0 * p) * math.cos(8.5 * p)


def _active_word(card: dict, t: float) -> int:
    idx = 0
    for i, w in enumerate(card["words"]):
        if w["s"] <= t:
            idx = i
        else:
            break
    return idx


# --------------------------------------------------------------------- states
def _intro(t: float) -> list[float]:
    return [
        round(_ease((t - 0.08) / 0.55), 3),      # title
        round(_ease((t - 0.26) / 0.55), 3),      # subtitle
        round(_ease((t - 0.40) / 0.60), 3),      # badges
        round(_spring((t - 0.55) / 0.45), 3),    # price
        round(_ease((t - 0.85) / 0.55), 3),      # footer
        round(_ease((t - 0.18) / 0.55), 3),      # rule
        round(_clamp((t - 0.72) / 0.60), 3),     # shine
    ]


def _state_at(cards: list[dict], total: float, t: float) -> dict:
    card_idx = None
    for i, c in enumerate(cards):
        if c["s"] <= t < c["e"]:
            card_idx = i
            break
    st = {"intro": _intro(t), "card": card_idx, "in": 1.0, "out": 0.0,
          "words": [], "active": 0, "start": round(t, 4)}
    if card_idx is None:
        return st
    c = cards[card_idx]
    st["in"] = round(_spring((t - c["s"]) / CARD_IN), 3)
    st["out"] = round(_ease((t - (c["e"] - CARD_OUT)) / CARD_OUT), 3)
    st["words"] = [round(_spring((t - (c["s"] + i * WORD_STAGGER)) / WORD_IN), 3)
                   for i in range(len(c["words"]))]
    st["active"] = _active_word(c, t)
    return st


def _signature(st: dict) -> tuple:
    return (tuple(st["intro"]), st["card"], round(st["in"], 2),
            round(st["out"], 2), tuple(round(w, 2) for w in st["words"]),
            st["active"])


def _sample_times(cards: list[dict], total: float) -> list[float]:
    step = 1.0 / STATE_FPS
    times: set[float] = {0.0, total, INTRO_SEC}
    t = 0.0
    while t <= INTRO_SEC:
        times.add(round(min(t, total), 4))
        t += step
    for c in cards:
        s, e = float(c["s"]), float(c["e"])
        for k in range(9):
            times.add(round(min(e, s + k / STATE_FPS), 4))
        for w in c["words"]:
            times.add(round(float(w["s"]), 4))
            times.add(round(min(e, float(w["s"]) + 1 / STATE_FPS), 4))
        for k in range(6):
            times.add(round(max(s, e - k / STATE_FPS), 4))
        times.add(round(e, 4))
    return sorted(t for t in times if 0.0 <= t <= total)


def build_states(cards: list[dict], total: float,
                 _fps: int = STATE_FPS) -> list[dict]:
    """Unique visual states covering 0..total, merged where nothing changes."""
    states: list[dict] = []
    prev_sig = None
    for t in _sample_times(cards, total):
        st = _state_at(cards, total, t)
        sig = _signature(st)
        if sig == prev_sig and states:
            continue
        if states:
            states[-1]["end"] = round(t, 4)
        st["end"] = round(min(total, t + 1 / _fps), 4)
        states.append(st)
        prev_sig = sig
    if states:
        states[-1]["end"] = round(total, 4)
    return states


# ---------------------------------------------------------------------- html
def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def _has_header(layout: dict) -> bool:
    """True when the static header has anything to draw (title/badges/price/footer)."""
    for key in ("title", "subtitle", "footer"):
        if str((layout.get(key) or {}).get("text") or "").strip():
            return True
    if layout.get("badges"):
        return True
    return bool(int((layout.get("price") or {}).get("size") or 0))


def _html(layout: dict, cards: list[dict], states: list[dict]) -> str:
    W = layout["canvas"]["width"]
    H = layout["canvas"]["height"]
    title, subtitle = layout.get("title") or {}, layout.get("subtitle") or {}
    price, footer = layout.get("price") or {}, layout.get("footer") or {}
    typo = layout["typography"]
    cx = layout["cx"]
    rule_y = int(layout["zone"]["badge_y"] + 62)

    badges = "".join(f'''
      <div class="badge" style="left:{b['x']}px;top:{b['y']}px;width:{b['w']}px;height:{b['h']}px">
        <span class="check"><svg viewBox="0 0 24 24" width="16" height="16">
          <path d="M20 6L9 17l-5-5" fill="none" stroke="#fff" stroke-width="3.6"
                stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        <span class="badge-t">{_esc(b['text'])}</span>
      </div>''' for b in layout.get("badges") or [])

    # A captions-only overlay has no header: emitting empty text/price nodes
    # used to draw a stray rule and made the alpha check fail on header pixels.
    has_header = _has_header(layout)
    title_el = (f'''
  <div class="txt" id="title" style="left:{title['x']}px;top:{title['y']}px;
       font-size:{title['size']}px;color:#fff;
       text-shadow:2px 2px 0 rgba(0,0,0,.78)">{_esc(title['text'])}</div>'''
                if str(title.get("text") or "").strip() else "")
    subtitle_el = (f'''
  <div class="txt" id="subtitle" style="left:{subtitle['x']}px;top:{subtitle['y']}px;
       font-family:'HFInterBold';font-size:{subtitle['size']}px;color:#6EE7B7;
       text-shadow:2px 2px 0 rgba(0,0,0,.75)">{_esc(subtitle['text'])}</div>'''
                   if str(subtitle.get("text") or "").strip() else "")
    rule_el = '<div id="rule"></div>' if has_header else ""
    price_el = (f'''
  <div id="price" style="left:{price['x0']}px;top:{price['y0']}px;
       width:{price['x1'] - price['x0']}px;height:{price['y1'] - price['y0']}px;
       font-size:{price['size']}px">{_esc(price['text'])}
    <div id="shine"></div>
  </div>''' if int(price.get("size") or 0) else "")
    footer_el = (f'''
  <div class="txt" id="footer" style="left:{footer['x']}px;top:{footer['y']}px;
       font-family:'HFInterBold';font-size:{footer['size']}px;color:#fff;
       text-shadow:2px 2px 0 rgba(0,0,0,.82)">{_esc(footer['text'])}</div>'''
                 if str(footer.get("text") or "").strip() else "")

    # one rendered frame per state, not one frame per output second
    duration = len(states) / STATE_FPS if states else 1.0
    return f'''<!doctype html>
<html lang="es">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width={W}, height={H}" />
<style>
  @font-face {{ font-family:"HFInterBlack";
    src:url("./assets/Inter-Black.ttf") format("truetype"); font-weight:900; }}
  @font-face {{ font-family:"HFInterBold";
    src:url("./assets/Inter-Bold.ttf") format("truetype"); font-weight:700; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:{W}px; height:{H}px; overflow:hidden; background:transparent; }}
  #root {{ position:relative; width:{W}px; height:{H}px; background:transparent;
           font-family:"HFInterBlack", Inter, Arial, sans-serif; }}
  .txt {{ position:absolute; white-space:nowrap; line-height:1; opacity:0; }}
  .badge {{ position:absolute; display:flex; align-items:center; gap:12px;
            background:#fff; border-radius:999px; padding-left:20px; opacity:0; }}
  .check {{ width:28px; height:28px; border-radius:50%; background:#059669;
            display:flex; align-items:center; justify-content:center; flex:0 0 28px; }}
  .badge-t {{ font-family:"HFInterBold"; font-size:{typo["badge_size"]}px;
              color:#111827; white-space:nowrap; padding-right:20px; }}
  #price {{ position:absolute; display:flex; align-items:center; justify-content:center;
            background:#059669; color:#fff; border-radius:{price["radius"]}px;
            white-space:nowrap; opacity:0; overflow:hidden;
            transform-origin:50% 50%; }}
  #shine {{ position:absolute; inset:0;
            background:linear-gradient(105deg, transparent 32%,
                       rgba(255,255,255,.55) 50%, transparent 68%);
            transform:translateX(-140%); opacity:0; pointer-events:none; }}
  #rule {{ position:absolute; left:{cx - 70}px; top:{rule_y}px; width:140px; height:3px;
           background:#10B981; border-radius:2px; transform-origin:50% 50%;
           transform:scaleX(0); opacity:.9; }}
  .card {{ position:absolute; display:flex; align-items:center; justify-content:center;
           background:#111827; border-radius:20px; white-space:nowrap;
           font-family:"HFInterBlack"; color:#fff; visibility:hidden; opacity:0;
           padding:0 30px; width:max-content; flex:none;
           transform-origin:50% 50%; }}
  .w {{ flex:0 0 auto; display:inline-block; opacity:0; will-change:transform,opacity; }}
</style>
</head>
<body>
<div id="root" data-composition-id="hf_overlay" data-start="0"
     data-duration="{duration:.3f}" data-width="{W}" data-height="{H}" data-fps="{STATE_FPS}">
  {title_el}
  {subtitle_el}
  {badges}
  {rule_el}
  {price_el}
  {footer_el}
  <div id="captions"></div>
</div>
<script src="./assets/gsap.min.js"></script>
<script>
  const CARDS = {json.dumps(cards, ensure_ascii=False)};
  const STATES = {json.dumps(states, ensure_ascii=False)};
  const FPS = {STATE_FPS};
  const CX = {cx};
  const INTRO = {INTRO_SEC};
  const clamp01 = (v) => Math.max(0, Math.min(1, v));

  const host = document.getElementById("captions");
  const title = document.getElementById("title");
  const subtitle = document.getElementById("subtitle");
  const footer = document.getElementById("footer");
  const price = document.getElementById("price");
  const shine = document.getElementById("shine");
  const rule = document.getElementById("rule");
  const badgeEls = [...document.querySelectorAll(".badge")];
  const els = [];
  const tl = gsap.timeline({{ paused: true }});

  CARDS.forEach((c) => {{
    const el = document.createElement("div");
    el.className = "card";
    el.style.left = CX + "px";
    el.style.top = c.y + "px";
    el.style.height = c.h + "px";
    el.style.fontSize = c.size + "px";
    el.style.gap = (c.gap || 0) + "px";
    gsap.set(el, {{ xPercent: -50 }});
    c.words.forEach((w, i) => {{
      const s = document.createElement("span");
      s.className = "w";
      s.textContent = w.w;
      el.appendChild(s);
      if (i < c.words.length - 1) el.appendChild(document.createTextNode(" "));
    }});
    host.appendChild(el);
    els.push(el);
  }});

  let prevCard = -1;
  STATES.forEach((st, i) => {{
    const t = i / FPS;
    const [tp, sp, bp, pp, fp, rp, sh] = st.intro;

    if (t <= INTRO + 1 / FPS) {{
      if (title) tl.set(title, {{ opacity: clamp01(tp), y: (1 - tp) * 22,
                       clipPath: `inset(0 ${{(1 - tp) * 100}}% 0 0)` }}, t);
      if (subtitle) tl.set(subtitle, {{ opacity: clamp01(sp), y: (1 - sp) * 16 }}, t);
      badgeEls.forEach((el, j) => {{
        const p = clamp01(bp * 1.45 - j * 0.22);
        tl.set(el, {{ opacity: p, x: (1 - p) * 26,
                     scale: 0.96 + 0.04 * p }}, t);
      }});
      if (price) tl.set(price, {{ opacity: clamp01(pp), scale: 0.90 + 0.10 * pp }}, t);
      if (shine) tl.set(shine, {{ xPercent: -140 + 280 * sh,
                      opacity: (sh > 0.02 && sh < 0.98) ? 0.55 : 0 }}, t);
      if (footer) tl.set(footer, {{ opacity: clamp01(fp), y: (1 - fp) * 14 }}, t);
      if (rule) tl.set(rule, {{ scaleX: rp }}, t);
    }}

    if (st.card !== prevCard) {{
      if (prevCard >= 0) tl.set(els[prevCard], {{ visibility: "hidden" }}, t);
      if (st.card !== null) {{
        const el = els[st.card];
        tl.set(el, {{ visibility: "visible" }}, t);
        el.querySelectorAll(".w").forEach((s) => {{
          tl.set(s, {{ opacity: 0, y: 14, scale: 0.96, color: "#FFFFFF" }}, t);
        }});
      }}
      prevCard = st.card;
    }}
    if (st.card !== null) {{
      const el = els[st.card];
      const p = clamp01(st["in"]);
      tl.set(el, {{ opacity: p * (1 - st.out), y: (1 - p) * 16 - st.out * 10,
                   scale: 0.94 + 0.06 * p }}, t);
      const spans = el.querySelectorAll(".w");
      st.words.forEach((wp, j) => {{
        const q = clamp01(wp);
        tl.set(spans[j], {{ opacity: q, y: (1 - q) * 14,
                           scale: (j === st.active) ? 1.06 : (0.96 + 0.04 * q),
                           color: (j === st.active) ? "#10B981" : "#FFFFFF" }}, t);
      }});
    }}
  }});

  window.__timelines = window.__timelines || {{}};
  window.__timelines["hf_overlay"] = tl;
  tl.seek(0);
</script>
</body></html>
'''


# -------------------------------------------------------------------- plumbing
def _cache_key(layout: dict, cards: list[dict], states: list[dict]) -> str:
    gsap = ASSETS_DIR / "hf" / "gsap.min.js"
    payload = json.dumps({
        "layout": layout, "cards": cards, "states": states,
        "hf": HF_VERSION, "fps": STATE_FPS, "template": TEMPLATE_VERSION,
        "gsap": gsap.stat().st_mtime_ns if gsap.exists() else 0,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def verify_png_alpha(png: Path, header_expected: bool = True) -> dict:
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(png),
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    if len(raw) < 1080 * 1920 * 4:
        return {"ok": False, "reason": "unexpected PNG size"}
    alpha = np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 4)[:, :, 3]
    mid = float((alpha[700:1200, :] == 0).mean())
    header_px = int((alpha[90:340, :] > 0).sum())
    caption_px = int((alpha[1300:1700, :] > 0).sum())
    # A captions-only overlay has no title/badges/price by design: requiring
    # header ink there rejected a valid layer. When a header *is* declared,
    # the pixels must still prove it rendered.
    return {"ok": mid > 0.80 and caption_px > 500
            and (header_px > 500 or not header_expected),
            "header_expected": header_expected,
            "mid_transparent_ratio": round(mid, 3),
            "header_px": header_px, "caption_px": caption_px}


def _copy_assets(project: Path) -> None:
    assets = project / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("Inter-Black.ttf", "Inter-Bold.ttf"):
        src = FONTS_DIR / name
        if not src.exists():
            sys.exit(f"[hf] missing font {src}")
        shutil.copy2(src, assets / name)
    gsap = ASSETS_DIR / "hf" / "gsap.min.js"
    if not gsap.exists():
        sys.exit(f"[hf] missing {gsap}; run tools/setup_wsl.sh")
    shutil.copy2(gsap, assets / "gsap.min.js")


def _ffconcat(pngs: list[Path], starts: list[float], total: float) -> str:
    lines = ["ffconcat version 1.0"]
    for i, png in enumerate(pngs):
        end = starts[i + 1] if i + 1 < len(starts) else total
        lines.append(f"file '{png.as_posix()}'")
        lines.append(f"duration {max(0.001, end - starts[i]):.6f}")
    lines.append(f"file '{pngs[-1].as_posix()}'")
    return "\n".join(lines) + "\n"


def ensure_overlay(cfg: dict, layout: dict, cards: list[dict], duration: float,
                   workers: int = 4, force: bool = False) -> dict:
    if not cards:
        return {"path": None, "skipped": "no caption cards"}
    states = build_states(cards, duration)
    key = _cache_key(layout, cards, states)
    project = CACHE / key
    manifest = project / "manifest.json"
    concat = project / "overlay.ffconcat"

    if concat.exists() and manifest.exists() and not force:
        return {**json.loads(manifest.read_text(encoding="utf-8")), "cached": True}

    project.mkdir(parents=True, exist_ok=True)
    _copy_assets(project)
    (project / "index.html").write_text(_html(layout, cards, states),
                                        encoding="utf-8")
    frames = project / "frames"
    if frames.exists():
        shutil.rmtree(frames)

    cmd = [
        _npx(), "--yes", f"hyperframes@{HF_VERSION}", "render", str(project),
        "--fps", str(STATE_FPS), "--format", "png-sequence",
        "--quality", "looks", "--workers", str(max(1, workers)),
        "--no-low-memory-mode", "--output", str(frames), "--json",
    ]
    print(f"[hf] rendering {len(states)} animation states "
          f"({len(cards)} cards) with {workers} Chrome workers ...")
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=project)
    elapsed = time.time() - started
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-8:]
        raise RuntimeError("[hf] render failed: " + " | ".join(tail))

    pngs = sorted(frames.glob("frame_*.png"))
    # duration*fps can round one frame up (497/30 * 30 -> 498); the extra tail
    # frame is unused because the concat list is driven by the state timings.
    if len(pngs) < len(states):
        raise RuntimeError(f"[hf] expected {len(states)} state frames, "
                           f"got {len(pngs)}")
    pngs = pngs[:len(states)]
    alpha = verify_png_alpha(pngs[len(pngs) // 2], _has_header(layout))
    if not alpha["ok"]:
        raise RuntimeError(f"[hf] overlay lost alpha: {alpha}")

    concat.write_text(
        _ffconcat(pngs, [s["start"] for s in states], duration),
        encoding="utf-8")

    data = {
        "path": str(concat),
        "mode": "state-png",
        "duration_sec": round(duration, 3),
        "cards": len(cards),
        "states": len(states),
        "workers": workers,
        "render_sec": round(elapsed, 1),
        "hf_version": HF_VERSION,
        "template": TEMPLATE_VERSION,
        "animation_layers": ["title-mask", "subtitle-rise", "badge-stagger",
                             "price-spring", "price-shine", "footer-rise",
                             "header-rule", "card-spring", "word-stagger",
                             "active-word-scale"],
        "alpha": alpha,
        "cached": False,
    }
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[hf] {len(states)} states in {elapsed:.1f}s "
          f"({elapsed / max(1, len(states)):.2f}s/state) -> {concat}")
    return data


def _npx() -> str:
    exe = shutil.which("npx")
    if not exe:
        sys.exit("[hf] npx not found: install Node.js >= 22 to use --hf-overlay")
    return exe


# -------------------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render the HyperFrames text layer")
    parser.add_argument("sku_dir", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seconds", type=float, default=None,
                        help="only the first N seconds (smoke test)")
    args = parser.parse_args(argv)

    from . import render_video as rv

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    sku_dir = resolve_sku_dir(args.sku_dir)
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    timings = json.loads((sku_dir / "word_timings.json").read_text(encoding="utf-8"))
    fonts = rv.FontBook()
    overlay = rv.build_overlay(cfg, sku, fonts)
    kc = cfg["kinetic_captions"]
    chunks = rv.group_words(timings["words"], int(kc["max_words_per_chunk"]),
                            int(kc["max_chars_per_chunk"]),
                            float(kc.get("min_display_ms", 450)) / 1000.0)
    cap = rv.CaptionRenderer(cfg, overlay, fonts)
    cards = cap.layout_cards(chunks, float(kc.get("hold_last_gap_ms", 600)) / 1000.0)
    total = float(timings.get("duration_sec") or cfg["target_duration_sec"])
    if args.seconds:
        total = args.seconds
        cards = [c for c in cards if c["s"] < total]
        for c in cards:
            c["e"] = min(c["e"], total)
    data = ensure_overlay(cfg, overlay["layout"], cards, total,
                          workers=args.workers, force=args.force)
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    sys.exit(main())
