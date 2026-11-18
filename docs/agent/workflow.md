# Agent workflow

The loop is: **job card → measure → choose → validate → render → inspect →
gate → deliver.** Every phase has an input, a command, an output and an
acceptance test. If the acceptance test fails, fix that phase; do not skip
ahead.

## Opening move — `fenix job`

```bash
./fenix job <project>          # human card
./fenix job <project> --json   # agent card
```

Accept: the card names the mode, `ready`/`missing`, required skills, stale
stages, render mode and the exact next command. Do not `ls`/`grep` the repo to
reconstruct this state, and do not start a render if `ready: no`. The full
contract (including measured failure cases) is
[job-contract.md](job-contract.md).

## Before Phase 0 — footage intake (optional)

If the clips arrive as a messy folder, run `fenix label` before naming the
project:

```bash
./fenix label scan <folder>
# review <folder>/.fenix-label/grids/, fill names.tsv
./fenix label apply <folder> --dry-run
./fenix label apply <folder>
```

Accept: `scan.json` has facts for every file, duplicates are flagged, and the
apply manifest + `undo-*.sh` exist. The work directory is disposable and lives
inside the scanned folder.

## Phase 0 — intake

```bash
./fenix doctor
./fenix lint <sku>
```

Accept: `doctor` exits 0; `lint` has no FAIL. Warnings (brand phonetics) need a
written reason or a `display_map`/`display_merge` entry in `sku.json`.

If `lint` fails `s_words`, cut the script. Do not raise the tempo ceiling to make
a long script fit: the read becomes rushed and QA will show it in caption CPS.

`doctor` and `job` also name the asset state, and the two classes are different:

| Class | Examples | Where it goes |
|---|---|---|
| tracked runtime assets | fonts, motion textures, `sfx/library.json`, GSAP | already in `assets/`; never edit per job |
| local media | music bed, SFX samples, model weights | `assets/music/`, `assets/sfx/`, `.cache/` |
| project inputs | clips, voice, specs | `input/<project>/` |

If a music bed or SFX library is missing, ask the user for the file or run
`./fenix sfx --install-library`; do not put a repo asset under `input/` and do not
invent media. The full map is [assets/README.md](../../assets/README.md).

## Phase 1 — look at the footage

```bash
./fenix beats <sku>
./fenix frames <sku>
```

Accept: `candidates.json` exists with at least one window per clip;
`keyframe_report.json` has no all-dark clips.

Inspect a few `input/<sku>/keyframes/*.jpg` and reject takes that are blurry,
dark, tilted or show the wrong product. Vision is for **identity and framing**,
not for numbers: sharpness, luma and motion come from
`keyframe_report.json` / `candidates.json`.

## Phase 2 — voiceover timing

```bash
# optional: generate the voiceover first (ElevenLabs/OpenAI keys in env)
./fenix tts <sku>
./fenix align <sku>
```

`fenix tts` reads `input/<sku>/tts.json` and writes `voice.mp3`; keys are read
from `ELEVENLABS_API_KEY` / `OPENAI_API_KEY` and never printed to disk. Run
`lint` first so a bad script does not spend a TTS credit.

Accept: `word_timings.json` and `narration_lines.json` exist; the printed script
similarity is ≥ `alignment.min_script_similarity` (0.55). If the tail guard
warns that the voice ends mid-word, replace or re-render the voiceover, do not
trim the video.

## Phase 3 — choose the edit shape

Optional, but preferred for anything that ships:

```json
// input/<sku>/picks.json
{"lines": [
  {"line": 0, "candidate": "01c-b"},
  {"line": 3, "clip": "04_box.mp4", "candidate": "04c-a"}
]}
```

```bash
./fenix plan <sku>
./fenix validate <sku>
```

Accept: `plan_edit.json → checks` all PASS, then `validate` prints
`RESULT: PASS`. Use the candidate IDs from `candidates.json`; never type `in` /
`dur` unless you are deliberately overriding a measured window.

Decision table:

| Decision | Source of truth |
|---|---|
| When a line starts/ends | `narration_lines.json` (align) |
| Which clip for a line | `candidates.json` shortlist + `picks.json` |
| In-point | candidate ID (measured window) |
| Cut length | planner, snapped to `beat_grid.json` |
| Caption text | `caption_audit.json` after render |
| Is it good? | QA + regression + the review sheet |

## Phase 4 — sound design

```bash
./fenix sfx <sku> --install-library   # first time on this machine
./fenix sfx <sku> --dry-run           # review the cue sheet before audio
./fenix sfx <sku>                     # cue sheet + SFX bus + music bed
```

Accept: `sfx_report.json` has `RESULT: PASS` (or warnings you can explain).
Read the cue sheet as a table before listening: every cut needs an
anticipation row, every keyword accent needs a tactile row, and subs must be at
least 1.5 s apart. The stage refuses a sheet with no pre-lap, stacked subs, a
major drop without a vacuum, or a bus above −1 dBFS.

The music bed is normalised to `audio.music_bed_lufs`, ducked under foreground
transients and vacuumed before major flips. Re-run `fenix sfx` after any
`fenix plan` or `edit_plan.json` edit; the renderer rejects a stale
`edit_plan_sha256`.

