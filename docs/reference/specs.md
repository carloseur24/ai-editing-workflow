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
    "beats": ["hook: the box in hand", "proof: 512GB · Snapdragon 8 Elite"],
    "shots": [
      {"clip": "01_hook.mp4", "in": 1.0, "dur": 5.5,
       "label": "hook: box in hand", "spec": "GALAXY S25 ULTRA"},
      {"clip": "02_detail.mp4", "in": 0.25, "dur": 6.0,
       "label": "detail", "spec": "DYNAMIC AMOLED 2X", "text_zone": "lower"}
    ],
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

### `story.shots` — order lock

An ordered shot table turns the brief into the edit contract. `fenix storyboard`
validates every entry against the real clip (`in >= 0`, `in + dur <= source`,
`dur >= min_cut_sec`, unknown names) and the sum against `duration_sec`
(`shots.sum_tolerance_sec`), then writes `output/<project>/shot_order.json`
(`locked: true`, `order_source: "brief"`, per-shot rows, `pass`). `fenix plan`
writes those exact cuts to `input/<project>/edit_plan.json` with
`"locked": true` — durations are **not** re-derived from narration. A project
without `story.shots` keeps the voiceover-line planner. `label` becomes the cut's
`text`, `spec` is kept on the cut for title cards. `text_zone`
(`header`/`lower`/`center`) puts that shot's cards in a safe-zone band without
editing the card.

`story.beats` accepts objects (`{"purpose", "weight", "text", ...}`) **or plain
strings** (`"hook: the box in hand"` → `purpose: "hook"`, `text: "the box in
hand"`). A malformed entry (non-numeric or `<= 0` weight, a number instead of a
string/object) fails with a `brief.story.beats schema error` naming the index —
never `'str' object has no attribute 'get'`. A shot without a numeric `dur > 0`
or with `in < 0` fails `shot_schema` with the offending field named.

## `sku.json` — edit manifest

```json
{
  "title": "iPhone 14 • 128GB", "subtitle": "Azul • eSIM • 77% Batería",
  "badges": ["Forro Incluido", "Garantía 1 Mes"],
  "price_label": "$330 USDT", "footer": "@fenixsoft • Mercado Social Local C-29",
  "safe_zone_mode": "reels_universal",
  "voiceover": "voice.mp3", "script_text": "…",
  "duration_sec": 33,
  "text_cards": [
    {"text": "GALAXY S25 ULTRA", "sub": "512GB · SNAPDRAGON 8 ELITE",
     "start": 0.7, "end": 5.1, "enter_sec": 0.55, "exit_sec": 0.30,
     "rise_px": 26, "zone": "header", "size": 86, "sub_size": 34}
  ],
  "display_map": {"14X330": ["14", "×", "330"]},
  "display_merge": {"ES SIM": "E-SIM"},
  "allow_fast_captions": false
}
```

- `voiceover: null` (or `"none"`, `"music"`) makes a **music-only** piece:
  `align` skips, `plan` expects `brief.story.shots`, the renderer disables
  captions and uses `duration_sec` (sku, then brief, then
  `target_duration_sec`) as the timeline length. A missing `voiceover` key still
  means `voice.mp3`.
- `text_cards[]` are native timed title/spec cards: title white, spec mint
  (`#6EE7B7`), top-centred, fade-and-rise in, fade out. Only `text`/`sub`,
  `start` and `end` are required; every other field falls back to
  `config/pipeline.json → text_cards`. `zone` (`header`/`lower`/`center`)
  replaces `y` with `safe_zones.<mode>.title_y` / `caption_y` / the canvas
  centre; an explicit `y` wins. A shot's `text_zone` applies to cards that name
  neither `y` nor their own zone. They render in the edit pass, with or
  without captions, and replace a motion plate + comp for text-only shots.
- When `brief.story.shots` is present, `fenix storyboard` also writes
  `output/<project>/text_cards.json`: one card per locked shot, big line from
  `spec`, sub from `label`, 0.6 s after the cut to 0.4 s before the next. The
  renderer reads that file only when `sku.text_cards` is absent, so a
  hand-authored list still wins.
