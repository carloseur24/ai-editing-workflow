# Config reference — `config/pipeline.json`

One file holds every number that policy depends on. Code reads it with safe
defaults; a missing key does not crash a render. Relative asset paths resolve
against the repo root.

`config/pipeline.json` is portable and public. Machine-specific paths (a Windows
delivery shelf, a personal SFX library) go in `config/pipeline.local.json`, which
is gitignored and deep-merged over the base when it exists. A sparse
`input/<project>/config.json` is then merged on top: project intent wins over
both the global defaults and the local file. An explicit `--config <other>.json`
replaces the base and skips the local file, but the project overlay still
applies. `fenix doctor` prints whether the local overlay was merged and which
projects carry a config; `fenix job` prints the effective values.

```json
{
  "version": 4,
  "canvas": {"width": 1080, "height": 1920, "fps": 60},
  "target_duration_sec": 28.0
}
```

## Project overlay (`input/<project>/config.json`)

Only the keys where the piece differs from the defaults. The merge is deep, so
one key does not erase its siblings; a `null` value removes an inherited key
(for example `{"audio": {"music_only": null}}`).

```json
{
  "target_duration_sec": 33.0,
  "cuts_per_video": 5,
  "quality": {"in_point_floor": 0.25, "beat_policy": "picture"},
  "transitions": {"enabled": true, "default": "dissolve", "sec": 0.2},
  "post": {"motion": false, "comp": false}
}
```

Freshness sees the effective merged config: `make` compares the render's stored
`config_slices` against the merged picture/audio keys, and stage stamps hash the
sections each stage reads. A project config change therefore invalidates only
what it actually touches.

## Blocks

