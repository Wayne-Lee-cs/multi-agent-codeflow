"""Task data model and tasks-file parsing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from cagent.compat import atomic_write


@dataclass
class Task:
    id: str
    prompt: str
    branch: str
    status: Literal["pending", "running", "done", "failed", "noop"] = "pending"
    commit_sha: str | None = None
    log_path: Path = field(default_factory=lambda: Path(os.devnull))
    depends_on: list[str] = field(default_factory=list)  # v2 hook, unused in v1
    retry_count: int = 0
    max_retries: int = 0


def parse_tasks_file(path: str | Path, run_id: str) -> list[Task]:
    """Parse a tasks file into a list of Task objects.

    Each non-empty, non-comment line becomes one task.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Tasks file not found: {path}\n"
            f"  Create a file with one task per line, e.g.:\n"
            f"    Add login form to settings page\n"
            f"    Create JWT token validation middleware"
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(
            f"Tasks file is not valid UTF-8: {path}\n"
            f"  Please save the file with UTF-8 encoding."
        )

    tasks: list[Task] = []
    task_counter = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        task_counter += 1
        task_id = f"{task_counter:03d}"
        tasks.append(
            Task(
                id=task_id,
                prompt=line,
                branch=f"cagent/{run_id}/task-{task_id}",
            )
        )

    if not tasks:
        raise ValueError(
            f"No tasks found in {path}\n"
            f"  Add one task per line (empty lines and # comments are ignored)."
        )

    return tasks


def parse_tasks_md(path: str | Path, run_id: str) -> tuple[list[Task], str]:
    """Parse a Markdown tasks file into (tasks, conventions).

    Supports the format:
        ### Task 001
        - **depends_on**: none (or 001, 002)
        - **files**: src/foo.py

        Task prompt text here...

    Conventions are read from conventions.md in the same directory,
    or from an inline ## Conventions section.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Tasks file not found: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"Tasks file is not valid UTF-8: {path}")

    # Try to load conventions from conventions.md in same directory
    conventions = ""
    conv_path = path.parent / "conventions.md"
    if conv_path.exists():
        conventions = conv_path.read_text(encoding="utf-8").strip()
    else:
        # Check for inline ## Conventions section
        conv_match = _extract_section(raw, "Conventions")
        if conv_match:
            conventions = conv_match

    # Parse task blocks
    tasks: list[Task] = []
    task_blocks = _split_task_blocks(raw)

    for block in task_blocks:
        # Extract task ID from ### Task NNN heading
        id_match = re.search(r"###\s+Task\s+(\d+)", block)
        if not id_match:
            continue
        task_id = id_match.group(1).zfill(3)

        depends_on_str = _extract_field(block, "depends_on") or "none"
        depends_on = []
        if depends_on_str.lower() != "none":
            depends_on = [d.strip() for d in depends_on_str.split(",") if d.strip()]

        # Extract files field (for prompt injection, not stored in Task)
        _files = _extract_field(block, "files") or ""

        # Remaining lines after fields = prompt
        prompt = _extract_prompt(block)
        if not prompt:
            continue

        tasks.append(
            Task(
                id=task_id,
                prompt=prompt,
                branch=f"cagent/{run_id}/task-{task_id}",
                depends_on=depends_on,
            )
        )

    if not tasks:
        raise ValueError(
            f"No tasks found in {path}\n"
            f"  Expected ### Task NNN blocks."
        )

    return tasks, conventions


def _extract_section(markdown: str, heading: str) -> str:
    """Extract content under a ## heading."""
    lines = markdown.splitlines()
    in_section = False
    result = []
    for line in lines:
        if line.strip().lower().startswith(f"## {heading.lower()}"):
            in_section = True
            continue
        if in_section:
            if line.strip().lower().startswith("## "):
                break
            result.append(line)
    return "\n".join(result).strip()


def _split_task_blocks(markdown: str) -> list[str]:
    """Split markdown into task blocks starting with ### Task."""
    blocks = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith("### Task"):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _extract_field(block: str, field_name: str) -> str | None:
    """Extract a **field**: value line from a task block."""
    pattern = rf"\*\*{re.escape(field_name)}\*\*\s*:\s*(.+)"
    match = re.search(pattern, block, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _extract_prompt(block: str) -> str:
    """Extract prompt text from a task block (lines after field declarations)."""
    lines = block.splitlines()
    prompt_lines = []
    past_fields = False
    for line in lines:
        stripped = line.strip()
        # Skip the ### Task header
        if stripped.startswith("### Task"):
            continue
        # Skip field lines like **depends_on**: ...
        if re.match(r"^\s*-\s*\*\*\w+\*\*\s*:", stripped):
            continue
        # Once we hit non-field content, collect it
        if stripped:
            past_fields = True
        if past_fields:
            prompt_lines.append(line)
    return "\n".join(prompt_lines).strip()


def _task_to_dict(t: Task) -> dict[str, Any]:
    """Convert Task to dict without dataclasses.asdict() deep copy overhead."""
    return {
        "id": t.id,
        "prompt": t.prompt,
        "branch": t.branch,
        "status": t.status,
        "commit_sha": t.commit_sha,
        "log_path": str(t.log_path),
        "depends_on": t.depends_on,
        "retry_count": t.retry_count,
        "max_retries": t.max_retries,
    }


def dump_state(run_dir: Path, tasks: list[Task]) -> None:
    """Serialize task state to tasks.json for crash recovery."""
    data = [_task_to_dict(t) for t in tasks]
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "tasks.json"
    atomic_write(target, json.dumps(data, indent=2, ensure_ascii=False))


_VALID_STATUSES = {"pending", "running", "done", "failed", "noop"}


def load_state(run_dir: Path) -> list[Task]:
    """Load task state from tasks.json."""
    target = run_dir / "tasks.json"
    if not target.exists():
        raise FileNotFoundError(f"No tasks.json in {run_dir}")
    data = json.loads(target.read_text(encoding="utf-8"))
    tasks = []
    valid_keys = {f.name for f in Task.__dataclass_fields__.values()}
    for d in data:
        # Validate required fields
        status = d.get("status", "pending")
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}' for task {d.get('id', '?')}")
        branch = d.get("branch", "")
        if not branch:
            raise ValueError(f"Missing branch for task {d.get('id', '?')}")
        if "log_path" in d:
            d["log_path"] = Path(d["log_path"])
        else:
            d["log_path"] = Path(os.devnull)
        # Filter out unknown keys (forward-compat: new fields from newer versions)
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        tasks.append(Task(**filtered))
    return tasks
