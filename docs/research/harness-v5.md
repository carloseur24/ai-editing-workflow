# Harness v5 — research and redesign

> **Archived research (pre-refactor).** Commands and file names below describe
> the flat-script layout at the time of the study (`script_lint.py`,
> `session3_config.json`, …). The current layout and commands are in
> [`docs/architecture.md`](../architecture.md) and
> [`docs/reference/commands.md`](../reference/commands.md). Keep this file for
> evidence and rationale, not as an operating guide.

**Scope:** how to make the agent (opencode + DeepSeek V4.1 Flash) reliably produce a
*good* reel, not just a technically valid MP4, with the least possible guessing.
**Date:** 2026-11-15 · **Method:** three literature/tooling research tracks,
measurements run against this repo, then a `curl` verification pass over every
cited claim (§3.5). Package versions read from PyPI the same day.

> Evidence labels: **[measured]** = run in this repo on 2026-11-15, with the
> command or method stated; **[verified]** = fetched with `curl` and quoted from
> the primary text (URL given); **[partial]** = the primary text supports the
> idea but not the exact number/wording, and the gap is stated; **[source]** =
> canonical pointer, verified on 2026-11-15 unless listed as partial in §3.5;
> **[judgment]** = design opinion.

---

## 0. TL;DR — fourteen changes, ranked by (impact ÷ effort)

| # | Change | Why it matters | Effort | Impact |
|---|---|---|---|---|
| 1 | **Beat grid from the actual track** (`beat_grid.json` sidecar) | cuts drift 0 → 176 ms off the beat; the track is 119.0 BPM, not 118 | S | high |
| 2 | **Script lint before TTS** (word budget, phonetics) | 93 words → 199 wpm after 1.26× tempo-fit; 17 words over budget | S | high |
| 3 | **`validate_edit_plan.py` before render** + **`qa_report.json`** (check id, measured value, threshold, remediation) | failures are caught in 1 s, not after a 3 min render, and every failure names its fix | S | high |
| 4 | **Candidate manifest with IDs** (`candidates.json`): the agent picks an ID, never types a timestamp | vision-estimated numbers are the single largest source of silent error | M | high |
| 5 | **In-point quality floor in the renderer** | 3 of 16 cuts are under 60 % of their clip's median sharpness (worst 33 %) | S | high |
| 6 | **Narration lines as a first-class artifact** + **`plan_edit.py`** | removes hand-guessed cut times; the agent maps line → clip, code lays out time | M | high |
| 7 | **Content checks in QA** (clip identity, caption text, in-point score) | technical QA cannot catch a clip/narration mismatch | M | high |
| 8 | **Caption audit as text** (`caption_audit.json`: every card, CPS, width) | 14/32 cards over the 20 CPS limit, worst 37.7; reading frames to find this costs 9× the resolution | S | med |
| 9 | **Vision sheets built right** (one frame per image, native-scale caption crop, no 9-up for text) | 270 px tiles render 46 px captions at ~11 px — below reliable OCR | S | med |
| 10 | **Schema validation + `--strict`** | free-form JSON silently accepts typos and bad in-points | S | med |
| 11 | **Golden-SKU regression suite** (`eval_run.py`) | nothing detects a re-render getting worse | M | med |
| 12 | **AGENTS.md v5 contract** (phases, decision table, one worked example, rationale log) | rules are prose; the agent rewrote the renderer despite "never do this" | S | med |
| 13 | **Safe-zone decision** for the bottom stack | our stack sits 292–615 px from the bottom, inside the ~672 px band third-party guides report (Meta publishes no numbers) | S | med |
| 14 | **`decision_log.json`** (every rejected shot + reason) | makes "why this shot" auditable and turns taste into training data | S | low–med |

---

## 0.1 Implementation status — what shipped on 2026-11-15

Items **1, 2, 3, 4, 5, 6, 7, 8 and 11** are implemented and wired into the
planner / renderer / QA gate. Everything below was re-measured on the demo SKU
after the change, not estimated.

| Item | New artifact / code | Before | After |
|---|---|---|---|
| 1 Beat grid | `beat_grid.py` → `output/<sku>/beat_grid.json`; renderer snaps to measured beats | worst cut 176 ms off the beat at cut 16 (nominal 118 BPM vs. real 118.97) | **max 3 ms, mean 2 ms** (16 cuts) |
| 2 Script lint | `script_lint.py` → `output/<sku>/script_lint.json` | 93 words discovered only after the TTS render (1.23× tempo fit) | **FAIL `s_words` 93 > 84** before a TTS credit is spent |
| 3 Pre-render gate | `validate_edit_plan.py` → `output/<sku>/plan_validation.json` | bad in-points surfaced as warnings after the render | **out-of-range cut + 3 soft in-points caught in ~2 s**, with suggested windows |
| 3 QA report | `qa_check.py --strict` → `output/<sku>/qa_report.json` | 15 pass/fail lines, no measured values | **21 rows with `measured` / `threshold` / `fix`**, strict mode for delivery |
| 5 In-point floor | `quality.py` (shared sharpness scan + cache) + renderer floor | min **0.33×** clip median (`05_accessories` @ 0.50 s) | **min 1.00×, mean 1.66×**; renderer exits below `quality.in_point_floor` |
| 8 Caption audit | `render_video.py` merge pass → `output/<sku>/caption_audit.json` | worst card **37.7 CPS**, 14/32 over 20 | **worst 28.4 CPS**, 32 → 30 cards, flagged via `allow_fast_captions` |

Supporting changes: `session3_config.json` gained `script` and `quality` blocks;
`render_video.py` gained `--allow-soft-in`; `librosa>=1.0` was added to
`requirements.txt` (tooling only — beat measurement is cached by file hash and
not imported at encode time). `AGENTS.md` §1/§4 and `README.md` now carry the new
sequence: `script_lint` → `beat_grid` → `extract_keyframes` → `align_words` →
`plan_edit` → `validate_edit_plan` → `render_video` → `qa_check --strict` →
`eval_run`.

### Second implementation pass (same day) — items 4, 6, 7, 11

