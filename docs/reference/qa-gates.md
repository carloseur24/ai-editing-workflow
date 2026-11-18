# QA gates

`fenix qa` writes `output/<sku>/<run>/qa_report.json` — local evidence. QA never
writes the delivery shelf: for a delivered master it reads the master and writes
the report to the local run when it exists, else to
`output/<sku>/qa/<run>/qa_report.json` (`finish` reuses the same path).

```json
{"video": "...", "pass": true, "errors": 0, "warnings": 0,
 "checks": [{"id": "v_level", "measured": "level=42", "threshold": "4.2",
             "ok": true, "severity": "error", "fix": "..."}]}
```

The good run has **30 base checks** plus one `w_layout` row per render-time
layout warning. `--strict` makes every warning a failure; delivery requires it.
`fenix finish` reuses a strict PASS for the same video bytes (size + mtime
fingerprint); `--re-qa` forces a fresh pass.

A **music-only** piece (`sku.voiceover: null`/`"none"`) has no voiceover,
transcript or caption stream. QA drops `c_timings`, `d_script_match`,
`d_tail_complete` and the `c_cps_*` / `c_caption_*` rows, records the skipped ids
in `qa_report.json → skipped_checks`, and adds one informational row
`a_music_only`. `d_matches_vo` stays: it compares the video against the run's
planned `duration_sec` (the label reads `plan`). Loudness, true peak, clipping,
black frames, size, container and `c_clip_identity` are unchanged.

## Container

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `v_resolution` | error | 1080×1920 | render at the canvas size |
| `v_fps` | error | 60 fps | re-encode with the project fps |
| `v_pix_fmt` | error | `yuv420p` | re-encode, not a remux |
| `v_profile` | error | Main | re-encode with `encoding.profile` |
| `v_level` | error | 4.2 (level 42) | 1080×1920@60 is invalid at 3.1/4.0 |
| `v_faststart` | error | `moov` before `mdat` | re-mux with `-movflags +faststart` |
| `v_size_budget` | error | ≤ 15.5 MB | the renderer falls back to CRF 21 / 2500k |

## Audio

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `a_stream` | error | AAC 48 kHz stereo | encode with `encoding.audio_*` |
| `a_lufs` | error | −14 ± 1 LUFS | inspect the mix; re-run `--remix` |
| `a_true_peak` | error | ≤ −1.0 dBTP | lower `audio.limiter_ceiling` (0.78 = −2.16 dBFS) |
| `a_clipping` | error | 0 full-scale samples | lower the mix gain |

## Sound design (separate stage)

`fenix sfx` is not part of `fenix qa`, because it gates the **plan** before a
three-minute render. It writes `output/<sku>/sfx_report.json` with the same row
shape:

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `sd_cues` | error | ≥ 8 foreground cues | run `fenix sfx` after locking the plan |
| `sd_layers` | error | anticipation + tactile + impact | add the missing layer |
| `sd_jcut` | error | every anticipation cue leads the cut by ≥ `jcut_min_ms` | start the riser/whoosh earlier |
| `sd_sub_gap` | error | ≥ `sub_spacing_sec` between major subs | drop or move a sub hit |
| `sd_vacuum` | warn | 80–150 ms before every major drop | set `vacuum_ms` |
| `sd_peak` | error | ≤ −1.0 dBFS | lower the bus gain |
| `sd_bed` | warn | target LUFS ± 1 | re-run the stage |
| `sd_bounds` | error | cue starts between 0 and duration | move the cue |

`fenix render` copies the cue sheet and this report into the run directory, so
the delivered evidence shows exactly which sound design the master used.

## Post-master QA profile — `fenix qa --post`

A comp/fx/grade master has already consumed the edit plan, the voiceover and the
captions, so the 30-check edit gate no longer applies. `fenix qa --post
<comp-run>` (or `--post --latest <sku>`) runs 19 post-master rows and writes
`qa_report.json` into the comp run (local, never the shelf):

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `pm_manifest` | warn | `manifest.json` present with a layer count | run `fenix comp` so the run carries its executed spec |
| `v_codec` / `v_resolution` / `v_fps` / `v_pix_fmt` / `v_profile` / `v_level` | error | h264 Main 4.2, comp canvas, `yuv420p` | re-run the stage with the shared encoding block |
| `v_faststart` | error | `moov` before `mdat` | re-mux with `-movflags +faststart` |
| `v_size_budget` | error | ≤ 15.5 MB | the stage's CRF/bitrate fallback |
| `pm_duration` | error | within 0.35 s of the comp spec / base plan | the graph trimmed a different duration |
| `q_run_versioned` | warn | `<sku>_<stamp>/<stage>.mp4` | run the stage into its run directory |
| `pm_base` | info | base `edit_plan.json` readable | point `comp.json` at `@render/latest` |
| `a_stream` / `a_lufs` / `a_true_peak` / `a_clipping` | error | AAC 48 kHz stereo, −14 ± 1 LUFS, ≤ −1.0 dBTP, 0 full-scale | fix the comp audio args / limiter ceiling |
| `v_black_frames` | error | darkest sampled mean luma ≥ 12 | layer opacity/fades crushed the picture |
| `pm_cut_identity` | error | corr ≥ 0.75 per base cut | a layer replaced the base shot or the base changed |
| `pm_cut_fidelity` | warn | corr ≥ 0.90 per base cut | inspect layer opacity/key/mask |

