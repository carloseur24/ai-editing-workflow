# Spec files

Most stages are driven by a declarative JSON file in `input/<project>/`. Paths
are relative to the repo root. Derived outputs live in `output/`; the one
exception is `fenix tts`, which writes `input/<project>/voice.mp3` by design.

## `brief.json` — storyboard intake

```json
{
  "title": "", "project_type": "edit|motion|hybrid|short|longform|brand|explainer|music",
  "goal": "", "audience": "", "platforms": ["reels"],
  "format": {"aspect": "9:16", "width": 1080, "height": 1920, "fps": 60},
  "duration_sec": 28, "tone": "",
  "references": [],
  "story": {
    "framework": "hook_retain_reward",
    "hook": "", "beats": [], "cta": "",
    "decisions": ["chose Hook–Retain–Reward over AIDA"]
  },
  "assets": {"footage_dir": "", "clips": [], "images": [],
             "voiceover": "", "music": "", "sfx": []},
  "motion": {"style": "", "elements": [], "transitions": []},
  "typography": {"family": "", "treatments": []},
  "deliverables": ["mp4"], "constraints": []
}
```

`fenix storyboard <project>` writes `output/<project>/storyboard.{json,md}`.
Frameworks: `hook_retain_reward`, `storybrand`, `explainer`, `save_the_cat`,
`kishotenketsu`, `three_act`, `aida`, `pas`, `bab`, `story_circle`,
`pixar_spine`, `hero`.

## `sku.json` — edit manifest

```json
{
  "title": "iPhone 14 • 128GB", "subtitle": "Azul • eSIM • 77% Batería",
  "badges": ["Forro Incluido", "Garantía 1 Mes"],
  "price_label": "$330 USDT", "footer": "@fenixsoft • Mercado Social Local C-29",
  "safe_zone_mode": "reels_universal",
  "voiceover": "voice.mp3", "script_text": "…",
  "display_map": {"14X330": ["14", "×", "330"]},
  "display_merge": {"ES SIM": "E-SIM"},
  "allow_fast_captions": false
}
```

## `picks.json` — creative steering for the planner

```json
{"lines": [{"line": 0, "clip": "01_back_cameras.mp4", "candidate": "01c-a"}]}
```

## `motion.json` — SDF/clay plate

```json
{
  "width": 1080, "height": 1920, "fps": 60, "duration_sec": 6,
  "background": "#0c0f0e", "grain": 0.05, "audio": null,
  "object": {
    "preset": "brain|sphere|torus|blob", "size": 860,
    "center": [0.5, 0.5], "scale": 1.0, "mesh": 280, "raymarch_steps": 20,
    "rotate": {"rx": [-28, 0], "ry": [-42, 318], "rz": [12, -12]},
    "light": {"angle": [-60, -145]},
    "texture_top": "assets/motion/brain.png",
    "texture_side": "assets/motion/brain_side.png"
  },
  "text": [{"text": "MOTION", "x": 540, "y": 400, "size": 72,
            "color": "#FFFFFF", "start": 0.4, "end": 3.2,
            "enter_sec": 0.35, "exit_sec": 0.3, "rise_px": 24}]
}
```

`background: null` → ProRes 4444 alpha. Text layers also accept `font` (a TTF
in `assets/fonts/`) and `shadow` (bool).

## `comp.json` — compositing / VFX

```json
{
  "base": "input/<p>/clips/base.mp4",
  "width": 1080, "height": 1920, "fps": 60, "duration": 10,
  "layers": [{
    "src": "output/<p>/motion/<run>/plate.mov",
    "start": 1.0, "end": 5.0,
    "x": 0.5, "y": 0.5, "scale": 1.0, "opacity": 1.0,
    "move": {"from": [0.5, 0.55], "to": [0.5, 0.45]},
    "track": "output/<p>/track/track_<stamp>.json",
    "mask": {"type": "rect|ellipse", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
    "key": {"color": "#00ff00", "similarity": 0.12, "blend": 0.05}
  }],
  "fade_in": 0.3, "fade_out": 0.4
}
```

`track` overrides `move`; tracking is sampled to 32 control points. For
`ellipse`, `x`/`y` are the centre and `w`/`h` the radii; values in `0..1` are
normalised, values above 1 are pixels.

## `track.json` — patch tracking

```json
{
  "input": "input/<p>/clips/a.mp4",
  "start": 0.0, "duration": 5.0,
  "template": {"x": 0.40, "y": 0.35, "w": 0.20, "h": 0.30},
  "search": 0.18, "smooth": 0.6, "threshold": 0.45
}
```

Output: `output/<p>/track/track_<stamp>.json` with normalized points.

