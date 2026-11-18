# Job contract — the measured failure cases and the correct move

This page turns the audit of the 26.1-minute demo session into rules. It is the
"Why" behind `fenix job` and `fenix make`; read it once, then follow the card.

## The measurement

The session produced one 28 s demo. Its wall time was **26.1 min**:

| What | Count / time |
|---|---|
| Model generation | **26.0 min** (the wall clock) |
| Assistant messages | 170 |
| Repo edits / writes | **82** (`sfx.py` 16, `render_video.py` 16, 10 docs touched) |
| Exploration reads / greps / `ls` | 85 |
| Actual `fenix` commands | 32 |
| Renders | 1 full (173.9 s) + 4 remixes (~12 s) |
| QA passes | **8** (~30 s each) |
| Finishes | 3 (each re-ran QA) |

The renderer was not the bottleneck. **The session spent ~20 min building and
documenting a feature during a video job.** The cases below are ordered by that
cost.

## The opening move

```bash
./fenix job <project>          # human card
./fenix job <project> --json   # agent card
```

It returns, in one process and under a second:

- `mode` — `edit`, `motion`, `hybrid`, `longform`, `post`, `intake` or `new`;
- `ready` / `missing` — the exact files the job cannot start without;
- `warnings` — non-blocking gaps (e.g. no `brief.json`);
- `config` — the project overlay path, whether it exists and how many leaf
  overrides it applies (the effective values are in `params`);
- `params` — the effective `duration_sec`, `cuts`, `beat_policy`,
  `in_point_floor`, `transitions` and text mode after the overlay;
- `skills` — which skills to load and whether they are installed;
- `stages` — run/skip + reason for the edit/hybrid chain (empty otherwise);
- `order` — the ordered shot table: `locked`, `source` (`brief`/`plan`),
  `manual` (hand-authored cut list), `total_sec`, `shots[]`/`cuts[]`,
  `order_confirmed` and any failing rows;
- `render` — `skip`, `remix` or `full` for edit/hybrid; `none` otherwise;
- `master` — the current run video, when one exists;
- `next` — the exact command(s), in order;
- `gates` — what will verify the result;
- `rules` / `budget` — the five boundaries and the expected call budget.

Do not run `ls`, `grep`, `cat` or a stage to discover any of this. If the card
is wrong, fix the harness (`make.py`/`job.py`), not the job.

## Case 1 — a skill was re-implemented in `src/`

**Evidence.** 82 repo edits, 32 of them in `src/fenixsoft/stages/sfx.py` and
`src/fenixsoft/render/render_video.py`. A new SFX stage was written from the
skill's text during a demo.

**Argument.** A skill is the creative source of truth and is versioned outside
the repo. Re-deriving it in code (a) takes ~20 min, (b) ships untested logic
inside a render, and (c) forks the skill: the next session reads a different
implementation. A video job is an *execution* of skills + config, not a feature
project.

**Correct move.**

1. `fenix job` lists the skills; load the one for the step with the `skill`
   tool.
2. Run the stage: `fenix sfx <p>`, `fenix render <p>`, etc.
3. If a value is wrong, change `input/<p>/config.json` for a project-only
   requirement, `config/pipeline.json` for machinery, or `input/<p>/` for the
   creative facts — never the rendered file. `job` shows the effective values.
   `src/`.
4. If a capability is genuinely missing, **stop the video job** and open a
   separate repo task ("add X to the renderer"). Do not mix it with the piece.

**Acceptance.** `git status` (or `find -newer`) shows no `src/`, `tests/` or
`docs/` change for the video job.

## Case 2 — exploration instead of the job card

**Evidence.** 85 reads/greps/`ls` calls, 170 assistant messages. Most of them
were reconstructing state that is derivable: which artifacts exist, which are
stale, what the next command is.

**Argument.** Every exploratory call is a model round trip and a chance to act
on stale information. The card computes the same state deterministically and
includes the reason for each decision. Exploration is for *debugging a failed
gate*, not for opening a job.

**Correct move.**

```bash
./fenix job <p>                 # state + next command
./fenix <command> --help        # flags only
docs/agent/<topic>.md           # only when the card points at a gate
```

**Acceptance.** A normal job has **zero** exploratory calls before the first
pipeline command.

## Case 3 — full renders for audio-only changes

**Evidence.** 1 full render (173.9 s) plus 4 remixes; audio was still being
tuned after the picture was locked.

**Argument.** `--remix` copies the video stream and rebuilds only the mix
(~12–15 s). A full render re-runs the frame loop, encode and QA stills to change
a bed that never touches a pixel. Repeating it is pure loss.

**Correct move.**

```bash
./fenix sfx <p> --dry-run       # cue sheet first, no audio files
./fenix sfx <p>                 # build bus + bed (~3 s)
./fenix make <p>                # detects audio-only -> --remix automatically
```

`make` prints `render remix` and the input that moved. It decides this from the
run's `config_slices` (`picture`/`audio`) plus mtimes, so `delivery.dir` or a
docs edit cannot force a re-encode. Use `fenix render <p> --remix` only when
diagnosing.

**Acceptance.** No full render for a change whose only inputs are
`sfx_cue_sheet.json`, `sfx_bus.wav`, `music_bed.wav`, the voice or `sfx.*`
config.

## Case 4 — repeating QA and finish

**Evidence.** 8 QA passes (~30 s each) and 3 finishes in one session; `finish`
re-ran the QA that had just passed.

**Argument.** QA decodes the master (loudnorm + PCM + frame seeks). Running it
twice on byte-identical bytes cannot change the verdict; it only adds time and
two places to disagree. `finish` is the delivery gate and already owns a strict
QA.

