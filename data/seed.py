"""Populate the VIBBO database with synthetic support data.

Run it from the repository root::

    python data/seed.py

What is real and what is not
----------------------------
The **catalog** mirrors the public VIBBO storefront (drinkvibbo.com), which
runs on Shopify: product titles, prices in USD, descriptions, SKUs, the
``Default Title`` variant name and the sold-out flags all come from the store's
public ``/products.json`` feed. Keeping them accurate is what lets the support
chatbot answer real questions about real products.

Everything that identifies a person or a transaction is **invented**: the eight
customers, their email addresses, the twenty-five orders, tracking numbers and
support tickets do not exist and never did. The exact on-hand unit counts are
invented too, because Shopify's public feed exposes only ``available:
true/false``; the sold-out products are seeded at zero to match it.

The script is deterministic: a fixed random seed and a fixed reference date
mean every run produces byte-identical data on any machine, so the order
numbers quoted in the README are always the ones in the database. Pass
``--reference-date`` to anchor the timeline on a different day.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.sql"
DEFAULT_DB_PATH = HERE / "vibbo.db"

RANDOM_SEED = 20260820
DEFAULT_REFERENCE_DATE = date(2026, 8, 20)

TIMEZONE_OFFSET = "-06:00"
CURRENCY = "USD"

# Carriers named in the published shipping policy. USPS is domestic only.
CARRIERS = ("UPS", "FedEx", "USPS")

# Published service levels, in days.
PROCESSING_DAYS = (1, 5)   # "processed and prepared for shipment within 1-5 business days"
DELIVERY_DAYS = (3, 7)     # "domestic orders arrive within 3-7 business days"

# Shopify's name for the single variant of a product that has no options.
DEFAULT_VARIANT_TITLE = "Default Title"


# ---------------------------------------------------------------------------
# Catalog -- taken from the public storefront
# ---------------------------------------------------------------------------
# (title, product_type, price, description, sku, on_hand)
#
# product_type is empty in the Shopify feed, so it is filled in here to give
# search_products something meaningful to match on. on_hand is synthetic: zero
# where the storefront reports the product sold out, an invented positive count
# otherwise.
PRODUCTS = [
    (
        "Energy & Hydration Functional Tea",
        "Functional tea",
        25.00,
        "This vibrant mix of cocoa husk, dried black lemon, and creamy coconut "
        "brings you a smooth lift, crafted for clarity, not chaos. "
        "Ingredients: Black tea, hibiscus, cocoa husk, pineapple pieces, "
        "coconut, raisins, dried black lemon.",
        "VBB-HIDRA-001",
        128,
    ),
    (
        "Detox Functional Tea",
        "Functional tea",
        25.00,
        "This crisp blend crafted with citrus and fragrant herbs gently renews "
        "from the inside out. Sip daily to boost your rhythm, clear the path, "
        "and unleash your inner power. "
        "Ingredients: Fennel, mint, ginger, coriander seeds, boldo, pineapple "
        "pieces.",
        "VBB-GUT-001",
        94,
    ),
    (
        "Calm Functional Tea",
        "Functional tea",
        25.00,
        "Packed with refreshing botanicals, this blend channels the Mayan "
        "secret to keeping your cool even on rush hours. Sip, sigh, and let "
        "peace sneak up on you. "
        "Ingredients: Rosemary, lavender, passionflower, lemon verbena, orange "
        "peel, ginger, cinnamon.",
        "VBB-CHILL-001",
        61,
    ),
    (
        "All Day Bundle Pack",
        "Bundle",
        60.00,
        "Your complete daily ritual. Rise with Energy, a vibrant black tea "
        "blend with cocoa husk, coconut, dried black lemon, hibiscus, "
        "pineapple, and raisins. Reset with Detox, an earthy, refreshing blend "
        "of fennel, mint, ginger, coriander, boldo, and pineapple. Rest with "
        "Calm, a botanical blend of rosemary, lavender, passionflower and "
        "lemon verbena.",
        None,
        37,
    ),
    (
        "Reset Bundle Assortment Box",
        "Bundle",
        41.00,
        "Assortment box with the three 30 gr. blends. "
        "Hydra Boost Natural Energy (30 gr.): Black tea, hibiscus, cocoa husk, "
        "pineapple pieces, coconut, elderberry, dried black lemon. "
        "Gut Bliss Natural Detox (30 gr.): Fennel, mint, ginger, coriander "
        "seeds, boldo, pineapple pieces. "
        "Chill Vibes Natural Relax (30 gr.): Rosemary, lavender, "
        "passionflower, lemon verbena, orange peel, ginger, cinnamon.",
        "VBB-BOX-001",
        0,  # sold out on the storefront
    ),
    (
        "Filter Bags",
        "Accessory",
        9.99,
        "Filter bag box with 100 biodegradable wood pulp sachets inside.",
        None,
        0,  # sold out on the storefront
    ),
]


# ---------------------------------------------------------------------------
# Invented people
# ---------------------------------------------------------------------------
CUSTOMERS = [
    ("ana.morales@example.com", "Ana", "Morales"),
    ("jrodas@example.com", "Javier", "Rodas"),
    ("clara.pineda@example.com", "Clara", "Pineda"),
    ("dmarroquin@example.com", "Diego", "Marroquín"),
    ("sofia.arriaga@example.com", "Sofía", "Arriaga"),
    ("l.castellanos@example.com", "Lucía", "Castellanos"),
    ("mfuentes@example.com", "Mario", "Fuentes"),
    ("renata.chavez@example.com", "Renata", "Chávez"),
]

# (financial_status, fulfillment_status, shipment_status or None)
ORDER_SHAPES = [
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "delivered"),
    ("paid", "fulfilled", "in_transit"),
    ("paid", "fulfilled", "in_transit"),
    ("paid", "fulfilled", "in_transit"),
    ("paid", "fulfilled", "in_transit"),
    ("paid", "fulfilled", "out_for_delivery"),
    ("paid", "fulfilled", "out_for_delivery"),
    ("paid", "fulfilled", "failure"),
    ("paid", "unfulfilled", None),
    ("paid", "unfulfilled", None),
    ("paid", "unfulfilled", None),
    ("paid", "unfulfilled", None),
    ("paid", "unfulfilled", None),
    ("pending", "unfulfilled", None),
    ("pending", "unfulfilled", None),
    ("paid", "partial", "in_transit"),
    ("voided", "restocked", None),
    ("refunded", "restocked", None),
    ("refunded", "restocked", None),
]

SEED_TICKETS = [
    (
        "ana.morales@example.com",
        "Box arrived crushed",
        "Order #1003 arrived with the outer box crushed. The pouches look "
        "fine but I wanted to report it.",
        "resolved",
        14,
    ),
    (
        "mfuentes@example.com",
        "When is the Reset Bundle back in stock?",
        "The Reset Bundle Assortment Box shows sold out. Can you let me know "
        "when it is available again?",
        "closed",
        9,
    ),
    (
        "sofia.arriaga@example.com",
        "No confirmation email received",
        "I placed an order yesterday and never got a confirmation email, "
        "although the charge did go through.",
        "open",
        2,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def timestamp(day: date, hour: int, minute: int) -> str:
    """Render an RFC 3339 timestamp, the format the Shopify API uses."""
    return datetime(day.year, day.month, day.day, hour, minute).strftime(
        "%Y-%m-%dT%H:%M:%S"
    ) + TIMEZONE_OFFSET


def money(value: float) -> float:
    return round(value + 1e-9, 2)


def tracking_number(rng: random.Random, carrier: str) -> str:
    """Build an invented tracking number that at least looks like the carrier's.

    The numbers are fabricated and track nothing, but each carrier uses a
    recognizable shape, so a customer reading one is not misled about which
    website to paste it into.
    """
    if carrier == "UPS":
        return "1Z" + "".join(rng.choice("0123456789") for _ in range(16))
    if carrier == "FedEx":
        return "".join(rng.choice("0123456789") for _ in range(12))
    return "9400" + "".join(rng.choice("0123456789") for _ in range(18))  # USPS


def shipping_timeline(
    rng: random.Random, reference: date, shipment_status: str
) -> tuple[date, date, date]:
    """Return (created, shipped, estimated_delivery) for a shipped order.

    The estimated delivery date is chosen *first*, relative to the reference
    day, and the earlier dates are derived backwards from it. Doing it in this
    order makes an incoherent timeline impossible to generate: an order still
    in transit cannot end up with a delivery estimate in the past, which is
    exactly what a naive "N days ago" spread produces.
    """
    if shipment_status == "delivered":
        estimated = reference - timedelta(days=rng.randint(12, 60))
    elif shipment_status == "in_transit":
        estimated = reference + timedelta(days=rng.randint(2, 6))
    elif shipment_status == "out_for_delivery":
        estimated = reference + timedelta(days=rng.randint(0, 1))
    else:  # failure: the attempt happened, the carrier will retry
        estimated = reference - timedelta(days=rng.randint(1, 3))

    shipped = estimated - timedelta(days=rng.randint(*DELIVERY_DAYS))
    created = shipped - timedelta(days=rng.randint(*PROCESSING_DAYS))
    return created, shipped, estimated


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def insert_customers(conn: sqlite3.Connection) -> list[int]:
    ids = []
    for email, first_name, last_name in CUSTOMERS:
        cursor = conn.execute(
            "INSERT INTO customers (email, first_name, last_name) VALUES (?, ?, ?)",
            (email, first_name, last_name),
        )
        ids.append(int(cursor.lastrowid))
    return ids


def insert_catalog(conn: sqlite3.Connection) -> list[dict]:
    catalog = []
    for title, product_type, price, description, sku, on_hand in PRODUCTS:
        cursor = conn.execute(
            "INSERT INTO products (title, product_type, price, description) "
            "VALUES (?, ?, ?, ?)",
            (title, product_type, price, description),
        )
        product_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO variants (product_id, title, sku, inventory_quantity) "
            "VALUES (?, ?, ?, ?)",
            (product_id, DEFAULT_VARIANT_TITLE, sku, on_hand),
        )
        catalog.append({"id": product_id, "price": price})
    return catalog


def insert_orders(
    conn: sqlite3.Connection,
    rng: random.Random,
    customer_ids: list[int],
    catalog: list[dict],
    reference: date,
) -> None:
    for index, (financial, fulfillment, shipment) in enumerate(ORDER_SHAPES):
        order_number = "#%d" % (1001 + index)
        customer_id = customer_ids[index % len(customer_ids)]

        cancelled_at = None
        if shipment is not None:
            created_day, shipped_day, estimated_day = shipping_timeline(
                rng, reference, shipment
            )
        elif fulfillment == "restocked":
            created_day = reference - timedelta(days=rng.randint(6, 25))
            shipped_day = estimated_day = None
        elif financial == "pending":
            # Payment not confirmed yet, so the order must be recent.
            created_day = reference - timedelta(days=rng.randint(0, 2))
            shipped_day = estimated_day = None
        else:
            # Paid but not shipped: still inside the published processing window.
            created_day = reference - timedelta(days=rng.randint(0, 4))
            shipped_day = estimated_day = None

        created_at = timestamp(
            created_day, rng.randint(8, 19), rng.choice([5, 17, 23, 41, 58])
        )
        if fulfillment == "restocked":
            cancelled_day = created_day + timedelta(days=rng.randint(1, 3))
            cancelled_at = timestamp(cancelled_day, rng.randint(9, 17), 30)

        cursor = conn.execute(
            "INSERT INTO orders (order_number, customer_id, financial_status, "
            "fulfillment_status, created_at, cancelled_at, total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_number, customer_id, financial, fulfillment, created_at,
             cancelled_at, 0.0),
        )
        order_id = int(cursor.lastrowid)

        subtotal = 0.0
        for product in rng.sample(catalog, rng.randint(1, 3)):
            quantity = rng.randint(1, 3)
            subtotal += product["price"] * quantity
            conn.execute(
                "INSERT INTO line_items (order_id, product_id, variant_title, "
                "quantity, price) VALUES (?, ?, ?, ?, ?)",
                (order_id, product["id"], DEFAULT_VARIANT_TITLE, quantity,
                 product["price"]),
            )

        # Shipping is quoted at checkout by weight and destination, so it is
        # not modelled here: the total is the merchandise subtotal.
        conn.execute(
            "UPDATE orders SET total = ? WHERE id = ?", (money(subtotal), order_id)
        )

        if shipment is not None:
            carrier = rng.choice(CARRIERS)
            conn.execute(
                "INSERT INTO fulfillments (order_id, tracking_company, "
                "tracking_number, estimated_delivery, shipped_at, shipment_status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    carrier,
                    tracking_number(rng, carrier),
                    estimated_day.isoformat(),
                    timestamp(shipped_day, rng.randint(9, 18), 0),
                    shipment,
                ),
            )


def insert_tickets(conn: sqlite3.Connection, reference: date) -> None:
    for email, subject, description, status, days_ago in SEED_TICKETS:
        created_at = timestamp(reference - timedelta(days=days_ago), 11, 15)
        conn.execute(
            "INSERT INTO support_tickets (email, subject, description, created_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, subject, description, created_at, status),
        )


def seed(db_path: Path, reference: date) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(RANDOM_SEED)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        customer_ids = insert_customers(conn)
        catalog = insert_catalog(conn)
        insert_orders(conn, rng, customer_ids, catalog, reference)
        insert_tickets(conn, reference)
        conn.commit()
        report(conn, db_path, reference)
    finally:
        conn.close()


def report(conn: sqlite3.Connection, db_path: Path, reference: date) -> None:
    tables = (
        "customers",
        "products",
        "variants",
        "orders",
        "line_items",
        "fulfillments",
        "support_tickets",
    )
    print("Seeded %s (reference date %s, currency %s)" % (db_path, reference, CURRENCY))
    for table in tables:
        count = conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
        print("  %-16s %3d rows" % (table, count))

    print("\nSample orders you can use to try the tools:")
    rows = conn.execute(
        """
        SELECT o.order_number, c.email, o.fulfillment_status, f.shipment_status
        FROM orders o
        JOIN customers c ON c.id = o.customer_id
        LEFT JOIN fulfillments f ON f.order_id = o.id
        WHERE o.id IN (1, 9, 15, 23)
        ORDER BY o.id
        """
    ).fetchall()
    for order_number, email, fulfillment, shipment in rows:
        print("  %-6s %-28s %-12s %s" % (order_number, email, fulfillment, shipment or "-"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create and populate the synthetic VIBBO database. "
        "Any existing database at the target path is dropped and rebuilt."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Where to write the SQLite file (default: %(default)s).",
    )
    parser.add_argument(
        "--reference-date",
        type=date.fromisoformat,
        default=DEFAULT_REFERENCE_DATE,
        help="Anchor day for every generated timestamp, as YYYY-MM-DD "
        "(default: %(default)s). Fixed by default so the seed is reproducible.",
    )
    args = parser.parse_args(argv)

    if args.database.exists():
        print("Rebuilding existing database at %s" % args.database)
    seed(args.database, args.reference_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
