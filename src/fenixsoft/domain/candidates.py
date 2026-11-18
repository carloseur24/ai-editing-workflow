#!/usr/bin/env python3
"""
candidates.py — resolve a scored shot window ID to a clip + in-point.

`candidates.json` is written into `input/<sku>/` by `fenix frames`. An
`edit_plan.json` cut may carry `"candidate": "01c-a"` instead of timing numbers.
The renderer and the pre-render validator both resolve it through this module,
so an ID means exactly the same shot in both places.
"""
from __future__ import annotations

import json
from pathlib import Path


def load(sku_dir: Path) -> dict | None:
    path = Path(sku_dir) / "candidates.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def find(cand: dict | None, cid: str) -> dict | None:
    """`01c-a` -> {"clip", "t", "dur", "ratio", "score", ...} or None."""
    if not cand:
        return None
    for name, info in (cand.get("clips") or {}).items():
        for w in info.get("windows", []):
            if w.get("id") == cid:
                return dict(w, clip=name, clip_duration_sec=info.get("duration_sec"))
    return None


def ids(cand: dict | None) -> list[str]:
    if not cand:
        return []
    return [w["id"] for info in (cand.get("clips") or {}).values()
            for w in info.get("windows", [])]
