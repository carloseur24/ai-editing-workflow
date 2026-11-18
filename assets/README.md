# Asset map

This repo is public and agentic: **source and intent travel, media does not.**
A fresh clone has everything the pipeline needs to start, except licensed media
and model weights, which are sourced locally. `.gitignore` enforces this.

## What ships in git

| Path | What it is | Consumed by |
|---|---|---|
| `assets/fonts/Inter-*.ttf` | Brand type family (Black / Bold / SemiBold) | `render`, `motion`, HyperFrames overlay |
| `assets/motion/*.png` | Reusable texture maps for the SDF/clay plate (`brain`, `brain_3q`, `brain_side`, `eyeball`) | `fenix motion` |
| `assets/sfx/library.json` | Semantic SFX roles: `impact_sub`, `riser_tension`, `whoosh_air`, `click_tactile`, … | `fenix sfx` |
| `assets/hf/gsap.min.js` | Pinned GSAP runtime for the HyperFrames overlay | `render --hf-overlay` |
| `assets/*/README.txt` | Local provisioning notes for the ignored media | humans, agents |

## What stays local (gitignored)

| Path | Why | How to provide it |
|---|---|---|
| `assets/music/*.wav` | Licensed instrumental beds; 40–60 MB each | Drop a 118/120 BPM track there. Either name it to match `config/pipeline.json → music.file` or let `beats`/`render` fall back to the first audio file in the folder. |
| `assets/sfx/*.wav` | Licensed legacy air-swish fallback | Drop a short swish/tap sample as `whoosh_short.wav`, or use the cue-sheet library below. |
| `assets/sfx/fenix_sfx/` | Procedural SFX core, synthesized on demand | `./fenix sfx --install-library` |
| `assets/motion/*.wav` | Demo/derived audio for motion plates | Supply your own track or point `motion.json → audio` at one. |
| `config/pipeline.local.json` | Machine paths: Windows shelf, personal SFX library | Copy the keys you need from `config/pipeline.json` and override them. Already gitignored. |
| `.cache/models/` | Whisper weights (~460 MB) | `bash tools/setup_wsl.sh` (one time) |
| `input/<project>/clips/`, `voice.*`, `keyframes/`, `candidates.json`, `edit_plan.json`, `word_timings.json` | Footage, voice and derived per-project data | Add footage/VO yourself; the stages regenerate the rest. |

## Asset flow

```text
assets/music/*.wav --------> fenix beats -> beat_grid.json
                           -> fenix sfx   -> music_bed.wav (ducked)
assets/sfx/library.json --> fenix sfx   -> sfx_bus.wav
assets/sfx/fenix_sfx/  ---^
assets/fonts/*.ttf ------> fenix render / motion / hf overlay
assets/motion/*.png -----> fenix motion -> plate.mp4 | plate.mov
config/pipeline.local.json -> delivery shelf, SFX library location
```

## Rules

- Never commit media, renders, model weights or a personal delivery path. If a
  file is over a few hundred KB and is not a font or a texture, it probably
  belongs in `.gitignore`.
- Do not add a second config file for normal settings. Put portable defaults in
  `config/pipeline.json`; put only machine-local paths in
  `config/pipeline.local.json`.
- A render must not fetch assets from the network. Fonts, textures, GSAP and the
  SFX role map are local files for that reason.
- If something is missing, do not invent it: `./fenix doctor` and
  `./fenix job <project>` name the exact path and the command that fixes it.

## Fresh-clone checklist

```bash
bash tools/setup_wsl.sh       # ffmpeg, venv, fonts, Whisper weights
./fenix doctor                # paths + which assets are present
./fenix job example_iphone14  # project inputs vs repo assets, kept separate
```

Expected on a clean clone: fonts, motion textures, `library.json` and GSAP are
present; music/SFX are reported missing until you add them; the demo examples
have no footage or voice. That is intentional, not a broken checkout.
