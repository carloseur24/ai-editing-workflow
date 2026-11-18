#!/usr/bin/env python3
"""Storyboard shots -> native timed title/spec cards.

Hand-authored ``sku.text_cards`` drift: a card says "UNBOXING" but its window
belongs to the box shot, or a shot loses its label in a re-cut. The ordered
shot table already carries the intent (``label``/``spec``), so the cards can be
derived from it instead of typed a second time.

Input is the normalized shot table from ``domain.shots`` (or the timeline
variant with ``t0``)::

    {"clip": "03_unboxing.mp4", "in": 0.5, "dur": 4.2, "t0": 9.8,
     "label": "unboxing", "spec": "PRODUCT NAME",
     "text_zone": "lower"}

Output is the existing ``text_cards`` schema (``domain/text_cards.py``), ready
for ``text_cards.normalize`` in the renderer.

Integration point (not wired yet)
---------------------------------
``stages/storyboard.py`` builds the shot table; after it validates the order it
should write ``output/<p>/text_cards.json`` from
``text_cards_from_shots(timeline, duration_sec, cfg)``. ``render_video.py``
should then load that file only when ``sku.text_cards`` is absent, so a
hand-authored card list still wins. Do not write it when ``story.shots`` is
missing (there is no order to attach cards to) and do not append it to
``sku.json``.

This module is pure: no PIL, no ffmpeg, no filesystem.
"""
from __future__ import annotations

import re

from . import text_cards

DEFAULTS = {
    # A card appears after the cut has landed and clears before the next one,
    # so a 0.35 s transition never shows two cards at once.
    "start_offset_sec": 0.6,
    "end_offset_sec": 0.4,
    # Below this the offsets are unusable; centre a whole-shot card instead.
    "min_window_sec": 0.45,
    "edge_pad_sec": 0.08,
    "max_title_chars": 48,
}

_LEADING_INDEX = re.compile(r"^\d+[\s_\-.]*")


