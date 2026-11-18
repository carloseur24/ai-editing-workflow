# Sound design — the dopamine blueprint

Sound is not decoration. It is the difference between a cut that lands and a cut
that just happens. This page is the repo-facing version of the
`sfx-dopamine-blueprint` skill (installed globally under
`~/.agents/skills/sfx-dopamine-blueprint/`). `fenix sfx` implements the
cue-sheet stage; `fenix render` consumes its bus and processed bed and owns the
final loudness limit.

## Three layers

### 1. Environmental bed — continuity and contrast

- Warm low-mid pad or room/air texture, roughly **100–600 Hz**, sitting at
  **−22 to −26 LUFS** when it is a dedicated pad. The current bed is a full
  music track, so it sits lower at `audio.music_bed_lufs` (**−28 LUFS**) and
  leaves the voice room.
- Duck the bed **4–6 dB** whenever a foreground transient fires.
- **Vacuum rule:** mute or low-pass the bed for **80–150 ms** immediately before
  a major visual flip or drop. The silence makes the hit feel louder without
  raising the level.

### 2. Foreground dopamine SFX — anticipation → resolution

- **J-cut pre-lap:** start risers, reverse whooshes and breath swells
  **150–300 ms before** the visual cut. The brain hears the transition coming.
- **Double transients:** layer a crisp **2–7 kHz click (15–40 ms)** with a body
  thud **80–180 Hz**, delayed **15–25 ms**, so text pops and paper splits feel
  physical.
- **Sub-bass weight (30–65 Hz):** reserve it for primary scene flips and the
  final lock-in. Never stack two sub hits within **1.5 s**; contrast needs
  recovery time.
- Align hard hits **1–2 frames early**; perceived sync is earlier than the frame.

### 3. Cue sheet first

Before synthesizing, list every **transition**, **hero motion**, **text beat**
and **contrast flip** with its timestamp. Assign each to Env Bed,
Anticipation (J-cut), Tactile Foley, or Sub Impact. Then synthesize at 48 kHz
stereo, soft-limit to −1.5 dBFS, and mux.

```json
{
  "sku": "iphone14_blue",
  "duration_sec": 28.371,
  "policy": {"music_bed_lufs": -28, "duck_db": -4.5, "vacuum_ms": 120},
  "cues": [
    {"t_sec": 5.596, "layer": "anticipation", "role": "whoosh_whip",
     "start_sec": 5.243, "onset_lead_ms": -353, "gain_db": -12},
    {"t_sec": 5.596, "layer": "impact", "role": "impact_sub",
     "vacuum_ms": 120, "gain_db": -6},
    {"t_sec": 10.489, "layer": "tactile", "role": "click_tactile",
     "gain_db": -10},
    {"t_sec": 26.749, "layer": "impact", "role": "impact_sub",
     "vacuum_ms": 120, "gain_db": -6}
  ]
}
```

## What the pipeline does today

| Rule | Status |
|---|---|
| Music bed normalised to `audio.music_bed_lufs` (−28 LUFS), sidechain-ducked under the VO | shipped |
| Foreground transient ducking (−4.5 dB, 8 ms attack / 350 ms release) | shipped |
| One air-swish per cut, pre-rolled so its peak lands on the cut | shipped (legacy path) |
| Two-pass loudnorm to −14 LUFS / −1.5 dBTP | shipped |
| Vacuum gap before major flips (120 ms, 15 ms cosine edges) | shipped in `fenix sfx` |
| Double transients (2–7 kHz click + 80–180 Hz body thud, +18 ms) | shipped in `fenix sfx` |
| Sub impacts reserved for real flips, 1.5 s spacing gate | shipped in `fenix sfx` |
| Cue sheet as a reviewable artifact before audio | shipped: `sfx_cue_sheet.json` |

The command is `fenix sfx <project>`:

```bash
./fenix sfx <project> --install-library   # synthesize the procedural core
./fenix sfx <project> --dry-run           # cue sheet only, no audio
./fenix sfx <project>                     # cue sheet + sfx_bus.wav + music_bed.wav
./fenix render <project>                  # render consumes the fresh artifacts
```

It reads `edit_plan.json` and `word_timings.json`, writes
`output/<project>/sfx_cue_sheet.json`, `sfx_bus.wav`, `music_bed.wav` and
`sfx_report.json`, then `fenix render` consumes them only when the cue sheet's
`edit_plan_sha256` matches the plan being rendered. A stale sheet is a warning
and the render falls back to the legacy one-whoosh bed.

The role map lives in `assets/sfx/library.json`; the procedural files are
installed under `sfx.library_dir` (default `assets/sfx/fenix_sfx/`, gitignored)
and are mixed with any licensed files you keep in that library. Put a personal
library path in `config/pipeline.local.json`, not in the public config. The sound
is reviewable as data before it is a waveform: `sfx_report.json` carries the same
`{id, measured, threshold, pass, fix}` rows as the other gates.
