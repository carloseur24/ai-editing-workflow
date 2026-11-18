#!/usr/bin/env python3
"""
align — voiceover prep + word-level forced alignment.

Pipeline:
  1. Decode the ElevenLabs voiceover to 16 kHz mono float32.
  2. Remove leading/trailing silence and internal dead-air pauses with a
     deterministic RMS gate (ffmpeg's silenceremove only trims the head on
     some builds, so we do it in numpy).
  3. Transcribe with faster-whisper (word timestamps, int8, CPU). No torch,
     no wav2vec2 checkpoint: `small` int8 is accurate enough for Spanish TTS
     and 10-20x lighter than the WhisperX stack.
  4. Optional script guard: trim hallucinated tail words so the last caption
     matches the written script.
  5. Tempo-fit to the target duration and rescale word timings accordingly.
  6. Write word_timings.json consumed by the renderer.

Falls back to a 300-3400 Hz vocal-bandpass energy alignment when
faster-whisper is unavailable.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from ..context import (DEFAULT_CONFIG, MODELS_DIR, artifact, load_config,
                       resolve_sku_dir)
from ..domain.narration import build_lines
from ..domain.voice import explicit_duration, is_music_only, voice_name

SR = 16000


# --------------------------------------------------------------------------- io
def decode_audio(path: Path, sr: int = SR) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path), "-f", "f32le",
         "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr),
         "-ac", "1", "-i", "-", "-c:a", "pcm_s16le", str(path)],
        input=samples.astype(np.float32).tobytes(), check=True,
    )


def audio_duration(path: Path) -> float:
    return float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]).decode().strip())


def _brief(sku_dir: Path) -> dict:
    path = sku_dir / "brief.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# ------------------------------------------------------------------- silence
def db_envelope(x: np.ndarray, sr: int, frame_ms: float = 20.0,
                hop_ms: float = 10.0) -> tuple[np.ndarray, int, int]:
    n = max(1, int(sr * frame_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if len(x) < n:
        return np.array([-90.0]), n, hop
    count = 1 + (len(x) - n) // hop
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(count, n), strides=(x.strides[0] * hop, x.strides[0])
    )
    rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1) + 1e-12)
    return 20.0 * np.log10(rms + 1e-9), n, hop


def plan_cut_regions(x: np.ndarray, sr: int, threshold_db: float,
                     min_silence: float, keep_pad: float) -> list[tuple[int, int]]:
    """Return [start,end) sample ranges to delete (head/tail pad preserved)."""
    env, n, hop = db_envelope(x, sr)
    silent = env < threshold_db
    regions: list[tuple[int, int]] = []
    i = 0
    while i < len(silent):
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < len(silent) and silent[j]:
            j += 1
        t0, t1 = i * hop / sr, (j * hop + n) / sr
        if t1 - t0 >= min_silence:
            c0, c1 = t0 + keep_pad, t1 - keep_pad
            if c1 > c0:
                regions.append((int(c0 * sr), int(c1 * sr)))
        i = j
    return regions


def concat_with_crossfade(x: np.ndarray, regions: list[tuple[int, int]],
                          sr: int, crossfade: float) -> np.ndarray:
    if not regions:
        return x
    fade = max(1, int(crossfade * sr))
    out = x[:regions[0][0]].copy()
    for idx, (s, e) in enumerate(regions):
        nxt = regions[idx + 1][0] if idx + 1 < len(regions) else len(x)
        chunk = x[e:nxt]
        if len(out) >= fade and len(chunk) >= fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            out[-fade:] *= (1.0 - ramp)
            chunk = chunk.copy()
            chunk[:fade] *= ramp
        out = np.concatenate([out, chunk])
    return out


def trim_voiceover(src: Path, dst: Path, cfg: dict) -> tuple[float, float]:
    """Trim dead air. Returns (duration_before, duration_after)."""
    vo = cfg.get("voiceover", {})
    threshold = float(vo.get("silence_threshold_db", -38.0))
    x = decode_audio(src)
    before = len(x) / SR
    regions = plan_cut_regions(
        x, SR, threshold_db=threshold,
        min_silence=float(vo.get("min_silence_sec", 0.26)),
        keep_pad=float(vo.get("keep_pad_sec", 0.05)),
    )
    y = concat_with_crossfade(x, regions, SR, float(vo.get("crossfade_sec", 0.012)))
    env, n, hop = db_envelope(y, SR)
    active = np.where(env > threshold)[0]
    if len(active):
        pad = int(0.04 * SR)
        y = y[max(0, active[0] * hop - pad):min(len(y), active[-1] * hop + n + pad)]
    write_wav(dst, y)
    return before, len(y) / SR


def speech_cut_point(src: Path, t0: float, pad: float = 0.12,
                     max_extra: float = 0.8, frame_sec: float = 0.01,
                     word_gap: float = 0.04, floor: float = 0.003
                     ) -> tuple[float, float]:
    """Where to cut so the phrase containing `t0` is complete.

    Whisper under-reports the end of the final word, so cutting at its `e`
    lands mid-syllable ("escríbenos al wha..."). This scans the decoded
    waveform forward from `t0` for the first real inter-word pause
    (`word_gap` of quiet, which normal syllable blips never reach) and
    returns `(spoken_end, cut)`:

      * `spoken_end` – the moment the last spoken syllable stops
      * `cut`        – inside that pause (after `pad`, but never past the
                       start of the next word), capped at `t0 + max_extra`
    """
    x = decode_audio(src)
    sr = SR
    a = max(0.0, t0 - 0.4)
    b = min(len(x) / sr, t0 + max_extra)
    seg = x[int(a * sr):int(b * sr)]
    step = max(1, int(frame_sec * sr))
    n = len(seg) // step
    if n == 0:
        return t0, t0
    r = np.sqrt(np.mean(seg[:n * step].reshape(n, step) ** 2, axis=1))
    thr = max(floor, float(np.percentile(r, 95)) * 0.10)
    hot = r > thr

    runs: list[tuple[float, float]] = []
    i = 0
    while i < n:
        if hot[i]:
            j = i
            while j < n and hot[j]:
                j += 1
            runs.append((a + i * frame_sec, a + j * frame_sec))
            i = j
        else:
            i += 1

    for k, (rs, re_) in enumerate(runs):
        if rs <= t0 <= re_ or 0.0 <= rs - t0 <= 0.15:
            # walk syllable blips forward until a genuine pause appears
            cur_end = re_
            for nxt_s, nxt_e in runs[k + 1:]:
                if nxt_s - cur_end >= word_gap:
                    return (round(cur_end, 3),
                            round(min(cur_end + pad, nxt_s), 3))
                cur_end = nxt_e
            # no pause found: stop where the waveform stops, never cut into
            # the word that is still sounding
            spoken_end = min(cur_end, t0 + max_extra)
            return round(spoken_end, 3), round(spoken_end, 3)
    return round(t0, 3), round(t0, 3)


def tail_silence(src: Path) -> float:
    """Length of the trailing silence in seconds (0 = file ends mid-speech)."""
    x = decode_audio(src)
    sr = SR
    if len(x) == 0:
        return 0.0
    peak = float(np.percentile(np.abs(x), 99.5)) or 1e-6
    thr = max(0.002, peak * 0.06)
    step = max(1, int(0.01 * sr))
    n = len(x) // step
    env = np.sqrt(np.mean(x[:n * step].reshape(n, step) ** 2, axis=1))
    quiet = 0
    for v in env[::-1]:
        if v > thr:
            break
        quiet += 1
    return round(quiet * 0.01, 3)


# ------------------------------------------------------------------ whisper
def transcribe_words(wav: Path, cfg: dict) -> tuple[list[dict], str, str]:
    al = cfg.get("alignment", {})
    from faster_whisper import WhisperModel  # imported late: optional engine

    audio = decode_audio(wav)
    model = WhisperModel(
        al.get("model", "small"),
        device="cpu",
        compute_type=al.get("compute_type", "int8"),
        cpu_threads=int(al.get("cpu_threads", 8)),
        download_root=str(MODELS_DIR),
    )
    segments, info = model.transcribe(
        audio,
        language=al.get("language", "es"),
        beam_size=int(al.get("beam_size", 5)),
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    text_parts: list[str] = []
    for seg in segments:
        text_parts.append(seg.text.strip())
        for w in (seg.words or []):
            token = (w.word or "").strip()
            if not token:
                continue
            item = {"w": token, "s": round(float(w.start), 3),
                    "e": round(float(w.end), 3)}
            # Whisper attaches the sentence punctuation to the word token, and
            # a segment often holds two sentences ("Es grado B. Tiene..."), so
            # the word is the reliable carrier. The chunker uses `sn` to keep
            # a caption card from straddling a sentence break.
            if re.search(r"[.!?…][\"'”’)\]]*$", token):
                item["sn"] = 1
            elif token.rstrip()[-1:] in {",", ";", ":"}:
                item["cs"] = 1     # clause break, weaker than a sentence end
            words.append(item)
    return words, info.language, " ".join(text_parts).strip()


def fallback_energy_align(wav: Path, script_text: str, duration: float) -> list[dict]:
    """300-3400 Hz vocal-bandpass energy-valley fallback (no ASR available)."""
    from scipy.signal import butter, filtfilt

    x = decode_audio(wav)
    b, a = butter(4, [300 / (SR / 2), 3400 / (SR / 2)], btype="band")
    vocal = np.abs(filtfilt(b, a, x))
    win = max(1, int(0.02 * SR))
    env = np.convolve(vocal, np.ones(win) / win, mode="same")
    active = np.where(env > np.percentile(env, 25))[0]
    t_start = float(active[0] / SR) if len(active) else 0.2
    t_end = min(duration, float(active[-1] / SR) if len(active) else duration - 0.2)
    tokens = [t for t in script_text.split() if t]
    if not tokens:
        return []
    weights = np.array([max(1, len(t)) for t in tokens], dtype=np.float32)
    weights /= weights.sum()
    words, cur = [], t_start
    span = max(0.5, t_end - t_start)
    for tok, w in zip(tokens, weights):
        d = float(w * span)
        words.append({"w": tok.upper(), "s": round(cur, 3), "e": round(cur + d, 3)})
        cur += d
    return words


# ------------------------------------------------------------------- tokens
_DISPLAY_STRIP = '.,;:!?¡¿"\'()[]{}…“”‘’'


_FLAGS = ("sn", "cs")


def _flags(word: dict) -> dict:
    """Carry sentence/clause markers across token rewrites."""
    return {k: word[k] for k in _FLAGS if word.get(k)}


def normalize_key(token: str) -> str:
    t = token.upper().strip(_DISPLAY_STRIP + " ")
    return re.sub(r"\s+", " ", t)


def sanitize_words(words: list[dict]) -> list[dict]:
    clean: list[dict] = []
    prev_end = 0.0
    for w in words:
        key = normalize_key(w["w"])
        if not re.search(r"[A-Z0-9ÁÉÍÓÚÑÜ]", key):
            # "%" / "$" / "-29" style tokens: attach to the previous word so
            # captions read "100%" and "C-29" instead of dropping the symbol.
            if clean and key and len(key) <= 3 and not key.isalpha():
                clean[-1]["w"] = clean[-1]["w"] + key
                clean[-1]["e"] = round(max(clean[-1]["e"], float(w["e"])), 3)
                prev_end = clean[-1]["e"]
            continue
        if clean and key.startswith("-") and re.search(r"\d", key):
            clean[-1]["w"] = clean[-1]["w"] + key
            clean[-1]["e"] = round(max(clean[-1]["e"], float(w["e"])), 3)
            prev_end = clean[-1]["e"]
            continue
        s = max(float(w["s"]), prev_end)
        e = max(float(w["e"]), s + 0.05)
        item = {"w": key, "s": round(s, 3), "e": round(e, 3)}
        item.update(_flags(w))   # keep Whisper's sentence/clause markers
        clean.append(item)
        prev_end = e
    return clean


def apply_display_merge(words: list[dict], merge_map: dict) -> list[dict]:
    """Merge two consecutive ASR tokens into one display token.

    Example: {"ES SIM": "E-SIM"} — Whisper splits ElevenLabs' "e-sim" into two
    words; the caption should still read E-SIM.
    """
    if not merge_map:
        return words
    lookup = {tuple(normalize_key(k).split()): v for k, v in merge_map.items()}
    out: list[dict] = []
    i = 0
    while i < len(words):
        if i + 1 < len(words):
            pair = (words[i]["w"], words[i + 1]["w"])
            if pair in lookup:
                item = {"w": lookup[pair], "s": words[i]["s"],
                        "e": words[i + 1]["e"]}
                item.update(_flags(words[i]))
                item.update(_flags(words[i + 1]))
                out.append(item)
                i += 2
                continue
        out.append(words[i])
        i += 1
    return out


def apply_display_map(words: list[dict], display_map: dict) -> list[dict]:
    """Map a normalized ASR token to one or more display tokens.

    Example: {"14X330": ["14", "×", "330"]}. The original duration is split
    evenly so captions keep moving instead of freezing on one blob.
    """
    if not display_map:
        return words
    lookup = {normalize_key(k): v for k, v in display_map.items()}
    out: list[dict] = []
    for w in words:
        repl = lookup.get(w["w"])
        if not repl:
            out.append(w)
            continue
        span, n = w["e"] - w["s"], len(repl)
        for i, text in enumerate(repl):
            item = {"w": text,
                    "s": round(w["s"] + span * i / n, 3),
                    "e": round(w["s"] + span * (i + 1) / n, 3)}
            if i == n - 1:
                item.update(_flags(w))     # marker belongs to the last split
            out.append(item)
    return out


def script_tail_cut(words: list[dict], script_text: str,
                    min_similarity: float) -> tuple[list[dict], float | None, float]:
    """Drop hallucinated ASR words after the written script's final token."""
    script_tokens = [t for t in (normalize_key(t) for t in script_text.split()) if t]
    asr_tokens = [w["w"] for w in words]
    if not script_tokens or not asr_tokens:
        return words, None, 0.0

    sm = difflib.SequenceMatcher(a=asr_tokens, b=script_tokens, autojunk=False)
    ratio = sm.ratio()
    if ratio < min_similarity:
        return words, None, ratio

    last_equal = None
    for op in sm.get_opcodes():
        if op[0] == "equal":
            last_equal = op
    if last_equal is None:
        return words, None, ratio

    _, _, i2, _, j2 = last_equal
    # only trim when the match reaches the end of the script
    if j2 < len(script_tokens) - 1 or i2 >= len(asr_tokens):
        return words, None, ratio
    return words[:i2], words[i2 - 1]["e"], ratio


