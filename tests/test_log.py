"""Tests for cagent.log — LinePrinter console output."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cagent.log import LinePrinter


@dataclass
class MockEvent:
    ts: float
    kind: Literal["start", "tool_use", "tool_result", "text", "thinking", "denied", "done", "error"]
    summary: str
    raw: dict = field(default_factory=dict)
    raw_line_len: int = 0
    usage: dict | None = None


@pytest.fixture
def mock_dashboard():
    """Create a mock Dashboard with a tasks dict."""
    dashboard = MagicMock()
    dashboard.tasks = {}
    return dashboard


class TestLinePrinterQuiet:
    """Test quiet mode — only START/DONE/FAIL/DENIED printed."""

    def test_quiet_start(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567890.0, kind="start", summary="Building API")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "001 START Building API" in out
        assert "tool_use" not in out
        assert "tool_result" not in out

    def test_quiet_done(self, mock_dashboard, capsys):
        mock_dashboard.tasks["001"] = MagicMock(
            tool_count=5,
            commit_sha="abc1234",
            started_at=1234567800.0,
            ended_at=1234567890.0,
        )
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567890.0, kind="done", summary="")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "001 DONE" in out
        assert "5 tools" in out
        assert "abc1234" in out

    def test_quiet_error(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567890.0, kind="error", summary="Connection refused")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "001 FAIL Connection refused" in out

    def test_quiet_denied(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567890.0, kind="denied", summary="git push blocked")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "001 DENIED git push blocked" in out

    def test_quiet_ignores_tool_use(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567890.0, kind="tool_use", summary="Edit file.py")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert out == ""


class TestLinePrinterVerbose:
    """Test verbose mode — all event types printed."""

    def test_verbose_start(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="start", summary="Building API")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "001 START Building API" in out

    def test_verbose_tool_use(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="tool_use", summary="Edit: src/main.py")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "001 Edit: src/main.py" in out

    def test_verbose_tool_result(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="tool_result", summary="File edited successfully")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "result: File edited successfully" in out

    def test_verbose_text_short_ignored(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        # Short text (<=10 chars) is ignored
        event = MockEvent(ts=1234567890.0, kind="text", summary="ok")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert out == ""

    def test_verbose_text_long_enough(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="text", summary="This is a longer text message that should be printed")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "This is a longer text message" in out

    def test_verbose_thinking_ignored(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="thinking", summary="Thinking...")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert out == ""


class TestLinePrinterCancel:
    """Test cancel behavior — flush remaining events before exit."""

    def test_cancel_flushes_remaining_events(self, mock_dashboard, capsys):
        """When CancelledError is raised, all queued events should be printed."""
        printer = LinePrinter(mock_dashboard, quiet=False)

        # Push multiple events before run starts
        events = [
            MockEvent(ts=1234567890.0, kind="tool_use", summary="Event 1"),
            MockEvent(ts=1234567891.0, kind="tool_use", summary="Event 2"),
            MockEvent(ts=1234567892.0, kind="tool_use", summary="Event 3"),
        ]
        for event in events:
            printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            # Give run() time to start consuming
            await asyncio.sleep(0.02)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "Event 1" in out
        assert "Event 2" in out
        assert "Event 3" in out


class TestPrintIntegration:
    """Test print_integration method."""

    def test_integration_message(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard)
        printer.print_integration("Merging task-001 and task-002")
        out = capsys.readouterr().out
        assert "integ" in out
        assert "Merging task-001 and task-002" in out


class TestLinePrinterVerboseDone:
    """Test verbose done event with elapsed time and commit sha."""

    def test_verbose_done_with_elapsed_and_commit(self, mock_dashboard, capsys):
        """Verbose done shows elapsed time and commit sha."""
        mock_dashboard.tasks["001"] = MagicMock(
            tool_count=5,
            commit_sha="abc1234567890",
            started_at=1234567800.0,
            ended_at=1234567890.0,  # 90s = 1m30s
        )
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="done", summary="")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "DONE" in out
        assert "1m30s" in out
        assert "5 tools" in out
        assert "abc1234" in out

    def test_verbose_done_under_60s(self, mock_dashboard, capsys):
        """Verbose done with elapsed < 60s shows seconds format."""
        mock_dashboard.tasks["001"] = MagicMock(
            tool_count=2,
            commit_sha=None,
            started_at=1234567800.0,
            ended_at=1234567830.0,  # 30s
        )
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567830.0, kind="done", summary="")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "30s" in out

    def test_verbose_done_no_task_progress(self, mock_dashboard, capsys):
        """Verbose done without task progress object."""
        # Don't add task to dashboard.tasks
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="done", summary="")
        printer.push("999", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "999 DONE" in out


class TestLinePrinterVerboseError:
    """Test verbose error event."""

    def test_verbose_error(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="error", summary="Connection timed out")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Connection timed out" in out


class TestLinePrinterVerboseDenied:
    """Test verbose denied event."""

    def test_verbose_denied(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=False)
        event = MockEvent(ts=1234567890.0, kind="denied", summary="safety blocked")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "DENIED" in out
        assert "safety blocked" in out


class TestLinePrinterQuietDoneNoCommit:
    """Test quiet done without commit sha."""

    def test_quiet_done_no_commit(self, mock_dashboard, capsys):
        mock_dashboard.tasks["001"] = MagicMock(
            tool_count=3,
            commit_sha=None,
            started_at=1234567800.0,
            ended_at=1234567830.0,
        )
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567830.0, kind="done", summary="")
        printer.push("001", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "DONE" in out
        assert "3 tools" in out
        assert "commit" not in out


class TestLinePrinterQuietDoneNoTask:
    """Test quiet done when task not found in dashboard."""

    def test_quiet_done_no_task_progress(self, mock_dashboard, capsys):
        printer = LinePrinter(mock_dashboard, quiet=True)
        event = MockEvent(ts=1234567890.0, kind="done", summary="")
        printer.push("999", event)

        async def run():
            push_task = asyncio.create_task(printer.run())
            await asyncio.sleep(0.01)
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        out = capsys.readouterr().out
        assert "999 DONE" in out