| Item | New artifact / code | Before | After |
|---|---|---|---|
| 6 Narration lines + planner | `narration.py`, `plan_edit.py`, `input/<sku>/picks.json` | cut times hand-written, 202 ms off the grid after clamping | **15 lines → 16 cuts, every boundary 0–0.5 ms off a measured beat**, line→clip table in `output/<sku>/plan_edit.json` |
| 4 Candidate manifest | `candidates.json` (27 windows, IDs `01c-a`…`05c-f`), `candidates.py` | in-points typed from vision estimates | **the plan references IDs**; validator fails on an unknown ID |
| 7 Content checks | QA rows `d_script_match`, `c_clip_identity`, `c_caption_text`, `c_caption_zone`, `c_caption_dupes`, `p_window_sharp`, `p_window_soft` | 21 rows, technical only | **29 rows**, 16/16 cuts verified against their source frame |
| 11 Regression | `eval_run.py` + `eval/<sku>_baseline.json` | nothing compared two renders | first-vs-last diff on 18 metrics incl. SSIM; fails on a regression |
| — Versioned outputs | `runs.py` → `output/<sku>/<sku>_<stamp>/`, `runs.json`, `latest.json` | every render overwrote `<sku>_final.mp4` | each render is immutable; `qa_check.py --latest <sku>` follows the pointer |
| — Sustained shot quality | `quality.window_stats`, planner window scoring, `window_floor` / `window_soft` gates | the planner scored only the first frame of each shot | **shot mean 1.14 → 1.42× clip median; soft share 7.0% → 0.9%** on the demo |

Partially shipped: **10** — `edit_plan` schema/ranges are enforced by
`validate_edit_plan.py` and QA has `--strict`, but there is no shared JSON-schema
file yet.

Still open from the ranked list: **9** (vision sheets), **12** (AGENTS.md v5
contract beyond §1/§4), **13** (safe-zone decision), **14** (`decision_log.json`).

---

## 1. What the harness is today

```
AGENTS.md (prose contract)
   ├─ extract_keyframes.py   → 1 fps JPEGs + luma/sharpness report
   ├─ agent writes sku.json + edit_plan.json (cut clip/in/dur by eye)
   ├─ align_words.py         → dead-air trim, tempo-fit, word timestamps, display fixes
   ├─ render_video.py        → deterministic layout, captions, beats, audio, encode
   └─ qa_check.py            → 15 technical gates
```

The model is asked to do two very different jobs: **judgement** (is this shot
good, does this line match this footage) and **measurement** (when does a word
start, where does text fit, when is the beat). Everything that went wrong in
practice came from blurring those two: guessed timestamps, guessed coordinates,
guessed in-points, and QA that only measured pixels rather than meaning.

---

## 2. Measured weaknesses (this repo, today)

### 2.1 The cut grid is not locked to the music **[measured]**

Re-measured today with librosa on `assets/music/groove_118bpm.wav` (216.6 s,
48 kHz), and cross-checked by hand against the demo's 16 cuts:

- detected beat interval **0.5043 s ± 0.0162 s → 119.0 BPM**; the config says
  118. librosa's unpinned estimate is 117.45, so the true tempo is ~119 — the
  track is simply not 118 BPM despite its filename;
- the tracker anchors the grid at **0.000 s** (`trim=False`); with `trim=True`
  the first counted beat is **3.065 s**, which is the intro, not a phase offset;
- the demo's cut times are **exact multiples of the nominal 0.50847 s beat**
  (≤ 3 ms), so the renderer's grid is implemented correctly — for 118 BPM;
- against the **actual** beats the error grows **monotonically from 0 ms to
  176 ms** across the edit (mean 78 ms, ≈6.5 ms per second), because a 0.8 %
  tempo error accumulates.

| cut | 1 | 4 | 8 | 12 | 16 |
|---|---|---|---|---|---|
| off the real beat | 0 ms | 1 ms | 42 ms | 103 ms | **176 ms** |

So "16 cuts on the 118 BPM grid" is literally true and audibly wrong: the edits
start locked and end a third of a beat late. That gradual slippage is worse than
a constant offset — it reads as drift, not style. The fix is not "snap to the
bar" (the demo's cuts follow narration lines, which is a legitimate choice) but
**two separate changes**: (a) derive the grid from the file, and (b) if beat
sync is wanted, snap only **within ±120 ms** (≈3 frames at 60 fps) so line sync
is not visibly broken.

### 2.2 The voiceover is over budget **[measured]**

- script: **93 words**, ElevenLabs at natural pace → **35.3 s / 162 wpm**;
- tempo-fit **1.2586×** → **28.02 s / 199 wpm**. That is a 26 % speed-up, audible
  as clipped consonants and compressed pauses.
- At 162 wpm, a 28 s slot fits **~76 words**. The script is 17 words too long.

The harness tells the agent "≤ 28.0 s" but nothing measures the script before
the voice goes to ElevenLabs. By render time the only remedies are ugly.

### 2.3 Captions are faster than subtitle standards **[measured]**

Recomputed against the current chunker and the renderer's real display span
(`min(next card start, card end + 0.6 s hold)`, characters counted with spaces):

- **32 cards**, mean **19.5 CPS**, **14 of 32 above 20 CPS**;
- worst **37.7 CPS** ("TE LLEVAS", 0.24 s), then 28.4 ("MERCADO SOCIAL",
  0.49 s), 28.0 ("TIENE DETALLES ESTÉTICOS", 0.86 s);
- bringing every card to 20 CPS would need **+2.31 s** of hold time — more than
  the 0.6 s hold budget and more than the edit has, since it already runs 28.0 s.

The mean looks acceptable; the tail does not. And the only honest fix is a
shorter script (§2.2), not a longer hold. Nothing in QA measures reading speed.

### 2.4 In-points ignore measured sharpness **[measured]**

Per-clip 5 fps scan (Laplacian variance) for the clip median, then the exact
frame at each of the **16 planned cuts** in
`output/demo_iphone14_blue/edit_plan.json`:

| cut | clip | planned in-point | sharpness there | median | ratio | motion |
|---|---|---|---|---|---|---|
| 9 | `05_accessories` | **0.50 s** | **3.8** | 11.5 | **0.33** | 9.5 |
| 10 | `04_box` | **1.50 s** | **5.5** | 15.5 | **0.35** | 21.3 |
| 13 | `03_edges_ports` | **3.40 s** | **12.7** | 26.7 | **0.47** | 9.4 |

Min ratio **0.33**, mean **0.86**, **3 of 16 cuts under 0.60** of their clip
median. Clip medians: `01_back_cameras` 41.4, `02_screen_faceid` 44.4,
`03_edges_ports` 26.7, `04_box` 15.5, `05_accessories` 11.5.

The agent picks in-points from 1 fps thumbnails with no score attached, so a
soft, fast-moving frame is indistinguishable from a clean one at thumbnail size.
`04_box` is the clearest case: its 0.30 s and 2.00 s cuts score **1.82×** and
**0.98×** of median, while the 1.50 s cut between them scores **0.35×** — same
clip, same lighting, inside one take.

