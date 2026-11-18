# Citation verification ledger — HARNESS_RESEARCH.md

> **Archived research (pre-refactor).** File names and commands refer to the
> flat-script layout that has since moved into `src/fenixsoft/`. See
> [`docs/reference/commands.md`](../reference/commands.md) for the current
> surface.

**Date:** 2026-11-15 · **Method:** every claim fetched with
`curl -sSL -m 25 -A '<browser UA>'`, HTML stripped locally, numbers and wording
grepped from the primary text. No `WebFetch` (its safe-domain pre-check is
unreachable in this environment; the network itself is fine). Cloudflare-gated
pages were read through the Wayback Machine.

Status vocabulary:

- **VERIFIED** — the primary text supports the exact number/wording.
- **PARTIAL** — the idea is supported but the exact figure/wording is not in
  that document (the note says what it actually says).
- **NOT_FOUND** — page fetched, claim absent.
- **BLOCKED** — could not fetch after one fallback.

---

## A. Agent-harness design (15 claims: 13 verified, 2 partial)

| # | Status | Evidence (verbatim, trimmed) | Source | Note |
|---|---|---|---|---|
| H1 | VERIFIED | code graders "Fast • Cheap • Objective • Reproducible"; model graders "Non-deterministic", "Requires calibration with human graders" | anthropic.com/engineering/demystifying-evals-for-ai-agents | comparison table |
| H2 | VERIFIED | "'Unknown' … grade each dimension with an isolated LLM-as-judge" | same | — |
| H3 | VERIFIED | "agents tend to respond by confidently praising the work—even when … the quality is obviously mediocre"; "Each criterion had a hard threshold" | anthropic.com/research/harness-design-long-running-apps | — |
| H4 | VERIFIED | "context rot: as the number of tokens … increases, the model's ability to accurately recall … decreases"; "smallest possible set of high-signal tokens"; subagent summary "often 1,000–2,000 tokens" | anthropic.com/engineering/effective-context-engineering-for-ai-agents | — |
| H5 | VERIFIED | "~⅓ of the tokens with 'concise' tool responses"; "concise tool response (72 tokens)" vs "detailed (206 tokens)"; "restrict tool responses to 25,000 tokens" | anthropic.com/engineering/writing-tools-for-agents | — |
| H6 | VERIFIED | "clear target to iterate against—a visual mock, a test case"; "after 2-3 iterations" | anthropic.com/research/claude-code-best-practices | — |
| H7 | **PARTIAL** | table: "No edit 10.3 / Editor edit 15.0 / w/ linting 18.0"; viewer "30 lines 14.3 / 100 lines 18.0 / Full file 12.7" | arxiv.org/abs/2405.15793 (body /html/) | **10.3 is the no-edit ablation, not a "consolidated command" baseline.** Lint 15→18 confirmed. |
| H8 | **PARTIAL** | "We add a linter that runs when an edit command is issued…" | swe-agent.com/latest/background/aci/ | the four ACI principles are in the paper §4, not on the docs page |
| H9 | VERIFIED | "LLMs struggle to self-correct their responses without external feedback, and at times their performance even degrades after self-correction" | arxiv.org/abs/2310.01798 | abstract |
| H10 | VERIFIED | "highest performance (32.00%, 96 correct fixes) and low cost ($0.70)" | arxiv.org/abs/2407.01489 | SWE-bench Lite |
| H11 | VERIFIED | "over 80% agreement"; "position, verbosity, and self-enhancement biases" | arxiv.org/abs/2306.05685 | abstract |
| H12 | VERIFIED | "layered defense mechanism … multiple, specialized guardrails"; exit conditions include "a maximum number of turns" | cdn.openai.com/…/a-practical-guide-to-building-agents.pdf | p.14 |
| H13 | VERIFIED | "Input tool guardrails run before the tool executes and can skip the call" | openai.github.io/openai-agents-python/guardrails/ | "blocking" wording is about model-call guardrails |
| H14 | VERIFIED | "build, lint, and test commands … focused verification steps" | opencode.ai/docs/rules | — |
| H15 | VERIFIED | "Always Apply … Apply to Specific Files … Apply Manually"; plus "Apply Intelligently" | cursor.com/docs/context/rules | fourth type exists |

**Corrections applied to the document:** H7 rewritten (no-edit ablation ≠
consolidation); H8 attribution moved to the paper; Cursor gets four rule types.

---

## B. Media tooling (17 rows: 11 verified, 5 partial, 1 arithmetic-only)

