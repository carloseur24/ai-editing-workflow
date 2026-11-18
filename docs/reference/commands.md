# Command reference

All commands run through the root launcher. `<sku>` is either a folder name
(`iphone14_blue`) or a path (`input/iphone14_blue`).

```bash
./fenix <command> [args...]
./fenix <command> --help
```

The launcher uses `.venv/bin/python` when present, puts `src/` on `PYTHONPATH`,
and works from any working directory. An editable install
(`pip install -e .`) exposes the same entry point as `fenixsoft`.

## Analysis and planning

| Command | Cost | Purpose | Exit |
|---|---|---|---|
| `fenix storyboard <project> [--questions] [--framework NAME] [--force] [--json]` | ~1 s | brief → timed scenes + `storyboard.md`/`.json`; 12 frameworks | 0/1 |
| `fenix doctor [--json]` | ~1 s | print resolved paths and asset state: ffmpeg, fonts, music, SFX library, motion textures, GSAP, delivery dir, models, SKUs | 0/1 |
| `fenix job <project> [--config PATH] [--json]` | ~1 s | one card: mode, missing inputs, skills, stage/render state, next command | 0/1 |
| `fenix lint <sku> [--target 28] [--config PATH] [--strict] [--json]` | ~1 s | word budget, tempo, phonetics, display coverage | 0/1 |
| `fenix beats [<sku>] [--music FILE] [--config PATH] [--force] [--print]` | ~2 s | measured BPM/phase → `beat_grid.json` | 0/1 |
| `fenix frames <sku> [--fps 1] [--width 360] [--out DIR] [--candidates-step 0.25] [--config PATH] [--no-candidates]` | ~20 s | keyframes + `candidates.json` | 0/1 |
| `fenix align <sku> [--config PATH] [--target-sec N] [--engine auto\|faster-whisper\|energy] [--refresh-asr]` | ~15 s | trim VO, word timings, narration lines | 0/1 |
| `fenix tts <project> [--spec PATH] [--out PATH] [--json]` | 2–20 s | ElevenLabs/OpenAI voiceover → `input/<p>/voice.mp3` | 0/1 |
| `fenix plan <sku> [--config PATH] [--dry-run] [--json]` | ~2 s | narration lines × candidates → `edit_plan.json` | 0/1 |
| `fenix validate <sku> [--config PATH] [--strict] [--json]` | ~2 s | pre-render gate | 0/1 |
| `fenix sfx <sku> [--config PATH] [--library DIR] [--install-library] [--force] [--dry-run] [--strict] [--json]` | ~2–5 s | cue sheet → layered SFX bus + ducked/vacuumed music bed | 0/1 |
| `fenix sfx --install-library [--force]` | ~1 s | synthesize the procedural core into `sfx.library_dir/<install_subdir>/` | 0/1 |

## Render and post

| Command | Cost | Purpose | Exit |
|---|---|---|---|
| `fenix render <sku> [--config PATH] [--out MP4] [--remix] [--from RUN] [--allow-soft-in] [--hf-overlay] [--hf-workers N] [--draft]` | ~3 min / 13 s | render a new immutable run | 0/1 |
| `fenix motion <project> [--spec PATH] [--out PATH] [--still T] [--seconds N] [--deliver] [--json]` | 10 s–3 min | spec-driven motion plate (MP4 or alpha MOV) | 0/1 |
| `fenix track <project> [--spec PATH] [--out JSON] [--json]` | 2–10 s | template-track a patch → normalised points for `comp` | 0/1 |
| `fenix comp <project> [--spec PATH] [--out MP4] [--deliver] [--json]` | 5 s–1 min | key/alpha layers, rect/ellipse masks, moves, track, fades → `output/<p>/comp/` | 0/1 |
| `fenix fx <project> [--spec PATH] [--out MP4] [--deliver] [--json]` | 5–30 s | seeded particles, glitch, shatter → `output/<p>/fx/` | 0/1 |
| `fenix grade <project> [--spec PATH] [--out MP4] [--deliver] [--json]` | 5–30 s | LUT + primaries + shot match + secondaries → `output/<p>/grade/` | 0/1 |
| `fenix scopes <project> --input MP4 [--spec PATH] [--time SEC] [--no-audio] [--json]` | ~2 s | histogram/vectorscope/waveform/spectrum + stats | 0/1 |
| `fenix longform <project> [--spec PATH] [--out MP4] [--deliver] [--json]` | 10 s–5 min | chapters, xfade, multicam, J/L offset → `output/<p>/longform/` | 0/1 |

