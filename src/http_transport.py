"""Streamable HTTP transport, the remote counterpart of stdio.

Same server, same handlers, same JSON-RPC layer. Only the framing changes:
instead of one message per line on a pipe, one message per HTTP request body.
That swap is the whole point of keeping mcp_server.py free of transport code.

MCP calls this transport "Streamable HTTP". A client POSTs a JSON-RPC message
to a single endpoint and gets the response in the body. The server hands out
an Mcp-Session-Id on initialize, and the client echoes it on every later
request, because HTTP is stateless and the lifecycle is not: the server has
to know which conversation a message belongs to.

Only the standard library is used, so this deploys with no dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import jsonrpc
from .mcp_server import MCPServer

logger = logging.getLogger(__name__)

MCP_ENDPOINT = "/mcp"
SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"

JSON_TYPE = "application/json"
# A body larger than this is refused before it is read into memory.
MAX_BODY_BYTES = 1_048_576
# Sessions are dropped after this long without a request.
SESSION_IDLE_TIMEOUT = timedelta(minutes=30)


class SessionRegistry:
    """One MCPServer per client session.

    Lifecycle state is per-connection: two clients must not share whether the
    handshake has happened. Over stdio one process is one connection, so this
    problem does not exist. Over HTTP it has to be made explicit.
    """

    def __init__(self, factory: Callable[[], MCPServer]) -> None:
        self._factory = factory
        self._sessions: dict[str, tuple[MCPServer, datetime]] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, MCPServer]:
        session_id = uuid.uuid4().hex
        server = self._factory()
        with self._lock:
            self._sessions[session_id] = (server, _now())
        logger.info("session %s opened", session_id[:8])
        return session_id, server

    def get(self, session_id: str) -> MCPServer | None:
        with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            server, _ = entry
            self._sessions[session_id] = (server, _now())
            return server

    def close(self, session_id: str) -> bool:
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
        if existed:
            logger.info("session %s closed", session_id[:8])
        return existed

    def sweep(self) -> int:
        """Drop idle sessions so a long-lived server does not grow forever."""
        cutoff = _now() - SESSION_IDLE_TIMEOUT
        with self._lock:
            stale = [
                key for key, (_, seen) in self._sessions.items() if seen < cutoff
            ]
            for key in stale:
                del self._sessions[key]
        if stale:
            logger.info("swept %d idle sessions", len(stale))
        return len(stale)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MCPRequestHandler(BaseHTTPRequestHandler):
    """Handles one HTTP request. One instance per request, per the base class."""

    protocol_version = "HTTP/1.1"
    server_version = "vibbo-mcp"
    sys_version = ""

    # Set by serve_http before the server starts.
    registry: SessionRegistry

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health"):
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "server": "vibbo-mcp-server",
                    "transport": "streamable-http",
                    "endpoint": MCP_ENDPOINT,
                    "sessions": len(self.registry),
                },
            )
            return
        # A GET on /mcp is where SSE would be opened. This server never pushes
        # anything on its own, so there is nothing to stream.
        if path == MCP_ENDPOINT:
            self._send_status(HTTPStatus.METHOD_NOT_ALLOWED, "This server does not stream")
            return
        self._send_status(HTTPStatus.NOT_FOUND, "No such path")

    def do_DELETE(self) -> None:  # noqa: N802
        """Ending a session explicitly, as the spec allows."""
        if self.path.split("?", 1)[0] != MCP_ENDPOINT:
            self._send_status(HTTPStatus.NOT_FOUND, "No such path")
            return
        session_id = self.headers.get(SESSION_HEADER, "")
        if session_id and self.registry.close(session_id):
            self._send_status(HTTPStatus.NO_CONTENT, "")
        else:
            self._send_status(HTTPStatus.NOT_FOUND, "No such session")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != MCP_ENDPOINT:
            self._send_status(HTTPStatus.NOT_FOUND, "No such path")
            return

        body = self._read_body()
        if body is None:
            return

        try:
            message = jsonrpc.parse_message(body)
        except jsonrpc.JsonRpcError as exc:
            # A malformed body still gets a JSON-RPC error, not an HTTP one:
            # the request reached the right place, its content was wrong.
            self._send_json(HTTPStatus.OK, exc.to_response(exc.request_id))
            return

        is_initialize = (
            not isinstance(message, jsonrpc.Notification)
            and message.method == "initialize"
        )

        if is_initialize:
            session_id, server = self.registry.create()
        else:
            session_id = self.headers.get(SESSION_HEADER, "")
            server = self.registry.get(session_id) if session_id else None
            if server is None:
                # Without a session there is no lifecycle state, so the server
                # cannot honour anything but a fresh initialize.
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    jsonrpc.make_error_response(
                        getattr(message, "id", None),
                        jsonrpc.InvalidRequest(
                            "Unknown or missing session. Send initialize first."
                        ),
                    ),
                    session_id=None,
                )
                return

        response = server.handle_message(message)

        if response is None:
            # A notification. Nothing to answer, so 202 says "accepted" without
            # inventing a body the client would have to ignore.
            self._send_status(HTTPStatus.ACCEPTED, "", session_id=session_id)
            return

        self._send_json(HTTPStatus.OK, response, session_id=session_id)

    # -- helpers -----------------------------------------------------------
    def _read_body(self) -> str | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_status(HTTPStatus.BAD_REQUEST, "Bad Content-Length")
            return None

        if length <= 0:
            self._send_status(HTTPStatus.BAD_REQUEST, "Empty body")
            return None
        if length > MAX_BODY_BYTES:
            self._send_status(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Body too large")
            return None

        raw = self.rfile.read(length)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self._send_status(HTTPStatus.BAD_REQUEST, "Body is not UTF-8")
            return None

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        session_id: str | None = "",
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", JSON_TYPE)
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header(SESSION_HEADER, session_id)
        self.end_headers()
        self.wfile.write(body)

    def _send_status(
        self, status: HTTPStatus, message: str, session_id: str | None = None
    ) -> None:
        body = message.encode("utf-8") if message else b""
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header(SESSION_HEADER, session_id)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Send access logs to logging instead of stderr directly."""
        logger.info("%s %s", self.address_string(), format % args)


def serve_http(
    factory: Callable[[], MCPServer],
    host: str = "0.0.0.0",
    port: int | None = None,
) -> None:
    """Run the server over HTTP until interrupted.

    Cloud Run passes the port in the PORT environment variable and expects the
    process to listen on every interface, which is why those are the defaults.
    """
    if port is None:
        port = int(os.environ.get("PORT", "8080"))

    registry = SessionRegistry(factory)

    handler = type("BoundHandler", (MCPRequestHandler,), {"registry": registry})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    sweeper = threading.Thread(
        target=_sweep_loop, args=(registry,), name="session-sweeper", daemon=True
    )
    sweeper.start()

    logger.info("listening on http://%s:%d%s", host, port, MCP_ENDPOINT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("interrupted, shutting down")
    finally:
        httpd.server_close()


def _sweep_loop(registry: SessionRegistry) -> None:
    stop = threading.Event()
    while not stop.wait(300):
        registry.sweep()
