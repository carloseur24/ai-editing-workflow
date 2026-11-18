#!/usr/bin/env python3
"""
deliver — copy a finished master to the delivery shelf and (optionally)
prune local media.

Policy: WSL is pre-production. Finished masters live outside the repo (a
Windows shelf on the author's machine, `output/delivery` by default):
  <delivery.dir>/<run>/<run>_final.mp4      (folder with the evidence)
  <delivery.downloads>/<run>_final.mp4      (flat copy for quick opening)

With `--prune` (default from `delivery.prune_local_media`) the local MP4,
contact sheet and QA frames are deleted **after** a verified copy. The run keeps
its JSON evidence and `runs.json` points at the delivered master, so
`fenix qa --latest` / `fenix runs` still resolve it.

    fenix deliver --latest <project>
    fenix deliver --latest <project> --downloads --prune
    fenix finish <project>          # QA strict -> deliver -> prune

Exit 0 = delivered, 1 = nothing to deliver / copy failed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from ..context import DEFAULT_CONFIG, load_config, repo_path
from ..domain import runs
from ..domain.voice import is_music_only
from . import bundle


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_run(target: Path | None, latest: str | None) -> tuple[Path, Path]:
    if latest:
        entry = runs.latest_run(latest)
        if not entry:
            sys.exit(f"[deliver] no completed run for {latest}")
        run_dir = Path(entry["dir"])
    elif target:
        run_dir = target if target.is_dir() else target.parent
    else:
        sys.exit("[deliver] pass a run directory or --latest <sku>")
    video = runs.run_video(run_dir)
    if not video.exists():
        if latest:
            entry = runs.latest_run(latest) or {}
            video = Path(entry.get("video") or video)
        if not video.exists():
            sys.exit(f"[deliver] no final MP4 in {run_dir}")
    return run_dir, video


def delivery_dir(cfg: dict, override: Path | None) -> Path:
    if override:
        return override
    configured = (cfg.get("delivery") or {}).get("dir")
    if not configured:
        sys.exit("[deliver] no delivery.dir in config/pipeline.json and no --dir")
    return repo_path(configured).expanduser()


def write_index(root: Path) -> Path:
    """Keep `<delivery.dir>/README.md` listing every delivered piece."""
    rows = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        video = runs.run_video(d)
        if not video.exists():
            continue
        mb = video.stat().st_size / 1024 / 1024
        notes = next((n.name for n in d.glob("*.md")), "-")
        rows.append((d.name, video.name, f"{mb:.2f} MB", notes))
    lines = ["# Deliveries", "",
             "Each folder holds one finished render: the MP4, contact sheet, "
             "QA report, caption audit and executed plan.", "",
             "| Run | Video | Size | Notes |", "|---|---|---|---|"]
    for name, video, size, notes in rows:
        lines.append(f"| `{name}/` | `{video}` | {size} | `{notes}` |")
    lines.append("")
    index = root / "README.md"
    index.write_text("\n".join(lines), encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Copy a finished run to the delivery shelf")
    parser.add_argument("run", nargs="?", type=Path, default=None)
    parser.add_argument("--latest", metavar="SKU",
                        help="deliver the newest run of this SKU")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dir", type=Path, default=None,
                        help="override delivery.dir")
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--downloads", dest="downloads", action="store_true",
                        help="also copy the master flat into delivery.downloads")
    policy.add_argument("--no-downloads", dest="downloads", action="store_false")
    policy.add_argument("--prune", dest="prune", action="store_true",
                        help="delete local MP4/contact sheet/QA frames after a "
                             "verified copy (runs.json points at the delivered master)")
    policy.add_argument("--no-prune", dest="prune", action="store_false")
    parser.set_defaults(downloads=None, prune=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    run_dir, video = resolve_run(args.run, args.latest)
    project = args.latest or runs.sku_of_run(run_dir)
    cfg = load_config(args.config, project=runs.input_dir(project))
    deliv = cfg.get("delivery") or {}
    want_downloads = deliv.get("copy_to_downloads", True) if args.downloads is None \
        else args.downloads
    want_prune = deliv.get("prune_local_media", True) if args.prune is None \
        else args.prune
    target = delivery_dir(cfg, args.dir) / run_dir.name

    # Effective config for the shared planner: --dir / --no-downloads override
    # without mutating the loaded config, and pipeline.local.json stays applied.
    eff = dict(cfg)
    eff_delivery = dict(deliv)
    eff_delivery["dir"] = str(target.parent)
    if not want_downloads:
        eff_delivery["downloads"] = None
    eff["delivery"] = eff_delivery

    no_voice = False
    try:
        sku_data = json.loads((runs.input_dir(project) / "sku.json")
                              .read_text(encoding="utf-8"))
        no_voice = is_music_only(sku_data)
    except (OSError, json.JSONDecodeError):
        no_voice = False

    plan = bundle.bundle_plan(project, run_dir, eff, master=video)
    if not plan:
        sys.exit(f"[deliver] nothing to deliver in {run_dir}")
    files = [Path(row["src"]) for row in plan if row["kind"] == "shelf"]
    size_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"[deliver] {run_dir.name} -> {target}  ({len(plan)} files, "
          f"{size_mb:.2f} MB)")
    if args.dry_run:
        for row in plan:
            print(f"  would copy [{row['kind']}] {Path(row['dest']).name}")
        if want_prune:
            print("  would prune local media after verification")
        return

    # The note is generated from the plan it ships with, so the readme is an
    # extra row rather than a file the planner had to guess about.
    readme_path = run_dir / "delivery_readme.md"
    readme_path.write_text(
        bundle.write_bundle_readme(project, run_dir, plan, eff,
                                   no_voice=no_voice),
        encoding="utf-8")
    readme_dest = target / readme_path.name
    if all(Path(row["dest"]) != readme_dest for row in plan):
        plan.append({"src": readme_path, "dest": readme_dest,
                     "kind": "shelf"})
        files.append(readme_path)
        size_mb = sum(p.stat().st_size for p in files) / 1024 / 1024

    result = bundle.apply_bundle(plan)
    for warning in result["skipped"]:
        print(f"[deliver] WARNING: missing source skipped: {warning}")

    # Verify before pruning: size for every file, sha256 for flat Downloads.
    for row in plan:
        src, dest = Path(row["src"]), Path(row["dest"])
        if not dest.exists():
            sys.exit(f"[deliver] missing after copy: {dest}")
        if dest.stat().st_size != src.stat().st_size:
            sys.exit(f"[deliver] size mismatch after copying {dest.name}")
        if row["kind"] == "downloads" and sha256(src) != sha256(dest):
            sys.exit(f"[deliver] hash mismatch in {dest}")
        print(f"  copied [{row['kind']}] {dest.name}")

    downloads = [row for row in plan if row["kind"] == "downloads"]
    downloads_to = (str(Path(downloads[0]["dest"]).parent)
                    if downloads else None)
    downloads_files = [Path(row["dest"]).name for row in downloads]
    for row in downloads:
        print(f"[deliver] downloads: {row['dest']}")

    freed_mb = 0.0
    pruned = False
    windows_video = target / video.name
    if want_prune:
        for p in [run_dir / video.name, run_dir / "contact_sheet.png"]:
            if p.exists():
                freed_mb += p.stat().st_size / 1024 / 1024
                p.unlink()
        frames = run_dir / "qa_frames"
        if frames.exists():
            freed_mb += sum(f.stat().st_size for f in frames.glob("*")) / 1024 / 1024
            shutil.rmtree(frames, ignore_errors=True)
        pruned = True
        print(f"[deliver] pruned local media ({freed_mb:.1f} MB freed); "
              f"master: {windows_video}")

    record = {
        "run": run_dir.name,
        "delivered_to": str(target),
        "delivered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": [p.name for p in files],
        "copied": result["copied"],
        "skipped": result["skipped"],
        "size_mb": round(size_mb, 3),
        "downloads_to": downloads_to,
        "downloads_files": downloads_files,
        "pruned_local_media": pruned,
        "freed_mb": round(freed_mb, 3),
        "master": str(windows_video),
    }
    (run_dir / "delivery.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    runs.register_run(project, run_dir,
                      windows_video if pruned else video,
                      metrics={"delivered_to": str(target),
                               "downloads_to": downloads_to,
                               "local_pruned": pruned})
    index = write_index(target.parent)
    print(f"[deliver] done: {target}")
    print(f"[deliver] index: {index}")
    windows = str(target).replace("/mnt/c/", "C:/").replace("/", "\\")
    print(f"[deliver] windows path: {windows}")


if __name__ == "__main__":
    main()
