"""Task data model and tasks-file parsing."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

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


def dump_state(run_dir: Path, tasks: list[Task]) -> None:
    """Serialize task state to tasks.json for crash recovery."""
    data = []
    for t in tasks:
        d = asdict(t)
        d["log_path"] = str(t.log_path)
        data.append(d)
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
