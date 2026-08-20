-- VIBBO tea shop -- synthetic support database.
--
-- The table and column names mirror the Shopify Admin API so the server could
-- later be pointed at a real store without reshaping the domain layer. All the
-- data in it is fabricated; there is no connection to any production store.
--
-- Running this file drops every table first, which is what makes the seed
-- reproducible: the same script always yields the same database.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS support_tickets;
DROP TABLE IF EXISTS fulfillments;
DROP TABLE IF EXISTS line_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS variants;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

-- ---------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL
);

-- Order lookups match on the email, so it is worth indexing case-folded.
CREATE UNIQUE INDEX idx_customers_email_lower ON customers (lower(email));

-- ---------------------------------------------------------------------------
-- Catalog
-- ---------------------------------------------------------------------------
CREATE TABLE products (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    product_type TEXT NOT NULL,
    price        REAL NOT NULL CHECK (price >= 0),
    description  TEXT NOT NULL
);

CREATE TABLE variants (
    id                 INTEGER PRIMARY KEY,
    product_id         INTEGER NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    title              TEXT NOT NULL,
    inventory_quantity INTEGER NOT NULL CHECK (inventory_quantity >= 0)
);

CREATE INDEX idx_variants_product ON variants (product_id);

-- ---------------------------------------------------------------------------
-- Orders
-- ---------------------------------------------------------------------------
CREATE TABLE orders (
    id                INTEGER PRIMARY KEY,
    order_number      TEXT NOT NULL UNIQUE,
    customer_id       INTEGER NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    -- Shopify vocabulary, kept verbatim.
    financial_status  TEXT NOT NULL CHECK (
        financial_status IN ('pending', 'paid', 'partially_refunded', 'refunded', 'voided')
    ),
    fulfillment_status TEXT NOT NULL CHECK (
        fulfillment_status IN ('unfulfilled', 'partial', 'fulfilled', 'restocked')
    ),
    created_at        TEXT NOT NULL,
    -- Set only when the order was cancelled. Distinguishes a cancellation from
    -- an ordinary refund, which otherwise look identical in the two status
    -- columns above.
    cancelled_at      TEXT,
    total             REAL NOT NULL CHECK (total >= 0)
);

CREATE INDEX idx_orders_customer ON orders (customer_id);

CREATE TABLE line_items (
    id            INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products (id) ON DELETE RESTRICT,
    variant_title TEXT NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    price         REAL NOT NULL CHECK (price >= 0)
);

CREATE INDEX idx_line_items_order ON line_items (order_id);

CREATE TABLE fulfillments (
    id                 INTEGER PRIMARY KEY,
    order_id           INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    tracking_company   TEXT NOT NULL,
    tracking_number    TEXT NOT NULL,
    estimated_delivery TEXT NOT NULL,
    shipped_at         TEXT NOT NULL,
    -- Shopify's own field. Stored rather than derived from estimated_delivery
    -- so that a seeded order does not silently change state as time passes.
    shipment_status    TEXT NOT NULL CHECK (
        shipment_status IN ('in_transit', 'out_for_delivery', 'delivered', 'failure')
    )
);

CREATE INDEX idx_fulfillments_order ON fulfillments (order_id);

-- ---------------------------------------------------------------------------
-- Support
-- ---------------------------------------------------------------------------
-- The only table the server ever writes to.
CREATE TABLE support_tickets (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL,
    subject     TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'pending', 'resolved', 'closed')
    )
);

CREATE INDEX idx_support_tickets_email ON support_tickets (lower(email));