| Block | Keys | Read by | Meaning |
|---|---|---|---|
| `version` | — | humans | config schema version; a breaking rename bumps it |
| `canvas` | `width`, `height`, `fps` | render, QA | output canvas; 1080×1920@60 is required for the level 4.2 profile |
| `target_duration_sec` | — | lint, align, plan, render | nominal piece length (33 s in this checkout; the brief can set another) |
| `bpm` | — | beats, render | nominal tempo label; the measured grid wins |
| `cuts_per_video` | — | plan, render | target cut count (5) |
| `cut_snap_to_beat` | — | plan | snap boundaries to measured beats |
| `min_cut_sec` | — | plan, validate | shortest allowed shot (1.2 s) |
| `shots` | `source_tolerance_sec`, `sum_tolerance_sec` | storyboard, plan, job | ordered `story.shots` validation: source overrun slack and how far the shot sum may drift from `duration_sec` |
| `script` | `spoken_wpm`, `max_tempo`, `target_sec` (optional; falls back to `target_duration_sec`) | lint | word budget and pacing ceiling |
| `quality` | `in_point_floor`, `beat_offset_warn_ms`, `beat_offset_fail_ms`, `caption_warn_cps`, `caption_fail_cps`, `max_duration_sec`, `beat_policy`, `bpm_tolerance` | validate, render, QA, beats | content and timing floors; `beat_policy` (`music` default / `picture`) and the requested-vs-measured BPM tolerance |
| `punch` | `enabled`, `duration_sec`, `scale`, `ease` | render | punch-in on every cut; `ease` is reserved (renderer currently uses out-cubic) |
| `transitions` | `enabled`, `default`, `sec` | render | pre-roll picture blend into each cut: `default` kind (`dissolve`, `fadeblack`, `wipeleft`…), length `sec` clamped to half the shorter shot; off by default; `--transition`/`--no-transition` override one render |
| `post` | `motion`, `comp` | `make --post comp`, `job` | project-owned post choice (defaults `true`); `motion:false` skips the plate, `comp:false` skips the whole post chain (comp, post QA and post delivery) and leaves the edit master as the product — the dry-run's `deliver` row is SKIP, and edit delivery stays a separate `make`/`finish` |
| `layout` | `bottom_stack_shift_pct` | render | shift the price/caption/footer stack by a fraction of canvas height |
| `grade` | `mode` (`baked`/`per_frame`), `lightness_alpha`, `lightness_beta` | render | LAB lightness boost; baked into the clip cache or applied per frame |
| `encoding` | `codec`, `preset`, `profile`, `level`, `pix_fmt`, `crf`, `maxrate`, `bufsize`, `fallback_crf`, `fallback_maxrate`, `audio_codec`, `audio_bitrate`, `audio_sample_rate`, `max_whatsapp_bytes`, `faststart` | render, comp, grade, longform, QA | H.264/AAC contract and WhatsApp fallback; post stages read the same block |
| `audio` | `target_lufs`, `true_peak_db`, `limiter_ceiling`, `lra`, `music_bed_lufs`, `duck_ratio`, `duck_threshold`, `duck_attack_ms`, `duck_release_ms`, `music_fade_in_sec`, `music_fade_out_sec`, `sfx_gain_db`, `sfx_fade_sec` | render, QA | mix and loudness |
| `music` | `file`, `bpm` | beats, render | bed path and nominal BPM |
| `sfx` | `file`, `on_every_cut`, `align_peak_to_cut`, `mode` (`cue_sheet`/`legacy`), `library_dir`, `library_manifest`, `install_subdir`, `cue_sheet_file`, `bus_file`, `music_bed_file`, `duck_db`, `duck_attack_ms`, `duck_release_ms`, `vacuum_ms`, `jcut_min_ms`, `sub_spacing_sec`, `bus_peak_db`, `min_cues` (optional, default 8) | `sfx`, render | cue-sheet sound design, library location, ducking/vacuum policy and legacy air-swish fallback; the cue floor is `ceil(duration/2)` capped by `min_cues`, so a 6 s piece needs 3 |
| `colors` | `emerald_primary`, `emerald_flash`, `text_white`, `text_mint`, `pill_dark` | render | palette |
| `safe_zones` | `reels_universal`, `status_story` | render, QA | text coordinates |
| `typography` | `title_size`, `subtitle_size`, `badge_size`, `price_size`, `footer_size`, `caption_size`, `min_size` | render | font sizes; auto-fit may shrink to `min_size` |
| `kinetic_captions` | `font_size`, `max_words_per_chunk`, `max_chars_per_chunk`, `active_flash_ms`, `pill_settle_ms`, `phrase_enter_ms`, `hold_last_gap_ms` | render | caption chunking and timing |
| `text_cards` | `y`, `size`, `sub_size`, `color`, `sub_color`, `enter_sec`, `exit_sec`, `rise_px`, `sub_gap_ratio`, `shadow` | render | defaults for native timed title/spec cards (`sku.text_cards`) |
| `audio.music_only` | `bed_lufs`, `sfx_dbfs` | render | music-only preset: bed loudness and foreground bus peak (no voiceover) |
| `preview` | `max_stills`, `tile_width`, `tile_height`, `columns` | render/comp `--preview` | stills budget and contact-sheet grid for previews |
| `alignment` | `engine` (`auto`/`faster-whisper`/`energy`), `model`, `compute_type`, `language`, `beam_size`, `cpu_threads`, `trim_tail_to_script`, `min_script_similarity` | align, QA | ASR settings; `auto` falls back to energy alignment when faster-whisper is unavailable |
| `voiceover` | `silence_threshold_db`, `min_silence_sec`, `keep_pad_sec`, `max_tempo`, `crossfade_sec`, `end_pad_sec`, `tail_extension_max_sec`, `tail_pad_sec`, `max_extra_sec` | align, render, QA | voice prep and tail allowance |
| `delivery` | `dir`, `downloads`, `copy`, `copy_to_downloads`, `prune_local_media` | finish/deliver | delivery shelf, flat Downloads copy, prune policy |
| `labeling` | `jobs`, `sheet_tile_width`, `grid_cols`, `grid_rows`, `grid_cell_width`, `duplicate_threshold`, `name_max_len` | `label` | footage scan parallelism, vision-grid layout, duplicate hash threshold and filename length |

## The values that actually gate a piece