`fenix make --post comp` runs strict edit QA first, then comp and this profile
before delivering; a comp master cannot bypass the edit gate.

## Post-production gates

`fenix motion`, `fenix track`, `fenix comp`, `fenix fx`, `fenix grade`,
`fenix scopes` and `fenix longform` do **not** run the 30-check edit QA.
`motion`/`comp`/`fx`/`grade`/`longform` validate their spec before encoding and
write `manifest.json`; `track` writes points; `scopes` writes QC stills/stats.
`fenix tts` has no gate beyond a successful provider response and a playable
file. Acceptance is:

- the spec passed validation (source/base/layer/reference/chapter paths exist);
- the manifest exists and the measured duration/layers/filters match the spec;
- `ffprobe` reports the expected codec, size and duration (alpha for a motion
  MOV when requested);
- `track` points have a useful match score and `comp` consumes the JSON;
- `scopes` produced the requested stills and `signalstats` were read;
- `tts` produced a playable voice file and the duration/pace were listened to;
- a human/agent watched the result (and for grade, the `before.png`/`after.png`).

Field tables: [specs.md](specs.md). Delivery is `--deliver`, which copies the
media and manifest to the delivery shelf and Downloads.

## Duration and timing

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `c_timings` | error | `word_timings.json` non-empty | `fenix align <sku>` |
| `d_script_match` | warn | similarity ≥ 0.55 | check the script/voiceover match |
| `d_tail_complete` | error | ≥ 0.03 s trailing silence | re-run align so the last phrase completes |
| `q_run_versioned` | warn | `<sku>_<stamp>/<run>_final.mp4` | re-render into a run directory |
| `d_matches_vo` | error | within 0.35 s of the VO (or of the plan for music-only) | re-render or re-align |
| `d_max` | error | ≤ 30.0 s (28.5 + 2.0 allowance) | shorten the script |

## Edit and content

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `p_cuts` | error | > 0 | render produced no cuts |
| `p_in_point` | error | ≥ 0.60× clip median, min/mean reported | pick a scored candidate |
| `p_window_sharp` | error | ≥ 0.60× median over the whole shot | re-plan with window scoring |
| `p_window_soft` | warn | ≤ 30 % of each shot under floor | move the in-point |
| `p_beat_offset` | warn | ≤ 60 ms | regenerate the plan on the measured grid |
| `p_beat_hard` | error | ≤ 120 ms | same |

With `quality.beat_policy = picture` both beat rows stay in the report as
`info`: the offsets are measured and printed but never fail a piece that cuts on
action (cinematic long takes, documentary, physical comedy).
| `c_clip_identity` | error | corr ≥ 0.90 for every shot | the renderer showed the wrong source clip |
| `c_cps_max` | warn | ≤ 20 CPS (or `allow_fast_captions`) | merge/shorten cards |
| `c_cps_fail` | warn | ≤ 25 CPS (or `allow_fast_captions`) | same |
| `c_caption_text` | error | non-empty, ≤ 29 chars, no Unicode check | fix `display_map`/`display_merge` |
| `c_caption_zone` | error | ≤ 885 px wide, font ≥ 24 px | shorten the copy or widen the zone deliberately |
| `c_caption_dupes` | warn | 0 repeated consecutive cards | fix chunking |
| `a_music_only` | info | VO/script/caption rows skipped for a music-only piece | not a failure; see `skipped_checks` |

## HyperFrames overlay (only with `render --hf-overlay`)

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `o_alpha` | error | decoded RGBA state has a transparent footage area and visible ink | rebuild the layer: `fenix render <sku> --hf-overlay` |
| `o_pill_colour` | error | max channel Δ ≤ 12 vs `colors.emerald_primary` | composite in RGB; never force a second colour matrix |

These two rows are added only for overlay runs, so a native render still reports
the documented 30 checks.

## Render warnings

| ID | Severity | Threshold | Fix |
|---|---|---|---|
| `w_layout` | warn | 0 warnings | the row's `fix` is the renderer's own message (text too wide, fast captions, …) |

## How to read a failure

1. The `measured` column is the fact; `threshold` is the contract; `fix` is the
   action.
2. `severity: error` blocks shipping; `warn` blocks `--strict` delivery.
3. Fix the source: `sku.json`, `picks.json`, `config/pipeline.json`, or the
   stage artifact. Never edit the MP4 or hand-write `qa_report.json`.
4. Re-run only the failing stage and then QA.

## Accepted exceptions

- `allow_fast_captions: true` in `sku.json` downgrades both CPS rows to
  warnings. Use only when the copy is deliberately dense; record why.
- `--allow-soft-in` ships a deliberately soft shot; QA records the ratio. Use
  only when the softness is intended.
- `d_max` follows `quality.max_duration_sec` + `voiceover.max_extra_sec`; a
  complete sentence may run long, an unfinished one may not.
