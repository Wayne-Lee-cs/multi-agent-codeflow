"""Configuration file support — load defaults from .cagentrc or pyproject.toml."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


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
    except tomllib.TOMLDecodeError:
        _log.warning("Invalid TOML in %s — ignoring config", path)
        return {}
    except OSError:
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


class _Unset:
    """Sentinel for argparse defaults.

    Distinguishes "the user did not pass this flag" from "the user passed a
    value that happens to equal the hard default" (e.g. ``--jobs 4`` or
    ``--strategy cherry-pick``). Config-overridable options in the run
    subparser use this as their default so an explicit CLI value always wins
    over the config file, even when it equals the built-in default.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<unset>"


UNSET = _Unset()


# Hard defaults for config-overridable options. Used to fill in a value when
# neither the CLI nor the config file provides one.
_ARGPARSE_DEFAULTS: dict[str, Any] = {
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


def apply_config(args: Any, config: dict[str, Any]) -> None:
    """Resolve each overridable option with precedence: CLI > config file > default.

    Overridable args use the :data:`UNSET` sentinel as their argparse default.
    An explicit CLI value (even one equal to the hard default, e.g. ``--jobs 4``)
    is therefore distinguishable from "not provided" and always wins over the
    config file. After this call every overridable attribute holds a concrete
    value (the sentinel is fully resolved).
    """
    for key, hard_default in _ARGPARSE_DEFAULTS.items():
        if not hasattr(args, key):
            continue
        current = getattr(args, key)
        if current is UNSET:
            # Not provided on the CLI → config file wins, else the hard default.
            setattr(args, key, config.get(key, hard_default))
        # else: explicitly provided on the CLI → keep as-is, ignore the config.