- Empty `title`/`subtitle`/`price_label`/`footer`/`badges` entries are not
  drawn at all (no empty pill, no blank block in the alpha layer).

## `picks.json` — creative steering for the planner

```json
{"lines": [{"line": 0, "clip": "01_back_cameras.mp4", "candidate": "01c-a"}]}
```

## `edit_plan.json` — the cut contract

```json
{
  "sku": "s25_ultra_fenixsoft", "generated_by": "fenix plan",
  "locked": true, "order_source": "brief", "duration_sec": 33.0,
  "cuts": [{"line": 0, "text": "hook", "kind": "shot",
            "clip": "01_hook.mp4", "in": 1.0, "dur": 5.5, "t0": 0.0,
            "spec": "GALAXY S25 ULTRA"}]
}
```

Only `cuts[].clip`, `in` and `dur` are required; `candidate`, `text`, `t0` and
`spec` are optional and additive. A plan with `"locked": true` is the brief's
order made executable: `fenix plan` leaves it alone (edit
`brief.story.shots` to change the order, or pass `--force`), `fenix make` never
re-plans it on a timer, and `fenix job` reports whether the plan still matches
the brief. The renderer lays cuts back-to-back from 0 s and extends the final
shot to the piece duration.

A plan with cuts that **no planner stamped** (`generated_by` is not
`"fenix plan"`/`"plan_edit.py"`) is **hand-locked**: it is the delivered order
until the user says otherwise. `fenix plan` refuses to overwrite it, `fenix
make` never re-plans it, and `fenix job` shows `locked plan (manual) ·
confirmed`. `--force` is the only override; the replaced file is kept as
`edit_plan.prev.json`.

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
  "text": [{"text": "MOTION", "size": 72, "zone": "header",
            "color": "#FFFFFF", "start": 0.4, "end": 3.2,
            "enter_sec": 0.35, "exit_sec": 0.3, "rise_px": 24}]
}
```

`background: null` → ProRes 4444 alpha. Text layers also accept `font` (a TTF
in `assets/fonts/`) and `shadow` (bool). `zone` (`header`/`lower`/`center`)
replaces `y` with the safe-zone band; layers that share a zone stack downward in
file order (title then spec). An explicit `x`/`y` always wins.

## `comp.json` — compositing / VFX

```json
{
  "base": "@render/latest",
  "width": 1080, "height": 1920, "fps": 60, "duration": 10,
  "layers": [{
    "src": "@motion/latest/plate.mov",
    "start": 1.0, "end": 5.0,
    "x": 0.5, "y": 0.5, "scale": 1.0, "opacity": 1.0,
    "move": {"from": [0.5, 0.55], "to": [0.5, 0.45]},
    "track": "@track/latest",
    "mask": {"type": "rect|ellipse", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
    "key": {"color": "#00ff00", "similarity": 0.12, "blend": 0.05}
  }],
  "fade_in": 0.3, "fade_out": 0.4
}
```

`base`, layer `src` and `track` accept **stable refs**, resolved against the
project's run index and newest stage output:

| Ref | Resolves to |
|---|---|
| `@render/latest` | newest edit run video (via `runs.json`) |
| `@render/latest/<file>` | a file inside that run (`edit_plan.json`, …) |
| `@motion/latest` | newest plate (`plate.mov`, else `plate.mp4`) |
| `@motion/latest/<file>` | a file inside the newest motion run |
| `@comp/latest`, `@fx/latest`, `@grade/latest`, `@longform/latest` | newest stage artifact |
| `@track/latest` | newest `track_*.json` |

An explicit repo-relative path keeps working. `fenix comp --reuse` copies the
newest comp whose spec **and** resolved input files (size + mtime) are unchanged
instead of re-encoding; `--reuse` also stores a `fingerprint` in the manifest.

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
