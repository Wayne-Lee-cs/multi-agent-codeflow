"""Safety sandbox — inject PreToolUse hooks to deny dangerous commands in worktrees.

Known limitations:
1. Indirect execution via compiled binaries or non-Bash interpreters
   (e.g., a Go program calling exec("git push")) can bypass the regex check.
2. Write-tool content check applies DENY_PATTERNS to full file content,
   which may block legitimate writes containing command strings in comments,
   tests, or documentation. This is a defense-in-depth tradeoff: it prevents
   writing malicious scripts but may cause false positives for tasks that
   write test/docs mentioning dangerous commands.

These are acceptable for v1 since claude -p in acceptEdits mode does not
intentionally circumvent hooks, and worktrees lack push credentials.
v2 may explore stronger sandboxing (seccomp/namespaces/Docker).
"""

from __future__ import annotations

import json
import shlex
import string
import sys
from pathlib import Path

# Patterns that should never run inside a cagent worktree.
# Each entry is a regex string; the hook script checks Bash tool_input.command.
# Uses \b word boundary instead of ^ anchor to catch commands in chains like
# `cd /tmp && git push` or `mkdir dir && cd dir && git push`.
#
# Note: cagent internal code (e.g., dispatcher._reset_worktree) may use blocked
# commands directly; the sandbox only applies to claude subprocess hooks.
DENY_PATTERNS = [
    # Unix dangerous commands (word boundary anchored, matches in command chains)
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b(?=\s+.*(?:-[a-z]*f|--force))",
    # rm with recursive flag: -rf, -fr, -r, -R, --recursive, and variants.
    # Uses a single pattern to avoid gaps between separate rf/fr/r patterns.
    # Matches: rm -rf, rm -fr, rm -Rf, rm -r, rm --recursive, rm -r/tmp, etc.
    r"\brm\s+-(?:[a-z]*[rRfF]){2}[a-z]*|\brm\s+-(?:[a-z]*[rR])\b|\brm\s+--recursive\b",
    r"\bgit\s+update-ref\b",
    r"\bgit\s+remote\s+(set-url|add)\b",
    # Windows dangerous commands (PowerShell) — also match alias 'ri'
    r"(?:Remove-Item|ri)\s.*-Recurse.*-Force",
    r"(?:Remove-Item|ri)\s.*-Force.*-Recurse",
    r"(?:Remove-Item|ri)\s.*-Recurse",
    r"\bdel\s+/[sS]",
    r"\brd\s+/[sS]",
    # Command chain / indirect execution patterns.
    # Note: bash -c / sh -c are intentionally broad — even "bash -c 'echo hello'"
    # is blocked. This is acceptable for cagent's headless worker context where
    # inline shell scripts are unnecessary and potentially dangerous.
    r"\bbash\s+-c\b",
    r"\bsh\s+-c\b",
    r"\bpython[3]?(?:\.\d+)?\s+-c\b",
    r"\|\s*(?:ba|z|k|da|a)?sh\b",
    # Additional indirect execution paths (node, powershell, cmd, deno)
    r"\bnode\s+-e\b",
    r"\bp(?:owershell|wsh)\s+-[Cc](?:ommand)?\b",
    r"\bcmd\s+/[cC]\b",
    # deno eval/run are blocked broadly — in cagent's headless context,
    # workers should not need to run Deno scripts inline.
    r"\bdeno\s+(eval|run)\b",
    # Ruby/Perl inline execution — same rationale as python -c / node -e
    r"\bruby\s+-e\b",
    r"\bperl\s+-e\b",
]


