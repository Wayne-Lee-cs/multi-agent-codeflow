"""WebSocket server for live dashboard updates."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import signal
import struct
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Limits for security
_MAX_WS_FRAME_SIZE = 1 * 1024 * 1024  # 1MB max WebSocket frame
_MAX_HTTP_HEADERS = 100  # Max number of HTTP headers
_MAX_HTTP_HEADER_SIZE = 8192  # Max total HTTP header size


def _is_localhost_origin(origin: str) -> bool:
    """Check if an Origin header value points to localhost.

    Empty origin is rejected: browsers send Origin for cross-origin requests,
    and same-origin requests from localhost will have an Origin header.
    Non-browser clients that omit Origin are handled by allowing connections
    without an Upgrade header (plain HTTP).
    """
    if not origin:
        return False
    if "\r" in origin or "\n" in origin:
        return False
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname or ""
    return hostname in ("127.0.0.1", "localhost", "::1")


# Simple HTML dashboard frontend
_DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>cagent dashboard</title>
    <style>
        body { font-family: monospace; background: #1a1a2e; color: #eaeaea; margin: 20px; }
        h1 { color: #00d4ff; font-size: 1.5em; }
        .status-bar { margin: 10px 0; padding: 10px; background: #16213e; border-radius: 4px; }
        table { border-collapse: collapse; width: 100%; margin-top: 10px; }
        th, td { border: 1px solid #333; padding: 8px; text-align: left; }
        th { background: #16213e; color: #00d4ff; }
        tr:nth-child(even) { background: #1a1a2e; }
        tr:hover { background: #0f3460; }
        .status-done { color: #4caf50; }
        .status-failed { color: #f44336; }
        .status-running { color: #ff9800; }
        .status-pending { color: #9e9e9e; }
        .status-noop { color: #607d8b; }
        .tokens { font-size: 0.9em; color: #888; }
        .budget { margin-top: 10px; padding: 10px; background: #16213e; border-radius: 4px; }
        .budget-warn { color: #ff9800; }
        .activity { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        #connection { position: fixed; top: 10px; right: 10px; padding: 5px 10px; border-radius: 4px; }
        .connected { background: #4caf50; color: white; }
        .disconnected { background: #f44336; color: white; }
    </style>
</head>
<body>
    <h1>cagent dashboard</h1>
    <div id="connection" class="disconnected">disconnected</div>
    <div class="status-bar" id="status-bar">Connecting...</div>
    <table>
        <thead>
            <tr>
                <th>Task</th>
                <th>Status</th>
                <th>Elapsed</th>
                <th>Tools</th>
                <th>Tokens</th>
                <th>Activity</th>
            </tr>
        </thead>
        <tbody id="tasks"></tbody>
    </table>
    <div class="budget" id="budget" style="display:none"></div>

    <script>
        let ws = null;
        let reconnectTimer = null;
        let allTasks = {};  // local state for incremental updates

        function connect() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${location.host}/ws`);

            ws.onopen = () => {
                document.getElementById('connection').className = 'connected';
                document.getElementById('connection').textContent = 'connected';
                if (reconnectTimer) {
                    clearTimeout(reconnectTimer);
                    reconnectTimer = null;
                }
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'diff') {
                        // Incremental update: merge into local state, remove deleted
                        for (const [tid, tp] of Object.entries(data.tasks)) {
                            if (tp._deleted) { delete allTasks[tid]; }
                            else { allTasks[tid] = tp; }
                        }
                        renderDashboard(data.run_id, allTasks, data.max_tokens);
                    } else {
                        // Full snapshot (on initial connect)
                        allTasks = data.tasks || {};
                        renderDashboard(data.run_id, allTasks, data.max_tokens);
                    }
                } catch (e) {
                    console.error('Failed to parse message:', e);
                }
            };

            ws.onclose = () => {
                document.getElementById('connection').className = 'disconnected';
                document.getElementById('connection').textContent = 'disconnected';
                reconnectTimer = setTimeout(connect, 2000);
            };

            ws.onerror = (err) => {
                console.error('WebSocket error:', err);
                ws.close();
            };
        }

        function renderDashboard(run_id, tasks, max_tokens) {
            // Update status bar
            const total = Object.keys(tasks).length;
            const done = Object.values(tasks).filter(t => t.status === 'done').length;
            const failed = Object.values(tasks).filter(t => t.status === 'failed').length;
            const running = Object.values(tasks).filter(t => t.status === 'running').length;

            let statusParts = [`${done}/${total} done`];
            if (running) statusParts.push(`${running} running`);
            if (failed) statusParts.push(`${failed} failed`);

            document.getElementById('status-bar').textContent =
                `RUN: ${run_id} | ${statusParts.join(' | ')}`;

            // Update tasks table
            const tbody = document.getElementById('tasks');
            tbody.innerHTML = '';

            const sortedIds = Object.keys(tasks).sort();
            let totalIn = 0, totalOut = 0;

            for (const tid of sortedIds) {
                const tp = tasks[tid];
                const tr = document.createElement('tr');

                // Task ID
                const tdId = document.createElement('td');
                tdId.textContent = tid;
                tr.appendChild(tdId);

                // Status
                const tdStatus = document.createElement('td');
                tdStatus.className = `status-${tp.status || 'pending'}`;
                tdStatus.textContent = tp.status || 'pending';
                tr.appendChild(tdStatus);

                // Elapsed
                const tdElapsed = document.createElement('td');
                if (tp.started_at) {
                    const end = tp.ended_at || Date.now() / 1000;
                    const secs = Math.floor(end - tp.started_at);
                    tdElapsed.textContent = secs >= 60
                        ? `${Math.floor(secs/60)}m${secs%60}s`
                        : `${secs}s`;
                }
                tr.appendChild(tdElapsed);

                // Tools
                const tdTools = document.createElement('td');
                tdTools.textContent = tp.tool_count || 0;
                tr.appendChild(tdTools);

                // Tokens
                const tdTokens = document.createElement('td');
                tdTokens.className = 'tokens';
                const tIn = tp.tokens_in || 0;
                const tOut = tp.tokens_out || 0;
                totalIn += tIn;
                totalOut += tOut;
                if (tIn || tOut) {
                    tdTokens.textContent = `${tIn.toLocaleString()}→${tOut.toLocaleString()}`;
                }
                tr.appendChild(tdTokens);

                // Activity
                const tdActivity = document.createElement('td');
                tdActivity.className = 'activity';
                if (tp.commit_sha) {
                    tdActivity.textContent = `commit ${tp.commit_sha.substring(0, 7)}`;
                } else if (tp.last_activity) {
                    tdActivity.textContent = tp.last_activity.substring(0, 50);
                } else if (tp.fail_reason) {
                    tdActivity.textContent = tp.fail_reason.substring(0, 50);
                }
                tr.appendChild(tdActivity);

                tbody.appendChild(tr);
            }

            // Update budget — use DOM API to prevent XSS
            const budgetDiv = document.getElementById('budget');
            if (max_tokens) {
                const combined = totalIn + totalOut;
                const pct = Math.floor(combined * 100 / max_tokens);
                budgetDiv.style.display = 'block';
                budgetDiv.textContent = '';
                budgetDiv.appendChild(document.createTextNode(
                    `Tokens: ${totalIn.toLocaleString()} in, ${totalOut.toLocaleString()} out ` +
                    `(${combined.toLocaleString()} combined / ${max_tokens.toLocaleString()} budget `
                ));
                const span = document.createElement('span');
                span.className = pct >= 80 ? 'budget-warn' : '';
                span.textContent = `${pct}%`;
                budgetDiv.appendChild(span);
                budgetDiv.appendChild(document.createTextNode(')'));
            } else if (totalIn || totalOut) {
                budgetDiv.style.display = 'block';
                budgetDiv.textContent = `Tokens: ${totalIn.toLocaleString()} in, ${totalOut.toLocaleString()} out (${(totalIn + totalOut).toLocaleString()} combined)`;
            } else {
                budgetDiv.style.display = 'none';
            }
        }

        connect();
    </script>
</body>
</html>"""


