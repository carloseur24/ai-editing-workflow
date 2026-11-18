#!/usr/bin/env python3
"""label — ingest a footage folder at speed: facts, vision grids, safe rename.

Why this exists
---------------
Naming a messy folder by hand means opening 40 clips one by one. The fast loop
is: measure everything in parallel (ffprobe + one 4-frame sheet per clip), let
vision look at a *few* 8-up grids instead of every file, then apply one vetted
mapping atomically. A 40-clip folder lands in seconds, not minutes.

Commands
--------
  fenix label scan  <folder>   # facts + sheets + grids + empty names.tsv
  fenix label apply <folder>   # names.tsv -> renames + manifest + undo.sh
  fenix label undo  <folder>   # reverse the latest apply

The work directory is ``<folder>/.fenix-label`` by default (hidden, disposable):
``scan.json``, ``sheets/``, ``grids/``, ``names.tsv``, ``renames-*.csv`` and
``undo-*.sh``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..context import FONTS_DIR, load_config
from ..domain.labeling import (MEDIA_SUFFIXES, average_hash, build_rename_plan,
                               duplicate_pairs, duplicate_roots, manifest_rows,
                               path_length_ok, shot_fractions, undo_rows)

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
WORK_DIR = ".fenix-label"

DEFAULTS = {
    "jobs": 8,
    "sheet_tile_width": 400,
    "grid_cols": 4,
    "grid_rows": 2,
    "grid_cell_width": 300,
    "duplicate_threshold": 8,
    "name_max_len": 80,
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _settings(config_path: Path | None) -> dict:
    cfg = load_config(config_path)
    merged = dict(DEFAULTS)
    merged.update(cfg.get("labeling") or {})
    merged["jobs"] = max(1, min(int(merged["jobs"]), os.cpu_count() or 8))
    return merged


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _media_files(folder: Path, recursive: bool, work: Path) -> list[Path]:
    out: list[Path] = []
    walker = folder.rglob("*") if recursive else folder.iterdir()
    for path in walker:
        if not path.is_file():
            continue
        if work == path or work in path.parents:
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() in MEDIA_SUFFIXES:
            out.append(path)
    return sorted(out, key=lambda p: p.name.lower())


def _ratio(value: str | None) -> float:
    if not value:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _probe_video(path: Path) -> dict:
    result = _run(["ffprobe", "-v", "error", "-print_format", "json",
                   "-show_format", "-show_streams", str(path)])
    if result.returncode != 0:
        return {"error": (result.stderr or "ffprobe failed").strip().splitlines()[-1]}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "ffprobe returned invalid JSON"}
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        return {"error": "no video stream"}
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    rotation = int((video.get("tags") or {}).get("rotate") or rotation or 0)
    duration = 0.0
    for source in (data.get("format") or {}, video):
        try:
            duration = float(source.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            break
    display_w, display_h = (height, width) if abs(rotation) % 180 == 90 else (width, height)
    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "display_width": display_w,
        "display_height": display_h,
        "rotation": rotation,
        "fps": round(_ratio(video.get("avg_frame_rate") or video.get("r_frame_rate")), 3),
        "codec": video.get("codec_name"),
        "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def _probe_image(path: Path) -> dict:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            width, height = image.size
        return {
            "duration": 0.0,
            "width": width,
            "height": height,
            "display_width": width,
            "display_height": height,
            "rotation": 0,
            "fps": 0.0,
            "codec": path.suffix.lower().lstrip("."),
            "audio": False,
        }
    except Exception as exc:  # noqa: BLE001 - report, do not crash a batch
        return {"error": f"cannot read image: {exc}"}


def _make_sheet(path: Path, meta: dict, out: Path, tile_width: int, frames: int = 4) -> str | None:
    if meta.get("kind") == "image":
        return str(path)
    duration = float(meta.get("duration") or 0.0)
    if duration <= 0:
        return None

    # Keyframe-only decode: ~10× faster than walking every frame, and the
    # select filter keeps the four samples spread across the timeline. fps(1)
    # would duplicate whichever keyframe is nearest; this does not.
    start = 0.03 * duration
    step = 0.30 * duration
    select = (f"select=gte(t\\,{start:.3f})*"
              f"(isnan(prev_selected_t)+gte(t-prev_selected_t\\,{step:.3f})),"
              f"scale={tile_width}:-2,tile=2x2")
    result = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-y", "-threads", "2", "-skip_frame", "nokey",
                   "-i", str(path), "-an", "-vf", select,
                   "-frames:v", "1", "-q:v", "3", str(out)])
    if result.returncode == 0 and out.exists():
        return str(out)

    # Sparse keyframes (very short clips, all-intra files): four fast seeks.
    times = [min(max(duration * f, 0.0), max(duration - 0.05, 0.0))
             for f in shot_fractions(frames)]
    inputs: list[str] = []
    for moment in times:
        inputs += ["-ss", f"{moment:.3f}", "-i", str(path)]
    graph = "".join(f"[{i}:v]scale={tile_width}:-2[s{i}];" for i in range(frames))
    graph += "[s0][s1]hstack[top];[s2][s3]hstack[bot];[top][bot]vstack"
    result = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                   "-y", "-threads", "2", *inputs, "-filter_complex", graph,
                   "-frames:v", "1", "-q:v", "3", str(out)])
    if result.returncode != 0 or not out.exists():
        return None
    return str(out)


def _hash_sheet(sheet: str, kind: str) -> list[int]:
    try:
        with Image.open(sheet) as image:
            gray = ImageOps.exif_transpose(image).convert("L")
            width, height = gray.size
            if kind == "image":
                tiles = [gray]
            else:
                half_w, half_h = width // 2, height // 2
                tiles = [
                    gray.crop((0, 0, half_w, half_h)),
                    gray.crop((half_w, 0, width, half_h)),
                    gray.crop((0, half_h, half_w, height)),
                    gray.crop((half_w, half_h, width, height)),
                ]
            return [average_hash(t.resize((16, 16), Image.Resampling.BILINEAR).tobytes())
                    for t in tiles]
    except Exception:  # noqa: BLE001
        return []


def _scan_one(path: Path, index: int, work: Path, cfg: dict) -> dict:
    suffix = path.suffix.lower()
    kind = "video" if suffix in VIDEO_SUFFIXES else "image"
    meta = _probe_video(path) if kind == "video" else _probe_image(path)
    entry = {
        "index": index,
        "file": path.name,
        "kind": kind,
        "suffix": suffix,
        "bytes": path.stat().st_size,
        "mtime": int(path.stat().st_mtime),
        **meta,
    }
    if meta.get("error"):
        return entry
    if kind == "video":
        sheet_path = work / "sheets" / f"{index:02d}.jpg"
        made = _make_sheet(path, entry, sheet_path, int(cfg["sheet_tile_width"]))
    else:
        made = str(path)
    entry["sheet"] = made
    entry["hashes"] = _hash_sheet(made, kind) if made else []
    return entry


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        FONTS_DIR / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _cell_preview(entry: dict, width: int, height: int) -> Image.Image:
    """One representative frame per file: the 35 % tile for videos, the photo otherwise."""
    sheet = entry.get("sheet")
    canvas = Image.new("RGB", (width, height), (24, 24, 24))
    if not sheet:
        return canvas
    try:
        with Image.open(sheet) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            if entry.get("kind") == "video":
                w, h = image.size
                image = image.crop((w // 2, 0, w, h // 2))
            fitted = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS,
                                  centering=(0.5, 0.45))
            canvas.paste(fitted, (0, 0))
    except Exception:  # noqa: BLE001
        pass
    return canvas


def _write_grid(entries: list[dict], out: Path, cfg: dict) -> dict:
    cols = int(cfg["grid_cols"])
    used_cols = min(cols, max(1, len(entries)))
    rows = max(1, (len(entries) + cols - 1) // cols)
    cell_w = int(cfg["grid_cell_width"])
    cell_h = round(cell_w * 16 / 9)
    label_h = 34
    padding = 6
    canvas = Image.new("RGB", (used_cols * cell_w + padding * 2,
                                rows * (cell_h + label_h) + padding * 2), (16, 16, 16))
    draw = ImageDraw.Draw(canvas)
    font = _font(20)
    for slot, entry in enumerate(entries):
        col, row = slot % cols, slot // cols
        x = padding + col * cell_w
        y = padding + row * (cell_h + label_h)
        canvas.paste(_cell_preview(entry, cell_w, cell_h), (x, y + label_h))
        label = f"#{int(entry['index']):02d}"
        if entry.get("kind") == "video" and entry.get("duration"):
            label += f"  {float(entry['duration']):.1f}s"
        draw.text((x + 8, y + 5), label, font=font, fill=(238, 238, 238))
        if entry.get("duplicate_of"):
            dup = f"dup #{int(entry['duplicate_of']):02d}"
            box = draw.textbbox((0, 0), dup, font=font)
            draw.text((x + cell_w - (box[2] - box[0]) - 8, y + 5), dup,
                      font=font, fill=(255, 120, 100))
    canvas.save(out, quality=88, optimize=True)
    return {"path": str(out), "files": [int(e["index"]) for e in entries]}


def _previous_names(work: Path) -> dict[str, str]:
    """Carry labels across a re-scan of the same files (keyed by filename)."""
    scan_path = work / "scan.json"
    if not scan_path.exists():
        return {}
    try:
        previous = json.loads(scan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    names = _read_names(work / "names.tsv")
    carried: dict[str, str] = {}
    for row in previous.get("files", []):
        name = names.get(int(row["index"]), "").strip()
        if name:
            carried[row["file"]] = name
    return carried


def _read_names(path: Path) -> dict[int, str]:
    names: dict[int, str] = {}
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        index, _, name = line.partition("\t")
        try:
            names[int(index)] = name.strip()
        except ValueError:
            continue
    return names


def _write_names(path: Path, entries: list[dict], existing: dict[str, str]) -> None:
    lines = []
    for entry in entries:
        current = existing.get(entry["file"], "")
        lines.append(f"{int(entry['index']):02d}\t{current}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_scan(folder: Path, work: Path) -> dict:
    path = work / "scan.json"
    if not path.exists():
        raise SystemExit(f"[label] no scan in {work}; run `fenix label scan {folder}` first")
    return json.loads(path.read_text(encoding="utf-8"))


def _print_scan(summary: dict, work: Path) -> None:
    print(f"scanned {summary['count']} files in {summary['elapsed_sec']:.1f}s"
          f"  ({summary['videos']} video, {summary['images']} image,"
          f" {summary['duplicate_pairs']} duplicate pair(s))")
    if summary["errors"]:
        print(f"  ! {len(summary['errors'])} unreadable file(s): "
              + ", ".join(summary["errors"][:5]))
    print(f"\ngrids (read these first, in one batch):\n")
    for grid in summary["grids"]:
        first, last = grid["files"][0], grid["files"][-1]
        print(f"  {grid['path']}   #{first:02d}-#{last:02d}")
    print(f"\ndetail sheets: {work / 'sheets'}")
    print(f"names:        {work / 'names.tsv'}  (column 2; empty = skip)")
    print(f"\nnext: fill names.tsv, then\n  ./fenix label apply {summary['folder']}")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def scan(args: argparse.Namespace) -> int:
    started = time.monotonic()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"[label] not a folder: {folder}")
    cfg = _settings(args.config)
    if args.jobs:
        cfg["jobs"] = max(1, int(args.jobs))
    work = Path(args.work).expanduser().resolve() if args.work else folder / WORK_DIR
    (work / "sheets").mkdir(parents=True, exist_ok=True)
    (work / "grids").mkdir(parents=True, exist_ok=True)

    files = _media_files(folder, args.recursive, work)
    if not files:
        raise SystemExit(f"[label] no media in {folder}")

    entries: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=int(cfg["jobs"])) as pool:
        futures = {
            pool.submit(_scan_one, path, index, work, cfg): index
            for index, path in enumerate(files, start=1)
        }
        for future in futures:
            index = futures[future]
            try:
                entries[index] = future.result()
            except Exception as exc:  # noqa: BLE001
                entries[index] = {"index": index, "file": files[index - 1].name,
                                  "kind": "unknown", "error": str(exc), "hashes": []}

    ordered = [entries[i] for i in range(1, len(files) + 1)]
    pairs = duplicate_pairs(ordered, threshold=int(cfg["duplicate_threshold"]))
    dup_of = duplicate_roots(ordered, pairs)
    for entry in ordered:
        entry["duplicate_of"] = dup_of.get(int(entry["index"]))

    # Grids: 8 files per image, ordered by index.
    per_grid = int(cfg["grid_cols"]) * int(cfg["grid_rows"])
    grids = []
    for start in range(0, len(ordered), per_grid):
        chunk = ordered[start:start + per_grid]
        grid_path = work / "grids" / f"grid-{start // per_grid + 1:02d}.jpg"
        grids.append(_write_grid(chunk, grid_path, cfg))

    _write_names(work / "names.tsv", ordered, _previous_names(work))
    summary = {
        "version": 1,
        "folder": str(folder),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.monotonic() - started, 2),
        "count": len(ordered),
        "videos": sum(1 for e in ordered if e.get("kind") == "video"),
        "images": sum(1 for e in ordered if e.get("kind") == "image"),
        "duplicate_pairs": len(pairs),
        "grids": grids,
        "files": ordered,
        "errors": [e["file"] for e in ordered if e.get("error")],
    }
    scan_path = work / "scan.json"
    tmp = scan_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(scan_path)

    if args.json:
        print(json.dumps({k: summary[k] for k in
                          ("folder", "count", "videos", "images",
                           "duplicate_pairs", "elapsed_sec", "grids")}, indent=2))
    else:
        _print_scan(summary, work)
    return 0


def apply(args: argparse.Namespace) -> int:
    folder = Path(args.folder).expanduser().resolve()
    work = Path(args.work).expanduser().resolve() if args.work else folder / WORK_DIR
    scan = _load_scan(folder, work)
    names_path = Path(args.names).expanduser().resolve() if args.names else work / "names.tsv"
    names = _read_names(names_path)
    if not any(name.strip() for name in names.values()):
        raise SystemExit(f"[label] {names_path} has no filled names; nothing to do")

    by_index = {int(row["index"]): row for row in scan["files"]}
    entries = []
    unknown = []
    for index, name in sorted(names.items()):
        row = by_index.get(index)
        if not row:
            unknown.append(index)
            continue
        entries.append({"file": row["file"], "name": name})
    if unknown:
        raise SystemExit(f"[label] indexes not in scan.json: {unknown}")

    existing = [p.name for p in folder.iterdir()]
    plan, errors = build_rename_plan(
        entries, existing, max_len=int(_settings(args.config)["name_max_len"]),
        slug=not args.exact)
    if errors:
        for error in errors:
            print(f"  ! {error}", file=sys.stderr)
        raise SystemExit(f"[label] {len(errors)} name error(s); nothing renamed")

    too_long = [row for row in plan if not path_length_ok(folder, row["name"])]
    if too_long:
        for row in too_long:
            print(f"  ! path too long: {row['name']}", file=sys.stderr)
        raise SystemExit("[label] shorten those names; nothing renamed")

    for row in plan:
        print(f"  {row['file']}\n    -> {row['name']}")
    if not plan:
        print("[label] every name already matches; nothing to rename")
        return 0
    if args.dry_run:
        print(f"[label] dry run: {len(plan)} rename(s) would be applied")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = manifest_rows(folder, plan)  # stats the sources before they move
    temp_names = [f".fenix-label-tmp-{stamp}-{i:03d}" for i in range(len(plan))]
    for row, temp in zip(plan, temp_names):
        os.replace(folder / row["file"], folder / temp)
    for row, temp in zip(plan, temp_names):
        os.replace(folder / temp, folder / row["name"])
    manifest = work / f"renames-{stamp}.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "name", "bytes", "mtime"])
        writer.writeheader()
        writer.writerows(rows)
    undo = work / f"undo-{stamp}.sh"
    undo.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'cd {json.dumps(str(folder))}\n'
        + "".join(f"mv -- {json.dumps(r['name'])} {json.dumps(r['file'])}\n" for r in rows),
        encoding="utf-8")
    undo.chmod(0o755)

    # Keep the scan usable if the command is run again.
    renamed = {row["file"]: row["name"] for row in plan}
    for entry in scan["files"]:
        entry["file"] = renamed.get(entry["file"], entry["file"])
    scan["last_apply"] = {"manifest": str(manifest), "at": stamp, "renames": len(plan)}
    (work / "scan.json").write_text(json.dumps(scan, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    _write_names(names_path, sorted(scan["files"], key=lambda r: int(r["index"])),
                 {r["file"]: "" for r in scan["files"]})

    if args.json:
        print(json.dumps({"renamed": len(plan), "manifest": str(manifest),
                          "undo": str(undo)}, indent=2))
    else:
        print(f"[label] renamed {len(plan)} file(s)")
        print(f"  manifest: {manifest}")
        print(f"  undo:     {undo}   (or ./fenix label undo {folder})")
    return 0


def undo(args: argparse.Namespace) -> int:
    folder = Path(args.folder).expanduser().resolve()
    work = Path(args.work).expanduser().resolve() if args.work else folder / WORK_DIR
    if args.manifest:
        manifest = Path(args.manifest).expanduser().resolve()
    else:
        candidates = sorted(work.glob("renames-*.csv"))
        if not candidates:
            raise SystemExit(f"[label] no renames-*.csv in {work}")
        manifest = candidates[-1]
    with manifest.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    plan = undo_rows(rows)
    existing = {p.name for p in folder.iterdir()}
    errors = []
    for row in plan:
        if row["file"] not in existing:
            errors.append(f"missing: {row['file']}")
        if row["name"] in existing:
            errors.append(f"already exists: {row['name']}")
    if errors:
        for error in errors:
            print(f"  ! {error}", file=sys.stderr)
        raise SystemExit("[label] cannot undo cleanly; nothing changed")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    temp_names = [f".fenix-label-undo-{stamp}-{i:03d}" for i in range(len(plan))]
    for row, temp in zip(plan, temp_names):
        os.replace(folder / row["file"], folder / temp)
    for row, temp in zip(plan, temp_names):
        os.replace(folder / temp, folder / row["name"])
    print(f"[label] restored {len(plan)} file(s) from {manifest}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="fenix label",
        description="Scan a footage folder, build vision grids and rename safely")
    sub = parser.add_subparsers(dest="action")

    p_scan = sub.add_parser("scan", help="probe, contact sheets, 8-up grids, names.tsv")
    p_scan.add_argument("folder")
    p_scan.add_argument("--work", help=f"work directory (default: <folder>/{WORK_DIR})")
    p_scan.add_argument("--config")
    p_scan.add_argument("--jobs", type=int, help="parallel ffmpeg/ffprobe workers")
    p_scan.add_argument("--recursive", action="store_true")
    p_scan.add_argument("--json", action="store_true")
    p_scan.set_defaults(func=scan)

    p_apply = sub.add_parser("apply", help="rename from names.tsv with a manifest + undo")
    p_apply.add_argument("folder")
    p_apply.add_argument("--work")
    p_apply.add_argument("--names", help="TSV: <index>\\t<name> (default: work/names.tsv)")
    p_apply.add_argument("--config")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--exact", action="store_true",
                         help="do not slugify; require the name as written")
    p_apply.add_argument("--json", action="store_true")
    p_apply.set_defaults(func=apply)

    p_undo = sub.add_parser("undo", help="reverse the latest apply")
    p_undo.add_argument("folder")
    p_undo.add_argument("--work")
    p_undo.add_argument("--manifest")
    p_undo.set_defaults(func=undo)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        raise SystemExit(2)
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
