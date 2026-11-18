#!/usr/bin/env python3
"""tts — generate a voiceover from text (ElevenLabs or OpenAI).

Spec (`input/<project>/tts.json`):

    {
      "provider": "auto",              // elevenlabs | openai | auto
      "text": "…",
      "voice_id": "…",                 // ElevenLabs voice id
      "openai_voice": "alloy",
      "model_id": "eleven_multilingual_v2",
      "stability": 0.45,
      "similarity": 0.75,
      "speed": 1.08,
      "out": "voice.mp3"
    }

Keys come from the environment: `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`.
They are never printed or written to disk.

Usage:
  fenix tts <project> [--spec PATH] [--out PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..context import resolve_sku_dir

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
OPENAI_URL = "https://api.openai.com/v1/audio/speech"


def _load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[tts] missing spec: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[tts] invalid {path}: {exc}")


def choose_provider(spec: dict) -> str:
    requested = (spec.get("provider") or "auto").lower()
    if requested != "auto":
        return requested
    if os.environ.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    sys.exit("[tts] no ELEVENLABS_API_KEY or OPENAI_API_KEY in the environment")


def build_request(spec: dict, provider: str) -> tuple[str, dict, bytes]:
    text = (spec.get("text") or "").strip()
    if not text:
        sys.exit("[tts] spec has no text")
    if provider == "elevenlabs":
        key = os.environ.get("ELEVENLABS_API_KEY")
        if not key:
            sys.exit("[tts] ELEVENLABS_API_KEY is not set")
        voice = str(spec.get("voice_id") or "21m00Tcm4TlvDq8ikWAM")
        url = ELEVEN_URL.format(voice=voice) + "?output_format=mp3_44100_128"
        payload = {
            "text": text,
            "model_id": spec.get("model_id") or "eleven_multilingual_v2",
            "voice_settings": {
                "stability": float(spec.get("stability", 0.45)),
                "similarity_boost": float(spec.get("similarity", 0.75)),
                "speed": float(spec.get("speed", 1.08)),
            },
        }
        headers = {"xi-api-key": key, "Content-Type": "application/json",
                   "Accept": "audio/mpeg"}
        return url, headers, json.dumps(payload).encode()
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            sys.exit("[tts] OPENAI_API_KEY is not set")
        payload = {
            "model": spec.get("openai_model") or "gpt-4o-mini-tts",
            "voice": spec.get("openai_voice") or "alloy",
            "input": text,
            "speed": float(spec.get("speed", 1.0)),
            "response_format": "mp3",
        }
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json"}
        return OPENAI_URL, headers, json.dumps(payload).encode()
    sys.exit(f"[tts] unknown provider '{provider}'")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a voiceover from text")
    parser.add_argument("project")
    parser.add_argument("--spec", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project_dir = resolve_sku_dir(args.project)
    spec = _load(args.spec or (project_dir / "tts.json"))
    provider = choose_provider(spec)
    url, headers, body = build_request(spec, provider)
    out = args.out or (project_dir / (spec.get("out") or "voice.mp3"))
    started = time.time()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        sys.exit(f"[tts] HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(f"[tts] network error: {exc.reason}")
    if code != 200 or len(data) < 1000:
        sys.exit(f"[tts] request returned {code}, {len(data)} bytes")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    result = {"provider": provider, "path": str(out),
              "bytes": len(data), "elapsed_sec": round(time.time() - started, 1)}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[tts] {provider} -> {out} ({len(data) / 1024:.0f} KB, "
              f"{result['elapsed_sec']}s)")


if __name__ == "__main__":
    main()
