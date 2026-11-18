#!/usr/bin/env python3
"""Delivery bundle: plan what a finished run ships, then copy it.

The delivery shelf is written only by delivery commands (``deliver``,
``finish``, ``comp --deliver``). This module is the pure planner/copier those
commands share, so a manual copy step cannot forget the contact sheet, the shot
order or the "no voiceover" note:

    plan = bundle_plan(project, run_dir, cfg, extras=[...])
    readme = write_bundle_readme(project, run_dir, plan, cfg,
                                 no_voice=not voice_present, shots=shots)
    (run_dir / "script_vo.md").write_text(readme)   # or pass it as an extra
    result = apply_bundle(plan)

Exit behaviour stays in the op: this module never raises for a missing
optional artifact, it returns a skip list the caller prints.

Integration point (not wired yet)
---------------------------------
``ops/deliver.py`` runs after QA and after the master exists; it should replace
its per-pattern glob with:

    cfg = load_config()                      # pipeline.local.json applies
    plan = bundle_plan(project, run_dir, cfg,
                       extras=[readme_path] if readme_path else None)

``stages/comp.py --deliver``, ``stages/grade.py --deliver`` and
``stages/longform.py --deliver`` call the same pair for their stage result and
manifest, with ``project`` and the stage run directory. ``apply_bundle`` never
prunes local media; pruning stays in ``deliver``.

Paths are configured, never hardcoded: the shelf and the flat Downloads folder
come from ``cfg["delivery"]["dir"]`` / ``cfg["delivery"]["downloads"]`` (a
relative path resolves against the repo root, exactly like ``deliver``).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import context
from ..domain import runs

# Always considered in this order; the first existing match wins. The master
# MP4 is resolved with ``runs.run_video`` so a renamed run still ships.
EVIDENCE = (
    "manifest.json",
    "comp.json",
    "qa_report.json",
    "contact_sheet.png",
    "shot_order.json",
    "edit_plan.json",
    "caption_audit.json",
    "word_timings.json",
    "script_vo.md",
    "sfx_cue_sheet.json",
    "sfx_report.json",
)

# Evidence that only belongs to a stage run (never an edit run's input/ copy).
STAGE_EVIDENCE = ("comp.json", "manifest.json")

# Copied flat into ``delivery.downloads`` in addition to the shelf folder.
DOWNLOADS_NAMES = ("contact_sheet.png",)

# Stage masters when a run has no ``*_final.mp4`` (comp/grade/longform/fx).
STAGE_MASTERS = ("comp.mp4", "master.mp4", "graded.mp4", "fx.mp4",
                 "plate.mov", "plate.mp4")


def _root(cfg: dict, key: str) -> Path | None:
    value = ((cfg or {}).get("delivery") or {}).get(key)
    if not value:
        return None
    return context.repo_path(str(value)).expanduser()


def _search_roots(project: str, run_dir: Path) -> list[Path]:
    """The run first, then the project's flat artifacts, then its inputs."""
    roots = [Path(run_dir)]
    for base in (context.OUTPUT_DIR, context.INPUT_DIR):
        cand = base / project
        if cand.is_dir() and cand not in roots:
            roots.append(cand)
    return roots


def _find(name_glob: str, roots: list[Path]) -> Path | None:
    for root in roots:
        matches = sorted(p for p in root.glob(name_glob) if p.is_file())
        if matches:
            return matches[-1]
    return None


def _find_all(name_glob: str, roots: list[Path]) -> list[Path]:
    """Every match for a configured ``delivery.copy`` pattern, deduped."""
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for path in sorted(p for p in root.glob(name_glob) if p.is_file()):
            key = path.resolve()
            if key not in seen:
                seen.add(key)
                out.append(path)
    return out


def _extras_paths(extras, run_dir: Path) -> list[Path]:
    out: list[Path] = []
    for item in extras or []:
        if isinstance(item, dict):
            src = item.get("src")
        else:
            src = item
        if not src:
            continue
        path = Path(src).expanduser()
        if not path.is_absolute():
            path = Path(run_dir) / path
        out.append(path)
    return out


def bundle_plan(project: str, run_dir: Path, cfg: dict, *,
                master: Path | str | None = None,
                extras=None) -> list[dict]:
    """Ordered ``{src, dest, kind}`` rows for the shelf and flat Downloads.

    ``kind`` is ``"shelf"`` (the run folder with evidence) or ``"downloads"``
    (flat, for opening from the desktop). Every evidence file that exists is
    planned for the shelf; only the master and the contact sheet are flattened
    into Downloads, matching ``fenix deliver`` so the folder does not collect
    one copy of every JSON per run.

    ``master`` overrides detection. Without it the planner tries
    ``*_final.mp4`` first (an edit render) and then the stage names in
    ``STAGE_MASTERS`` (``comp.mp4``/``master.mp4``/…) so a comp run delivers
    the same evidence bundle as an edit. An explicit path may live outside
    ``run_dir`` (comp ``--out``); it must exist.

    Missing optional artifacts are simply absent from the plan. A run with no
    master yields no rows: without the media there is nothing to deliver.
    """
    run_dir = Path(run_dir)
    shelf = _root(cfg, "dir")
    downloads = _root(cfg, "downloads")
    if not shelf and not downloads:
        return []

    if master is not None:
        master_path = Path(master).expanduser()
        video = master_path if master_path.exists() else None
    else:
        candidate = runs.run_video(run_dir)
        video = candidate if candidate.exists() else None
        if video is None:
            video = next((run_dir / name for name in STAGE_MASTERS
                          if (run_dir / name).exists()), None)
    if video is None:
        return []

    plan: list[dict] = []
    seen: set[tuple[Path, Path]] = set()
    destinations: set[Path] = set()

    def add(src: Path, kind: str, dest: Path) -> None:
        key = (src.resolve(), dest)
        if key in seen or dest in destinations:
            # One destination, one source: evidence found in the run wins over
            # a same-named file in output/ or input/.
            return
        seen.add(key)
        destinations.add(dest)
        plan.append({"src": src, "dest": dest, "kind": kind})

    if shelf:
        add(video, "shelf", shelf / run_dir.name / video.name)
    if downloads:
        add(video, "downloads", downloads / video.name)

    roots = _search_roots(project, run_dir)
    for name in EVIDENCE:
        source_roots = [run_dir] if name in STAGE_EVIDENCE else roots
        src = _find(name, source_roots)
        if src is None:
            continue
        if shelf:
            add(src, "shelf", shelf / run_dir.name / src.name)
        if downloads and name in DOWNLOADS_NAMES:
            add(src, "downloads",
                downloads / f"{run_dir.name}_{src.name}")

    # Everything configured in delivery.copy that exists and is not already in
    # the table (e.g. an extra note or a poster added to config/pipeline.json).
    # A glob such as ``*.md`` ships every match, like ``fenix deliver``.
    for pattern in ((cfg or {}).get("delivery") or {}).get("copy") or []:
        for src in _find_all(str(pattern), roots):
            if shelf:
                add(src, "shelf", shelf / run_dir.name / src.name)
            elif downloads:
                add(src, "downloads", downloads / src.name)

    for src in _extras_paths(extras, run_dir):
        if not src.exists():
            continue
        if shelf:
            add(src, "shelf", shelf / run_dir.name / src.name)
        else:
            add(src, "downloads", downloads / src.name)
    return plan


