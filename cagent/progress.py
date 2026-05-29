"""Event parsing, TaskProgress tracking, and Dashboard for observability."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from cagent.compat import atomic_write

_log = logging.getLogger(__name__)


_VALID_EVENT_KINDS = frozenset({
    "start", "tool_use", "tool_result", "text", "thinking",
    "denied", "done", "error",
})


@dataclass(slots=True)
class Event:
    ts: float
    kind: Literal[
        "start", "tool_use", "tool_result", "text", "thinking",
        "denied", "done", "error",
    ]
    summary: str
    raw: dict[str, Any] = field(default_factory=dict)
    raw_line_len: int = 0
    usage: dict[str, Any] | None = None


@dataclass(slots=True)
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

        try:
            events = self._parse_event(obj)
        except Exception:
            # A malformed-but-valid-JSON event (unexpected shapes/types) must
            # never crash the streaming loop in run_agent and fail the whole
            # task. Degrade to a raw text event instead.
            _log.warning("Failed to parse stream event, degrading to raw text", exc_info=True)
            return [Event(
                ts=time.time(),
                kind="text",
                summary=line[:80],
                raw={"raw": line},
                raw_line_len=line_len,
            )]
        for ev in events:
            ev.raw_line_len = line_len
        return events

    def _parse_event(self, obj: dict[str, Any]) -> list[Event]:
        ts = time.time()
        typ = obj.get("type", "")

        if typ == "system":
            subtype = obj.get("subtype", "")
            if subtype == "init":
                model = obj.get("model", "unknown")
                return [Event(ts=ts, kind="start", summary=f"start (model={model})")]
            return []

        if typ == "assistant":
            return self._parse_assistant(obj, ts)

        if typ == "user":
            return self._parse_user(obj, ts)

        if typ == "result":
            subtype = obj.get("subtype", "")
            usage = obj.get("usage")
            if subtype == "success":
                return [Event(ts=ts, kind="done", summary="done", usage=usage)]
            return [Event(ts=ts, kind="error", summary=f"error: {subtype}", usage=usage)]

        return []

    def _parse_assistant(self, obj: dict[str, Any], ts: float) -> list[Event]:
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
                events.append(Event(ts=ts, kind="tool_use", summary=self._summarize_tool(name, inp)))
            elif block_type == "text":
                events.append(Event(ts=ts, kind="text", summary=block.get("text", "")[:500]))
            elif block_type == "thinking":
                events.append(Event(ts=ts, kind="thinking", summary="thinking..."))
        return events

    def _parse_user(self, obj: dict[str, Any], ts: float) -> list[Event]:
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
                    if result_content:
                        first = result_content[0]
                        # List items are normally {"type": "text", "text": ...},
                        # but tolerate plain strings / other shapes defensively.
                        if isinstance(first, dict):
                            result_content = str(first.get("text", ""))[:80]
                        else:
                            result_content = str(first)[:80]
                    else:
                        result_content = ""
                elif isinstance(result_content, str):
                    result_content = result_content[:80]
                else:
                    result_content = str(result_content)[:80]

                is_error = block.get("is_error", False)
                if is_error and ("denied" in result_content.lower() or "not allowed" in result_content.lower()):
                    events.append(Event(ts=ts, kind="denied", summary=f"denied: {result_content}"))
                else:
                    events.append(Event(ts=ts, kind="tool_result", summary=result_content))
        return events

    @staticmethod
    def _summarize_tool(name: str, inp: dict[str, Any]) -> str:
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


def _task_progress_dict(tp: TaskProgress) -> dict[str, Any]:
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


def _truncate_jsonl_if_large(path: Path, max_bytes: int, keep_ratio: float) -> None:
    """Truncate a JSONL file from the beginning if it exceeds max_bytes.

    For large files (>1MB), uses streaming seek to avoid full read.
    For smaller files, reads lines directly (guarantees at least 1 line kept).
    """
    try:
        size = path.stat().st_size
    except OSError as e:
        _log.warning("Failed to stat JSONL file %s: %s", path, e)
        return
    if size <= max_bytes:
        return

    _STREAMING_THRESHOLD = 1024 * 1024  # 1MB
    try:
        if size > _STREAMING_THRESHOLD:
            keep_bytes = int(size * keep_ratio)
            with open(path, "rb") as f:
                f.seek(-keep_bytes, 2)
                tail = f.read()
            nl_idx = tail.find(b"\n")
            if nl_idx >= 0:
                tail = tail[nl_idx + 1:]
            if tail:
                path.write_bytes(tail)
        else:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            keep_count = max(1, int(len(lines) * keep_ratio))
            path.write_text("".join(lines[-keep_count:]), encoding="utf-8")
    except OSError as e:
        _log.warning("Failed to truncate JSONL file %s: %s", path, e)


def _validate_task_id(task_id: str) -> str:
    """Validate task_id to prevent path traversal and illegal filename chars."""
    if not task_id or not re.match(r'^[a-zA-Z0-9_-]+$', task_id):
        raise ValueError(f"Invalid task_id: {task_id!r}")
    return task_id


class Dashboard:
    """Tracks progress of all tasks, persists to dashboard.json."""

    _DASHBOARD_THROTTLE = 1.0  # seconds between dashboard.json writes
    _IO_THROTTLE = 0.5  # seconds between per-task file writes
    _VALID_STATUSES = frozenset({"pending", "running", "done", "failed", "noop"})

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
        self._dashboard_dirty_tasks: set[str] = set()
        self._last_io_flush: float = 0.0
        self._io_queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] | None = None
        self._io_task: asyncio.Task[None] | None = None
        self._io_lock = threading.Lock()
        self._last_dashboard_snapshot: dict[str, dict[str, Any]] = {}  # task_id -> last serialized dict

        # Load existing dashboard data if present (for resume support)
        dashboard_path = run_dir / "dashboard.json"
        if dashboard_path.exists():
            try:
                data = json.loads(dashboard_path.read_text(encoding="utf-8"))
                for tid, tp_dict in data.items():
                    try:
                        _validate_task_id(tid)
                    except ValueError:
                        continue
                    tp = TaskProgress(task_id=tid)
                    for k, v in tp_dict.items():
                        if k == "task_id":
                            continue
                        if k == "last_event" and v is not None:
                            event_kind = v.get("kind", "text")
                            if event_kind not in _VALID_EVENT_KINDS:
                                event_kind = "text"
                            tp.last_event = Event(
                                ts=v.get("ts", 0.0),
                                kind=event_kind,
                                summary=v.get("summary", ""),
                                raw=v.get("raw", {}),
                            )
                        elif k == "status":
                            if v in self._VALID_STATUSES:
                                tp.status = v
                        elif k in TaskProgress.__dataclass_fields__:
                            field_type = TaskProgress.__dataclass_fields__[k].type
                            if field_type in ("int", int) and not isinstance(v, int):
                                continue
                            if field_type in ("float | None", "float") and v is not None and not isinstance(v, (int, float)):
                                continue
                            if field_type in ("str", str) and not isinstance(v, str):
                                continue
                            setattr(tp, k, v)
                    self.tasks[tid] = tp
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # Start fresh if data is corrupt

    def start_async_io(self) -> None:
        """Start the background I/O task. Call from async context."""
        if self._io_task is not None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError("start_async_io() must be called from an async context")
        self._io_queue = asyncio.Queue()
        self._io_task = asyncio.create_task(self._io_worker())

    async def stop_async_io(self) -> None:
        """Stop the background I/O task. Call from async context.

        Prerequisite: call flush_async() first to ensure all buffered data
        is enqueued before stopping the worker.
        """
        if self._io_task is None:
            return
        # Flush any remaining buffered data before stopping
        self.flush()
        # Signal worker to stop
        if self._io_queue:
            await self._io_queue.put(None)
        try:
            await self._io_task
        except asyncio.CancelledError:
            pass
        self._io_task = None
        self._io_queue = None

    async def _io_worker(self) -> None:
        """Background worker that processes I/O requests from the queue."""
        queue = self._io_queue
        assert queue is not None  # guaranteed by start_async_io()
        while True:
            item = await queue.get()
            if item is None:
                break
            op_type, data = item
            try:
                if op_type == "flush":
                    await asyncio.to_thread(self._do_flush_io, data)
                elif op_type == "dashboard":
                    await asyncio.to_thread(self._do_write_dashboard, data)
                elif op_type == "done":
                    # Signal that all prior work is complete
                    event = data.get("event")
                    if event:
                        event.set()
            except Exception:
                _log.exception("I/O worker error during %s", op_type)
            finally:
                queue.task_done()

    def set_event_handler(self, handler: Callable[[str, Event], None] | None) -> None:
        """Set or clear the event handler callback (used by LinePrinter)."""
        self._on_event = handler

    def update(self, task_id: str, event: Event) -> None:
        """Update task progress with a new event and persist."""
        _validate_task_id(task_id)
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
        self._dashboard_dirty_tasks.add(task_id)
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
        _validate_task_id(task_id)
        if status not in self._VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}")
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
        self._dashboard_dirty_tasks.add(task_id)
        is_final = status in ("done", "failed", "noop")
        if is_final:
            self._flush_io()
        else:
            self._maybe_flush_io()
        self._write_dashboard(force=is_final)

    def get_snapshot(self) -> dict[str, dict[str, Any]]:
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
        line = json.dumps(d, ensure_ascii=False, separators=(',', ':')) + "\n"
        with self._io_lock:
            if task_id not in self._event_buffers:
                self._event_buffers[task_id] = []
            self._event_buffers[task_id].append(line)

    def _maybe_flush_io(self) -> None:
        """Flush buffered I/O if throttle interval has elapsed."""
        with self._io_lock:
            now = time.time()
            if (now - self._last_io_flush) < self._IO_THROTTLE:
                return
        self._flush_io()

    def _flush_io(self) -> None:
        """Write all buffered events and dirty progress to disk."""
        # Atomic swap under lock to avoid losing events buffered during write
        with self._io_lock:
            self._last_io_flush = time.time()
            buffers, self._event_buffers = self._event_buffers, {}
            dirty, self._dirty_progress = self._dirty_progress, set()

        # Snapshot progress in the event loop thread (safe — no concurrent mutation)
        progress_snap = {}
        for task_id in dirty:
            if task_id in self.tasks:
                progress_snap[task_id] = _task_progress_dict(self.tasks[task_id])

        if self._io_queue is not None:
            # Use async I/O queue
            self._io_queue.put_nowait(("flush", {"buffers": buffers, "progress": progress_snap}))
        else:
            # Fallback to synchronous I/O
            self._do_flush_io({"buffers": buffers, "progress": progress_snap})

    _MAX_EVENT_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    _TRUNCATE_KEEP_RATIO = 0.8  # keep last 80% of lines when truncating

    def _do_flush_io(self, data: dict[str, Any]) -> None:
        """Actually write buffered events and progress to disk (runs in thread)."""
        buffers = data["buffers"]
        progress_snap = data["progress"]
        for task_id, lines in buffers.items():
            if lines:
                target = self._events_dir / f"task-{task_id}.jsonl"
                with open(target, "a", encoding="utf-8") as f:
                    f.writelines(lines)
                _truncate_jsonl_if_large(target, self._MAX_EVENT_FILE_SIZE, self._TRUNCATE_KEEP_RATIO)
        for task_id, d in progress_snap.items():
            target = self._progress_dir / f"task-{task_id}.json"
            atomic_write(target, json.dumps(d, ensure_ascii=False, separators=(',', ':')))

    def _write_dashboard(self, force: bool = False) -> None:
        """Write dashboard.json with time-based throttling (incremental).

        Only serializes dirty tasks (O(dirty) not O(all)).
        On force=True with no dirty tasks, falls back to full serialization.
        """
        now = time.time()
        if not force and (now - self._last_dashboard_write) < self._DASHBOARD_THROTTLE:
            self._dashboard_dirty = True
            return
        self._dashboard_dirty = False
        self._last_dashboard_write = now

        dirty, self._dashboard_dirty_tasks = self._dashboard_dirty_tasks, set()

        # On force with no dirty tasks, serialize all (e.g. after flush clears dirty set)
        if force and not dirty and self.tasks:
            dirty = set(self.tasks.keys())

        diff: dict[str, dict[str, Any]] = {}
        for tid in dirty:
            if tid in self.tasks:
                tp_dict = _task_progress_dict(self.tasks[tid])
                self._last_dashboard_snapshot[tid] = tp_dict
                diff[tid] = tp_dict

        full_snapshot = self._last_dashboard_snapshot

        if self._io_queue is not None:
            self._io_queue.put_nowait(("dashboard", {"diff": diff, "full": full_snapshot}))
        else:
            self._do_write_dashboard({"diff": diff, "full": full_snapshot})

    def _do_write_dashboard(self, data: dict[str, Any]) -> None:
        """Actually write dashboard.json (runs in thread, full snapshot)."""
        target = self.run_dir / "dashboard.json"
        diff = data.get("diff", {})
        full_snapshot = data.get("full")
        if not diff and full_snapshot is None:
            return
        # Use full snapshot to ensure deleted tasks are removed
        if full_snapshot is None:
            # Fallback: build from diff + existing (legacy path)
            full_snapshot = {}
            if target.exists():
                try:
                    full_snapshot = json.loads(target.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    _log.warning("Corrupt JSON in %s, starting fresh", target)
                except OSError as e:
                    _log.warning("Failed to read snapshot %s: %s", target, e)
            full_snapshot.update(diff)
        atomic_write(
            target,
            json.dumps(full_snapshot, ensure_ascii=False, separators=(',', ':')),
        )

    def flush(self) -> None:
        """Force-write all buffered data. Call when run completes."""
        self._flush_io()
        if self._dashboard_dirty:
            self._write_dashboard(force=True)

    async def flush_async(self) -> None:
        """Force-write all buffered data and wait for I/O to complete."""
        self._flush_io()
        if self._dashboard_dirty:
            self._write_dashboard(force=True)
        # Wait for queue to drain by sending a sentinel and waiting for it
        if self._io_queue is not None:
            done_event = asyncio.Event()
            await self._io_queue.put(("done", {"event": done_event}))
            try:
                await done_event.wait()
            except asyncio.CancelledError:
                # If cancelled (e.g. KeyboardInterrupt), force synchronous
                # flush without modifying _io_queue to avoid inconsistent state
                self.flush()
