-- B2B Marketplace database schema
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('supplier','buyer','admin')),
    phone           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    failed_logins   INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    is_featured     INTEGER NOT NULL DEFAULT 0,
    featured_until  TEXT,
    logo_url        TEXT,
    address         TEXT,
    trade_info      TEXT,
    business_description TEXT,
    delivery_areas  TEXT,
    payment_info    TEXT,
    business_type   TEXT,
    billing_address TEXT,
    tax_info        TEXT,
    kyc_document_url TEXT,
    kyc_status      TEXT NOT NULL DEFAULT 'unsubmitted'
                    CHECK (kyc_status IN ('unsubmitted','pending','approved','rejected')),
    kyc_note        TEXT,
    notify_new_order    INTEGER NOT NULL DEFAULT 1,
    notify_order_status INTEGER NOT NULL DEFAULT 1,
    notify_low_stock    INTEGER NOT NULL DEFAULT 1,
    notify_message      INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    parent_id       INTEGER REFERENCES categories(id) ON DELETE CASCADE
);
-- SQLite treats every NULL as distinct in a normal UNIQUE constraint, so a
-- plain UNIQUE(name, parent_id) would let duplicate top-level categories
-- slip in. Partial unique indexes handle top-level and sub-level separately.
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_top_unique
    ON categories(name) WHERE parent_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_sub_unique
    ON categories(name, parent_id) WHERE parent_id IS NOT NULL;

INSERT OR IGNORE INTO categories (name, parent_id) VALUES ('Agriculture', NULL);
INSERT OR IGNORE INTO categories (name, parent_id) VALUES ('Jute', NULL);
INSERT OR IGNORE INTO categories (name, parent_id) VALUES ('Apparels', NULL);
INSERT OR IGNORE INTO categories (name, parent_id) VALUES ('Shoes', NULL);
INSERT OR IGNORE INTO categories (name, parent_id) VALUES ('Fashion', NULL);

INSERT OR IGNORE INTO categories (name, parent_id)
    SELECT 'Men''s Clothing', id FROM categories WHERE name = 'Apparels' AND parent_id IS NULL;
INSERT OR IGNORE INTO categories (name, parent_id)
    SELECT 'Women''s Clothing', id FROM categories WHERE name = 'Apparels' AND parent_id IS NULL;

INSERT OR IGNORE INTO categories (name, parent_id)
    SELECT 'Men''s Shoes', id FROM categories WHERE name = 'Shoes' AND parent_id IS NULL;
INSERT OR IGNORE INTO categories (name, parent_id)
    SELECT 'Women''s Shoes', id FROM categories WHERE name = 'Shoes' AND parent_id IS NULL;

INSERT OR IGNORE INTO categories (name, parent_id)
    SELECT 'Men''s Fashion', id FROM categories WHERE name = 'Fashion' AND parent_id IS NULL;
INSERT OR IGNORE INTO categories (name, parent_id)
    SELECT 'Women''s Fashion', id FROM categories WHERE name = 'Fashion' AND parent_id IS NULL;

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    description     TEXT NOT NULL DEFAULT '',
    specification   TEXT,
    price           REAL NOT NULL CHECK (price >= 0),
    moq             INTEGER NOT NULL CHECK (moq >= 1),
    stock           INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    low_stock_threshold INTEGER NOT NULL DEFAULT 5,
    image_url       TEXT,
    delivery_method TEXT,
    delivery_charge REAL,
    delivery_areas  TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS product_colors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    image_url       TEXT
);