## `fx.json` — particles, glitch, shatter

```json
{
  "input": "input/<p>/clips/a.mp4",
  "width": 1080, "height": 1920, "fps": 60, "start": 0, "duration": 3,
  "effects": [
    {"type": "particles", "start": 0, "end": 3, "count": 60,
     "color": "#10B981", "size": 4, "speed": 120, "gravity": 200, "seed": 7},
    {"type": "glitch", "start": 0.4, "end": 1.0, "intensity": 0.5},
    {"type": "shatter", "start": 1.8, "end": 2.4, "tiles": 8, "intensity": 0.7}
  ]
}
```

`glitch` fields: `intensity`, `seed`, `channels`; `particles`: `count`, `color`,
`size`, `speed`, `gravity`, `seed`, `life`; `shatter`: `tiles`, `intensity`.
Effects are 2D and deterministic per seed; audio is copied from the source.

## `grade.json` — LUT, primaries, match, secondaries

```json
{
  "input": "output/<p>/<run>/<run>_final.mp4",
  "lut": "assets/luts/editorial.cube",
  "eq": {"contrast": 1.06, "saturation": 1.08, "gamma": 1.0, "brightness": 0.0},
  "temperature": 0.0,
  "shot_match": {"reference": "assets/stills/hero.png", "reference_frame": 0,
                 "sample_frame": 5, "strength": 0.6},
  "secondaries": [{"color": "#1E90FF", "similarity": 0.3, "blend": 0.1,
                   "hue": 140, "saturation": 1.2}],
  "fade_in": 0.0, "fade_out": 0.0
}
```

`temperature` is clamped to −1 (cool) … 1 (warm). Shot matching is a global
per-channel gain (clamped 0.7–1.4); secondaries are hue/saturation only near
one key colour. Use `fenix scopes` for histograms,
vectorscopes and waveforms; there is no curves/secondary mask UI.

## `longform.json` — chaptered master

```json
{
  "width": 1920, "height": 1080, "fps": 30,
  "chapters": [{"title": "Cold open", "source": "input/<p>/clips/a.mp4",
                "in": 0, "out": 12}],
  "audio": "input/<p>/voice.wav", "audio_offset": 0.0,
  "transition": "fade", "transition_sec": 0.5,
  "multicam": {"angles": ["a.mp4", "b.mp4"], "cuts": [2, 3, 2],
               "audio": "dialogue.wav", "audio_offset": -0.2}
}
```

Transitions: `fade`, `wipeleft/right/up/down`, `slideleft/right/up/down`,
`circleopen`, `circleclose`, `dissolve`, `pixelize`. `transition_sec` is the
overlap and is subtracted from the master duration. `audio_offset` negative =
audio leads the picture, positive = trails. `multicam` expands
`angles[]` + `cuts[]` (durations) into chapters; it assumes synced angles.

## `tts.json` — voice generation

```json
{
  "provider": "auto",
  "text": "…", "voice_id": "…", "openai_voice": "alloy",
  "model_id": "eleven_multilingual_v2",
  "stability": 0.45, "similarity": 0.75, "speed": 1.08,
  "out": "voice.mp3"
}
```

Keys: `ELEVENLABS_API_KEY` / `OPENAI_API_KEY` (environment only). `provider:
auto` prefers ElevenLabs when its key is present; otherwise OpenAI. OpenAI also
accepts `openai_model` (default `gpt-4o-mini-tts`).

## `scopes.json` — QC

```json
{"input": "output/<p>/<run>/<run>_final.mp4"}
```

Only `input` is read from the spec. Use `--time SEC` for the sampled frame and
`--no-audio` to skip the spectrum. Output:
`output/<p>/scopes/scopes_<stamp>/{histogram,vectorscope,waveform,spectrum}.png`
plus `scopes.json` with measured `signalstats`. There is no `--deliver`.

## `assets/sfx/library.json` — sound-design roles

The manifest maps semantic roles to files inside
`config/pipeline.json → sfx.library_dir`; a `repo:` prefix resolves from the repo
root. Each role has `files[]` (candidate paths, deterministic choice per cue),
`gain_db`, `max_sec` and `align` (`start`, `peak`, `end`, `loop`). The
procedural core installed by `fenix sfx --install-library` is `impact_sub`,
`impact_braam`, `stinger_final`, `riser_tension`, `whoosh_air`, `whoosh_whip`,
`click_tactile`, `thud_body`, `tick_micro`, `chime_lock`, `paper_slice`,
`ui_data`, `env_room`. A role may declare `fallback` for when its licensed files
are absent; a relative fallback resolves inside `sfx.library_dir`, a `repo:`
prefix resolves from the repo root. Licensed roles can be added without code.
