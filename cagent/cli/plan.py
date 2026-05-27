"""Plan command — architect agent generates tasks.md + conventions.md."""

from __future__ import annotations

import argparse
import atexit
import subprocess
import sys
from pathlib import Path

from .base import _get_repo_root, _preflight_check


def _scan_dir_tree(path: Path, max_depth: int = 2, _depth: int = 0) -> str:
    """Build a text representation of directory tree (for architect prompt)."""
    if _depth >= max_depth:
        return ""
    skip = {".git", ".cagent", "__pycache__", "node_modules", ".venv", ".idea", ".vscode"}
    lines = []
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except (PermissionError, FileNotFoundError, OSError):
        return ""
    for entry in entries:
        if entry.name in skip or entry.name.startswith("."):
            continue
        if entry.is_symlink():
            continue
        indent = "  " * _depth
        if entry.is_dir():
            lines.append(f"{indent}{entry.name}/")
            sub = _scan_dir_tree(entry, max_depth, _depth + 1)
            if sub:
                lines.append(sub)
        else:
            lines.append(f"{indent}{entry.name}")
    return "\n".join(lines)


def _cmd_plan(args: argparse.Namespace) -> None:
    """Use an architect agent to generate tasks.md + conventions.md from a goal."""
    repo_root = _get_repo_root()
    _preflight_check(check_auth=True, repo_root=repo_root)

    from cagent.safety import prepare_sandbox
    from cagent.agent import _CAGENT_GITIGNORE_MARKER, _CAGENT_GITIGNORE_LINES
    prepare_sandbox(repo_root)
    sandbox_files = [
        repo_root / ".claude" / "settings.local.json",
        repo_root / ".claude" / "hooks" / "cagent-guard.py",
    ]

    gitignore_path = repo_root / ".gitignore"
    _gitignore_existed = gitignore_path.exists()
    _gitignore_original = gitignore_path.read_text(encoding="utf-8") if _gitignore_existed else ""
    if _CAGENT_GITIGNORE_MARKER not in _gitignore_original:
        prefix = "" if not _gitignore_original or _gitignore_original.endswith("\n") else "\n"
        with open(gitignore_path, "a", encoding="utf-8") as f:
            f.write(f"{prefix}{_CAGENT_GITIGNORE_MARKER}\n{_CAGENT_GITIGNORE_LINES}")

    _cleanup_done = False

    def _cleanup_sandbox() -> None:
        nonlocal _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True
        for f in sandbox_files:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
        # Remove hooks dir if now empty
        hooks_dir = repo_root / ".claude" / "hooks"
        try:
            if hooks_dir.exists() and not any(hooks_dir.iterdir()):
                hooks_dir.rmdir()
        except OSError:
            pass
        try:
            if _gitignore_existed:
                gitignore_path.write_text(_gitignore_original, encoding="utf-8")
            elif gitignore_path.exists():
                gitignore_path.unlink()
        except OSError:
            pass
        claude_dir = repo_root / ".claude"
        try:
            if claude_dir.exists() and not any(claude_dir.iterdir()):
                claude_dir.rmdir()
        except OSError:
            pass

    atexit.register(_cleanup_sandbox)

    try:
        goal = args.goal
        ref_content = ""
        if args.ref:
            ref_path = Path(args.ref)
            if not ref_path.is_file():
                ref_path = repo_root / args.ref
            if not ref_path.is_file():
                print(f"Error: reference file not found: {args.ref}", file=sys.stderr)
                sys.exit(1)
            ref_content = ref_path.read_text(encoding="utf-8", errors="replace")

        dir_tree = _scan_dir_tree(repo_root, max_depth=2)

        prompt = f"""You are a project architect. Your job is to break down a goal into multiple independent, conflict-free tasks that can run in parallel.

## Goal
{goal}
"""
        if ref_content:
            if len(ref_content) > 4000:
                print(f"Warning: reference content truncated from {len(ref_content)} to 4000 chars")
            prompt += f"""
## Reference Document
```
{ref_content[:4000]}
```
"""
        prompt += f"""
## Current Project Structure
```
{dir_tree}
```

## Requirements

1. **File boundary isolation**: Each task must ONLY create/modify its own files. No two tasks should touch the same file.
2. **Shared interfaces first**: If tasks need shared types/interfaces/contracts, put them in a separate task with `depends_on: none` that runs first.
3. **Clear dependencies**: Use `depends_on` to mark tasks that need output from other tasks.
4. **Global conventions**: Define coding standards (language, style, naming, docstrings) that all tasks must follow.

## Output

Create TWO files:

### 1. tasks.md
Format:
```markdown
# Task Plan

## Tasks

### Task 001
- **depends_on**: none
- **files**: path/to/file.py

Description of what to create/modify...

### Task 002
- **depends_on**: 001
- **files**: path/to/another.py

Description...
```

### 2. conventions.md
Format:
```markdown
# Global Conventions

## Language & Runtime
- Python 3.11+, stdlib only
- Type hints on all functions

## Code Style
- Functions: snake_case
- Classes: PascalCase
- Google-style docstrings

## Constraints
- Do NOT modify files belonging to other tasks
- Each module is self-contained
```

Create both files now. Make tasks granular enough for parallel execution but not too fine-grained (3-8 tasks is ideal).
"""

        from cagent.agent import _resolve_claude
        claude_bin = _resolve_claude()

        cmd = [claude_bin, "-p", "-", "--permission-mode", "acceptEdits"]
        if args.model:
            cmd.extend(["--model", args.model])

        print(f"Planning: {goal}")
        print(f"  Scanning project structure...")
        print(f"  Running architect agent...")
        print()

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                cwd=repo_root,
            )
        except subprocess.TimeoutExpired:
            print("Error: architect agent timed out (5 min limit).", file=sys.stderr)
            sys.exit(1)

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            print(f"Architect agent failed: {err[:500]}", file=sys.stderr)
            sys.exit(1)

        tasks_path = repo_root / "tasks.md"
        conv_path = repo_root / "conventions.md"

        if not tasks_path.exists():
            print("Error: architect agent did not create tasks.md", file=sys.stderr)
            sys.exit(1)

        try:
            from cagent.tasks import parse_tasks_md
            tasks, conventions = parse_tasks_md(tasks_path, "preview")
        except (ValueError, FileNotFoundError) as e:
            print(f"Error parsing generated tasks: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Generated {len(tasks)} tasks:")
        for t in tasks:
            deps = f" (depends on: {', '.join(t.depends_on)})" if t.depends_on else ""
            first_line = t.prompt.split("\n")[0][:72]
            print(f"  [{t.id}] {first_line}{deps}")

        if conv_path.exists():
            print(f"\nConventions: {conv_path}")
        print(f"\nNext step: cagent run tasks.md")
        if conventions:
            print(f"  Conventions will be automatically injected into each worker.")
    finally:
        atexit.unregister(_cleanup_sandbox)
        _cleanup_sandbox()
