"""Console progress printing — subscribes to dashboard events and prints lines."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Literal

from cagent.progress import Dashboard, Event


class LinePrinter:
    """Prints real-time progress lines to stdout as tasks execute."""

    def __init__(self, dashboard: Dashboard, quiet: bool = False):
        self.dashboard = dashboard
        self.quiet = quiet
        self._queue: asyncio.Queue[tuple[str, Event]] = asyncio.Queue()

    def push(self, task_id: str, event: Event) -> None:
        """Called by dashboard to push an event for printing.

        Note: asyncio.Queue.put_nowait() is not thread-safe, but push() is
        only called from the event-loop thread (via dashboard.update()), so
        this is safe. Do NOT call from a different thread.
        """
        self._queue.put_nowait((task_id, event))

    async def run(self) -> None:
        """Consume events and print to stdout. Run as a background task.

        Blocks on the queue until an event arrives or the task is cancelled.
        We deliberately use a plain ``await queue.get()`` rather than a
        ``wait_for(..., timeout=0.5)`` polling loop: there is no periodic work
        to do, so polling only wasted wakeups — and the pending 0.5s timer it
        left behind could keep the Windows ProactorEventLoop spinning in
        ``_poll`` on Python 3.11, hanging loop teardown in tests.
        """
        while True:
            try:
                task_id, event = await self._queue.get()
            except asyncio.CancelledError:
                # Flush remaining events before exiting
                while not self._queue.empty():
                    try:
                        task_id, event = self._queue.get_nowait()
                        self._print_line(task_id, event)
                    except asyncio.QueueEmpty:
                        break
                raise

            self._print_line(task_id, event)

    def _print_line(self, task_id: str, event: Event) -> None:
        ts = datetime.fromtimestamp(event.ts).strftime("%H:%M:%S")
        kind = event.kind
        summary = event.summary

        if self.quiet:
            # Only print START / DONE / FAIL / DENIED
            if kind == "start":
                print(f"[{ts}] {task_id} START {summary}")
            elif kind == "done":
                tp = self.dashboard.tasks.get(task_id)
                extra = ""
                if tp:
                    extra = f"  {tp.tool_count} tools"
                    if tp.commit_sha:
                        extra += f"  commit {tp.commit_sha[:7]}"
                print(f"[{ts}] {task_id} DONE{extra}")
            elif kind == "error":
                print(f"[{ts}] {task_id} FAIL {summary}")
            elif kind == "denied":
                print(f"[{ts}] {task_id} DENIED {summary}")
            return

        # Verbose mode — print everything
        if kind == "start":
            print(f"[{ts}] {task_id} START {summary}")
        elif kind == "tool_use":
            print(f"[{ts}] {task_id} {summary}")
        elif kind == "tool_result":
            print(f"[{ts}] {task_id} result: {summary[:60]}")
        elif kind == "denied":
            print(f"[{ts}] {task_id} DENIED {summary}")
        elif kind == "text":
            # Only print non-trivial text
            if len(summary.strip()) > 10:
                print(f"[{ts}] {task_id} text: {summary[:60]}")
        elif kind == "thinking":
            pass  # Folded — too noisy for console
        elif kind == "done":
            tp = self.dashboard.tasks.get(task_id)
            extra = ""
            if tp:
                elapsed = ""
                if tp.started_at and tp.ended_at:
                    secs = int(tp.ended_at - tp.started_at)
                    if secs >= 60:
                        elapsed = f"  {secs // 60}m{secs % 60}s"
                    else:
                        elapsed = f"  {secs}s"
                extra = f"{elapsed} {tp.tool_count} tools"
                if tp.commit_sha:
                    extra += f"  commit {tp.commit_sha[:7]}"
            print(f"[{ts}] {task_id} DONE{extra}")
        elif kind == "error":
            print(f"[{ts}] {task_id} FAIL {summary}")

    def print_integration(self, msg: str) -> None:
        """Print an integration-phase message."""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] integ    {msg}")
