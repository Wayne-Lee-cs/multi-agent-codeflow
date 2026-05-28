"""Configuration file support — load defaults from .cagentrc or pyproject.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


# Config key validation: (type, min_value, max_value)
# None means no bound check
_VALID_KEYS: dict[str, tuple[type, int | None, int | None]] = {
    "jobs": (int, 1, 64),
    "timeout": (int, 1, 86400),  # 1s to 24h
    "strategy": (str, None, None),
    "squash": (bool, None, None),
    "quiet": (bool, None, None),
    "retries": (int, 0, 10),
    "worker_model": (str, None, None),
    "integrator_model": (str, None, None),
    "max_turns": (int, 1, 1000),
    "max_tokens": (int, 1, None),
    "keep_worktrees": (bool, None, None),
}

_VALID_STRATEGIES = {"cherry-pick", "merge", "rebase"}


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
    for key, (expected_type, min_val, max_val) in _VALID_KEYS.items():
        if key in data:
            value = data[key]
            # bool is a subclass of int — reject booleans for int-typed keys
            # so that e.g. `jobs = true` is not silently accepted as 1.
            if expected_type is int and isinstance(value, bool):
                continue
            if not isinstance(value, expected_type):
                continue
            # Value range validation for numeric types
            if expected_type is int and isinstance(value, int):
                if min_val is not None and value < min_val:
                    continue
                if max_val is not None and value > max_val:
                    continue
            # Strategy enum validation
            if key == "strategy" and value not in _VALID_STRATEGIES:
                continue
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
