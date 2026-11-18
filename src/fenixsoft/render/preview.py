#!/usr/bin/env python3
"""Stills preview for the edit renderer — review placement without an encode.

A full render costs minutes just to discover a title sits over a settings list. A stills preview composites the *same* overlay, native text
cards, captions and grade at one frame per cut midpoint plus the first visible
frame of every card, so text placement is reviewable in a couple of seconds.

Nothing here writes a run: previews live under ``output/<p>/preview/`` and never
enter ``runs.json`` or ``latest.json``.

    fenix render <project> --preview
    fenix comp <project> --preview        # stills at the base cuts (comp.py)

The frames are read from the normalised clip cache when it exists (pixel-equal
to the render) and scaled from the source otherwise (approximately equal; the
manifest records which path was used).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
from PIL import Image

from ..context import output_dir
from ..domain import preview as preview_mod
from ..domain import runs
from ..domain.quality import normalized_clip_path
from ..domain.voice import explicit_duration, is_music_only
from .render_video import (CaptionRenderer, FontBook, OverlayCompositor,
                           TextCardRenderer, build_caption_audit, build_overlay,
                           chunk_at, group_words, load_text_cards, probe,
                           score_clip_positions)

DEFAULTS = {"max_stills": 24, "tile_width": 270, "tile_height": 480,
            "columns": 4}


def preview_cfg(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update(cfg.get("preview") or {})
    return out


class StillSource:
    """Reads one frame per sample from the cache normalised clip or the source."""

    def __init__(self, clip: Path, width: int, height: int, fps: int,
                 bake: dict | None, per_frame: dict | None = None):
        self.width, self.height = width, height
        self.bake = bake
        self.per_frame = per_frame
        cached = normalized_clip_path(clip, width, height, fps, bake)
        self.normalised = cached.exists()
        self.path = cached if self.normalised else clip
        self.cap = cv2.VideoCapture(str(self.path))
        if not self.cap.isOpened():
            self.cap.release()
            self.cap = cv2.VideoCapture(str(clip))
            self.path = clip
            self.normalised = False

    def frame(self, t: float):
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = self.cap.read()
        if not ok:
            return None
        if self.normalised:
            return _lab_boost(frame, self.per_frame)
        # Same geometry as render_video.normalize_clip: scale to cover + centre
        # crop. Lanczos is not available through cv2 resize, so use INTER_AREA.
        h, w = frame.shape[:2]
        scale = max(self.width / w, self.height / h)
        nw, nh = max(self.width, int(round(w * scale))), \
            max(self.height, int(round(h * scale)))
        frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        x0, y0 = (nw - self.width) // 2, (nh - self.height) // 2
        frame = frame[y0:y0 + self.height, x0:x0 + self.width]
        return _lab_boost(frame, self.bake or self.per_frame)

    def release(self) -> None:
        self.cap.release()


def _lab_boost(frame, grade: dict | None):
    """LAB lightness boost, matching render_video's baked and per-frame paths."""
    if not grade:
        return frame
    alpha = float(grade.get("lightness_alpha", 1.06))
    beta = float(grade.get("lightness_beta", 3))
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_ch = cv2.convertScaleAbs(l_ch, alpha=alpha, beta=beta)
    return cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2BGR)


