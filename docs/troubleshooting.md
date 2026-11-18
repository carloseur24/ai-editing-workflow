# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `[sku] not found` | SKU name/path is wrong | `./fenix doctor` lists the known SKUs; pass the folder name or `input/<sku>` |
| `ModuleNotFoundError: fenixsoft` | running a module directly (`python src/...py`) or without the launcher | use `./fenix <command>`; the launcher sets `PYTHONPATH=src` |
| `Missing voice.mp3` | filename does not match `sku.json → voiceover` | rename the file or update the field |
| Whisper download warnings | model not cached yet | harmless on first run; set `HF_TOKEN` to raise rate limits |
| `script_lint` fails `s_words` | script longer than 84 words at 1.12× | cut words; do not raise `script.max_tempo` to force a fit |
| `script_lint` fails `s_speakable` | digits/symbols/URLs in `script_text` | write numbers in words; use `display_map` for captions |
| `validate` fails `in_point_floor` | in-point below 60 % of the clip median | move `in` to the printed `best_window()` or pick another candidate ID |
| `validate` fails `window_floor` | the shot is soft after its first frame | re-run `fenix plan` (scores whole shots) or choose a better candidate |
| `validate` fails `unknown_candidate` | candidate ID is not in `candidates.json` | use an ID from the manifest; do not invent one |
| `render` exits “sustained-quality floor” | stale plan predates the window gate | re-run `fenix plan`; `--allow-soft-in` only if intended |
| `render` says “Missing voice_28s.wav” | align has not run | `fenix align <sku>` |
| `qa` fails `q_run_versioned` | legacy flat render (`output/<sku>/<sku>_final.mp4`) | re-render; every new render lands in its own run |
| `qa --strict` fails `w_layout` | renderer warning (text too wide, fast card) | read the row's `fix`; usually shorten the copy |
| Captions read too fast | dense voiceover | read `caption_audit.json`, shorten the script or merge cards |
| `p_beat_offset` warns | plan generated on an old/nominal grid | `fenix beats <sku>` then `fenix plan <sku>` |
| Render > 15.5 MB | CRF 18 exceeded the WhatsApp budget | automatic CRF 21 fallback; check `v_size_budget` |
| Different music bed | config points at the old file | drop the file in `assets/music/` and update `music.file`, then `fenix beats <sku>` |
| Re-tune captions only | `sku.json` changed | re-run `fenix align <sku>` (ASR cache hit), then plan/render |
| Re-tune audio only | mix changed | `fenix render <sku> --remix` (~13 s), then QA |
| Clips look soft | source footage is 720×1280 | shoot 1080×1920; the pipeline cannot invent detail |
| `fenix job` says `ready no` and lists `missing` | a project input is missing | add the named file under `input/<p>/`; do not render |
| `fenix job` lists `assets` | a repo asset is missing (usually the music bed) | add it at the printed path in `assets/`, or set `music.file` in `config/pipeline.local.json`; it is not a project input |
| `doctor` says `no music bed` | public clone has no licensed music | drop a 118/120 BPM track in `assets/music/`, or point `music.file` at one |
| `doctor` says `SFX library not installed` | procedural core not generated | `./fenix sfx --install-library` (default `assets/sfx/fenix_sfx/`, gitignored) |
| `doctor` says `delivery dir missing` | first run, or a stale Windows path | it is created on first `deliver`; otherwise set `delivery.dir` in `config/pipeline.local.json` |
| `fenix job` shows a skill as `MISSING` | skill not installed in the harness | install it under `~/.agents/skills/<name>/SKILL.md` before the stage |
| `fenix job` says `render full` after an audio-only change | a picture input or config is newer than the run | `make` will still remix if only audio moved; check the listed `why` |
| `fenix job` says `no-op` but you need a rebuild | mtime says everything is current | `./fenix make <project> --force` |
| `fenix job` mode is unexpected | precedence is longform > motion/hybrid > comp/grade > edit | remove the stale spec or put it in the right project |
| `fenix make` skips a stage that should rerun | mtime is older after a copy/edit-back | `./fenix make <project> --force` |
| `fenix make --draft` did not deliver | drafts are never delivery masters | run a normal `fenix make` or `fenix render` + `fenix finish` |
| `fenix test` fails on `test_context` | `input/` sample SKUs were removed | keep at least one `input/<sku>/sku.json` or adjust the test |
| `.cache/` huge | models + normalised clips | delete `.cache/`; the next run rebuilds (models re-download) |
| `sfx` fails `sd_jcut` | a whoosh/riser starts after the cut | move the cue earlier; `jcut_min_ms` is the floor |
| `sfx` fails `sd_sub_gap` | two major subs inside 1.5 s | drop or move one sub; contrast needs recovery |
| `sfx` fails `sd_vacuum` | a major drop has no 80–150 ms gap | set `vacuum_ms` on that cue |
| `sfx` fails `sd_peak` | foreground bus above −1 dBFS | lower bus gain or `sfx.bus_peak_db` |
| `sfx` says “no source for role” | procedural core not installed or wrong library | `./fenix sfx <sku> --install-library`; check `sfx.library_dir` |
| `render` warns “cue sheet is stale” | `edit_plan.json` changed after `fenix sfx` | re-run `fenix sfx <sku>` |
| QA `a_true_peak` fails after remix | inter-sample peak above −1.0 dBTP | lower `audio.limiter_ceiling` (0.78 = −2.16 dBFS) |
| `comp` invalid spec | base/layer/mask/duration missing | read the printed spec error; all `src` paths must exist; only `rect` masks |
| `comp` key edge is dirty | chroma similarity too tight/loose | tune `layers[].key.similarity` / `blend`; light the screen evenly |
| `grade` invalid spec | input/LUT/reference missing | fix the path; `.cube` only; shot-match reference must exist |
| `grade` match looked wrong | global per-channel gain, not a secondary | lower `shot_match.strength` or remove the match |
| `longform` invalid spec | chapter source/`out`/audio missing | `out > in`; every source and the global audio must exist |
| `longform` joins feel hard | no transitions in assembly mode | cut chapters with handles, or use `comp`/an NLE for crossfades |
| `motion` invalid spec | no preset/object/duration | use `brain`, `sphere`, `torus` or `blob`; `duration_sec > 0` |
| `motion` alpha looks black | player/ffprobe reports `yuv420p` | decode a frame and check alpha; deliver the `.mov`, not a re-encoded MP4 |
| `track` points are flat/low score | template/search/threshold wrong or patch not visible | choose a textured patch, widen `search`, lower `threshold`; it is template matching, not planar tracking |
| `fx` invalid spec | input/duration/effect type missing | fix the path; `end > start`; type must be `particles`, `glitch` or `shatter` |
| `scopes` says “pass --input” | no input in `scopes.json` or flag | `fenix scopes <p> --input path.mp4` |
| `tts` says no key | neither provider key is in the environment | export `ELEVENLABS_API_KEY` or `OPENAI_API_KEY`; never put keys in JSON |
| `tts` HTTP error | provider rejected the request | read the HTTP detail; check voice/model/plan |
| `grade` secondary rejected | colour is not `#RRGGBB` | fix `secondaries[].color`; secondaries are hue/sat only |
| `longform` unknown transition | typo in `transition` | use one of `fade`, `wipe*`, `slide*`, `circleopen/close`, `dissolve`, `pixelize` |
| `label scan` finds nothing | wrong folder or nested media | pass the folder, add `--recursive`; `.fenix-label/` is skipped |
| `label apply` name conflict | target exists or two rows map to one name | read the error, run `--dry-run`; use `--exact` only when the name is already safe |
| `label undo` missing manifest | work dir deleted or apply never ran | pass `--manifest renames-*.csv`; the work dir is disposable |
| `label` renames feel wrong | agent labels were poor | inspect `grids/` + `scan.json`; re-run `apply` after fixing `names.tsv` |
| `comp`/`grade`/`longform` not in `runs` | they are post stages, not edit runs | read their `manifest.json`; use `--deliver` for the delivery shelf |