### 2.5 Scene detection cannot help this footage **[measured]**

`ffmpeg select='gt(scene,0.30)'` and `0.45` return **0 cuts on all five clips**.
These are continuous handheld takes, not multi-shot files. Any "detect the
shots" tool is the wrong abstraction; what is needed is **usable-window
scoring** (sharpness, motion, framing), not scene segmentation.

### 2.6 QA validates the container, not the edit **[measured]**

`qa_check.py` checks resolution, fps, profile, loudness, black frames, size,
duration, captions-present and warnings. It does **not** check:

- that the frame at cut *n* actually comes from the planned clip (this is how
  `norm[cut_idx % n]` shipped the wrong footage while passing 15/15);
- that the caption text is the right words (a BGR/RGB bug shipped brown pills);
- that the chosen in-point is sharp;
- that the shot matches the narration line.

Both bugs were found by looking, not by QA. That is the core harness gap: **the
gate measures delivery, and delivery passed while the edit was wrong.**

### 2.7 The vision pass is under-resolved **[measured sizes + vendor doc]**

`extract_keyframes.py` writes 360 px-wide JPEGs. A 46 px caption is drawn at
46 × 360/1080 ≈ **15 px**. The contact sheet's 270 px tiles put it at ~**11 px**.
A 1080×1920 frame sent whole survives much better: Claude's standard tier lands
it at ~819×1456, so the same caption arrives at ~**35 px**; OpenAI's 32 px patch
tokeniser puts the frame at 2 040 patches, which is native for the models that
budget 2 500 patches at `high` (budgets are model-specific).
But a 3×3 sheet of full frames costs the *same* tokens as one frame and leaves
each cell ~273 px wide: text back to ~12 px. So the two things that work are
**(a) one frame per image** and **(b) a crop at native width**, never a grid.
Reading "330 DÓLARES" is plausible at 15 px; "GARANTÍA 1 MES" reliably is not.

### 2.8 Instructions and code drifted **[measured]**

- AGENTS.md: "You NEVER rewrite `render_video.py` per video." The v4 renderer was
  rewritten by the agent that was told this.
- README/`edit_plan.json`: the file was written but ignored by the renderer until
  it was fixed today; the docs described behaviour the code did not have.
- qa_check counts "edit-plan" as one check plus separate failure rows for each
  warning, so the "15/15" headline is not 15 independent risks.

Prose rules do not bind an agent; **code and tests do**. Any rule worth keeping
should be executable or have a visible failing check.

### 2.9 No regression suite **[judgment]**

Nothing compares today's render with yesterday's. A change to the chunker, the
grade or the encoder can only be judged by eye, per SKU, from scratch. There is
no baseline, no diff, no score.

---

### 2.10 A clip-identity check is feasible **[measured, prototype]**

Before recommending the guardrail, a prototype matcher was run on the demo:
downscale frames to 96×54 grey, try scales 1.00 / 1.06 / 1.12 (the punch-in is
1.12×), search ±0.25 s around the expected time, and take the best normalised
correlation per source clip.

| | best correlation |
|---|---|
| planned clip (correct) | **≥ 0.622** |
| any other clip | **≤ 0.479** |

A threshold of 0.55 separates every case across the 16 cuts. The sample is
small, so treat the exact number as a starting point — but the separation is
wide enough that this check is worth shipping, and it is exactly the bug
(`cut_idx % n`) that passed 15/15.

## 3. What the research says

Three tracks: **(A)** agent-harness design, **(B)** what vision models can
actually see, **(C)** the media tooling stack. Every claim carries its own
confidence marker; §3.4 lists where the tracks contradict this repo or each
other and what was decided.

### 3.1 Harness design — deterministic where possible, agentic where necessary

The strongest evidence converges on one shape: **a fixed pipeline with a
narrow, constrained agent role**, not an autonomous loop. Agents are good at
choosing between labelled options and bad at producing numbers.

- **Code graders are the backbone; model graders are a calibrated supplement.**
  Anthropic's eval guidance compares the two directly: code-based graders are
  "Fast • Cheap • Objective • Reproducible"; model-based graders are
  "Non-deterministic" and "Requires calibration with human graders for
  accuracy". It also advises giving the judge "a way out" — return `"Unknown"` —
  and grading "each dimension with an isolated LLM-as-judge". **[verified]**
  `anthropic.com/engineering/demystifying-evals-for-ai-agents`
- **Guardrails at the action boundary measurably change outcomes.** SWE-agent's
  ablation (paper §4): with **no edit action at all** resolution is **10.3 %**;
  with the normal edit action **15.0 %**; adding a lint guardrail that blocks
  edits which fail syntax check raises it to **18.0 %**. The file viewer is
  non-monotonic — 30-line window 14.3 %, 100-line 18.0 %, whole file 12.7 % — so
  *both* too little and too much context hurt. Their ACI principles (simple
  actions; compact, consolidated actions; feedback concise but informative;
  guardrails that stop error propagation) are stated in the paper, while the
  ACI docs page lists the resulting features. **[source]**
  `arxiv.org/abs/2405.15793`, `swe-agent.com/latest/background/aci/`
- **Self-critique without external feedback does not work.** "LLMs struggle to
  self-correct their responses without external feedback, and at times their
  performance even degrades after self-correction" (ICLR 2024). Anthropic's
  long-running-harness post is blunter: "agents tend to respond by confidently
  praising the work—even when, to a human observer, the quality is obviously
  mediocre". Their fix is a *separate* evaluator tuned "to be skeptical", with a
  hard threshold per criterion: "if any one fell below it, the sprint failed."
  **[verified]** `arxiv.org/abs/2310.01798`,
  `anthropic.com/research/harness-design-long-running-apps`
- **The simplest architecture that fits wins.** Agentless (fixed
  localize → repair → validate) beat complex agent scaffolds on SWE-bench Lite
  at 32.0 % for $0.70. If a script can do the step, it should. **[source]**
  `arxiv.org/abs/2407.01489`
- **Context is a budget.** "context rot: as the number of tokens in the context
  window increases, the model's ability to accurately recall information from
  that context decreases" — so aim at the "smallest possible set of high-signal
  tokens". Subagents should return a distilled summary "(often 1,000–2,000
  tokens)", not a trajectory. Tool-output shape matters too: resolving opaque
  IDs to names "significantly improves Claude's precision in retrieval", and the
  post's own example uses **72 tokens** for a concise response versus **206**
  for a detailed one. Claude Code caps tool responses at 25,000 tokens by
  default. **[verified]**
  `anthropic.com/engineering/effective-context-engineering-for-ai-agents`,
  `anthropic.com/engineering/writing-tools-for-agents`
