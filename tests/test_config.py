"""Tests for cagent.config — configuration file loading."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from cagent.config import apply_config, load_config


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a temporary repo root directory."""
    return tmp_path


class TestLoadConfig:
    def test_no_config_files(self, repo: Path) -> None:
        """Returns empty dict when no config files exist."""
        assert load_config(repo) == {}

    def test_cagentrc_basic(self, repo: Path) -> None:
        """Reads .cagentrc with basic key-value pairs."""
        (repo / ".cagentrc").write_text(
            'jobs = 8\ntimeout = 3600\nstrategy = "merge"\n',
            encoding="utf-8",
        )
        cfg = load_config(repo)
        assert cfg["jobs"] == 8
        assert cfg["timeout"] == 3600
        assert cfg["strategy"] == "merge"

    def test_cagentrc_bool_keys(self, repo: Path) -> None:
        """Boolean keys are parsed correctly."""
        (repo / ".cagentrc").write_text(
            "squash = true\nquiet = false\nkeep_worktrees = true\n",
            encoding="utf-8",
        )
        cfg = load_config(repo)
        assert cfg["squash"] is True
        assert cfg["quiet"] is False
        assert cfg["keep_worktrees"] is True

    def test_cagentrc_bool_rejected_for_int_key(self, repo: Path) -> None:
        """A boolean value for an int-typed key is rejected (not coerced to 1)."""
        (repo / ".cagentrc").write_text(
            "jobs = true\ntimeout = 3600\n", encoding="utf-8",
        )
        cfg = load_config(repo)
        assert "jobs" not in cfg
        assert cfg["timeout"] == 3600

    def test_cagentrc_ignores_unknown_keys(self, repo: Path) -> None:
        """Unknown keys in .cagentrc are silently ignored."""
        (repo / ".cagentrc").write_text(
            'jobs = 8\nunknown_key = "hello"\nanother = 42\n',
            encoding="utf-8",
        )
        cfg = load_config(repo)
        assert cfg == {"jobs": 8}

    def test_cagentrc_type_mismatch_ignored(self, repo: Path) -> None:
        """Values with wrong types are silently ignored."""
        (repo / ".cagentrc").write_text(
            'jobs = "not_an_int"\ntimeout = 3600\n',
            encoding="utf-8",
        )
        cfg = load_config(repo)
        assert "jobs" not in cfg
        assert cfg["timeout"] == 3600

    def test_pyproject_toml_tool_cagent(self, repo: Path) -> None:
        """Reads [tool.cagent] section from pyproject.toml."""
        (repo / "pyproject.toml").write_text(
            '[tool.cagent]\njobs = 16\nretries = 3\nstrategy = "rebase"\n',
            encoding="utf-8",
        )
        cfg = load_config(repo)
        assert cfg["jobs"] == 16
        assert cfg["retries"] == 3
        assert cfg["strategy"] == "rebase"

    def test_pyproject_toml_no_tool_cagent(self, repo: Path) -> None:
        """pyproject.toml without [tool.cagent] returns empty."""
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\n',
            encoding="utf-8",
        )
        assert load_config(repo) == {}

    def test_cagentrc_takes_priority(self, repo: Path) -> None:
        """.cagentrc wins over pyproject.toml."""
        (repo / ".cagentrc").write_text("jobs = 2\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text(
            "[tool.cagent]\njobs = 99\n", encoding="utf-8"
        )
        cfg = load_config(repo)
        assert cfg["jobs"] == 2

    def test_invalid_toml_returns_empty(self, repo: Path) -> None:
        """Malformed TOML file returns empty dict gracefully."""
        (repo / ".cagentrc").write_text("this is not valid toml {{{}}", encoding="utf-8")
        assert load_config(repo) == {}

    def test_all_valid_keys(self, repo: Path) -> None:
        """All documented config keys are accepted."""
        (repo / ".cagentrc").write_text(
            'jobs = 4\n'
            'timeout = 900\n'
            'strategy = "cherry-pick"\n'
            'squash = false\n'
            'quiet = true\n'
            'retries = 1\n'
            'worker_model = "claude-sonnet-4-6"\n'
            'integrator_model = "claude-opus-4-6"\n'
            'max_turns = 10\n'
            'max_tokens = 50000\n'
            'keep_worktrees = true\n',
            encoding="utf-8",
        )
        cfg = load_config(repo)
        assert len(cfg) == 11


class TestApplyConfig:
    def _make_args(self, **kwargs: object) -> argparse.Namespace:
        """Create an argparse Namespace with run-command defaults."""
        defaults = {
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
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_applies_defaults(self) -> None:
        """Config values override argparse defaults."""
        args = self._make_args()
        apply_config(args, {"jobs": 8, "timeout": 3600})
        assert args.jobs == 8
        assert args.timeout == 3600

    def test_cli_overrides_config(self) -> None:
        """Explicit CLI values are not overwritten by config."""
        args = self._make_args(jobs=16)
        apply_config(args, {"jobs": 8})
        assert args.jobs == 16

    def test_none_defaults_overridden(self) -> None:
        """None-valued defaults (optional args) are overridden."""
        args = self._make_args()
        apply_config(args, {"worker_model": "claude-sonnet-4-6", "max_turns": 15})
        assert args.worker_model == "claude-sonnet-4-6"
        assert args.max_turns == 15

    def test_bool_defaults_overridden(self) -> None:
        """Bool defaults (False) are overridden by config True."""
        args = self._make_args()
        apply_config(args, {"squash": True, "quiet": True})
        assert args.squash is True
        assert args.quiet is True

    def test_unknown_keys_ignored(self) -> None:
        """Config keys not in argparse are silently skipped."""
        args = self._make_args()
        apply_config(args, {"nonexistent_key": "value"})
        assert not hasattr(args, "nonexistent_key")

    def test_empty_config(self) -> None:
        """Empty config dict changes nothing."""
        args = self._make_args()
        apply_config(args, {})
        assert args.jobs == 4
        assert args.timeout == 1800
