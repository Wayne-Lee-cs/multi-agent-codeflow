"""Tests for cagent/__main__.py — version check and entry point."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


class TestCheckVersion:
    """Tests for _check_version function."""

    def test_exits_on_old_python(self) -> None:
        """Should sys.exit when Python < 3.11."""
        from cagent.__main__ import _check_version

        with patch.object(sys, "version_info", (3, 10, 0)):
            with pytest.raises(SystemExit):
                _check_version()

    def test_exits_message_contains_version(self) -> None:
        """Exit message should mention the Python version."""
        from cagent.__main__ import _check_version

        with patch.object(sys, "version_info", (3, 10, 0)):
            with pytest.raises(SystemExit, match="3.11"):
                _check_version()

    def test_does_not_exit_on_valid_python(self) -> None:
        """Should not sys.exit when Python >= 3.11."""
        from cagent.__main__ import _check_version

        with patch.object(sys, "version_info", (3, 11, 0)):
            _check_version()  # Should not raise

    def test_does_not_exit_on_newer_python(self) -> None:
        """Should not sys.exit when Python > 3.11."""
        from cagent.__main__ import _check_version

        with patch.object(sys, "version_info", (3, 12, 7)):
            _check_version()  # Should not raise
