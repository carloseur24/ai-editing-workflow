#!/usr/bin/env python3
"""
qa_check — automated delivery gate for a rendered Fenixsoft video.

Checks the things that actually break a WhatsApp/Reels delivery:
  * canvas, fps, pixel format, H.264 profile/level (4.2 is required for
    1080x1920@60 -- level 3.1 is invalid at this frame rate)
  * faststart (moov atom before mdat) so WhatsApp can stream it
  * duration vs. the voiceover, and the 15.5 MB WhatsApp budget
  * integrated loudness / true peak
  * no black or near-black frames at sampled timestamps
  * captions present in the word timings
  * content: each rendered cut correlated against its planned source frame,
    caption text / width / duplicates, whole-shot and in-point sharpness
  * per-run bookkeeping: the video must live in a timestamped run directory

Usage:  fenix qa output/<sku>/<run>/<run>_final.mp4 --strict
        fenix qa --latest <sku> --strict
Exit code 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..context import DEFAULT_CONFIG, REPO_ROOT, load_config
from ..domain import runs
from ..domain.quality import (ensure_normalized_clip, frame_crop_gray,
                              frame_similarity, normalized_clip_path,
                              picture_driven)
from ..domain.voice import explicit_duration, is_music_only

# Rows that only make sense when a voiceover exists. A music-only master is
# still checked for duration, loudness, peak, black frames, size and shot
# identity; there is simply no script, transcript or caption stream to judge.
VO_CHECK_IDS = {
    "c_timings", "d_script_match", "d_tail_complete",
    "c_cps_max", "c_cps_fail", "c_caption_text", "c_caption_zone",
    "c_caption_dupes", "c_caption_audit",
}


def strip_vo_checks(checks: list[dict], music_only: bool
                    ) -> tuple[list[dict], list[str]]:
    """Drop voice/caption rows for a music-only piece, append one note row."""
    if not music_only:
        return checks, []
    kept, skipped = [], []
    for check in checks:
        if check["id"] in VO_CHECK_IDS:
            skipped.append(check["id"])
        else:
            kept.append(check)
    kept.append({
        "id": "a_music_only", "check": "a_music_only",
        "measured": f"{len(skipped)} VO/script/caption gate(s) skipped",
        "threshold": "music-only piece: no voiceover to check",
        "ok": True, "detail": "music-only: no voiceover",
        "severity": "warn", "fix": "",
    })
    return kept, skipped


def probe(path: Path) -> dict:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=index,codec_type,codec_name,profile,level,width,height,"
        "r_frame_rate,pix_fmt,sample_rate,channels:format=duration,size,bit_rate",
        "-of", "json", str(path),
    ])
    return json.loads(out)


def audio_metrics(path: Path) -> tuple[float, float, np.ndarray]:
    """Loudness, true peak and float PCM from one decode.

    ``loudnorm`` and the clipping scan used to decode the whole file twice.
    One asplit graph gives both: the null sink consumes the measured stream and
    the raw f32 stream goes to stdout.
    """
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-filter_complex",
        "[0:a]asplit=2[ln][pcm];[ln]loudnorm=print_format=json[lo];[pcm]anull[po]",
        "-map", "[lo]", "-f", "null", "-",
        "-map", "[po]", "-f", "f32le", "-ac", "2", "-ar", "48000", "-",
    ], capture_output=True)
    text = proc.stderr.decode("utf-8", "replace")
    try:
        meas = json.loads(text[text.rfind("{"):text.rfind("}") + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not measure audio:\n{text[-600:]}") from exc
    pcm = np.frombuffer(proc.stdout, dtype=np.float32)
    return float(meas["input_i"]), float(meas["input_tp"]), pcm


def has_faststart(path: Path) -> bool:
    head = path.open("rb").read(2 * 1024 * 1024)
    moov, mdat = head.find(b"moov"), head.find(b"mdat")
    return moov != -1 and (mdat == -1 or moov < mdat)


def _luma_at(path: Path, tmp: Path, ts: float, index: int) -> float | None:
    out = tmp / f"f{index}.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}",
                    "-i", str(path), "-frames:v", "1", str(out)],
                   check=True)
    if not out.exists():
        return None
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-i", str(out),
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return float(np.frombuffer(raw, dtype=np.uint8).mean())


def frame_luma(path: Path, timestamps: list[float],
               workers: int = 4) -> list[float]:
    """Mean luma at N timestamps, sampled concurrently.

    Seven sequential ffmpeg seeks used to cost ~8 s of a 30 s QA run; the
    seeks are independent and subprocess-bound, so four at a time is free.
    """
    if not timestamps:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(timestamps)))) as pool:
            futures = [pool.submit(_luma_at, path, tmp_path, ts, i)
                       for i, ts in enumerate(timestamps)]
            values = [f.result() for f in futures]
    return [v for v in values if v is not None]


def _pill_rgb(video: Path, box: list[int]) -> tuple[int, int, int] | None:
    """Median colour of the price-pill interior (top padding, no glyphs).

    Crop dimensions are forced even: the source is yuv420p and FFmpeg rounds an
    odd width/height down to the chroma grid, so an odd crop returns fewer bytes
    than `w * h * 3` and used to be rejected as "could not sample the pill".
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    x, y = x0 + 12, y0 + 6
    w = max(4, (x1 - x0 - 24) // 2 * 2)
    h = max(4, (min(10, y1 - y0 - 12)) // 2 * 2)
    raw = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-ss", "1.0", "-i", str(video),
         "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    px = np.frombuffer(raw[: (len(raw) // 3) * 3], dtype=np.uint8).reshape(-1, 3)
    if px.shape[0] < 12:
        return None
    return tuple(int(v) for v in np.median(px, axis=0))


def _media_ref(value, sku: str) -> Path | None:
    """Resolve a comp spec path: repo-relative, absolute or a stable ref."""
    if not value:
        return None
    raw = str(value)
    if runs.is_stable_ref(raw):
        resolved = runs.resolve_stable_ref(raw, sku)
        return Path(resolved) if resolved else None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def post_report(video: Path, cfg: dict, *, strict: bool = False) -> dict:
    """Post-master profile: a comp/fx/grade master, not the 30-check edit gate.

    The edit gate judges the cut list, the voiceover and the captions; a
    post-master no longer has those inputs (the base is an encoded run). This
    profile checks what can still be wrong after a composite: container,
    duration, loudness/peak, black frames, byte budget and whether each base cut
    still shows the same picture.
    """
    sku_name, _sku_dir = runs.resolve_for_video(video)
    run_dir = video.parent
    comp_spec = _read_json(run_dir / "comp.json")
    manifest = _read_json(run_dir / "manifest.json")
    data = probe(video)
    streams = {s["codec_type"]: s for s in data.get("streams", [])}
    v, a = streams.get("video", {}), streams.get("audio", {})
    fmt = data.get("format", {})
    num, _, den = str(v.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1)
    duration = float(fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)

    canvas = cfg.get("canvas") or {}
    want_w = int(comp_spec.get("width") or canvas.get("width") or 1080)
    want_h = int(comp_spec.get("height") or canvas.get("height") or 1920)
    want_fps = float(comp_spec.get("fps") or canvas.get("fps") or 60)
    max_bytes = int((cfg.get("encoding") or {}).get("max_whatsapp_bytes",
                                                     16252928))
    target_lufs = float((cfg.get("audio") or {}).get("target_lufs", -14.0))

    checks: list[dict] = []

    def check(cid: str, ok: bool, measured, threshold, fix: str = "",
              severity: str = "error") -> None:
        checks.append({"id": cid, "check": cid, "measured": measured,
                       "threshold": threshold, "ok": bool(ok),
                       "detail": str(measured), "severity": severity,
                       "fix": fix})

    check("pm_manifest", bool(manifest),
          f"{manifest.get('layers', '?')} layer(s), "
          f"{manifest.get('duration_sec', '?')}s" if manifest
          else "missing manifest.json",
          "comp run manifest present", "run fenix comp so the run carries its "
          "executed spec and layer count", "warn")
    check("v_codec", v.get("codec_name") == "h264", str(v.get("codec_name")),
          "h264", "re-encode with the shared encoding block")
    check("v_resolution", (v.get("width"), v.get("height")) == (want_w, want_h),
          f"{v.get('width')}x{v.get('height')}", f"{want_w}x{want_h}",
          "render at the project canvas; a post master must not change size")
    check("v_fps", abs(fps - want_fps) < 0.01, f"{fps:.2f}", f"{want_fps:.0f}",
          "re-encode with the project fps")
    check("v_pix_fmt", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt")),
          "yuv420p", "WhatsApp/Reels need 4:2:0")
    check("v_profile", str(v.get("profile", "")).lower() == "main",
          str(v.get("profile")), "main", "set encoding.profile = main")
    check("v_level", str(v.get("level")) == "42", f"level={v.get('level')}",
          "4.2 (level 42)", "set encoding.level = 4.2")
    check("v_faststart", has_faststart(video), "moov before mdat", "faststart",
          "add -movflags +faststart")
    check("v_size_budget", size <= max_bytes,
          f"{size / 1024 / 1024:.2f} MB",
          f"<= {max_bytes / 1024 / 1024:.1f} MB",
          "re-encode at CRF 21 / 2.5 Mbps")

    expected = comp_spec.get("duration")
    base = _media_ref(comp_spec.get("base"), sku_name)
    base_plan = _read_json(base.parent / "edit_plan.json") if base else {}
    base_cuts = [c for c in (base_plan.get("cuts") or [])
                 if c.get("dur") is not None and c.get("t0") is not None]
    if expected is None and base_plan.get("duration_sec"):
        expected = base_plan.get("duration_sec")
    if expected is not None:
        check("pm_duration", abs(duration - float(expected)) < 0.35,
              f"{duration:.2f}s master / {float(expected):.2f}s spec",
              "within 0.35s", "re-run comp: the graph trimmed a different "
              "duration than the spec")
    check("q_run_versioned",
          bool(runs.RUN_RE.match(run_dir.name)) and video.suffix == ".mp4",
          f"{run_dir.name}/{video.name}", "<sku>_<YYYYmmdd-HHMMSS>/<stage>.mp4",
          "run the stage without --out so the result lands in its own run",
          "warn")
    check("pm_base", bool(base_cuts) or base is None,
          f"{len(base_cuts)} base cut(s)" if base_cuts
          else ("no base plan: identity compared on samples"
                if base else "base missing"),
          "base edit_plan.json readable", "point comp.json at @render/latest "
          "(or a run with edit_plan.json) so cut identity can be measured",
          "info")

    check("a_stream", bool(a) and a.get("codec_name") == "aac",
          f"{a.get('codec_name')} {a.get('sample_rate')} Hz {a.get('channels')} ch",
          "aac 48 kHz stereo", "set encoding.audio_codec = aac")
    lufs, tp, pcm = audio_metrics(video)
    check("a_lufs", abs(lufs - target_lufs) <= 1.0, f"{lufs:.2f} LUFS",
          f"{target_lufs:+.1f} +/- 1.0",
          "the base mix was re-encoded: check the comp audio args")
    check("a_true_peak", tp <= -1.0, f"{tp:.2f} dBTP", "<= -1.0 dBTP",
          "lower audio.limiter_ceiling in config/pipeline.json")
    clipped = int((np.abs(pcm) >= 0.999).sum())
    check("a_clipping", clipped == 0, f"{clipped} full-scale samples",
          "0", "reduce the master gain or the limiter ceiling")

    sample_ts = [duration * f for f in (0.02, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    lumas = frame_luma(video, sample_ts)
    darkest = min(lumas) if lumas else 0.0
    check("v_black_frames", darkest >= 12.0,
          f"darkest sampled mean luma {darkest:.1f}/255", ">= 12",
          "the composite is too dark; check the layer opacity/fades")

    if base is not None and base.exists():
        if base_cuts:
            points = [(i, float(c["t0"]) + float(c["dur"]) / 2.0)
                      for i, c in enumerate(base_cuts)]
        else:
            points = [(i, duration * (i + 0.5) / 5.0) for i in range(5)]

        def _compare(item):
            index, t_mid = item
            a_crop = frame_crop_gray(video, t_mid)
            b_crop = frame_crop_gray(base, t_mid)
            if a_crop is None or b_crop is None:
                return None
            sim = frame_similarity(a_crop, b_crop)
            return index, sim["corr"], sim["mad"]

        with ThreadPoolExecutor(max_workers=min(4, len(points))) as pool:
            results = [r for r in pool.map(_compare, points) if r is not None]
        if results:
            corrs = [r[1] for r in results]
            worst = min(corrs)
            label = "cut identity" if base_cuts else "sample identity"
            check("pm_cut_identity", worst >= 0.75,
                  f"{label}: min corr {worst:.2f} / mean "
                  f"{sum(corrs) / len(corrs):.2f} over {len(results)} frames",
                  "corr >= 0.75 (a layer must not replace the base shot)",
                  "a layer covers the base picture or the base changed; check "
                  "comp.json layers and the base ref")
            check("pm_cut_fidelity", worst >= 0.90,
                  f"{label}: min corr {worst:.2f}",
                  "corr >= 0.90 (the composite keeps the base picture)",
                  "inspect the layer opacity/key/mask; the base frame should "
                  "still dominate outside the layer", "warn")
        else:
            check("pm_cut_identity", False,
                  "no frames could be compared", "every base cut compared",
                  "the base video is unreadable; re-run comp", "warn")

    errors = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    warns = [c for c in checks if not c["ok"] and c["severity"] == "warn"]
    passed = not errors and (not warns or not strict)
    stat = video.stat()
    return {
        "video": str(video),
        "sku": sku_name,
        "run": run_dir.name,
        "profile": "post",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_sec": round(duration, 3),
        "size_mb": round(size / 1024 / 1024, 3),
        "video_bytes": stat.st_size,
        "video_mtime_ns": stat.st_mtime_ns,
        "base": str(base) if base else None,
        "layers": manifest.get("layers"),
        "strict": strict,
        "pass": passed,
        "errors": len(errors),
        "warnings": len(warns),
        "checks": [{k: c[k] for k in ("id", "measured", "threshold", "ok",
                                      "severity", "fix")} for c in checks],
    }


def _print_report(report: dict, report_path: Path) -> None:
    checks = report["checks"]
    width = max(len(c["id"]) for c in checks) + 2
    for c in checks:
        mark = "PASS" if c["ok"] else ("FAIL" if c["severity"] == "error"
                                       else ("INFO" if c["severity"] == "info"
                                             else "WARN"))
        print(f"[{mark}] {c['id']:<{width}} {c['measured']}")
        if not c["ok"] and c["fix"]:
            print(f"        fix: {c['fix']}")
    print(f"\n{'RESULT: PASS' if report['pass'] else 'RESULT: FAIL'} "
          f"({len(checks) - report['errors'] - report['warnings']}/"
          f"{len(checks)} checks, {report['errors']} error(s), "
          f"{report['warnings']} warning(s))")
    if report.get("strict") and report["warnings"] and not report["errors"]:
        print("[qa] strict: warnings are failures in delivery mode")
    print(f"[qa] wrote {report_path}")


def _effective_config(video: Path, config_path: Path) -> dict:
    """Effective config for the project a video belongs to (overlay included).

    QA is often handed a bare path or ``--latest``; both resolve back to
    ``input/<project>/`` when possible so the checks use the piece's intent.
    """
    try:
        _, project = runs.resolve_for_video(video)
        if project.is_dir():
            return load_config(config_path, project=project)
    except Exception:                              # a foreign/moved master
        pass
    return load_config(config_path) if config_path.exists() else {}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path, nargs="?")
    parser.add_argument("--latest", metavar="SKU",
                        help="QA the newest run of this SKU instead of a path")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--target-lufs", type=float, default=None)
    parser.add_argument("--max-bytes", type=int, default=None)
    parser.add_argument("--strict", action="store_true",
                        help="warnings fail too (delivery mode)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--post", action="store_true",
                        help="post-master profile: QA a comp/fx/grade run "
                             "(container, duration, audio, black frames, size, "
                             "cut identity) instead of the edit cut list")
    args = parser.parse_args(argv)

    if args.post:
        if args.latest:
            video = runs.resolve_stable_ref("@comp/latest", args.latest)
            if not video:
                sys.exit(f"[qa] no comp run for {args.latest}; run fenix comp")
            video = Path(video)
        elif args.video:
            video = args.video
            if video.is_dir():
                video = runs.run_video(video)
        else:
            parser.error("--post needs a comp run directory or --latest <sku>")
        if not video.exists():
            sys.exit(f"[qa] missing {video}")
        cfg = _effective_config(video, args.config)
        report = post_report(video, cfg, strict=args.strict)
        report_path = runs.evidence_path(video, cfg)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_report(report, report_path)
        sys.exit(0 if report["pass"] else 1)

    if args.latest:
        latest = runs.latest_run(args.latest)
        if not latest or not latest.get("video"):
            sys.exit(f"[qa] no completed run for {args.latest}")
        video = Path(latest["video"])
    elif args.video:
        video = args.video
    else:
        parser.error("pass a video path or --latest <sku>")
    if not video.exists():
        sys.exit(f"[qa] missing {video}")

    cfg: dict = {}
    if args.config.exists():
        cfg = _effective_config(video, args.config)
    q = {"in_point_floor": 0.60, "beat_offset_warn_ms": 60.0,
         "beat_offset_fail_ms": 120.0, "caption_warn_cps": 20.0,
         "caption_fail_cps": 25.0, "max_duration_sec": 28.5}
    q.update(cfg.get("quality") or {})
    target_lufs = (args.target_lufs if args.target_lufs is not None
                   else float(cfg.get("audio", {}).get("target_lufs", -14.0)))
    max_bytes = (args.max_bytes if args.max_bytes is not None
                 else int(cfg.get("encoding", {}).get("max_whatsapp_bytes",
                                                      16252928)))

    data = probe(video)
    streams = {s["codec_type"]: s for s in data.get("streams", [])}
    v, a = streams.get("video", {}), streams.get("audio", {})
    fmt = data.get("format", {})
    num, _, den = str(v.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1)
    duration = float(fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)

    checks: list[dict] = []

    def check(cid: str, ok: bool, measured, threshold, fix: str = "",
              severity: str = "error") -> None:
        checks.append({"id": cid, "check": cid, "measured": measured,
                       "threshold": threshold, "ok": bool(ok),
                       "detail": str(measured), "severity": severity,
                       "fix": fix})

    check("v_resolution", (v.get("width"), v.get("height")) == (1080, 1920),
          f"{v.get('width')}x{v.get('height')}", "1080x1920",
          "render at 1080x1920; the safe-zone maths assumes that canvas")
    check("v_fps", abs(fps - 60.0) < 0.01, f"{fps:.2f}", "60",
          "set canvas.fps = 60 and re-render")
    check("v_pix_fmt", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt")),
          "yuv420p", "WhatsApp/Reels need 4:2:0")
    check("v_profile", str(v.get("profile", "")).lower() == "main",
          str(v.get("profile")), "main", "set encoding.profile = main")
    check("v_level", str(v.get("level")) == "42",
          f"level={v.get('level')}", "4.2 (level 42)",
          "1080x1920@60 is outside level 3.1/4.0; set encoding.level = 4.2")
    check("v_faststart", has_faststart(video), "moov before mdat", "faststart",
          "add -movflags +faststart")
    check("v_size_budget", size <= max_bytes,
          f"{size / 1024 / 1024:.2f} MB",
          f"<= {max_bytes / 1024 / 1024:.1f} MB",
          "re-encode at CRF 21 / 2.5 Mbps")
    check("a_stream", bool(a) and a.get("codec_name") == "aac",
          f"{a.get('codec_name')} {a.get('sample_rate')} Hz {a.get('channels')} ch",
          "aac 48 kHz stereo", "set encoding.audio_codec = aac")

    # expected duration comes from the sibling word_timings.json when present
    sku_name, sku_dir = runs.resolve_for_video(video)
    sku_info: dict = {}
    sku_path = sku_dir / "sku.json"
    if sku_path.exists():
        try:
            sku_info = json.loads(sku_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sku_info = {}
    allowed_fast = bool(sku_info.get("allow_fast_captions"))
    # A music-only run records the flag in its own plan; prefer that over the
    # current sku.json so an immutable run stays reproducible after the copy or
    # the voiceover setting changes.
    run_music_only = False
    run_plan_path = video.parent / "edit_plan.json"
    if run_plan_path.exists():
        try:
            run_music_only = bool(json.loads(
                run_plan_path.read_text(encoding="utf-8")).get("music_only"))
        except json.JSONDecodeError:
            run_music_only = False
    music_only = run_music_only or is_music_only(sku_info)
    # Prefer the run's own snapshot so an immutable run stays reproducible
    # after the SKU's voiceover is replaced.
    timings = video.parent / "word_timings.json"
    if not timings.exists():
        timings = sku_dir / "word_timings.json"
    expected = None
    run_plan = video.parent / "edit_plan.json"
    if run_plan.exists():
        try:
            expected = float(json.loads(run_plan.read_text(encoding="utf-8"))
                             .get("duration_sec") or 0) or None
        except json.JSONDecodeError:
            expected = None
    if music_only and expected is None:
        brief: dict = {}
        brief_path = sku_dir / "brief.json"
        if brief_path.exists():
            try:
                brief = json.loads(brief_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                brief = {}
        expected = explicit_duration(sku_info, brief, cfg)
    if timings.exists() and not music_only:
        tdata = json.loads(timings.read_text(encoding="utf-8"))
        expected = expected or float(tdata.get("duration_sec") or 0)
        check("c_timings", bool(tdata.get("words")), "word timings present",
              "non-empty 'words'", "run fenix align")
        min_sim = float(cfg.get("alignment", {}).get("min_script_similarity", 0.55))
        sim = float(tdata.get("script_similarity") or 0)
        check("d_script_match", sim >= min_sim, f"similarity {sim:.2f}",
              f">= {min_sim:.2f}",
              "the ASR transcript drifted from script_text; re-record or fix "
              "script_text so the caption tail matches", "warn")
        if "tail_complete" in tdata:
            tail_ok = bool(tdata.get("tail_complete"))
            tail_sil = float(tdata.get("tail_silence_sec") or 0.0)
            check("d_tail_complete", tail_ok,
                  f"{tail_sil:.2f}s trailing silence",
                  ">= 0.03s (last phrase not clipped)",
                  "the voiceover ends mid-word; re-run fenix align so the "
                  "tail cut lands on silence, or re-record the line")
    elif not music_only:
        check("c_timings", False, f"missing {timings}", "word_timings.json",
              "run fenix align <sku>")
    check("q_run_versioned",
          bool(runs.RUN_RE.match(video.parent.name))
          and video.name.endswith("_final.mp4"),
          f"{video.parent.name}/{video.name}",
          "<sku>_<YYYYmmdd-HHMMSS>/<run>_final.mp4",
          "render without --out so each result lands in its own timestamped run "
          "directory and older outputs are never overwritten", "warn")
    if expected:
        duration_label = "plan" if music_only else "VO"
        check("d_matches_vo", abs(duration - expected) < 0.35,
              f"{duration:.2f}s video / {expected:.2f}s {duration_label}",
              "within 0.35s",
              "the video must follow the voiceover; re-render after changing "
              "the script" if not music_only else
              "the video must match the plan duration_sec; re-render after "
              "changing the brief/shot table")
    max_extra = float((cfg.get("voiceover") or {}).get("max_extra_sec", 0.0))
    max_dur = max(float(q["max_duration_sec"]),
                  float(cfg.get("target_duration_sec", 28.0)) + max_extra)
    check("d_max", duration <= max_dur, f"{duration:.2f}s", f"<= {max_dur:.1f}s",
          "cut the script (fenix lint) or trim the timeline")

    lufs, tp, pcm = audio_metrics(video)
    check("a_lufs", abs(lufs - target_lufs) <= 1.0,
          f"{lufs:.2f} LUFS", f"{target_lufs:+.1f} +/- 1.0",
          "re-run loudnorm with linear=true; check the ducking ratio")
    check("a_true_peak", tp <= -1.0, f"{tp:.2f} dBTP", "<= -1.0 dBTP",
          "lower audio.limiter_ceiling in config/pipeline.json")

    # the same decode feeds the full-scale sample scan
    clipped = int((np.abs(pcm) >= 0.999).sum())
    check("a_clipping", clipped == 0,
          f"{clipped} full-scale samples",
          "0 (peak "
          f"{20 * np.log10(np.abs(pcm).max() + 1e-9):.2f} dBFS)",
          "reduce the master gain or the limiter ceiling")

    sample_ts = [duration * f for f in (0.02, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    lumas = frame_luma(video, sample_ts)
    darkest = min(lumas) if lumas else 0.0
    check("v_black_frames", darkest >= 12.0,
          f"darkest sampled mean luma {darkest:.1f}/255", ">= 12",
          "the clip or the grade is too dark; pick a brighter in-point")

    plan_path = video.parent / "edit_plan.json"
    plan: dict = {}
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        cuts = plan.get("cuts", [])
        if plan.get("draft"):
            check("q_not_draft", False, "draft render (veryfast / CRF 23)",
                  "delivery master",
                  "render again without --draft before delivering", "error")
        check("p_cuts", len(cuts) > 0, f"{len(cuts)} cuts", "> 0",
              "the renderer wrote no cut list")
        ratios = [c.get("sharpness_ratio") for c in cuts
                  if c.get("sharpness_ratio") is not None]
        if ratios:
            check("p_in_point", min(ratios) >= float(q["in_point_floor"]) - 1e-6,
                  f"min {min(ratios):.2f}x / mean "
                  f"{sum(ratios)/len(ratios):.2f}x",
                  f">= {q['in_point_floor']:.2f}x clip median",
                  "re-pick the soft in-points from quality.samples (see "
                  "fenix validate suggestions)")
        shots = [c["window"] for c in cuts if c.get("window")]
        if shots:
            means = [w["mean_ratio"] for w in shots]
            check("p_window_sharp",
                  min(means) >= float(q["in_point_floor"]) - 1e-6,
                  f"min {min(means):.2f}x / mean {sum(means)/len(means):.2f}x "
                  f"over the whole shot",
                  f">= {q['in_point_floor']:.2f}x clip median",
                  "the in-point is sharp but the shot is soft after it; "
                  "re-plan with fenix plan")
            worst_soft = max(w["soft_frac"] for w in shots)
            check("p_window_soft", worst_soft <= 0.30,
                  f"worst shot {worst_soft:.0%} under floor",
                  "<= 30% of each shot", "pick windows without the soft "
                  "portion (quality.window_stats)", "warn")
        offs = [abs(c["beat_offset_ms"]) for c in cuts
                if c.get("beat_offset_ms") is not None]
        if offs:
            worst = max(offs)
            picture_policy = picture_driven({"quality": q})
            if picture_policy:
                # A picture-driven piece cuts on action, not on the grid; the
                # measured offsets stay in the report as information.
                check("p_beat_offset", True,
                      f"max {worst:.0f} ms / mean {sum(offs)/len(offs):.0f} ms "
                      f"(picture-driven: informational)",
                      "quality.beat_policy = picture", "", "info")
                check("p_beat_hard", True, f"max {worst:.0f} ms "
                      f"(picture-driven: informational)",
                      "quality.beat_policy = picture", "", "info")
            else:
                check("p_beat_offset", worst <= float(q["beat_offset_warn_ms"]),
                      f"max {worst:.0f} ms / mean {sum(offs)/len(offs):.0f} ms",
                      f"<= {q['beat_offset_warn_ms']:.0f} ms",
                      f"cuts drift off the measured grid; snap to "
                      f"output/{video.parent.name}/beat_grid.json", "warn")
                check("p_beat_hard", worst <= float(q["beat_offset_fail_ms"]),
                      f"max {worst:.0f} ms",
                      f"<= {q['beat_offset_fail_ms']:.0f} ms",
                      "regenerate the cut list on the measured beat grid")
    # a render-time warning is re-judged against the current sku.json: the
    # fast-caption one must not fail QA when the ship was opted into
    for w in plan.get("warnings", []):
        if allowed_fast and " CPS, fail " in w and "caption #" in w:
            continue
        checks.append({"id": "w_layout", "check": "w_layout", "measured": w,
                       "threshold": "0 warnings", "ok": False, "detail": w,
                       "severity": "warn", "fix": w})

    # ---- content check: does each rendered shot actually show its clip?
    # The container can be perfect while the renderer shows the wrong angle.
    if plan.get("cuts"):
        canvas = cfg.get("canvas") or {"width": 1080, "height": 1920, "fps": 60}
        W, H, FPS = int(canvas["width"]), int(canvas["height"]), int(canvas["fps"])
        grade = cfg.get("grade") if (cfg.get("grade") or {}).get(
            "mode", "baked") == "baked" else None
        by_name = {p.name: p for p in (sku_dir / "clips").glob("*.mp4")}
        # Build the normalised cache first (sequential, writes to disk), then
        # compare all cuts concurrently: 32 frame seeks dominate this block.
        jobs: list[tuple[dict, Path, float, float]] = []
        missing = 0
        for c in plan["cuts"]:
            src = by_name.get(c.get("clip", ""))
            if src is None:
                missing += 1
                continue
            norm = ensure_normalized_clip(src, W, H, FPS, grade)
            if not norm.exists():
                missing += 1
                continue
            t_mid = float(c["t0"]) + float(c["dur"]) / 2.0
            t_src = float(c["in"]) + (t_mid - float(c["t0"]))
            jobs.append((c, norm, t_mid, t_src))

        def _compare(job: tuple[dict, Path, float, float]):
            cut, norm, t_mid, t_src = job
            a = frame_crop_gray(video, t_mid)
            b = frame_crop_gray(norm, t_src)
            if a is None or b is None:
                return None
            sim = frame_similarity(a, b)
            return (cut["index"], cut.get("clip", ""), sim["corr"], sim["mad"])

        results = []
        if jobs:
            with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
                results = list(pool.map(_compare, jobs))
        compared, mismatches = 0, []
        for result in results:
            if result is None:
                missing += 1
                continue
            index, clip, corr, mad = result
            compared += 1
            if corr < 0.90 or mad > 0.14:
                mismatches.append((index, clip, corr, mad))
        if compared:
            check("c_clip_identity", not mismatches,
                  f"{compared - len(mismatches)}/{compared} cuts match their "
                  f"source" + (f"; worst corr {min(m[2] for m in mismatches):.2f}"
                               if mismatches else ""),
                  "corr >= 0.90 for every rendered shot",
                  "; ".join(f"cut {i} shows {clip} at corr {corr:.2f}"
                            for i, clip, corr, _ in mismatches[:4]) or "")
        else:
            check("c_clip_identity", False,
                  f"{missing} cuts could not be compared (clip cache missing)",
                  "every cut compared to its source",
                  "render once (populates .cache/clips) before running QA", "warn")

    allowed_fast = bool(sku_info.get("allow_fast_captions"))

    audit_path = video.parent / "caption_audit.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("disabled"):
            pass       # music-only render: nothing to judge, rows stripped below
        else:
            check("c_cps_max",
                  audit["max_cps"] <= float(q["caption_warn_cps"]) or allowed_fast,
                  f"{audit['max_cps']:.1f} CPS max / {audit['mean_cps']:.1f} mean"
                  + (" (allow_fast_captions)" if allowed_fast else ""),
                  f"<= {q['caption_warn_cps']:.0f} CPS",
                  f"{audit['cards_over_warn']} of {len(audit['cards'])} cards are "
                  f"too fast; shorten the script or merge cards", "warn")
            check("c_cps_fail",
                  audit["max_cps"] <= float(q["caption_fail_cps"]) or allowed_fast,
                  f"{audit['max_cps']:.1f} CPS max"
                  + (" (allow_fast_captions)" if allowed_fast else ""),
                  f"<= {q['caption_fail_cps']:.0f} CPS"
                  + (" or allow_fast_captions" if allowed_fast else ""),
                  "not readable at 60 fps: shorten the card (caption_audit.json "
                  "lists every card's text and CPS)",
                  "warn" if allowed_fast else "error")

            # ---- caption content + safe-zone containment (text, not pixels)
            kc = cfg.get("kinetic_captions") or {}
            max_chars = int(kc.get("max_chars_per_chunk", 26))
            min_px = int((cfg.get("typography") or {}).get("min_size", 24))
            zone = ((cfg.get("safe_zones") or {}).get(
                sku_info.get("safe_zone_mode", "reels_universal")) or {})
            zone_w = int(zone.get("x_max", 950)) - int(zone.get("x_min", 65))
            cards = audit.get("cards", [])
            bad_text = [c for c in cards
                        if not c.get("text", "").strip()
                        or len(c.get("text", "")) > max_chars + 3
                        or any(ch in c.get("text", "") for ch in "✓✔☑")]
            check("c_caption_text", not bad_text,
                  f"{len(cards) - len(bad_text)}/{len(cards)} cards clean",
                  f"non-empty, <= {max_chars + 3} chars, no Unicode checkmarks",
                  "; ".join(f"card {c['index']} {c['text'][:24]!r}"
                            for c in bad_text[:4]) or "")
            over = [c for c in cards
                    if c.get("width_px", 0) > zone_w + 2
                    or c.get("font_px", 99) < min_px]
            check("c_caption_zone", not over,
                  f"{len(cards) - len(over)}/{len(cards)} inside the {zone_w} px zone",
                  f"width <= {zone_w} px, font >= {min_px} px",
                  "; ".join(f"card {c['index']} {c['width_px']} px / "
                            f"{c['font_px']} px font" for c in over[:4]) or "")
            if cards:
                dupes = [b for a, b in zip(cards, cards[1:])
                         if a["text"].strip() and a["text"] == b["text"]]
                check("c_caption_dupes", not dupes,
                      f"{len(dupes)} repeated consecutive cards", "0 repeats",
                      "two identical cards in a row read as a freeze: merge them",
                      "warn")
    elif not music_only:
        check("c_cps_max", False, "missing caption_audit.json",
              "caption audit present", "re-render: fenix render writes it",
              "warn")

    # ---- HyperFrames text layer: alpha transport + brand colour ---------
    # Only present on `render --hf-overlay` runs, so native runs keep the
    # documented 30-row gate; an overlay run adds two colour/alpha rows.
    if (plan or {}).get("overlay_engine") == "hyperframes":
        hf = (plan or {}).get("hyperframes") or {}
        alpha = hf.get("alpha") or {}
        check("o_alpha", bool(alpha.get("ok")),
              f"mid transparent {alpha.get('mid_transparent_ratio')}, "
              f"header px {alpha.get('header_px')}, "
              f"caption px {alpha.get('caption_px')}",
              "decoded RGBA state has a transparent footage area and visible ink",
              "rebuild the text layer: fenix render <sku> --hf-overlay")

        target_hex = (cfg.get("colors") or {}).get("emerald_primary", "#059669")
        target = tuple(int(target_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        box = (plan or {}).get("price_box")
        sampled = _pill_rgb(video, box) if box else None
        if sampled is None:
            check("o_pill_colour", False, "could not sample the price pill",
                  "rgb within tolerance of the brand emerald",
                  "inspect the run's contact sheet", "warn")
        else:
            delta = max(abs(a - b) for a, b in zip(sampled, target))
            check("o_pill_colour", delta <= 12,
                  f"rgb{sampled} vs {target_hex} (max delta {delta})",
                  "max channel delta <= 12",
                  "composite the overlay in RGB; do not force a second "
                  "colour matrix")

    checks, skipped_vo = strip_vo_checks(checks, music_only)
    errors = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    warns = [c for c in checks if not c["ok"] and c["severity"] == "warn"]
    passed = not errors and (not warns or not args.strict)
    stat = video.stat()
    report = {
        "video": str(video),
        "sku": sku_name,
        "run": video.parent.name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_sec": round(duration, 3),
        "size_mb": round(size / 1024 / 1024, 3),
        "video_bytes": stat.st_size,
        "video_mtime_ns": stat.st_mtime_ns,
        "strict": args.strict,
        "music_only": music_only,
        "skipped_checks": skipped_vo,
        "pass": passed,
        "errors": len(errors),
        "warnings": len(warns),
        "checks": [{k: c[k] for k in ("id", "measured", "threshold", "ok",
                                      "severity", "fix")} for c in checks],
    }
    report_path = runs.evidence_path(video, cfg)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    if runs.is_on_delivery_shelf(video, cfg):
        print(f"[qa] local evidence: {report_path} (delivery shelf untouched)")
    if runs.RUN_RE.match(video.parent.name):
        try:
            runs.register_run(sku_name, video.parent, video, {
                "qa_pass": passed,
                "qa_errors": len(errors),
                "qa_warnings": len(warns),
                "lufs": round(lufs, 2),
                "true_peak_db": round(tp, 2),
            })
        except OSError as exc:  # read-only kit folders etc.
            print(f"[qa] note: could not update runs.json ({exc})")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report, report_path)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
