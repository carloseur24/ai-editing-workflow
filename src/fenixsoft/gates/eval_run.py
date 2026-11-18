#!/usr/bin/env python3
"""
eval_run — golden-SKU regression: render, measure, diff against a baseline.

Nothing detected a re-render getting *worse*. This runs the pipeline for one
SKU, collects every measurable property of the result, compares it with a
stored baseline and fails on regressions. The perceptual diff (SSIM) catches
frame-level changes that metrics alone miss.

    fenix eval demo_iphone14_blue                  # render + compare
    fenix eval demo_iphone14_blue --update-baseline
    fenix eval <sku> --run output/<sku>/<run>
    fenix eval <sku> --baseline <old_run_mp4>

Baseline file: `eval/<sku>_baseline.json`
History file:  `eval/<sku>_<YYYYmmdd-HHMMSS>.json`

Usage flags:
  --skip-align     reuse word_timings.json
  --skip-render    evaluate the newest existing run
  --strict         treat metric warnings as regressions
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from ..context import (EVAL_DIR, REPO_ROOT, python_cmd, resolve_sku_dir,
                       subprocess_env)
from ..domain import runs
from ..domain.quality import cached_scan, window_stats

# metric -> direction: "up"/"down" = better on change, "same" = any change is
# a warning, "floor" = gated against an absolute floor, "info" = reported only
DIRECTION = {
    "duration_sec": "same",
    "cuts": "same",
    "size_mb": "down",
    "in_point_min_ratio": "floor",
    "in_point_mean_ratio": "info",
    "window_min_ratio": "floor",
    "window_mean_ratio": "up",
    "window_soft_frac": "down",
    "beat_max_offset_ms": "down",
    "beat_mean_offset_ms": "down",
    "caption_max_cps": "down",
    "hyperframes": "info",
    "caption_mean_cps": "down",
    "lufs": "same",
    "true_peak_db": "down",
    "warnings": "down",
    "qa_errors": "down",
    "qa_warnings": "down",
    "clip_identity_mismatches": "down",
    "ssim_vs_baseline": "up",
}

# metrics that are gates, not scores: they regress only when below the floor
FLOOR = {
    "in_point_min_ratio": 0.60,
    "window_min_ratio": 0.60,
}

# absolute tolerance before a difference counts (None = relative 2 %)
TOLERANCE = {
    "duration_sec": 0.15,
    "cuts": 0,
    "size_mb": 0.35,
    "in_point_min_ratio": 0.02,
    "in_point_mean_ratio": 0.02,
    "window_min_ratio": 0.02,
    "window_mean_ratio": 0.05,
    "window_soft_frac": 0.03,
    "beat_max_offset_ms": 10.0,
    "beat_mean_offset_ms": 5.0,
    "caption_max_cps": 1.0,
    "caption_mean_cps": 0.5,
    "lufs": 0.25,
    "true_peak_db": 0.25,
    "warnings": 0,
    "qa_errors": 0,
    "qa_warnings": 0,
    "clip_identity_mismatches": 0,
    "ssim_vs_baseline": 0.002,
}


def _num(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group(0)) if m else None


def window_metrics(plan: dict, run_dir: Path) -> tuple[dict, list[dict]]:
    """Re-measure every cut over its whole window (works on old plans too)."""
    clips = runs.input_dir(runs.sku_of_run(run_dir)) / "clips"
    per: list[dict] = []
    for c in plan.get("cuts") or []:
        path = clips / str(c.get("clip"))
        if not path.exists():
            continue
        st = window_stats(cached_scan(path), float(c["in"]), float(c["dur"]))
        if st["n"]:
            per.append({"index": c.get("index"), "clip": c.get("clip"),
                        "in": c.get("in"), "dur": c.get("dur"),
                        "in_point_ratio": c.get("sharpness_ratio"), **st})
    if not per:
        return {}, []
    means = [row["mean_ratio"] for row in per]
    return {
        "window_min_ratio": round(min(means), 3),
        "window_mean_ratio": round(sum(means) / len(means), 3),
        "window_soft_frac": round(
            sum(row["soft_frac"] for row in per) / len(per), 3),
    }, per


def collect(run_dir: Path) -> dict:
    plan_path = run_dir / "edit_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    qa_path = run_dir / "qa_report.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else {}
    rows = {c["id"]: c for c in qa.get("checks", [])}
    quality = plan.get("quality") or {}
    beat = plan.get("beat") or {}
    metrics = {
        "duration_sec": plan.get("duration_sec"),
        "cuts": len(plan.get("cuts") or []),
        "size_mb": plan.get("size_mb"),
        "in_point_min_ratio": quality.get("in_point_min_ratio"),
        "in_point_mean_ratio": quality.get("in_point_mean_ratio"),
        "beat_max_offset_ms": beat.get("max_cut_offset_ms"),
        "beat_mean_offset_ms": beat.get("mean_cut_offset_ms"),
        "caption_max_cps": quality.get("caption_max_cps"),
        "caption_mean_cps": quality.get("caption_mean_cps"),
        "lufs": _num(rows.get("a_lufs", {}).get("measured")),
        "true_peak_db": _num(rows.get("a_true_peak", {}).get("measured")),
        "warnings": len(plan.get("warnings") or []),
        "qa_errors": qa.get("errors"),
        "qa_warnings": qa.get("warnings"),
        "qa_pass": qa.get("pass"),
    }
    win_metrics, win_rows = window_metrics(plan, run_dir)
    metrics.update(win_metrics)
    if win_rows:
        metrics["_cut_quality"] = win_rows
    ident = rows.get("c_clip_identity", {})
    if ident:
        measured = str(ident.get("measured", ""))
        m = re.match(r"(\d+)/(\d+)", measured)
        metrics["clip_identity_mismatches"] = (
            int(m.group(2)) - int(m.group(1)) if m else None)
    return {k: v for k, v in metrics.items() if v is not None}


def probe_duration(video: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(video)]).decode().strip()
    return float(out or 0)


def ssim(a: Path, b: Path) -> float | None:
    """SSIM over the common duration; None when the comparison is impossible."""
    try:
        dur = min(probe_duration(a), probe_duration(b))
        if dur <= 0.5:
            return None
        proc = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(a), "-i", str(b),
            "-lavfi", f"[0:v]trim=0:{dur:.3f},setpts=PTS-STARTPTS[x];"
                      f"[1:v]trim=0:{dur:.3f},setpts=PTS-STARTPTS[y];"
                      f"[x][y]ssim",
            "-f", "null", "-"], capture_output=True, text=True)
        m = re.search(r"All:([0-9.]+)", proc.stderr)
        return float(m.group(1)) if m else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def run_step(cmd: list[str]) -> bool:
    print("  $ " + " ".join(cmd[1:]))
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=subprocess_env())
    return proc.returncode == 0


def compare(cur: dict, base: dict, strict: bool) -> list[dict]:
    diffs: list[dict] = []
    for key, direction in DIRECTION.items():
        if key == "ssim_vs_baseline" or key not in cur or key not in base:
            continue
        a, b = cur[key], base[key]
        if a is None or b is None:
            continue
        if direction == "info":
            diffs.append({"metric": key, "baseline": b, "current": a,
                          "delta": round(a - b, 4), "status": "info",
                          "severity": "ok"})
            continue
        if direction == "floor":
            ok = a >= FLOOR.get(key, 0.0) - 1e-9
            diffs.append({"metric": key, "baseline": b, "current": a,
                          "delta": round(a - b, 4),
                          "status": "ok" if ok else "regressed",
                          "severity": "ok" if ok else "error"})
            continue
        delta = a - b
        tol = TOLERANCE.get(key, max(abs(b) * 0.02, 1e-9))
        if abs(delta) <= tol:
            status = "same"
        elif (direction == "up" and delta > 0) or \
                (direction == "down" and delta < 0):
            status = "better"
        elif direction == "same":
            status = "changed"
        else:
            status = "regressed"
        diffs.append({"metric": key, "baseline": b, "current": a,
                      "delta": round(delta, 4), "status": status,
                      "severity": ("error" if status == "regressed" else
                                   "warn" if status == "changed" else "ok")})
    return diffs


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Golden-SKU regression run")
    ap.add_argument("sku_dir", type=Path)
    ap.add_argument("--skip-align", action="store_true")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--run", type=Path, default=None,
                    help="evaluate this existing run directory")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="baseline run dir or MP4 (default: eval/<sku>_baseline.json)")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sku_dir = resolve_sku_dir(args.sku_dir)
    sku = sku_dir.name
    t0 = time.monotonic()

    if not args.skip_render:
        steps: list[list[str]] = []
        if not args.skip_align:
            steps.append(python_cmd("align", str(sku_dir)))
        steps.append(python_cmd("render", str(sku_dir)))
        for cmd in steps:
            if not run_step(cmd):
                sys.exit(f"[eval] step failed: {' '.join(cmd[2:4])}")
        run_dir = Path(runs.latest_run(sku)["dir"])
    elif args.run:
        run_dir = args.run
    else:
        latest = runs.latest_run(sku)
        if not latest:
            sys.exit(f"[eval] no runs for {sku}; render once first")
        run_dir = Path(latest["dir"])

    video = runs.run_video(run_dir)
    if not video.exists():
        sys.exit(f"[eval] missing video in {run_dir}")

    # QA gate (writes/refreshes qa_report.json for this run)
    qa_cmd = python_cmd("qa", str(video), "--strict" if args.strict else "--json")
    subprocess.run(qa_cmd, cwd=REPO_ROOT, env=subprocess_env(),
                   capture_output=True, text=True)

    cur = collect(run_dir)
    cut_quality = cur.pop("_cut_quality", [])

    # ---- baseline resolution
    baseline_path = EVAL_DIR / f"{sku}_baseline.json"
    base_metrics: dict | None = None
    base_video: Path | None = None
    if args.baseline:
        if args.baseline.is_dir():
            base_video = runs.run_video(args.baseline)
            base_metrics = collect(args.baseline)
            base_metrics.pop("_cut_quality", None)
        else:
            base_video = args.baseline
            meta = args.baseline.with_suffix(".json")
            if meta.exists():
                base_metrics = json.loads(meta.read_text(encoding="utf-8"))
    elif baseline_path.exists():
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        base_metrics = data.get("metrics")
        base_metrics = {k: v for k, v in (base_metrics or {}).items()
                        if k != "_cut_quality"}
        base_video = Path(data["video"]) if data.get("video") else None

    if base_video and base_video.exists():
        cur["ssim_vs_baseline"] = ssim(video, base_video)

    diffs = compare(cur, base_metrics, args.strict) if base_metrics else []
    regressions = [d for d in diffs if d["status"] == "regressed"]
    changed = [d for d in diffs if d["status"] == "changed"]
    passed = not regressions and (not args.strict or not changed)

    report = {
        "sku": sku,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run": run_dir.name,
        "video": str(video),
        "baseline_video": str(base_video) if base_video else None,
        "metrics": cur,
        "baseline_metrics": base_metrics,
        "cut_quality": cut_quality,
        "diffs": diffs,
        "pass": passed,
        "regressions": len(regressions),
        "elapsed_sec": round(time.monotonic() - t0, 1),
    }

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = EVAL_DIR / f"{sku}_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if args.update_baseline or base_metrics is None:
        (EVAL_DIR / f"{sku}_baseline.json").write_text(json.dumps({
            "sku": sku, "run": run_dir.name, "video": str(video),
            "created_at": report["generated_at"], "metrics": cur,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[eval] baseline {'updated' if base_metrics else 'created'}: "
              f"{EVAL_DIR / f'{sku}_baseline.json'}")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n[eval] run {run_dir.name}")
        print(f"{'metric':<26} {'baseline':>10} {'current':>10} {'delta':>9}  status")
        for d in diffs:
            print(f"{d['metric']:<26} {d['baseline']:>10.3f} "
                  f"{d['current']:>10.3f} {d['delta']:>+9.3f}  {d['status']}")
        for key, val in cur.items():
            if not any(d["metric"] == key for d in diffs):
                print(f"{key:<26} {'-':>10} {val:>10}  {'new' if base_metrics else 'baseline'}")
        if not base_metrics:
            print("[eval] no baseline existed: this run is now the baseline")
        print(f"\nRESULT: {'PASS' if passed else 'FAIL'} "
              f"({len(regressions)} regression(s), {len(changed)} changed)")
        print(f"[eval] wrote {report_path}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    sys.exit(main())
