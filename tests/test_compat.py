"""Unit tests for cagent/compat.py — atomic_write, is_tty, enable_ansi."""

import os
import sys
from pathlib import Path

import pytest

from cagent.compat import atomic_write, enable_ansi, is_tty


class TestAtomicWrite:
    def test_basic_write(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "dir" / "out.txt"
        atomic_write(target, "nested")
        assert target.read_text(encoding="utf-8") == "nested"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "first")
        atomic_write(target, "second")
        assert target.read_text(encoding="utf-8") == "second"

    def test_no_tmp_file_left_behind(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "content")
        tmp_file = target.with_suffix(".tmp")
        assert not tmp_file.exists()

    def test_unicode_content(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "你好世界 🌍")
        assert target.read_text(encoding="utf-8") == "你好世界 🌍"


class TestIsTty:
    def test_returns_bool(self):
        result = is_tty()
        assert isinstance(result, bool)


class TestEnableAnsi:
    def test_does_not_raise(self):
        # Should work on both Windows and Unix without error
        enable_ansi()
