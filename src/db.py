"""Data access for the VIBBO database.

The only module that writes SQL. Everything above it gets plain dicts, so
SQLite could be swapped for the real Shopify API without touching the tools.

Three security rules live here instead of in the tool layer, because a rule in
the data layer cannot be forgotten by a future caller:

1. There is no lookup by order number alone. find_order needs the email too,
   otherwise anyone could walk #1001, #1002, #1003 and read every order.
2. The schema has no address, phone or payment columns at all. A field that
   does not exist cannot leak.
3. Every query is parameterized, and LIKE wildcards in user text are escaped.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "vibbo.db"
DB_PATH_ENV_VAR = "VIBBO_DB_PATH"

SEARCH_RESULT_LIMIT = 10
LIKE_ESCAPE_CHAR = "\\"

# Same offset as the seeded data.
TIMEZONE_OFFSET = "-06:00"


class DatabaseNotFound(RuntimeError):
    """The SQLite file does not exist yet."""


def database_path() -> Path:
    override = os.environ.get(DB_PATH_ENV_VAR)
    return Path(override) if override else DEFAULT_DB_PATH


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    resolved = Path(path) if path is not None else database_path()
    if not resolved.exists():
        raise DatabaseNotFound(
            "No database at %s. Create it with: python data/seed.py" % resolved
        )
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    # SQLite disables foreign keys unless asked, once per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_order_number(value: str) -> str:
    """Accept 1001 or #1001 and return #1001.

    Customers copy the number from an email and usually drop the hash.
    """
    cleaned = value.strip()
    if cleaned.startswith("#"):
        return cleaned
    return "#" + cleaned.lstrip("#")


def escape_like(value: str) -> str:
    """Escape LIKE wildcards so searching for % does not match everything."""
    for char in (LIKE_ESCAPE_CHAR, "%", "_"):
        value = value.replace(char, LIKE_ESCAPE_CHAR + char)
    return value


def now_timestamp() -> str:
    """Current time in the same RFC 3339 format as the seeded rows."""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S") + (
        TIMEZONE_OFFSET
    )


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Orders (read only)
# ---------------------------------------------------------------------------
def find_order(
    conn: sqlite3.Connection, email: str, order_number: str
) -> dict[str, Any] | None:
    """Look up an order by email + order number. Both must match.

    Returns None when either one is wrong. The caller must not say which half
    failed, or it would confirm that an order number exists.
    """
    order_row = conn.execute(
        """
        SELECT o.id, o.order_number, o.financial_status, o.fulfillment_status,
               o.created_at, o.cancelled_at, o.total
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        WHERE o.order_number = ? AND lower(c.email) = lower(?)
        """,
        (normalize_order_number(order_number), email.strip()),
    ).fetchone()

    if order_row is None:
        return None

    order = dict(order_row)
    order_id = order.pop("id")

    order["items"] = _rows_to_dicts(
        conn.execute(
            """
            SELECT p.title, li.variant_title, li.quantity, li.price
            FROM line_items li
            JOIN products p ON p.id = li.product_id
            WHERE li.order_id = ?
            ORDER BY li.id
            """,
            (order_id,),
        ).fetchall()
    )

    fulfillment_row = conn.execute(
        """
        SELECT tracking_company, tracking_number, estimated_delivery,
               shipped_at, shipment_status
        FROM fulfillments
        WHERE order_id = ?
        ORDER BY id
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    order["fulfillment"] = dict(fulfillment_row) if fulfillment_row else None

    return order


# ---------------------------------------------------------------------------
# Catalog (read only)
# ---------------------------------------------------------------------------
def search_products(
    conn: sqlite3.Connection, query: str, limit: int = SEARCH_RESULT_LIMIT
) -> list[dict[str, Any]]:
    """Find products by title, type or description. Ingredients match too."""
    pattern = "%" + escape_like(query.strip()) + "%"
    rows = conn.execute(
        """
        SELECT id, title, product_type, price, description
        FROM products
        WHERE title LIKE ? ESCAPE ?
           OR product_type LIKE ? ESCAPE ?
           OR description LIKE ? ESCAPE ?
        ORDER BY title
        LIMIT ?
        """,
        (
            pattern,
            LIKE_ESCAPE_CHAR,
            pattern,
            LIKE_ESCAPE_CHAR,
            pattern,
            LIKE_ESCAPE_CHAR,
            limit,
        ),
    ).fetchall()

    products = []
    for row in rows:
        product = dict(row)
        product_id = product.pop("id")
        variants = conn.execute(
            """
            SELECT title, sku, inventory_quantity
            FROM variants
            WHERE product_id = ?
            ORDER BY id
            """,
            (product_id,),
        ).fetchall()
        product["variants"] = [
            {
                "title": variant["title"],
                "sku": variant["sku"],
                "inventory_quantity": variant["inventory_quantity"],
                "in_stock": variant["inventory_quantity"] > 0,
            }
            for variant in variants
        ]
        products.append(product)
    return products


# ---------------------------------------------------------------------------
# Support tickets (the only write)
# ---------------------------------------------------------------------------
def create_support_ticket(
    conn: sqlite3.Connection, email: str, subject: str, description: str
) -> dict[str, Any]:
    """Store a ticket and return its id.

    Subject and description are stored exactly as received. They are customer
    text: data, never instructions.
    """
    created_at = now_timestamp()
    cursor = conn.execute(
        """
        INSERT INTO support_tickets (email, subject, description, created_at, status)
        VALUES (?, ?, ?, ?, 'open')
        """,
        (email.strip(), subject, description, created_at),
    )
    conn.commit()
    ticket_id = int(cursor.lastrowid)
    logger.info("created support ticket %d", ticket_id)
    return {"ticket_id": ticket_id, "status": "open", "created_at": created_at}
