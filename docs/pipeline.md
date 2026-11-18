# Pipeline

Every stage is a `fenix` command, reads declared inputs, writes one declared
artifact (or the voice source for `tts`), and has an acceptance condition.
Nothing downstream re-invents a value that an upstream stage measured.

| # | Command | Reads | Writes | Cost |
|---|---|---|---|---|
| 0 | `fenix storyboard <project>` | `input/<project>/brief.json` | `output/<project>/storyboard.{json,md}` | ~1 s |
| 0.5 | `fenix lint <sku>` | `input/<sku>/sku.json` | `output/<sku>/script_lint.json` | ~1 s |
| 0.6 | `fenix tts <project>` (optional) | `tts.json`, provider key in env | `input/<project>/voice.mp3` | 2–20 s |
| 1 | `fenix beats <sku>` | `assets/music/*`, config | `output/<sku>/beat_grid.json` | ~2 s |
| 2 | `fenix frames <sku>` | `input/<sku>/clips/*` | `input/<sku>/keyframes/`, `input/<sku>/candidates.json` | ~20 s |
| 3 | `fenix align <sku>` | `voice.mp3`, `sku.json` | `word_timings.json`, `narration_lines.json`, `voice_28s.wav` | ~15 s |
| 4 | `fenix plan <sku>` | `narration_lines.json`, `candidates.json`, `picks.json` | `input/<sku>/edit_plan.json`, `output/<sku>/plan_edit.json` | ~2 s |
| 5 | `fenix validate <sku>` | `edit_plan.json`, clips, beat grid | `output/<sku>/plan_validation.json` | ~2 s |
| 5.5 | `fenix sfx <sku>` | `edit_plan.json`, `word_timings.json`, SFX library | `output/<sku>/sfx_cue_sheet.json`, `sfx_bus.wav`, `music_bed.wav`, `sfx_report.json` | ~2–5 s |
| 6 | `fenix render <sku>` | `edit_plan.json`, clips, audio assets | `output/<sku>/<run>/…` | ~3 min |
| 6.5 | `fenix motion <project>` | `motion.json` | `output/<p>/motion/<run>/plate.mp4|.mov`, manifest | 10 s–3 min |
| 6.6 | `fenix track <project>` | `track.json`, clip | `output/<p>/track/track_<stamp>.json` | 2–10 s |
| 6.7 | `fenix comp <project>` | `comp.json`, base + layer media, optional track | `output/<p>/comp/<run>/comp.mp4`, manifest | 5 s–1 min |
| 6.8 | `fenix fx <project>` | `fx.json`, clip | `output/<p>/fx/<run>/fx.mp4`, manifest | 5–30 s |
| 6.9 | `fenix grade <project>` | `grade.json`, input, LUT/reference | `output/<p>/grade/<run>/graded.mp4`, stills, manifest | 5–30 s |
| 6.10 | `fenix scopes <project>` | `scopes.json`/`--input`, frame time | `output/<p>/scopes/scopes_<stamp>/` | ~2 s |
| 6.11 | `fenix longform <project>` | `longform.json`, chapter media | `output/<p>/longform/<run>/master.mp4`, `chapters.md` | 10 s–5 min |
| 7 | `fenix qa --latest <sku> --strict` | the run | `<run>/qa_report.json` | ~30 s |
| 8 | `fenix eval <sku> --skip-align --skip-render` | the run + `eval/<sku>_baseline.json` | `eval/<sku>_<stamp>.json` | ~30 s |
| 9 | `fenix deliver --latest <sku>` | the run | `delivery.dir/<run>/` + `delivery.json` | ~1 s |
| 9.5 | `fenix finish <project>` | latest run | strict QA → delivery shelf + flat Downloads → prune local media | ~40 s |

