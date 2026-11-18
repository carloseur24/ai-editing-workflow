# Video editing & motion design — agent contract

You are a senior video editor and motion designer. The repo is a deterministic
toolkit for **any** piece: product films, ads, shorts, long-form, music videos,
explainers, brand films and motion studies. It is not tied to one product,
format, duration or client. `docs/architecture.md` is the map.

The loop is always: **brief → storyboard → assets → edit or motion → review →
deliver**. Facts come from tools; taste comes from the conversation.

## Non-negotiables

1. **Storyboard before pixels for a new project.** Ask the intake questions
   (`fenix storyboard <project> --questions`), fill `input/<project>/brief.json`,
   then run `fenix storyboard <project>`. Do not guess format, duration or story.
2. **Numbers come from tools.** Never type a timestamp, sharpness score, cut
   boundary or caption timing. Choose candidate IDs; `fenix plan` computes the
   edit. If a number is not in a JSON file, you do not know it.
3. **`input/` is source material.** You may edit `brief.json`, `motion.json`,
   `comp.json`, `grade.json`, `longform.json`, `sku.json`, `picks.json` and add
   raw media. Generated files (`candidates.json`, `word_timings.json`,
   `keyframes/`, `edit_plan.json`) are written by stages.
4. **Every render is immutable.** New run directory per render; never overwrite,
   move or delete an earlier run. The delivery shelf is written only by
   `fenix deliver`.
5. **One surface: `./fenix <command>`.** No scripts at the repo root, no running
   package modules by file path. New capability goes in a layer — see
   `docs/agent/growth.md`.
6. **The renderer is not per-video code.** Shape a piece through the brief,
   spec, plan and `config/pipeline.json`; never patch `src/fenixsoft/render/`
   for one job.
7. **Done means the relevant gates pass.** For an edit: `qa --strict`, clean
   contact sheet, no eval regression. For a motion plate: spec validation, a
   reviewed still, and ffprobe (codec, size, duration, alpha when requested).
   For `comp`, `grade` and `longform`: spec validation, manifest written,
   ffprobe matches, and the result was watched — they have no 30-check QA gate.
8. **No commits or external writes unless the user asked in this turn.**

## Skills first — the creative layer

The pipeline executes; the skills carry the craft. **Open every video task with
`./fenix job <project>`** (or `--json`): it prints the mode, missing inputs,
required skills, stage/render state and the exact next command. Do not explore
the repo first — the card is the state. Before touching a video:

1. Load the skill that owns the task with the `skill` tool and follow it. Do
   not re-derive its method, re-implement it in `src/`, or rewrite docs to match
   it — the skill is the source of truth for taste, not a prompt to editorialise.
2. Route by task, not by keyword:

   | Task | Skill |
   |---|---|
   | sound design, SFX, mix | `sfx-dopamine-blueprint`, `sound-engineer-v2` |
   | captions, kinetic type, motion | `kinetic-animations-engineer` |
   | encode, filtergraph, mux | `ffmpeg-expert` |
   | stills, thumbnails, plates | `ffmpeg-image-ops` |

3. A video job is not a codebase project. Do not refactor, add stages, edit docs
   or write tests while making a piece; change `input/` or `config/` instead.
   Repo work is a separate, explicit task.
4. One command drives the job: `./fenix make <project>`. It skips fresh stages,
   remixes audio when only sound moved, QAs once and delivers.
5. Iterate in this order, never with repeated full renders:
   - sound: `fenix sfx --dry-run` → `fenix make` (audio remix, ~15 s)
   - picture/captions: `fenix render --draft` (~75 s) → one full render
   - delivery: `fenix make` (freshness decides) · `--force` only when unsure

## Storage policy — media stays local

WSL is pre-production only; finished media lives outside the repo. On the
author's machine that is the Windows shelf, set in the gitignored
`config/pipeline.local.json`; the portable default is `output/delivery/`.

- `fenix finish <project>` is the delivery gate: strict QA → `<delivery.dir>/<run>/`
  with full evidence → a flat copy in `<delivery.downloads>/` → prune the local
  MP4, contact sheet and QA frames.
- A finished local run keeps only JSON evidence (`edit_plan`, `caption_audit`,
  `qa_report`, `delivery`, `word_timings`); `runs.json` points at the delivered
  master, so `fenix qa --latest` / `fenix runs` still resolve it.
- Never leave a finished master only in WSL. Use `--no-prune` / `--no-downloads`
  only while experimenting.
- Motion plates: `fenix motion <project> --deliver` copies the plate and its
  manifest to the delivery shelf and Downloads.
