#!/usr/bin/env python3
"""Preview sample plan: which stills represent an edit or a comp.

A preview is the cheap review gate: one frame per cut (its midpoint) plus the
first fully visible frame of every text card, composited with the same overlay,
cards, captions and grade as the real render. The plan is pure so the sample
set can be tested without decoding a frame.

A card's "first visible frame" is ``start + enter_sec``: at ``start`` the card
is at alpha 0, so sampling the raw start would produce an empty still and hide
exactly the placement problem the preview exists to catch.
"""
from __future__ import annotations

DEFAULTS = {"max_stills": 24}


def _clamp(value: float, low: float, high: float) -> float:
    if high < low:
        return low
    return max(low, min(high, value))


def cut_at(cuts: list[dict], t: float) -> dict | None:
    """The cut whose ``[t0, t1)`` window contains ``t`` (last cut inclusive)."""
    if not cuts:
        return None
    for cut in cuts:
        if float(cut["t0"]) <= t < float(cut["t1"]):
            return cut
    return cuts[-1] if t >= float(cuts[-1]["t0"]) else cuts[0]


def source_time(cut: dict, t: float) -> float:
    """Source in-point for timeline time ``t`` inside ``cut``."""
    offset = t - float(cut["t0"])
    return max(0.0, float(cut["in"]) + offset)


def sample_plan(cuts: list[dict], cards: list[dict] | None = None, *,
                duration: float | None = None,
                max_stills: int = DEFAULTS["max_stills"],
                tolerance: float = 0.02) -> list[dict]:
    """Ordered still samples: one per cut midpoint, one per card first frame.

    ``max_stills`` caps the total; cut midpoints are kept first because they are
    the framing check, then cards fill the remaining budget in time order.
    """
    if not cuts:
        return []
    total = float(duration if duration is not None else cuts[-1]["t1"])
    samples: list[dict] = []
    for i, cut in enumerate(cuts):
        t = _clamp(float(cut["t0"]) + float(cut["dur"]) / 2.0, 0.0,
                   max(0.0, total - 0.05))
        samples.append({
            "kind": "cut",
            "cut": i,
            "clip": cut.get("clip"),
            "t": round(t, 3),
            "t_src": round(source_time(cut, t), 3),
            "label": f"cut {i} midpoint",
        })
    budget = max(0, int(max_stills) - len(samples))
    card_samples: list[dict] = []
    for i, card in enumerate(cards or []):
        if len(card_samples) >= budget:
            break
        enter = float(card.get("enter_sec") or 0.0)
        t = _clamp(float(card["start"]) + enter, 0.0,
                   max(0.0, min(total, float(card["end"])) - 0.001))
        cut = cut_at(cuts, t)
        if cut is None:
            continue
        card_samples.append({
            "kind": "card",
            "card": i,
            "cut": next(j for j, c in enumerate(cuts) if c is cut),
            "clip": cut.get("clip"),
            "t": round(t, 3),
            "t_src": round(source_time(cut, t), 3),
            "label": f"card {i} first frame: {card.get('text') or card.get('sub')}",
        })
    samples.extend(card_samples)
    # A card can become visible on the same frame as a cut midpoint; one still
    # already shows both, so the cut label wins and the card is dropped.
    kept: list[dict] = [s for s in samples if s["kind"] == "cut"]
    for sample in samples:
        if sample["kind"] == "cut":
            continue
        if any(abs(sample["t"] - other["t"]) <= tolerance for other in kept):
            continue
        kept.append(sample)
    kept.sort(key=lambda s: (s["t"], 0 if s["kind"] == "cut" else 1))
    return kept
