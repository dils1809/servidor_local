"""An MCP client, written by hand.

One client owns one server. It launches the server as a subprocess, speaks
JSON-RPC 2.0 over its stdin and stdout, and exposes the MCP methods as normal
Python calls.

This is the mirror image of src/mcp_server.py, and it reuses the same
src/jsonrpc.py for the wire format. There is one definition of a request in
this repository, used from both ends.

Threading: the server writes on its own schedule, so a reader thread drains
stdout continuously. Responses go into a dict keyed by request id; notifica-
tions and logs never block a caller waiting on a reply.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from queue import Empty, Queue
from typing import Any

from src import jsonrpc
from .logbook import Logbook

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-11-25"
CLIENT_NAME = "vibbo-chatbot"
CLIENT_VERSION = "1.0.0"

# How long to wait for one response before giving up.
DEFAULT_TIMEOUT = 30.0
# Servers installed with npx can take a while the first time.
STARTUP_TIMEOUT = 120.0


class MCPClientError(Exception):
    """The client could not talk to the server at all."""


class MCPClient:
    """A connection to one MCP server over stdio."""

    def __init__(
        self,
        name: str,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        logbook: Logbook | None = None,
    ) -> None:
        self.name = name
        self._command = command
        self._cwd = cwd
        self._env = env
        self._logbook = logbook

        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        # One queue per pending request, so a slow call cannot swallow the
        # response meant for another.
        self._pending: dict[jsonrpc.RequestId, Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()

        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.protocol_version: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Launch the server and run the MCP handshake."""
        logger.info("starting %s: %s", self.name, " ".join(self._command))
        try:
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self._cwd,
                env=self._env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise MCPClientError(f"Could not launch {self.name}: {exc}") from exc

        # Popen takes no newline argument, so the stream is fixed afterwards.
        # Without this, Windows rewrites every \n as \r\n and the server sees
        # two delimiters per message.
        if self._process.stdin is not None:
            self._process.stdin.reconfigure(newline="\n")

        self._reader = threading.Thread(
            target=self._read_loop, name=f"mcp-reader-{self.name}", daemon=True
        )
        self._reader.start()
        threading.Thread(
            target=self._drain_stderr, name=f"mcp-stderr-{self.name}", daemon=True
        ).start()

        self._handshake()

    def _handshake(self) -> None:
        """initialize, then notifications/initialized. In that order."""
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
            timeout=STARTUP_TIMEOUT,
        )

        self.protocol_version = result.get("protocolVersion")
        self.capabilities = result.get("capabilities", {})
        self.server_info = result.get("serverInfo", {})

        # The server is not allowed to serve anything until it gets this, and
        # it carries no id, so there is nothing to wait for.
        self.notify("notifications/initialized")

        logger.info(
            "%s ready: %s %s, protocol %s, capabilities %s",
            self.name,
            self.server_info.get("name", "?"),
            self.server_info.get("version", "?"),
            self.protocol_version,
            sorted(self.capabilities),
        )

    def stop(self) -> None:
        """Shut the server down. Closing stdin is the polite way."""
        process = self._process
        if process is None:
            return
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            process.kill()
        finally:
            self._process = None
            logger.info("stopped %s", self.name)

    # -- sending -----------------------------------------------------------
    def request(
        self, method: str, params: Any = None, *, timeout: float = DEFAULT_TIMEOUT
    ) -> Any:
        """Send a request and wait for its response.

        Raises RemoteError if the server answered with an error object.
        """
        request_id = self._allocate_id()
        message = jsonrpc.make_request(request_id, method, params)

        inbox: Queue[dict[str, Any]] = Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = inbox

        try:
            self._write(message)
            if self._logbook is not None:
                self._logbook.record_request(self.name, message)
            try:
                payload = inbox.get(timeout=timeout)
            except Empty:
                raise MCPClientError(
                    f"{self.name} did not answer {method} within {timeout:g}s"
                ) from None
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

        if self._logbook is not None:
            self._logbook.record_response(self.name, payload)
        return jsonrpc.result_or_raise(payload)

    def notify(self, method: str, params: Any = None) -> None:
        """Send a notification. No id, so nothing is waited for."""
        message = jsonrpc.make_notification(method, params)
        self._write(message)
        if self._logbook is not None:
            self._logbook.record_notification(self.name, message)

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPClientError(f"{self.name} is not running")
        line = jsonrpc.encode(message)
        try:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPClientError(f"{self.name} closed its input: {exc}") from exc

    def _allocate_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    # -- receiving ---------------------------------------------------------
    def _read_loop(self) -> None:
        """Drain stdout, handing each response to whoever is waiting for it."""
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip().lstrip("﻿").strip()
            if not line:
                continue
            try:
                payload = jsonrpc.parse_response(line)
            except jsonrpc.JsonRpcError as exc:
                # A server that sends garbage is a bug on its side. Log it and
                # keep reading; one bad line must not kill the connection.
                logger.warning("%s sent an unusable message: %s", self.name, exc)
                continue

            request_id = payload.get("id")
            with self._pending_lock:
                inbox = self._pending.get(request_id)
            if inbox is None:
                # A response nobody is waiting for: a timed-out call, or a
                # request from the server that this client does not implement.
                logger.debug("%s: unmatched response id=%r", self.name, request_id)
                continue
            inbox.put(payload)

        logger.debug("%s closed its output", self.name)

    def _drain_stderr(self) -> None:
        """Forward the server's log to ours, so it is not lost or blocking."""
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            line = line.rstrip()
            if line:
                logger.debug("[%s] %s", self.name, line)

    # -- MCP methods -------------------------------------------------------
    def ping(self) -> None:
        self.request("ping")

    def list_tools(self) -> list[dict[str, Any]]:
        if "tools" not in self.capabilities:
            return []
        return self.request("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def list_resources(self) -> list[dict[str, Any]]:
        if "resources" not in self.capabilities:
            return []
        return self.request("resources/list").get("resources", [])

    def read_resource(self, uri: str) -> list[dict[str, Any]]:
        return self.request("resources/read", {"uri": uri}).get("contents", [])

    def list_prompts(self) -> list[dict[str, Any]]:
        if "prompts" not in self.capabilities:
            return []
        return self.request("prompts/list").get("prompts", [])

    def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("prompts/get", {"name": name, "arguments": arguments})

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