def _check_tokens(cmd: str) -> str | None:
    """Token-based check for split-flag patterns that regex misses.

    Catches cases like: rm -i -r -f dir/, rm --force --recursive dir/
    Returns the deny reason if blocked, None if safe.
    """
    # Pre-tokenization check for absolute path git commands.
    # shlex.split mishandles Windows paths with spaces (e.g., "C:\Program Files\Git\bin\git.exe"),
    # so we search the raw command for "git[.exe] <subcmd>" patterns after path separators.
    _blocked_git_subcmds = {
        "push": "git push",
        "update-ref": "git update-ref",
    }
    _blocked_git_subcmds_with_flag = {
        "reset": ("--hard", "git reset --hard"),
        "remote": (("set-url", "add"), "git remote"),
    }
    # Normalize for matching: lowercase, strip quotes
    cmd_normalized = cmd.lower().replace('"', '').replace("'", "")
    for marker in ("git push", "git.exe push", "git update-ref", "git.exe update-ref",
                   "git reset --hard", "git.exe reset --hard",
                   "git remote set-url", "git.exe remote set-url",
                   "git remote add", "git.exe remote add"):
        # Check that the marker appears after a path separator or at the start
        idx = cmd_normalized.find(marker)
        if idx >= 0:
            # Verify it's after a path separator (/ or \) or at the start of the command
            if idx == 0 or cmd_normalized[idx - 1] in ("/", "\\"):
                return f"{marker.split(' ', 1)[1]} (abs path check)"
    # Check git clean with -f flag
    for marker in ("git clean", "git.exe clean"):
        idx = cmd_normalized.find(marker)
        if idx >= 0 and (idx == 0 or cmd_normalized[idx - 1] in ("/", "\\")):
            # Check if any subsequent token has -f flag
            rest = cmd[idx + len(marker):]
            for part in rest.split():
                if part.startswith("-") and ("f" in part.lower()):
                    return "git clean with force (abs path check)"

    try:
        tokens = shlex.split(cmd, posix=(sys.platform != "win32"))
    except ValueError:
        return "malformed command (unbalanced quotes)"

    # On Windows posix=False, shlex preserves quotes around tokens.
    # Strip them so token-based checks match correctly.
    if sys.platform == "win32":
        tokens = [t.strip('"').strip("'") for t in tokens]

    # Walk through semicolons and && / || chains — check each sub-command
    i = 0
    while i < len(tokens):
        # Find the command name (skip leading environment var assignments like A=B)
        cmd_start = i
        while cmd_start < len(tokens) and "=" in tokens[cmd_start] and not tokens[cmd_start].startswith("-"):
            cmd_start += 1
        if cmd_start >= len(tokens):
            break

        base = tokens[cmd_start].rstrip(";")
        # Absolute path bypass: /usr/bin/git push should still be caught
        base_name = Path(base).name if "/" in base or "\\" in base else base

        # Check git subcommands (absolute path bypass for regex patterns)
        if base_name == "git" and cmd_start + 1 < len(tokens):
            subcmd = tokens[cmd_start + 1]
            if subcmd == "push":
                return "git push (token check)"
            if subcmd == "reset" and "--hard" in tokens[cmd_start + 2:]:
                return "git reset --hard (token check)"
            if subcmd == "clean" and any(
                t.startswith("-") and ("f" in t or "F" in t)
                for t in tokens[cmd_start + 2:]
                if t.startswith("-") and t not in ("&&", "||", ";", "|")
            ):
                return "git clean with force (token check)"
            if subcmd == "update-ref":
                return "git update-ref (token check)"
            if subcmd == "remote" and cmd_start + 2 < len(tokens):
                if tokens[cmd_start + 2] in ("set-url", "add"):
                    return f"git remote {tokens[cmd_start + 2]} (token check)"

        if base_name == "rm":
            flags: set[str] = set()
            has_recursive_long = False
            has_force_long = False
            past_end_of_options = False
            for t in tokens[cmd_start + 1:]:
                if t in ("&&", "||", ";", "|"):
                    break
                if t == "--":
                    past_end_of_options = True
                    continue
                if past_end_of_options:
                    continue  # positional args after -- are not flags
                if t.startswith("--"):
                    if t == "--recursive":
                        has_recursive_long = True
                    elif t == "--force":
                        has_force_long = True
                elif t.startswith("-") and not t.startswith("--"):
                    for ch in t[1:]:
                        flags.add(ch)

            has_r = "r" in flags or "R" in flags or has_recursive_long
            has_f = "f" in flags or has_force_long
            if has_r and has_f:
                return "rm with recursive+force flags (split)"
            if has_r:
                return "rm with recursive flag (split)"

        # Advance past current sub-command to next separator
        i = cmd_start + 1
        while i < len(tokens) and tokens[i] not in ("&&", "||", ";", "|"):
            i += 1
        if i < len(tokens):
            i += 1  # skip the separator

    return None


