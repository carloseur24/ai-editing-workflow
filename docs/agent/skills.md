# Agent skills — shipped, limits, and what is next

The agent is a **deterministic execution layer + an editor/motion-designer
brain**. The human decides; the agent turns the decision into measured artifacts
and pixels. This page is the honest capability map.

## Shipped

| Skill | Command | What it actually does |
|---|---|---|
| Job card | `fenix job` | mode, missing inputs, required skills, stage/render state, next command |
| Intake & storyboard | `fenix storyboard` | brief → timed scenes, format/duration/tone, missing facts |
| Story frameworks | `fenix storyboard --framework` | 12 structures, per-type defaults, decision log |
| Script / TTS prep | `fenix lint` + `fenix tts` | word budget/phonetics; ElevenLabs or OpenAI voiceover |
| Music analysis | `fenix beats` | measured BPM, phase, drift |
| Footage review | `fenix frames` | keyframes, sharpness/luma/motion, scored candidates |
| Voiceover alignment | `fenix align` | dead-air trim, tempo fit, word timings, narration lines |
| Edit planning | `fenix plan` + `validate` | beat-locked cuts, candidate choice, shot/beat/range gates |
| Sound design | `fenix sfx` | cue sheet, procedural roles, J-cut, double transients, subs, vacuum, ducked bed |
| Editing & render | `fenix render` | cuts, punch-in, grade, captions, bus/bed mix, −14 LUFS |
| Kinetic typography | `render --hf-overlay` | 10 animated text/decor layers, state-cached |
| Motion design / 3D | `fenix motion` | SDF/clay objects, triplanar textures, studio light, grain, alpha |
| Motion tracking | `fenix track` | template tracking → normalized points for `comp` |
| Compositing / VFX | `fenix comp` | alpha/chroma layers, rect/ellipse masks, animated placement, fades |
| FX | `fenix fx` | particles, glitch, shatter (deterministic, seeded) |
| Color | `fenix grade` | LUT, primaries, temperature, shot matching, keyed secondaries |
| Scopes | `fenix scopes` | histogram, vectorscope, waveform, signal stats |
| Long-form | `fenix longform` | chapters, xfade set, multicam angle switching, J/L audio offset |
| QA | `fenix qa --strict` | 31 checks (33 with overlay) |
| Regression | `fenix eval` | metrics + SSIM against a baseline |
| Delivery | `fenix finish` | QA → delivery shelf + Downloads → prune local |
| One-shot chain | `fenix make` | mtime + config slices → render/remix → QA → finish |
| Batch | `fenix batch` | several projects, isolated failures |
| Asset intake | `fenix label` | folder scan, 4-frame sheets, 8-up vision grids, duplicate detection, safe rename + undo |

## Limits of the shipped versions

| Skill | Limit |
|---|---|
| **Tracking / roto** | template matching, not planar/optical-flow; masks are rect/ellipse, no spline roto |
| **Comp** | one base + layers; no depth sort or blend modes beyond alpha/chroma |
| **FX** | 2D particles/glitch/shatter; no 3D or volumetric effects |
| **Transitions** | `xfade` set; no match cuts or content-aware transitions |
| **Long-form** | multicam assumes synced angles; J/L is an assembly-level audio offset, not per-cut trims; no transcript-driven selection |
| **TTS** | ElevenLabs/OpenAI only; no voice cloning or voice design |
| **Grade/scopes** | keyed hue/sat secondaries; SDR scopes only |
| **Asset intake** | local folders only; no stock search, licence tracking or proxy generation |

## Still missing

1. Planar tracking and spline roto masks.
2. Match cuts and content-aware transitions.
3. 3D/volumetric particles and simulation.
4. Multicam sync UI and per-cut J/L trim handles.
5. Transcript-driven dialogue editing (select takes by text).
6. Voice cloning / voice design.
7. HDR scopes and wide-gamut grading.
8. Stock/clip search, licence tracking and proxy generation (local intake is `fenix label`).

## The decision loop

| Gate | Human decides | Agent produces |
|---|---|---|
| 1. Intent | goal, audience, platform | 2–3 directions with references |
| 2. Format | aspect, fps, duration | canvas + safe margins |
| 3. Story | framework, hook, beats, CTA | timed scene table |
| 4. Visual | palette, type, motion, grade | stills / animatic |
| 5. Sound | VO, music, SFX density | cue sheet + mixed bus |
| 6. Delivery | master, alpha, captions | verified delivered master |

Every choice lands in `input/<project>/brief.json → story.decisions`.

## Guides and examples

- [Job contract](job-contract.md) · [Footage labeling](labeling.md) · [Storytelling frameworks](storytelling.md) · [Sound design](sound-design.md)
- [Spec files](../reference/specs.md) · `docs/examples/{motion,track,comp,fx,grade,scopes,longform,tts}.json`
