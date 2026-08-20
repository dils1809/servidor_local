"""Populate the VIBBO database with synthetic data.

Run it from the repository root::

    python data/seed.py

The script is deterministic: a fixed random seed and a fixed reference date
mean every run produces byte-identical data, on any machine. That matters for a
demo, where the order numbers quoted in the README have to be the ones actually
in the database. Pass ``--reference-date`` to shift every timestamp to a
different anchor day.

Every customer, order and product below is fabricated. There is no connection
to any real store, and no real personal data is involved.
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

# Guatemala City, where the fictional shop operates.
TIMEZONE_OFFSET = "-06:00"
CURRENCY = "GTQ"

FREE_SHIPPING_THRESHOLD = 300.00
SHIPPING_COST = 25.00

CARRIERS = ("Guatex", "Cargo Expreso", "Forza Delivery", "DHL Express")


# ---------------------------------------------------------------------------
# Fixed reference data
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

# (title, product_type, price, description, [(variant_title, inventory), ...])
PRODUCTS = [
    (
        "Sencha Verde Clásico",
        "Té verde",
        68.00,
        "Sencha japonés de primera cosecha, sabor herbáceo y fresco con final dulce.",
        [("50 g", 42), ("100 g", 18), ("250 g", 6)],
    ),
    (
        "Matcha Ceremonial Uji",
        "Té verde",
        245.00,
        "Matcha ceremonial molido en piedra, procedente de Uji. Textura sedosa y umami intenso.",
        [("30 g", 12), ("60 g", 0)],
    ),
    (
        "Earl Grey Bergamota",
        "Té negro",
        72.00,
        "Base de Ceylan con aceite natural de bergamota de Calabria. Nuestro más vendido.",
        [("50 g", 60), ("100 g", 34), ("250 g", 11), ("Bolsitas (20)", 25)],
    ),
    (
        "English Breakfast Reserva",
        "Té negro",
        65.00,
        "Mezcla robusta de Assam y Ceylan, pensada para tomar con leche.",
        [("100 g", 28), ("250 g", 9), ("Bolsitas (20)", 40)],
    ),
    (
        "Darjeeling Primera Cosecha",
        "Té negro",
        130.00,
        "Darjeeling first flush, ligero y floral, con el característico toque moscatel.",
        [("50 g", 15), ("100 g", 4)],
    ),
    (
        "Oolong Leche Taiwanés",
        "Té oolong",
        155.00,
        "Oolong de altura con notas cremosas naturales. Admite varias infusiones.",
        [("50 g", 20), ("100 g", 7)],
    ),
    (
        "Té Blanco Aguja de Plata",
        "Té blanco",
        198.00,
        "Bai Hao Yin Zhen, solo brotes. Delicado, dulce y muy poco astringente.",
        [("30 g", 9), ("50 g", 3)],
    ),
    (
        "Pu-erh Añejo 2015",
        "Té pu-erh",
        175.00,
        "Pu-erh fermentado y prensado en 2015. Cuerpo terroso y profundo.",
        [("100 g", 14), ("Torta 357 g", 2)],
    ),
    (
        "Rooibos Vainilla",
        "Infusión",
        58.00,
        "Rooibos sudafricano con vaina de vainilla. Sin cafeína, apto para la noche.",
        [("100 g", 45), ("250 g", 16), ("Bolsitas (20)", 30)],
    ),
    (
        "Manzanilla Dorada",
        "Infusión",
        45.00,
        "Flores enteras de manzanilla egipcia. Suave y ligeramente amielada.",
        [("50 g", 52), ("100 g", 24), ("Bolsitas (20)", 38)],
    ),
    (
        "Menta Marroquí",
        "Infusión",
        48.00,
        "Hierbabuena secada al sol, tradicional para preparar té a la menta.",
        [("50 g", 33), ("100 g", 19)],
    ),
    (
        "Chai Masala Especiado",
        "Mezcla",
        85.00,
        "Assam con cardamomo, canela, jengibre y clavo. Se prepara hervido con leche.",
        [("100 g", 26), ("250 g", 8), ("Bolsitas (20)", 22)],
    ),
    (
        "Jazmín Perlas de Dragón",
        "Té verde",
        142.00,
        "Hojas enrolladas a mano y perfumadas con flor de jazmín en cinco pasadas.",
        [("50 g", 17), ("100 g", 5)],
    ),
    (
        "Frutos Rojos del Bosque",
        "Infusión",
        62.00,
        "Hibisco, escaramujo, arándano y fresa. Se toma frío o caliente.",
        [("100 g", 41), ("250 g", 13), ("Bolsitas (20)", 27)],
    ),
    (
        "Jengibre y Cúrcuma",
        "Infusión",
        55.00,
        "Raíz de jengibre y cúrcuma con un toque de pimienta negra y limón.",
        [("100 g", 36), ("250 g", 10)],
    ),
]

# Order shapes, in the order they are created. Each entry is
# (financial_status, fulfillment_status, shipment_status or None).
# Roughly a third already delivered, a third in transit, a third not yet sent,
# plus a few edge cases the support chatbot has to handle gracefully.
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

# Pre-existing tickets, so the table is not empty before the tool writes to it.
SEED_TICKETS = [
    (
        "ana.morales@example.com",
        "El matcha llegó con el empaque abierto",
        "Recibí el pedido #1003 y la bolsa de matcha venía sin sello. Adjunto fotos.",
        "resolved",
        14,
    ),
    (
        "mfuentes@example.com",
        "Quiero cambiar la variante de mi pedido",
        "Pedí Earl Grey de 50 g pero necesito el de 250 g. El pedido aún no ha salido.",
        "closed",
        9,
    ),
    (
        "sofia.arriaga@example.com",
        "No me llegó el correo de confirmación",
        "Hice una compra ayer y no recibí ningún correo, aunque sí me apareció el cargo.",
        "open",
        2,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def timestamp(day: date, hour: int, minute: int) -> str:
    """Render an RFC 3339 timestamp, the format the Shopify API uses."""
    moment = datetime(day.year, day.month, day.day, hour, minute)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + TIMEZONE_OFFSET


def money(value: float) -> float:
    return round(value + 1e-9, 2)


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
    for title, product_type, price, description, variants in PRODUCTS:
        cursor = conn.execute(
            "INSERT INTO products (title, product_type, price, description) "
            "VALUES (?, ?, ?, ?)",
            (title, product_type, price, description),
        )
        product_id = int(cursor.lastrowid)
        variant_titles = []
        for variant_title, inventory in variants:
            conn.execute(
                "INSERT INTO variants (product_id, title, inventory_quantity) "
                "VALUES (?, ?, ?)",
                (product_id, variant_title, inventory),
            )
            variant_titles.append(variant_title)
        catalog.append(
            {"id": product_id, "price": price, "variants": variant_titles}
        )
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

        # Newer orders first in the list would be confusing; instead the list
        # runs oldest to newest, with delivered orders furthest in the past.
        days_ago = 75 - index * 3
        created_day = reference - timedelta(days=days_ago)
        created_at = timestamp(created_day, rng.randint(8, 19), rng.choice([5, 17, 23, 41, 58]))

        cancelled_at = None
        if fulfillment == "restocked":
            cancelled_day = created_day + timedelta(days=rng.randint(1, 3))
            cancelled_at = timestamp(cancelled_day, rng.randint(9, 17), 30)

        cursor = conn.execute(
            "INSERT INTO orders (order_number, customer_id, financial_status, "
            "fulfillment_status, created_at, cancelled_at, total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_number, customer_id, financial, fulfillment, created_at, cancelled_at, 0.0),
        )
        order_id = int(cursor.lastrowid)

        subtotal = 0.0
        for product in rng.sample(catalog, rng.randint(1, 3)):
            quantity = rng.randint(1, 3)
            variant_title = rng.choice(product["variants"])
            subtotal += product["price"] * quantity
            conn.execute(
                "INSERT INTO line_items (order_id, product_id, variant_title, "
                "quantity, price) VALUES (?, ?, ?, ?, ?)",
                (order_id, product["id"], variant_title, quantity, product["price"]),
            )

        shipping = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_COST
        conn.execute(
            "UPDATE orders SET total = ? WHERE id = ?",
            (money(subtotal + shipping), order_id),
        )

        if shipment is not None:
            shipped_day = created_day + timedelta(days=rng.randint(1, 3))
            estimated = shipped_day + timedelta(days=rng.randint(2, 6))
            conn.execute(
                "INSERT INTO fulfillments (order_id, tracking_company, "
                "tracking_number, estimated_delivery, shipped_at, shipment_status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    rng.choice(CARRIERS),
                    "VB%09d" % rng.randint(100000000, 999999999),
                    estimated.isoformat(),
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
        "SELECT o.order_number, c.email, o.financial_status, o.fulfillment_status "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "ORDER BY o.id LIMIT 3"
    ).fetchall()
    for order_number, email, financial, fulfillment in rows:
        print("  %-6s %-28s %s / %s" % (order_number, email, financial, fulfillment))


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
