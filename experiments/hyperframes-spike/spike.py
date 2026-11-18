#!/usr/bin/env python3
"""HyperFrames overlay spike for the Fenixsoft pipeline.

Proves the three things the research could not settle from docs alone:

1. a 1080x1920@60 composition with a real transparent background renders as
   VP9/alpha WebM on this WSL2 box;
2. the alpha plane survives an FFmpeg composite onto H.264 footage when the
   decoder is declared before `-i` (the documented trap);
3. the existing caption/pill/footer contract can be expressed as DOM + a
   paused GSAP timeline, using the demo SKU's own caption cards.

Run from anywhere:  python spikes/hyperframes_overlay/spike.py
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SKU = "demo_iphone14_blue"
SKU_DIR = REPO / "input" / SKU
RUN = REPO / "output" / SKU / "demo_iphone14_blue_20261115-185938"
CLIP = SKU_DIR / "clips" / "01_back_cameras.mp4"
OVERLAY_SEC = 3.0
FPS = 60
WORKERS = 4

HYPERFRAMES = ["npx", "--yes", "hyperframes@0.9.54"]


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:], file=sys.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def load_cards() -> list[dict]:
    audit = json.loads((RUN / "caption_audit.json").read_text())
    cards = [c for c in audit["cards"] if c["t0"] < OVERLAY_SEC]
    for card in cards:
        card["t1"] = min(card["t1"], OVERLAY_SEC)
    return cards


def build_html(cards: list[dict], sku: dict) -> str:
    price = sku.get("price_label", "$330 USDT")
    footer = sku.get("footer", "@fenixsoft")
    badges = "".join(
        f'<div class="badge"><svg viewBox="0 0 24 24" width="34" height="34">'
        f'<path d="M20 6L9 17l-5-5" fill="none" stroke="#059669" stroke-width="3.4" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>{esc(b)}</div>'
        for b in sku.get("badges", [])
    )
    payload = [
        {"text": c["text"], "t0": round(c["t0"], 3), "t1": round(c["t1"], 3)}
        for c in cards
    ]
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=1080, height=1920" />
<script src="./assets/vendor/gsap.min.js"></script>
<style>
  @font-face {{
    font-family: "Inter Black";
    src: url("./assets/Inter-Black.ttf") format("truetype");
    font-weight: 900; font-style: normal;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html, body {{ width:1080px; height:1920px; background:transparent; overflow:hidden; }}
  #root {{ position:relative; width:1080px; height:1920px; background:transparent;
           font-family:"Inter Black", Inter, "DejaVu Sans", sans-serif; }}
  .clip {{ position:absolute; }}
  /* header zone y=110..318 (current locked spec) */
  #header {{ left:65px; top:110px; width:950px; display:flex; flex-direction:column; gap:12px; }}
  #title {{ font-size:56px; line-height:1; letter-spacing:-1px; color:#fff;
            text-shadow:0 2px 6px rgba(0,0,0,.75), 0 0 2px rgba(0,0,0,.55); }}
  #subtitle {{ font-size:34px; line-height:1; color:#cfe9e1;
               text-shadow:0 2px 6px rgba(0,0,0,.75); }}
  #badges {{ display:flex; gap:16px; }}
  .badge {{ display:flex; align-items:center; gap:8px; font-size:28px; color:#d1fae5;
            text-shadow:0 2px 6px rgba(0,0,0,.75); }}
  /* caption band y=1305..1455 */
  #captions {{ left:65px; top:1305px; width:885px; height:150px; }}
  .card {{ position:absolute; inset:0; font-size:46px; line-height:1.06; letter-spacing:-0.5px;
           text-transform:uppercase; color:#fff; opacity:0;
           text-shadow:0 2px 6px rgba(0,0,0,.8), 0 0 2px rgba(0,0,0,.6); }}
  .word {{ display:inline-block; will-change:transform, opacity; }}
  /* pill y=1470, footer y=1584 */
  #pill {{ left:65px; top:1470px; padding:16px 34px; border-radius:999px;
           background:#059669; color:#04231a; font-size:62px; line-height:1; white-space:nowrap; }}
  #foot {{ left:65px; top:1584px; font-size:30px; color:#e6fff5; opacity:.92;
           text-shadow:0 2px 5px rgba(0,0,0,.8); }}
</style>
</head>
<body>
<div id="root" data-composition-id="hfspike" data-start="0" data-duration="{OVERLAY_SEC}"
     data-width="1080" data-height="1920" data-fps="{FPS}">
  <div id="header" class="clip" data-start="0" data-duration="{OVERLAY_SEC}">
    <div id="title">{esc(sku.get('title', 'iPhone 14'))}</div>
    <div id="subtitle">{esc(sku.get('subtitle', ''))}</div>
    <div id="badges">{badges}</div>
  </div>
  <div id="captions" class="clip" data-start="0" data-duration="{OVERLAY_SEC}"></div>
  <div id="pill" class="clip" data-start="0" data-duration="{OVERLAY_SEC}">{esc(price)}</div>
  <div id="foot" class="clip" data-start="0" data-duration="{OVERLAY_SEC}">{esc(footer)}</div>
</div>
<script>
const CARDS = {json.dumps(payload, ensure_ascii=False)};
const tl = gsap.timeline({{ paused: true }});
const host = document.getElementById("captions");

CARDS.forEach((c, i) => {{
  const card = document.createElement("div");
  card.className = "card";
  card.id = "card" + i;
  const words = c.text.split(/\\s+/).filter(Boolean);
  const span = Math.max(0.2, c.t1 - c.t0);
  const step = Math.min(0.09, Math.max(0.03, (span - 0.32) / Math.max(1, words.length)));
  words.forEach((w) => {{
    const s = document.createElement("span");
    s.className = "word";
    s.textContent = w;
    card.appendChild(s);
    card.appendChild(document.createTextNode(" "));
  }});
  host.appendChild(card);
  tl.set(card, {{ opacity: 1 }}, c.t0);
  tl.fromTo(card.querySelectorAll(".word"),
    {{ y: 30, opacity: 0, rotateX: -35 }},
    {{ y: 0, opacity: 1, rotateX: 0, duration: 0.16, stagger: step, ease: "power3.out" }},
    c.t0 + 0.03);
  tl.to(card, {{ opacity: 0, duration: 0.10, ease: "power2.in" }}, Math.max(c.t0 + 0.12, c.t1 - 0.11));
  tl.set(card, {{ opacity: 0 }}, c.t1);
}});

tl.fromTo("#pill", {{ scale: 0.9, opacity: 0 }},
  {{ scale: 1, opacity: 1, duration: 0.34, ease: "back.out(2.4)" }}, 0.18);
tl.fromTo("#header", {{ y: -18, opacity: 0 }},
  {{ y: 0, opacity: 1, duration: 0.30, ease: "power2.out" }}, 0.30);
tl.fromTo("#foot", {{ opacity: 0 }}, {{ opacity: 0.92, duration: 0.4 }}, 0.6);

window.__timelines = window.__timelines || {{}};
window.__timelines["hfspike"] = tl;
tl.seek(0);
</script>
</body></html>
"""


