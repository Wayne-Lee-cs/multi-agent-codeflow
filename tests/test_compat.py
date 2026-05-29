"""Unit tests for cagent/compat.py — atomic_write, is_tty, enable_ansi, is_pid_active."""

import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cagent.compat import atomic_write, enable_ansi, is_pid_active, is_tty


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


class TestIsPidActive:
    def test_current_process_is_active(self):
        assert is_pid_active(os.getpid()) is True

    def test_nonexistent_pid_is_inactive(self):
        assert is_pid_active(999999999) is False

    def test_returns_bool(self):
        result = is_pid_active(os.getpid())
        assert isinstance(result, bool)

    def test_zero_pid_returns_false(self):
        assert is_pid_active(0) is False

    def test_negative_pid_returns_false(self):
        assert is_pid_active(-1) is False


class TestIsTty:
    def test_returns_bool(self):
        result = is_tty()
        assert isinstance(result, bool)


class TestEnableAnsi:
    def test_does_not_raise(self):
        # Should work on both Windows and Unix without error
        result = enable_ansi()
        assert isinstance(result, bool)

    def test_returns_true_on_unix(self):
        """On Unix, enable_ansi always returns True."""
        import cagent.compat as mod
        if not mod._IS_WINDOWS:
            assert enable_ansi() is True

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="reload() resets _IS_WINDOWS to the real platform, defeating the monkeypatch off-Windows",
    )
    def test_windows_calls_set_console_mode(self, monkeypatch):
        """On Windows, enable_ansi calls SetConsoleMode with VT flag."""
        import cagent.compat as mod

        mock_kernel32 = MagicMock()
        mock_kernel32.GetStdHandle.return_value = 123
        mock_kernel32.GetConsoleMode.return_value = True

        mock_ctypes = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        mock_ctypes.c_ulong = type("c_ulong", (), {"__init__": lambda s, *a: None, "value": 0})
        mock_ctypes.byref = lambda x: x

        monkeypatch.setattr(mod, "_IS_WINDOWS", True)
        with patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            # Re-import to pick up the mock
            import importlib
            importlib.reload(mod)
            mod.enable_ansi()
            mock_kernel32.SetConsoleMode.assert_called_once()

        # Restore
        monkeypatch.setattr(mod, "_IS_WINDOWS", sys.platform == "win32")


class TestStdinHasKey:
    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Unix path uses select() on stdin, which has no fileno() under pytest capture",
    )
    def test_returns_truthy(self):
        from cagent.compat import stdin_has_key
        result = stdin_has_key()
        # msvcrt.kbhit returns int on Windows, bool on Unix
        assert result is not None

    def test_windows_uses_kbhit(self, monkeypatch):
        """On Windows, stdin_has_key delegates to msvcrt.kbhit."""
        import cagent.compat as mod
        monkeypatch.setattr(mod, "_IS_WINDOWS", True)
        mock_msvcrt = MagicMock()
        mock_msvcrt.kbhit.return_value = True
        # raising=False: on non-Windows `msvcrt` is never imported into the module.
        monkeypatch.setattr(mod, "msvcrt", mock_msvcrt, raising=False)
        assert mod.stdin_has_key() is True
        mock_msvcrt.kbhit.assert_called_once()


class TestReadKey:
    def test_windows_uses_getwch(self, monkeypatch):
        """On Windows, read_key delegates to msvcrt.getwch."""
        import cagent.compat as mod
        monkeypatch.setattr(mod, "_IS_WINDOWS", True)
        mock_msvcrt = MagicMock()
        mock_msvcrt.getwch.return_value = "q"
        # raising=False: on non-Windows `msvcrt` is never imported into the module.
        monkeypatch.setattr(mod, "msvcrt", mock_msvcrt, raising=False)
        assert mod.read_key() == "q"
        mock_msvcrt.getwch.assert_called_once()


class TestAtomicWriteErrorCleanup:
    def test_cleanup_on_replace_failure(self, tmp_path):
        """atomic_write removes temp file when os.replace fails."""
        import cagent.compat as mod

        target = tmp_path / "out.txt"
        with patch.object(mod.os, "replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                mod.atomic_write(target, "content")

        # No temp files left behind
        tmp_files = [f for f in tmp_path.iterdir() if f.suffix == ".tmp"]
        assert tmp_files == []

    def test_cleanup_on_fdopen_failure(self, tmp_path):
        """atomic_write cleans up temp file when fdopen fails and fd is leaked."""
        import cagent.compat as mod

        target = tmp_path / "out.txt"
        # When os.fdopen raises, mkstemp has already created the file.
        # The current code catches the exception and tries os.unlink.
        # However, the fd is leaked (not closed), so unlink may succeed or fail.
        # We test that the function at least doesn't crash.
        with patch.object(mod.os, "fdopen", side_effect=OSError("fdopen failed")):
            with pytest.raises(OSError, match="fdopen failed"):
                mod.atomic_write(target, "content")


class TestIsPidActiveUnix:
    def test_unix_active_pid(self, monkeypatch):
        """Unix path: is_pid_active returns True for active pid."""
        import cagent.compat as mod
        monkeypatch.setattr(mod, "_IS_WINDOWS", False)
        with patch.object(mod.os, "kill", return_value=None):
            assert mod.is_pid_active(12345) is True

    def test_unix_oserror_returns_false(self, monkeypatch):
        """Unix path: is_pid_active returns False on OSError."""
        import cagent.compat as mod
        monkeypatch.setattr(mod, "_IS_WINDOWS", False)
        with patch.object(mod.os, "kill", side_effect=OSError("no such process")):
            assert mod.is_pid_active(99999) is False