- **A concrete target to iterate against is the main lever** — a mock, a test
  case, a screenshot to compare. **[source]**
  `anthropic.com/research/claude-code-best-practices`
- **LLM judges are usable but biased** (~80 % human agreement; position,
  verbosity and self-enhancement bias). Advisory, never gating. **[source]**
  `arxiv.org/abs/2306.05685`
- **opencode reads `AGENTS.md` and expects build/lint/test commands plus
  "focused verification steps"**; Cursor's four rule types (always-on,
  glob-scoped, agent-decided, manual) make the same point — scope instructions
  to where they apply instead of one giant always-on prompt. **[source]**
  `opencode.ai/docs/rules`, `cursor.com/docs/context/rules`

**What this changes here.** The repo already has the right skeleton; what is
missing is that *nothing the agent can do wrong is caught before the render*,
and *the agent is asked for numbers it cannot know*. Five rules follow:

1. Every gate is code, and emits `{check, measured, threshold, pass, fix}` —
   not a sentence in a log.
2. The agent's action surface is "pick an ID from a generated list", never
   "type a time in seconds".
3. Validate **before** the expensive step; fail on the offending element index.
4. The evaluator is a separate pass; the renderer and the authoring agent never
   grade their own output.
5. A retry must consume a specific error code and a specific fix. Re-running the
   same command after the same failure is not self-correction.

### 3.2 Vision — what a model can and cannot measure

This track is the most decisive, because it says the harness's central premise
("the model looks at frames and judges") is only reliable for a narrow class of
questions.

- **Vendor preprocessing is lossy and differs by vendor.** Claude: "Each patch
  is a 28×28-pixel block … referred to as a visual token", and a 1920×1080
  image arrives at **1456×819 = 1,560 tokens** on the standard tier (2,691 if
  not resized). A portrait 1080×1920 frame therefore lands at ~819×1456. OpenAI
  covers images with **32 px × 32 px patches** — 2 040 here — but whether that
  is native depends on the model: some 5.x models allow 2 500 patches at
  `high`, newer ones differ. Gemini: "258 tokens if both dimensions <= 384
  pixels", then **768×768 tiles**, capped at 1 120 tokens by default. **[verified]**
  `platform.claude.com/docs/en/build-with-claude/vision`,
  `developers.openai.com/api/docs/guides/images-vision`,
  `ai.google.dev/gemini-api/docs/image-understanding`
- **Coordinate grounding fails at high resolution.** ScreenSpot-Pro: best model
  18.9 % zero-shot; *strategically reducing the search area* with cascaded crops
  lifts it to **48.1 %**. Set-of-Mark exists so the model picks a **label**
  instead of a coordinate. **[source]** `arxiv.org/abs/2504.07981`,
  `arxiv.org/abs/2310.11441`
- **"Crop before asking" is now a published architecture, not just a trick.**
  AwaRes (2026) runs a low-resolution global view and uses tool calls to fetch
  only the high-resolution segment a question needs; its stated motivation is
  exactly our case — low-res inputs "potentially miss critical visual
  information, like small text". DT-VQA (2024, 170 K QA pairs) shows dense text
  remains hard for GPT-4V/Gemini even at full resolution. **[source]**
  `arxiv.org/abs/2603.16932`, `arxiv.org/abs/2405.06706`
- **Vision is not a measuring instrument.** BlindTest: four frontier VLMs
  averaged **58.07 %** on trivial spatial tasks (do two circles overlap?), best
  77.84 %. OCRBench finds the weak zones are "multilingual text, handwritten
  text, non-semantic text, and mathematical expression recognition" — not
  ordinary rendered labels, which is why our uppercase Spanish captions are
  probably readable at native resolution but still not something to
  *measure* by eye. ChartQA exists because reading numeric values off pixels is
  unreliable. **[verified]** `arxiv.org/abs/2407.06581`,
  `arxiv.org/abs/2305.07895`, `arxiv.org/abs/2203.10244`
- **Contact sheets lose exactly what we inspect.** A 3×3 sheet of full frames
  costs the same visual tokens as a single frame but gives each cell ~1/9 the
  area; 46 px text → ~12 px. Multi-image reasoning is itself weak (MMIU: GPT-4o
  peaks at 55.7 %). **[source]** `arxiv.org/abs/2408.02718`
- **Video sampling.** Video-MME: "the restricted input frames can become an
  information bottleneck", and it argues you "should sample video frames at a
  fixed fps". Accuracy falls over duration in its tables — LLaVA-NeXT-Video
  **61.7 → 50.1 → 44.3** and Gemini 1.5 Pro **81.7 → 74.3 → 67.4** across
  short → medium → long. At ~1.9 s per cut, 1 fps gives ~2 frames per shot —
  enough for framing, not for punch-in transients. **[verified]**
  `arxiv.org/abs/2405.21075`
- **Safe zones: the concept is first-party, the numbers are not.** Meta's own
  help article says to "keep the edges (top, bottom and sides) free of key
  creative elements, text and logos" and carries a bottom-40 % disclaimer note,
  but publishes **no percentages**; TikTok documents safe zones "determined by
  the dimension …, ad caption length, and any additional formats" and ships
  files, also without pixel numbers. The widely quoted 14 % top / 35 % bottom /
  6 % sides (= 269 / 672 / 65 px) comes from **third-party guides**. Treat it as
  a plausible default, not a platform spec. Against it, our bottom stack at
  `y=1305..1628` sits **292–615 px from the bottom**, i.e. inside the 672 px
  band. **[verified concept + secondary numbers]**
  `facebook.com/business/help/980593475366490`,
  `ads.tiktok.com/help/article/video-ads-specifications`

**What this changes here.** Frame review stays, but only for **categorical**
questions ("is the product framed and unobstructed?"), one frame per image, with
candidates labelled 1..n and answers as labels. Everything numeric — luma,
clipping, caption overflow, contrast, safe-zone containment, card text — is
computed in code and handed to the agent as a JSON table. Caption text in
particular should be reviewed from `caption_audit.json` (exact strings), not by
reading pixels: that is both cheaper and strictly more reliable.

### 3.3 Media tooling — what is worth adding, what is a trap

