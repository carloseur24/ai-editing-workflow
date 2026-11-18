"""Config slices — the keys that make a render's picture and its mix.

A render depends on a *slice* of `config/pipeline.json`, not the whole file.
Changing `delivery.dir` or a docs-only key must not re-encode the picture;
changing `audio.limiter_ceiling` must not re-run the frame loop. The digests are
written into `edit_plan.json` so `fenix make` can tell the two apart.
"""
from __future__ import annotations

import hashlib
import json

PICTURE_KEYS = [
    "canvas", "target_duration_sec", "cuts_per_video", "min_cut_sec",
    "cut_snap_to_beat", "bpm", "punch", "grade", "kinetic_captions",
    "typography", "colors", "safe_zones", "hyperframes", "encoding",
]

AUDIO_KEYS = [
    "audio", "music", "sfx", "voiceover", "encoding",
]


def digest(cfg: dict, keys: list[str]) -> str:
    payload = {key: cfg.get(key) for key in keys}
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def render_slices(cfg: dict) -> dict[str, str]:
    return {"picture": digest(cfg, PICTURE_KEYS),
            "audio": digest(cfg, AUDIO_KEYS)}


def picture_changed(cfg: dict, stored: dict | None) -> bool | None:
    """``None`` when the run predates the slice fingerprint."""
    if not isinstance(stored, dict) or "picture" not in stored:
        return None
    return stored["picture"] != digest(cfg, PICTURE_KEYS)


def audio_changed(cfg: dict, stored: dict | None) -> bool | None:
    if not isinstance(stored, dict) or "audio" not in stored:
        return None
    return stored["audio"] != digest(cfg, AUDIO_KEYS)
