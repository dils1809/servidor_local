"""An MCP client that talks to a remote server over HTTP.

Same public surface as MCPClient, so Host cannot tell the difference: it
calls list_tools() and call_tool() and never learns whether the server is a
subprocess on this machine or a container in Google Cloud. That is the point
of the whole layered design, demonstrated.

What actually changes:

* framing -- one message per HTTP request body instead of one per line
* session -- HTTP is stateless, so the server issues an Mcp-Session-Id on
  initialize and every later request carries it
* notifications -- answered with 202 and no body, since there is nothing to
  send back but the HTTP layer still needs a status

TLS key logging: if SSLKEYLOGFILE is set, Python writes the session keys
there and Wireshark can decrypt the capture. That is how the remote traffic
becomes readable for the protocol analysis.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin

from src import jsonrpc

from .logbook import Logbook
from .mcp_client import MCPClientError

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-11-25"
CLIENT_NAME = "vibbo-chatbot"
CLIENT_VERSION = "1.0.0"
SESSION_HEADER = "Mcp-Session-Id"

DEFAULT_TIMEOUT = 60.0
# A cold Cloud Run container has to start before it can answer.
STARTUP_TIMEOUT = 120.0


def build_ssl_context() -> ssl.SSLContext:
    """A normal verifying context, plus key logging when asked for.

    Python honours SSLKEYLOGFILE on its own, but setting keylog_filename
    explicitly means this does not depend on that behaviour staying implicit,
    and it gives somewhere to log that it happened.
    """
    context = ssl.create_default_context()
    keylog = os.environ.get("SSLKEYLOGFILE")
    if keylog:
        context.keylog_filename = keylog
        logger.info("TLS session keys will be written to %s", keylog)
    return context


class HttpMCPClient:
    """A connection to one MCP server over Streamable HTTP."""

    def __init__(
        self,
        name: str,
        url: str,
        *,
        logbook: Logbook | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.name = name
        # A trailing slash on the base would swallow the path segment.
        self._url = url if url.endswith("/mcp") else urljoin(url + "/", "mcp")
        self._logbook = logbook
        self._timeout = timeout
        self._context = build_ssl_context()

        self._session_id: str | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()

        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self.protocol_version: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Run the MCP handshake. There is no process to launch."""
        logger.info("connecting %s to %s", self.name, self._url)
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
        """Tell the server to drop the session. Failing to is harmless."""
        if self._session_id is None:
            return
        request = urllib.request.Request(
            self._url, method="DELETE", headers={SESSION_HEADER: self._session_id}
        )
        try:
            urllib.request.urlopen(request, timeout=10, context=self._context)
        except (urllib.error.URLError, OSError) as exc:
            logger.debug("could not close session on %s: %s", self.name, exc)
        finally:
            self._session_id = None
            logger.info("disconnected %s", self.name)

    # -- sending -----------------------------------------------------------
    def request(
        self, method: str, params: Any = None, *, timeout: float | None = None
    ) -> Any:
        request_id = self._allocate_id()
        message = jsonrpc.make_request(request_id, method, params)

        status, body = self._post(message, timeout or self._timeout)
        if self._logbook is not None:
            self._logbook.record_request(self.name, message)

        if not body:
            raise MCPClientError(f"{self.name} answered {status} with no body")

        payload = jsonrpc.parse_response(body)
        if self._logbook is not None:
            self._logbook.record_response(self.name, payload)
        return jsonrpc.result_or_raise(payload)

    def notify(self, method: str, params: Any = None) -> None:
        message = jsonrpc.make_notification(method, params)
        self._post(message, self._timeout)
        if self._logbook is not None:
            self._logbook.record_notification(self.name, message)

    def _post(self, message: dict[str, Any], timeout: float) -> tuple[int, str]:
        data = jsonrpc.encode(message).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers[SESSION_HEADER] = self._session_id

        request = urllib.request.Request(
            self._url, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=self._context
            ) as response:
                issued = response.headers.get(SESSION_HEADER)
                if issued:
                    self._session_id = issued
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # The server answered, just not with 2xx. A JSON-RPC error body is
            # more useful than the status, so it is passed through.
            body = exc.read().decode("utf-8", errors="replace")
            if body.strip().startswith("{"):
                return exc.code, body
            raise MCPClientError(f"{self.name} returned {exc.code}: {body[:200]}") from exc
        except urllib.error.URLError as exc:
            raise MCPClientError(f"{self.name} is unreachable: {exc.reason}") from exc
        except (OSError, TimeoutError) as exc:
            raise MCPClientError(f"{self.name} failed: {exc}") from exc

    def _allocate_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

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
    def __enter__(self) -> HttpMCPClient:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