- **Scene detection is the wrong abstraction for this footage.** PySceneDetect
  0.7.3 (released 2026-11-08) reports default-setting F1 of **91.59 / 73.86 /
  55.75** for AdaptiveDetector and **86.69 / 69.26 / 55.84** for ContentDetector
  on BBC Planet Earth / AutoShot / ClipShots — precision collapses on short web
  clips, so `min_scene_len` is mandatory there. The commonly quoted sweep
  (threshold 3.5, window 3, 0.6 s) is community tuning, not vendor guidance, and
  the CLI calls the window option `--frame-window`. `save-images` writes **3
  images per scene by default**; `-n 1` gives one per shot. Our five clips are
  continuous takes — ffmpeg scene detection returns **0 cuts** at 0.30 and 0.45
  — so keep the tool in reserve and score windows at 3–5 fps instead.
  **[verified + measured]** `scenedetect.com/benchmarks/`,
  `scenedetect.com/docs/latest/cli.html`
- **Beat detection: pin the tempo, do not trust the detector's default.**
  `librosa.beat.beat_track(bpm=118, units="time")` avoids half/double-time
  errors; hop 512 @ 22.05 kHz is 23 ms, so snap the beat to the 60 fps frame
  grid and refine phase on `onset_strength`. At 118 BPM a beat is 0.5085 s and a
  4/4 bar 2.034 s — cut on 2- or 4-beat bars. **madmom (2018) and aubio (2019)
  are stale**; for true downbeats use Essentia (small wheels,
  `RhythmExtractor2013`) or beat_this. **[source]**
  `librosa.org/doc/latest/generated/librosa.beat.beat_track.html`,
  `essentia.upf.edu/reference/std_RhythmExtractor2013.html`,
  `github.com/CPJKU/beat_this`
- **Do not add librosa to the render environment.** It installs cleanly but
  pulls ~488 MB (llvmlite + numba) on a 7 GB WSL box for one number every 28 s.
  Generate `beat_grid.json` once per track in a separate venv and commit it; the
  renderer only reads `{bpm, phase, beat_sec, bars}`. **[measured install size]**
- **Amplitude-based auto-cutters fail once there is a music bed.** auto-editor
  cuts on loudness/motion; run silence detection on the **voice stem alone**
  with a 0.1–0.2 s margin. We already exceed this with the numpy RMS gate —
  keep it, and keep the margin. **[source]** `github.com/WyattBlue/auto-editor`
- **Audio hygiene.** `loudnorm` documents `measured_I/TP/LRA/thresh/offset`,
  `linear` and the failure mode — "If any of these conditions aren't met,
  normalization mode will revert to dynamic" — but the two-pass JSON recipe
  itself lives in the FFmpeg wiki, not the filter reference. `sidechaincompress`
  documents `threshold/ratio/attack/release` (defaults 20 ms / 250 ms) and gives
  no musical advice: **attack < 20 ms / release > 200 ms is studio practice**
  and must be A/B'd by ear. `adelay` takes per-channel milliseconds at the
  output rate. **[verified options + judgment values]**
  `ffmpeg.org/ffmpeg-filters.html`
- **Caption pacing has published numbers — including in Spanish.** BBC:
  160–180 wpm, ≤3 lines, 37 chars. Netflix: ≤42 chars/line, **20 CPS** adult,
  17 children. The Spanish broadcast accessibility standard **UNE 153010:2012**
  is stricter still: **15 CPS** maximum reading speed, 37 chars/line, 2 lines
  (3 only when justified), and a 4.5:1 contrast minimum. A 4-word, ~24-char card
  therefore needs **≥1.2 s** on screen at 20 CPS — our mean is 19.5 CPS but the
  worst card runs at **37.7 CPS** and the shortest shows for **0.24 s**, i.e.
  2.5× the Spanish accessibility limit.
  UNE is written for deaf/HoH broadcast, not Reels, so it is a floor for
  comprehension rather than a target — but it is the only Spanish-specific
  number we have. **[source]**
  `bbc.co.uk/accessibility/forproducts/guides/subtitles/`,
  `partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-Timed-Text-Style-Guide`,
  `subtitulado.es/subtitulos-sordos-une-153010/` (summary of UNE 153010:2012)
- **Output QA additions.** `blackdetect`, `freezedetect`, a decode smoke test
  (`ffmpeg -xerror -f null -`), and `libvmaf` only when comparing two encodes of
  the **same** source — never across different videos. **[source]**
- **TTS settings are a request, not a contract.** ElevenLabs exposes `speed`
  (default 1.0), `stability`, `similarity_boost`, `style`; Spanish wants speed
  1.05–1.10, stability 0.4–0.5 — and **always re-align**, because the realised
  duration is what matters. **[source]**
  `elevenlabs.io/docs/api-reference/text-to-speech/convert`
- **Shot↔line matching with CLIP is promising but weak on near-identical
  product views** ("back cameras" vs "front"). Use it to order shots and flag
  low-confidence matches; keep the final choice with the model. **[source]**
  ICMR 2023, `dl.acm.org/doi/fullHtml/10.1145/3591106.3592247`

### 3.4 Where the tracks disagree — and what was decided

| Tension | Track says | This repo says | Decision |
|---|---|---|---|
| Scene detection | "Add PySceneDetect adaptive" | 0 cuts on all 5 clips | **Defer.** Score windows at 3–5 fps instead; revisit if multi-shot B-roll arrives |
| Contact sheets | "Never use a sheet for judgment" | caption strip sheet in §4.6 | **Both.** Drop the 9-up full-frame sheet; keep the caption strip because it is a **native-width crop** (3 rows of 1080×323 fits inside 1456 px, so no downscale) |
| 20 CPS | Netflix adult standard | reels convention is faster | **20 warn / 25 fail**, with an explicit override flag |
| Beat grid | "snap every cut to the bar" | cuts follow narration lines | **Snap within ±120 ms** (3 frames) — alignment to the line wins beyond that |
| librosa | "use `beat_track`" | 7 GB WSL | **Sidecar JSON**, librosa not a runtime dep |
| Keyframe review | "1 fps is enough for framing" | 1 fps misses the sharp moment in a 1.2 s shot | **3–5 fps scoring**, contact sheet for human eyes only |

---

### 3.5 Verification ledger — what survived checking

48 claims were fetched and checked against primary text on 2026-11-15:
**38 verified, 10 partial, 0 blocked** (full table with quotes in
`VERIFICATION.md`). The partials were not cosmetic — ten statements changed:

