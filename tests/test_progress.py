"""Unit tests for cagent/progress.py — EventParser, Dashboard, Event."""

import json
import time
from pathlib import Path

import pytest

from cagent.progress import Dashboard, Event, EventParser, TaskProgress, _truncate_jsonl_if_large


class TestEventParser:
    def setup_method(self):
        self.parser = EventParser()

    def test_empty_line(self):
        events = self.parser.feed("")
        assert events == []

    def test_whitespace_only(self):
        events = self.parser.feed("   ")
        assert events == []

    def test_non_json_fallback(self):
        events = self.parser.feed("some random text")
        assert len(events) == 1
        assert events[0].kind == "text"
        assert events[0].summary == "some random text"

    def test_non_json_short_circuit(self):
        """Non-JSON lines should short-circuit without attempting json.loads."""
        events = self.parser.feed("not json at all")
        assert len(events) == 1
        assert events[0].kind == "text"

    def test_system_init(self):
        obj = {"type": "system", "subtype": "init", "model": "claude-opus-4-7"}
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "start"
        assert "claude-opus-4-7" in events[0].summary

    def test_system_non_init_ignored(self):
        obj = {"type": "system", "subtype": "other"}
        events = self.parser.feed(json.dumps(obj))
        assert events == []

    def test_assistant_tool_use(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/foo.py"}}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "tool_use"
        assert "Edit" in events[0].summary
        assert "src/foo.py" in events[0].summary

    def test_assistant_tool_use_bash(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -k test"}}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "tool_use"
        assert "Bash" in events[0].summary
        assert "pytest" in events[0].summary

    def test_assistant_text(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "I have completed the task."}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "text"
        assert events[0].summary == "I have completed the task."

    def test_assistant_text_truncated(self):
        long_text = "A" * 600
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": long_text}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert len(events[0].summary) == 500

    def test_assistant_thinking(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "text": "Let me think..."}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "thinking"

    def test_user_tool_result_success(self):
        obj = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "file written ok"}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert events[0].summary == "file written ok"

    def test_user_tool_result_denied(self):
        obj = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": "Permission denied by hook", "is_error": True}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "denied"

    def test_user_tool_result_list_content(self):
        """tool_result with content as list of objects."""
        obj = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": [{"type": "text", "text": "result text"}]}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "tool_result"
        assert events[0].summary == "result text"

    def test_user_tool_result_empty_content_list(self):
        """Empty content list should not crash."""
        obj = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_1", "content": []}
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1

    def test_result_success(self):
        obj = {"type": "result", "subtype": "success"}
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "done"

    def test_result_success_with_usage(self):
        obj = {
            "type": "result",
            "subtype": "success",
            "usage": {"input_tokens": 1500, "output_tokens": 800},
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "done"
        assert events[0].usage == {"input_tokens": 1500, "output_tokens": 800}

    def test_result_error_with_usage(self):
        obj = {
            "type": "result",
            "subtype": "error",
            "usage": {"input_tokens": 500, "output_tokens": 100},
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].usage == {"input_tokens": 500, "output_tokens": 100}

    def test_result_no_usage(self):
        obj = {"type": "result", "subtype": "success"}
        events = self.parser.feed(json.dumps(obj))
        assert events[0].usage is None

    def test_result_error(self):
        obj = {"type": "result", "subtype": "error"}
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 1
        assert events[0].kind == "error"

    def test_unknown_type_ignored(self):
        obj = {"type": "unknown_xyz"}
        events = self.parser.feed(json.dumps(obj))
        assert events == []

    def test_multiple_content_blocks(self):
        obj = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "x.py"}},
                ]
            }
        }
        events = self.parser.feed(json.dumps(obj))
        assert len(events) == 2
        assert events[0].kind == "text"
        assert events[1].kind == "tool_use"

    def test_raw_line_len_recorded(self):
        obj = {"type": "result", "subtype": "success"}
        line = json.dumps(obj)
        events = self.parser.feed(line)
        assert events[0].raw_line_len == len(line)

    def test_raw_dict_preserved(self):
        obj = {"type": "result", "subtype": "success", "extra": "data"}
        events = self.parser.feed(json.dumps(obj))
        assert events[0].raw["extra"] == "data"