def _encode_ws_frame(payload: bytes, opcode: int = 0x01) -> bytearray:
    """Encode a WebSocket frame (server-to-client, no masking)."""
    header = bytearray()
    header.append(0x80 | opcode)  # FIN + opcode

    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header.extend(struct.pack(">H", len(payload)))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", len(payload)))

    return header + payload


class WebSocketConnection:
    """A single WebSocket connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.connected = True

    async def send(self, data: str) -> None:
        """Send a text frame to the client."""
        if not self.connected:
            return
        try:
            payload = data.encode("utf-8")
            frame = _encode_ws_frame(payload, 0x01)
            self.writer.write(frame)
            await self.writer.drain()
        except (ConnectionError, OSError):
            self.connected = False

    async def close(self) -> None:
        """Close the connection."""
        if not self.connected:
            return
        self.connected = False
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    _READ_TIMEOUT = 10.0  # per-read timeout to prevent slowloris

    async def read_frame(self) -> tuple[int, bytes] | None:
        """Read one complete WebSocket frame (header + payload).

        Returns (opcode, payload) or None on error/disconnect.
        Each internal read has a timeout to prevent slowloris-style DoS.
        """
        try:
            hdr = await asyncio.wait_for(self.reader.read(2), timeout=self._READ_TIMEOUT)
        except (asyncio.TimeoutError, OSError):
            return None
        if not hdr or len(hdr) < 2:
            return None
        first_byte = hdr[0]
        second_byte = hdr[1]
        opcode = first_byte & 0x0F
        masked = bool(second_byte & 0x80)
        payload_len = second_byte & 0x7F
        if not masked:
            return None
        if payload_len == 126:
            try:
                ext = await asyncio.wait_for(self.reader.read(2), timeout=self._READ_TIMEOUT)
            except (asyncio.TimeoutError, OSError):
                return None
            if len(ext) < 2:
                return None
            payload_len = struct.unpack(">H", ext)[0]
        elif payload_len == 127:
            try:
                ext = await asyncio.wait_for(self.reader.read(8), timeout=self._READ_TIMEOUT)
            except (asyncio.TimeoutError, OSError):
                return None
            if len(ext) < 8:
                return None
            payload_len = struct.unpack(">Q", ext)[0]
        if payload_len > _MAX_WS_FRAME_SIZE:
            return None
        try:
            mask_key = await asyncio.wait_for(self.reader.read(4), timeout=self._READ_TIMEOUT)
        except (asyncio.TimeoutError, OSError):
            return None
        if len(mask_key) < 4:
            return None
        try:
            payload = await asyncio.wait_for(self.reader.read(payload_len), timeout=self._READ_TIMEOUT)
        except (asyncio.TimeoutError, OSError):
            return None
        if len(payload) < payload_len:
            return None
        payload = bytes(
            b ^ mask_key[i % 4] for i, b in enumerate(payload)
        )
        return opcode, payload


class DashboardServer:
    """HTTP + WebSocket server for live dashboard updates."""

    _MAX_CONNECTIONS = 50

    def __init__(self, run_dir: Path, port: int = 8080, host: str = "127.0.0.1", poll_interval: float = 1.0):
        self.run_dir = run_dir
        self.port = port
        self.host = host
        self.connections: list[WebSocketConnection] = []
        self._last_mtime: float = 0.0
        self._last_data: dict[str, Any] | None = None
        self._last_tasks: dict[str, dict[str, Any]] = {}  # task_id -> last known state for diff
        self._server: asyncio.Server | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._poll_interval = poll_interval

    async def start(self) -> None:
        """Start the HTTP/WebSocket server."""
        self._server = await asyncio.start_server(
            self._handle_connection, self.host, self.port
        )
        print(f"Dashboard server started on http://{self.host}:{self.port}")
        print(f"Open in browser to see live updates. Press Ctrl+C to stop.")

        # Start background task to watch for dashboard changes
        self._watch_task = asyncio.create_task(self._watch_dashboard())

        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the server and close all connections."""
        # Cancel background task
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for conn in self.connections:
            await conn.close()
        self.connections.clear()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a new HTTP connection."""
        writer_closed = False
        try:
            # Read the HTTP request
            request_line_raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request_line = request_line_raw.decode("utf-8", errors="replace").strip()

            if not request_line:
                writer.close()
                writer_closed = True
                return

            # Read headers with limits
            headers: dict[str, str] = {}
            header_count = 0
            header_bytes = 0
            while True:
                header_line_raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
                header_line_bytes = len(header_line_raw)
                header_line = header_line_raw.decode("utf-8", errors="replace").strip()

                if not header_line:
                    break

                header_count += 1
                header_bytes += header_line_bytes
                if header_count > _MAX_HTTP_HEADERS or header_bytes > _MAX_HTTP_HEADER_SIZE:
                    await self._send_http_response(writer, 431, "Request Header Fields Too Large")
                    return

                if ":" in header_line:
                    key, value = header_line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()

            # Parse request
            parts = request_line.split()
            if len(parts) < 2:
                writer.close()
                writer_closed = True
                return

            method, path = parts[0], parts[1]

            # Check for WebSocket upgrade
            if (
                path == "/ws"
                and headers.get("upgrade", "").lower() == "websocket"
                and "connection" in headers
                and "upgrade" in headers["connection"].lower()
            ):
                # _handle_websocket takes ownership of the writer
                await self._handle_websocket(reader, writer, headers)
                writer_closed = True
                return

            # Handle CORS preflight
            if method == "OPTIONS":
                origin = headers.get("origin", "")
                if origin and not _is_localhost_origin(origin):
                    await self._send_http_response(writer, 403, b"Forbidden: non-localhost origin")
                else:
                    await self._send_cors_preflight(writer, origin)
                return

            # Handle HTTP requests
            if method not in ("GET", "OPTIONS"):
                await self._send_http_response(writer, 405, b"Method Not Allowed")
            elif method == "GET" and path in ("/", "/index.html"):
                await self._serve_dashboard(writer, headers)
            elif method == "GET" and path == "/api/data":
                await self._serve_api_data(writer, headers)
            else:
                await self._send_http_response(writer, 404, b"Not Found")

        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            if not writer_closed:
                try:
                    writer.close()
                except (ConnectionError, OSError):
                    pass

    async def _handle_websocket(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
    ) -> None:
        """Handle WebSocket upgrade and connection."""
        # Perform WebSocket handshake
        key = headers.get("sec-websocket-key", "")
        if not key:
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
            return

        # Validate WebSocket version
        ws_version = headers.get("sec-websocket-version", "")
        if ws_version != "13":
            response = (
                "HTTP/1.1 426 Upgrade Required\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            )
            writer.write(response.encode())
            await writer.drain()
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
            return

        # Validate Origin — only allow localhost connections
        origin = headers.get("origin", "")
        if origin and not _is_localhost_origin(origin):
            await self._send_http_response(writer, 403, b"Forbidden: non-localhost origin")
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
            return

        # Connection limit
        if len(self.connections) >= self._MAX_CONNECTIONS:
            await self._send_http_response(writer, 503, b"Service Unavailable: max connections reached")
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass
            return

        accept_key = hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-5AB5DC76E4B5").encode()
        ).digest()

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {base64.b64encode(accept_key).decode()}\r\n"
            "\r\n"
        )
        writer.write(response.encode())
        await writer.drain()

        # Create connection and add to list
        conn = WebSocketConnection(reader, writer)
        self.connections.append(conn)

        try:
            # Send current state immediately (full snapshot for new clients)
            data = self._get_dashboard_data()
            if data:
                await conn.send(json.dumps(data))

            # Keep connection alive and handle incoming frames
            while conn.connected:
                try:
                    frame = await asyncio.wait_for(conn.read_frame(), timeout=30.0)
                    if frame is None:
                        break
                    opcode, payload = frame

                    # Handle close frame
                    if opcode == 0x08:
                        break

                    # Handle ping
                    if opcode == 0x09:
                        # Send pong with proper frame encoding
                        pong_frame = _encode_ws_frame(payload, 0x0A)
                        writer.write(pong_frame)
                        await writer.drain()

                    # Ignore unknown opcodes (0x00, 0x02, etc.)

                except asyncio.TimeoutError:
                    # Send ping to keep connection alive
                    try:
                        ping_frame = _encode_ws_frame(b"", 0x09)
                        writer.write(ping_frame)
                        await writer.drain()
                    except (ConnectionError, OSError):
                        break
                except (ConnectionError, OSError):
                    break

        finally:
            conn.connected = False
            if conn in self.connections:
                self.connections.remove(conn)
            # Send close frame before closing writer (RFC 6455 §5.5.1)
            try:
                close_frame = _encode_ws_frame(b"", 0x08)
                writer.write(close_frame)
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass

    async def _send_cors_preflight(self, writer: asyncio.StreamWriter, origin: str) -> None:
        """Handle OPTIONS preflight request with CORS headers."""
        if not origin:
            await self._send_http_response(writer, 204, b"")
            return
        sanitized = origin.replace("\r", "").replace("\n", "")
        extra_headers = (
            f"Access-Control-Allow-Origin: {sanitized}\r\n"
            f"Access-Control-Allow-Methods: GET, OPTIONS\r\n"
            f"Access-Control-Allow-Headers: Content-Type\r\n"
            f"Access-Control-Max-Age: 600\r\n"
        )
        await self._send_http_response(writer, 204, b"", extra_headers=extra_headers)

    async def _serve_dashboard(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None,
    ) -> None:
        """Serve the HTML dashboard."""
        content = _DASHBOARD_HTML.encode("utf-8")
        origin = (headers or {}).get("origin", "")
        extra_headers = self._cors_headers(origin)
        await self._send_http_response(
            writer, 200, content, content_type="text/html; charset=utf-8",
            extra_headers=extra_headers,
        )

    async def _serve_api_data(
        self, writer: asyncio.StreamWriter, headers: dict[str, str] | None = None,
    ) -> None:
        """Serve dashboard data as JSON API."""
        data = self._get_dashboard_data()
        origin = (headers or {}).get("origin", "")
        extra_headers = self._cors_headers(origin)
        if data:
            content = json.dumps(data).encode("utf-8")
            await self._send_http_response(
                writer, 200, content, content_type="application/json",
                extra_headers=extra_headers,
            )
        else:
            await self._send_http_response(writer, 404, b'{"error": "no data"}',
                                           extra_headers=extra_headers)

    @staticmethod
    def _cors_headers(origin: str) -> str:
        """Return CORS headers string for a given Origin."""
        if _is_localhost_origin(origin):
            sanitized = origin.replace("\r", "").replace("\n", "")
            return f"Access-Control-Allow-Origin: {sanitized}\r\n"
        return ""

    async def _send_http_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes | str,
        content_type: str = "text/plain",
        extra_headers: str = "",
    ) -> None:
        """Send an HTTP response with security headers."""
        if isinstance(body, str):
            body = body.encode("utf-8")

        status_text = {
            200: "OK", 204: "No Content", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed",
            431: "Request Header Fields Too Large",
            500: "Internal Server Error", 503: "Service Unavailable",
        }.get(status, "Unknown")

        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"X-Content-Type-Options: nosniff\r\n"
            f"Content-Security-Policy: default-src 'self'; script-src 'unsafe-inline'\r\n"
            f"{extra_headers}"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(response.encode() + body)
        await writer.drain()

    def _get_dashboard_data(self) -> dict[str, Any] | None:
        """Read and return current dashboard data."""
        dashboard_path = self.run_dir / "dashboard.json"
        if not dashboard_path.exists():
            return None

        try:
            data = json.loads(dashboard_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        # Load budget
        max_tokens = None
        budget_path = self.run_dir / "budget.json"
        if budget_path.exists():
            try:
                budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
                max_tokens = budget_data.get("max_tokens")
            except (json.JSONDecodeError, OSError):
                pass

        return {
            "run_id": self.run_dir.name,
            "tasks": data,
            "max_tokens": max_tokens,
        }

    async def _watch_dashboard(self) -> None:
        """Watch dashboard.json for changes and broadcast diffs to clients."""
        dashboard_path = self.run_dir / "dashboard.json"

        while True:
            try:
                if not self.connections:
                    await asyncio.sleep(self._poll_interval)
                    continue
                if dashboard_path.exists():
                    try:
                        mtime = os.stat(dashboard_path).st_mtime
                    except OSError:
                        await asyncio.sleep(self._poll_interval)
                        continue

                    if mtime != self._last_mtime:
                        self._last_mtime = mtime
                        data = self._get_dashboard_data()

                        if data:
                            # Compute diff against last known state
                            current_tasks = data.get("tasks", {})
                            diff_tasks: dict[str, dict[str, Any]] = {}
                            for tid, tp in current_tasks.items():
                                if tid not in self._last_tasks or tp != self._last_tasks[tid]:
                                    diff_tasks[tid] = tp
                            # Mark deleted tasks so clients can remove them
                            deleted_tids = set(self._last_tasks) - set(current_tasks)
                            for tid in deleted_tids:
                                diff_tasks[tid] = {"_deleted": True}

                            if diff_tasks:
                                msg = {
                                    "type": "diff",
                                    "run_id": data.get("run_id"),
                                    "tasks": diff_tasks,
                                    "max_tokens": data.get("max_tokens"),
                                }
                                await self._broadcast(json.dumps(msg))

                            # Update local state after broadcast succeeds
                            for tid in deleted_tids:
                                self._last_tasks.pop(tid, None)
                            self._last_tasks.update({k: v for k, v in diff_tasks.items() if not v.get("_deleted")})

                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(self._poll_interval)

    async def _broadcast(self, message: str) -> None:
        """Send a message to all connected clients."""
        if not self.connections:
            return

        active = [c for c in self.connections if c.connected]
        results = await asyncio.gather(
            *(c.send(message) for c in active),
            return_exceptions=True,
        )

        # Clean up disconnected clients
        for conn, result in zip(active, results):
            if isinstance(result, Exception) or not conn.connected:
                if conn in self.connections:
                    self.connections.remove(conn)
        # Also remove any that were already disconnected before the send
        self.connections[:] = [c for c in self.connections if c.connected]


async def run_dashboard_server(run_dir: Path, port: int = 8080) -> None:
    """Run the dashboard server with graceful shutdown on SIGINT/SIGTERM."""
    server = DashboardServer(run_dir, port)

    loop = asyncio.get_running_loop()

    async def _shutdown() -> None:
        await server.stop()
        loop.stop()

    if sys.platform == "win32":
        # Windows: signal.signal() is the only option (add_signal_handler not supported)
        def _signal_handler(sig: int, frame: object) -> None:
            asyncio.create_task(_shutdown())

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))

    try:
        await server.start()
    except KeyboardInterrupt:
        await server.stop()
