-- Telegram Dropship Store MVP v1.1
-- SQLite schema. Apply once to an empty database via migration runner.
-- Enable foreign_keys on EACH new SQLite connection.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    age_confirmed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT,
    description TEXT NOT NULL DEFAULT '',
    price_rp INTEGER NOT NULL CHECK (price_rp >= 0),
    stock_qty INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    reserved_qty INTEGER NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    photo_file_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (stock_qty >= reserved_qty)
);

CREATE TABLE IF NOT EXISTS cart_items (
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    recipient_name TEXT NOT NULL,
    recipient_phone TEXT NOT NULL,
    address_line TEXT NOT NULL,
    subdistrict TEXT,
    city TEXT NOT NULL,
    province TEXT NOT NULL,
    postal_code TEXT,
    subtotal_rp INTEGER NOT NULL CHECK (subtotal_rp >= 0),
    shipping_fee_rp INTEGER NOT NULL DEFAULT 0 CHECK (shipping_fee_rp = 0),
    total_rp INTEGER NOT NULL CHECK (total_rp >= 0),
    shipping_cost_owner_rp INTEGER CHECK (shipping_cost_owner_rp >= 0),
    status TEXT NOT NULL CHECK (status IN (
        'AWAITING_PAYMENT','PAYMENT_REVIEW','PAID','PROCESSING',
        'SHIPPED','COMPLETED','EXPIRED','CANCELLED','ISSUE',
        'REFUND_PENDING','REFUNDED'
    )),
    payment_deadline TEXT NOT NULL,
    payment_verified_at TEXT,
    paid_at TEXT,
    courier TEXT,
    tracking_no TEXT,
    shipped_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (total_rp = subtotal_rp + shipping_fee_rp)
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    product_name TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price_rp INTEGER NOT NULL CHECK (unit_price_rp >= 0),
    supplier_cost_unit_rp INTEGER CHECK (supplier_cost_unit_rp >= 0),
    UNIQUE (order_id, product_id)
);

CREATE TABLE IF NOT EXISTS payment_proofs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    uploaded_by INTEGER NOT NULL REFERENCES users(telegram_id),
    telegram_file_id TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING','ACCEPTED','REJECTED')),
    reviewed_by INTEGER,
    reviewed_at TEXT,
    review_note TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    amount_rp INTEGER NOT NULL CHECK (amount_rp > 0),
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','SENT','FAILED')),
    refund_channel TEXT,
    recipient_masked TEXT,
    transfer_reference TEXT,
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    processed_by INTEGER
);

CREATE TABLE IF NOT EXISTS inventory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    order_id INTEGER REFERENCES orders(id),
    event_type TEXT NOT NULL CHECK (event_type IN ('RESERVE','RELEASE','SALE','ADJUST')),
    qty INTEGER NOT NULL CHECK (qty > 0),
    actor_user_id INTEGER,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_user_id INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER REFERENCES orders(id),
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_PROGRESS','CLOSED')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_user_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS report_runs (
    local_report_date TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_orders_user_created ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at);
CREATE INDEX IF NOT EXISTS idx_payment_proofs_order ON payment_proofs(order_id);
CREATE INDEX IF NOT EXISTS idx_notification_outbox_retry ON notification_outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, created_at);
CREATE INDEX IF NOT EXISTS idx_inventory_events_product ON inventory_events(product_id, created_at);

-- Implementation transaction pseudocode (do NOT paste as raw SQL):
-- BEGIN IMMEDIATE;
--   perform idempotency lookup; validate all items and address;
--   UPDATE products SET reserved_qty=reserved_qty+:qty
--   WHERE id=:product_id AND is_active=1 AND stock_qty-reserved_qty>=:qty;
--   ensure exactly one row updated for EVERY item;
--   insert order and snapshots; clear cart; insert inventory/order events;
-- COMMIT;
--
-- On admin VERIFIED payment: BEGIN IMMEDIATE; verify current status PAYMENT_REVIEW;
-- for each item: UPDATE products SET stock_qty=stock_qty-:qty, reserved_qty=reserved_qty-:qty
-- WHERE id=:product_id AND reserved_qty>=:qty; update order PAID; audit; COMMIT.
--
-- On expiry: BEGIN IMMEDIATE; update only if status AWAITING_PAYMENT and deadline passed;
-- for each item release reserved_qty once; set EXPIRED; audit; COMMIT.
--
-- Store timestamps in UTC ISO 8601 consistently and convert to Asia/Jakarta for UI/reports.