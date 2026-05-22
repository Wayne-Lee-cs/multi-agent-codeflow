"""Configuration file support — load defaults from .cagentrc or pyproject.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


_VALID_KEYS = {
    "jobs": int,
    "timeout": int,
    "strategy": str,
    "squash": bool,
    "quiet": bool,
    "retries": int,
    "worker_model": str,
    "integrator_model": str,
    "max_turns": int,
    "max_tokens": int,
    "keep_worktrees": bool,
}


def load_config(repo_root: Path) -> dict[str, Any]:
    """Load cagent configuration from .cagentrc or pyproject.toml [tool.cagent].

    Lookup order (first found wins):
      1. .cagentrc  (TOML)
      2. pyproject.toml [tool.cagent]

    Returns a dict of validated config values. Unknown keys are ignored.
    """
    rc_path = repo_root / ".cagentrc"
    if rc_path.is_file():
        return _parse_and_validate(rc_path, section=None)

    pyproject_path = repo_root / "pyproject.toml"
    if pyproject_path.is_file():
        return _parse_and_validate(pyproject_path, section="tool.cagent")

    return {}


def _parse_and_validate(
    path: Path,
    section: str | None,
) -> dict[str, Any]:
    """Parse a TOML file and extract/validate cagent config keys."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError):
        return {}

    if section:
        for key in section.split("."):
            if not isinstance(data, dict) or key not in data:
                return {}
            data = data[key]

    if not isinstance(data, dict):
        return {}

    result: dict[str, Any] = {}
    for key, expected_type in _VALID_KEYS.items():
        if key in data:
            value = data[key]
            if isinstance(value, expected_type):
                result[key] = value
    return result


def apply_config(args: Any, config: dict[str, Any]) -> None:
    """Apply config defaults to argparse Namespace — CLI args take precedence.

    Only sets a value if the argparse attribute is at its default (None, False, or
    the argparse-declared default).
    """
    _ARGPARSE_DEFAULTS = {
        "jobs": 4,
        "timeout": 1800,
        "strategy": "cherry-pick",
        "squash": False,
        "quiet": False,
        "retries": 0,
        "worker_model": None,
        "integrator_model": None,
        "max_turns": None,
        "max_tokens": None,
        "keep_worktrees": False,
    }

    for key, value in config.items():
        if not hasattr(args, key):
            continue
        current = getattr(args, key)
        default = _ARGPARSE_DEFAULTS.get(key)
        if current == default:
            setattr(args, key, value)
