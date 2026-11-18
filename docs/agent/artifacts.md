# Artifacts and output locations

There are seven storage classes. Know which one you are writing before you touch
anything.

| Class | Path | Rule |
|---|---|---|
| Source | `input/<sku>/` | human/agent intent + raw media; never overwrite generated files by hand |
| Project config | `input/<sku>/config.json` | optional sparse overlay: project-only keys, deep-merged over global/local |
| Derived | `output/<sku>/` | tool output; safe to delete and regenerate |
| Runs | `output/<sku>/<sku>_<stamp>/` | immutable evidence for one render |
| Runtime assets | `assets/fonts`, `assets/motion/*.png`, `assets/sfx/library.json`, `assets/hf` | tracked; read-only during a job |
| Local media | `assets/music/*.wav`, `assets/sfx/*.wav`, `assets/sfx/fenix_sfx/`, `.cache/models/` | licensed/generated, gitignored; provision locally |
| Cache | `.cache/` | disposable; never required, never committed |
| Label work | `<scanned-folder>/.fenix-label/` | disposable footage intake; never commit |

The full asset inventory and missing-file behavior live in
[`assets/README.md`](../../assets/README.md).

## Source files — `input/<sku>/`

| File | Written by | Read by | Notes |
|---|---|---|---|
| `brief.json` | human / `fenix storyboard` | storyboard, agent | intake facts, format, framework, decisions; see [specs](../reference/specs.md) |
| `config.json` | human/agent | every project-aware command | optional sparse pipeline overlay (duration, cuts, beat policy, safe zones, `post` flags); see [config](../reference/config.md#project-overlay-inputprojectconfigjson) |
| `motion.json` | human/agent | `fenix motion` | plate spec; see [specs](../reference/specs.md) |
| `track.json` | human/agent | `fenix track` | template-tracking spec; see [specs](../reference/specs.md) |
| `comp.json` | human/agent | `fenix comp` | compositing spec; see [specs](../reference/specs.md) |
| `fx.json` | human/agent | `fenix fx` | particles/glitch/shatter spec; see [specs](../reference/specs.md) |
| `grade.json` | human/agent | `fenix grade` | grade/shot-match/secondary spec; see [specs](../reference/specs.md) |
| `scopes.json` | human/agent | `fenix scopes` | QC input path; see [specs](../reference/specs.md) |
| `longform.json` | human/agent | `fenix longform` | chapter/xfade/multicam spec; see [specs](../reference/specs.md) |
| `tts.json` | human/agent | `fenix tts` | provider/text/voice spec; see [specs](../reference/specs.md) |
| `clips/*.mp4` | human | frames, plan, render | vertical footage; 1080×1920 preferred |
| `voice.mp3` | human / `fenix tts` | align | name must match `sku.json → voiceover`; `tts` is the only stage that writes into `input/` by design |
| `sku.json` | human/agent | lint, align, plan, render, QA | copy, badges, price, footer, display maps, `voiceover`/`duration_sec`, `text_cards` |
| `picks.json` | human/agent | plan | optional creative choice per line (ignored by a locked shot order) |
| `candidates.json` | `fenix frames` | plan, validate, render | scored windows with stable IDs; do not hand-edit IDs |
| `edit_plan.json` | `fenix plan` (or hand-authored) | validate, sfx, render | the cut contract; `locked: true` means the order comes from `brief.story.shots`; a plan with cuts and no planner stamp is hand-locked and only `--force` replaces it |
| `keyframes/` | `fenix frames` | agent review | 1 fps JPEGs + `keyframe_report.json` |
| `word_timings.json` | `fenix align` | plan, sfx, render, QA | word-level timestamps + `source` voice/script fingerprint |
| `voice_28s.wav` | `fenix align` | render | tempo-fitted voiceover |
| `_voice_clean.wav` | `fenix align` | align | dead-air-trimmed intermediate |
| `.asr_cache.json` | `fenix align` | align | transcript cache by audio hash |

`sku.json` fields:

```json
{
  "title": "iPhone 14 • 128GB",
  "subtitle": "Azul • eSIM • 77% Batería",
  "badges": ["Forro Protector Incluido", "Garantía 1 Mes"],
  "price_label": "$330 USDT",
  "footer": "@fenixsoft • Mercado Social Local C-29",
  "safe_zone_mode": "reels_universal",
  "voiceover": "voice.mp3",
  "script_text": "¿Un iPhone 14 por 330 dólares en Maracay? ...",
  "display_map": {"14X330": ["14", "×", "330"]},
  "display_merge": {"ES SIM": "E-SIM"}
}
```

`display_map` splits/replaces one ASR token; `display_merge` joins two tokens.
They change captions only — word timings stay intact.

`voiceover: null` (or `"none"`) is a music-only piece: no `voice_28s.wav`, no
`word_timings.json`, no captions; the length comes from `sku.duration_sec` →
`brief.duration_sec` → config. `text_cards[]` are native timed title/spec cards
drawn by the edit renderer (title white, spec mint) — no motion plate or comp.

`edit_plan.json` also carries `config_slices` (`picture`/`audio` digests). The
renderer writes them into the run, and `fenix make` uses them to choose a full
render vs an audio-only remix without guessing from the whole config mtime.

## Derived artifacts — `output/<sku>/`

| File | Written by | Purpose |
|---|---|---|
| `storyboard.json` | storyboard | timed scenes, framework, format and missing facts; `order` carries the locked shot table |
| `storyboard.md` | storyboard | human-readable version of the same plan |
| `shot_order.json` | storyboard | validated `brief.story.shots`: `locked`, `order_source`, per-shot rows, `pass` (only when the brief has a shot table) |
| `text_cards.json` | storyboard | one native title/spec card per locked shot, derived from `label`/`spec`; render loads it only when `sku.text_cards` is absent |
| `preview/<stamp>/` | render/comp `--preview` | stills + `contact_sheet.png` + `preview.json`; review artifacts, never runs |
| `script_lint.json` | lint | word budget, tempo, phonetics, display coverage |
| `beat_grid.json` | beats | measured BPM, beat times, phase, drift |
| `narration_lines.json` | align | timed sentences/clauses the planner consumes |
| `plan_edit.json` | plan | per-line → clip/candidate table, scores, notes |
| `plan_validation.json` | validate | pre-render rows with `measured`/`threshold`/`fix` |
| `sfx_cue_sheet.json` | sfx | every event: time, layer, role, source, gain, lead, vacuum |
| `sfx_report.json` | sfx | cue counts, bus/bed levels and `{id, measured, threshold, pass, fix}` rows |
| `sfx_bus.wav` | sfx | foreground SFX only, 48 kHz stereo PCM24 |
| `music_bed.wav` | sfx | music bed normalised, ducked under foreground transients, vacuumed |
| `runs.json` | render / deliver | append-only index of every run |
| `latest.json` | render / deliver | pointer to the newest completed run |
| `.state/<stage>.json` | `fenix make` | last config-slice digest per stage; controls skip/run without mtime guessing |

Legacy flat files (`output/<sku>/<sku>_final.mp4`, `qa_report.json`, …) may exist
from before the run directory. They are readable but are **not** runs and QA's
`q_run_versioned` warns about them. Re-render to get a proper run.

## Run directory — `output/<sku>/<sku>_<YYYYmmdd-HHMMSS>/`

| File | Written by | Purpose |
|---|---|---|
| `<run>_final.mp4` | render | the piece |
| `edit_plan.json` | render | the plan actually executed |
| `caption_audit.json` | render | every card: text, start/end, CPS, font, ink width |
| `word_timings.json` | render | snapshot of the alignment used, so QA stays reproducible after the VO changes |
| `sfx_cue_sheet.json` | sfx → render | the sound design actually consumed by this run |
| `sfx_report.json` | sfx → render | sound-design gate evidence kept with the master |
| `contact_sheet.png` | render | one frame per cut |
| `qa_frames/*.png` | render | full-resolution review frames |
| `qa_report.json` | qa | 30 rows (32 with `--hf-overlay`) with measured/threshold/fix; local evidence, never written to the delivery shelf |
| `delivery.json` | deliver | where and when this run was copied, plus `copied`/`skipped` |
| `delivery_readme.md` | deliver | generated hand-off note: duration, shot table, audio mode, file list |
| `*.md` | human/agent | hand-off notes; copied by deliver |

A run is named by its SKU and creation time. Same-second renders get `-2`,
`-3`. Nothing ever overwrites an existing run directory.

**Only delivery commands write the delivery shelf.** `qa --latest` and
`qa --post` read a delivered master but write their report to the local run
(`output/<sku>/<run>/qa_report.json`) or, when the run directory is gone, to
`output/<sku>/qa/<run>/qa_report.json`. `finish` reads the same path.

## Post-production outputs

These are **not** edit runs and are not indexed in `runs.json`. Most validate
their spec and write a manifest; `track` writes only tracking JSON, `scopes`
writes QC stills + stats, and `tts` writes a source voice file. `motion`,
`comp`, `fx`, `grade` and `longform` accept `--deliver` to copy the result and
manifest to the delivery shelf and Downloads. There is no 30-check QA gate; check
the manifest/ffprobe, then watch the result.

| Path | Written by | Contents |
|---|---|---|
| `output/<p>/motion/<run>/plate.mp4` | motion | opaque H.264 plate |
| `output/<p>/motion/<run>/plate.mov` | motion | ProRes 4444 with alpha when `background: null` |
| `output/<p>/motion/<run>/motion.json`, `manifest.json` | motion | executed spec + duration/frames/alpha |
| `output/<p>/motion/still.png` | motion `--still` | review frame (or `--out`) |
| `output/<p>/track/track_<stamp>.json` | track | fps/size/start/template + `points[]` (`t,x,y,nx,ny,score`) |
| `output/<p>/comp/<run>/comp.mp4` | comp | composited base + layers (shared encoding + budget fallback) |
| `output/<p>/comp/<run>/comp.json`, `manifest.json` | comp | executed spec, layer count/elapsed, `fingerprint` + resolved `inputs` for `--reuse` |
| `output/<p>/comp/<run>/delivery.json` | comp `--deliver` | shelf/Downloads paths and the delivered file list |
| `output/<p>/preview/<stamp>/frame_*.png`, `contact_sheet.png`, `preview.json` | render `--preview` | stills at cut midpoints + card first frames |
| `output/<p>/preview/comp_<stamp>/frame_*.png`, `contact_sheet.png`, `preview.json` | comp `--preview` | layer placement at the base cut midpoints |
| `output/<p>/fx/<run>/fx.mp4` | fx | effected clip |
| `output/<p>/fx/<run>/fx.json`, `manifest.json` | fx | executed spec + effects/frames/elapsed |
| `output/<p>/grade/<run>/graded.mp4` | grade | graded video |
| `output/<p>/grade/<run>/grade.json`, `manifest.json` | grade | executed spec + filters/means/secondaries |
| `output/<p>/grade/<run>/before.png`, `after.png` | grade | review stills at the match sample frame |
| `output/<p>/scopes/scopes_<stamp>/histogram.png`, `vectorscope.png`, `waveform.png`, `spectrum.png` | scopes | QC stills (spectrum only when audio) |
| `output/<p>/scopes/scopes_<stamp>/scopes.json` | scopes | input info, file status, measured `signalstats` |
| `output/<p>/longform/<run>/master.mp4` | longform | concatenated/xfaded chapters |
| `output/<p>/longform/<run>/longform.json`, `manifest.json` | longform | executed spec + chapter count/duration |
| `output/<p>/longform/<run>/chapters.md` | longform | chapter table with in/out times |

`--out` overrides the media path; the run directory and its JSON evidence are
still written.

## Evaluation — `eval/`

| File | Purpose |
|---|---|
| `<sku>_baseline.json` | accepted reference metrics for regression |
| `<sku>_<stamp>.json` | one report per eval run |
| `*_first_vs_latest.md` | human-readable diff (optional) |

## Cache — `.cache/`

| Path | Contents |
|---|---|
| `models/` | faster-whisper `small` (HuggingFace layout, ~460 MB) |
| `clips/` | normalised 1080×1920@60 clips |
| `clip_quality/` | hash-cached sharpness/luma/motion scans |
| `beat_grid/` | measured grids by music hash |
| `hf_overlay/` | HyperFrames PNG states by caption-state hash |

Deleting `.cache/` is always safe; the next command pays the rebuild cost.

## Label work — `<folder>/.fenix-label/`

Created by `fenix label scan`, consumed by `apply`/`undo`. It lives inside the
scanned footage folder, is hidden and disposable. Naming grammar and duplicate
rules: [labeling.md](labeling.md).

| Path | Written by | Purpose |
|---|---|---|
| `scan.json` | label scan | ffprobe/image facts, perceptual hashes, duplicate flags, sheet paths |
| `sheets/*.jpg` | label scan | 4-frame contact sheet per video |
| `grids/*.jpg` | label scan | 8-up vision grids for human/agent review |
| `names.tsv` | label scan, human | index + label to apply |
| `renames-*.csv` | label apply | what moved, bytes, mtime |
| `undo-*.sh` | label apply | shell reverse of that apply |

## Where do I write?

- New copy or caption fix → `input/<sku>/sku.json`.
- Shot choice → `input/<sku>/picks.json`.
- New intent/spec → `input/<project>/{brief,motion,track,comp,fx,grade,scopes,longform,tts}.json`.
- Footage labels → `<folder>/.fenix-label/names.tsv` via `fenix label`.
- A new report or diagnostic → `output/<sku>/` with a stage-named `.json`.
- A job card → `fenix job`; it is read-only and writes no artifact.
- A render → only through `fenix render` (never create a run directory by hand).
- One-shot edit chain → `fenix make`; it writes the same artifacts as the stages.
- Post output → the stage's own `output/<project>/<stage>/<run>/`.
- Delivery → only through `fenix deliver` / `--deliver`.
- Documentation → `docs/`.
- Anything else at the repo root → stop; it belongs in a package layer (see
  [growth.md](growth.md)).

## Naming rules

- SKU folder: lowercase `snake_case` (`iphone14_blue`, `demo_iphone14_blue`).
- Run: `<sku>_<YYYYmmdd-HHMMSS>`; never rename a run after creation.
- Artifacts: `<stage>.json` (`script_lint.json`, `plan_validation.json`).
- No spaces in repo paths. The Windows delivery folder may contain spaces; only
  `deliver.py` writes there.

## After `fenix finish`

The delivery shelf keeps the full evidence. The local run keeps only JSON
(`edit_plan.json`, `caption_audit.json`, `qa_report.json`, `delivery.json`,
`word_timings.json`, `sfx_cue_sheet.json`, `sfx_report.json`); `runs.json`
points at the delivered master. `qa --latest`
and `runs` resolve that path, and QA rebuilds the normalised clip cache if it
was pruned.
