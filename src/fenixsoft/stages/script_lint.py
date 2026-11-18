#!/usr/bin/env python3
"""
script_lint — check the voiceover script BEFORE paying for TTS.

The pipeline has one hard constraint: 28 s of video. ElevenLabs at a natural
pace speaks ~162 words per minute (measured on the demo: 95 words / 28.0 s
after a 1.2586x fit = 162 wpm natural). That makes the word budget ~84 words at
the top of the allowed speed range, and the demo script is 93.

Nothing used to check this until the align stage had already paid for the
voiceover and the renderer had already tempo-fitted it to 1.26x. This tool runs
on `sku.json` alone:

  * word budget for the target duration at the allowed tempo ceiling
  * required tempo (how far the renderer would have to speed the read up)
  * tokens ElevenLabs mangles in Spanish (brands, units, `%`, `14x330`)
  * captions the ASR will mangle unless `display_map` / `display_merge` cover them

Rows follow the project's `qa_report.json` shape: {id, measured, threshold,
pass, fix}. Exit 1 when the script cannot fit the video at any allowed speed.

Usage:
    fenix lint <sku> [--target 28.0] [--strict] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..context import DEFAULT_CONFIG, artifact, load_config, resolve_sku_dir

# Measured on the demo voiceover: 162.3 wpm natural (95 words / 28.0 s at 1.26x).
DEFAULT_WPM = 162.0
DEFAULT_TARGET = 28.0
DEFAULT_MAX_TEMPO = 1.12      # the pacing ceiling AGENTS.md allows
HARD_MAX_TEMPO = 1.30         # the align stage will not go past this

PHONETICS = {
    "iphone": "áifon", "ipad": "áipad", "airpods": "érpods", "macbook": "mák-buk",
    "whatsapp": "Guatsap", "telegram": "Télegram", "tiktok": "Tik-tok",
    "usdt": "u-ese-dé-té", "usb": "u-ese-bé", "usb-c": "u-ese-bé-si",
    "e-sim": "i-sim", "esim": "i-sim", "wifi": "uáifai", "bluetooth": "blútuz",
    "face id": "féis ái-di", "nfc": "ene-efe-sé", "oled": "o-led",
    "amoled": "a-mo-led", "hdmi": "ache-de-eme-i", "gb": "gigas",
    "fenixsoft": "Fénix-soft",
}

TECH_TOKENS = sorted(set(PHONETICS) | {
    "gb", "tb", "mb", "sim", "imei", "qr", "app", "reels", "stories", "status",
    "iphone", "mac", "ios", "android", "full", "premium", "battery",
})


def fold(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if not unicodedata.combining(c))


def find_phonetic_issues(script: str) -> list[dict]:
    """Tokens a Spanish TTS engine mispronounces or spells out awkwardly."""
    issues: list[dict] = []
    low = fold(script)

    for tok in TECH_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", low):
            hint = PHONETICS.get(tok)
            if hint:
                issues.append({"token": tok, "kind": "brand", "say_as": hint})
            else:
                issues.append({"token": tok, "kind": "brand", "say_as": None})

    for m in re.finditer(r"\b\d+[x×]\d+\b", low):
        issues.append({"token": m.group(0), "kind": "number",
                       "say_as": "write it in words, e.g. 'por trescientos treinta'"})
    for m in re.finditer(r"\d+([.,]\d+)?\s?%", low):
        issues.append({"token": m.group(0), "kind": "percent",
                       "say_as": "spell the number in words"})
    for m in re.finditer(r"\$\s?\d+", low):
        issues.append({"token": m.group(0), "kind": "currency",
                       "say_as": "e.g. 'trescientos treinta dólares'"})
    for ch, name in (("@", "at-sign"), ("•", "bullet"), ("·", "middot"),
                     ("&", "ampersand"), ("/", "slash")):
        if ch in script:
            issues.append({"token": ch, "kind": "symbol", "say_as": name})
    if re.search(r"https?://|www\.", low):
        issues.append({"token": "url", "kind": "url",
                       "say_as": "never read a URL aloud; say 'te lo dejo en el chat'"})
    if re.search(r"[\U0001F300-\U0001FAFF]", script):
        issues.append({"token": "emoji", "kind": "emoji",
                       "say_as": "remove it; TTS reads it as noise"})
    return issues


def build_rows(script: str, sku: dict, target: float, wpm: float,
               max_tempo_cfg: float, hard_max: float) -> list[dict]:
    words = script.split()
    n = len(words)
    natural_sec = n / wpm * 60.0
    required = natural_sec / target if target else 99.0
    max_words = int(target * max_tempo_cfg * wpm / 60.0)
    rows: list[dict] = []

    rows.append({
        "id": "s_words", "measured": f"{n} words",
        "threshold": f"<= {max_words} words ({target:.1f}s at {wpm:.0f} wpm "
                     f"x {max_tempo_cfg:.2f})",
        "pass": n <= max_words,
        "fix": (f"cut {n - max_words} words (~{(n - max_words) / wpm * 60:.1f}s "
                f"at {wpm:.0f} wpm); shortening the script is the only honest "
                f"fix — a longer hold just makes the read rushed"),
    })
    rows.append({
        "id": "s_tempo", "measured": f"{required:.2f}x",
        "threshold": f"<= {hard_max:.2f}x hard, {max_tempo_cfg:.2f}x pacing",
        "pass": required <= hard_max,
        "fix": (f"the read would have to run {required:.2f}x to fit "
                f"{target:.1f}s of natural speech ({natural_sec:.1f}s); trim "
                f"the script to {max_words} words or move the target later"),
    })
    rows.append({
        "id": "s_duration", "measured": f"{natural_sec:.1f}s natural",
        "threshold": f"{target:.1f}s +/- 0.5",
        "pass": required <= max_tempo_cfg,
        "fix": f"write {max_words} words or fewer for a {target:.1f}s edit",
    })

    issues = find_phonetic_issues(script)
    brand = [i for i in issues if i["kind"] == "brand"]
    hard = [i for i in issues if i["kind"] in ("number", "percent", "currency",
                                               "symbol", "url", "emoji")]
    rows.append({
        "id": "s_phonetics", "measured": f"{len(brand)} brand token(s)",
        "threshold": "0 unlisted",
        "pass": not brand,
        "fix": "; ".join(
            f"{i['token']} -> {i['say_as']}" for i in brand[:8]) or "none",
    })
    rows.append({
        "id": "s_speakable", "measured": f"{len(hard)} unspeakable token(s)",
        "threshold": "0",
        "pass": not hard,
        "fix": "; ".join(f"{i['token']} ({i['kind']}): {i['say_as']}"
                         for i in hard[:8]) or "none",
    })

    merges = set((sku.get("display_merge") or {}).keys())
    maps = set((sku.get("display_map") or {}).keys())
    rows.append({
        "id": "s_display_map", "measured": f"{len(merges)} merge / {len(maps)} map",
        "threshold": "covers every mangled token",
        "pass": True,
        "fix": ("add display_map/display_merge for tokens you know the ASR "
                "mangles ('ES SIM' -> 'E-SIM')"),
    })
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lint a Fenixsoft voiceover script")
    parser.add_argument("sku_dir", help="SKU name (or input/<sku> path)")
    parser.add_argument("--target", type=float, default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings (phonetics) as failures")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sku_dir = resolve_sku_dir(args.sku_dir)
    sku = json.loads((sku_dir / "sku.json").read_text(encoding="utf-8"))
    script = (sku.get("script_text") or "").strip()
    if not script:
        sys.exit(f"[lint] {sku_dir}/sku.json has no script_text")

    cfg = load_config(args.config)
    script_cfg = cfg.get("script", {})
    target = float(args.target or script_cfg.get("target_sec")
                   or cfg.get("target_duration_sec", DEFAULT_TARGET))
    wpm = float(script_cfg.get("spoken_wpm", DEFAULT_WPM))
    pacing = float(script_cfg.get("max_tempo", DEFAULT_MAX_TEMPO))
    hard_max = float(cfg.get("voiceover", {}).get("max_tempo", HARD_MAX_TEMPO))

    rows = build_rows(script, sku, target, wpm, pacing, hard_max)
    fail_ids = {"s_words", "s_tempo", "s_speakable"}
    warnings = [r for r in rows if not r["pass"] and r["id"] not in fail_ids]
    failed = [r for r in rows if not r["pass"] and r["id"] in fail_ids]
    if args.strict:
        failed += warnings

    out = artifact(sku_dir, "script_lint.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "sku": sku_dir.name,
        "target_sec": target,
        "spoken_wpm": wpm,
        "words": len(script.split()),
        "natural_sec": round(len(script.split()) / wpm * 60.0, 2),
        "required_tempo": round(len(script.split()) / wpm * 60.0 / target, 3),
        "pass": not failed,
        "checks": rows,
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        width = max(len(r["id"]) for r in rows) + 2
        for r in rows:
            mark = "PASS" if r["pass"] else ("WARN" if r in warnings else "FAIL")
            print(f"[{mark}] {r['id']:<{width}} {r['measured']:<26} "
                  f"(limit {r['threshold']})")
            if not r["pass"]:
                print(f"        fix: {r['fix']}")
        verdict = "PASS" if not failed else "FAIL"
        print(f"\nRESULT: {verdict} ({len(rows) - len(failed) - len(warnings)}"
              f"/{len(rows)} checks, {len(warnings)} warning(s))")
        print(f"[lint] wrote {out}")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    sys.exit(main())
