# The production line — four layers, one direction

The pipeline exists so a finished piece never becomes a source of truth. Every
file has exactly one owner, and information flows one way:

```
machinery  ->  project intent  ->  generated media  ->  product
src/, config/   input/<p>/         output/<p>/           delivery shelf
```

## 1. Machinery — `src/` + `config/pipeline.json`

Portable defaults that describe the pipeline, not a piece: canvas, encoder,
audio defaults, safe-zone presets, story frameworks, QA thresholds. Nothing in
`src/` may name a project, a clip, a timestamp or a client value. If a stage
needs a number, it reads it from the effective config or from a project input.

`config/pipeline.local.json` is machine state (Windows shelf, personal SFX
library). It is gitignored and never a place for creative decisions.

## 2. Project intent — `input/<p>/`

Everything that makes *this* piece: `brief.json`, `sku.json`, `picks.json`,
`motion.json`, `comp.json`, `grade.json`, `longform.json`, raw clips and voice.
This is the only layer a video job edits.

A project may also carry a sparse **`config.json` overlay** with only the keys
where this piece differs from the defaults:

```json
{
  "target_duration_sec": 33.0,
  "cuts_per_video": 5,
  "quality": {"in_point_floor": 0.25, "beat_policy": "picture"},
  "transitions": {"enabled": true, "default": "dissolve", "sec": 0.2},
  "post": {"motion": false, "comp": false}
}
```

The overlay is deep-merged, so one key does not erase its siblings. A `null`
value **removes** an inherited key — that is how a project opts out of a global
preset such as `audio.music_only`.

## Config precedence

Low to high:

1. `config/pipeline.json` (portable defaults)
2. `config/pipeline.local.json` (machine-local, gitignored)
3. `input/<p>/config.json` (project intent)

An explicit `--config PATH` replaces the global base and, as before, suppresses
the machine-local file. It does **not** suppress the project overlay: the
project's own requirements cannot be silently defeated by pointing the command
at another base file. `doctor`/`label` have no project and never see an overlay.

Every command that has a project resolves it first, then loads
`load_config(args.config, project=sku_dir)` (`load_config_for` is the same
thing). `job` prints the overlay path, its override count and the effective
values that matter; `doctor` lists the project configs it found.

## 3. Generated media — `output/<p>/`

Derived artifacts (`candidates.json`, `word_timings.json`, `beat_grid.json`,
`edit_plan.json`, `sfx_*`, `preview/`) and immutable timestamped runs. A run is
never edited, moved, renamed or reused in place; a new render means a new run
directory. `runs.json` is the index, not a source.

Freshness is computed, never guessed:

- stage artifacts carry a `.state/<stage>.json` digest of the config sections
  that stage reads;
- an edit run stores `config_slices` (picture + audio) in its `edit_plan.json`
  and `make` compares the **effective merged config** against them;
- a project config change that touches a picture key forces a new picture, an
  audio key forces a remix, and an unrelated key forces nothing.

`make --post comp` reads `post.motion` / `post.comp` (default `true`) from the
effective config. A project that draws text natively sets both to `false` and
the edit master stays the product; no plate, no comp, no post QA.

## 4. Product — the delivery shelf

Only `deliver`, `finish`, `make` and a stage's `--deliver` write here, and only
after the gate. A delivered folder is the piece of record: master, contact
sheet, QA report, caption audit, executed plan and a readme. The local run may
keep JSON evidence only; `runs.json` points at the delivered master.

## Skills are craft, not data

`kinetic-animations-engineer`, `sfx-dopamine-blueprint`, `sound-engineer`,
`ffmpeg-expert` and the rest are reusable instructions for taste and technique.
They are loaded per task and never forked into `src/` or into a project folder.
A project that needs different craft records the choice in its brief or specs;
it does not get a private copy of a skill.

## The checklist

- A number came from a JSON file, not from memory.
- A project-only value lives in `input/<p>/`, not in `config/pipeline.json`.
- A new run is a new directory; nothing earlier was overwritten.
- The shelf was written by a delivery command after a gate.
- A skill was loaded, not re-implemented.
