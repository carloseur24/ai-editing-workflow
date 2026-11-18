#!/usr/bin/env python3
"""motion — render a spec-driven motion-graphics plate.

A plate is one shot: background, SDF object (brain/sphere/torus/blob), optional
text, grain and audio. Output is H.264 MP4, or ProRes 4444 MOV with alpha when
`background` is null.

Usage:
  fenix motion <project>                 # input/<project>/motion.json
  fenix motion <project> --spec path.json
  fenix motion <project> --still 1.5 --out frame.png
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from ..context import (DEFAULT_CONFIG, INPUT_DIR, OUTPUT_DIR, load_config,
                       repo_path, resolve_sku_dir)
from ..render.motion import plate


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_spec(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[motion] missing spec: {path}\n"
                 f"create it or copy docs/examples/motion.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[motion] invalid {path}: {exc}")
    spec = _merge(plate.DEFAULTS, data)
    if data.get("background", "sentinel") is None:
        spec["background"] = None
    return spec


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Render a motion-graphics plate")
    parser.add_argument("project", help="project folder under input/")
    parser.add_argument("--spec", type=Path, default=None,
                        help="default: input/<project>/motion.json")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--still", type=float, default=None,
                        help="render one frame at this second (PNG)")
    parser.add_argument("--seconds", type=float, default=None,
                        help="render only the first N seconds")
    parser.add_argument("--deliver", action="store_true",
                        help="copy the finished plate to the Windows shelf + Downloads")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project = INPUT_DIR / args.project
    project.mkdir(parents=True, exist_ok=True)
    spec_path = args.spec or (project / "motion.json")
    spec = load_spec(spec_path)
    cfg = load_config(DEFAULT_CONFIG, project=project)
    zone_warnings = plate.resolve_text_zones(spec, cfg)
    for warning in zone_warnings:
        print(f"[motion] WARNING: {warning}")

    if args.still is not None:
        out = args.out or (OUTPUT_DIR / args.project / "motion" / "still.png")
        data = plate.render(spec, out, still_at=args.still)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run = OUTPUT_DIR / args.project / "motion" / f"{args.project}_{stamp}"
        run.mkdir(parents=True, exist_ok=True)
        ext = ".mov" if spec.get("background") in (None, "transparent") else ".mp4"
        out = args.out or (run / f"plate{ext}")
        data = plate.render(spec, out, seconds=args.seconds)
        (run / "motion.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        (run / "manifest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        if args.deliver:
            deliv = cfg.get("delivery") or {}
            shelf = Path(deliv.get("dir", "")).expanduser() / run.name
            shelf.mkdir(parents=True, exist_ok=True)
            for item in (out, run / "motion.json", run / "manifest.json"):
                if item.exists():
                    shutil.copy2(item, shelf / item.name)
            downloads = deliv.get("downloads")
            if downloads:
                dl = Path(downloads).expanduser()
                dl.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out, dl / f"{run.name}{ext}")
                print(f"[motion] downloads: {dl / (run.name + ext)}")
            print(f"[motion] shelf: {shelf}")

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[motion] {data.get('path')}")
        if not data.get("still"):
            print(f"[motion] {data.get('seconds')}s, {data.get('frames')} frames, "
                  f"{data.get('elapsed_sec')}s wall, alpha={data.get('alpha')}")


if __name__ == "__main__":
    main()
