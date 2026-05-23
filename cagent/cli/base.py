"""Shared CLI utilities — repo lookup, preflight checks, helpers."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

_AUTH_CACHE_TTL = 300  # 5 minutes


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds into a human-readable string."""
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m{secs}s"
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins}m{secs}s"


def _preflight_check(
    check_auth: bool = False,
    repo_root: Path | None = None,
    force_auth: bool = False,
) -> None:
    """Verify required tools are available before running.

    If check_auth=True, also verify that `claude -p` can authenticate.
    If repo_root is given, auth success is cached at .cagent/auth_ok for 5 minutes.
    If force_auth=True, always re-verify (e.g. when --api-key is used).
    """
    if not shutil.which("git"):
        print("Error: 'git' not found in PATH. Please install Git.", file=sys.stderr)
        sys.exit(1)
    claude_bin = shutil.which("claude") or shutil.which("claude.cmd")
    if not claude_bin:
        print(
            "Error: 'claude' CLI not found in PATH.\n"
            "  Install Claude Code: https://docs.anthropic.com/en/docs/claude-code\n"
            "  Ensure 'claude' is in your PATH after installation.",
            file=sys.stderr,
        )
        sys.exit(1)

    if check_auth:
        _auth_preflight_check(claude_bin, repo_root=repo_root, force_auth=force_auth)


def _auth_preflight_check(
    claude_bin: str,
    repo_root: Path | None = None,
    force_auth: bool = False,
) -> None:
    """Run a quick claude -p test to verify authentication works.

    If repo_root is given, caches success at .cagent/auth_ok for _AUTH_CACHE_TTL seconds.
    If force_auth=True, always re-validates (key may have changed).
    """
    # Check cache unless forced
    if not force_auth and repo_root is not None:
        cache_path = repo_root / ".cagent" / "auth_ok"
        if cache_path.exists():
            try:
                cached_ts = float(cache_path.read_text(encoding="utf-8").strip())
                if (time.time() - cached_ts) < _AUTH_CACHE_TTL:
                    print("Checking claude CLI authentication... cached OK")
                    return
            except (ValueError, OSError):
                pass  # Cache corrupt, re-check

    print("Checking claude CLI authentication... ", end="", flush=True)
    try:
        result = subprocess.run(
            [claude_bin, "-p", "say hello", "--output-format", "json", "--max-turns", "1"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except subprocess.TimeoutExpired:
        print("TIMEOUT")
        _print_auth_diagnostics()
        sys.exit(1)
    except FileNotFoundError:
        print("FAILED")
        print(f"  Could not execute: {claude_bin}", file=sys.stderr)
        sys.exit(1)

    if result.returncode == 0:
        print("OK")
        # Cache auth success
        if repo_root is not None:
            try:
                cache_dir = repo_root / ".cagent"
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / "auth_ok").write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass  # Best effort
        return

    print("FAILED")

    combined = stderr + stdout
    if "apiKeySource" in combined or "403" in combined or "not allowed" in combined.lower():
        print("\n  Authentication failed: claude -p cannot authenticate.", file=sys.stderr)
    elif "not found" in combined.lower():
        print(f"\n  claude CLI not found at: {claude_bin}", file=sys.stderr)
    else:
        print(f"\n  claude -p exited with code {result.returncode}", file=sys.stderr)
        if stderr.strip():
            print(f"  stderr: {stderr.strip()[:200]}", file=sys.stderr)

    _print_auth_diagnostics()
    sys.exit(1)


def _print_auth_diagnostics() -> None:
    """Print environment diagnostics for authentication troubleshooting."""
    print("\nAuth diagnostics:", file=sys.stderr)
    env_vars = [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    ]
    for var in env_vars:
        val = os.environ.get(var)
        if val is None:
            print(f"  {var}: not set", file=sys.stderr)
        elif var == "ANTHROPIC_API_KEY":
            print(f"  {var}: {val[:8]}...{val[-4:]}" if len(val) > 12 else f"  {var}: (set, short)", file=sys.stderr)
        else:
            print(f"  {var}: {val}", file=sys.stderr)

    print("\nPossible fixes:", file=sys.stderr)
    print("  1. Run 'claude auth login' to authenticate via OAuth", file=sys.stderr)
    print("  2. Set a valid API key: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
    print("  3. If using a proxy, verify ANTHROPIC_BASE_URL is correct", file=sys.stderr)
    print("  4. Use --api-key flag: cagent run --api-key sk-ant-... tasks.txt", file=sys.stderr)


def _get_repo_root() -> Path:
    """Find the git repo root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        print("Error: not inside a git repository.", file=sys.stderr)
        sys.exit(1)
    return Path(result.stdout.strip())


def _get_runs_dir(repo_root: Path) -> Path:
    return repo_root / ".cagent" / "runs"


def _find_run_dir(repo_root: Path, run_id: str | None) -> Path:
    """Find a run directory by ID, or the latest one."""
    runs_dir = _get_runs_dir(repo_root)
    if not runs_dir.exists():
        print("No runs found.", file=sys.stderr)
        sys.exit(1)

    if run_id:
        target = runs_dir / run_id
        if not target.exists():
            print(f"Run not found: {run_id}", file=sys.stderr)
            sys.exit(1)
        return target

    dirs = sorted(runs_dir.iterdir(), reverse=True)
    for d in dirs:
        if d.is_dir() and ((d / "dashboard.json").exists() or (d / "tasks.json").exists()):
            return d
    print("No completed runs found.", file=sys.stderr)
    sys.exit(1)


def _is_pid_active(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION works even for elevated processes
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


def _terminate_pid(pid: int) -> None:
    """Terminate a process (cross-platform).

    On Windows, sends CTRL_BREAK_EVENT for graceful shutdown (requires the
    target process to have been created with CREATE_NEW_PROCESS_GROUP).
    Falls back to TerminateProcess (via taskkill) if CTRL_BREAK fails.
    On Unix, sends SIGTERM.
    """
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGTERM)
    except (PermissionError, OSError) as e:
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                )
            except (OSError, subprocess.SubprocessError):
                print(f"Failed to terminate process {pid}: {e}", file=sys.stderr)
        else:
            print(f"Permission denied sending signal to process {pid}.", file=sys.stderr)
    except ProcessLookupError:
        pass


def _prompt_clean_memory(memory_dir: Path) -> None:
    """Ask user whether to keep or delete subagent memory files."""
    try:
        response = input("  Delete memory files? [y/N] ").strip().lower()
    except EOFError:
        print("  Memory preserved.")
        return
    if response in ("y", "yes"):
        shutil.rmtree(memory_dir, ignore_errors=True)
        print("  Memory deleted.")
    else:
        print("  Memory preserved.")