def probe(path: Path) -> dict:
    out = run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=width,height,r_frame_rate,pix_fmt,nb_read_frames,duration,codec_name",
         "-of", "json", str(path)]
    ).stdout
    return json.loads(out)["streams"][0]


def verify_alpha(overlay: Path) -> dict:
    """VP9 stores alpha out-of-band: pix_fmt lies, ALPHA_MODE + decoded RGBA do not."""
    png = HERE / "stills" / "overlay_frame_rgba.png"
    png.parent.mkdir(exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1.10", "-c:v", "libvpx-vp9",
         "-i", str(overlay), "-frames:v", "1", "-pix_fmt", "rgba", str(png)])
    from PIL import Image

    img = Image.open(png).convert("RGBA")
    alpha = list(img.getchannel("A").getdata())
    total = len(alpha)
    ink = sum(1 for v in alpha if v > 128) / total
    tag = run(
        ["ffprobe", "-v", "error", "-show_entries", "stream_tags=ALPHA_MODE",
         "-of", "default=nw=1:nk=1", str(overlay)]
    ).stdout.strip()
    return {
        "alpha_mode": tag,
        "alpha_mean": round(sum(alpha) / total / 255, 4),
        "alpha_corner": img.getpixel((2, 2))[3],
        "ink_coverage": round(ink, 4),
        "size": f"{img.width}x{img.height}",
    }


def pill_colour(final: Path) -> dict:
    from PIL import Image

    png = HERE / "stills" / "pill_sample.png"
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1.10", "-i", str(final),
         "-frames:v", "1", str(png)])
    rgb = Image.open(png).convert("RGB").getpixel((80, 1517))
    return {"rgb": list(rgb), "distance_to_059669": round(sum((a - b) ** 2 for a, b in zip(rgb, (5, 150, 105))) ** 0.5, 1)}