def _num(value, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean(value) -> str:
    return str(value or "").strip()


def _shorten(text: str, limit: int) -> str:
    """Cut on a word boundary so a card never runs past the safe zone."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    return (cut or text[:limit].rstrip()) + "…"


def _title_from_label(label: str, limit: int) -> str:
    """The big line for a shot with no spec.

    A storyboard label often reads ``hook: box in hand``. The clause after the
    colon is the human-facing title; the beat name (``hook``) is planning
    vocabulary and would put "HOOK" on screen.
    """
    text = label.strip()
    if ":" in text:
        tail = text.split(":", 1)[1].strip()
        text = tail or text
    return _shorten(text, limit)


def _title_from_clip(clip: str, limit: int) -> str:
    """Last-resort title: ``03_unboxing_closeup.mp4`` -> ``Unboxing Closeup``."""
    stem = _clean(clip)
    if not stem:
        return ""
    stem = stem.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    stem = _LEADING_INDEX.sub("", stem)
    words = [w for w in re.split(r"[_\-\s]+", stem) if w]
    if not words:
        return ""
    return _shorten(" ".join(words).title(), limit)


def _card_text(shot: dict, limit: int) -> tuple[str, str]:
    """``(big_line, sub_line)`` for one shot, never both empty when avoidable.

    Preference: ``spec`` is the big line (product/spec is what the viewer reads
    first), ``label`` is the sub. A label-only shot promotes the label clause
    to the big line instead of printing the same words twice; a clip name is
    the documented last resort.
    """
    spec = _clean(shot.get("spec"))
    label = _clean(shot.get("label")) or _clean(shot.get("text"))
    if spec:
        return _shorten(spec, limit), label
    if label:
        return _title_from_label(label, limit), ""
    return _title_from_clip(_clean(shot.get("clip")), limit), ""


def _window(shot: dict, t0: float, dur: float, limit: float,
            timing: dict) -> tuple[float, float] | None:
    """Clamp a card window inside its shot and inside the piece."""
    start_off = max(0.0, _num(timing.get("start_offset_sec"),
                              DEFAULTS["start_offset_sec"]))
    end_off = max(0.0, _num(timing.get("end_offset_sec"),
                            DEFAULTS["end_offset_sec"]))
    min_window = max(0.0, _num(timing.get("min_window_sec"),
                               DEFAULTS["min_window_sec"]))
    pad = max(0.0, _num(timing.get("edge_pad_sec"), DEFAULTS["edge_pad_sec"]))

    t1 = min(t0 + dur, limit) if limit > 0 else t0 + dur
    start = max(0.0, t0 + start_off)
    end = t1 - end_off
    if end - start < min_window:
        # Short shot: drop the offsets, keep a small symmetric pad so the card
        # still enters and exits inside the shot instead of being cut off.
        edge = min(pad, max(0.01, dur * 0.1))
        start = max(0.0, t0 + edge)
        end = t1 - edge
    if end <= start:
        return None
    return round(start, 3), round(end, 3)


def text_cards_from_shots(shots: list[dict], duration_sec: float | None,
                          cfg: dict | None, *,
                          defaults: dict | None = None) -> list[dict]:
    """One native text card per shot, timed to that shot's visible window.

    ``shots`` may already carry ``t0`` (``domain.shots.timeline``); otherwise
    cumulative time is used. ``duration_sec`` clamps the last card to the piece
    so a rounded cut total cannot put a card past the master's end.

    Styling comes from ``cfg["text_cards"]`` (the renderer's own defaults),
    timing from ``cfg["labels"]``, and ``defaults`` overrides both for tests or
    a one-off job. ``text_zone``/``zone`` on a shot becomes the card's ``zone``;
    the renderer resolves it against the safe-zone preset, so no pixel ``y`` is
    copied here. Empty cards are never emitted.
    """
    cfg = cfg or {}
    style = dict(cfg.get("text_cards") or {})
    style.update(defaults or {})
    timing = dict(DEFAULTS)
    timing.update(cfg.get("labels") or {})
    if defaults:
        timing.update({k: v for k, v in defaults.items()
                       if k in DEFAULTS})

    # ``0`` means "no piece-level clamp"; the shot window still applies.
    limit = _num(duration_sec)

    max_chars = int(_num(style.get("max_title_chars"),
                         DEFAULTS["max_title_chars"]))

    raw: list[dict] = []
    cursor = 0.0
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        dur = _num(shot.get("dur"))
        if dur <= 0:
            continue
        t0 = shot.get("t0")
        start = _num(t0, cursor) if t0 is not None else cursor
        cursor = start + dur
        window = _window(shot, start, dur, limit, timing)
        if window is None:
            continue
        big, sub = _card_text(shot, max_chars)
        if not big and not sub:
            continue
        zone = _clean(shot.get("text_zone")) or _clean(shot.get("zone"))
        card = {
            "text": big,
            "sub": sub,
            "start": window[0],
            "end": window[1],
            "enter_sec": _num(style.get("enter_sec"),
                              text_cards.DEFAULTS["enter_sec"]),
            "exit_sec": _num(style.get("exit_sec"),
                             text_cards.DEFAULTS["exit_sec"]),
            "rise_px": _num(style.get("rise_px"),
                            text_cards.DEFAULTS["rise_px"]),
            "size": _num(style.get("size"), text_cards.DEFAULTS["size"]),
            "sub_size": _num(style.get("sub_size"),
                             text_cards.DEFAULTS["sub_size"]),
            "color": style.get("color") or text_cards.DEFAULTS["color"],
            "sub_color": style.get("sub_color")
            or text_cards.DEFAULTS["sub_color"],
        }
        if zone:
            card["zone"] = zone
        raw.append(card)

    # Normalization is the same pass the renderer runs, so this output is the
    # contract: rounded times, integer sizes, zone kept for per-frame resolve.
    cards = text_cards.normalize(raw, defaults=style)
    for card in cards:
        # ``y``/``explicit_y`` are derived renderer internals. A card that did
        # not name an explicit y must stay zone-resolvable after the JSON
        # round-trip, otherwise the shot's text_zone is silently ignored.
        if not card.get("explicit_y"):
            card.pop("y", None)
        card.pop("explicit_y", None)
    return cards
