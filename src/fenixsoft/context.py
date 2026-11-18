"""Repository locations — the only module that knows the tree.

Every path a stage needs is defined here once. If a directory moves, it moves
here and every command follows. Agent-facing contract: inputs are read from
``input/<sku>/``, derived artifacts are written to ``output/<sku>/`` and
``output/<sku>/<run>/``, never anywhere else.

Production-line config layers
-----------------------------
``config/pipeline.json`` is machinery: portable defaults, no project values.
``config/pipeline.local.json`` is machine-local and gitignored.
``input/<project>/config.json`` is project intent: a sparse overlay of only the
keys this piece changes. Precedence, low to high:

    base (global defaults or explicit --config)  <  machine-local  <  project

An explicit ``--config`` replaces the global base (and, as before, suppresses
the machine-local file) but never defeats the project overlay: a project's
config is part of that project's intent. In the project overlay a ``null``
value removes an inherited key (deep-merge delete), which is how a project
opts out of a global preset such as ``audio.music_only``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# src/fenixsoft/context.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_CONFIG = CONFIG_DIR / "pipeline.json"
# Machine-local overrides (Windows shelf, SFX library). Gitignored; the public
# config stays portable so a fresh clone can run doctor/job/motion unchanged.
LOCAL_CONFIG = CONFIG_DIR / "pipeline.local.json"

INPUT_DIR = REPO_ROOT / "input"
OUTPUT_DIR = REPO_ROOT / "output"
EVAL_DIR = REPO_ROOT / "eval"
ASSETS_DIR = REPO_ROOT / "assets"
DOCS_DIR = REPO_ROOT / "docs"

CACHE_DIR = REPO_ROOT / ".cache"
MODELS_DIR = CACHE_DIR / "models"

FONTS_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
SFX_DIR = ASSETS_DIR / "sfx"

# Names of the per-SKU files, in one place so docs and code cannot drift.
PROJECT_CONFIG_JSON = "config.json"
SKU_JSON = "sku.json"
PICKS_JSON = "picks.json"
CANDIDATES_JSON = "candidates.json"
EDIT_PLAN_JSON = "edit_plan.json"
NARRATION_JSON = "narration_lines.json"
WORD_TIMINGS_JSON = "word_timings.json"


def _read_config(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[config] invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[config] {path} must contain a JSON object")
    return data


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto ``base`` without mutating either.

    A ``None`` in the overlay removes the inherited key, so a project can opt
    out of a global preset instead of re-stating its values.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if value is None:
            merged.pop(key, None)
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def project_config_path(project: Path | str) -> Path:
    """``input/<project>/config.json`` — the project's sparse config overlay."""
    return Path(project) / PROJECT_CONFIG_JSON


def project_overrides(project: Path | str | None) -> dict[str, Any]:
    """The project overlay as written (empty when absent)."""
    if project is None:
        return {}
    path = project_config_path(project)
    if not path.exists():
        return {}
    return _read_config(path)


def count_overrides(overlay: dict[str, Any]) -> int:
    """Number of leaf values a project overlay sets (for ``job``)."""
    total = 0
    for value in overlay.values():
        if isinstance(value, dict):
            total += count_overrides(value)
        else:
            total += 1
    return total


def load_config(path: Path | str | None = None,
                project: Path | str | None = None) -> dict[str, Any]:
    """Load the effective pipeline config for an optional project.

    The default config is portable. A gitignored ``config/pipeline.local.json``
    is deep-merged on top when it exists, so one machine can point at its
    Windows delivery shelf or SFX library without changing the public config.
    ``input/<project>/config.json`` is then deep-merged on top: project intent
    wins over both the global defaults and the machine-local file.

    An explicit ``path`` is a deliberate base: it replaces the global config
    (and, as before, skips the machine-local file) but the project overlay
    still applies, because a project cannot silently lose its own requirements.
    """
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    if not config_path.is_absolute() and not config_path.exists():
        config_path = REPO_ROOT / config_path
    if not config_path.exists():
        raise SystemExit(f"[config] missing {config_path}")
    data = _read_config(config_path)
    is_default = config_path.resolve() == DEFAULT_CONFIG.resolve()
    if is_default and LOCAL_CONFIG.exists():
        data = deep_merge(data, _read_config(LOCAL_CONFIG))
    if project is not None:
        data = deep_merge(data, project_overrides(project))
    return data


def load_config_for(project: Path | str,
                    path: Path | str | None = None) -> dict[str, Any]:
    """``load_config`` scoped to a resolved project directory."""
    return load_config(path, project=project)


def resolve_sku_dir(value: Path | str) -> Path:
    """Accept ``iphone14_blue``, ``input/iphone14_blue`` or an absolute path."""
    raw = Path(value).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        candidates.append(INPUT_DIR / raw)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    known = ", ".join(sorted(known_skus())) or "(none)"
    looked = "\n".join(f"  - {c}" for c in candidates)
    raise SystemExit(f"[sku] not found: {value}\nlooked in:\n{looked}\nknown SKUs: {known}")


def known_skus() -> list[str]:
    """Every folder under input/ that carries a sku.json."""
    if not INPUT_DIR.exists():
        return []
    return [d.name for d in INPUT_DIR.iterdir()
            if d.is_dir() and (d / SKU_JSON).exists()]


def sku_name(sku_dir: Path | str) -> str:
    return Path(sku_dir).name


def output_dir(sku_dir: Path | str) -> Path:
    """Per-SKU artifact directory: output/<sku>/."""
    return OUTPUT_DIR / sku_name(sku_dir)


def artifact(sku_dir: Path | str, name: str) -> Path:
    """A flat derived artifact such as beat_grid.json or narration_lines.json."""
    return output_dir(sku_dir) / name


def stage_dir(sku_dir: Path | str, stage: str) -> Path:
    """``output/<sku>/<stage>/`` — timestamped runs of a post stage.

    ``motion``, ``comp``, ``fx``, ``grade``, ``longform`` and ``track`` each own
    one of these directories; stable refs (``@motion/latest``) resolve here.
    """
    return output_dir(sku_dir) / stage


def cache_dir(name: str) -> Path:
    return CACHE_DIR / name


def repo_path(value: str | Path) -> Path:
    """Resolve a config-relative asset path (e.g. assets/music/track.wav)."""
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def display_path(path: Path) -> str:
    """Repo-relative path for logs when possible."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def subprocess_env() -> dict[str, str]:
    """Environment for child processes so ``python -m fenixsoft`` always imports."""
    env = os.environ.copy()
    src = str(SRC_DIR)
    existing = env.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p and p != src]
    env["PYTHONPATH"] = os.pathsep.join([src, *parts])
    return env


def python_cmd(*args: str) -> list[str]:
    """Command for a nested pipeline step, keeping the active interpreter."""
    return [sys.executable, "-m", "fenixsoft", *args]
