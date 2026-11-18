# P2 handoff — transitions, derived labels, delivery bundle

Status: **wired.** P1 released the core files; P2 merged the three modules into
`render_video.py`, `storyboard.py`, `deliver.py` and `comp.py`. Tests and gates
are in the table below.

| Deliverable | Code | Call site | State |
|---|---|---|---|
| Transition engine | `render/transitions.py` | `render/render_video.py` frame loop + `--transition`/`--no-transition` | wired, tested |
| Shot → text cards | `domain/labels.py` | `stages/storyboard.py` writes `output/<p>/text_cards.json`; `render_video.load_text_cards()` reads it | wired, tested |
| Delivery bundle | `ops/bundle.py` | `ops/deliver.py` and `stages/comp.deliver_run()` | wired, tested |

```bash
./fenix test
./fenix doctor
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_transitions tests.test_labels tests.test_bundle
```

## 1. Transitions — wired

Config (`config/pipeline.json`):

```json
"transitions": {"enabled": false, "default": "dissolve", "sec": 0.35}
```

- `transition_plan(cuts, default, sec)` is built once per render, after the cut
  list is final. A cut may carry `transition`/`transition_sec`; `cut`/`none`
  forces a hard cut. Boundary length is clamped to half the shorter shot.
- The frame loop keeps a reader per cut index. During `[at - sec, at)` the
  outgoing reader plays its tail and the incoming reader is seeked `sec` of
  handle **before** its in-point, reaching `in` exactly at the cut. This keeps
  the planned first frame on the cut and QA `c_clip_identity` passing.
- If the incoming clip has less than `sec` of handle before `in`, that boundary
  is skipped and the reason is appended to the render `warnings` and to
  `edit_plan.json → transitions.skipped`.
- `blend_frames` runs on the picture only, before punch-in, overlay, cards and
  captions. A transition replaces the punch-in on its incoming shot (the
  outgoing punch has settled by the window), so there is no 1.12× snap.
- `--transition <kind>` forces one render; `--no-transition` forces hard cuts.
  `xfade_filter` remains unused by the frame path (future post assembly only).

Verified on a 3-shot/6 s music-only project: the frame at a transition midpoint
differs from both neighbouring shots (mean BGR distance ≈ 186/168), and the same
project with `--no-transition` is byte-stable at the pre-cut frame.

## 2. Derived labels — wired

- `stages/storyboard.py`, after a passing order lock, writes
  `output/<p>/text_cards.json` via
  `labels.text_cards_from_shots(order["shots"], duration_sec, cfg)`. It writes
  nothing for legacy/VO projects without `story.shots`.
- `render/render_video.py → load_text_cards(sku, sku_dir)` prefers
  `sku.text_cards`; otherwise it loads the derived file. `render/preview.py`
  uses the same loader, so `--preview` and the encode see identical cards.
- One card per shot: big line from `spec`, sub from `label`, label clause as a
  fallback, clip stem as the last resort. Window is cut + 0.6 s → next cut −
  0.4 s, clamped inside shot/piece; a short shot keeps an 80 ms edge pad.
- Cards keep `zone`/shot `text_zone` and omit `y` unless the spec named one, so
  a JSON round-trip cannot silently turn a zone into a pixel coordinate.

## 3. Delivery bundle — wired

- `bundle_plan(project, run_dir, cfg, master=None, extras=None)` accepts an
  explicit master and otherwise detects `*_final.mp4`, then
  `comp.mp4`/`master.mp4`/`graded.mp4`/`fx.mp4`/`plate.mov`. Stage evidence
  (`comp.json`, `manifest.json`) is only read from the run directory.
- `ops/deliver.py` loads `load_config()` (so `pipeline.local.json` applies),
  plans, writes `run_dir/delivery_readme.md` and appends it as an extra, copies
  with `apply_bundle`, verifies shelf size + Downloads sha256, then prunes.
  `delivery.json` records `files`, `copied` and `skipped`.
- `stages/comp.deliver_run()` uses the same planner with the comp master; it
  keeps `--reuse` behaviour and writes the same readme/record.
- One destination gets one source: evidence in the run wins over a same-named
  file in `output/<p>/` or `input/<p>/`.
- `apply_bundle` never prunes; missing optional sources are reported, not fatal.

Verified with a temp delivery root: shelf contains master, contact sheet, shot
order, edit plan, script/no-VO note, QA report and `delivery_readme.md`;
`delivery.json` lists them with `skipped: []`; a second delivery is a pure copy.
The real Windows shelf was not written during verification.

## Remaining notes / risks

- Temp verification used a temp config with `audio.target_lufs = -16` because a
  6 s music-only fixture cannot reach the −14 LUFS delivery target without
  raising the bed above the music-only preset; this is fixture-only, the
  production config is unchanged.
- `comp.deliver_run` imports `ops.bundle` lazily (stage → ops is a
  delivery-only exception). If the layering rule is enforced strictly later,
  move the planner to `domain/` and keep the thin copier in `ops/`.
- The readme's audio line for a comp run defaults to “voiceover + music bed”
  unless the caller passes `no_voice`; a music-only comp is cosmetically
  mislabelled. The edit path detects it from `sku.json`.