def contact_sheet(frames: list[Path], out: Path,
                  cols: int = 4, tile: tuple[int, int] = (270, 480)) -> Path | None:
    """Grid of preview stills, tile-sized, 9:16 preserved."""
    if not frames:
        return None
    tw, th = int(tile[0]), int(tile[1])
    cols = max(1, int(cols))
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (16, 18, 20))
    for i, path in enumerate(frames):
        try:
            img = Image.open(path).convert("RGB")
        except OSError:
            continue
        img.thumbnail((tw, th))
        x = (i % cols) * tw + (tw - img.width) // 2
        y = (i // cols) * th + (th - img.height) // 2
        sheet.paste(img, (x, y))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def plan_samples(cfg: dict, sku_dir: Path) -> dict:
    """Cuts, cards and the sample list — everything but pixels.

    Shared with the stage so ``--preview`` and the full renderer agree on the
    timeline. Returns a dict with ``cuts``, ``cards``, ``samples``, ``warnings``
    and the measured ``duration``.
    """
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    brief: dict = {}
    brief_path = sku_dir / "brief.json"
    if brief_path.exists():
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    music_only = is_music_only(sku)
    timings_path = sku_dir / "word_timings.json"
    timings: dict = {}
    if timings_path.exists():
        timings = json.loads(timings_path.read_text(encoding="utf-8"))
    words = [] if music_only else (timings.get("words") or [])
    clips = sorted((sku_dir / "clips").glob("*.mp4"))
    if not clips:
        raise SystemExit(f"[preview] no .mp4 clips in {sku_dir / 'clips'}")
    W, H, FPS = (int(cfg["canvas"]["width"]), int(cfg["canvas"]["height"]),
                 int(cfg["canvas"]["fps"]))
    total = (explicit_duration(sku, brief, cfg) if music_only
             else float(timings.get("duration_sec")
                        or cfg["target_duration_sec"]))
    total = max(2.0, total)

    fonts = FontBook()
    overlay = build_overlay(cfg, sku, fonts)
    cap = CaptionRenderer(cfg, overlay, fonts)
    cards = TextCardRenderer(cfg, overlay, fonts,
                             load_text_cards(sku, sku_dir))
    kc = cfg["kinetic_captions"]
    chunks = group_words(words, int(kc["max_words_per_chunk"]),
                         int(kc["max_chars_per_chunk"]),
                         float(kc.get("min_display_ms", 450)) / 1000.0)
    hold = float(kc.get("hold_last_gap_ms", 600)) / 1000.0

    # Cuts: the same plan snapshot the renderer consumes. A missing plan falls
    # back to the auto-planner so the preview still works before `fenix plan`.
    from .render_video import beat_grid_for, load_cut_override
    durations = [probe(c)["duration"] for c in clips]
    cuts, notes = load_cut_override(sku_dir, clips, durations, total, cfg=cfg)
    if not cuts:
        grid = beat_grid_for(cfg, sku_dir)
        slot_est = total / max(1, int(cfg.get("cuts_per_video", 16)))
        scores = {c.name: score_clip_positions(c, FPS, slot_est, samples=4)
                  for c in clips}
        from .render_video import plan_cuts
        cuts = plan_cuts(clips, durations, total, cfg, seed=sku_dir.name,
                         scores=scores, grid=grid)
        notes = ["no edit_plan.json: auto-planned in-points for this preview"]
    pc = preview_cfg(cfg)
    samples = preview_mod.sample_plan(
        cuts, cards.cards, duration=total,
        max_stills=int(pc["max_stills"]))
    return {
        "sku": sku, "music_only": music_only, "total": total,
        "W": W, "H": H, "FPS": FPS, "clips": clips, "timings": timings,
        "fonts": fonts, "overlay": overlay, "cap": cap, "cards": cards,
        "chunks": chunks, "hold": hold, "cuts": cuts, "samples": samples,
        "warnings": list(overlay["warnings"]) + list(notes),
        "caption_audit": ({"disabled": True, "reason": "music-only"}
                          if music_only else
                          build_caption_audit(chunks, cfg, cap, hold)),
    }


def render_preview(cfg: dict, sku_dir: Path) -> dict:
    """Composite every preview still and write the sheet + manifest."""
    started = time.time()
    out_dir = runs.new_timestamped_dir(output_dir(sku_dir) / "preview")
    plan = plan_samples(cfg, sku_dir)
    W, H, FPS = plan["W"], plan["H"], plan["FPS"]
    overlay, cap, cards = plan["overlay"], plan["cap"], plan["cards"]
    compositor = OverlayCompositor(overlay)
    grade_cfg = cfg.get("grade") or {}
    baked = grade_cfg if grade_cfg.get("mode", "baked") == "baked" else None
    per_frame = grade_cfg if grade_cfg.get("mode") == "per_frame" else None
    sources: dict[str, StillSource] = {}
    frames: list[Path] = []
    rows: list[dict] = []
    try:
        for i, sample in enumerate(plan["samples"]):
            clip_name = sample.get("clip") or ""
            clip_path = next((c for c in plan["clips"] if c.name == clip_name),
                             None)
            if clip_path is None:
                continue
            src = sources.get(clip_name)
            if src is None:
                src = StillSource(clip_path, W, H, FPS, baked, per_frame)
                sources[clip_name] = src
            frame = src.frame(float(sample["t_src"]))
            if frame is None:
                continue
            if frame.shape[1] != W or frame.shape[0] != H:
                frame = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA)
            compositor.apply(frame)
            cuts = plan["cuts"]
            cut = cuts[int(sample.get("cut") or 0)]
            cards.draw(frame, float(sample["t"]), zone=cut.get("text_zone"))
            if not plan["music_only"]:
                active = chunk_at(plan["chunks"], float(sample["t"]),
                                  plan["hold"])
                if active:
                    cap.draw(frame, active, float(sample["t"]))
            name = (f"frame_{i:02d}_{sample['kind']}_"
                    f"{float(sample['t']):05.2f}s.png")
            path = out_dir / name
            cv2.imwrite(str(path), frame)
            frames.append(path)
            rows.append({**sample, "file": name})
    finally:
        for src in sources.values():
            src.release()

    pc = preview_cfg(cfg)
    sheet = contact_sheet(frames, out_dir / "contact_sheet.png",
                          cols=int(pc["columns"]),
                          tile=(int(pc["tile_width"]),
                                int(pc["tile_height"])))
    elapsed = round(time.time() - started, 2)
    manifest = {
        "kind": "render",
        "sku": sku_dir.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dir": str(out_dir),
        "duration_sec": round(plan["total"], 3),
        "cuts": len(plan["cuts"]),
        "cards": len(cards.cards),
        "music_only": plan["music_only"],
        "frames": rows,
        "contact_sheet": str(sheet) if sheet else None,
        "warnings": plan["warnings"],
        "caption_audit": plan["caption_audit"],
        "elapsed_sec": elapsed,
        "full_render_sec_estimate": None,
        "note": "stills only: no video encode, no run directory, not in runs.json",
    }
    (out_dir / "preview.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main_preview(cfg: dict, sku_dir: Path) -> None:
    """CLI body for ``fenix render <project> --preview``."""
    manifest = render_preview(cfg, sku_dir)
    print(f"[preview] {len(manifest['frames'])} still(s) in "
          f"{manifest['elapsed_sec']}s -> {manifest['dir']}")
    if manifest.get("contact_sheet"):
        print(f"[preview] sheet: {manifest['contact_sheet']}")
    for warning in manifest.get("warnings") or []:
        print(f"[preview] WARNING: {warning}")
    print("[preview] previews are review artifacts: not in runs.json, "
          "not a delivery master")
