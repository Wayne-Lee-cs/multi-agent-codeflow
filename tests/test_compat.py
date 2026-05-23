"""Unit tests for cagent/compat.py — atomic_write, is_tty, enable_ansi."""

import os
import sys
import threading
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
        # With mkstemp, temp files have random names; check nothing extra remains
        remaining = list(tmp_path.iterdir())
        assert remaining == [target]

    def test_unicode_content(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "你好世界 🌍")
        assert target.read_text(encoding="utf-8") == "你好世界 🌍"

    def test_concurrent_writes_different_files(self, tmp_path):
        """Concurrent atomic_write calls to different files succeed (58.3.2)."""
        errors = []

        def writer(filename: str, value: str):
            try:
                for _ in range(10):
                    atomic_write(tmp_path / filename, value)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(f"out-{i}.txt", f"data-{i}"))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # All files should exist with correct content
        for i in range(5):
            assert (tmp_path / f"out-{i}.txt").read_text(encoding="utf-8") == f"data-{i}"
        # No temp files left behind
        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert tmp_files == []


class TestIsTty:
    def test_returns_bool(self):
        result = is_tty()
        assert isinstance(result, bool)


class TestEnableAnsi:
    def test_does_not_raise(self):
        # Should work on both Windows and Unix without error
        enable_ansi()
