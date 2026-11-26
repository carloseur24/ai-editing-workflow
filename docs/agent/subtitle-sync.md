# Subtitle sync: ASR → timed captions

A 23.8 min Japanese episode, no subtitle stream, burned-in Spanish/English text,
Spanish target. v1 passed every structural gate and still lost 32 s of dialogue.
The viewer's report: *"our lines only appear where the burned-in subs already
appear."*

## When this applies

Use it when **you do not own the transcript** and the target language differs
from the speech: ASR → translate → time → mux.

Do **not** use it when:

- you own the script (a written VO script is ground truth — `fenix align` maps
  its punctuation to words; ASR is only a timing aid, not a text source);
- the video is already correctly subtitled in the target language (re-timing
  someone else's good cues is cheaper than re-deriving them).

Everything below assumes the hard case: speech you must transcribe, a language
you must translate, and burned-in text competing for the same pixels.

## Engine selection is arithmetic

Measured on this box (16 CPU threads, int8, no GPU), 1430.0155 s source,
23.976 fps, aac 48 kHz stereo, `speech_ratio` 0.9701:

| Engine / config | Measured | Projection | Verdict |
|---|---|---|---|
| `large-v3`, int8, beam 5, word ts | rtf **4.0548** (486.57 s to decode 120 s) | **96.6 min** | killed at minute 2 |
| OpenAI `whisper-1` | HTTP **403** `unsupported_country_region_territory` | — | region-blocked, key valid |
| `large-v3-turbo`, int8, 16 threads, beam 1 | **717.3 s wall** for 1430 s (rtf ≈ **0.50**); 253 segments, cjk_ratio **0.9936**, 0 repetition loops | ~12 min | shipped |

Rules:

1. **Compute rtf on a 120 s slice before committing.** If rtf > 1.5, change
   model or batching first — do not start the full run and hope.
2. A 403 is a region decision, not a transient error. Record the HTTP body
   (`openai_http.status`), stop, pick a local engine. Never retry a 403.
3. Any ASR run longer than 60 s must print a progress line (s decoded, rtf,
   projected finish) at least every 60 s. "No output for 70 s" is what makes
   an operator abort a run that was working.
4. Container audio language tags lie. This file said `eng`; two independent
   30 s/90 s windows said `ja` at p=0.9768/0.9749. Detect on a slice; do not
   trust the muxer.

## The pipeline that worked

Order matters: each step exists because the previous one cannot answer the next
question.

| # | Step | Why it sits here |
|---|---|---|
| 1 | `ffprobe` → facts (duration, fps, streams, speech ratio) | decides ASR vs script path; no subtitle stream → ASR |
| 2 | language detect on 30 s + 90 s windows | cheapest step that can invalidate the whole plan |
| 3 | ASR **with word timestamps** on the full file | word timings are the only way to split a 51 s segment later |
| 4 | translate in **dialogue blocks**, not per segment | keeps register and sentence context; per-segment translation fragments sentences |
| 5 | split the Spanish across **measured word clusters** (≥ 0.8 s silence) | timing decision made on finished text, so no re-translation |
| 6 | emit SRT **and** positioned ASS from one `cues.json` | one timing truth, two renderers; never two independent muxes |
| 7 | gate against **the audio**, not the transcript | the only check that does not trust our own output |

Secondary pass that was necessary here: 36 of 253 segments (`gap_fill`,
`hole_recovery`) carry **no word timings** — they can only be timed from segment
bounds. Mark them `anchored: "segment"` and audit them separately.

## The failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Audit says **0 holes / 18 gaps / PASS**, but lines are missing | Silero VAD judging Silero VAD output — circular | re-audit with VAD **and** every guard off: 63 gaps ≥ 1.5 s = **599.6 s**, only **72.4 s** with speech (22.6 s dialogue in 6 zones, 49.7 s OP/ED lyrics); ~525 s really was music/silence/credits |
| Real ASR text thrown away as "music" | `merge_gaps.py` gate required `speech_pct ≥ 10 %` in the window | treat recovered text as evidence; lower/remove the percentage veto, keep only phrase/hallucination filters |
| Lines exist for the tail of a sentence but not the head | `anchors_for()` kept **only the largest word cluster** of any segment > 7 s | split **every** cluster; give each its own share of the text. 35/253 segments, 27 clusters, **32.20 s / 55 words** were outside all cues (worst: seg 252 **15.56 s**, seg 110 1.86 s, seg 225 1.12 s) |
| v1 "covered 253/253", 0 overlaps, max CPS 17.21, 0 boundary failures — and still silent | coverage defined **against our own transcript** | define coverage against the audio (see gates) |
| `gap_fill` cues drift a second off the voice | those segments have `words: []` | time them from segment bounds only, label `anchored: "segment"`, accept the wider error |
| A flagged/low-confidence line reads fluent but wrong | translation with no confidence handling | carry the segment's `no_speech`/`logprob` flag into the block; translate flagged lines literally and mark them for review |
| Spanish cues land **on** the burned-in text | band never measured; `luma > 210` over-fires on bright art | measure per-frame: ink = `luma > 200 AND 5x5 min < 90`. Here: band **y 598–681** over 64 frames → `MarginV 148`, `BorderStyle 3`, `Outline 9` |
| SRT "positioned above the subs" is still at the bottom | SRT cannot express position | position lives in ASS only; SRT is the portable fallback |
| Player shows a subtitle you did not choose, from a file you moved | 8 stale tracks accumulated in one VLC session | one file per review session; re-open the player, do not re-select |
| Playback silently uses the wrong file | plain `<basename>.srt` auto-loads next to the video | never leave an auto-load SRT pointing at a superseded version; name variants explicitly (`*.v2.es.srt`) |

Two traps are environmental, not creative: VLC refused to save settings ("no se
pudo guardar la configuración") because its config was read-only, so a subtitle
option change never applied; and `BorderStyle 3` draws **one box per line** — inherent, not a bug.

Process mistakes that cost the most time: accepting `rtf = 4` instead of killing
it in minute one; shipping v1 with **0 boundary failures** and **32 s of speech
outside cues**; creating the auto-load `.srt` without asking what it would point
at later; and long ASR runs with no progress line.

## The three gates that actually catch errors

Run all three on the emitted `cues.json`; gates 1 and 3 never read the transcript.

**G1 — audio coverage.** For every 0.5 s window (hop 0.25 s) whose 300–3000 Hz
RMS exceeds −40 dBFS, a cue must cover ≥ 50 % of the window, unless an audited
music/silence zone covers it.

**G2 — word coverage.** Every ASR word midpoint sits inside some cue. Classify
misses as artifacts (ghost prefix tokens, audited non-dialogue zones) and
require **0 unexplained**.

**G3 — boundary provenance.** Every cue edge is within 0.20 s of a measured word
edge or segment edge.

```python
band = bandpass(audio, 300, 3000)                 # G1
for w in windows(0.5, hop=0.25):
    if rms_db(band[w]) > -40 and covered_by_cue(w) < 0.5 * 0.5:
        flag(w)                                    # unless audited zone
for word in words:                                 # G2
    expect(in_any_cue((word.start + word.end) / 2))
for edge in cue_edges:                             # G3
    expect(min_dist(edge, word_edges + segment_edges) <= 0.20)
```

Result: v1 → v2 went 291 → **287 cues** (201 unchanged, 20 re-timed, 66 re-split
— 52 existing cues plus 14 word clusters that had no cue at all), word coverage
**100 % of speech words** (40 classifier artifacts, **0 unexplained**), **0
unjustified silent stretches**, wording token-identical at 1263 tokens — the fix
moved time, not text.

## Concrete recommendations for this repo

- **`align` should emit cluster boundaries.** It already produces word timings;
  `narration_lines.json` takes its boundaries from the *script's* punctuation
  (difflib against ASR), which is the wrong source for a transcript you did not
  write. Add a `clusters` array per segment, split at ≥ 0.8 s silence. Every
  downstream split in this job depended on that one array.
- **`qa` has no speech-vs-cue check.** Its caption checks are `c_timings`
  (words present) and `d_script_match` (similarity to our own script) — the same
  circularity that let v1 pass. G1 is ~30 lines with numpy and `soundfile`
  (bandpass, window RMS, overlap mask) and would have caught this in one run.
- **`config` alignment should allow `large-v3-turbo`.** `alignment.model` is
  `"small"`, which is right for a Spanish VO you already have a script for and
  inadequate for Japanese dialogue: `small` cannot hold kana/kanji well enough to
  translate from. Document the split: `small` = timing against known text;
  `large-v3-turbo` = transcription you must trust (`large-v3` is correct but
  rtf 4 on CPU).
- **There is no subtitle emit stage.** If one is added, it reads one `cues.json`
  and writes SRT + ASS from it. Two independent muxes is how a correct cue set
  becomes two files that disagree.

## Checklist

1. Probe first; confirm there is no subtitle stream and measure `speech_ratio`.
2. Detect language on a 30 s slice; distrust the container tag.
3. Time a 120 s ASR slice; abort and switch if rtf > 1.5.
4. Require word timestamps; refuse a transcript without them.
5. Translate in dialogue blocks, never per segment.
6. Build clusters at ≥ 0.8 s silence, then split the text across **all** of them.
7. Emit SRT and positioned ASS from the same `cues.json`.
8. Measure the hardsub band per frame before choosing `MarginV`.
9. Run G1, G2, G3; ship only with 0 unjustified stretches and 0 unexplained words.
10. Spot-check by ear at the worst three stretches the gates flagged — not at the ones they passed.
