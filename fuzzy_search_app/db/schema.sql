-- SQLite Schema based on MySQL reference schema
-- Tables: brands, categories, products, product_group, transactions, transaction_sell_lines

PRAGMA foreign_keys = ON;

-- ─── brands ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS brands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 0,
    name        TEXT    NOT NULL,
    description TEXT    DEFAULT NULL,
    created_by  INTEGER NOT NULL DEFAULT 0,
    deleted_at  TEXT    DEFAULT NULL,
    created_at  TEXT    DEFAULT NULL,
    updated_at  TEXT    DEFAULT NULL
);

-- ─── categories ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS categories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    business_id   INTEGER NOT NULL DEFAULT 0,
    short_code    TEXT    DEFAULT NULL,
    parent_id     INTEGER NOT NULL DEFAULT 0,
    created_by    INTEGER NOT NULL DEFAULT 0,
    category_type TEXT    DEFAULT NULL,
    description   TEXT    DEFAULT NULL,
    slug          TEXT    DEFAULT NULL,
    deleted_at    TEXT    DEFAULT NULL,
    created_at    TEXT    DEFAULT NULL,
    updated_at    TEXT    DEFAULT NULL
);

-- ─── product_group ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_group (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    DEFAULT NULL,
    created_by INTEGER DEFAULT NULL,
    created_at TEXT    DEFAULT NULL,
    updated_at TEXT    DEFAULT NULL
);

-- ─── products ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    item_code            TEXT    DEFAULT NULL,
    business_id          INTEGER NOT NULL DEFAULT 0,
    type                 TEXT    DEFAULT NULL,   -- single|variable|modifier|combo
    brand_id             INTEGER DEFAULT NULL REFERENCES brands(id),
    category_id          INTEGER DEFAULT NULL REFERENCES categories(id),
    sub_category_id      INTEGER DEFAULT NULL REFERENCES categories(id),
    sku                  TEXT    NOT NULL DEFAULT '',
    sku2                 TEXT    NOT NULL DEFAULT '',
    sku3                 TEXT    NOT NULL DEFAULT '',
    barcode_type         TEXT    DEFAULT 'C128',
    enable_stock         INTEGER NOT NULL DEFAULT 0,
    alert_quantity       REAL    DEFAULT NULL,
    weight               TEXT    DEFAULT NULL,
    image                TEXT    DEFAULT NULL,
    main_image           TEXT    DEFAULT NULL,
    product_description  TEXT    DEFAULT NULL,
    product_custom_field1 TEXT   DEFAULT NULL,
    product_custom_field2 TEXT   DEFAULT NULL,
    product_custom_field3 TEXT   DEFAULT NULL,
    product_custom_field4 TEXT   DEFAULT NULL,
    srp                  REAL    DEFAULT NULL,
    sales_price          REAL    DEFAULT NULL,
    is_inactive          INTEGER NOT NULL DEFAULT 0,
    not_for_selling      INTEGER NOT NULL DEFAULT 0,
    out_of_stock         INTEGER NOT NULL DEFAULT 0,
    aisle                INTEGER DEFAULT 0,
    rack                 INTEGER DEFAULT 0,
    shelf                INTEGER DEFAULT 0,
    bin                  INTEGER DEFAULT 0,
    qty_box              TEXT    DEFAULT NULL,
    case_qty             TEXT    DEFAULT NULL,
    master_case_qty      REAL    DEFAULT NULL,
    ml                   REAL    NOT NULL DEFAULT 0.0,
    product_group_id     INTEGER DEFAULT NULL REFERENCES product_group(id),
    group_variation_name TEXT    DEFAULT NULL,
    note                 TEXT    DEFAULT NULL,
    created_by           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    DEFAULT NULL,
    updated_at           TEXT    DEFAULT NULL,
    synced_at            TEXT    DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_name        ON products(name);
CREATE INDEX IF NOT EXISTS idx_products_brand_id    ON products(brand_id);
CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_sku         ON products(sku);

-- ─── transactions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id         INTEGER NOT NULL DEFAULT 0,
    location_id         INTEGER DEFAULT NULL,
    type                TEXT    DEFAULT NULL,
    sub_type            TEXT    DEFAULT NULL,
    status              TEXT    NOT NULL DEFAULT '',
    payment_status      TEXT    DEFAULT NULL,   -- paid|due|partial
    contact_id          INTEGER DEFAULT NULL,
    invoice_no          TEXT    DEFAULT NULL,
    ref_no              TEXT    DEFAULT NULL,
    transaction_date    TEXT    NOT NULL,
    total_before_tax    REAL    NOT NULL DEFAULT 0.0,
    tax_amount          REAL    NOT NULL DEFAULT 0.0,
    discount_type       TEXT    DEFAULT NULL,   -- fixed|percentage
    discount_amount     REAL    DEFAULT 0.0,
    shipping_charges    REAL    NOT NULL DEFAULT 0.0,
    final_total         REAL    NOT NULL DEFAULT 0.0,
    sub_total           REAL    DEFAULT NULL,
    item_qty            INTEGER DEFAULT NULL,
    total_qty           INTEGER DEFAULT NULL,
    additional_notes    TEXT    DEFAULT NULL,
    staff_note          TEXT    DEFAULT NULL,
    is_direct_sale      INTEGER NOT NULL DEFAULT 0,
    is_suspend          INTEGER NOT NULL DEFAULT 0,
    delivery_method     TEXT    DEFAULT NULL,
    delivery_date       TEXT    DEFAULT NULL,
    created_by          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    DEFAULT NULL,
    updated_at          TEXT    DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_transactions_type        ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_transactions_date        ON transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_transactions_contact     ON transactions(contact_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status      ON transactions(status);

-- ─── transaction_sell_lines ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transaction_sell_lines (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id            INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    product_id                INTEGER NOT NULL REFERENCES products(id)     ON DELETE CASCADE,
    variation_id              INTEGER NOT NULL DEFAULT 0,
    quantity                  REAL    NOT NULL DEFAULT 0.0,
    quantity_returned         REAL    NOT NULL DEFAULT 0.0,
    unit_price_before_discount REAL   NOT NULL DEFAULT 0.0,
    unit_price                REAL    DEFAULT NULL,
    line_discount_type        TEXT    DEFAULT NULL,
    line_discount_amount      REAL    NOT NULL DEFAULT 0.0,
    unit_price_inc_tax        REAL    DEFAULT NULL,
    item_tax                  REAL    NOT NULL DEFAULT 0.0,
    tax_id                    INTEGER DEFAULT NULL,
    sell_line_note            TEXT    DEFAULT NULL,
    purchase_price            REAL    DEFAULT NULL,
    out_of_stock              INTEGER NOT NULL DEFAULT 0,
    is_picked                 INTEGER DEFAULT 0,
    is_packed                 INTEGER NOT NULL DEFAULT 0,
    created_at                TEXT    DEFAULT NULL,
    updated_at                TEXT    DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_tsl_transaction ON transaction_sell_lines(transaction_id);
CREATE INDEX IF NOT EXISTS idx_tsl_product     ON transaction_sell_lines(product_id);

-- ─── sync_log (tracks MySQL → SQLite sync state) ─────────────────────────────
CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT    NOT NULL,
    last_synced TEXT    DEFAULT NULL,
    records_synced INTEGER DEFAULT 0,
    status      TEXT    DEFAULT 'pending',
    error_msg   TEXT    DEFAULT NULL,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);
