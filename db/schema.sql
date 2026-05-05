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
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name     TEXT    NOT NULL,
    last_synced    TEXT    DEFAULT NULL,
    records_synced INTEGER DEFAULT 0,
    status         TEXT    DEFAULT 'pending',
    error_msg      TEXT    DEFAULT NULL,
    created_at     TEXT    DEFAULT CURRENT_TIMESTAMP
);

-- ─── search_history (analytics: every search query logged) ───────────────────
--
-- Column notes:
--   result_count    : raw count returned for this specific search event
--   is_zero_result  : 1 if result_count = 0, else 0  (indexed for fast filtering)
--   search_count    : cumulative counter — incremented each time the same
--                     normalised query is searched again.  Starts at 1.
--                     Allows trending queries to be identified without a
--                     full GROUP BY scan on every request.
--   last_searched   : timestamp of the most recent search for this query,
--                     updated on every repeat.  Used for trending (24h window).
--   top_score       : fuzzy score of the best result (0–100).  0.0 when no
--                     results.  Used by the synonym suggester to detect queries
--                     that returned results but with low confidence — these are
--                     better synonym candidates than zero-result queries alone.
CREATE TABLE IF NOT EXISTS search_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT    NOT NULL,
    result_count    INTEGER NOT NULL DEFAULT 0,
    timestamp       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_zero_result  INTEGER NOT NULL DEFAULT 0,   -- BOOLEAN: 1 = zero results
    search_count    INTEGER NOT NULL DEFAULT 1,   -- cumulative repeat counter
    last_searched   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    top_score       REAL    NOT NULL DEFAULT 0.0  -- best result score (0–100)
);

CREATE INDEX IF NOT EXISTS idx_search_history_query          ON search_history(query);
CREATE INDEX IF NOT EXISTS idx_search_history_timestamp      ON search_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_search_history_zero_result    ON search_history(is_zero_result);
CREATE INDEX IF NOT EXISTS idx_search_history_last_searched  ON search_history(last_searched);

-- ─── synonyms (user-managed variant → canonical mappings) ────────────────────
--
-- Replaces the hardcoded SYNONYMS dict in modules/fuzzy_search.py.
-- Loaded into memory at startup and reloaded after any API mutation.
--
-- variant   : the misspelling / alternate term the user types  (e.g. "hooka")
-- canonical : the correct product term to search for           (e.g. "hookah")
-- created_at: when the mapping was added
--
-- UNIQUE(variant) ensures each misspelling maps to exactly one canonical form.
CREATE TABLE IF NOT EXISTS synonyms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    variant    TEXT    NOT NULL,
    canonical  TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(variant)
);

CREATE INDEX IF NOT EXISTS idx_synonyms_variant   ON synonyms(variant);
CREATE INDEX IF NOT EXISTS idx_synonyms_canonical ON synonyms(canonical);

-- ─── synonym_suggestions (AI-generated candidates awaiting admin review) ──────
--
-- The suggester compares low-result search queries against product keywords
-- using RapidFuzz.  Matches in the 60–85 score band are stored here as
-- "pending" candidates.  An admin reviews them via the API and either
-- approves (→ copied to synonyms table) or rejects them.
--
-- status values:
--   pending  — awaiting admin review
--   approved — moved to synonyms table; synonym is now active
--   rejected — admin dismissed this suggestion
--
-- UNIQUE(variant) prevents the same misspelling from being suggested twice.
CREATE TABLE IF NOT EXISTS synonym_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    variant    TEXT    NOT NULL,
    canonical  TEXT    NOT NULL,
    score      REAL    NOT NULL,          -- RapidFuzz WRatio score (0–100)
    status     TEXT    NOT NULL DEFAULT 'pending',
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(variant)
);

CREATE INDEX IF NOT EXISTS idx_syn_sug_status    ON synonym_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_syn_sug_variant   ON synonym_suggestions(variant);
CREATE INDEX IF NOT EXISTS idx_syn_sug_score     ON synonym_suggestions(score);
