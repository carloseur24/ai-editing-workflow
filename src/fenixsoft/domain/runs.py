#!/usr/bin/env python3
"""
runs.py — immutable, timestamped output runs.

Every render writes into its own directory and never touches a previous one:

    output/<sku>/<sku>_<YYYYmmdd-HHMMSS>/<sku>_<YYYYmmdd-HHMMSS>_final.mp4
    output/<sku>/<sku>_<YYYYmmdd-HHMMSS>/contact_sheet.png
    output/<sku>/<sku>_<YYYYmmdd-HHMMSS>/qa_frames/
    output/<sku>/<sku>_<YYYYmmdd-HHMMSS>/{edit_plan,caption_audit,qa_report}.json
    output/<sku>/runs.json      # append-only index: run id, video, metrics
    output/<sku>/latest.json    # pointer to the newest completed run

Legacy flat renders (`output/<sku>/<sku>_final.mp4`) are never overwritten and
remain readable; they are simply not "runs" until they are re-rendered.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from ..context import INPUT_DIR, OUTPUT_DIR, resolve_sku_dir

STAMP_FMT = "%Y%m%d-%H%M%S"
RUN_RE = re.compile(r"^(?P<sku>.+?)_(?P<stamp>\d{8}-\d{6})(?:-(?P<n>\d+))?$")


def output_root(sku: str, root: Path | None = None) -> Path:
    return (root / "output" / sku) if root else (OUTPUT_DIR / sku)


def new_run_dir(sku: str, root: Path | None = None,
                when: datetime | None = None) -> Path:
    """Create `output/<sku>/<sku>_<stamp>/`; same-second runs get `-2`, `-3`."""
    base = output_root(sku, root)
    stamp = (when or datetime.now()).strftime(STAMP_FMT)
    run_id = f"{sku}_{stamp}"
    path = base / run_id
    n = 2
    while path.exists():
        run_id = f"{sku}_{stamp}-{n}"
        path = base / run_id
        n += 1
    path.mkdir(parents=True)
    return path


def run_name(sku: str, when: datetime | None = None) -> str:
    return f"{sku}_{(when or datetime.now()).strftime(STAMP_FMT)}"


def run_id_of(path: Path) -> str:
    return path.name


def sku_of_run(path: Path | str) -> str:
    """`demo_iphone14_blue_20261115-190210` -> `demo_iphone14_blue`."""
    name = Path(path).name
    m = RUN_RE.match(name)
    return m.group("sku") if m else name


def is_run_dir(path: Path) -> bool:
    return bool(RUN_RE.match(path.name)) and path.is_dir()


def run_video(run_dir: Path) -> Path:
    """The final MP4 inside a run directory (or the expected name)."""
    candidates = sorted(run_dir.glob("*_final.mp4"))
    if candidates:
        return candidates[-1]
    return run_dir / f"{run_dir.name}_final.mp4"


def input_dir(sku: str, root: Path | None = None) -> Path:
    return (root / "input" / sku) if root else (INPUT_DIR / sku)


def resolve_for_video(video: Path, root: Path | None = None) -> tuple[str, Path]:
    """Map any output video path back to (sku name, input/<sku> directory)."""
    video = Path(video)
    sku = sku_of_run(video.parent)
    cand = input_dir(sku, root)
    if cand.exists():
        return sku, cand
    # legacy flat layout: output/<sku>/<sku>_final.mp4
    cand = input_dir(video.parent.name, root)
    if cand.exists():
        return video.parent.name, cand
    # last resort: edit_plan.json records the sku
    plan = video.parent / "edit_plan.json"
    if plan.exists():
        try:
            sku = json.loads(plan.read_text(encoding="utf-8")).get("sku") or sku
        except json.JSONDecodeError:
            pass
    return sku, input_dir(sku, root)


def list_runs(sku: str, root: Path | None = None) -> list[dict]:
    base = output_root(sku, root)
    if not base.exists():
        return []
    # A finished run may have had its local media pruned; runs.json then points
    # at the Windows master so `--latest`, QA and eval still resolve it.
    indexed: dict[str, str] = {}
    try:
        data = json.loads((base / "runs.json").read_text(encoding="utf-8"))
        indexed = {r.get("run"): r.get("video") for r in data.get("runs", [])
                   if r.get("run") and r.get("video")}
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    out: list[dict] = []
    for d in sorted(base.iterdir()):
        if not is_run_dir(d):
            continue
        video = run_video(d)
        if not video.exists() and indexed.get(d.name):
            video = Path(indexed[d.name])
        out.append({
            "run": d.name,
            "dir": str(d),
            "video": str(video) if video.exists() else None,
            "created_at": datetime.fromtimestamp(d.stat().st_mtime)
                                  .strftime("%Y-%m-%dT%H:%M:%S"),
            "size_mb": (round(video.stat().st_size / 1024 / 1024, 3)
                        if video.exists() else None),
        })
    return out


def latest_run(sku: str, root: Path | None = None) -> dict | None:
    runs = [r for r in list_runs(sku, root) if r["video"]]
    return runs[-1] if runs else None


def register_run(sku: str, run_dir: Path, video: Path,
                 metrics: dict | None = None, root: Path | None = None) -> dict:
    """Append (or update) this run in `output/<sku>/runs.json` + latest.json."""
    base = output_root(sku, root)
    index_path = base / "runs.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        index = {"sku": sku, "runs": []}
    entry = {
        "run": run_dir.name,
        "video": str(video),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "size_mb": round(video.stat().st_size / 1024 / 1024, 3)
                   if video.exists() else None,
    }
    prior_run = next((r for r in index.get("runs", [])
                      if r.get("run") == run_dir.name), {})
    # QA/deliver re-register an existing run; the render time must not move.
    if prior_run.get("created_at"):
        entry["created_at"] = prior_run["created_at"]
    if metrics:
        prior = prior_run.get("metrics") or {}
        entry["metrics"] = {**prior, **metrics}
    runs = [r for r in index.get("runs", []) if r.get("run") != run_dir.name]
    runs.append(entry)
    index["runs"] = runs
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    pointer = {k: entry[k] for k in ("run", "video", "created_at", "size_mb")}
    (base / "latest.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="List the render runs of a SKU")
    parser.add_argument("sku", help="SKU name (or input/<sku> path)")
    args = parser.parse_args(argv)
    sku_dir = resolve_sku_dir(args.sku)
    runs = list_runs(sku_dir.name)
    if not runs:
        print(f"[runs] no runs for {sku_dir.name} yet")
        return
    for r in runs:
        size = r["size_mb"] or 0.0
        print(f"{r['run']:<44} {size:>6.2f} MB  {r['created_at']}")


if __name__ == "__main__":
    sys.exit(main())
