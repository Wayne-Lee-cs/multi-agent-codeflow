"""Event parsing, TaskProgress tracking, and Dashboard for observability."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

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
    raw_line_len: int = 0  # character length of stripped JSON line (avoids re-serialization)
    usage: dict | None = None  # token usage from result events


@dataclass
class TaskProgress:
    task_id: str
    status: Literal["pending", "running", "done", "failed", "noop"] = "pending"
    started_at: float | None = None
    ended_at: float | None = None
    last_event: Event | None = None
    last_activity: str = ""
    tool_count: int = 0
    bytes_seen: int = 0
    commit_sha: str | None = None
    fail_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0


class EventParser:
    """Parse stream-json output from `claude -p --output-format stream-json`."""

    def feed(self, line: str) -> list[Event]:
        """Parse a single JSON line into zero or more Events."""
        line = line.strip()
        if not line:
            return []
        if not line.startswith('{'):
            return [Event(
                ts=time.time(),
                kind="text",
                summary=line[:80],
                raw={"raw": line},
                raw_line_len=len(line),
            )]
        line_len = len(line)
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return [Event(
                ts=time.time(),
                kind="text",
                summary=line[:80],
                raw={"raw": line},
                raw_line_len=line_len,
            )]

        events = self._parse_event(obj)
        for ev in events:
            ev.raw_line_len = line_len
        return events

    def _parse_event(self, obj: dict) -> list[Event]:
        ts = time.time()
        typ = obj.get("type", "")

        if typ == "system":
            subtype = obj.get("subtype", "")
            if subtype == "init":
                model = obj.get("model", "unknown")
                return [Event(ts=ts, kind="start", summary=f"start (model={model})", raw=obj)]
            return []

        if typ == "assistant":
            return self._parse_assistant(obj, ts)

        if typ == "user":
            return self._parse_user(obj, ts)

        if typ == "result":
            subtype = obj.get("subtype", "")
            usage = obj.get("usage")
            if subtype == "success":
                return [Event(ts=ts, kind="done", summary="done", raw=obj, usage=usage)]
            return [Event(ts=ts, kind="error", summary=f"error: {subtype}", raw=obj, usage=usage)]

        return []

    def _parse_assistant(self, obj: dict, ts: float) -> list[Event]:
        message = obj.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list) or not content:
            return []

        events = []
        for block in content:
            block_type = block.get("type", "")
            if block_type == "tool_use":
                name = block.get("name", "unknown")
                inp = block.get("input", {})
                events.append(Event(ts=ts, kind="tool_use", summary=self._summarize_tool(name, inp), raw=obj))
            elif block_type == "text":
                events.append(Event(ts=ts, kind="text", summary=block.get("text", "")[:500], raw=obj))
            elif block_type == "thinking":
                events.append(Event(ts=ts, kind="thinking", summary="thinking...", raw=obj))
        return events

    def _parse_user(self, obj: dict, ts: float) -> list[Event]:
        message = obj.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list) or not content:
            return []

        events = []
        for block in content:
            block_type = block.get("type", "")
            if block_type == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = str(result_content[0].get("text", ""))[:80] if result_content else ""
                elif isinstance(result_content, str):
                    result_content = result_content[:80]
                else:
                    result_content = str(result_content)[:80]

                is_error = block.get("is_error", False)
                if is_error and ("denied" in result_content.lower() or "not allowed" in result_content.lower()):
                    events.append(Event(ts=ts, kind="denied", summary=f"denied: {result_content}", raw=obj))
                else:
                    events.append(Event(ts=ts, kind="tool_result", summary=result_content, raw=obj))
        return events

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


def _task_progress_dict(tp: TaskProgress) -> dict:
    """Build a serializable dict from TaskProgress, excluding large raw data."""
    last_ev = tp.last_event
    return {
        "task_id": tp.task_id,
        "status": tp.status,
        "started_at": tp.started_at,
        "ended_at": tp.ended_at,
        "last_event": {
            "ts": last_ev.ts if last_ev else None,
            "kind": last_ev.kind if last_ev else None,
            "summary": last_ev.summary if last_ev else None,
            "raw": {"summary": last_ev.summary[:200] if last_ev and last_ev.summary else ""},
            "raw_line_len": last_ev.raw_line_len if last_ev else None,
            "usage": last_ev.usage if last_ev else None,
        } if last_ev else None,
        "last_activity": tp.last_activity,
        "tool_count": tp.tool_count,
        "bytes_seen": tp.bytes_seen,
        "commit_sha": tp.commit_sha,
        "fail_reason": tp.fail_reason,
        "tokens_in": tp.tokens_in,
        "tokens_out": tp.tokens_out,
    }


class Dashboard:
    """Tracks progress of all tasks, persists to dashboard.json."""

    _DASHBOARD_THROTTLE = 1.0  # seconds between dashboard.json writes
    _IO_THROTTLE = 0.5  # seconds between per-task file writes

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.tasks: dict[str, TaskProgress] = {}
        self._progress_dir = run_dir / "progress"
        self._events_dir = run_dir / "events"
        self._progress_dir.mkdir(parents=True, exist_ok=True)
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._on_event: Callable[[str, Event], None] | None = None
        self._last_dashboard_write: float = 0.0
        self._dashboard_dirty: bool = False
        self._event_buffers: dict[str, list[str]] = {}
        self._dirty_progress: set[str] = set()
        self._last_io_flush: float = 0.0

        # Load existing dashboard data if present (for resume support)
        dashboard_path = run_dir / "dashboard.json"
        if dashboard_path.exists():
            try:
                data = json.loads(dashboard_path.read_text(encoding="utf-8"))
                for tid, tp_dict in data.items():
                    tp = TaskProgress(task_id=tid)
                    for k, v in tp_dict.items():
                        if k == "last_event" and v is not None:
                            # Defensive rebuild: handle missing fields gracefully
                            tp.last_event = Event(
                                ts=v.get("ts", 0.0),
                                kind=v.get("kind", "text"),
                                summary=v.get("summary", ""),
                                raw=v.get("raw", {}),
                            )
                        elif k in TaskProgress.__dataclass_fields__:
                            setattr(tp, k, v)
                    self.tasks[tid] = tp
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # Start fresh if data is corrupt

    def set_event_handler(self, handler: Callable[[str, Event], None] | None) -> None:
        """Set or clear the event handler callback (used by LinePrinter)."""
        self._on_event = handler

    def update(self, task_id: str, event: Event) -> None:
        """Update task progress with a new event and persist."""
        if task_id not in self.tasks:
            self.tasks[task_id] = TaskProgress(task_id=task_id)

        tp = self.tasks[task_id]
        tp.last_event = event
        if event.raw_line_len:
            tp.bytes_seen += event.raw_line_len
        elif event.raw:
            tp.bytes_seen += len(json.dumps(event.raw))

        if event.kind == "start" and tp.status == "pending":
            tp.status = "running"
            tp.started_at = event.ts

        if event.kind == "tool_use":
            tp.tool_count += 1
            tp.last_activity = event.summary

        if event.kind == "tool_result":
            tp.last_activity = event.summary

        if event.kind == "text":
            tp.last_activity = event.summary

        if event.kind == "denied":
            tp.last_activity = f"DENIED: {event.summary}"

        if event.kind == "done":
            # Don't set tp.status here — set_task_status() is the sole
            # authority for final status transitions. The stream "done" event
            # fires before the commit step, so setting status here would be
            # premature. Only collect token usage.
            if event.usage:
                tp.tokens_in += event.usage.get("input_tokens", 0)
                tp.tokens_out += event.usage.get("output_tokens", 0)

        if event.kind == "error":
            # Don't set tp.status here — set_task_status() handles it after
            # the full agent lifecycle completes.
            tp.last_activity = f"error: {event.summary[:60]}"

        # Buffer per-task progress and event for periodic flush
        self._dirty_progress.add(task_id)
        self._buffer_event(task_id, event)
        # Notify event handler (LinePrinter)
        if self._on_event:
            self._on_event(task_id, event)
        # Periodic I/O flush
        self._maybe_flush_io()
        # Update dashboard
        self._write_dashboard()

    def set_task_status(self, task_id: str, status: str, **kwargs) -> None:
        """Directly set task status (used by dispatcher for noop/failed)."""
        if task_id not in self.tasks:
            self.tasks[task_id] = TaskProgress(task_id=task_id)
        tp = self.tasks[task_id]
        tp.status = status  # type: ignore
        if status in ("done", "noop") and "fail_reason" not in kwargs:
            tp.fail_reason = None
        if status in ("done", "failed", "noop") and tp.ended_at is None:
            tp.ended_at = time.time()
        for k, v in kwargs.items():
            if k not in TaskProgress.__dataclass_fields__:
                raise ValueError(f"Unknown TaskProgress field: {k!r}")
            setattr(tp, k, v)

        # Create a synthetic event for persistence + printer notification
        if status == "done":
            sha = kwargs.get("commit_sha", "")
            summary = f"commit {sha[:7]}" if sha else "done"
            event = Event(ts=time.time(), kind="done", summary=summary, raw={})
        elif status == "failed":
            reason = kwargs.get("fail_reason", "")
            event = Event(ts=time.time(), kind="error", summary=reason, raw={})
        elif status == "noop":
            event = Event(ts=time.time(), kind="done", summary="no changes", raw={})
        else:
            event = None

        if event is not None:
            self._buffer_event(task_id, event)
            if self._on_event:
                self._on_event(task_id, event)

        self._dirty_progress.add(task_id)
        is_final = status in ("done", "failed", "noop")
        if is_final:
            self._flush_io()
        else:
            self._maybe_flush_io()
        self._write_dashboard(force=is_final)

    def get_snapshot(self) -> dict:
        """Return a serializable snapshot of all task progress.

        Manual dict construction avoids asdict() recursively serializing
        last_event.raw (which can be large), keeping I/O lightweight.
        """
        return {tid: _task_progress_dict(tp) for tid, tp in self.tasks.items()}

    def _buffer_event(self, task_id: str, event: Event) -> None:
        """Buffer an event line in memory for batch writing."""
        d = {
            "ts": event.ts,
            "kind": event.kind,
            "summary": event.summary,
            "raw_line_len": event.raw_line_len,
            "usage": event.usage,
        }
        line = json.dumps(d, ensure_ascii=False) + "\n"
        if task_id not in self._event_buffers:
            self._event_buffers[task_id] = []
        self._event_buffers[task_id].append(line)

    def _maybe_flush_io(self) -> None:
        """Flush buffered I/O if throttle interval has elapsed."""
        now = time.time()
        if (now - self._last_io_flush) >= self._IO_THROTTLE:
            self._flush_io()

    def _flush_io(self) -> None:
        """Write all buffered events and dirty progress to disk."""
        self._last_io_flush = time.time()
        # Atomic swap to avoid losing events buffered during write
        buffers, self._event_buffers = self._event_buffers, {}
        dirty, self._dirty_progress = self._dirty_progress, set()
        for task_id, lines in buffers.items():
            if lines:
                target = self._events_dir / f"task-{task_id}.jsonl"
                with open(target, "a", encoding="utf-8") as f:
                    f.writelines(lines)
        for task_id in dirty:
            if task_id in self.tasks:
                tp = self.tasks[task_id]
                d = _task_progress_dict(tp)
                target = self._progress_dir / f"task-{tp.task_id}.json"
                atomic_write(target, json.dumps(d, indent=2, ensure_ascii=False))

    def _write_dashboard(self, force: bool = False) -> None:
        """Write dashboard.json with time-based throttling."""
        now = time.time()
        if not force and (now - self._last_dashboard_write) < self._DASHBOARD_THROTTLE:
            self._dashboard_dirty = True
            return
        self._dashboard_dirty = False
        self._last_dashboard_write = now
        target = self.run_dir / "dashboard.json"
        atomic_write(
            target,
            json.dumps(self.get_snapshot(), indent=2, ensure_ascii=False),
        )

    def flush(self) -> None:
        """Force-write all buffered data. Call when run completes."""
        self._flush_io()
        if self._dashboard_dirty:
            self._write_dashboard(force=True)
