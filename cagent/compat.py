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
    if _IS_WINDOWS:
        return msvcrt.kbhit()
    return bool(select.select([sys.stdin], [], [], 0)[0])


def read_key() -> str:
    """Read a single keypress character."""
    if _IS_WINDOWS:
        return msvcrt.getwch()
    return sys.stdin.read(1)


def is_tty() -> bool:
    """Check if stdin is a terminal."""
    return sys.stdin.isatty()


def enable_ansi() -> None:
    """Enable VT100 ANSI escape processing on Windows terminals.

    On Unix this is a no-op. On Windows, this enables color/escape codes
    in cmd.exe (Windows Terminal and PowerShell 7 already support them natively).
    """
    if _IS_WINDOWS:
        os.system("")


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tmp + rename.

    Uses os.replace() which is atomic on both Windows and Unix,
    unlike Path.replace() which fails on Windows if the target exists.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))
