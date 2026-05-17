"""Safety sandbox — inject PreToolUse hooks to deny dangerous commands in worktrees.

Known limitation: indirect execution via compiled binaries or non-Bash
interpreters (e.g., a Go program calling exec("git push")) can bypass
the regex check. This is acceptable for v1 since claude -p in acceptEdits
mode does not intentionally circumvent hooks, and worktrees lack push
credentials. v2 may explore stronger sandboxing (seccomp/namespaces/Docker).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    r"python[3]?\s*-c.*subprocess",
    r"\|\s*(ba)?sh\b",
]

# Generate the hook script content — a Python script that reads stdin JSON,
# extracts the Bash command, and checks all deny patterns.
_HOOK_SCRIPT = '''\
import json, re, sys

DENY_PATTERNS = {patterns_json}

try:
    inp = json.load(sys.stdin)
except Exception:
    sys.exit(0)

cmd = inp.get("tool_input", {{}}).get("command", "")
if not cmd:
    sys.exit(0)

for pattern in DENY_PATTERNS:
    if re.search(pattern, cmd, re.IGNORECASE):
        result = {{
            "hookSpecificOutput": {{
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "cagent sandbox: blocked dangerous command matching " + pattern
            }}
        }}
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
    script_content = _HOOK_SCRIPT.format(patterns_json=patterns_json)
    hook_script_path.write_text(script_content, encoding="utf-8")

    # Use current Python executable and forward-slash path for cross-platform compat
    python_exe = sys.executable
    script_posix = hook_script_path.as_posix()

    # Build settings.local.json with command-based hook
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"\"{python_exe}\" \"{script_posix}\"",
                            "timeout": 10,
                            "statusMessage": "cagent safety check...",
                        }
                    ],
                }
            ]
        }
    }

    target = claude_dir / "settings.local.json"
    target.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")

    # Exclude .claude/ from git commits (sandbox files should not be committed)
    gitignore = worktree_path / ".gitignore"
    entry = ".claude/\n"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".claude/" not in content:
            gitignore.write_text(content + entry, encoding="utf-8")
    else:
        gitignore.write_text(entry, encoding="utf-8")