| # | What the draft said | What the source says | Fix |
|---|---|---|---|
| 1 | SWE-agent: "consolidating edits into one command" 10.3 → 18.0 | 10.3 is the **no-edit** ablation; edit 15.0, +lint 18.0 | §3.1 rewritten |
| 2 | ACI principles on `swe-agent.com` | principles are in the **paper**; the page lists features | attribution fixed |
| 3 | PySceneDetect `save-images` = one image per shot | **3 per scene by default** | `-n 1` documented |
| 4 | two-pass `loudnorm` recipe in the filter docs | options are documented; the recipe is in the **wiki** | attributed |
| 5 | ducking "attack < 20 ms / release > 200 ms" as spec | defaults only (20 / 250 ms); the rest is **practice** | marked as judgment |
| 6 | `adelay` "sample-accurate" | per-channel ms delays documented, that phrase is not | softened |
| 7 | Netflix minimum duration 5/6 s on the style-guide page | not on that page | dropped |
| 8 | OpenAI `high` = 2,500 patches (universal) | **model-specific** rows (5.4/5.5); newer models differ | hedged |
| 9 | Video-MME drops of −12/−18.5 and −7/−14.8 | actual tables: 61.7→50.1→44.3 and 81.7→74.3→67.4 | replaced with the table values |
| 10 | Meta safe zone 14 / 35 / 6 % | first-party article has **no percentages** | relabelled third-party |

Three local numbers were also corrected by re-running the measurements: the
beat drift is **0 → 176 ms** (not 212 ms), captions are **19.5 CPS mean /
37.7 CPS worst** (not 20.6 / 29.7), and the in-point weakness is **3 of 16
cuts under 0.60× median, worst 0.33×** (not a single 04_box reading — that cut
actually scores 0.35× while the cuts either side of it in the same take score
1.82× and 0.98×). The lesson is the document's own thesis: anything not
re-derived or re-fetched drifts.

## 4. Harness v5 design

### 4.1 Principle: the model chooses among measured options

Every number the agent needs should come from a tool, and every choice it makes
should be a *constrained selection* whose consequences the code can verify.

| Decision | Today | v5 |
|---|---|---|
| When a line starts/ends | agent guesses times | `narration_lines.json` from ASR + script alignment |
| Which clip for a line | agent eyeballs thumbnails | `shot_windows.json` shortlist with scores |
| In-point | agent guesses `in` | must be a scored window; renderer rejects low scores |
| Cut length | agent writes `dur` | planner derives from line length, snapped to `beat_grid.json` |
| Caption text | agent reads frames | `caption_audit.json` lists every card |
| Is it good? | 15 technical gates | technical gates + content checks + visual review sheet |

### 4.2 New artifacts

**`output/<sku>/narration_lines.json`** (written by `align_words.py`)

```json
{"lines": [
  {"id": 0, "t0": 0.00, "t1": 2.56, "text": "Un iPhone 14 por 330 dólares en Maracay",
   "kind": "hook"},
  {"id": 1, "t0": 2.72, "t1": 4.86, "text": "Te muestro exactamente por qué tiene este precio",
   "kind": "proof"}
]}
```

Line boundaries come from **script punctuation aligned to ASR words** (difflib
`SequenceMatcher`, ratio 0.97 on the demo) rather than from Whisper's optional
punctuation, which only flagged 10 of 15 sentence ends here. Clause-level
(comma) splits give ~16 units, which is exactly the target cut count.

**`input/<sku>/candidates.json`** (written by `extract_keyframes.py`, 3–5 fps scan)

```json
{"01_back_cameras.mp4": {
  "median_sharpness": 41.4,
  "windows": [
    {"id": "01c-a", "t": 3.60, "dur": 1.6, "sharpness": 43.6, "motion": 6.39, "score": 0.93},
    {"id": "01c-b", "t": 4.40, "dur": 1.6, "sharpness": 41.9, "motion": 6.51, "score": 0.88}
  ]}}
```

Windows are scored on sharpness (Laplacian variance) with a motion penalty; the
renderer refuses an in-point below 60 % of the clip median. **The agent's
`edit_plan.json` entry is `{"clip": "...", "candidate": "01c-a"}`, not a
timestamp** — this is the single change that removes the largest class of silent
error (BlindTest / ScreenSpot-Pro both say the model cannot reliably produce a
number it was not given). Explicit `in`/`dur` stay available for overrides and
are validated against the same table.

**`output/<sku>/beat_grid.json`** (written by a new `beat_grid.py`)

```json
{"bpm": 119.0, "beat_sec": 0.5042, "offset": 3.065, "beats": [3.065, 3.576, ...]}
```

Measured from `assets/music/*.wav`, cached by file hash. Either the planner
snaps cuts to real beat times, or the music is trimmed to start on the first
downbeat — but the grid is never assumed from a config constant again.

**`output/<sku>/caption_audit.json`** (written by `render_video.py`)

Every card with `text`, `t0`, `t1`, `cps`, `width_px`, `font_px`, plus aggregate
`mean_cps`, `max_cps`, `cards_over_20cps`. This lets the agent review captions as
**text**, which is cheap and reliable, instead of OCR-ing frames.

### 4.3 New tools

| Tool | Job | Replaces |
|---|---|---|
| `script_lint.py` | word budget for the target duration, unspeakable tokens, number/unit checks, phonetic hints | prose advice in AGENTS.md |
| `beat_grid.py` | tempo + phase + beat times from the music (run once, emits JSON; not a runtime dep) | config constant |
| `validate_edit_plan.py` | pre-render boundary check: unknown clip, `in+dur > source`, gaps/overlaps, duplicate shots, sum vs voiceover | a 3-minute render that fails at the end |
| `plan_edit.py` | takes `narration_lines.json` + `candidates.json` + agent picks, emits valid `cuts` on beats | hand-written `in`/`dur` |
| `audit_frames.py` | one full frame per cut, native-scale caption strips, labels burned in | 270 px tiles |
| `eval_run.py` | golden-SKU regression: runs the pipeline, diffs metrics, fails on regressions | nothing |

`plan_edit.py` is the important one. The agent's input becomes a simple,
checkable choice per line:

```json
{"line": 0, "clip": "01_back_cameras.mp4", "shot": 0}
```

The planner then: picks the scored window, snaps the cut to the beat grid,
distributes lines to cuts, and writes `edit_plan.json`. If a line is longer than
one shot's usable window, it splits across two clips from the same shortlist and
marks it. The agent can still override with explicit times, but that override is
validated (in-range, sharp enough, on a beat) and reported.

### 4.4 Guardrails that should be code, not prose

Every check emits one machine-readable row — `{id, measured, threshold, pass,
fix}` — into `qa_report.json`:

```json
{"checks": [
  {"id": "v_level",   "measured": "4.2",   "threshold": "4.2",       "pass": true},
  {"id": "a_lufs",    "measured": -14.33,  "threshold": "-14.0±0.5", "pass": true},
  {"id": "c_cps_max", "measured": 37.7,    "threshold": 25.0,        "pass": false,
   "fix": "card #11 'TE LLEVAS' shows for 0.24 s; merge it into the next card or cut a word"}
]}
```