| # | Status | Evidence (verbatim, trimmed) | Source | Note |
|---|---|---|---|---|
| M1 | VERIFIED | AdaptiveDetector F1 91.59 / 73.86 / 55.75; ContentDetector 86.69 / 69.26 / 55.84 | scenedetect.com/benchmarks/ | v0.7 defaults; 0.7.3 released 2026-11-08 |
| M2 | **PARTIAL** | "min_scene_len defaults to 15 frames in Python, while the CLI default is 0.6s"; "Number of images to generate per scene … Default: 3" | scenedetect.com/docs/latest/cli.html | **save-images gives 3 per scene, not 1**; use `-n 1`; option is `--frame-window` |
| M3 | VERIFIED | "Good values are in the [8.0, 14.0] range." | ffmpeg.org/ffmpeg-filters.html#scdet | default 10 |
| M4 | **PARTIAL** | "If any of these conditions aren't met, normalization mode will revert to dynamic." | ffmpeg.org/ffmpeg-filters.html#loudnorm | options exist; the 2-pass JSON recipe is in the wiki, not the filter page |
| M5 | **PARTIAL** | attack "Default is 20"; release default 250 ms | ffmpeg.org/ffmpeg-filters.html#sidechaincompress | attack<20 / release>200 is **practice, not doc** |
| M6 | **PARTIAL** | "Set list of delays in milliseconds for each channel" | ffmpeg.org/ffmpeg-filters.html#adelay | sample-accurate wording not used |
| M7 | VERIFIED | "start_bpm = 120.0 , tightness = 100 , trim = True … units = 'frames'" | librosa.org/doc/0.11.0/generated/librosa.beat.beat_track.html | `/latest/` 404s |
| M8 | VERIFIED | madmom "0.16.1", upload 2018-11-14; aubio 0.4.9, 2019-02-08 | pypi.org/pypi/madmom/json, /aubio/json | no later releases |
| M9 | VERIFIED | `method ∈ {multifeature, degara}`, default multifeature; ticks + confidence; minTempo [40,180], maxTempo [60,250] | essentia.upf.edu/reference/std_RhythmExtractor2013.html | 90/140 inside ranges |
| M10 | VERIFIED | "`--edit audio:threshold=0.04,stream=all` is used by default"; margin 0.2 s | raw.githubusercontent.com/WyattBlue/auto-editor/master/README.md | — |
| M11 | VERIFIED | "160-180 words-per-minute (WPM) or 0.33 to 0.375 second per word"; "maximum of three lines"; "maximum of 37 characters per line" | bbc.co.uk/accessibility/forproducts/guides/subtitles/ | — |
| M12 | **PARTIAL** | "42 characters per line"; "Maximum two lines"; "Adult programs: Up to 20 characters per second"; children 17 | partnerhelp.netflixstudios.com/…/217350977-… | **no 5/6 s minimum on this page** (separate doc) |
| M13 | VERIFIED | "1.0 is the default speed, while values less than 1.0 slow down … greater than 1.0 speed it up"; stability 0.5, similarity_boost 0.75 | elevenlabs.io/docs/api-reference/text-to-speech/convert | — |
| M14 | VERIFIED | "Accurate word-level timestamps using wav2vec2 alignment"; `word_timestamps=True`, `vad_filter=True` | github.com/m-bain/whisperX; github.com/SYSTRAN/faster-whisper | Spanish model `VOXPOPULI_ASR_BASE_10K_ES` |
| M15 | VERIFIED | `mfa align CORPUS DICTIONARY ACOUSTIC OUTPUT`; `--output_format json` | montreal-forced-aligner.readthedocs.io/…/alignment.html | signature changes in MFA 4.0 |
| M16 | VERIFIED | title, ICMR '23, pp. 298–306 | dl.acm.org/doi/… (403) → Wayback + Crossref | full text not read |

**Corrections applied:** save-images default; sweep parameters marked as
community tuning; ducking values marked as practice; loudnorm recipe attributed
to the wiki; Netflix minimum duration dropped.

---

## C. Vision limits (17 claims: 14 verified, 3 partial)

