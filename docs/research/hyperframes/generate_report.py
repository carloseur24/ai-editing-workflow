#!/usr/bin/env python3
"""Generate report.md from research-hyperframes/results/*.json + fields.yaml."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
FIELDS = ROOT / "fields.yaml"
OUT = ROOT / "report.md"

EXEC_SUMMARY = """## Executive summary

HyperFrames is a deterministic frame server, not a timeline editor. For every output
frame it computes `t = floor(frame) / fps`, seeks registered paused timelines to that
exact time in headless Chrome, captures, then encodes with FFmpeg. `<video>` is never
played during capture: sources are pre-extracted into per-frame stills and injected as
`<img>`. Time is an input, never an emergent property.

For this pipeline the evidence supports one shape: **augment, do not replace.**
`plan_edit.py` and the FFmpeg cut assembly stay the source of truth for footage and
beat-synced cuts. HyperFrames takes the layer it is strictly better at - captions,
price pill, badges, footer, transitions - and returns a transparent overlay that
`render_video.py` composites in one extra input. Measured composite overhead is +4%.

Three measured facts drive the operational plan:

| Fact | Measured on this box | Consequence |
| --- | --- | --- |
| 7.9 GB RAM trips the low-memory profile | auto = 1 worker, 6 m 31 s | pass `--no-low-memory-mode --workers 4` -> 1 m 48 s |
| Per-worker memory is 414 MB PSS, not ~256 MB | 4 workers fit, 6 risky | cap workers, never use `auto` |
| WSL2 exposes the Radeon through /dev/dxg | 8.6 fps vs 6.1 on SwiftShader | keep the GPU flags HyperFrames passes |

The locked layout needs one decision: HyperFrames' own Reels safe zone is
y 269-1248, x 65-1015, so the current header (110-318) and bottom stack
(1305-1628) sit outside it - 14 `outside_safe_zone` findings and contrast
between 1.25:1 and 2.58:1 against a 3:1 floor. Either the spec is intentionally
wider than Instagram Reels (defensible for WhatsApp Status, which is why it was
drawn), or the caption band must move. That is a product call, not a bug.
"""


def load_schema():
    schema = yaml.safe_load(FIELDS.read_text())
    cats = []
    for cat in schema.get("categories", []):
        cats.append(
            {
                "name": cat["name"],
                "key": cat.get("key", cat["name"]),
                "fields": [f["name"] for f in cat.get("fields", [])],
            }
        )
    return cats


def flatten(data: dict) -> dict:
    """Return field -> value for flat or one-level-nested JSON."""
    if any(k in data for k in ("basic", "capabilities", "api", "integration", "constraints", "evidence")):
        out = {}
        for value in data.values():
            if isinstance(value, dict):
                out.update(value)
        return out
    return data


def slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text)


def render_value(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, dict):
        parts = [f"**{k}**: {render_value(v)}" for k, v in value.items()]
        return "; ".join(parts)
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(v, str) for v in value):
            return "\n".join(f"- {v}" for v in value)
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append("- " + " | ".join(f"{k}: {render_value(v)}" for k, v in item.items()))
            else:
                lines.append(f"- {render_value(item)}")
        return "\n".join(lines)
    return str(value)


def clean(text: str) -> str:
    return " ".join(str(text).split())


def main() -> None:
    cats = load_schema()
    items = []
    for path in sorted(RESULTS.glob("*.json")):
        data = json.loads(path.read_text())
        fields = flatten(data)
        uncertain = set(data.get("uncertain", []))
        title = fields.get("title") or path.stem.replace("_", " ")
        items.append({"title": title, "fields": fields, "uncertain": uncertain})

    items.sort(key=lambda i: i["title"].lower())

    lines = ["# HyperFrames integration research", "", EXEC_SUMMARY, "", "## Contents", ""]
    for n, item in enumerate(items, 1):
        meta = []
        for key in ("maturity", "confidence"):
            val = item["fields"].get(key)
            if isinstance(val, (int, float)):
                meta.append(f"{key}: {val:.2f}")
            elif val:
                meta.append(f"{key}: {val}")
        suffix = f" — {', '.join(meta)}" if meta else ""
        lines.append(f"{n}. [{item['title']}](#{slug(item['title'])}){suffix}")

    lines += ["", "---", ""]

    for item in items:
        lines += [f'<a id="{slug(item["title"])}"></a>', f'## {item["title"]}', ""]
        seen = set()
        for cat in cats:
            cat_lines = []
            for field in cat["fields"]:
                if field in item["uncertain"]:
                    continue
                value = item["fields"].get(field)
                if value in (None, "", [], {}):
                    continue
                rendered = render_value(value)
                if "[uncertain]" in rendered or "uncertain]" in rendered:
                    continue
                cat_lines += [f"**{field.replace('_', ' ').title()}**", "", rendered, ""]
                seen.add(field)
            if cat_lines:
                lines += [f"### {cat['name']}", ""] + cat_lines
        extra = {
            k: v
            for k, v in item["fields"].items()
            if k not in seen and k != "title" and v not in (None, "", [], {})
        }
        if extra:
            lines += ["### Other Info", ""]
            for key, value in extra.items():
                lines += [f"**{key.replace('_', ' ').title()}**", "", render_value(value), ""]
        lines += ["---", ""]

    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(items)} items)")


if __name__ == "__main__":
    main()