| Key | Default | Effect |
|---|---|---|
| `script.spoken_wpm` | 162 | measured natural pace of the demo voice |
| `script.max_tempo` | 1.12 | pacing ceiling; the word budget is derived from it |
| `quality.in_point_floor` | 0.25 | in-point must be ≥ 25 % of the clip median sharpness |
| `quality.beat_offset_warn_ms` / `fail_ms` | 350 / 400 | cut-to-beat distance |
| `quality.caption_warn_cps` / `fail_cps` | 20 / 25 | reading speed |
| `quality.max_duration_sec` | 33.5 | hard duration ceiling; the brief and voice tail set the target |
| `quality.beat_policy` | `music` | `music` gates cut-to-beat distance; `picture` reports it as info (cinematic long takes, action cuts) |
| `quality.bpm_tolerance` | 1.5 | `fenix beats` warns when the measured BPM drifts more than this percent from the requested BPM |
| `audio.limiter_ceiling` | 0.78 | −2.16 dBFS limiter ceiling; leaves room for inter-sample true peaks under the −1.0 dBTP gate |
| `voiceover.max_tempo` | 1.30 | hard atempo cap |
| `voiceover.tail_pad_sec` | 11.176 | silence used to fill the tail; tune it to the target duration |
| `encoding.max_whatsapp_bytes` | 16252928 | 15.5 MB → CRF 21 / 2500k fallback |
| `sfx.jcut_min_ms` | 150 | every anticipation cue must start at least this early (`sd_jcut`) |
| `sfx.sub_spacing_sec` | 1.5 | minimum gap between two major sub hits (`sd_sub_gap`) |
| `sfx.vacuum_ms` | 120 | bed mute before a major drop; the gate accepts 80–150 (`sd_vacuum`) |
| `sfx.bus_peak_db` | -16.0 | target for the foreground bus; the gate fails above −1.0 dBFS (`sd_peak`) |
| `sfx.duck_db` | -4.5 | music bed duck depth under foreground transients |
| `sfx.library_dir` | `assets/sfx` | SFX library root, relative to the repo; `--library` overrides it. Point this at a personal library in `pipeline.local.json`. |
| `labeling.jobs` | 8 | parallel ffprobe/ffmpeg workers; clamped to CPU count |
| `labeling.duplicate_threshold` | 8 | max Hamming distance for a perceptual duplicate |
| `labeling.name_max_len` | 80 | Windows-safe filename stem limit |
| `labeling.grid_cols` / `grid_rows` | 4 / 2 | 8-up vision grid layout |
| `labeling.sheet_tile_width` | 400 | contact-sheet tile width |
| `labeling.grid_cell_width` | 300 | vision-grid cell width |
| `delivery.dir` | `output/delivery` | delivery shelf; set a Windows path in `pipeline.local.json` when WSL is only pre-production |
| `delivery.downloads` | `output/delivery_downloads` | flat master + contact sheet |
| `delivery.copy_to_downloads` | `true` | also copy flat into Downloads |
| `delivery.prune_local_media` | `true` | delete local MP4/sheet/QA frames after a verified copy |
| `shots.sum_tolerance_sec` | 0.25 | how far the `story.shots` durations may sum from `duration_sec` before the order lock fails |
| `shots.source_tolerance_sec` | 0.02 | slack allowed on `in + dur` against the measured source duration |
| `text_cards.y` / `size` / `sub_size` | 250 / 86 / 34 | default card block position and font sizes (px); per-card fields win |
| `text_cards.enter_sec` / `exit_sec` / `rise_px` | 0.45 / 0.30 / 24 | fade-and-rise timing for native title/spec cards |
| `text_cards.color` / `sub_color` | `#FFFFFF` / `#6EE7B7` | white title, mint spec; per-card fields win |
| `text_cards.sub_gap_ratio` / `shadow` | 0.30 / `true` | gap between the two lines (× title size), soft drop shadow |
| `transitions.enabled` / `default` / `sec` | `false` / `dissolve` / 0.35 | opt-in pre-roll blend at each cut; a transition replaces the punch-in on its incoming shot |
| `transitions.default` kinds | `dissolve`, `fadeblack`, `wipeleft`, `wiperight`, `wipeup`, `wipedown` | `cut`/`none` in a cut's `transition` field forces a hard cut |
| `preview.max_stills` | 24 | still cap for `render --preview` (cut midpoints are kept first) |
| `preview.tile_width` / `tile_height` / `columns` | 270 / 480 / 4 | preview contact-sheet tile and grid |
| `audio.music_only.bed_lufs` | −18.0 | bed loudness when `sku.voiceover` is `null`/`"none"` |
| `audio.music_only.sfx_dbfs` | −16.0 | foreground SFX bus peak for a music-only mix |

## Accepted values and fallbacks

