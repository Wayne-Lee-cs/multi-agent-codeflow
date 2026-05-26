"""Per-run memory manager — shared context between agents within a single run."""

from __future__ import annotations

from pathlib import Path

__all__ = ["RunMemory"]


def _validate_agent_id(agent_id: str) -> str:
    """Validate agent_id to prevent path traversal attacks."""
    import re
    if not agent_id or ".." in agent_id or "/" in agent_id or "\\" in agent_id:
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    if "\x00" in agent_id:
        raise ValueError(f"Invalid agent_id (null byte): {agent_id!r}")
    if not re.match(r"^[a-zA-Z0-9_-]+$", agent_id):
        raise ValueError(f"Invalid agent_id (must be alphanumeric/dash/underscore): {agent_id!r}")
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
        try:
            path.write_text(content, encoding="utf-8")
        except OSError:
            return
        self._version += 1

    def append(self, agent_id: str, content: str) -> None:
        """Append memory for a specific agent (preserves previous entries)."""
        _validate_agent_id(agent_id)
        path = self._dir / f"{agent_id}.md"
        try:
            with open(path, "a", encoding="utf-8") as f:
                if f.seek(0, 2) > 0:
                    f.write("\n\n---\n\n")
                f.write(content)
        except OSError:
            return
        self._version += 1

    def read(self, agent_id: str) -> str:
        """Read memory for a specific agent. Returns empty string if not found."""
        _validate_agent_id(agent_id)
        path = self._dir / f"{agent_id}.md"
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except OSError:
            pass
        return ""

    def read_all(self) -> dict[str, str]:
        """Read all agent memories. Returns {agent_id: content}."""
        result = {}
        for path in sorted(self._dir.glob("*.md")):
            if path.name == "shared_context.md":
                continue
            agent_id = path.stem
            try:
                result[agent_id] = path.read_text(encoding="utf-8")
            except OSError:
                continue
        return result

    def build_shared_context(self, task_ids: list[str], max_chars: int = 4000) -> str:
        """Build a shared context string from completed task memories.

        Used to inject into worker prompts so later tasks can see what
        earlier tasks accomplished. Capped at max_chars to avoid exceeding
        the model's context window. Truncates individual entries to fit
        all tasks rather than dropping later tasks entirely.
        """
        sorted_ids = sorted(task_ids)
        ids_tuple = tuple(sorted_ids)
        cache_key = (ids_tuple, self._version)
        if cache_key == self._cache_key:
            return self._cached_context
        raw_entries: list[tuple[str, str]] = []
        for tid in sorted_ids:
            content = self.read(tid)
            if content:
                raw_entries.append((tid, content))
        if not raw_entries:
            self._cache_key = cache_key
            self._cached_context = ""
            return ""
        n = len(raw_entries)
        headers = [f"[Task {tid}]\n" for tid, _ in raw_entries]
        join_overhead = 2 * (n - 1)  # "\n\n" only between entries
        total_header = sum(len(h) for h in headers)
        total_raw = total_header + join_overhead + sum(len(c) for _, c in raw_entries)
        parts = []
        if total_raw <= max_chars:
            for (tid, content), hdr in zip(raw_entries, headers):
                parts.append(f"{hdr}{content}")
        else:
            available = max_chars - total_header - join_overhead
            per_entry = max(50, available // n)
            for (tid, content), hdr in zip(raw_entries, headers):
                parts.append(f"{hdr}{content[:per_entry]}")
        result = "\n\n".join(parts)
        if len(result) > max_chars:
            result = result[:max_chars]
        self._cache_key = cache_key
        self._cached_context = result
        return result

    def write_shared(self, content: str) -> None:
        """Write the aggregated shared_context.md."""
        path = self._dir / "shared_context.md"
        try:
            path.write_text(content, encoding="utf-8")
        except OSError:
            return

    def load_shared(self) -> str:
        """Load the aggregated shared_context.md."""
        path = self._dir / "shared_context.md"
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except OSError:
            pass
        return ""
