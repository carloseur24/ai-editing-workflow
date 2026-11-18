#!/usr/bin/env python3
"""Shared FFmpeg encoding arguments for post stages.

The edit renderer, ``comp``, ``grade`` and ``longform`` must ship the same
H.264/AAC contract and the same WhatsApp byte budget. Before this module each
post stage hardcoded ``libx264 -preset medium -crf 18``: a 10.7 MB base came
out of a comp as 22.4 MB and then failed the delivery budget.

This module is pure: it builds argument lists and compares sizes. The stage
still owns the ffmpeg process.
"""
from __future__ import annotations

DEFAULTS = {
    "codec": "libx264",
    "preset": "medium",
    "profile": "main",
    "level": "4.2",
    "pix_fmt": "yuv420p",
    "crf": 18,
    "maxrate": "3800k",
    "bufsize": "7600k",
    "fallback_crf": 21,
    "fallback_maxrate": "2500k",
    "audio_codec": "aac",
    "audio_bitrate": "128k",
    "audio_sample_rate": 48000,
    "max_whatsapp_bytes": 16252928,
    "faststart": True,
}


def settings(cfg_or_enc: dict | None) -> dict:
    """Encoding block with defaults, accepting either the block or the config."""
    data = cfg_or_enc or {}
    if isinstance(data.get("encoding"), dict):
        data = data["encoding"]
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def video_args(cfg_or_enc: dict | None, *, draft: bool = False) -> list[str]:
    """Codec/preset/profile/level/pix_fmt/crf/maxrate/bufsize for an encode."""
    enc = settings(cfg_or_enc)
    args = [
        "-c:v", str(enc["codec"]),
        "-preset", "veryfast" if draft else str(enc["preset"]),
        "-profile:v", str(enc["profile"]),
        "-level", str(enc["level"]),
        "-pix_fmt", str(enc["pix_fmt"]),
        "-crf", "23" if draft else str(enc["crf"]),
        "-maxrate", str(enc["maxrate"]),
        "-bufsize", str(enc["bufsize"]),
    ]
    if enc.get("faststart", True):
        args += ["-movflags", "+faststart"]
    return args


def fallback_args(cfg_or_enc: dict | None) -> list[str]:
    """Second-pass encode for a file over the delivery byte budget."""
    enc = settings(cfg_or_enc)
    return [
        "-c:v", str(enc["codec"]),
        "-profile:v", str(enc["profile"]),
        "-level", str(enc["level"]),
        "-pix_fmt", str(enc["pix_fmt"]),
        "-crf", str(enc["fallback_crf"]),
        "-maxrate", str(enc["fallback_maxrate"]),
        "-bufsize", str(enc["bufsize"]),
        "-c:a", "copy",
        "-movflags", "+faststart",
    ]


def audio_args(cfg_or_enc: dict | None, *, copy_audio: bool = True) -> list[str]:
    """Copy the base audio when untouched; only re-encode when a filter ran."""
    enc = settings(cfg_or_enc)
    if copy_audio:
        return ["-c:a", "copy"]
    return ["-c:a", str(enc["audio_codec"]),
            "-b:a", str(enc["audio_bitrate"]),
            "-ar", str(enc["audio_sample_rate"])]


def budget_bytes(cfg_or_enc: dict | None) -> int:
    return int(settings(cfg_or_enc)["max_whatsapp_bytes"])


def over_budget(path, cfg_or_enc: dict | None) -> bool:
    try:
        return path.stat().st_size > budget_bytes(cfg_or_enc)
    except OSError:
        return False