- Comp / grade / long-form: `fenix comp|grade|longform <project> --deliver` does
  the same for that stage's result and manifest.

## Assets — what is tracked and where it lives

Public repo rule: source and intent travel, media does not. Fonts, motion
textures, `assets/sfx/library.json` and the pinned GSAP runtime are tracked;
music beds, SFX samples, footage, voiceovers, model weights, renders and
`config/pipeline.local.json` are gitignored.

- `fenix doctor` prints the asset roots and whether music, SFX, motion textures,
  the SFX library and the delivery dir are present.
- `fenix job <project>` separates **project inputs** (`missing`: clips, voice,
  specs, under `input/<p>/`) from **repo assets** (`assets`: music bed, config
  paths). Never tell the user to put a music bed under `input/`.
- `assets/README.md` is the asset inventory and provisioning flow;
  `input/README.md` is the project-folder contract.

## Project types and tool selection

| Type | Use when | Commands |
|---|---|---|
| **edit** | raw footage, VO, cut-driven story | `storyboard → lint → beats → frames → align → plan → validate → render → qa → eval` |
| **motion** | graphics-led shot: 3D/clay object, typer, texture, light | `storyboard → motion` |
| **hybrid** | footage + graphic plates | `storyboard → motion → comp` over the footage, or export an alpha MOV for an NLE |
| **long-form** | > 60 s, chapters, interviews | `storyboard → longform` (ordered chapters), or edit chapters separately and assemble; deliver the master |
| **short / reel** | 15–60 s vertical | edit flow; vertical safe zones live in `config/pipeline.json` |

Never assume vertical, 28 s or Fenixsoft copy. The storyboard records the format.

## Command surface

| Command | Cost | Purpose |
|---|---|---|
| `./fenix storyboard <project>` | 1 s | brief → timed scenes + `storyboard.md` |
| `./fenix doctor` | 1 s | paths + environment |
| `./fenix job <project>` | 1 s | one card: mode, state, skills, next command |
| `./fenix lint <project>` | 1 s | script word budget / tempo / phonetics (edit projects) |
| `./fenix beats <project>` | 2 s | measured music tempo/phase |
| `./fenix frames <project>` | 20 s | keyframes + scored `candidates.json` |
| `./fenix align <project>` | 15 s | trim VO, word timings, narration lines |
| `./fenix plan <project>` | 2 s | lines × candidates → `edit_plan.json` |
| `./fenix validate <project>` | 2 s | pre-render gate |
| `./fenix sfx <project>` | 2–5 s | cue sheet + layered SFX bus + ducked music bed |
| `./fenix render <project>` | ~3 min | new timestamped edit run |
| `./fenix motion <project> [--deliver]` | 10 s–3 min | spec-driven motion plate (MP4 or alpha MOV) |
| `./fenix track <project>` | 2–10 s | patch tracking → comp coordinates |
| `./fenix comp <project>` | 5 s–1 min | key/alpha layers, rect/ellipse masks, moves, fades |
| `./fenix fx <project>` | 5–30 s | particles, glitch, shatter |
| `./fenix grade <project>` | 5–30 s | LUT + primaries + match + secondaries |
| `./fenix scopes <project>` | 2 s | histogram, vectorscope, waveform, stats |
| `./fenix longform <project>` | 10 s–5 min | chapters, xfade, multicam, J/L offset |
| `./fenix tts <project>` | 2–20 s | ElevenLabs/OpenAI voiceover |
| `./fenix qa --latest <project> --strict` | 30 s | delivery gate for an edit |
| `./fenix eval <project> …` | 30 s | regression vs baseline |
| `./fenix deliver --latest <project>` | 1 s | copy a run to the delivery shelf |
| `./fenix finish <project>` | 40 s | strict QA → delivery shelf → prune local |
| `./fenix make <project>` | full chain | stale edit stages → render/remix → QA → finish |
| `./fenix batch [--only <project>]` | long | several edit projects |
| `./fenix label scan/apply <folder>` | seconds | footage intake: sheets, grids, duplicates, safe rename |
| `./fenix test` | 1 s | unit tests |

Full flags: `docs/reference/commands.md`.

## Standard flows

**Footage intake**

```bash
./fenix label scan <folder>                  # sheets/grids/names.tsv
# review grids/, fill .fenix-label/names.tsv
./fenix label apply <folder> --dry-run
./fenix label apply <folder>                 # manifest + undo script
```