class TestDashboard:
    def test_basic_update(self, tmp_path):
        dash = Dashboard(tmp_path)
        event = Event(ts=time.time(), kind="start", summary="start (model=x)", raw={})
        dash.update("001", event)

        assert "001" in dash.tasks
        tp = dash.tasks["001"]
        assert tp.status == "running"
        assert tp.started_at is not None

    def test_tool_count_increments(self, tmp_path):
        dash = Dashboard(tmp_path)
        for _ in range(3):
            event = Event(ts=time.time(), kind="tool_use", summary="Edit x.py", raw={})
            dash.update("001", event)
        assert dash.tasks["001"].tool_count == 3

    def test_denied_does_not_change_status(self, tmp_path):
        dash = Dashboard(tmp_path)
        # Start the task
        dash.update("001", Event(ts=time.time(), kind="start", summary="start", raw={}))
        assert dash.tasks["001"].status == "running"
        # Denied should not change status
        dash.update("001", Event(ts=time.time(), kind="denied", summary="denied: git push", raw={}))
        assert dash.tasks["001"].status == "running"
        assert "DENIED" in dash.tasks["001"].last_activity

    def test_done_event_does_not_set_status(self, tmp_path):
        """Stream 'done' events only collect tokens — set_task_status() is the authority."""
        dash = Dashboard(tmp_path)
        usage = {"input_tokens": 100, "output_tokens": 50}
        dash.update("001", Event(ts=time.time(), kind="done", summary="done", raw={}, usage=usage))
        assert dash.tasks["001"].status == "pending"
        assert dash.tasks["001"].tokens_in == 100
        assert dash.tasks["001"].tokens_out == 50

    def test_error_event_does_not_set_status(self, tmp_path):
        """Stream 'error' events don't set status — set_task_status() handles it."""
        dash = Dashboard(tmp_path)
        dash.update("001", Event(ts=time.time(), kind="error", summary="crash", raw={}))
        assert dash.tasks["001"].status == "pending"

    def test_set_task_status(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "done", commit_sha="abc123")
        assert dash.tasks["001"].status == "done"
        assert dash.tasks["001"].commit_sha == "abc123"

    def test_set_task_status_noop(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "noop")
        assert dash.tasks["001"].status == "noop"

    def test_set_task_status_invalid(self, tmp_path):
        dash = Dashboard(tmp_path)
        with pytest.raises(ValueError, match="Invalid status"):
            dash.set_task_status("001", "invalid_status")

    def test_event_handler_called(self, tmp_path):
        dash = Dashboard(tmp_path)
        received = []
        dash.set_event_handler(lambda tid, ev: received.append((tid, ev)))
        event = Event(ts=time.time(), kind="start", summary="start", raw={})
        dash.update("001", event)
        assert len(received) == 1
        assert received[0][0] == "001"

    def test_set_task_status_notifies_handler(self, tmp_path):
        dash = Dashboard(tmp_path)
        received = []
        dash.set_event_handler(lambda tid, ev: received.append((tid, ev)))
        dash.set_task_status("001", "done", commit_sha="abc123")
        assert len(received) == 1

    def test_dashboard_json_persisted(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "done", commit_sha="abc123")
        dash.flush()
        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert "001" in data
        assert data["001"]["status"] == "done"

    def test_resume_from_dashboard_json(self, tmp_path):
        # First run — use set_task_status for authoritative status
        dash1 = Dashboard(tmp_path)
        dash1.update("001", Event(ts=time.time(), kind="start", summary="start", raw={}))
        dash1.set_task_status("001", "done", commit_sha="abc123")
        dash1.flush()

        # Simulate resume
        dash2 = Dashboard(tmp_path)
        assert "001" in dash2.tasks
        assert dash2.tasks["001"].status == "done"

    def test_resume_defensive_event_rebuild(self, tmp_path):
        """Dashboard with incomplete Event data should not crash."""
        # Manually write a dashboard.json with incomplete last_event
        data = {
            "001": {
                "task_id": "001",
                "status": "done",
                "last_event": {"ts": 1.0},  # missing kind, summary, raw
            }
        }
        (tmp_path / "dashboard.json").write_text(json.dumps(data), encoding="utf-8")
        dash = Dashboard(tmp_path)
        assert "001" in dash.tasks
        tp = dash.tasks["001"]
        assert tp.last_event is not None
        assert tp.last_event.kind == "text"  # default

    def test_resume_ignores_unknown_fields(self, tmp_path):
        """dashboard.json with unknown fields should not crash or set extra attrs."""
        data = {
            "001": {
                "task_id": "001",
                "status": "done",
                "unknown_field": "should be ignored",
                "another_extra": 42,
            }
        }
        (tmp_path / "dashboard.json").write_text(json.dumps(data), encoding="utf-8")
        dash = Dashboard(tmp_path)
        assert "001" in dash.tasks
        tp = dash.tasks["001"]
        assert tp.status == "done"
        assert not hasattr(tp, "unknown_field") or tp.unknown_field != "should be ignored"

    def test_done_event_accumulates_tokens(self, tmp_path):
        dash = Dashboard(tmp_path)
        usage = {"input_tokens": 2000, "output_tokens": 500}
        event = Event(ts=time.time(), kind="done", summary="done", raw={}, usage=usage)
        dash.update("001", event)
        tp = dash.tasks["001"]
        assert tp.tokens_in == 2000
        assert tp.tokens_out == 500

    def test_done_event_no_usage(self, tmp_path):
        dash = Dashboard(tmp_path)
        event = Event(ts=time.time(), kind="done", summary="done", raw={})
        dash.update("001", event)
        tp = dash.tasks["001"]
        assert tp.tokens_in == 0
        assert tp.tokens_out == 0

    def test_tokens_persisted_in_dashboard_json(self, tmp_path):
        dash = Dashboard(tmp_path)
        usage = {"input_tokens": 3000, "output_tokens": 1200}
        dash.update("001", Event(ts=time.time(), kind="done", summary="done", raw={}, usage=usage))
        dash.flush()
        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert data["001"]["tokens_in"] == 3000
        assert data["001"]["tokens_out"] == 1200

    def test_tokens_restored_on_resume(self, tmp_path):
        # First run
        dash1 = Dashboard(tmp_path)
        usage = {"input_tokens": 1000, "output_tokens": 400}
        dash1.update("001", Event(ts=time.time(), kind="done", summary="done", raw={}, usage=usage))
        dash1.flush()

        # Resume
        dash2 = Dashboard(tmp_path)
        assert dash2.tasks["001"].tokens_in == 1000
        assert dash2.tasks["001"].tokens_out == 400

    def test_resume_rejects_invalid_status(self, tmp_path):
        """dashboard.json with invalid status should be ignored (defaults to pending)."""
        data = {
            "001": {
                "task_id": "001",
                "status": "INVALID_STATUS",
            }
        }
        (tmp_path / "dashboard.json").write_text(json.dumps(data), encoding="utf-8")
        dash = Dashboard(tmp_path)
        assert "001" in dash.tasks
        tp = dash.tasks["001"]
        assert tp.status == "pending"

    def test_resume_rejects_path_traversal_task_id(self, tmp_path):
        """dashboard.json with path traversal task_id should be skipped."""
        data = {
            "../../evil": {"task_id": "../../evil", "status": "done"},
            "001": {"task_id": "001", "status": "done"},
        }
        (tmp_path / "dashboard.json").write_text(json.dumps(data), encoding="utf-8")
        dash = Dashboard(tmp_path)
        assert "../../evil" not in dash.tasks
        assert "001" in dash.tasks

    def test_resume_ignores_task_id_field_override(self, tmp_path):
        """dashboard.json task_id field in dict should not override the key-based id."""
        data = {
            "001": {"task_id": "TAMPERED", "status": "done"},
        }
        (tmp_path / "dashboard.json").write_text(json.dumps(data), encoding="utf-8")
        dash = Dashboard(tmp_path)
        assert "001" in dash.tasks
        assert dash.tasks["001"].task_id == "001"

    def test_path_traversal_rejected_in_update(self, tmp_path):
        """task_id with path traversal components should be rejected."""
        dash = Dashboard(tmp_path)
        event = Event(ts=time.time(), kind="start", summary="start", raw={})
        with pytest.raises(ValueError, match="Invalid task_id"):
            dash.update("../../etc/passwd", event)
        with pytest.raises(ValueError, match="Invalid task_id"):
            dash.update("..\\windows\\system32", event)
        with pytest.raises(ValueError, match="Invalid task_id"):
            dash.update("task/../../../secret", event)

    def test_path_traversal_rejected_in_set_task_status(self, tmp_path):
        """task_id with path traversal should be rejected in set_task_status."""
        dash = Dashboard(tmp_path)
        with pytest.raises(ValueError, match="Invalid task_id"):
            dash.set_task_status("../../etc/passwd", "done")
        with pytest.raises(ValueError, match="Invalid task_id"):
            dash.set_task_status("", "done")

    def test_resume_rejects_wrong_types(self, tmp_path):
        """dashboard.json with wrong field types should skip those fields."""
        data = {
            "001": {
                "task_id": "001",
                "status": "running",
                "tokens_in": "not_a_number",
                "tokens_out": [1, 2, 3],
                "tool_count": "five",
                "started_at": "yesterday",
                "last_activity": 12345,
            },
        }
        (tmp_path / "dashboard.json").write_text(json.dumps(data), encoding="utf-8")
        dash = Dashboard(tmp_path)
        tp = dash.tasks["001"]
        assert tp.status == "running"
        assert tp.tokens_in == 0  # default, not "not_a_number"
        assert tp.tokens_out == 0  # default, not [1,2,3]
        assert tp.tool_count == 0  # default, not "five"
        assert tp.started_at is None  # default, not "yesterday"
        assert tp.last_activity == ""  # default, not 12345


