#!/usr/bin/env python3
"""storyboard — turn a project brief into a scene-by-scene plan.

The agent's job is to ask the questions and fill `brief.json`; this stage turns
that brief into a timed storyboard and names every missing fact. It is
format-agnostic: 9:16, 16:9, 1:1, 4:5, shorts, long-form, motion pieces.

Usage:
  fenix storyboard <project>              # write/refresh the storyboard
  fenix storyboard <project> --questions  # print the intake questionnaire
  fenix storyboard <project> --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..context import DEFAULT_CONFIG, INPUT_DIR, load_config, output_dir
from ..domain import brief as brief_mod
from ..domain import labels as labels_mod
from ..domain import shots as shots_mod
from ..domain.quality import probe_duration

QUESTIONS: list[tuple[str, list[str]]] = [
    ("Intent", [
        "What is this piece for (ad, product demo, explainer, music video, "
        "brand film, motion study, social short, long-form)?",
        "What should the viewer do or feel at the end?",
        "Who is the audience and where will it be watched?",
    ]),
    ("Format", [
        "Platform(s) and aspect ratio: 9:16, 16:9, 1:1, 4:5, other?",
        "Target duration and hard limit?",
        "Frame rate and resolution (24/30/60, 1080p/4K)?",
    ]),
    ("Story", [
        "What is the hook in the first 2-3 seconds?",
        "Which story framework fits (hook_retain_reward, aida, pas, bab, "
        "story_circle, pixar_spine, hero)?",
        "What are the 3-6 beats between hook and close?",
        "What is the closing line / call to action?",
    ]),
    ("Assets", [
        "Do you have raw footage, images, screen recordings, or should this be "
        "generated motion graphics?",
        "Where are the files and which takes are mandatory?",
        "Is there a voiceover script, a recorded VO, music, or SFX?",
    ]),
    ("Motion & style", [
        "References: links, stills, or the feeling you want (editorial, brutalist, "
        "clay 3D, kinetic type, documentary, ...)?",
        "Which motion elements matter: 3D object, camera move, transitions, "
        "type animation, grain/texture, light sweeps?",
        "Palette, typography and any brand rules?",
    ]),
    ("Delivery", [
        "Deliverables: MP4, alpha overlay, captions burned in or sidecar, "
        "thumbnails, project files?",
        "Any deadline, file-size or platform constraint?",
    ]),
]

FRAMEWORKS: dict[str, dict] = {
    "hook_retain_reward": {
        "name": "Hook - Retain - Reward",
        "beats": [
            ("hook", 0.09, "2-3 s pattern interrupt; signal who this is for"),
            ("problem", 0.14, "the status-quo cost, in the viewer's words"),
            ("mechanism", 0.36, "one demonstrable proof, not a feature list"),
            ("payoff", 0.27, "the after-state; the emotional reward"),
            ("cta", 0.14, "one action, one line, no new information"),
        ],
    },
    "storybrand": {
        "name": "StoryBrand SB7 (compressed)",
        "beats": [
            ("character", 0.15, "the customer, not the brand, is the hero"),
            ("problem", 0.20, "external, internal and philosophical problem"),
            ("guide", 0.20, "the brand as guide: empathy + authority"),
            ("plan", 0.20, "three steps or one clear mechanism"),
            ("success_cta", 0.25, "success picture + direct CTA"),
        ],
    },
    "explainer": {
        "name": "Explainer stepping stones",
        "beats": [
            ("context", 0.15, "why this matters now"),
            ("story", 0.20, "a concrete situation the viewer recognises"),
            ("connections", 0.20, "the idea that links the pieces"),
            ("steps", 0.30, "how it works, in order, with the why"),
            ("cta", 0.15, "what to do next"),
        ],
    },
    "save_the_cat": {
        "name": "Save the Cat! (15 beats, after Blake Snyder)",
        "beats": [
            ("opening_image", 0.01, "the world before the change"),
            ("theme", 0.05, "the idea the story will prove"),
            ("setup", 0.10, "ordinary life and its limits"),
            ("catalyst", 0.10, "the event that breaks the routine"),
            ("debate", 0.10, "resistance and the choice"),
            ("break_into_two", 0.10, "commit to the new world"),
            ("b_story", 0.05, "the relationship or idea that carries the theme"),
            ("fun_and_games", 0.10, "the promise of the premise"),
            ("midpoint", 0.05, "false victory or false defeat"),
            ("bad_guys_close_in", 0.10, "pressure tightens"),
            ("all_is_lost", 0.05, "the lowest point"),
            ("dark_night", 0.03, "the realisation"),
            ("break_into_three", 0.05, "the new plan"),
            ("finale", 0.10, "the proof under pressure"),
            ("final_image", 0.01, "the changed world"),
        ],
    },
    "kishotenketsu": {
        "name": "Kishotenketsu",
        "beats": [
            ("ki", 0.25, "introduce the world and its texture"),
            ("sho", 0.30, "develop it; deepen the detail"),
            ("ten", 0.25, "recontextualise: the twist or shift"),
            ("ketsu", 0.20, "conclude; the new reading"),
        ],
    },
    "three_act": {
        "name": "Three-act",
        "beats": [
            ("setup", 0.25, "the situation and the promise"),
            ("confrontation", 0.50, "the obstacle, the attempt, the cost"),
            ("resolution", 0.25, "the new state and the CTA"),
        ],
    },
    "aida": {
        "name": "AIDA (Attention - Interest - Desire - Action)",
        "beats": [
            ("attention", 0.15, "stop the scroll with a concrete claim"),
            ("interest", 0.30, "the detail that makes the claim believable"),
            ("desire", 0.30, "what it changes for the viewer"),
            ("action", 0.25, "one clear next step"),
        ],
    },
    "pas": {
        "name": "Problem - Agitate - Solve",
        "beats": [
            ("problem", 0.25, "name the pain in the viewer's words"),
            ("agitate", 0.30, "the cost of leaving it unsolved"),
            ("solve", 0.45, "the product/idea as the relief, then the CTA"),
        ],
    },
    "bab": {
        "name": "Before - After - Bridge",
        "beats": [
            ("before", 0.25, "the current state, honestly"),
            ("after", 0.30, "the same scene resolved"),
            ("bridge", 0.45, "the mechanism that gets you there + CTA"),
        ],
    },
    "story_circle": {
        "name": "Dan Harmon Story Circle",
        "beats": [
            ("comfort", 0.10, "a character in a familiar world"),
            ("want", 0.12, "they want something concrete"),
            ("unfamiliar", 0.18, "they enter a new situation"),
            ("adapt", 0.20, "they struggle and change"),
            ("find", 0.15, "they get what they came for"),
            ("take", 0.10, "the price of taking it"),
            ("return", 0.10, "back to the familiar world"),
            ("change", 0.05, "they are not the same"),
        ],
    },
    "pixar_spine": {
        "name": "Story Spine (Kenn Adams)",
        "beats": [
            ("once", 0.08, "Once upon a time..."),
            ("every_day", 0.15, "Every day..."),
            ("one_day", 0.17, "One day..."),
            ("because", 0.30, "Because of that..."),
            ("until", 0.20, "Until finally..."),
            ("ever_since", 0.10, "Ever since then..."),
        ],
    },
    "hero": {
        "name": "Hero's Journey (condensed, after Vogler)",
        "beats": [
            ("ordinary", 0.10, "the ordinary world"),
            ("call", 0.12, "the call to change"),
            ("refusal", 0.08, "the resistance"),
            ("mentor", 0.10, "the guide / mechanism"),
            ("threshold", 0.12, "crossing into the new"),
            ("tests", 0.25, "the proof under pressure"),
            ("ordeal", 0.13, "the decisive moment"),
            ("return", 0.10, "the new normal + CTA"),
        ],
    },
}

# project type -> default framework
FRAMEWORK_BY_TYPE = {
    "short": "hook_retain_reward",
    "reel": "hook_retain_reward",
    "ad": "hook_retain_reward",
    "edit": "hook_retain_reward",
    "hybrid": "hook_retain_reward",
    "brand": "storybrand",
    "explainer": "explainer",
    "longform": "save_the_cat",
    "music": "kishotenketsu",
    "motion": "kishotenketsu",
}

DEFAULT_FRAMEWORK = "hook_retain_reward"

DEFAULT_BEATS = [
    {"purpose": "hook", "weight": 0.12},
    {"purpose": "context", "weight": 0.20},
    {"purpose": "proof", "weight": 0.28},
    {"purpose": "payoff", "weight": 0.22},
    {"purpose": "close", "weight": 0.18},
]

BRIEF_TEMPLATE = {
    "title": "",
    "project_type": "",
    "goal": "",
    "audience": "",
    "platforms": [],
    "format": {"aspect": "", "width": 0, "height": 0, "fps": 0},
    "duration_sec": 0,
    "tone": "",
    "references": [],
    "story": {"framework": "", "hook": "", "beats": [], "cta": "",
              "decisions": [], "shots": []},
    "assets": {"footage_dir": "", "clips": [], "images": [],
               "voiceover": "", "music": "", "sfx": []},
    "motion": {"style": "", "elements": [], "transitions": []},
    "typography": {"family": "", "treatments": []},
    "deliverables": [],
    "constraints": [],
}

REQUIRED = {
    "title": "a working title",
    "project_type": "edit | motion | hybrid | short | longform",
    "goal": "what the piece must achieve",
    "format.aspect": "9:16 | 16:9 | 1:1 | 4:5",
    "format.width": "pixels",
    "format.height": "pixels",
    "format.fps": "24 | 30 | 60",
    "duration_sec": "seconds",
    "story.hook": "the first 2-3 seconds",
    "deliverables": "at least one output",
}


def _get(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def questionnaire() -> str:
    lines = ["Project intake — answer these and the brief is complete:", ""]
    for section, qs in QUESTIONS:
        lines.append(f"[{section}]")
        lines += [f"  - {q}" for q in qs]
        lines.append("")
    return "\n".join(lines)


def load_brief(project_dir: Path) -> tuple[dict, list[str]]:
    path = project_dir / "brief.json"
    if not path.exists():
        path.write_text(json.dumps(BRIEF_TEMPLATE, ensure_ascii=False, indent=2)
                        + "\n", encoding="utf-8")
        return dict(BRIEF_TEMPLATE), [f"created {path}"]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except json.JSONDecodeError as exc:
        sys.exit(f"[storyboard] invalid {path}: {exc}")


def missing_fields(brief: dict) -> list[str]:
    missing = []
    has_shots = bool(shots_mod.shots_from_brief(brief))
    for path, hint in REQUIRED.items():
        if path == "story.hook" and has_shots:
            continue      # an ordered shot table is the story; hook text is optional
        value = _get(brief, path)
        if value in (None, "", 0, []):
            missing.append(f"{path} ({hint})")
    return missing


def scene_from_shot(shot: dict) -> dict:
    """One storyboard scene from an order-locked shot entry."""
    return {
        "id": shot["index"],
        "purpose": shot.get("label") or "shot",
        "t0": shot["t0"],
        "t1": shot["t1"],
        "seconds": round(shot["t1"] - shot["t0"], 3),
        "text": shot.get("spec", ""),
        "visual": shot.get("label", ""),
        "audio": "",
        "motion": "",
        "clip": shot.get("clip"),
        "in": shot.get("in"),
        "dur": shot.get("dur"),
        "text_zone": shot.get("text_zone"),
    }


def build_storyboard(brief: dict, framework: str | None = None,
                     durations: dict[str, float] | None = None,
                     shots_cfg: dict | None = None,
                     min_cut_sec: float = 0.0) -> dict:
    duration = float(brief.get("duration_sec") or 0)
    story = brief.get("story", {}) or {}
    project_type = str(brief.get("project_type") or "").strip().lower()
    default_key = FRAMEWORK_BY_TYPE.get(project_type, DEFAULT_FRAMEWORK)
    key = (framework or story.get("framework") or default_key).strip()
    fw = FRAMEWORKS.get(key)
    if fw is None:
        key = default_key
        fw = FRAMEWORKS[key]

    ordered = shots_mod.shots_from_brief(brief)
    order = None
    if ordered:
        sh_cfg = {**shots_mod.DEFAULTS, **(shots_cfg or {})}
        checks = (shots_mod.validate_shots(
            ordered, durations or {}, duration,
            min_dur=float(min_cut_sec or 0.0),
            source_tolerance=float(sh_cfg["source_tolerance_sec"]),
            sum_tolerance=float(sh_cfg["sum_tolerance_sec"]))
            if durations is not None else [])
        ordered = shots_mod.timeline(ordered)
        order = {
            "locked": True,
            "source": "brief",
            "total_sec": round(sum(float(s.get("dur") or 0) for s in ordered), 3),
            "shots": ordered,
            "checks": checks,
            "pass": shots_mod.shots_pass(checks) if checks else True,
        }
        scenes = [scene_from_shot(s) for s in ordered]
    else:
        beats, beat_errors = brief_mod.normalize_beats(story.get("beats"))
        if beat_errors:
            raise SystemExit(brief_mod.schema_message(beat_errors))
        if not beats:
            beats = [{"purpose": purpose, "weight": weight,
                      "prompt": prompt}
                     for purpose, weight, prompt in fw["beats"]]
        weight_sum = sum(float(b.get("weight", 1.0)) for b in beats) or 1.0
        scenes, cursor = [], 0.0
        for i, beat in enumerate(beats):
            share = float(beat.get("weight", 1.0)) / weight_sum
            length = duration * share
            start, end = cursor, min(duration, cursor + length)
            scenes.append({
                "id": i,
                "purpose": beat.get("purpose") or f"beat_{i}",
                "t0": round(start, 3),
                "t1": round(end, 3),
                "seconds": round(end - start, 3),
                "text": beat.get("text", ""),
                "visual": beat.get("visual", ""),
                "audio": beat.get("audio", ""),
                "motion": beat.get("motion", ""),
            })
            cursor = end
        if scenes:
            scenes[-1]["t1"] = duration
            scenes[-1]["seconds"] = round(duration - scenes[-1]["t0"], 3)

    return {
        "title": brief.get("title", ""),
        "project_type": brief.get("project_type", ""),
        "framework": {"id": key, "name": fw["name"]},
        "decisions": story.get("decisions") or brief.get("decisions") or [],
        "format": brief.get("format", {}),
        "duration_sec": duration,
        "tone": brief.get("tone", ""),
        "scenes": scenes,
        "order": order,
        "missing": missing_fields(brief),
        "deliverables": brief.get("deliverables", []),
    }


def write_markdown(story: dict) -> str:
    fmt = story["format"]
    lines = [
        f"# {story['title'] or 'Untitled'} — storyboard",
        "",
        f"- Type: **{story['project_type'] or 'TBD'}**",
        f"- Story framework: **{story['framework']['name']}**",
        f"- Format: **{fmt.get('aspect', '?')}** "
        f"({fmt.get('width', '?')}×{fmt.get('height', '?')} @ "
        f"{fmt.get('fps', '?')} fps)",
        f"- Duration: **{story['duration_sec']:.2f} s**",
        f"- Tone: {story.get('tone', '') or 'TBD'}",
        "",
        "| # | Purpose | In | Out | Visual | Audio | Motion |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in story["scenes"]:
        lines.append(
            f"| {s['id']} | {s['purpose']} | {s['t0']:.2f} | {s['t1']:.2f} | "
            f"{s['visual']} | {s['audio']} | {s['motion']} |")
    order = story.get("order")
    if order:
        lines += ["", "## Locked shot order", "",
                  f"Source: **{order['source']}** · "
                  f"{len(order['shots'])} shots · {order['total_sec']:.2f} s", "",
                  "| # | Clip | In | Dur | Label | Spec |",
                  "|---|---|---|---|---|---|"]
        for shot in order["shots"]:
            lines.append(
                f"| {shot['index']} | {shot['clip']} | {shot['in']:.2f} | "
                f"{shot['dur']:.2f} | {shot.get('label', '')} | "
                f"{shot.get('spec', '')} |")
        if order["checks"]:
            lines += ["", "Order checks: " + ", ".join(
                f"{c['id']} {'PASS' if c['pass'] else 'FAIL'}"
                for c in order["checks"])]
    if story["missing"]:
        lines += ["", "## Missing before production", ""]
        lines += [f"- {m}" for m in story["missing"]]
    return "\n".join(lines) + "\n"


def clip_durations(project_dir: Path, brief: dict) -> dict[str, float]:
    """Duration of every clip the brief names (missing files stay absent)."""
    names = {str(s.get("clip") or "") for s in
             (shots_mod.shots_from_brief(brief) or [])}
    out: dict[str, float] = {}
    for name in sorted(names):
        path = project_dir / "clips" / name
        if name and path.exists():
            out[name] = probe_duration(path)
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Turn a brief into a storyboard")
    parser.add_argument("project", help="project folder under input/")
    parser.add_argument("--questions", action="store_true",
                        help="print the intake questionnaire and exit")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--framework", choices=sorted(FRAMEWORKS),
                        help="story framework (default: hook_retain_reward)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite the brief template")
    args = parser.parse_args(argv)

    if args.questions:
        print(questionnaire())
        return

    project_dir = INPUT_DIR / args.project
    if not project_dir.exists():
        project_dir.mkdir(parents=True)
    if args.force:
        (project_dir / "brief.json").write_text(
            json.dumps(BRIEF_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    brief, notes = load_brief(project_dir)
    cfg = load_config(args.config, project=project_dir)
    ordered = shots_mod.shots_from_brief(brief)
    durations = clip_durations(project_dir, brief) if ordered else None
    story = build_storyboard(
        brief, args.framework, durations,
        shots_cfg=cfg.get("shots") or {},
        min_cut_sec=float(cfg.get("min_cut_sec", 0) or 0))
    out = output_dir(project_dir.name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "storyboard.json").write_text(
        json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "storyboard.md").write_text(write_markdown(story), encoding="utf-8")

    order = story.get("order")
    order_path = None
    cards_path = None
    if order:
        order_path = out / "shot_order.json"
        order_path.write_text(json.dumps({
            "sku": project_dir.name,
            "generated_by": "fenix storyboard",
            "locked": True,
            "order_source": "brief",
            "duration_sec": story["duration_sec"],
            "total_sec": order["total_sec"],
            "pass": order["pass"],
            "shots": order["shots"],
            "checks": order["checks"],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if order["pass"]:
            # One title/spec card per shot, derived from the locked order. The
            # renderer reads this only when sku.text_cards is absent, so a
            # hand-authored card list still wins. Legacy/VO projects have no
            # story.shots and get no file.
            cards = labels_mod.text_cards_from_shots(
                order["shots"], story["duration_sec"], cfg)
            cards_path = out / "text_cards.json"
            cards_path.write_text(
                json.dumps(cards, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")

    if args.json:
        print(json.dumps(story, ensure_ascii=False, indent=2))
        if order and not order["pass"]:
            sys.exit(1)
        return
    print(f"[storyboard] {project_dir / 'brief.json'}")
    if order:
        print(f"[storyboard] locked shot order: {len(order['shots'])} shots, "
              f"{order['total_sec']:.2f}s -> {order_path}")
        if cards_path:
            print(f"[storyboard] text cards: {len(cards)} derived from the "
                  f"shot labels -> {cards_path}")
        for check in order["checks"]:
            print(f"[{'PASS' if check['pass'] else 'FAIL'}] {check['id']:<14} "
                  f"{check['measured']}")
            if not check["pass"] and check["fix"]:
                print(f"        fix: {check['fix']}")
    else:
        print(f"[storyboard] {len(story['scenes'])} scenes, "
              f"{story['duration_sec']:.2f}s -> {out / 'storyboard.md'}")
    if story["missing"]:
        print(f"[storyboard] {len(story['missing'])} missing fact(s):")
        for m in story["missing"]:
            print(f"  - {m}")
        print("\nAsk the intake questions: fenix storyboard "
              f"{args.project} --questions")
    elif not order:
        print("[storyboard] brief complete; next: gather assets, then plan/render")
    if order and not order["pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
