#!/usr/bin/env python3
"""
batch_run — render every SKU folder in input/.

Runs align -> render -> qa per folder, isolates failures so one bad SKU does not
stop the batch, and prints a summary table. Each render creates its own
timestamped run directory, so a batch never overwrites an earlier output.

Usage:
  fenix batch                        # all SKUs
  fenix batch --only iphone14_blue
  fenix batch --skip-existing        # only SKUs without a completed run
  fenix batch --remix                # rebuild audio for already-rendered SKUs
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from ..context import (INPUT_DIR, OUTPUT_DIR, REPO_ROOT, python_cmd,
                       subprocess_env)
from ..domain import runs


def run_step(label: str, cmd: list[str]) -> tuple[bool, str]:
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=subprocess_env(),
                          capture_output=True, text=True)
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-4:]
        return False, f"{label} failed after {elapsed:.0f}s: " + " | ".join(tail)
    return True, f"{label} ok ({elapsed:.0f}s)"


def missing_inputs(sku_dir: Path) -> list[str]:
    """A folder is a template (or incomplete) rather than a renderable SKU."""
    try:
        sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["sku.json unreadable"]
    missing = []
    if not (sku_dir / sku.get("voiceover", "voice.mp3")).exists():
        missing.append("voiceover")
    if not list((sku_dir / "clips").glob("*.mp4")):
        missing.append("clips")
    return missing


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", default=None,
                        help="render only these SKU folder names")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--remix", action="store_true",
                        help="rebuild audio only for existing renders")
    parser.add_argument("--no-qa", action="store_true")
    args = parser.parse_args(argv)

    sku_dirs = sorted(d for d in INPUT_DIR.iterdir()
                      if d.is_dir() and (d / "sku.json").exists())
    if args.only:
        wanted = set(args.only)
        sku_dirs = [d for d in sku_dirs if d.name in wanted]
    if args.skip_existing:
        sku_dirs = [d for d in sku_dirs if not runs.latest_run(d.name)]
    if not sku_dirs:
        print("No SKU folders to process in input/")
        return

    results: list[tuple[str, str, str]] = []
    t0 = time.monotonic()
    for d in sku_dirs:
        print(f"\n=== {d.name} ===")
        latest = runs.latest_run(d.name)
        out = (Path(latest["video"]) if latest else
               OUTPUT_DIR / d.name / f"{d.name}_final.mp4")
        absent = missing_inputs(d)
        if absent:
            message = f"skipped: missing {', '.join(absent)}"
            print(f"  - {message}")
            results.append((d.name, "SKIP", message))
            continue
        if args.remix and not (latest and out.exists()):
            message = "skipped: no existing render to remix"
            print(f"  - {message}")
            results.append((d.name, "SKIP", message))
            continue
        steps = []
        if not args.remix:
            steps.append(("align", python_cmd("align", str(d))))
            steps.append(("render", python_cmd("render", str(d))))
        else:
            steps.append(("remix", python_cmd("render", str(d), "--remix")))
        if not args.no_qa:
            # the renderer names its own run, so resolve the video afterwards
            if not args.remix:
                steps.append(("qa", python_cmd("qa", "--latest", d.name)))
            else:
                steps.append(("qa", python_cmd("qa", str(out))))

        ok, message = True, ""
        for label, cmd in steps:
            ok, message = run_step(label, cmd)
            print(("  " if ok else "  ! ") + message)
            if not ok:
                break
        results.append((d.name, "PASS" if ok else "FAIL", message))

    print("\n=== summary ===")
    name_w = max(len(r[0]) for r in results) + 2
    for name, status, message in results:
        print(f"{name:<{name_w}} {status:<5} {message}")
    failures = sum(1 for r in results if r[1] == "FAIL")
    skipped = sum(1 for r in results if r[1] == "SKIP")
    ran = len(results) - failures - skipped
    print(f"\n{ran}/{len(results) - skipped} SKUs ok in "
          f"{time.monotonic() - t0:.0f}s" + (f" ({skipped} skipped)" if skipped else ""))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
