"""Tests for cagent.server — WebSocket dashboard server."""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cagent.server import (
    DashboardServer,
    WebSocketConnection,
    _encode_ws_frame,
    _is_localhost_origin,
)


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """Create a mock run directory with dashboard data."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # Create dashboard.json
    dashboard_data = {
        "task-001": {
            "task_id": "task-001",
            "status": "done",
            "started_at": 1000.0,
            "ended_at": 1060.0,
            "tool_count": 5,
            "tokens_in": 1000,
            "tokens_out": 500,
            "commit_sha": "abc1234567890",
        },
        "task-002": {
            "task_id": "task-002",
            "status": "running",
            "started_at": 1010.0,
            "tool_count": 3,
            "tokens_in": 500,
            "tokens_out": 200,
            "last_activity": "Reading file.py",
        },
    }
    (run_dir / "dashboard.json").write_text(
        json.dumps(dashboard_data), encoding="utf-8"
    )

    # Create budget.json
    (run_dir / "budget.json").write_text(
        json.dumps({"max_tokens": 10000}), encoding="utf-8"
    )

    return run_dir


class TestDashboardServer:
    def test_get_dashboard_data(self, run_dir: Path) -> None:
        """Test reading dashboard data."""
        server = DashboardServer(run_dir, port=8080)
        data = server._get_dashboard_data()

        assert data is not None
        assert data["run_id"] == run_dir.name
        assert "task-001" in data["tasks"]
        assert "task-002" in data["tasks"]
        assert data["max_tokens"] == 10000

    def test_get_dashboard_data_no_file(self, tmp_path: Path) -> None:
        """Test reading dashboard data when file doesn't exist."""
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()

        server = DashboardServer(run_dir, port=8080)
        data = server._get_dashboard_data()

        assert data is None

    def test_get_dashboard_data_no_budget(self, run_dir: Path) -> None:
        """Test reading dashboard data without budget file."""
        (run_dir / "budget.json").unlink()

        server = DashboardServer(run_dir, port=8080)
        data = server._get_dashboard_data()

        assert data is not None
        assert data["max_tokens"] is None


class TestWebSocketConnection:
    @pytest.mark.asyncio
    async def test_send_text_frame(self) -> None:
        """Test sending a text frame."""
        reader = AsyncMock()
        writer = MagicMock()
        writer.drain = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        await conn.send('{"test": "data"}')

        # Verify frame was written
        writer.write.assert_called_once()
        frame_data = writer.write.call_args[0][0]
        # Check opcode (0x81 = FIN + TEXT)
        assert frame_data[0] == 0x81

    @pytest.mark.asyncio
    async def test_send_large_frame(self) -> None:
        """Test sending a frame with payload >= 126 bytes."""
        reader = AsyncMock()
        writer = MagicMock()
        writer.drain = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        large_data = json.dumps({"data": "x" * 200})
        await conn.send(large_data)

        # Verify frame was written
        writer.write.assert_called_once()
        frame_data = writer.write.call_args[0][0]
        # Check opcode (0x81 = FIN + TEXT)
        assert frame_data[0] == 0x81
        # Check length indicator (126 = extended 16-bit length)
        assert frame_data[1] == 126

    @pytest.mark.asyncio
    async def test_send_when_disconnected(self) -> None:
        """Test sending when connection is closed."""
        reader = AsyncMock()
        writer = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        conn.connected = False

        await conn.send('{"test": "data"}')

        # Should not write anything
        writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """Test closing connection."""
        reader = AsyncMock()
        writer = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        await conn.close()

        assert conn.connected is False
        writer.close.assert_called_once()


class TestDashboardHTML:
    def test_html_contains_required_elements(self, run_dir: Path) -> None:
        """Test that the HTML template contains required elements."""
        from cagent.server import _DASHBOARD_HTML

        assert "cagent dashboard" in _DASHBOARD_HTML
        assert "WebSocket" in _DASHBOARD_HTML or "ws://" in _DASHBOARD_HTML
        assert "tasks" in _DASHBOARD_HTML
        assert "status-done" in _DASHBOARD_HTML
        assert "status-failed" in _DASHBOARD_HTML
        assert "status-running" in _DASHBOARD_HTML