class TestAsyncIO:
    """Tests for async I/O functionality."""

    @pytest.mark.asyncio
    async def test_start_stop_async_io(self, tmp_path):
        """Test starting and stopping async I/O task."""
        dash = Dashboard(tmp_path)
        dash.start_async_io()
        assert dash._io_task is not None

        await dash.stop_async_io()
        assert dash._io_task is None

    @pytest.mark.asyncio
    async def test_async_io_writes_dashboard(self, tmp_path):
        """Test that async I/O writes dashboard.json."""
        import asyncio

        dash = Dashboard(tmp_path)
        dash.start_async_io()

        dash.set_task_status("001", "done", commit_sha="abc123")
        # Give worker time to process
        await asyncio.sleep(0.2)

        await dash.stop_async_io()

        # Verify dashboard was written
        assert (tmp_path / "dashboard.json").exists()
        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert "001" in data
        assert data["001"]["status"] == "done"

    @pytest.mark.asyncio
    async def test_async_io_writes_events(self, tmp_path):
        """Test that async I/O writes event files."""
        import asyncio

        dash = Dashboard(tmp_path)
        dash.start_async_io()

        event = Event(ts=time.time(), kind="start", summary="start", raw={})
        dash.update("001", event)
        # Give worker time to process
        await asyncio.sleep(0.2)

        await dash.stop_async_io()

        # Verify event file was written
        event_file = tmp_path / "events" / "task-001.jsonl"
        assert event_file.exists()

    @pytest.mark.asyncio
    async def test_flush_async_waits_for_io(self, tmp_path):
        """Test that flush_async waits for all I/O to complete."""
        dash = Dashboard(tmp_path)
        dash.start_async_io()

        dash.set_task_status("001", "done", commit_sha="abc123")
        await dash.flush_async()

        # Verify dashboard was written
        assert (tmp_path / "dashboard.json").exists()

        await dash.stop_async_io()

    def test_fallback_sync_io(self, tmp_path):
        """Test that synchronous I/O works when async is not started."""
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "done", commit_sha="abc123")
        dash.flush()

        # Verify dashboard was written synchronously
        assert (tmp_path / "dashboard.json").exists()
        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert "001" in data


