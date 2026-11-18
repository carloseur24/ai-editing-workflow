#!/usr/bin/env python3
"""
narration.py — narration lines: the script's own sentences/clauses, timed.

The agent should map *lines* to clips, never invent timestamps. Whisper's
optional punctuation only flagged 10 of the 15 sentence ends in the demo, so the
line boundaries come from `script_text` punctuation aligned to the ASR words
with difflib (`SequenceMatcher`), which measured 0.97 similarity on the demo.

Sentence ends give 8 lines; comma/semicolon clause ends give 16 units — exactly
the target cut count for a 28 s reel.

Output shape (written by the align stage to
`output/<sku>/narration_lines.json`):

    {"lines": [
       {"id": 0, "t0": 0.0, "t1": 2.56, "text": "...", "kind": "hook",
        "words": 8, "matched": 1.0}
    ]}
"""
from __future__ import annotations

import re
import unicodedata

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")


def _norm(token: str) -> str:
    txt = unicodedata.normalize("NFKD", token)
    txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
    txt = re.sub(r"[^\w%$]", "", txt.lower())
    return txt


def script_units(script: str) -> list[str]:
    """Sentences, then comma/semicolon clauses (edge punctuation is kept)."""
    units: list[str] = []
    for sentence in SENT_SPLIT.split(script.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        clauses = [c.strip() for c in CLAUSE_SPLIT.split(sentence) if c.strip()]
        units.extend(clauses)
    return units


def _map_script_to_words(script_tokens: list[str],
                         words: list[dict]) -> list[int | None]:
    """For each script token, the ASR word index it matched (or None)."""
    import difflib
    asr = [_norm(w.get("w", "")) for w in words]
    matcher = difflib.SequenceMatcher(None, script_tokens, asr, autojunk=False)
    mapping: list[int | None] = [None] * len(script_tokens)
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            if a + k < len(mapping):
                mapping[a + k] = b + k
    return mapping


def build_lines(script: str, words: list[dict],
                duration: float) -> dict:
    """Split the script into timed lines and classify them by position."""
    units = script_units(script)
    if not units or not words:
        return {"lines": [], "script_similarity": 0.0, "method": "none"}

    tokens_by_unit = [[_norm(t) for t in u.split()] for u in units]
    flat: list[str] = []
    spans: list[tuple[int, int]] = []
    for toks in tokens_by_unit:
        start = len(flat)
        flat.extend(toks)
        spans.append((start, len(flat)))

    mapping = _map_script_to_words(flat, words)
    matched = sum(1 for m in mapping if m is not None)
    similarity = round(matched / max(1, len(flat)), 3)

    lines: list[dict] = []
    for i, (unit, (a, b)) in enumerate(zip(units, spans)):
        idxs = [m for m in mapping[a:b] if m is not None]
        if idxs:
            t0 = float(words[idxs[0]]["s"])
            t1 = float(words[idxs[-1]]["e"])
        else:
            t0 = t1 = None
        lines.append({"id": i, "text": unit.strip(), "t0": t0, "t1": t1,
                      "words": b - a, "matched": round(len(idxs) / max(1, b - a), 2),
                      "w0": min(idxs) if idxs else None,
                      "w1": max(idxs) + 1 if idxs else None})

    # Fill unmatched units by interpolating between their timed neighbours.
    total = max(float(duration), 0.1)
    for i, line in enumerate(lines):
        if line["t0"] is not None:
            continue
        prev_end = next((ln["t1"] for ln in reversed(lines[:i])
                         if ln["t1"] is not None), 0.0)
        next_start = next((ln["t0"] for ln in lines[i + 1:]
                           if ln["t0"] is not None), total)
        n = max(1, sum(1 for ln in lines[i:] if ln["t0"] is None))
        share = max(0.2, (next_start - prev_end) / n)
        line["t0"] = round(prev_end, 3)
        line["t1"] = round(min(next_start, prev_end + share), 3)

    # Monotonic, non-degenerate, clipped to the voiceover.
    for i, line in enumerate(lines):
        line["t0"] = round(max(0.0, min(float(line["t0"]), total - 0.05)), 3)
        line["t1"] = round(max(line["t0"] + 0.05,
                               min(float(line["t1"]), total)), 3)
        if i and line["t0"] < lines[i - 1]["t1"] - 0.02:
            line["t0"] = lines[i - 1]["t1"]
    if lines:
        lines[-1]["t1"] = total

    for line in lines:
        frac = line["t0"] / total
        line["kind"] = ("hook" if frac < 0.12 else
                        "cta" if frac >= 0.86 else
                        "trust" if frac >= 0.62 else "proof")
        line["dur"] = round(line["t1"] - line["t0"], 3)

    return {
        "method": "script_punctuation_aligned_to_asr",
        "script_similarity": similarity,
        "units": len(lines),
        "lines": lines,
    }