`motion`, `track`, `comp`, `fx`, `grade`, `scopes` and `longform` are post/
assembly modes: they validate their spec and write their own evidence, but the
30-check `qa` gate is edit-render specific. Check the manifest/points/stats and
`ffprobe`, then watch the result. `tts` is the one source-writing stage. Full
field tables: [specs.md](specs.md).

## Asset intake — label

| Command | Cost | Purpose | Exit |
|---|---|---|---|
| `fenix label scan <folder> [--work DIR] [--config PATH] [--jobs N] [--recursive] [--json]` | seconds–minutes | probe clips/images, 4-frame sheets, 8-up vision grids, `names.tsv` | 0/1 |
| `fenix label apply <folder> [--work DIR] [--names TSV] [--config PATH] [--dry-run] [--exact] [--json]` | instant | validate labels, atomically rename, write manifest + `undo-*.sh` | 0/1 |
| `fenix label undo <folder> [--work DIR] [--manifest CSV]` | instant | reverse the latest apply | 0/1 |

The work directory is `<folder>/.fenix-label/` (disposable): `scan.json`,
`sheets/`, `grids/`, `names.tsv`, `renames-*.csv`, `undo-*.sh`. `names.tsv` is
`<index>\t<label>`; blank cells and `#` comment lines are skipped. `scan` also
flags perceptual duplicates; `apply` is a two-phase rename so swaps are safe and
uses Windows-safe slugs (`--exact` disables slugging). There is no delivery step.
Naming grammar, measured budget and duplicate rules:
[labeling.md](../agent/labeling.md).

## One-shot chain

| Command | Cost | Purpose | Exit |
|---|---|---|---|
| `fenix make <project> [--config PATH] [--dry-run] [--force] [--remix] [--no-finish] [--no-qa] [--draft]` | full chain | run stale edit stages → render/remix → QA → finish; mtime + config slices | 0/1 |

`make` is the edit-chain shortcut: it skips stages whose artifacts are newer
than their inputs **and** whose config slice is unchanged (`.state/<stage>.json`),
full-renders when picture inputs or `config.picture` moved, remixes when only
audio/`config.audio` moved, and stops at the first failure. After running
stages it re-decides the render mode, so a changed `edit_plan.json` becomes a
full render instead of a remix of the old picture. `align` also checks
the voice+script fingerprint instead of realigning on a copy/title edit. It does
**not** run `tts`, `label` or the post stages. `--draft` renders a review encode,
then skips QA and delivery; `--no-qa` skips QA and blocks `finish`. `--dry-run`
prints the plan without running anything.

## Gates, delivery and maintenance

| Command | Cost | Purpose | Exit |
|---|---|---|---|
| `fenix qa [VIDEO] [--latest SKU] [--config PATH] [--target-lufs N] [--max-bytes N] [--strict] [--json]` | ~30 s | delivery gate, writes `qa_report.json` | 0/1 |
| `fenix eval <sku> [--skip-align] [--skip-render] [--run DIR] [--baseline PATH] [--update-baseline] [--strict] [--json]` | ~30 s | regression vs `eval/<sku>_baseline.json` | 0/1 |
| `fenix deliver [RUN] [--latest SKU] [--config PATH] [--dir PATH] [--downloads\|--no-downloads] [--prune\|--no-prune] [--dry-run]` | ~1 s | copy a finished run to the delivery shelf | 0/1 |
| `fenix finish <project> [--config PATH] [--no-prune] [--no-downloads] [--re-qa]` | ~40 s | strict QA → delivery shelf + Downloads → prune local media; reuses a strict PASS for the same bytes | 0/1 |
| `fenix batch [--only SKU]... [--skip-existing] [--remix] [--no-qa]` | long | align → render → qa for several SKUs | 0/1 |
| `fenix runs <sku>` | ~1 s | list run directories, sizes and timestamps | 0 |
| `fenix test` | ~1 s | run the fast unit tests in `tests/` | 0/1 |
| `fenix --version` | instant | print the package version | 0 |
| `fenix` / `fenix --help` | instant | command map and output locations | 0 |