class TestIncrementalDashboard:
    """Tests for Phase 54.1: incremental dashboard updates."""

    def test_write_dashboard_only_serializes_dirty_tasks(self, tmp_path):
        """Only changed tasks appear in the diff sent to I/O."""
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "done", commit_sha="aaa")
        dash.flush()

        # Reset tracking
        dash._last_dashboard_snapshot.clear()
        dash._dashboard_dirty = False

        # Write initial snapshot
        dash._write_dashboard(force=True)
        initial_snap = dict(dash._last_dashboard_snapshot)
        assert "001" in initial_snap

        # Update task 002 — should only diff 002
        dash.set_task_status("002", "running")
        dash._write_dashboard(force=True)

        # 001 should still be in snapshot from before, 002 newly added
        assert "002" in dash._last_dashboard_snapshot

    def test_do_write_dashboard_merges_into_existing_file(self, tmp_path):
        """_do_write_dashboard merges diff into existing dashboard.json."""
        dash = Dashboard(tmp_path)
        # Write initial state
        dash.set_task_status("001", "done", commit_sha="aaa")
        dash.flush()

        # Now write a diff for a new task only
        dash._do_write_dashboard({"diff": {"002": {"task_id": "002", "status": "running"}}})

        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        # Both tasks should be present
        assert "001" in data
        assert "002" in data
        assert data["001"]["status"] == "done"
        assert data["002"]["status"] == "running"

    def test_do_write_dashboard_no_diff_skips(self, tmp_path):
        """Empty diff does not write anything."""
        dash = Dashboard(tmp_path)
        dash._do_write_dashboard({"diff": {}})
        assert not (tmp_path / "dashboard.json").exists()

    def test_do_write_dashboard_recovers_from_corrupt_file(self, tmp_path):
        """Corrupted dashboard.json is recovered gracefully on next write."""
        dash = Dashboard(tmp_path)
        # Write corrupted JSON
        (tmp_path / "dashboard.json").write_text("{invalid json!!!", encoding="utf-8")
        # Should not raise — starts fresh
        dash._do_write_dashboard({"diff": {"001": {"task_id": "001", "status": "done"}}})
        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert "001" in data
        assert data["001"]["status"] == "done"

    def test_incremental_preserves_full_state_across_writes(self, tmp_path):
        """Multiple incremental writes accumulate to full state."""
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "done", commit_sha="aaa")
        dash.flush()
        dash.set_task_status("002", "running")
        dash.flush()
        dash.set_task_status("003", "failed", fail_reason="timeout")
        dash.flush()

        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert len(data) == 3
        assert data["001"]["status"] == "done"
        assert data["002"]["status"] == "running"
        assert data["003"]["status"] == "failed"


