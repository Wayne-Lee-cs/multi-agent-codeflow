"""Per-run memory manager — shared context between agents within a single run."""

from __future__ import annotations

from pathlib import Path


def _validate_agent_id(agent_id: str) -> str:
    """Validate agent_id to prevent path traversal attacks."""
    if not agent_id or ".." in agent_id or "/" in agent_id or "\\" in agent_id:
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    return agent_id


class RunMemory:
    """Manages per-run, per-agent memory files.

    Directory layout:
        <run_dir>/memory/
            shared_context.md      # aggregated context for injection
            task-001.md            # worker 001 output summary
            task-002.md            # worker 002 output summary
            _integrator.md         # integrator decisions
    """

    def __init__(self, run_dir: Path):
        self._dir = run_dir / "memory"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache_key: tuple[tuple[str, ...], int] | None = None
        self._cached_context: str = ""
        self._version: int = 0  # incremented on every write/append

    def write(self, agent_id: str, content: str) -> None:
        """Write memory for a specific agent (worker or integrator)."""
        _validate_agent_id(agent_id)
        path = self._dir / f"{agent_id}.md"
        path.write_text(content, encoding="utf-8")
        self._version += 1

    def append(self, agent_id: str, content: str) -> None:
        """Append memory for a specific agent (preserves previous entries)."""
        _validate_agent_id(agent_id)
        path = self._dir / f"{agent_id}.md"
        with open(path, "a", encoding="utf-8") as f:
            if f.seek(0, 2) > 0:
                f.write("\n\n---\n\n")
            f.write(content)
        self._version += 1

    def read(self, agent_id: str) -> str:
        """Read memory for a specific agent. Returns empty string if not found."""
        _validate_agent_id(agent_id)
        path = self._dir / f"{agent_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def read_all(self) -> dict[str, str]:
        """Read all agent memories. Returns {agent_id: content}."""
        result = {}
        for path in sorted(self._dir.glob("*.md")):
            if path.name == "shared_context.md":
                continue
            agent_id = path.stem
            result[agent_id] = path.read_text(encoding="utf-8")
        return result

    def build_shared_context(self, task_ids: list[str], max_chars: int = 4000) -> str:
        """Build a shared context string from completed task memories.

        Used to inject into worker prompts so later tasks can see what
        earlier tasks accomplished. Capped at max_chars to avoid exceeding
        the model's context window.
        """
        sorted_ids = sorted(task_ids)
        ids_tuple = tuple(sorted_ids)
        cache_key = (ids_tuple, self._version)
        if cache_key == self._cache_key:
            return self._cached_context
        parts = []
        total = 0
        for tid in sorted_ids:
            content = self.read(tid)
            if content:
                entry = f"[Task {tid}]\n{content}"
                if total + len(entry) > max_chars:
                    break
                parts.append(entry)
                total += len(entry)
        result = "\n\n".join(parts)
        self._cache_key = cache_key
        self._cached_context = result
        return result

    def write_shared(self, content: str) -> None:
        """Write the aggregated shared_context.md."""
        path = self._dir / "shared_context.md"
        path.write_text(content, encoding="utf-8")

    def load_shared(self) -> str:
        """Load the aggregated shared_context.md."""
        path = self._dir / "shared_context.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
