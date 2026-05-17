"""Unit tests for cagent/progress.py — EventParser, Dashboard, Event."""

import json
import time
from pathlib import Path

import pytest

from cagent.progress import Dashboard, Event, EventParser, TaskProgress


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

    def test_done_sets_status(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.update("001", Event(ts=time.time(), kind="done", summary="done", raw={}))
        assert dash.tasks["001"].status == "done"
        assert dash.tasks["001"].ended_at is not None

    def test_error_sets_failed(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.update("001", Event(ts=time.time(), kind="error", summary="crash", raw={}))
        assert dash.tasks["001"].status == "failed"
        assert dash.tasks["001"].fail_reason == "crash"

    def test_set_task_status(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "done", commit_sha="abc123")
        assert dash.tasks["001"].status == "done"
        assert dash.tasks["001"].commit_sha == "abc123"

    def test_set_task_status_noop(self, tmp_path):
        dash = Dashboard(tmp_path)
        dash.set_task_status("001", "noop")
        assert dash.tasks["001"].status == "noop"

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
        dash.update("001", Event(ts=time.time(), kind="done", summary="done", raw={}))
        dash.flush()
        data = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
        assert "001" in data
        assert data["001"]["status"] == "done"

    def test_resume_from_dashboard_json(self, tmp_path):
        # First run
        dash1 = Dashboard(tmp_path)
        dash1.update("001", Event(ts=time.time(), kind="start", summary="start", raw={}))
        dash1.update("001", Event(ts=time.time(), kind="done", summary="done", raw={}))
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
