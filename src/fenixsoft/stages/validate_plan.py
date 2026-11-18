#!/usr/bin/env python3
"""
validate — catch a bad cut list in one second, not after a render.

The renderer used to be the only validator: an unknown clip or an impossible
shot failed after normalising every clip and building the audio mix (~2-3
minutes), and soft in-points or off-beat cuts were not checked at all.

This validates `input/<sku>/edit_plan.json` against the clips, the voiceover
timing, the measured beat grid and the measured sharpness profile:

  * schema / unknown clips / negative or non-numeric times
  * in + dur past the end of the source clip
  * cuts shorter than `min_cut_sec`
  * total shorter/longer than the voiceover
  * in-point sharpness below `quality.in_point_floor` of the clip median
  * cuts off the real beat (warn at `beat_offset_warn_ms`, fail at `fail_ms`)

Every row is {id, measured, threshold, pass, severity, fix}; the report goes to
`output/<sku>/plan_validation.json`. Exit 1 on any error-row failure.

Usage:
    fenix validate <sku> [--strict] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..context import DEFAULT_CONFIG, artifact, load_config, resolve_sku_dir
from ..domain import candidates as cand_mod
from ..domain.quality import (cached_scan, frame_sharpness, picture_driven,
                              probe_duration, window_stats)
from .beat_grid import load_grid, nearest_beat

DEFAULTS = {
    "in_point_floor": 0.60,
    "beat_offset_warn_ms": 60.0,
    "beat_offset_fail_ms": 120.0,
}


def quality_cfg(cfg: dict) -> dict:
    q = dict(DEFAULTS)
    q.update(cfg.get("quality") or {})
    return q


def clip_duration(path: Path) -> float:
    return probe_duration(path)


def row(rid: str, measured, threshold, ok: bool, fix: str,
        severity: str = "error") -> dict:
    return {"id": rid, "measured": measured, "threshold": threshold,
            "pass": bool(ok), "severity": severity, "fix": fix}


def validate(sku_dir: Path, cfg: dict) -> dict:
    q = quality_cfg(cfg)
    plan_path = sku_dir / "edit_plan.json"
    rows: list[dict] = []
    if not plan_path.exists():
        return {"sku": sku_dir.name, "pass": False, "checks": [
            row("plan_exists", "missing", str(plan_path), False,
                "write input/<sku>/edit_plan.json with a 'cuts' list; without "
                "it the renderer auto-plans and ignores your shot choices")]}

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"sku": sku_dir.name, "pass": False, "checks": [
            row("plan_parse", f"line {exc.lineno}", "valid JSON", False,
                f"fix the JSON at line {exc.lineno}: {exc.msg}")]}

    raw = plan.get("cuts")
    if not isinstance(raw, list) or not raw:
        return {"sku": sku_dir.name, "pass": False, "checks": [
            row("plan_schema", type(raw).__name__, "'cuts' list", False,
                "edit_plan.json needs {\"cuts\": [{\"clip\", \"in\", \"dur\"}, ...]}")]}
    rows.append(row("plan_parse", f"{len(raw)} cuts", "non-empty list", True, ""))

    clips_dir = sku_dir / "clips"
    durations = {p.name: clip_duration(p) for p in clips_dir.glob("*.mp4")}

    cand = cand_mod.load(sku_dir)
    unknown, bad_schema, out_of_range, too_short = [], [], [], []
    unknown_cand: list[tuple[int, str]] = []
    cuts: list[dict] = []
    total = 0.0
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            bad_schema.append(i)
            continue
        clip = item.get("clip")
        cid = item.get("candidate")
        win = None
        if cid:
            win = cand_mod.find(cand, str(cid))
            if win is None:
                unknown_cand.append((i, str(cid)))
                continue
            if not clip:
                clip = win["clip"]
        if not isinstance(clip, str) or not clip:
            bad_schema.append(i)
            continue
        try:
            in_pt = float(item["in"]) if item.get("in") is not None else \
                (float(win["t"]) if win else 0.0)
            dur = float(item["dur"]) if item.get("dur") is not None else \
                float(item.get("t1")) - float(item.get("t0", 0.0))
        except (TypeError, ValueError, KeyError):
            bad_schema.append(i)
            continue
        if clip not in durations:
            unknown.append((i, clip))
            continue
        if in_pt < 0 or in_pt + dur > durations[clip] + 0.02:
            out_of_range.append((i, clip, in_pt, dur, durations[clip]))
        if dur < float(cfg.get("min_cut_sec", 1.2)) - 1e-6:
            too_short.append((i, round(dur, 2)))
        cuts.append({"index": i, "clip": clip, "in": in_pt, "dur": dur,
                     "t0": round(total, 3), "candidate": cid})
        total += dur

    rows.append(row(
        "cut_schema", f"{len(bad_schema)} bad entr{'y' if len(bad_schema) == 1 else 'ies'}",
        "clip:str, in:number>=0, dur:number>0", not bad_schema,
        f"fix cut(s) {bad_schema[:8]}: each needs a string 'clip' and numeric "
        f"'in'/'dur'"))
    rows.append(row(
        "clip_known", f"{len(unknown)} unknown", "every clip exists in clips/",
        not unknown,
        "; ".join(f"cut {i}: {c!r} is not in {clips_dir.name}/"
                  for i, c in unknown[:6]) or ""))
    rows.append(row(
        "candidate_known", f"{len(unknown_cand)} unknown",
        "every candidate ID exists in candidates.json", not unknown_cand,
        "; ".join(f"cut {i}: {c!r} not found; run `fenix frames <sku>` or pick "
                  f"from candidates.json" for i, c in unknown_cand[:6]) or ""))
    if cand is None:
        rows.append(row("candidates_manifest", "missing candidates.json",
                        "scored windows available", True,
                        "run `fenix frames <sku>` to get "
                        "candidate IDs the planner can verify", "warn"))
    rows.append(row(
        "in_range", f"{len(out_of_range)} out of range",
        "0 <= in and in+dur <= source",
        not out_of_range,
        "; ".join(f"cut {i}: {c} in={a:.2f}+{b:.2f} > {d:.2f}"
                  for i, c, a, b, d in out_of_range[:6]) or ""))
    rows.append(row(
        "min_cut", f"min {min((c['dur'] for c in cuts), default=0):.2f}s",
        f">= {cfg.get('min_cut_sec', 1.2):.2f}s", not too_short,
        f"cuts {[i for i, _ in too_short][:8]} are shorter than a beat; merge "
        f"them into a neighbour" if too_short else ""))

    # total vs voiceover / target
    timings_path = sku_dir / "word_timings.json"
    vo = None
    if timings_path.exists():
        vo = float(json.loads(timings_path.read_text(encoding="utf-8"))
                   .get("duration_sec") or 0) or None
    target = float(cfg.get("target_duration_sec", 28.0))
    rows.append(row(
        "total_vs_vo", f"{total:.2f}s cuts / {vo:.2f}s VO" if vo else f"{total:.2f}s cuts",
        "cuts <= voiceover", True if vo is None else total <= vo + 0.01,
        "shorten the last shot or the script; the renderer extends the final "
        "cut to the voiceover length", "warn" if vo is None else "error"))
    rows.append(row(
        "duration_target", f"{total:.2f}s", f"<= {target + 0.5:.1f}s",
        total <= target + 0.5,
        f"cut {total - target:.2f}s of timeline or shorten the script "
        f"(fenix lint computes the word budget)", "warn"))

    # in-point sharpness against each clip's own median
    soft: list[dict] = []
    medians: dict[str, float] = {}
    for c in cuts:
        if c["clip"] not in medians:
            medians[c["clip"]] = cached_scan(clips_dir / c["clip"])["median"]
        med = medians[c["clip"]] or 0.0
        score = frame_sharpness(clips_dir / c["clip"], c["in"])
        c["sharpness"] = round(score, 2)
        c["sharpness_ratio"] = round(score / med, 3) if med else 0.0
        if c["sharpness_ratio"] < q["in_point_floor"]:
            best = best_window(clips_dir / c["clip"], c["dur"])
            c["suggested_in"] = best
            soft.append(c)
    floor = q["in_point_floor"]
    rows.append(row(
        "in_point_floor",
        f"min {min((c['sharpness_ratio'] for c in cuts), default=0):.2f}x",
        f">= {floor:.2f}x clip median", not soft,
        "; ".join(f"cut {c['index']} ({c['clip']} @ {c['in']:.2f}s) is "
                  f"{c['sharpness_ratio']:.2f}x; try in={c.get('suggested_in')}"
                  for c in soft[:6]) or ""))

    # sustained sharpness: a cut can open sharp and stay soft for the rest
    for c in cuts:
        prof = cached_scan(clips_dir / c["clip"])
        c["window"] = window_stats(prof, c["in"], c["dur"], floor)
    shots = [c["window"] for c in cuts]
    weak = [c for c in cuts if c["window"]["n"] >= 3
            and c["window"]["mean_ratio"] < floor]
    blurry = [c for c in cuts if c["window"]["n"] >= 3
              and c["window"]["soft_frac"] > 0.30]
    rows.append(row(
        "window_floor",
        f"min {min((w['mean_ratio'] for w in shots), default=0):.2f}x mean",
        f">= {floor:.2f}x over the whole shot", not weak,
        "; ".join(f"cut {c['index']} ({c['clip']} @ {c['in']:.2f}s) averages "
                  f"{c['window']['mean_ratio']:.2f}x; move the in-point"
                  for c in weak[:6]) or ""))
    rows.append(row(
        "window_soft", f"{sum(w['soft_frac'] > 0.30 for w in shots)}/{len(cuts)}"
        f" shots > 30% under floor",
        "each shot mostly above the floor", not blurry,
        "; ".join(f"cut {c['index']} ({c['clip']}) is {c['window']['soft_frac']:.0%}"
                  f" soft" for c in blurry[:6]) or "", "warn"))

    # beat alignment, when a measured grid exists
    grid = load_grid(sku_dir)
    picture_policy = picture_driven({"quality": q})
    if grid and grid.get("beats"):
        offsets = [abs(nearest_beat(c["t0"], grid["beats"])[1]) for c in cuts]
        worst = max(offsets) if offsets else 0.0
        if picture_policy:
            # Cinematic long takes cut on action. The offsets are still
            # measured and reported, but they cannot fail the plan.
            rows.append(row(
                "beat_offset",
                f"max {worst:.0f} ms (picture-driven: informational)",
                "quality.beat_policy = picture", True,
                "cuts follow the picture; set quality.beat_policy = music for "
                "a beat-cut piece", "info"))
            rows.append(row(
                "beat_offset_warn",
                f"{sum(o > q['beat_offset_warn_ms'] for o in offsets)}/"
                f"{len(offsets)} cuts > {q['beat_offset_warn_ms']:.0f} ms "
                f"(picture-driven: informational)",
                "quality.beat_policy = picture", True, "", "info"))
        else:
            rows.append(row(
                "beat_offset", f"max {worst:.0f} ms, mean {sum(offsets)/len(offsets):.0f} ms",
                f"<= {q['beat_offset_fail_ms']:.0f} ms hard, "
                f"{q['beat_offset_warn_ms']:.0f} ms warn",
                worst <= q["beat_offset_fail_ms"],
                f"the nearest cut is {worst:.0f} ms off the measured grid "
                f"({grid['bpm']:.2f} BPM); move cut times onto beat_grid.json "
                f"beats or accept the drift", "error"))
            rows.append(row(
                "beat_offset_warn", f"{sum(o > q['beat_offset_warn_ms'] for o in offsets)}"
                f"/{len(offsets)} cuts > {q['beat_offset_warn_ms']:.0f} ms",
                f"all <= {q['beat_offset_warn_ms']:.0f} ms",
                not any(o > q["beat_offset_warn_ms"] for o in offsets),
                "snap each cut to the nearest measured beat", "warn"))
    else:
        rows.append(row("beat_offset", "no beat_grid.json",
                        "measured grid available", True,
                        "run `python beat_grid.py input/<sku>`; without it the "
                        "renderer falls back to the config BPM", "warn"))

    # variety
    repeats = [c for a, c in zip(cuts, cuts[1:]) if a["clip"] == c["clip"]]
    rows.append(row("cut_variety", f"{len(cuts) - len(repeats)}/{len(cuts)} fresh",
                    "no repeated clip back-to-back", not repeats,
                    f"cut(s) {[c['index'] for c in repeats][:8]} repeat the "
                    f"previous clip; insert another angle", "warn"))

    failed = [r for r in rows if not r["pass"] and r["severity"] == "error"]
    return {
        "sku": sku_dir.name,
        "pass": not failed,
        "cuts": cuts,
        "total_sec": round(total, 3),
        "in_point_floor": floor,
        "checks": rows,
    }


def best_window(path: Path, dur: float) -> float:
    """Sharpest in-point that still fits `dur` seconds of the clip."""
    prof = cached_scan(path)
    if not prof["samples"]:
        return 0.0
    fitting = [(t, s) for t, s in prof["samples"]
               if t + dur <= prof["duration_sec"] - 0.02]
    if not fitting:
        return 0.0
    return round(max(fitting, key=lambda x: x[1])[0], 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate a Fenixsoft edit plan")
    parser.add_argument("sku_dir", help="SKU name (or input/<sku> path)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--strict", action="store_true",
                        help="warnings fail too")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sku_dir = resolve_sku_dir(args.sku_dir)
    cfg = load_config(args.config, project=sku_dir)
    report = validate(sku_dir, cfg)
    if args.strict:
        report["pass"] = all(r["pass"] for r in report["checks"])

    out = artifact(sku_dir, "plan_validation.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        width = max(len(r["id"]) for r in report["checks"]) + 2
        for r in report["checks"]:
            mark = "PASS" if r["pass"] else ("WARN" if r["severity"] == "warn"
                                             else "FAIL")
            print(f"[{mark}] {r['id']:<{width}} {str(r['measured']):<34} "
                  f"(limit {r['threshold']})")
            if not r["pass"] and r.get("fix"):
                print(f"        fix: {r['fix']}")
        errs = [r for r in report["checks"] if not r["pass"] and r["severity"] == "error"]
        print(f"\nRESULT: {'PASS' if report['pass'] else 'FAIL'} "
              f"({len(errs)} error(s))")
        print(f"[validate] wrote {out}")
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
