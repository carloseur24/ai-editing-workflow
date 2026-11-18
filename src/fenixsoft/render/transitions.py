#!/usr/bin/env python3
"""Optical transitions between edit cuts: plan, frame blend, FFmpeg helper.

The edit renderer used to hard-cut every boundary. This module is the pure
half of the fix: it says *where* a transition starts, *how long* it may run,
and what one blended picture frame looks like. It imports numpy only and never
touches files, ffmpeg or the rendered video.

Integration point in ``render/render_video.py`` (not wired yet)
---------------------------------------------------------------
The frame loop already knows the active cut (``cut_idx``, ``cut["t0"]``) and
reads one frame per shot around line 1697::

    frame = reader.read()
    if frame.shape[1] != W or frame.shape[0] != H:
        frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)

Wire it like this:

1. Build ``plan = transition_plan(cuts, default=cfg["transitions"]["default"],
   sec=cfg["transitions"]["sec"])`` once beside ``readers``.
2. Keep **two** ``ClipReader`` instances during a boundary: the outgoing cut
   and the incoming cut. The outgoing plays its last ``sec``; the incoming is
   seeked ``sec`` of handle *before* its in-point and reaches ``in`` exactly at
   the cut, so the planned first frame still lands on the cut.
3. Blend the picture **before** punch-in, the static overlay, text cards and
   captions: ``frame = blend_frames(a, b, progress, kind)``. Punch-in applies
   to the blended result so both sides move together.
4. Text cards: their windows already end ``end_offset_sec`` (0.4 s) before the
   next cut and start 0.6 s after the cut, so they are clear of a 0.35 s
   transition. Do not blend a drawn card into the picture; fade it with
   ``text_cards.alpha_at`` as today.
5. Captions are baked by HyperFrames (``hf_overlay``) in transport mode; they
   are composited by FFmpeg after this frame stream and cannot be blended here.
   Do not try: a blended caption would ghost two different words.

For a future FFmpeg post path (e.g. ``comp``/``longform`` assembly), use
``xfade_filter`` to emit ``xfade`` filter steps for the same plan.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# Transition kinds the renderer can draw from two decoded frames.
TRANSITIONS = frozenset({
    "dissolve",
    "fadeblack",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
})

# Values accepted in a cut's ``transition`` field to force a hard cut.
CUT_WORDS = frozenset({"", "cut", "none", "hard"})

# A transition may use at most this share of the shorter neighbouring shot, so
# both sides keep visible frames outside the blend.
MAX_SHARE = 0.5

# FFmpeg's xfade uses ``fade`` for a cross-dissolve; wipes keep their names.
XFADE_NAMES = {
    "dissolve": "fade",
    "fadeblack": "fadeblack",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "wipeup": "wipeup",
    "wipedown": "wipedown",
}


def _number(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _number(value)))


def _shot_start(cut: dict) -> float:
    return _number(cut.get("t0"))


def _shot_dur(cut: dict, start: float) -> float:
    dur = _number(cut.get("dur"))
    if dur > 0:
        return dur
    return max(0.0, _number(cut.get("t1"), start) - start)


def transition_plan(cuts: list[dict], default: str | None = None,
                    sec: float = 0.35) -> list[dict]:
    """One entry per visible cut boundary, clamped to the shorter neighbour.

    ``cuts`` is the renderer's cut list (``clip``/``in``/``dur``/``t0``). The
    first cut starts at 0 s with nothing before it, so that boundary is skipped
    and an N-shot piece yields at most N-1 entries::

        [{"at": 5.5, "kind": "dissolve", "sec": 0.35,
          "from_index": 0, "to_index": 1}]

    A cut may override the global default with ``transition`` (a kind, or
    ``"cut"``/``"none"`` for a hard cut) and shorten its own blend with
    ``transition_sec``. ``default=None`` means a cross-dissolve; pass
    ``"cut"`` to plan none. An unknown kind raises ``ValueError`` instead of
    silently degrading to a hard cut.

    ``sec`` is clamped to ``MAX_SHARE`` (half) of the shorter neighbouring
    shot. Without that cap a 0.3 s shot would be entirely transition and its
    content would never be seen on its own.
    """
    if not cuts:
        return []
    fallback = "dissolve" if default is None else str(default).strip().lower()
    if fallback not in TRANSITIONS and fallback not in CUT_WORDS:
        raise ValueError(
            f"unknown default transition {default!r}; "
            f"expected one of {sorted(TRANSITIONS)} or 'cut'")

    out: list[dict] = []
    for i in range(1, len(cuts)):
        prev, cur = cuts[i - 1], cuts[i]
        raw = cur.get("transition", fallback)
        kind = str(raw if raw is not None else fallback).strip().lower()
        if kind in CUT_WORDS:
            continue
        if kind not in TRANSITIONS:
            raise ValueError(
                f"unknown transition {kind!r} before cut {i}; "
                f"expected one of {sorted(TRANSITIONS)} or 'cut'")

        at = _shot_start(cur)
        prev_start = _shot_start(prev)
        shorter = min(_shot_dur(prev, prev_start), _shot_dur(cur, at))
        if shorter <= 0:
            continue
        wanted = _number(cur.get("transition_sec"), sec)
        length = min(max(0.0, wanted), shorter * MAX_SHARE)
        if length <= 0:
            continue
        out.append({
            "at": round(at, 3),
            "kind": kind,
            "sec": round(length, 3),
            "from_index": i - 1,
            "to_index": i,
        })
    return out


def blend_frames(a: np.ndarray, b: np.ndarray, progress: float,
                 kind: str = "dissolve") -> np.ndarray:
    """Blend two BGR frames for one transition frame.

    ``progress`` 0 = fully ``a``, 1 = fully ``b``. Shapes must match exactly
    (the caller resizes before blending); a mismatch raises ``ValueError``
    because a silent resize would shift one side of the wipe.

    - ``dissolve``: linear cross-fade.
    - ``fadeblack``: ``a`` fades to black over the first half, ``b`` fades up
      over the second; progress 0.5 is black.
    - ``wipeleft``/``wiperight``: the incoming frame enters from the right/left
      and the hard edge travels left/right.
    - ``wipeup``/``wipedown``: the incoming frame enters from the bottom/top
      and the edge travels up/down.

    The result is always a new ``uint8`` array; the inputs are not mutated.
    """
    kind = str(kind or "").strip().lower()
    if kind not in TRANSITIONS:
        raise ValueError(
            f"unknown transition {kind!r}; expected one of {sorted(TRANSITIONS)}")
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape:
        raise ValueError(
            f"transition frames differ in shape: {a.shape} vs {b.shape}")
    if a.ndim < 2:
        raise ValueError(f"transition frames need at least 2 dims, got {a.shape}")

    p = _clamp01(progress)

    if kind == "dissolve":
        out = a.astype(np.float32) * (1.0 - p) + b.astype(np.float32) * p
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)

    if kind == "fadeblack":
        k = 1.0 - 2.0 * p if p <= 0.5 else 2.0 * p - 1.0
        src = a if p <= 0.5 else b
        out = src.astype(np.float32) * max(0.0, k)
        return np.clip(out + 0.5, 0, 255).astype(np.uint8)

    out = a.copy()
    h, w = a.shape[:2]
    if kind in ("wipeleft", "wiperight"):
        edge = int(round(p * w))
        if kind == "wipeleft":       # incoming from the right, edge moves left
            if edge:
                out[:, w - edge:] = b[:, w - edge:]
        else:                        # incoming from the left, edge moves right
            if edge:
                out[:, :edge] = b[:, :edge]
    else:
        edge = int(round(p * h))
        if kind == "wipeup":         # incoming from the bottom, edge moves up
            if edge:
                out[h - edge:, :] = b[h - edge:, :]
        else:                        # incoming from the top, edge moves down
            if edge:
                out[:edge, :] = b[:edge, :]
    return out


def xfade_filter(kind: str, sec: float, offset: float = 0.0,
                 *, in_label: str = "", out_label: str = "") -> str:
    """FFmpeg ``xfade`` step for a future filter-graph post path.

    ``offset`` is the transition start on the *joined* timeline (the same
    ``at`` ``transition_plan`` reports). Returns a bare filter, or a labelled
    step when both labels are given::

        [0:v][1:v]xfade=transition=fade:duration=0.350:offset=5.500[v1]
    """
    kind = str(kind or "").strip().lower()
    if kind not in TRANSITIONS:
        raise ValueError(
            f"unknown transition {kind!r}; expected one of {sorted(TRANSITIONS)}")
    length = max(0.0, _number(sec))
    at = max(0.0, _number(offset))
    step = (f"xfade=transition={XFADE_NAMES[kind]}:"
            f"duration={length:.3f}:offset={at:.3f}")
    if in_label and out_label:
        return f"{in_label}{out_label}{step}{out_label}"
    return step