| Key | Accepted values / behavior |
|---|---|
| `grade.mode` | `baked` (default; grade applied once to the normalised clip cache) or `per_frame` (grade applied while streaming). Any other value disables both. |
| `sfx.mode` | `cue_sheet` (default; consume `fenix sfx` artifacts) or `legacy` (one air-swish per cut). |
| `punch.ease` | reserved. The renderer currently implements out-cubic easing for every punch. |
| `alignment.engine` | `auto` (default), `faster-whisper`, `energy`. `auto` uses ASR when available and falls back to the 300–3400 Hz energy valley aligner. |
| `script.target_sec` | optional lint target; falls back to top-level `target_duration_sec`. |
| `music.file` | if the path does not exist, `beats`/`sfx`/`render` use the first audio file in `assets/music/`. |
| `sfx.file` | legacy fallback air-swish; only read when `sfx.mode` is not `cue_sheet` or the cue sheet is stale. |
| `audio.sfx_gain_db`, `audio.sfx_fade_sec` | legacy fallback only; the cue-sheet bus carries its own gain staging. |
| `delivery.copy` | glob patterns resolved inside the run directory; `*_final.mp4`, contact sheet, QA/caption JSON, `*.md`, `edit_plan.json`, `word_timings.json`, `sfx_cue_sheet.json`, `sfx_report.json`. |
| `delivery.copy_to_downloads`, `delivery.prune_local_media` | defaults for `fenix deliver`; `--downloads/--no-downloads` and `--prune/--no-prune` override one run. |
| `layout.bottom_stack_shift_pct` | fraction of canvas height added to the price/caption/footer stack; `0.2` = 20 %. |
| `shots.*` | the ordered shot table is validated by `fenix storyboard` and written by `fenix plan`; the tolerances only decide PASS/FAIL, they never re-time a shot. |
| `audio.music_only.*` | read only when `sku.json → voiceover` is `null`, `""` or `"none"`/`"music"`; a missing `voiceover` key keeps the legacy `voice.mp3` default. |
| `quality.beat_policy = picture` | beat rows stay in `plan_validation.json` / `qa_report.json` as `info` (measured offsets, no failure). Music-driven pieces keep the `warn`/`error` thresholds. |
| `safe_zones.<mode>.title_y` / `caption_y` | the `header` / `lower` text-card and motion-text zones read these; `center` uses the canvas. An explicit `y` in the spec wins. |
| `text_cards.*` | defaults for `sku.text_cards[]`; a per-card `y`/`size`/`sub_size`/`color`/`sub_color`/`enter_sec`/`exit_sec`/`rise_px` overrides them. |
| `encoding.*` | one contract for render, comp, grade and longform; comp also enforces `max_whatsapp_bytes` with the CRF fallback. |

## Safe-zone coordinates

`sku.json → safe_zone_mode` selects one preset. Coordinates are 1080×1920
pixels; `x_min`/`x_max` define the text width, the `_y` keys are top edges.

| Preset | x_min | x_max | header_y_start | title_y | sub_y | badge_y | price_y | caption_y | footer_y |
|---|---|---|---|---|---|---|---|---|---|
| `reels_universal` | 65 | 950 | 110 | 118 | 194 | 254 | 1310 | 1445 | 1585 |
| `status_story` | 65 | 1015 | 110 | 118 | 194 | 254 | 1350 | 1485 | 1625 |

## Rules

- `fenix make` hashes only the config sections each stage reads (and the
  `picture`/`audio` render slices), so a delivery/docs edit does not invalidate
  frames, align or a render.
- Change one value at a time and re-run only the affected gate.
- Safe zones and the palette are locked from Sessions 1–3; change them
  deliberately, never to “make this one video fit”.
- Do not add a second config file for normal settings. Portable defaults belong
  in `config/pipeline.json`; machine-local paths belong in the gitignored
  `config/pipeline.local.json`; a value that is true for one piece belongs in
  `input/<project>/config.json`. If a new knob belongs to a stage, add it to the
  matching block and document it here.
- `version` tracks the config schema, not the package version. A breaking rename
  bumps it.
- Deferred (do not add keys for these yet): `text_cards.drift_px` horizontal
  drift and `beats --fit` BPM stretching. The beat-drift warning is active; the
  workaround is to re-measure the bed (`fenix beats --force`) rather than warp
  the piece.
