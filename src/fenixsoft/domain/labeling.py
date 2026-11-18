"""Pure helpers for footage labeling.

Nothing here touches ffmpeg, Pillow or the filesystem. ``ops/label.py`` does the
I/O; this module owns the rules that must stay testable: slugs, filename safety,
perceptual hashes and rename plans.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

# Windows refuses these stems regardless of extension.
RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

INVALID_CHARS = frozenset('<>:"/\\|?*')
MEDIA_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi",
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff",
}


def slugify(text: str, max_len: int = 80) -> str:
    """ASCII kebab-case: accents folded, punctuation collapsed, no edge dashes."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len].rstrip("-")


def split_stem(name: str) -> tuple[str, str]:
    """Return ``(stem, suffix)`` with the suffix exactly as written."""
    suffix = Path(name).suffix
    return (name[: -len(suffix)], suffix) if suffix else (name, "")


def target_name(raw: str, source_suffix: str, max_len: int = 80, slug: bool = True) -> str:
    """Turn an agent-authored label into a safe filename with the source suffix."""
    stem, suffix = split_stem(raw.strip())
    if suffix.lower() != source_suffix.lower():
        stem, suffix = raw.strip(), source_suffix
    if slug:
        stem = slugify(stem, max_len=max(1, max_len - len(suffix)))
    return f"{stem}{suffix}"


def validate_filename(name: str, max_len: int = 80) -> list[str]:
    """Return human-readable reasons the name cannot be used (empty = valid)."""
    errors: list[str] = []
    if not name:
        return ["empty name"]
    if name != name.strip():
        errors.append("leading/trailing whitespace")
    if len(name) > max_len:
        errors.append(f"longer than {max_len} chars ({len(name)})")
    if any(ch in INVALID_CHARS for ch in name):
        errors.append("contains one of <>:\"/\\|?*")
    if name.endswith("."):
        errors.append("ends with a dot")
    if any(ord(ch) < 32 for ch in name):
        errors.append("contains a control character")
    stem = split_stem(name)[0]
    if stem.strip().upper() in RESERVED_STEMS:
        errors.append(f"'{stem}' is a reserved Windows device name")
    return errors


def shot_fractions(count: int = 4) -> list[float]:
    """Evenly spaced sample points, one per bucket, away from the hard edges."""
    if count < 1:
        raise ValueError("count must be >= 1")
    return [(i + 0.5) / count for i in range(count)]


def average_hash(gray_16x16: bytes | bytearray) -> int:
    """64-bit average hash from 256 grayscale bytes (16×16)."""
    if len(gray_16x16) < 256:
        raise ValueError("average_hash needs 256 bytes")
    pixels = gray_16x16[:256]
    mean = sum(pixels) / 256.0
    bits = 0
    for i, value in enumerate(pixels):
        if value > mean:
            bits |= 1 << i
    return bits


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def same_content(
    left: dict,
    right: dict,
    threshold: int = 8,
    duration_tolerance: float = 0.03,
) -> bool:
    """Two scans are the same clip when every sampled frame matches.

    Comparing all four frames (not just one) keeps two different shots of the
    same phone from collapsing into a false duplicate.
    """
    if left.get("kind") != right.get("kind"):
        return False
    lh, rh = left.get("hashes") or [], right.get("hashes") or []
    if not lh or len(lh) != len(rh):
        return False
    if left.get("kind") == "video":
        ld, rd = float(left.get("duration") or 0.0), float(right.get("duration") or 0.0)
        if ld <= 0 or rd <= 0:
            return False
        if abs(ld - rd) > max(1.0, duration_tolerance * max(ld, rd)):
            return False
    return all(hamming(a, b) <= threshold for a, b in zip(lh, rh))


