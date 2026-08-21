"""The ``tools`` capability: what the model is allowed to do.

Two kinds of failure live in this file and they are answered differently, as
the MCP specification requires:

* **Protocol errors** -- an unknown tool name, a missing argument, a wrong
  type -- are JSON-RPC errors (-32601 / -32602). The model never sees them;
  they mean the client sent something malformed.
* **Execution errors** -- "no order matches that email" -- are *successful*
  responses carrying ``isError: true``. The model does see them, which is the
  point: it needs to read the failure in order to tell the customer what went
  wrong, or to offer opening a ticket.

Every tool declares an ``outputSchema`` and returns ``structuredContent``. For
``get_order_status`` that turns the privacy rule into a machine-checkable
contract: the schema lists exactly five fields, so no future edit can quietly
start returning an address.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Callable

from .. import db
from ..jsonrpc import InvalidParams

logger = logging.getLogger(__name__)

CAPABILITY_NAME = "tools"
CAPABILITY_CONFIG: dict[str, Any] = {"listChanged": False}

CURRENCY = "USD"

# Bounds on customer-authored text. Without them a single call could write an
# unbounded blob into the database.
MAX_SUBJECT_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000
MAX_QUERY_LENGTH = 100

# One message for both "no such order" and "email does not match". Telling the
# caller which half failed would confirm that an order number exists.
ORDER_NOT_FOUND_MESSAGE = (
    "No order matches that order number and email address together. "
    "Check that both are exactly as they appear in the confirmation email."
)


# ---------------------------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_order_status",
        "title": "Get order status",
        "description": (
            "Look up the delivery status of a single order. Requires both the "
            "customer's email address and the order number; one without the "
            "other will not return anything. Returns the shipping status, "
            "carrier, tracking number, estimated delivery date and the items "
            "in the order. It never returns addresses, phone numbers or "
            "payment details."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Email address used to place the order.",
                },
                "order_number": {
                    "type": "string",
                    "description": "Order number, with or without the leading '#' (e.g. '#1009' or '1009').",
                },
            },
            "required": ["email", "order_number"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "carrier": {"type": ["string", "null"]},
                "tracking_number": {"type": ["string", "null"]},
                "estimated_delivery": {"type": ["string", "null"]},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "variant_title": {"type": "string"},
                            "quantity": {"type": "integer"},
                        },
                        "required": ["title", "variant_title", "quantity"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["status", "carrier", "tracking_number", "estimated_delivery", "items"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Get order status",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "search_products",
        "title": "Search products",
        "description": (
            "Search the VIBBO catalog by product name, product type or "
            "ingredient. Returns matching products with their price in US "
            "dollars and the stock available for each variant, so it can also "
            "answer whether something is currently sold out."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free text matched against the product name, type and description. Ingredients work too, e.g. 'detox', 'bundle', 'lavender', 'filter bags'.",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "count": {"type": "integer"},
                "currency": {"type": "string"},
                "products": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "product_type": {"type": "string"},
                            "price": {"type": "number"},
                            "description": {"type": "string"},
                            "variants": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "sku": {"type": ["string", "null"]},
                                        "inventory_quantity": {"type": "integer"},
                                        "in_stock": {"type": "boolean"},
                                    },
                                    "required": ["title", "sku", "inventory_quantity", "in_stock"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["title", "product_type", "price", "description", "variants"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["query", "count", "currency", "products"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Search products",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "create_support_ticket",
        "title": "Create support ticket",
        "description": (
            "Open a support ticket for a human agent. Use this when the "
            "customer's problem cannot be resolved with the other tools, for "
            "example a damaged shipment, a change of address or a refund "
            "request. Returns the ticket identifier so it can be given to the "
            "customer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Customer email address for the follow-up.",
                },
                "subject": {
                    "type": "string",
                    "description": "Short summary of the problem (max %d characters)." % MAX_SUBJECT_LENGTH,
                },
                "description": {
                    "type": "string",
                    "description": "Full description, including the order number when there is one (max %d characters)." % MAX_DESCRIPTION_LENGTH,
                },
            },
            "required": ["email", "subject", "description"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "integer"},
                "status": {"type": "string"},
                "created_at": {"type": "string"},
            },
            "required": ["ticket_id", "status", "created_at"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Create support ticket",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
]


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    definition["name"]: definition["inputSchema"] for definition in TOOL_DEFINITIONS
}


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
def _require_string(
    arguments: dict[str, Any], name: str, *, max_length: int | None = None
) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise InvalidParams(
            "'%s' is required and must be a string" % name,
            data={"argument": name, "received": value},
        )
    value = value.strip()
    if not value:
        raise InvalidParams(
            "'%s' must not be empty" % name, data={"argument": name}
        )
    if max_length is not None and len(value) > max_length:
        raise InvalidParams(
            "'%s' must be at most %d characters" % (name, max_length),
            data={"argument": name, "length": len(value), "max_length": max_length},
        )
    return value


def _reject_unknown_arguments(arguments: dict[str, Any], tool_name: str) -> None:
    """Enforce the ``additionalProperties: false`` each input schema declares.

    Silently ignoring unknown arguments would make the published schema a lie,
    and would hide client bugs -- a misspelled ``order_no`` would look like a
    missing order rather than a typo.
    """
    allowed = set(TOOL_SCHEMAS[tool_name]["properties"])
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise InvalidParams(
            "Unexpected argument(s): " + ", ".join(unexpected),
            data={"unexpected": unexpected, "allowed": sorted(allowed)},
        )


def _require_email(arguments: dict[str, Any], name: str = "email") -> str:
    value = _require_string(arguments, name, max_length=254)
    # Deliberately loose: this rejects obvious typos, not exotic-but-valid
    # addresses. Rejecting a real customer's address would be worse than
    # letting a lookup return nothing.
    local, separator, domain = value.partition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise InvalidParams(
            "'%s' is not a valid email address" % name,
            data={"argument": name},
        )
    return value


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------
def _success(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload as a successful tool result.

    The text block is the same data serialized, because that is what the model
    actually reads; ``structuredContent`` is what a client can validate
    against the declared ``outputSchema``.
    """
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
        ],
        "structuredContent": payload,
        "isError": False,
    }


