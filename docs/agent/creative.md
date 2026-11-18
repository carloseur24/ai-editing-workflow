# Editorial contract

These are the rules that make a Fenixsoft piece feel like Fenixsoft. The gates
check the measurable parts; this page explains the intent behind them.

## Story shape (28 s target)

| Time | Job | Example |
|---|---|---|
| 0–3 s | price + honest condition | “iPhone 14 en $330 USDT — 77% de batería, 100% funcional, sin cuentos.” |
| 3–18 s | physical proof, 2–3 visible details | cameras, Face ID, storage, included case/charger |
| 18–24 s | trust stack | 1-month store warranty (+ extended option), trade-in (`parte de pago`) |
| 24–28 s | single-unit urgency + CTA | “Solo una unidad disponible. Escríbenos o visítanos en Mercado Social, Local C-29, Maracay.” |

The piece sells one unit of one product. No “varios disponibles”, no generic
catalog copy, no price without the condition that explains it.

## Script budget

At the measured pace of the demo voice (162 wpm natural) and the pacing ceiling
of 1.12×, 28 s holds **84 spoken words**:

```text
162 wpm × 1.12 × (28 / 60) min = 84.7 → 84 words
```

`fenix lint` computes this from `config/pipeline.json → script`, not from prose.
If the script needs more words, cut words; a longer hold or a faster read shows
up as rushed captions and a worse piece.

## Phonetics for ElevenLabs (Spanish)

Spell tech and brand tokens the way the voice must say them:

| Write | Say as |
|---|---|
| iPhone | áifon |
| WhatsApp | Guatsap |
| Fenixsoft | Fénix-soft |
| eSIM / e-sim | i-sim |
| GB | gigas |
| USDT | u-ese-dé-té |
| Face ID | féis ái-di |
| Wi-Fi | uáifai |

Numbers in words, not digits: `trescientos treinta dólares`, not `$330`;
`setenta y siete por ciento`, not `77%`. `fenix lint` fails unspeakable tokens
and lists the brand hints it found.

## Captions

- ≤ 4 words / 26 chars per card; the DP chunker never ends on a function word
  and keeps numbers with their noun.
- Cards shorter than 0.45 s are merged into a neighbour (≤ 6 words / 26 chars).
- The active word turns emerald `#10B981`; the pill stays stable.
- CPS is gated: **warn > 20**, **fail > 25**. `allow_fast_captions: true` in
  `sku.json` downgrades both to warnings; prefer shortening the script.
- `display_map` fixes a mangled token (`14X330` → `14` `×` `330`);
  `display_merge` joins tokens (`ES SIM` → `E-SIM`).

## Safe zones (`config/pipeline.json → safe_zones`)

| Preset | x | Bottom stack |
|---|---|---|
| `reels_universal` | 65–950 | price `y=1310`, captions `y=1445`, footer `y=1585` |
| `status_story` | 65–1015 | price `y=1350`, captions `y=1485`, footer `y=1625` |

- Keep the centre phone footage unobstructed: header `y=110..318`, bottom stack
  roughly `y=1305..1628`.
- Every text element auto-fits its zone; the caption block is ≤ 885 px wide and
  never below 24 px.
- Vector emerald checkmarks (`#059669`) only — never the Unicode `✓`, which the
  font may render inconsistently.

Change a safe zone only deliberately, in `config/pipeline.json`; the layout is
asserted before a single frame is encoded.

## Edit rhythm

- 14–17 cuts over 28 s, 4–6 angles, no clip back-to-back.
- Cuts snap to the **measured** beat grid (118.97 BPM on the demo bed), not the
  nominal 118.
- Every cut gets a 0.16 s punch-in: 1.12× → 1.0× with an out-cubic ease.
- Minimum cut 1.2 s; the planner splits long lines at word boundaries and merges
  short ones so the count is reached without violating the minimum.

## Audio

- **Cue sheet first:** `fenix sfx` plans the events, then mixes them. The layers
  are environmental bed, J-cut anticipation (whooshes/risers), tactile Foley
  (2–7 kHz click + 80–180 Hz body thud at +18 ms) and sub impacts on real flips.
- Music bed −28 LUFS, ducked **4.5 dB** under foreground transients and muted
  **120 ms** before a major flip (the vacuum rule). No two major subs within
  1.5 s.
- Legacy fallback (no fresh cue sheet): one soft air-swish per cut, peak aligned
  to the cut.
- Integrated loudness −14 LUFS (±1), true peak ≤ −1.0 dBTP, no full-scale
  samples; AAC 128 kbps, 48 kHz stereo.

The full standard is [sound-design.md](sound-design.md); the command is
`fenix sfx <project>`.

## Words that help and words that hurt

- Help: `funcional`, `batería`, `garantía`, `parte de pago`, `única unidad`,
  `Local C-29`, `Maracay`.
- Hurt: `barato`, `regalado`, `perfecto`, `como nuevo`, `sin detalles` — the
  hook is “honest condition”, not hype.
- Never claim a warranty, capacity or part you cannot show in the footage or the
  `sku.json`.