Notes:

- `--config` is accepted by `lint`, `beats`, `frames`, `align`, `plan`,
  `validate`, `sfx`, `render`, `qa`, `deliver`, `finish`, `make` and
  `label scan`/`label apply`. `storyboard`, `motion`, `track`, `comp`, `fx`, `grade`,
  `scopes`, `longform`, `tts`, `eval`, `batch`, `doctor` and `runs` use
  `config/pipeline.json` directly.
- `deliver` defaults to `delivery.copy_to_downloads` and
  `delivery.prune_local_media`; the `--downloads/--no-downloads` and
  `--prune/--no-prune` pairs override them for one run.
- `batch` scans `input/*/sku.json`, skips folders without a voiceover or clips,
  isolates failures and prints a summary; `--remix` requires an existing render.
- `motion`, `comp`, `fx`, `grade` and `longform` accept `--deliver` to copy the
  result and its manifest to the delivery shelf and Downloads; `track` and
  `scopes` are review artifacts and have no delivery step.
- `tts` writes `input/<project>/voice.mp3` by default and reads provider keys
  only from the environment; it never prints or stores them.
- `render --draft` uses `veryfast`/CRF 23 and skips per-cut QA stills for a fast
  review pass; it is not a delivery master. `finish` should not be pointed at a
  draft run.
- `track` writes `output/<project>/track/track_<stamp>.json`; point a comp
  layer at it with `"track": ...`.

`--hf-overlay` is opt-in: it renders ≈500 text states (28–46 s at 4 workers) and
composites them inside the existing encode pass; a 28 s render measured ~130 s.

## Job card (`fenix job`)

`fenix job <project> [--config PATH] [--json]` is the opening move for every
video task. It writes nothing and renders nothing; it prints one card from the
current tree:

- `mode` — `edit`, `motion`, `hybrid`, `longform`, `post`, `intake` or `new`;
- `ready` / `missing` — exact missing **project inputs** under `input/<p>/`;
- `assets` — missing **repo assets** (music bed, config paths), kept separate so
  a music bed is never mistakenly copied under `input/`;
- `warnings` — e.g. no `brief.json`, SFX library not installed;
- `skills` — the skills to load, with `installed` / `MISSING`;
- `stages` — run/skip + reason for the edit/hybrid chain;
- `render` — `skip`, `remix`, `full` (or `none` outside edit/hybrid) + why;
- `master` — the current run video, when one exists;
- `next` — exact command(s), in order;
- `gates` — what will verify the result;
- `rules` / `budget` — the job contract and expected call budget.

Use `--json` for the machine card. The full per-case rules and measured
failure modes are in [job-contract.md](../agent/job-contract.md).

## Spec files

The JSON each command reads is summarized in [specs.md](specs.md):
`brief.json`, `picks.json`, `motion.json`, `comp.json`, `grade.json`,
`longform.json`, `assets/sfx/library.json`, `sku.json` and `edit_plan.json`.

## Examples

