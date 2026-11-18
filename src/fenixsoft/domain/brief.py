#!/usr/bin/env python3
"""``brief.json`` schema tolerance: beats and shot-table entries.

Two shapes used to crash the storyboard instead of naming the problem:

* ``story.beats`` written as plain strings (``"hook: the box in hand"``) — the
  stage called ``beat.get("weight")`` on a ``str`` and died with
  ``'str' object has no attribute 'get'``;
* a shot with ``dur`` missing, non-numeric or ``<= 0`` — it passed the schema
  row and failed later in the planner or the renderer.

This module normalises the tolerated shapes and returns human-readable schema
errors for the rest. It is pure: the stage prints the errors and exits.
"""
from __future__ import annotations

import re

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    text = _SLUG.sub("_", str(value).strip().lower()).strip("_")
    return text or "beat"


def normalize_beats(raw) -> tuple[list[dict], list[str]]:
    """``story.beats`` -> ``[{purpose, weight, ...}]`` plus schema errors.

    Accepted entries:

    * an object with an optional numeric ``weight`` (default 1.0) and an
      optional ``purpose``; every other field (``text``, ``visual``, ``audio``,
      ``motion``) is kept for the storyboard table;
    * a plain string: ``"hook: the box in hand"`` becomes
      ``{purpose: "hook", text: "the box in hand"}``; a string without a colon
      keeps its text and gets ``beat_<n>`` as the purpose.

    ``[]``/``None`` mean "no beats given" and use the framework default.
    """
    if raw in (None, [], {}):
        return [], []
    if not isinstance(raw, list):
        return [], [
            "story.beats must be a list of objects or strings, got "
            f"{type(raw).__name__}"
        ]
    beats: list[dict] = []
    errors: list[str] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            text = item.strip()
            if not text:
                errors.append(f"story.beats[{i}] is an empty string")
                continue
            head, sep, tail = text.partition(":")
            if sep and head.strip():
                beats.append({"purpose": _slug(head), "weight": 1.0,
                              "text": tail.strip() or text})
            else:
                beats.append({"purpose": f"beat_{i}", "weight": 1.0,
                              "text": text})
            continue
        if not isinstance(item, dict):
            errors.append(
                f"story.beats[{i}] must be an object or a string, got "
                f"{type(item).__name__}")
            continue
        weight = item.get("weight", 1.0)
        if isinstance(weight, bool):
            errors.append(f"story.beats[{i}].weight must be a number > 0")
            continue
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            errors.append(
                f"story.beats[{i}].weight must be a number > 0, got "
                f"{item.get('weight')!r}")
            continue
        if weight <= 0:
            errors.append(f"story.beats[{i}].weight must be > 0, got {weight}")
            continue
        purpose = str(item.get("purpose") or f"beat_{i}").strip()
        beats.append({**item, "purpose": purpose, "weight": weight})
    if not beats and not errors:
        errors.append("story.beats has no usable entry")
    return beats, errors


def schema_message(errors: list[str], where: str = "brief.story.beats") -> str:
    """One multi-line message naming the file, the fields and an example."""
    lines = [f"[storyboard] {where} schema error:"]
    lines += [f"  - {e}" for e in errors[:8]]
    if len(errors) > 8:
        lines.append(f"  ... {len(errors) - 8} more")
    lines.append('example: "beats": [{"purpose": "hook", "weight": 0.2, '
                 '"text": "the box in hand"}, "proof: 512GB"]')
    return "\n".join(lines)
