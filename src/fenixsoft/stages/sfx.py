#!/usr/bin/env python3
"""sfx — cue-sheet-first sound design for an edit plan.

The stage implements the three-layer blueprint from the
`sfx-dopamine-blueprint` skill:

  * Environmental bed: the music bed is normalised, ducked under foreground
    transients and carved with a 80-150 ms vacuum before major drops.
  * Foreground dopamine SFX: J-cut whooshes/risers, double-transient clicks +
    body thuds, and sub impacts reserved for real scene flips.
  * Cue-sheet-first: every event is planned from `edit_plan.json` and
    `word_timings.json` before a single sample is mixed.

It writes four derived artifacts under `output/<sku>/`:

    sfx_cue_sheet.json   the plan: timestamps, layers, roles, offsets, sources
    sfx_bus.wav          foreground SFX only, 48 kHz stereo PCM24
    music_bed.wav        music bed after ducking + vacuum automation
    sfx_report.json      counts, measured levels and pass/fail checks

`fenix render` consumes the cue sheet + bus + bed when they are fresh, and
falls back to the legacy one-whoosh-per-cut path when they are not.

Usage:
  fenix sfx <sku>                    # plan + mix
  fenix sfx <sku> --dry-run          # cue sheet only
  fenix sfx <sku> --install-library  # synthesize the procedural core first
  fenix sfx --install-library        # install only, no project needed
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import numpy as np
from scipy import signal

from ..context import (DEFAULT_CONFIG, MUSIC_DIR, SFX_DIR, artifact,
                       display_path, load_config, repo_path, resolve_sku_dir)

# 48 kHz is the pipeline audio contract (config.encoding.audio_sample_rate).
SAMPLE_RATE = 48000
PCM_CODEC = "pcm_s24le"
DEFAULT_SUBDIR = "fenix_sfx"

SYNTH_ROLES = [
    "impact_sub",
    "riser_tension",
    "whoosh_air",
    "whoosh_whip",
    "click_tactile",
    "thud_body",
    "tick_micro",
    "chime_lock",
    "paper_slice",
    "env_room",
]

# A keyword hit is a *musical* accent, not a transcription exercise: the regex
# runs against an ASCII-normalised, punctuation-free uppercase token.
KEYWORD_CUES: list[tuple[str, list[dict]]] = [
    ("^330$", [{"role": "click_tactile"},
               {"role": "thud_body", "offset_sec": 0.018}]),
    ("^PRECIO$", [{"role": "click_tactile"},
                  {"role": "thud_body", "offset_sec": 0.018}]),
    ("^GRADO$", [{"role": "click_tactile"}]),
    ("^77$", [{"role": "tick_micro", "gain_offset_db": -4.0},
              {"role": "thud_body", "gain_offset_db": -6.0, "offset_sec": 0.018}]),
    ("^100$", [{"role": "impact_sub", "duck": True, "major": True,
                "vacuum_ms": None},
               {"role": "click_tactile"},
               {"role": "thud_body", "offset_sec": 0.018}]),
    ("^FUNCIONAL$", [{"role": "click_tactile"}]),
    ("^128$", [{"role": "ui_data", "gain_offset_db": -4.0, "duck": True},
               {"role": "tick_micro", "offset_sec": 0.012}]),
    ("^(E)?SIM$", [{"role": "click_tactile"}]),
    ("^FORRO$", [{"role": "paper_slice"}]),
    ("^GARANTIA$", [{"role": "chime_lock", "duck": True, "offset_sec": 0.18},
                    {"role": "click_tactile", "gain_offset_db": -4.0}]),
    ("^PAGO$", [{"role": "click_tactile"}]),
    ("^PIEZA$", [{"role": "click_tactile"}]),
    ("^FENIXSOFT$", [{"role": "click_tactile"}]),
    ("^C29$", [{"role": "click_tactile"}]),
    ("^WHATSAPP$", [{"role": "click_tactile", "gain_offset_db": -4.0}]),
]

SUB_ROLES = {"impact_sub", "stinger_final"}


# --------------------------------------------------------------------- utils
def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kw)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_pick(items: list[Path], key: str) -> Path:
    if len(items) == 1:
        return items[0]
    idx = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16)
    return items[idx % len(items)]


def _ascii_token(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"[sfx] missing {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"[sfx] invalid JSON in {path}: {exc}")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# --------------------------------------------------------------- sound design
def _db(value: float) -> float:
    return 10.0 ** (value / 20.0)


def _sweep(f0: float, f1: float, dur: float, curve: str = "exp") -> np.ndarray:
    n = max(1, int(dur * SAMPLE_RATE))
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    if curve == "exp" and f0 > 0:
        freq = f0 * (f1 / f0) ** (t / max(dur, 1e-6))
    else:
        freq = f0 + (f1 - f0) * (t / max(dur, 1e-6))
    phase = 2.0 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    return np.sin(phase)


def _noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n)


def _band(x: np.ndarray, lo: float, hi: float, order: int = 2) -> np.ndarray:
    nyq = SAMPLE_RATE / 2.0
    lo = max(20.0, min(lo, nyq * 0.95))
    hi = max(lo + 10.0, min(hi, nyq * 0.98))
    sos = signal.butter(order, [lo / nyq, hi / nyq], btype="bandpass",
                        output="sos")
    return signal.sosfilt(sos, x)


def _low(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    nyq = SAMPLE_RATE / 2.0
    cutoff = max(20.0, min(cutoff, nyq * 0.95))
    sos = signal.butter(order, cutoff / nyq, btype="lowpass", output="sos")
    return signal.sosfilt(sos, x)


def _high(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    nyq = SAMPLE_RATE / 2.0
    cutoff = max(20.0, min(cutoff, nyq * 0.95))
    sos = signal.butter(order, cutoff / nyq, btype="highpass", output="sos")
    return signal.sosfilt(sos, x)


def _fade(x: np.ndarray, in_sec: float = 0.002,
          out_sec: float = 0.02) -> np.ndarray:
    out = np.array(x, copy=True)
    a = min(len(out), max(1, int(in_sec * SAMPLE_RATE)))
    b = min(len(out), max(1, int(out_sec * SAMPLE_RATE)))
    ramp_in = np.linspace(0.0, 1.0, a, dtype=np.float64) ** 0.5
    ramp_out = np.linspace(1.0, 0.0, b, dtype=np.float64) ** 0.5
    if out.ndim == 2:
        ramp_in = ramp_in[:, None]
        ramp_out = ramp_out[:, None]
    out[:a] *= ramp_in
    out[-b:] *= ramp_out
    return out


def _center(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1)


def _stereo(l_mono: np.ndarray, r_mono: np.ndarray) -> np.ndarray:
    n = min(len(l_mono), len(r_mono))
    return np.stack([l_mono[:n], r_mono[:n]], axis=1)


def _normalise(samples: np.ndarray, peak_db: float = -3.0) -> np.ndarray:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-9:
        return samples
    return samples * (_db(peak_db) / peak)


def _synth_impact_sub() -> np.ndarray:
    dur = 1.25
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    sub = _sweep(95.0, 30.0, 0.95)
    sub = np.pad(sub, (0, max(0, n - len(sub))))[:n] * np.exp(-t / 0.34)
    click = _band(_noise(n, 11), 1500.0, 9500.0) * np.exp(-t / 0.004)
    thud = np.zeros(n)
    d = min(n, int(0.5 * SAMPLE_RATE))
    tt = np.arange(d, dtype=np.float64) / SAMPLE_RATE
    thud[:d] = _sweep(150.0, 55.0, 0.5) * np.exp(-tt / 0.075)
    mix = 0.90 * sub + 0.55 * click + 0.45 * thud
    mix = np.tanh(mix * 1.35)
    return _normalise(_fade(_center(mix), 0.001, 0.08))


def _synth_riser_tension() -> np.ndarray:
    dur = 1.25
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    bands = [(250.0, 650.0, 0.30), (500.0, 1300.0, 0.44),
             (1000.0, 2600.0, 0.56), (2000.0, 4800.0, 0.68),
             (3500.0, 8500.0, 0.78)]
    left = np.zeros(n)
    right = np.zeros(n)
    for i, (lo, hi, centre) in enumerate(bands):
        env = np.exp(-((t - centre) / 0.22) ** 2)
        left += _band(_noise(n, 20 + i), lo, hi) * env * (0.45 + 0.08 * i)
        right += _band(_noise(n, 40 + i), lo, hi) * env * (0.45 + 0.08 * i)
    swell = (t / dur) ** 1.35
    sine = _sweep(180.0, 900.0, dur) * 0.14 * (t / dur)
    left = _fade(np.tanh((left * swell + sine) * 1.25), 0.005, 0.012)
    right = _fade(np.tanh((right * swell + sine) * 1.25), 0.005, 0.012)
    return _normalise(_stereo(left, right))


def _synth_whoosh_air() -> np.ndarray:
    dur = 0.85
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    bands = [(180.0, 650.0, 0.38), (500.0, 1600.0, 0.48),
             (1200.0, 3600.0, 0.56), (2600.0, 7500.0, 0.62)]
    left = np.zeros(n)
    right = np.zeros(n)
    for i, (lo, hi, centre) in enumerate(bands):
        env = np.exp(-((t - centre) / 0.16) ** 2)
        left += _band(_noise(n, 60 + i), lo, hi) * env
        right += _band(_noise(n, 80 + i), lo, hi) * env
    left = _fade(left * 0.55, 0.01, 0.04)
    right = _fade(right * 0.55, 0.01, 0.04)
    return _normalise(_stereo(left, right))


def _synth_whoosh_whip() -> np.ndarray:
    dur = 0.55
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    bands = [(900.0, 2600.0, 0.24), (2200.0, 5200.0, 0.30),
             (4500.0, 10500.0, 0.34)]
    left = np.zeros(n)
    right = np.zeros(n)
    for i, (lo, hi, centre) in enumerate(bands):
        env = np.exp(-((t - centre) / 0.10) ** 2)
        left += _band(_noise(n, 100 + i), lo, hi) * env
        right += _band(_noise(n, 120 + i), lo, hi) * env
    fall = _sweep(1400.0, 220.0, dur) * np.exp(-t / 0.16) * 0.18
    left = _fade(np.tanh((left + fall) * 1.3), 0.005, 0.015)
    right = _fade(np.tanh((right + fall) * 1.3), 0.005, 0.015)
    return _normalise(_stereo(left, right))


def _synth_click_tactile() -> np.ndarray:
    dur = 0.08
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    noise = _band(_noise(n, 140), 1600.0, 10500.0) * np.exp(-t / 0.0035)
    tone = np.sin(2.0 * np.pi * 3200.0 * t) * np.exp(-t / 0.006) * 0.55
    mix = np.tanh((0.9 * noise + tone) * 1.2)
    return _normalise(_fade(_center(mix), 0.0005, 0.012))


def _synth_thud_body() -> np.ndarray:
    dur = 0.22
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    thud = _sweep(160.0, 65.0, dur) * np.exp(-t / 0.065)
    grit = _low(_noise(n, 150), 420.0) * np.exp(-t / 0.018) * 0.35
    mix = np.tanh((thud + grit) * 1.3)
    return _normalise(_fade(_center(mix), 0.001, 0.03))


def _synth_tick_micro() -> np.ndarray:
    dur = 0.055
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    tone = np.sin(2.0 * np.pi * 4300.0 * t) * np.exp(-t / 0.0075)
    noise = _high(_noise(n, 160), 3000.0) * np.exp(-t / 0.0025) * 0.5
    mix = np.tanh((tone + noise) * 1.4)
    return _normalise(_fade(_center(mix), 0.0004, 0.008))


def _synth_chime_lock() -> np.ndarray:
    dur = 1.9
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    partials = [(220.0, 0.50, 1.25), (329.63, 0.34, 1.05),
                (440.0, 0.30, 0.95), (554.37, 0.22, 0.85),
                (880.0, 0.16, 0.70), (1760.0, 0.09, 0.45)]
    left = np.zeros(n)
    right = np.zeros(n)
    for i, (freq, amp, decay) in enumerate(partials):
        detune = 1.0 + (0.0012 if i % 2 else -0.0012)
        left += np.sin(2.0 * np.pi * freq * detune * t) * amp * np.exp(-t / decay)
        right += np.sin(2.0 * np.pi * freq / detune * t) * amp * np.exp(-t / decay)
    shimmer = _high(_noise(n, 170), 6000.0) * np.exp(-t / 0.12) * 0.04
    left += shimmer
    right -= shimmer
    attack = min(n, int(0.006 * SAMPLE_RATE))
    env = np.ones(n)
    env[:attack] = np.linspace(0.0, 1.0, attack) ** 0.6
    left *= env
    right *= env
    return _normalise(_fade(_stereo(left, right), 0.001, 0.25))


def _synth_paper_slice() -> np.ndarray:
    dur = 0.32
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    body = _band(_noise(n, 180), 1800.0, 7800.0) * np.exp(-t / 0.055)
    snap = _band(_noise(n, 181), 700.0, 2600.0) * np.exp(-t / 0.012) * 0.5
    low = _sweep(180.0, 90.0, dur) * np.exp(-t / 0.045) * 0.18
    mix = np.tanh((body + snap + low) * 1.2)
    return _normalise(_fade(_center(mix), 0.001, 0.03))


def _synth_env_room() -> np.ndarray:
    # Seamless 9 s loop: low warm drone + filtered air, 1 s crossfade.
    dur = 10.0
    n = int(dur * SAMPLE_RATE)
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    lfo = 0.5 + 0.5 * np.sin(2.0 * np.pi * 0.07 * t)
    drone = (np.sin(2.0 * np.pi * 110.0 * t) * 0.22
             + np.sin(2.0 * np.pi * 165.0 * t) * 0.13
             + np.sin(2.0 * np.pi * 220.0 * t) * 0.05)
    air = _low(_noise(n, 190), 650.0) * 0.05
    mono = (drone * (0.8 + 0.2 * lfo) + air)
    left = mono + _low(_noise(n, 191), 420.0) * 0.018
    right = mono + _low(_noise(n, 192), 420.0) * 0.018
    loop = 9.0
    keep = int(loop * SAMPLE_RATE)
    fade = n - keep
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float64)
    left[:fade] = left[:fade] * ramp + left[keep:] * (1.0 - ramp)
    right[:fade] = right[:fade] * ramp + right[keep:] * (1.0 - ramp)
    return _normalise(_fade(_stereo(left[:keep], right[:keep]), 0.01, 0.01),
                      peak_db=-18.0)


SYNTHESISERS = {
    "impact_sub": _synth_impact_sub,
    "riser_tension": _synth_riser_tension,
    "whoosh_air": _synth_whoosh_air,
    "whoosh_whip": _synth_whoosh_whip,
    "click_tactile": _synth_click_tactile,
    "thud_body": _synth_thud_body,
    "tick_micro": _synth_tick_micro,
    "chime_lock": _synth_chime_lock,
    "paper_slice": _synth_paper_slice,
    "env_room": _synth_env_room,
}


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(samples, dtype="<f4")
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    proc = subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "f32le",
        "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "-",
        "-c:a", PCM_CODEC, str(path),
    ], input=data.tobytes(), capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))


def _read_wav(path: Path, max_sec: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if max_sec:
        cmd += ["-t", f"{max_sec:.3f}"]
    cmd += ["-f", "f32le", "-ac", "2", "-ar", str(SAMPLE_RATE), "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    data = np.frombuffer(proc.stdout, dtype="<f4")
    if data.size == 0:
        return np.zeros((1, 2), dtype=np.float64)
    return data.reshape(-1, 2).astype(np.float64)


def _anchor_index(samples: np.ndarray, align: str) -> int:
    if align == "end":
        return max(0, len(samples) - 1)
    if align == "peak":
        return int(np.argmax(np.max(np.abs(samples), axis=1)))
    return 0


def _onset_sec(samples: np.ndarray, threshold_db: float = -20.0) -> float:
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak < 1e-9:
        return 0.0
    threshold = peak * _db(threshold_db)
    env = np.max(np.abs(samples), axis=1)
    idx = int(np.argmax(env > threshold))
    return idx / SAMPLE_RATE


def _probe_loudness(path: Path) -> dict:
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ], capture_output=True, text=True)
    text = proc.stderr
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError(f"loudnorm produced no JSON for {path}")
    return json.loads(text[start:end + 1])


def install_library(library_dir: Path, subdir: str = DEFAULT_SUBDIR,
                    force: bool = False) -> list[Path]:
    """Synthesize the procedural core into `<library_dir>/<subdir>/`."""
    target = library_dir / subdir
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for role in SYNTH_ROLES:
        path = target / f"fenix_{role}.wav"
        if path.exists() and not force:
            written.append(path)
            continue
        samples = SYNTHESISERS[role]()
        _write_wav(path, samples)
        written.append(path)
    readme = target / "README.txt"
    readme.write_text(
        "Fenix procedural SFX core\n"
        "=========================\n\n"
        "Generated by `./fenix sfx --install-library`. These are the precise,\n"
        "deterministic foreground elements: sub impacts, risers, whooshes,\n"
        "clicks, body thuds, ticks, a lock chime, a paper slice and a room bed.\n"
        "They are peak-normalised to -3 dBFS (room bed -18 dBFS) and sit next to\n"
        "the licensed Epidemic Sound files referenced by\n"
        "`assets/sfx/library.json`.\n\n"
        "Re-generate with:\n"
        "  ./fenix sfx --install-library --force\n",
        encoding="utf-8")
    return written


# ---------------------------------------------------------------- cue sheet
def _cut_label(index: int, cut: dict) -> str:
    clip = cut.get("clip") or "clip"
    return f"cut {index} · {clip}"


def _add(cues: list[dict], seen: set[tuple[str, float]], role: str, t: float,
         layer: str, visual_beat: str, **kw) -> None:
    key = (role, round(float(t), 3))
    if key in seen:
        return
    seen.add(key)
    cue = {
        "id": f"{layer}-{len(cues):02d}-{role}",
        "t_sec": round(max(0.0, float(t)), 3),
        "layer": layer,
        "role": role,
        "visual_beat": visual_beat,
        "gain_offset_db": round(float(kw.pop("gain_offset_db", 0.0)), 2),
        "offset_ms": int(kw.pop("offset_ms", 0)),
        "duck": bool(kw.pop("duck", False)),
        "vacuum_ms": int(kw.pop("vacuum_ms", 0)),
        "pan": float(kw.pop("pan", 0.0)),
        "align": kw.pop("align", None),
        "major": bool(kw.pop("major", False)),
    }
    cue.update(kw)
    cues.append(cue)


def plan_cues(cuts: list[dict], words: list[dict], duration: float,
              music_label: str, bed_lufs: float, policy: dict) -> list[dict]:
    """Pure planner: timeline + words -> layered cue rows (no audio I/O)."""
    if not cuts:
        return []
    cues: list[dict] = []
    seen: set[tuple[str, float]] = set()
    vacuum_ms = int(policy.get("vacuum_ms", 120))
    last = len(cuts) - 1
    final_t = float(cuts[-1].get("t0", 0.0))

    # Layer 1: the environmental bed is the music, documented as a cue row so
    # the sheet always answers "what is the glue?".
    cues.append({
        "id": "env-00-music_bed",
        "t_sec": 0.0,
        "layer": "env",
        "role": "music_bed",
        "visual_beat": "open · environmental bed",
        "file": music_label,
        "gain_offset_db": 0.0,
        "gain_db": round(float(bed_lufs), 2),
        "offset_ms": 0,
        "duck": False,
        "vacuum_ms": 0,
        "pan": 0.0,
        "align": "loop",
        "major": False,
        "note": "normalised to bed LUFS, ducked under foreground transients, "
                "vacuumed before major drops",
    })

    # Opening hook: sub weight + braam + a tactile click. The first frame is a
    # scene open, not a transition, so there is no whoosh.
    _add(cues, seen, "impact_sub", 0.0, "impact",
         "scene 1 open", offset_ms=0, duck=True, major=True,
         note="hook sub impact")
    _add(cues, seen, "impact_braam", 0.0, "impact", "scene 1 open",
         gain_offset_db=-2.0, duck=True, note="cinematic weight under the hook")
    _add(cues, seen, "click_tactile", 0.035, "tactile",
         "scene 1 open", note="high transient so the first frame is tactile")

    # Transitions: every cut gets a J-cut whoosh whose peak lands 1 frame
    # before the visual cut. Major flips also get a sub.
    for i, cut in enumerate(cuts[1:], start=1):
        if i == last:
            continue  # final gets a riser + stinger, not a generic whoosh
        t = float(cut.get("t0", 0.0))
        whip = i in {1, 2, 5} or cut.get("kind") == "hook"
        _add(cues, seen, "whoosh_whip" if whip else "whoosh_air", t,
             "anticipation", _cut_label(i, cut), offset_ms=-20,
             duck=whip, note="J-cut pre-lap; peak lands 20 ms before the cut")
        if i == 2:
            # Contrast flip: the first hard scene change gets the full
            # anticipation + vacuum + sub treatment.
            _add(cues, seen, "impact_sub", t, "impact",
                 _cut_label(i, cut), offset_ms=-15, duck=True, major=True,
                 vacuum_ms=vacuum_ms, note="contrast flip: vacuum -> sub")
        else:
            _add(cues, seen, "tick_micro", t + 0.012, "tactile",
                 f"line {i} lands · {cut.get('clip', 'clip')}",
                 gain_offset_db=-4.0, pan=(-0.18 if i % 2 else 0.18))

    # Word-level tactile accents. These are chosen from the script, not from
    # every word: the hook price, the grade, the battery, the capacity, the
    # warranty and the final CTA.
    for word in words:
        token = _ascii_token(str(word.get("w", "")))
        for pattern, events in KEYWORD_CUES:
            if not re.match(pattern, token):
                continue
            t = float(word.get("s", 0.0))
            for event in events:
                role = event["role"]
                _add(cues, seen, role, t + float(event.get("offset_sec", 0.0)),
                     "impact" if role in SUB_ROLES else
                     ("accent" if role in {"chime_lock"} else "tactile"),
                     f"word: {word.get('w', token)}",
                     gain_offset_db=float(event.get("gain_offset_db", 0.0)),
                     duck=bool(event.get("duck", False)),
                     major=bool(event.get("major", False)),
                     vacuum_ms=(vacuum_ms if event.get("vacuum_ms") is None
                                and event.get("major") else
                                int(event.get("vacuum_ms") or 0)),
                     offset_ms=-10 if role in SUB_ROLES else 0,
                     note="keyword accent" if role not in SUB_ROLES else
                          "keyword sub impact")

    # Final lock: riser ends on the cut, stinger supplies the sub weight, the
    # chime resolves the dopamine loop.
    _add(cues, seen, "riser_tension", final_t, "anticipation",
         "final CTA riser", align="end", gain_offset_db=-4.0, offset_ms=-16,
         duck=True, note="J-cut riser ends 1 frame before the final cut")
    _add(cues, seen, "impact_sub", final_t, "impact", "final lock",
         offset_ms=-15, duck=True, major=True, vacuum_ms=vacuum_ms,
         note="final lock: vacuum -> sub impact")
    _add(cues, seen, "click_tactile", final_t + 0.020, "tactile",
         "final lock", note="double-transient click on the lock")
    _add(cues, seen, "chime_lock", final_t + 0.180, "accent",
         "final lock", duck=True, note="consummatory chime")

    # Keep only events inside the piece; the renderer cannot use negative time.
    cues = [c for c in cues if -0.02 <= c["t_sec"] <= duration + 0.02]
    cues.sort(key=lambda c: (c["t_sec"], c["id"]))
    return cues


def load_manifest(cfg: dict) -> tuple[dict, Path]:
    sfx_cfg = cfg.get("sfx") or {}
    manifest_path = repo_path(sfx_cfg.get("library_manifest")
                              or "assets/sfx/library.json")
    manifest = _json(manifest_path)
    library_dir = repo_path(sfx_cfg.get("library_dir") or SFX_DIR).expanduser()
    return manifest, library_dir


def resolve_role(manifest: dict, library_dir: Path,
                 role: str) -> tuple[list[Path], dict]:
    entry = (manifest.get("roles") or {}).get(role)
    if not entry:
        sys.exit(f"[sfx] role {role!r} is missing from the library manifest")
    files: list[Path] = []
    for raw in entry.get("files") or []:
        path = (repo_path(raw[5:]) if raw.startswith("repo:")
                else library_dir / raw)
        if path.exists():
            files.append(path)
    if not files and entry.get("fallback"):
        path = repo_path(entry["fallback"])
        if path.exists():
            files.append(path)
    return files, entry


def attach_sources(cues: list[dict], manifest: dict,
                   library_dir: Path) -> list[dict]:
    for cue in cues:
        if cue["layer"] == "env":
            continue
        role = cue["role"]
        files, entry = resolve_role(manifest, library_dir, role)
        if not files:
            sys.exit(
                f"[sfx] no source for role {role!r}. Run "
                f"`./fenix sfx --install-library` or set sfx.library_dir in "
                f"config/pipeline.json (currently {library_dir}).")
        chosen = _stable_pick(files, cue["id"])
        max_sec = entry.get("max_sec")
        sample = _read_wav(chosen, float(max_sec) if max_sec else None)
        sample = _normalise(_fade(sample, 0.001, 0.02))
        align = cue.get("align") or entry.get("align") or "start"
        anchor = _anchor_index(sample, align)
        cue["file"] = display_path(chosen)
        cue["source"] = chosen.name
        cue["role_gain_db"] = round(float(entry.get("gain_db", 0.0)), 2)
        cue["gain_db"] = round(cue["role_gain_db"]
                               + float(cue.get("gain_offset_db", 0.0)), 2)
        cue["align"] = align
        cue["anchor_sec"] = round(anchor / SAMPLE_RATE, 3)
        cue["sample_sec"] = round(len(sample) / SAMPLE_RATE, 3)
        cue["onset_sec"] = round(_onset_sec(sample), 3)
        cue["start_sec"] = round(cue["t_sec"]
                                 + cue["offset_ms"] / 1000.0
                                 - cue["anchor_sec"], 3)
        cue["lead_ms"] = round((cue["start_sec"] - cue["t_sec"]) * 1000.0, 1)
        cue["onset_lead_ms"] = round(
            (cue["start_sec"] + cue["onset_sec"] - cue["t_sec"]) * 1000.0, 1)
    return cues


# --------------------------------------------------------------- audio render
def _pan_gains(pan: float) -> tuple[float, float]:
    pan = max(-1.0, min(1.0, pan))
    angle = (pan + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def render_bus(cues: list[dict], duration: float, peak_db: float) -> np.ndarray:
    n = int(math.ceil(duration * SAMPLE_RATE)) + SAMPLE_RATE
    bus = np.zeros((n, 2), dtype=np.float64)
    cache: dict[str, np.ndarray] = {}
    for cue in cues:
        if cue["layer"] == "env":
            continue
        path = cue["file"]
        if path not in cache:
            raw = _read_wav(repo_path(path)
                            if not Path(path).is_absolute() else
                            Path(path), cue.get("sample_sec") or None)
            cache[path] = _normalise(_fade(raw, 0.001, 0.02))
        sample = cache[path]
        gain = _db(float(cue.get("gain_db", 0.0)))
        gl, gr = _pan_gains(float(cue.get("pan", 0.0)))
        start = int(round(cue["start_sec"] * SAMPLE_RATE))
        end = min(n, start + len(sample))
        if end <= 0:
            continue
        src_start = max(0, -start)
        start = max(0, start)
        chunk = sample[src_start:src_start + (end - start)]
        if not len(chunk):
            continue
        bus[start:end, 0] += chunk[:, 0] * gain * gl
        bus[start:end, 1] += chunk[:, 1] * gain * gr
    peak = float(np.max(np.abs(bus))) if bus.size else 0.0
    target = _db(peak_db)
    if peak > target and peak > 1e-9:
        bus *= target / peak
    bus = bus[:int(round(duration * SAMPLE_RATE))]
    # A resolution chime may ring past the last frame; a short final fade
    # prevents a hard truncation click at the end of the master.
    fade = min(len(bus), max(1, int(0.020 * SAMPLE_RATE)))
    bus[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None]
    return bus


def _load_music(cfg: dict, duration: float) -> tuple[Path | None, np.ndarray]:
    music_cfg = cfg.get("music") or {}
    music = repo_path(music_cfg.get("file") or "")
    if not music.exists():
        pool = sorted(p for p in MUSIC_DIR.glob("*")
                      if p.suffix.lower() in (".wav", ".mp3", ".m4a"))
        music = pool[0] if pool else None
    if not music or not music.exists():
        return None, np.zeros((int(duration * SAMPLE_RATE), 2))
    proc = subprocess.run([
        "ffmpeg", "-v", "error", "-stream_loop", "-1", "-i", str(music),
        "-t", f"{duration:.3f}", "-f", "f32le", "-ac", "2",
        "-ar", str(SAMPLE_RATE), "-",
    ], capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    data = np.frombuffer(proc.stdout, dtype="<f4").reshape(-1, 2)
    return music, data.astype(np.float64)


def _envelope(n: int, events: list[dict], attack_ms: float,
              release_ms: float) -> np.ndarray:
    env = np.zeros(n, dtype=np.float64)
    attack = max(1, int(attack_ms / 1000.0 * SAMPLE_RATE))
    release = max(1, int(release_ms / 1000.0 * SAMPLE_RATE))
    for cue in events:
        start = max(0, int((cue["t_sec"] - 0.02) * SAMPLE_RATE))
        a_end = min(n, start + attack)
        r_end = min(n, a_end + release)
        if start >= n:
            continue
        env[start:a_end] = np.maximum(env[start:a_end],
                                      np.linspace(0.0, 1.0,
                                                  a_end - start))
        env[a_end:r_end] = np.maximum(env[a_end:r_end],
                                      np.linspace(1.0, 0.0,
                                                  r_end - a_end))
    return env


def _apply_vacuum(gain: np.ndarray, events: list[dict]) -> np.ndarray:
    n = len(gain)
    for cue in events:
        ms = int(cue.get("vacuum_ms", 0))
        if ms <= 0:
            continue
        start = max(0, int((cue["t_sec"] - ms / 1000.0) * SAMPLE_RATE))
        end = max(start, int(cue["t_sec"] * SAMPLE_RATE))
        if start >= n:
            continue
        end = min(n, end)
        ramp = max(1, int(0.015 * SAMPLE_RATE))
        gain[start:min(end, start + ramp)] *= np.linspace(1.0, 0.0,
                                                          min(end, start + ramp) - start)
        if end > start + ramp:
            gain[start + ramp:end] = 0.0
        after = min(n, end + int(0.030 * SAMPLE_RATE))
        if after > end:
            gain[end:after] *= np.linspace(0.0, 1.0, after - end)
    return gain


def render_music_bed(cfg: dict, cues: list[dict], duration: float,
                     target_lufs: float) -> tuple[np.ndarray | None, float]:
    music, bed = _load_music(cfg, duration)
    if music is None:
        return None, 0.0
    # Measure the raw loop on disk so the gain is a static LUFS offset, not a
    # guess based on sample peak. The probe is disposable.
    probe_dir = Path(tempfile.mkdtemp(prefix="fenix_sfx_probe_"))
    probe = probe_dir / "probe.wav"
    try:
        _write_wav(probe, bed)
        meas = _probe_loudness(probe)
        gain_db = float(target_lufs) - float(meas["input_i"])
        bed = bed * _db(gain_db)
        # Measure the gain-corrected bed before ducking/vacuum so the report
        # proves the static offset instead of echoing the target.
        _write_wav(probe, bed)
        meas = _probe_loudness(probe)
        lufs = float(meas["input_i"])
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    audio = cfg.get("audio") or {}
    fade_in = float(audio.get("music_fade_in_sec", 0.25))
    fade_out = float(audio.get("music_fade_out_sec", 0.7))
    n = len(bed)
    a = min(n, max(1, int(fade_in * SAMPLE_RATE)))
    b = min(n, max(1, int(fade_out * SAMPLE_RATE)))
    bed[:a] *= np.linspace(0.0, 1.0, a)[:, None]
    bed[-b:] *= np.linspace(1.0, 0.0, b)[:, None]

    sfx_cfg = cfg.get("sfx") or {}
    depth = 1.0 - _db(-abs(float(sfx_cfg.get("duck_db", -4.5))))
    duck_events = [c for c in cues if c.get("duck") and c["layer"] != "env"]
    env = _envelope(n, duck_events,
                    float(sfx_cfg.get("duck_attack_ms", 8)),
                    float(sfx_cfg.get("duck_release_ms", 350)))
    bed *= (1.0 - depth * env)[:, None]
    gain = _apply_vacuum(np.ones(n), [c for c in cues if c.get("vacuum_ms")])
    bed *= gain[:, None]
    return bed, lufs


# ------------------------------------------------------------------- checks
def evaluate(cues: list[dict], bus_peak_db: float, bed_lufs: float,
             policy: dict) -> list[dict]:
    rows: list[dict] = []

    def check(cid: str, ok: bool, measured, threshold, severity: str,
              fix: str) -> None:
        rows.append({"id": cid, "pass": bool(ok), "measured": measured,
                     "threshold": threshold, "severity": severity,
                     "fix": fix})

    foreground = [c for c in cues if c["layer"] != "env"]
    layers = {c["layer"] for c in foreground}
    check("sd_cues", len(foreground) >= 8, f"{len(foreground)} foreground cues",
          ">= 8", "error", "run `fenix sfx <project>` after the edit plan is locked")
    check("sd_layers", {"anticipation", "tactile", "impact"}.issubset(layers),
          ", ".join(sorted(layers)) or "none", "anticipation + tactile + impact",
          "error", "add the missing layer in `stages/sfx.py`")

    anticipation = [c for c in foreground if c["layer"] == "anticipation"]
    bad_lead = [c for c in anticipation
                if c.get("onset_lead_ms", 0) > -int(policy.get("jcut_min_ms", 150))]
    check("sd_jcut", not bad_lead,
          f"{len(bad_lead)} of {len(anticipation)} lack pre-lap",
          f">= {int(policy.get('jcut_min_ms', 150))} ms before the cut",
          "error", "start the riser/whoosh earlier so it leads the cut")

    subs = sorted((c for c in foreground
                   if c["role"] in SUB_ROLES and c.get("major")),
                  key=lambda c: c["t_sec"])
    gaps = [b["t_sec"] - a["t_sec"] for a, b in zip(subs, subs[1:])]
    min_gap = min(gaps) if gaps else 99.0
    check("sd_sub_gap", min_gap >= float(policy.get("sub_spacing_sec", 1.5)),
          f"{min_gap:.2f} s", f">= {float(policy.get('sub_spacing_sec', 1.5)):.1f} s",
          "error", "drop or move one sub hit; contrast needs recovery time")

    majors = [c for c in foreground if c.get("major") and c["t_sec"] > 0.15]
    bad_vac = [c for c in majors
               if not (80 <= int(c.get("vacuum_ms", 0)) <= 150)]
    check("sd_vacuum", not bad_vac,
          f"{len(bad_vac)} of {len(majors)} major cues lack a vacuum",
          "80-150 ms", "warn", "set vacuum_ms 80-150 on the major drop")

    check("sd_peak", bus_peak_db <= -1.0, f"{bus_peak_db:.2f} dBFS",
          "<= -1.0 dBFS", "error", "lower sfx bus gain or normalise the bus")
    check("sd_bed", abs(bed_lufs - float(policy.get("music_bed_lufs", -28.0))) <= 1.0,
          f"{bed_lufs:.2f} LUFS",
          f"{float(policy.get('music_bed_lufs', -28.0)):+.1f} +/- 1.0",
          "warn", "re-run the sfx stage; the music bed gain is measured")

    # A resolution chime is allowed to ring past the last frame (the renderer
    # trims the bus to duration), but a cue must not start before 0 or after
    # the piece ends.
    out_of_bounds = [c for c in foreground
                     if c.get("start_sec", 0) < -0.02
                     or c.get("t_sec", 0) > float(policy.get("duration_sec", 0)) + 0.02]
    check("sd_bounds", not out_of_bounds,
          f"{len(out_of_bounds)} cue(s) outside the timeline",
          "0 <= start and cue <= duration", "error",
          "move the cue; a whoosh cannot start before 0 or after the piece")
    return rows


# --------------------------------------------------------------------- main
def build(sku_dir: Path, cfg: dict, library_dir: Path | None = None,
          dry_run: bool = False, strict: bool = False,
          force_install: bool = False) -> dict:
    plan_path = sku_dir / "edit_plan.json"
    plan = _json(plan_path)
    cuts = plan.get("cuts") or []
    if not cuts:
        sys.exit(f"[sfx] {plan_path} has no cuts; run `fenix plan` first")
    duration = float(plan.get("duration_sec") or 0.0)
    if duration <= 0:
        duration = float((_json(sku_dir / "word_timings.json")
                          .get("duration_sec") or 0.0))
    words_path = sku_dir / "word_timings.json"
    words = (_json(words_path).get("words") or []) if words_path.exists() else []

    manifest, default_library = load_manifest(cfg)
    library = (library_dir or default_library).expanduser()
    sfx_cfg = cfg.get("sfx") or {}
    subdir = str(sfx_cfg.get("install_subdir") or DEFAULT_SUBDIR)
    if not dry_run:
        install_library(library, subdir, force=force_install)

    music_cfg = cfg.get("music") or {}
    music_label = display_path(repo_path(music_cfg.get("file") or ""))
    policy = {
        "vacuum_ms": int(sfx_cfg.get("vacuum_ms", 120)),
        "jcut_min_ms": int(sfx_cfg.get("jcut_min_ms", 150)),
        "sub_spacing_sec": float(sfx_cfg.get("sub_spacing_sec", 1.5)),
        "music_bed_lufs": float((cfg.get("audio") or {}).get("music_bed_lufs", -28.0)),
        "duration_sec": duration,
    }
    cues = plan_cues(cuts, words, duration, music_label,
                     policy["music_bed_lufs"], policy)
    cues = attach_sources(cues, manifest, library)

    bus = None
    bed = None
    bus_peak_db = -99.0
    bed_lufs = 0.0
    if not dry_run:
        bus = render_bus(cues, duration,
                         float(sfx_cfg.get("bus_peak_db", -3.0)))
        peak = float(np.max(np.abs(bus))) if bus.size else 0.0
        bus_peak_db = 20.0 * math.log10(max(peak, 1e-9))
        _write_wav(artifact(sku_dir, sfx_cfg.get("bus_file", "sfx_bus.wav")), bus)
        bed, bed_lufs = render_music_bed(
            cfg, cues, duration,
            float((cfg.get("audio") or {}).get("music_bed_lufs", -28.0)))
        if bed is not None:
            _write_wav(artifact(sku_dir,
                                sfx_cfg.get("music_bed_file", "music_bed.wav")),
                       bed)

    checks = evaluate(cues, bus_peak_db, bed_lufs, policy)
    if dry_run:
        # A dry run has no waveform to measure; keep the plan checks only so
        # the cue sheet can be reviewed without a false FAIL.
        checks = [c for c in checks if c["id"] not in {"sd_peak", "sd_bed"}]
    passed = all(c["pass"] for c in checks
                 if c["severity"] == "error") and (
        not strict or all(c["pass"] for c in checks))
    summary = {
        "foreground_cues": sum(1 for c in cues if c["layer"] != "env"),
        "by_layer": {layer: sum(1 for c in cues if c["layer"] == layer)
                     for layer in ("env", "anticipation", "tactile",
                                   "impact", "accent")},
        "by_role": {role: sum(1 for c in cues if c["role"] == role)
                    for role in sorted({c["role"] for c in cues})},
        "sub_hits": [round(c["t_sec"], 3) for c in cues
                     if c["role"] in SUB_ROLES],
        "vacuum_windows": [
            [round(max(0.0, c["t_sec"] - c["vacuum_ms"] / 1000.0), 3),
             round(c["t_sec"], 3)]
            for c in cues if c.get("vacuum_ms")
        ],
    }
    cue_sheet = {
        "sku": sku_dir.name,
        "generated_by": "fenix sfx",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_sec": round(duration, 3),
        "source": {
            "edit_plan": display_path(plan_path),
            "edit_plan_sha256": _sha256(plan_path),
            "word_timings": display_path(words_path) if words_path.exists() else None,
            "music": music_label,
            "library_dir": str(library),
            "manifest": display_path(repo_path(
                (cfg.get("sfx") or {}).get("library_manifest")
                or "assets/sfx/library.json")),
        },
        "policy": {
            "bpm": cfg.get("bpm"),
            "music_bed_lufs": policy["music_bed_lufs"],
            "duck_db": float(sfx_cfg.get("duck_db", -4.5)),
            "duck_attack_ms": int(sfx_cfg.get("duck_attack_ms", 8)),
            "duck_release_ms": int(sfx_cfg.get("duck_release_ms", 350)),
            "vacuum_ms": policy["vacuum_ms"],
            "jcut_min_ms": policy["jcut_min_ms"],
            "sub_spacing_sec": policy["sub_spacing_sec"],
            "bus_peak_db": float(sfx_cfg.get("bus_peak_db", -3.0)),
        },
        "summary": summary,
        "cues": cues,
    }
    report = {
        "sku": sku_dir.name,
        "generated_by": "fenix sfx",
        "generated_at": cue_sheet["generated_at"],
        "cue_sheet": display_path(artifact(
            sku_dir, sfx_cfg.get("cue_sheet_file", "sfx_cue_sheet.json"))),
        "bus": display_path(artifact(
            sku_dir, sfx_cfg.get("bus_file", "sfx_bus.wav"))) if bus is not None else None,
        "music_bed": display_path(artifact(
            sku_dir, sfx_cfg.get("music_bed_file", "music_bed.wav"))) if bed is not None else None,
        "duration_sec": round(duration, 3),
        "summary": summary,
        "levels": {
            "bus_peak_dbfs": round(bus_peak_db, 2),
            "music_bed_lufs": round(bed_lufs, 2),
            "target_bed_lufs": policy["music_bed_lufs"],
        },
        "checks": checks,
        "pass": passed,
        "strict": strict,
    }
    _write_json(artifact(sku_dir,
                         sfx_cfg.get("cue_sheet_file", "sfx_cue_sheet.json")),
                cue_sheet)
    _write_json(artifact(sku_dir, "sfx_report.json"), report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Cue-sheet-first SFX design for an edit plan")
    parser.add_argument("sku_dir", nargs="?",
                        help="SKU name (or input/<sku> path); omit with "
                             "--install-library")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--library", type=Path, default=None,
                        help="override sfx.library_dir")
    parser.add_argument("--install-library", action="store_true",
                        help="synthesize the procedural SFX core into the "
                             "configured library folder")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing procedural samples")
    parser.add_argument("--dry-run", action="store_true",
                        help="write sfx_cue_sheet.json only; no audio render")
    parser.add_argument("--strict", action="store_true",
                        help="warnings fail too")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    sfx_cfg = cfg.get("sfx") or {}
    library = repo_path(args.library
                        or sfx_cfg.get("library_dir") or SFX_DIR).expanduser()

    if args.install_library and not args.sku_dir:
        written = install_library(
            library, str(sfx_cfg.get("install_subdir") or DEFAULT_SUBDIR),
            force=args.force)
        print(f"[sfx] installed {len(written)} procedural samples in "
              f"{library / str(sfx_cfg.get('install_subdir') or DEFAULT_SUBDIR)}")
        return
    if not args.sku_dir:
        parser.error("sku_dir is required unless --install-library is used")

    sku_dir = resolve_sku_dir(args.sku_dir)
    if args.install_library:
        written = install_library(
            library, str(sfx_cfg.get("install_subdir") or DEFAULT_SUBDIR),
            force=args.force)
        print(f"[sfx] installed {len(written)} procedural samples in "
              f"{library / str(sfx_cfg.get('install_subdir') or DEFAULT_SUBDIR)}")

    report = build(sku_dir, cfg, args.library, dry_run=args.dry_run,
                   strict=args.strict, force_install=args.force)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        width = max(len(c["id"]) for c in report["checks"]) + 2
        for check in report["checks"]:
            mark = "PASS" if check["pass"] else (
                "WARN" if check["severity"] == "warn" else "FAIL")
            print(f"[{mark}] {check['id']:<{width}} "
                  f"{str(check['measured']):<34} (limit {check['threshold']})")
            if not check["pass"] and check.get("fix"):
                print(f"        fix: {check['fix']}")
        layers = report["summary"]["by_layer"]
        print(f"\n[sfx] {report['summary']['foreground_cues']} foreground cues "
              f"({', '.join(f'{k}={v}' for k, v in layers.items() if v)})")
        print(f"[sfx] bus peak {report['levels']['bus_peak_dbfs']:.2f} dBFS | "
              f"music bed {report['levels']['music_bed_lufs']:.2f} LUFS")
        print(f"[sfx] wrote {report['cue_sheet']} + sfx_report.json")
        print(f"RESULT: {'PASS' if report['pass'] else 'FAIL'}")
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