def _failure(message: str) -> dict[str, Any]:
    """An execution failure the model is meant to read and act on.

    No ``structuredContent`` here: the declared output schema describes a
    successful result, and a failure does not conform to it.
    """
    return {"content": [{"type": "text", "text": message}], "isError": True}


def describe_order_status(order: dict[str, Any]) -> str:
    """Turn the Shopify status columns into one sentence a customer understands."""
    if order.get("cancelled_at"):
        return "Cancelled"

    financial = order["financial_status"]
    fulfillment = order["fulfillment_status"]
    shipment = (order.get("fulfillment") or {}).get("shipment_status")

    if financial == "pending":
        return "Awaiting payment confirmation"
    if financial in ("refunded", "partially_refunded") and fulfillment == "restocked":
        return "Refunded and returned to stock"
    if fulfillment == "unfulfilled":
        return "Paid, being prepared for shipment"

    shipment_labels = {
        "in_transit": "In transit",
        "out_for_delivery": "Out for delivery",
        "delivered": "Delivered",
        "failure": "Delivery attempt failed, the carrier will try again",
    }
    label = shipment_labels.get(shipment, "Shipped")
    if fulfillment == "partial":
        return "Partially shipped, " + label.lower()
    return label


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
ConnectionFactory = Callable[[], sqlite3.Connection]


class ToolHandlers:
    """Implements ``tools/list`` and ``tools/call``.

    The connection factory is injected so tests can point at a scratch
    database. Connections are opened per call and closed immediately, which
    keeps this class safe to reuse under a threaded HTTP transport later.
    """

    def __init__(self, connect: ConnectionFactory = db.connect) -> None:
        self._connect = connect
        self._tools: dict[str, Callable[[dict[str, Any], sqlite3.Connection], dict[str, Any]]] = {
            "get_order_status": self._get_order_status,
            "search_products": self._search_products,
            "create_support_ticket": self._create_support_ticket,
        }

    def methods(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        return {"tools/list": self.list_tools, "tools/call": self.call_tool}

    # -- tools/list --------------------------------------------------------
    def list_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        # No pagination: three tools fit in one page, so no cursor is returned.
        return {"tools": TOOL_DEFINITIONS}

    # -- tools/call --------------------------------------------------------
    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidParams("'name' is required and must be a string")

        implementation = self._tools.get(name)
        if implementation is None:
            # An unknown tool is the client's mistake, not a tool failure, so
            # it is a protocol error rather than an isError result.
            raise InvalidParams(
                "Unknown tool: " + name,
                data={"name": name, "available": sorted(self._tools)},
            )

        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise InvalidParams(
                "'arguments' must be an object",
                data={"received": arguments},
            )
        _reject_unknown_arguments(arguments, name)

        connection = self._connect()
        try:
            return implementation(arguments, connection)
        finally:
            connection.close()

    # -- individual tools --------------------------------------------------
    def _get_order_status(
        self, arguments: dict[str, Any], connection: sqlite3.Connection
    ) -> dict[str, Any]:
        email = _require_email(arguments)
        order_number = _require_string(arguments, "order_number", max_length=32)

        order = db.find_order(connection, email, order_number)
        if order is None:
            # Logged without the email so the diagnostic log does not become a
            # second copy of the customer data.
            logger.info("order lookup miss for %r", db.normalize_order_number(order_number))
            return _failure(ORDER_NOT_FOUND_MESSAGE)

        fulfillment = order.get("fulfillment") or {}
        # This dictionary is the entire privacy boundary. Anything not listed
        # here cannot reach the model, whatever the database holds.
        payload = {
            "status": describe_order_status(order),
            "carrier": fulfillment.get("tracking_company"),
            "tracking_number": fulfillment.get("tracking_number"),
            "estimated_delivery": fulfillment.get("estimated_delivery"),
            "items": [
                {
                    "title": item["title"],
                    "variant_title": item["variant_title"],
                    "quantity": item["quantity"],
                }
                for item in order["items"]
            ],
        }
        return _success(payload)

    def _search_products(
        self, arguments: dict[str, Any], connection: sqlite3.Connection
    ) -> dict[str, Any]:
        query = _require_string(arguments, "query", max_length=MAX_QUERY_LENGTH)
        products = db.search_products(connection, query)

        if not products:
            # An empty catalog search is a legitimate answer, not a failure:
            # the model should say "we do not carry that" and move on.
            return _success(
                {"query": query, "count": 0, "currency": CURRENCY, "products": []}
            )

        return _success(
            {
                "query": query,
                "count": len(products),
                "currency": CURRENCY,
                "products": products,
            }
        )

    def _create_support_ticket(
        self, arguments: dict[str, Any], connection: sqlite3.Connection
    ) -> dict[str, Any]:
        email = _require_email(arguments)
        subject = _require_string(arguments, "subject", max_length=MAX_SUBJECT_LENGTH)
        description = _require_string(
            arguments, "description", max_length=MAX_DESCRIPTION_LENGTH
        )

        # subject and description are customer-authored text. They are stored
        # verbatim and never parsed, interpreted or executed: to this server
        # they are opaque data.
        ticket = db.create_support_ticket(connection, email, subject, description)
        return _success(ticket)