## Reading a failure correctly

- A gate failure is a fact about the artifact, not an opinion. Open the JSON and
  read `measured`, `threshold`, `fix`.
- If the same command fails twice identically, the input is wrong; changing the
  command will not help.
- Do not “fix” a failure by deleting a run or editing a report. The run is
  evidence and the report is regenerated.

## Where to look first

| Question | File |
|---|---|
| What does the job need next? | `./fenix job <p> --json` |
| Is the script too long? | `output/<sku>/script_lint.json` |
| Is the music grid right? | `output/<sku>/beat_grid.json` |
| Why is this cut here? | `input/<sku>/edit_plan.json`, `output/<sku>/plan_edit.json` |
| Is the shot sharp? | `output/<sku>/plan_validation.json` |
| Why did QA fail? | `output/<sku>/<run>/qa_report.json` |
| Are captions readable? | `output/<sku>/<run>/caption_audit.json` |
| Is the sound design balanced? | `output/<sku>/sfx_report.json`, `sfx_cue_sheet.json` |
| Did comp/grade/longform run? | `output/<p>/{comp,grade,longform}/<run>/manifest.json` |
| Is the motion plate alpha? | `output/<p>/motion/<run>/manifest.json` |
| Is the track stable? | `output/<p>/track/track_<stamp>.json` |
| Did fx render? | `output/<p>/fx/<run>/manifest.json` |
| Is the grade in range? | `output/<p>/scopes/scopes_<stamp>/scopes.json` |
| Are the footage labels ready? | `<folder>/.fenix-label/{scan.json,names.tsv,grids/}` |
| Did the last change regress? | `eval/<sku>_<stamp>.json` |

## HyperFrames overlay

### `--hf-overlay` renders a black box

FFmpeg's native VP9 decoder ignores sidecar alpha. The renderer uses
`-c:v libvpx-vp9` before `-i` for `.webm` transports; the preferred transport is
plain RGBA PNG states listed by `overlay.ffconcat`. `ffprobe` reporting
`yuv420p` is **not** evidence that alpha was lost — decode a frame and check
`alpha=0` outside the ink.

### Overlay green looks wrong

Composite in RGB. Blending the overlay into the base's YUV mixed the BT.601
RGB→YUV matrix with the overlay's BT.709 matrix and shifted `#059669`. The gate
row `o_pill_colour` samples the rendered pill and fails above Δ12 per channel.

### `--hf-overlay` is slow

It is 28–46 s for 243 text states; the whole render measured 118–164 s
against 139 s native. Reuse the cache:
the key is the state list, so changing only footage timing keeps the overlay.
`--hf-workers 4` is safe here; `auto` pins to 1 worker by design.
