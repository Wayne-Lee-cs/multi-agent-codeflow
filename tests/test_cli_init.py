"""Tests for cagent/cli/__init__.py - lazy imports."""

from __future__ import annotations

import pytest


class TestLazyImports:
    """Tests for __getattr__ lazy import mechanism."""

    def test_known_attribute_resolved(self) -> None:
        """Known lazy attributes should be importable."""
        from cagent.cli import _fmt_elapsed
        assert callable(_fmt_elapsed)

    def test_unknown_attribute_raises(self) -> None:
        """Unknown attributes should raise AttributeError."""
        import cagent.cli
        with pytest.raises(AttributeError, match="no attribute"):
            _ = cagent.cli.nonexistent_function

    def test_all_lazy_imports_listed(self) -> None:
        """All lazy imports should be in _LAZY_IMPORTS dict."""
        from cagent.cli import _LAZY_IMPORTS
        expected = {
            "_fmt_elapsed", "_get_repo_root", "_find_run_dir",
            "_terminate_pid", "_write_summary", "_print_dashboard_table",
            "_cmd_cancel", "_cmd_clean",
        }
        assert set(_LAZY_IMPORTS.keys()) == expected
