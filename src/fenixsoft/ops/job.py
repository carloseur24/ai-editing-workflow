#!/usr/bin/env python3
"""job — one card with the mode, state, skills and next command.

The audit of the 26-minute demo session found the real cost was not ffmpeg:
85 exploration calls and 82 repo edits went into reconstructing state and
re-implementing a skill. This command answers the questions those calls were
looking for, deterministically and in one process:

    fenix job <project>           # human card
    fenix job <project> --json    # same card for an agent/tool

It never renders, never writes an artifact and never asks a question. If an
input is missing it says which one; if the job is ready it prints the exact
`make`/`motion`/`post` command and the gate it will run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..context import (DEFAULT_CONFIG, REPO_ROOT, SFX_DIR, count_overrides,
                       display_path, load_config, output_dir,
                       project_config_path, project_overrides, repo_path,
                       resolve_sku_dir)
from ..domain import runs
from ..domain import shots as shots_mod
from ..domain.quality import probe_duration
from ..domain.voice import explicit_duration, is_music_only, voice_name
from . import make

# The global skill registry used by the OpenCode harness. A project-local
# `.opencode/skills` folder wins for project-only skills.
SKILL_DIRS = [
    REPO_ROOT / ".opencode" / "skills",
    Path.home() / ".config" / "opencode" / "skills",
    Path.home() / ".agents" / "skills",
]

SKILLS = {
    "edit": [
        ("kinetic-animations-engineer", "captions, kinetic type, text motion"),
        ("sfx-dopamine-blueprint", "sound design, SFX, mix"),
        ("ffmpeg-expert", "only if an encode/filtergraph gate fails"),
    ],
    "hybrid": [
        ("kinetic-animations-engineer", "captions and motion plates"),
        ("sfx-dopamine-blueprint", "sound design, SFX, mix"),
        ("ffmpeg-expert", "composite, alpha and encode"),
    ],
    "motion": [
        ("kinetic-animations-engineer", "typography and motion timing"),
        ("ffmpeg-expert", "encode, alpha, ffprobe"),
    ],
    "longform": [
        ("ffmpeg-expert", "concat, xfade, audio padding"),
    ],
    "post": [
        ("ffmpeg-expert", "keying, grade, filters"),
    ],
    "intake": [],
    "new": [
        ("kinetic-animations-engineer", "captions and kinetic type"),
        ("sfx-dopamine-blueprint", "sound design, SFX, mix"),
    ],
}

GATES = {
    "edit": ["validate (pre-render)", "sfx (cue sheet)", "qa --strict (31 checks)",
             "finish (deliver + prune)"],
    "hybrid": ["validate", "sfx (cue sheet)", "render plate + composite",
               "qa --strict", "finish"],
    "motion": ["spec validation", "review --still", "ffprobe (codec/size/"
               "duration/alpha)", "deliver"],
    "longform": ["spec validation", "manifest written", "ffprobe matches",
                 "watch the master"],
    "post": ["spec validation", "manifest written", "ffprobe matches",
             "watch the result"],
    "intake": ["answer the intake questions", "brief.json filled",
               "fenix storyboard"],
    "new": ["fenix storyboard --questions", "brief.json filled",
            "fenix storyboard"],
}

RULES = [
    "Load the listed skills before the step that needs them; never "
    "re-implement a skill in src/.",
    "A video job changes input/ or config/ only. src/, docs/ and tests/ are "
    "a separate repo task.",
    "Use `make`; hand-run a stage only to diagnose a failed gate.",
    "Audio: `sfx --dry-run` -> `make` (remix). Picture: `render --draft` -> "
    "one full render.",
    "QA once per byte-identical master; `finish` reuses that PASS.",
]

BUDGET = {
    "job_calls": 1,
    "pipeline_commands": 3,
    "src_edits": 0,
    "docs_edits": 0,
    "full_renders": 1,
    "qa": 1,
    "finish": 1,
}


def detect_mode(sku_dir: Path) -> str:
    has = lambda name: (sku_dir / name).exists()
    clips = bool(list((sku_dir / "clips").glob("*.mp4")))
    if has("longform.json"):
        return "longform"
    if has("motion.json"):
        return "hybrid" if clips else "motion"
    if has("comp.json") or has("grade.json"):
        return "post"
    if has("sku.json"):
        return "edit"
    return "intake"


def _missing_inputs(sku_dir: Path, mode: str, cfg: dict
                    ) -> tuple[list[str], list[str], list[str]]:
    """Return (project inputs, warnings, repo asset gaps).

    Project inputs live under ``input/<project>/``. Repo assets live at the
    paths in ``config/pipeline.json`` (music bed, SFX library); telling an agent
    to put those under ``input/`` is wrong, so they are reported separately.
    """
    missing: list[str] = []
    warnings: list[str] = []
    asset_gaps: list[str] = []
    clips = sorted((sku_dir / "clips").glob("*.mp4"))

    if mode in ("edit", "hybrid"):
        sku_path = sku_dir / "sku.json"
        brief: dict = {}
        if (sku_dir / "brief.json").exists():
            try:
                brief = json.loads((sku_dir / "brief.json")
                                   .read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                brief = {}
        if not sku_path.exists():
            missing.append("sku.json")
        else:
            sku = json.loads(sku_path.read_text(encoding="utf-8"))
            if is_music_only(sku):
                warnings.append(
                    "music-only: captions disabled; cuts come from "
                    "brief.story.shots, not narration lines")
                if not (sku.get("duration_sec") or brief.get("duration_sec")):
                    warnings.append(
                        "music-only: no explicit duration_sec; using "
                        f"config target_duration_sec "
                        f"({cfg.get('target_duration_sec')}s)")
            else:
                voice = voice_name(sku)
                if not (sku_dir / voice).exists():
                    missing.append(voice)
                if not (sku.get("script_text") or "").strip():
                    warnings.append("no script_text: captions and planning degrade")
        if not clips:
            missing.append("clips/*.mp4")
        if not (sku_dir / "brief.json").exists():
            warnings.append("no brief.json: intake decisions are not recorded")
        music = (cfg.get("music") or {}).get("file")
        if music and not repo_path(music).exists():
            asset_gaps.append(str(music))
        sfx_cfg = cfg.get("sfx") or {}
        library = repo_path(sfx_cfg.get("library_dir") or SFX_DIR).expanduser()
        subdir = str(sfx_cfg.get("install_subdir") or "fenix_sfx")
        if not (library / subdir).exists():
            warnings.append(
                f"SFX library not installed at {display_path(library / subdir)}"
                "; run ./fenix sfx --install-library or set "
                "sfx.library_dir in config/pipeline.local.json")
    if mode == "motion" and not (sku_dir / "motion.json").exists():
        missing.append("motion.json")
    if mode == "longform" and not (sku_dir / "longform.json").exists():
        missing.append("longform.json")
    if mode == "post" and not ((sku_dir / "comp.json").exists()
                               or (sku_dir / "grade.json").exists()):
        missing.append("comp.json or grade.json")
    if mode == "intake" and not (sku_dir / "brief.json").exists():
        missing.append("brief.json")
    return missing, warnings, asset_gaps


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def order_block(sku_dir: Path, cfg: dict) -> dict:
    """The ordered shot table + lock state, for the card and ``--json``."""
    brief = _read_json(sku_dir / "brief.json")
    plan = _read_json(sku_dir / "edit_plan.json")
    sku = _read_json(sku_dir / "sku.json")
    shots = shots_mod.shots_from_brief(brief)
    durations: dict[str, float] | None = None
    duration = explicit_duration(sku, brief, cfg)
    if shots:
        durations = {}
        for name in sorted({s["clip"] for s in shots if s.get("clip")}):
            path = sku_dir / "clips" / name
            if path.exists():
                durations[name] = probe_duration(path)
    sh_cfg = {**shots_mod.DEFAULTS, **(cfg.get("shots") or {})}
    state = shots_mod.order_state(
        brief, plan, durations, duration,
        min_dur=float(cfg.get("min_cut_sec", 1.2) or 0.0),
        source_tolerance=float(sh_cfg["source_tolerance_sec"]),
        sum_tolerance=float(sh_cfg["sum_tolerance_sec"]))
    cuts = plan.get("cuts") or []
    if shots_mod.is_manual_plan(plan):
        # A hand-authored plan outranks a shot table: it is the delivered order
        # until the user runs `fenix plan --force`.
        state.update({
            "locked": True,
            "manual": True,
            "source": "plan",
            "plan_cuts": len(cuts),
            "total_sec": round(sum(float(c.get("dur") or 0)
                                   for c in cuts), 3),
            "order_confirmed": bool(cuts),
            "checks": [],
            "cuts": [{k: c.get(k) for k in ("clip", "in", "dur", "text")}
                     for c in cuts],
        })
        return state
    if not shots:
        locked = bool(plan.get("locked")) and bool(cuts)
        state.update({
            "locked": bool(plan.get("locked")),
            "manual": False,
            "source": "plan" if cuts else None,
            "plan_cuts": len(cuts),
            "total_sec": round(sum(float(c.get("dur") or 0)
                                   for c in cuts), 3),
            "order_confirmed": bool(locked and cuts),
            "cuts": [{k: c.get(k) for k in ("clip", "in", "dur", "text")}
                     for c in cuts],
        })
    else:
        state["cuts"] = [{k: c.get(k) for k in ("clip", "in", "dur", "text")}
                         for c in cuts]
        state["plan_locked"] = bool(plan.get("locked"))
    return state


def config_values(sku_dir: Path, cfg: dict) -> dict:
    """The effective project values an agent must not guess (``job`` params)."""
    quality = cfg.get("quality") or {}
    transitions = cfg.get("transitions") or {}
    sku = _read_json(sku_dir / "sku.json")
    authored = sku.get("text_cards") or []
    derived_path = output_dir(sku_dir) / "text_cards.json"
    if authored:
        text = f"native sku.text_cards ({len(authored)} cards)"
    elif derived_path.exists():
        try:
            cards = json.loads(derived_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cards = []
        text = f"derived text_cards.json ({len(cards)} cards)"
    elif (sku_dir / "motion.json").exists():
        text = "motion plate (motion.json)"
    else:
        text = "captions only"
    transition = ("off" if not transitions.get("enabled") else
                  f"{transitions.get('default') or 'dissolve'} "
                  f"{float(transitions.get('sec') or 0):.2f}s")
    return {
        "duration_sec": cfg.get("target_duration_sec"),
        "cuts": cfg.get("cuts_per_video"),
        "beat_policy": quality.get("beat_policy") or "music",
        "in_point_floor": quality.get("in_point_floor"),
        "transitions": transition,
        "text": text,
    }


def _skill_installed(name: str) -> bool:
    return any((d / name / "SKILL.md").exists() for d in SKILL_DIRS)


def _skills(mode: str) -> list[dict]:
    return [{"name": name, "when": when, "installed": _skill_installed(name)}
            for name, when in SKILLS.get(mode, [])]


def build_card(project: str, config_path: Path = DEFAULT_CONFIG) -> dict:
    try:
        sku_dir = resolve_sku_dir(project)
    except SystemExit:
        sku_dir = None
    cfg = load_config(config_path, project=sku_dir)

    if sku_dir is None:
        name = Path(project).name
        cards_skus = [f"input/{name}/"]
        return {
            "project": name,
            "mode": "new",
            "ready": False,
            "missing": cards_skus,
            "asset_gaps": [],
            "warnings": [],
            "config": {"path": None, "exists": False, "overrides": 0},
            "params": {},
            "skills": _skills("new"),
            "stages": [],
            "render": {"mode": "none", "why": ["no project folder"]},
            "order": {"locked": False, "shots": [], "cuts": [],
                      "order_confirmed": False},
            "next": [{"label": "intake",
                      "command": f"./fenix storyboard {name} --questions",
                      "reason": "create the folder and record the brief "
                                "before guessing"}],
            "gates": GATES["new"],
            "rules": RULES,
            "budget": BUDGET,
        }

    mode = detect_mode(sku_dir)
    missing, warnings, asset_gaps = _missing_inputs(sku_dir, mode, cfg)
    skills = _skills(mode)
    order = order_block(sku_dir, cfg)

    stages: list[dict] = []
    render = {"mode": "none", "why": ["not an edit project"]}
    master = None
    latest = runs.latest_run(sku_dir.name)
    if latest:
        master = latest.get("video")

    if mode in ("edit", "hybrid"):
        for name in make.STAGE_ORDER:
            run, why = make.stage_decision(name, sku_dir, cfg,
                                           config_path=config_path)
            stages.append({"name": name, "run": run, "why": why})
        render_mode, render_why = make.render_decision(
            sku_dir, cfg, config_path)
        render = {"mode": render_mode, "why": render_why}

    next_actions: list[dict] = []
    blocked = False
    if mode == "intake":
        if missing:
            next_actions.append({
                "label": "intake",
                "command": f"./fenix storyboard {sku_dir.name} --questions",
                "reason": "the brief/folder is incomplete; ask before guessing",
            })
        else:
            next_actions.append({
                "label": "storyboard",
                "command": f"./fenix storyboard {sku_dir.name}",
                "reason": "the brief is recorded; turn it into timed scenes",
            })
        blocked = True
    else:
        if missing:
            next_actions.append({
                "label": "add inputs",
                "command": f"add {', '.join(missing)} under input/{sku_dir.name}/",
                "reason": "the job cannot start without them",
            })
            blocked = True
        if asset_gaps:
            next_actions.append({
                "label": "add assets",
                "command": f"add {', '.join(asset_gaps)} (repo asset, not input/)",
                "reason": "these are config paths; see assets/README.md",
            })
            blocked = True

    if not blocked and mode in ("edit", "hybrid"):
        stale = [s["name"] for s in stages if s["run"]]
        if order.get("shots") and not order.get("order_confirmed"):
            next_actions.append({
                "label": "relock order",
                "command": f"./fenix plan {sku_dir.name}",
                "reason": "edit_plan.json does not match brief.story.shots",
            })
        if render["mode"] == "skip" and not stale:
            next_actions.append({
                "label": "no-op",
                "command": f"current master: {master or 'none'}",
                "reason": "no picture or audio input is newer than the run",
            })
        else:
            next_actions.append({
                "label": "job",
                "command": f"./fenix make {sku_dir.name}",
                "reason": (f"{len(stale)} stale stage(s); render "
                           f"{render['mode']}"),
            })
            next_actions.append({
                "label": "picture review",
                "command": f"./fenix render {sku_dir.name} --draft",
                "reason": "only when the picture/captions changed",
            })
    elif not blocked and mode == "motion":
        next_actions.append({
            "label": "plate",
            "command": f"./fenix motion {sku_dir.name} --still 1.5",
            "reason": "review one frame before the full plate",
        })
    elif not blocked and mode == "longform":
        next_actions.append({
            "label": "master",
            "command": f"./fenix longform {sku_dir.name}",
            "reason": "assemble the chapter spec",
        })
    elif not blocked and mode == "post":
        stage = "grade" if (sku_dir / "grade.json").exists() else "comp"
        next_actions.append({
            "label": stage,
            "command": f"./fenix {stage} {sku_dir.name}",
            "reason": "run the post spec",
        })

    return {
        "project": sku_dir.name,
        "mode": mode,
        "ready": not missing and not asset_gaps,
        "missing": missing,
        "asset_gaps": asset_gaps,
        "warnings": warnings,
        "config": {
            "path": display_path(project_config_path(sku_dir)),
            "exists": project_config_path(sku_dir).exists(),
            "overrides": count_overrides(project_overrides(sku_dir)),
        },
        "params": config_values(sku_dir, cfg),
        "skills": skills,
        "stages": stages,
        "render": render,
        "order": order,
        "master": master,
        "next": next_actions,
        "gates": GATES.get(mode, []),
        "rules": RULES,
        "budget": BUDGET,
    }


def _print_human(card: dict) -> None:
    print(f"[job] {card['project']}")
    print(f"  mode      {card['mode']}")
    print(f"  ready     {'yes' if card['ready'] else 'no'}")
    if card.get("master"):
        print(f"  master    {card['master']}")
    if card["missing"]:
        print(f"  missing   {', '.join(card['missing'])}")
    if card.get("asset_gaps"):
        print(f"  assets    {', '.join(card['asset_gaps'])}")
    config = card.get("config") or {}
    if config.get("path"):
        state = (f"({config['overrides']} overrides)"
                 if config.get("exists") else "(none; global + local defaults)")
        print(f"  config    {config['path']} {state}")
    params = card.get("params") or {}
    if params:
        print(f"  params    {params.get('duration_sec')}s · "
              f"{params.get('cuts')} cuts · "
              f"beat {params.get('beat_policy')} · "
              f"in-point floor {params.get('in_point_floor')} · "
              f"transitions {params.get('transitions')} · "
              f"text {params.get('text')}")
    for warning in card["warnings"]:
        print(f"  warning   {warning}")
    if card["skills"]:
        for i, skill in enumerate(card["skills"]):
            mark = "installed" if skill["installed"] else "MISSING"
            label = "skills   " if i == 0 else "          "
            print(f"  {label} {skill['name']} ({skill['when']}) [{mark}]")
    if card["stages"]:
        state = " | ".join(
            f"{s['name']} {'run' if s['run'] else 'skip'}"
            for s in card["stages"])
        print(f"  stages    {state}")
        print(f"  render    {card['render']['mode']}  "
              f"{'; '.join(card['render']['why'][:3])}")
    order = card.get("order") or {}
    rows = ((order.get("cuts") if order.get("manual") else order.get("shots"))
            or order.get("cuts") or [])
    if rows:
        if order.get("manual"):
            lock = "locked plan (manual)"
        elif order.get("shots"):
            lock = "locked by brief" if order.get("locked") else "brief FAILS"
        elif order.get("locked"):
            lock = "locked plan"
        else:
            lock = "plan (not locked)"
        confirmed = "confirmed" if order.get("order_confirmed") else "NOT confirmed"
        print(f"  order     {len(rows)} shots · {order.get('total_sec', 0):.2f}s "
              f"· {lock} · {confirmed}")
        for i, shot in enumerate(rows[:8]):
            label = shot.get("text") or shot.get("label") or ""
            print(f"            {i:>2} {str(shot.get('clip'))[:34]:<34} "
                  f"in {float(shot.get('in') or 0):6.2f}  "
                  f"dur {float(shot.get('dur') or 0):5.2f}  "
                  f"{str(label)[:26]}")
        if len(rows) > 8:
            print(f"            ... {len(rows) - 8} more")
        for check in order.get("checks") or []:
            if not check.get("pass"):
                print(f"            FAIL {check['id']}: {check['fix']}")
    for action in card["next"]:
        print(f"  next      {action['command']}  # {action['reason']}")
    print("  gates     " + " -> ".join(card["gates"]))
    print("  rules     " + card["rules"][0])
    for rule in card["rules"][1:]:
        print("            " + rule)
    budget = card["budget"]
    print(f"  budget    {budget['job_calls']} job call · "
          f"<={budget['pipeline_commands']} pipeline commands · "
          f"{budget['src_edits']} src/docs edits · "
          f"<={budget['full_renders']} full render · "
          f"{budget['qa']} QA · {budget['finish']} finish")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-card job contract: mode, state, skills, next command")
    parser.add_argument("project", help="project/SKU name (may not exist yet)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true",
                        help="machine-readable card (no prose)")
    args = parser.parse_args(argv)

    card = build_card(args.project, args.config)
    if args.json:
        print(json.dumps(card, ensure_ascii=False, indent=2))
    else:
        _print_human(card)
    return 0


if __name__ == "__main__":
    sys.exit(main())