CREATE TABLE IF NOT EXISTS product_sizes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    label           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_price_tiers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    min_qty         INTEGER NOT NULL CHECK (min_qty >= 1),
    price           REAL NOT NULL CHECK (price >= 0)
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    supplier_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity        INTEGER NOT NULL CHECK (quantity >= 1),
    unit_price      REAL NOT NULL CHECK (unit_price >= 0),
    total_price     REAL NOT NULL CHECK (total_price >= 0),
    color_id        INTEGER REFERENCES product_colors(id) ON DELETE SET NULL,
    customization_note TEXT,
    recipient_name  TEXT,
    recipient_phone TEXT,
    recipient_email TEXT,
    recipient_address TEXT,
    courier_name    TEXT,
    tracking_number TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','confirmed','processing','ready_for_delivery','shipped','delivered','cancelled')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS order_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    size_label      TEXT NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity >= 1)
);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    amount          REAL NOT NULL CHECK (amount >= 0),
    method          TEXT NOT NULL CHECK (method IN ('bkash','nagad','card','sslcommerz')),
    commission_rate REAL NOT NULL DEFAULT 0.03,
    commission_amt  REAL NOT NULL DEFAULT 0,
    supplier_net    REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'escrow'
                    CHECK (status IN ('escrow','released','refunded')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    released_at     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE CASCADE,
    sender_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    is_read         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    buyer_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(product_id, buyer_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount          REAL NOT NULL CHECK (amount >= 0),
    method          TEXT NOT NULL CHECK (method IN ('bkash','nagad','card','sslcommerz')),
    days            INTEGER NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    method          TEXT NOT NULL CHECK (method IN ('bkash','nagad','card','sslcommerz')),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL
);

INSERT OR IGNORE INTO settings (key, value) VALUES ('commission_rate', '0.03');
INSERT OR IGNORE INTO settings (key, value) VALUES ('subscription_price', '500');
INSERT OR IGNORE INTO settings (key, value) VALUES ('subscription_days', '30');
INSERT OR IGNORE INTO settings (key, value) VALUES ('ad_price', '300');
INSERT OR IGNORE INTO settings (key, value) VALUES ('ad_days', '7');

CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    message         TEXT NOT NULL DEFAULT '',
    link            TEXT,
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS withdrawals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount          REAL NOT NULL CHECK (amount >= 0),
    method          TEXT NOT NULL,
    account_details TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','rejected')),
    note            TEXT,
    requested_at    TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at    TEXT
);

CREATE TABLE IF NOT EXISTS buyer_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_name    TEXT NOT NULL,
    category        TEXT,
    quantity        REAL NOT NULL CHECK (quantity > 0),
    unit            TEXT NOT NULL DEFAULT 'pcs',
    location         TEXT,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed','cancelled')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quotations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER NOT NULL REFERENCES buyer_requests(id) ON DELETE CASCADE,
    supplier_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id      INTEGER REFERENCES products(id) ON DELETE SET NULL,
    price           REAL NOT NULL CHECK (price >= 0),
    message         TEXT,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(request_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_type     TEXT NOT NULL CHECK (target_type IN ('product','supplier')),
    target_id       INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_orders_supplier ON orders(supplier_id);
CREATE INDEX IF NOT EXISTS idx_messages_order ON messages(order_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_supplier ON subscriptions(supplier_id);
CREATE INDEX IF NOT EXISTS idx_ads_user ON ads(user_id);
CREATE INDEX IF NOT EXISTS idx_ads_status ON ads(status);
CREATE INDEX IF NOT EXISTS idx_colors_product ON product_colors(product_id);
CREATE INDEX IF NOT EXISTS idx_sizes_product ON product_sizes(product_id);
CREATE INDEX IF NOT EXISTS idx_tiers_product ON product_price_tiers(product_id);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_withdrawals_supplier ON withdrawals(supplier_id);
CREATE INDEX IF NOT EXISTS idx_buyer_requests_buyer ON buyer_requests(buyer_id);
CREATE INDEX IF NOT EXISTS idx_buyer_requests_status ON buyer_requests(status);
CREATE INDEX IF NOT EXISTS idx_quotations_request ON quotations(request_id);
CREATE INDEX IF NOT EXISTS idx_quotations_supplier ON quotations(supplier_id);
CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);