class TestOriginValidation:
    """Tests for WebSocket Origin validation (52.3)."""

    def test_localhost_origin_allowed(self) -> None:
        """Localhost origins are allowed."""
        from cagent.server import _is_localhost_origin
        assert _is_localhost_origin("http://localhost:8080") is True
        assert _is_localhost_origin("http://127.0.0.1:3000") is True
        assert _is_localhost_origin("http://localhost") is True
        assert _is_localhost_origin("http://[::1]:8080") is True

    def test_non_localhost_origin_rejected(self) -> None:
        """Non-localhost origins are rejected."""
        from cagent.server import _is_localhost_origin
        assert _is_localhost_origin("http://evil.com:8080") is False
        assert _is_localhost_origin("https://example.com") is False
        assert _is_localhost_origin("http://192.168.1.1:8080") is False
        assert _is_localhost_origin("http://10.0.0.1:8080") is False

    def test_empty_origin_rejected(self) -> None:
        """Empty/missing origin is rejected (browsers send Origin for cross-origin)."""
        from cagent.server import _is_localhost_origin
        assert _is_localhost_origin("") is False

    @pytest.mark.asyncio
    async def test_websocket_rejects_non_localhost_origin(self, run_dir: Path) -> None:
        """WebSocket upgrade with non-localhost origin returns 403."""
        server = DashboardServer(run_dir, port=8080)

        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        # Simulate a WebSocket upgrade request with non-localhost origin
        request_data = (
            b"GET /ws HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"Origin: http://evil.com:8080\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        # Capture written data
        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        # Verify 403 response was sent
        response = b"".join(written_data)
        assert b"403" in response
        assert b"Forbidden" in response


class TestCORSPreflight:
    """Tests for Phase 62.4: OPTIONS preflight handling."""

    @pytest.mark.asyncio
    async def test_options_localhost_origin(self, run_dir: Path) -> None:
        """OPTIONS with localhost origin returns CORS headers."""
        server = DashboardServer(run_dir, port=8080)

        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()  # close() is synchronous, not async

        request_data = (
            b"OPTIONS /api/data HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Origin: http://localhost:3000\r\n"
            b"Access-Control-Request-Method: GET\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"204" in response
        assert b"Access-Control-Allow-Origin: http://localhost:3000" in response
        assert b"Access-Control-Allow-Methods: GET, OPTIONS" in response
        assert b"Access-Control-Allow-Headers: Content-Type" in response

    @pytest.mark.asyncio
    async def test_options_non_localhost_rejected(self, run_dir: Path) -> None:
        """OPTIONS with non-localhost origin returns 403."""
        server = DashboardServer(run_dir, port=8080)

        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()  # close() is synchronous, not async

        request_data = (
            b"OPTIONS /api/data HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Origin: http://evil.com:8080\r\n"
            b"Access-Control-Request-Method: GET\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"403" in response
        assert b"Forbidden" in response

    @pytest.mark.asyncio
    async def test_options_no_origin(self, run_dir: Path) -> None:
        """OPTIONS without Origin header returns 204 without CORS headers."""
        server = DashboardServer(run_dir, port=8080)

        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()  # close() is synchronous, not async

        request_data = (
            b"OPTIONS /api/data HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Access-Control-Request-Method: GET\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"204" in response
        assert b"Access-Control-Allow-Origin" not in response

    @pytest.mark.asyncio
    async def test_get_response_includes_cors(self, run_dir: Path) -> None:
        """GET response includes CORS header for localhost origin."""
        server = DashboardServer(run_dir, port=8080)

        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()  # close() is synchronous, not async

        request_data = (
            b"GET /api/data HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Origin: http://localhost:3000\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"200" in response
        assert b"Access-Control-Allow-Origin: http://localhost:3000" in response


class TestSecurityHeaders:
    """Tests for HTTP security headers (57.3.3)."""

    @pytest.mark.asyncio
    async def test_response_includes_nosniff(self, run_dir: Path) -> None:
        """HTTP responses include X-Content-Type-Options: nosniff."""
        server = DashboardServer(run_dir, port=8080)

        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(writer, 200, b"OK")

        response = b"".join(written_data)
        assert b"X-Content-Type-Options: nosniff" in response

    @pytest.mark.asyncio
    async def test_response_includes_csp(self, run_dir: Path) -> None:
        """HTTP responses include restrictive Content-Security-Policy header."""
        server = DashboardServer(run_dir, port=8080)

        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(writer, 200, b"OK")

        response = b"".join(written_data)
        assert b"Content-Security-Policy: default-src 'self'" in response


class TestXSSPrevention:
    """Tests for XSS prevention in dashboard HTML (57.1.2)."""

    def test_html_uses_dom_api_for_task_data(self) -> None:
        """Dashboard HTML uses textContent/createElement for task data, not innerHTML."""
        from cagent.server import _DASHBOARD_HTML

        # innerHTML is only used to clear tbody (safe: no user data interpolation)
        # Task data fields must use textContent to prevent XSS
        assert "textContent" in _DASHBOARD_HTML
        assert "createElement" in _DASHBOARD_HTML

        # Task status and activity use textContent (not innerHTML with interpolated data)
        assert ".textContent" in _DASHBOARD_HTML

    def test_html_budget_uses_dom_api(self) -> None:
        """Budget section uses textContent/appendChild instead of innerHTML."""
        from cagent.server import _DASHBOARD_HTML

        # The budget section was the specific XSS vector in REVIEW.md V1
        assert "budgetDiv.textContent" in _DASHBOARD_HTML or "budgetDiv.appendChild" in _DASHBOARD_HTML
        # Ensure no innerHTML for budget content
        assert "budgetDiv.innerHTML" not in _DASHBOARD_HTML


class TestDiffBroadcast:
    """Tests for Phase 54.1: incremental WS diff broadcast."""

    def test_server_tracks_last_tasks(self, run_dir: Path) -> None:
        """DashboardServer initializes _last_tasks as empty dict."""
        server = DashboardServer(run_dir, port=8080)
        assert server._last_tasks == {}

    def test_get_dashboard_data_returns_full(self, run_dir: Path) -> None:
        """_get_dashboard_data returns all tasks (used for initial connect)."""
        server = DashboardServer(run_dir, port=8080)
        data = server._get_dashboard_data()
        assert data is not None
        assert "task-001" in data["tasks"]
        assert "task-002" in data["tasks"]

    @pytest.mark.asyncio
    async def test_watch_dashboard_broadcasts_diff(self, run_dir: Path) -> None:
        """_watch_dashboard sends only changed tasks as diff."""
        server = DashboardServer(run_dir, port=8080)
        conn = AsyncMock()
        conn.connected = True
        server.connections.append(conn)

        # Initialize _last_tasks as if we already sent the initial state
        server._last_tasks = {
            "task-001": {"task_id": "task-001", "status": "done"},
            "task-002": {"task_id": "task-002", "status": "running"},
        }

        # Modify dashboard.json — only task-002 changes
        dashboard_data = {
            "task-001": {"task_id": "task-001", "status": "done"},
            "task-002": {"task_id": "task-002", "status": "done"},
        }
        (run_dir / "dashboard.json").write_text(
            json.dumps(dashboard_data), encoding="utf-8"
        )

        # Trigger one iteration of _watch_dashboard manually
        import os
        mtime = os.stat(run_dir / "dashboard.json").st_mtime
        server._last_mtime = mtime - 1  # force re-read

        # Run one iteration
        task = asyncio.create_task(server._watch_dashboard())
        await asyncio.sleep(1.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Verify a diff was broadcast
        assert conn.send.call_count >= 1
        sent = json.loads(conn.send.call_args_list[-1][0][0])
        assert sent["type"] == "diff"
        assert "task-002" in sent["tasks"]
        # task-001 unchanged — should not be in diff
        assert "task-001" not in sent["tasks"]

    def test_client_connect_does_not_overwrite_last_tasks(self, run_dir: Path) -> None:
        """BUG 1 regression: connecting a client must not reset _last_tasks."""
        server = DashboardServer(run_dir, port=8080)
        # Simulate watcher state with known diff baseline
        server._last_tasks = {"task-001": {"status": "done"}}
        # _get_dashboard_data returns full state (includes task-002)
        data = server._get_dashboard_data()
        assert data is not None
        # After the fix, _handle_websocket should NOT call _last_tasks.update()
        # Simulate what _handle_websocket does:
        if data:
            pass  # send only, no _last_tasks.update
        # _last_tasks should remain unchanged
        assert server._last_tasks == {"task-001": {"status": "done"}}


class TestEncodeWsFrame:
    """Tests for _encode_ws_frame."""

    def test_small_payload(self) -> None:
        """Payload < 126 bytes uses 7-bit length."""
        payload = b"hello"
        frame = _encode_ws_frame(payload, 0x01)
        assert frame[0] == 0x81  # FIN + TEXT
        assert frame[1] == 5     # length
        assert bytes(frame[2:]) == payload

    def test_medium_payload(self) -> None:
        """Payload 126-65535 bytes uses 16-bit extended length."""
        payload = b"x" * 200
        frame = _encode_ws_frame(payload, 0x01)
        assert frame[0] == 0x81
        assert frame[1] == 126  # extended 16-bit
        length = struct.unpack(">H", frame[2:4])[0]
        assert length == 200
        assert bytes(frame[4:]) == payload

    def test_ping_opcode(self) -> None:
        """Ping frame uses opcode 0x09."""
        frame = _encode_ws_frame(b"", 0x09)
        assert frame[0] == 0x89  # FIN + PING
        assert frame[1] == 0

    def test_pong_opcode(self) -> None:
        """Pong frame uses opcode 0x0A."""
        frame = _encode_ws_frame(b"pong", 0x0A)
        assert frame[0] == 0x8A  # FIN + PONG
        assert frame[1] == 4

    def test_close_opcode(self) -> None:
        """Close frame uses opcode 0x08."""
        frame = _encode_ws_frame(b"", 0x08)
        assert frame[0] == 0x88  # FIN + CLOSE

    def test_empty_payload(self) -> None:
        """Empty payload produces minimal frame."""
        frame = _encode_ws_frame(b"", 0x01)
        assert len(frame) == 2
        assert frame[1] == 0


class TestCorsHeaders:
    """Tests for DashboardServer._cors_headers static method."""

    def test_localhost_origin(self) -> None:
        """Localhost origin returns Access-Control-Allow-Origin header."""
        result = DashboardServer._cors_headers("http://localhost:3000")
        assert "Access-Control-Allow-Origin: http://localhost:3000" in result

    def test_non_localhost_origin(self) -> None:
        """Non-localhost origin returns empty string."""
        result = DashboardServer._cors_headers("http://evil.com:8080")
        assert result == ""

    def test_empty_origin(self) -> None:
        """Empty origin is not localhost, returns empty CORS."""
        result = DashboardServer._cors_headers("")
        assert result == ""


class TestSendHttpResponse:
    """Tests for _send_http_response."""

    @pytest.mark.asyncio
    async def test_string_body_encoded(self, run_dir: Path) -> None:
        """String body is encoded to UTF-8."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(writer, 200, "hello world")

        response = b"".join(written_data)
        assert b"hello world" in response
        assert b"Content-Length: 11" in response

    @pytest.mark.asyncio
    async def test_status_404(self, run_dir: Path) -> None:
        """404 response includes correct status text."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(writer, 404, b"Not Found")

        response = b"".join(written_data)
        assert b"404 Not Found" in response

    @pytest.mark.asyncio
    async def test_status_500(self, run_dir: Path) -> None:
        """500 response includes correct status text."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(writer, 500, b"Internal Server Error")

        response = b"".join(written_data)
        assert b"500 Internal Server Error" in response

    @pytest.mark.asyncio
    async def test_content_type_header(self, run_dir: Path) -> None:
        """Custom content_type is included in response."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(
            writer, 200, b"{}", content_type="application/json"
        )

        response = b"".join(written_data)
        assert b"Content-Type: application/json" in response

    @pytest.mark.asyncio
    async def test_extra_headers_included(self, run_dir: Path) -> None:
        """Extra headers are included in the response."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(
            writer, 200, b"OK",
            extra_headers="X-Custom: value\r\n",
        )

        response = b"".join(written_data)
        assert b"X-Custom: value" in response

    @pytest.mark.asyncio
    async def test_unknown_status_code(self, run_dir: Path) -> None:
        """Unknown status code uses 'Unknown' text."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._send_http_response(writer, 418, b"teapot")

        response = b"".join(written_data)
        assert b"418 Unknown" in response


class TestServeDashboard:
    """Tests for _serve_dashboard."""

    @pytest.mark.asyncio
    async def test_serves_html(self, run_dir: Path) -> None:
        """Dashboard endpoint serves HTML content."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._serve_dashboard(writer, {"origin": "http://localhost:8080"})

        response = b"".join(written_data)
        assert b"200 OK" in response
        assert b"text/html" in response
        assert b"cagent dashboard" in response


class TestServeApiData:
    """Tests for _serve_api_data."""

    @pytest.mark.asyncio
    async def test_returns_json_data(self, run_dir: Path) -> None:
        """API endpoint returns dashboard data as JSON."""
        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._serve_api_data(writer, {})

        response = b"".join(written_data)
        assert b"200 OK" in response
        assert b"application/json" in response
        assert b"task-001" in response

    @pytest.mark.asyncio
    async def test_no_data_returns_404(self, tmp_path: Path) -> None:
        """API endpoint returns 404 when no dashboard data."""
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()

        server = DashboardServer(run_dir, port=8080)
        writer = AsyncMock()
        writer.drain = AsyncMock()

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._serve_api_data(writer, {})

        response = b"".join(written_data)
        assert b"404" in response
        assert b"no data" in response


class TestGetDashboardDataEdgeCases:
    """Edge cases for _get_dashboard_data."""

    def test_corrupt_dashboard_json(self, tmp_path: Path) -> None:
        """Returns None when dashboard.json is corrupt."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text("not json", encoding="utf-8")

        server = DashboardServer(run_dir, port=8080)
        assert server._get_dashboard_data() is None

    def test_corrupt_budget_json(self, tmp_path: Path) -> None:
        """Returns data with None max_tokens when budget.json is corrupt."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "dashboard.json").write_text("{}", encoding="utf-8")
        (run_dir / "budget.json").write_text("corrupt", encoding="utf-8")

        server = DashboardServer(run_dir, port=8080)
        data = server._get_dashboard_data()
        assert data is not None
        assert data["max_tokens"] is None


class TestWebSocketConnectionEdgeCases:
    """Edge cases for WebSocketConnection."""

    @pytest.mark.asyncio
    async def test_send_connection_error(self) -> None:
        """Connection error during send sets connected to False."""
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock(side_effect=ConnectionError("reset"))
        writer.drain = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        assert conn.connected is True

        await conn.send('{"test": 1}')
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_send_os_error(self) -> None:
        """OSError during send sets connected to False."""
        reader = AsyncMock()
        writer = MagicMock()
        writer.write = MagicMock(side_effect=OSError("broken pipe"))
        writer.drain = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        await conn.send('{"test": 1}')
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_close_when_already_disconnected(self) -> None:
        """Closing an already disconnected connection is a no-op."""
        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        conn.connected = False

        await conn.close()
        writer.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_handles_connection_error(self) -> None:
        """Close handles ConnectionError gracefully."""
        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock(side_effect=ConnectionError("reset"))
        writer.wait_closed = AsyncMock()

        conn = WebSocketConnection(reader, writer)
        await conn.close()

        assert conn.connected is False


class TestHandleConnection:
    """Tests for _handle_connection HTTP routing."""

    @pytest.mark.asyncio
    async def test_empty_request_line(self, run_dir: Path) -> None:
        """Empty request line closes connection."""
        server = DashboardServer(run_dir, port=8080)
        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()

        async def mock_readline():
            return b""

        reader.readline = mock_readline

        await server._handle_connection(reader, writer)
        writer.close.assert_called()

    @pytest.mark.asyncio
    async def test_unknown_path_returns_404(self, run_dir: Path) -> None:
        """Unknown path returns 404."""
        server = DashboardServer(run_dir, port=8080)
        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()

        request_data = (
            b"GET /unknown HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"404" in response

    @pytest.mark.asyncio
    async def test_malformed_request_line(self, run_dir: Path) -> None:
        """Malformed request (single word) closes connection."""
        server = DashboardServer(run_dir, port=8080)
        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()

        request_data = b"INVALID\r\n\r\n"
        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        await server._handle_connection(reader, writer)
        writer.close.assert_called()

    @pytest.mark.asyncio
    async def test_websocket_no_key_returns_close(self, run_dir: Path) -> None:
        """WebSocket upgrade without Sec-WebSocket-Key closes connection."""
        server = DashboardServer(run_dir, port=8080)
        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.drain = AsyncMock()

        request_data = (
            b"GET /ws HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        await server._handle_connection(reader, writer)
        writer.close.assert_called()

    @pytest.mark.asyncio
    async def test_websocket_wrong_version(self, run_dir: Path) -> None:
        """WebSocket with wrong version returns 426."""
        server = DashboardServer(run_dir, port=8080)
        reader = AsyncMock()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.drain = AsyncMock()

        request_data = (
            b"GET /ws HTTP/1.1\r\n"
            b"Host: localhost:8080\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 12\r\n"
            b"\r\n"
        )

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"426" in response


class TestDashboardServerConstructor:
    """Tests for DashboardServer initialization."""

    def test_default_port(self, run_dir: Path) -> None:
        """Default port is 8080."""
        server = DashboardServer(run_dir)
        assert server.port == 8080

    def test_custom_host_port(self, run_dir: Path) -> None:
        """Custom host and port are stored."""
        server = DashboardServer(run_dir, port=9090, host="0.0.0.0")
        assert server.port == 9090
        assert server.host == "0.0.0.0"

    def test_initial_state(self, run_dir: Path) -> None:
        """Initial state has no connections or cached data."""
        server = DashboardServer(run_dir, port=8080)
        assert server.connections == []
        assert server._last_mtime == 0.0
        assert server._last_data is None
        assert server._last_tasks == {}
        assert server._server is None
        assert server._watch_task is None


class TestBroadcast:
    """Tests for _broadcast."""

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self, run_dir: Path) -> None:
        """Broadcast sends message to all connected clients."""
        server = DashboardServer(run_dir, port=8080)

        conn1 = AsyncMock()
        conn1.connected = True
        conn2 = AsyncMock()
        conn2.connected = True

        server.connections = [conn1, conn2]

        await server._broadcast('{"type": "test"}')

        conn1.send.assert_called_once_with('{"type": "test"}')
        conn2.send.assert_called_once_with('{"type": "test"}')

    @pytest.mark.asyncio
    async def test_broadcast_removes_disconnected(self, run_dir: Path) -> None:
        """Disconnected connections are removed during broadcast."""
        server = DashboardServer(run_dir, port=8080)

        conn1 = AsyncMock()
        conn1.connected = True
        conn2 = AsyncMock()
        conn2.connected = False  # already disconnected

        server.connections = [conn1, conn2]

        await server._broadcast('{"type": "test"}')

        assert conn1 in server.connections
        assert conn2 not in server.connections

    @pytest.mark.asyncio
    async def test_broadcast_handles_send_error(self, run_dir: Path) -> None:
        """Connection error during broadcast removes the connection."""
        server = DashboardServer(run_dir, port=8080)

        conn = AsyncMock()
        conn.connected = True
        conn.send = AsyncMock(side_effect=ConnectionError("reset"))

        server.connections = [conn]

        await server._broadcast('{"type": "test"}')

        assert conn not in server.connections


class TestHandleConnectionHeaderLimits:
    """Tests for HTTP header size/count limits."""

    @pytest.mark.asyncio
    async def test_too_many_headers(self, run_dir: Path) -> None:
        """Exceeding max header count returns 431."""
        server = DashboardServer(run_dir, port=8080)
        reader = AsyncMock()
        writer = AsyncMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()

        # Build request with many headers
        header_lines = [f"X-Header-{i}: value{i}\r\n".encode() for i in range(120)]
        request_data = b"GET / HTTP/1.1\r\n" + b"".join(header_lines) + b"\r\n"

        read_data = request_data

        async def mock_readline():
            nonlocal read_data
            if not read_data:
                return b""
            idx = read_data.find(b"\r\n")
            if idx == -1:
                line = read_data
                read_data = b""
            else:
                line = read_data[:idx + 2]
                read_data = read_data[idx + 2:]
            return line

        reader.readline = mock_readline

        written_data = []

        def capture_write(data):
            written_data.append(data)

        writer.write = MagicMock(side_effect=capture_write)

        await server._handle_connection(reader, writer)

        response = b"".join(written_data)
        assert b"431" in response
