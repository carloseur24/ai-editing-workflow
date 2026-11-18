# Projects — `input/<project>/`

`input/` is human/agent intent. Media and derived artifacts are gitignored, so
the public repo carries the plan, not the footage. Every folder here is one
piece: an edit, a motion plate, a hybrid, a long-form assembly or a post job.

## Start here

```bash
./fenix job <project>                    # mode, missing inputs, skills, next command
./fenix storyboard <project> --questions # intake questions for a new piece
# fill input/<project>/brief.json
./fenix storyboard <project>             # timed scenes + missing facts
```

`fenix job` is the state card. Do not inspect the repo to reconstruct it:
`ready`, `missing` (project inputs), `assets` (repo-wide assets such as the music
bed), `stages`, `render` and `next` are computed from the files.

## Folder anatomy

| File / folder | Class | Written by | Purpose |
|---|---|---|---|
| `brief.json` | source | human / agent | format, duration, story, assets, deliverables; see [specs](../docs/reference/specs.md) |
| `config.json` | source | human / agent | optional sparse pipeline overlay: only this piece's duration, cuts, beat policy, safe zones, audio or `post` flags; see [config](../docs/reference/config.md#project-overlay-inputprojectconfigjson) |
| `sku.json` | source | human / agent | title, price, badges, footer, script, voice filename |
| `picks.json` | source | human / agent | optional clip/candidate choice per narration line |
| `motion.json`, `comp.json`, `fx.json`, `grade.json`, `track.json`, `longform.json`, `tts.json` | source | human / agent | optional post/motion spec for that mode |
| `clips/*.mp4` | **local media** | human | footage; ignored by git |
| `voice.mp3` (or `voiceover` in `sku.json`) | **local media** | human / `fenix tts` | voiceover; ignored by git |
| `candidates.json` | derived | `fenix frames` | scored windows and stable candidate IDs |
| `edit_plan.json` | derived | `fenix plan` | the cut contract |
| `keyframes/`, `keyframe_report.json` | derived | `fenix frames` | review stills |
| `word_timings.json`, `narration_lines.json`, `_voice*.wav` | derived | `fenix align` | timing, not source |
| `.asr_cache.json` | derived | `fenix align` | transcript cache |

Only the small intent files are tracked: `brief.json`, `sku.json`, `picks.json`,
the optional post specs and notes such as `script_vo.md`. Everything else is
regenerated or sourced locally.

## What goes where

| You have… | Put it in… | Then |
|---|---|---|
| raw vertical footage | `input/<project>/clips/` | `fenix frames`, then `fenix plan` |
| a recorded or generated voiceover | `input/<project>/voice.mp3` | `fenix align` |
| a script only | `sku.json → script_text` | `fenix lint`, then `fenix tts` |
| a music bed | `assets/music/` (not here) | `fenix beats`, `fenix sfx` |
| SFX samples | `assets/sfx/` or your `sfx.library_dir` | `fenix sfx` |
| a pipeline value true only for this piece | `input/<project>/config.json` | `fenix job` shows it; `make` hashes it |
| fonts / motion textures | already in `assets/` | `render`, `motion` |

Repo-wide assets are documented once in [assets/README.md](../assets/README.md).
A missing music bed is an **asset gap**, not a project input; `fenix job` says so
and prints the repo path.

## Example

`input/example_iphone14/` carries a complete `brief.json` and `sku.json` for a
28 s vertical product demo, with no media. It is the reference for a new edit
project: copy the two JSON files, replace the facts, add your footage and VO, and
run `fenix job <name>`.
