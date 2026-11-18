#!/usr/bin/env python3
"""finish — the delivery gate: strict QA, delivered master, prune local media.

    fenix finish <project>            # QA -> deliver -> prune local
    fenix finish <project> --no-prune # keep the local copy (experiment)
    fenix finish <project> --re-qa    # force a fresh QA pass

Policy: WSL is pre-production. Finished masters live on the delivery shelf:
`<delivery.dir>/<run>/` (folder with evidence) and a flat copy in
`<delivery.downloads>/`. Local runs keep only JSON evidence; `runs.json` points
at the delivered master.

A strict PASS is reused when `qa_report.json` was produced for the same bytes.
`make` renders -> qa -> finish in one run, so re-running the 30 s gate here would
only duplicate work. `--re-qa` forces the full check.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from ..context import DEFAULT_CONFIG, load_config, python_cmd, subprocess_env
from ..domain import runs
from . import deliver


def qa_is_current(video: Path, cfg: dict | None = None) -> bool:
    """True when a strict PASS report exists for exactly these video bytes.

    The report is local evidence, never the delivery shelf: for a delivered
    master it is read from `output/<sku>/<run>/qa_report.json` (or the
    `output/<sku>/qa/<run>/` sidecar), which is where QA writes it.
    """
    report_path = runs.evidence_path(video, cfg)
    if not report_path.exists() or not video.exists():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        stat = video.stat()
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        report.get("pass")
        and report.get("strict")
        and report.get("video_bytes") == stat.st_size
        and report.get("video_mtime_ns") == stat.st_mtime_ns
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="QA, deliver and prune a run")
    parser.add_argument("project", help="project/SKU name")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-prune", action="store_true",
                        help="keep the local MP4 (WSL experiment)")
    parser.add_argument("--no-downloads", action="store_true",
                        help="skip the flat Downloads copy")
    parser.add_argument("--re-qa", action="store_true",
                        help="run QA even when a strict PASS exists for this file")
    args = parser.parse_args(argv)

    entry = runs.latest_run(args.project)
    if not entry:
        sys.exit(f"[finish] no completed run for {args.project}; render first")

    video = Path(entry.get("video") or "")
    cfg = load_config(args.config, project=runs.input_dir(args.project))
    if not args.re_qa and video.exists() and qa_is_current(video, cfg):
        print("[finish] QA gate: reusing the strict PASS for this file")
    else:
        qa = python_cmd("qa", "--latest", args.project, "--strict")
        print(f"[finish] QA gate: {' '.join(qa)}")
        proc = subprocess.run(qa, env=subprocess_env())
        if proc.returncode != 0:
            sys.exit("[finish] QA failed; master NOT delivered and local media kept")

    deliver_argv = ["--latest", args.project, "--config", str(args.config)]
    if args.no_prune:
        deliver_argv.append("--no-prune")
    if args.no_downloads:
        deliver_argv.append("--no-downloads")
    deliver.main(deliver_argv)
    if not args.no_prune:
        print("[finish] WSL keeps JSON evidence only; the master is delivered.")


if __name__ == "__main__":
    main()