## Phase 5 — render

```bash
./fenix render <sku>
```

Accept: exit 0 and a new `output/<sku>/<sku>_<stamp>/` directory. A refused
render (soft in-point, impossible cut) removes its own incomplete run; fix the
plan and re-run. Use `--draft` for a fast review encode, then one full render;
a draft is never a delivery master. `--allow-soft-in` is a deliberate
exception, not a workaround.

## Phase 6 — review the piece

Read, in this order:

1. `qa_report.json` — every row has `measured`, `threshold`, `fix`. Fix failures
   at their source.
2. `caption_audit.json` — read the cards as text; CPS, width and duration are
   already computed. Do not OCR frames to find a caption problem.
3. `contact_sheet.png` — one frame per cut, eight tiles across the runtime.
4. `qa_frames/*.png` — full-resolution frames at sampled points for framing,
   clipping and text overflow.

What vision may decide: product identity, framing, obvious clipping/overlap,
whether a frame is unusable. What vision must not decide: luma, sharpness, CPS,
beat offset, duration, safe-zone containment — those are already numbers.

## Phase 7 — gate and deliver

```bash
./fenix qa --latest <sku> --strict
./fenix eval <sku> --skip-align --skip-render
./fenix deliver --latest <sku>
```

Accept: QA prints `RESULT: PASS` with 0 warnings; eval reports no regression
(or the regression is understood and accepted by the user); delivery copies the
run to the configured shelf. Report the delivery path, repo path, size and
duration.

## Post-production paths

These are assembly/finishing modes, not a creative re-edit. The spec-first rule
is the same: author the JSON, validate, render, inspect the manifest/ffprobe.

**Motion plate**

```bash
./fenix storyboard <project> --questions
./fenix motion <project> --still 1.5     # review one frame
./fenix motion <project> --deliver       # MP4 or alpha MOV -> delivery shelf
```

Accept: `manifest.json` exists, `ffprobe` reports the expected codec/size/
duration, and alpha is present when `background: null`.

**Tracking**

```bash
./fenix track <project>          # -> output/<p>/track/track_<stamp>.json
```

Accept: points have a useful match score; point a comp layer at the JSON with
`"track": ...`. This is template matching, not planar tracking.

**Compositing**

```bash
./fenix track <project>
./fenix comp <project> --deliver
```

Accept: `comp/manifest.json` layer count matches the spec; the base and every
layer resolve; watch the composite for edge/key artefacts.

**FX**

```bash
./fenix fx <project> --deliver
```

Accept: `fx/manifest.json` effect list matches the spec; seeds are fixed, so a
re-run is deterministic; watch the affected frames.

**Grade and scopes**

```bash
./fenix grade <project> --deliver
./fenix scopes <project> --input output/<p>/grade/<run>/graded.mp4
```

Accept: `grade/manifest.json` records the filters, shot-match gains and
secondary count; compare `before.png` and `after.png`; read the histogram/
vectorscope/waveform and `signalstats` before accepting the look.

**Long-form**

```bash
./fenix longform <project> --deliver
```

Accept: `longform/manifest.json` duration equals the sum of chapter out−in
(minus `transition_sec` overlaps); read `chapters.md`; watch the joins and set
`transition`/`transition_sec` when a cut needs an `xfade`.

Full field tables: [specs.md](../reference/specs.md). These commands have no
30-check QA gate; the acceptance evidence is the manifest + ffprobe + your review.

## Failure protocol

1. Find the failing row and read its `fix`.
2. Change the **input** (script, `picks.json`, `edit_plan.json`, config) or the
   stage that produced the bad value — never the rendered file.
3. Re-run only the failing stage and its gate.
4. Two identical failures in a row mean a fact is missing: stop and report what
   you measured. Do not brute-force random re-renders.
5. Never delete a failed run to make `runs.json` look clean. A failed render is
   evidence.

## What "done" means

- [ ] `./fenix doctor` clean.
- [ ] `./fenix test` green.
- [ ] `./fenix sfx <sku>` → `RESULT: PASS`; cue sheet has anticipation,
  tactile and impact layers and no stacked sub hits.
- [ ] `./fenix qa --latest <sku> --strict` → `RESULT: PASS`, 0 warnings.
- [ ] `./fenix eval <sku> --skip-align --skip-render` → no regression.
- [ ] Contact sheet has no dark, blurry or wrong-product frame.
- [ ] Captions read correctly at native size and stay inside the safe zone.
- [ ] Delivery (only if asked) copied and reported with its delivery path.

For a **motion plate**: spec valid, `--still` reviewed, `ffprobe` (codec,
size, duration, alpha when requested).
For **track**: points have a useful score and the comp consumes the JSON.
For **fx**: spec valid, manifest written, seeds fixed, affected frames watched.
For **comp / grade / longform**: spec valid, manifest written, `ffprobe`
matches, and the result was watched; there is no 30-check QA gate.
For **scopes**: the PNGs and `signalstats` were read; no deliver step.

## Delegation and context

Long research (library behaviour, "where is X", impact analysis) goes to a
subagent that loads the `delegated-research` skill; the main session keeps only
the digest. Use the codebase graph before grep. Never delegate the same question
twice in a session.