def _cell(value) -> str:
    """Markdown table cell: escape a pipe so a label cannot break the row."""
    text = str(value or "").replace("|", "\\|").strip()
    return text or "—"


def write_bundle_readme(project: str, run_dir: Path, plan: list[dict],
                        cfg: dict, *, no_voice: bool = False,
                        shots: list[dict] | None = None) -> str:
    """Delivery note as Markdown: what shipped, in what order, with what audio.

    ``shots`` is the ordered shot table; when omitted it is read from the run's
    ``edit_plan.json`` cuts so the note matches what was actually rendered.
    The caller writes it (``(run_dir / "script_vo.md").write_text(...)``) and
    adds it to the plan as an extra, so this module owns no file location.
    """
    run_dir = Path(run_dir)
    if shots is None:
        shots = []
        plan_file = run_dir / "edit_plan.json"
        try:
            data = json.loads(plan_file.read_text(encoding="utf-8"))
            shots = data.get("cuts") or []
        except (OSError, ValueError):
            shots = []

    total = 0.0
    for shot in shots:
        try:
            total += float(shot.get("dur") or 0.0)
        except (TypeError, ValueError):
            continue
    if total <= 0:
        for name in ("edit_plan.json", "manifest.json"):
            try:
                data = json.loads((run_dir / name).read_text(encoding="utf-8"))
                total = float(data.get("duration_sec") or 0.0)
            except (OSError, ValueError, TypeError):
                total = 0.0
            if total > 0:
                break

    deliv = (cfg or {}).get("delivery") or {}
    shelf = deliv.get("dir") or "(not configured)"

    lines = [f"# {project}", "",
             f"Run: `{run_dir.name}`  ",
             f"Duration: {total:.1f} s  ",
             "Audio: " + ("music only, no voiceover"
                          if no_voice else "voiceover + ducked music bed"),
             ""]
    if no_voice:
        lines += ["No voiceover was recorded for this piece. Captions are "
                  "intentionally absent; the master is music + SFX only. "
                  "This is a delivery choice, not a QA failure.", ""]

    if shots:
        lines += ["## Shots", "",
                  "| # | Shot | Spec | Clip | Dur | Window |",
                  "|---|---|---|---|---|---|"]
        cursor = 0.0
        for i, shot in enumerate(shots, 1):
            try:
                dur = float(shot.get("dur") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            t0 = shot.get("t0")
            try:
                t0 = float(t0) if t0 is not None else cursor
            except (TypeError, ValueError):
                t0 = cursor
            label = _cell(shot.get("label") or shot.get("text"))
            spec = _cell(shot.get("spec"))
            clip = _cell(shot.get("clip"))
            lines.append(
                f"| {i} | {label} | {spec} | `{clip}` | "
                f"{dur:.2f} s | {t0:.2f}–{t0 + dur:.2f} s |")
            cursor = t0 + dur
        lines.append("")

    if plan:
        lines += ["## Files", "", "| Kind | File |", "|---|---|"]
        for item in plan:
            lines.append(f"| {item['kind']} | `{Path(item['dest']).name}` |")
        lines.append("")

    lines += ["## Delivery note", "",
              f"Copied from the local run `{run_dir.name}` to "
              f"`{shelf}`. The repository run stays canonical; this shelf is a "
              "deliverable, not a second source of truth.", ""]
    return "\n".join(lines)


def apply_bundle(plan: list[dict]) -> dict:
    """Copy every planned file, creating parents; return copied/skipped names.

    A missing source is skipped and reported instead of aborting the bundle, so
    one optional note cannot block the master. Copy errors (permissions, full
    disk) still raise: a partial shelf that looks complete is worse than a
    loud failure.
    """
    copied: list[str] = []
    skipped: list[str] = []
    for item in plan:
        src = Path(item["src"])
        dest = Path(item["dest"])
        if not src.exists():
            skipped.append(str(src))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() == dest.resolve():
            # Delivering an already-pruned run: runs.json points at the shelf
            # master, so source and destination are the same file.
            copied.append(str(dest))
            continue
        shutil.copy2(src, dest)
        copied.append(str(dest))
    return {"copied": copied, "skipped": skipped}
