"""JSON-RPC 2.0 message layer, implemented by hand.

This module is deliberately unaware of both MCP and of how bytes reach the
process. It only knows the JSON-RPC 2.0 specification
(https://www.jsonrpc.org/specification). Keeping it isolated is what lets the
same server run over stdio today and over HTTP later without changes here.

Two distinctions matter for the rest of the project:

* A **request** carries an ``id`` and expects exactly one response.
* A **notification** carries no ``id`` and MUST NOT be answered, not even
  when it is malformed.

Batching (a JSON array of messages) is part of JSON-RPC 2.0 but was removed
from MCP in revision 2025-11-25, so it is rejected here as an invalid request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Union

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

RequestId = Union[str, int]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class JsonRpcError(Exception):
    """A JSON-RPC error that can be turned into a wire response.

    ``request_id`` and ``is_notification`` are filled in by :func:`parse_message`
    on a best-effort basis so the dispatcher knows whether to reply at all and,
    if so, which id to echo back.
    """

    code: int = INTERNAL_ERROR
    default_message: str = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        data: Any = None,
        request_id: RequestId | None = None,
        is_notification: bool = False,
    ) -> None:
        self.message = message or self.default_message
        self.data = data
        self.request_id = request_id
        self.is_notification = is_notification
        super().__init__(self.message)

    def to_error_object(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error

    def to_response(self, request_id: RequestId | None = None) -> dict[str, Any]:
        if request_id is None:
            request_id = self.request_id
        return make_error_response(request_id, self)


class ParseError(JsonRpcError):
    code = PARSE_ERROR
    default_message = "Parse error"


class InvalidRequest(JsonRpcError):
    code = INVALID_REQUEST
    default_message = "Invalid Request"


class MethodNotFound(JsonRpcError):
    code = METHOD_NOT_FOUND
    default_message = "Method not found"


class InvalidParams(JsonRpcError):
    code = INVALID_PARAMS
    default_message = "Invalid params"


class InternalError(JsonRpcError):
    code = INTERNAL_ERROR
    default_message = "Internal error"


# --------------------------------------------------------------------------
# Message objects
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Request:
    """An incoming call that expects a response."""

    id: RequestId
    method: str
    params: dict[str, Any] | list[Any] | None = None

    @property
    def is_notification(self) -> bool:
        return False


@dataclass(frozen=True)
class Notification:
    """An incoming one-way message. Never answered, not even on error."""

    method: str
    params: dict[str, Any] | list[Any] | None = None

    @property
    def is_notification(self) -> bool:
        return True


IncomingMessage = Union[Request, Notification]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _is_valid_id(value: Any) -> bool:
    # bool is a subclass of int in Python, but `true` is not a valid id.
    if isinstance(value, bool):
        return False
    return isinstance(value, (str, int))


def parse_message(raw: str) -> IncomingMessage:
    """Turn one raw JSON text into a :class:`Request` or :class:`Notification`.

    Raises :class:`JsonRpcError` (already annotated with ``request_id`` and
    ``is_notification``) when the text is not a well-formed JSON-RPC 2.0
    message.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        # No id can be recovered from unparseable text: the spec says to reply
        # with a null id.
        raise ParseError(data=str(exc)) from exc

    return parse_payload(payload)


def parse_payload(payload: Any) -> IncomingMessage:
    """Validate an already-decoded JSON value as a JSON-RPC 2.0 message."""
    if isinstance(payload, list):
        raise InvalidRequest("Batch requests are not supported")
    if not isinstance(payload, dict):
        raise InvalidRequest("A JSON-RPC message must be a JSON object")

    has_id = "id" in payload
    raw_id = payload.get("id")

    # The id is validated first so that every later error can echo it back.
    if has_id and not _is_valid_id(raw_id):
        raise InvalidRequest(
            "The 'id' member must be a string or an integer",
            data={"received": raw_id},
        )

    try:
        return _build_message(payload, has_id=has_id, raw_id=raw_id)
    except JsonRpcError as exc:
        exc.request_id = raw_id if has_id else None
        exc.is_notification = not has_id
        raise


def _build_message(
    payload: dict[str, Any], *, has_id: bool, raw_id: Any
) -> IncomingMessage:
    version = payload.get("jsonrpc")
    if version != JSONRPC_VERSION:
        raise InvalidRequest(
            "The 'jsonrpc' member must be exactly \"2.0\"",
            data={"received": version},
        )

    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise InvalidRequest(
            "The 'method' member must be a non-empty string",
            data={"received": method},
        )

    params = payload.get("params")
    if params is not None and not isinstance(params, (dict, list)):
        raise InvalidRequest(
            "The 'params' member must be an object or an array",
            data={"received": params},
        )

    if has_id:
        return Request(id=raw_id, method=method, params=params)
    return Notification(method=method, params=params)


# --------------------------------------------------------------------------
# Response building
# --------------------------------------------------------------------------
def make_response(request_id: RequestId, result: Any) -> dict[str, Any]:
    """Build a successful response object."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error_response(
    request_id: RequestId | None, error: JsonRpcError
) -> dict[str, Any]:
    """Build an error response object. A null id means "id unknown"."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": error.to_error_object(),
    }


def encode(message: dict[str, Any]) -> str:
    """Serialize one message to a single line of JSON.

    ``json.dumps`` escapes control characters inside strings, so the result
    never contains a raw newline. That is what makes newline-delimited framing
    safe for the stdio transport.
    """
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))