# Static copy of _check_tokens for embedding in hook script.
# This avoids inspect.getsource which can fail in frozen/optimized environments.
_CHECK_TOKENS_STATIC = '''\
from pathlib import Path

def _check_tokens(cmd):
    """Token-based check for split-flag patterns that regex misses."""
    cmd_normalized = cmd.lower().replace('"', '').replace("'", "")
    for marker in ("git push", "git.exe push", "git update-ref", "git.exe update-ref",
                   "git reset --hard", "git.exe reset --hard",
                   "git remote set-url", "git.exe remote set-url",
                   "git remote add", "git.exe remote add"):
        idx = cmd_normalized.find(marker)
        if idx >= 0:
            if idx == 0 or cmd_normalized[idx - 1] in ("/", "\\\\"):
                return f"{marker.split(' ', 1)[1]} (abs path check)"
    for marker in ("git clean", "git.exe clean"):
        idx = cmd_normalized.find(marker)
        if idx >= 0 and (idx == 0 or cmd_normalized[idx - 1] in ("/", "\\\\")):
            rest = cmd[idx + len(marker):]
            for part in rest.split():
                if part.startswith("-") and ("f" in part.lower()):
                    return "git clean with force (abs path check)"
    try:
        tokens = shlex.split(cmd, posix=(sys.platform != "win32"))
    except ValueError:
        return "malformed command (unbalanced quotes)"
    if sys.platform == "win32":
        tokens = [t.strip('"').strip("'") for t in tokens]
    i = 0
    while i < len(tokens):
        cmd_start = i
        while cmd_start < len(tokens) and "=" in tokens[cmd_start] and not tokens[cmd_start].startswith("-"):
            cmd_start += 1
        if cmd_start >= len(tokens):
            break
        base = tokens[cmd_start].rstrip(";")
        base_name = Path(base).name if "/" in base or "\\\\" in base else base
        if base_name == "git" and cmd_start + 1 < len(tokens):
            subcmd = tokens[cmd_start + 1]
            if subcmd == "push":
                return "git push (token check)"
            if subcmd == "reset" and "--hard" in tokens[cmd_start + 2:]:
                return "git reset --hard (token check)"
            if subcmd == "clean" and any(
                t.startswith("-") and ("f" in t or "F" in t)
                for t in tokens[cmd_start + 2:]
                if t.startswith("-") and t not in ("&&", "||", ";", "|")
            ):
                return "git clean with force (token check)"
            if subcmd == "update-ref":
                return "git update-ref (token check)"
            if subcmd == "remote" and cmd_start + 2 < len(tokens):
                if tokens[cmd_start + 2] in ("set-url", "add"):
                    return f"git remote {tokens[cmd_start + 2]} (token check)"
        if base_name == "rm":
            flags = set()
            has_recursive_long = False
            has_force_long = False
            past_end_of_options = False
            for t in tokens[cmd_start + 1:]:
                if t in ("&&", "||", ";", "|"):
                    break
                if t == "--":
                    past_end_of_options = True
                    continue
                if past_end_of_options:
                    continue
                if t.startswith("--"):
                    if t == "--recursive":
                        has_recursive_long = True
                    elif t == "--force":
                        has_force_long = True
                elif t.startswith("-") and not t.startswith("--"):
                    for ch in t[1:]:
                        flags.add(ch)
            has_r = "r" in flags or "R" in flags or has_recursive_long
            has_f = "f" in flags or has_force_long
            if has_r and has_f:
                return "rm with recursive+force flags (split)"
            if has_r:
                return "rm with recursive flag (split)"
        i = cmd_start + 1
        while i < len(tokens) and tokens[i] not in ("&&", "||", ";", "|"):
            i += 1
        if i < len(tokens):
            i += 1
    return None
'''


