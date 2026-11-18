#!/usr/bin/env python3
"""
plan — turn narration lines + scored candidates into a valid cut list.

The agent's remaining creative choice is small and checkable:

    input/<sku>/picks.json
    {"lines": [
       {"line": 0, "clip": "01_back_cameras.mp4", "candidate": "01c-a"},
       {"line": 1, "clip": "02_screen_faceid.mp4", "candidate": "02c-a"}
    ]}

Everything numeric is computed here:

1. **Lines** come from `output/<sku>/narration_lines.json` (written by the
   align stage), or are rebuilt from `script_text` + `word_timings.json`.
   Short lines are merged and long ones split at word boundaries until the edit
   has `cuts_per_video` units, each at least `min_cut_sec`.
2. **Timing**: cut lengths are the line lengths scaled to the voiceover and
   snapped to the measured beat grid (`beat_grid.json`); cuts run back-to-back
   from 0 s, so there is no gap for the agent to guess.
3. **Footage**: each line gets a candidate window whose `dur` fits the cut,
   preferring the highest score and never repeating the previous clip. Explicit
   `in`/`dur` overrides are still accepted, but they must pass
   `fenix validate`.

Without `picks.json` the planner rotates the best-scored candidates and records
that it auto-picked. Outputs:

    input/<sku>/edit_plan.json          (the renderer's contract)
    output/<sku>/plan_edit.json         (per-line → clip/candidate table)

Usage:
  fenix plan <sku> [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from pathlib import Path

from ..context import (DEFAULT_CONFIG, artifact, load_config, resolve_sku_dir)
from ..domain.narration import build_lines
from ..domain.quality import cached_scan, window_stats
from .beat_grid import load_grid


def load_lines(sku_dir: Path, sku: dict, timings: dict) -> tuple[dict, str]:
    path = artifact(sku_dir, "narration_lines.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), str(path)
    data = build_lines(sku.get("script_text", ""),
                       timings.get("words", []),
                       float(timings.get("duration_sec") or 0))
    return data, "rebuilt from script_text + word_timings.json"


def load_candidates(sku_dir: Path) -> tuple[dict | None, str]:
    path = sku_dir / "candidates.json"
    if not path.exists():
        return None, f"missing {path} (run `fenix frames <sku>`)"
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def split_units(lines: list[dict], words: list[dict], keep: int,
                min_cut: float) -> list[dict]:
    """Merge short lines / split long ones until there are exactly `keep` units."""
    units = [dict(ln, src_id=ln["id"]) for ln in lines]

    def length(u: dict) -> float:
        return float(u["t1"]) - float(u["t0"])

    # too many lines -> merge the adjacent pair with the smallest combined length
    while len(units) > keep:
        best = min(range(len(units) - 1),
                   key=lambda i: length(units[i]) + length(units[i + 1]))
        a, b = units[best], units[best + 1]
        merged = {
            "id": f"{a['src_id']}-{b['src_id']}",
            "text": f"{a['text']} {b['text']}",
            "t0": a["t0"], "t1": b["t1"], "kind": a["kind"],
            "src_id": a["src_id"],
            "w0": a.get("w0") if a.get("w0") is not None else b.get("w0"),
            "w1": b.get("w1") if b.get("w1") is not None else a.get("w1"),
        }
        units[best:best + 2] = [merged]

    # too few lines -> split the longest unit at a word boundary, halves >= min_cut
    guard = 0
    while len(units) < keep and guard < 64:
        guard += 1
        order = sorted(range(len(units)), key=lambda i: length(units[i]), reverse=True)
        split_done = False
        for idx in order:
            u = units[idx]
            if length(u) < 2 * min_cut:
                continue
            w0, w1 = u.get("w0"), u.get("w1")
            if w0 is None or w1 is None or w1 - w0 < 2:
                continue
            mid = (w0 + w1) // 2
            # widen around a silence if the words overlap it
            a_end = float(words[mid - 1]["e"])
            b_start = float(words[mid]["s"])
            if a_end - float(u["t0"]) < min_cut or float(u["t1"]) - b_start < min_cut:
                continue
            left = dict(u, id=f"{u['src_id']}a", t1=round(a_end, 3),
                        w1=mid, text=" ".join(x["w"] for x in words[w0:mid]))
            right = dict(u, id=f"{u['src_id']}b", t0=round(b_start, 3),
                         w0=mid, text=" ".join(x["w"] for x in words[mid:w1]))
            units[idx:idx + 1] = [left, right]
            split_done = True
            break
        if not split_done:
            break

    # duration from word spans (gaps between lines collapse)
    for u in units:
        u["dur"] = round(max(0.05, float(u["t1"]) - float(u["t0"])), 3)
    return units


def beat_bounds(lengths: list[float], total: float, grid: dict | None,
                min_cut: float) -> list[float]:
    """Cumulative cut boundaries, every interior boundary *on* a measured beat.

    Snapping a proportional boundary and then clamping it to `total - min_cut`
    is what pushed cuts 200 ms off the grid in the first planner run: the clamp
    overrode the snap. Here the choice is made in *beat indices*, so a minimum
    cut of `k` beats means index gaps of `k`, and every boundary is exactly a
    beat time. With no grid the proportional fallback keeps the same contract.
    """
    n = len(lengths)
    scale = total / max(1e-6, sum(lengths))
    targets: list[float] = []
    acc = 0.0
    for length in lengths[:-1]:
        acc += length * scale
        targets.append(acc)
    if not targets:
        return [0.0, round(total, 3)]

    beats = [b for b in (grid or {}).get("beats") or []
             if min_cut - 1e-6 <= b <= total - min_cut + 1e-6]
    if not beats:
        bounds = [0.0] + targets + [total]
        for i in range(1, n):
            upper = total - min_cut * (n - i)
            bounds[i] = round(max(bounds[i - 1] + min_cut, min(bounds[i], upper)), 3)
        return bounds

    beat_sec = float((grid or {}).get("beat_sec") or 0.0)
    if beat_sec <= 0:  # fall back to the measured median gap
        gaps = sorted(b - a for a, b in zip(beats, beats[1:])) or [min_cut]
        beat_sec = gaps[len(gaps) // 2]
    min_gap = max(1, math.ceil(min_cut / beat_sec - 1e-9))
    room = len(beats) - 1  # last usable index

    picks: list[int] = []
    for j, target in enumerate(targets):
        lo = picks[-1] + min_gap if picks else 0
        # every later boundary also needs `min_gap` beats of its own
        hi = room - min_gap * (len(targets) - 1 - j)
        k = bisect.bisect_left(beats, target)
        picks.append(max(lo, min(k, hi)))

    return [0.0] + [round(beats[k], 3) for k in picks] + [round(total, 3)]


SUSTAIN_FLOOR = 0.62   # mean ratio a whole shot must keep (in-point floor: 0.60)
SOFT_MAX = 0.30        # share of a shot allowed below the in-point floor
REUSE_PENALTY = 0.85   # per repeat: a good repeated window beats a soft fresh one
MAX_REUSE = 2          # the same in-point may appear at most twice


def pick_window(clip_name: str, cut_dur: float, cand: dict,
                used: dict[str, int], prof: dict | None = None,
                floor: float = 0.60, strict: bool = True) -> dict | None:
    """Best window for a `cut_dur` shot, scored over the *whole* shot.

    The candidate's own score is `min(ratio, window_ratio) x motion`, measured
    over whatever window it was built with — so using it for a 2 s cut said
    nothing about that 2 s. Here every window is re-scored on the actual cut
    inside the source (sustained mean ratio, soft share, motion), a window
    whose shot is mostly below the floor is rejected, and a reused window only
    pays a small penalty instead of forcing the pass to a soft fresh one.
    """
    info = (cand.get("clips") or {}).get(clip_name)
    if not info:
        return None
    options: list[dict] = []
    for w in info["windows"]:
        t = float(w["t"])
        if t + cut_dur > float(info["duration_sec"]) - 0.02:
            continue  # the source has no room for this cut from here
        item = dict(w, clip=clip_name, extended=w["dur"] < cut_dur - 1e-6)
        if prof and prof.get("samples"):
            st = window_stats(prof, t, cut_dur, floor)
            item["sustained"] = st
            if st["n"] >= 3 and strict and (
                    st["mean_ratio"] < SUSTAIN_FLOOR or st["soft_frac"] > SOFT_MAX):
                continue
            rank = (st["mean_ratio"] * (1.0 - 0.6 * st["soft_frac"])
                    * math.exp(-st["mean_motion"] / 25.0))
        else:  # no profile available: fall back to the candidate's own score
            rank = float(w["score"])
        repeats = int(used.get(w["id"], 0))
        if strict and repeats >= MAX_REUSE:
            continue  # a third pass over the same frames reads as a freeze
        rank *= REUSE_PENALTY ** repeats
        options.append(dict(item, rank=round(rank, 4)))
    if not options:
        return None
    return max(options, key=lambda o: o["rank"])


def plan(sku_dir: Path, cfg: dict, dry_run: bool) -> dict:
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    timings_path = sku_dir / "word_timings.json"
    if not timings_path.exists():
        sys.exit(f"[ERROR] missing {timings_path}; run `fenix align <sku>` first")
    timings = json.loads(timings_path.read_text(encoding="utf-8"))
    words = timings.get("words", [])
    total = float(timings.get("duration_sec") or cfg.get("target_duration_sec", 28.0))

    lines_doc, lines_src = load_lines(sku_dir, sku, timings)
    if not lines_doc.get("lines"):
        sys.exit("[ERROR] no narration lines: write script_text and run "
                 "fenix align")
    cand, cand_src = load_candidates(sku_dir)
    keep = int(cfg.get("cuts_per_video", 16))
    q_floor = float((cfg.get("quality") or {}).get("in_point_floor", 0.60))
    min_cut = float(cfg.get("min_cut_sec", 1.2))
    units = split_units(lines_doc["lines"], words, keep, min_cut)

    picks_path = sku_dir / "picks.json"
    picks: dict[int, dict] = {}
    if picks_path.exists():
        data = json.loads(picks_path.read_text(encoding="utf-8"))
        for item in data.get("lines", []):
            try:
                picks[int(item["line"])] = item
            except (KeyError, TypeError, ValueError):
                sys.exit(f"[ERROR] picks.json entry has no integer 'line': {item}")

    bounds = beat_bounds([u["dur"] for u in units], total,
                         load_grid(sku_dir), min_cut)

    clip_names = sorted(cand["clips"]) if cand else []
    profiles = {name: cached_scan(sku_dir / "clips" / name)
                for name in clip_names if (sku_dir / "clips" / name).exists()}
    used: dict[str, int] = {}
    usage = {c: 0 for c in clip_names}
    cuts: list[dict] = []
    notes: list[str] = []
    prev_clip = None
    for i, (unit, t0, t1) in enumerate(zip(units, bounds, bounds[1:])):
        cut_dur = round(t1 - t0, 3)
        pick = picks.get(unit["src_id"])
        chosen = None
        if pick:
            cid = pick.get("candidate")
            clip = pick.get("clip")
            for name, info in (cand["clips"].items() if cand else []):
                for w in info["windows"]:
                    if w["id"] == cid:
                        chosen = dict(w, clip=name)
            if chosen is None:
                sys.exit(f"[ERROR] picks.json line {unit['src_id']}: candidate "
                         f"{cid!r} is not in candidates.json")
            if clip and clip != chosen["clip"]:
                notes.append(f"line {unit['src_id']}: pick clip {clip} disagreed "
                             f"with candidate {cid} ({chosen['clip']}); used the "
                             f"candidate's clip")
            if chosen["dur"] < cut_dur - 1e-6:
                sys.exit(f"[ERROR] picks.json line {unit['src_id']}: candidate "
                         f"{cid} is {chosen['dur']:.2f}s but the cut is "
                         f"{cut_dur:.2f}s; pick a longer window")
        else:
            # score every clip, not the first that fits: variety is a mild
            # penalty on repeated use, never a reason to take a soft window
            order = sorted(clip_names, key=lambda c: (usage[c], c))
            for strict in (True, False):
                options = []
                for name in order:
                    if name == prev_clip and len(order) > 1:
                        continue
                    w = pick_window(name, cut_dur, cand or {}, used,
                                    profiles.get(name), q_floor, strict)
                    if w:
                        options.append(dict(w, rank=w["rank"]
                                            / (1.0 + 0.15 * usage[name])))
                if options:
                    chosen = max(options, key=lambda o: o["rank"])
                    break
                # last resort: allow the previous clip again
                for name in order:
                    w = pick_window(name, cut_dur, cand or {}, used,
                                    profiles.get(name), q_floor, False)
                    if w:
                        options.append(dict(w, rank=w["rank"]
                                            / (1.0 + 0.15 * usage[name])))
                if options:
                    chosen = max(options, key=lambda o: o["rank"])
                    break
        if chosen is None:
            sys.exit(f"[ERROR] no candidate window fits a {cut_dur:.2f}s cut "
                     f"(line {unit['src_id']}); rerun `fenix frames <sku>` or "
                     f"lower cuts_per_video")
        used[chosen["id"]] = used.get(chosen["id"], 0) + 1
        usage[chosen["clip"]] += 1
        prev_clip = chosen["clip"]
        cuts.append({
            "line": unit["src_id"],
            "text": unit["text"],
            "kind": unit["kind"],
            "clip": chosen["clip"],
            "candidate": chosen["id"],
            "in": round(float(chosen["t"]), 3),
            "dur": cut_dur,
            "t0": t0,
            "t1": t1,
            "score": chosen.get("rank", chosen.get("score")),
            "sharpness_ratio": chosen.get("ratio"),
            "sustained": chosen.get("sustained"),
        })

    # last cut absorbs rounding so the timeline ends exactly with the voice
    if cuts:
        cuts[-1]["dur"] = round(total - cuts[-1]["t0"], 3)
        cuts[-1]["t1"] = round(total, 3)

    result = {
        "sku": sku_dir.name,
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "narration_source": lines_src,
        "candidates_source": cand_src,
        "picks": str(picks_path) if picks else "auto",
        "cuts_per_video": len(cuts),
        "total_sec": round(total, 3),
        "checks": {
            "every_line_has_cut": len(cuts) == len(units),
            "all_candidates_known": all(c["candidate"] for c in cuts),
            "min_cut_sec_ok": all(c["dur"] >= min_cut - 0.02 for c in cuts),
            "clip_variety_ok": all(a["clip"] != b["clip"]
                                   for a, b in zip(cuts, cuts[1:])),
        },
        "notes": notes,
        "cuts": cuts,
    }
    result["pass"] = all(result["checks"].values())

    if not dry_run:
        plan_path = sku_dir / "edit_plan.json"
        prev = plan_path.with_name("edit_plan.prev.json")
        if plan_path.exists() and prev != plan_path:
            old = plan_path.read_text(encoding="utf-8")
            if json.dumps(json.loads(old), sort_keys=True) != \
                    json.dumps({"cuts": [{k: c[k] for k in ("clip", "in", "dur")}
                                         for c in cuts]}, sort_keys=True):
                prev.write_text(old, encoding="utf-8")
        plan_path.write_text(json.dumps({
            "sku": sku_dir.name,
            "generated_by": "fenix plan",
            "narration_lines": len(units),
            "cuts": [{k: c[k] for k in ("line", "text", "kind", "clip",
                                        "candidate", "in", "dur", "t0")}
                     for c in cuts],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        out = artifact(sku_dir, "plan_edit.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Plan a Fenixsoft edit from "
                                                 "narration lines + candidates")
    parser.add_argument("sku_dir", help="SKU name (or input/<sku> path)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan without writing edit_plan.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    sku_dir = resolve_sku_dir(args.sku_dir)
    result = plan(sku_dir, cfg, args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[plan] {result['cuts_per_video']} cuts, "
              f"{result['total_sec']:.2f}s; narration from "
              f"{result['narration_source']}")
        print(f"{'#':>2} {'line':>6} {'t0':>6} {'dur':>5} {'clip':<24} "
              f"{'cand':>6} {'score':>5} {'shot':>5} {'soft':>5}  text")
        for i, c in enumerate(result["cuts"]):
            st = c.get("sustained") or {}
            print(f"{i:>2} {str(c['line']):>6} {c['t0']:>6.2f} {c['dur']:>5.2f} "
                  f"{c['clip']:<24} {c['candidate']:>6} "
                  f"{(c['score'] or 0):>5.2f} {st.get('mean_ratio', 0):>5.2f} "
                  f"{st.get('soft_frac', 0):>5.2f}  {c['text'][:40]}")
        for note in result["notes"]:
            print(f"[plan] note: {note}")
        for name, ok in result["checks"].items():
            print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"\nRESULT: {'PASS' if result['pass'] else 'FAIL'}")
        if not args.dry_run:
            print(f"[plan] wrote {sku_dir / 'edit_plan.json'}")
    sys.exit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