```bash
# Full demo
./fenix doctor
./fenix lint demo_iphone14_blue
./fenix beats demo_iphone14_blue
./fenix frames demo_iphone14_blue
./fenix align demo_iphone14_blue
./fenix plan demo_iphone14_blue
./fenix validate demo_iphone14_blue
./fenix sfx demo_iphone14_blue --install-library
./fenix render demo_iphone14_blue
./fenix qa --latest demo_iphone14_blue --strict
./fenix eval demo_iphone14_blue --skip-align --skip-render

# Post-production paths
./fenix storyboard project --questions          # write the brief first
./fenix motion project --still 1.5              # review one plate frame
./fenix motion project --deliver                # full plate -> delivery shelf
./fenix comp project --deliver                  # layers over a base
./fenix grade project --deliver                 # LUT/primaries/match
./fenix longform project --deliver              # chaptered master

# One-shot edit chain
./fenix make demo_iphone14_blue --dry-run        # show the plan
./fenix make demo_iphone14_blue                  # stale stages -> render -> QA -> finish

# Footage intake
./fenix label scan /mnt/c/Users/<you>/footage
# fill .fenix-label/names.tsv, then:
./fenix label apply /mnt/c/Users/<you>/footage --dry-run
./fenix label apply /mnt/c/Users/<you>/footage

# Re-tune audio only (new run, previous video reused)
./fenix render demo_iphone14_blue --remix

# Ship the newest accepted render
./fenix deliver --latest demo_iphone14_blue

# Inspect what would be copied
./fenix deliver --latest demo_iphone14_blue --dry-run
```

## Optional: HyperFrames text layer

`--hf-overlay` renders the DOM text layer (title, badges, price pill, captions,
footer) with HyperFrames instead of PIL, then composites the transparent result
in the same FFmpeg pass as the cuts. Off by default; the PIL renderer stays the
reference path.

- Geometry is shared: `build_overlay()` and `layout_cards()` are the single
  source of truth, so both renderers draw the same boxes.
- Only ~250 unique states are captured (10 fps states plus a six-step slide),
  not 1,680 frames. The overlay is cached by state hash in `.cache/hf_overlay/`.
- The composite runs in RGB with an explicit BT.709 conversion. Blending in YUV
  shifted the brand emerald (`#059669` → `#008469`); the RGB path keeps the pill
  byte-identical to the PIL renderer.
- `--hf-workers 4` fits an 8 GB box; `auto` resolves to one worker because the
  machine trips HyperFrames' low-memory profile.
- The first run downloads `hyperframes@0.9.55` through npx; later runs reuse the
  cached overlay and add only the composite time.
- A failed overlay fails the render. There is no silent fallback to PIL.

## Sound design (`fenix sfx`)

`fenix sfx <sku>` is cue-sheet-first. `--dry-run` writes only
`output/<sku>/sfx_cue_sheet.json` so the event list can be reviewed before any
audio is generated. A full run also writes `sfx_bus.wav`, `music_bed.wav` and
`sfx_report.json`.

- `--install-library` synthesizes the deterministic core (sub impact, riser,
  whooshes, click, body thud, tick, chime, paper slice, room bed) into
  `sfx.library_dir/<install_subdir>/`. Existing files are kept unless `--force`.
- `--library DIR` overrides `sfx.library_dir` for one run.
- The manifest `assets/sfx/library.json` maps semantic roles to files inside the
  library folder; licensed files and procedural files can be mixed freely.
- `--strict` turns warning rows (vacuum, bed LUFS) into failures.

`fenix render` consumes the artifacts only when the cue sheet's
`source.edit_plan_sha256` matches the plan being rendered. Re-run `fenix sfx`
after any `fenix plan` or manual `edit_plan.json` change.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success / gate PASS |
| 1 | a gate or stage failed; read the report and its `fix` field |
| 2 | CLI usage error (unknown command or missing argument) |

## Notes

- `--config` defaults to `config/pipeline.json`; relative paths resolve against
  the repo root.
- `--strict` is required for delivery: warnings become failures.
- `--json` prints the machine-readable artifact instead of the table.
- `--allow-soft-in` and `allow_fast_captions` are deliberate exceptions. Their
  use is recorded in QA and must be accepted by the user.
- `fenix render` without `--out` always creates a new run; it never overwrites
  an earlier one.
