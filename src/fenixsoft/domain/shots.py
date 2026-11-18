#!/usr/bin/env python3
"""Order-locked shot tables from ``brief.json``.

The planner normally derives cut durations from voiceover lines, which is wrong
for a piece whose order is already decided (a product film cut to music, a
scene-by-scene brief). ``story.shots`` records that order once, in the brief:

    "story": {"shots": [
        {"clip": "01_hook.mp4", "in": 1.0, "dur": 5.5,
         "label": "hook: box in hand", "spec": "PRODUCT NAME"},
        {"clip": "02_detail.mp4", "in": 0.25, "dur": 6.0, "label": "detail"}
    ]}

This module is pure: it validates the table against measured clip durations and
turns it into the renderer's ``edit_plan.json`` payload. Probing media and
writing files stay in the stages.
"""
from __future__ import annotations

from typing import Any

DEFAULTS = {
    "source_tolerance_sec": 0.02,
    "sum_tolerance_sec": 0.25,
}

# ``generated_by`` markers written by the planner. ``plan_edit.py`` is the
# pre-package module name: legacy plans are machine output and must keep being
# replaced when their inputs change, unlike a hand-authored cut list.
PLANNER_MARKERS = ("fenix plan", "plan_edit")


def is_manual_plan(plan: dict | None) -> bool:
    """True for an existing cut list that no planner wrote.

    A hand-authored plan (e.g. a storyboard-derived order pasted into
    ``input/<p>/edit_plan.json``) has cuts but no planner stamp. It is the
    delivered order until the user overrides it, so ``fenix plan`` and
    ``fenix make`` must keep it and only ``--force`` may replace it. A plan
    written by ``fenix plan`` (or the legacy ``plan_edit.py``) stays
    machine-owned; a plan that is already ``locked`` is handled by the brief.
    """
    if not isinstance(plan, dict) or plan.get("locked"):
        return False
    cuts = plan.get("cuts")
    if not isinstance(cuts, list) or not cuts:
        return False
    marker = str(plan.get("generated_by") or "").strip().lower()
    return not any(token in marker for token in PLANNER_MARKERS)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def shots_from_brief(brief: dict) -> list[dict] | None:
    """Normalize ``brief.story.shots``; ``None`` when the brief has no table."""
    story = brief.get("story") if isinstance(brief, dict) else None
    raw = (story or {}).get("shots")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return [{"index": 0, "invalid": True, "clip": "", "in": None,
                 "dur": None, "label": "", "spec": ""}]
    shots: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            shots.append({"index": i, "invalid": True, "clip": "",
                          "in": None, "dur": None, "label": "", "spec": ""})
            continue
        shots.append({
            "index": i,
            "clip": str(item.get("clip") or "").strip(),
            "in": _number(item.get("in", 0.0)),
            "dur": _number(item.get("dur")),
            "label": str(item.get("label") or item.get("text") or "").strip(),
            "spec": str(item.get("spec") or "").strip(),
            # Optional safe-zone band for the shot's title/spec card. The
            # renderer resolves it from config.safe_zones so a spec can sit in
            # the lower third without copying a pixel coordinate.
            "text_zone": str(item.get("text_zone") or "").strip() or None,
        })
    return shots


def row(rid: str, measured, threshold, ok: bool, fix: str,
        severity: str = "error") -> dict:
    """Same row shape as ``validate_plan``/QA: one number, one threshold, one fix."""
    return {"id": rid, "measured": measured, "threshold": threshold,
            "pass": bool(ok), "severity": severity, "fix": fix}


def _fmt_shots(items: list[dict], fields: tuple[str, ...] = ("index", "clip"),
                limit: int = 6) -> str:
    parts = []
    for item in items[:limit]:
        parts.append(", ".join(f"{f}={item.get(f)!r}" for f in fields))
    return "; ".join(parts)


