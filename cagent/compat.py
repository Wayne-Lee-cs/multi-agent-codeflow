"""Cross-platform compatibility layer — stdin polling, ANSI, atomic writes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import msvcrt
else:
    import select


def stdin_has_key() -> bool:
    """Non-blocking check for a keypress on stdin."""
    # _IS_WINDOWS is the runtime guard (also monkeypatched in tests), so mypy
    # cannot platform-narrow it; msvcrt is Windows-only in typeshed.
    if _IS_WINDOWS:
        return bool(msvcrt.kbhit())  # type: ignore[attr-defined]
    return bool(select.select([sys.stdin], [], [], 0)[0])


def read_key() -> str:
    """Read a single keypress character."""
    if _IS_WINDOWS:
        return str(msvcrt.getwch())  # type: ignore[attr-defined]
    return sys.stdin.read(1)


def is_tty() -> bool:
    """Check if stdin is a terminal."""
    return sys.stdin.isatty()


def enable_ansi() -> bool:
    """Enable VT100 ANSI escape processing on Windows terminals.

    On Unix this is a no-op and always returns True. On Windows, this enables
    color/escape codes in cmd.exe (Windows Terminal and PowerShell 7 already
    support them natively). Returns True if ANSI processing is available.
    """
    if _IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # STD_OUTPUT_HANDLE = -11
            handle = kernel32.GetStdHandle(-11)
            # Get current console mode
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            result = kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return bool(result)
        except (OSError, AttributeError):
            return False
    return True


def is_pid_active(pid: int) -> bool:
    """Check if a process is still running (cross-platform).

    On Windows, uses GetExitCodeProcess to distinguish running processes
    from exited-but-not-yet-reaped ones (OpenProcess alone is insufficient).
    """
    if pid <= 0:
        return False
    try:
        if _IS_WINDOWS:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            _STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == _STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tmp + rename.

    Uses os.replace() which is atomic on both Windows and Unix,
    unlike Path.replace() which fails on Windows if the target exists.

    Uses tempfile.mkstemp to generate a unique temporary filename,
    preventing concurrent write conflicts.
    """
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create unique temp file in the same directory
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
