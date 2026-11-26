# AI Editing Workflow — Fenixsoft Content Maxing

Deterministic 1080×1920 @ 60 fps vertical-video pipeline for **Fenixsoft VE**
(`@fenixsoft`, Mercado Social Av. Ayacucho, Local C-29, Maracay). Drop vertical
clips, a Spanish ElevenLabs voiceover and a `sku.json` into `input/<sku>/` and
get a WhatsApp/Reels-ready MP4 with beat-synced cuts, kinetic captions, a price
pill, badges and a loudness-normalised mix.

This is an **agentic workflow**, not a pile of render scripts. An agent opens
with `./fenix job <project>`, loads the skill that owns the craft, changes
`input/` or `config/`, and lets the pipeline compute every timestamp, score and
gate. `AGENTS.md` is the contract; `docs/agent/` is the method; the skills carry
taste.

Validated on WSL2 / Ubuntu 24.04, Python 3.12, FFmpeg 6.1.1. A 28 s render takes
~3 min cold (including clip normalisation) and ~13 s for an audio-only remix.
The demo SKU passes **30/30 QA checks**, is 28.37 s and 13.65 MB, with a
cue-sheet sound design pass (57 foreground events, 4 sub hits, 3 vacuum gaps).

## Storage policy

WSL is pre-production. Finished masters live on Windows on the author's machine;
the portable default is `output/delivery/`. Machine-specific paths belong in
`config/pipeline.local.json`, which is gitignored and deep-merged over the
public config.

- `fenix finish <project>` runs the strict QA gate, copies the run to the
  delivery shelf plus a flat copy in Downloads, then deletes the local MP4,
  contact sheet and QA frames.
- The local run keeps only JSON evidence; `runs.json` points at the delivered
  master, so `qa --latest` and `runs` still work.
- `fenix deliver` is the shelf-only copy; `--no-prune` is for experiments.
- `fenix motion <project> --deliver` does the same for a motion plate.

## Quickstart

```bash
git clone git@github.com:carloseur24/ai-editing-workflow.git
cd ai-editing-workflow
bash tools/setup_wsl.sh
source .venv/bin/activate

./fenix doctor                    # paths + which assets are present
./fenix job example_iphone14      # complete brief, no media: the state card
./fenix test                      # fast unit tests
```

The example carries a complete `brief.json` and `sku.json` but no footage or
voiceover, because media is not tracked. Add your own, then:

```bash
./fenix job <project>             # missing inputs vs repo assets, exact next command
./fenix make <project>            # stale stages -> render/remix -> QA -> finish
```

The checked-in `demo_iphone14_blue` manifest is a reference for a 28 s piece; its
original clips and VO are local to the author's machine, not in the repo.

Everything goes through the `./fenix` launcher; it works from any directory and
needs no editable install. `pip install -e .` exposes the same entry point as the
optional `fenixsoft` console script (`PYTHONPATH=src python -m fenixsoft` is the
equivalent without installing).

## Assets

This is a public, agentic repo: **source and intent travel, media does not.**
`assets/fonts`, the motion textures, `assets/sfx/library.json` and the pinned GSAP
runtime are tracked. Music beds, SFX samples, model weights, footage, voiceovers
and renders are gitignored.

| Need | Where it goes | If it is missing |
|---|---|---|
| Music bed | `assets/music/*.wav` (or `music.file`) | `fenix job` lists it as an **asset gap**, not a project input |
| Cue-sheet SFX | `sfx.library_dir` (default `assets/sfx`) | `./fenix sfx --install-library` synthesizes the procedural core |
| Footage / VO | `input/<project>/clips/`, `voice.mp3` | `fenix job` lists them under `missing` |
| Machine paths | `config/pipeline.local.json` (gitignored) | keep them out of the public config |

The full inventory, flow and provisioning commands are in
[assets/README.md](assets/README.md); the project-folder contract is in
[input/README.md](input/README.md).

## Layout

```text
fenix                      # launcher
config/pipeline.json       # portable canvas, timing, quality, audio, safe zones
config/pipeline.local.json # optional machine paths (gitignored)
src/fenixsoft/             # package: context, domain, stages, render, gates, ops
assets/                    # tracked fonts/textures/runtime; media gitignored
input/<project>/           # intent + local media (brief, sku, clips, voice)
output/<project>/          # derived artifacts + one immutable directory per render
eval/                      # accepted baselines and eval history
tests/                     # fast unit tests
docs/                      # architecture, pipeline, agent and reference guides
tools/setup_wsl.sh         # idempotent environment setup
```

`input/` is source material. `output/` is derived. `.cache/` is disposable. No
generated file lives at the repo root. Full map:
[docs/architecture.md](docs/architecture.md).

## Commands