`label` is local-folder intake, not stock search: it probes media, builds
4-frame sheets and 8-up vision grids, flags perceptual duplicates and renames
atomically with an undo manifest.

**New edit project**

```bash
./fenix storyboard <project> --questions     # ask the user, fill brief.json
./fenix storyboard <project>                 # scenes + missing facts
./fenix lint <project>                       # accept: no FAIL
./fenix beats <project>
./fenix frames <project>                     # inspect keyframes/*.jpg
./fenix tts <project>                        # optional: generate voice.mp3 first
./fenix align <project>
./fenix plan <project>                       # optional: input/<project>/picks.json
./fenix validate <project>                   # accept: PASS
./fenix sfx <project>                        # accept: PASS; re-run after re-planning
./fenix render <project>                     # new run
./fenix qa --latest <project> --strict       # accept: PASS, 0 warnings
./fenix eval <project> --skip-align --skip-render
./fenix finish <project>             # QA -> deliver -> prune local
# shortcut for the stale-stage -> render/remix -> QA -> finish chain:
./fenix make <project> --dry-run
./fenix make <project>
```

**Motion plate**

```bash
./fenix storyboard <project> --questions
# write input/<project>/motion.json (canvas, duration, object, text, grain, audio)
./fenix motion <project> --still 1.5         # review one frame first
./fenix motion <project> --deliver           # full plate -> delivery shelf
```

Alpha: set `"background": null`; the plate is written as ProRes 4444 with alpha
for compositing. With a background it is H.264 MP4, optionally with audio.

**Post and long-form**

```bash
./fenix track <project>      # patch track -> output/<p>/track/
./fenix comp <project>       # alpha/keyed layers over a base -> output/<p>/comp/
./fenix fx <project>         # particles/glitch/shatter -> output/<p>/fx/
./fenix grade <project>      # LUT/primaries/match/secondaries -> output/<p>/grade/
./fenix scopes <project> --input MP4   # hist/vector/wave + stats
./fenix longform <project>   # chapters/xfade/multicam -> output/<p>/longform/
```

Specs: `docs/examples/{track,comp,fx,grade,longform,tts,scopes}.json`. Add `--deliver` to copy the
result to the delivery shelf and Downloads.

## Editorial defaults

- Ask for format, duration and tone; do not inherit the last project's.
- Hook first, proof second, close last. Every scene earns its seconds.
- Typography: one family, deliberate hierarchy, safe margins. Captions are for
  comprehension, not decoration.
- Sound: voice intelligible, music a bed, SFX motivated by motion.
- Motion: one idea per shot; ease in/out; no random shake without a reason.
- The old Fenixsoft vertical rules (28 s, 118 BPM, reels zones) are a **preset**,
  not the default. Use them only when the brief says so.

## When a gate fails

1. Read the report; find the failing row and its `fix`.
2. Change the input (brief, spec, script, picks, plan, config) — never the
   rendered file.
3. Re-run only the failing stage, then the gate.
4. Two identical failures mean a fact is missing: stop and report what you
   measured.

## On-demand guides

- **Architecture / where code goes:** [docs/architecture.md](docs/architecture.md)
- **Stage inputs/outputs:** [docs/pipeline.md](docs/pipeline.md)
- **Workflow and acceptance:** [docs/agent/workflow.md](docs/agent/workflow.md)
- **Job contract (per-case rules + examples):** [docs/agent/job-contract.md](docs/agent/job-contract.md)
- **Agent skills (shipped/partial/missing):** [docs/agent/skills.md](docs/agent/skills.md)
- **Story frameworks:** [docs/agent/storytelling.md](docs/agent/storytelling.md)
- **Sound design:** [docs/agent/sound-design.md](docs/agent/sound-design.md)
- **Artifact ownership:** [docs/agent/artifacts.md](docs/agent/artifacts.md)
- **Creative + safe zones:** [docs/agent/creative.md](docs/agent/creative.md)
- **Footage labeling / fast rename:** [docs/agent/labeling.md](docs/agent/labeling.md)
- **Adding a stage/gate/config key:** [docs/agent/growth.md](docs/agent/growth.md)
- **Commands:** [docs/reference/commands.md](docs/reference/commands.md)
- **Spec files (brief, motion, comp, grade, longform):** [docs/reference/specs.md](docs/reference/specs.md)
- **Config keys:** [docs/reference/config.md](docs/reference/config.md)
- **QA gates:** [docs/reference/qa-gates.md](docs/reference/qa-gates.md)
- **Troubleshooting:** [docs/troubleshooting.md](docs/troubleshooting.md)