# -------------------------------------------------------------------- tempo
def tempo_fit(src: Path, dst: Path, target_sec: float,
              max_tempo: float) -> tuple[float, float]:
    dur = audio_duration(src)
    tempo = 1.0
    if dur > target_sec + 0.02:
        tempo = min(max_tempo, dur / target_sec)
    if tempo > 1.001:
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-af", f"atempo={tempo:.6f}", "-ar", str(SR), "-ac", "1", str(dst),
        ], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                        "-ar", str(SR), "-ac", "1", str(dst)], check=True)
        tempo = 1.0
    return tempo, audio_duration(dst)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Prepare + align a Fenixsoft voiceover")
    parser.add_argument("sku_dir", help="SKU name (or input/<sku> path)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--target-sec", type=float, default=None)
    parser.add_argument("--engine", choices=["auto", "faster-whisper", "energy"],
                        default="auto")
    parser.add_argument("--refresh-asr", action="store_true",
                        help="ignore the cached transcript and re-run ASR")
    args = parser.parse_args(argv)

    sku_dir: Path = resolve_sku_dir(args.sku_dir)
    cfg = load_config(args.config, project=sku_dir)
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    if is_music_only(sku):
        duration = explicit_duration(sku, _brief(sku_dir), cfg)
        print("[align] music-only project (voiceover: "
              f"{sku.get('voiceover')!r}): nothing to align.")
        print(f"[align] duration comes from sku/brief duration_sec = "
              f"{duration:.2f}s; captions stay off.")
        print("[align] next: ./fenix plan <project> (locked by brief.story.shots)")
        return
    voice_in = sku_dir / voice_name(sku)
    if not voice_in.exists():
        sys.exit(f"[ERROR] Missing voiceover: {voice_in}")

    target = args.target_sec or float(cfg.get("target_duration_sec", 28.0))

    clean_wav = sku_dir / "_voice_clean.wav"
    before, after = trim_voiceover(voice_in, clean_wav, cfg)
    print(f"[align] dead-air trim: {before:.2f}s -> {after:.2f}s "
          f"({before - after:.2f}s removed)")

    detected_text, method = "", "energy_fallback"
    asr_cache = sku_dir / ".asr_cache.json"
    audio_sha = hashlib.sha1(clean_wav.read_bytes()).hexdigest()
    cached = None
    if asr_cache.exists() and not args.refresh_asr:
        try:
            data = json.loads(asr_cache.read_text(encoding="utf-8"))
            # schema 2 carries the per-segment sentence-end flags ("sn");
            # schema 1 caches cannot be used for sentence-aware caption cards.
            if (data.get("audio_sha1") == audio_sha and data.get("words")
                    and data.get("schema") == 3):
                cached = data
        except json.JSONDecodeError:
            cached = None

    try:
        if args.engine == "energy":
            raise RuntimeError("forced energy engine")
        if cached:
            words = cached["words"]
            lang = cached.get("language", "es")
            detected_text = cached.get("detected_text", "")
            method = cached.get("method", "cached_asr")
            print(f"[align] ASR cache hit ({len(words)} words)")
        else:
            words, lang, detected_text = transcribe_words(clean_wav, cfg)
            method = f"faster_whisper_{cfg['alignment'].get('model', 'small')}_int8"
            asr_cache.write_text(json.dumps({
                "schema": 3, "audio_sha1": audio_sha, "language": lang,
                "detected_text": detected_text, "method": method,
                "words": words,
            }, ensure_ascii=False), encoding="utf-8")
        print(f"[align] ASR language={lang} words={len(words)}")
        print(f"[align] heard: {detected_text[:140]}...")
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on any ASR failure
        print(f"[align] ASR unavailable ({exc}); using bandpass fallback.")
        words = fallback_energy_align(clean_wav, sku.get("script_text", ""), after)

    words = sanitize_words(words)
    warnings: list[str] = []
    ratio = 0.0

    if cfg.get("alignment", {}).get("trim_tail_to_script", True) and sku.get("script_text"):
        words, cut_at, ratio = script_tail_cut(
            words, sku["script_text"],
            float(cfg["alignment"].get("min_script_similarity", 0.55)))
        print(f"[align] script similarity={ratio:.2f}")
        if cut_at is not None and cut_at + 0.05 < after:
            vo_cfg = cfg.get("voiceover", {})
            # complete the phrase: cut where the waveform goes quiet, not at
            # Whisper's (early) end time for the last word
            spoken_end, keep = speech_cut_point(
                clean_wav, cut_at,
                pad=float(vo_cfg.get("end_pad_sec", 0.12)),
                max_extra=float(vo_cfg.get("tail_extension_max_sec", 0.8)))
            print(f"[align] tail guard: ASR end {cut_at:.2f}s, "
                  f"waveform end {spoken_end:.2f}s, cut {keep:.2f}s")
            trimmed = clean_wav.with_name("_voice_trim.wav")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clean_wav),
                            "-t", f"{keep:.3f}", "-ar", str(SR), "-ac", "1",
                            str(trimmed)], check=True)
            trimmed.replace(clean_wav)
            after = audio_duration(clean_wav)
            print(f"[align] tail guard: cut at {keep:.2f}s -> {after:.2f}s")

    final_wav = sku_dir / "voice_28s.wav"
    tempo, final_dur = tempo_fit(clean_wav, final_wav, target,
                                 float(cfg.get("voiceover", {}).get("max_tempo", 1.3)))
    if tempo > 1.001:
        words = [{**w, "s": round(w["s"] / tempo, 3),
                  "e": round(w["e"] / tempo, 3)} for w in words]
        print(f"[align] tempo x{tempo:.3f} -> {final_dur:.2f}s")
    max_extra = float(cfg.get("voiceover", {}).get("max_extra_sec", 2.0))
    tail_pad = float(cfg.get("voiceover", {}).get("tail_pad_sec", 0.0))
    if tail_pad > 0.01:
        padded = final_wav.with_name("_voice_pad.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final_wav),
                        "-af", f"apad=pad_dur={tail_pad:.3f}",
                        "-ar", str(SR), "-ac", "1", str(padded)], check=True)
        padded.replace(final_wav)
        final_dur = audio_duration(final_wav)
        print(f"[align] tail pad +{tail_pad:.2f}s -> {final_dur:.2f}s "
              f"(the last word ends before the file does)")
    tail_sil = tail_silence(final_wav)
    tail_ok = tail_sil >= 0.03
    if not tail_ok:
        warnings.append(f"voiceover ends mid-speech (only {tail_sil:.2f}s of "
                        f"trailing silence) - the last phrase is clipped")
    if final_dur > target + 0.15:
        if tail_ok and final_dur <= target + max_extra:
            print(f"[align] extended to {final_dur:.2f}s (target "
                  f"{target:.1f}s, allowed +{max_extra:.1f}s) so the last "
                  f"phrase is complete")
        else:
            warnings.append(f"voiceover {final_dur:.2f}s exceeds target "
                            f"{target:.1f}s (allowed {target + max_extra:.1f}s); "
                            f"shorten script_text")

    words = apply_display_merge(words, sku.get("display_merge", {}))
    words = apply_display_map(words, sku.get("display_map", {}))
    words = [w for w in words if w["s"] < final_dur]
    if words:
        words[-1]["e"] = min(words[-1]["e"], final_dur)
    if not words:
        warnings.append("no aligned words produced; captions will be empty")

    clean_wav.unlink(missing_ok=True)
    out_json = sku_dir / "word_timings.json"
    out_json.write_text(json.dumps({
        "method": method,
        "language": cfg.get("alignment", {}).get("language", "es"),
        "duration_sec": round(final_dur, 3),
        "tempo": round(tempo, 4),
        "script_similarity": round(ratio, 3),
        "tail_complete": tail_ok,
        "tail_silence_sec": tail_sil,
        "detected_text": detected_text,
        "source": {
            "voice": voice_in.name,
            "voice_sha256": hashlib.sha256(voice_in.read_bytes()).hexdigest(),
            "script_sha256": hashlib.sha256(
                (sku.get("script_text") or "").encode("utf-8")).hexdigest(),
            "display_merge": sku.get("display_merge", {}),
        },
        "words": words,
        "warnings": warnings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[align] wrote {len(words)} words -> {out_json} ({method})")
    for w in warnings:
        print(f"[align] WARNING: {w}")

    # Narration lines: the planner's unit of meaning (sentence / clause).
    if sku.get("script_text"):
        lines = build_lines(sku["script_text"], words, final_dur)
        lines_path = artifact(sku_dir, "narration_lines.json")
        lines_path.parent.mkdir(parents=True, exist_ok=True)
        lines_path.write_text(json.dumps(lines, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"[align] {lines['units']} narration lines "
              f"(script similarity {lines['script_similarity']:.2f}) -> "
              f"{lines_path}")


if __name__ == "__main__":
    main()