def _bad_fields(shot: dict) -> list[str]:
    """Names of the required fields a shot entry is missing or invalid on."""
    bad: list[str] = []
    if shot.get("invalid") or not shot.get("clip"):
        bad.append("clip")
    if shot.get("in") is None:
        bad.append("in")
    elif shot["in"] < -1e-9:
        bad.append("in<0")
    if shot.get("dur") is None:
        bad.append("dur")
    elif shot["dur"] <= 1e-9:
        bad.append("dur<=0")
    return bad


def validate_shots(shots: list[dict], durations: dict[str, float],
                   duration_sec: float, *, min_dur: float = 0.0,
                   source_tolerance: float = DEFAULTS["source_tolerance_sec"],
                   sum_tolerance: float = DEFAULTS["sum_tolerance_sec"],
                   ) -> list[dict]:
    """Check an ordered shot table against the real clips.

    ``durations`` maps clip file name -> source duration in seconds (measured by
    the caller). A missing key means the clip does not exist under ``clips/``.
    A shot with a non-numeric, negative or zero duration is a schema failure
    named by field, not a later planner crash.
    """
    if not shots:
        return [row("shot_schema", "empty story.shots", ">= 1 shot", False,
                    "add at least one {clip, in, dur} entry to brief.json "
                    "story.shots")]

    schema = [s for s in shots if _bad_fields(s)]
    unknown = [s for s in shots
               if not _bad_fields(s)
               and s["clip"] not in durations]
    out_of_range = []
    for s in shots:
        if _bad_fields(s):
            continue
        clip_dur = durations.get(s.get("clip") or "")
        if clip_dur is None:
            continue
        if s["in"] + s["dur"] > clip_dur + source_tolerance:
            out_of_range.append({**s, "source": clip_dur})
    too_short = [s for s in shots
                 if not _bad_fields(s) and min_dur > 0
                 and s["dur"] < min_dur - 1e-9]
    total = round(sum(s["dur"] for s in shots
                      if s.get("dur") is not None and s["dur"] > 0), 3)

    detail = "; ".join(
        f"shot {s['index']} ({s.get('clip') or 'no clip'}): "
        f"{', '.join(_bad_fields(s))}" for s in schema[:6])
    rows = [
        row("shot_schema",
            f"{len(shots)} entries / {len(schema)} malformed",
            "clip:str, in:number>=0, dur:number>0", not schema,
            (detail or _fmt_shots(schema, ("index", "clip", "in", "dur")))
            + " -> each shot needs a clip name and numeric in (>= 0) / "
              "dur (> 0); a string like 'dur': \"5.5\" is accepted, "
              "'dur': null or 0 is not"),
        row("shot_clip", f"{len(unknown)} unknown", "every clip exists in clips/",
            not unknown,
            _fmt_shots(unknown, ("index", "clip"))
            + " -> copy the file into input/<project>/clips/ or fix the name"),
        row("shot_range", f"{len(out_of_range)} out of range",
            "0 <= in and in+dur <= source",
            not out_of_range,
            "; ".join(f"shot {s['index']} ({s['clip']}) "
                      f"in={s['in']:.2f}+{s['dur']:.2f} > {s['source']:.2f}s"
                      for s in out_of_range[:6])
            + (" -> shorten dur or move in earlier" if out_of_range else "")),
        row("shot_total", f"{total:.2f}s cuts / {float(duration_sec or 0):.2f}s brief",
            f"sum within +/-{sum_tolerance:.2f}s of duration_sec",
            bool(duration_sec) and abs(total - float(duration_sec)) <= sum_tolerance,
            f"story.shots sums to {total:.2f}s but brief.duration_sec is "
            f"{float(duration_sec or 0):.2f}s; adjust one shot's dur (music-only "
            f"pieces have no voiceover to absorb the difference)"),
    ]
    if min_dur > 0:
        rows.append(row(
            "shot_min_cut", f"min {min((s['dur'] for s in shots if s.get('dur') is not None), default=0):.2f}s",
            f">= {min_dur:.2f}s", not too_short,
            _fmt_shots(too_short, ("index", "dur"))
            + f" -> merge shots shorter than {min_dur:.2f}s"))
    return rows


def shots_pass(rows: list[dict]) -> bool:
    return all(r["pass"] for r in rows if r.get("severity") == "error")