`fenix job <project>` is the state card that opens a video task: it prints the
mode, missing inputs, required skills, stage/render freshness and the next
command without writing anything. It is not a stage.
`fenix make <project>` orchestrates the edit chain (stale stages → render/remix
→ QA → finish) from the same artifacts; it is a shortcut, not a separate stage.
It combines mtime, per-stage config slices (`output/<p>/.state/`), the
voice/script fingerprint in `word_timings.json`, and the render's
`config_slices` so a delivery/config edit does not trigger a re-encode and an
audio edit does not re-run the frame loop.
`fenix label scan/apply` is the footage-intake companion, not a chain stage: it
probes a folder, builds sheets/vision grids, flags duplicates and renames safely
before the clips enter `input/<project>/clips/`. See
[reference/commands.md](reference/commands.md#asset-intake--label).

## 0. Storyboard (all projects)

`fenix storyboard <project>` reads `input/<project>/brief.json` and writes a
timed scene table. `story.framework` (or `--framework`) selects the beat
structure: `hook_retain_reward` (short-form default), `storybrand` (brand),
`explainer`, `save_the_cat` (long-form), `kishotenketsu` (music/mood),
`three_act`, `aida`, `pas`, `bab`, `story_circle`, `pixar_spine`, `hero`.
`story.decisions` records the human choices. Nothing renders until format,
duration and story exist.

## 0.5 Script lint

Reads `sku.json → script_text` and measures it before a TTS credit is spent:

- words vs `script.spoken_wpm × script.max_tempo × target_duration_sec`
  (162 wpm × 1.12 × 28 s ⇒ **84 words**; the demo is 93 and fails on purpose);
- the tempo the renderer would need to fit the target;
- unspeakable tokens (`14x330`, `77%`, `$330`, URLs, emoji) and brand tokens
  that need `display_map`/`display_merge` coverage.

Rows follow the QA shape: `{id, measured, threshold, pass, fix}`. Exit 1 on a
hard failure. Warnings (phonetics) become failures with `--strict`.

## 0.6 TTS (optional)

`fenix tts <project>` reads `input/<project>/tts.json`, calls ElevenLabs or
OpenAI, and writes `input/<project>/voice.mp3` (or the spec's `out`). Keys come
from `ELEVENLABS_API_KEY` / `OPENAI_API_KEY` in the environment and are never
printed or written to disk. `provider: auto` prefers ElevenLabs. This is the
only stage that writes a source file into `input/` by design. Run it before
`fenix align`; `lint` still runs first so a bad script does not spend a TTS
credit.

## 1. Beat grid

Decodes the music bed once, runs librosa `beat_track`, and writes the measured
BPM, beat length, phase, beat count and the drift against the nominal config
`bpm`. Measurement is cached by file SHA-256 in `.cache/beat_grid/`.

The demo bed is **118.97 BPM**, not the nominal 118: by the last beat the
nominal grid is **235 ms** away. The planner snaps to the measured beat list, so
cuts land within **3 ms**.

## 2. Keyframes and candidates

Writes 1 fps JPEGs plus `keyframe_report.json` (luma + Laplacian sharpness) for
the agent's visual review, then a scored candidate manifest:

- samples the clip every `--candidates-step` (default 0.25 s = 4 fps);
- window score = `(sharpness / clip median) × exp(−motion / 25)`;
- dark samples (mean luma < 35) are rejected;
- each window gets a stable ID (`01c-a`, `01c-b`, …) and a duration that fits a
  beat-sized cut.

`candidates.json` is the contract that removes guessed timestamps. The agent
picks `{"line": 3, "candidate": "04c-b"}`; the planner and validator resolve the
same window.

## 3. Align

1. Decode the voiceover to 16 kHz mono float32.
2. RMS-gate pauses ≥ 0.26 s at −38 dB (ffmpeg `silenceremove` only trims the
   head on some builds).
3. Transcribe with faster-whisper `small` int8, word timestamps, Spanish.
   Model cache: `.cache/models/` (no torch, ~460 MB).
4. Tail guard: cut hallucinated/breath words so the last caption matches
   `script_text`.
5. Tempo-fit to the target with `atempo` (cap 1.30) and rescale word times.
6. Write `word_timings.json` and, when `script_text` exists, `narration_lines.json`
   (sentences and comma clauses matched to ASR words by difflib).

ASR transcripts are cached by clean-audio hash in `input/<sku>/.asr_cache.json`
(schema 3). `--refresh-asr` ignores the cache after replacing `voice.mp3`.

The tail cut is decided on the waveform (`speech_cut_point`): it walks past
Whisper's end time to the first real inter-word pause, so the last phrase is
complete, and `voiceover.tail_pad_sec` (0.35 s) of silence is appended so the
last word is followed by a breath instead of a hard stop. A piece may run past
28.0 s by up to `voiceover.max_extra_sec` (2.0 s)
to finish the sentence; QA's `d_max` uses the same allowance.

## 4. Plan

One cut per narration line. Short lines merge and long lines split at word
boundaries until there are `cuts_per_video` units (16) of at least `min_cut_sec`
(1.2 s). Boundaries are chosen **by beat index** on the measured grid, so a
two-beat gap is 1.0086 s, not an arbitrary 1.0 s.

Footage selection:

- `picks.json` maps a line to a candidate ID and/or clip:
  `{"lines": [{"line": 0, "candidate": "01c-b"}]}`;
- without picks, the planner rotates the best-scored windows, never repeats the
  previous clip, and records that it auto-picked;
- explicit `in`/`dur` overrides are accepted but validated against the same
  sharpness and beat rules.

Outputs `input/<sku>/edit_plan.json` (the renderer's contract) and
`output/<sku>/plan_edit.json` (per-line table with scores and notes).

## 5. Validate

A one-second gate before a three-minute render. Checks:

- schema, unknown clips, unknown candidate IDs, non-numeric times;
- `in + dur` inside the source clip;
- each cut ≥ `min_cut_sec`;
- total matches the voiceover;
- in-point sharpness ≥ `quality.in_point_floor` × clip median;
- whole-shot quality (`window_floor`, `window_soft`);
- boundary within `beat_offset_warn_ms` / `beat_offset_fail_ms` of a beat.

Every row carries a `fix`; soft in-points get a `best_window()` suggestion. Exit
1 on any error row. The renderer independently refuses a soft shot unless
`--allow-soft-in` is passed.

## 5.5 Sound design

`fenix sfx` turns the locked timeline into a **cue sheet before a waveform**:

- reads `edit_plan.json` (cuts, `t0`, line text) and `word_timings.json`
  (keyword accents); writes `sfx_cue_sheet.json` with one row per event:
  `t_sec`, visual beat, layer, role, source, gain, align, start, lead;
- plans three layers: environmental music bed, anticipation (J-cut
  whooshes/risers), tactile Foley (clicks + body thuds at +18 ms) and sub
  impacts reserved for the hook, contrast flips and the final lock;
- enforces the blueprint gates in `sfx_report.json`: at least one cue per
  layer, every anticipation cue leads the cut by ≥150 ms, no two major subs
  within 1.5 s, a vacuum on every major drop, bus peak ≤ −1 dBFS, bed LUFS
  within ±1 of `audio.music_bed_lufs`;
- mixes `sfx_bus.wav` (foreground only) and `music_bed.wav` (normalised,
  ducked 4.5 dB under foreground transients, 120 ms vacuum before drops);
- installs the procedural core under `sfx.library_dir/<install_subdir>/` with
  `--install-library` (default `assets/sfx/fenix_sfx/`, gitignored); the licensed
  library in that same folder is selected by `assets/sfx/library.json` roles.

`fenix render` uses the cue sheet only when `source.edit_plan_sha256` matches
the plan it is rendering. A stale sheet is a warning and falls back to the
legacy one-whoosh-per-cut bed, so a re-plan never silently renders old sound.

## 6. Render

Deterministic, single encode:

- source clips normalised once to exact 1080×1920 @ 60 fps CFR with the colour
  grade baked in, cached in `.cache/clips/`;
- frames streamed into x264 over a rawvideo pipe (no intermediate file);
- cuts laid out back-to-back from 0 s, the final shot extended to the voiceover;
- punch-in 1.12× → 1.0× over 0.16 s on every cut;
- static overlay blended per band, caption card with the spoken word in emerald;
- captions DP-chunked to ≤ 4 words / 26 chars on Whisper's sentence/clause flags,
  then a merge pass folds cards shorter than 0.45 s into a neighbour;
- audio prefers the fresh `fenix sfx` artifacts: the already-ducked/vacuumed
  music bed plus the layered SFX bus; the music is still sidechain-ducked under
  the voice, then two-pass `loudnorm` to −14 LUFS / −1.5 dBTP; AAC 128 kbps.
  Without a fresh cue sheet it falls back to the legacy bed at −28 LUFS with
  one air-swish per cut, peak pre-rolled onto the cut;
- H.264 **Main @ level 4.2** (level 3.1/4.0 are invalid for 1080×1920@60),
  `faststart`, CRF 18 / 3800k maxrate, automatic CRF 21 / 2500k fallback above
  the 15.5 MB WhatsApp budget.

Each render creates `output/<sku>/<sku>_<YYYYmmdd-HHMMSS>/` and indexes it in
`output/<sku>/runs.json` + `latest.json`. `--remix` copies the previous video
into a new run and rebuilds only the audio mix (~13 s). `--draft` uses
`veryfast`/CRF 23 and skips per-cut QA stills for review; it is not a delivery
master.
### Optional HyperFrames text layer

`--hf-overlay` moves the text/caption layer to HyperFrames (headless Chrome +
a paused GSAP timeline) and composites a transparent state sequence in the same
encode pass. It is opt-in and measured:

- text changes only at discrete moments (card appears, 100 ms slide, active word
  turns emerald), so `hf_overlay.py` renders **one PNG per unique state**
  (≈243 for a 28 s reel) at 10 fps instead of 1,680 frames;
- an `ffconcat` list holds each PNG for its real duration and is fed to FFmpeg
  as a concat input;
- measured: a full 243-state layer takes 28–46 s at 4 workers (warm/cold),
  and the whole render 118–164 s versus 139 s native, against 8–13 min for a
  per-frame VP9-alpha overlay;
- composite in RGB (`overlay=format=rgb`) then the default RGB→YUV conversion:
  the pill measured `rgb(3,149,105)` against the native `rgb(3,148,104)`;
  forcing BT.709 shifted it to `rgb(14,167,104)`;
- the cache key is the state list (layout + cards + word times), so a copy tweak
  re-renders the layer while a re-cut that leaves text alone reuses it.

`--hf-workers` defaults to 4. HyperFrames' low-memory profile pins to 1 worker
because this box reports 7.9 GB; 4 workers × ~414 MB PSS fits and is ~3.5×
faster, so the renderer passes `--no-low-memory-mode`.


## 6.5 Motion plates

`fenix motion <project>` renders `input/<project>/motion.json` (`docs/examples/motion.json`):
canvas/fps/duration, background, one SDF object (`brain`, `sphere`, `torus`,
`blob`), optional typography, grain and audio. A colour background produces
H.264 MP4; `"background": null` produces ProRes 4444 with alpha for compositing.
`--still 1.5` renders a review frame first. The object renderer is the motion
lab's raymarcher: signed-distance shape, tri-planar clay textures, soft studio
lighting.

## 6.6 Track (`fenix track`)

Template-matches a patch through a clip and writes normalised `nx`/`ny` points
to `output/<project>/track/track_<stamp>.json`. `fenix comp` consumes it through
a layer's `"track"` field, which overrides a linear `move`. It is deterministic
CPU template matching, not planar/optical-flow tracking; best on a locked-off
screen or a smoothly moving product. Spec: `docs/examples/track.json`.

## 6.7 Compositing (`fenix comp`)

One base + N layers in one FFmpeg graph: alpha MOV/PNG layers or chroma-keyed
footage, `rect` or `ellipse` masks, linear move (`from` → `to`) or tracked
`nx`/`ny`, scale/opacity and fade in/out. Spec: `docs/examples/comp.json`.
Output: `output/<project>/comp/<run>/comp.mp4` + manifest. This is the VFX
joining point: a `fenix motion` plate becomes a layer over footage, and a phone
screen can be replaced with a keyed insert. No rotation, blend modes or roto;
tracking is template matching.

## 6.8 FX (`fenix fx`)

Seeded, frame-accurate 2D effects streamed into FFmpeg: `particles`, `glitch`
and `shatter`. Spec: `docs/examples/fx.json`. Output:
`output/<project>/fx/<run>/fx.mp4` + manifest. Audio is copied from the source.
This is a texture/impact layer, not a 3D particle system or rigid-body
simulation.

## 6.9 Grade (`fenix grade`)

`lut3d` (`.cube`), `eq` primaries, temperature, **shot matching** (mean RGB of a
reference still vs the source, per-channel gains blended by `strength`, clamped
0.7–1.4) and **secondaries** (hue/saturation only near a key colour). Writes the
graded MP4, before/after stills and a manifest with the measured means/gains.
Spec: `docs/examples/grade.json`. Use `fenix scopes` for histograms/
vectorscopes/waveforms; there is no interactive scopes UI.

## 6.10 Scopes (`fenix scopes`)

Reads one frame (mid-clip or `--time`) and writes `histogram.png`,
`vectorscope.png`, `waveform.png`, `spectrum.png` (when audio) plus measured
`signalstats` to `output/<project>/scopes/scopes_<stamp>/`. `scopes.json` only
supplies `input`; `--time`/`--no-audio` are flags. This is the QC companion to
`grade`, with no delivery step.

## 6.11 Long-form (`fenix longform`)

Ordered chapters (existing MP4s or clips with `in`/`out`) are normalised to one
canvas/fps and joined by concat or `xfade` (13 transitions, `transition_sec`).
A `multicam` block expands synced angles + cut durations into chapters. Optional
global audio supports a J/L offset (`audio_offset`; negative = audio leads).
Writes `master.mp4`, `chapters.md` and a manifest. Spec:
`docs/examples/longform.json`. It is an assembly mode, not a dialogue editor:
no per-chapter audio, transcript-driven selection or multicam audio mixing yet.

## 7. QA gate

30 checks across container, audio, duration, edit, content and bookkeeping. See
[reference/qa-gates.md](reference/qa-gates.md). `--strict` treats warnings as
failures and is required for delivery.

## 8. Regression

Collects duration, cuts, size, sharpness, sustained window stats, beat offset,
caption CPS, loudness, QA counts and SSIM against the baseline video; diffs them
against `eval/<sku>_baseline.json` and fails on a regression. Sharpness is gated
by absolute floors; sustained quality, beat offset and QA counts are
directional. `--update-baseline` promotes a run once accepted.

## 9. Delivery

Copies the MP4, contact sheet, QA report, caption audit and `*.md` notes into
`delivery.dir` from `config/pipeline.json`, under the run name, and records
`delivered_to` in `runs.json`. The repo stays canonical; the delivery folder is a
shelf, never a second source of truth.
