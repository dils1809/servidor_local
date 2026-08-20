"""MCP lifecycle and method routing.

This layer turns parsed JSON-RPC messages into MCP semantics. It knows nothing
about stdio (it talks to the abstract ``Transport``) and nothing about SQLite
(feature handlers are injected through ``register_feature``). Those two
boundaries are what make the same server reusable over HTTP later.

Lifecycle, per MCP revision 2025-11-25::

    UNINITIALIZED --initialize--> INITIALIZING --notifications/initialized--> READY

Before ``initialize`` succeeds only ``initialize`` and ``ping`` are accepted;
anything else is an invalid request. ``notifications/initialized`` is a
notification and is therefore never answered, not even to acknowledge it.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable

from .jsonrpc import (
    IncomingMessage,
    InternalError,
    InvalidParams,
    InvalidRequest,
    JsonRpcError,
    MethodNotFound,
    Notification,
    Request,
    encode,
    make_response,
    parse_message,
)
from .transport import Transport

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-11-25"
"""The revision this server implements and offers by default."""

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
"""Revisions this server can speak, newest first, used for negotiation."""

SERVER_NAME = "vibbo-mcp-server"
SERVER_TITLE = "VIBBO Tea Shop Support"
SERVER_VERSION = "0.1.0"

SERVER_INSTRUCTIONS = (
    "Customer support data for VIBBO, an online tea shop. Use the tools to look "
    "up order status, search the product catalog, and open a support ticket when "
    "a human agent is needed. Shipping and returns policies are available as "
    "resources. All data is synthetic."
)

PRE_INIT_METHODS = frozenset({"initialize", "ping"})
"""Methods accepted before the lifecycle handshake completes."""


class LifecycleState(Enum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"


MethodHandler = Callable[[dict[str, Any]], Any]


class MCPServer:
    """Routes MCP methods and owns the connection lifecycle."""

    def __init__(
        self,
        name: str = SERVER_NAME,
        title: str = SERVER_TITLE,
        version: str = SERVER_VERSION,
        instructions: str | None = SERVER_INSTRUCTIONS,
    ) -> None:
        self.name = name
        self.title = title
        self.version = version
        self.instructions = instructions

        self._state = LifecycleState.UNINITIALIZED
        self._client_info: dict[str, Any] | None = None
        self._negotiated_version: str | None = None

        self._methods: dict[str, MethodHandler] = {
            "initialize": self._handle_initialize,
            "ping": self._handle_ping,
        }
        self._notifications: dict[str, Callable[[dict[str, Any]], None]] = {
            "notifications/initialized": self._handle_initialized,
        }
        # Capabilities start empty and grow as features register themselves, so
        # the server never advertises something it cannot actually serve.
        self._capabilities: dict[str, Any] = {}

    # -- registration ------------------------------------------------------
    def register_feature(
        self,
        capability: str,
        config: dict[str, Any],
        methods: dict[str, MethodHandler],
    ) -> None:
        """Attach a feature (tools, resources, prompts) and its capability entry."""
        overlap = set(methods) & set(self._methods)
        if overlap:
            raise ValueError("Methods already registered: " + ", ".join(sorted(overlap)))
        self._capabilities[capability] = config
        self._methods.update(methods)
        logger.debug("registered capability %r with methods %s", capability, sorted(methods))

    # -- state -------------------------------------------------------------
    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def negotiated_version(self) -> str | None:
        return self._negotiated_version

    # -- dispatch ----------------------------------------------------------
    def handle_message(self, message: IncomingMessage) -> dict[str, Any] | None:
        """Process one message. Returns a response, or ``None`` for notifications."""
        if isinstance(message, Notification):
            self._handle_notification(message)
            return None
        return self._handle_request(message)

    def _handle_notification(self, message: Notification) -> None:
        handler = self._notifications.get(message.method)
        if handler is None:
            # Unknown notifications are ignored by design: replying to one would
            # send the client a response it has no pending id for.
            logger.info("ignoring unknown notification %r", message.method)
            return
        try:
            handler(_as_object(message.params))
        except JsonRpcError as exc:
            logger.warning("notification %r failed: %s", message.method, exc.message)
        except Exception:
            logger.exception("unhandled error in notification %r", message.method)

    def _handle_request(self, message: Request) -> dict[str, Any]:
        try:
            handler = self._methods.get(message.method)
            if handler is None:
                raise MethodNotFound(
                    "Unknown method: " + message.method,
                    data={"method": message.method},
                )
            self._check_lifecycle(message.method)
            result = handler(_as_object(message.params))
            return make_response(message.id, result)
        except JsonRpcError as exc:
            logger.info("request %r failed: %s (%s)", message.method, exc.message, exc.code)
            return exc.to_response(message.id)
        except Exception as exc:
            # Never let an unexpected exception kill the connection: the client
            # is entitled to a response for every request it sent.
            logger.exception("unhandled error in method %r", message.method)
            return InternalError(data={"exception": type(exc).__name__}).to_response(
                message.id
            )

    def _check_lifecycle(self, method: str) -> None:
        if method in PRE_INIT_METHODS:
            return
        if self._state is LifecycleState.UNINITIALIZED:
            raise InvalidRequest(
                "Server is not initialized: send 'initialize' first",
                data={"method": method, "state": self._state.value},
            )

    # -- core methods ------------------------------------------------------
    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._state is not LifecycleState.UNINITIALIZED:
            raise InvalidRequest(
                "Server is already initialized",
                data={"state": self._state.value},
            )

        requested = params.get("protocolVersion")
        if not isinstance(requested, str) or not requested:
            raise InvalidParams(
                "'protocolVersion' is required and must be a string",
                data={"received": requested},
            )

        # Echo the client's revision when we speak it; otherwise offer ours and
        # let the client decide whether to continue or disconnect.
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            negotiated = requested
        else:
            negotiated = PROTOCOL_VERSION
            logger.warning(
                "client requested unsupported protocol %r, offering %r",
                requested,
                negotiated,
            )

        client_info = params.get("clientInfo")
        self._client_info = client_info if isinstance(client_info, dict) else None
        self._negotiated_version = negotiated
        self._state = LifecycleState.INITIALIZING

        logger.info(
            "initialize from %s, protocol %s",
            (self._client_info or {}).get("name", "unknown client"),
            negotiated,
        )

        result: dict[str, Any] = {
            "protocolVersion": negotiated,
            "capabilities": self._capabilities,
            "serverInfo": {
                "name": self.name,
                "title": self.title,
                "version": self.version,
            },
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    def _handle_initialized(self, params: dict[str, Any]) -> None:
        if self._state is LifecycleState.UNINITIALIZED:
            logger.warning("received 'notifications/initialized' before 'initialize'")
            return
        self._state = LifecycleState.READY
        logger.info("handshake complete, server is ready")

    def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        # Ping carries no data in either direction; an empty result is the
        # whole contract.
        return {}


def _as_object(params: Any) -> dict[str, Any]:
    """Normalize ``params`` to a dict. MCP only ever uses named parameters."""
    if params is None:
        return {}
    if isinstance(params, dict):
        return params
    raise InvalidParams(
        "Parameters must be passed as an object, not an array",
        data={"received": params},
    )


def serve(transport: Transport, server: MCPServer) -> None:
    """Read, dispatch and reply until the peer closes the channel.

    This is the composition root: the only place where transport and protocol
    meet. It depends on the abstract ``Transport``, so swapping stdio for HTTP
    later does not touch this function.
    """
    logger.info("%s %s listening", server.name, server.version)
    while True:
        try:
            raw = transport.read_message()
        except (KeyboardInterrupt, EOFError):
            logger.info("interrupted while reading, shutting down")
            break
        if raw is None:
            logger.info("peer closed the connection, shutting down")
            break

        try:
            message = parse_message(raw)
        except JsonRpcError as exc:
            if exc.is_notification:
                # A malformed notification gets no reply, by definition.
                logger.info("dropping malformed notification: %s", exc.message)
                continue
            _write(transport, exc.to_response())
            continue

        response = server.handle_message(message)
        if response is not None:
            _write(transport, response)


def _write(transport: Transport, message: dict[str, Any]) -> None:
    try:
        transport.write_message(encode(message))
    except (BrokenPipeError, RuntimeError, ValueError) as exc:
        logger.error("failed to write response: %s", exc)