| # | Status | Evidence (verbatim, trimmed) | Source | Note |
|---|---|---|---|---|
| V1 | VERIFIED | "Each patch is a 28×28-pixel block … referred to as a visual token"; cost = ⌈w/28⌉×⌈h/28⌉ | platform.claude.com/…/vision (Wayback 2026-11-10) | 1000×1000 = 1,296 tokens |
| V2 | VERIFIED | "1920x1080 … 1456x819 … 1,560"; high-res tier "Not resized … 2,691" | same | standard tier shrinks portrait to ~819×1456 |
| V3 | VERIFIED | "more than 20 images, a stricter per-image dimension limit applies" | same | 8000×8000 max; 20 per message on claude.ai; stricter = 3,000 px |
| V4 | VERIFIED | "pixel positions in the image Claude sees: your image after Claude resizes it"; "pads every image … next multiple of 28" | …/vision-coordinates (Wayback 2026-10-20) | resizes called "most common cause of misaligned coordinates" |
| V5 | **PARTIAL** | "Some models tokenize images by covering them with 32px x 32px patches." | developers.openai.com/…/images-vision (Wayback) | 2,500 / 10,000 budgets are model-specific rows; platform.openai.com 403s |
| V6 | VERIFIED | "258 tokens if both dimensions <= 384 pixels … tiled into 768x768 pixel tiles, each costing 258 tokens" | ai.google.dev/gemini-api/docs/image-understanding | media_resolution 280/560/1120/2240; video 70/280 |
| V7 | VERIFIED | "best model achieving only 18.9% … cascaded search … 48.1% without any additional training" | arxiv.org/abs/2504.07981 | ScreenSeekeR |
| V8 | VERIFIED | "GPT-4V with SoM … outperforms the state-of-the-art fully-finetuned … model on RefCOCOg" | arxiv.org/abs/2310.11441 | — |
| V9 | VERIFIED | "average gain of 12.7% on math tasks, and 8.6% on vision tasks" | arxiv.org/abs/2406.09403 | — |
| V10 | VERIFIED | "four … VLMs are only 58.07% accurate on average. Claude 3.5 Sonnet … 77.84%" | arxiv.org/abs/2407.06581 | 7 tasks |
| V11 | VERIFIED | "multilingual text, handwritten text, non-semantic text, and mathematical expression recognition" | arxiv.org/abs/2305.07895 | — |
| V12 | VERIFIED | "9.6K human-written questions as well as 23.1K … generated" | arxiv.org/abs/2203.10244 | total 32.7K |
| V13 | VERIFIED | "11K … questions … 24 popular LVLMs … GPT-4o, achieve only 55.7%" | arxiv.org/abs/2408.02718 | spatial tasks weakest |
| V14 | **PARTIAL** | "restricted input frames can become an information bottleneck"; "should sample video frames at a fixed fps" | arxiv.org/html/2405.21075 | **percentage pairs in the draft were wrong**; tables give 61.7→50.1→44.3 and 81.7→74.3→67.4 |
| V15 | **PARTIAL** | "keep the edges (top, bottom and sides) free of key creative elements, text and logos" | facebook.com/business/help/980593475366490 (Wayback) | **no 14/35/6 % figures first-party**; bottom-40 % disclaimer only |
| V16 | VERIFIED | "safe zone size is determined by the dimension …, ad caption length, and any additional formats" | ads.tiktok.com/help/article/video-ads-specifications | no pixel numbers; files only |
| V17 | VERIFIED | "contrast ratio of at least 4.5:1" | w3.org/WAI/WCAG22/Understanding/contrast-minimum.html | 3:1 for ≥18 pt / 14 pt bold |

**Wayback fallbacks:** `platform.claude.com` geo-blocks this host ("App
unavailable in region"), so V1–V4 use snapshots 2026-11-10 and 2026-10-20;
`platform.openai.com` 403s, V5 uses `developers.openai.com` (2026-11-07) and a
January snapshot; V15 uses the 2025-05-10 Facebook business-help capture.

**Corrections applied:** OpenAI budgets hedged as model-specific; Video-MME
figures replaced with the paper's table values; Meta percentages relabelled as
third-party.

---

## Totals

| track | verified | partial | blocked |
|---|---|---|---|
| harness | 13 | 2 | 0 |
| media | 11 | 5 | 0 |
| vision | 14 | 3 | 0 |
| **total** | **38** | **10** | **0** |

---

## Package versions (PyPI, 2026-11-15)

| package | version | uploaded |
|---|---|---|
| faster-whisper | 1.2.1 | 2025-10-31 |
| whisperx | 3.8.6 | 2026-05-25 |
| auto-editor | 29.3.1 | 2025-11-04 |
| essentia | 2.1b6.dev1438 | 2026-05-19 |
| montreal-forced-aligner | 3.4.3 | 2026-10-08 |
| librosa | 1.0.1 | 2026-11-12 |
| scenedetect | 0.7.3 | 2026-11-08 |
| beat-this | 1.1.0 | 2026-04-14 |