| Command | Cost | Purpose |
|---|---|---|
| `fenix storyboard <project>` | 1 s | brief → timed scenes + storyboard.md |
| `fenix doctor` | 1 s | check paths, ffmpeg, fonts, media, models, SKUs |
| `fenix job <project>` | 1 s | one card: mode, missing inputs, skills, stage state, next command |
| `fenix lint <sku>` | 1 s | word budget, tempo, phonetics before TTS |
| `fenix beats <sku>` | 2 s | measured BPM/phase → `beat_grid.json` |
| `fenix frames <sku>` | 20 s | keyframes + scored `candidates.json` |
| `fenix align <sku>` | 15 s | trim voiceover, word timings, narration lines |
| `fenix plan <sku>` | 2 s | lines × candidates → `edit_plan.json` |
| `fenix validate <sku>` | 2 s | pre-render gate (ranges, sharpness, beats) |
| `fenix sfx <sku>` | 2–5 s | cue sheet → layered SFX bus + ducked music bed |
| `fenix tts <project>` | 2–20 s | ElevenLabs/OpenAI voiceover → `voice.mp3` |
| `fenix motion <project>` | 10 s–3 min | 3D/clay motion plate, MP4 or alpha |
| `fenix track <project>` | 2–10 s | template tracking → comp coordinates |
| `fenix comp <project>` | 5 s–1 min | key/alpha layers, rect/ellipse masks, moves, fades |
| `fenix fx <project>` | 5–30 s | seeded particles, glitch, shatter |
| `fenix grade <project>` | 5–30 s | LUT, primaries, shot matching, secondaries |
| `fenix scopes <project>` | 2 s | histogram, vectorscope, waveform, stats |
| `fenix longform <project>` | 10 s–5 min | chapters, xfade, multicam, J/L offset |
| `fenix render <sku>` | ~3 min | new timestamped run |
| `fenix render <sku> --remix` | ~13 s | rebuild audio into a new run |
| `fenix qa --latest <sku> --strict` | 30 s | 31-check delivery gate |
| `fenix eval <sku> --skip-align --skip-render` | 30 s | regression vs baseline |
| `fenix finish <project>` | 40 s | QA → delivery shelf → prune local |
| `fenix make <project>` | full chain | stale stages → render/remix → QA → finish |
| `fenix batch` | long | align → render → qa for several SKUs |
| `fenix label scan/apply` | seconds | footage intake: sheets, grids, duplicates, safe rename |
| `fenix test` | 1 s | unit tests |

Full flags: [docs/reference/commands.md](docs/reference/commands.md).
Field-level specs for `brief`, `motion`, `comp`, `grade`, `longform`, `picks`
and the SFX library: [docs/reference/specs.md](docs/reference/specs.md).

## Outputs

```text
output/<sku>/
├── storyboard.json / .md        # storyboard
├── script_lint.json             # lint
├── beat_grid.json               # beats
├── narration_lines.json         # align
├── plan_edit.json               # plan
├── plan_validation.json         # validate
├── sfx_cue_sheet.json           # sfx
├── sfx_bus.wav / music_bed.wav  # sfx
├── sfx_report.json              # sfx gate
├── motion/<run>/                # motion plate + manifest
├── track/track_<stamp>.json     # tracked points for comp
├── comp/<run>/                  # composite + manifest
├── fx/<run>/                    # particles/glitch/shatter + manifest
├── grade/<run>/                 # graded video, before/after, manifest
├── scopes/scopes_<stamp>/       # hist/vector/wave/spectrum + stats
├── longform/<run>/              # chaptered master + chapters.md
├── runs.json / latest.json      # edit-run index + pointer
└── <sku>_<YYYYmmdd-HHMMSS>/     # one directory per edit render, never overwritten
    ├── <run>_final.mp4
    ├── edit_plan.json
    ├── caption_audit.json
    ├── qa_report.json
    ├── contact_sheet.png
    └── qa_frames/
```

`motion`, `comp`, `fx`, `grade` and `longform` write their own `<run>/` folders
but are not indexed in `runs.json` and are not covered by the 31-check QA gate;
each writes a manifest and validates its spec. `track` writes one JSON,
`scopes` writes one QC folder. See [artifacts.md](docs/agent/artifacts.md).

`fenix label` writes a disposable `.fenix-label/` work directory inside the
folder it scans (sheets, vision grids, `names.tsv`, rename manifests, undo
scripts); it never writes into `output/`.

`fenix deliver` copies the MP4, contact sheet, QA report, caption audit and any
`*.md` notes to `delivery.dir` from `config/pipeline.json`. The repo stays
canonical; the delivery folder is a shelf outside the repo.

## Documentation

- [Architecture](docs/architecture.md) — layers, dependency rules, invariants.
- [Pipeline](docs/pipeline.md) — every stage, input, output and internal.
- [Agent workflow](docs/agent/workflow.md) — phased loop and acceptance gates.
- [Artifacts](docs/agent/artifacts.md) — who writes what, and where.
- [Creative contract](docs/agent/creative.md) — story shape, phonetics, safe zones.
- [Growing the project](docs/agent/growth.md) — where new code belongs.
- [Config reference](docs/reference/config.md) — every block in `pipeline.json`.
- [QA gates](docs/reference/qa-gates.md) — the 31 checks and their fixes.
- [Troubleshooting](docs/troubleshooting.md) — symptom → fix.

## Why v4

The previous kit read one source frame per output frame (30 fps clips played at
2×), wrote a lossy intermediate, ignored the planned clip identity, mixed no
audio, assumed 118 BPM while the track runs 118.97, and had no delivery gate.
v4 normalises clips once, streams frames into a single x264 encode, honours a
validated `edit_plan.json`, mixes and loudness-normalises the audio, snaps cuts
to a measured beat grid, and refuses to ship without a QA report. The full
change log and research are archived in
[docs/research/harness-v5.md](docs/research/harness-v5.md).

## Environment note

The music beds and the legacy whoosh are licensed production assets, so they are
gitignored and never ship in this public repo. Model weights live in
`.cache/models/` (also gitignored) and are re-downloaded by
`tools/setup_wsl.sh`.