def timeline(shots: list[dict]) -> list[dict]:
    """Cumulative ``t0``/``t1`` for a validated shot table."""
    out: list[dict] = []
    cursor = 0.0
    for i, shot in enumerate(shots):
        dur = float(shot.get("dur") or 0.0)
        out.append({**shot, "line": i, "t0": round(cursor, 3),
                    "t1": round(cursor + dur, 3)})
        cursor += dur
    return out


def locked_plan(shots: list[dict], sku: str, *, order_source: str = "brief",
                duration_sec: float | None = None) -> dict:
    """The ``edit_plan.json`` payload for an order-locked brief.

    ``locked``/``order_source``/``duration_sec`` are additive: an old reader
    still sees ``cuts[].clip/in/dur`` and ignores the rest. ``duration_sec``
    lets ``sfx`` and ``qa`` work without a voiceover file.
    """
    cuts = []
    for shot in timeline(shots):
        cut = {
            "line": shot["line"],
            "text": shot.get("label") or shot.get("spec") or "",
            "kind": "shot",
            "clip": shot["clip"],
            "in": round(float(shot.get("in") or 0.0), 3),
            "dur": round(float(shot.get("dur") or 0.0), 3),
            "t0": shot["t0"],
        }
        if shot.get("spec"):
            cut["spec"] = shot["spec"]
        if shot.get("text_zone"):
            cut["text_zone"] = shot["text_zone"]
        cuts.append(cut)
    total = round(sum(float(s.get("dur") or 0.0) for s in shots), 3)
    return {
        "sku": sku,
        "generated_by": "fenix plan",
        "locked": True,
        "order_source": order_source,
        "duration_sec": round(float(duration_sec if duration_sec else total), 3),
        "narration_lines": len(cuts),
        "cuts": cuts,
    }


def plan_cuts_match(plan: dict, shots: list[dict]) -> bool:
    """True when an existing plan carries exactly this ordered shot table."""
    cuts = (plan or {}).get("cuts") or []
    if len(cuts) != len(shots):
        return False
    for cut, shot in zip(cuts, shots):
        if str(cut.get("clip") or "") != shot.get("clip"):
            return False
        if (cut.get("text_zone") or None) != (shot.get("text_zone") or None):
            return False
        try:
            if abs(float(cut.get("in")) - float(shot.get("in"))) > 1e-3:
                return False
            if abs(float(cut.get("dur")) - float(shot.get("dur"))) > 1e-3:
                return False
        except (TypeError, ValueError):
            return False
    return True


def order_state(brief: dict | None, plan: dict | None,
                durations: dict[str, float] | None = None,
                duration_sec: float | None = None, *,
                min_dur: float = 0.0,
                source_tolerance: float = DEFAULTS["source_tolerance_sec"],
                sum_tolerance: float = DEFAULTS["sum_tolerance_sec"]) -> dict:
    """One card-ready view of the order lock (used by ``fenix job``)."""
    shots = shots_from_brief(brief or {})
    state: dict = {"locked": False, "source": None, "shots": [],
                   "manual": False,
                   "total_sec": 0.0, "order_confirmed": False,
                   "checks": []}
    if not shots:
        state["plan_locked"] = bool((plan or {}).get("locked"))
        state["plan_cuts"] = len((plan or {}).get("cuts") or [])
        return state
    checks = []
    if durations is not None:
        checks = validate_shots(
            shots, durations, float(duration_sec or 0.0), min_dur=min_dur,
            source_tolerance=source_tolerance, sum_tolerance=sum_tolerance)
    locked = bool(checks and shots_pass(checks)) if checks else True
    total = round(sum(float(s.get("dur") or 0.0) for s in shots
                      if s.get("dur") is not None), 3)
    state.update({
        "locked": locked,
        "source": "brief",
        "shots": shots,
        "total_sec": total,
        "checks": checks,
        "order_confirmed": bool(locked and plan and plan.get("locked")
                                and plan_cuts_match(plan, shots)),
    })
    return state
