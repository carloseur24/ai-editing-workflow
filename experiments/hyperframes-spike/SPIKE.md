> **Status: superseded.** This spike proved alpha + compositing on 3 s and
> measured the 8–13 min cost of a per-frame VP9-alpha overlay. Production now
> renders **unique caption states** (≈243 PNGs at 10 fps) and holds each for its
> real duration via `overlay.ffconcat`; see `docs/pipeline.md` §6. The colour
> rule here was pipeline-specific: the current path composites in RGB with the
> default RGB→YUV conversion, measured pill Δ2 vs native.

# HyperFrames overlay spike — results

Proof that a transparent HyperFrames overlay composites cleanly over the existing
footage path on this WSL2 box. Not wired into the pipeline yet; everything here is
additive and reversible.

## Run it

```bash
python spikes/hyperframes_overlay/spike.py             # full: render + composite
python spikes/hyperframes_overlay/spike.py --skip-render   # reuse overlay.webm
```

Artifacts: `overlay.webm`, `spike_composited.mp4`, `stills/*.png`, `spike_report.json`.

## Measured

| Metric | Value |
|---|---|
| Overlay | VP9, 1080x1920, 60 fps, 180 frames (= 3 s) |
| Size | 486 KB (3 s) |
| `ALPHA_MODE` | **1** |
| Alpha at frame edge | 0 (transparent) |
| Ink coverage | 3.7% of the frame |
| Mean alpha | 3.92% |
| Render wall time | 52 s / 87 s across two runs (0.29–0.48 s per frame, 4 workers) |
| Composite wall time | 7–11 s (includes a full re-encode) |
| Pill colour after composite | `rgb(2,149,104)` vs `#059669` = `rgb(5,150,105)` — **ΔE ≈ 3** |
| Existing pipeline pill | `rgb(0,148,104)` — same tolerance |

## What the spike proved

1. **Alpha works end to end.** `ffprobe` reports `pix_fmt=yuv420p`, which is a
   lie: VP9 stores alpha out of band. The only valid check is
   `stream_tags=ALPHA_MODE=1` plus a decoded RGBA frame with `alpha=0` outside the
   glyphs. Both pass.
2. **`-c:v libvpx-vp9` must precede `-i`.** Without it the decoder ignores the
   alpha plane and the reel turns black.
3. **The DOM contract reproduces the current design.** Header at y=110, caption
   band at y=1305, pill at y=1470, footer at y=1584, 46 px Inter Black, emerald
   checkmarks — all authored as HTML + a paused GSAP timeline, no pixel math.
4. **Fonts are deterministic.** Inter-Black.ttf is served from the project, not
   `local()`; Chrome never falls back.

## Two defects found and fixed here (both would have shipped)

| Defect | Cause | Fix |
|---|---|---|
| Quotes rendered around every string | `json.dumps()` used as an HTML escaper | `html.escape()` |
| Brand pill shifted from `#059669` to `#008469` | YUV-space compositing + implicit BT.601 RGB→YUV matrix | composite in `rgb24`, then `scale=out_color_matrix=bt709:out_range=tv` |

The second one is subtle: the pixel is still "green", but it is a different green
from the one the current renderer emits. Any brand-colour QA must sample the pill
after compositing, not before.

## Cost, honestly

The overlay render is **the slow stage**, not the composite:

- 0.29–0.48 s per frame at 1080x1920@60 with 4 workers and VP9 alpha.
- A full 28 s reel is 1,680 frames → **8–13 minutes** for the overlay alone,
  against ~3 minutes for the entire current render.
- The same 28 s reel as opaque H.264 measured **1 m 48 s** in the earlier spike;
  VP9 alpha is roughly 5× slower per frame.

Consequences for the integration design:

1. **Cache the overlay by content hash** (text + timings + layout). A copy tweak
   re-renders one overlay; a re-cut that does not touch text reuses it.
2. **Render overlays per scene, not per reel**, then `concat` them. Changing one
   caption costs one scene, not 28 seconds.
3. **Composite inside the existing FFmpeg pass** as a second input. The 7–11 s
   measured here includes a full second encode; as an extra input to the pass
   `render_video.py` already runs, the research measured **+4%**.

## Not proven yet

- 28 s continuous overlay (only 3 s measured).
- `eof_action=pass` when the overlay is *shorter* than the base (tested only at
  equal length).
- Whether `c_caption_zone` (width ≤ 885 px, font ≥ 24 px) survives Chrome's text
  metrics — the generator currently measures with PIL.
- MOV/ProRes 4444 as a faster alpha path; not rendered.
