# HyperFrames integration research

## Executive summary

HyperFrames is a deterministic frame server, not a timeline editor. For every output
frame it computes `t = floor(frame) / fps`, seeks registered paused timelines to that
exact time in headless Chrome, captures, then encodes with FFmpeg. `<video>` is never
played during capture: sources are pre-extracted into per-frame stills and injected as
`<img>`. Time is an input, never an emergent property.

For this pipeline the evidence supports one shape: **augment, do not replace.**
`plan_edit.py` and the FFmpeg cut assembly stay the source of truth for footage and
beat-synced cuts. HyperFrames takes the layer it is strictly better at - captions,
price pill, badges, footer, transitions - and returns a transparent overlay that
`render_video.py` composites in one extra input. Measured composite overhead is +4%.

Three measured facts drive the operational plan:

| Fact | Measured on this box | Consequence |
| --- | --- | --- |
| 7.9 GB RAM trips the low-memory profile | auto = 1 worker, 6 m 31 s | pass `--no-low-memory-mode --workers 4` -> 1 m 48 s |
| Per-worker memory is 414 MB PSS, not ~256 MB | 4 workers fit, 6 risky | cap workers, never use `auto` |
| WSL2 exposes the Radeon through /dev/dxg | 8.6 fps vs 6.1 on SwiftShader | keep the GPU flags HyperFrames passes |

The locked layout needs one decision: HyperFrames' own Reels safe zone is
y 269-1248, x 65-1015, so the current header (110-318) and bottom stack
(1305-1628) sit outside it - 14 `outside_safe_zone` findings and contrast
between 1.25:1 and 2.58:1 against a 3:1 floor. Either the spec is intentionally
wider than Instagram Reels (defensible for WhatsApp Status, which is why it was
drawn), or the caption band must move. That is a product call, not a bug.


## Contents

1. [Animation Adapters and Determinism](#animation-adapters-and-determinism) — maturity: production, confidence: 0.87
2. [Audio and Media Pipeline](#audio-and-media-pipeline) — maturity: beta, confidence: 0.87
3. [CLI and Programmatic API](#cli-and-programmatic-api) — maturity: beta, confidence: 0.85
4. [Composition Authoring Model](#composition-authoring-model) — maturity: stable, confidence: 0.90
5. [Limitations, Known Issues and Roadmap](#limitations-known-issues-and-roadmap) — maturity: beta, confidence: 0.80
6. [Local_Rendering_On_WSL2](#local-rendering-on-wsl2) — maturity: stable, confidence: 0.90
7. [Migration and Ecosystem Comparison](#migration-and-ecosystem-comparison) — maturity: beta, confidence: 0.81
8. [Motion Graphics Catalog and Transitions](#motion-graphics-catalog-and-transitions) — maturity: stable, confidence: 0.86
9. [Overview and Architecture](#overview-and-architecture) — maturity: stable, confidence: 0.90
10. [Python Pipeline Integration](#python-pipeline-integration) — maturity: beta, confidence: 0.86
11. [Rendering Engine Performance and Output](#rendering-engine-performance-and-output) — maturity: stable, confidence: 0.82
12. [Text, Typography and Kinetic Captions](#text-typography-and-kinetic-captions) — maturity: production, confidence: 0.83
13. [Vertical_9x16_Reels_Workflow](#vertical-9x16-reels-workflow) — maturity: stable, confidence: 0.86

---

<a id="animation-adapters-and-determinism"></a>
## Animation Adapters and Determinism

### Basic Info

**Title**

Animation Adapters and Determinism

**Domain**

Authoring / seekable animation runtime

**One Liner**

HyperFrames renders by seeking to time t through pluggable adapters instead of playing, so every animation must be a pure function of t.

**Official Sources**

- https://hyperframes.heygen.com/concepts/frame-adapters
- https://hyperframes.heygen.com/concepts/determinism
- https://hyperframes.heygen.com/guides/gsap-animation
- https://hyperframes.heygen.com/guides/frame-sources
- https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes/references/runtimes.md
- https://www.npmjs.com/package/@hyperframes/core
- https://www.heygen.com/research/html-to-video

**Maturity**

production

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

Frame-exact, reproducible capture of any web animation inside a 1080x1920@60 reel: kinetic type, CSS loops, WAAPI, Lottie character animation, Three.js scenes, anime.js, WebGPU/TypeGPU canvases and hand-written render(t) functions all resolve to the same pixels on every run, with no dropped frames on slow hardware because nothing is captured in real time.

**Key Features**

- Single seek contract: the renderer calls window.__hf.seek(tSeconds, opts) once per output frame; it never calls play(). Time is computed as floor(frame)/fps with integer math, never from a clock.
- GSAP is the default and first-class runtime: a paused gsap.timeline({paused:true}) registered as window.__timelines[data-composition-id] is all a composition needs.
- The browser runtime ships eight named adapters: gsap, css, waapi, lottie, three, animejs, typegpu and frame-source. Five more adapters (mapbox, leaflet, google-maps, maplibre, d3) exist only as readiness gates, not time drivers.
- Exported adapter interface for custom hosts (experimental v0): FrameAdapter { id, init?(ctx), getDurationFrames(), seekFrame(frame), destroy?() } with FrameAdapterContext { compositionId, fps, width, height, rootElement? }.
- createGSAPFrameAdapter({id, fps, timeline}) is shipped in @hyperframes/core: it pauses the timeline, derives duration from totalDuration()/duration(), and maps frame N to timeline.seek(N/fps, false).
- GSAP seeks go through totalTime(t, suppressEvents) plus a settle pass that re-renders keyframed tweens, _startAt values and zero-duration set() calls at t-epsilon and t+epsilon, so backward and random seeks land exactly.
- CSS @keyframes are discovered by scanning computed style for animation-name != none; the adapter seeks via Animation.currentTime when the element's animations are reachable, and otherwise rewrites animation-delay to (authoredDelay - elapsed) and forces animation-play-state: paused.
- WAAPI is captured by monkey-patching Element.prototype.animate at runtime init, so animations created by later scene scripts are registered automatically; animation.currentTime is set in milliseconds.
- Lottie (lottie-web and dotLottie) is driven through lottie.getRegisteredAnimations() or a manual window.__hfLottie push, supporting goToAndStop(ms|frame), setFrame, setCurrentRawFrameValue and seek(percent), with loop handled by modulo and frames clamped to totalFrames-1.
- Three.js scenes read window.__hfThreeTime and are gated on THREE.DefaultLoadingManager itemsLoaded === itemsTotal, so textures and models are loaded before frame 0.
- anime.js v4 animations are collected from window.anime.running into window.__hfAnime and seeked with anim.seek(milliseconds).
- TypeGPU/WebGPU compositions read window.__hfTypegpuTime and need data-requires-webgpu on the composition root.
- Custom canvas/WebGL/p5 renderers either listen for the hf-seek CustomEvent ({detail:{time, waitUntil}}) or register window.__hyperframes.registerFrameSource({element, ready, render, dispose}).
- window.__hf.buildReady['key'] = promise gates capture: heavy setup (shader compile, mesh upload) resolves before any frame is taken.
- Capture readiness is a two-flag handshake: window.__playerReady (composition booted) and window.__renderReady (all adapters discovered, all ready promises settled), plus __hfWaitForSeekCompletion for asynchronous seek handlers.
- <video> and <audio> are never seeked through the decoder during capture: the producer decodes exact frames and injects a sibling <img id="__render_frame_<renderId>__"> that the composition paints from, so picture is frame-accurate and independent of Chrome's video decoder timing.
- Static-frame dedup reuses byte-identical frames instead of re-seeking and re-screenshotting them (engine default on, screenshot capture mode only).

**Technical Mechanism**

A composition is ordinary HTML plus data-* timing attributes. The compiler inlines sub-compositions and fonts and ships a sha256-pinned runtime bundle (hyperframe.runtime.iife.js). The producer opens the page in chrome-headless-shell with BeginFrame control, waits for window.__hf and the readiness flags, then for every frame index i: computes t = quantizeSeekTime(i/fps, fps) = floor(i/fps*fps + 1e-9)/fps, calls window.__hf.seek(t) which walks every registered adapter in order (discover -> seek), awaits the seek-completion barrier and any hf-seek waitUntil promises, then asks CDP HeadlessExperimental.beginFrame for an atomic layout->paint->composite->screenshot with frameTimeTicks and interval, falling back to Page.captureScreenshot when BeginFrame is unavailable (macOS/Windows desktop Chrome, some GPUs). Frames are written as JPEG (default) or PNG and encoded by FFmpeg, with audio mixed from the <audio>/<video> elements using the same authored timeline. Because each frame is a fresh seek plus a full repaint of changed raster tiles, the worker count, chunk boundaries and seek order cannot change a frame's pixels.

**Text And Motion Capabilities**

Temporal precision is exactly one output frame: 16.667 ms at 60 fps, 33.333 ms at 30 fps. Sub-frame sampling exists only for motion blur (subFrameDivisions), which rounds on a finer grid rather than moving a frame instant. Any value that can be expressed as f(t) is renderable: GSAP eases (power/expo/elastic/back/custom), CustomEase curves authored in the Studio (parsed and re-registered deterministically), hyperframes' own wiggle ease (closed-form wiggles/type/amplitude), springs expressed as closed-form functions of t or approximated with elastic.out(1, 0.5) / back.out(1.4), per-character and per-word staggers, clip-path and mask reveals, filter blur ramps, and canvas/SVG draw calls. Motion blur, if enabled, is the documented exception to byte-identical output because it accumulates sub-frames along the motion path.

**Output Formats**

- MP4 H.264 (default; Main profile for 1080x1920@60, yuv420p)
- MOV (ProRes/H.264 depending on flags)
- WebM VP9 with real alpha channel (verified with ALPHA_MODE + alphaextract) for overlays
- GIF
- PNG sequence (one file per frame, for external compositing)
- Still frames / snapshots at arbitrary timestamps (hyperframes snapshot --at, --range) for review gates

### Usage & API

**Setup And Install**

Prerequisites: Node.js 22 or newer, FFmpeg on PATH, and a Chromium build (the CLI resolves chrome-headless-shell itself; `npx hyperframes doctor` checks all three).

```bash
npx hyperframes init my-video   # scaffold index.html, compositions/, assets/
cd my-video
npx hyperframes preview         # live preview with reload
npx hyperframes render          # render to MP4
```

For adapter code or a custom host, install the packages:

```bash
npm i @hyperframes/core @hyperframes/engine @hyperframes/producer
# or, for a Python-driven pipeline, no install is needed beyond the CLI:
npx hyperframes@0.9.54 render --help
```

GSAP itself is loaded by the composition (CDN or a local file); the runtime does not bundle it. HyperFrames pins nothing about the GSAP version, but the docs examples use gsap@3.14.2 and the bundle's GSAP helpers assume the v3 timeline API.

**Minimal Code Example**

A complete, renderable composition with one adapter-driven animation:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      html, body { margin: 0; width: 1080px; height: 1920px; background: #06080c; }
      #root { position: relative; width: 1080px; height: 1920px; overflow: hidden; }
      #title { position: absolute; inset: 0; display: grid; place-items: center;
               font: 900 96px/1.05 Inter, sans-serif; color: #fff; letter-spacing: -0.02em; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="hook" data-start="0" data-duration="3"
         data-fps="60" data-width="1080" data-height="1920">
      <h1 id="title" class="clip" data-start="0" data-duration="3" data-track-index="1">
        iPhone 14 at $330
      </h1>
    </div>
    <script>
      const tl = gsap.timeline({ paused: true });
      tl.fromTo('#title',
        { opacity: 0, y: 64, scale: 0.96 },
        { opacity: 1, y: 0, scale: 1, duration: 0.42, ease: 'expo.out' }, 0.1);
      tl.to('#title', { opacity: 0, duration: 0.18, ease: 'power2.in' }, 2.7);
      window.__timelines = window.__timelines || {};
      window.__timelines['hook'] = tl;   // key MUST equal data-composition-id
    </script>
  </body>
</html>
```

The same file driven by a hand-written adapter instead of GSAP:

```js
import { createGSAPFrameAdapter } from '@hyperframes/core';

const adapter = createGSAPFrameAdapter({ id: 'hook', fps: 60, timeline });
await adapter.init?.({ compositionId: 'hook', fps: 60, width: 1080, height: 1920 });
await adapter.seekFrame(42);            // 42/60 = 0.7 s, exact and repeatable
console.log(adapter.getDurationFrames()); // 180 for a 3 s timeline
```

And the escape hatch for a renderer that owns its own draw loop (canvas, WebGL, p5):

```html
<canvas id="scene" width="1080" height="1920"></canvas>
<script>
  const draw = (t) => { /* pure function of t: no rAF, no Date.now, no Math.random */ };
  window.addEventListener('hf-seek', (e) => {
    draw(e.detail.time);
    // async GPU work must be declared synchronously inside the handler:
    e.detail.waitUntil(device.queue.onSubmittedWorkDone());
  });
  draw(0);
</script>
```

Or, with the frame-source registry (preferred: it carries timing, ranges and a dispose path):

```js
const unregister = window.__hyperframes.registerFrameSource({
  element: document.getElementById('scene'),
  ready: initWebGL(),                       // awaited before capture
  render: (sourceTime, signal) => drawScene(sourceTime, signal),
  dispose: () => releaseWebGL(),
});
```

**Cli Or Api Surface**

- window.__timelines[compositionId] = gsapTimeline  — the primary registration point; the key must equal data-composition-id.
- window.__hf.seek(tSeconds, { suppressEvents })  — called once per output frame by the engine; the only clock the page gets.
- window.__hf.duration / window.__hf.lengthSource / window.__hf.durationSource  — resolved render length and what determined it.
- window.__hf.buildReady['key'] = Promise  — defer capture until heavy setup resolves; a rejection fails the render with the reason.
- window.__hf.animationEnd() / window.__hf.clipWindow(el)  — introspect the captured timeline and a clip's visibility window.
- window.__hfWaitForSeekCompletion()  — page-side barrier for asynchronous seek handlers.
- window.__hyperframes.registerFrameSource({ element, ready, render, dispose, sourceRange })  — registers a function-of-time scene; returns an unregister function.
- window.addEventListener('hf-seek', e => { e.detail.time; e.detail.waitUntil(promise) })  — the low-level frame callback; waitUntil must be called synchronously.
- window.__hfAnime / window.__hfLottie / window.__hfThreeTime / window.__hfTypegpuTime  — the globals the built-in adapters read.
- FrameAdapter, FrameAdapterContext, createGSAPFrameAdapter  — exported by @hyperframes/core for custom hosts (experimental v0).
- npx hyperframes render [--fps 60] [--workers N] [--quality draft|standard|high] [--start s --end s] [--format mp4|webm|mov|gif|png] [--docker]
- npx hyperframes snapshot --at 0,0.6,2.9  — render specific timestamps to inspect seeking correctness without a full render.
- npx hyperframes keyframes --json [--selector '#id']  — lists GSAP/CSS/anime.js keyframes and eases for audit.
- npx hyperframes lint / check  — static contract and runtime behaviour gates (catches missing timeline registrations, CSS transform conflicts, static scenes).
- data-no-timeline  — on a root or host that registers no timeline, skipping the 45 s player-ready poll.

**Key Config Options**

- fps (default 30; the reel pipeline uses 60) — locks frame count and the seek grid; cannot be changed per frame.
- quality: draft | standard | high (default standard) — trades capture/encode quality and speed.
- format: jpeg | png (default jpeg) and jpegQuality (default 80) — PNG is required for lossless or alpha work and is much heavier on disk.
- concurrency: number | 'auto' (default auto) — worker count; coresPerWorker default 2.5 and minParallelFrames default 120 decide when parallel capture starts.
- playerReadyTimeout (45 s) — how long each composition may take to register its timeline; the reason a timeline-free composition costs 45 s per render without data-no-timeline.
- renderReadyTimeout (15 s) — how long the runtime may take to finish adapter discovery and buildReady promises.
- staticFrameDedup (default true) — reuses byte-identical frames instead of re-seeking them; only in screenshot capture mode.
- browserGpuMode: software | hardware | auto (default software) — WebGL availability; software SwiftShader is 5-50x slower for WebGL work.
- forceScreenshot (default false) — skip BeginFrame and use Page.captureScreenshot; slower but works where BeginFrame is unsupported.
- useDrawElement / enableDrawElementWorkerEncode (default true, macOS/Windows hardware GPU only) — faster capture path with a self-verification fallback to screenshots.
- data-motion-blur="on" plus data-motion-blur-shutter on the composition root — opt-in per-element blur; breaks byte-identical output.
- data-requires-webgpu — required on a WebGPU composition root; without it the TypeGPU adapter never runs.
- data-playback-rate (0.1-10) and data-media-start on media — retiming and trimming are authored, never scripted with currentTime.

### Integration

**Pipeline Fit**

Augments, and for overlays replaces, the current stack. The Fenixsoft pipeline already owns beat-synced cuts, audio mixing and QA in Python/FFmpeg; HyperFrames is a bad fit for cutting 4-6 camera angles of product footage (that is FFmpeg's job and it is already measured against the beat grid) but a very good fit for everything that must be typographically exact and repeatable: the price pill, the kinetic captions, the badge stack, the trust strip and the CTA card. The recommended shape is a hybrid: FFmpeg produces the cut picture and the mixed bed, HyperFrames renders a transparent WebM (VP9 alpha) or a full-frame overlay pass that FFmpeg composites on top. That keeps render_video.py's cut/beat logic and qa_check.py's gates intact while moving all text and motion graphics to a seekable timeline where the same JSON always yields the same pixels.

**Python Integration**

Three levels, cheapest first.

1) CLI via subprocess (works today, no Node code): write the composition folder, run the render, parse the log.

```python
import json, subprocess, pathlib

def render_overlay(project_dir: pathlib.Path, out: pathlib.Path, timeout=900) -> dict:
    cmd = ["npx", "--yes", "hyperframes@0.9.54", "render",
           "--format", "webm", "--fps", "60", "--workers", "2",
           "--output", str(out)]
    proc = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"hyperframes render failed ({proc.returncode}):\n{proc.stderr[-4000:]}")
    return {"output": str(out), "log_tail": proc.stdout[-2000:]}
```

Caveats: npm resolution must be warm (pin the version, or install once into the project); the process needs a writable HOME for ~/.cache/hyperframes; timeouts must exceed playerReadyTimeout (45 s) plus per-frame capture; stderr carries the actionable error and stdout the progress lines.

2) Node bridge for adapter-level tests: a ~40-line script that imports @hyperframes/core and @hyperframes/engine, opens the page with Puppeteer, calls window.__hf.seek(t) for a list of timestamps and returns per-frame image hashes. That is the cheapest way to prove determinism (same input -> same sha256 per frame) in CI without a full encode.

3) Direct producer API from Node (`@hyperframes/producer`) when you need progress events, still export or partial ranges without shelling out; call it from Python through a tiny Node helper because there is no Python binding.

**Data Injection**

Per-video data enters through composition variables, never string concatenation inside the script block. Declare a schema on <html data-composition-variables='[{"id":"price","type":"string","default":"$330"}]'>, bind declaratively with data-var-text="price" / data-var-src / CSS var(--price), and pass values with data-variable-values='{...}' on a scene host or with the CLI's variable flag. For lists that must drive a timeline (caption words, per-word timings) emit a JSON island — <script type="application/json" id="words">…</script> — escape it with json.dumps(..., ensure_ascii=False).replace('</', '<\\/') and parse it in the composition, so a quote in a product name cannot break the timeline code. Word timings generated by align_words.py map one-to-one onto the {id, text, start, end} shape HyperFrames' own transcribe command emits.

**Footage And Asset Handling**

Existing MP4s are consumed as <video class="clip" src="assets/shot01.mp4" data-start="0" data-duration="1.52" data-media-start="3.20" data-track-index="0" muted playsinline> — the in-point is data-media-start, the slot is data-start/data-duration, and the renderer decodes the exact frame instead of relying on the decoder. Audio beds are <audio> elements with data-volume and optional data-fade-in/out; the engine feeds FFmpeg -ss for the same offsets, so A/V stays locked to one authored timeline. Fonts: the compiler inlines @font-face with woff2 data URIs (18 bundled families resolve by name, everything else must ship a file), which removes FOIT and network latency from capture. Images and SVGs are local project assets; remote URLs are a determinism and CORS risk and should be downloaded first.

**Migration Effort**

medium

**Integration Steps**

- Freeze the current FFmpeg cut list as the source of truth; export it as JSON (clip, in-point, duration, beat index) so both renderers can consume it.
- Install Node 22 + FFmpeg and pin hyperframes@0.9.54 in the repo (or bake it into the render image) — an unpinned npx is a silent reproducibility hole.
- Create one template project: index.html (1080x1920, data-fps="60", data-duration="28"), compositions/overlay.html holding the price pill, badges, captions and CTA, and assets/fonts with the brand woff2 files.
- Port the caption stage first: convert align_words.py output into a words JSON island, build word groups in the composition, and register one paused GSAP timeline under the scene's data-composition-id.
- Render a transparent WebM of the overlay only and composite it in FFmpeg over the existing cut; compare against the current burn-in with qa_frames/contact_sheet.
- Add the determinism gate to CI: render 3 fixed timestamps twice (snapshot --at) and assert identical sha256 per frame; fail the build on drift.
- Only after overlays are stable, consider moving the cut picture itself into HyperFrames (multiple <video> clips on tracks) — that is a rewrite of the cut stage, not an overlay change.

### Constraints

**Requirements**

- Node.js 22+ (the CLI and all @hyperframes/* packages).
- FFmpeg on PATH for encoding and audio muxing.
- A Chromium build reachable by Puppeteer; BeginFrame capture specifically needs chrome-headless-shell with --enable-begin-frame-control, which regular desktop Chrome on macOS does not support (falls back to screenshots).
- Writable HOME for ~/.cache/hyperframes (fonts) and the FFmpeg/frame temp space.
- Optional GPU for WebGL/WebGPU compositions; without one, SwiftShader is 5-50x slower and some compositions are refused unless allowSoftwareWebGpu is set.
- A finite authored data-duration on the root for Three.js, WebGPU, unbounded or timeline-free compositions.

**Limitations**

- Seek resolution is one output frame. Anything that must happen between frames (true sub-frame motion blur, 120 Hz sampling) is not available except through the motion-blur sub-frame path, which is explicitly not byte-identical.
- Every animated value must be a pure function of t. Any library that owns its own clock (most canvas/WebGL helpers, p5's draw loop, autoplaying video, CSS animations without a controller) produces nondeterministic output until it is adapted.
- gsap.from() records its start values on the first seek and then drifts when the scene is re-seeked; use fromTo() for anything that must survive backward or random seeks. The engine's own comments call this out as a real corruption source.
- Infinite repeats break duration inference and can hang the render; repeats must be finite (repeat: Math.max(0, Math.floor(duration / cycle) - 1)).
- CSS animations need finite animation-iteration-count and animation-fill-mode: both, and their timing is read from computed style; when the adapter cannot reach the Animation object it rewrites animation-delay every seek, so any code that also writes animation-delay will fight it.
- WAAPI animations run on the document timeline, so an animation created at page load must be offset by its composition start (the adapter uses delay/compositionTimeMs); creating them at time 0 in a scene that starts later is a classic desync.
- Lottie is only discovered through window.lottie.getRegisteredAnimations() or a manual window.__hfLottie push; dotLottie-web must be initialized with autoplay:false, loop set, autoResize:false and a local WASM URL.
- Three.js must read window.__hfThreeTime instead of THREE.Clock; a scene that calls clock.getDelta() in its own rAF loop renders a different frame every run.
- TypeGPU/WebGPU requires data-requires-webgpu on the root and is polled on a 250 ms debounce, so very short WebGPU beats can lose their first frames if setup is not behind buildReady.
- The exported FrameAdapter interface is explicitly experimental v0 and its signatures may change before v1; the built-in runtime uses an internal seek({time, suppressEvents}) shape, not seekFrame(frame).
- Motion blur (data-motion-blur) is the documented exception to byte-identical determinism; if the pipeline hashes frames, it must be off.
- A composition that never registers a timeline costs a fixed 45 s per render unless the root declares data-no-timeline.
- Parallel workers and <video>-heavy compositions interact badly: Chrome cannot seek several video elements at once without exhausting decoders, which is why the docs warn that video-heavy compositions can time out in parallel mode.
- data-track-index is a Studio lane only; it does not control paint order, so overlap must be authored with z-index.

**Known Issues**

- Missing data-no-timeline on a timeline-free composition adds a fixed 45 s wait to every render (documented in the troubleshooting guide as 'every render takes about 45 seconds longer').
- Timeline registered before an asynchronous build finishes renders blank; lint code gsap_timeline_registered_before_async_build.
- A host id that differs from its scene's timeline key makes each render wait 45 s per scene and then show static frames.
- CSS transform set in stylesheet plus GSAP animating the same transform is a lint error (gsap_css_transform_conflict) and produces a wrong first frame under random access.
- Tweening visibility on a .clip element freezes scenes; the documented pattern is to tween opacity and then tl.set(visibility) at the end.
- Page-side WebGL compositing (enablePageSideCompositing) computes in f32, so byte-equality fixtures are not valid on that path; correctness is pinned by PSNR instead.
- Two timelines animating the same property of the same element produce order-dependent results that differ between preview and render.

### Evidence

**Confidence**

0.87

**Sources**

- url: https://hyperframes.heygen.com/concepts/frame-adapters | note: Exact FrameAdapter/FrameAdapterContext signature, createGSAPFrameAdapter usage, the five-point determinism contract, and the explicit 'experimental v0' status.
- url: https://hyperframes.heygen.com/concepts/determinism | note: Frame clock t = floor(frame)/fps, the no-clock/no-randomness/no-mid-render-fetch rules, BeginFrame capture, and the motion-blur exception.
- url: https://hyperframes.heygen.com/guides/gsap-animation | note: The paused-timeline contract, the fromTo-over-from rule, font-load-before-measure pattern, and 'media belongs to the runtime'.
- url: https://hyperframes.heygen.com/guides/frame-sources | note: registerFrameSource({element, ready, render, dispose}) semantics, sourceRange clamping, per-element uniqueness, cancellation via AbortSignal.
- url: https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes/references/runtimes.md | note: Per-runtime driving recipe: CSS fill-mode, WAAPI pause + document time, Lottie window.__hfLottie, anime.js window.__hfAnime + createSeededRandom, hf-seek for canvas, buildReady, mulberry32, finite repeats.
- url: https://www.heygen.com/research/html-to-video | note: Author's account of seek-don't-play, the three-method FrameAdapter, chrome-headless-shell + BeginFrame flags, sha256-pinned runtime bundle, N-Chrome chunked rendering, and the video-heavy parallel timeout gotcha.
- url: https://www.npmjs.com/package/@hyperframes/core | note: Published package (0.9.54) whose dist/adapters/*.d.ts and dist/hyperframe.runtime.iife.js were read directly for the adapter list, seek implementations and the __hyperframes public surface.
- url: https://hyperframes.heygen.com/help | note: The 45 s player-ready timeout, data-no-timeline, 'animation is static' and 'media plays out of sync' failure modes, and the 'do not animate width/height on a video' rule.
- url: https://hyperframes.heygen.com/reference/html-schema | note: Root/clip/media attribute contract, the animation contract bullet list, and asynchronous timeline registration rule.

**Open Questions**

- Measured wall-clock per frame for 1080x1920@60 on this WSL2 box, with and without hardware WebGL — needs a benchmark run, not a guess.
- Whether the CSS adapter's animation-delay rewriting behaves correctly when a composition also mutates animation-delay at runtime (e.g. a stagger built by script after init).
- Whether seekFrame(frame) on a custom FrameAdapter is what the shipped engine actually calls, or only the host-facing v0 surface (the engine calls window.__hf.seek(time), and the runtime's internal adapter shape is seek({time})).
- Exact byte-identical guarantee across different Chromium minor versions; the docs only promise it when the environment is pinned (Docker).
- How the Lottie adapter handles expressions (lottie-web evaluates them per frame) — whether expression-driven properties stay deterministic under random access.

### Other Info

**Performance And Cost**

Cost is dominated by seek + capture, not by encode: every frame is a fresh seek through all adapters plus one BeginFrame screenshot. Documented knobs rather than measured numbers: concurrency 'auto' with coresPerWorker 2.5 and a 120-frame minimum before parallel capture; static-frame dedup removes repeated frames; the drawElement fast path (macOS/Windows + hardware GPU) pipelines JPEG encode in a worker and claims a ~1.65-1.96x wall-time speedup; software WebGL is 5-50x slower than hardware. [uncertain] Wall-clock seconds per frame at 1080x1920@60 on this machine are unmeasured; the project's own 28 s / 1680-frame target needs a benchmark before adoption. No per-render fee: rendering is local and free; the only paid surface is HyperFrames Pro/cloud/HeyGen models, which this pipeline does not need.

**Licensing**

HyperFrames is Apache-2.0 (CLI, core, engine, producer, player, shader-transitions, aws-lambda), with no per-render fees and no commercial-use threshold. GSAP is free for commercial use since 3.13 under Webflow's standard 'no charge' license, including SplitText, MorphSVG and the other former Club plugins; the only restriction is building a no-code visual animation tool that competes with Webflow. Three.js (MIT), anime.js (MIT), lottie-web (MIT) and dotLottie-web (MIT) are permissive; the bundled fonts are Fontsource/OFL. Individual catalog blocks are part of the Apache-2.0 repository, but the catalog pages do not state a per-block license — [uncertain] whether a block copied into a commercial template carries an additional attribution requirement.

---

<a id="audio-and-media-pipeline"></a>
## Audio and Media Pipeline

### Basic Info

**Title**

Audio and Media Pipeline

**Domain**

Media handling: audio mixing, video/image assets, codecs, alpha

**One Liner**

Media is plain <audio>/<video>/<img> markup with timing attributes; one Web Audio graph drives preview and an equivalent FFmpeg mix drives the render, so the two agree.

**Official Sources**

- https://hyperframes.heygen.com/reference/html-schema
- https://hyperframes.heygen.com/reference/audio-effects
- https://hyperframes.heygen.com/guides/voice-and-audio
- https://hyperframes.heygen.com/guides/rendering
- https://hyperframes.heygen.com/guides/video-components
- https://hyperframes.heygen.com/guides/media
- https://hyperframes.heygen.com/packages/cli
- https://hyperframes.heygen.com/reference/lint-rules

**Maturity**

beta

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

A composition can carry a complete, repeatable mix - narration, a music bed that ducks itself under the voice, beat-placed effects, per-clip fades and automation - plus video, images, SVG, animated GIFs and custom fonts, and render it to H.264/ProRes/VP9 with alpha, without an external DAW or NLE step.

**Key Features**

- Media is declared in HTML: `<audio>`/`<video>` with `id`, `data-start`, `data-duration`, `data-media-start` (source in-point), `data-playback-rate` (0.1-10), `data-volume`, `data-fade-in`/`data-fade-out`, `data-has-audio` and `muted`.
- Timing may be numeric or relative: `data-start="intro + 0.5"` references another clip's end (`clip-id`, `+s`, `-s`), must resolve inside one composition and may not cycle.
- `data-track-index` is a Studio lane label only - the render never reads it, it does not order paint (use z-index) and it does not prevent overlap. All audible clips are mixed regardless of track.
- `data-volume` is a linear gain where 1 = 0 dB and the ceiling is +12 dB (= 3.98); `MAX_AUDIO_GAIN_DB = 12` is shared by Studio, preview and render so the fader cannot drift.
- Clip-edge fades: `data-fade-in`/`data-fade-out` in seconds, linear, anchored to the clip's own edges, multiplied on top of `data-volume` and any volume lane; over-long fades are scaled to meet inside the clip.
- Looping: the native `loop` attribute repeats a short source inside a longer slot in preview and export; every repeat starts at `data-media-start`, and playback rate applies to the repeats.
- Four audio attributes carry the mix: `data-fx-chain` (JSON, ordered effect list), `data-automation` (JSON, lanes on volume or an effect param), `data-fx-carve` (JSON, ducking settings) and `data-audio-group` (a plain group id).
- Effect registry: 16 effects in four families - filters (highpass, lowpass, peaking, lowshelf, highshelf), dynamics (gain, compressor, limiter, truepeak, gate), nonlinear (saturate, bitcrush), time (delay, reverb, chorus, phaser, pitchshift). Out-of-range values are clamped on read.
- `truepeak` is the delivery limiter: 4x oversampled, lookahead 0.5-10 ms, reads the ceiling correctly instead of letting inter-sample peaks through like `limiter` does.
- 22 presets in four families (voice/repair/character/space) plus 5 named EQ jobs (Tame Boominess, Reduce Mud, Reduce Boxiness, Add Clarity, Soften Harshness) and 5 one-knob profiles (Evenness, Tightness, Warmth, Space, Crush).
- Automation lanes: `target` is `volume` or `fx.<nodeId>.<param>`, points are `{t, v, curve}` with optional `viaX`/`viaY`, max 512 points per lane, and `curve` bends the segment leaving a point.
- Voiceover carve (sidechain ducking): `data-fx-carve={"enabled":true,"sources":["voiceover"],"strength":0.25}` on the bed; strength derives cut depth, band count, width, intelligibility bias, level room and target depth. It writes ordinary peaking filters + a gain stage tagged `fromCarve`, so re-running replaces only its own nodes.
- Audio groups: `<hf-audio-group id="voiceover" data-label data-volume>` sums its members onto one bus with its own chain, fader and automation; groups do not nest, membership lives on the member (`data-audio-group`), and a muted video ignores the attribute.
- Master bus: `data-fx-chain` on the top-level composition root runs once over the sum of every clip and group, after each group's chain - the only way to put one limiter across everything. There is no master automation.
- Mixing is a straight sum at unity: `amix` with `normalize=0`, so adding a track never turns the others down and loud overlaps can exceed 0 dBFS; put a `truepeak` on the master bus to bound it.
- Delivery ceiling: export keeps the file under -1 dBTP by inserting a `truepeak` at -3 dBTP when the mix would pass it, and if a peak still gets through it lowers the whole mix by one flat amount and prints an `audio_lowered` warning.
- Adaptive helpers in core: voiceover carve, an 'Even Out Levels' leveller that writes its own `gain` lane (windowed RMS, explicitly not LUFS normalisation), presets, jobs and profiles - all producing the same chain you could have written by hand.
- Media analysis CLI: `hyperframes audio` reports per-sound moments (start, steepest rise, peak, 40 dB tail), onsets with strength, LUFS integrated/short-term (ITU-R BS.1770, matching FFmpeg ebur128 to 0.1 LU), sample/true peak, clipping, silence spans, a `kind` guess and a ~2,000-value envelope - plus a whole-recording `--read` mode with voice/effects/music/ambience lines.
- `hyperframes beats` decodes the bed with FFmpeg, runs the same beat tracker Studio uses, and writes `beats/<audio-path>.json` (`{version, audio, beats:[{time, strength}]}`); it refuses to invent a grid for beatless music (count 0, exit 0).
- `hyperframes normalize-audio --reference <id> --target <id>` measures both clips with FFmpeg's integrated EBU R128 loudness, accounts for their current `data-volume`, and writes an absolute matched gain that respects the +12 dB and -1.5 dBTP caps.
- Images: `<img>` and `<svg>` work directly; HEIF/HEIC photos are pre-converted, animated GIFs of 5 MB+ in the entry composition are prepared into a seekable video, and base64 media is a lint error.
- Fonts: project-local `.woff2` via `@font-face` is the deterministic path (aliases + a bundled font set, `injectDeterministicFontFaces`, `failClosedFontFetch`), Google Fonts is supported by localizing the stylesheet, and `local-only` system families alias in distributed renders.
- Alpha in and out: VP9/VP8 WebM (sidecar alpha) and ProRes 4444 inputs are decoded with the alpha-aware decoder and extracted as PNG frames; output alpha is WebM (VP9 yuva420p, `alpha_mode=1`), MOV (ProRes 4444 yuva444p10le) or a PNG sequence.
- Codec coverage: MP4 = H.264 + AAC (H.265 10-bit BT.2020 for HDR), WebM = VP9 + Opus, MOV = ProRes 4444 (+alpha) or 422 profiles, plus GIF, PNG sequence and HLS (H.264/AAC remuxed to .ts).
- `/media-use` skill + `hyperframes media-use` subcommands resolve, generate or import music, SFX, images, icons, logos, voiceover, avatar video and color grades into the project, cached and de-duplicated.

**Technical Mechanism**

The compiler parses `<audio>`/`<video>`/`<img>` (parseAudioElements, parseVideoElements, parseImageElements) into AudioTrack/VideoElement records. At render time each audio source is decoded by FFmpeg to PCM, trimmed with `atrim`/`-ss`, gained (`volume`, with `eval=frame` for envelopes), faded (`afade`), delayed to its film position (`adelay`) and summed with `amix=...:normalize=0`; at most 96 inputs enter one graph, larger mixes are summed in stages. Any `data-fx-chain`/`data-automation` is applied on WAV with an OfflineAudioContext inside the same headless Chrome that renders frames, using the same graph builders as preview - one implementation per effect, so preview predicts render by construction. The result is PCM s16le 48 kHz stereo (`audio.m4a`), then `truepeak`/delivery limiting, then AAC 192k for MP4/MOV or Opus 128k for WebM. Video is decoupled: FFmpeg extracts every needed source frame ahead of capture (JPEG by default, PNG for alpha-capable or colour-sensitive sources), and the renderer injects those frames into the page so Chrome never has to decode the source itself.

**Text And Motion Capabilities**

Sound and type share one clock, which is the point for kinetic captions: word timings come from `transcribe` (word-level `[{text,start,end}]` in transcript.json) and each caption gets `data-start`/`data-duration` in the same film seconds the audio mix uses, so a caption cannot drift from its syllable. `hyperframes audio --only onsets` gives the exact steepest-rise and peak instants of an effect so a cut or a punch-in can land on the transient rather than the file start; `beats` gives a measured beat grid for snapping. Audio-reactive captions are a documented pattern (single tween per group, sampling the audio at 100-200 ms). On the delivery side the same pipeline guarantees the mix is <= -1 dBTP, which is what keeps platform players from compressing the top end of a caption hit.

### Usage & API

**Setup And Install**

Nothing to install for the media model itself - it is HTML. To use the analysis/mixing commands you need the CLI plus FFmpeg:

```bash
npm install -g hyperframes@0.9.54   # or npx hyperframes ...
ffmpeg -version && ffprobe -version
npx hyperframes doctor              # checks ffmpeg, ffprobe, node, bundled audio
```

Optional local models: `npx hyperframes models install parakeet` (~640 MB, faster/more accurate ASR), Kokoro TTS (no API key), `u2net_human_seg` ONNX for `remove-background` (~168 MB). HeyGen-backed sources (music catalog, cloned voices, avatars, stem separation) need `npx hyperframes auth login` and the separate `heygen` CLI >= 0.3.0.

**Minimal Code Example**

```html
<!-- A 12 s scene: muted footage, a music bed that ducks under narration, one SFX -->
<div id="root" data-composition-id="main" data-start="0" data-duration="12"
     data-width="1080" data-height="1920"
     data-fx-chain='{"version":1,"nodes":[{"type":"truepeak","id":"m1","label":"Delivery ceiling","params":{"ceiling":-1.5,"lookahead":3,"release":80}}]}'>

  <video id="shot-01" class="clip" src="assets/01_back_cameras.mp4" muted playsinline
         data-start="0" data-duration="3.2" data-media-start="3.20" data-track-index="0"></video>

  <style>hf-audio-group { display: none !important; }</style>
  <hf-audio-group id="voiceover" data-label="Voiceover" data-volume="1"></hf-audio-group>

  <audio id="vo-01" data-audio-group="voiceover" src="assets/vo.wav"
         data-start="0.4" data-duration="11.6" data-volume="1"
         data-fx-chain='{"version":1,"nodes":[{"type":"highpass","id":"n1","params":{"frequency":120,"q":0.707,"poles":"2"}}]}'></audio>

  <audio id="music" src="assets/bed.wav" loop
         data-start="0" data-duration="12" data-volume="0.45"
         data-fade-in="0.4" data-fade-out="1.2"
         data-fx-carve='{"enabled":true,"sources":["voiceover"],"strength":0.25}'
         data-automation='{"version":1,"lanes":[{"target":"volume","points":[{"t":0,"v":0.45},{"t":11,"v":0.45},{"t":12,"v":0}]}]}'></audio>

  <audio id="whoosh" src="assets/sfx/whoosh.mp3"
         data-start="3.15" data-duration="0.6" data-volume="0.7"></audio>
</div>
```

```bash
# Measure what you placed, then render
npx hyperframes audio --element '#music' --json          # loudness, onsets, envelope in film time
npx hyperframes beats . --json                           # writes beats/bed.wav.json
npx hyperframes normalize-audio --reference vo-01 --target music --json
npx hyperframes lint . && npx hyperframes render --output out.mp4
```

**Cli Or Api Surface**

- `hyperframes audio <files...>` - one report per sound: moments, onsets, loudness (integrated + short-term max LUFS), sample/true peak, clipping, silence, kind, envelope. `--json` returns file seconds plus each clip's `start`, `mediaStart`, `playbackRate`, `dataStart` so film time = `start + (t - mediaStart)/playbackRate`.
- `hyperframes audio --element '#riser' | --at 12 | --range 10,14` - query the film instead of a file; `--only onsets,beats,downbeats,bpm,sections,describe` adds music structure (downbeat meter is a low-confidence guess, bpm comes from the beat provider).
- `hyperframes audio <recording> --read [--quality best]` - one line per sentence/effect/music stretch/ambience span, with voice-vs-music loudness over the overlap; `--layer music|sfx|voice` reads a known single-layer file locally with no upload.
- `hyperframes beats [dir|file] [--json]` - writes `beats/<audio-path>.json`; `{ok, file, count, bpm}` for a folder, plus `beatTimes` for a file.
- `hyperframes normalize-audio --reference <id> --target <id> [--write] [--tolerance 0.5] [--json]` - EBU R128 integrated match, respects `data-volume`, rejects remote or out-of-project sources.
- `hyperframes transcribe <audio|video|srt|vtt|json> --to srt|vtt --model --language --engine --json` - word-level timings for captions; `--preserve-cues` keeps authored cue boundaries.
- `hyperframes tts "text" --voice --speed --lang --output` - local Kokoro speech, no API key.
- `hyperframes remove-background <video|image> -o out.webm|out.mov|out.png [--background-output plate.webm] [--device] [--quality]` - produces alpha media for `<video>`/`<img>`.
- `hyperframes media-use resolve|candidates|adopt|analyze|voices|avatars|doctor|stats --type bgm|sfx|image|icon|logo|voice|video|brand|grade|lut` - fetch/generate/import one asset and record it in `.media/`.
- `hyperframes media-treatment --selector '#hero' --grading '{"preset":"skin-soft","intensity":0.6}' --apply` - writes `data-color-grading` on real `<img>`/`<video>`/`<canvas>`.
- `hyperframes capture ... --video <folder>` - pulls video/audio out of a captured page into the project.
- HTML attributes (the real API): `data-start`, `data-duration`, `data-media-start` (`data-playback-start` for nested composition hosts), `data-playback-rate`, `data-volume`, `data-fade-in`, `data-fade-out`, `data-has-audio`, `loop`, `data-audio-group`, `data-fx-chain`, `data-automation`, `data-fx-carve`, `data-color-grading`, `data-beat`.
- `<hf-audio-group id data-label data-volume data-hidden data-fx-chain data-automation>` - a bus; must be closed explicitly (`</hf-audio-group>`), must be inert (`display:none!important` or the runtime's `display:contents!important`).
- @hyperframes/core audio entry points: `./audio-fx`, `./audio-fx-runtime`, `./audio-fx-presets`, `./audio-fx-jobs`, `./audio-fx-profiles`, `./audio-fx-eq`, `./audio-fx-tail`, `./audio-automation`, `./audio-carve`, `./audio-groups`, `./audio-gain` (MAX_AUDIO_GAIN_DB), `./audio-leveller`, `./audio-analysis`, `./audio-fade`, `./media-timing`, `./media-volume-envelope`, `./audible-video`, `./beats`.
- @hyperframes/engine internals that define behaviour: `processCompositionAudio`, `parseAudioElements`, `parseMasterFxChain`, `applyAudioFxChain` (OfflineAudioContext), `MAX_MIX_INPUTS = 96`, `MIXED_AUDIO_FILENAME = 'audio.m4a'`, `resolveFrameFormat` (alpha-capable -> PNG), `extractVideoFramesRange`, `createFileServer`.

**Key Config Options**

- `data-volume` 0..3.98 (0..+12 dB), default 1. On a clip it is applied ahead of the clip's effects (so a compressor reacts to it); on a group it stays after its effects, like a track fader.
- `data-fade-in`/`data-fade-out` seconds, linear, anchored to clip edges, multiplied on top of volume and any lane; fades that together exceed the clip are scaled to meet.
- `data-media-start` seconds into the source (trim); `data-playback-rate` 0.1-10, overridden per time by a `rate` automation lane; `loop` for in-slot repetition.
- `data-automation` = `{version:1, lanes:[{target:'volume'|'fx.<id>.<param>', points:[{t,v,curve,viaX,viaY}]}]}` - `t` is clip-relative on a clip and composition-relative on a group; max 512 points; a lane holds its first value backwards and last value forwards (a bed that starts before the voice needs an explicit `t:0` no-cut point).
- `data-fx-chain` = `{version:1, nodes:[{type,id,label,params,enabled,fromCarve,fromPreset}]}` - array order is signal order; `id` is the automation handle; `enabled:false` is bypass; unknown types only surface at render.
- `data-fx-carve` = `{enabled, sources:[ids|group], strength}` - put it on the bed, never on a voice; strength 0 = spectral only, 0.25 default = 6 dB dip in 3 bands, 0.5 = 10 dB (audible as an effect), above 0.5 for a loud bed under a quiet voice.
- `data-audio-group` = a plain group id; ignored on a muted video or `data-has-audio="false"`; `""` means no group; group ids and element ids share one namespace.
- Master bus = `data-fx-chain` on the top-level `data-composition-id` root only; not applied when the composition is embedded as a sub-composition; master `data-automation` is not read.
- `PRODUCER_AUDIO_GAIN` (default 1) multiplies the finished sum once, before the master bus chain - the render-side master gain.
- Encoder side: MP4/MOV AAC 192k, WebM Opus 128k, mix written as PCM s16le 48 kHz stereo; `PRODUCER_ENABLE_STREAMING_ENCODE`, `PRODUCER_RESTRICT_PAGE_NETWORK` and `PRODUCER_NON_PUBLIC_CIDRS` gate network access for remote media during render.
- Fonts: `@font-face` + project-local `.woff2`, or the bundled alias map; `failClosedFontFetch` makes unresolved fonts a typed error instead of a silent system-font fallback; `hyperframes-localize-fonts` bin rewrites Google Fonts to local files.

### Integration

**Pipeline Fit**

Mostly irrelevant to the current core loop and highly relevant to the overlay layer. render_video.py already mixes voice + bed with FFmpeg to -14 LUFS / -1 dBTP and cuts on a measured beat grid from beat_grid.py; HyperFrames would not improve that. What it adds is (a) an in-HTML mix for compositions rendered by HyperFrames - carve ducking, per-clip lanes, a master true-peak - so an overlay render carries its own sound instead of needing a second FFmpeg pass, and (b) a measurement toolkit (`audio`, `beats`, `normalize-audio`) that can cross-check the Python beat grid and loudness targets. It does not replace FFmpeg for the beat-synced footage cut.

**Python Integration**

Three concrete integration shapes, all file- or process-based:

1) Analysis only (zero risk):
```python
import json, subprocess
rep = json.loads(subprocess.run(
    ["npx","hyperframes","audio",str(wav),"--json"],
    capture_output=True, text=True, check=True).stdout)
onsets = [o["time"] for o in rep["onsets"]]
```
Use it to verify beat_grid.py's phase against `beats` and to place SFX on real transients.

2) Mix in the composition (recommended when HyperFrames renders the overlay): author `data-volume`/`data-fx-chain`/`data-fx-carve` from the same sku.json that drives the Python side, and let the render bake the mix; no audio file juggling. Put the master `truepeak` at -1.5 dBTP and let the CLI's `audio_lowered` warning tell you if it had to pull the whole mix.

3) Post-check the rendered file: keep the existing `qa_check.py` LUFS/true-peak gate on `output/<sku>.mp4` - HyperFrames guarantees <= -1 dBTP, not your -14 LUFS integrated target, and it can only lower the whole mix flat (quiet passages included).

Caveats: `audio` and `normalize-audio` reject remote or out-of-project sources, so pass local paths; the mix is a unity sum, so overlapping narration takes add up (doubling a voice = +6 dB) and nothing limits them unless you add a master `truepeak`; a bad `data-fx-chain` fails the whole render (`audio_processing_failed`) while preview plays dry, so gate with `lint` before spending the render.

**Data Injection**

Word timings and beat grids are the two data feeds. Word timings: write `transcript.json` (`[{text,start,end}]`) from your existing Whisper alignment, or run `hyperframes transcribe --to srt --json`; caption elements then take `data-start`/`data-duration` per word. Beat grid: `hyperframes beats` writes `beats/<audio>.json` (`{version, audio, beats:[{time,strength}]}`) which Studio loads as-is, so authoring and review share the same grid; a `data-beat="name"` element is what `snapshot --review` uses to find beat frames. Per-video copy and asset paths go through `data-composition-variables` + `--variables` (types include `image`, so a per-SKU image path can be a variable). Volume and effect settings are plain JSON attributes, so a Jinja template can emit the mix directly from sku.json.

**Footage And Asset Handling**

Existing MP4 cuts are placed with `data-media-start` = the in-point from edit_plan.json, `data-duration` = the shot length; nothing is re-encoded and the original file is the render source (FFmpeg pre-decodes, so HEVC/ProRes sources are fine even though Chrome cannot play them). Audio beds and voice MP3/WAV are used as-is by the FFmpeg mixer - no need to pre-convert to 48 kHz, mono or a specific codec; the mixer decodes, trims and re-encodes. Fonts must ship inside the project as `.woff2` with `@font-face` (the deterministic path; Google Fonts gets localized, system fonts alias under distributed rendering). Images and SVG work directly; HEIF is converted; large animated GIFs become video. Alpha footage (VP9/VP8 alpha WebM, ProRes 4444) keeps its alpha because it is extracted as PNG.

**Migration Effort**

medium

**Integration Steps**

- Audit the current mix chain: confirm where -14 LUFS is enforced today (render_video.py) and keep it - HyperFrames will only guarantee the -1 dBTP ceiling.
- Create the audio skeleton in the composition: one `<audio>` for the voiceover, one for the bed with `loop`, `data-fade-in/out`, `data-volume`, and a master `truepeak` at -1.5 dBTP on the root.
- Add `data-fx-carve` to the bed naming a `voiceover` group instead of listing clip ids (the linter reports `audio_carve_ungrouped_sources` otherwise), and keep the bed out of that group.
- Run `hyperframes audio --element '#music'` and `--element '#vo-01'` and compare the measured integrated loudness with the Python side; adjust `data-volume` until the voice sits ~4-6 dB over the bed, or let the carve do the work where they overlap.
- Place SFX with `hyperframes audio <sfx> --json --place-at <t>` and use the reported steepest-rise/peak instants rather than the file start, so whooshes land on the transient.
- Cross-check the beat grid: `hyperframes beats . --json` and diff its `beats[].time` against beat_grid.py's grid; keep whichever is measured from the same file, and note they can differ by a frame or two because Studio decodes in the browser at a different sample rate.
- Only if the overlay render must carry sound: render the overlay to `--format webm` (VP9 + Opus, alpha) or MP4, then feed it into the FFmpeg composite as a second input instead of mixing the same voice twice.

### Constraints

**Limitations**

- No automatic loudness normalisation. The mix is a unity sum; overlapping tracks add and can pass 0 dBFS. The only guarantees are the -1 dBTP delivery ceiling and (if you add it) a `truepeak` on the master bus.
- `limiter` is an envelope follower with no lookahead and can pass inter-sample peaks; use `truepeak` for delivery. Even `truepeak` holds its ceiling against a 4x estimate: measured up to 0.6 dB over on tones/noise/dense mixes and 1.2-1.7 dB over on full-band white noise driven 8+ dB into it. Leave ~2 dB of margin under a hard platform limit.
- `data-track-index` is display-only: it neither orders paint nor isolates tracks, and two clips on the same track can overlap in time and both play.
- Automation is only possible on parameters backed by a native `AudioParam`. Worklet effects (compressor, limiter, truepeak, gate, bitcrush, pitchshift) are not automatable at all; a lane on such a parameter is silently inert - automate a `gain` stage around the effect instead.
- Two clocks: a lane on a clip counts from that clip's start, a lane on a group counts from composition time. Writing a group lane in clip-relative time is the most expensive documented mistake.
- A `volume` lane multiplies `data-volume` and wins over a GSAP tween on `volume` (the tween is ignored; lint `audio_volume_double_automation`); a tween's values are absolute and replace `data-volume` (lint `audio_volume_tween_overrides_gain`).
- `data-playback-start` vs `data-media-start` is a real split-brain: the timing compiler, HTML parser and audio mixer read only `data-media-start`, while the runtime, Studio and snapshot read `data-playback-start` first. A `<video>` authored with only `data-playback-start` renders a trimmed picture over untrimmed audio.
- No static validation of an effect chain: an unknown effect type or a bad param only surfaces at render, and an unreadable master chain fails the whole mix under every strictness setting (preview plays dry).
- Effects with a tail (`reverb`, `delay`) make the rendered track longer than its source, so a bed no longer ends exactly at `data-duration`.
- Audio is mixed by film time from `data-start`/`data-duration`/`data-media-start`/rate - it is sample-accurate to that mapping but not sample-locked to a per-frame animation; sync precision is the timeline's, not the audio engine's.
- Base64 audio/video is a lint error (`base64_media_prohibited`); `preload="none"` blocks the renderer (`media_preload_none`); a self-closing `<video />` swallows every following element (`self_closing_media_tag`).
- `crossorigin` on a media element forces a CORS-checked fetch: if the host omits CORS headers, preview shows blank media while the render still works (`media_crossorigin_breaks_preview`).
- Preview needs Chrome-decodable codecs; HEVC/ProRes/AV1 fall back to a cached H.264 proxy, so what you see in preview is a proxy, not the source pixels.
- At most 96 inputs per FFmpeg mix graph; larger mixes are summed in stages, so a very large composition costs extra intermediate WAVs.
- `normalize-audio` refuses remote or out-of-project sources and caps the correction at +12 dB and -1.5 dBTP, reporting how far short it had to stop.

**Known Issues**

- `hevc_preview_codec` (info): HEVC sources render fine but preview depends on the H.264 proxy; if preview is black, proxying was disabled or the source is not local.
- `video_audio_double_source` (error): an `<audio>` and an unmuted `<video>` on the same source at the same time echo; let the video carry its own sound or mute it.
- `video_media_start_at_or_eof` (warning): `data-media-start` at/past the source's playable duration holds the final frame for the whole slot.
- `audio_group_no_members` (error) and `audio_group_holds_elements` (warning): a self-closed `<hf-audio-group />` swallows later siblings, so the bus silently has no members and the elements vanish while `data-hidden` is set.
- `audio_attribute_unreadable` (error): a chain/lane that is not `{version:1, nodes|lanes:[...]}` fails the render; the linter catches only structure, never the effect type or parameter.
- `texture_mask_asset_not_found` / `missing_stylesheet_asset`: a `url()` resolved from the stylesheet's own folder, not the document, so a font or mask 404s and falls back silently.
- VP9 alpha verification trap: `ffprobe` reporting `pix_fmt=yuv420p` does not mean alpha was lost; select the alpha decoder before the input (`ffmpeg -c:v libvpx-vp9 -i overlay.webm -frames:v 1 -pix_fmt rgba out.png`) or check `ALPHA_MODE=1`.
- `truepeak` adds latency equal to `lookahead` plus ~0.3 ms (3.3-10.3 ms); the offline render trims it but live preview plays it, so preview sound sits slightly behind picture.

**Licensing**

HyperFrames itself is Apache-2.0, and the audio graph, effects, presets and analysis are part of it. The content sources are mixed: bundled SFX library ships with the CLI (offline), Openverse SFX are CC0 or CC BY with credits written to `.media/audio/sfx/CREDITS.md`, theSVG/GitHub-avatar/favicon logos are fetched from public sources, and HeyGen music/voice/avatar catalogs plus stem separation are metered account features. `u2net_human_seg` is Apache-2.0 (~168 MB ONNX). The exact licences of the bundled SFX pack and of the Kokoro/Parakeet model weights are not stated in the docs.

### Evidence

**Confidence**

0.87

**Sources**

- https://hyperframes.heygen.com/reference/audio-effects - the authoritative page for the four audio attributes, two clocks, +12 dB ceiling, unity summing, master bus, the 16-effect registry with every parameter range, automatable-parameter rules, 22 presets, 5 jobs, 5 profiles, carve semantics, groups/mute, preview-vs-render table and the four audio lint rules.
- https://hyperframes.heygen.com/reference/html-schema - media attributes (data-media-start vs data-playback-start, data-playback-rate 0.1-10, data-volume, fades, data-has-audio), the muted/data-has-audio rule, nested composition variables, and the 'HyperFrames owns playback' contract.
- https://hyperframes.heygen.com/guides/voice-and-audio - loop attribute semantics, music ducking, carve pointer, the free-vs-HeyGen split for voice/music/SFX/captions.
- https://hyperframes.heygen.com/guides/rendering - output formats, ProRes profiles, input codec proxying (HEVC/ProRes/AV1), transparent-video caveats, VP9 alpha verification recipe, 4:4:4 measurements.
- https://hyperframes.heygen.com/packages/cli - `audio`, `beats`, `transcribe`, `tts`, `normalize-audio`, `remove-background`, `media-treatment`, `media-use`, `capture --video`; LUFS method statement and the `--read` recording modes.
- https://hyperframes.heygen.com/reference/lint-rules - media/audio/font rules: audio_attribute_unreadable, audio_carve_ungrouped_sources, audio_group_*, base64_media_prohibited, media_crossorigin_breaks_preview, media_src_kind_mismatch, self_closing_media_tag, hevc_preview_codec, video_* rules, font_family_without_font_face.
- npm tarball engine-0.9.54.tgz dist/services/audioMixer.js - the actual FFmpeg graph: `atrim`, `volume` (eval=frame), `afade`, `adelay`, `amix=...:normalize=0`, `-ar 48000 -acodec pcm_s16le`, MAX_MIX_INPUTS=96, MIXED_AUDIO_FILENAME='audio.m4a', atempo for rate.
- npm tarball engine-0.9.54.tgz dist/services/audioFxRender.d.ts - the OfflineAudioContext FX pass and why it is not reimplemented as FFmpeg filters.
- npm tarball engine-0.9.54.tgz dist/services/videoFrameExtractor.js - ALPHA_CAPABLE_CODECS {vp9, vp8, prores}, SIDECAR_ALPHA_CODECS, resolveFrameFormat returning PNG for alpha sources.
- npm tarball engine-0.9.54.tgz dist/services/chunkEncoder.js - `-c:a aac -b:a 192k` for mp4/mov, `-c:a libopus -b:a 128k` for webm, PRORES_PROFILES and ENCODER_PRESETS.
- npm tarball core-0.9.54.tgz dist/audioGain.d.ts - MAX_AUDIO_GAIN_DB = 12, MAX_AUDIO_GAIN = 3.981; and audioLeveller.d.ts ('Even Out Levels' is windowed RMS, explicitly not LUFS).
- npm tarball engine-0.9.54.tgz dist/services/fileServer.d.ts - the project is served over a loopback HTTP server to Chrome (no file:// origin), which is why relative paths work and CORS only matters for remote URLs.
- https://hyperframes.heygen.com/guides/video-components and /guides/media - img/SVG/MP4/WebM/MOV import, copy assets into the project, cross-origin and expiring-URL failure modes.

**Open Questions**

- Whether `loop` repeats sample-exactly at the boundary (no gap) for compressed sources - documented as 'repeats until the slot ends' but not measured; a spike should record the seam.
- The bundled SFX library's licence and size, and the Kokoro/Parakeet weight licences - not stated in the docs I could fetch.
- Whether the master bus chain is applied before or after the flat `audio_lowered` trim (the docs describe the truepeak at -3 dBTP then a flat lowering, but not the exact ordering with a user-authored master chain).
- How a 44.1 kHz source is resampled into the 48 kHz mix (FFmpeg default swr settings) and whether that is audible on a phone speaker - unmeasured.
- Whether `data-playback-rate` on audio produces pitch-shifted audio (atempo preserves pitch) or true varispeed; the mixer uses atempo, so pitch is preserved, but this is inferred from the filtergraph rather than documented.
- Real cost of the per-clip OfflineAudioContext pass on a 28 s composition with 5 audio tracks - no published benchmark.

### Other Info

**Output Formats**

- MP4 / H.264 (yuv420p; yuv444p with `--chroma-subsampling 444`) + AAC 192k, faststart - H.265 10-bit BT.2020 instead when HDR is active.
- WebM / VP9 yuva420p with `alpha_mode=1` + Opus 128k - the only format whose alpha browsers decode.
- MOV / ProRes 4444 yuva444p10le with alpha + AAC 192k; `--prores-profile hq|422|lt|proxy` switches to 4:2:2 without alpha.
- PNG sequence (RGBA, `frame_000001.png`, ...) + an `audio.m4a` sidecar when the composition has sound; GIF (no audio, binary transparency, <=30 fps).
- HLS VOD directory: `master.m3u8`, `video.m3u8` + `video_NNNNN.ts`, and `audio.m3u8`/`audio_NNNNN.ts` when there is sound (stream copy of the H.264/AAC encode).
- Intermediate audio artefacts: per-clip extracted WAV, trimmed WAV, FX-processed WAV, group WAVs, `master-sum.wav`, and the final `audio.m4a` (16-bit PCM 48 kHz stereo at the mix stage).
- Analysis artefacts: `beats/<audio>.json`, transcript.json / transcript.srt / .vtt, `audio` JSON reports (moments, onsets, loudness, silence, envelope), `caption_audit`-style CPS data is Studio-side.

**Requirements**

- FFmpeg and ffprobe on PATH for every audio/video operation (mix, extraction, probe, normalize, beats, remove-background); the CLI's `media-use doctor` fails only when ffmpeg, ffprobe or the bundled sound library is missing.
- A Chromium build for anything that applies an FX chain at render time: the chain runs in an OfflineAudioContext inside the same headless browser used for frame capture.
- Node.js >= 22 for the CLI and packages.
- For alpha output, a composition that leaves `html`/`body` backgrounds unpainted - an opaque full-frame background is encoded as visible pixels.
- For HEVC/ProRes/AV1 source playback in preview only: auto-proxying enabled (Chrome cannot decode them reliably); renders do not need it because FFmpeg pre-decodes.
- For remote media: public HTTPS URLs reachable from the render host; for cloud rendering, media is offloaded to public unsigned HeyGen asset URLs under a 200 MB zip cap.

**Performance And Cost**

Documented facts, not measured here: the FX pass runs once per clip/group WAV in headless Chrome (OfflineAudioContext), so cost scales with number of processed tracks x duration, not with frame count; every source video frame is extracted before capture by default (~25 GB/min raw RGBA at 1080p30 if frames land on disk), and `HF_VIDEO_FRAME_WINDOW=true` bounds it per segment. `audio --read --quality best` uploads a 192 kb/s MP3 to HeyGen for 6-stem separation (30-minute default stems timeout). Analysis is local and free; `remove-background` peaks around 1.5 GB RAM with u2net. No published figures for mix time on a 28 s composition - measure on the target host.

---

<a id="cli-and-programmatic-api"></a>
## CLI and Programmatic API

### Basic Info

**Title**

CLI and Programmatic API

**Domain**

Developer interface: command-line surface + Node.js packages

**One Liner**

HyperFrames exposes every operation as a non-interactive CLI command with a JSON envelope, exit codes 0/1/2, and Node packages (@hyperframes/core, /producer, /player) for embedding parsing, rendering and playback.

**Official Sources**

- https://hyperframes.heygen.com/packages/cli
- https://hyperframes.heygen.com/developers/cli
- https://hyperframes.heygen.com/packages/core
- https://hyperframes.heygen.com/packages/producer
- https://hyperframes.heygen.com/packages/player
- https://hyperframes.heygen.com/packages/engine
- https://github.com/heygen-com/hyperframes
- https://www.npmjs.com/package/hyperframes

**Maturity**

beta

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

A script or agent can scaffold a project, lint and browser-check it, capture stills for visual review, edit the timeline by verb, render MP4/WebM/MOV/GIF/PNG/HLS, upload for review, and render in the cloud - all headlessly and without prompts; a Node service can do the same in-process through createRenderJob/executeRenderJob or the Producer HTTP server.

**Technical Mechanism**

The CLI is a thin citty command layer over the packages. `render` builds a RenderRequest, calls `createRenderJob` + `executeRenderJob` from @hyperframes/producer, which compiles the HTML (inlining assets, localizing remote media and fonts), serves the project over a loopback HTTP server, drives headless Chrome through CDP - seeking each frame with `window.__hf`/`__timelines` and capturing via BeginFrame (Linux) or screenshots - extracts source video frames with FFmpeg, mixes audio with FFmpeg + an OfflineAudioContext FX pass, and encodes with FFmpeg. The CLI adds progress rendering (TTY bar vs `@hf-progress` machine lines), the `_meta` update envelope, exit-code mapping, and a per-user render lock file so two local renders never starve each other. Lint/check/snapshot/keyframes reuse @hyperframes/lint and the same capture session without the encoder.

**Text And Motion Capabilities**

The API surface itself does not draw type, but it is the inspection and control plane for typography and motion: `snapshot --zoom '#selector' --zoom-scale 3` gives glyph-level pixel crops to judge letterfit, weight and stroke; `check` returns a WCAG AA contrast section per sampled frame; `keyframes --shot` writes an onion skin of an element's real positions so easing and overlap are visible; `--at`/`--range` seek to exact film seconds (frame-quantised by the engine) for per-beat review; `timeline set '#captions' rate=0.8` and `move/trim/split` retime caption clips without touching HTML; `render --variables` swaps copy per SKU while the animation contract stays byte-identical. Frame accuracy is the engine's (seek to `quantizeTimeToFrame(t)`), so CLI-driven reviews are reproducible.

**Output Formats**

- stdout text (human), `--json` envelopes with `_meta`, and non-TTY `@hf-progress` NDJSON-ish lines.
- `render --format mp4` (H.264 + AAC 192k, faststart) - the default delivery file.
- `render --format webm` (VP9 + yuva420p alpha + Opus 128k), `--format mov` (ProRes 4444 + alpha + AAC), `--format gif` (no audio, <=30 fps), `--format png-sequence` (RGBA frames + audio.m4a sidecar), `--format hls` (master.m3u8 + .ts segments).
- `snapshot` writes 1920x1080 PNGs (or cropped/zoomed crops) into `<project>/.hyperframes/artifacts/snapshots/<id>/`, plus contact sheets; `keyframes --shot` writes an onion-skin PNG.
- `compare`/`grade-compare` write a labelled PNG grid; `cloud get` returns presigned `video_url` and `thumbnail_url`.
- `--json` documents for lint (errorCount/warningCount/infoCount/findings/unmountedCompositions), check (ok/browserSkipped/scope/lint/runtime/layout/motion/contrast/craft/audio/hdr/snapshots), snapshot (frames[] with path+time+role), add (installed[]/errors[]), catalog rows (name/type/title/description/tags/dimensions/duration/preview).

### Usage & API

**Setup And Install**

Requirements: Node.js >= 22 (package `engines`), FFmpeg + ffprobe on PATH, and a Chromium build (the CLI resolves a bundled headless-shell, or uses `HYPERFRAMES_BROWSER_PATH`).

```bash
# no install
npx hyperframes render

# global install
npm install -g hyperframes
# or per project
npm install -D hyperframes

# libraries
npm install @hyperframes/core @hyperframes/producer @hyperframes/player

# verify the machine
npx hyperframes doctor --json
```

Version verified during research: hyperframes 0.9.54 (npm dist-tag `latest`), all four packages versioned in lockstep.

**Minimal Code Example**

```bash
# CLI: scaffold a 1080x1920 project, gate it, render it
npx hyperframes init sku-reel --resolution portrait --non-interactive
cd sku-reel
npx hyperframes lint . --json
npx hyperframes render . --fps 60 --quality looks --output renders/reel.mp4
```

```js
// Node service: same render, in-process, with progress and cancellation
import { createRenderJob, executeRenderJob, RenderCancelledError } from "@hyperframes/producer";

const job = createRenderJob({
  fps: { num: 60, den: 1 },      // exact rational, not 59.94 rounding
  quality: "high",               // draft | standard | high
  format: "mp4",
  entryFile: "index.html",
  variables: { price: "$330", battery: "77%" },
  strictVariables: true,
});

const controller = new AbortController();

try {
  await executeRenderJob(
    job,
    "./sku-reel",
    "./out/reel.mp4",
    (j, message) => console.log(j.status, Math.round(j.progress), message),
    controller.signal,
  );
  console.log(job.outcome, job.duration, job.totalFrames, job.outputPath);
} catch (err) {
  if (err instanceof RenderCancelledError) console.error("cancelled:", err.reason);
  else throw err;
}
```

```js
// Player: embed the same composition in a review page
import "@hyperframes/player";
const p = document.querySelector("hyperframes-player");
p.addEventListener("ready", (e) => console.log("duration", e.detail.duration));
p.seek(2.5);
```

**Key Config Options**

- `--quality looks` (default) = encoder preset `standard` + explicit CRF 16; `draft` = ultrafast/CRF 28; `standard` = medium/CRF 18; `high` (`delivery`) = fast/CRF 15. Source: QUALITY_ALIASES in the CLI bundle and ENCODER_PRESETS in @hyperframes/engine.
- `--fps` accepts 1-240 or an ffmpeg rational (`30000/1001`); default is the root `data-fps`, else 30. GIF output is forced to <=30 fps.
- `--format` mp4|webm|mov|gif|png-sequence|hls. Transparency: webm (VP9 alpha) and mov (ProRes 4444). `--prores-profile hq|422|lt|proxy` drops alpha.
- `--workers` 1-24, each ~256 MB Chrome; auto weighs cores, RAM, frame count and cost. `--low-memory-mode` auto-enables at <=8 GB host RAM (reads host RAM, not cgroup - set PRODUCER_LOW_MEMORY_MODE in containers).
- `--strict` fails on lint errors; `--strict-all` fails on errors and warnings; `--strict-variables` fails on undeclared/mistyped variable keys.
- `--resume` reuses finished capture segments after re-validating them and is documented as byte-identical to an uninterrupted render; segments live in `renders/.hf-segments/<planHash>/`.
- `--variables '{"title":"Q4"}'` or `--variables-file vars.json` merges over `<html data-composition-variables='[...]'>` defaults; read in the page with `window.__hyperframes.getVariables()`. Types: string, number, color, boolean, enum, font, image.
- `--batch` takes a JSON array or `{"rows":[...]}` and renders one file per row; `--batch-concurrency` default 1; `--batch-fail-fast` default off; with `--json` prints exactly one final document.
- Timeouts (three separate budgets, all with ms env fallbacks): `--browser-timeout` seconds (default 60) / PRODUCER_PAGE_NAVIGATION_TIMEOUT_MS; `--player-ready-timeout` ms (default 45000) / PRODUCER_PLAYER_READY_TIMEOUT_MS; `--protocol-timeout` ms (default 300000, auto-scales with pixel area) / PRODUCER_PUPPETEER_PROTOCOL_TIMEOUT_MS.
- Env: HYPERFRAMES_NO_UPDATE_CHECK=1 (no version check/install/notice), HYPERFRAMES_NO_AUTO_INSTALL=1, HYPERFRAMES_SKIP_SKILLS=1, HYPERFRAMES_AGENT=1, HYPERFRAMES_CACHE_DIR, HYPERFRAMES_RENDER_LOCK=off, HYPERFRAMES_CHROME_SANDBOX=0|1, HYPERFRAMES_CHROME_ENV_PASSTHROUGH, PRODUCER_ENABLE_STREAMING_ENCODE, HF_SEGMENTED_CAPTURE, HF_SEGMENT_FRAMES.
- Producer server: port default 9847, host 127.0.0.1 unless PRODUCER_HOST; any non-loopback bind requires authToken/PRODUCER_AUTH_TOKEN (Bearer on every route but /health); request `projectDir` must resolve inside PRODUCER_PROJECTS_DIR, `outputPath` inside PRODUCER_RENDERS_DIR (default /tmp); PRODUCER_RESTRICT_PAGE_NETWORK=true fences the render browser to its own project server + public IPv4.
- RenderJob fields that map 1:1 to CLI flags: fps (Fps rational), quality, format, entryFile, crf, videoBitrate (mutually exclusive), keyframeIntervalSeconds, maxBitrate, bufferSize, chromaSubsampling '420'|'444', proresProfile, hlsSegmentSeconds, gifLoop, motionBlur, pageMotionBlur 'off', workers, maxRenderMs, useGpu, debug, strictness 'strict'|'best-effort', hdrMode auto|force-hdr|force-sdr, outputResolution, variables, strictVariables, resumeSegments, keepSegments, logger, producerConfig.
- `_meta` in every --json payload: version, latestVersion, updateAvailable, checkedAt, skillsOutdated, skillsMissing, updateNotice; absent under CI, HYPERFRAMES_NO_UPDATE_CHECK=1 or an agent plugin; `--json` never makes its own network request.

### Integration

**Pipeline Fit**

Augments, and in the overlay role replaces, the current Python+FFmpeg renderer - not the whole stack. Today render_video.py owns beat-synced cuts, pill/caption layout and the audio bed. The cheapest adoption is to keep that pipeline and use only the CLI as (a) a `snapshot`/`check`/`keyframes` visual gate and (b) a renderer for transparent text/motion overlays (webm/mov) that FFmpeg composites over the existing footage. A full adoption would move every caption, pill and transition into a HyperFrames composition and call `render` once per SKU, leaving Python as the JSON/asset producer.

**Python Integration**

Primary path is subprocess, not a Node bridge - no native bindings, no IPC protocol to maintain:

```python
import json, re, subprocess

PROGRESS = re.compile(r'^@hf-progress (\{.*\})$')

def render(project: str, out: str, timeout: int = 900) -> dict:
    proc = subprocess.run(
        ["npx", "hyperframes", "render", project,
         "--fps", "60", "--quality", "looks", "--output", out, "--quiet"],
        capture_output=True, text=True, timeout=timeout, cwd=project,
        env={**os.environ, "HYPERFRAMES_NO_UPDATE_CHECK": "1"},
    )
    if proc.returncode == 2:
        raise ValueError(f"CLI usage error: {proc.stderr.strip()}")
    if proc.returncode == 1:
        raise RuntimeError(f"render failed: {proc.stderr.strip()[:2000]}")
    return {"out": out, "log": proc.stderr}
```

Caveats: (1) progress lines go to **stdout** and are only emitted when stdout is not a TTY - keep `capture_output=True` or pipe stdout, and keep `--quiet` off if you want `@hf-progress`; (2) human progress goes to stderr, so parse stderr for the first exact error rather than the final 'render failed'; (3) `--json` is not accepted by every command and a command that does not support it exits 2; (4) `npx` adds ~1 s and may fetch, so install the CLI once in the image (`npm i -g hyperframes@0.9.54`) and call the `hyperframes` binary; (5) one local render per user account is serialised by a lock file - run parallel SKUs with separate `HYPERFRAMES_CACHE_DIR` values or use `--batch`. For in-process rendering without a subprocess, run a tiny Node sidecar that imports @hyperframes/producer and exposes POST /render (or `startServer`) - the Producer HTTP API already returns JSON and supports SSE progress.

**Data Injection**

Two mechanisms, both declarative so the composition HTML never needs rewriting per SKU:

1. Composition variables: declare on the root `<html data-composition-variables='[{"id":"price","type":"string","label":"Price","default":"$330"}]'>`, bind with `data-var-text` / `data-var-src` / CSS `var(--price)`, then pass `--variables '{"price":"$330 USDT"}'` or `--variables-file sku.json` per render. `--strict-variables` turns an undeclared or mistyped key into a failure instead of a warning.
2. Word timings and captions: write `transcript.json` (`[{text,start,end}]`) with `npx hyperframes transcribe`, or generate the caption clips yourself from your existing Whisper alignment: one element per word with `data-start` (absolute film seconds) and `data-duration`.

For many SKUs at once, `--batch rows.json` renders one output per JSON row from a single composition, which is the closest analogue to the current `batch_run.py`. Note that render length comes from the root `data-duration`, which the compiler reads before composition scripts run - a variable cannot change duration for that render, so build one HTML per duration or per cut pattern.

**Footage And Asset Handling**

Existing MP4s are consumed as `<video src="assets/01_back_cameras.mp4" muted playsinline data-start="0" data-duration="1.525" data-media-start="3.20">` - `data-media-start` is the trim in-point, so the current `edit_plan.json` cut list maps one-to-one onto video elements without re-encoding. The original files stay the render source: FFmpeg pre-decodes frames, so HEVC and ProRes inputs render fine even though Chrome cannot play them (preview uses a cached H.264 proxy). Fonts must be project-local `@font-face` files (`.woff2`) or a bundled/Google family; system fonts alias under distributed rendering. Alpha footage (VP9/VP8 WebM with sidecar alpha, ProRes 4444) is extracted as PNG and composited with its alpha intact.

**Migration Effort**

medium

**Integration Steps**

- Pin the CLI in the render image: `npm i -g hyperframes@0.9.54`, install FFmpeg/ffprobe, run `hyperframes doctor --json` in the Docker build and fail the build on a non-zero result.
- Start read-only: run `hyperframes lint` and `hyperframes check --json` against a generated 1080x1920 composition from one existing SKU and compare its findings with your own qa_check.py gates. This costs seconds and validates the contract (data-start on every clip, muted vs data-has-audio, unique ids).
- Generate composition HTML from the existing sku.json with a Jinja/JSON template that emits `<video>` elements per cut (`data-media-start` = in-point) and caption elements per word, and inject all copy through `data-composition-variables` rather than string interpolation.
- Replace the vision-review loop with `hyperframes snapshot --at <t1,t2,t3> --json` into `.hyperframes/artifacts/snapshots/`, and use `keyframes --selector '#captions' --shot` when a kinetic caption looks wrong; keep contact_sheet.png for the final human check.
- Render the overlay path first: one transparent `--format webm` (or `--format mov` for ProRes 4444) per SKU and composite it over the existing FFmpeg cut in a single `overlay` filter; this proves sync and alpha without touching the cut logic.
- Only then move the full frame into HyperFrames: `render --fps 60 --quality looks --output renders/<sku>.mp4`, then re-run the existing LUFS/true-peak gate with qa_check.py on the output file (HyperFrames guarantees <= -1 dBTP but not your -14 LUFS target).
- Wire the Python driver to the CLI contract: exit 0 = ok, exit 2 = usage bug (raise loudly, never retry), exit 1 = render failure (read the first stderr error, then `--debug` for render.log).
- For SKU fan-out, separate `HYPERFRAMES_CACHE_DIR` per concurrent worker or switch to `--batch` with `--batch-concurrency`; never run two renders against one cache dir expecting parallelism.

### Constraints

**Requirements**

- Node.js >= 22 (declared in package.json engines); Node 20 is not supported.
- FFmpeg and ffprobe on PATH - used for video frame extraction, audio mixing, encoding, probing and (optionally) proxy generation. `doctor` fails when they are missing.
- A Chromium/Chrome build: bundled headless-shell, system Chrome, or HYPERFRAMES_BROWSER_PATH. Sandbox is kept on macOS/Windows and for non-root Linux outside containers; dropped as root/in Docker/in Kubernetes/Lambda.
- Enough disk for raw frames when the disk path is used: ~25 GB per minute at 1080p30 (frames x w x h x 4). Streaming encode avoids it for single-worker mp4/mov.
- Host RAM is read for low-memory auto-detection - containers must set PRODUCER_LOW_MEMORY_MODE explicitly.
- Network access for the version check (unless disabled), for `add`/`catalog` registry fetches, for remote media localization, and for any cloud command.

**Limitations**

- Three exit codes only (0/1/2); there is no distinct code for 'render failed on frame 731' vs 'no disk' - the discriminator is the error text and, under --json, `error.code`.
- `--json` is not universal: unsupported commands reject it with exit 2, so a wrapper must know per-command whether JSON is available.
- `@hf-progress` only appears when stdout is NOT a TTY, and stage objects carry `code|done|total|pct` but no ETA; there is no documented completion event besides process exit.
- Progress and warnings go to stderr while machine-progress lines go to stdout - a naive `subprocess.run(capture_output=True)` reader must parse both streams or it loses the human error.
- One local render per user account: a second render waits for the first (lock file in the cache dir). Multi-SKU fan-out needs HYPERFRAMES_RENDER_LOCK=off (accepting CPU starvation) or separate cache dirs.
- The CLI installs and can auto-update itself (owned npm/bun/pnpm installs), which is non-deterministic in CI; pin the version and set HYPERFRAMES_NO_UPDATE_CHECK=1 / HYPERFRAMES_NO_AUTO_INSTALL=1.
- `init` is the only scaffolder and it installs AI skills by default; `--skip-skills` or HYPERFRAMES_SKIP_SKILLS=1 is required in hermetic builds.
- There is no `dev` command (it is `preview`) and no `still` command (it is `snapshot`); `validate`, `inspect` and `layout` are deprecated aliases that only set `_meta.deprecated: true`.
- Timeline edit verbs refuse rather than guess: an overlapping move exits 2 unless `--overwrite`, and `--snap` requires `data-fps` on the project.
- The Node API is pre-1.0 and explicitly unstable: `hyperframes/api` is documented as 'for HyperFrames' own apps; it may change between minor versions', and RenderConfig is a large flat object with mutually exclusive fields (crf vs videoBitrate; chromaSubsampling 444 rejected with useGpu/hdr/other formats).
- `executeRenderJob` resolves to `void` and reports through mutation of the job plus the callback - there is no returned result object, so a wrapper must read `job.outcome`, `job.error` and `job.perfSummary` after the await.
- Producer HTTP server binds 127.0.0.1 by default and refuses non-loopback binds without a bearer token; `debug: true` is refused unless explicitly allowed.
- Cloud rendering uploads the project as a zip capped at 200 MB (direct-upload limit); media over the cap is offloaded to public, unsigned HeyGen asset URLs - i.e. shareable by anyone with the link.

**Known Issues**

- `add` with a tag installs what it can and exits 1 when any block in the tag fails; `--json` then reports `ok: false` with per-block `errors[]` and the successful `installed[]` - a naive wrapper treats a partial install as total failure.
- Deprecated `validate`/`inspect`/`layout` still run but print a deprecation line on stderr; automation that greps stderr for errors can trip on it.
- `check` returns `browserSkipped: true` with empty `layout`/`motion`/`contrast` sections when the browser never ran - empty sections are not 'clean', and a wrapper that only looks for findings will pass a project that was never inspected.
- A master audio chain that does not parse fails the whole mix with `audio_processing_failed` under every strictness setting, while preview plays dry - `check` cannot catch it statically beyond `audio_attribute_unreadable`.
- `render --start/--end` (partial range) is SDR-only and limited to 24/25/30/60 fps MP4; it is not a general 'render this segment' switch.
- On macOS/Windows multi-worker capture is screenshot-based and writes frames to disk unless HF_CAPTURE_PARALLEL_STREAM=true, so the documented streaming savings do not apply there.
- WSL2 renders may not reach the GPU (documented in help.md: 'the render is slow under WSL2'), which matters for this machine.

**Licensing**

Apache-2.0 for hyperframes, @hyperframes/core, /engine, /producer, /player, /studio and /shader-transitions - no per-render fee and no commercial-use threshold. The paid boundary is around hosted services and content: HeyGen cloud rendering (credits), HeyGen voice/music/avatar catalogs, and two Pro skills (`/reverse-any-video`, `/style-sampling`). The CLI itself phones home for the version check and anonymous telemetry, both opt-out (HYPERFRAMES_NO_UPDATE_CHECK, HYPERFRAMES_NO_TELEMETRY / DO_NOT_TRACK).

### Evidence

**Confidence**

0.85

**Sources**

- https://hyperframes.heygen.com/packages/cli - the complete command/flag reference (3,243 lines); source for init/add/catalog/capture/transcribe/tts/remove-background/media-use/beats/audio/normalize-audio/preview/lint/check/snapshot/keyframes/timeline/history/render/benchmark/doctor/skills/auth/cloud/lambda/share and for the `_meta` envelope, exit codes and unattended rules.
- https://hyperframes.heygen.com/packages/core - @hyperframes/core entry points (compiler, variables, composition-contract, runtime) and the parse/generate/validate/GSAP-adapter snippets.
- https://hyperframes.heygen.com/packages/producer - createRenderJob/executeRenderJob usage, output formats, HLS, reference_background, cancellation, startServer and the security knobs (PRODUCER_PROJECTS_DIR, PRODUCER_RESTRICT_PAGE_NETWORK).
- https://hyperframes.heygen.com/packages/player - full attribute table, JS API and 14-event table for <hyperframes-player>.
- https://hyperframes.heygen.com/guides/rendering - format/quality/CRF mapping, 4:4:4 measurements, input codec handling, provenance tags, 4K/HDR constraints.
- https://www.npmjs.com/package/hyperframes (registry JSON) - version 0.9.54 for all packages, `engines.node >= 22`, bin entries, exports map (`./api`), dependency list (citty, hono, puppeteer-core, esbuild, fontkit).
- npm tarball hyperframes-0.9.54.tgz (dist/) - QUALITY_ALIASES (looks = standard+crf16), RENDER_FORMATS, `@hf-progress` emitter in ui/progress.ts, failUsage/unknown_flag error shape, renderMachineProgress guard on TTY.
- npm tarball producer-0.9.54.tgz (dist/) - RenderConfig/RenderJob/RenderStageCode/ProgressCallback/executeRenderJob signatures, executeRenderJob returning Promise<void>, server.d.ts route list and port 9847 default, RenderConfigInput = Omit<RenderConfig,'fps'> & {fps: FpsInput}.
- npm tarball core-0.9.54.tgz (package.json exports) - 110 subpath exports including ./compiler, ./variables, ./runtime, ./audio-fx, ./beats, ./safe-areas.
- npm tarball engine-0.9.54.tgz (dist/) - ENCODER_PRESETS (draft 28/ultrafast, standard 18/medium, high 15/fast), createFileServer, audioMixer and chunkEncoder signatures.
- https://hyperframes.heygen.com/reference/html-schema - the attributes the CLI/API validate and write (data-start, data-duration, data-media-start, variables, nested compositions).

**Open Questions**

- Exact JSON schema of `render --json` for a single (non-batch) render: the docs only document `--json` for `--batch`, and the single-render path prints human progress plus `@hf-progress` lines. A spike should capture the full payload.
- Whether `@hf-progress` lines are emitted when `--quiet` is passed (the emitter is inside the same progress callback that `--quiet` disables), and whether `--json` on render implies quiet.
- The precise `RenderConfigInput`/`RenderJob` optionality for every field is only visible in the .d.ts; the published docs show a small subset, so generated TypeScript clients should be built from the d.ts, not the docs.
- How much of `@hyperframes/player` works headless in Node (it is a DOM custom element with a sandboxed iframe) - assumed browser-only, unverified.
- Whether `check --json` guarantees a stable schema across 0.9.x minor versions; only the section names are documented.
- Real wall-clock render time for a 28 s 1080x1920@60 composition with ~15 video cuts on this WSL2 host - not measured, and the docs give no vertical-60fps benchmark.

### Other Info

**Key Features**

- Single binary entry `hyperframes` (bin/hyperframes.mjs, citty-based) with ~60 subcommands; `npx hyperframes <cmd>` works with no install, `npm i -g hyperframes` installs it.
- Non-interactive by default: a missing required flag fails immediately with a usage example; only `init`, `catalog --human-friendly` and a first `share` prompt on a TTY.
- Unattended detection: no TTY, CI, inside a coding agent, or `HYPERFRAMES_AGENT=1` makes any consent prompt exit 2 and name the flag to pass (`--yes`).
- `--json` on most commands returns a machine-readable payload wrapped with a `_meta` object: `{version, latestVersion, updateAvailable, checkedAt, skillsOutdated, skillsMissing, updateNotice}` so a script can detect an outdated CLI without a second call.
- Failures under `--json` print exactly one object: `{"ok": false, "error": {"code", "message", "hint"}}`; `lint` adds `findings`, timeline refusals add `reason` and `fix`.
- Exit codes are a fixed three-value contract: 0 success, 1 ran-and-failed, 2 the command line itself is wrong (unknown command/flag, missing or invalid value).
- Render progress is machine-readable on non-TTY stdout: one line per update `@hf-progress {"code":"capture","done":N,"total":M,"pct":42}` with codes compile|extract_video|process_audio|start_browsers|capture|encode|assemble.
- Project directory is the first positional argument for most commands (default: cwd); `add`, `transcribe`, `feedback`, `skills`, `history` and timeline edit verbs take `--dir` instead.
- `render --batch` renders one file per row of a JSON array (or `{rows:[...]}`) and prints one final JSON result document; `--batch-concurrency` and `--batch-fail-fast` control fan-out.
- `timeline` is a read/write timeline API from the shell: move, trim, split, delete, set volume/rate/track, duplicate, apply (all-or-nothing JSON edit list), undo by receipt, ids.
- Every timeline edit supports `--plan` (print result + diff, write nothing) and refuses an invalid edit with exit 2 before touching a file.
- `snapshot` is the 'still frame' command: `--at 2.9,10.4`, `--frames N`, `--range`, `--zoom '#headline'` (raised DPR crop), `--angle` orthographic 3D, `--against ref.mp4` pair comparison, `--review` contact sheets for beats and seams.
- `check` is one browser session that runs lint + runtime + layout + motion + WCAG AA contrast + craft + audio + HDR audits and returns sections in JSON, with `--strict` promoting warnings to failures.
- `keyframes` reports the GSAP/CSS/Anime.js keyframes actually detected and can write an onion-skin PNG of one element's motion (`--selector`, `--shot`, `--samples`, `--layout path|strip`, `--angle`).
- Node render API: `createRenderJob(config)` builds a job, `executeRenderJob(job, projectDir, outputPath, onProgress?, abortSignal?, assertActive?)` runs it, `RenderCancelledError` on abort, `RenderQualityError` in strict mode.
- Producer also ships an HTTP service: `startServer({port})` with POST /render (blocking), POST /render/stream (SSE), GET /render/queue, POST /lint, GET /health, GET /outputs/:token.
- @hyperframes/core exposes parse/generate/compile surfaces (`parseHtml`, `extractCompositionMetadata`, `generateHyperframesHtml`, `compileHtml`, `bundleToSingleHtml`, `validateHyperframeHtmlContract`, `validateVariables`, `getVariables`, `createGSAPFrameAdapter`).
- @hyperframes/player is a `<hyperframes-player>` custom element with a media-like JS API (play/pause/seek/currentTime/playbackRate/muted/volume/duration/paused/ready) and 13 documented events, rendering the composition in a sandboxed shadow-DOM iframe.
- `hyperframes/api` (a Node subpath export of the CLI) exposes `transcribe()` and `removeBackground()` as library calls with typed errors and progress callbacks - no argv, no printing, no exit.
- Auth is a first-class command family: `auth login` (OAuth via browser, or `--device` for SSH, or `--api-key` verified against GET /v3/users/me), `auth status --json`, `auth refresh`, `auth logout`.
- Remote rendering has three drivers with the same credential: `cloud render` (HeyGen-hosted), `lambda render`/`render-batch` (your AWS), `cloudrun` (your GCP).

**Cli Or Api Surface**

- init [dir] --example/-e, --resolution, --video/-v, --audio/-a, --tailwind, --non-interactive, --skip-transcribe, --keep-original, --model, --language, --skip-skills
- add <name|tag> --dir, --json, --no-clipboard (installs a block or component, resolves registryDependencies transitively, checks minCliVersion, keeps successful installs and exits 1 if any block in a tag failed)
- catalog --type block|component, --tag, --query, --on-device, --json, --human-friendly, --yes
- capture <url> -o/--output, --timeout, --capture-budget, --skip-assets, --skip-vision, --max-screenshots, --local-file, --local-root, --video <folder> --list/--index/--video-url, --json
- transcribe <media|transcript> --dir/-d, --engine auto|parakeet|whisper|heygen, --model, --language, --to srt|vtt, --output, --preserve-cues, --optional, --timeout, --json
- models list|install parakeet [--json]
- tts "text"|file --output/-o, --voice/-v, --speed, --lang, --list, --json
- remove-background <media> -o, --background-output/-b, --device auto|cpu|coreml|cuda, --quality fast|balanced|best, --info, --json
- media-treatment --capabilities, --capability <id>, --all, --project, --file, --selector, --selector-index, --grading <json>, --apply, --analyze, --clear, --dry-run, --json
- media-use resolve|candidates|adopt|analyze|voices|avatars|doctor|stats --type, --intent, --entity, --project/-p, --candidates, --reuse <sha>, --from <file>, --params, --for, --analyze, --local-only, --provider, --voice-id, --avatar-id, --days, --json
- beats [dir|file] --json  ->  {ok, file, count, bpm, beatTimes?}
- audio <files...> | --element <sel> | --at <s> | --range a,b  --only onsets,beats,downbeats,bpm,sections,describe  --silence-db, --place-at, --skip-file-seconds, --read, --layer music|sfx|voice, --quality, --transcript, --json
- normalize-audio --reference <id> --target <id> [--write] [--tolerance 0.5] [--json]  (EBU R128 integrated match, respects data-volume, caps at +12 dB / -1.5 dBTP)
- preview [dir] --port (3002), --open/--no-open, --background, --foreground, --status, --stop, --list, --kill-all, --force-new, --proxy/--no-proxy, --browser-gpu/--no-browser-gpu, --browser-path, --selection, --context [--context-fields, --context-detail], --json
- present | play  (presentation mode over a running preview)
- publish / open / catch-up  (legacy publication + project resume)
- lint [dir] --json, --verbose, --include-unmounted, --lint-verbose
- check [dir] --json, --snapshots, --at, --strict, --proxy, --browser-gpu, --include-unmounted
- snapshot [dir] --at, --frames, --range, --composition, --output/-o, --end/--no-end, --keep, --zoom, --against, --zoom-scale, --angle, --review, --describe, --timeout, --json
- keyframes [dir] --selector, --runtime gsap|css|anime|all, --shot <png>, --samples, --layout, --from, --to, --angle, --fit/--no-fit, --ghost, --json
- compare <dirs...> --at, --labels, --out, --cols, --timeout, --json  |  grade-compare --for, --grades, --luts, --project, --out, --baseline/--no-baseline, --json
- validate | inspect | layout  (deprecated; still run, print a deprecation line, set _meta.deprecated: true; superseded by `check`)
- reverse | measure | diff  (rebuild-an-existing-video tooling)
- timeline [dir] --json  |  timeline move|trim|split|delete|set|duplicate|apply|undo|ids ... --plan, --overwrite, --snap, --at, --dir, --json
- history [dir] --since, --who, --limit, --json; history show|undo|restore|peek|pin|begin|end
- render [dir] --output/-o, --composition/-c, --format, --fps/-f, --quality/-q, --resolution, --crf, --video-bitrate, --keyframe-interval, --start, --end, --max-bitrate, --buffer-size, --vp9-cpu-used, --chroma-subsampling, --gif-loop, --hls-segment-seconds, --video-frame-format, --hdr, --sdr, --page-motion-blur, --workers/-w, --low-memory-mode, --gpu, --browser-gpu, --page-side-compositing, --experimental-fast-capture, --frames-cache-dir, --max-concurrent-renders, --best-effort/--no-best-effort, --wait/--no-wait, --strict/--strict-all, --strict-variables, --variables, --variables-file, --batch, --batch-concurrency, --batch-fail-fast, --resume, --keep-segments, --docker, --debug, --quiet, --skill, --json
- benchmark [dir] --runs 1-20 [--json]
- doctor [--json] | clean | info [--media] | compositions | update | upgrade | browser | docs | feedback | telemetry | skills [check|update] | usage | billing | templates | figma | events
- auth login [--device|--api-key] | auth status [--json] | auth refresh | auth logout [--keep-api-key] [--yes]
- share --public|--team|--invite-only|--private, --include-source, --video-only, --update, --status, --manage, --space, --wait-seconds, --yes, --json
- cloud render [dir] --fps, --quality, --format mp4|webm|mov, --resolution 1080p|4k, --aspect-ratio, --composition/-c, --variables, --variables-file, --strict-variables, --title, --output/-o, --dry-run, --no-wait, --callback-url, --callback-id, --asset-id, --url, --poll-interval, --max-wait, --idempotency-key, --json
- cloud list --limit, --token, --all; cloud get <render_id>; cloud delete <render_id> --no-confirm
- lambda deploy|sites create|render|render-batch|progress|destroy|policies; cloudrun deploy|render
- @hyperframes/core: parseHtml, extractCompositionMetadata, generateHyperframesHtml, generateHyperframesStyles, serializeGsapAnimations, getVariables, validateVariables, formatVariableValidationIssue, createGSAPFrameAdapter, compileHtml, bundleToSingleHtml, compileTimingAttrs, injectDurations, extractResolvedMedia, validateHyperframeHtmlContract
- @hyperframes/producer: createRenderJob, executeRenderJob, renderSpan (partial-range), startServer, createProducerApp, createRenderHandlers, resolveConfig, DEFAULT_CONFIG, createConsoleLogger, RenderCancelledError, RenderQualityError, plan/renderChunk/assemble (distributed), localizeRemoteMediaSources, localizeRemoteImageSources, localizeRemoteFontFaces, prepareAnimatedGifInputs, prepareHeifImageInputs
- @hyperframes/player: <hyperframes-player> attributes (src, type, srcdoc, width, height, controls, autoplay, loop, muted, volume, poster, playback-rate, audio-src, audio-locked, assets-loading-ui, low-power-idle, disable-click-to-play, no-load-timeout, range-start, range-end); methods play/pause/seek/setRuntimeData/setColorGrading/stopMedia/scenes; events ready, durationchange, resize, timeupdate, play, pause, ended, rangeclamped, ratechange, volumechange, scenes, error, playbackerror
- hyperframes/api (Node): transcribe(options), removeBackground(options), offloadHeavyMedia(...) - documented as 'for HyperFrames' own apps; may change between minor versions'

**Performance And Cost**

Documented, not measured here: 4K is roughly 4x slower per frame with a 3-5x larger file; `--chroma-subsampling 444` took about 2x as long for a text/UI composition and about 3.5x for full-frame footage; page-side WebGL compositing is about 6x faster for SDR shader transitions; `--experimental-fast-capture` (Chrome drawElement) is about 2x faster where it engages; each worker costs roughly 256 MB. Per-frame capture time is exposed as `stages.captureFrameMs` in RenderPerfSummary. No published benchmark for 1080x1920@60 exists - this must be measured on the target host. Money: the CLI, packages and local rendering are free (Apache-2.0); `cloud render` bills HeyGen credits (4k bills at 1.5x), `lambda` bills AWS compute, `/reverse-any-video` and `/style-sampling` require HyperFrames Pro.

---

<a id="composition-authoring-model"></a>
## Composition Authoring Model

### Basic Info

**Title**

Composition Authoring Model

**Domain**

Authoring / HTML composition contract

**One Liner**

HyperFrames compositions are plain HTML files where data-* attributes place timed clips on tracks and paused, seekable timelines describe all motion.

**Official Sources**

- https://hyperframes.heygen.com/concepts/compositions
- https://hyperframes.heygen.com/concepts/data-attributes
- https://hyperframes.heygen.com/reference/html-schema
- https://hyperframes.heygen.com/concepts/variables
- https://hyperframes.heygen.com/concepts/index
- https://hyperframes.heygen.com/packages/cli
- https://hyperframes.heygen.com/reference/lint-rules
- https://github.com/heygen-com/hyperframes/blob/main/docs/concepts/compositions.mdx

**Maturity**

stable

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

It makes one HTML document simultaneously the preview, the editable project and the render source: the same attributes that place a clip in the browser editor are the ones the renderer reads, with no export step, no bundler and no second timeline format. A designer or an agent can author a 12-second vertical reel by hand, mount each scene as its own file, expose prices and headlines as variables, and get a frame-exact MP4 out of exactly that markup.

**Key Features**

- One file, one composition: index.html is the root composition; any other .html file can be a scene, and any composition can be mounted inside any other (there is no special root type).
- Root contract: data-composition-id, data-start="0", data-width, data-height, usually data-duration, optional data-fps / data-no-timeline / data-requires-webgpu.
- Clip contract: an element becomes a clip by carrying timing attributes - data-start (seconds or a reference expression), data-duration, data-track-index - plus id and, for DOM/image clips, class="clip".
- Four clip families: <video> (B-roll/A-roll), <img> (stills and overlays), <audio> (music/SFX) and a nested <div data-composition-id> host; any timed DOM element (section, h1, div) is also a clip.
- Relative timing expressions: data-start="intro", "intro + 0.5" and "intro - 0.5" chain clips to the end of another clip in the same composition, with cycle detection.
- Sub-compositions: inline (a nested div with data-composition-id) or external (data-composition-src pointing at a <template>-wrapped file); child time is local, the host window clips or extends it, data-playback-start offsets the child timeline.
- Variables: seven typed kinds (string, number, color, boolean, enum, font, image) declared once with data-composition-variables and overridden per instance with data-variable-values or per render with --variables, bound via data-var-text, data-var-src or CSS var(--id).
- Tracks are editor lanes, not layers: data-track-index only decides which row the browser editor draws; overlap is allowed on one track and paint order is CSS z-index.
- Media attributes: data-media-start (source in-point), data-playback-rate (0.1-10), data-volume (0-3.98), data-fade-in/out, data-has-audio, and JSON data-automation lanes for volume and speed ramps.
- Reusable template pattern: one file per scene, variables declared on the scene root with sensible defaults, every id unique across the assembled project, per-instance values passed on the host, selectors scoped to the mounted composition, static scenes marked data-no-timeline, no wall clock or unseeded randomness - and `hyperframes add` can drop catalog blocks into compositions/ as editable source to start from.
- Statically checkable: `hyperframes lint` validates the same contract (timing, duplicate ids, deprecated attributes, missing timelines, z-index and asset-path traps) before a render is spent.

**Technical Mechanism**

Authoring is parsed, not executed. The HTML parser reads the document into typed clips (start expression, duration, track, media metadata, variables), the timing compiler resolves relative starts and probes media durations, and the bundler inlines every data-composition-src template into a single servable document with the runtime attached. The browser runtime then walks the DOM, resolves which clips are visible at the requested time, seeks each registered timeline by composition id, and exposes a single seek(time) entry point for the renderer. The compiler derives data-end from data-start + data-duration, so data-end never has to be authored. Because the root's data-duration is read before composition scripts run, a script or a variable cannot change the render length for that run. Nested compositions are mounted with their own timeline key and seeked independently - adding a child timeline to the parent GSAP timeline double-drives it and is explicitly forbidden.

**Text And Motion Capabilities**

Text is ordinary DOM text styled by ordinary CSS, so the authoring model puts no ceiling on typography: any web font in assets/fonts, variable-font axes, gradients with background-clip, -webkit-text-stroke, shadows, transforms, SVG text and per-character spans all render because Chrome lays them out. Precision comes from the timeline model, not from a text API: motion is expressed as tweens on a paused timeline, so a per-word caption pop is a tl.fromTo('.word-3', ...) at an absolute composition time and lands on the exact frame the renderer asks for (t = frame/fps, so 16.67 ms granularity at 60 fps). There is no built-in rich-text or caption primitive in the contract - splitting words, computing line breaks, auto-fitting and safe zones are authoring decisions done with CSS, GSAP SplitText or the caption blocks/skills. Font loading is the one hard sequencing rule: load and await fonts before measuring text, and register the timeline only after the async build finishes, or the first frames render with fallback metrics.

**Output Formats**

- The authored file itself is the deliverable source; it previews in any browser with no build step.
- Rendered outputs inherit the composition's data-width/data-height and data-fps: MP4 (H.264/AAC), WebM (VP9/Opus, alpha), MOV (ProRes 4444), GIF, PNG sequence, HLS.
- Still frames at exact times via `hyperframes snapshot --at 0,2,5` (PNG), which is the intended visual gate for authoring.
- Machine-readable structure via `hyperframes timeline --json` (tracks and clips) and `hyperframes compositions`.

### Usage & API

**Setup And Install**

Prerequisites: Node.js 22+ and FFmpeg on PATH.

  npx hyperframes init my-video --resolution portrait --non-interactive
  cd my-video

`init` scaffolds a project with this layout (verified against packages/cli/src/commands/init.ts):

  my-video/
  |- index.html            the root composition (the only required file)
  |- hyperframes.json      project config: registry URL, paths.blocks=compositions,
  |                        paths.components=compositions/components, paths.assets=assets,
  |                        media.autoProxy=true
  |- meta.json             { id, name, createdAt }
  |- package.json          private ESM package with dev/check/render/publish scripts
  |- AGENTS.md, CLAUDE.md  agent instructions for preview/check/snapshot/render
  |- assets/               images, video, audio, fonts (created as needed)
  |- compositions/         scenes and reusable parts (created by `hyperframes add`)
  `- renders/              default render output

Larger projects conventionally add BRIEF.md (what the video must say), STORYBOARD.md (sequence and review state), SCRIPT.md (locked narration) and frame.md (visual direction); only index.html plus its assets are essential. frame.md is the design-system layer HeyGen productizes as 'a DESIGN.md superset for the frame': YAML frontmatter with normative tokens (exact hex palette, typography ramps sized in cqw - container-query width, i.e. relative to the 1920x1080 frame - spacing, component recipes) plus prose intent and do/don't rules, so an agent quotes values verbatim instead of guessing. init does not scaffold frame.md; it is authored or copied from a frame preset and read as an input. --resolution portrait makes the canvas 1080x1920, and --video/--audio additionally transcode and transcribe a source clip with Whisper and patch the result into the composition.

**Minimal Code Example**

Complete, valid, 12-second 1080x1920 project. index.html is the root composition; it mounts one external sub-composition, declares two variables and drives one GSAP timeline.

```html index.html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
    <style>
      html, body { margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #0b0b0f; }
      #root { position: relative; width: 1080px; height: 1920px; overflow: hidden; font-family: system-ui, sans-serif; }
      .clip { position: absolute; inset: 0; }
      #bg { width: 1080px; height: 1920px; object-fit: cover; }
      #headline { display: grid; place-items: center; color: #fff; font-size: 96px; font-weight: 800; letter-spacing: -0.03em; }
      #headline span { color: var(--accent, #059669); }
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="reel"
      data-start="0"
      data-duration="12"
      data-width="1080"
      data-height="1920"
      data-fps="60"
      data-composition-variables='[
        {"id":"price","type":"string","label":"Price shown in the headline","default":"$330"},
        {"id":"accent","type":"color","label":"Accent colour","default":"#059669"}
      ]'
    >
      <video id="bg" class="clip" src="assets/product.mp4"
             data-start="0" data-duration="12" data-track-index="0"
             data-media-start="0" muted playsinline></video>

      <h1 id="headline" class="clip" data-start="0.5" data-duration="4" data-track-index="1">
        iPhone 14 <span data-var-text="price">$330</span>
      </h1>

      <div id="card"
           data-composition-id="price-card"
           data-composition-src="compositions/price-card.html"
           data-start="4.5"
           data-duration="6"
           data-track-index="2"
           data-width="1080"
           data-height="1920"
           data-variable-values='{"price":"$330","accent":"#059669"}'></div>

      <audio id="bed" src="assets/music.mp3"
             data-start="0" data-duration="12" data-track-index="3"
             data-volume="0.3" data-fade-in="0.8" data-fade-out="1.2"></audio>

      <script>
        window.__timelines = window.__timelines || {};
        const tl = gsap.timeline({ paused: true });
        tl.fromTo("#headline", { y: 48, opacity: 0 },
                  { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" }, 0.5);
        window.__timelines.reel = tl;
      </script>
    </div>
  </body>
</html>
```

```html compositions/price-card.html
<template>
  <style>
    #price-card-root { position: absolute; inset: 0; font-family: system-ui, sans-serif; }
    #price-card-root .card {
      position: absolute; left: 72px; right: 72px; bottom: 240px;
      background: rgba(10, 12, 16, 0.72);
      border: 2px solid var(--accent, #059669);
      border-radius: 36px; padding: 48px 56px; color: #fff;
    }
    #price-card-root .price { font: 800 88px/1 system-ui, sans-serif; }
    #price-card-root .note { font: 500 34px/1.3 system-ui, sans-serif; opacity: 0.85; }
  </style>

  <div
    id="price-card-root"
    data-composition-id="price-card"
    data-width="1080"
    data-height="1920"
    data-composition-variables='[
      {"id":"price","type":"string","label":"Price shown on the card","default":"$0"},
      {"id":"accent","type":"color","label":"Card accent colour","default":"#059669"}
    ]'
  >
    <div class="card">
      <p class="price" data-var-text="price">$0</p>
      <p class="note">Includes a one-month store warranty</p>
    </div>

    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });
      tl.from(".card", { y: 80, opacity: 0, duration: 0.45, ease: "power3.out" });
      window.__timelines["price-card"] = tl;
    </script>
  </div>
</template>
```

Render it with `npx hyperframes render --output out.mp4`, override the data without editing HTML with `npx hyperframes render --variables '{"price":"$299","accent":"#f59e0b"}' --output promo.mp4`, and lint the contract first with `npx hyperframes lint`.

**Cli Or Api Surface**

- hyperframes init <dir> - scaffold; --example/-e (default: centered blank), --resolution (portrait = 1080x1920), --video, --audio, --tailwind, --non-interactive, --skip-transcribe, --keep-original, --model, --language, --skip-skills.
- hyperframes lint - static contract validation of the authored attributes (the pre-render gate); `hyperframes check` adds browser runtime, layout, motion and contrast checks.
- hyperframes timeline [--json] - prints tracks and clips; accepts editing verbs move / trim / split / delete with row refs, --plan for a dry-run diff.
- hyperframes compositions - lists every composition in the project; `hyperframes keyframes --runtime css|anime --json` probes animation behaviour.
- hyperframes snapshot --at <seconds,...> - PNG stills at exact times.
- hyperframes add <block|component|tag> - installs editable blocks into compositions/ (paths.blocks) and components into compositions/components/, with --json and --dir.
- hyperframes render - reads the authored contract; --variables / --variables-file / --batch for data, --fps to override data-fps, --resolution to render at higher DPR.
- @hyperframes/core - parseHtml(html), extractCompositionMetadata(html) -> {compositionId, compositionDuration, variables}, generateHyperframesHtml(elements, duration, {...}), validateVariables(values, declarations).
- @hyperframes/core/compiler - compileHtml(rawHtml, projectDir, probeDuration) and bundleToSingleHtml(projectDir, { entryFile, inlineAssets }).
- Browser globals inside a composition: window.__timelines[compositionId] (registered paused timeline), window.__hyperframes.getVariables(), window.__hyperframes.assetUrl(path), and the __playerReady / __renderReady gates.

**Key Config Options**

- data-composition-id (root and every nested host) - the unique composition identifier. For a timed composition it is the key used in window.__timelines. The documented rule for a host is that it matches the id inside the source file and that timeline key; when the same file is mounted more than once, give each host its own unique instance id (the docs' card-pro / card-enterprise pattern) and keep the template's inner id stable. Required. Default: none.
- data-start (root, clip, host) - entry time in seconds, or a reference expression "clipId", "clipId + 0.5", "clipId - 0.5" resolved to the referenced clip's end inside the same composition (no cycles). Required on the top-level root as 0. Default when absent on a clip: 0.
- data-duration (root, clip, host) - slot length in seconds. Required for DOM clips and nested-composition hosts; optional for <img> (default 3 s) and for <video>/<audio> (default: the source's remaining length). On the root it is the render length and is read before scripts run, so it cannot be changed by a variable or script.
- data-track-index (clip) - integer lane the browser editor draws. Display only: the render never reads it, it does not schedule anything and it does not prevent overlap. Default: 0. (Legacy alias data-layer is rejected by lint.)
- class="clip" (authored DOM and image clips) - convention that supplies the full-frame absolutely positioned box; visibility is keyed off data-start, not the class. Video visibility is managed by the media runtime and audio has no visual lifecycle, so neither needs it.
- data-width / data-height (root, host) - authored frame size in CSS px; the aspect ratio locks the output. Required by the contract; the runtime timeline model falls back to 1920x1080 if absent, which is why omitting them silently produces the wrong canvas.
- data-fps (root) - authored frame rate; `hyperframes render` defaults to it and otherwise to 30. Accepts integers 24/25/30/50/60/120/240 or ffmpeg rationals; CLI range 1-240.
- data-composition-src (host) - path to an external composition file, resolved from the project root (never ../). The file must wrap its markup, styles and scripts in a <template>.
- data-playback-start (nested host) - which moment of the child timeline shows first; default 0. Trimming or splitting from the left advances it by elapsed time x data-playback-rate so the child keeps playing instead of restarting.
- data-media-start (video/audio) - source in-point in seconds; default 0. This is the name the timing compiler, HTML parser, validator and audio mixer read (the mixer passes it to ffmpeg -ss).
- data-playback-rate (video/audio/nested host) - constant speed multiplier, clamped 0.1-10; default 1. A rate lane in data-automation overrides it.
- data-volume (video/audio) - static gain where 1 = 0 dB, 0 = silence and up to 3.98 = +12 dB; default 1. Multiplied with any volume lane and applied before the clip's audio effects.
- data-fade-in / data-fade-out (video/audio) - linear edge fades in seconds, default 0, anchored to the clip's own edges (a fade-out follows a later trim) and multiplied on top of data-volume; if both exceed the clip they are scaled to meet inside it.
- data-has-audio="true" (video) - declares that the video contributes audio and must not be muted. Every timed <video> must be either muted or data-has-audio (lint rule video_missing_muted).
- data-automation (video/audio) - JSON envelope {"version":1,"lanes":[{"target":"volume"|"rate","points":[{"t":0,"v":1,"curve":0}]}]}; t is seconds from the clip start, v is gain or speed, curve bends the outgoing segment; a lane wins over the constant attribute.
- data-composition-variables (composition root) - JSON array of {"id","type","label","default","description","portrays","role"} where type is string | number | color | boolean | enum | font | image. Declares the approved inputs; lint rejects malformed declarations and wrong default types.
- data-variable-values (nested host) - JSON object of values for one mounted instance, layered over the child's declared defaults. This is how one source file appears several times with different content.
- data-var-text / data-var-src (any element) - direct bindings: replace the element's text, or replace an image/video/audio/<source> URL. Non-URL values on data-var-src are dropped with a warning and the placeholder renders.
- var(--variableId) (CSS) - every scalar variable is applied as a CSS custom property on the composition root, so colors, numbers and font stacks can be consumed in pure CSS with no script.
- data-no-timeline (composition root or host) - declares that no timeline will be registered. Without it, any element with data-composition-id makes the producer poll up to 45 s (player-ready timeout) on every render; lint reports missing_data_no_timeline.
- data-requires-webgpu (root) - capture refuses to start unless a WebGPU device can be created within a 10 s probe (WebGpuUnavailableError otherwise).
- data-color-grading (img/video) - JSON preset/adjust/details/effects/colorSpace object for colour correction, LUTs and media effects on real media elements (it does not grade a whole HTML scene).
- data-motion-blur family (root or any subtree) - opt-in shutter smear: data-motion-blur="on"|off (default off), data-motion-blur-shutter in degrees 0-720 (default 240), data-motion-blur-phase in degrees of a frame -360..360 (default centred), data-motion-blur-samples 2-64 (default at least 32, scaled to the smear), data-motion-blur-events (default off; on lets blur see motion set from callbacks), data-motion-blur-tier auto|filter|capture (default auto). Turning it on plans screenshot capture instead of BeginFrame.
- data-hf-media-start-basis="global" (legacy root opt-out) - keeps media authored in root time instead of the default local composition time; new compositions should use local timing.
- data-end / data-layer (deprecated) - data-end is compiler-derived from data-start + data-duration (authoring it without data-duration, or disagreeing with it, is a lint error); data-layer is the legacy name for data-track-index.

### Integration

**Pipeline Fit**

This is the layer that would replace hand-built FFmpeg drawtext/ASS caption graphs and the bespoke pixel-coordinate layout code in render_video.py. It augments the existing pipeline rather than the whole stack: footage cutting and beat sync stay in FFmpeg, while every text/motion element becomes a HyperFrames composition whose timing is data, not Python arithmetic. The composition contract is also the clean seam for a hybrid design, because a 1080x1920 transparent overlay composition can be rendered once per SKU and composited over the existing graded footage with a single ffmpeg -filter_complex overlay.

**Python Integration**

Treat the composition as a generated artifact with a schema. Concretely: (1) keep a Jinja template of index.html plus one template per overlay scene, with the attribute names above; (2) escape all injected text with MarkupSafe and put untrusted strings in data-variable-values or text nodes, never inside a <script> block; (3) generate one folder per SKU, write a small JSON manifest with the same values, then call `npx hyperframes lint` and `npx hyperframes render --variables-file vars.json --json` via subprocess with a timeout; (4) read the produced file back with ffprobe instead of trusting stdout, because without --batch the --json flag only suppresses progress and emits an error envelope on failure; (5) keep a `snapshot --at` step so a human/vision check happens before the full render. Caveat: the HTML you generate must be valid HTML5 - a bare fragment without doctype/html/body is a lint error (root_composition_missing_html_wrapper) and quirks-mode layout will differ from the render.

**Data Injection**

Three tiers, in order of preference. Variables for content that must not change design: declare string/number/color/boolean/enum/font/image on the root, bind with data-var-text, data-var-src or var(--id), then pass --variables, --variables-file or --batch rows.json (one row per video, output template like 'renders/{name}.mp4'). Per-instance variables for repeated sub-compositions: data-variable-values on each host. Structural data for anything variables cannot touch - scene count, clip order, root duration, fps, viewport - which must be baked into the generated HTML because those values are read before composition scripts run. Word-level caption timings are structural: emit one span per word and one tween per word at absolute composition times.

**Footage And Asset Handling**

Footage stays where it is: <video src="assets/product.mp4"> with data-start/data-duration/data-media-start selects the exact source range, data-playback-rate retimes it and data-has-audio="true" (with the element unmuted) folds its audio into the mix; silent B-roll uses muted. Audio beds are <audio> with the same window plus data-volume and edge fades. Paths resolve from the project root, so assets/ is the single convention (assets/fonts for webfonts, which must be loaded and awaited before any text measurement). Paths built inside a script are not rewritten - use window.__hyperframes.assetUrl() for files that sit beside a mounted composition. Remote https media is downloaded into the work directory before capture, so it is legal but adds download limits (8 GiB, 30 min, 32 KiB/s floor) and a network dependency to an otherwise deterministic render.

**Migration Effort**

medium

**Integration Steps**

- Scaffold one throwaway project with `npx hyperframes init scratch --resolution portrait --non-interactive` and read the generated index.html to internalise the root/clip contract before writing templates.
- Define the scene inventory for a 28 s reel (hook, 2-3 proof beats, trust stack, CTA) and make one sub-composition file per scene, each declaring its own variables and defaults.
- Write the root index.html as pure sequencing: root data-width=1080, data-height=1920, data-fps=60, data-duration set from the voiceover, and one host element per scene with data-start computed from the line timings.
- Move every per-SKU value (price, model, battery health, badges, footer) into declared variables; keep only structural/timing values in the generator.
- Add a Python pre-flight that runs `npx hyperframes lint` and fails the job before spending a render, plus a `snapshot --at` gate for the first and last frame of each scene.
- Render one SKU as a transparent MOV/WebM overlay and composite it in the existing FFmpeg graph; only move to full-frame HyperFrames renders if the composite proves worse than a single render.
- Pin the CLI version, commit the generated project folder next to the MP4 for reproducibility, and record which template revision produced which render.
- Once stable, promote the overlay templates into a small internal block library (one folder per effect) so new reels compose existing scenes instead of new HTML.

### Constraints

**Requirements**

- Node.js 22+ and FFmpeg on PATH to lint or render; authoring itself only needs a browser (index.html plays as-is).
- A valid HTML5 document: <!doctype html>, <html>, <head> with charset, <body> - bare fragments are a lint error.
- An explicitly sized root: data-width/data-height in px, with html/body sized to match and overflow hidden, otherwise the canvas and the capture frame disagree.
- Unique element ids across the whole assembled project, because render-time frame injection and clip references target ids.
- Every timed composition needs a finite duration: an explicit root data-duration, or an inferable one from registered timelines or timed media (Three.js, unbounded animation and timeline-free compositions must declare it).

**Limitations**

- Timing lives in HTML only: scripts must not play/pause/seek media or show/hide clips by time - the framework owns that and a script doing it fights the runtime.
- data-track-index is a display lane, not a layer: overlapping clips composite in DOM/z-index order, so crossfades need CSS z-index discipline, not track numbers.
- Negative z-index clips can disappear behind an opaque ancestor background unless the parent creates a stacking context (isolation: isolate); lint warns on the CSS text even when the computed result is fine.
- A composition host whose id never registers a timeline stalls every render for up to 45 s unless it carries data-no-timeline, which is easy to forget on static scenes.
- Nested composition paths resolve from the project root, never from the referencing file, so ../composition.html is wrong and lint-flaggable; parent traversal above the root is clamped.
- Root data-duration, fps, viewport and output format are locked before composition logic runs - they cannot be variables, so per-video length changes require regenerating the HTML or driving --fps/--start/--end.
- The media in-point attribute naming is split: <video>/<audio> must use data-media-start, nested hosts use data-playback-start; using the wrong one on a video yields a trimmed picture over untrimmed audio.
- A file mounted several times runs each copy's script wrapped with a scoped document/gsap/window, so string selectors stay inside that instance - but its internal element ids are duplicated, which matters for anything that targets ids globally (render-time frame injection, data-start references).
- A child timeline must not be added to the parent GSAP timeline - HyperFrames seeks nested timelines independently, and double-driving desynchronises them.
- Asynchronous setup must register the timeline at the end of the callback; registering an empty timeline first makes the render capture an unbuilt scene.
- data-var-src only accepts relative paths, http(s) or blob URLs - data: video URIs are dropped with a warning and the placeholder renders.
- Inspection commands clamp the viewport at 4096 px, so canvases larger than that are only fully verifiable from a render, not from check/snapshot.

**Known Issues**

- documented lint rules encode the common contract failures: timeline_element_missing_timing, clip_ends_past_root_duration, nested_clip_never_visible, duplicate_composition_id, deprecated_data_end, deprecated_data_layer, missing_data_no_timeline, root_composition_missing_duration_source, root_composition_missing_html_wrapper, invalid_parent_traversal_in_asset_path.
- The data-media-start vs data-playback-start split is a documented inconsistency between readers, not a bug that is fixed - it must be encoded in the template generator.
- Very large single composition files trigger composition_file_too_large and become hard to diff; the documented fix is splitting into sub-compositions.
- Many heavy overlays (filter: blur, radial-gradient, clip-path) trigger composition_heavy_overlay_count_high and cap capture performance even when hidden.
- Negative z-index and requestAnimationFrame are both authoring traps that lint catches statically but only warns/errors on text patterns, so computed-style edge cases still need a visual check.

**Licensing**

Apache License 2.0 covers the format, the parser/compiler, the runtime and the CLI, so compositions you author and generate are yours to ship commercially with no per-render fee or attribution requirement beyond the license terms. Catalog blocks installed with `hyperframes add` arrive as editable source under the same repository license; the commercial boundary sits in the hosted Studio Pro plan and HeyGen's model APIs, not in the authoring contract.

### Evidence

**Confidence**

0.9

**Sources**

- https://hyperframes.heygen.com/reference/html-schema - the authoritative attribute tables: composition root, timed clips, media, relative timing, nested compositions, variables, animation contract, and the data-media-start vs data-playback-start split.
- https://hyperframes.heygen.com/concepts/data-attributes - the three core timing attributes, 'tracks are not layers', relative start syntax, negative z-index guidance.
- https://hyperframes.heygen.com/concepts/compositions - the composition/clip definition, the 5 s / 150-frame worked example, external vs inline sub-compositions, <template> requirement, data-playback-start semantics, project file tree, variables example.
- https://hyperframes.heygen.com/concepts/variables - the seven variable kinds, data-var-text/data-var-src/var(--id) bindings, per-instance values, CLI overrides, batch renders, and the explicit list of what cannot be a variable.
- https://hyperframes.heygen.com/concepts/index - the project folder model (BRIEF.md, STORYBOARD.md, SCRIPT.md, frame.md, index.html, hyperframes.json, compositions/, assets/, renders/).
- https://hyperframes.heygen.com/prompting/design-systems - what frame.md is for: normative frontmatter tokens, brand-as-truth for colour/type but not layout, and how a series shares one spec.
- Source: skills/hyperframes/assets/frame-presets/*/FRAME.md - the actual frame.md shape: YAML frontmatter with colors, typography ramps in cqw/px, spacing and component recipes, plus prose rules.
- https://hyperframes.heygen.com/packages/cli - `init` flags and what init scaffolds (AGENTS.md/CLAUDE.md, core skills, --resolution presets, --video/--audio transcription) and the `add` block/component install model.
- Source: packages/cli/src/commands/init.ts - the exact scaffold: template index.html, meta.json {id,name,createdAt}, hyperframes.json, package.json with dev/check/render/publish scripts, shared AGENTS.md/CLAUDE.md.
- Source: packages/cli/src/utils/projectConfig.ts - default project config paths: blocks=compositions, components=compositions/components, assets=assets; media.autoProxy=true.
- Source: packages/parsers/src/compositionContract.ts - canonical attributes (data-start/data-duration/data-track-index), data-end derived, data-layer legacy, default start 0 and default track index 0.
- Source: packages/core/src/compiler/compositionScoping.ts - composition scripts are wrapped and receive a scoped document / gsap / window / __hyperframes, so selectors resolve inside the mounted instance; CSS is scoped to the composition root as well.
- Source: packages/core/src/runtime/timeline.ts - runtime fallback of composition width/height to 1920x1080 when data-width/data-height are absent.
- https://hyperframes.heygen.com/reference/lint-rules - the exact lint rule names and fixes used in the limitations and known-issues lists.
- https://hyperframes.heygen.com/reference/speed-ramps - data-automation rate lane shape, clamping to 0.1-10 and interaction with data-playback-rate.
- https://hyperframes.heygen.com/guides/motion-blur - the data-motion-blur / -shutter / -phase / -samples / -events / -tier attributes with their defaults, and the fact that blur switches the capture plan to screenshots.
- Source: templates/blank/index.html (packages/cli/src/templates/blank) - the actual scaffolded composition, including the data-attributes and the paused-timeline registration pattern.

**Open Questions**

- Does the browser editor write data-track-index back into source for every edit, and does it ever renumber lanes in a way that matters for generated templates?
- What are the exact semantics of data-audio-group (seen in the runtime/engine sources but not described in the public attribute reference)?
- How does data-playback-rate interact with a rate lane and fades on a clip that is also trimmed by data-media-start - the docs describe each independently but not the combined envelope?
- Is there a hard limit on nesting depth or total clip count per composition before capture time degrades noticeably?

### Other Info

**Performance And Cost**

Authoring choices dominate render cost: heavy blur/filter stacks, dozens of shadowed layers and oversized source images are the documented causes of slow capture, and splitting a composition so each capture sees fewer heavy overlays is the documented mitigation. Composition size also has soft limits - a large single HTML file is warned about and harder to iterate on. There is no published per-attribute cost model and no measured number for a 1080x1920@60 authored reel, so the only reliable figure is a local benchmark of the real template.

---

<a id="limitations-known-issues-and-roadmap"></a>
## Limitations, Known Issues and Roadmap

### Basic Info

**Title**

Limitations, Known Issues and Roadmap

**Domain**

Risk assessment / release cadence, support and production-adoption risk

**One Liner**

HyperFrames is a fast-moving Apache-2.0 pre-1.0 project: real 9:16, font and alpha caveats, no public roadmap, and CPU-only WSL2 lands in its slowest render profile.

**Official Sources**

- https://github.com/heygen-com/hyperframes
- https://github.com/heygen-com/hyperframes/issues
- https://github.com/heygen-com/hyperframes/discussions
- https://hyperframes.heygen.com/faq
- https://hyperframes.heygen.com/help
- https://hyperframes.heygen.com/community/adopters
- https://hyperframes.heygen.com/contributing/release-channels
- https://hyperframes.heygen.com/guides/performance
- https://hyperframes.heygen.com/changelog
- https://hyperframes.heygen.com/weekly-updates
- https://hyperframes.heygen.com/product-updates
- https://hyperframes.studio/pricing
- https://www.npmjs.com/package/hyperframes

**Maturity**

beta

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

This item is the decision layer for adoption, not a feature. It turns the project's public signals into a risk register the shop can act on: which failure modes were already fixed before this research (the 10 s WSL2 probe penalty, audio nondeterminism, the 9:16 crop, the 8 px MP4 crop, and the 60 fps seek-quantisation and BeginFrame hangs), which are open today and would hit a 28 s 1080x1920@60 reel (fonts missing on a bare Linux host, off-grid cut times, silent AAC level shifts, transparent-frame artifacts under parallel capture, ProRes alpha in segmented capture), how fast the surface moves (125 npm versions in 30 days, no LTS, no public roadmap), where the Apache-2.0 line stops (hosted cloud, HeyGen models, two Pro skills, a $25/month Studio plan), and what the 8 GB CPU-only WSL2 profile actually costs (one worker, screenshot capture, raised protocol timeouts, software WebGL because there is no GPU to reach). It also quantifies support health: 84.7k stars, ~5.2M npm downloads a month, 109 open issues and ~185 open PRs, median first reply about 9 hours on sampled threads.

**Technical Mechanism**

Evidence was gathered on 2026-11-15 from four primary streams rather than from blog posts. (1) GitHub REST: repo metadata (created_at, stars, forks, watchers, open_issues_count, has_discussions, license), the search API for issue/PR counts (`is:issue state:open` = 109, `is:pr state:open` = 185, `is:issue state:closed` = 576), the full 109-issue open list for label and age distribution, individual issue bodies for the 30 issues closest to this use case, and a 60-issue sample of closed issues plus their first comments to time triage and resolution. The milestones endpoint returns an empty array, which is how 'no public roadmap' was established rather than assumed. (2) The npm registry document for `hyperframes`: 641 version timestamps, dist-tags (latest 0.9.54, alpha 0.9.28-alpha.3, desktop 0.8.53-desktop.0, hdr 0.8.190-hdr.1), and the download-counts API. (3) The docs site: llms.txt, the FAQ, Troubleshooting, Adopters, Release channels, Performance, the CLI reference (including the low-memory, timeout and segmented-capture sections) and the weekly/changelog pages. (4) The GitHub Discussions list and the pricing page. Claims that could not be measured on this machine (render seconds per frame at 1080x1920@60, the true cost of the low-memory profile here, whether this WSL2 install's fontconfig is non-empty) are marked uncertain instead of estimated.

**Output Formats**

- MP4 H.264/AAC: the delivery path for an opaque 9:16 reel. No alpha. Default capture is JPEG 4:2:0; `--chroma-subsampling 444` switches to lossless PNG capture and yuv444p for thin coloured text but many players cannot decode 4:4:4. Historic defect class: 0.8.81-0.8.153 cropped the rightmost 8 px of any 1080-wide composition (#5551, fixed).
- WebM VP9/Opus: the only practical transparent web overlay. CPU-heavy encoding (cost scales with duration, not frames), `yuva420p` with `-auto-alt-ref 0`. Alpha only survives where the composition paints nothing. Verified with an alpha-aware decoder (`ffmpeg -c:v libvpx-vp9 -i overlay.webm -frames:v 1 -pix_fmt rgba out.png`), never with a bare ffprobe pix_fmt read.
- MOV ProRes 4444: alpha editing intermediate, but three open caveats - segmented capture always fails with `--format mov` because segments are hardcoded to `.mp4` (#6481), ProRes mov carries 8-bit levels in a 10-bit container despite the docs (#5479), and parallel capture can emit a fully transparent first frame per worker shard in alpha output (#4435). Segmented mov is byte-identical to unsegmented once patched (698/698 frames, PSNR = inf, per the reporter).
- PNG sequence (RGBA): lossless per-frame output for compositing, but `render` can capture pending virtual-rAF DOM updates after a seek (#5888 open) and it takes the disk path (raw frames).
- GIF: no audio, capped at 30 fps, limited transparency.
- HLS VOD: SDR only, `--hdr` rejected.
- No format carries alpha and HDR together, and `--resolution` supersampling is MP4-only, SDR-only, exact-aspect-ratio-only, so a 1080x1920 composition cannot be delivered to a 1440x2560 preset.

### Usage & API

**Setup And Install**

For reproducing the risk picture rather than for rendering (rendering setup is documented in the other items): Node.js >= 22, npm/npx, FFmpeg and ffprobe on PATH, and an optional GitHub token to avoid the 60-requests-per-hour unauthenticated search limit.

```bash
# 1. Pin the version under evaluation and freeze the moving parts
export HYPERFRAMES_NO_UPDATE_CHECK=1
export HYPERFRAMES_NO_AUTO_INSTALL=1
export HYPERFRAMES_SKIP_SKILLS=1
npx --yes hyperframes@0.9.54 doctor

# 2. Risk snapshot: cadence, backlog, current release
curl -s https://registry.npmjs.org/hyperframes \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["dist-tags"]); print(len([k for k in d["time"] if k not in ("created","modified")]),"versions")'
curl -s "https://api.github.com/search/issues?q=repo:heygen-com/hyperframes+is:issue+state:open&per_page=1" \
  | python3 -c 'import json,sys; print("open issues:", json.load(sys.stdin)["total_count"])'

# 3. Local profile the renderer will choose on this box
nproc; free -g | head -2          # 8C/16T, 7.8 GB -> low-memory mode is automatic
fc-list | wc -l                   # 0 fonts = #7406 (textless render, exit 0)
```

**Minimal Code Example**

The smallest complete thing that exercises the risk surface before any template work: a 3 s 1080x1920@60 stub with real type, rendered opaque and transparent, then verified for text presence and alpha.

```bash
set -e
npx --yes hyperframes@0.9.54 init risk-spike --example blank --resolution 1080x1920
cd risk-spike
# replace index.html with a 3 s 1080x1920 root, one @font-face, one 46 px caption
npx hyperframes lint && npx hyperframes check
npx hyperframes render --fps 60 --quality draft -o spike.mp4        # opaque, measurable
npx hyperframes render --fps 60 --format webm --workers 1 -o spike.webm  # alpha, 1 worker avoids #4435
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,pix_fmt -of csv spike.mp4
# text actually present? (mean luma of the caption band must not equal the empty-plate value)
ffmpeg -v error -i spike.mp4 -vf "crop=1080:300:0:1300,signalstats,metadata=print:key=lavfi.signalstats.YAVG" -frames:v 1 -f null -
# alpha actually present? (select the VP9 alpha decoder BEFORE the input)
ffmpeg -v error -c:v libvpx-vp9 -i spike.webm -frames:v 1 -pix_fmt rgba spike.png && ffprobe -v error -show_entries stream=pix_fmt -of csv spike.png
```

A 3 s stub renders in a fraction of a 28 s reel, and it already answers the three go/no-go questions: does type render at all on this font stack, does 60 fps cost what the extrapolation says, and does alpha survive the format.

**Cli Or Api Surface**

- `hyperframes doctor` - runtime, browser, FFmpeg, frames-cache headroom; the first thing to run after any upgrade or WSL restart.
- `hyperframes benchmark` - the only first-party way to get real capture throughput on this host; includes a 60 fps configuration.
- `hyperframes lint` / `hyperframes check` - static contract and browser runtime gate; `check` now replays an unchanged project in about a second. Note: `check` can return `browserSkipped: true` with empty layout/motion/contrast sections, which is not the same as clean.
- `hyperframes render --strict` / `--strict-all` - fail on lint errors, or errors and warnings, before spending render time.
- `hyperframes render --low-memory-mode` / `--no-low-memory-mode` - auto at 8 GB RAM or less (one worker, screenshot capture, no calibration).
- `hyperframes render --workers 1..24` - ceiling only; the drawElement fast path uses one worker regardless of the number.
- `hyperframes render --protocol-timeout`, `--browser-timeout`, `--player-ready-timeout` - the three budgets that expire on a small host; docs explicitly recommend raising the protocol timeout on machines with 8 GB or less.
- `hyperframes render --resume` - reuses the segments a crashed long render already finished, byte-identically.
- `hyperframes snapshot --at/--frames/--range` - sampled stills; also the cheapest way to prove text exists after a suspiciously fast render.
- `hyperframes info` - version and environment block for bug reports.
- `hyperframes feedback --rating N --comment ... [--file-issue]` - the in-band feedback path (the `--file-issue` flow offers to upload the project to a public URL; it is not a privacy scrubber).
- `hyperframes telemetry disable` - stops telemetry, the feedback prompt and canary enrolment ('nothing that reports nothing is ever picked for a staged release').
- `hyperframes upgrade --project . --check` - reports drift between a scaffolded project's pin and the CLI.
- Node API: `hyperframes/api` and @hyperframes/producer (`createRenderJob`/`executeRenderJob`) exist but are documented as pre-1.0 and allowed to change between minors; `executeRenderJob` resolves to void and reports through job mutation plus callbacks.

**Key Config Options**

- `HYPERFRAMES_NO_UPDATE_CHECK=1` + `HYPERFRAMES_NO_AUTO_INSTALL=1` - stop the CLI from self-updating out from under a pinned render; without them a CI render is non-deterministic by construction.
- `HYPERFRAMES_SKIP_SKILLS=1` (or `init --skip-skills`) - `init` installs agent skills by default; unwanted in a hermetic pipeline.
- `PRODUCER_LOW_MEMORY_MODE` - set explicitly for containers, because detection reads host RAM rather than cgroup limits and `--docker` on an 8 GB host can otherwise pick the wrong profile.
- `PRODUCER_PUPPETEER_PROTOCOL_TIMEOUT_MS` / `--protocol-timeout` - raise on hosts with 8 GB or less and on asset-heavy compositions; the default auto-scales with output pixel area up to 30 minutes.
- `PRODUCER_PAGE_NAVIGATION_TIMEOUT_MS` / `--browser-timeout` - 60 s default navigation budget; too short for a cold cache with many fonts and videos.
- `PRODUCER_PLAYER_READY_TIMEOUT_MS` / `--player-ready-timeout` - 45 s default; this is also the fixed penalty paid per render when a composition never registers a timeline and has no `data-no-timeline`.
- `HYPERFRAMES_RENDER_LOCK=off` - the default is one local render at a time per user account (a lock file in the cache dir), so a multi-SKU loop serialises; switching it off trades correctness of throughput for CPU starvation.
- `--workers` + `HF_CAPTURE_PARALLEL_STREAM` - on Linux with the bundled chrome-headless-shell multi-worker capture streams; on macOS/Windows it writes frames to disk unless the env var opts in.
- `--chroma-subsampling 444` - the only fix for thin coloured text fringing; ~2x render time and many players cannot decode it, so it is a master-only option.
- `--video-frame-format png` - per-source extraction format; use PNG for screen recordings and colour-sensitive footage, JPEG otherwise (cost and disk).
- `PRODUCER_FORCE_SCREENSHOT=true` - the historical escape hatch when a low-memory host froze during browser init (#1236); still the first thing to try if capture stalls on this box.
- `HYPERFRAMES_CHROME_SANDBOX=0|1` - force the sandbox choice on Linux where unprivileged user namespaces are restricted; the renderer warns and continues without it otherwise.
- `HYPERFRAMES_CHROME_ENV_PASSTHROUGH` - comma-separated env names Chrome needs (Chrome only receives a curated subset); no way to pass Chrome command-line flags at all (#7661).

### Integration

**Pipeline Fit**

This item does not replace or augment the renderer at all: it is the adoption gate and the standing risk register for the rest of the HyperFrames research. Concretely, it decides whether `render_video.py` keeps full ownership of the 28 s 1080x1920@60 reel or whether a HyperFrames layer is added for typography and motion. Its conclusion for this shop is narrow and specific: the OSS CLI is usable and genuinely deterministic, but on a CPU-only WSL2 host with 7.8 GB of RAM the renderer auto-selects its slowest profile (one worker, screenshot capture, no calibration), the alpha path has three open defects, and 125 releases in 30 days means 'latest' is not a version you can ship against. The low-risk integration is therefore: opaque full-frame composition only (no alpha compositing), one worker, pinned exact version, frame-aligned cut times, local @font-face with a fontconfig presence check, and the existing FFmpeg loudness stage kept as the final audio authority. Under those constraints HyperFrames is an optional upgrade for the text/motion layer, not a replacement for the beat-cut FFmpeg path, and the migration is reversible because the overlay is just another asset.

**Python Integration**

Python drives it as an opaque subprocess and never parses progress semantically. The wrapper's real job is risk containment: (1) resolve an exact version string and call it as `npx --yes hyperframes@<pin>` with HYPERFRAMES_NO_UPDATE_CHECK/HYPERFRAMES_NO_AUTO_INSTALL set, so an upgrade is a deliberate commit and never a side effect; (2) run `doctor` plus a fontconfig assertion (`fc-list | wc -l` > 0, the #7406 guard) as preflight and fail before the render rather than after; (3) read stdout and stderr separately, because `@hf-progress` machine lines only appear when stdout is not a TTY while human errors and warnings go to stderr, and a naive `capture_output=True` loses the error text; (4) discriminate failures on `error.code` under `--json` where available, since there are only three exit codes (0/1/2) and no distinct code for 'no disk' vs 'frame 731 failed'; (5) apply its own wall-clock timeout per render, sized from the measured spike rather than the CLI's internal budgets; (6) verify the artefact rather than the exit code: ffprobe width/height/fps/pix_fmt, sampled frame luma in the caption band (catches #7406), ebur128/true-peak on the muxed audio (catches #6871), and a frame-alignment assertion on every cut time (catches the #6753 class). Caveats: one local render at a time per user account via a cache-dir lock file, so a multi-SKU loop must either serialise (fine on 8 GB) or set HYPERFRAMES_RENDER_LOCK=off and accept starvation; and the Node API is pre-1.0, so a subprocess boundary is also the most stable boundary.

**Data Injection**

For this item the injected data is the risk snapshot, not per-video content: GitHub REST counts (issues, PRs, labels, ages), the npm registry `time` map (version cadence), download counts, and the docs/pricing facts are collected into one JSON beside `fields.yaml` and re-run on a schedule (monthly, or before any version bump) so 'is it still safe to upgrade' is a diff rather than an opinion. Per-video risk knobs (fps, workers, low-memory mode, output format, chroma subsampling, protocol timeout) belong in `sku.json`/`session3_config.json` and are passed as flags by the Python wrapper, never authored into the HTML template, because the root duration/fps/viewport are locked before composition logic runs.

**Footage And Asset Handling**

Footage is where the failure modes cluster. Every `<video>` is pre-extracted to per-frame stills by FFmpeg and injected as an `<img>`, so the first render of a footage-heavy composition pays a large extraction cost and writes to a content-addressed cache (2 GiB default budget). Clip times that are not on the frame grid are the documented trap for footage: a `data-start` between two frames shows the media one frame late, a value a hair below a boundary repeats/skips frames, and word-level transcript timings produce exactly those values (#6753, fixed 2026-10-30) - so the wrapper should round every cut to `round(round(t*fps)/fps)` before writing the HTML. Two videos on screen at once stalled capture in `--docker` until 2026-11-15 (#5473), a failed remote fetch can be silently dropped from the render (#6606, open), and `data-playback-start` vs `data-media-start` is a live split-brain where a video authored with the wrong one renders a trimmed picture over untrimmed audio. For this pipeline: keep all assets local and inside the project, never rely on remote URLs, mute or omit video audio entirely (the narration and bed are mixed in FFmpeg), and treat the video layer as pre-trimmed stills rather than as an editable NLE track.

**Migration Effort**

medium

**Integration Steps**

- Freeze the surface: pin one exact version (0.9.54 at the time of writing), set HYPERFRAMES_NO_UPDATE_CHECK=1, HYPERFRAMES_NO_AUTO_INSTALL=1 and HYPERFRAMES_SKIP_SKILLS=1, and record the pin in the repo so upgrades are reviewed changes rather than silent ones.
- Measure before designing: run `npx hyperframes@<pin> doctor` and `benchmark` on the WSL2 box, then render a 3 s 1080x1920@60 stub and extrapolate to 1,680 frames. If the projection is worse than the current FFmpeg path by an unacceptable factor, stop here - the decision costs one hour instead of two weeks.
- Prove the font stack: assert `fc-list | wc -l` > 0 in preflight (#7406), vendor every family as a woff2 with `@font-face` in the file that uses it, and render one 46 px caption at 1080x1920 to confirm weight, hinting and stroke width against the current FFmpeg drawtext output.
- Prove alpha or drop it: render the same stub as WebM VP9 with `--workers 1` (avoiding the parallel first-frame defect #4435), decode one frame with the alpha-aware VP9 decoder, and compare against a ProRes mov render. If either path is not clean, choose opaque full-frame output and skip compositing entirely.
- Frame-align the edit: convert every beat and word timestamp to a frame index at 60 fps and back to seconds before writing `data-start`/`data-duration`, so the #6753 class of one-frame-late footage cannot occur, and keep the existing beat-grid validation as the source of truth.
- Add the artefact gates: sampled caption-band luma (text actually present), ffprobe geometry and pix_fmt, ebur128 loudness plus true peak on the muxed file, a blank/static-hold detector over the composition (#7861 shows `check` will not catch it), and a hash of the video stream across three identical renders.
- Keep `qa_check.py` and `eval_run.py` authoritative: the overlay's own lint/check is necessary but not sufficient, so the 29-check gate and the SSIM baseline stay in place and gain two rows for text presence and frame-grid alignment.
- Adopt an upgrade policy: batch upgrades (for example monthly, or when a specific fix is needed), never in the middle of a delivery, always re-running the 3 s spike, the visual gate and the three-run hash before promoting the new pin.
- Watch the boundary, not the features: re-read the pricing page and the changelog at each upgrade for anything that moves behind Pro (cloud exports, HeyGen models, `/reverse-any-video`, `/style-sampling`) and confirm that local rendering still needs no account.
- Write the kill switch: keep the FFmpeg-only path buildable from the same `sku.json` so a bad release, a broken font stack or an abandoned project does not block delivery of the next reel.

### Constraints

**Requirements**

- Node.js >= 22 for the CLI and every @hyperframes/* package; Node 20 is not supported.
- FFmpeg and ffprobe on PATH for capture, extraction, mixing and encoding; `doctor` fails without them and the docs only show `brew install ffmpeg` in the skills workflow (#5232).
- A Chromium build: the CLI downloads a pinned chrome-headless-shell (152.0.7977.30 as observed in 0.8.x/0.9.x reports) into ~/.cache/hyperframes/chrome; on Linux BeginFrame capture requires that bundled shell rather than desktop Chrome.
- Disk for captured frames and the extraction cache (2 GiB default budget; `doctor` fails the frames-cache row under 2 GB free). Streaming encode avoids the raw-frame path for single-worker MP4/MOV.
- RAM: each Chrome worker is roughly 256 MB, and at 8 GB or less the renderer switches itself to the low-memory profile (one worker, screenshot capture, no calibration).
- A healthy fontconfig on Linux: a host that lists zero fonts renders every frame textless and exits 0 (#7406).
- Network for the version check (unless disabled), for `add`/`catalog` registry fetches from raw.githubusercontent.com, and for any Google Fonts family that is not one of the bundled ones.
- GitHub account/token only for research tooling (issue and PR counts); rendering needs no account and no API key.

**Limitations**

- Pre-1.0 by semver and by behaviour: 0.9.x with 125 publishes in 30 days, a FrameAdapter API documented as 'experimental v0 ... may change before v1', and a Node API explicitly allowed to change between minor versions. Pin an exact version and treat every upgrade as a code change with a visual gate.
- No public roadmap, no milestones, no LTS branch, no published support policy or SLA. Planning against future features is guesswork; plan against what is in the changelog today.
- The 8 GB CPU-only WSL2 profile is the slowest supported path by design: low-memory mode is automatic at 8 GB or less, which means one Chrome worker, screenshot capture instead of BeginFrame, and no calibration - the exact opposite of the parallel BeginFrame path the performance numbers describe.
- WSL2 GPU access is conditional, not guaranteed: the probe can log 'software (WebGL unavailable)' and fall back to SwiftShader, which the docs describe as several times slower. The blocking bug was fixed in 0.8.157 (PR #5591 retries without WAYLAND_DISPLAY, which Chrome 150+ picks under WSLg and which has no offscreen configs on Mesa's D3D12 path), but the fix only helps a host that actually has a GPU, needs the user in the `render` group, and was confirmed on an RTX 4070 SUPER - a GPU-less box like this one stays on software rendering permanently. Plain DOM/CSS/GSAP text does not need WebGL; any shader, Three.js or WebGPU block does.
- Alpha is a second-class output: MP4 has none, WebM/VP9 is CPU-heavy and only transparent where the composition paints nothing, ProRes MOV has three open defects (#6481 segmented capture, #5479 8-bit levels in a 10-bit container, #4435 transparent first frame per parallel worker), and PNG sequence can capture pending virtual-rAF updates (#5888).
- Determinism is per-environment, not universal: identical settings reproduce frames on the same machine, but Chrome version, installed fonts, GPU/driver and encoder build can shift pixels, and only `--docker` pins all of them. Motion blur is the documented exception; page-side WebGL compositing is f32 and pinned by PSNR rather than byte equality.
- The renderer assumes it owns media playback: calling play(), pause() or setting currentTime from composition code fights the runtime and desynchronises audio; two videos on screen at once has been a repeated source of capture stalls (#5473, closed 2026-11-15).
- One local render at a time per user account (lock file in the cache dir), and one process per render: a 6-SKU batch is 6 serial renders, not 6 parallel ones, unless the lock is disabled and the CPU is allowed to starve.
- Fixed timing budgets can fail rather than wait: 60 s navigation, 45 s player-ready, 300 s per CDP call (auto-scaled by pixel area, max 30 min), and a fixed 45 s poll for any composition that never registers a timeline and lacks data-no-timeline.
- Inspection is clamped to a 4096 px viewport, so anything larger is only fully verifiable from an actual render; `check` can also return `browserSkipped: true` with empty layout/motion/contrast sections, which a naive wrapper reads as 'clean'.
- There is no reading-speed, minimum-hold or blank-frame lint: a 10-minute narrated 9:16 explainer passed `check` with 11.8 s of blank screen and ~30 static holds (#7861, opened 2026-11-15). Content QA stays the shop's job.
- The catalog is landscape-first (203 of 219 dimensioned items are 1920x1080, 13 are 1080x1920) and its components are finished scenes with their own timing, so a 9:16 pipeline re-frames and re-times almost every pick rather than parameterising it.
- Single-vendor governance: HeyGen owns the repo, the release process, the alpha/canary channels and the commercial surface; the community contributes fixes but 185 open PRs show review bandwidth is the constraint, and there is no foundation or multi-vendor stewardship to fall back on.
- Commercial gravity runs toward the hosted product: the desktop Studio app is closed and requires a HeyGen sign-in, cloud exports are metered (5/month free), the strongest agent skills (`/reverse-any-video`, `/style-sampling`) are Pro, and 'Reverse any video'/'Reference videos'/'Storyboarding' are Pro-only features rather than Apache-2.0 code.
- Telemetry is on by default (anonymous command names and render performance, opt-out via `hyperframes telemetry disable` or DO_NOT_TRACK), and disabling it also removes the install from canary rollouts - opting out of data collection means opting out of staged releases.
- Documentation is broad (135 pages) but does not enumerate limitations, does not publish a 9:16 60 fps benchmark, and in places promises gates that do not hold (the FAQ says lint catches undeclared fonts; #7406 shows a textless render passing every check).

**Known Issues**

- OPEN #7406 (2026-11-08) - 'render exits 0 with all text missing when Chromium finds no system fonts on Linux': zero fontconfig entries produce a textless video while check, validate, snapshot and doctor all pass. Direct WSL2/slim-container risk; guard with `fc-list | wc -l` and a sampled-luma assertion. https://github.com/heygen-com/hyperframes/issues/7406
- OPEN #7661 (2026-11-13) - 'no way to pass Chrome a launch flag': Windows text renders with ClearType fringes (red/blue edge split 0.500 on 0.9.40, 0.003 with `--disable-lcd-text`, which cannot be passed). Text over transparency and anything under will-change: transform are unaffected; Linux/macOS unaffected. https://github.com/heygen-com/hyperframes/issues/7661
- OPEN #4435 (2026-09-24) - parallel capture emits a fully transparent first frame at every worker shard boundary in transparent ProRes output (frames 30/60/90 with 4 workers); `--workers 1` is clean. On an 8 GB host low-memory mode's single worker happens to avoid it, at the cost of speed. https://github.com/heygen-com/hyperframes/issues/4435
- OPEN #6481 (2026-10-26) - segmented capture always fails with `--format mov`: segments are hardcoded to `.mp4`, so the ProRes muxer rejects them; still present on main in 0.8.204. Matters when segmented capture is enabled or its default duration threshold is lowered (the source plans a 600 s default). https://github.com/heygen-com/hyperframes/issues/6481
- OPEN #5479 (2026-10-12) - ProRes `mov` output carries 8-bit levels in a 10-bit container although the docs say 10-bit colour; an alpha master from HyperFrames is effectively 8-bit. https://github.com/heygen-com/hyperframes/issues/5479
- OPEN #5888 (2026-10-19) - 'PNG sequence can capture pending virtual-rAF DOM updates after seek (0.8.145)': the RGBA sequence path can capture a frame that has not settled, which undermines PNG-sequence-as-ground-truth for alpha. https://github.com/heygen-com/hyperframes/issues/5888
- OPEN #6871 (2026-10-31, labelled stale) - every render with audio re-encodes AAC 2-3 times (duration check counts priming packets, true-peak guard re-encodes with a gain change logged only at info level, so `--quiet` hides it). Final loudness can differ from the mix with no visible warning; verify LUFS/true peak after muxing. https://github.com/heygen-com/hyperframes/issues/6871
- CLOSED #5669 (2026-10-15 -> 2026-10-23) - render audio differed run to run because amix input order followed prep completion order and the -1 dBTP guard flipped between runs; fixed. Historical evidence that audio determinism is newer than video determinism. https://github.com/heygen-com/hyperframes/issues/5669
- CLOSED #6753 (2026-10-29 -> 2026-10-30) - a `<video>` whose data-start is off the frame grid shows its media one frame late, or repeats/skips frames when the value is a hair below a boundary. The reporter's framing is this pipeline's exact working method ('we cut on words'). Round cut times to the frame grid. https://github.com/heygen-com/hyperframes/issues/6753
- CLOSED #5473 (2026-10-11 -> 2026-11-15) - `render --docker` stalled capture at a deterministic frame (~200) when two `<video>` clips played at once ('no frame progress for 60000ms'), while the same composition rendered locally in 29 s. https://github.com/heygen-com/hyperframes/issues/5473
- CLOSED #5548 (2026-10-13 -> 2026-10-24) - WSL2 render always fell back to SwiftShader even with a visible NVIDIA GPU (Mesa/D3D12 fine, Chrome's GPU process exited): a 7:23 1080p30 composition with 26 videos took 48 min in WSL2 against ~5x faster on native Windows. Root cause was Chrome 150+ choosing its Wayland backend whenever WSLg sets WAYLAND_DISPLAY; fixed in 0.8.157 (PR #5591 retries without that variable) and confirmed on the reporter's RTX 4070 SUPER. The 48 min figure therefore describes exactly the path a GPU-less host still takes. https://github.com/heygen-com/hyperframes/issues/5548
- CLOSED #6465 (2026-10-26 -> 2026-11-05) - every render, check and snapshot waited 10 s on WSL2 with mirrored networking because the loopback Studio probe had no connect bound; fixed with a 1 s net socket bound. https://github.com/heygen-com/hyperframes/issues/6465
- CLOSED #1236 (2026-06-06) - renderer froze on low-memory hardware (7.6 GB physical) unless PRODUCER_FORCE_SCREENSHOT was set, even with low-memory mode enabled; fixed the same day, still the first thing to try if capture stalls on 8 GB. https://github.com/heygen-com/hyperframes/issues/1236
- OPEN #4060 (2026-09-18) - disk-capture headroom gate bills uncompressed RGBA (`frames x width x height x 4`): a 44 s 1080p30 export was estimated at ~11 GB and refused against 11.4 GB free, with a real JPEG footprint of a few hundred MB; when the gate rejects, the render fails even where streaming was possible. https://github.com/heygen-com/hyperframes/issues/4060
- CLOSED #5551 (2026-10-13) - 0.8.81-0.8.153 dropped the rightmost 8 px of any 1080-wide composition in MP4 only (H.264 path), padding them black; PNG sequence and WebM were correct. The canonical '9:16 output silently loses pixels' bug. https://github.com/heygen-com/hyperframes/issues/5551
- CLOSED #4001 (2026-09-16) - a 1080x1920 root rendered only the top ~1080 px when html/body kept the 1920x1080 scaffold size, the exact shape of an agent-generated portrait project. https://github.com/heygen-com/hyperframes/issues/4001
- OPEN #5230 (2026-10-08) - `check` and `snapshot` cannot target a composition file (only `render` can), so a second aspect ratio can only be verified by duplicating the project; a 9:16 and a 16:9 cut of the same piece has no single gate. https://github.com/heygen-com/hyperframes/issues/5230
- OPEN #7861 (2026-11-15) - a 10-minute narrated explainer (16:9 plus a 9:16 cut) passed `check` with 0 errors while a viewer saw a 11.8 s blank stretch, 9 shorter blanks and ~30 static holds; ffmpeg freezedetect missed them because a grain overlay moves every frame. The clearest evidence that the built-in gates do not measure content. https://github.com/heygen-com/hyperframes/issues/7861
- OPEN #7458 (2026-11-09) - capture and software H.264 encoding size their CPU demand independently, so overlap can oversubscribe the CPU (reported from code inspection, not a measured regression); on an 8-core CPU-only box this is the exact contention a 60 fps render creates. https://github.com/heygen-com/hyperframes/issues/7458
- OPEN #7834 (2026-11-15) - an unread download body can throw an uncatchable undici assertion when a server closes the connection during asset download (seen on pages whose font downloads time out); a network blip can surface as an uncaughtException rather than a render error. https://github.com/heygen-com/hyperframes/issues/7834
- OPEN #7429 (2026-11-09) - the Linux AppImage's chrome_crashpad_handler intermittently SIGBUSes on exit; Studio-desktop-only, but it is the Linux packaging of the app a small shop would try first. https://github.com/heygen-com/hyperframes/issues/7429
- OPEN #5025 (2026-10-04) and #5232 (2026-10-08) - the agent-facing skills carry 10 cross-skill contradictions and 11 wrong facts or broken paths, and the workflow skills reach render before FFmpeg is checked while the docs only offer `brew install ffmpeg`. Agent-driven authoring inherits these errors. https://github.com/heygen-com/hyperframes/issues/5025
- CLOSED #1737 (2026-06-26), #3012 (2026-08-04), #3049 (2026-08-07) - three of the highest-impact historical defects for this use case: `--fps 60` rendered GSAP/DOM motion at 30 fps (_every other frame duplicated_), BeginFrame could hang at 60 fps after warmup moved Chrome's clock backwards, and transparent renders duplicated content in horizontal bands at renderHeight/4 offsets across two major Chrome versions. All fixed; all are the kind of defect that ships silently because the file is still a valid MP4. https://github.com/heygen-com/hyperframes/issues/3049
- CLOSED #5069 (2026-10-05 -> 2026-10-10) - `lowerAscii()` over a large compiled HTML with many inlined `@font-face` rules aborted Node with 'Fatal JavaScript invalid size error' (SIGTRAP, exit 133) after capture started; `check` passed. Font inlining plus a big template is a real crash class, not a theoretical one. https://github.com/heygen-com/hyperframes/issues/5069

**Licensing**

Apache License 2.0 for the repository and every published package (CLI, core, parsers, lint, engine, producer, studio, studio-server, sdk, player, shader-transitions, inspector, share, aws-lambda, gcp-cloud-run), with no per-render fee, no seat count, no commercial-use threshold and no watermark; rendered files carry only two unsigned container tags (hyperframes_renderer, hyperframes_version) that `ffmpeg -map_metadata -1 -c copy` strips. The paid boundary sits around hosted services and content, not the renderer: HyperFrames Pro at $25/month (25,000 credits, unlimited cloud exports, HeyGen Video 1 / Seedance / MiniMax / GPT Image / Nano Banana / HeyGen Voice / Lyria models, 'Reverse any video', 'Reference videos', 'Storyboarding', 'Ask Framey'), a Free plan with 3 AI video clips and 5 cloud exports a month, metered `hyperframes cloud render` credits, and two Pro skills (`/reverse-any-video`, `/style-sampling`). The desktop Studio app is free but closed-source and requires a HeyGen sign-in; the browser editor in the Apache-2.0 repo does not. Third-party obligations travel with the catalog: per-item licences are declared in `registry-item.json` where applicable (36 items 'Apache-2.0 AND MIT' for MIT shaders, one OFL-1.1, one MIT, one Apache-2.0, one 'MIT AND OFL-1.1'), seven 3D-motion blocks vendor GSAP 3.14.2 under the GSAP Standard License (not an OSI licence) plus three.js MIT, and bundled fonts are Fontsource/OFL. Catalog items are installed as editable source and may be shipped inside commercial videos with the notices preserved. The project is a HeyGen product: the repo lives under the heygen-com organisation, the release process, canary channel and Pro surface are HeyGen-controlled, and the README/ADOPTERS list HeyGen as the first production user.

### Evidence

**Confidence**

0.8

**Sources**

- https://api.github.com/repos/heygen-com/hyperframes - created 2026-03-10, 84,729 stars, 7,844 forks, 203 watchers, 294 open issues+PRs combined, has_discussions true, Apache-2.0, pushed 2026-11-15T23:05Z.
- https://api.github.com/search/issues?q=repo:heygen-com/hyperframes+is:issue+state:open - 109 open issues; the same query for closed returned 576 and for open PRs returned 185, which fixes the backlog ratio and the PR-queue bottleneck.
- https://api.github.com/repos/heygen-com/hyperframes/issues?state=open (all 109 items) - label distribution (57 bug, 40 enhancement, 52 triage/ready, 42 triage/needs-design, 9 needs-info, 3 stale, 4 documentation) and age distribution (median 19 days, max 206).
- Individual issue pages fetched for 30 issues - the bodies quoted in known_issues, including #7406 (textless render on Linux), #4435 (transparent first frame per worker), #6481 (segmented mov), #6753 (off-grid data-start), #5548 (WSL2 SwiftShader, 48 min for 7:23), #7861 (blank screens passing check), #7661 (ClearType, no Chrome flags).
- https://registry.npmjs.org/hyperframes - 641 versions since 2026-03-23, dist-tags latest 0.9.54 / alpha 0.9.28-alpha.3, and the `time` map used to compute 125 publishes in 30 days, 36 in 7 days, median 4.15 h between publishes, 0.8.0 on 2026-08-18 and 0.9.0 on 2026-11-02.
- https://api.npmjs.org/downloads/point/last-month/hyperframes - 5,223,278 downloads in the 30 days to 2026-11-15 (1,320,899 in the last week); @hyperframes/core 1,904,699. Community-size signal, inflated by CI and npx usage.
- https://hyperframes.heygen.com/faq - 'Is HyperFrames free?', commercial use under Apache 2.0, Windows support, the Pro plan price, and the fonts answer whose promise #7406 contradicts.
- https://hyperframes.heygen.com/help - Troubleshooting, including the WSL2 section ('browserGpuMode probe -> software (WebGL unavailable)', 'the render is slow under WSL2', render from Windows instead) and the 45 s timeline-poll explanation.
- https://hyperframes.heygen.com/community/adopters - 12 named adopters and what each uses it for (tldraw PR walkthroughs, Typeframe speech videos, DoneCut Shorts edits, reap.video, THU-MAIC), i.e. production evidence that includes short-form speech and Shorts-style work.
- https://hyperframes.heygen.com/contributing/release-channels - the stable/alpha channel policy, branch separation and CI guardrails; establishes that 'stable' means an npm dist-tag, not an LTS promise.
- https://hyperframes.heygen.com/packages/cli - the low-memory row ('auto at 8 GB RAM or less: one worker, screenshot capture, no calibration'), the three timeout budgets with the explicit 'raise --protocol-timeout on hosts with 8 GB RAM or less' note, the render-lock paragraph and the segmented-capture/streaming sections.
- https://hyperframes.heygen.com/guides/performance - the only published benchmark (300 frames, 1920x1080, 9.8 s vs 25.0 s median of three) and the note that transparent WebM is CPU-heavy.
- https://hyperframes.heygen.com/guides/rendering - format matrix, transparent-video rules, the VP9 alpha verification trap, and the MOV/ProRes description that #5479 contradicts.
- https://hyperframes.heygen.com/changelog and /weekly-updates - v0.9.55 released 2026-11-15, weekly digests describing seeded randomness, film-time timers and preview/render parity; the only forward-looking narrative the project publishes.
- https://hyperframes.studio/pricing - Free vs Pro ($25/month, 25,000 credits, unlimited cloud exports, Reverse any video / Reference videos / Storyboarding / Ask Framey), and 'HyperFrames Pro is separate from HeyGen plans'.
- https://github.com/heygen-com/hyperframes/discussions - six categories, a recent page dominated by show-and-tell and questions (e.g. #7567 '13-minute narrated explainer with 72 scenes', #5958 asking about the Windows app), i.e. a community that shares work more than it files structured reports.
- https://api.github.com/repos/heygen-com/hyperframes/milestones - returns an empty list, which is the evidence for 'no public roadmap' rather than an assumption.

**Open Questions**

- What does a 28 s 1080x1920@60 reel actually cost on this machine? `hyperframes benchmark` plus a timed 3 s stub (extrapolated to 1,680 frames) is the only way to replace the derived 1.5-8 min band with a number; the answer decides go/no-go more than any other fact here.
- Is #7406 a live risk on this WSL2 install? `fc-list | wc -l` plus a textless-frame detector answers it in minutes, and the answer decides whether a preflight font assertion is mandatory or merely prudent.
- Does the alpha path survive contact with this pipeline? A WebM VP9 render at `--workers 1` decoded with the alpha-aware decoder, compared against a ProRes mov and against an FFmpeg-native overlay, is the only honest test - three open defects (#4435, #6481, #5479) all sit on that path.
- Will 0.9.x keep 60 fps safe? #1737 and #3012 are closed, but both were 60 fps-specific regressions that shipped silently; a three-run frame-hash on 0.9.54 (and on each future pin) is the cheap insurance.
- Is the Pro boundary stable? Nothing in the repo commits to keeping HDR, motion blur, cloud render or the reverse/style skills free; a pricing-page diff at each upgrade is the only signal available.
- When is 1.0, and does it come with an LTS-style branch or a deprecation policy? No milestone, no roadmap file and no maintainer statement found; the honest planning assumption is 'not soon' and 'pin everything'.
- Would a maintainer respond to a small shop's blocking bug within a delivery window? Median first reply on sampled closed issues was ~9 h, but 42 issues sit in needs-design and 185 PRs are open, so the tail is the risk - the fallback has to be the FFmpeg-only path, not a support expectation.
- Do the agent skills actually produce valid 9:16 projects today? #5025 (10 contradictions, 11 wrong facts) and #4001 (portrait crop from a scaffold) suggest the skills are the weakest documented surface, and this pipeline is agent-authored.

### Other Info

**Key Features**

- Backlog shape at 0.9.54: 109 open issues and 185 open PRs against 576 closed issues since the repo was created on 2026-03-10. Labels split 57 bug / 40 enhancement, with 52 'triage/ready' (accepted, not started), 42 'triage/needs-design' (no agreed design), 9 'triage/needs-info', 3 'stale', 13 'help wanted', 9 'good first issue', 4 'documentation'. Median age of an open issue is 19 days; the oldest is 206 days; 6 are older than 90 days.
- Release cadence: 641 npm versions since 2026-03-23 (592 stable, 47 alpha prereleases), 125 publishes in the 30 days to 2026-11-15, 36 in the last 7 days, median gap 4.15 h between publishes (mean 5.95 h), and 5 npm publishes plus 6 GitHub releases on 2026-11-15 alone (v0.9.50 to v0.9.55). 0.8.x absorbed 229 patch releases in the ~76 days between 0.8.0 (2026-08-18) and 0.9.0 (2026-11-02); 0.9.x has taken 54 patches in its first 13 days.
- Semver reality: still 0.9.x, so minor bumps may break. The exported FrameAdapter interface is documented as 'experimental v0 API, signatures may change before v1'; the Node entry `hyperframes/api` is documented as built for HyperFrames' own apps and allowed to change between minors. No 1.0 date, no LTS branch, no deprecation policy beyond a `_meta.deprecated` flag on three rename aliases.
- Roadmap transparency: there is no ROADMAP.md, no GitHub milestones (the milestones API returns an empty list) and no public board. The only forward-looking signals are the weekly-update posts, the changelog, ~40 open enhancement issues, the 'ideas' discussion category, and internal design notes under `plans/` in the repo. Anything stronger than that is inference.
- Direction of travel (Nov 2026): determinism hardening (seeded randomness per render, page timers on film time, render/preview frame parity), Studio preview parity, cloud-render robustness, a ~1 s cached `check`, motion-blur tiers, and more authoring lint. The 0.9 line reads as 'make renders match preview and each other', not 'add features'.
- Community: 84,729 stars, 7,844 forks, 203 watchers, 5,223,278 npm downloads for `hyperframes` in the 30 days to 2026-11-15 (1,320,899 in the last week) and 1,904,699 for @hyperframes/core. 12 published adopters (HeyGen, tldraw, TanStack, OptinMonster, reap, Typeframe, THU-MAIC, Mini Course Generator, PandaStudio, Skyflo, DoneCut, varg), two of them working on short-form speech and Shorts-style output.
- Support surface: GitHub Issues is the only bug channel (no paid support, no SLA, no status page found); Discussions exists with six categories (announcements, general, ideas, polls, q-a, show-and-tell) and a Discord invite; `hyperframes feedback --file-issue` can pre-fill an issue but offers to upload the project to a public URL and is explicitly not a privacy scrubber.
- Responsiveness measured on 2026-11-15: on a 60-issue sample of recently closed issues the median time to close was 66.9 h (mean 260.7 h); on the 36 of those with any comment, the median time to the first reply was 8.7 h (mean 125.8 h). Fast triage, uneven resolution: 52 issues sit in 'triage/ready' waiting for work, and the PR queue (185 open) is the real bottleneck for outside fixes.
- OSS vs Pro boundary: the repo, CLI, core, engine, producer, player, SDK, studio (browser editor) and shader-transitions are Apache-2.0 with no watermark, no render fee and no commercial threshold. Paid surfaces are the hosted ones: HyperFrames Pro at $25/month (25,000 credits, unlimited cloud exports, HeyGen Video/Image/Voice/Music models, 'Reverse any video', 'Reference videos', 'Storyboarding', 'Ask Framey'), the Free plan (3 AI video clips and 5 cloud exports a month), `hyperframes cloud render` billing credits, and the two Pro skills `/reverse-any-video` and `/style-sampling`. The desktop Studio app is free but closed-source and needs a HeyGen sign-in; the repo's own browser editor does not.
- Fixed-before-adoption list (all closed and verified in the issue tracker): WSL2 always falling back to SwiftShader (#5548, closed 2026-10-24 after PR #5591, which helps GPU hosts only), the 10 s WSL2 loopback probe on every render/check/snapshot (#6465, closed 2026-11-05), render audio differing run to run (#5669, closed 2026-10-23), 60 fps renders quantised to 30 fps (#1737, closed 2026-06-26), BeginFrame hanging at 60 fps (#3012, closed 2026-08-05), transparent renders with horizontal-band ghosting (#3049, closed 2026-08-07), the 9:16 top-1080px crop (#4001, closed 2026-09-16), the 1080-wide MP4 losing its rightmost 8 px (#5551, closed 2026-10-13).
- Open-and-relevant list for this exact use case: #7406 (Linux with zero fontconfig fonts renders every frame textless and exits 0), #7661 (no way to pass Chrome flags; Windows ClearType fringes on small text), #4435 (first frame of each parallel worker renders transparent in alpha output), #6481 (segmented capture always fails with --format mov), #6871 (AAC re-encoded 2-3x with silent level changes), #5230 (check/snapshot cannot target a composition file, so a second aspect ratio is ungated), #7861 (a 10-minute narrated 9:16 explainer passed `check` with blank stretches and 3-8 s static holds), #7458 (capture and software H.264 encoding size their CPU demand independently), #4060 (disk headroom gate bills raw RGBA and fails instead of streaming).
- Documentation: 135 docs pages plus 449 catalog pages, generated on Mintlify with an llms.txt index that makes agent ingestion easy. Gaps are specific rather than structural: no page enumerates limitations or a support policy, the performance page's only benchmark is 300 frames at 1920x1080/30, the FAQ's font answer promises lint will catch undeclared fonts while #7406 shows a textless render passing check and doctor, and the agent skills still carry contradictions (#5025 open: 10 cross-skill contradictions and 11 wrong facts or broken paths; #5232 open: skills reach render before FFmpeg is checked and the docs only offer `brew install ffmpeg`).
- [uncertain] Roadmap intent cannot be verified from outside: whether 1.0 is quarters or years away, whether FrameAdapter v1 lands before or after 1.0, whether the Pro boundary will keep absorbing features that are free today (motion blur, HDR, cloud render), and whether HeyGen commits to an LTS-style branch for a production shop.

**Text And Motion Capabilities**

Typography is where the risk concentrates for this pipeline, because the reel is text over footage and the text is the deliverable. Known open typography risks: on Linux with a fontconfig that lists zero fonts, the render exits 0 and every frame has no text while check, validate, snapshot and doctor all pass (#7406, open since 2026-11-08) - a bare or misconfigured WSL2/container font stack is exactly that failure mode; there is no supported way to pass Chrome a launch flag, so `--disable-lcd-text` cannot be set and Windows hosts render small text with ClearType colour fringes (measured red/blue edge split 0.500 on 0.9.40, #7661 open); text over transparency is antialiased in grayscale (no subpixel AA without an opaque backdrop), so a transparent overlay's 46 px captions look thinner than the browser preview; thin coloured glyphs fringe under the default JPEG/4:2:0 capture unless `--chroma-subsampling 444` is paid for (~2x render time for text/UI); non-Latin text at a bundled weight could silently fall back to a system font until #7313 was fixed on 2026-11-08; Arabic per-letter splitting still renders isolated letters and reversed rows with no lint warning (#5885 open). Motion risk is different: the seek model is sound (t = floor(frame)/fps) but anything that must be true between frames is not available except through motion blur, which is the one documented exception to byte-identical determinism, and the catalog's motion-heavy blocks are landscape-first (203 of 219 dimensioned items are 1920x1080, 13 are 1080x1920), so most picks must be re-framed by hand. There is no reading-speed (CPS) or minimum-hold lint in HyperFrames at all: the shop's own caption_audit gate remains the only thing standing between a dense voiceover and unreadable cards. [uncertain] Whether this specific WSL2 install has a healthy fontconfig (and therefore whether #7406 is a live risk here) was not tested; `fc-list | wc -l` before the first render resolves it in seconds.

**Performance And Cost**

Documented numbers, with conditions, plus one hard external measurement. First-party: 300 frames at 1920x1080 rendered in 9.8 s (median of three, ~30.6 output fps, ~0.033 s/frame) versus 25.0 s with one expensive CSS declaration added and pixels proven identical - a landscape 1080p30 figure, not 9:16 60 fps. Real-world WSL2 (issue #5548, before its 0.8.157 fix): a 7:23 1080p30 composition with 26 `<video>` elements and 28 logical CPUs took 48 min wall time under SwiftShader, i.e. ~2,880 s for 13,303 frames = ~0.216 s/frame and ~6.5x realtime, while the same project ran ~5x faster on native Windows once Chrome could reach the GPU - which makes that number a good proxy for a GPU-less host, i.e. this one. Extrapolating that per-frame cost to this pipeline's 1,680 frames gives ~6 min, but that composition was video-heavy and this one is text-over-still-frames, so the plausible band is wide - roughly 1.5-8 min per 28 s reel on this box, against a few tens of seconds for the current FFmpeg path. The profile itself is the bigger finding: at 7.8 GB the renderer selects low-memory mode automatically (one worker, screenshot capture, no calibration), so the parallel BeginFrame throughput the docs advertise is simply unavailable, and the docs additionally recommend raising --protocol-timeout on hosts with 8 GB or less. Disk: measured 1080p JPEG captures are ~150-400 KB/frame (#4060), so ~250-670 MB of captured frames per 1,680-frame reel; if the render takes the disk path with raw RGBA the same reel needs 1080x1920x4x1680 = ~13.9 GB of scratch, and the headroom gate bills exactly that ceiling rather than the JPEG reality. Money: local rendering is $0 with no watermark and no per-render fee; the only paid surfaces are HyperFrames Pro ($25/month, 25,000 credits, unlimited cloud exports, HeyGen models), metered `cloud render` credits (4K billing at 1.5x), and AWS/GCP compute for Lambda/Cloud Run. Human cost is the real one: 125 releases a month means every upgrade is a re-validation, and the shop's own gate (contact sheet + 29 checks + SSIM baseline) is what makes that affordable. [uncertain] No 1080x1920@60 measurement exists in the docs, in the issue tracker, or on this machine; the 1.5-8 min band is derived from one video-heavy WSL2 datapoint and must be replaced by `hyperframes benchmark` plus a timed 3 s stub before any commitment.

---

<a id="local-rendering-on-wsl2"></a>
## Local_Rendering_On_WSL2

### Basic Info

**Title**

Local_Rendering_On_WSL2

**Domain**

Deployment — local render infrastructure (headless Chrome + FFmpeg)

**One Liner**

HyperFrames renders a 28 s 1080x1920@60 reel on this WSL2 box in 1m48s with four workers, but its low-memory profile silently pins one worker and takes 6m31s.

**Official Sources**

- https://hyperframes.heygen.com/help.md (section 'The render is slow under WSL2')
- https://hyperframes.heygen.com/deploy/overview.md
- https://hyperframes.heygen.com/guides/performance.md
- https://hyperframes.heygen.com/faq.md
- https://github.com/heygen-com/hyperframes
- https://hyperframes.studio/pricing

**Maturity**

stable

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

A fully offline, account-free render path for the Fenixsoft reel on the machine that already edits it: HyperFrames 0.9.54 drives its own chrome-headless-shell 152 build through CDP, captures 1680 frames of a 1080x1920@60 composition, encodes H.264 High@4.2 with the system FFmpeg 6.1.1 and writes an MP4 — no Docker, no cloud, no API key. Measured end to end in 1 m 48 s at four workers and 6 m 31 s at the default one worker.

**Key Features**

- One command, no daemon: `npx hyperframes@0.9.54 render <dir> --fps 60 --quality looks --output out.mp4` runs compile → browser probe → source-video extraction → capture+encode streamed together → assemble, and prints `@hf-progress` NDJSON lines plus a one-line summary with a per-stage timing breakdown.
- The toolchain is already satisfied on this box: `doctor` reports Node v24.22.1, 16 cores (Ryzen 7 5825U), 7.8 GB RAM, 853.6 GB free, FFmpeg 6.1.1 + ffprobe on PATH, /dev/shm 3970 MB, Chrome resolved from the bundled cache, and `Environment: WSL · non-TTY`.
- Chrome is not the user's /usr/bin/google-chrome: HyperFrames downloads `chrome-headless-shell` linux-152.0.7977.30 into ~/.cache/hyperframes/chrome (261 MB on disk) and pins it, which is what makes the deterministic re-render claim plausible on a machine with an independently updated Chrome 147.
- WSL2 GPU handling is built in, not left to the user: on this machine the default probe returns SwiftShader (`browserGpuMode probe → software (WebGL unavailable)`), then because it detects WSL and WAYLAND_DISPLAY=wayland-0 it retries with WAYLAND_DISPLAY removed and finds real hardware (`ANGLE (Microsoft Corporation, D3D12 (AMD Radeon (TM) Graphics), OpenGL ES 3.1)`), and captures through that path.
- Hardware capture is measurably faster even for a composition with no WebGL: the same seek+JPEG-screenshot loop at 1080x1920 ran 6.1 fps on SwiftShader and 8.6 fps on the ANGLE/D3D12 path (60 frames each, measured with a CDP harness).
- Per-worker cost is real and larger than documented: one idle headless-shell tree is 345 MB PSS; with a 1080x1920 composition loaded and painted it is 414 MB PSS (1191 MB summed RSS). Budget ~400–450 MB per worker, not the ~256 MB the CLI help quotes.
- Render output is a normal MP4 you can post-process: H.264 High profile, level 4.2, yuv420p, 1080x1920, 60/1, 12.6 Mbps, 42.5 MB for 28 s — deliberately re-encoded by us to 8.54 MB (Main@4.2, CRF 21, 2.5 Mbps) for WhatsApp.
- The low-memory profile is the single most expensive default on this machine and it is overridable: `--no-low-memory-mode` (or PRODUCER_LOW_MEMORY_MODE=false) turns 391 s into 107 s by allowing 4 workers and the BeginFrame capture path.
- Cache and disk are cheap: the shared extraction cache at /tmp/hyperframes-extract-cache-1000 held 39 MB for this project, fonts cache 1.1 MB, chrome-headless-shell 261 MB, and each render writes its own ~42 MB MP4.

**Technical Mechanism**

The CLI builds a RenderJob and calls @hyperframes/producer, which compiles the HTML (inlining Google Fonts as woff2 data URIs — the log shows 'Fetched 11 font face(s) for "Inter"'), serves the project from a loopback HTTP server, and drives chrome-headless-shell over CDP. Each frame is produced by seeking the paused GSAP timeline (`window.__timelines[...]`) to a quantised time and capturing either via BeginFrame or via Page.captureScreenshot; on this machine the choice is made by the memory profile. Source video is pre-extracted to still frames with FFmpeg (5 unique 720x1280@60 clips, ~38 s of footage, 3.6 s of extraction) and the encoder runs concurrently with capture ('encode (during capture) 6m 20.7s' in the one-worker log), so capture dominates wall time. A per-user lock file serialises renders, and the output is assembled by FFmpeg with faststart.

**Text And Motion Capabilities**

For this deployment item the relevant text fact is that capture fidelity is not resolution-limited but compositing-limited: text is rasterised by Chrome at exact CSS pixel sizes (a 46 px caption is 46 px in the 1080-wide frame, no scaling), fonts are inlined at compile time so a missing local font cannot silently substitute, and the software path rasterises the same pixels more slowly rather than differently. The measurable difference between SwiftShader and ANGLE here was throughput (6.1 vs 8.6 fps), not glyph quality. Antialiasing degrades only in the transparent-overlay case (grayscale AA instead of subpixel AA), which matters if overlays are composited in FFmpeg rather than rendered full-bleed.

**Output Formats**

- mp4 — H.264 High profile, level 4.2, yuv420p, AAC 192k, faststart. Measured: 1080x1920, 60/1, 12.6 Mbps, 42.5 MB for 28 s at `--quality looks` (CRF 16).
- webm — VP9 + Opus, the alpha-capable format; VP9 encode is CPU-heavy and is the wrong choice for a full-bleed reel.
- mov — ProRes 4444 with alpha, for compositing; large files, not a delivery format here.
- png-sequence — RGBA frames plus an audio sidecar; useful as an intermediate for a different encoder, and the only lossless capture path.
- gif (≤30 fps) and hls (H.264/AAC, SDR only) — irrelevant for this pipeline.
- Re-encoded derivative (our step): 8.54 MB MP4, Main@4.2, CRF 21, maxrate 2500k, 128k AAC, faststart — measured 33.9 s of FFmpeg on this CPU.

### Usage & API

**Setup And Install**

Nothing to install beyond what the machine has. `npx --yes hyperframes@0.9.54 doctor --json` passed every hard check on 2026-11-15 and, on first use, downloaded chrome-headless-shell 152.0.7977.30 into ~/.cache/hyperframes/chrome. Pin the version: `npx hyperframes@latest` failed with npm ETARGET ('No matching version found for hyperframes@0.9.55') during the registry rollover, while 0.9.54 resolved and ran. Optional extras the doctor flags as missing and that this pipeline does not need: whisper-cpp, Kokoro TTS, MusicGen.

**Minimal Code Example**

```bash
# measured command: 28 s, 1080x1920, 60 fps, 4 workers, low-memory profile disabled
cd /path/to/project
npx --yes hyperframes@0.9.54 render . \
  --fps 60 \
  --quality looks \
  --workers 4 --no-low-memory-mode \
  --output out/reel.mp4

# what the log says when you leave the defaults on an 8 GB box:
# [INFO] [Render] Low-memory render profile active: screenshot capture,
#   auto-worker calibration skipped, pinned to 1 worker.
#   Override with --no-low-memory-mode or PRODUCER_LOW_MEMORY_MODE=false.
#   {"totalMemMb":7941,"thresholdMb":8192}

# then make it a WhatsApp-sized file (H.264 Main, no B-frames, faststart)
ffmpeg -i out/reel.mp4 -c:v libx264 -preset medium -profile:v main -level 4.2 \
  -pix_fmt yuv420p -crf 21 -maxrate 2500k -bufsize 5000k \
  -c:a aac -b:a 128k -ar 48000 -movflags +faststart -y out/reel_wa.mp4
```

**Cli Or Api Surface**

- `render <dir> --fps 60 --quality looks|draft|standard|high --workers 1..24|auto --no-low-memory-mode --output <file>` — the four flags that decide this machine's fate.
- `render --browser-gpu | --no-browser-gpu` — force hardware or SwiftShader; default `auto` runs the WSL-aware probe described above.
- `doctor --json` — the only honest preflight; returns per-check `{name, ok, detail}` including Memory, /dev/shm, Frames cache, Chrome path, FFmpeg version and `Environment: WSL · non-TTY`.
- `check <dir> --safe-zone reels|tiktok|shorts|standard|off --caption-zone "x0=0;y0=.82;x1=1;y1=1"` — one browser session that returns lint/runtime/layout/motion/contrast/craft/audio sections as JSON; the contrast section is a real per-frame WCAG AA audit.
- `lint <dir> --json` — 0 errors is achievable; it is the cheapest gate before a 2-minute render (`media_missing_id` means a <video> will render frozen).
- `benchmark <dir> --runs 1..20 --json` — first-party way to re-measure worker settings when the composition or the machine changes.
- `@hf-progress {code, done, total, pct}` lines on non-TTY stdout with codes compile|extract_video|process_audio|start_browsers|capture|encode|assemble — the progress contract a Python wrapper should parse.
- `@hf-result` JSON line at the end, plus exit codes 0 / 1 / 2 (ran-and-succeeded / ran-and-failed / bad command line).
- `cloud render`, `lambda render`, `cloudrun render` — the three remote drivers, all requiring an authenticated account or your own cloud account.

**Key Config Options**

- `--workers` (default auto). Measured on this box: auto → 1 (because of the memory profile); explicit 4 → 107.6 s render; explicit 6 → 100.3 s render. Beyond 4 the gain is ~6%, and each worker adds ~400 MB.
- `--low-memory-mode` / `--no-low-memory-mode` (or PRODUCER_LOW_MEMORY_MODE). Triggers at totalMemMb ≤ 8192 — this machine reports 7941, so it is always on unless you disable it. It changes two things at once: worker count is pinned to 1 and capture falls back from BeginFrame to Page.captureScreenshot.
- `--quality` draft|looks|standard|high. `looks` is CRF 16 + preset standard on this version; it produced 42 MB for 28 s. Draft is the right setting for iteration, not delivery.
- `--chroma-subsampling 420|444`. 444 is the text/UI master switch (lossless PNG capture) and costs ~2x render time for text; it also produces files most players cannot decode.
- `--frames-cache-dir` / HYPERFRAMES_CACHE_DIR — where the extraction cache lives; defaults outside the project, so a project's own folder stays small (16 MB here) while /tmp holds the cache.
- `HYPERFRAMES_CHROME_SANDBOX=0|1` — force the sandbox decision; irrelevant in practice because the producer already passes `--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage` to every capture browser.
- `PRODUCER_LOW_MEMORY_MODE=false` — the env-var equivalent of `--no-low-memory-mode`, the right knob for a headless batch runner.
- `--gpu` (FFmpeg hardware encode) — it does work here: FFmpeg 6.1.1 ships h264_vaapi and the AMD iGPU answers on /dev/dri/renderD128 (measured, 6.3x faster than libx264 medium), so this is a real lever once capture stops being the bottleneck.

### Integration

**Pipeline Fit**

Augments, does not replace, the current stack. FFmpeg still does the beat-cut assembly, the loudness normalisation and the WhatsApp re-encode; HyperFrames would take over only the layer that FFmpeg is bad at — kinetic captions, the price pill, the header stack and vector checkmarks — and hand back either a full-bleed MP4 or a transparent overlay to composite. On this machine that costs 1 m 48 s per 28 s reel at the measured settings, which fits the existing 2–3 minute render budget, but the default settings cost 6 m 31 s and that does not.

**Python Integration**

Drive the CLI with subprocess, not the Node API: `subprocess.run(["npx", "--yes", "hyperframes@0.9.54", "render", str(project_dir), "--fps", "60", "--workers", "4", "--no-low-memory-mode", "--output", str(out_mp4)], capture_output=True, text=True, timeout=900, env={**os.environ, "HYPERFRAMES_NO_UPDATE_CHECK": "1"})`. Parse stdout for `@hf-progress` (JSON per line, prefixed) to render progress and for the final `@hf-result`; treat exit 1 as render failure with stderr carrying the reason, exit 2 as a bug in your own argv. Two caveats measured here: (a) the first invocation may spend a minute in npm resolution and the chrome-headless-shell download, so keep a warm cache and a generous first-run timeout; (b) `npx hyperframes@latest` can fail with ETARGET mid-release, so pin the exact version string in code.

**Data Injection**

Two mechanisms, both file-based. Composition variables: `data-composition-variables` on <html> declares typed defaults and `--variables '{"price":"$330"}'` (or `--variables-file vars.json`, plus `--strict-variables`) overrides them; the page reads them with `window.__hyperframes.getVariables()`. Per-video copy and timings are better injected by generating the HTML: the existing pipeline already owns the numbers (narration_lines.json, caption_audit.json, sku.json), so a Python template step should emit the caption word list and its start/end times literally into the composition, exactly as the catalog components do with their `WORDS = [{text, start, end}, ...]` arrays.

**Footage And Asset Handling**

Existing MP4s are consumed by `<video class="clip" id="..." data-start data-duration data-media-start src="./assets/x.mp4">` and are pre-extracted to frames by FFmpeg, so a footage-heavy composition pays a fixed extraction cost and needs disk headroom (39 MB of cache for 38 s of 720x1280 source). Two measured traps: a <video> without an `id` renders FROZEN (lint `media_missing_id`), and a <video> nested inside a timed wrapper is a lint error (`video_nested_in_timed_element`) — the video carries the timing and the wrapper must be untimed. Audio beds are mixed by the producer; this pipeline's music/SFX/loudness chain should stay in FFmpeg so `qa_check` keeps its LUFS and true-peak gates.

**Migration Effort**

medium

**Integration Steps**

- 1. Prove the toolchain offline: `npx --yes hyperframes@0.9.54 doctor --json` and record the memory/totalMemMb line, because it decides the worker profile.
- 2. Build one composition from a real SKU (16 cuts, 46 px captions, header/pill/footer) and gate it with `lint` until errors are 0 and `check --safe-zone reels` stops emitting the failures you care about.
- 3. Render it twice — once with defaults, once with `--workers 4 --no-low-memory-mode` — and keep the faster one only if memory stayed above ~1.5 GB available during the run.
- 4. Compare the HyperFrames frame against the FFmpeg frame at the same timestamp for the same caption text and layout, before changing the pipeline (this is the same discipline as eval_run's SSIM baseline).
- 5. Wrap the render in `render_video.py`'s run-directory convention (<sku>_<stamp>/) so every render stays immutable and comparable.
- 6. Re-encode the output to the WhatsApp envelope (Main@4.2, CRF 21/2.5 Mbps, faststart) and assert the byte size against the existing 16 252 928-byte guard.
- 7. Add a worker/memory preflight to `doctor`'s contract: if MemAvailable < ~1.5 GB, refuse to start rather than swap-thrash for six minutes.
- 8. Keep `PRODUCER_LOW_MEMORY_MODE=false` out of CI unless the runner is known to have ≥16 GB, because on a genuinely small box it will OOM instead of running slowly.

### Constraints

**Requirements**

- WSL2 Ubuntu 24.04.4, kernel 6.18.58.1-microsoft-standard-WSL2 (measured with `uname -a`).
- Node.js ≥ 22 — this box has v24.22.1 (doctor) / v24.22.2 (shell), npm 11.19.1.
- FFmpeg + ffprobe on PATH — 6.1.1-3ubuntu5 present; `doctor` adds a caution that 'loudness numbers are only comparable on 8.0.1', so HF-side LUFS readings are not interchangeable with the pipeline's FFmpeg 6.1 numbers.
- A Chrome build — the bundled chrome-headless-shell 152.0.7977.30 (261 MB) is downloaded on first use; /usr/bin/google-chrome 147.0.7727.101 also works but is not what HF uses.
- RAM: 7.8 GB total (7941 MB), 3.1–4.0 GB available at idle, 2 GB of WSL swap. The memory threshold that flips the low-memory profile is 8192 MB, so this machine is 251 MB under it.
- Disk: 853.6 GB free on /dev/sdd; ~275 MB of tooling caches + ~40 MB of extraction cache + ~42 MB per render.
- /dev/shm: 3970 MB — no need for Docker-style --shm-size tuning.
- No discrete GPU and no CUDA; the reachable accelerator is the Ryzen 7 5825U iGPU through WSL's D3D12 bridge (/dev/dxg, /dev/dri/renderD128) — ANGLE uses it for rasterisation and FFmpeg's h264_vaapi uses it for encoding (both verified working).

**Limitations**

- The default render on this machine is 3.6x slower than it needs to be, silently: totalMemMb 7941 ≤ thresholdMb 8192 activates the low-memory profile, which pins workerCount to 1 and forces screenshot capture. Measured 391 s vs 107 s for the identical project.
- The worker formula in the brief (auto = max(6, min(16, cores/8)) = 6) never runs here — the memory profile short-circuits worker selection before that arithmetic is reached. 'auto' means 1 on this box, and the CLI prints 'auto workers (16 cores usable)', which reads as if it had chosen 6.
- Memory per worker is ~414 MB PSS for a 1080x1920 composition, not the ~256 MB documented; six workers is therefore ~2.5 GB and leaves under 1 GB of headroom on a 4 GB-available desktop.
- Swap is a 2 GB file and the machine already showed 1.28 GB of swap in use after a heavy session; once MemAvailable falls near 1 GB, capture latency degrades and the render stalls rather than failing fast.
- WSL2 memory is not capped (cgroup memory.max = max), so WSL2 can balloon into Windows RAM; a 6-worker render with 48 chrome processes is the kind of load that makes the Windows host page-file thrash even though Linux reports success.
- Every render serialises per user through a lock file, so a batch of SKUs is sequential — 4 workers do not overlap two videos.
- A composition that never registers a GSAP timeline pays a fixed ~45 s player-ready wait unless it declares `data-no-timeline`; on a 6-minute default render that is noise, on a 107 s render it would be 40%.
- Only one capture implementation is fast: BeginFrame requires chrome-headless-shell and no resolution upscale, and the low-memory profile disables it ('BeginFrame did not run. Needs chrome-headless-shell and no --resolution upscale').
- Hardware GPU is available but only through explicit ANGLE flags: HyperFrames' own WSL probe finds it by retrying without WAYLAND_DISPLAY and launching with `--use-gl=angle --use-angle=gl-egl`. A hand-rolled Chrome invocation with default flags gets SwiftShader and ~30% less capture throughput.
- The sandbox question is moot for renders: the producer passes `--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage` on every capture launch, so renders never exercise it. Outside HyperFrames the machine is also fine — /usr/bin/google-chrome 147 starts and screenshots with no flags, the setuid helper /opt/google/chrome/chrome-sandbox is 4755, unprivileged user namespaces are unrestricted (`unshare -Ur true` succeeds, max_user_namespaces 31729, no apparmor_restrict_unprivileged_userns key) — so the WSL sandbox is a non-issue in both directions.
- FFmpeg 6.1.1 is old enough that HF warns loudness numbers are only comparable on 8.0.1; do not move loudness QA into HyperFrames without upgrading and re-baselining.
- The encoder is not the bottleneck at 1–4 workers, so the hardware path is a second-order lever: h264_vaapi encoded the same 1680 frames in 21.1 s against 133.7 s for libx264 medium, but HF streams encode during capture, so only a 6+ worker render makes encode the wall.

**Licensing**

Apache-2.0 for CLI, producer, engine and catalog; local rendering is free and needs no HeyGen account or API key. Rendered MP4s carry two container tags (hyperframes_renderer, hyperframes_version) that can be stripped with `ffmpeg -map_metadata -1 -c copy`. The paid boundary is hosted, not local: HeyGen voices/avatars, `cloud render`, and the Studio app's tiers (Free: 3 AI video clips + 5 cloud exports/month; Pro $25/month for unlimited cloud exports). Fonts and footage keep their own licences — the compile step inlines Google Fonts (Inter here), which is fine for OFL families and a licensing question for anything else.

### Evidence

**Confidence**

0.9

**Sources**

- https://hyperframes.heygen.com/help.md — the 'The render is slow under WSL2' section: software-WebGL fallback, the exact log lines, `usermod -aG render`, render-from-Windows advice; also the 45 s player-ready timeout and `data-no-timeline`.
- https://hyperframes.heygen.com/guides/performance.md — first-party 300-frame 1920x1080 measurement (25.0 s vs 9.8 s) and the advice to benchmark worker settings.
- https://hyperframes.heygen.com/deploy/overview.md — the CLI → Producer → Engine → Cloud → Lambda/Cloud Run decision table that frames 'local is the default'.
- https://hyperframes.heygen.com/faq.md — Apache-2.0, 'rendering on your own computer is free and needs no account', Studio Free/Pro cloud-export quotas.
- https://hyperframes.studio/pricing — linked as the plan source (Free plan: 3 AI video clips + 5 cloud exports/month; Pro $25/month).
- https://github.com/heygen-com/hyperframes — upstream repository for version and issue context (0.9.54 installed via npm).
- https://docs.github.com/en/billing/concepts/product-billing/github-actions — GitHub Actions is now billed as included compute usage: GitHub Free = $12/month, Linux 2-core $0.006/min, Linux 1-core $0.002/min, arm64 $0.005/min, Windows $0.010/min, macOS $0.062/min, artifacts 500 MB on Free, public-repo standard runners free.
- https://aws.amazon.com/lambda/pricing/ — free tier 1M requests + 400,000 GB-s/month, $0.0000166667/GB-s after, memory 128–10,240 MB, ephemeral storage 512–10,240 MB.
- Local measurements (2026-11-15): `npx hyperframes@0.9.54 doctor --json`, `lint`, `check --safe-zone reels`, three timed renders at 1/4/6 workers, a CDP capture benchmark at 1080x1920 (software vs ANGLE, 1/2/4 concurrent workers), and `ffmpeg` encode + WhatsApp re-encode timings on this Ryzen 7 5825U / WSL2 box.

**Open Questions**

- Whether 8 workers buys anything over 6: the curve was still improving slightly at 6 (100.3 s) but memory headroom was down to 2.9 GB, and no test was run above 6 to avoid destabilising the session.
- Whether the Windows host's memory pressure (the WSL2 balloon is uncapped) causes intermittent stalls on long batches — a single 28 s render cannot show that; a 10-SKU batch with the host idle vs busy would.
- Whether `--quality looks` (CRF 16) is the right master for this pipeline or whether the FFmpeg re-encode should be fed a ProRes/PNG-sequence intermediate; not A/B'd here.
- Whether HF's hardware-GPU path changes anything on a shader-heavy composition (the measured 1.4x was on a composition with no WebGL); the docs claim 5–50x for actual software-WebGL workloads, which was not reproduced.
- Whether FFmpeg 8.0.1 (as `doctor` recommends) changes the loudness numbers enough to re-baseline qa_check; untested, and it is a system-wide upgrade.
- Whether the extraction cache actually pays off across SKUs that reuse the same clips — measured only for one project.

### Other Info

**Known Issues**

- Measured: `npx hyperframes@latest` resolved to a version that did not exist yet (ETARGET for 0.9.55) for several minutes during research; pin 0.9.54. Symptom is an npm error before any HyperFrames code runs.
- Measured: lint reports `media_missing_id` — '<video> has data-start but no id attribute. The renderer requires id to discover media elements, so this video will be FROZEN in renders.' Silent bad output, not a crash.
- Measured: lint reports `video_nested_in_timed_element` as an error when a <video> is inside a wrapper that also carries data-start; the fix hint is to time the video and leave the wrapper untimed.
- Measured: `font_face_local_only` warning when a composition declares an @font-face with only `src: local(...)`; a local render silently uses whatever is installed, so text metrics are not reproducible across machines.
- Documented (help.md): WSL2 software-WebGL renders are 'several times slower on a long or effect-heavy video', with the exact log line `[hyperframes] WSL: the GPU check found no hardware GPU...`; the documented remedy is `usermod -aG render` or rendering from Windows.
- Documented (help.md): a composition without a GSAP timeline adds a fixed ~45 s to every render via the player-ready timeout unless it carries `data-no-timeline`.
- Documented (CLI docs): `--resolution` refuses non-MP4, forbids HDR, and needs an exact aspect match plus integer upscale — a 1080x1920 composition cannot be pushed to an arbitrary size.

**Performance And Cost**

All figures measured on this machine on 2026-11-15 with a 28.0 s, 1080x1920@60 composition (1680 frames, 16 cuts of 5 real 720x1280@60 clips, kinetic captions, header/pill/footer), unless labelled otherwise.

WHOLE-RENDER, identical project, three worker configurations:
• auto / low-memory profile (workerCount 1, captureMode screenshot): render 391.2 s, wall 403.3 s of which capture+encode 380.7 s, CPU 212% of 1600%, min MemAvailable 1.9 GB, 16 chrome processes, 42.0 MB output.
• `--workers 4 --no-low-memory-mode` (captureMode beginframe): render 107.6 s, wall 119.4 s of which capture+encode 102.2 s, CPU 711%, min MemAvailable 3.2 GB, 32 chrome processes, 42.5 MB output.
• `--workers 6 --no-low-memory-mode`: render 100.3 s, wall 112.8 s, capture+encode 94.7 s, CPU 822%, min MemAvailable 2.9 GB, 48 chrome processes, 42.6 MB output.
Fixed stages (any worker count): compile 2.9 s, source extraction 3.6 s, browser probe 0.6 s, setup 2.5 s, assemble 0.5 s. So ~10 s is irreducible overhead and the rest is frames.

THROUGHPUT:
• Effective capture rate 1680/380.7 = 4.4 fps (1 worker, screenshot) vs 1680/94.7 = 17.7 fps (6 workers, beginframe).
• Raw CDP seek+JPEG-screenshot at 1080x1920, single worker: 6.1 fps software, 8.6 fps hardware. Two concurrent workers 5.5 fps each (11 fps aggregate); four concurrent 5.3 fps each (21 fps aggregate) at only 41% CPU — i.e. per-frame latency, not CPU, is the limit up to ~4 workers.
• Native FFmpeg H.264 encode of 1680 synthetic 1080x1920 frames: preset medium CRF 18 = 133.7 s at 1041% CPU; preset fast = 81.8 s; `h264_vaapi` on /dev/dri/renderD128 at 6 Mbps = **21.1 s** at 148% CPU (High@4.2, yuv420p, 20.1 MB). HF streams encode during capture, so the CPU encode is hidden at 1–4 workers and only becomes the wall past ~6.

SIZE AND DISK:
• `--quality looks` (CRF 16) 28 s reel: 42.0–42.6 MB, H.264 High@4.2, ~12.6 Mbps. WhatsApp re-encode to Main@4.2 CRF 21/2.5 Mbps: 8.54 MB in 33.9 s.
• Capture intermediates: measured 64 KB/frame JPEG for UI-like frames (≈104 MB per 1680 frames) and 484 KB/frame for noisy photographic content (≈812 MB) — plan for 0.1–0.8 GB of frame traffic per reel depending on how detailed the source is.
• Persistent caches: chrome-headless-shell 261 MB, fonts 1.1 MB, extraction cache 39 MB for this project (default 2 GiB budget).

MONEY: zero. Apache-2.0, no account, no per-render fee, no watermark. The only cost is ~2 minutes of a Ryzen 7 5825U and ~50 MB of disk per reel.

FREE-TIER FALLBACKS (optional, measured costs where possible, published prices where not — none of these were executed):
• GitHub Actions. GitHub Free now bills Actions as **$12/month of included compute**, not a minute count; a standard Linux 2-core runner costs $0.006/min ($0.002 1-core, $0.005 arm64, $0.010 Windows, $0.062 macOS), so the allowance is ~2,000 Linux minutes/month. Standard runners in **public** repositories are free and do not draw it down; artifact storage on Free is 500 MB. A 28 s reel at 4 workers takes ~2.5–4 min on a 4-vCPU runner (the standard ubuntu-latest spec, fewer cores than this box, plus checkout/setup), so ~40–60 reels/month fit inside the allowance and each reel past it costs ~$0.02–0.03. This is the cheapest real fallback: no image build, `npx hyperframes@0.9.54 render` just runs in a job. Caveats: 4 vCPU means 2–3 workers, a 6-hour job ceiling per job, and the 42 MB artifact needs a release/upload step rather than artifact storage.
• AWS Lambda. The free tier is 1M requests + **400,000 GB-seconds/month**, then $0.0000166667/GB-s; memory caps at 10,240 MB and ephemeral storage at 10,240 MB (512 MB free). One 28 s reel at 10 GB × ~240 s = ~2,450 GB-s, so ~160 free renders/month and ~$0.04 per render afterwards — cheap, but only if the render fits the 15-minute invocation ceiling and the 10 GB RAM ceiling. HyperFrames ships `@hyperframes/aws-lambda` + `lambda deploy/render`, which needs a container image in ECR and a Chromium layer, i.e. it needs Docker to build — the brief says treat Docker as unavailable, so this path is documentation-only here.
• HyperFrames-hosted. `cloud render` requires a HeyGen account (`auth login`); the Studio Free plan includes **5 cloud exports/month** (plus 3 AI video clips), Pro is $25/month for unlimited cloud exports, and 4K cloud renders are billed at 1.5×. There is no documented à-la-carte per-render price for extra cloud renders, so the practical statement is: 5 free cloud renders/month, then either $25/month or buy extra credits at an undisclosed rate. [uncertain]
• Net recommendation: local at 4 workers is 1 m 48 s and free, so it wins on both axes; GitHub Actions on a public repo is the only fallback that is free, unlimited and needs no infrastructure of your own.

---

<a id="migration-and-ecosystem-comparison"></a>
## Migration and Ecosystem Comparison

### Basic Info

**Title**

Migration and Ecosystem Comparison

**Domain**

Ecosystem: choosing and migrating a text/motion renderer for a Python + FFmpeg reel shop

**One Liner**

HyperFrames, Remotion, Motion Canvas, After Effects/Lottie and plain FFmpeg compared on license, authoring, measured render cost and migration effort for 28-second vertical reels.

**Official Sources**

- https://hyperframes.heygen.com/guides/hyperframes-vs-remotion
- https://github.com/heygen-com/hyperframes
- https://www.remotion.dev/docs/license/pricing
- https://www.remotion.dev/docs/license/faq
- https://www.remotion.dev/docs/renderer/render-media
- https://www.remotion.dev/docs/transparent-videos
- https://motion-canvas.github.io/docs/rendering
- https://github.com/motion-canvas/motion-canvas
- https://lottiefiles.github.io/lottie-web/
- https://helpx.adobe.com/after-effects/system-requirements.html

**Maturity**

beta

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

It turns 'which tool should render our text?' into a decision with numbers instead of vibes: who owns the source (HTML, React, canvas code, AE project or FFmpeg filter strings), what each license actually costs a small shop, how long 1680 frames of 1080x1920@60 text takes on this exact CPU-only WSL2 box, what a migration really involves, and which option leaves the existing Python/FFmpeg pipeline intact. The conclusion is narrow and useful: HyperFrames for the text layer, FFmpeg for footage/audio, Remotion only if the shop standardises on React, and no migration at all for Motion Canvas or After Effects in this environment.

**Key Features**

- Authoring model is the real fork in the road: HyperFrames = HTML + CSS + seekable JS timelines (no build step, a file you can open in a browser); Remotion = React/TSX components as a pure function of the frame (bundler required); Motion Canvas = TypeScript generator functions drawing on a canvas scene graph (no DOM/CSS); AE/Lottie = a keyframe timeline exported to JSON and played by lottie-web; FFmpeg/PIL = imperative per-frame drawing with no layout engine.
- Licensing: HyperFrames is Apache-2.0 with no seat count and no per-render fee. Remotion is source-available under the Remotion License: free for individuals, organisations of up to 3 people, non-profits and evaluation; otherwise Remotion for Creators is $25/month per seat and Remotion for Automators is $0.01 per render with a $100/month minimum (Enterprise from $500/month). Motion Canvas and lottie-web are MIT. After Effects is a proprietary subscription and runs only on macOS/Windows. FFmpeg/Pillow are free.
- Measured head-to-head on this machine, same 2 s overlay (1080x1920@60, 120 frames, three text blocks, VP9+alpha): HyperFrames 0.9.54 rendered in 61.5 s (1 worker, low-memory profile, 115.7 KB), Remotion 4.0.543 in 110 s cold (bundling included), 32.9 s warm at `--concurrency=1` and 30.0 s at `--concurrency=4` (84.3 KB). Both produced `ALPHA_MODE=1` WebM that FFmpeg composites correctly with `-c:v libvpx-vp9`.
- Per-frame economics at 28 s: HyperFrames' static-frame dedup makes the overlay cheap - the 8 s benchmark reused 348/480 frames (73%) at an average of 146 ms/frame, so 1680 frames extrapolate to ~4-6 min; Remotion renders every frame at ~250-275 ms here (no dedup), i.e. ~7 min at concurrency 1, and concurrency 4 gave no gain on this workload.
- The `/remotion-to-hyperframes` skill is a real migration path but not for us: it ports an existing Remotion codebase (maps `useCurrentFrame`/`interpolate` to timeline tweens, `Sequence` to clips, frames to seconds), refuses what it cannot translate faithfully (`useState`/`useEffect` state machines, async metadata, third-party React UI) and verifies by rendering both versions and diffing frame by frame. It is an on-demand skill (76 files in the published manifest, not part of the core set installed by `init`), so it costs nothing to skip. This shop has no Remotion project, so the skill has nothing to port; the equivalent for an existing MP4 is `hyperframes init --video` (import + optional transcribe) or the `reverse-any-video` skill.
- Asset import paths that do exist: `init --video <file>` converts/keeps an MP4 and scaffolds a composition around it; `add <block>` installs catalog blocks with their dependencies; `capture <url>` turns a live site into editable components; Lottie JSON, Three.js scenes, canvas and WAAPI all have adapters in the runtime.
- Ecosystem size (npm downloads, last 30 days to 2026-11-14): lottie-web 39.0M, remotion 13.5M, hyperframes 5.2M, @motion-canvas/core 56k. GitHub: hyperframes 84.7k stars (repo created 2026-03-10, 293 open issues), remotion 73.4k (created 2020-06-23, 208 open issues), motion-canvas 19.9k (created 2022-08-03, 179 open issues).
- Release health: HyperFrames published 642 npm versions since 2026-03-23 and shipped 0.9.54 -> 0.9.55 on the day of this research, but is still pre-1.0. Remotion shipped 4.0.543 on 2026-11-11. Motion Canvas' last stable release is 3.17.2 (2024-12-14) with only a 3.18.0-alpha.0 (2025-02-16) since - effectively dormant for ~21 months.
- Hiring pool is inverted from what the star counts suggest: React developers are plentiful (Remotion), but HTML/CSS authors are even more plentiful and every web dev can read a HyperFrames file, while Motion Canvas has a small niche following and AE motion designers are common but expensive and do not write pipeline code. For an agent-authored pipeline, HTML is the format LLMs produce most reliably.

**Technical Mechanism**

HyperFrames and Remotion share one architecture: bundle/serve a page, open it in headless Chrome, set the clock to a frame time, capture, and pipe frames to FFmpeg. HyperFrames seeks registered timelines (`window.__timelines`) and captures with BeginFrame where available; Remotion evaluates the component for frame N and captures with `Page.captureScreenshot`, running `--concurrency` browser workers (default half the CPU threads, capped at 8). Motion Canvas renders into a `<canvas>` from a generator-driven scene graph and exports an image sequence (or via `@motion-canvas/ffmpeg`) from its editor; there is no documented non-interactive render command. Lottie is a format, not a renderer: something (a browser, `lottie-web`, or a native player) must rasterise the JSON. FFmpeg drawtext/libass renders text inside the encoder with no layout engine, which is exactly why this repo grew a PIL compositor instead.

**Text And Motion Capabilities**

Ceiling by tool, for 1080x1920 mobile type. FFmpeg drawtext: single-line text, manual x/y, `textfile=` for content, no wrapping, no kerning control, no per-glyph animation; libass/ASS adds styling and karaoke `\k` timing but no layout engine and no real easing. PIL (today's pipeline): full TTF control and pixel-exact placement, but one static bitmap plus per-word colour blits - no wrapping, no masks, no gradients, no springs. Lottie: vector shapes and AE-authored keyframes, per-character text animators exist in AE but bodymovin export is lossy for effects, fonts must be installed, and there is no data-driven layout. Motion Canvas: full canvas 2D/WebGL drawing with a tween/easing library and signals, excellent for procedural diagrams, but you write the layout maths yourself. Remotion: the whole CSS/React ecosystem plus `spring()`, `interpolate()`, `@remotion/google-fonts`, and per-word splitting via libraries; the most expressive of the code options if you accept React. HyperFrames: the same CSS/GSAP expressiveness as Remotion without React - `power3.out`, stagger, clip-path wipes, gradient text, per-glyph transforms, SplitText-style spans, and shader/Lottie/Three.js blocks from the catalog; measured alpha fidelity against its own PNG still was 0.31/255 MAE, so VP9 alpha does not soften 46 px type.

**Output Formats**

- HyperFrames: MP4 (H.264/H.265, 4:2:0 or 4:4:4), WebM VP9 + alpha, MOV ProRes 4444 + alpha, PNG sequence, GIF, HLS; measured overlay sizes 115.7 KB for 2 s and 392 KB for 8 s.
- Remotion: MP4 (H.264/H.265), WebM VP8/VP9 with `yuva420p` alpha (needs `--image-format=png --pixel-format=yuva420p`), ProRes, GIF, PNG sequence, WAV; also Lambda/Cloud Run/Vercel render targets.
- Motion Canvas: image sequence (PNG) or video via the `@motion-canvas/ffmpeg` exporter; transparent background is supported by leaving the project background empty.
- After Effects/Lottie: AE renders anything (with Media Encoder); Lottie delivers JSON + a player - video only after a separate rasterise+encode step, and bodymovin carries no audio and no footage.
- Plain FFmpeg/PIL: whatever the encoder writes (this pipeline: H.264 Main@4.2, yuv420p, AAC 128k, +faststart, 13.5 MB for 28 s).

### Usage & API

**Setup And Install**

HyperFrames: `npx --yes hyperframes@0.9.54 render ...` with Node >= 22, FFmpeg and a Chrome (system Chrome worked here; otherwise a pinned headless-shell is downloaded) - zero install into the Python project.

Remotion: `npm i remotion @remotion/cli react react-dom` plus an entry point and a bundler; measured 242 packages / 256 MB of `node_modules` and 53 s install for a minimal single-composition project (a full template is ~950 MB per HyperFrames' own comparison).

Motion Canvas: `npm create @motion-canvas@latest` scaffolds a Vite project; rendering is started from the editor's Video Settings tab, with `@motion-canvas/ffmpeg` as the video exporter.

AE/Lottie: After Effects (macOS/Windows, subscription) + the Bodymovin extension to export JSON, then `npm i lottie-web` plus your own headless-Chrome harness to rasterise frames.

Plain FFmpeg: already installed (`ffmpeg 6.1.1` here); drawtext needs libfreetype, ASS needs libass.

**Minimal Code Example**

The same 3-second title card, written three ways (all three were rendered on this machine for the benchmark):

```html
<!-- HyperFrames: timing is markup, motion is a paused GSAP timeline -->
<div id="stage" data-composition-id="card" data-start="0" data-width="1080" data-height="1920"
     data-duration="2" data-fps="60">
  <div id="title" class="clip" data-start="0" data-duration="2" data-track-index="0">IPHONE 14 AZUL</div>
</div>
<script>
  const tl = gsap.timeline({paused:true});
  tl.fromTo("#title", {y:40, opacity:0}, {y:0, opacity:1, duration:0.35, ease:"power3.out"}, 0);
  window.__timelines = {card: tl};
</script>
```

```tsx
// Remotion: the component is a function of the frame number
const Card = () => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 21], [0, 1], {extrapolateRight: "clamp"});
  const y = interpolate(f, [0, 21], [40, 0], {extrapolateRight: "clamp"});
  return <AbsoluteFill style={{backgroundColor: "transparent"}}>
    <div style={{position:"absolute", left:65, top:118, fontFamily:"InterBlack", fontSize:58,
                 color:"#fff", opacity:o, transform:`translateY(${y}px)`}}>IPHONE 14 AZUL</div>
  </AbsoluteFill>;
};
// plus: <Composition id="Card" component={Card} durationInFrames={120} fps={60} width={1080} height={1920} />
```

```bash
# Plain FFmpeg: no wrapping, no easing, no per-glyph control
ffmpeg -f lavfi -i color=c=0x1f2937:s=1080x1920:r=60:d=2 \
  -vf "drawtext=fontfile=assets/fonts/Inter-Black.ttf:text='IPHONE 14 AZUL':fontsize=58:fontcolor=white:x=(w-text_w)/2:y=118" \
  -c:v libx264 -pix_fmt yuv420p out.mp4
```

```bash
# Render commands used for the benchmark
npx --yes hyperframes@0.9.54 render . --fps 60 --format webm --workers 1 --low-memory-mode --output out/hf.webm
npx remotion render Overlay out/rem.webm --concurrency=1   # image-format png + yuva420p + vp9 in remotion.config.js
```

**Cli Or Api Surface**

- HyperFrames CLI: `render`, `lint`, `check`, `snapshot`, `still`-equivalent stills, `add`, `catalog`, `init`, `capture`, `transcribe`, `doctor`, `skills`, `timeline`, `benchmark`; exit codes 0/1/2 and `@hf-progress` on stdout.
- Remotion CLI: `npx remotion render <id> <out>` with `--concurrency`, `--codec`, `--pixel-format`, `--image-format`, `--crf`, `--frames`, `--props`, `--log`; plus `npx remotion benchmark`, `npx remotion studio`, `npx remotion lambda`.
- Remotion Node API: `bundle()`, `selectComposition()`, `renderMedia()`, `renderStill()`, `renderFrames()`, `renderMediaOnLambda()`; `inputProps` + `calculateMetadata()` for per-video data; `frameRange` accepts an array of ranges since 4.0.502.
- Motion Canvas: the editor (Vite dev server) is the interface; `@motion-canvas/ffmpeg` adds a video exporter; the project config (`vite.config.ts`) registers scenes, and rendering settings (range, resolution, fps, exporter) live in the UI.
- AE/Lottie: Bodymovin export to JSON, `lottie-web` `loadAnimation()` + `goToAndStop(frame, true)` for deterministic seeking, or a native player (rlottie/thorvg) for speed.
- FFmpeg: `drawtext`, `subtitles`/`ass`, `overlay`, `zoompan`, `xfade`; the existing pipeline instead draws with PIL and pipes raw bgr24.

**Key Config Options**

- HyperFrames: `--format webm` + `--fps 60` for an alpha overlay; `--workers`/`--low-memory-mode` for RAM; `--variables-file` + `--strict-variables` for typed data; `--no-wait` to fail fast against the per-account render lock.
- Remotion: `Config.setVideoImageFormat("png")`, `Config.setPixelFormat("yuva420p")`, `Config.setCodec("vp9")` are all three required for alpha; `--concurrency` defaults to half the threads capped at 8.
- Motion Canvas: background must be left empty for transparency; resolution/scale/fps are per-project render settings, not CLI flags.
- AE/Lottie: no runtime knobs - the JSON is the contract; text as text requires the same font installed on the rendering machine.
- FFmpeg drawtext: `fontfile`, `fontsize`, `fontcolor`, `box`, `borderw`, `enable='between(t,a,b)'`; ASS adds `\k` karaoke timing but no layout.

### Integration

**Pipeline Fit**

Only one of the five actually fits without bending the stack. HyperFrames augments the existing pipeline: it takes over the text layer (the PIL overlay/captions), emits a transparent WebM that the current FFmpeg encode composites in one pass, and leaves `beat_grid.py`, `edit_plan.py`, the audio mix, `runs.json` and 29-check QA untouched. Remotion could do the same job with equal output quality but replaces the authoring language with React/TSX, adds a bundler and a licensing decision - it is a fit only if the shop is already a React shop. Motion Canvas is a poor fit: no DOM/CSS, no documented headless render, and a project that has not shipped a stable release since December 2024. After Effects/Lottie is not a fit on Linux at all (AE is macOS/Windows only) and Lottie cannot carry footage or audio. Plain FFmpeg drawtext is what the pipeline already outgrew: it stays the zero-dependency fallback for a single static line, not for kinetic captions.

**Decision matrix** (0-5, higher is better for this shop: Python + FFmpeg, CPU-only WSL2, 28 s vertical reels, free tiers only):

| Criterion | HyperFrames | Remotion | Motion Canvas | AE + Lottie | FFmpeg/PIL (today) |
| --- | --- | --- | --- | --- | --- |
| License cost at 4+ people | 5 (Apache-2.0, $0) | 3 ($0.01/render, $100/mo floor) | 5 (MIT) | 2 (~$23/seat/mo + a Windows box) | 5 (free) |
| Authoring fit (Python shop, agent-authored) | 5 (HTML/CSS, no build) | 2 (React/TSX + bundler) | 2 (TS canvas code) | 1 (GUI, not code) | 4 (Python, but no layout engine) |
| Renders on this exact box | 4 (146 ms/frame measured, 4-6 min per overlay) | 3 (~7 min estimated, RAM-bound) | 2 (headless path undocumented) | 0 (no Linux build) | 5 (158-186 s full reel) |
| Typography / motion ceiling | 5 (CSS + GSAP + shaders) | 5 (CSS + React + spring) | 4 (canvas 2D/WebGL) | 5 authoring / 2 runtime fidelity | 2 (one static bitmap) |
| Per-video data injection | 5 (typed variables + generated HTML) | 5 (typed props) | 2 (regenerate TS) | 2 (JSON patching) | 2 (Python strings) |
| Transparent overlay output | 5 (VP9/ProRes alpha, verified) | 4 (needs 3 settings to agree) | 3 (empty background) | 2 (no video container) | 4 (composite only) |
| Integration effort (5 = least) | 4 (2-3 days) | 3 (4-6 days here) | 1 (4-7 days + automation debt) | 1 (5-8 days + second OS) | 5 (0 days) |
| Ecosystem / hiring pool | 3 (pre-1.0, but 84.7k stars, 5.2M dl/mo) | 5 (13.5M dl/mo, 6 years of tutorials) | 2 (dormant since Dec 2024) | 4 (designers, not engineers) | 5 (ubiquitous) |
| Maintenance risk (5 = lowest) | 3 (0.9.x, weekly releases) | 4 | 1 | 3 | 5 |
| **Total** | **39** | **34** | **22** | **20** | **37** |

Read the totals honestly: staying on FFmpeg/PIL scores 37 because it costs nothing and never breaks - it loses only on the two criteria this project exists for (typography ceiling, per-video layout). HyperFrames wins at 39, and the practical answer is the hybrid the matrix cannot express: HyperFrames for the text layer, FFmpeg/PIL for footage, audio and encode, i.e. adopt HF's five scoring wins without giving up FFmpeg's five. Remotion is a close second and becomes the better choice the moment the shop hires React developers or already owns Remotion compositions.

**Realistic effort for this shop** (person-days, assuming the spike is run first):

| Path | Effort | Why |
| --- | --- | --- |
| HyperFrames text overlay only | 2-3 days | 0.1 spike (measured 45 min) + 0.5-1 generator + 0.5 renderer + 0.5-1 QA/eval + 0.25 docs |
| HyperFrames for the whole reel (cuts + audio too) | 5-8 days | re-authoring the beat edit as clips, losing `loudnorm`/ducking control - not recommended |
| Remotion text overlay | 4-6 days here (2-3 if React-fluent) | TS project, props schema, bundler/render server, alpha config, compositing |
| Motion Canvas overlay | 4-7 days | canvas layout by hand, no headless CLI, Playwright automation of the editor |
| AE -> Lottie overlay | 5-8 days + hardware | AE license, a macOS/Windows host, JSON templating, plus a lottie-web rasteriser and FFmpeg step |
| Plain FFmpeg drawtext/ASS | 0.5 day | already possible; the ceiling is the reason this research exists |

**Python Integration**

HyperFrames: subprocess only - `npx --yes hyperframes@0.9.54 render <project> --fps 60 --format webm --output <run>/overlay/overlay.webm`, parse `@hf-progress` on stdout, keep stderr for warnings, enforce your own timeout, kill the process group, and treat exit 2 as a bug and exit 1 as a render failure. No Node service, no Python binding, no daemon. That is roughly 120 lines of Python and it was proven end to end in the integration spike.

Remotion: also subprocess (`npx remotion render <id> <out> --props=props.json`), but the project needs a Node toolchain, a bundler pass per render unless you cache the bundle, and `--props` is JSON-only so word timings travel the same way they would with HyperFrames. The Node API (`bundle` + `renderMedia`) is the equivalent of the HF producer API and would need a long-lived Node bridge.

Motion Canvas: Python would have to drive the editor with Playwright (click Render in the Video Settings tab) or reverse-engineer its Vite plugin protocol; that is automation debt, not integration.

AE/Lottie: Python can template the Lottie JSON (text, colours, timings) and then needs a headless Chrome + `lottie-web` harness with `goToAndStop` per frame to rasterise, plus FFmpeg to encode - i.e. you rebuild a worse HyperFrames, with a paid AE license upstream.

Plain FFmpeg/PIL: the current in-process path - fastest to run, hardest to make beautiful.

**Data Injection**

HyperFrames: `--variables-file` + `data-composition-variables` for typed scalars (title, price, footer, accent, safe-zone mode) with `--strict-variables`; structural data (card count, word timings, root duration) baked into the generated HTML as an escaped JSON block because the runtime reads those before scripts run. Remotion: `inputProps` validated with Zod, optionally reshaped by `calculateMetadata()`, passed as `--props=file.json`; typed, but every timing still becomes React props. Motion Canvas: project-level variables are code, so per-video data means regenerating TS or reading a JSON file at runtime - no built-in contract. Lottie: text/colour slots are whatever the AE author exposed; anything dynamic is a JSON patch. FFmpeg drawtext: `textfile=` per line or ASS file generation; no structure, no validation.

**Footage And Asset Handling**

HyperFrames: footage can live inside a composition (`<video class="clip">` with `data-start`/`data-duration`, source frames pre-extracted by FFmpeg), but for this pipeline it should not - the overlay stays text-only, so there is no extraction cache and no 2 GiB budget. Fonts must be local files (`@font-face` on a copied TTF); audio never enters. Remotion: `<OffthreadVideo>`/`<Video>` from `@remotion/media`, `staticFile()` for assets, and the same pre-extraction model; audio can be mixed in-app. Motion Canvas: media via refs and scenes; images/video are drawn onto the canvas. Lottie: no footage, no audio. FFmpeg: footage is the pipeline's native currency (`normalize_clip`, `ClipReader`, rawvideo pipe).

**Migration Effort**

medium

**Integration Steps**

- Decide the boundary first: text in HyperFrames, footage/audio/encode in FFmpeg. Do not migrate cutting, grading or loudness - that is where the current pipeline is strongest and where HyperFrames has no advantage.
- Run the 45-minute overlay spike (see Python_Pipeline_Integration): one transparent composition, one WebM, one `-c:v libvpx-vp9` composite. If alpha and colours survive, the migration is real; if not, stop before writing a generator.
- Port the copy model, not the pixels: turn `group_words()` output into a card list with absolute times, and let HTML/CSS lay the text out. Delete `build_overlay()`'s auto-fit arithmetic only after the browser version passes the zone check.
- Write the generator + subprocess bridge (about a day), then re-ground QA (overlay alpha, coverage, ink, frame-count equality) and add overlay SSIM/alpha metrics to `eval_run.py` (about half a day).
- Re-promote `eval/<sku>_baseline.json` in the switching commit, run both renderers on the same SKU, and compare contact sheets side by side before flipping the default.
- If the shop already owns Remotion code, install `/remotion-to-hyperframes` (`npx skills add heygen-com/hyperframes --skill remotion-to-hyperframes` or `npx hyperframes skills update remotion-to-hyperframes`) and let it port and frame-diff the compositions; expect it to refuse stateful components rather than mistranslate them.
- If the source material is an existing finished MP4 rather than code, use `hyperframes init --video <file>` (imports and optionally transcribes) or the `/reverse-any-video` skill instead of hand-rebuilding the edit.
- Keep the old renderer behind a config flag for one release (`overlay.mode = pil|hyperframes`); the rollback is then a one-line change, not a revert.

### Constraints

**Requirements**

- HyperFrames: Node >= 22, FFmpeg/ffprobe, Chrome (system or downloaded headless-shell), ~2.5 GB RAM free for one worker plus the Python renderer, and a writable `~/.cache/hyperframes`.
- Remotion: Node >= 18 (22 recommended), FFmpeg (bundled or on PATH), ~250 MB-1 GB of `node_modules`, and a bundler step per project.
- Motion Canvas: Node + Vite, a browser to host the editor, and `@motion-canvas/ffmpeg` for video export.
- AE/Lottie: a macOS or Windows machine with After Effects (Linux is not supported), the Bodymovin extension, and a separate rasterisation harness for server-side rendering.
- FFmpeg/PIL: Python 3.10+, PyAV/OpenCV/Pillow (already in `.venv`), FFmpeg with libx264/libass/libfreetype.
- For every code-based option: fonts installed or embedded, because text metrics and glyph shapes change the render.

**Limitations**

- Only HyperFrames and Remotion render a DOM in headless Chrome, which is what makes CSS typography possible; Motion Canvas draws on a canvas (no CSS layout), Lottie replays keyframes (no logic), drawtext has no layout engine.
- Alpha output is a per-tool configuration, not a default: HyperFrames needs `--format webm` (or mov/png-sequence), Remotion needs PNG image format + `yuva420p` + VP8/VP9, Motion Canvas needs an empty background, Lottie has no video container, and FFmpeg needs an overlay input.
- Licensing is a headcount question, not a feature question: Remotion's free license covers individuals and organisations of up to 3 people; above that, automation costs $0.01/render with a $100/month floor. At ~100-300 reels/month that is $100/month, i.e. ~$1,200/year, against $0 for HyperFrames.
- After Effects is unavailable on Linux, so the AE path requires a second machine/OS in the loop and breaks the 'one box renders everything' property this pipeline relies on.
- Motion Canvas has not shipped a stable release since 2024-12-14 (last alpha 2025-02-16); adopting it in 2026 means owning an unmaintained renderer.
- Remotion's model needs React knowledge; every hour spent translating a layout into JSX is an hour not spent on the reel, and a Python shop has no React tooling to reuse.
- HyperFrames is pre-1.0 (0.9.x): the FrameAdapter interface is documented as experimental v0 and the CLI ships several times a week, so the version must be pinned and the render re-verified after an upgrade.
- None of these tools removes the need for a compositing step in this pipeline: the beat-cut footage stays in FFmpeg, so the migration is additive no matter which text renderer wins.

**Licensing**

HyperFrames: Apache-2.0 (repo LICENSE and npm `license: Apache-2.0`); no seat count, no per-render fee, no commercial threshold; the optional HeyGen cloud and the AWS/GCP drivers are separate paid services. Remotion: source-available under the Remotion License (GitHub reports `NOASSERTION`), free for individuals, organisations of up to 3 people, non-profits and evaluation, with Company licensing at $25/month per seat (Creators), $0.01/render with a $100/month minimum (Automators) or from $500/month (Enterprise); the definition of 'automation' explicitly includes `renderMedia()`, `npx remotion render` and the Player. Motion Canvas: MIT. lottie-web and Bodymovin: MIT, but After Effects itself is a proprietary subscription and its output is bound by Adobe's terms. FFmpeg: LGPL/GPL depending on the build flags (libx264 is GPL, which is fine for in-house rendering); Pillow is HPND. Third-party catalog blocks and GSAP plugins carry their own licenses - GSAP's free build covers the tweens used here.

### Evidence

**Confidence**

0.81

**Sources**

- https://hyperframes.heygen.com/guides/hyperframes-vs-remotion - the vendor's own comparison (authoring model, seeking vs frame-number model, license difference, the '12 min 30 s vs 11 min 50 s' agent benchmark, and the explicit list of what Remotion does better), plus the `/remotion-to-hyperframes` install command and what it maps/refuses.
- https://www.remotion.dev/docs/license/faq and https://www.remotion.dev/docs/license/pricing - free-license eligibility (individuals, <=3 people, non-profits, evaluation), $25/seat/month Creators, $0.01/render with $100/month minimum Automators, $500+/month Enterprise, and the explicit definition of an automation (`renderMedia()`, `npx remotion render`, ...).
- https://www.remotion.dev/docs/renderer/render-media - `concurrency` default 'half of the CPU threads, capped at 8', `inputProps`, `frameRange` array support, CRF/bitrate options.
- https://www.remotion.dev/docs/transparent-videos - the three required settings for alpha (PNG image format, `yuva420p`, VP8/VP9).
- https://www.remotion.dev/docs/performance - `--concurrency` guidance and `npx remotion benchmark`; no published wall-time numbers of its own.
- https://motion-canvas.github.io/docs/rendering and /docs/rendering/video - rendering is configured and started in the editor's Video Settings tab, transparent background by leaving it empty, `@motion-canvas/ffmpeg` 'relatively new'.
- GitHub API (2026-11-15): heygen-com/hyperframes 84,732 stars, 7,845 forks, 293 open issues, created 2026-03-10, Apache-2.0; remotion-dev/remotion 73,432 stars, 208 open issues, created 2020-06-23, license NOASSERTION; motion-canvas/motion-canvas 19,949 stars, 179 open issues, MIT, last push 2026-07-02.
- npm registry/API (2026-11-15): hyperframes latest 0.9.55, 642 versions since 2026-03-23, 5,223,278 downloads in the 30 days to 2026-11-14; remotion 4.0.543 (published 2026-11-11), 13,546,361 downloads; @motion-canvas/core 3.17.2 (published 2024-12-14, last alpha 3.18.0-alpha.0 on 2025-02-16), 56,222 downloads; lottie-web 39,047,971 downloads.
- Hands-on head-to-head, 2026-11-15, /tmp/opencode/hfspike and /tmp/opencode/remspike: the same 2 s 1080x1920@60 overlay rendered by HyperFrames 0.9.54 (61.5 s, 115.7 KB, `ALPHA_MODE=1`) and Remotion 4.0.543 (110 s cold / 32.9 s warm at concurrency 1 / 30.0 s at concurrency 4, 84.3 KB, `ALPHA_MODE=1`); Remotion's minimal install measured 242 packages / 256 MB / 53 s; both overlays decoded with `-c:v libvpx-vp9` and composited over #1f2937 identically.
- https://helpx.adobe.com/after-effects/system-requirements.html - system requirements list macOS and Windows only (no Linux), which rules AE out on this machine.
- Repo baseline: output/*/*/edit_plan.json `elapsed_sec` 158.4-185.9 for the current 28 s PIL/FFmpeg render, used as the 'stay put' cost.

**Open Questions**

- No 28 s end-to-end head-to-head was run for either framework; the 28 s figures are extrapolations from a measured 2 s (both tools) and 8 s (HyperFrames only) benchmark. A 1680-frame comparison on this box would settle it.
- Motion Canvas was not benchmarked and its headless-render story is inferred from the docs (no documented non-interactive command); a Playwright-driven editor render could change the effort estimate.
- The After Effects/Lottie path was not tested at all (no AE license, no macOS/Windows machine here); its cost and quality claims are documented, not measured.
- Remotion's real cost for this shop depends on headcount (<=3 people = free); that is a business fact, not a technical one, and was assumed rather than confirmed.
- Whether HyperFrames' 73% static-frame dedup holds for a real 28 s reel with 16 cards (more changes per second than the 8 s benchmark) is unmeasured.
- Whether Remotion scales better on a machine with more RAM (concurrency 8 at 1080x1920) - this 7.9 GB box could not test it, and the 4-worker run showed no gain at 2 s, which may be a workload-size artefact.

### Other Info

**Known Issues**

- HyperFrames: the per-account render lock serialises renders (a second render waits or fails with exit 1 under `--no-wait`); `--json` suppresses progress; the human progress bar is printed even to a pipe.
- Remotion: alpha requires three settings to agree (PNG frames, yuva420p, VP8/VP9) and silently produces an opaque file if one is missed; `--concurrency` above the RAM budget causes browser crashes rather than graceful degradation. [uncertain] the crash behaviour is inferred from Chrome memory use, not reproduced here.
- Motion Canvas: the FFmpeg exporter's own docs call it 'relatively new and may not have all the features you need'; there is no documented headless render entry point.
- After Effects/Lottie: bodymovin export loses effects (blurs, track mattes, some expressions), text animators are partially supported, and fonts must be present on the rendering host. [uncertain] documented by the Lottie ecosystem, not tested here (no AE license).
- FFmpeg drawtext: no wrapping, no kerning control, and font fallback through fontconfig makes output machine-dependent.

**Performance And Cost**

Measured on this box (WSL2, Ryzen 7 5825U 8C/16T, 7.9 GB RAM, software x264, Chrome on the integrated Radeon through ANGLE/D3D12, 2026-11-15). Same 2 s 1080x1920@60 overlay, 120 frames, three text blocks: HyperFrames 0.9.54 = 61.5 s wall (compile 2.9, setup 8.5, capture 49.5; 1 worker, low-memory profile), 115.7 KB; Remotion 4.0.543 = 110 s cold including bundling, 32.9 s warm at concurrency 1, 30.0 s at concurrency 4, 84.3 KB. Per frame that is ~410 ms (HF, no dedup benefit at this length) versus ~250-275 ms (Remotion, every frame rendered). At 28 s the ranking flips because HyperFrames dedupes static frames: 73% reuse at 146 ms/frame measured over 8 s (480 frames, 74.8 s) extrapolates to ~4-6 min for 1680 frames, while Remotion's ~250 ms/frame extrapolates to ~7 min and concurrency did not help this workload at 4 workers. The current PIL/FFmpeg render is 158-186 s for 28 s, so either code-based option roughly doubles to triples wall time - the text layer becomes the slowest stage. Money: HyperFrames $0, Motion Canvas $0, FFmpeg $0; Remotion $0 for a <=3-person shop, otherwise $25/seat/mo (Creators) or $0.01/render with $100/mo minimum (Automators); After Effects ~$23/mo single-app plus a Windows/macOS machine and Media Encoder. Cloud rendering was out of scope and none of the measurements above used it. [uncertain]

---

<a id="motion-graphics-catalog-and-transitions"></a>
## Motion Graphics Catalog and Transitions

### Basic Info

**Title**

Motion Graphics Catalog and Transitions

**Domain**

Motion graphics catalog (blocks, components, shader transitions)

**One Liner**

About 450 editable HTML blocks and components cover transitions, overlays, titles, kinetic type, captions, charts, maps, 3D and camera moves, installed by npx hyperframes add.

**Official Sources**

- https://github.com/heygen-com/hyperframes
- https://hyperframes.heygen.com/catalog
- https://hyperframes.heygen.com/developers/catalog-search
- https://hyperframes.heygen.com/concepts/compositions
- https://hyperframes.heygen.com/contributing/catalog
- https://hyperframes.heygen.com/packages/cli
- https://github.com/heygen-com/hyperframes/tree/main/registry
- https://github.com/heygen-com/hyperframes/blob/main/packages/shader-transitions/README.md
- https://github.com/heygen-com/hyperframes/blob/main/CREDITS.md
- https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes-registry/SKILL.md
- https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes-registry/references/component-quality-bar.md

**Maturity**

stable

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

You can start any named motion-graphics beat from an existing, hand-authored, seekable HTML composition instead of writing it, then edit that source freely: transitions between scenes, lower thirds, social/chat/UI cards, kinetic typography, word-level caption styles, animated charts and maps, 3D/WebGL/WebGPU backdrops and camera moves. Every item is plain HTML + CSS + JS that installs into the project tree, so it composes with the same data attributes, variables and GSAP timeline contract as hand-written compositions.

**Key Features**

- 449 published catalog entries: 210 blocks (standalone compositions with their own timeline, dimensions and duration) and 239 components (snippets you paste into your own composition), plus 9 project examples installed by `init --example`, not by `add`.
- Documented taxonomy (catalog navigation): 3D (3D motion, 3D objects), Carousels, Text & captions (Captions, Typography & Text, Lower Thirds), Code (Code Animations, Code Snippets), Transitions (Shader Transitions, CSS Transitions), Data & charts (Data), Cursors, Motion & effects (Motion Primitives, Effects, Camera), Surfaces (Texture, HTML-in-Canvas), Blocks (backdrops/VFX), and a separate Scenes & demos group (Showcases, Product Demo, Social Overlays, Motion Scenes, 93 pages).
- Transitions specifically: 14 shader transitions (flash-through-white, glitch, whip-pan, sdf-iris, light-leak, domain-warp, ridged-burn, ripple-waves, gravitational-lens, cinematic-zoom, chromatic-radial-split, swirl-vortex, thermal-distortion, cross-warp-morph) plus 17 CSS/overlay transition items (3D, blur, cover, destruction, dissolve, distortion, grid, light, mechanical, push, radial, scale, other, beat-freeze-cut, scribble, text-clone-wall, organic light leak), and 52 items carry the `transition` tag overall.
- 17 dedicated caption styles (pill karaoke, TikTok highlight, kinetic slam, glitch RGB, matrix decode, emoji pop, gradient fill, neon glow, parallax layers, particle burst, camera follow, clip wipe, editorial emphasis, weight shift, texture, blend difference) and 12 lower thirds.
- Block durations in the registry range from 2 s to 25.77 s; 219 items declare explicit dimensions, of which 203 are 1920x1080 landscape, 13 are 1080x1920 portrait, 1 is 1440x2560, 1 is 1080x1350 and 1 is 1080x1080.
- 231 items declare a typed `variables` array (2 to 26 variables each: title, color, image slots, numbers) so one file can be mounted several times with different content; 16 items declare Studio `params` controls (color, text, number, select).
- Registry metadata per item: name, type, title, description, tags (619 distinct tags), author, license (SPDX), minCliVersion, deprecated, dimensions, duration, files[], preview video/poster, relatedSkill, registryDependencies. 19 items are explicitly flagged `stability: experimental`.
- Catalog search is local: a word-match tier by default, plus an optional on-device meaning tier (bge-small-en-v1.5, 384-dim vectors, ~33 MB one-time download) reachable with `catalog --query --on-device`, filterable by `--type block|component` and `--tag`.
- `add <tag>` bulk-installs every item carrying a tag (e.g. `add captions`, `add html-in-canvas`), resolving dependencies first and installing all-or-nothing.
- 40 WebGPU shader items ship a vendored `lib/shaders.iife.js` runtime (from shaders.com, MIT) and a `data-requires-webgpu` attribute; 20 items are WebGL/canvas effects; 21 blocks create their own WebGL contexts with inline fragment shaders.
- Shader-transition blocks are self-contained: `flash-through-white.html` inlines its own WebGL program plus from/to scene texture uniforms, so it does not require the `@hyperframes/shader-transitions` package at runtime.
- Every installed item is editable source - no compiled package, no binary, no build step. Blocks land in `compositions/`, components in `compositions/components/`, assets in `assets/` (or `compositions/lib/`).

**Technical Mechanism**

The registry is a directory tree in the GitHub repo (`registry/blocks/*`, `registry/components/*`), each item holding a `registry-item.json` manifest plus its composition HTML, snippet and assets. `npx hyperframes add <name>` reads the manifest from the registry base URL (default `https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry`), walks `registryDependencies` depth-first (topologically sorted, cycles and missing deps thrown), compatibility-gates every item against `minCliVersion`, remaps each file's `target` through the project's `hyperframes.json#paths`, then fetches and validates every file before writing any of them. It writes plain HTML/CSS/JS, optionally bakes `--vars` defaults into the installed file, prints a mount snippet (and copies it to the clipboard unless `--no-clipboard`), and records what came from the registry in `hyperframes.json` so a later render can distinguish a catalog block from hand-written code. At render time a block is mounted as a nested composition (`data-composition-src`), its `<template>` content is mounted, its scripts run, and its paused GSAP timeline is registered in `window.__timelines[<composition-id>]`; the engine seeks that timeline per frame. Components have no host of their own: their markup, `<style>` and script are pasted into the host composition and their timeline is appended to the host's.

**Text And Motion Capabilities**

Text is the strongest part of the catalog: caption styles are authored as deterministic per-word GSAP timelines built from a `WORDS = [{text, start, end}]` array plus `RAW_GROUPS` index pairs, so word-level karaoke, background sweeps and per-word brightness are frame-seekable rather than CSS-timed. Typography components cover per-glyph reveals (bottom-up letters, top-down letters, char slam explode, wordmark tiles), variable-font axis animation (`weight-wave` drives wght/slnt rather than transforms), mask/clip reveals, scramble/matrix decode, split-flap and slot-machine rolls, marquees, kinetic type swap and text-along-SVG-path. Emphasis tools include hand-drawn marker strokes, strikethrough-replace, Vox-style annotate callouts and inline highlights. Motion primitives supply the easing/behaviour layer: motion blur integrated over a shutter window, camera shake with nine measured profiles, focus racks, whip-pan and match cuts, kinetic camera punch-ins, spring sheet reveals, and 'cut the curve' velocity-matched hard cuts. Limits: no GSAP SplitText anywhere in the registry (0 hits), so splitting is done with plain JS/DOM; captions are uppercase-by-style in most components; sizes and safe-area offsets are authored against the item's own dimensions (1920x1080 in 203 of 219 dimensioned items).

**Output Formats**

- Installed items are HTML compositions/snippets, not media; they produce pixels only through the renderer (MP4 H.264, WebM VP9, MOV ProRes 4444, GIF, PNG sequence, HLS).
- Each item ships a preview MP4 + poster on static.heygen.ai used by the catalog page; these are marketing renders, not the installed artifact.
- Components ship a `hyperframes:snippet` file (238 of them) that must be pasted; blocks ship a `hyperframes:composition` file mounted by reference.

### Usage & API

**Setup And Install**

Prerequisites: Node.js 22+ and FFmpeg (plus a Chrome/Chromium the CLI can drive). Then:

```bash
npx hyperframes init my-video      # creates index.html, hyperframes.json, assets/
cd my-video
npx hyperframes catalog --query "fast whip pan transition" --json
npx hyperframes add flash-through-white
npx hyperframes add instagram-follow
npx hyperframes add data-chart
```

`init` writes `hyperframes.json` with `registry` and `paths` (`blocks: compositions`, `components: compositions/components`, `assets: assets`); `add` may be pointed at another project directory with `--dir`.

**Minimal Code Example**

```bash
# install a block, then mount the snippet `add` prints
npx hyperframes add flash-through-white
```

```html
<!-- index.html: the snippet add builds for a block -->
<div
  data-composition-id="flash-through-white"
  data-composition-src="compositions/flash-through-white.html"
  data-duration="4"
  data-width="1920"
  data-height="1080"
  data-start="0"
  data-track-index="3"
></div>
```

```bash
# components install as snippets you paste into your own composition
npx hyperframes add caption-pill-karaoke --vars '{"accent":"#22c55e"}'
```

**Cli Or Api Surface**

- `npx hyperframes catalog [query]` - browse/search; flags `--query`, `--type block|component`, `--tag <tag>`, `--json`, `--pick`, `--on-device`, `--yes`.
- `npx hyperframes add <name|tag>` - install one item or every item carrying a tag; flags `--dir`, `--force`, `--vars '<json>'`, `--no-clipboard`, `--json`.
- `npx hyperframes add <name> --json` - envelope `{ok, installed[], written[], preserved[], snippet, warnings[], errors[]}`.
- `npx hyperframes init <dir> --example <name>` - the only way to install the 9 example projects (add refuses `hyperframes:example`).
- Registry item manifest (`registry-item.json`): `$schema, name, type, title, description, tags[], author, authorUrl, license, minCliVersion, deprecated, dimensions{width,height}, duration, registryDependencies[], files[{path,target,type,url?}], variables[], params[], preview{video,poster}, relatedSkill, stability, jobs[]`.
- Project config (`hyperframes.json`): `registry` (base URL), `paths.blocks`, `paths.components`, `paths.assets`, `media.autoProxy`.
- `@hyperframes/shader-transitions`: `init({bgColor, accentColor, scenes, transitions, timeline?, compositionId?, previewCaptureFps?})` returns a paused GSAP timeline; also `SHADER_NAMES` and `isHtmlInCanvasCaptureSupported()`.
- `@hyperframes/shader-transitions` `TransitionConfig`: `{time, shader, duration = 0.7, ease = "power2.inOut"}`.

**Key Config Options**

- `hyperframes.json#paths.blocks` (default `compositions`) - where `hyperframes:block` files land; the prefix `compositions/` in each manifest target is rewritten to it.
- `hyperframes.json#paths.components` (default `compositions/components`) - where component snippets land.
- `hyperframes.json#registry` (default `https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry`) - any HTTPS registry (private fork or mirror) can serve items; a project that points elsewhere gets its own registry's manifests and files.
- `data-variable-values='{"title":"..."}'` on the host element - per-mount values, layered over the composition's `data-composition-variables` defaults and read with `window.__hyperframes.getVariables()`.
- `add --vars '<json>'` - bakes the same values into the printed mount snippet (and into the installed component); unknown keys are warned and ignored.
- `--force` - overwrite files the project has changed since install; without it those files are preserved and reported in `preserved[]`.
- `minCliVersion` per item - an item installed by an older CLI is refused up front with an `incompatible-cli` error rather than half-written.
- `data-duration` on the mount - the host decides how long the block plays; if the host duration exceeds the block's own timeline the tail is blank (arithmetic, not a defect).
- `data-start="<previous-composition-id>"` - chain blocks sequentially without doing time arithmetic.
- `previewCaptureFps` (default 30) in `@hyperframes/shader-transitions` - browser-preview pre-capture rate; render uses deterministic per-frame compositing instead.

### Integration

**Pipeline Fit**

Augments, and only partially. The catalog is a library of finished, beat-agnostic HTML scenes authored mostly at 1920x1080 with their own internal timing (2-25 s each). That does not replace a 28 s, 1080x1920@60, beat-snapped FFmpeg cut list: the beat-synced shot layout, the punch-in and the audio bed should stay where they are. What it does replace is hand-authoring of named motion beats - shader transitions between shots, lower thirds, caption styling, number counters, chart/map cards - and it gives you a reference implementation you can re-frame to 9:16 instead of inventing the animation. Practically: use blocks as overlay/full-frame inserts (rendered as transparent WebM/PNG sequences and composited in FFmpeg), or lift components (motion primitives, caption behaviours) into your own 1080x1920 composition that the engine renders in one pass.

**Python Integration**

Drive it with `subprocess`: `npx --yes hyperframes add <name> --json --no-clipboard` and parse the JSON envelope (`installed`, `written`, `preserved`, `snippet`), then `npx hyperframes render -c compositions/<name>.html --format webm --output overlay.webm --quality looks --fps 60` for an alpha overlay, and composite with FFmpeg. Caveats: (1) `add` always needs the network - item files are not cached, only manifests are, so a CI job behind a proxy needs `HTTPS_PROXY`; (2) first `npx` run downloads the CLI and the Chrome build; (3) exit codes are meaningful (2 = usage/mistyped flag, non-zero `incompatible-cli` / `unknown-item` / `install-failed`), so check `ok` in the JSON rather than only the exit code; (4) rendering a block standalone requires its own `data-composition-id` root and any sibling assets, which `add` places for you; (5) for components there is no CLI-side 'apply' step - your generator must paste the snippet into the host HTML, so keep the paste as a Jinja/string include with the item's markup unchanged.

**Data Injection**

Two mechanisms. (a) Declared variables: an item declares `data-composition-variables` (id, type, label, default) on its root; the host passes `data-variable-values` JSON per mount; inside, `window.__hyperframes.getVariables()` layers host values over defaults, and CSS bindings can consume them without scripting. `add --vars` writes the values into the snippet for you. (b) Literal replacement: caption components carry a `WORDS = [{text, start, end}]` array plus `RAW_GROUPS` index pairs, so replacing those two literals with Whisper word timings is the intended data path - the timing logic, grouping and per-word animation stay intact. Text-only items (titles, lower thirds) expose their copy as markup or variables and are edited by string substitution.

**Footage And Asset Handling**

Blocks accept your footage as ordinary `<video class="clip" data-start data-duration data-track-index src>` elements; caption/demo components ship a full-frame `<video id="...-video">` behind the text that you point at your own file. Image slots in carousels and grids are `<img>` elements (12-24 slots per carousel). Assets install beside the composition (`assets/...`, `compositions/lib/...`, or `models/` for GLB/glTF on the turntable block) and are fetched from the registry's CDN `url` when the source bytes are hosted. Fonts are pulled from Google Fonts by `@import`/`<link>` in the item (no local font files in most items), and GSAP comes from `cdn.jsdelivr.net` (gsap@3.14.2 in 597 references), so a fully offline render needs those dependencies vendored locally.

**Migration Effort**

medium

**Integration Steps**

- Create the HyperFrames project beside the Python pipeline (`npx hyperframes init reel-overlays`) and keep it out of the video build path until step 6.
- Run `npx hyperframes catalog --query "<the beat you need>" --json --type block` for each visual you would otherwise hand-build; record the item names in the SKU plan.
- Install the shortlist (`npx hyperframes add <name>`) and read each installed file before using it - confirm it registers `window.__timelines[...]`, has markup of its own, and matches its description.
- Re-frame the keepers to 1080x1920: change `data-width`/`data-height`, the `width/height` in `html, body` and the item's own CSS, and move the safe-area offsets from 140 px bottom (16:9 default) into the 9:16 band.
- Replace copy, images and caption arrays with generated data (variables or the `WORDS`/`RAW_GROUPS` literals), then render a still (`npx hyperframes snapshot . --at <t>`) to check legibility at 1080 px width.
- Render overlays as `--format webm` (VP9 + alpha) or `--format png-sequence` and composite over the beat-cut footage in FFmpeg; keep the 28 s cut list and audio bed in the existing pipeline.
- Gate the result: assert the overlay duration equals its slot, the alpha survives (ffprobe `alpha_mode=1`), and the frame at each cut has no clipped text; fail the SKU rather than shipping a blank overlay.
- Pin the registry commit or mirror the used items into the repo, so a registry change cannot silently alter a delivered reel.

### Constraints

**Requirements**

- Node.js 22+ and FFmpeg for the CLI (the catalog itself is static HTML, but `add` and `render` are CLI operations).
- Network access for every `add`: item files are fetched from the registry each time (only manifests are cached), default base `https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry`.
- A Chrome/Chromium build the CLI can launch for preview and render; WebGPU items (`data-requires-webgpu`, present in 37 item files) need a GPU-backed browser or the opt-in software-WebGPU escape hatch (`allowSoftwareWebGpu`, off by default because it is slow).
- GSAP 3.14.2 from jsDelivr for 597 references in item HTML; Google Fonts reachable for most items; three.js (41 references) and d3/topojson/lottie/clipper for the map, chart and 3D items.
- Items that vendor assets need their sibling files: 759 `hyperframes:asset` entries across the registry, some served from static.heygen.ai rather than the repo.

**Limitations**

- Landscape-first: 203 of the 219 dimensioned items are authored at 1920x1080 and only 13 at 1080x1920, so a 9:16 pipeline must re-frame most picks by hand (dimensions, CSS sizes and safe-area offsets all assume the item's own aspect ratio).
- An installed item is a finished scene, not a parameterised template: its internal timing is fixed by its own timeline (2-25.77 s), so aligning it to a beat grid means retiming its GSAP calls or trimming the mount, not passing a duration.
- The repo's own component quality bar records that 97 of 213 newly added components had no `__timelines` registration at all - they render a still frame when installed even though the catalog preview looks animated, because the preview generator transplants the demo's timeline.
- Duplication is real: the same audit states one wipe shipped eight times and one word-stagger seven times, and that some names claim a technique the code lacks (no spring, no mask, no backdrop-filter). Picking by name is not reliable; read the installed file.
- Some items are illegible or theme-fragile by the project's own bar: text under 4.5:1 contrast, smallest meaningful feature under ~24 px at 1920x1080, hardcoded ink colours with no token fallback, unseeded `Math.random()` scatter, and number variables declared without bounds.
- Runtime dependencies are external: GSAP is loaded from jsDelivr and fonts from Google Fonts inside the item HTML, so offline or air-gapped rendering needs those files vendored and the `<script src>`/`@import` rewritten. Only the WebGPU shader runtime (`lib/shaders.iife.js`) and the 3D blocks' three.js copies are vendored in-tree.
- No `registryDependencies` are currently declared by any item, so `add` never installs shared helpers for you; a block that expects a sibling utility expects it to be pasted by hand.
- Components are snippets, not mountable units: `add` prints a paste comment instead of a snippet, and nothing marks where in your composition the markup, `<style>` and script must go beyond the item's own comment block and `demo.html`.
- Item ids are only prefixed by convention; two items pasted into one scene can still collide if you rename them, and the quality bar explicitly requires keeping installed ids as shipped.
- The registry is read from `main` by default, so `add` is not reproducible across time unless you pin a commit, mirror the registry, or commit the installed files.

**Licensing**

The repository and therefore the catalog source are Apache-2.0, with no per-render fees and no commercial-use threshold. Per-item licences are declared in `registry-item.json#license` where third-party code is involved: 36 items 'Apache-2.0 AND MIT' (the WebGPU shaders are MIT, from shaders.com), one OFL-1.1 (fonts), one MIT, one Apache-2.0, one 'MIT AND OFL-1.1'. Known third-party obligations carried by specific blocks: GSAP 3.14.2 under the GSAP Standard License (not OSI) in the seven 3D-motion blocks, three.js MIT, three-mesh-bvh and opentype.js MIT, Clipper Boost-1.0, d3-delaunay ISC, Geist/Archivo/Cormorant Garamond under OFL-1.1, Poly Haven HDR CC0, ambientCG textures CC0. Using the catalog is free; the paid boundary is elsewhere - HyperFrames Pro covers the `/reverse-any-video` and `/style-sampling` skills, and the Studio Pro plan bundles HeyGen's video/image/voice/music models. Blocks are source you may modify and ship inside your own video; the Apache-2.0 notice and the per-item third-party notices must travel with redistributed source.

### Evidence

**Confidence**

0.86

**Sources**

- https://github.com/heygen-com/hyperframes - repo front page: 84.7k stars, Apache-2.0, Node 22+/FFmpeg requirement, package split including @hyperframes/shader-transitions, catalog examples (`add flash-through-white`, `add instagram-follow`, `add data-chart`), 9 published skills, Pro boundary.
- https://hyperframes.heygen.com/llms.txt and /_llms/catalog/catalog.txt - authoritative catalog taxonomy (3D, Carousels, Text & captions, Code, Transitions, Data & charts, Cursors, Motion & effects, Surfaces, Blocks, Scenes & demos) and the per-item descriptions quoted for transitions, captions, lower thirds and typography; also states 449 catalog pages.
- Local clone of heygen-com/hyperframes @ main (2026-11-15) - counted 210 block dirs, 239 component dirs, 9 example dirs; 456 registry.json items; parsed all 457 registry-item.json manifests for dimensions (203 x 1920x1080, 13 x 1080x1920), durations (2-25.7667 s), variables (231 items), params (16), stability (19 experimental), licence (40), registryDependencies (0).
- Local clone: registry/blocks/flash-through-white/flash-through-white.html - inline WebGL context plus fragment shaders with u_from/u_to textures, proving shader blocks are self-contained rather than importing the npm package.
- Local clone: registry/components/caption-highlight/caption-highlight.html - the `WORDS = [{text,start,end}]` + `RAW_GROUPS` data contract and per-word GSAP timeline, the concrete data-injection path for captions.
- Local clone: packages/cli/src/commands/add.ts and registry/*.ts - the whole `add` flow: dependency resolution order, compatibility gate, target remapping, all-or-nothing write, `--vars` baking, clipboard snippet, `recordProjectRegistryItems`, and DEFAULT_REGISTRY_URL = raw.githubusercontent.com/.../registry.
- Local clone: packages/cli/src/utils/projectConfig.ts and docs/schema/hyperframes.json - default `paths` (`compositions`, `compositions/components`, `assets`) and the `registry` override.
- Local clone: packages/shader-transitions/README.md - the 14 shader names, `init()` signature, TransitionConfig defaults (0.7 s, power2.inOut), html2canvas fallback, Chrome CanvasDrawElement path, IndexedDB preview cache, ESM/CJS/IIFE distribution.
- Local clone: skills/hyperframes-registry/SKILL.md - documented wiring rules (block = mount with data-composition-src + matching id + data-duration; component = paste markup/style/script), `add <tag>`, `add` needs the network, install paths from hyperframes.json.
- Local clone: skills/hyperframes-registry/references/component-quality-bar.md - the project's own audit criteria and findings: 97 of 213 new components without a timeline, one wipe shipped 8x, one word-stagger 7x, name/technique mismatches, illegibility and theming defects.
- Local clone: CREDITS.md - third-party licences per block (GSAP Standard License, three.js MIT, Clipper Boost-1.0, d3-delaunay ISC, OFL fonts, CC0 HDR/textures) and the list of texture origins still to be confirmed.
- https://hyperframes.heygen.com/developers/catalog-search - search tiers, bge-small-en-v1.5 (384-dim, 33 MB download, pinned revision), local-only privacy, vector revision hashing.
- https://hyperframes.heygen.com/concepts/compositions - nesting rules (`data-composition-src` resolved from project root, `<template>` mounting, `data-playback-start`, `data-track-index`), variables and `getVariables()`, and the warning not to script media play/pause.
- https://hyperframes.heygen.com/contributing/catalog - block vs component required files, `params` controls, optional manifest fields, and the CDN hosting of binary assets via `files[].url`.

**Open Questions**

- Which of the 13 portrait blocks actually survive re-framing at 1080x1920@60 without retiming, and how much of the 203 landscape items can be re-flowed rather than rebuilt? Needs a hands-on spike on 5-10 candidate items.
- How expensive is a typical shader-transition overlay render on this WSL2 box (software WebGL), measured end to end at 1080x1920@60 for a 1 s transition?
- Do the caption components' Google Fonts + CDN GSAP dependencies resolve reliably inside the offline/CI render, or must every used item be vendored first?
- Whether installing items is compatible with the project's existing FFmpeg-first pipeline at all, versus lifting only the motion primitives into a single HyperFrames overlay composition - a decision that needs a prototype of both.
- Current state of the quality-bar audit numbers (97 of 213, duplicate counts) on today's `main`, since the doc is branch-scoped.

### Other Info

**Known Issues**

- `hyperframes-registry` skill and the component-quality-bar doc enumerate concrete failure classes (F1-F5 fatal, X1-X8 fixable) verified on real items: does not implement its description, redundant duplicate, renders nothing, description is a different item, cannot be made seekable, no timeline, name claims a missing technique, illegible, placeholder gradients, hardcoded ink, no markup, unseeded randomness, declared bounds it cannot honour. [uncertain] The counts (97 of 213) come from that audit branch and may have moved since.
- Catalog items are redistributed code from many contributors, so licence and provenance vary per item: 36 items are 'Apache-2.0 AND MIT' (WebGPU shaders from shaders.com), one is OFL-1.1, one MIT, one Apache-2.0, one 'MIT AND OFL-1.1'; the remaining items carry no per-item licence field and fall back to the repo's Apache-2.0.
- The seven 3D-motion blocks vendor or CDN-load GSAP 3.14.2 under the GSAP Standard License (not an OSI licence) and three.js (MIT, r185 vendored / r181 bundled / 0.170.0-0.181.2 from CDN), with `GSAP-NOTICE.txt` / `Three-LICENSE.txt` beside the copies; CREDITS.md still lists some texture origins as 'to be confirmed with the author'.
- `add` needs the network even for an item installed before, and a transport failure surfaces as a bare `fetch failed` unless the CLI can name the registry host - a common failure mode when copying a command off the catalog page behind a proxy.
- The catalog search index is a published artifact with a `revision` hash; a CLI whose local vectors are stale silently returns older results until it refetches, and the meaning tier is unavailable until the 33 MB model is downloaded.

**Performance And Cost**

Zero runtime cost: items are static HTML/CSS/JS with no package to install, no build step and no per-render fee. Disk cost is small (single HTML files, plus 759 asset files for image/mesh/texture-bearing items). Render cost varies by item class and is not published per block: 40 WebGPU items need a GPU or software WebGPU (slow), 21 blocks create WebGL contexts, and shader-transition renders are the expensive path - the CLI's page-side WebGL compositing exists specifically because it is '~6x faster for SDR shader-transition renders', and it is unavailable for webm/mov output, which take the layered Node-side blend instead. [uncertain] No measured per-block render times exist in the docs; only engine-level numbers (workers, 4K ~4x) apply.

---

<a id="overview-and-architecture"></a>
## Overview and Architecture

### Basic Info

**Title**

Overview and Architecture

**Domain**

Platform / HTML-to-video rendering engine

**One Liner**

HeyGen's Apache-2.0 framework renders HTML compositions to deterministic MP4 by seeking paused timelines frame by frame in headless Chrome and encoding with FFmpeg.

**Official Sources**

- https://github.com/heygen-com/hyperframes
- https://hyperframes.heygen.com/introduction
- https://hyperframes.heygen.com/concepts/determinism
- https://hyperframes.heygen.com/concepts/compositions
- https://hyperframes.heygen.com/packages/producer
- https://hyperframes.heygen.com/packages/engine
- https://hyperframes.heygen.com/packages/core
- https://www.npmjs.com/package/hyperframes
- https://hyperframes.heygen.com/quickstart

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

HyperFrames turns a folder of ordinary HTML, CSS, media and seekable JavaScript animation into a frame-exact video file, without a bundler, without a React-style component model and without a proprietary timeline format. For a product-reel pipeline the practical meaning is: any layout you can build in a browser (kinetic type, gradients, masks, WebGL, Three.js, shaders, Lottie) can be rendered as a deterministic 1080x1920 asset that is reproducible in CI, and the same project folder previews in a browser, renders locally, renders in Docker, or fans out to AWS Lambda / Cloud Run.

**Key Features**

- Attribute-driven timeline: any element carrying data-start / data-duration / data-track-index becomes a clip, so timing lives in HTML rather than in JavaScript.
- Paused, seekable animation: GSAP timelines are created with { paused: true }, registered on window.__timelines[compositionId], and only ever seeked - never played - by the renderer.
- Adapter-based motion: CSS animations, WAAPI, Lottie, Three.js, Anime.js and custom frame adapters all plug into the same seek contract (init -> seekFrame(frame) repeated -> destroy).
- Sub-compositions: a composition can mount another inline or from a <template> file via data-composition-src, with a host time window, its own local timeline and per-instance variables.
- Frame-exact capture: Chrome's HeadlessExperimental.beginFrame grabs each frame atomically, with Page.captureScreenshot as a supported fallback mode.
- Not a screenshot pipeline: every frame is produced by seeking to an exact t and capturing once, with video frames pre-extracted and audio mixed separately, so output never depends on page load time, playback speed or machine speed.
- Video is taken off the browser clock: every <video> source is pre-extracted by FFmpeg into per-frame images that replace the element during capture, so decode latency cannot desync or drop a frame.
- Audio is mixed rather than recorded: all <audio>/<video> clips are gain-, fade- and automation-mixed into one timeline WAV by FFmpeg, then encoded to AAC (MP4/MOV) or Opus (WebM) and muxed.
- Parallel capture: the default worker count is 'auto' (CPU/memory/heap/frame-count heuristic); each worker is a separate Chrome process of roughly 256 MB and takes a contiguous frame range.
- Determinism contract: t = floor(frame) / fps, no wall clock, no unseeded randomness, no mid-render network fetch, fps/width/height/variables locked before frame 0, and __playerReady/__renderReady gates holding capture until the project is loaded.
- Docker render path (--docker) pins Chromium, the font set and the FFmpeg encoder so the host stops being an input; that is the documented route to cross-machine reproducibility.
- Outputs: MP4 (H.264/AAC, optional HDR), WebM (VP9/Opus, alpha), MOV (ProRes 4444 alpha or 422 profiles), GIF, PNG sequence (RGBA, optional AAC sidecar) and HLS VOD.
- Operational tooling: lint (static contract), check (browser runtime, layout, motion, contrast), snapshot, compare, keyframes, timeline, doctor and clean; every command is non-interactive and most accept --json.
- Distributed rendering is first-class: the producer exposes plan / renderChunk / assemble activities, shipped as adapters for AWS Lambda + Step Functions + S3 and for Google Cloud Run + Workflows.
- Agent-first packaging: init scaffolds AGENTS.md and CLAUDE.md, installs the core skill set, and the CLI defaults to non-interactive so agents and CI can drive it.

**Technical Mechanism**

Definitions used throughout this research.
Composition: one HTML document that is a finite, seekable piece of video. Its root carries data-composition-id, data-width/data-height and usually data-duration; index.html is the top-level composition and any composition can be mounted inside any other. The length is fixed before frame 0, and everything visible inside it is either a clip or plain untimed decoration.
Clip: any element carrying timing attributes (data-start, data-duration, data-track-index) - a <video>, an <img>, an <audio>, a timed DOM element, or a host <div data-composition-id> that mounts a sub-composition. The runtime shows a clip only inside [start, start+duration) and the renderer composites whatever is visible at t.
Frame: one still image at index i, captured at t = floor(i)/fps, for a composition whose fps, width and height were locked before frame 0. A render of D seconds at F fps is ceil(D x F) independently requested frames (0..N-1); nothing is ever played, so a slow machine cannot drop one. 28 s at 60 fps = 1,680 frames.

Package split (one monorepo, 15 published packages plus a private SDK playground, all published ones versioned together at 0.9.54). `hyperframes` (packages/cli) is the published CLI and bin: init, preview, lint, check, snapshot, timeline, keyframes, render, add/catalog, audio/transcribe/tts/capture, publish/share, cloud/lambda/cloudrun, skills, doctor, clean. @hyperframes/core is the shared model - types, parser re-exports, the compiler (compileHtml, bundleToSingleHtml), variables, generators and the browser runtime. @hyperframes/parsers is the standalone HTML + GSAP AST parser/writer with no @hyperframes dependencies, and @hyperframes/lint is the rule engine. @hyperframes/engine is the low-level seekable capture layer: browser manager and lease pool, frameCapture, parallelCoordinator, videoFrameExtractor/Injector, audioMixer, chunkEncoder/streamingEncoder, encoders, muxers, motion blur and HDR. @hyperframes/producer is the six-stage pipeline plus the render-job API, the Hono render server and the distributed plan/renderChunk/assemble entry. @hyperframes/studio and @hyperframes/studio-server are the browser editor UI and its server; @hyperframes/sdk is a headless, framework-neutral editing engine; @hyperframes/player is the <hyperframes-player> web component; @hyperframes/shader-transitions is the WebGL transition library; @hyperframes/aws-lambda is the Lambda handler + client SDK + CDK construct (Step Functions fan-out, S3 transport); @hyperframes/gcp-cloud-run is the Cloud Run/Workflows/Terraform equivalent; @hyperframes/inspector and @hyperframes/share cover inspection and review-link identity.

Why it is not a web-page screenshot tool: a screenshot tool answers "what does this page look like now?" - it depends on load timing, viewport, animations in progress and the wall clock, so two runs differ. HyperFrames answers "what does this project look like at frame 903?" Time is an input rather than an emergent property: fps, size and variables are locked before frame 0, every animation is a paused timeline that is seeked to t, native video is replaced by FFmpeg-extracted stills so decode timing cannot drift, audio is mixed from the timeline instead of being recorded, and capture is an atomic BeginFrame rather than a best-effort screen grab. The output is an encoded frame sequence at an exact fps with muxed audio, not a recording of a browser session.

Render pipeline, six stages (packages/producer/src/services/renderOrchestrator.ts):
1. compile - the HTML compiler parses the project, inlines data-composition-src templates, resolves relative start expressions and media durations, rewrites asset paths and bundles everything plus the hyperframe runtime into one servable document.
1b. probe - a browser pass discovers the true duration (when the root has no data-duration) and reconciles media metadata against the authored clips.
2. extract videos - FFmpeg pre-extracts each <video> source into per-frame stills (JPEG by default, PNG for alpha or colour-sensitive sources), honouring data-media-start and data-playback-rate; during capture those stills are injected as <img> elements in place of the native video.
3. audio - every <audio>/<video> track is mixed into a single timeline WAV with gain, edge fades, volume/rate automation lanes and loops.
4. capture - a local file server serves the bundle; Puppeteer (puppeteer-core) drives headless Chrome. For frame i the engine computes t = floor(i)/fps (quantised to the frame grid) and calls the page's seek protocol, which seeks every registered timeline and frame adapter, then captures via HeadlessExperimental.beginFrame or the screenshot fallback. Frame ranges are spread across worker processes.
5. encode - FFmpeg encodes chunks with libx264 / libvpx-vp9 / ProRes (CRF or bitrate), concatenates them and writes faststart output; HDR uses a separate frame pipeline.
6. assemble - the mixed audio is muxed, and output validation checks duration, dimensions and (when the container reports it) expected frame count.
Everything below the producer is @hyperframes/engine: browser manager and lease pool, frameCapture, parallelCoordinator, videoFrameExtractor/Injector, audioMixer, chunkEncoder/streamingEncoder, encoders and muxers.

**Text And Motion Capabilities**

Text is real DOM text in real fonts, laid out by Chrome at the authored pixel/container-query sizes, so every CSS typographic device is available: variable fonts, gradients and background-clip text, -webkit-text-stroke, text-shadow, masks, filters, 3D transforms, SVG text and per-glyph spans. Because the renderer seeks instead of playing, each frame is exact to t = frame/fps, so a 60 fps render gives 16.67 ms motion granularity - enough for per-word caption pops that land on measured audio timings, but you must not rely on wall-clock sequencing. Precision limits are the practical kind: fonts must be loaded before any measuring (render waits on __playerReady), split text is a DOM operation you (or GSAP SplitText, or the caption blocks/skills) perform, and the core renderer ships no caption engine of its own - captions arrive as compositions, catalog blocks or skills. Motion primitives come from the adapter you choose: GSAP eases, CustomEase, spring eases generated by @hyperframes/parsers/spring-ease, CSS keyframes, WAAPI, Lottie, Three.js/WebGL, Anime.js; shader transitions are provided by @hyperframes/shader-transitions. Motion blur is an opt-in extra with its own cost and limits.

**Output Formats**

- MP4 - H.264 + AAC, the default delivery format; quality presets draft / looks (CRF 16, default) / delivery / high, optional HDR (hdrMode), optional 4:4:4 master (chromaSubsampling 444, software encoder only).
- WebM - VP9 + Opus, supports alpha for web overlays; much slower than MP4 because VP9 is CPU-heavy.
- MOV - ProRes 4444 with alpha by default, or 4:2:2 profiles hq/422/lt/proxy (no alpha); AAC audio.
- GIF - silent, 30 fps ceiling, loop count 0-65535; intended for previews and PRs.
- PNG sequence - RGBA frames written to a directory plus an optional AAC sidecar, for AE/Nuke/Fusion handoff.
- HLS - H.264/AAC VOD packaged into master.m3u8 + segments; SDR only, GPU encoding refused, segments locked to keyframes.
- Side products: still frames via `hyperframes snapshot`, contact sheets/compare reports, and a reference_background metadata field describing the composition's computed background.

### Usage & API

**Setup And Install**

Prerequisites: Node.js 22 or newer (the npm package declares engines.node >= 22) and FFmpeg/ffprobe on PATH.

  npm install -g hyperframes            # or use npx, no global install
  npx hyperframes doctor                # verifies node, Chrome, FFmpeg, disk
  npx hyperframes init my-video         # scaffold (use --resolution portrait for 1080x1920)
  cd my-video
  npx hyperframes preview               # browser preview with live reload
  npx hyperframes render --output out.mp4

Chrome is not bundled: puppeteer-core plus @puppeteer/browsers downloads a pinned build on first use. Docker is optional and only needed for pinned, cross-machine reproducible renders (--docker).

**Minimal Code Example**

A complete 8-second 1080x1920 composition. Save as index.html, put product.mp4 and music.mp3 in assets/, then run `npx hyperframes render --output out.mp4`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
    <style>
      html, body { margin: 0; width: 1080px; height: 1920px; overflow: hidden; background: #05070a; }
      #root { position: relative; width: 1080px; height: 1920px; overflow: hidden; }
      .clip { position: absolute; inset: 0; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="reel" data-start="0" data-duration="8"
         data-width="1080" data-height="1920" data-fps="60">
      <video id="bg" class="clip" src="assets/product.mp4" data-start="0" data-duration="8"
             data-track-index="0" data-media-start="0" muted playsinline></video>
      <h1 id="headline" class="clip" data-start="1" data-duration="5" data-track-index="1"
          style="display:grid;place-items:center;color:#fff;font:800 92px/1.05 system-ui">
        Precio real
      </h1>
      <audio id="bed" src="assets/music.mp3" data-start="0" data-duration="8"
             data-track-index="2" data-volume="0.35"></audio>
      <script>
        window.__timelines = window.__timelines || {};
        const tl = gsap.timeline({ paused: true });
        tl.fromTo("#headline", { y: 60, opacity: 0 },
                  { y: 0, opacity: 1, duration: 0.5, ease: "power3.out" }, 1);
        window.__timelines.reel = tl;
      </script>
    </div>
  </body>
</html>
```

Equivalent programmatic render from Node:

```ts
import { createRenderJob, executeRenderJob } from "@hyperframes/producer";

const job = createRenderJob({ fps: 60, quality: "high", format: "mp4" });
await executeRenderJob(job, "./my-video", "./renders/reel.mp4", (j, msg) =>
  console.log(`${Math.round(j.progress)}% ${msg}`),
);
```

**Cli Or Api Surface**

- hyperframes init <dir> - scaffold; flags --example/-e, --resolution (landscape|portrait|square + 4k/1440p presets), --video, --audio, --tailwind, --non-interactive, --skip-transcribe, --keep-original, --model, --language, --skip-skills.
- hyperframes preview - local preview + browser editor with live reload.
- hyperframes lint / check - static contract lint vs browser runtime, layout, motion and contrast checks.
- hyperframes snapshot --at 0,2,5 - PNG stills for visual verification.
- hyperframes render - the main command; see key_config_options for flags.
- hyperframes timeline / compositions / keyframes - inspect tracks and clips (--json), list compositions, probe keyframe behaviour.
- hyperframes add <block|component|tag> / catalog - install registry items into compositions/ and compositions/components/; --json, --dir, --no-clipboard.
- hyperframes audio / transcribe / tts / capture - measure audio, Whisper transcription, narration generation, website capture.
- hyperframes doctor / clean / compare / benchmark - machine diagnostics, disk cleanup, version comparison, render benchmarks.
- hyperframes publish / share / auth / cloud / lambda / cloudrun / skills - delivery, review links, identity, distributed renders, skill installation.
- @hyperframes/producer: createRenderJob(options), executeRenderJob(job, projectDir, outputPath, onProgress?, abortSignal?), createProducerApp(), startServer({ port }), and the @hyperframes/producer/distributed entry with plan / renderChunk / assemble.
- @hyperframes/engine: createCaptureSession, initializeSession, getCompositionDuration, captureFrame, captureFrameToBuffer, closeCaptureSession, encodeFramesChunkedConcat, concatVideoFiles, muxVideoWithAudio, browser pool helpers.
- @hyperframes/core: parseHtml, extractCompositionMetadata, generateHyperframesHtml, validateVariables, getVariables, and @hyperframes/core/compiler with compileHtml / bundleToSingleHtml; @hyperframes/core/runtime is the browser runtime.

**Key Config Options**

- --fps / -f - integer (24/25/30/50/60/120/240) or ffmpeg rational (30000/1001). Range 1-240. Default: the root's data-fps, else 30.
- --quality / -q - draft | looks | delivery | standard | high. Default: looks (CRF 16).
- --format - mp4 | webm | mov | gif | png-sequence | hls. Default: mp4.
- --output / -o - output path. Default: renders/<project>_<YYYY-MM-DD>_<HH-MM-SS>.<ext>.
- --resolution - landscape|portrait|square plus -4k/-1440p variants. Does not change the composition: Chrome renders at a higher deviceScaleFactor; aspect ratio must match and upscaling must be an integer multiple. Not supported with --hdr.
- --workers / -w - parallel render workers, a number or 'auto'. Default: auto (CPU/memory/heap/frame-count heuristic). Each worker is a separate Chrome process (~256 MB RAM).
- --docker - render inside the pinned container for deterministic, cross-machine output. Default: false.
- --crf / --video-bitrate - encoder quality overrides (mutually exclusive; rejected for MOV).
- --start / --end - partial-range render (MP4, SDR, whole frame rates 24/25/30/60 only).
- --variables '<json>' / --variables-file <path> / --strict-variables / --batch <rows.json> / --batch-concurrency N - per-render data injection and batch rendering; default batch concurrency is 1.
- --gpu - GPU encoding (NVENC/VideoToolbox/VAAPI). Default: false.
- --hdr / --sdr - force HDR or SDR output; HDR is MP4-only and SDR is forced for transparent formats.
- --keyframe-interval / --max-bitrate / --buffer-size - delivery constraints for MP4 (buffer defaults to twice the cap).
- --hls-segment-seconds - default 4; also locks the GOP so every segment starts on a keyframe.
- --prores-profile - hq | 422 | lt | proxy for MOV (no alpha).
- --json / --debug - machine-readable result (exactly one document with --batch) and retained render.log diagnostics.
- --page-motion-blur off - disable authored page motion blur for a render; --video-frame-format auto|jpg|png controls source extraction.

### Integration

**Pipeline Fit**

Augments, and for the overlay layer replaces, the current FFmpeg-only stack rather than the whole pipeline. The existing Python beat grid, keyframe QA, script lint and FFmpeg cut assembly stay the source of truth for footage and beat-synced cuts; HyperFrames takes over everything that is text, layout and motion-graphics: kinetic captions, price pills, badges, lower thirds, transitions and end cards, rendered as a transparent WebM/MOV overlay (or as a full-frame MP4) that FFmpeg composites over the product footage. A full replacement is possible but throws away the already-validated beat-locked cutting model; the hybrid keeps render determinism where it matters (text) and keeps the fast, cheap footage path where it already works.

**Python Integration**

Contract-first, three options in increasing coupling:
1. Subprocess (recommended): generate the project folder from Jinja/JSON, then run `npx hyperframes render <dir> --output <path> --json` with subprocess.run(timeout=...), parse the JSON result or the stderr progress lines, and treat exit code 0 as success. Exit codes are documented: 0 success, 1 ran and failed, 2 bad command line. Note that without --batch, --json only silences progress and prints a JSON error envelope on failure, so parse the produced file (ffprobe) as the real success signal.
2. HTTP: run `@hyperframes/producer`'s Hono server (startServer) once and POST render jobs; it binds 127.0.0.1 by default, requires a bearer token off-loopback, and confines projectDir/outputPath to configured roots.
3. Node bridge: a ~30-line Node script importing @hyperframes/producer and emitting newline-delimited JSON progress, invoked from Python - useful when you need per-frame progress or cancellation (AbortSignal).
Caveats: Node 22+ and FFmpeg must exist in the same environment (or use --docker), first run downloads Chrome, and long renders should stream stdout/stderr rather than buffer them.

**Data Injection**

Two layers. Composition variables: declare data-composition-variables on the root (string, number, color, boolean, enum, font, image), bind them with data-var-text / data-var-src / CSS var(--id), then override per render with --variables '{...}', --variables-file, or one row per video with --batch rows.json --output 'renders/{name}.mp4'. Nested hosts get per-instance values with data-variable-values. Structural data (how many scenes, which clips) is normally injected by generating the HTML itself, because variables explicitly cannot change viewport, root duration, fps, codec or layout structure. For this pipeline: word timings become GSAP tween positions inside the generated HTML, prices/titles/colours become variables, and the render stays a pure function of the generated folder.

**Footage And Asset Handling**

Existing MP4s are consumed directly as <video class="clip"> elements with data-start, data-duration, data-media-start (source in-point), data-playback-rate, data-volume and optional data-fade-in/out; the engine extracts their frames with FFmpeg and also takes their audio into the mix (declare data-has-audio="true" and do not mute, or use muted for silent B-roll). Audio beds are <audio> elements with the same timing attributes and are mixed to -14 LUFS-style targets by your own post chain, not automatically. Fonts live in assets/fonts and are loaded before measuring; the runtime can be told to use deterministic font handling, and Docker pins the font set. Remote https assets are downloaded into the work directory before capture (8 GiB / 30 min / 32 KiB/s / 512 MiB free-space guards, each env-overridable), which is also the reason mid-render network access is a determinism risk.

**Migration Effort**

medium

**Integration Steps**

- Pin a version (e.g. hyperframes@0.9.54) and add a bootstrap step that installs Node 22+, FFmpeg and the Chrome build in the render image; verify with `npx hyperframes doctor`.
- Build one Jinja template pair: a root index.html at 1080x1920 with data-fps="60" and data-duration set from the voiceover, plus one composition per overlay type (caption block, price pill, end card).
- Express every per-video value as a declared composition variable; keep word timings out of variables and bake them into GSAP tween positions when generating the HTML.
- Render a transparent overlay first (`--format webm` for the web, `--format mov --prores-profile 4444` for an editing master) and composite it in the existing FFmpeg graph with -c:v libx264 for the final MP4.
- Add a pre-render gate in Python: run `npx hyperframes lint` and fail the job on non-zero exit before spending a render.
- Add a post-render gate: ffprobe duration/size/pix_fmt plus a handful of extracted frames checked by the existing QA scripts, mirroring qa_check.py's role.
- Render in --docker in CI to pin Chromium/fonts/FFmpeg, and cache the Chrome download, node_modules and the extraction cache between runs.
- Measure one real 28 s 1080x1920@60 overlay render end-to-end and set per-job timeouts and worker counts from that number, not from the 1080p benchmark.

### Constraints

**Requirements**

- Node.js >= 22 (declared in package.json engines); the CLI is ESM and ships as the npm package `hyperframes`.
- FFmpeg and ffprobe on PATH for every local render (encoding, frame extraction, audio mixing, probing).
- A Chrome/Chromium build downloaded by @puppeteer/browsers via puppeteer-core; no system Chrome is required but a pinned one can be supplied.
- Linux, macOS or Windows; WSL is handled explicitly (separate env/launch paths in the browser manager), and a Docker image is provided for pinned renders.
- Disk for frame extraction plus per-render scratch: a 28 s 60 fps render is ~1,680 frames, and source video extraction can add several GB for long footage.
- Optional GPU for WebGL/WebGPU compositions; without one, SwiftShader software rendering works but is documented as 5-50x slower.
- For data-requires-webgpu compositions, a WebGPU device must be creatable within a 10 s probe or capture aborts with WebGpuUnavailableError.

**Limitations**

- Pre-1.0 and fast-moving: 0.9.x shipping several npm versions a day (125 versions in the 30 days to 2026-11-15), so the CLI/API surface can change between minor versions - pin an exact version and re-run the visual gate on upgrade.
- Determinism is per-environment, not universal: the same machine and settings reproduce frames, but Chrome version, installed fonts, GPU/driver and encoder build can shift pixels; only --docker makes the platform stop being an input.
- Motion blur is the documented exception to seek-order independence, because it accumulates samples across a shutter window.
- Real-time playback semantics are unavailable by design: requestAnimationFrame, Date.now, autoplaying media and mid-render fetches break determinism and are lint-flagged or silently wrong.
- Render speed is not real-time: capture is one frame at a time, so a heavy 60 fps 1080x1920 composition costs seconds per second of output, and every extra worker costs ~256 MB RAM.
- Transparent output is limited to WebM (VP9/Opus) and MOV (ProRes 4444); MP4 is opaque, and HDR plus alpha is not a supported combination.
- The renderer assumes it owns media playback; author code that calls play(), pause() or sets currentTime fights the runtime.
- Distributed rendering is infrastructure glue, not a managed service: you own the bucket, state machine, retries and cost, and Lambda output is SDR-only.
- The free/open surface is the renderer and CLI; the Studio app's Pro plan, some skills (/reverse-any-video, /style-sampling) and cloud/share quotas are commercial.

**Known Issues**

- Local renders can differ by a pixel or two from another machine's render of the same project (documented font/Chrome variance), so golden-image tests must run in the pinned container.
- The tree carries ~109 open issues and ~185 open pull requests at 0.9.54, i.e. a very active but not fully triaged backlog.
- Two attribute readers disagree on data-media-start vs data-playback-start; a <video> authored with only data-playback-start renders a trimmed picture over untrimmed audio (documented trap in the HTML schema).
- A composition root with data-composition-id but no registered timeline and no data-no-timeline makes the producer poll for up to 45 s on every render (lint rule missing_data_no_timeline).
- Inspection commands (check, validate, snapshot, compare) clamp to a 4096 px viewport, so a larger authored canvas is only fully verified by an actual render.
- GIF has no audio and is capped at 30 fps; WebM VP9 is CPU-heavy and much slower than MP4.
- The Docker CLI wrapper does not currently return the child process's result metadata (documented limitation for reference_background).

**Licensing**

Apache License 2.0 for the repository and all published packages, with no per-render fee, seat count or commercial-use threshold. The commercial boundary is around the hosted product, not the renderer: the HyperFrames Studio desktop app is free, its Pro plan sells HeyGen's video/image/voice/music models, two skills (/reverse-any-video, /style-sampling) are marked Pro, and share/review links have plan limits (five free shared projects per workspace). Catalog blocks installed with `hyperframes add` are editable source shipped under the same repo license.

### Evidence

**Confidence**

0.9

**Sources**

- https://github.com/heygen-com/hyperframes - README: positioning ('Write HTML. Render video. Built for agents.'), Apache-2.0, Node 22+/FFmpeg requirements, the eight documented packages, the inline data-attribute example, the HyperFrames-vs-Remotion table, the stack table and community section.
- https://hyperframes.heygen.com/concepts/determinism - the frame model (t = floor(frame)/fps), the five determinism rules, BeginFrame capture, Docker for reproducibility, and the motion-blur exception.
- https://hyperframes.heygen.com/packages/producer - output formats, HDR/4:4:4/HLS constraints, executeRenderJob API, the Hono render server and the distributed plan/renderChunk/assemble entry.
- https://hyperframes.heygen.com/packages/engine - the seek protocol (window.__hf with duration and seek(time)), capture/encode primitives, WebGPU probe, remote-download limits.
- https://hyperframes.heygen.com/packages/core - core surfaces (compiler, variables, runtime), compileHtml / bundleToSingleHtml, parse/generate helpers.
- https://registry.npmjs.org/hyperframes and https://registry.npmjs.org/hyperframes/latest - version 0.9.54 published 2026-11-15, first publish 2026-03-23, 641 versions, engines node>=22, Apache-2.0, puppeteer-core dependency, bin entries.
- https://api.github.com/repos/heygen-com/hyperframes - 84,726 stars, 7,844 forks, 203 watchers, 294 open issues+PRs, created 2026-03-10, license Apache-2.0.
- https://api.github.com/repos/heygen-com/hyperframes/contributors - Link header reports 144 contributor pages at per_page=1, i.e. 144 contributors.
- https://api.npmjs.org/downloads/point/last-month/hyperframes - 5,223,278 downloads for 2026-10-16..2026-11-14.
- https://hyperframes.heygen.com/guides/performance - the 25.0 s vs 9.8 s, 300-frame 1920x1080 benchmark and the VP9/WebGL cost notes.
- Source: packages/producer/src/services/renderOrchestrator.ts - the six named stages (compile, probe, extract videos, audio, capture, encode, assemble).
- Source: packages/engine/src/services/frameCapture.ts and browserManager.ts - HeadlessExperimental.beginFrame primary path with Page.captureScreenshot fallback.
- Source: packages/engine/src/services/videoFrameExtractor.ts / videoFrameInjector.ts - videos pre-extracted by FFmpeg and replaced with <img> during capture.
- Source: packages/cli/src/commands/render.ts - exact CLI flags and defaults (fps, quality looks/CRF16, workers auto, formats, resolution semantics).
- https://hyperframes.heygen.com/quickstart - the current quickstart paths (Studio app, agent skills, AI chat) and the npx hyperframes init/preview/render loop.

**Open Questions**

- What is the real throughput of a 28 s 1080x1920@60 composition with kinetic captions on this workstation (no documented 9:16 60 fps number exists)?
- How large is the byte-level drift between two machines rendering the same project without Docker, and is it visible after H.264 at delivery bitrates?
- Does the AWS Lambda adapter's cost per 28 s 1080x1920@60 render beat a local multi-worker render for this volume?
- How stable is the CLI/API surface across 0.9.x minor bumps in practice, and how often do golden baselines break on upgrade?

### Other Info

**Maturity**

stable

**Performance And Cost**

Documented benchmark: 300 frames at 1920x1080 rendered in 9.8 s (median of three runs, ~30.6 output fps) and 25.0 s with one expensive CSS declaration added, with pixel comparison proving the picture was unchanged. That is a 1080p landscape number, not 1080x1920@60, so treat 9:16 60 fps throughput as unmeasured until benchmarked on the render host. Cost drivers: capture (per-frame Chrome work, heavy filters/blur/masks), source-video extraction (FFmpeg, cached), VP9 encoding (CPU-heavy), worker count (auto; each worker ~256 MB), and software WebGL (5-50x slower than hardware). AWS Lambda renders bill by compute time and are SDR-only; Cloud Run and local Docker are the other paths. Disk: frames plus extraction cache dominate, and `hyperframes clean` exists to reclaim it.

---

<a id="python-pipeline-integration"></a>
## Python Pipeline Integration

### Basic Info

**Title**

Python Pipeline Integration

**Domain**

Integration: driving HyperFrames from an existing Python/FFmpeg renderer

**One Liner**

Render the text layer as a transparent HyperFrames overlay through a subprocess bridge, then composite it over the beat-cut footage inside the existing single-pass FFmpeg encode.

**Official Sources**

- https://hyperframes.heygen.com/packages/cli
- https://hyperframes.heygen.com/developers/cli
- https://hyperframes.heygen.com/concepts/variables
- https://hyperframes.heygen.com/guides/rendering
- https://hyperframes.heygen.com/packages/producer
- https://hyperframes.heygen.com/packages/core
- https://github.com/heygen-com/hyperframes
- https://www.npmjs.com/package/hyperframes

**Maturity**

beta

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

It moves every piece of type out of PIL/FFmpeg and into a real browser layout engine, while leaving the parts of the pipeline that already work (beat-snapped cutting, punch-ins, audio mix, loudness, run bookkeeping) untouched. The renderer keeps writing raw bgr24 frames into FFmpeg; a second input carries the alpha overlay, so text and footage are composited and encoded in one pass, with no intermediate generation loss and no second full encode. What that buys concretely: per-glyph and per-word kinetic type with real easing curves, CSS text wrapping/auto-fit, gradients, strokes, masks, shaders and Lottie/Three.js blocks, plus a visual QA path (`snapshot`, `check`) that does not exist for drawtext.

**Key Features**

- Transparent output is a first-class format: `--format webm` (VP9 + alpha), `--format mov` (ProRes 4444 + alpha) or `--format png-sequence` (RGBA). MP4 carries no alpha, so it is the wrong format for an overlay.
- VP9 alpha is stored out-of-band: ffprobe reports `pix_fmt=yuv420p` and `ALPHA_MODE=1`; FFmpeg only decodes the alpha plane when `-c:v libvpx-vp9` is passed BEFORE `-i` (verified: default decoder returned alpha=255 everywhere, alpha-aware decoder returned 0 in empty areas and 255 on glyphs).
- Measured alpha fidelity of the VP9 overlay against the composition's own PNG still: alpha MAE 0.31/255 (max 10), composited RGB MAE 0.056/255 overall and 0.395/255 in the caption band, identical ink coverage (20.09% both) - the compression is visually lossless for 46 px text.
- Machine progress on piped stdout: `@hf-progress {"code":"capture","done":480,"total":480,"pct":80}`. Observed codes and percentages: compile 5, extract_video 10, process_audio 20, start_browsers 25, capture 25-80, encode 80-90, assemble 90-99, then a final `@hf-result {"version":1,"operation":"render",...}` line.
- Fixed exit-code contract, verified on this machine: 0 success, 1 ran-and-failed (lint refusal, runtime failure, render-lock busy with `--no-wait`), 2 wrong command line (unknown flag). A Python bridge can therefore distinguish 'our bug' (2, never retry) from 'the render failed' (1, report the stderr tail).
- One local render at a time per user account: a lock file at `~/.cache/hyperframes/locks/render.lock`; a second render waits (default) or exits 1 with `--no-wait`, printing the holder's pid, project and elapsed time. Verified live against another session's render: `EXIT=1` and `pid 973876, project /tmp/opencode/myhf/reel28, running 5m 52s`.
- Static-frame dedup is on by default and is what makes an overlay cheap: the 8 s benchmark reused 348 of 480 frames (73%), logging `[static-dedup] reused 348/480 frame(s) (73%), est. ~50808ms saved (avg 146ms/frame)`.
- Root duration is baked into the HTML (`data-duration`); a composition without it is refused at render time (`[FrameCapture] Composition has zero duration`, exit 1), which is exactly the guard a generator wants.
- `--variables` / `--variables-file` with `--strict-variables` give typed, validated per-video injection (string, number, color, boolean, enum, font, image) bound with `data-var-text`, `data-var-src` or `var(--id)`.
- `snapshot --at <t>` writes RGBA PNG stills plus a contact sheet without encoding a video, so a generator can be visually reviewed in ~15 s before a 5-minute overlay render.
- `lint --json` is a pre-flight gate for the generated HTML (it caught a missing doctype and a missing `data-duration` on a deliberately broken fixture, and with `--strict` it refuses the render).
- The renderer writes project state (`.hyperframes/render-outputs.json`, `video-state.json`) into the project directory and history blobs into `~/.cache/hyperframes/history`, so the generated project must live inside the run directory or a disposable cache dir.

**Technical Mechanism**

The CLI compiles the HTML (inlining assets, localising fonts), serves the project from a loopback HTTP server, drives a pinned Chrome/`chrome-headless-shell` through CDP, seeks every registered timeline to the frame time and captures each frame (BeginFrame, or `Page.captureScreenshot` in the low-memory profile this machine triggers), then encodes with FFmpeg. For our integration only three of those details matter: the output can be VP9+alpha, progress is machine-readable on stdout, and the render is serialised per user by a lock file. On this box the log said `screenshot capture - hardware gpu - compile 2.9s - setup 8.5s - capture 49.5s`, i.e. Chrome did get the ANGLE/D3D12 GPU through WSL2, but the <=8 GB RAM profile still forced the slower screenshot capture path and one worker.

**Text And Motion Capabilities**

This is the reason to do it. Today `build_overlay()` measures text with PIL, draws it once into a static RGBA bitmap, and `CaptionRenderer` blits per-word highlights with fixed pixel offsets; there is no wrapping engine, no kerning beyond the TTF, no per-glyph animation, no masks, no gradients per word and no easing other than the three hand-written curves in the file. In HyperFrames the same copy is DOM text: CSS does the line breaking and auto-fit, GSAP/WAAPI do the timing (power2/power3/back/elastic, yoyo, stagger, SplitText-free per-word spans), and per-word or per-glyph transforms, clip-path wipes, gradient fills, strokes and blurs are ordinary CSS. Frame accuracy is the renderer's: it seeks to `floor(frame)/fps` and captures, so a stutter in the preview never becomes a dropped frame in the file. The precision limit moves from 'the PIL font size that fits' to 'what the browser lays out at DPR 1', which is why the QA width checks have to be re-grounded (see constraints).

### Usage & API

**Setup And Install**

Node.js >= 22, FFmpeg/ffprobe on PATH, and a Chrome the CLI can launch (it downloads a pinned `chrome-headless-shell` into `~/.cache/hyperframes/chrome`, or uses the system Chrome it finds). Verified on this machine with `npx --yes hyperframes@0.9.54 doctor --json`: node v24.22.2, ffmpeg 6.1.1, Chrome `system: /usr/bin/google-chrome`, `ok: true`. Nothing is installed into the Python project; pin the CLI version in the command line (`hyperframes@0.9.54`) so a background npm release cannot change rendering behaviour between two runs.

```bash
node --version                 # >= 22
npx --yes hyperframes@0.9.54 doctor --json | jq '.ok'
```

**Minimal Code Example**

The overlay composition that was actually rendered for this research (1080x1920, 2 s, transparent, GSAP paused timeline registered on `window.__timelines`):

```html
<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<style>
  @font-face { font-family:"InterBlack"; src:url("assets/fonts/Inter-Black.ttf") format("truetype"); }
  html, body { margin:0; background:transparent; }        /* nothing painted = transparent output */
  #stage { position:relative; width:1080px; height:1920px; overflow:hidden; }
  .clip { position:absolute; }
  #title { left:65px; top:118px; width:885px; font-family:"InterBlack"; font-size:58px; color:#fff; text-align:center; }
  #pill { left:340px; top:1280px; width:400px; padding:16px 0; border-radius:24px; background:#059669;
          color:#fff; font-family:"InterBlack"; font-size:54px; text-align:center; }
  #caption { left:65px; top:1420px; width:950px; font-family:"InterBlack"; font-size:46px; color:#fff; text-align:center; }
  #caption .hot { color:#10b981; }
</style></head><body>
  <div id="stage" data-composition-id="overlay" data-start="0"
       data-width="1080" data-height="1920" data-duration="2" data-fps="60">
    <div id="title"   class="clip" data-start="0"    data-duration="2"    data-track-index="0">IPHONE 14 AZUL</div>
    <div id="pill"    class="clip" data-start="0.25" data-duration="1.75" data-track-index="0">$330 USDT</div>
    <div id="caption" class="clip" data-start="0.5"  data-duration="1.5"  data-track-index="0">BATERIA <span class="hot">77%</span> REAL</div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <script>
    const tl = gsap.timeline({ paused: true });
    tl.fromTo("#title",   {y:40, opacity:0}, {y:0, opacity:1, duration:0.35, ease:"power3.out"}, 0);
    tl.fromTo("#pill",    {scale:1.12, opacity:0}, {scale:1, opacity:1, duration:0.18, ease:"power2.out"}, 0.25);
    tl.fromTo("#caption", {y:26, opacity:0}, {y:0, opacity:1, duration:0.22, ease:"power2.out"}, 0.5);
    window.__timelines = window.__timelines || {};
    window.__timelines["overlay"] = tl;   // id must equal data-composition-id
  </script>
</body></html>
```

Render and composite (both commands were run for this research):

```bash
npx --yes hyperframes@0.9.54 render . --fps 60 --format webm --workers 1 \
    --low-memory-mode --output out/overlay.webm
# -> 2.0s video, 115.7 KB, rendered in 1m 1.5s

ffmpeg -y -f lavfi -i color=c=0x1f2937:s=1080x1920:r=60:d=2 \
  -c:v libvpx-vp9 -i out/overlay.webm \
  -filter_complex "[0:v][1:v]overlay=format=auto:eof_action=pass[out]" -map "[out]" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p out/composite.mp4
```

**Cli Or Api Surface**

- `hyperframes render [dir] --fps 60 --format webm --output <file>` - the overlay render; `--quality draft|standard|looks|high|delivery` maps to VP9/H.264 CRF tiers.
- `--workers 1|auto` and `--low-memory-mode` - on a <=8 GB box the low-memory profile is auto-enabled (1 worker, screenshot capture); `--workers` only parallelises inside one render.
- `--chroma-subsampling 444` is MP4-only (lossless PNG capture + yuv444p); it does not apply to the WebM overlay path.
- `--variables '<json>'`, `--variables-file <file>`, `--strict-variables` - typed per-video values validated against `data-composition-variables`.
- `--json` - suppresses progress entirely and prints exactly one `{"ok":false,"error":{...}}` document on failure; do not combine it with a progress parser.
- `--no-wait` / `HYPERFRAMES_RENDER_LOCK=off` - fail fast instead of queueing behind another render of the same user account.
- `--browser-timeout <s>`, `--protocol-timeout`, `--player-ready-timeout` - per-stage limits; there is no whole-render timeout, so the Python caller must own one.
- `hyperframes lint . --json` - structural pre-flight (exit 1 + findings when broken, `--strict` refuses the render).
- `hyperframes snapshot . --at 1.0 --output out/still.png --json` - RGBA stills + contact sheet, ~15 s, no video encode.
- `hyperframes doctor --json` - machine readiness; gate on `.ok`, not on the exit code (it exits 0 whenever it ran).
- Node API (`@hyperframes/producer`): `createRenderJob(config)` + `executeRenderJob(job, projectDir, outputPath, onProgress, abortSignal)` with `RenderCancelledError`; useful only if you need in-process cancellation/progress events and can afford a small long-lived Node service.

**Key Config Options**

- `--format webm` + default quality: the overlay. `--format mov` for ProRes 4444 alpha if VP9 alpha ever fails downstream.
- `--fps` must equal `canvas.fps` (60); a mismatch silently changes the overlay's frame count relative to the base render.
- `data-duration` on the root element: the only way to set overlay length - it is not a variable, not a CLI flag, and not derived from the audio.
- `--workers 1` + `--low-memory-mode`: correct for 7.8 GB RAM; measured 146 ms per captured frame at 1080x1920 with 73% dedup.
- `--quality looks` (CRF 16 equivalent) is the default and what the measured files used; `draft` halves quality for iteration renders.
- `--strict-variables` on every production render: an undeclared or mistyped value becomes an error instead of a silent default.
- `HYPERFRAMES_CHROME_SANDBOX=0` on Ubuntu 23.10+/WSL2 (unprivileged user namespaces are restricted; the renderer warns and retries without the sandbox).
- `HYPERFRAMES_CACHE_DIR` if the pipeline must run as a different user than the interactive shell, so both share the same browser/fonts cache and render lock.

### Integration

**Pipeline Fit**

Augments, and partially replaces. It replaces the text half of `render_video.py` - `build_overlay()` (the static PIL title/subtitle/badges/price/footer bitmap), `CaptionRenderer` (per-frame PIL blits) and the hand-written auto-fit maths - with one generated HTML composition rendered to a transparent overlay. It keeps the half that is genuinely good: `beat_grid.py`'s measured tempo, `edit_plan.py`'s cuts, the punch-in, the grade, the audio mix (music bed, ducking, SFX, loudnorm), the single x264 encode, and `runs.json`/`latest.json`. Nothing about cut timing, in-point quality or audio moves into HyperFrames, so the renderer still owns the two things it is uniquely good at: frame-exact footage and loudness-compliant audio. The seam is a file: `<run>/overlay/overlay.webm` at exactly the same fps and frame count as the base render.

**Python Integration**

One module, `hf_overlay.py`, with a subprocess bridge - not the Node API. Reasons: the CLI is already the supported non-interactive surface, it emits machine progress, and a subprocess cannot leak a Chrome instance into the Python process. The bridge must (a) drain stdout and stderr on two threads (a single pipe deadlocks once the renderer writes a few hundred progress lines), (b) parse only `@hf-progress {...}` lines and ignore the interleaved human bar (`  ####  42%  Encoding frame 81/120`), which the CLI prints even when stdout is a pipe, (c) start the child with `start_new_session=True` and kill the whole process group on timeout - npx -> node -> Chrome is a tree, so killing only the npx pid can orphan Chrome (verified: after `os.killpg` no `chrome` or `hyperframes` process from the killed render remained), (d) enforce its own wall-clock timeout because HyperFrames has per-stage timeouts but no whole-render one, and (e) translate exit code 2 into a `HFUsageError` that is never retried and 1 into a `HFRenderError` carrying the last ~20 stderr lines.

Layering: `overlay_gen.py` (sku.json + narration_lines.json + caption_audit cards -> `overlay/index.html` + `overlay/vars.json`) -> `hf_overlay.py render` -> `render_video.py --overlay <webm>`. The renderer change is about 15 lines: add the overlay as a second FFmpeg input with `-c:v libvpx-vp9` BEFORE `-i`, add `-filter_complex "[0:v][1:v]overlay=format=auto:eof_action=pass,format=yuv420p[v]" -map "[v]"`, and keep mapping audio from the mix input (its index shifts by one). Measured cost of that filter on 120 frames of raw bgr24: 5.2 s without the overlay vs 5.4 s with it (+4%), i.e. negligible next to the overlay render itself.

**Data Injection**

Three tiers, because they fail differently. (1) Scalars that a human may want to tweak without regenerating HTML - title, subtitle, price, footer, accent colour, safe-zone mode - are declared in `data-composition-variables` and passed with `--variables-file overlay_vars.json --strict-variables`, so a renamed or retyped key fails the render instead of silently rendering the default. (2) Timing data - the cards array `[{text, t0, t1, words:[{w,t0,t1}], hot:[i]}]` derived from `narration_lines.json` and the word timings - is structural: it decides how many spans exist, so it is inlined into the generated HTML as `<script type="application/json" id="hf-data">...</script>` and the GSAP timeline is built from it at runtime (paused, deterministic, absolute times). (3) Everything else - root duration, fps, canvas size, asset paths - is baked into the HTML because the runtime reads those before any composition script runs. The inlined JSON is escaped for the `<script>` context (`<`, `>`, `&` -> `\u003c`, `\u003e`, `\u0026`), and every text node goes through `html.escape(..., quote=True)`; never build a JS string by concatenating user copy.

**Footage And Asset Handling**

Footage never enters HyperFrames: the overlay is text and vector only, so there is no video extraction step, no frame cache and no 2 GiB extract budget to manage. Audio never enters either - the overlay has no audio track, and `render_video.py` keeps muxing the loudnorm'd mix, which also means HyperFrames' AAC 192 kbps default never touches the delivery audio. Fonts must be local files inside the project (copy `assets/fonts/Inter-*.ttf` into `overlay/assets/fonts/` and `@font-face` them): a Google Fonts CDN link makes the render depend on network latency and on a font version you do not control, and the measured spike loaded from a CDN only because it was a throwaway. The generated project lives at `output/<sku>/<run>/overlay/`, so `.hyperframes/` state and any `snapshot` artifacts stay inside the run and are never written into `input/<sku>/`.

**Migration Effort**

medium

**Integration Steps**

- Spike first, in a throwaway dir, with no pipeline changes: one 2 s transparent composition, `render --format webm`, then `ffmpeg -c:v libvpx-vp9 -i overlay.webm ... -filter_complex overlay` over a colour source. Gate: ffprobe shows `ALPHA_MODE=1`, the decoded frame has alpha 0 in an empty corner and 255 on a glyph, and the composited pixel on the pill equals the configured emerald (#059669 -> measured RGB 2,148,104 after yuv420).
- Prove the pipe shape before touching render_video.py: a 30-line script that writes 120 raw bgr24 frames into `ffmpeg -f rawvideo ... -i -` with the overlay as input 1 and the filter graph above. Gate: output duration and frame count match the base, and a mid-timeline frame shows the overlay; measured 5.2 s vs 5.4 s, so the filter is not the cost centre.
- Write `overlay_gen.py`: pure function `(cfg, sku, lines, cards) -> html_string`, with `html.escape` for text, `\u003c`-escaped JSON for the data block, integer-clamped pixel values, and a `data-composition-variables` block for the six scalar slots. Unit-test it with adversarial copy (`</script>`, `<!--`, quotes, `&`, emoji, RTL, a 40-character word).
- Generate the card list once, in Python, from the same `group_words()` used today, and write it to `output/<sku>/caption_audit.json` BEFORE the render (the audit stops being a by-product of drawing). Keep `font_px`, `width_px` (measured with the same TTF via PIL, now a prediction, not a fact), `cps`, `display_sec` so `c_caption_text`, `c_caption_zone`, `c_caption_dupes` and `c_cps_*` keep working unchanged.
- Run `hyperframes lint overlay/ --json` inside the generator and fail on `errorCount > 0`. Gate: the deliberately broken fixture (no doctype, no data-duration) is rejected with exit 1 and named findings.
- Render the overlay with `hf_overlay.py` (subprocess, two threads, its own timeout, process-group kill, `--workers 1 --low-memory-mode`, pinned CLI version) into `<run>/overlay/overlay.webm`, and record `render_sec`, `frames`, `stages` and the stderr warnings into `<run>/edit_plan.json` under a new `overlay` key.
- Assert the contract before compositing: `abs(overlay_frames - round(total*fps)) <= 1`, fps equal, size 1080x1920, and `ALPHA_MODE=1`. On mismatch, fail loudly - never let `eof_action=repeat` freeze the last card over the ending (verified: `repeat` leaves the caption on screen at 2.6 s of a 3 s base, `pass` does not).
- Composite inside the existing encode: keep the rawvideo pipe, add `-c:v libvpx-vp9 -i overlay.webm` and `-filter_complex ... -map "[v]"`, shift the audio map index, and keep `-shortest` off the video side so a short overlay cannot truncate the reel.
- Re-ground QA: add `o_overlay_alpha` (ALPHA_MODE tag), `o_overlay_coverage` (mean alpha > 0.8% and < 25% via `alphaextract,scale=135:240`, measured 3.3% for a real text overlay), `o_overlay_frames`, `o_text_ink` (at 3 sample times the caption zone must contain >0.2% pixels above the local background luma), and keep `c_clip_identity` untouched because its crop band (y 22%-60%) is exactly the band the overlay never covers.
- Add a 1-frame `overlay_ssim` to `eval_run.py` (decode both runs' overlays with `-c:v libvpx-vp9`, compare 3 frames) plus `overlay_alpha_mean` and `overlay_render_sec`; re-promote `eval/<sku>_baseline.json` once, in the same commit that switches the renderer, so the SSIM diff is meaningful from the next run on.
- Run one SKU end to end and compare against the last PIL run: same 29 QA rows plus the new overlay rows, same duration +/- 1 frame, size within the WhatsApp budget, and a human look at `contact_sheet.png` for clipping in the new text (Chrome metrics differ from PIL's by a few pixels).
- Only then flip the default in `session3_config.json` (`overlay: {"mode": "hyperframes", "cli": "hyperframes@0.9.54"}`) and keep `"mode": "pil"` reachable for one release as the rollback path.

### Constraints

**Requirements**

- Node.js >= 22 (the CLI's `engines`), npx, and a Chrome the CLI can drive: it downloads a pinned `chrome-headless-shell` into `~/.cache/hyperframes/chrome` on first use, or uses the system Chrome (`/usr/bin/google-chrome` here).
- FFmpeg + ffprobe on PATH for the renderer, and an FFmpeg build with the VP9 decoder for the compositor (`libvpx-vp9`); ffmpeg 6.1.1 on this box decodes the alpha plane correctly.
- RAM: each Chrome worker is ~256 MB; <=8 GB total flips the low-memory profile (1 worker, screenshot capture). This machine (7.9 GB, 3.6 GB available) ran every measurement in that profile.
- Disk: the overlay path needs almost nothing (115.7 KB for 2 s, 392 KB for 8 s); the PNG-sequence fallback is the expensive one (~1.5-3 MB per 1080x1920 RGBA frame, ~2.5-5 GB for 28 s).
- A per-user cache dir writable by the pipeline user (`~/.cache/hyperframes`), because both the browser binary, the font cache and the render lock live there.
- No network at render time: fonts and assets must be local; a CDN `<script>`/`@font-face` turns a deterministic render into a networked one.

**Limitations**

- Alpha only exists in WebM/MOV/PNG-sequence; MP4 is opaque. The pipeline must therefore treat the overlay as a separate file and composite it, not ask for 'a transparent MP4'.
- FFmpeg drops the alpha plane unless `-c:v libvpx-vp9` is passed before `-i`; with the default decoder the overlay composites as a fully opaque black frame and the reel turns black. Verified: default decoder alpha=255 everywhere, alpha-aware decoder 0/255.
- `overlay` filter defaults to `eof_action=repeat`: if the overlay is even one frame shorter than the base, the last caption stays on screen until the end. Use `eof_action=pass` and assert equal frame counts.
- Renders are serialised per user account by a lock file. A parallel `batch_run.py` or a second agent session will queue (or fail with exit 1 under `--no-wait`) - the pipeline must serialise overlay renders itself and say so in the error message.
- Render duration is fixed by `data-duration` in the HTML; it is not a CLI flag and not a variable. The generator must compute `round(vo_duration*fps)/fps` and the compositor must tolerate <=1 frame of rounding.
- Every render writes `.hyperframes/` into the project directory and history blobs into `~/.cache/hyperframes/history`; generating the project inside `input/<sku>/` would pollute the source tree and confuse `extract_keyframes.py`.
- Chrome text metrics are not PIL metrics: a card that measured 940 px with PIL can wrap or overflow at 950 px in the browser. `c_caption_zone` must either measure the rendered overlay (pixel ink bbox) or accept a tolerance, otherwise it will fail on cards that look fine.
- The first render after a Chrome/font change is not byte-identical to the previous one; only the Docker path pins Chromium and fonts. Baselines and SSIM comparisons must be re-promoted after such a change.
- No whole-render timeout exists (only per-stage `--browser-timeout`, `--protocol-timeout`, `--player-ready-timeout`), so a hung Chrome is the caller's problem to detect and kill.
- A render that is killed mid-flight leaves no partial output (good) but does leave the lock owner file; the next render detects the dead pid and takes over, which costs a few seconds.

**Known Issues**

- Low-memory mode forces `screenshot capture` and logs `BeginFrame did not run. Needs chrome-headless-shell and no --resolution upscale`; on this box that was ~410 ms/frame on the 2 s composition versus 146 ms/frame on the 8 s one, where 73% of frames were deduped.
- `--json` is not a progress mode: it suppresses the `@hf-progress` lines entirely. A bridge that pipes both must choose one (progress parser OR json envelope).
- The human progress bar is printed even to a non-TTY stdout, interleaved with `@hf-progress` lines (`  ####  87%  Encoding frame 81/120`), so a naive line parser that assumes pure NDJSON breaks.
- `--chroma-subsampling 444` (the fix for thin coloured glyphs on MP4) does not exist on the WebM alpha path; text sharpness there is VP9 quality, which measured visually lossless for 46 px type but has no documented 4:4:4 equivalent.
- VP9 alpha is only decoded by browsers and by FFmpeg when told to; editors and most NLEs show black. If a human ever has to open the overlay in Resolve/Premiere, render MOV/ProRes 4444 instead.
- `hyperframes doctor` exits 0 even when checks fail; gates must read `.ok` from the JSON, not `$?`.

**Licensing**

HyperFrames is Apache-2.0 (npm package `hyperframes@0.9.54`, repo LICENSE Apache-2.0): no seat count, no per-render fee, no commercial-use threshold, and the CLI can be pinned and vendored. The only paid surfaces are the optional HeyGen-hosted cloud render, the AWS/GCP drivers you would pay for yourself, and HyperFrames Pro features inside the desktop app - none of which this integration touches. GSAP, if used in the composition, has its own license (the free build is fine for this use; GSAP's paid plugins are not needed for plain tweens and are not bundled by the CLI).

### Evidence

**Confidence**

0.86

**Sources**

- https://hyperframes.heygen.com/packages/cli - render flag table (workers, low-memory-mode, variables, strict-variables, no-wait, browser/protocol/player-ready timeouts, format matrix), the per-account render lock and `HYPERFRAMES_RENDER_LOCK`.
- https://hyperframes.heygen.com/developers/cli - exit-code contract (0/1/2), `@hf-progress` NDJSON on non-TTY stdout with the stage codes, `_meta` envelope.
- https://hyperframes.heygen.com/guides/rendering - transparent-video rules (leave html/body unpainted), WebM VP9 alpha vs MOV ProRes 4444, the warning that FFmpeg's default VP9 decoder produces an opaque image and that `-c:v libvpx-vp9` must precede `-i`.
- https://hyperframes.heygen.com/concepts/variables - `data-composition-variables` kinds, `data-var-text`/`data-var-src`, `--variables-file`, `--strict-variables`, and the explicit list of what cannot be a variable (viewport, duration, fps, format).
- https://hyperframes.heygen.com/packages/producer and https://hyperframes.heygen.com/packages/core - `createRenderJob`/`executeRenderJob`/`RenderCancelledError`, `createGSAPFrameAdapter`, and the note that the FrameAdapter interface is experimental v0.
- Local clone of heygen-com/hyperframes @ e2430ed (2026-11-15): packages/cli/src/ui/progress.ts (the `@hf-progress` writer and its TTY guard), packages/cli/src/utils/renderLock.ts (lock path `hyperframesCacheDir("locks","render.lock")`, 1 s poll, `HYPERFRAMES_RENDER_LOCK=off`), packages/cli/src/commands/render/renderSlot.ts (busy message with pid/project/elapsed), packages/cli/src/commands/render.ts (flag descriptions incl. `--variables`, `--no-wait`, `--low-memory-mode`).
- Hands-on spike, 2026-11-15, /tmp/opencode/hfspike: HF 0.9.54 `doctor --json` ok with system Chrome; `lint --json` clean on the good fixture and 3 errors on a broken one; `render --format webm` at 1080x1920@60 produced 2 s/120 frames in 61.5 s and 8 s/480 frames in 74.8 s with 73% static-dedup reuse; exit codes 2 (unknown flag), 1 (lint-strict refusal), 1 (lock busy with --no-wait against another session's live render); ffprobe `ALPHA_MODE=1`; alpha-aware vs default decoder comparison (0/255 vs 255/255); composited pixels over #1f2937 matched the configured colours; `eof_action=repeat` vs `pass` behaviour at 2.6 s of a 3 s base.
- Composite overhead measurement, same spike: 120 raw bgr24 frames through libx264 CRF 18 took 5.2 s base-only vs 5.4 s with `-c:v libvpx-vp9 -i overlay.webm` and the overlay filter (+4%).
- Quality measurement, same spike: overlay webm decoded at t=1.0 vs the composition's own `snapshot --at 1.0` PNG - alpha MAE 0.31/255 (max 10, p99 4), composited RGB MAE 0.056/255, caption-band MAE 0.395/255, ink coverage 20.09% both.
- Repo baseline for the cost comparison: output/demo_iphone14_blue/*/edit_plan.json recorded `elapsed_sec` 185.9 and 172.9, output/iphone14_blue/*/edit_plan.json 158.4 for the current 28 s render.

**Open Questions**

- A full 28 s overlay was not rendered end to end; the 4-6 min figure is an extrapolation from a measured 146 ms/frame at 8 s with 73% dedup. A 28 s run with 16 cards will have a slightly lower reuse ratio and should be measured before committing.
- The ProRes 4444 (`--format mov`) path was not rendered or composited here; its alpha decoding by this FFmpeg build is documented, not measured.
- Whether the existing `c_caption_zone` thresholds (width <= 885 px, font >= 24 px) survive Chrome's text metrics is unmeasured; the generator measures with PIL, the browser lays out with HarfBuzz/fontconfig, and the two can differ by a few pixels per line.
- Whether `--quality high` or a different VP9 `--vp9-cpu-used` improves text edge crispness on a real product reel (the 2 s sample was already visually lossless, but it had no thin strokes on a busy background).
- Whether the Node producer API is worth using instead of the CLI when the pipeline needs mid-render cancellation (`AbortSignal`) rather than a timeout kill; not tested.
- How Chrome behaves under memory pressure when the base render (Python + x264) runs concurrently with the overlay render on an 8 GB box; not tested, and likely to swap.

### Other Info

**Output Formats**

- WebM/VP9 + alpha (`yuva420p`, `ALPHA_MODE=1`) - the overlay format for this pipeline; 2 s of three text blocks measured 115.7 KB, 8 s of seven blocks 392 KB.
- MOV/ProRes 4444 + alpha - editor-grade intermediate, much larger; the fallback if a tool in the chain cannot decode VP9 alpha. [uncertain] documented, not rendered in this spike.
- PNG sequence (RGBA frames + audio sidecar) - lossless, ~1-3 MB per 1080x1920 frame, only for debugging or a frame-exact diff. [uncertain] size is derived, not measured.
- MP4/H.264 - opaque only; correct for a full reel rendered entirely in HyperFrames, wrong for an overlay.
- GIF and HLS - irrelevant here (GIF has no audio and <=30 fps; HLS is a delivery package).

**Performance And Cost**

Measured on this machine (WSL2, Ryzen 7 5825U, 7.9 GB RAM, HF 0.9.54, 1 worker, low-memory profile, Chrome via ANGLE/D3D12): a 2 s / 120-frame 1080x1920@60 overlay with three text blocks rendered in 61.5 s wall (compile 2.9 s, setup 8.5 s, capture 49.5 s), 115.7 KB. An 8 s / 480-frame overlay with six cards and two static blocks rendered in 74.8 s (capture 71.0 s), 392 KB, with 348/480 frames reused by static dedup and an average of 146 ms per frame. Extrapolating the measured per-frame cost to a 28 s reel (1680 frames) gives ~245 s of capture, and because card density is comparable (0.57-0.75 cards/s) a realistic range is 4.1-5.5 min plus ~10 s of setup - call it 4-6 min. The existing full render is 158-186 s (measured on the last three runs), so the pipeline goes from ~3 min to ~7-9 min per reel: roughly 2.5x wall time, entirely in the overlay render. Mitigations that follow from the measurements: cache the overlay by content hash (HTML + vars + fonts) so footage-only iterations reuse it; render it in parallel with the base render only if RAM allows (the HF lock does not block FFmpeg); and use `--quality draft` for review renders. Money: zero - everything above ran locally with no account, no API key and no cloud call. [uncertain]

---

<a id="rendering-engine-performance-and-output"></a>
## Rendering Engine Performance and Output

### Basic Info

**Title**

Rendering Engine Performance and Output

**Domain**

Rendering engine

**One Liner**

Headless Chrome seeks and captures each frame, FFmpeg encodes it: configurable fps, supersampling, H.264/H.265/VP9/ProRes, alpha, audio mux and worker-parallel capture.

**Official Sources**

- https://github.com/heygen-com/hyperframes
- https://hyperframes.heygen.com/guides/rendering
- https://hyperframes.heygen.com/guides/performance
- https://hyperframes.heygen.com/guides/4k-rendering
- https://hyperframes.heygen.com/concepts/determinism
- https://hyperframes.heygen.com/packages/cli
- https://hyperframes.heygen.com/packages/engine
- https://hyperframes.heygen.com/packages/producer
- https://hyperframes.heygen.com/help

**Maturity**

stable

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

It turns a composition into a deliverable file with production-grade controls: 1-240 fps (including NTSC rationals), resolution presets that supersample through Chrome's deviceScaleFactor while the composition stays authored at one size, six output containers (MP4, WebM, MOV, GIF, PNG sequence, HLS), CRF or bitrate targeting, keyframe-interval and peak-bitrate caps, 4:4:4 colour for text-heavy masters, HDR10, alpha for overlays, AAC/Opus audio, Partial-range renders, stills, and a Docker path for cross-machine reproducibility.

**Key Features**

- Capture model: the engine never plays the video. For each frame it computes `time = floor(frame) / fps` with integer math, seeks every registered timeline to that time, and captures with Chrome's `HeadlessExperimental.beginFrame` (atomic, no half-painted frames) on a pinned `chrome-headless-shell`, falling back to `Page.captureScreenshot` where BeginFrame is unavailable.
- Frame parallelism: `--workers` takes 1-24 (each worker is a separate Chrome process, roughly 256 MB); `auto` resolves to `max(6, min(16, floor(cpuCores / 8)))`. Parallelism only engages above `minParallelFrames` (120) and each worker needs at least 30 frames, with `coresPerWorker` defaulting to 2.5.
- fps: 1-240, integer (24, 25, 30, 50, 60, 120, 240) or ffmpeg rationals (30000/1001, 24000/1001, 60000/1001). Default is the root `data-fps`, else 30.
- Resolution: `--resolution` presets (landscape/portrait/square at 1080, 1440p and 4K) are applied by raising Chrome's `deviceScaleFactor`, not by re-authoring: the composition keeps its layout and is captured at the requested pixel size. Constraints: same aspect ratio, integer multiple, never smaller than the composition, MP4 only, never with `--hdr`.
- Encoding tiers: `draft` = libx264 ultrafast at CRF 28; `standard` = medium at CRF 18; `high` (= `delivery`) = fast at CRF 15; the default `looks` tier is `standard` with CRF 16. `--crf 0-51` and `--video-bitrate` override and are mutually exclusive; both are rejected for MOV.
- Delivery controls on MP4: `--keyframe-interval <s>` forces a keyframe at least every N seconds (locks the GOP, needed for `-c copy` segmenting), `--max-bitrate 8M` caps the peak while the quality target still decides the rest, `--buffer-size` (default 2x the cap) sets the measurement window.
- Colour: 4:2:0 by default; `--chroma-subsampling 444` encodes H.264 High 4:4:4 Predictive (yuv444p) and switches frame capture to lossless PNG. SDR MP4 + software encoder only; rejected with `--gpu`, `--hdr`, other formats and distributed renders.
- Alpha: `--format webm` (VP9 with `yuva420p`, `-auto-alt-ref 0`, `alpha_mode=1`) or `--format mov` (ProRes 4444) or `--format png-sequence` (RGBA frames). MP4 carries no alpha. Transparency only survives where the composition paints nothing.
- Audio: all `<audio>`/`<video>` tracks are mixed to 48 kHz PCM, then encoded AAC 192 kbps for MP4/MOV or libopus 128 kbps for WebM, and muxed; GIF and PNG sequence carry no audio.
- HDR: HDR10 MP4 (H.265 10-bit, BT.2020, PQ or HLG) chosen automatically when the composition references HDR media (BT.2020 PQ/HLG video or 16-bit PNG) with `--hdr`/`--sdr` to force either path; not available on Lambda, not combinable with 4K supersampling.
- Partial range: `--start <s>` / `--end <s>` render only that span as an MP4 that starts at 0 and carries the span's audio. MP4 only, SDR only, integer fps of 24/25/30/60 only.
- Stills: `npx hyperframes snapshot` captures PNG stills at `--at` timestamps, `--frames N` evenly spaced, `--range start,end`, or per-sub-composition; `--zoom` crops with a raised device scale factor, `--against` pairs each still with the same frame of a reference video, `--review` builds beat/seam contact sheets. `--format png-sequence` writes every frame as RGBA PNG.
- Docker: `--docker` builds and runs a local `hyperframes-renderer:<cli-version>` image from the bundled `Dockerfile.render` (per-arch tag `-arm64` on Apple Silicon), with `--shm-size=2g`, the project mounted read-only at `/project` and the output directory writable at `/output`, plus `--gpus all` when `--gpu` is set.
- GPU: `--gpu` selects a hardware FFmpeg encoder (NVENC, VideoToolbox, AMF, VAAPI, QSV) while `--browser-gpu` selects host GPU acceleration for Chrome/WebGL capture; both are independent of the CPU-side parallelism and of each other.
- Determinism: identical input yields identical frames; worker count, chunk boundaries and seek order do not change pixels (motion blur is the stated exception, because it samples sub-frame times). Docker pins Chromium, fonts and the FFmpeg encoder so the platform stops being an input.

**Technical Mechanism**

Puppeteer (~25.13.0) drives a pinned `chrome-headless-shell` downloaded into `~/.cache/hyperframes/chrome`. The producer bundles the project, serves it from a local file server, opens the entry HTML, waits for the `__playerReady`/`__renderReady` gates, then per frame: sets the frame clock, calls the frame adapters' `seekFrame`, and captures. Capture has three paths - `HeadlessExperimental.beginFrame` (default), `Page.captureScreenshot` (fallback / forced with `--force-screenshot`), and an experimental `drawElementImage` fast path (macOS/Windows with a hardware-GPU browser only, ~2x faster, with an in-page OffscreenCanvas worker that encodes frame N while the main thread paints frame N+1). Source videos are pre-extracted to image frames with FFmpeg and injected as `<img>` elements so video decode never depends on wall-clock playback; that extraction is content-addressed and cached. Captured frames go either to a disk directory (JPEG by default, PNG for alpha/444) that FFmpeg reads, or straight into a streaming FFmpeg stdin (single-worker renders). FFmpeg encodes with libx264/libx265/VP9/ProRes, applies the colour conversion to tagged BT.709, then muxes the mixed AAC/Opus audio and writes container metadata naming the renderer and version.

**Text And Motion Capabilities**

Per-frame seeking means a render never drops or duplicates a frame: a composition that stutters in real-time preview still renders every frame exactly, and 60 fps is simply twice as many captured frames, not a different playback engine. Text sharpness is a render decision, not a typographic one: default capture is JPEG at 4:2:0, so thin coloured glyphs can fringe; `--chroma-subsampling 444` captures lossless PNG and encodes yuv444p, which the docs measure as about +16 dB chroma PSNR on coloured text (and about 2x the render time for text/UI, 3.5x for full-frame footage). Supersampling via `--resolution` re-rasterizes HTML text, SVG and CSS at the higher DPR, but cannot add detail to a 1080p `<video>`, a fixed-size `<canvas>` or a sub-4K image. Motion blur is not free temporal integration: the engine captures one instant per frame, so blur must be authored (the catalog's motion-blur component integrates a shutter window by sampling the timeline at sub-frame times) or injected per page. Easing and per-word/per-glyph control come from the animation layer (GSAP timelines seeked by the engine), not from the encoder.

**Output Formats**

- MP4 - H.264 (4:2:0 default, 4:4:4 optional) or H.265 (HDR10, hvc1 tag); AAC 192 kbps audio; `+faststart` on the muxed MP4; renderer/version container tags.
- WebM - VP9, with alpha when the composition leaves the background unpainted (yuva420p + alpha_mode=1); libopus 128 kbps audio; `--vp9-cpu-used -8..8` trades speed for size.
- MOV - ProRes 4444 with alpha by default, or `--prores-profile hq|422|lt|proxy` for a smaller 4:2:2 file with no alpha (transparent areas render black). No CRF/bitrate controls.
- PNG sequence - lossless RGBA frames in a directory, for AE/Nuke/Fusion. Largest output by far.
- GIF - no audio, 30 fps cap (60 fps option disabled for GIF), `--gif-loop 0-65535`, best around 15 fps.
- HLS - VOD playlist directory (`master.m3u8`, `video.m3u8`, `video_NNNNN.ts`, plus audio playlists when there is audio); identical encodes to MP4, packaged by stream copy; SDR only, `--gpu` rejected, segment length `--hls-segment-seconds` 1-60 (default 4).
- Stills - PNG via `snapshot` (default 1920x1080 unless the composition says otherwise), with optional zoom crops, reference pairing and review contact sheets.

### Usage & API

**Setup And Install**

Node.js 22+, FFmpeg and FFprobe on PATH, and a Chrome the CLI can launch (it downloads a pinned `chrome-headless-shell` into `~/.cache/hyperframes/chrome` on first use). No project install is required:

```bash
npx hyperframes doctor            # verifies node, ffmpeg, ffprobe, chrome, docker, disk
npx hyperframes render --output final.mp4
npx hyperframes render --docker --output final.mp4   # reproducible path
```


**Minimal Code Example**

```bash
# vertical 28 s reel, 60 fps, delivery quality
npx hyperframes render \
  --resolution portrait \
  --fps 60 \
  --quality delivery \
  --output reel.mp4

# transparent overlay for compositing, partial range
npx hyperframes render --format webm --fps 60 --start 3 --end 8 --output overlay.webm

# stills for QA
npx hyperframes snapshot . --at 2.0,14.5,27.0 --review --json
```

```ts
// programmatic (Node)
import { createRenderJob, executeRenderJob } from "@hyperframes/producer";
const job = createRenderJob({
  projectDir: "./reel",
  outputPath: "./out/reel.mp4",
  fps: { num: 60, den: 1 },
  quality: "high",
});
await executeRenderJob(job, { onProgress: (p) => console.log(p) });
```

**Cli Or Api Surface**

- `hyperframes render [dir]` with `--output/-o`, `--composition/-c`, `--fps/-f`, `--quality/-q`, `--format`, `--resolution`, `--crf`, `--video-bitrate`, `--max-bitrate`, `--buffer-size`, `--keyframe-interval`, `--chroma-subsampling`, `--prores-profile`, `--vp9-cpu-used`, `--gif-loop`, `--hls-segment-seconds`, `--video-frame-format`, `--page-motion-blur`, `--start`, `--end`.
- Machine/cost flags: `--workers/-w`, `--low-memory-mode`, `--gpu`, `--browser-gpu`, `--page-side-compositing`, `--experimental-fast-capture`, `--frames-cache-dir`, `--max-concurrent-renders`, `--docker`, `--wait`.
- Gate flags: `--strict`, `--strict-all`, `--best-effort/--no-best-effort`, `--strict-variables`, `--resume`, `--keep-segments`, `--debug`, `--quiet`, `--json`.
- `hyperframes benchmark [dir]` - renders five preset configurations (30 fps draft/standard/high at 2 workers, 30 fps standard at 4 workers, 60 fps standard at 4 workers), `--runs 1-20` (default 3), `--json`; the empirical way to get this machine's numbers.
- `hyperframes snapshot [dir]` - `--at`, `--frames`, `--range`, `--composition`, `--output`, `--end/--no-end`, `--keep`, `--zoom`, `--zoom-scale`, `--angle`, `--against`, `--review`, `--describe`, `--json`.
- `hyperframes doctor --json` - reports node/CPU/memory/disk, the frames cache location and free space, ffmpeg/ffprobe, Chrome, Docker, and an `ok` boolean; exits 0 whenever it ran, so CI gates on `.ok`.
- `@hyperframes/producer` API: `createRenderJob`, `executeRenderJob`, `RenderConfig`, `RenderJob`, `RenderOutcome`, `ProgressCallback`, `RenderPerfSummary`, plus lower-level `createCaptureSession`, `initializeSession`, `captureFrame`, `captureFrameToBuffer`, `getCompositionDuration`, `getCapturePerfSummary`.
- `@hyperframes/producer` distributed API: `createRenderRequest`, `parseRenderRequest`, `renderConfigFromRequest`, `serializeRenderRequest`, `renderSpan` / `spanFrameRange` (used by Lambda and Cloud Run).
- `@hyperframes/engine` config type `EngineConfig` (fps, quality, format, jpegQuality, concurrency, coresPerWorker, browserGpuMode, useDrawElement, enableDrawElementWorkerEncode, enablePageSideCompositing, streaming/chunked encode, timeouts, HDR, cache limits).

**Key Config Options**

- `fps` - default 30 (or the root `data-fps`); 1-240; rationals accepted.
- `quality` - `draft` (ultrafast, CRF 28) / `standard` (medium, CRF 18) / `high` (fast, CRF 15); CLI default `looks` = standard + CRF 16.
- `format` - `jpeg` (default, quality 80) or `png`; PNG is forced for alpha and for `--chroma-subsampling 444`.
- `concurrency` / `--workers` - `auto` by default, resolved from CPU count (see key features); hard ceiling 24.
- `coresPerWorker` - 2.5; `minParallelFrames` - 120; `largeRenderThreshold` - 1000 frames; minimum 30 frames per worker.
- `browserGpuMode` - raw default `software` (SwiftShader, 'always works; ~5-50x slower than GPU'); the CLI default is `auto`, which probes WebGL once (one extra Chrome launch, ~1-2 s, cached) and falls back to software.
- `forceScreenshot` - off; forces `Page.captureScreenshot` and skips BeginFrame.
- `staticFrameDedup` - on; reuses byte-identical frames instead of re-seeking and re-screenshotting (screenshot capture mode only; kill switch `HF_STATIC_DEDUP=false`).
- `useDrawElement` - on where it can engage (macOS/Windows + hardware GPU); kill switch `PRODUCER_EXPERIMENTAL_FAST_CAPTURE=false` or `--experimental-fast-capture=false`.
- `enableDrawElementWorkerEncode` - on; in-page OffscreenCanvas JPEG encode on a worker, documented as a 1.65-1.96x wall-time speedup, macOS hardware GPU only (`HF_DE_WORKER_ENCODE=false` to disable).
- `enablePageSideCompositing` - on; shader transitions blend in a page-side WebGL canvas instead of the Node-side layered blend, '~6x faster for SDR shader-transition renders'; mp4/hls/gif only.
- `enableStreamingEncode` - on; frames go straight to an FFmpeg stdin for single-worker renders instead of a disk directory. `chunkSizeFrames` - 360 when chunked encoding is on (`enableChunkedEncode` - off).
- `enableBrowserPool` - on, but disabled for every parallel worker (pages in one browser share six connections to the file server, and sharing also crashes BeginFrame).
- `lowMemoryMode` - auto when host RAM <= 8192 MB (or `PRODUCER_LOW_MEMORY_MODE` / `--low-memory-mode`): one worker, screenshot capture, no calibration browser, no WorkerBrowserPool.
- `frameDataUriCacheLimit` - 256 entries; `frameDataUriCacheBytesLimitMb` - 1500 MB, sized so a worker stays under a few GB even at 4K.
- `extractCacheMaxBytes` - 2 GiB default soft budget for the content-addressed source-frame cache, with an LRU sweep after extraction.
- Timeouts: `ffmpegEncodeTimeout` 600 s without a new frame, `ffmpegProcessTimeout` 300 s, `ffmpegStreamingTimeout` 600 s inactivity, `pageNavigationTimeout` 60 s, `protocolTimeout` 300 s, `playerReadyTimeout` 45 s, `renderReadyTimeout` 15 s.
- `--max-concurrent-renders` - 2 (1-10) for the producer server; locally, one render at a time per user account via a lock file (`--wait` default, `HYPERFRAMES_RENDER_LOCK=off` to disable).
- `--resolution` - deviceScaleFactor supersampling, not re-layout; `--start`/`--end` - MP4 + SDR + 24/25/30/60 fps only.

### Integration

**Pipeline Fit**

Partially replaces and mostly augments. Today `render_video.py` does both the cut assembly and the text/motion overlay in one FFmpeg pass at 60 fps. HyperFrames is the better tool for the overlay half (seekable per-frame animation, real HTML text) and a *worse* tool for the beat-grid cut half (it renders one composition end to end and its own seek clock, not an FFmpeg concat of measured beats). The low-risk shape is hybrid: keep beat-snapped cuts, punch-in and the -14 LUFS audio bed in FFmpeg, and render transparent overlay segments (1080x1920, 60 fps, `--format webm` or `png-sequence`) from HyperFrames, composited per cut. A full replacement (HyperFrames renders the whole 28 s reel) is possible and simpler to reason about, but throws away the measured beat grid, the per-cut sharpness gate and the WhatsApp size guard unless they are re-implemented inside the composition.

**Python Integration**

Three viable contracts. (1) CLI + subprocess: `subprocess.run(["npx","--yes","hyperframes","render",...])` with `--json` for machine-readable results and `_meta` (cli version, updateAvailable) in the payload; progress goes to stderr, so read stdout as JSON only. Exit codes matter (2 = usage/mistyped flag; non-zero with an error envelope otherwise). (2) Node bridge: a small `render.mjs` that imports `@hyperframes/producer` (`createRenderJob`/`executeRenderJob`) and emits newline-delimited JSON progress on stdout, called from Python with `subprocess.Popen` and parsed line by line - this is the only way to get per-frame progress and `RenderPerfSummary` as structured data. (3) File contract: Python generates the composition HTML + a variables JSON, invokes the CLI, then inspects the produced file with ffprobe (duration, fps, pix_fmt, alpha_mode) and with `snapshot` stills. Caveats: npx cold start and Chrome download on first run; each worker is a Chrome (~256 MB) so a 16 GB box should stay at 2-4 workers; a local render takes an exclusive per-account lock, so parallel SKU renders queue unless `HYPERFRAMES_RENDER_LOCK=off`; and on WSL2 Chrome usually cannot reach the GPU, so expect software WebGL.

**Data Injection**

Per-render data goes in as (a) `--variables '<json>'` / `--variables-file` / `--batch <rows.json>`, merged over the composition's `data-composition-variables` defaults and read inside with `window.__hyperframes.getVariables()`; (b) `--strict-variables` to fail instead of warn on undeclared or mistyped keys; (c) plain generation - text, prices and word timings written into the HTML before render, which is the only path for per-word caption timing arrays. `--batch` renders one output per variable row, which is the natural fit for a SKU catalogue.

**Footage And Asset Handling**

Existing MP4 footage is consumed as `<video class="clip" data-start data-duration data-track-index src>`, but not decoded during capture: the engine pre-extracts each source video's frames with FFmpeg and swaps in `<img>` elements, caching the result content-addressed (2 GiB default budget) keyed on path, mtime, size, mediaStart, duration, fps and format. Extraction format is `auto|jpg|png` (`--video-frame-format`), forcing PNG for UI recordings and for any alpha-capable source. Audio beds are ordinary `<audio data-start data-duration data-volume>` elements mixed to 48 kHz PCM before AAC/Opus. Fonts are local files or @font-face URLs localized to same-origin before capture; remote images/videos/audio are localized the same way. Implication for this pipeline: a 28 s 60 fps reel built from 16 source clips costs a one-time extraction pass per clip (cached across renders), and the cache directory needs real disk headroom.

**Migration Effort**

medium

**Integration Steps**

- Run `npx hyperframes doctor --json` on the WSL2 box and record the `ok` payload, the frames-cache free space and whether Chrome reports a hardware GPU.
- Prototype one SKU: generate a 1080x1920@60 composition from the existing `sku.json` fields (title, price, badges, footer) with no footage, and render it with `--quality draft`. Measure wall time and compare against the current 3 min render.
- Add the real footage as `<video>` clips with the same in/out points the edit plan already computes, and confirm extraction cost and cache behaviour (`--frames-cache-dir`) on a second render.
- Render a 3 s overlay as `--format webm` and verify alpha end to end (`ffprobe` shows `alpha_mode=1`, and an extracted frame over a checkerboard is transparent).
- Composite the overlay onto the existing beat-snapped cut with one FFmpeg filter graph and compare the result frame by frame against the current renderer's output at the same timestamps.
- Decide the split: overlays-only (recommended first) versus whole-reel rendering. Record the decision with measured numbers per render.
- Wrap the winner in Python (`subprocess` + `--json`, or a Node bridge for progress), add a timeout, parse the error envelope, and assert output duration/fps/size before the SKU is allowed to ship.
- Add a QA gate that reruns `snapshot --at` on the key beats and fails on a blank or clipped frame, mirroring the existing `qa_check.py --strict` contract.
- Pin the CLI version (`hyperframes@x.y.z`) in the pipeline so an upgrade cannot silently change encoder defaults (CRF, preset) between deliveries.

### Constraints

**Limitations**

- No temporal motion blur from the encoder: one instant is captured per frame. Any blur must be authored in the composition (the catalog's motion-blur component samples the timeline at sub-frame times and averages copies) and that component is the stated determinism exception.
- Default capture is JPEG at 4:2:0, so thin coloured text and saturated UI edges can fringe until you pay for `--chroma-subsampling 444` (about 2x render time for text/UI, 3.5x for footage) - and many players cannot decode 4:4:4.
- 60 fps doubles the frames the engine must seek, capture and encode; 4K superampling is roughly 4x slower per frame and 3-5x larger on disk. Neither is 'free detail' for content locked to a pixel grid.
- Renders are serialised per user account: a second local or `--docker` render waits for the first (`--wait` default) because side-by-side renders starve each other. `--workers` only sets parallelism *inside* one render.
- Source video is pre-extracted to image frames, so footage-heavy compositions pay a large fixed extraction cost and need real disk headroom; the extraction cache is the mitigation, not a fix for a cold first render.
- `--start`/`--end` is restricted to MP4, SDR, and integer 24/25/30/60 fps; it is not a general ranged render.
- `--resolution` refuses anything but MP4, forbids HDR, and requires an exact aspect-ratio match and integer upscale - a 1080x1920 composition cannot be rendered to a 1440x2560 preset.
- Alpha exists only in webm/mov/png-sequence, and only where the composition paints nothing; an opaque `html`/`body` background is encoded as visible pixels, so a full-bleed reel has nothing to gain from a transparent format.
- Local renders can differ by a pixel between machines (font versions, Chrome version). Only the Docker path pins Chromium, fonts and FFmpeg.
- The page-side WebGL compositing path is f32, not f64, so byte-equality baselines do not apply to it; its correctness pin is PSNR-based instead.
- One render at a time, plus a 45 s player-ready budget and a 60 s navigation budget, means very heavy compositions fail rather than wait unless the timeouts are raised.

**Known Issues**

- #4060 [Windows] disk-capture headroom gate: the pre-render estimate bills uncompressed RGBA (`frames x width x height x 4`) while frames are written as JPEG/PNG, so an ordinary ~44 s 1080p30 export was estimated at ~11 GB (actual footprint of a few hundred MB) and refused; 5 minutes of 1080p30 estimated ~60 GB. Measured 1080p JPEG captures land at ~150-400 KB per frame, roughly 1/20 to 1/30 of the estimate.
- #7370 each worker re-verifies static-frame dedup for the whole timeline, slowing parallel renders.
- #6096 no GPU video decode on Linux (NVDEC): extraction of 4K sources runs entirely on CPU.
- #6530 macOS XProtect can block `hyperframes render` mid-'Compiling composition'.
- Issue #1199 is the reason `--browser-timeout` exists: heavy compositions (many videos, fonts, asset requests) blow past the 60 s navigation budget on a cold cache.
- Sandboxed Linux Chrome launches fail on distributions that restrict unprivileged user namespaces (Ubuntu 23.10+); the render warns and continues without the sandbox unless `HYPERFRAMES_CHROME_SANDBOX=1`.
- Low-memory detection reads host RAM, not cgroup/container limits, so containerised callers (including `--docker`) must set `PRODUCER_LOW_MEMORY_MODE` explicitly.

**Performance And Cost**

Documented numbers, with conditions: workers each cost ~256 MB of RAM and `auto` picks `max(6, min(16, floor(cores/8)))`, so a 16-core box gets 6 and a 64-core box 8; `--experimental-fast-capture` is quoted at ~2x faster where it engages (macOS/Windows + hardware GPU) and its in-page encode worker at 1.65-1.96x wall time; page-side shader compositing is quoted at ~6x faster for SDR shader-transition renders; 4K is ~4x slower per frame with a 3-5x larger file; 4:4:4 costs ~2x (text/UI) to ~3.5x (footage) render time; software WebGL (SwiftShader) is described as 5-50x slower than GPU. The performance guide's own demo measures 300 frames at 1920x1080 at 25.0 s against 9.8 s (median of three runs), though the page does not say which stage was timed, so treat it as soft evidence. Disk: measured 1080p JPEG capture is ~150-400 KB per frame (issue #4060), which implies roughly 250-670 MB of frame data for a 28 s 60 fps reel - a derived figure, not a published one; the extraction cache defaults to a 2 GiB budget, the frame data-URI cache to 1500 MB / 256 entries. NOT MEASURED ANYWHERE IN THE DOCS: a render-time benchmark at 1080x1920@60 fps specifically. The only first-party way to get that number on this machine is `npx hyperframes benchmark` (which includes a 60 fps / standard / 4-worker configuration) or a timed draft render of the actual composition.

**Licensing**

Apache-2.0 for the engine, CLI, producer and all packages; no per-render fees, no commercial-use threshold, no watermark. Rendering is free locally and in your own Docker. Paid boundaries are around the hosted paths, not the renderer: AWS Lambda / Cloud Run rendering bills AWS/GCP compute for the fan-out, and HyperFrames Studio Pro bundles HeyGen's video/image/voice/music models. Rendered MP4s carry two unsigned container tags (`hyperframes_renderer`, `hyperframes_version`) and nothing else - no paths, usernames or project data - and they can be stripped with `ffmpeg -map_metadata -1 -c copy`; they are a diagnostic hint, not an authenticity or licensing control.

### Evidence

**Confidence**

0.82

**Sources**

- https://hyperframes.heygen.com/guides/rendering - format matrix, quality tiers (default `looks` = CRF 16), `--resolution` behaviour, 4:4:4 measurements (~2x text/UI, 3.5x footage, +16 dB text chroma PSNR, player support table), transparent video rules, HLS rules, render provenance tags, Docker vs local, the '4K ~4x slower per frame / 3-5x larger file' and '60 fps doubles frames' statements.
- https://hyperframes.heygen.com/guides/performance - the 300-frame 1920x1080 25.0 s vs 9.8 s demo (median of three), expensive-browser-work guidance, `--quality draft` review renders, VP9 `--vp9-cpu-used`.
- https://hyperframes.heygen.com/guides/4k-rendering - supersampling semantics, per-content sharpness table, and the full constraint list (aspect ratio, integer multiple, MP4 only, no HDR).
- https://hyperframes.heygen.com/concepts/determinism - the frame clock (`time = floor(frame)/fps`), BeginFrame atomic capture, 'worker count, chunk boundaries and seek order don't change pixels' with motion blur as the exception, and Docker as the cross-machine pin.
- https://hyperframes.heygen.com/packages/cli - the full render flag table with defaults, including `--workers` 1-24 at ~256 MB each, `--low-memory-mode` auto at 8 GB, `--page-side-compositing` ~6x, `--experimental-fast-capture` ~2x, `--max-concurrent-renders` 2, the per-account render lock, HLS/GIF/PNG-sequence semantics, `snapshot` and `benchmark`.
- Local clone of heygen-com/hyperframes @ main (2026-11-15): packages/engine/src/config.ts - `DEFAULT_CONFIG` (fps 30, quality standard, jpeg 80, concurrency auto, coresPerWorker 2.5, minParallelFrames 120, staticFrameDedup on, useDrawElement on, pageSideCompositing on, streaming encode on, chunkSizeFrames 360, all timeouts, cache budgets, the drawElement 1.65-1.96x and SwiftShader 5-50x comments).
- Local clone: packages/engine/src/services/chunkEncoder.ts - `ENCODER_PRESETS` (draft ultrafast/28, standard medium/18, high fast/15), libx264/libx265 argument construction, `-bf 0` for h264, x264 aq-mode/colour params, `-tag:v hvc1` for HEVC, VP9 `yuva420p` + `alpha_mode=1`, ProRes 4444 `prores_ks`, AAC 192 kbps / libopus 128 kbps muxing.
- Local clone: packages/engine/src/services/streamingEncoder.ts - the full per-encoder argument paths including NVENC/QSV/VAAPI/AMF/VideoToolbox rate control and the BT.709 tagging branch.
- Local clone: packages/engine/src/services/parallelCoordinator.ts - `ABSOLUTE_MAX_WORKERS = 24`, `defaultSafeMaxWorkers()` = `max(6, min(16, floor(usableCpuCount()/8)))`, `MIN_FRAMES_PER_WORKER = 30`, one browser per parallel worker, and the measured Apple-GPU disk-capture heap notes.
- Local clone: packages/engine/src/services/systemMemory.ts - `LOW_MEMORY_TOTAL_MB_THRESHOLD = 8192`.
- Local clone: packages/engine/src/services/videoFrameExtractor.ts - 'Pre-extracts video frames using FFmpeg for frame-accurate rendering. Videos are replaced with <img> elements during capture.'
- Local clone: packages/cli/src/utils/dockerRunArgs.ts and packages/cli/src/commands/render.ts - `hyperframes-renderer:<version>` (+`-arm64`), `--shm-size=2g`, read-only `/project`, writable `/output`, `--gpus all` with `--gpu`, and the platform resolution rules.
- Local clone: packages/cli/src/commands/render.ts + render/plan.ts - `QUALITY_ALIASES` (`looks` = standard + CRF 16, `delivery` = high), and every render flag with its help text including `--start`/`--end` restrictions and `--resolution` presets.
- https://github.com/heygen-com/hyperframes/issues/4060 - measured 1080p JPEG capture size (~150-400 KB/frame) and the RGBA disk-estimate failure mode.
- GitHub issue search (2026-11-15) - #7370 static-frame dedup re-verification in parallel renders, #6096 no NVDEC on Linux, #6530 macOS XProtect blocking a render.
- https://hyperframes.heygen.com/help - the WSL2 section: headless Chrome may not reach the GPU, software WebGL fallback, the `render` group fix and the 'render from Windows instead' workaround.

**Open Questions**

- Actual wall-clock render time at 1080x1920@60 fps for a 28 s composition on this WSL2 machine, both with and without footage - unmeasured in every first-party doc and needs `hyperframes benchmark` plus a timed draft render.
- How much of the render time is extraction versus capture versus encode for a 16-clip, 28 s reel; the docs give no per-stage breakdown.
- Whether software WebGL (no GPU under WSL2) is fast enough for shader-transition overlays at 60 fps in this pipeline, or whether those must be rendered once and reused.
- Whether a 60 fps 1080x1920 MP4 at delivery quality with `--chroma-subsampling 444` still fits the WhatsApp ~16 MB guardrail, which would need an FFmpeg re-encode anyway.
- Whether the PNG-sequence or WebM-alpha overlay path composites more cheaply in the existing FFmpeg graph for 28 s of overlay.
- Peak RAM for 4 workers at 60 fps on a 16 GB box, which the docs bound only by the ~256 MB-per-Chrome rule and the 8 GB low-memory threshold.

### Other Info

**Requirements**

- Node.js 22+ (repo badge: node >= 22) and npm/npx.
- FFmpeg and FFprobe on PATH. No explicit minimum version is documented; `doctor` reports the installed one and its sample output shows FFmpeg 7.x. [uncertain]
- A Chrome/Chromium build: the CLI downloads a pinned `chrome-headless-shell` into `~/.cache/hyperframes/chrome` per platform (darwin arm64/x64, linux x64, win32 ia32/x64).
- Disk: a render work directory for captured frames plus a content-addressed extraction cache (2 GiB default budget) under `<tmpdir>/hyperframes-extract-cache-<uid>`; `doctor` fails the frames-cache row under 2 GB free.
- RAM: each Chrome worker is roughly 256 MB; <= 8 GB total RAM flips the low-memory profile automatically (1 worker, screenshot capture).
- Docker (optional): `--docker` needs a working daemon; it builds `hyperframes-renderer:<version>` per architecture and runs with `--shm-size=2g`.
- Linux sandbox: Chrome keeps its sandbox for non-root users outside containers; it runs without one as root, in Docker/Podman/Kubernetes, on Lambda and Cloud Run, and where unprivileged user namespaces are restricted (Ubuntu 23.10+). `HYPERFRAMES_CHROME_SANDBOX=0|1` forces the choice.
- WSL2: headless Chrome often cannot reach the GPU, so WebGL falls back to SwiftShader ('several times slower on a long or effect-heavy video'); the render log says so explicitly.

---

<a id="text-typography-and-kinetic-captions"></a>
## Text, Typography and Kinetic Captions

### Basic Info

**Title**

Text, Typography and Kinetic Captions

**Domain**

Motion graphics / typography and captions

**One Liner**

HyperFrames treats text as styled DOM plus a paused timeline, with bundled fonts, canvas-based fitting and word-level caption components instead of a subtitle renderer.

**Official Sources**

- https://hyperframes.heygen.com/catalog/components/caption-highlight
- https://hyperframes.heygen.com/catalog/components/caption-kinetic-slam
- https://hyperframes.heygen.com/prompting/captions-catalog
- https://hyperframes.heygen.com/guides/captions-and-recuts
- https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes/references/captions.md
- https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes/references/format.md
- https://hyperframes.heygen.com/guides/gsap-animation
- https://www.npmjs.com/package/@hyperframes/core

**Maturity**

production

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

Broadcast-grade on-screen type inside a deterministic render: per-word and per-character kinetic captions driven by real word timings, automatic single-line fitting measured off a canvas, 18 bundled font families with no network at capture time, and a catalog of ~60 text blocks and components (highlight sweeps, karaoke pills, gradient fills, neon, glitch, scramble, variable-axis morphs, texture masks) that are editable source, not opaque presets.

**Key Features**

- Caption model: words come in as [{id, text, start, end}], are grouped 2-3 words for high energy, 3-5 conversational, 4-6 calm, and each group is one absolutely positioned element with one entrance, per-word highlights and a hard kill.
- Word timings are produced by `npx hyperframes transcribe <file> --json` (Whisper `small.en` by default) into assets/transcript.json, with `--to srt` and `--to vtt` sidecars; an existing corrected transcript can be re-applied with `npx hyperframes transcribe transcript.json --dir <project>`.
- Auto-fit: `window.__hyperframes.fitTextFontSize(text, {fontFamily, fontWeight, maxWidth, baseFontSize, minFontSize, step})` returns {fontSize, fits}; defaults are base 78 px, min 42 px, step 2, weight 900, family Outfit, and it shrinks until the string fits on ONE line.
- Measurement without reflow: `window.__hyperframes.pretext` exposes prepare / layout / prepareWithSegments / measureLineStats / measureNaturalWidth from @chenglou/pretext, all canvas-based, so line counts and natural widths can be read at any frame without triggering layout. clearCache and setLocale are deliberately withheld because they mutate process-global state.
- Fonts: 18 families are bundled and resolve by name (Inter, Montserrat, Outfit, Nunito, Oswald, League Gothic, Archivo Black, Space Mono, IBM Plex Mono, JetBrains Mono, EB Garamond, Playfair Display, Source Code Pro, Noto Sans JP, Roboto, Open Sans, Lato, Poppins). Anything else must ship an @font-face or lint fails `font_family_without_font_face`.
- Font determinism: at compile time the HTML is rewritten so every resolvable family is inlined as woff2 data URIs (injectDeterministicFontFaces), with 2 fetch attempts, an 8 s per-attempt timeout, a 20 s total budget and a 250 ms jittered backoff, cached under ~/.cache/hyperframes/fonts. Capture therefore never waits on Google Fonts and cannot flash a fallback.
- Per-character splitting in the catalog is hand-written vanilla JS (wrap each code point in an inline-block span), not GSAP SplitText; this is deliberate and dependency-free, and it also means the split is by code point, not grapheme cluster.
- Per-word reveal recipes measured from the official films: per word blur(6px)->0 with opacity in 0.13 s, 0.067 s apart, no translate; per word grey-to-ink in 0.07-0.1 s; whole line blur 8 px->0 in 0.13-0.2 s; per letter for a name 0.1 s apart at opacity 0.3->1.
- Style primitives actually used by the catalog: background sweep behind a word (an inner span with transform: scaleX(0->1), transform-origin 0 50%), karaoke pill (per-word background + colour), gradient fill (`background-clip: text` on the inner text span), stroke (`-webkit-text-stroke: 3px` plus `paint-order: stroke fill`), neon glow (layered text-shadow), RGB glitch (three offset copies + clip-path), scramble (fixed glyph rows), texture mask (background-image + background-clip: text), parallax layers (z-index split with a matte), variable-axis type (`font-variation-settings` on wght/wdth axes).
- Layout safety: `npx hyperframes check` audits text against platform safe areas (`--safe-zone tiktok|reels|shorts|standard|off`, all three social zones by default on a 9:16 canvas) and against a reserved caption band (`--caption-zone "x0=0;y0=.82;x1=1;y1=1"` — an explicit band, the docs example being the bottom 18%), with per-element opt-outs `data-layout-allow-safe-zone`, `data-layout-allow-caption-zone`, `data-layout-bleed`, `data-layout-allow-overlap`.
- Text-behind-subject compositions stack video, captions, then a background-removed cutout by z-index; `check` treats a <video> as opaque, so the cutout needs `data-layout-allow-occlusion` and the result is judged from snapshots.
- Caption identity is scoped by `data-track-kind="captions"` on the host element, so every caption group lands on one captions track in the browser editor instead of scattering across lanes.

**Technical Mechanism**

Text is ordinary DOM. During authoring (or a script step) the words are turned into spans; each group is a div with opacity 0 and visibility hidden; a single paused GSAP timeline sets visibility, tweens opacity/transform per group and per word, and hard-kills the group at its end. Chrome rasterizes the type at the composition's pixel size (1080x1920 by default, optionally supersampled through deviceScaleFactor/--scale, which forces the screenshot capture path because BeginFrame ignores DPR). Fonts are inlined as woff2 data URIs by the compiler, so the font binary is part of the composition HTML and the shasum of the render inputs; `document.fonts.load(font, text)` is the supported way to force a face used only by script-created text, and `document.fonts.ready` alone does not request an unused face. Fitting and line counting go through canvas measurement (pretext) rather than getBoundingClientRect, because reading layout mid-timeline both costs a reflow per frame and makes the value depend on when it was read.

**Text And Motion Capabilities**

Typography is fully CSS-controlled: any web font (variable axes included), any OpenType feature Chrome supports, stroke, gradient fill, texture fill, shadow stacks, blend modes, clip-path and masks. Temporal control is one frame (16.67 ms at 60 fps), which is finer than any caption cue needs; per-word highlights resolve exactly on the word's start time because each tween is placed at absolute seconds. Motion detail that is documented as good practice: stagger letters one frame apart, words 60-120 ms, list items 40-80 ms; title size 4-9% of frame height; a readable line holds about 0.5 s plus 0.3 s per word; exits are 40-50% shorter than entrances. Precise limits: fitTextFontSize optimises for a single line (it reports fits:false at minFontSize rather than wrapping), splitting is per code point unless you write a grapheme-aware splitter, and there is no built-in reading-speed (CPS) gate — that check has to live in the pipeline that knows the delivery platform.

**Output Formats**

- Any render output the CLI supports: MP4 H.264 (default), MOV, WebM VP9 with alpha (for text overlays composited elsewhere), GIF, PNG sequence.
- Transparent PNG stills per caption card via `hyperframes snapshot`, useful as a contact sheet / QA artifact.
- assets/transcript.json plus .srt and .vtt sidecars from `hyperframes transcribe --to srt|vtt`, reusable by other tools.
- Snapshot sheets (`--against ref.mp4`) for side-by-side typography comparison against an approved reference.

### Usage & API

**Setup And Install**

Node 22+ and FFmpeg are the only prerequisites; fonts and components come from the project, not from a service.

```bash
npx hyperframes init my-reel
cd my-reel
npx hyperframes add caption-highlight     # writes compositions/components/caption-highlight.html
npx hyperframes catalog --tag caption-style --json   # list every caption component
npx hyperframes transcribe assets/voice.mp3 --model small --language es --json
npx hyperframes check --safe-zone reels --caption-zone "x0=0;y0=.82;x1=1;y1=1;severity=error"
npx hyperframes render --fps 60 --output out/reel.mp4
```

For a custom font, drop the file in assets/fonts and declare it in the same file that uses it:

```css
@font-face {
  font-family: "Inter";
  src: url("assets/fonts/Inter-Black.woff2") format("woff2");
  font-weight: 900;
  font-display: block;   /* never flash a fallback during capture */
}
```

**Minimal Code Example**

A complete 9:16 caption card with word-level highlight, auto-fit and a hard kill:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      @font-face { font-family: "Inter"; src: url("assets/fonts/Inter-Black.woff2") format("woff2");
                   font-weight: 900; font-display: block; }
      html, body { margin: 0; width: 1080px; height: 1920px; background: #06080c; overflow: hidden; }
      #root { position: relative; width: 1080px; height: 1920px; }
      .cap-group { position: absolute; left: 0; right: 0; top: 1445px; padding: 0 90px;
                   text-align: center; opacity: 0; visibility: hidden; }
      .cap-line { font-family: Inter, sans-serif; font-weight: 900; text-transform: uppercase;
                  color: #fff; line-height: 1.02; letter-spacing: -0.01em; }
      .w { display: inline-block; position: relative; padding: 0 .05em; margin: 0 .08em; }
      .w .bg { position: absolute; inset: .02em -.06em .04em; background: #059669;
               border-radius: .08em; transform: scaleX(0); transform-origin: 0 50%; z-index: -1; }
      .w .t { position: relative; }
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="captions" data-start="0" data-duration="2.4"
         data-fps="60" data-width="1080" data-height="1920">
      <div id="cap-host" data-track-kind="captions"></div>
    </div>

    <script type="application/json" id="words">
      [{"text":"IPHONE","start":0.20,"end":0.62},{"text":"14","start":0.62,"end":0.90},
       {"text":"FOR","start":0.90,"end":1.05},{"text":"330","start":1.05,"end":1.55}]
    </script>

    <script>
      (async function () {
        const words = JSON.parse(document.getElementById('words').textContent);
        const host = document.getElementById('cap-host');

        // 1. load the face before measuring, or the fit is computed on fallback metrics
        await document.fonts.load('900 78px "Inter"', words.map(w => w.text).join(' '));
        const tl = gsap.timeline({ paused: true });

        // 2. group 2-5 words, breaking on a sentence end or a pause >= 150 ms
        const groups = [];
        for (const w of words) {
          const g = groups.at(-1);
          const pause = g ? w.start - g.at(-1).end : 0;
          if (!g || g.length >= 3 || pause >= 0.15 || /[.!?]$/.test(g.at(-1).text)) groups.push([w]);
          else g.push(w);
        }

        groups.forEach(function (g, gi) {
          const text = g.map(w => w.text).join(' ');
          // 3. fit to ONE line inside the safe width; portrait uses maxWidth ~900 of 1080
          const fit = window.__hyperframes.fitTextFontSize(text, {
            maxWidth: 900, baseFontSize: 78, minFontSize: 42, fontWeight: 900,
            fontFamily: 'Inter', step: 2,
          });
          if (!fit.fits) console.warn('caption needs a shorter group:', text);

          const group = document.createElement('div');
          group.className = 'cap-group';
          group.id = 'grp-' + gi;
          const line = document.createElement('div');
          line.className = 'cap-line';
          line.style.fontSize = fit.fontSize + 'px';

          g.forEach(function (w) {
            const span = document.createElement('span');
            span.className = 'w';
            span.innerHTML = '<span class="bg"></span><span class="t"></span>';
            span.querySelector('.t').textContent = w.text;
            line.appendChild(span);

            // 4. word entrance + highlight, placed at absolute seconds
            tl.fromTo(span, { opacity: 0, y: 18 },
              { opacity: 1, y: 0, duration: 0.13, ease: 'power3.out' }, w.start);
            tl.to(span.querySelector('.bg'),
              { scaleX: 1, duration: 0.12, ease: 'power2.out' }, w.start);
            tl.to(span.querySelector('.bg'),
              { scaleX: 0, transformOrigin: '100% 50%', duration: 0.10, ease: 'power2.in' }, w.end);
          });
          host.appendChild(group);

          const start = Math.max(0.1, g[0].start);   // never at exactly 0 s
          const end = g.at(-1).end + 0.5;
          tl.set(group, { visibility: 'visible' }, start);
          tl.fromTo(group, { opacity: 0 }, { opacity: 1, duration: 0.10 }, start);
          // 5. hard kill, or a stale group stays on screen
          tl.to(group, { opacity: 0, duration: 0.12, ease: 'power2.in' }, end - 0.12);
          tl.set(group, { opacity: 0, visibility: 'hidden' }, end);
        });

        // 6. register LAST, after every tween exists
        window.__timelines = window.__timelines || {};
        window.__timelines['captions'] = tl;
      })();
    </script>
  </body>
</html>
```

Measuring instead of guessing a wrap:

```js
const p = window.__hyperframes.pretext;
const prepared = p.prepare('iPhone 14 for 330 USDT', '900 78px Inter');
p.layout(prepared, 900, 78 * 1.2).lineCount;      // 1 = fits on one line
p.measureNaturalWidth(prepared);                   // unwrapped width in px
p.measureLineStats(prepared, 900, 78 * 1.2);       // { lineCount, maxLineWidth }
```

**Cli Or Api Surface**

- window.__hyperframes.fitTextFontSize(text, options) -> { fontSize, fits }  — single-line fit; options maxWidth, baseFontSize, minFontSize, fontWeight, fontFamily, step.
- window.__hyperframes.pretext.{prepare, prepareWithSegments, layout, measureLineStats, measureNaturalWidth}  — canvas text metrics without reflow.
- window.__hyperframes.getVariables()  — read data-composition-variables values once at init for copy/colour logic.
- npx hyperframes transcribe <file> [--model small.en|medium.en|small] [--language es] [--json] [--to srt|vtt]  — word-level timings into assets/transcript.json.
- npx hyperframes transcribe <corrected.json> --dir <project>  — replaces transcript text while keeping the caption code.
- npx hyperframes add <caption-component>  /  npx hyperframes catalog --tag caption-style --json  /  --query "<style>" --tag motion-primitive.
- npx hyperframes check --safe-zone tiktok|reels|shorts|standard|off  — text-outside-safe-area findings.
- npx hyperframes check --caption-zone "x0=0;y0=.82;x1=1;y1=1;severity=error;seek=.5,1"  — reserved caption band audit (fractions of the canvas).
- npx hyperframes keyframes --json --selector '#title'  — lists the actual per-word keyframes, eases and times for audit.
- npx hyperframes snapshot --at 0.4,1.2 --zoom '#grp-0'  — crop a caption card to check clipping, stroke weight and alignment.
- data-track-kind="captions" on a scene host  — groups caption clips on one editor track.
- data-layout-allow-caption-zone / data-layout-allow-safe-zone / data-layout-bleed / data-layout-allow-overlap / data-layout-allow-occlusion  — narrow, per-element audit opt-outs.
- data-var-text="id" and CSS var(--id)  — bind copy and colour from composition variables without script.

**Key Config Options**

- fitTextFontSize.maxWidth: 1600 default in code, but 900 is the documented portrait value of a 1080-wide canvas; divide it by the emphasis scale when one word pops.
- fitTextFontSize.baseFontSize 78 / minFontSize 42 / step 2 — the search walks down from 78 in 2 px steps and stops at 42 with fits:false.
- fitTextFontSize.lineHeightRatio is fixed at 1.2 inside the helper; a tighter leading must be set in CSS, not in the fit call.
- @font-face font-display: block — avoids a fallback flash; the compiler inlines the binary anyway, so no swap ever occurs.
- Caption group size: 2-3 words (high energy), 3-5 (conversational), 4-6 (calm); break at a sentence end or a pause >= 150 ms.
- Caption start: 0.1-0.3 s in, never exactly 0 s (a caption at 0 s looks pre-existing); group end = last word end + ~0.5 s.
- Portrait caption anchor: the HyperFrames guide assumes 600-700 px from the bottom of 1920, while this pipeline's caption_y is a TOP anchor at 1445 px (reels_universal) and 1485 px (status_story) — about 425/385 px from the bottom for 46 px type. Position the group with top: 1445px to reproduce the existing layout, and treat the guide's numbers as a different style.
- Safe zones used by `check`: reels = y 14%-65%, x 6%-94%; shorts = y 288-1248 px, x 48-888 px of 1080x1920; tiktok = y 160-840 px of 1280 with x 80-640; standard = 80% title-safe box.
- Reserved caption band (`--caption-zone`): explicit fractions x0,y0,x1,y1 — the docs example is y0=0.82 to y1=1.0 (bottom 18%); severity defaults to warning and seek to 1.
- data-font-family — an HTML attribute surface the font planner also reads, useful when copy comes from a CMS field.
- Motion values that read as designed: per-word stagger 0.067 s (blur reveal), per-letter stagger 0.1 s, word highlight 0.12-0.15 s, exit 40-50% shorter than entrance.

### Integration

**Pipeline Fit**

Replaces the caption burn-in stage (drawtext/ASS) and can also replace the price pill, badges and CTA card, while the beat-synced cut stays in FFmpeg. The current stack already computes the two things captions need — word timings from align_words.py and a measured beat grid from beat_grid.py — and already enforces a readability budget (quality.caption_warn_cps = 20, caption_fail_cps = 25) and a safe zone (caption_y = 1445 for reels_universal, 1485 for status_story, 46 px type). HyperFrames consumes exactly that data and gives back real kinetic typography (per-word highlight, karaoke pill, emphasis scaling) that FFmpeg's drawtext cannot express, plus a deterministic frame-by-frame render so the same sku.json always produces the same pixels. The clean split is: FFmpeg owns picture and sound, HyperFrames owns type and graphics, and the pipeline composites the overlay pass.

**Python Integration**

The contract is files plus one subprocess call.

```python
import json, pathlib, re, subprocess

def write_transcript(words, project: pathlib.Path):
    # words: [{"id", "text", "start", "end"}] from align_words.py
    payload = [{"id": w["id"], "text": w["text"],
                "start": round(w["start"], 3), "end": round(w["end"], 3)} for w in words]
    (project / "assets").mkdir(parents=True, exist_ok=True)
    (project / "assets" / "transcript.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")

def cps_gate(words, warn=20, fail=25):
    bad = []
    for i in range(0, len(words), 3):
        g = words[i:i+3]
        span = max(0.001, g[-1]["end"] - g[0]["start"])
        cps = sum(len(w["text"]) for w in g) / span
        if cps > fail:
            bad.append((g[0]["start"], round(cps, 1)))
    return bad   # non-empty -> shorten the script, do not ship

def render_captions(project: pathlib.Path, out: pathlib.Path):
    cmd = ["npx", "--yes", "hyperframes@0.9.54", "render",
           "--format", "webm", "--fps", "60", "--output", str(out)]
    p = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=1200)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-4000:])
    return out
```

The composition reads assets/transcript.json with a fetch (local file, resolved through the project server) or, simpler and safer, gets the JSON inlined into a <script type="application/json"> island by Jinja. Escape it with `json.dumps(..., ensure_ascii=False).replace('</', '<\\/')` so a product name containing a quote or a closing tag cannot break the page. Caveats: the Node process needs a writable HOME (font cache), the first render is slower because fonts are fetched and inlined once, and `--fps` must equal the composition's data-fps or the seek grid and the encode disagree.

**Data Injection**

Copy and colour go through composition variables; timings go through a JSON island. Declare <html data-composition-variables='[{"id":"price","type":"string","default":"$330"}]'>, bind with data-var-text="price" or CSS var(--price), and set per-mount values with data-variable-values='{...}'. For the timeline, write the words array into <script type="application/json" id="words"> at build time and parse it in the composition — that keeps the GSAP script static and reviewable while the data varies per SKU. The three values that must agree across systems are: the composition's data-duration (28.0 s), data-fps (60, matching the render flag), and the last word's end time (the caption hard kill must land before the root duration).

**Footage And Asset Handling**

Product footage is not touched: the overlay render carries only type and graphics on a transparent background, and FFmpeg composites it over the cut picture, so the existing beat-synced edit, the −14 LUFS mix and the WhatsApp guardrail stay exactly as they are. Fonts are the one asset that must move into the HyperFrames project: either a bundled family (no file needed) or a woff2 in assets/fonts with its @font-face in the same file. Voiceover is used only to derive timings (transcribe or align_words.py); the audio itself stays in the FFmpeg mix, so there is no double audio path. Images, badges and SVG marks are local project assets and are inlined or served by the project's own file server — remote URLs are downloaded first, never fetched during capture.

**Migration Effort**

medium

**Integration Steps**

- Pick one SKU and build a single caption-only composition: 1080x1920, data-fps 60, data-duration equal to the voiceover, one caption host with data-track-kind="captions".
- Generate assets/transcript.json from the existing align_words.py output (id, text, start, end) and confirm the words line up with the voice by rendering snapshots at three timestamps.
- Port the current caption style: 46 px equivalent, uppercase, emerald #059669 highlight, anchored at the project's caption_y 1445 as a top offset rather than the guide's 600-700 px-from-bottom default; verify with `check --safe-zone reels --caption-zone`.
- Add the CPS gate in Python before the render (reuse quality.caption_fail_cps = 25) so a too-dense group fails in seconds instead of after a full render.
- Render the caption pass as transparent WebM, composite in FFmpeg over the existing cut, and diff against the current burn-in frames to confirm position, weight and highlight timing.
- Extend the same composition to the price pill, badges and CTA once captions are stable — they share the same timeline and font stack.
- Add a determinism check to CI: two renders of the same project must produce identical frame hashes at 0.0/14.0/27.9 s.

### Constraints

**Known Issues**

- Lint `font_family_without_font_face` fires for any family that is neither bundled nor declared, and `font_face_local_only` warns when an @font-face uses local() only — that font will be missing in a cloud render.
- Catalog caption components ship as fixed demos: an 8 s timeline, 1920x1080 sizing and a placeholder word list, so they must be re-authored for 1080x1920 and the real narration.
- The caption-highlight demo links Google Fonts over the network; in a capture that link is a FOIT/latency risk unless the compiler inlines the family (it does for the 18 bundled ones).
- Groups hidden with `visibility: hidden` still occupy space in a flex column, so the next group lands lower; position groups absolutely.
- In a stacked (accumulating) caption block, groups must share one exit time or the stack reflows mid-sentence.
- Fading both the group and its words multiplies the opacities and reads as a pop; fade one level only.
- The documented `--caption-zone` example covers only the bottom 18% (y0=0.82) while this pipeline's caption row starts at 1445/1920 = 75.3% from the top, so auditing with that band leaves the real caption row unguarded; pass an explicit y0 (for example 0.68) for this layout.

### Evidence

**Confidence**

0.83

**Sources**

- url: https://hyperframes.heygen.com/catalog/components/caption-highlight | note: Full editable source of a real caption component: word/group data model, canvas measureText fit (80 px base, 1620 px max width), scaleX background sweep, brightness pop, visibility sets.
- url: https://hyperframes.heygen.com/catalog/components/bottom-up-letters | note: The catalog's character splitter: Array.from(text) per code point, inline-block spans, CSS custom properties driven by composition variables, GSAP stagger recipe.
- url: https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes/references/captions.md | note: The caption contract: word timing shape, transcribe models, group sizes, hard-kill pattern, fitTextFontSize usage and portrait maxWidth, portrait/landscape baselines, blend-mode thresholds, text-behind-speaker coverage rules.
- url: https://github.com/heygen-com/hyperframes/blob/main/skills/hyperframes/references/format.md | note: Bundled font list, Google Fonts fallback fetch and cache, @font-face lint rules, font_face_local_only warning, data-track-kind, layout audit opt-outs.
- url: https://hyperframes.heygen.com/prompting/captions-catalog | note: Taxonomy of 16 caption components plus text-effect components, tone mapping, and the explicit failure modes (stacking effects, mixing styles, caption names that do not exist).
- url: https://hyperframes.heygen.com/guides/captions-and-recuts | note: Rail vs embed model, cut-before-caption rule, per-word emphasis via inline `emphasis`, and the transcribe-a-corrected-transcript workflow.
- url: https://hyperframes.heygen.com/guides/gsap-animation | note: Load-fonts-before-measuring-text pattern with document.fonts.load(font, text), and why registering a timeline before an async build renders blank.
- url: https://www.npmjs.com/package/@hyperframes/core | note: Read dist/text/* (fitTextFontSize defaults, pretext surface and its deliberate omissions) and dist/fonts/deterministicFonts.js (18 canonical families, Fontsource packages, retry policy, data-URI inlining).
- url: https://hyperframes.heygen.com/reference/html-schema | note: data-composition-variables / data-var-text binding contract and the root duration rules that govern caption length.
- url: https://gsap.com/docs/v3/Plugins/SplitText | note: SplitText capabilities and limits (aria handling, autoSplit, mask, no SVG <text>, avoid text-wrap: balance) used to judge it as an alternative splitter.

**Open Questions**

- Which exact font package supplies colour emoji in the render image, and whether `check`'s contrast pass accounts for emoji glyphs.
- Whether an RTL/Arabic caption is viable at all without writing a custom splitter and group order — no code path in the catalog or core handles bidi.
- The real CPS ceiling for 46 px uppercase Inter on a 1080-wide WhatsApp Status view; the project's 25 CPS gate is a design choice, not a measured legibility limit.
- Whether SplitText's autoSplit can be used safely inside a seeked timeline (a re-split would change the DOM the timeline targets).
- Measured cost of neon glow / texture-mask captions at 60 fps versus plain type, to decide how often those styles can ship in a 28 s reel.
- Whether the compiler inlines a variable font's full axis range or pins the used instances, which decides if wght morphing survives the compile step.

### Other Info

**Requirements**

- Node.js 22+ and FFmpeg for the CLI; no Python package is required.
- A woff2 file per non-bundled family, declared with @font-face in the file that uses it, or lint fails before the render.
- Chrome/Chromium with the emoji font installed if emoji are used — a bare headless container may render tofu instead of colour emoji. [uncertain] exact font package required.
- Composition root must declare data-width, data-height, data-fps and a data-duration that covers the last caption's hard kill.

**Limitations**

- fitTextFontSize optimises a single line: it returns fits:false at minFontSize rather than wrapping, so multi-line captions must be authored as explicit lines (or measured with pretext.measureLineStats).
- pretext measures with a canvas, so it only works in the browser (prepare needs a real font + canvas); it cannot be used in Node to pre-compute fits without a browser.
- The catalog's character splitter uses Array.from(text), i.e. code points: emoji ZWJ sequences, flags, skin-tone modifiers and combining accents split into fragments. GSAP SplitText (free since 3.13, with aria-label/aria-hidden and grapheme-aware handling) is the safer splitter when emoji or Devanagari/Arabic text is in scope.
- GSAP SplitText is not used anywhere in the HyperFrames runtime or catalog, so its autoSplit/onSplit re-splitting behaviour is unproven inside a seeked timeline; re-splitting mid-render would invalidate the timeline's targets.
- Bundled files carry Latin cuts at 400/700/900 only; other weights and scripts (including CJK through Noto Sans JP) are fetched from Google Fonts at compile time and inlined as data URIs, so a full CJK face inflates the composition HTML substantially. `font-feature-settings` is fine, but `text-wrap: balance` and `hyphens` interact badly with per-word splitting (`text-wrap: balance` is explicitly called out as breaking splits).
- RTL/bidi is not handled anywhere in the caption tooling: groups are ordered left-to-right by array position, `fitTextFontSize` measures a single string, and there is no `dir` awareness in the catalog components. Arabic/Hebrew captions need custom work. [uncertain]
- Variable-font animation only works with a font file that actually carries the axis; the bundled families ship static 400/700/900 cuts, so `wght`/`wdth` morphing needs a variable file added to the project.
- Text on a transparent background (needed for overlay compositing) is antialiased in grayscale, not with LCD subpixel AA, because subpixel AA requires an opaque backdrop; fine strokes look thinner than in a browser over white.
- `background-clip: text` and gradient fills must live on the inner text span, never on the transformed container, or the fill and the transform fight and the gradient clips to the wrong box.
- `-webkit-text-stroke` is non-standard (Chrome-only, which is acceptable here) and always draws centred on the glyph outline, so a thick stroke eats the letterform; pair it with `paint-order: stroke fill`.
- Text shadows, neon glow stacks and blur filters are per-frame raster work: a 40 px glow on 6 words costs more than the type itself, and several overlapping backdrop-filter blurs can dominate render time.
- There is no built-in reading-speed (CPS) or minimum-hold lint. HyperFrames checks geometry (safe zones, caption band, overflow, overlap) but not how fast the words go by; the pipeline must keep its own caption_audit gate.
- A caption that starts at exactly 0 s is flagged in the guidance as looking pre-existing, and a group without an explicit hard kill stays visible for the rest of the composition.
- `check` treats any <video> as opaque across its box, so caption-behind-subject compositions report text_occluded until the cutout is marked data-layout-allow-occlusion and judged by snapshot.
- Burned-in subtitles already present in source footage cannot be detected or removed by the caption layer; they must be found by a 1 fps contact-sheet scan first.

**Performance And Cost**

Type itself is cheap: a caption group is a handful of spans with transform/opacity tweens, and text rasterization on a 1080x1920 canvas is a few milliseconds. The expensive text-adjacent work is filters (neon glow, blur, texture fills), background removal for text-behind-subject (documented at about 2 fps at 1080p on CPU with a 168 MB first-run model download), and font plumbing (one compile-time fetch per non-bundled family, cached in ~/.cache/hyperframes). [uncertain] Per-frame milliseconds for a 46 px caption layer on this machine are unmeasured; the cost that matters in practice is render wall time, which is dominated by seek + capture, not by the text.

**Licensing**

HyperFrames and its catalog are Apache-2.0, so the caption components are editable source you can ship commercially. Bundled fonts are Fontsource packages under the SIL Open Font License, which permits embedding and commercial use. GSAP (if SplitText or CustomEase is used) is free for commercial use under Webflow's standard no-charge license since 3.13, with the sole restriction of not building a competing no-code animation builder. [uncertain] Individual catalog pages state no per-block license, so attribution requirements for a block copied into a client template are unconfirmed.

---

<a id="vertical-9x16-reels-workflow"></a>
## Vertical_9x16_Reels_Workflow

### Basic Info

**Title**

Vertical_9x16_Reels_Workflow

**Domain**

Use case — vertical short-form video (Reels, Stories, WhatsApp Status)

**One Liner**

A 1080x1920@60 HyperFrames composition renders in 1m48s, but HyperFrames' Reels safe area (y 269–1248) rejects the current Fenixsoft header and price stack.

**Official Sources**

- https://hyperframes.heygen.com/guides/rendering.md
- https://hyperframes.heygen.com/concepts/compositions (docs/compositions.md)
- https://hyperframes.heygen.com/reference/data-attributes
- https://hyperframes.heygen.com/prompting/captions-catalog.md
- https://hyperframes.heygen.com/guides/performance.md
- https://hyperframes.heygen.com/catalog
- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media
- https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html

**Maturity**

stable

**Last Verified**

2026-11-15

### Capabilities

**What It Enables**

A vertical reel can be authored as one 1080x1920 HTML document whose canvas is exactly the delivered frame: `init --resolution portrait` scaffolds 1080x1920, `--fps 60` quantises the timeline to 1680 frames, kinetic captions are DOM elements you style in real pixels, and `check` audits the result against Reels/TikTok/Shorts safe areas plus WCAG AA contrast before a render is spent. Measured on this machine: 1 m 48 s and 42.5 MB for 28 s at 60 fps with four workers.

**Key Features**

- Canvas setup is literal, not scaled: the scaffold writes `html, body { width:1080px; height:1920px }`, `data-width="1080" data-height="1920"` and `data-resolution="portrait"` on <html>, so a 46 px caption is 46 device pixels in the delivered frame at deviceScaleFactor 1.
- Frame rate is not implied by the canvas: there is no data-fps in the portrait scaffold, the schema default is 30, and `--fps 60` (or `data-fps="60"` on the root) is what turns 840 frames into 1680. Measured cost of that choice on the same project: 72.1 s / 37.7 MB at 30 fps vs 107.6 s / 42.5 MB at 60 fps, i.e. 1.49x the wall time for 13% more bytes.
- The catalog is landscape-first, so 9:16 is an authoring discipline: of 448 catalog entries, 238 are dimensionless components but 195 of the 210 blocks are authored at 1920x1080 and only 12 at 1080x1920 (plus one square 1080x1080, one 1440x2560 and one 1080x1350). Any block dropped into a vertical reel needs reflow, not just a resize.
- Caption components are the exception and the right primitive: 17 `caption-*` components (highlight, kinetic-slam, pill-karaoke, neon-glow, editorial-emphasis, matrix-decode, parallax-layers ...) are aspect-agnostic because they are absolutely positioned word layers, and the docs say outright that they 'ship as demos — a fixed word list, landscape sizing, an 8-second timeline' and that re-authoring the words, timings and size for your format 'is expected, not a workaround'.
- A real 1080x1920 demo of the pattern was built and rendered here: 16 cuts of 720x1280 footage, 46 px uppercase caption cards, an emerald `#059669` price pill, an `@fenixsoft` footer and SVG checkmarks in the exact Fenixsoft zones — output 1080x1920@60 H.264 High@4.2 at 12.6 Mbps.
- Safe-zone auditing is shipped as a check, not a guideline: `check --safe-zone reels|tiktok|shorts|standard|off` defaults to all three social zones on a 9:16 canvas (applied when the aspect is within 0.01 of 9:16) and emits `outside_safe_zone` findings naming the text and the edge it violates.
- HyperFrames' own Reels box is y 14%..65% and x 6%..94% of the frame — on 1080x1920 that is **y 269..1248, x 65..1015**. The current Fenixsoft header (y 110..318) and bottom stack (y 1305..1628) both fail it; measured, 14 warnings, no configuration needed to reproduce.
- Contrast is measured per frame, not eyeballed: `check` walks every text element against the actual composited background and returns `contrast_aa_failure` with the ratio, the required ratio (3:1 for large text, 4.5:1 for normal) and a suggested colour. On the demo reel it returned five errors and one warning, worst 1.25:1 (a badge) and 2.58:1 (the footer).
- Line duration is judged as craft, not taste: `check` flags `line_unread` when a line is on screen for less time than it needs to be read ('28 s film ... last line on screen 1.68 s, under the 2.00 s a closing line needs'), which is the vertical analogue of the pipeline's CPS gate.
- The showcase is a gallery, not a template library for 9:16: `init --example` offers ten generic starters (blank, kinetic-type, product-promo, path-traced-3d, play-mode, swiss-grid, vignelli, warm-grain, nyt-graph, decision-tree), the public showcase at hyperframes.heygen.com/examples is a feed of finished films, and the only portrait assets you can actually install are the 12 blocks authored at 1080x1920 (plus one at 1440x2560 and one at 1080x1350) and the dimensionless caption components — two of those blocks (instagram-follow, tiktok-follow, both 4.5 s at 1080x1920) are directly reusable as social overlays.
- Rendering a vertical reel is cheap enough to iterate: measured 1 m 48 s at 4 workers on a 2.6 GB-available WSL2 box, with stills available from the finished MP4 in one ffmpeg call and from `snapshot --at` without a full render.

**Technical Mechanism**

A composition is one HTML document; the root element carries `data-composition-id`, `data-width`, `data-height` and (optionally) `data-duration`/`data-fps`. Elements with `data-start`/`data-duration` become timed clips, `<video>` elements are pre-extracted to frames by FFmpeg and re-drawn per frame, and animation is a paused GSAP timeline registered on `window.__timelines[<compositionId>]` that the renderer seeks to a quantised time before each capture. On a 9:16 canvas the page is laid out at exactly 1080x1920 with deviceScaleFactor 1, so CSS pixels and delivered pixels are the same number; fonts are inlined as woff2 data URIs at compile time (11 Inter faces were fetched and cached for the demo) so the render cannot fall back to a different face. The `check` command reuses the same browser machinery without the encoder, collecting per-sample element bounding boxes (which is how a safe-zone violation becomes a finding with a y coordinate rather than an opinion).

**Output Formats**

- mp4 (default): H.264 High profile, level 4.2, yuv420p, 1080x1920@60, ~12.6 Mbps at `--quality looks`, 42.5 MB for 28 s — measured with ffprobe on the demo render.
- WhatsApp-ready derivative: re-encode to Main@4.2, CRF 21, maxrate 2500k, 128k AAC, faststart → 8.54 MB for the same 28 s (measured, 33.9 s of FFmpeg). At 2.5 Mbps the 16 MB ceiling holds up to ~51 s of video.
- webm (VP9 + Opus) and mov (ProRes 4444): the alpha-capable formats, only relevant if the captions are rendered as an overlay to composite in FFmpeg instead of full-bleed.
- png-sequence: RGBA frames, the lossless intermediate if the final encode must stay in the existing libx264 Main@4.2/CRF 18 recipe.
- Stills for review: `snapshot --at 2.9,10.4` writes PNG crops into .hyperframes/artifacts/snapshots/, and `check --snapshots` returns per-sample frames with the audit.
- gif (≤30 fps) and hls: not applicable to a 9:16 social delivery.

### Usage & API

**Setup And Install**

```bash
# scaffold a vertical project (exact resolution preset, non-interactive)
npx --yes hyperframes@0.9.54 init reel --resolution portrait --non-interactive --skip-skills
# 1080x1920 -> <html data-resolution="portrait">, root data-width="1080" data-height="1920"

# add a caption component to copy from (installs editable source, not a package)
npx --yes hyperframes@0.9.54 add caption-kinetic-slam --dir reel
# -> compositions/components/caption-kinetic-slam.html

# gate, then render at 60 fps with the memory profile disabled
npx --yes hyperframes@0.9.54 lint reel --json
npx --yes hyperframes@0.9.54 check reel --safe-zone reels --json
npx --yes hyperframes@0.9.54 render reel --fps 60 --quality looks --workers 4 --no-low-memory-mode --output out/reel.mp4
```

**Minimal Code Example**

```html
<!doctype html>
<html lang="es" data-resolution="portrait"
      data-composition-variables='[{"id":"price","type":"string","label":"Price","default":"$330"}]'>
<head><meta charset="utf-8"><style>
  *{margin:0;box-sizing:border-box}
  html,body{width:1080px;height:1920px;overflow:hidden;background:#08110f;
            font-family:Inter,"DejaVu Sans",Arial,sans-serif}
  #root{position:relative;width:1080px;height:1920px}
  .shot{position:absolute;inset:0}
  .shot video{width:1080px;height:1920px;object-fit:cover}
  /* bottom stack: y 1305..1628, x 65..950 (Fenixsoft reels_universal) */
  #cap{position:absolute;left:65px;top:1305px;width:885px;font-weight:900;font-size:46px;
       line-height:1.06;letter-spacing:-.5px;text-transform:uppercase;color:#fff;
       text-shadow:0 4px 18px rgba(0,0,0,.55)}
  #pill{position:absolute;left:65px;top:1470px;padding:16px 34px;border-radius:999px;
        background:#059669;color:#04231a;font-size:62px;font-weight:900}
  #foot{position:absolute;left:65px;top:1584px;font-size:30px;color:#e6fff5}
</style></head>
<body>
<div id="root" data-composition-id="reel" data-start="0" data-duration="28"
     data-width="1080" data-height="1920" data-fps="60">
  <div class="shot">
    <video class="clip" id="v0" data-start="0" data-duration="1.75" data-media-start="0.4"
           src="./assets/01_back_cameras.mp4" muted playsinline></video>
  </div>
  <div id="cap" class="clip" data-start="0" data-duration="1.75">&nbsp;</div>
  <div id="pill" class="clip" data-start="0" data-duration="28">$330</div>
  <div id="foot" class="clip" data-start="0" data-duration="28">@fenixsoft • Mercado Social C-29</div>
</div>
<script>
const tl = gsap.timeline({paused:true});
// one card per narration line, words injected from the pipeline's timings
const card = document.getElementById('cap');
const words = 'IPHONE 14 EN 330 DÓLARES'.split(' ');
card.innerHTML = words.map(w=>`<span class="w">${w}</span>`).join(' ');
tl.set(card,{visibility:'visible'},0);
tl.fromTo('.w',{y:26,opacity:0},{y:0,opacity:1,duration:.16,stagger:.045,ease:'power3.out'},0.04);
// 0.16 s punch-in on the cut
tl.fromTo('#v0',{scale:1.12},{scale:1.0,duration:.16,ease:'power2.out'},0);
window.__timelines = window.__timelines || {}; window.__timelines['reel'] = tl; tl.seek(0);
</script>
</body></html>
```
```bash
npx hyperframes@0.9.54 render reel --fps 60 --workers 4 --no-low-memory-mode --output out/reel.mp4
npx hyperframes@0.9.54 check reel --safe-zone reels --caption-zone "x0=0;y0=.82;x1=1;y1=1" --json
```

**Cli Or Api Surface**

- `init <name> --resolution portrait|portrait-4k|portrait-1440p|square|landscape ... --example <name> --non-interactive` — portrait = 1080x1920, portrait-4k = 2160x3840, portrait-1440p = 1440x2560.
- `add <component> --dir <dir>` — installs editable HTML source under compositions/components/ (caption-kinetic-slam, caption-highlight, caption-blend-difference, instagram-follow, tiktok-follow ...).
- `catalog --tag social --json` / `catalog <words> --json` — rows carry `dimensions {width,height}`, `duration`, and a preview poster/video, which is how you tell a 1920x1080 block from a 1080x1920 one before installing it.
- `lint <dir> --json` — 0 errors is achievable; the codes that bite on 9:16 are `media_missing_id`, `video_nested_in_timed_element`, `timed_element_missing_clip_class`, `font_face_local_only`, `nested_structure_needs_subcomposition`.
- `check <dir> --safe-zone reels|tiktok|shorts|standard|off --caption-zone "x0=0;y0=.82;x1=1;y1=1" --strict --json` — layout (per-element bbox per sample), contrast (WCAG AA per text element), motion, craft, audio; `--strict` promotes warnings to failures.
- `snapshot <dir> --at 2.9,10.4 --zoom '#cap' --zoom-scale 3 --review` — stills and raised-DPR crops for reviewing 46 px type without a 2-minute render.
- `render <dir> --fps 60 --quality looks --format mp4 --crf N | --video-bitrate 2.5M --max-bitrate --chroma-subsampling 420|444 --workers --no-low-memory-mode --variables '{...}'`.
- `benchmark <dir> --runs 1..20 --json` — re-measure the fps/worker trade-off on the composition you actually ship.
- `@hf-progress {code,done,total,pct}` + `@hf-result` JSON lines and exit codes 0/1/2 — the automation contract.

**Key Config Options**

- `data-width` / `data-height` (or `--resolution portrait`): the composition's own size. 1080x1920 is the delivery size; 2160x3840 is the only sanctioned upscale because `--resolution` requires an exact aspect match and an integer multiple.
- `data-fps` on the root, else `--fps` at render; **default 30**. A 9:16 reel that must be 60 fps needs one of the two set explicitly — the portrait scaffold sets neither.
- `--quality draft|looks|standard|high`: `looks` = CRF 16 + preset standard (42.5 MB measured), `draft` = ultrafast/CRF 28 for iteration, `high` = fast/CRF 15 for masters.
- `--safe-zone` (default: all three social zones on 9:16) and `--caption-zone "x0=0;y0=.82;x1=1;y1=1"` (fractions of the frame, default band = y 82%..100%).
- `--chroma-subsampling 444` for text/UI masters — thin coloured type stops fringing but the file stops being playable in many players, so it is a master, not a delivery.
- `--workers` and `--no-low-memory-mode`: on an 8 GB box the memory profile forces 1 worker and screenshot capture, which triples the render time (391 s → 107 s measured).
- `--variables` / `--variables-file` / `--strict-variables` — typed per-SKU copy (price, footer, title) without editing HTML.
- `data-start` / `data-duration` / `data-media-start` / `data-volume` / `data-fade-in|out` on media, `class="clip"` on timed elements, and `data-no-timeline` when a composition has no GSAP timeline (saves a fixed ~45 s wait).

### Integration

**Pipeline Fit**

Augments the current stack for the 9:16 deliverable, and the cleanest first use is an **overlay**, not a full-bleed replacement: keep `plan_edit.py`/`render_video.py` cutting the beat-locked 1080x1920 footage and mixing audio, and render only the text layer (captions, price pill, header, checkmarks) as a transparent webm/mov or as PNG frames, then composite in the same FFmpeg pass that already applies the grade and the loudness chain. Full-bleed is the alternative and is simpler to author, but it moves the footage compositing into Chrome and duplicates work FFmpeg already does faster.

**Python Integration**

Two half-bridges, both simple. Generation: a Python template writes `index.html` from `sku.json` + `narration_lines.json` + `caption_audit.json`, injecting the word list and its `{text, start, end}` timings literally into the GSAP timeline (the catalog components do exactly this with a `WORDS` array) and the price/footer through `data-composition-variables` + `--variables`. Rendering: `subprocess.run([..."render", dir, "--fps", "60", "--workers", "4", "--no-low-memory-mode", "--output", mp4], env={... "HYPERFRAMES_NO_UPDATE_CHECK": "1"}, timeout=900)` and parse `@hf-progress` lines for progress. Gating: run `lint` first (seconds) and `check --safe-zone reels` second (~40 s of browser work), and only then spend the render — the same 'validate before the expensive step' shape the repo already uses for `validate_edit_plan.py`.

**Data Injection**

Per-SKU copy goes through `data-composition-variables` (typed defaults) plus `--variables '{"price":"$330"}'` or `--variables-file vars.json`, read in-page with `window.__hyperframes.getVariables()`; `--strict-variables` fails on undeclared or mistyped keys, which is the guard the current pipeline lacks. Per-word timings cannot come through variables (they are structured data), so the honest pattern is HTML generation: emit one `<span>` per word and one GSAP tween per word from the same `word_timings.json` that the FFmpeg path consumes, so both renderers stay on one source of truth.

**Footage And Asset Handling**

Existing clips are 720x1280@60 in this repo, which is 1.5x below the 1080x1920 canvas — measured here, the upscale is visible as softness on the phone edges, so a vertical deliverable should either shoot/export native 1080x1920 or accept the interpolated look. `<video class="clip" id="..." data-start data-duration data-media-start src="./assets/x.mp4" muted>` is the contract; the id is mandatory (a <video> without one renders frozen), the wrapper must be untimed, and sparse-keyframe sources make preview seeking slow (the render itself probes colour spaces and extracts per unique file, 3.6 s for five clips here). Fonts: use a bundled family (Inter, Montserrat, Outfit, Archivo Black ...) or embed a woff2 file; a `src: local(...)` @font-face is lint-flagged because the real font then depends on the machine.

**Migration Effort**

medium

**Integration Steps**

- 1. Decide the placement contract first: WhatsApp Status (almost no bottom chrome) and Reels (bottom ~35% covered) cannot share one layout — add a placement field to sku.json (`status_story` vs `reels_ads`) exactly as the existing safe_zone_mode hints at.
- 2. Move the header and the bottom stack inside HyperFrames' Reels box (y 269..1248) for the Reels variant, or accept the current 110..318 / 1305..1628 layout only for Status and pass `--safe-zone off|standard` deliberately rather than by accident.
- 3. Generate the composition HTML from sku.json + narration_lines.json, with 46 px Inter Black cards ≤ 4 words / ≤ 26 chars, and the emerald checkmarks as inline SVG paths (never a Unicode ✓).
- 4. Add a scrim or stroke behind any text that sits over footage, then prove it with `check --safe-zone reels` returning 0 contrast failures before the first real render.
- 5. Render at `--fps 60 --workers 4 --no-low-memory-mode`, and keep the run in the existing `<sku>_<stamp>/` convention so the file is comparable with the FFmpeg renders.
- 6. Re-encode to the WhatsApp envelope (Main@4.2, CRF 21/2.5 Mbps, faststart) and assert ≤ 16 252 928 bytes; measured 8.54 MB for a 28 s cut.
- 7. Extend qa_check with the two new numbers this workflow produces — the safe-zone violation list from `check --json` and the minimum measured contrast ratio — instead of asking the model to look at frames.
- 8. Only then consider moving the footage itself into the composition; until then keep FFmpeg as the compositor and HyperFrames as the type layer.

### Constraints

**Requirements**

- Node ≥ 22 (v24.22.1 here), FFmpeg + ffprobe on PATH (6.1.1-3ubuntu5), and chrome-headless-shell (152.0.7977.30, downloaded to ~/.cache/hyperframes/chrome).
- A composition whose root declares `data-width="1080" data-height="1920"` and either `data-fps="60"` or a `--fps 60` render flag.
- Fonts that resolve without the network at render time: bundled families, or woff2 files in the project; Google Fonts work because the compiler fetches and inlines them, but that turns a cache miss into a render-time network dependency.
- Footage at or near 1080x1920: 720x1280 sources are upscaled 1.5x and lose edge detail on a phone held close to the camera.
- For delivery to WhatsApp: H.264 **Main** profile without B-frames (High + B-frames is documented as unsupported by Android WhatsApp clients), AAC, `-movflags +faststart`.
- RAM/CPU measured here: 4 workers on 16 threads used 711% CPU and 3.2 GB was still available; 6 workers used 822% and left 2.9 GB.

**Limitations**

- HyperFrames' Reels safe area is y 269..1248 (14%..65%) and x 65..1015 — the current header at y 110..318 starts 159 px above it and the bottom stack at y 1305..1628 starts 57 px below it. `check --safe-zone reels` reports 14 `outside_safe_zone` findings on the current layout with no tuning.
- The repo's own `status_story` zone (x 65..1015, y 250..1570) matches HyperFrames' Reels box horizontally — both are x 65..1015 — but vertically it runs 322 px past the Reels bottom line (1570 vs 1248), so the WhatsApp layout is structurally a Status layout, not a Reels one.
- The three social zones do not agree: TikTok (720x1280) allows only y 160..840 and x 80..640, Shorts (1080x1920) allows y 288..1248 and x 48..888, so the cross-platform intersection is roughly y 288..1248 and x 80..888 — a 960x672 usable rectangle in a 1080x1920 frame, which is exactly where 'central phone footage must stay unobstructed' wants to be.
- Most of the catalog is landscape: 195 of 210 blocks are 1920x1080 and only 12 are 1080x1920. Installed blocks do not reflow themselves; they are absolutely positioned compositions authored for 16:9.
- Caption components are demos: fixed word lists, landscape 1920x1080 canvases, `data-fps="30"`, 8-second timelines and a 220 px base font that auto-shrinks to 99 px at 1720 px max width — none of those numbers survive a move to 885 px-wide cards at 46 px.
- Contrast is not enforced, only reported: white 46 px text over the demo's real footage measured 1.25:1 (badge), 1.63:1 (subtitle), 1.86:1 (title) and 2.58:1 (footer) against the 3:1 large-text floor, and nothing in the render path prevents shipping it.
- Frame rate defaults to 30: the portrait scaffold writes no `data-fps` and `render` falls back to 30, so '1080x1920@60' only happens if the flag or attribute is explicit.
- 60 fps costs real time for a small size gain: measured 107.6 s / 42.5 MB at 60 fps vs 72.1 s / 37.7 MB at 30 fps on the identical composition.
- HyperFrames' MP4 defaults are H.264 **High**@4.2 at ~12.6 Mbps — 42.5 MB for 28 s, i.e. 2.6x the WhatsApp ceiling — so every delivery needs a re-encode that also fixes the profile.
- Safe-zone and caption-zone audits only apply to a canvas whose aspect is within 0.01 of 9:16; a 1080x1350 or 4:5 variant silently gets no social-zone checks.
- A three-line header stack plus two badges in the 208 px header box (110..318) is tight at these type sizes: in the demo render the badge row collided with the subtitle descender, and lint had already warned that the header's nested structure needs a sub-composition.
- Reels captures the top ~14% and bottom ~35% with chrome regardless of what the safe-zone audit says, so a layout that passes only because the audit was disabled will still lose its price pill behind the caption UI on a real phone.

**Known Issues**

- Measured: `outside_safe_zone` fires on every Fenixsoft-zone element (header title/subtitle/badges at the top edge; captions, price pill and footer at the bottom edge) when `--safe-zone reels` is used on the current layout.
- Measured: `contrast_aa_failure` × 5 (+1 warning) with ratios 1.25, 1.63, 1.86, 1.98, 2.41 and 2.58:1 against `requiredRatio: 3` for large text, each with a `suggestedColor` — useful, but the suggested greys would destroy the brand palette, so the real fix is a scrim.
- Measured: `line_unread` — 'the film's last line "ESCRÍBENOS AL WHATSAPP" (3 words) is on screen 1.68 s, under the 2.00 s a closing line needs'.
- Measured: `media_missing_id` — a <video> with data-start but no id 'will be FROZEN in renders'; and `video_nested_in_timed_element` is an error when the wrapper is timed too.
- Measured: `font_face_local_only` — an @font-face with only `src: local(...)` makes a local render depend on the installed font; the compiler's deterministic @font-face injection only covers families it can resolve.
- Measured: the portrait scaffold and every catalog component load GSAP from `cdn.jsdelivr.net` and the caption component loads Anton from Google Fonts — two remote dependencies at render time.
- Documented (WhatsApp Cloud API media reference): MP4 max 16 MB, 'videos encoded with the H.264 "High" profile and B-frames are not supported by Android WhatsApp clients', recommended Main or Baseline without B-frames plus `-movflags faststart`.
- Documented (caption catalog): caption components 'ship as demos — a fixed word list, landscape sizing, an 8-second timeline. The agent re-authors the words and timings to your narration and re-sizes for your format; that's expected, not a workaround'.

**Licensing**

Apache-2.0 for the CLI, engine, producer and catalog; caption components and blocks are installed as editable source into your own project, so the licence you ship is yours. Fonts are the real constraint in a vertical reel: bundled families are OFL, Google Fonts families are fetched and inlined at compile time under their own licences (Inter is OFL), and any brand face shipped as woff2 must be licensed for embedding. Platform terms are separate: WhatsApp's 16 MB messaging limit and Reels' UI chrome are delivery constraints, not licensing ones. HeyGen-hosted features (voices, avatars, cloud render) sit behind the Studio plans and are not needed for this workflow.

### Evidence

**Confidence**

0.86

**Sources**

- https://hyperframes.heygen.com/prompting/captions-catalog.md — the tone→component map and the explicit note that caption components ship as landscape demos with fixed words and an 8 s timeline.
- https://hyperframes.heygen.com/guides/performance.md — 'render is slow but the result is correct' triage: source extraction, frame capture, encoding; expensive blurs/shadows enlarge capture cost.
- https://hyperframes.heygen.com/help.md — the 45 s player-ready penalty and `data-no-timeline`; the WSL2 section that explains the software-WebGL fallback.
- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media — 16 MB MP4 ceiling, H.264 High + B-frames unsupported on Android clients, Main/Baseline + faststart recommendation.
- https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html — 4.5:1 normal text, 3:1 large text (the ratio `check` enforces as `requiredRatio`).
- https://marxx.ai/guides/instagram-reels-safe-zone — third-party compilation dated 'checked 3 Nov 2026': keep text out of the top 14%, bottom 35% and 6% per side (= 269/672/65 px on 1080x1920), and its own caveat that Instagram publishes no separate figure for organic Reels.
- https://hyperframes.heygen.com/catalog and `npx hyperframes catalog --json` — 448 entries; block dimensions counted locally (195 × 1920x1080, 12 × 1080x1920, 1 × 1080x1080, 1 × 1440x2560, 1 × 1080x1350).
- Local measurements (2026-11-15): `init --resolution portrait` scaffold, `add caption-kinetic-slam` source, a purpose-built 28 s 1080x1920 composition rendered at 30/60 fps and 1/4/6 workers, `lint`, `check --safe-zone reels` (14 `outside_safe_zone` warnings, 5 contrast errors + 1 warning, 2 craft warnings), PIL contrast sampling on extracted frames, and the WhatsApp re-encode.

**Open Questions**

- Whether the current layout should move for Reels or stay for WhatsApp Status — a product decision, not a technical one; the audit gives the numbers but the placement mix is unknown.
- How Reels' chrome actually behaves on a 2026 device (the 65% line is HyperFrames' constant and matches the widely quoted 35% bottom band, but Meta publishes no pixel numbers; only a device test settles it).
- Whether 60 fps survives platform re-encoding: Instagram and WhatsApp both transcode, and no upload test was run to see whether the 60 fps motion (or the 0.16 s punch-in) is preserved.
- Whether a transparent-overlay path (webm/mov alpha composited in FFmpeg) is visually identical to a full-bleed HyperFrames render on this footage — not A/B'd.
- Whether the 46 px Inter Black size is optimal at arm's length or should shrink to 40–42 px to buy line length; only a phone-in-hand test answers that.
- Whether source footage should be re-exported at native 1080x1920 — the 720x1280 upscale was visible, but the perceptual cost was not measured with SSIM/VMAF.

### Other Info

**Text And Motion Capabilities**

Mobile legibility is the whole job here, and the numbers are hard. A 46 px black-weight caption in a 885 px card holds ~24–26 uppercase characters per line (~11 px per character average at Inter Black 46 px), which is the practical reason the pipeline's 4-word/26-char card rule exists; at 1080 wide the card can grow to 950 px (x 65..1015) but the right-hand third of a Reels frame is where the action buttons sit, so 885 is the honest width. Auto-fit is available and is what the catalog components use: a canvas `measureText` loop shrinks from a base size to 45% of it (caption-kinetic-slam: 220 px base at 1920-wide, floor 99 px at max width 1720), which for a 1080-wide canvas means re-deriving the base (46–56 px here) rather than reusing 220 px. Per-word animation is the norm: word spans with staggered `y/opacity` in ~0.13–0.16 s, and the pipeline's 0.16 s 1.12x→1.0x punch-in maps cleanly onto a `fromTo(scale)` per cut. What is *not* automatic is contrast: white 46 px text over the demo's real footage measured 1.25–2.58:1 depending on the element and the frame, below even the 3:1 large-text floor, and `check` reported it as five AA errors plus one warning. The fix is a scrim, a stroke, or `caption-blend-difference` (which auto-inverts per pixel) — not a bigger font.

**Performance And Cost**

Measured on this WSL2 box (Ryzen 7 5825U, 7.8 GB) with a real 28.0 s, 1080x1920 composition (16 cuts of 720x1280@60 footage, 46 px caption cards, price pill, footer, SVG checkmarks):
• 1080x1920@60, `--quality looks`, `--workers 4 --no-low-memory-mode`: render 1 m 47.6 s, wall 1 m 59.4 s, capture+encode 1 m 42.2 s, CPU 711%, output 42.5 MB (H.264 High@4.2, 12.7 Mbps).
• Same at 30 fps: render 1 m 12.1 s, output 37.7 MB → 60 fps costs ~1.49x time for ~13% more bytes.
• Same at 1080x1920@60 with default settings (low-memory profile → 1 worker, screenshot capture): 6 m 31.4 s, output 42.0 MB — the default is 3.6x slower than the tuned one.
• 6 workers: 1 m 40.3 s — 6% better than 4 workers, at ~50% more RAM.
• Fixed per-render overhead: compile 2.9 s, source extraction 3.6 s, probe 0.6 s, setup 2.5 s, assemble 0.5 s (~10 s); plus a ~45 s player-ready wait if the composition never registers a GSAP timeline.
• Delivery: 42.5 MB is 2.6x the WhatsApp 16 MB ceiling; re-encoding to Main@4.2/CRF 21/2.5 Mbps took 33.9 s and produced 8.54 MB for the same 28 s. At 2.5 Mbps the 16 MB limit is reached at ~51 s of video, so 28 s leaves comfortable headroom for the fallback CRF-21 path to stay under the cap.
• Disk per reel: 42 MB output + ~40 MB extraction cache + 0.1–0.8 GB of transient frame traffic depending on how detailed the frames are (64 KB/frame measured for UI-like frames, 484 KB/frame for noisy photographic content).
• Cost: zero — Apache-2.0, local render, no account. The only recurring cost is ~2 minutes of CPU per reel.

---

