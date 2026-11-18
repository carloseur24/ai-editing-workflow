# Architecture

The repo is a Python application, not a folder of scripts. Code lives in
`src/fenixsoft/`, inputs in `input/`, generated artifacts in `output/`, and the
only numeric policy in `config/pipeline.json`. The four layers (machinery,
project intent, generated media, product) and their config precedence are
defined in [agent/production-line.md](agent/production-line.md).

## Layout

```text
.
├── fenix                          # launcher: python -m fenixsoft (no install needed)
├── config/pipeline.json           # portable canvas, timing, quality, audio, encoding, safe zones
├── config/pipeline.local.json     # optional, gitignored machine paths (deep-merged)
├── input/<project>/config.json    # optional, sparse project overlay (project intent)
├── pyproject.toml                 # package metadata + optional `fenixsoft` console script
├── src/fenixsoft/
│   ├── cli.py                     # command map: doctor, lint, beats, frames, align,
│   │                              # plan, validate, render, qa, eval, deliver, batch, runs, test
│   ├── context.py                 # the ONLY module that knows the directory tree
│   ├── domain/                    # pure problem model, no CLI, no filesystem policy
│   │   ├── candidates.py          # candidate ID -> clip + in-point
│   │   ├── narration.py           # script sentences/clauses -> timed lines
│   │   ├── quality.py             # sharpness / luma / motion / sustained windows
│   │   ├── labeling.py            # slug/filename safety + perceptual hashes
│   │   └── runs.py                # immutable run directories + runs.json index
│   ├── stages/                    # analysis + planning stages
│   │   ├── storyboard.py          # brief -> timed scenes (any format)
│   │   ├── motion.py              # spec-driven motion plate CLI
│   │   ├── track.py               # template tracking -> comp coordinates
│   │   ├── comp.py                # VFX: alpha/key, rect/ellipse masks, moves
│   │   ├── fx.py                  # particles, glitch, shatter
│   │   ├── grade.py               # LUT, primaries, shot match, secondaries
│   │   ├── scopes.py              # histogram/vector/wave/spectrum QC
│   │   ├── longform.py            # chapters, xfade, multicam, J/L
│   │   ├── tts.py                 # ElevenLabs/OpenAI voiceover
│   │   ├── script_lint.py
│   │   ├── beat_grid.py
│   │   ├── keyframes.py
│   │   ├── align.py
│   │   ├── plan.py
│   │   ├── sfx.py                 # cue sheet -> SFX bus + ducked music bed
│   │   └── validate_plan.py
│   ├── render/                    # compositors
│   │   ├── render_video.py        # edit renderer (cuts, captions, audio)
│   │   ├── hf_overlay.py          # HyperFrames text layer
│   │   └── motion/                # SDF/clay motion plates (sdf.py, plate.py)
│   ├── gates/                     # delivery gate + regression evaluation
│   │   ├── qa_check.py
│   │   └── eval_run.py
│   └── ops/                       # multi-SKU + delivery + environment
│       ├── batch_run.py
│       ├── deliver.py
│       ├── doctor.py
│       ├── finish.py
│       ├── job.py               # read-only job card: mode/state/skills/next
│       ├── label.py             # footage intake: sheets, grids, safe rename
│       └── make.py              # one-shot edit chain
├── assets/                        # fonts, music bed, SFX, HyperFrames runtime
├── input/<sku>/                   # source material + agent-authored intent
├── output/<sku>/                  # per-SKU artifacts + one directory per render
├── eval/                          # accepted baselines and eval history
├── tests/                         # fast unit tests (./fenix test)
├── docs/                          # this documentation set
└── tools/setup_wsl.sh             # idempotent environment setup
```

## Dependency direction

```text
cli
 ├── ops      (batch, deliver, doctor, finish, job, label, make)
 ├── gates    (qa, eval)
 ├── stages   (storyboard, motion, track, comp, fx, grade, scopes, longform, tts, lint, beats, frames, align, plan, sfx, validate)
 └── render   (render_video, hf_overlay)

stages → domain + context
render → domain + stages.beat_grid + context
gates  → domain + context
ops    → domain + context
domain → context
context → stdlib only
```

Rules:

- Nothing below `cli` imports `cli`.
- A stage never imports a gate or an op.
- `domain/` never reads `output/` or spawns ffmpeg; it is pure measurement and
  bookkeeping.
- `context.py` is the only place that maps a directory to a constant. If a path
  changes, it changes there once.
- `render_video.py` is a library with a `main()`; it can be imported by tests or
  the overlay tool without running a render.

## Invariants

1. **One repo root.** `context.REPO_ROOT` is computed from the package location,
   never from the current working directory. Every command works from any cwd.
2. **`input/` is source material.** Humans edit `sku.json` and `picks.json` and
   add media. Tools write the derived files (`keyframes/`, `candidates.json`,
   `word_timings.json`, `_voice_clean.wav`, `voice_28s.wav`).
3. **`output/<sku>/` is derived.** Flat JSON reports are per-stage and reusable;
   each render gets its own `<sku>_<YYYYmmdd-HHMMSS>/` directory.
4. **Runs are immutable.** A new render never writes into an earlier run. The
   only mutable pointers are `runs.json` and `latest.json`.
5. **Caches are disposable.** `.cache/` can be deleted at any time; the next
   command rebuilds what it needs (models are re-downloaded, not required for QA).
6. **Config is policy, code is mechanism.** Thresholds and layout numbers live in
   `config/pipeline.json`; code reads them with defaults. A gitignored
   `config/pipeline.local.json` is deep-merged for machine paths, and a sparse
   `input/<project>/config.json` is then merged on top for project intent. The
   public config stays portable; project-only values never enter it. An explicit
   `--config` replaces the base but the project overlay still applies.
7. **A gate is not advisory.** `--strict` turns warnings into failures for
   delivery; the agent does not edit the output to make a gate pass.

## Data flow

```text
input/<sku>/clips/*.mp4 ─┐
input/<sku>/voice.mp3  ──┼─ align ──> word_timings.json + narration_lines.json
input/<sku>/sku.json   ──┘
                          frames ──> candidates.json + keyframes/
                          beats  ──> beat_grid.json
narration_lines + candidates + picks ── plan ──> input/<sku>/edit_plan.json
                                         validate ──> plan_validation.json
edit_plan + assets + config ── render ──> output/<sku>/<run>/<run>_final.mp4
                                         qa     ──> <run>/qa_report.json
                                         eval   ──> eval/<sku>_<stamp>.json
                                         deliver ──> delivery.dir/<run>/
```

## Why this shape

The previous version put 17 scripts at the repo root and resolved every path
from `Path(__file__).parent`. That made a renderer import look like a sibling
import, put generated files next to source, and made "where do I write this?"
depend on which file you opened. The package layout gives one path module, one
CLI, and a testable domain layer.
