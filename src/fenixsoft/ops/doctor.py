#!/usr/bin/env python3
"""doctor — print the resolved paths and check the environment.

This is the first command an agent should run in an unfamiliar checkout: it
answers "where does this project keep things" and "is the box ready" without
touching a single input or spending a render.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ..context import (ASSETS_DIR, CACHE_DIR, DEFAULT_CONFIG, EVAL_DIR,
                       FONTS_DIR, INPUT_DIR, LOCAL_CONFIG, MODELS_DIR,
                       MUSIC_DIR, OUTPUT_DIR, REPO_ROOT, SFX_DIR,
                       display_path, known_skus, load_config, repo_path)


def _version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    line = (out.stdout or out.stderr).strip().splitlines()
    return line[0] if out.returncode == 0 and line else None


def _size(path: Path) -> str:
    if not path.exists():
        return "missing"
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return f"{total / 1024 / 1024:.1f} MB"


def _audio_files(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted(p.name for p in path.iterdir()
                  if p.suffix.lower() in {".wav", ".mp3", ".m4a"})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check paths and environment")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    failures: list[str] = []
    warnings: list[str] = []

    try:
        cfg = load_config()
    except SystemExit as exc:
        failures.append(str(exc))
        cfg = {}

    ffmpeg = _version(["ffmpeg", "-version"])
    ffprobe = _version(["ffprobe", "-version"])
    if not ffmpeg:
        failures.append("ffmpeg not found on PATH")
    if not ffprobe:
        failures.append("ffprobe not found on PATH")

    fonts = sorted(p.name for p in FONTS_DIR.glob("*.ttf")) if FONTS_DIR.exists() else []
    if not fonts:
        failures.append(f"no fonts in {FONTS_DIR} (run tools/setup_wsl.sh)")

    music = _audio_files(MUSIC_DIR)
    sfx = _audio_files(SFX_DIR)
    if not music:
        warnings.append(f"no music bed in {MUSIC_DIR} "
                        "(drop one there or set music.file)")
    if not sfx:
        warnings.append(f"no legacy SFX in {SFX_DIR} "
                        "(the cue-sheet library is separate)")

    motion = (sorted(p.name for p in (ASSETS_DIR / "motion").glob("*.png"))
              if (ASSETS_DIR / "motion").exists() else [])
    hf_runtime = (ASSETS_DIR / "hf" / "gsap.min.js").exists()

    sfx_cfg = cfg.get("sfx") or {}
    sfx_library = repo_path(sfx_cfg.get("library_dir") or SFX_DIR).expanduser()
    sfx_subdir = str(sfx_cfg.get("install_subdir") or "fenix_sfx")
    sfx_install = sfx_library / sfx_subdir
    sfx_library_ready = sfx_install.is_dir() and any(sfx_install.iterdir())
    if not sfx_library_ready:
        warnings.append(
            f"SFX library not installed at {display_path(sfx_install)}; "
            "run ./fenix sfx --install-library or set sfx.library_dir")

    delivery_cfg = cfg.get("delivery") or {}
    delivery_dir = (repo_path(delivery_cfg["dir"]).expanduser()
                    if delivery_cfg.get("dir") else None)
    delivery_ready = bool(delivery_dir and delivery_dir.exists())
    if delivery_dir and not delivery_ready:
        warnings.append(
            f"delivery dir missing: {display_path(delivery_dir)} "
            "(created on first deliver, or set delivery.dir)")

    models = "present" if MODELS_DIR.exists() and any(MODELS_DIR.iterdir()) else "absent"
    if models == "absent":
        warnings.append(f"Whisper model not cached in {MODELS_DIR}; first align downloads it")

    skus = known_skus()
    rows = []
    for name in skus:
        sku_dir = INPUT_DIR / name
        clips = len(list((sku_dir / "clips").glob("*.mp4")))
        has_voice = any(sku_dir.glob("voice.*"))
        has_script = False
        try:
            import json
            has_script = bool(json.loads((sku_dir / "sku.json").read_text(
                encoding="utf-8")).get("script_text"))
        except (OSError, ValueError):
            pass
        rows.append({
            "sku": name,
            "clips": clips,
            "voice": has_voice,
            "script": has_script,
            "latest_run": _latest_run(name),
        })

    data = {
        "repo": str(REPO_ROOT),
        "config": str(DEFAULT_CONFIG),
        "config_loaded": bool(cfg),
        "local_config": str(LOCAL_CONFIG) if LOCAL_CONFIG.exists() else None,
        "python": sys.version.split()[0],
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "fonts": fonts,
        "music": music,
        "sfx": sfx,
        "motion": motion,
        "hf_runtime": hf_runtime,
        "sfx_library": str(sfx_install),
        "sfx_library_ready": sfx_library_ready,
        "delivery_dir": str(delivery_dir) if delivery_dir else None,
        "delivery_ready": delivery_ready,
        "models": models,
        "input_dir": str(INPUT_DIR),
        "output_dir": str(OUTPUT_DIR),
        "output_size": _size(OUTPUT_DIR),
        "eval_dir": str(EVAL_DIR),
        "cache_dir": str(CACHE_DIR),
        "assets_dir": str(ASSETS_DIR),
        "skus": rows,
        "failures": failures,
        "warnings": warnings,
    }

    if args.json:
        import json
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"repo        {REPO_ROOT}")
        print(f"config      {DEFAULT_CONFIG} ({'ok' if cfg else 'MISSING'})")
        if LOCAL_CONFIG.exists():
            print(f"local       {LOCAL_CONFIG} (merged)")
        print(f"python      {data['python']}")
        print(f"ffmpeg      {ffmpeg or 'MISSING'}")
        print(f"ffprobe     {ffprobe or 'MISSING'}")
        print(f"assets      {ASSETS_DIR}")
        print(f"fonts       {len(fonts)} file(s) in {FONTS_DIR}")
        print(f"music       {', '.join(music) or 'none'}")
        print(f"sfx         {', '.join(sfx) or 'none (legacy; see sfx library)'}")
        print(f"motion      {len(motion)} texture(s) in {ASSETS_DIR / 'motion'}")
        hf_label = (f"gsap.min.js -> {ASSETS_DIR / 'hf'}"
                    if hf_runtime else "MISSING (assets/hf/gsap.min.js)")
        print(f"hf          {hf_label}")
        print(f"sfx library {sfx_install} "
              f"({'ok' if sfx_library_ready else 'MISSING -> ./fenix sfx --install-library'})")
        if delivery_dir:
            print(f"delivery    {display_path(delivery_dir)} "
                  f"({'ok' if delivery_ready else 'missing'})")
        print(f"models      {models} ({MODELS_DIR})")
        print(f"input       {len(skus)} SKU(s) in {INPUT_DIR}")
        for row in rows:
            flags = [f"{row['clips']} clips"]
            flags.append("voice" if row["voice"] else "NO VOICE")
            flags.append("script" if row["script"] else "NO SCRIPT")
            latest = row["latest_run"] or "no runs"
            print(f"  {row['sku']:<26} {', '.join(flags):<30} {latest}")
        print(f"output      {data['output_size']} in {OUTPUT_DIR}")
        print(f"eval        {EVAL_DIR}")
        print(f"cache       {CACHE_DIR}")
        for message in warnings:
            print(f"[warn] {message}")
        for message in failures:
            print(f"[FAIL] {message}")
        print(f"\nRESULT: {'PASS' if not failures else 'FAIL'} "
              f"({len(warnings)} warning(s))")

    if failures:
        sys.exit(1)


def _latest_run(sku: str) -> str | None:
    latest = Path(OUTPUT_DIR / sku / "latest.json")
    if not latest.exists():
        return None
    try:
        import json
        return json.loads(latest.read_text(encoding="utf-8")).get("run")
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    main()