def main() -> None:
    started = time.time()
    sku = json.loads((SKU_DIR / "sku.json").read_text())
    cards = load_cards()
    print(f"cards in {OVERLAY_SEC}s: {[c['text'] for c in cards]}")

    (HERE / "index.html").write_text(build_html(cards, sku))

    # 1. render the transparent overlay
    overlay = HERE / "overlay.webm"
    skip_render = "--skip-render" in sys.argv
    t0 = time.time()
    if skip_render and overlay.exists():
        overlay_sec = 0.0
        print("reusing", overlay)
    else:
        run(HYPERFRAMES + [
            "render", ".", "--format", "webm", "--fps", str(FPS),
            "--workers", str(WORKERS), "--no-low-memory-mode",
            "--quality", "looks", "--output", "overlay.webm",
        ], cwd=HERE)
        overlay_sec = time.time() - t0
    ov = probe(overlay)
    alpha = verify_alpha(overlay)
    print(f"overlay: {ov['codec_name']} {ov['width']}x{ov['height']} {ov['pix_fmt']} "
          f"{ov['nb_read_frames']} frames -> {overlay.stat().st_size} bytes in {overlay_sec:.1f}s")
    print(f"alpha: ALPHA_MODE={alpha['alpha_mode']} mean={alpha['alpha_mean']} "
          f"corner={alpha['alpha_corner']} ink={alpha['ink_coverage']}")
    if alpha["alpha_mode"] != "1":
        raise SystemExit("overlay has no ALPHA_MODE=1 tag")
    if alpha["alpha_corner"] != 0 or alpha["ink_coverage"] < 0.005:
        raise SystemExit(f"alpha plane is wrong: {alpha}")

    # 2. clean 3 s of footage at the target format (no burned-in text)
    clean = HERE / "clean.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-ss", "0.4", "-t", str(OVERLAY_SEC), "-i", str(CLIP),
         "-vf", f"scale=1080:1920:flags=lanczos,fps={FPS},setsar=1",
         "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", str(clean)])

    # 3. composite in RGB: the YUV path shifts flat brand colours (#059669 -> #008469).
    #    `-c:v libvpx-vp9` MUST precede -i or the alpha plane is dropped.
    final = HERE / "spike_composited.mp4"
    t1 = time.time()
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clean),
         "-c:v", "libvpx-vp9", "-i", str(overlay),
         "-filter_complex",
         "[0:v]format=rgb24[b];[1:v]format=rgba[a];"
         "[b][a]overlay=0:0:eof_action=pass:format=rgb,"
         "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
         "-c:v", "libx264", "-crf", "18", "-preset", "medium",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(final)])
    composite_sec = time.time() - t1

    fin = probe(final)
    pill = pill_colour(final)
    print(f"final: {fin['pix_fmt']} {fin['nb_read_frames']} frames, "
          f"{final.stat().st_size} bytes, composite {composite_sec:.1f}s")
    print(f"pill pixel at (80,1517): {pill['rgb']} (distance to #059669: {pill['distance_to_059669']})")

    # 4. stills for visual QA
    stills = HERE / "stills"
    stills.mkdir(exist_ok=True)
    for t in (0.45, 1.10, 2.40):
        run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(final),
             "-frames:v", "1", str(stills / f"t{t:.2f}.png")])
    # overlay alpha proof frame
    run(["ffmpeg", "-y", "-v", "error", "-ss", "1.10", "-c:v", "libvpx-vp9", "-i", str(overlay),
         "-frames:v", "1", str(stills / "overlay_alpha.png")])

    report = {
        "sku": SKU,
        "hyperframes": "0.9.54",
        "overlay": {
            "file": str(overlay.relative_to(REPO)),
            "codec": ov["codec_name"], "pix_fmt": ov["pix_fmt"],
            "frames": int(ov["nb_read_frames"]), "bytes": overlay.stat().st_size,
            "render_sec": round(overlay_sec, 2),
            "sec_per_frame": round(overlay_sec / max(1, int(ov["nb_read_frames"])), 3),
            **alpha,
        },
        "composite": {
            "file": str(final.relative_to(REPO)),
            "bytes": final.stat().st_size,
            "composite_sec": round(composite_sec, 2),
            "frames": int(fin["nb_read_frames"]),
            "pill_rgb": pill["rgb"],
            "pill_distance_to_059669": pill["distance_to_059669"],
        },
        "cards": [c["text"] for c in cards],
        "total_sec": round(time.time() - started, 1),
    }
    (HERE / "spike_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
