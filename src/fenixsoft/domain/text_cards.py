#!/usr/bin/env python3
"""Timed title/spec cards: parsing, timing, safe-zone bands and defaults.

A card is a two-line text block (white title + mint spec) that fades and rises
in at ``start`` and out before ``end``. It is the native replacement for the
"render a 33 s motion plate, then composite it" path: the edit renderer draws
the card per frame, so a spec change costs a re-render, not a new plate + comp.

Spec (``sku.text_cards``), all fields optional except ``text``/``start``/``end``:

    {"text": "PRODUCT NAME", "sub": "128GB · SNAPDRAGON",
     "start": 0.7, "end": 5.1, "enter_sec": 0.55, "exit_sec": 0.30,
     "rise_px": 26, "y": 250, "size": 86, "sub_size": 32,
     "color": "#FFFFFF", "sub_color": "#6EE7B7"}

``zone`` (``header``/``lower``/``center``) replaces a pixel ``y`` with the same
band the captions use; an explicit ``y`` still wins. A shot can also carry a
``text_zone`` in ``brief.story.shots`` so a card for that shot sits in the lower
third without editing the card itself.

This module is pure (no PIL, no ffmpeg) so the timeline can be tested and
audited without rendering a frame.
"""
from __future__ import annotations

from . import safe_zones

DEFAULTS = {
    "y": 250,
    "size": 86,
    "sub_size": 34,
    "color": "#FFFFFF",
    "sub_color": "#6EE7B7",
    "enter_sec": 0.45,
    "exit_sec": 0.30,
    "rise_px": 24,
    "sub_gap_ratio": 0.30,
    "shadow": True,
}


def _num(value, default: float) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_y(item: dict) -> bool:
    value = item.get("y")
    return value is not None and not isinstance(value, bool)


def normalize(cards, defaults: dict | None = None,
              zone_resolver=None) -> list[dict]:
    """Resolve every card against the defaults and drop unusable entries.

    ``zone_resolver(zone, size) -> y | None`` maps ``zone`` to a top edge; the
    stage passes ``domain.safe_zones.zone_y`` bound to the project's config and
    safe-zone preset. Without a resolver a zone card keeps the default ``y`` and
    is marked so the renderer can resolve it per frame.
    """
    base = dict(DEFAULTS)
    base.update(defaults or {})
    out: list[dict] = []
    for item in cards or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        sub = str(item.get("sub") or "").strip()
        if not text and not sub:
            continue
        start = _num(item.get("start"), 0.0)
        end = _num(item.get("end"), start + 1.0)
        if end <= start:
            continue
        size = max(8, int(round(_num(item.get("size"), base["size"]))))
        sub_size = max(8, int(round(_num(item.get("sub_size"),
                                        base["sub_size"]))))
        if not text:
            # A sub-only card sits where the title would have been.
            size = sub_size
        zone = safe_zones.normalize_zone(item.get("zone"))
        explicit_y = _has_y(item)
        y = int(round(_num(item.get("y"), base["y"])))
        if not explicit_y and zone and zone_resolver is not None:
            resolved = zone_resolver(zone, size)
            if resolved is not None:
                y = int(resolved)
        out.append({
            "text": text,
            "sub": sub,
            "start": round(start, 3),
            "end": round(end, 3),
            "enter_sec": max(0.0, _num(item.get("enter_sec"),
                                       base["enter_sec"])),
            "exit_sec": max(0.0, _num(item.get("exit_sec"),
                                      base["exit_sec"])),
            "rise_px": max(0.0, _num(item.get("rise_px"), base["rise_px"])),
            "y": y,
            "zone": zone,
            "explicit_y": explicit_y,
            "size": size,
            "sub_size": sub_size,
            "color": str(item.get("color") or base["color"]),
            "sub_color": str(item.get("sub_color") or base["sub_color"]),
            "shadow": bool(item.get("shadow", base.get("shadow", True))),
            "sub_gap_ratio": _num(item.get("sub_gap_ratio"),
                                  base["sub_gap_ratio"]),
        })
    return out


def resolve_y(card: dict, zone: str | None = None, *,
              mode: str | None = None, cfg: dict | None = None) -> int:
    """Top edge for a card: explicit ``y`` > card zone > shot zone > default.

    The shot's ``text_zone`` only applies to a card that names neither ``y`` nor
    its own zone, so one card can stay pinned while its neighbours follow the
    scene band.
    """
    if card.get("explicit_y"):
        return int(card.get("y") or 0)
    for candidate in (card.get("zone"), zone):
        if not candidate:
            continue
        if cfg is None:
            break
        y = safe_zones.zone_y(candidate, cfg, mode=mode,
                              size=int(card.get("size") or 0))
        if y is not None:
            return int(y)
    return int(card.get("y") or 0)


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def alpha_at(card: dict, t: float) -> tuple[float, int]:
    """``(alpha 0..1, rise px)`` for one card at time ``t``.

    The enter ramp fades and rises; the exit ramp only fades so the card does
    not drop out of the block on its last frames.
    """
    start, end = float(card["start"]), float(card["end"])
    enter = max(1e-6, float(card.get("enter_sec") or 0.0))
    exit_ = max(1e-6, float(card.get("exit_sec") or 0.0))
    if t < start or t > end:
        return 0.0, 0
    p_in = ease_out_cubic((t - start) / enter) if enter > 0 else 1.0
    p_out = ease_out_cubic((end - t) / exit_) if exit_ > 0 else 1.0
    alpha = max(0.0, min(1.0, min(p_in, p_out)))
    rise = int(round((1.0 - p_in) * float(card.get("rise_px") or 0.0)))
    return alpha, rise


def active_at(cards: list[dict], t: float) -> list[tuple[dict, float, int]]:
    """Every card visible at ``t`` with its alpha and rise (normally one)."""
    return [(c, *alpha_at(c, t)) for c in cards
            if float(c["start"]) <= t <= float(c["end"])]


def sub_y(card: dict, y: int | None = None) -> int:
    """Top edge of the spec line, from the title size and the configured gap."""
    gap = float(card.get("size") or 0) * float(card.get("sub_gap_ratio") or 0.0)
    top = float(card.get("y") or 0) if y is None else float(y)
    return int(round(top + float(card.get("size") or 0) + gap))


def audit(cards: list[dict]) -> dict:
    return {
        "count": len(cards),
        "cards": [{"text": c["text"], "sub": c["sub"], "t0": c["start"],
                   "t1": c["end"], "y": c["y"], "zone": c.get("zone"),
                   "size": c["size"]}
                  for c in cards],
        "total_seconds": round(sum(c["end"] - c["start"] for c in cards), 3),
    }