def _get_check_tokens_source() -> str:
    """Return _check_tokens source code for embedding in hook script."""
    return _CHECK_TOKENS_STATIC


# Generate the hook script content — a Python script that reads stdin JSON,
# extracts the Bash command or Write content, and checks all deny patterns.
_HOOK_SCRIPT = '''\
import json, re, shlex, sys

DENY_PATTERNS = $patterns_json
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS]


$check_tokens_source


def _deny(reason):
    result = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "cagent sandbox: " + reason
        }
    }
    json.dump(result, sys.stdout)
    sys.exit(0)


def _check_command(cmd):
    """Check a command string against deny patterns and token checks."""
    if not cmd:
        return
    for cp, raw in zip(_COMPILED_PATTERNS, DENY_PATTERNS):
        if cp.search(cmd):
            _deny("blocked dangerous command matching " + raw)
    token_reason = _check_tokens(cmd)
    if token_reason:
        _deny(token_reason)


try:
    inp = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = inp.get("tool_name", "")
tool_input = inp.get("tool_input", {})

# Check Bash and PowerShell tool commands
if tool_name in ("Bash", "PowerShell"):
    _check_command(tool_input.get("command", ""))

# Check Write/Edit/MultiEdit tool content — block file content that contains
# dangerous patterns (defense-in-depth: prevents writing malicious scripts that
# could be executed later). MultiEdit applies several edits at once, so scan the
# new_string of every edit (otherwise it is a hole in the content check).
if tool_name == "Write":
    content = tool_input.get("content", "")
elif tool_name == "Edit":
    content = tool_input.get("new_string", "")
elif tool_name == "MultiEdit":
    edits = tool_input.get("edits", [])
    content = "\\n".join(
        str(e.get("new_string", "")) for e in edits if isinstance(e, dict)
    ) if isinstance(edits, list) else ""
else:
    content = ""

if content:
    for cp, raw in zip(_COMPILED_PATTERNS, DENY_PATTERNS):
        if cp.search(content):
            _deny("blocked file content matching " + raw)

sys.exit(0)
'''


_HOOK_TEMPLATE = string.Template(_HOOK_SCRIPT)


def prepare_sandbox(worktree_path: str | Path) -> None:
    """Write .claude/settings.local.json with PreToolUse Bash deny hooks."""
    worktree_path = Path(worktree_path)
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Write the hook script
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_script_path = hooks_dir / "cagent-guard.py"

    patterns_json = json.dumps(DENY_PATTERNS, ensure_ascii=False)
    check_tokens_src = _get_check_tokens_source()
    script_content = _HOOK_TEMPLATE.substitute(
        patterns_json=patterns_json,
        check_tokens_source=check_tokens_src,
    )
    hook_script_path.write_text(script_content, encoding="utf-8")

    # Use current Python executable and forward-slash path for cross-platform compat
    python_exe = sys.executable
    script_posix = hook_script_path.as_posix()

    # Build settings.local.json with hooks for Bash, PowerShell, Write, and Edit tools
    hook_entry = {
        "type": "command",
        "command": f"\"{python_exe}\" \"{script_posix}\"",
        "timeout": 10,
        "statusMessage": "cagent safety check...",
    }
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [hook_entry]},
                {"matcher": "PowerShell", "hooks": [hook_entry]},
                {"matcher": "Write", "hooks": [hook_entry]},
                {"matcher": "Edit", "hooks": [hook_entry]},
                {"matcher": "MultiEdit", "hooks": [hook_entry]},
            ]
        }
    }

    target = claude_dir / "settings.local.json"
    target.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
