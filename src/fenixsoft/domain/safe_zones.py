#!/usr/bin/env python3
"""Safe-zone text placement for every text surface.

A title in ``sku.text_cards``, a line in ``motion.json`` and a caption all have
to land inside the same vertical bands. Before this module the only way to put
a card in the lower third was to copy the caption's ``y`` from
``config/pipeline.json`` into each spec — a number that moves when the safe-zone
preset changes and that drifts between the edit renderer and the motion plate.

A spec can now name a band instead::

    {"text": "DYNAMIC AMOLED 2X", "zone": "lower"}

``header`` maps to ``safe_zones.<mode>.title_y``, ``lower`` to ``caption_y`` and
``center`` to the vertical middle of the canvas. An explicit ``y`` (or ``x``)
always wins, so existing specs keep their exact placement.

This module is pure: no filesystem, no fonts, no ffmpeg.
"""
from __future__ import annotations

ZONES = ("header", "lower", "center")

# Zone -> key in ``config.safe_zones.<mode>``. ``center`` is computed from the
# canvas instead of a config key.
ZONE_KEYS = {"header": "title_y", "lower": "caption_y"}


def normalize_zone(value) -> str | None:
    """Canonical zone name, or ``None`` for an absent/unknown value."""
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return None
    zone = str(value).strip().lower()
    return zone if zone in ZONES else None


def zone_y(zone, cfg: dict, *, mode: str | None = None,
           size: int = 0) -> int | None:
    """Top edge in pixels for ``zone``, or ``None`` when it cannot resolve.

    ``size`` is the text block's font size; it only matters for ``center``,
    where the block is centred on the canvas rather than top-aligned.
    """
    name = normalize_zone(zone)
    if name is None:
        return None
    if name == "center":
        height = int((cfg.get("canvas") or {}).get("height") or 1920)
        return int(round((height - max(0, int(size))) / 2.0))
    zones = cfg.get("safe_zones") or {}
    preset = zones.get(mode) if mode else None
    if not isinstance(preset, dict):
        preset = zones.get("reels_universal") or {}
    key = ZONE_KEYS[name]
    if key not in preset:
        return None
    try:
        return int(round(float(preset[key])))
    except (TypeError, ValueError):
        return None


def describe(cfg: dict, *, mode: str | None = None) -> dict:
    """Every band as ``{zone: y}`` for ``fenix job``/manifests and docs."""
    out = {}
    for name in ZONES:
        y = zone_y(name, cfg, mode=mode)
        if y is not None:
            out[name] = y
    return out
