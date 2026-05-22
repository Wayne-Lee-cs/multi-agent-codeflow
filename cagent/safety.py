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
import sys
from pathlib import Path
from string import Template

# Patterns that should never run inside a cagent worktree.
# Each entry is a regex string; the hook script checks Bash tool_input.command.
# Uses \b word boundary instead of ^ anchor to catch commands in chains like
# `cd /tmp && git push` or `mkdir dir && cd dir && git push`.
DENY_PATTERNS = [
    # Unix dangerous commands (word boundary anchored, matches in command chains)
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f",
    # rm with recursive flag: -rf, -fr, -r, -R, --recursive, and variants.
    # Uses a single pattern to avoid gaps between separate rf/fr/r patterns.
    # Matches: rm -rf, rm -fr, rm -Rf, rm -r, rm --recursive, rm -r/tmp, etc.
    r"\brm\s+-(?:[a-z]*[rRfF]){2}[a-z]*|\brm\s+-(?:[a-z]*[rR])\b|\brm\s+--recursive\b",
    r"\bgit\s+update-ref\b",
    r"\bgit\s+remote\s+(set-url|add)\b",
    # Windows dangerous commands (PowerShell)
    r"Remove-Item\s.*-Recurse.*-Force",
    r"Remove-Item\s.*-Force.*-Recurse",
    r"\bdel\s+/[sS]",
    r"\brd\s+/[sS]",
    # Command chain / indirect execution patterns.
    # Note: bash -c / sh -c are intentionally broad — even "bash -c 'echo hello'"
    # is blocked. This is acceptable for cagent's headless worker context where
    # inline shell scripts are unnecessary and potentially dangerous.
    r"\bbash\s+-c\b",
    r"\bsh\s+-c\b",
    r"\bpython[3]?\s+-c\b",
    r"\|\s*(ba)?sh\b",
    # Additional indirect execution paths (node, powershell, cmd, deno)
    r"\bnode\s+-e\b",
    r"\bp(?:owershell|wsh)\s+-[Cc](?:ommand)?\b",
    r"\bcmd\s+/[cC]\b",
    # deno eval/run are blocked broadly — in cagent's headless context,
    # workers should not need to run Deno scripts inline.
    r"\bdeno\s+(eval|run)\b",
]


def _check_tokens(cmd: str) -> str | None:
    """Token-based check for split-flag patterns that regex misses.

    Catches cases like: rm -i -r -f dir/, rm --force --recursive dir/
    Returns the deny reason if blocked, None if safe.
    """
    try:
        tokens = shlex.split(cmd, posix=(sys.platform != "win32"))
    except ValueError:
        return None

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
        if base == "rm":
            flags: set[str] = set()
            has_recursive_long = False
            has_force_long = False
            for t in tokens[cmd_start + 1:]:
                if t in ("&&", "||", ";", "|"):
                    break
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


# Generate the hook script content — a Python script that reads stdin JSON,
# extracts the Bash command or Write content, and checks all deny patterns.
_HOOK_SCRIPT = '''\
import json, re, shlex, sys

DENY_PATTERNS = $patterns_json


def _check_tokens(cmd):
    """Token-based check for split-flag patterns (e.g. rm -i -r -f dir/)."""
    try:
        tokens = shlex.split(cmd, posix=(sys.platform != "win32"))
    except ValueError:
        return None
    i = 0
    while i < len(tokens):
        cmd_start = i
        while cmd_start < len(tokens) and "=" in tokens[cmd_start] and not tokens[cmd_start].startswith("-"):
            cmd_start += 1
        if cmd_start >= len(tokens):
            break
        base = tokens[cmd_start].rstrip(";")
        if base == "rm":
            flags = set()
            has_recursive_long = False
            has_force_long = False
            for t in tokens[cmd_start + 1:]:
                if t in ("&&", "||", ";", "|"):
                    break
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


try:
    inp = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = inp.get("tool_name", "")
tool_input = inp.get("tool_input", {})

# Check Bash tool commands
if tool_name == "Bash":
    cmd = tool_input.get("command", "")
    if cmd:
        for pattern in DENY_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                result = {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "cagent sandbox: blocked dangerous command matching " + pattern
                    }
                }
                json.dump(result, sys.stdout)
                sys.exit(0)
        token_reason = _check_tokens(cmd)
        if token_reason:
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "cagent sandbox: " + token_reason
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)

# Check Write/Edit tool content — block file content that contains dangerous patterns
# (defense-in-depth: prevents writing malicious scripts that could be executed later)
if tool_name == "Write":
    content = tool_input.get("content", "")
elif tool_name == "Edit":
    content = tool_input.get("new_string", "")
else:
    content = ""

if content:
    for pattern in DENY_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "cagent sandbox: blocked file content matching " + pattern
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)

sys.exit(0)
'''


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
    script_content = Template(_HOOK_SCRIPT).safe_substitute(patterns_json=patterns_json)
    hook_script_path.write_text(script_content, encoding="utf-8")

    # Use current Python executable and forward-slash path for cross-platform compat
    python_exe = sys.executable
    script_posix = hook_script_path.as_posix()

    # Build settings.local.json with hooks for Bash, Write, and Edit tools
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
                {"matcher": "Write", "hooks": [hook_entry]},
                {"matcher": "Edit", "hooks": [hook_entry]},
            ]
        }
    }

    target = claude_dir / "settings.local.json"
    target.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