The `fix` string is the whole point: a retry that consumes a specific
remediation is self-correction; re-running the same command after the same
failure is not. `AGENTS.md` may deliver only on `pass: true` for every row.

1. **Clip identity assertion** — sample the rendered frame at each cut midpoint,
   match it against the planned source (perceptual hash of the normalised clip
   frame) and fail QA on mismatch.
2. **In-point quality floor** — reject `in` whose window sharpness < 60 % of the
   clip median; report the score in `edit_plan.json`.
3. **Caption CPS** — warn above 20 CPS, fail above 25 CPS (or allow with an
   explicit `allow_fast_captions` flag in `sku.json`).
4. **Duration** — hard fail if final > 28.5 s; the current warning path makes
   this fail indirectly, which is fragile.
5. **Schema validation** (`jsonschema`) for `sku.json`, `edit_plan.json`,
   `narration_lines.json` — unknown clip names and misspelled keys fail before
   a 2-minute render.
6. **`--strict`** — treat every warning as failure, for delivery runs.

### 4.5 AGENTS.md v5 contract

Replace "do this, never do that" prose with a phased loop. Each phase: inputs,
tool, output, acceptance test, and what to do when it fails.

```
PHASE 0  intake      → script_lint.py            accept: tempo estimate ≤ 1.12×
PHASE 1  look        → extract_keyframes.py      accept: shot_windows.json exists, ≥1 window/clip
PHASE 2  choose      → plan_edit.py (you pick clip per line)
                                                  accept: no line without a clip, all picks in shortlist
PHASE 3  render      → render_video.py            accept: exit 0, caption_audit.json written
PHASE 4  review      → caption_audit.json + visual_audit.json
                                                  accept: read every card; images for framing only
PHASE 5  deliver     → qa_check.py --strict       accept: RESULT: PASS, 0 warnings
```

Rules that survive as prose should be the ones a test cannot express:

- "You are reviewing an edit, not approving an encode."
- "If a number is not in a JSON file, you do not know it."
- "One clip per narration line; if a line needs two shots, say why in the log."
- "Record every rejected shot and the reason in `decision_log.json`."

### 4.6 Visual review sheet

`audit_frames.py` produces what a VLM can actually judge:

- **one frame per image**, full 1080×1920 (arrives at ~819×1456 on Claude's
  standard tier — the 46 px captions survive at ~35 px), labelled `#1...#n` in
  the prompt, never a 9-up sheet for framing or text;
- extra frames at **cut ±100 ms** and through the **0.16 s punch-in**, because
  1 fps gives ~2 frames per shot and misses the transient;
- a **caption strip** sheet: native-width crops of `y=1305..1628`, 3 per row
  (1080×969 — under the 1456 long edge, so *no downscale*: text stays 46 px).
  This one is safe as a sheet precisely because it is a crop, not a tile;
- a **shot-line sheet**: each narration line printed above the frame at its
  midpoint, so the model judges match, not memory;
