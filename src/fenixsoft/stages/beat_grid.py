#!/usr/bin/env python3
"""
beat_grid — measure the real beat grid of the music bed.

`config/pipeline.json` carries a nominal `bpm` (118). The actual track is
119.0 BPM, so every cut planned on the nominal grid drifts further from the
music: 0 ms at the first cut, 176 ms by the last one. This tool measures tempo
and phase from the audio itself and writes the beat list as a sidecar
(`output/<sku>/beat_grid.json`), which the renderer uses for snapping and the
plan validator / QA use to score cut alignment.

The measurement is cached by file hash in `.cache/beat_grid/`, so repeated
renders pay nothing.

Usage:
    fenix beats <sku>            # write output/<sku>/beat_grid.json
    fenix beats --music assets/music/groove_118bpm.wav --print
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

from ..context import (CACHE_DIR, DEFAULT_CONFIG, MUSIC_DIR, REPO_ROOT, artifact,
                       load_config, repo_path, resolve_sku_dir)

CACHE = CACHE_DIR / "beat_grid"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _load_mono(path: Path, sr: int = 22050):
    try:
        import librosa  # imported lazily: only this tool needs it
    except ImportError:  # pragma: no cover - environment error path
        sys.exit("[beat_grid] librosa is missing: pip install librosa")
    y, rate = librosa.load(str(path), sr=sr, mono=True)
    return y, rate, librosa


def measure(music: Path, config_bpm: float | None = None) -> dict:
    """Return the measured grid: tempo, beat interval, phase and beat times."""
    y, sr, librosa = _load_mono(music)
    duration = float(len(y)) / float(sr)

    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time", trim=False)
    tempo = float(tempo[0]) if hasattr(tempo, "__len__") else float(tempo)
    beat_times = [round(float(b), 4) for b in beats]
    if len(beat_times) < 4:
        raise SystemExit(f"[beat_grid] too few beats detected in {music.name}")

    intervals = [b - a for a, b in zip(beat_times, beat_times[1:]) if b > a]
    # The tracker quantises beats to frames (23.2 ms at hop 512), so most gaps
    # are 22 frames with occasional 21-frame gaps: the MEDIAN reads 117.5 BPM
    # while the true rate is 119.0. Use the mean interval (= regression slope
    # over the whole track), and keep the median for transparency.
    beat_sec = statistics.fmean(intervals)
    median_iv = statistics.median(intervals)
    stdev = statistics.pstdev(intervals) if len(intervals) > 2 else 0.0
    bpm = 60.0 / beat_sec if beat_sec else tempo
    first = beat_times[0]

    grid = {
        "source": str(music.relative_to(REPO_ROOT)) if music.is_relative_to(REPO_ROOT) else str(music),
        "sha256": file_sha256(music),
        "duration_sec": round(duration, 3),
        "bpm": round(bpm, 2),
        "beat_sec": round(beat_sec, 4),
        "median_beat_sec": round(median_iv, 4),
        "beat_sec_stdev": round(stdev, 4),
        "offset": round(first, 4),
        "beats": beat_times,
        "confident": stdev <= 0.15 * beat_sec,
        "tracker": {"librosa": _librosa_version(librosa), "sr": sr,
                    "units": "time", "trim": False, "start_bpm": 120.0},
    }
    if config_bpm:
        nominal = 60.0 / float(config_bpm)
        grid["config_bpm"] = float(config_bpm)
        grid["config_beat_sec"] = round(nominal, 4)
        # How far a cut laid on the NOMINAL grid lands from the real beat,
        # measured at the end of the first 28 s (the delivery length) and at
        # the end of the track.
        grid["drift_ms_at_28s"] = _nominal_drift(beat_times, nominal, 28.0)
        grid["drift_ms_at_end"] = _nominal_drift(beat_times, nominal, duration)
        grid["drift_ms_per_sec"] = round(
            (nominal - beat_sec) / nominal * 1000.0, 2)
    return grid


def _nominal_drift(beats: list[float], nominal: float, target: float) -> float:
    """Signed ms between the nominal-grid beat near `target` and the real beat."""
    if not beats:
        return 0.0
    t = target if target <= beats[-1] else beats[-1]
    k = max(1, round(t / nominal))
    nominal_t = k * nominal
    real = min(beats, key=lambda b: abs(b - nominal_t))
    return round((nominal_t - real) * 1000.0, 1)


def _librosa_version(librosa) -> str:
    return getattr(librosa, "__version__", "unknown")


def interval_span(beat_times: list[float]) -> list[float]:
    return beat_times


def cached_measure(music: Path, config_bpm: float | None = None,
                   force: bool = False) -> dict:
    """`measure`, memoised on the audio file's hash."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = file_sha256(music)[:16]
    cache_path = CACHE / f"{key}.json"
    if cache_path.exists() and not force:
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data.get("sha256") == file_sha256(music):
                if config_bpm:
                    data["config_bpm"] = float(config_bpm)
                return data
        except json.JSONDecodeError:
            pass
    grid = measure(music, config_bpm=config_bpm)
    cache_path.write_text(json.dumps(grid, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return grid


def load_grid(sku_dir: Path) -> dict | None:
    """Read `output/<sku>/beat_grid.json` (or the input copy) if present."""
    for candidate in (artifact(sku_dir, "beat_grid.json"),
                      sku_dir / "beat_grid.json"):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
    return None


def write_grid(sku_dir: Path, grid: dict) -> Path:
    out = artifact(sku_dir, "beat_grid.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(grid, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def nearest_beat(t: float, beats: list[float]) -> tuple[float, float]:
    """Return (nearest beat time, signed offset in ms: cut minus beat)."""
    if not beats:
        return t, 0.0
    best = min(beats, key=lambda b: abs(b - t))
    return best, round((t - best) * 1000.0, 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Measure the music beat grid")
    parser.add_argument("sku_dir", nargs="?", help="SKU name (or input/<sku> path)")
    parser.add_argument("--music", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--force", action="store_true", help="ignore the hash cache")
    parser.add_argument("--print", action="store_true", help="print the JSON")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    nominal_bpm = cfg.get("music", {}).get("bpm") or cfg.get("bpm")
    music = args.music
    if music is None:
        music = repo_path(cfg.get("music", {}).get("file") or "")
    if not music.exists():
        pool = sorted(p for p in MUSIC_DIR.glob("*")
                      if p.suffix.lower() in (".wav", ".mp3", ".m4a"))
        if not pool:
            sys.exit("[beats] no music file found")
        music = pool[0]

    grid = cached_measure(music, config_bpm=nominal_bpm, force=args.force)
    if args.sku_dir:
        sku_dir = resolve_sku_dir(args.sku_dir)
        out = write_grid(sku_dir, grid)
        print(f"[beats] {grid['bpm']:.2f} BPM "
              f"(beat {grid['beat_sec']:.4f}s ± {grid['beat_sec_stdev']:.4f}), "
              f"offset {grid['offset']:.3f}s, {len(grid['beats'])} beats")
        if "drift_ms_at_end" in grid:
            print(f"[beats] config says {grid['config_bpm']:.1f} BPM -> "
                  f"{grid['drift_ms_at_end']:.0f} ms drift by the last beat")
        print(f"[beats] wrote {out}")
    if args.print or not args.sku_dir:
        print(json.dumps(grid, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
