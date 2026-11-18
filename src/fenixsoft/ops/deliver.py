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

DEFAULT_COPY = ["*_final.mp4", "contact_sheet.png", "qa_report.json",
                "caption_audit.json", "edit_plan.json", "word_timings.json",
                "*.md"]


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


def patterns(cfg: dict) -> list[str]:
    copy = (cfg.get("delivery") or {}).get("copy") or DEFAULT_COPY
    return [str(p) for p in copy]


def delivery_dir(cfg: dict, override: Path | None) -> Path:
    if override:
        return override
    configured = (cfg.get("delivery") or {}).get("dir")
    if not configured:
        sys.exit("[deliver] no delivery.dir in config/pipeline.json and no --dir")
    return repo_path(configured).expanduser()


def downloads_dir(cfg: dict) -> Path | None:
    configured = (cfg.get("delivery") or {}).get("downloads")
    return repo_path(configured).expanduser() if configured else None


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

    cfg = load_config(args.config)
    deliv = cfg.get("delivery") or {}
    want_downloads = deliv.get("copy_to_downloads", True) if args.downloads is None \
        else args.downloads
    want_prune = deliv.get("prune_local_media", True) if args.prune is None \
        else args.prune

    run_dir, video = resolve_run(args.run, args.latest)
    target = delivery_dir(cfg, args.dir) / run_dir.name

    files: list[Path] = []
    for pattern in patterns(cfg):
        files.extend(sorted(p for p in run_dir.glob(pattern) if p.is_file()))
    seen: set[Path] = set()
    files = [p for p in files if not (p in seen or seen.add(p))]
    if not files:
        sys.exit(f"[deliver] nothing matched in {run_dir}")

    size_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"[deliver] {run_dir.name} -> {target}  ({len(files)} files, "
          f"{size_mb:.2f} MB)")
    if args.dry_run:
        for p in files:
            print(f"  would copy {p.name}")
        if want_downloads:
            print(f"  would copy {video.name} + contact sheet to Downloads")
        if want_prune:
            print("  would prune local media after verification")
        return

    try:
        target.mkdir(parents=True, exist_ok=True)
        for p in files:
            shutil.copy2(p, target / p.name)
            if (target / p.name).stat().st_size != p.stat().st_size:
                sys.exit(f"[deliver] size mismatch after copying {p.name}")
            print(f"  copied {p.name}")
    except OSError as exc:
        sys.exit(f"[deliver] copy failed: {exc}")

    downloads_to = None
    downloads_files: list[str] = []
    if want_downloads:
        dl = downloads_dir(cfg)
        if dl:
            dl.mkdir(parents=True, exist_ok=True)
            dl_video = dl / video.name
            shutil.copy2(video, dl_video)
            if sha256(video) != sha256(dl_video):
                sys.exit(f"[deliver] hash mismatch in {dl_video}")
            downloads_files.append(dl_video.name)
            sheet = run_dir / "contact_sheet.png"
            if sheet.exists():
                dl_sheet = dl / f"{run_dir.name}_contact_sheet.png"
                shutil.copy2(sheet, dl_sheet)
                downloads_files.append(dl_sheet.name)
            downloads_to = str(dl)
            print(f"[deliver] downloads: {dl_video}")
            print(f"[deliver] downloads: {dl / (run_dir.name + '_contact_sheet.png')}")

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
        "size_mb": round(size_mb, 3),
        "downloads_to": downloads_to,
        "downloads_files": downloads_files,
        "pruned_local_media": pruned,
        "freed_mb": round(freed_mb, 3),
        "master": str(windows_video),
    }
    (run_dir / "delivery.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    runs.register_run(runs.sku_of_run(run_dir), run_dir,
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