- ≤20 images per request (Claude's stricter many-image limit), **one question
  per call**, forced-choice answers ("A or B"), and an explicit `Unknown`.

This follows the rule the research supports: **ask the model what it can see at
the resolution it will receive, and compute everything else.** Numeric
questions — luma, clipping, overflow, contrast, safe-zone containment, card
text — never go to the model at all.

---

## 5. Metrics and evaluation

**Per video (auto):**
technical pass/fail; `duration_sec`; `mean_lufs`; `beat_offset_ms` (mean/max);
`caption_mean_cps`, `caption_max_cps`; `in_point_sharpness_ratio` (min/mean);
`warnings` count; `clip_identity_mismatches`.

**Per golden SKU (regression):** the three metrics above plus a perceptual diff
(SSIM) against the approved baseline render; fail if SSIM < 0.98 on unchanged
inputs, or if any metric crosses a threshold.

**Process metrics (harness quality):** tool calls per video, wall time, number
of re-renders, number of agent-authored overrides, number of decisions with no
rationale in `decision_log.json`.

**Content (human or LLM-judge, advisory only):** hook clarity in the first 2 s,
shot↔line match, caption readability, trust stack present by 24 s, single CTA.
These stay out of the pass/fail gate until they are shown to correlate with
human ratings — LLM judges are noisy and should not gate delivery.

---

## 6. Roadmap

**Week 1 — deterministic wins (no agent changes):**
`beat_grid.json` sidecar; script word-budget check in `align_words.py`;
`validate_edit_plan.py`; `qa_report.json` with `{measured, threshold, fix}`;
in-point sharpness floor in `render_video.py`; `caption_audit.json` + CPS
warn/fail; `--strict` in `qa_check.py`; keyframes at 540 px, 3–5 fps scoring
pass, caption-band crops.

**Week 2 — constrained planning:**
`narration_lines.json` from script alignment; `candidates.json` with IDs;
`plan_edit.py` (agent picks IDs); schema validation; clip-identity check in QA;
`decision_log.json`.

**Week 3 — regression and docs:**
`eval_run.py` with three golden SKUs; rewrite AGENTS.md as the phase contract
with a decision table and one worked example per phase; README truth pass;
delete claims the code does not implement.

---

## 7. Risks and open questions

- **Evidence quality:** every citation was re-fetched with `curl` and checked
  (§3.5): 38 verified, 10 partial, 0 blocked. The remaining soft spots are
  named there — Meta's pixel safe zones are still third-party, OpenAI's patch
  budgets are per-model and moving, and the Netflix minimum-duration rule was
  dropped. No study measures VLM legibility of a 46 px caption inside a video
  frame; that number is derived from vendor preprocessing rules. The nearest
  evidence (AwaRes, DT-VQA) says small/dense text is exactly what
  low-resolution input loses — enough to support cropping, not to pin a pixel
  threshold.
- **Most harness evidence comes from coding and research agents.** The transfer
  to video editing is plausible and consistent with our own failures, but it is
  analogy, not a controlled result. Treat §3.1 as strong guidance, not proof.
- **Vision-degradation figures are about *coordinates and small text*** — they
  say nothing about aesthetic judgement, which is a different (and less
  validated) capability. Hence §5 keeps creative checks advisory.
- **Beat detection on stems with intros:** the detected first beat is 3.065 s;
  either trim the music or offset the grid. Validate by ear on a real render.
- **Scene detection is deliberately not in the plan.** It returns nothing on
  continuous takes; adding it now would manufacture a tool without a job.
- **CLIP/embedding line↔shot matching** is tempting but weak on generic product
  footage (five clips of the same phone). Keep clip choice with the model;
  use code for scoring, not semantics.
- **Caption CPS:** 20 CPS is a *subtitle* standard, not a reels standard; fast
  captions are common on social. Treat >25 CPS as the hard line, 20–25 as a
  warning the agent may accept with a reason.
- **Safe-zone change needs a product decision — and the numbers are third-party.**
  The measured stack occupies 292–615 px from the bottom; the widely quoted
  ad exclusion is 672 px, but Meta's first-party article publishes no
  percentages (only "keep the edges free of key creative elements"). That makes
  the current layout *correct* for WhatsApp Status (little bottom chrome) and
  *possibly risky* for paid Reels. Do not move it silently — add a `reels_ads`
  preset, choose per placement, and if it matters commercially, verify against
  Meta's own downloadable spec rather than a blog.
- **A 28 s ceiling is a product choice**, not a platform rule; if longer edits
  are acceptable, relax it explicitly rather than through the warning path.

---

## Appendix — sources

Every source below was fetched with `curl -sSL` on 2026-11-15; per-claim status
and verbatim quotes are in **`VERIFICATION.md`**. Where a host blocked this
machine — `platform.claude.com` geo-blocks it, `platform.openai.com` and
`dl.acm.org` return 403 — the same URL was read through the Wayback Machine
(capture dates in the ledger). Tool versions were read from PyPI the same day:
PySceneDetect 0.7.3, librosa 1.0.1, faster-whisper 1.2.1, WhisperX 3.8.6,
auto-editor 29.3.1, Essentia 2.1b6, MFA 3.4.3, beat_this 1.1.0.

**Agent-harness design (§3.1)**

- Anthropic, *Demystifying evals for AI agents* — `anthropic.com/engineering/demystifying-evals-for-ai-agents`
- Anthropic, *Harness design for long-running apps* — `anthropic.com/research/harness-design-long-running-apps`
- Anthropic, *Effective context engineering for AI agents* — `anthropic.com/engineering/effective-context-engineering-for-ai-agents`
- Anthropic, *Writing tools for agents* — `anthropic.com/engineering/writing-tools-for-agents`
- Anthropic, *Claude Code best practices* — `anthropic.com/research/claude-code-best-practices`
- SWE-agent — `arxiv.org/abs/2405.15793` · ACI — `swe-agent.com/latest/background/aci/`
- *LLMs cannot self-correct reasoning yet* — `arxiv.org/abs/2310.01798`
- *Agentless* — `arxiv.org/abs/2407.01489`
- *Judging LLM-as-a-Judge (MT-Bench)* — `arxiv.org/abs/2306.05685`
- OpenAI, *A practical guide to building agents* — `cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf`
- OpenAI Agents SDK guardrails — `openai.github.io/openai-agents-python/guardrails/`
- opencode rules — `opencode.ai/docs/rules` · Cursor rules — `cursor.com/docs/context/rules`

**Vision limits (§3.2)**

- Claude vision + coordinates — `platform.claude.com/docs/en/build-with-claude/vision`, `/vision-coordinates` (geo-blocked here; read via Wayback 2026-11-10 / 2026-10-20)
- OpenAI vision — `developers.openai.com/api/docs/guides/images-vision` (the `platform.openai.com` path 403s; budgets are model-specific)
- Gemini image understanding — `ai.google.dev/gemini-api/docs/image-understanding`, `/media-resolution`
- *ScreenSpot-Pro* (18.9 % → 48.1 % with crops) — `arxiv.org/abs/2504.07981`
- *Set-of-Mark prompting* — `arxiv.org/abs/2310.11441`
- *Visual Sketchpad* — `arxiv.org/abs/2406.09403`
- *BlindTest* (VLM spatial failures) — `arxiv.org/abs/2407.06581`
- *OCRBench* — `arxiv.org/abs/2305.07895` · *ChartQA* — `arxiv.org/abs/2203.10244`
- *MMIU* (multi-image reasoning) — `arxiv.org/abs/2408.02718`
- *Video-MME* (video sampling) — `arxiv.org/abs/2405.21075` · *TVBench* — `arxiv.org/abs/2410.07752`
- *AwaRes* (low-res view + high-res crops for small text, 2026) — `arxiv.org/abs/2603.16932`
- *DT-VQA* (dense text is hard for LMMs, 170 K QA) — `arxiv.org/abs/2405.06706`
- Meta ad safe zones (secondary) — `marxx.ai/guides/instagram-reels-safe-zone` · TikTok spec — `ads.tiktok.com/help/article/video-ads-specifications`
- WCAG contrast — `w3.org/WAI/WCAG22/Understanding/contrast-minimum.html`

**Media tooling (§3.3)**

- PySceneDetect — `github.com/Breakthrough/PySceneDetect`, `scenedetect.com/benchmarks/`, `/docs/latest/cli.html`
- FFmpeg filters (`loudnorm`, `sidechaincompress`, `scdet`, `adelay`, `blackdetect`) — `ffmpeg.org/ffmpeg-filters.html`
- librosa `beat_track` — `librosa.org/doc/0.11.0/generated/librosa.beat.beat_track.html` (the `/latest/` path 404s; signature also confirmed in `librosa/beat.py`)
- Essentia `RhythmExtractor2013` — `essentia.upf.edu/reference/std_RhythmExtractor2013.html`
- beat_this — `github.com/CPJKU/beat_this` · BeatNet — `github.com/mjhydri/BeatNet`
- auto-editor — `github.com/WyattBlue/auto-editor` · ffmpeg-normalize — `github.com/slhck/ffmpeg-normalize`
- faster-whisper — `github.com/SYSTRAN/faster-whisper` · WhisperX — `github.com/m-bain/whisperX`
- MFA alignment — `montreal-forced-aligner.readthedocs.io/en/latest/user_guide/workflows/alignment.html`
- ElevenLabs create-speech — `elevenlabs.io/docs/api-reference/text-to-speech/convert`
- BBC subtitle guidelines — `bbc.co.uk/accessibility/forproducts/guides/subtitles/`
- Netflix timed-text style guide — `partnerhelp.netflixstudios.com/hc/en-us/articles/217350977-English-Timed-Text-Style-Guide`
- Shot retrieval with script (ICMR 2023) — `dl.acm.org/doi/fullHtml/10.1145/3591106.3592247`
- awesome-video-editing — `github.com/wentianli/awesome-video-editing`

**Stale — do not adopt:** madmom (last release 2018), aubio (2019),
JumpCutter (notebook, unmaintained).
