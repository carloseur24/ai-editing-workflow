#!/usr/bin/env python3
"""Voiceover presence and the duration a piece runs for.

A music-only piece (``sku.voiceover = null`` or ``"none"``) has no
``word_timings.json``: its length comes from an explicit duration and its cut
order from ``brief.story.shots``. Keeping this decision in one pure function
means ``align``, ``plan``, ``render``, ``qa``, ``job`` and ``sfx`` agree about
what "no voiceover" means instead of each testing for a missing file.
"""
from __future__ import annotations

NO_VOICEOVER = {"", "none", "no", "null", "false", "music", "music-only",
                "music_only", "instrumental"}


def is_music_only(sku: dict) -> bool:
    """True when the SKU explicitly declares no voiceover.

    A *missing* ``voiceover`` key keeps the legacy default (``voice.mp3``), so
    old projects keep their behaviour; only an explicit empty/None/"none" value
    turns the voiceover off.
    """
    if not isinstance(sku, dict) or "voiceover" not in sku:
        return False
    value = sku.get("voiceover")
    if value is None:
        return True
    return str(value).strip().lower() in NO_VOICEOVER


def voice_name(sku: dict) -> str:
    """The voiceover file name to look for (legacy default ``voice.mp3``)."""
    return str((sku or {}).get("voiceover") or "voice.mp3")


def explicit_duration(sku: dict, brief: dict | None, cfg: dict,
                      fallback: float | None = None) -> float:
    """Duration from ``sku.duration_sec`` -> ``brief.duration_sec`` -> config.

    Music-only projects must not depend on a voiceover to know how long they
    are, so the SKU wins over the brief and the config is only the last resort.
    """
    for source in (sku or {}, brief or {}):
        try:
            value = float(source.get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    if fallback is not None and fallback > 0:
        return float(fallback)
    return float((cfg or {}).get("target_duration_sec", 28.0))