def duplicate_pairs(
    items: list[dict],
    threshold: int = 8,
    duration_tolerance: float = 0.03,
) -> list[tuple[int, int]]:
    """Pair indexes that show the same footage, in stable ``(first, later)`` order."""
    pairs: list[tuple[int, int]] = []
    for i, left in enumerate(items):
        for right in items[i + 1:]:
            if same_content(left, right, threshold, duration_tolerance):
                pairs.append((int(left["index"]), int(right["index"])))
    return pairs


def duplicate_of_map(pairs: list[tuple[int, int]]) -> dict[int, int]:
    """Map each later duplicate to the earliest index in its cluster."""
    primary: dict[int, int] = {}
    for first, later in pairs:
        root = primary.get(first, first)
        primary[later] = root
    return primary


def duplicate_roots(items: list[dict], pairs: list[tuple[int, int]]) -> dict[int, int]:
    """Cluster duplicate pairs and point every member at the oldest file.

    Alphabetical order is a bad primary: ``...-copia`` sorts before the file it
    copied. The original capture has the older mtime, so it wins; ties fall back
    to the lower scan index.
    """
    parent = {int(item["index"]): int(item["index"]) for item in items}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for first, later in pairs:
        if first in parent and later in parent:
            union(first, later)
    by_index = {int(item["index"]): item for item in items}
    clusters: dict[int, list[int]] = {}
    for index in parent:
        clusters.setdefault(find(index), []).append(index)

    roots: dict[int, int] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        primary = min(members, key=lambda i: (int(by_index[i].get("mtime") or 0), i))
        for member in members:
            if member != primary:
                roots[member] = primary
    return roots


def build_rename_plan(
    entries: list[dict],
    existing_names: list[str],
    max_len: int = 80,
    slug: bool = True,
) -> tuple[list[dict], list[str]]:
    """Validate labels and return ``(plan, errors)``.

    ``entries`` are ``{"file": "...", "name": "..."}`` rows. Targets are checked
    case-insensitively because the destination is often a Windows volume. Files
    being renamed in the same batch are allowed to free a name for each other.
    """
    sources = {e["file"] for e in entries}
    remaining = {n.lower() for n in existing_names if n not in sources}
    seen: dict[str, str] = {}
    plan: list[dict] = []
    errors: list[str] = []

    for entry in entries:
        old = entry["file"]
        raw = (entry.get("name") or "").strip()
        if not raw:
            continue
        suffix = Path(old).suffix
        new = target_name(raw, suffix, max_len=max_len, slug=slug)
        problems = validate_filename(new, max_len=max_len)
        if problems:
            errors.append(f"{old}: {', '.join(problems)}")
            continue
        key = new.lower()
        if key in remaining:
            errors.append(f"{old}: target already exists: {new}")
            continue
        if key in seen:
            errors.append(f"{old}: duplicate target {new} (also from {seen[key]})")
            continue
        if new == old:
            continue
        seen[key] = old
        plan.append({"file": old, "name": new})
    return plan, errors


def rename_order(plan: list[dict]) -> list[dict]:
    """Stable order for execution; two-phase renames make swaps safe."""
    return list(plan)


def undo_rows(rows: list[dict]) -> list[dict]:
    """Reverse a manifest: current name -> original name, last rename first."""
    return [{"file": row["name"], "name": row["file"]} for row in reversed(rows)]


def manifest_rows(folder: Path, plan: list[dict]) -> list[dict]:
    """Enrich a plan with the data an undo or audit needs."""
    rows = []
    for item in plan:
        path = folder / item["file"]
        stat = path.stat()
        rows.append({
            "file": item["file"],
            "name": item["name"],
            "bytes": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    return rows


def ensure_suffix(name: str, suffix: str) -> str:
    return name if name.lower().endswith(suffix.lower()) else name + suffix


def path_length_ok(folder: Path, name: str, limit: int = 250) -> bool:
    """Conservative Windows MAX_PATH guard (the folder is usually /mnt/c/...)."""
    return len(os.fspath(folder)) + 1 + len(name) <= limit
