"""Event parsing, TaskProgress tracking, and Dashboard for observability."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from cagent.compat import atomic_write


@dataclass
class Event:
    ts: float
    kind: Literal[
        "start", "tool_use", "tool_result", "text", "thinking",
        "denied", "done", "error",
    ]
    summary: str
    raw: dict = field(default_factory=dict)


@dataclass
class TaskProgress:
    task_id: str
    status: Literal["pending", "running", "done", "failed", "noop", "denied"] = "pending"
    started_at: float | None = None
    ended_at: float | None = None
    last_event: Event | None = None
    last_activity: str = ""
    tool_count: int = 0
    bytes_seen: int = 0
    commit_sha: str | None = None
    fail_reason: str | None = None


class EventParser:
    """Parse stream-json output from `claude -p --output-format stream-json`."""

    def feed(self, line: str) -> Event | None:
        """Parse a single JSON line into an Event, or None if unparseable/irrelevant."""
        line = line.strip()
        if not line:
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return Event(
                ts=time.time(),
                kind="text",
                summary=line[:80],
                raw={"raw": line},
            )

        return self._parse_event(obj)

    def _parse_event(self, obj: dict) -> Event | None:
        ts = time.time()
        typ = obj.get("type", "")

        if typ == "system":
            subtype = obj.get("subtype", "")
            if subtype == "init":
                model = obj.get("model", "unknown")
                return Event(ts=ts, kind="start", summary=f"start (model={model})", raw=obj)
            return None

        if typ == "assistant":
            return self._parse_assistant(obj, ts)

        if typ == "user":
            return self._parse_user(obj, ts)

        if typ == "result":
            subtype = obj.get("subtype", "")
            if subtype == "success":
                return Event(ts=ts, kind="done", summary="done", raw=obj)
            return Event(ts=ts, kind="error", summary=f"error: {subtype}", raw=obj)

        return None

    def _parse_assistant(self, obj: dict, ts: float) -> Event | None:
        message = obj.get("message", {})
        content = message.get("content", [])
        if not content:
            return None

        block = content[0] if isinstance(content, list) and content else {}
        block_type = block.get("type", "")

        if block_type == "tool_use":
            name = block.get("name", "unknown")
            inp = block.get("input", {})
            summary = self._summarize_tool(name, inp)
            return Event(ts=ts, kind="tool_use", summary=summary, raw=obj)

        if block_type == "text":
            text = block.get("text", "")
            return Event(ts=ts, kind="text", summary=text[:80], raw=obj)

        if block_type == "thinking":
            return Event(ts=ts, kind="thinking", summary="thinking...", raw=obj)

        return None

    def _parse_user(self, obj: dict, ts: float) -> Event | None:
        message = obj.get("message", {})
        content = message.get("content", [])
        if not content:
            return None

        block = content[0] if isinstance(content, list) and content else {}
        block_type = block.get("type", "")

        if block_type == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                result_content = str(result_content[0].get("text", ""))[:80]
            elif isinstance(result_content, str):
                result_content = result_content[:80]
            else:
                result_content = str(result_content)[:80]

            # Check if this is a denial
            is_error = block.get("is_error", False)
            if is_error and ("denied" in result_content.lower() or "not allowed" in result_content.lower()):
                return Event(ts=ts, kind="denied", summary=f"denied: {result_content}", raw=obj)

            return Event(ts=ts, kind="tool_result", summary=result_content, raw=obj)

        return None

    @staticmethod
    def _summarize_tool(name: str, inp: dict) -> str:
        if name == "Edit":
            return f"Edit {inp.get('file_path', '?')}"
        if name == "Read":
            return f"Read {inp.get('file_path', '?')}"
        if name == "Write":
            return f"Write {inp.get('file_path', '?')}"
        if name == "Bash":
            cmd = inp.get("command", "")
            return f"Bash: {cmd[:60]}"
        if name == "Glob":
            return f"Glob {inp.get('pattern', '?')}"
        if name == "Grep":
            return f"Grep {inp.get('pattern', '?')}"
        return name


class Dashboard:
    """Tracks progress of all tasks, persists to dashboard.json."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.tasks: dict[str, TaskProgress] = {}
        self._progress_dir = run_dir / "progress"
        self._events_dir = run_dir / "events"
        self._progress_dir.mkdir(parents=True, exist_ok=True)
        self._events_dir.mkdir(parents=True, exist_ok=True)

    def update(self, task_id: str, event: Event) -> None:
        """Update task progress with a new event and persist."""
        if task_id not in self.tasks:
            self.tasks[task_id] = TaskProgress(task_id=task_id)

        tp = self.tasks[task_id]
        tp.last_event = event
        tp.bytes_seen += len(json.dumps(event.raw))

        if event.kind == "start" and tp.status == "pending":
            tp.status = "running"
            tp.started_at = event.ts

        if event.kind == "tool_use":
            tp.tool_count += 1
            tp.last_activity = event.summary

        if event.kind == "text":
            tp.last_activity = event.summary

        if event.kind == "denied":
            tp.last_activity = f"DENIED: {event.summary}"

        if event.kind == "done":
            tp.status = "done"
            tp.ended_at = event.ts

        if event.kind == "error":
            tp.status = "failed"
            tp.ended_at = event.ts
            tp.fail_reason = event.summary

        # Persist per-task progress
        self._write_task_progress(tp)
        # Append to events.jsonl
        self._append_event(task_id, event)
        # Update dashboard
        self._write_dashboard()

    def set_task_status(self, task_id: str, status: str, **kwargs) -> None:
        """Directly set task status (used by dispatcher for noop/failed)."""
        if task_id not in self.tasks:
            self.tasks[task_id] = TaskProgress(task_id=task_id)
        tp = self.tasks[task_id]
        tp.status = status  # type: ignore
        for k, v in kwargs.items():
            setattr(tp, k, v)
        self._write_task_progress(tp)
        self._write_dashboard()

    def get_snapshot(self) -> dict:
        """Return a serializable snapshot of all task progress."""
        result = {}
        for tid, tp in self.tasks.items():
            d = asdict(tp)
            if d["last_event"]:
                d["last_event"] = asdict(d["last_event"])
            result[tid] = d
        return result

    def _write_task_progress(self, tp: TaskProgress) -> None:
        d = asdict(tp)
        if d["last_event"]:
            d["last_event"] = asdict(d["last_event"])
        target = self._progress_dir / f"task-{tp.task_id}.json"
        atomic_write(target, json.dumps(d, indent=2, ensure_ascii=False))

    def _append_event(self, task_id: str, event: Event) -> None:
        target = self._events_dir / f"task-{task_id}.jsonl"
        line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)

    def _write_dashboard(self) -> None:
        target = self.run_dir / "dashboard.json"
        atomic_write(
            target,
            json.dumps(self.get_snapshot(), indent=2, ensure_ascii=False),
        )