class TestTruncation:
    """Tests for Phase 56.1: log file truncation."""

    def test_no_truncation_under_limit(self, tmp_path):
        """Files under max_bytes are not modified."""
        path = tmp_path / "test.jsonl"
        lines = [f'{{"i": {i}}}\n' for i in range(10)]
        path.write_text("".join(lines), encoding="utf-8")
        original = path.read_text(encoding="utf-8")

        _truncate_jsonl_if_large(path, max_bytes=1_000_000, keep_ratio=0.8)

        assert path.read_text(encoding="utf-8") == original

    def test_truncation_keeps_tail(self, tmp_path):
        """Files over max_bytes are truncated, keeping the tail."""
        path = tmp_path / "test.jsonl"
        lines = [f'{{"i": {i}}}\n' for i in range(100)]
        path.write_text("".join(lines), encoding="utf-8")

        # Set max_bytes small enough to trigger truncation
        _truncate_jsonl_if_large(path, max_bytes=500, keep_ratio=0.5)

        remaining = path.read_text(encoding="utf-8").splitlines()
        # Should keep ~50% of lines (the last ones)
        assert len(remaining) >= 49  # at least half
        assert len(remaining) <= 51
        # Should contain the last entries
        assert '"i": 99' in remaining[-1]

    def test_truncation_nonexistent_file(self, tmp_path):
        """Truncation on missing file is a no-op."""
        path = tmp_path / "missing.jsonl"
        _truncate_jsonl_if_large(path, max_bytes=100, keep_ratio=0.5)  # should not raise

    def test_truncation_preserves_at_least_one_line(self, tmp_path):
        """Even with aggressive ratio, at least one line is kept."""
        path = tmp_path / "test.jsonl"
        path.write_text('{"i": 0}\n{"i": 1}\n', encoding="utf-8")

        _truncate_jsonl_if_large(path, max_bytes=5, keep_ratio=0.01)

        remaining = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(remaining) >= 1