**Correct move.**

```bash
./fenix make <p>                # runs QA once, then finish (reuses that PASS)
./fenix finish <p>              # reuses a strict PASS for the same bytes
./fenix finish <p> --re-qa      # only when the report is missing/stale
```

The reuse key is `video_bytes` + `video_mtime_ns`. A re-encode changes it and
forces a fresh gate.

**Acceptance.** One strict QA per master; `finish` prints
`reusing the strict PASS`.

## Case 4b — a full render to check text placement

**Evidence.** The S25 job spent a 33 s full render (~3 min) to discover a spec
sat over the settings list; then a 57 s comp to see the plate over the footage.

**Argument.** Placement is a still-frame property. Encoding 1,000 frames cannot
tell you more about it than one composited frame, and it costs two orders of
magnitude more.

**Correct move.**

```bash
./fenix render <p> --preview     # cut midpoints + every card, 3–5 s
./fenix comp <p> --preview       # layer placement per base cut, <10 s
```

Both write `output/<p>/preview/…` with a contact sheet and never create a run.
A card or a shot can name a safe-zone band (`header`/`lower`/`center`) instead
of a pixel `y`, so the fix is in the spec, not re-measured per render.

**Acceptance.** No full render (edit or comp) for a change that only moves text.

## Case 4c — re-rendering a comp to deliver it again

**Evidence.** `comp --deliver` re-encoded an unchanged 33 s composite because
`--reuse` had to be remembered.

**Argument.** Delivery is a copy job. The manifest already carries a
`fingerprint` of the spec plus resolved inputs.

**Correct move.** `fenix comp <p> --deliver` implies `--reuse`; an unchanged
spec delivers the existing run in ~0.5 s with no ffmpeg. `--force` re-encodes.
The post-master gate is `fenix qa --post --latest <p>` (or `make --post comp`),
which writes local evidence and never touches the shelf.

**Acceptance.** No ffmpeg in a repeat delivery; `qa --post` PASS before the
shelf copy.

## Case 5 — orchestration turn bloat

**Evidence.** 170 assistant messages for a 28 s piece; the chain was hand-run
stage by stage, with status reads between steps.

**Argument.** Each turn is a full model round trip. A deterministic chain does
not need an agent between every stage: the agent decides *whether* to run it,
`make` executes it and stops at the first failure.

**Correct move.**

```bash
./fenix make <p> --dry-run      # decide once
./fenix make <p>                # execute the whole chain
```

Batch independent reads in one shell call; do not poll a background render —
`make` is synchronous and prints the stage timings.

**Acceptance.** A routine change is ≤ 3 pipeline commands (job, make, review).

## Case 6 — installing a skill mid-job

**Evidence.** The session unzipped a skill, created
`~/.agents/skills/<name>/references/`, installed it, then debugged three failed
installs — all before the first render.

**Argument.** Skills are harness assets. Installing one is setup, not editing,
and it silently changes the tools available mid-piece. It is also the step that
tempts the agent to "just implement it here" (Case 1).

**Correct move.**

1. Install skills before the session, in `~/.agents/skills/<name>/SKILL.md`.
2. `fenix job` reports `[installed]` or `[MISSING]` for each required skill and
   `next` tells you to install the missing one.
3. If a skill is missing, stop; do not improvise its content.

**Acceptance.** The card shows every required skill as installed before the
first stage runs.

## Case 7 — rewriting docs during a video job

**Evidence.** 10 docs touched, including 22 edits to `commands.md`; docs were
updated while the demo was being mixed.

**Argument.** Docs are a deliverable with a different acceptance test (accurate
prose) than the video (pixels + gates). Mixing them inflates the diff, tempts
scope creep and makes the "why did this take 26 min" question unanswerable. It
also risks documenting a command that the video job has not finished testing.

**Correct move.** Finish the video, then run a **separate** docs task with its
own diff. During the video job, the only writable paths are `input/<p>/` and
`config/pipeline.json`.

**Acceptance.** The video job's change set contains no `docs/`, `README.md`,
`AGENTS.md` or `tests/`.

## Case 8 — guessing missing intake facts

**Evidence.** `demo_iphone14_blue` has no `brief.json`; the card now warns
"intake decisions are not recorded". The session inferred format, copy and pace
from previous projects.

**Argument.** Format, duration and tone are decisions, not defaults. Guessing
them produces a correct render of the wrong piece, and the mistake is only
visible after the encode.

**Correct move.**

```bash
./fenix job <p>                        # ready: no -> missing: brief.json
./fenix storyboard <p> --questions     # ask, then fill brief.json
./fenix storyboard <p>
./fenix make <p>
```

If the card says `ready: no`, stop and ask. Do not start a render to "see how it
looks".

**Acceptance.** `ready: yes` before the first stage; `brief.json` exists for an
edit project.

## When repo work is legitimate

Repo work is a first-class task — just not *inside* a video job. Open it
separately when:

- a gate fails twice with the same measurement (a fact is missing or code is
  wrong);
- the brief needs a capability the pipeline does not have;
- the user asks to change the harness/skills/docs.

In that task, the normal rules apply: tests, docs, review. The video job stays
parked with its artifacts intact.

## Expected budget for a routine change

| Signal | Budget |
|---|---|
| `fenix job` calls | 1 |
| exploratory shell/read calls | 0 before the first pipeline command |
| `src/`, `docs/`, `tests/` edits | 0 |
| full renders | 1 (0 if audio-only) |
| QA passes | 1 per master |
| finishes | 1 |
| pipeline commands | ≤ 3 |

If a session exceeds a budget, stop and report which case above it hit. That is
the signal the harness needs another fix, not another workaround.
