"""Unified git command helpers — sync and async with timeout."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path


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
) -> subprocess.CompletedProcess[str]:
    """Run a git command synchronously, raising RuntimeError on failure."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("'git' not found in PATH. Please install Git.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} timed out after {timeout}s")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {e.returncode}): {e.stderr.strip()}"
        ) from e


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
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError("'git' not found in PATH. Please install Git.")
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
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
