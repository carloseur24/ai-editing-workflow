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

from ..context import INPUT_DIR, OUTPUT_DIR, REPO_ROOT, resolve_sku_dir

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


def new_timestamped_dir(base: Path, when: datetime | None = None) -> Path:
    """Create ``<base>/<YYYYmmdd-HHMMSS>/`` (``-2`` on a same-second clash).

    Used by previews: they are timestamped review artifacts under
    ``output/<sku>/preview/`` but are not runs, so they never enter
    ``runs.json`` or ``latest.json``.
    """
    stamp = (when or datetime.now()).strftime(STAMP_FMT)
    path = base / stamp
    n = 2
    while path.exists():
        path = base / f"{stamp}-{n}"
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


# Post-stage artifacts with a stable name, so `@<stage>/latest` needs no path.
STAGE_ARTIFACTS = {
    "motion": ("plate.mov", "plate.mp4"),
    "comp": ("comp.mp4",),
    "fx": ("fx.mp4",),
    "grade": ("graded.mp4",),
    "longform": ("master.mp4",),
}


def is_stable_ref(value: str | Path | None) -> bool:
    return str(value or "").startswith("@")


def resolve_stable_ref(value: str | Path, sku: str,
                       root: Path | None = None) -> Path | None:
    """Resolve ``@render/latest`` / ``@motion/latest/plate.mov`` to a real file.

    Stable refs let a post spec point at "the newest edit render" or "the
    newest motion plate" without pasting a timestamp that changes every run:

        @render/latest                     newest run video
        @render/latest/edit_plan.json      a file inside the newest run
        @motion/latest                     newest plate (plate.mov or .mp4)
        @motion/latest/manifest.json       a file inside the newest stage run
        @track/latest                      newest tracking JSON

    Explicit paths keep working; an unresolvable ref returns ``None`` so the
    caller can name the missing stage instead of silently rendering nothing.
    """
    raw = str(value or "")
    if not raw.startswith("@"):
        return None
    parts = [p for p in raw[1:].split("/") if p]
    if len(parts) < 2 or parts[1] != "latest":
        return None
    stage, rest = parts[0], parts[2:]

    if stage == "render":
        latest = latest_run(sku, root)
        if not latest:
            return None
        base = Path(latest.get("dir") or Path(latest["video"]).parent)
        if rest:
            path = base.joinpath(*rest)
            if path.exists():
                return path
            # The run may have been pruned after delivery; the index still
            # points at the delivered master, so sibling evidence is gone.
            return None
        video = latest.get("video")
        return Path(video) if video and Path(video).exists() else None

    base = output_root(sku, root) / stage
    if not base.exists():
        return None
    if stage == "track":
        candidates = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if rest:
            path = base.joinpath(*rest)
            return path if path.exists() else None
        return candidates[-1] if candidates else None
    dirs = sorted((d for d in base.iterdir() if d.is_dir()),
                  key=lambda p: p.stat().st_mtime)
    if not dirs:
        return None
    newest = dirs[-1]
    if rest:
        path = newest.joinpath(*rest)
        return path if path.exists() else None
    for name in STAGE_ARTIFACTS.get(stage, ()):
        path = newest / name
        if path.exists():
            return path
    videos = sorted(newest.glob("*.mp4"))
    return videos[0] if videos else None


def delivery_roots(cfg: dict | None) -> list[Path]:
    """Resolved delivery shelf/downloads roots from the config (may be empty)."""
    deliv = (cfg or {}).get("delivery") or {}
    roots: list[Path] = []
    for key in ("dir", "downloads"):
        value = deliv.get(key)
        if not value:
            continue
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = REPO_ROOT / path
        roots.append(path.resolve())
    return roots


def is_on_delivery_shelf(video: Path | str, cfg: dict | None) -> bool:
    """True when ``video`` lives inside the configured delivery shelf."""
    try:
        target = Path(video).resolve()
    except OSError:
        return False
    for root in delivery_roots(cfg):
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def evidence_dir(video: Path | str, cfg: dict | None) -> Path:
    """Local directory that keeps evidence (QA report) about ``video``.

    Policy: only delivery commands write to the delivery shelf. A local run
    keeps its report next to the master; a delivered master resolves to the
    local run directory when it still exists (finish prunes media, not JSON),
    otherwise to ``output/<sku>/qa/<run>/``.
    """
    video = Path(video)
    if not is_on_delivery_shelf(video, cfg):
        return video.parent
    sku, _ = resolve_for_video(video)
    local = output_root(sku) / video.parent.name
    if local.is_dir():
        return local
    return output_root(sku) / "qa" / video.parent.name


def evidence_path(video: Path | str, cfg: dict | None,
                  name: str = "qa_report.json") -> Path:
    return evidence_dir(video, cfg) / name


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
