"""Unified git command helpers — sync and async with timeout."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["GitTimeoutError", "GitResult", "run_git", "run_git_async"]


class GitTimeoutError(RuntimeError):
    """Raised when a git command exceeds its timeout."""
    pass


@dataclass(slots=True)
class GitResult:
    """Container for git command output."""

    returncode: int
    stdout: str
    stderr: str


def run_git(
    *args: str,
    cwd: str | Path | None = None,
    timeout: int = 60,
    check: bool = True,
) -> GitResult:
    """Run a git command synchronously.

    Raises:
        GitTimeoutError: when the command exceeds *timeout* seconds.
        RuntimeError: when git is not found or returns a non-zero exit code (if check=True).
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("'git' not found in PATH. Please install Git.")
    except subprocess.TimeoutExpired:
        raise GitTimeoutError(f"git {' '.join(args)} timed out after {timeout}s")
    result = GitResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result


async def run_git_async(
    *args: str,
    cwd: str | Path,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: float = 60,
) -> GitResult:
    """Run a git command asynchronously with timeout.

    When env=None (default), subprocess inherits the parent process environment.
    If check=True (default), raises RuntimeError on non-zero exit code.

    On Windows, the subprocess is created with CREATE_NEW_PROCESS_GROUP so
    that the entire process tree can be killed on timeout.
    """
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
    except FileNotFoundError:
        raise RuntimeError("'git' not found in PATH. Please install Git.")
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        # Kill the entire process tree on Windows via CTRL_BREAK_EVENT
        if sys.platform == "win32":
            try:
                import os as _os
                import signal as _signal
                _os.kill(proc.pid, _signal.CTRL_BREAK_EVENT)
            except (ProcessLookupError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        raise GitTimeoutError(
            f"git {' '.join(args)} timed out after {timeout}s"
        ) from None
    result = GitResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr}"
        )
    return result
