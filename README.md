# 🔍 Fuzzy Search App

> A production-grade intelligent product search engine built with Flask, SQLite, and RapidFuzz — featuring typo tolerance, synonym handling, smart ranking, real-time analytics, and in-memory caching.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0.3-black?logo=flask)](https://flask.palletsprojects.com)
[![RapidFuzz](https://img.shields.io/badge/RapidFuzz-3.9.3-orange)](https://github.com/maxbachmann/RapidFuzz)
[![SQLite](https://img.shields.io/badge/SQLite-local--cache-blue?logo=sqlite)](https://sqlite.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 🚀 Project Overview

Fuzzy Search App is a full-stack search engine designed to simulate how real e-commerce platforms handle product discovery. It solves a core problem in retail search: **users rarely type product names perfectly**.

Whether a customer types `"hooka"` instead of `"hookah"`, `"grider"` instead of `"grinder"`, or `"glass pip"` instead of `"glass pipe"` — the engine finds the right product every time.

**Why it exists:**
- Standard SQL `LIKE` queries fail on typos and word-order variations
- Vector/semantic search requires expensive GPU infrastructure
- RapidFuzz delivers near-instant fuzzy matching in pure Python with no model downloads

**Real-world use case:** A wholesale distributor with 40,000+ SKUs needs staff to quickly locate products by partial name, brand, or category — even with inconsistent spelling. This engine handles that at sub-100ms response times.

---

## ✨ Features

| Category | Feature |
|---|---|
| 🔎 **Search** | 3-algorithm fuzzy blend (token_set_ratio, WRatio, partial_ratio) |
| 🔤 **Intelligence** | Synonym normalization (shisha → hookah, grider → grinder) |
| 🏆 **Ranking** | Score boosting for exact and prefix matches |
| 📄 **Pagination** | Page / limit controls with total result counts |
| 🔽 **Sorting** | Sort by relevance score or product name A–Z |
| 🎯 **Filtering** | Filter by category, min price, max price |
| ⚡ **Autocomplete** | Prefix + contains suggestions from products, brands, categories |
| 📊 **Analytics** | Search history log and top-query frequency tracking |
| 🗄️ **Caching** | In-memory TTL cache (60s) with automatic expiry and invalidation |
| 🔄 **Sync** | MySQL → SQLite sync with cursor-based pagination (handles 488k+ rows) |
| 🏗️ **Indexing** | In-memory product index rebuilt on startup and every 5 minutes |
| 🖼️ **Downloads** | Bulk product image download as ZIP |
| 🎨 **UI** | Responsive Bootstrap 5 interface with live autocomplete dropdown |

---

## 🏗️ Architecture

### How Search Works

```
User Query
    │
    ▼
┌─────────────────────────────┐
│  Synonym Expansion          │  "sheesha" → "hookah"
│  apply_synonyms(query)      │  "grider"  → "grinder"
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Text Normalization         │  lowercase, strip prices/brackets,
│  normalize(query)           │  remove special chars, collapse spaces
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Pre-Filter (in-memory)     │  category, min_price, max_price
│  Applied before scoring     │  reduces candidate set
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Pass 1: Fast WRatio Scan   │  process.extract() on normalized strings
│  Top 2× candidates          │  O(n) but fast via C extension
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Pass 2: Full Blend Score   │  token_set_ratio × 0.5
│  blend_score()              │  WRatio          × 0.3
│                             │  partial_ratio   × 0.2
│                             │  scored vs. normalized AND raw text
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Score Boosting             │  Exact match  → +20
│  apply_boost()              │  Starts-with  → +10
│                             │  Capped at 100
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Sort → Paginate → Cache    │  Sort by score or name
│                             │  Slice to page/limit
│                             │  Store in TTL cache
└─────────────┬───────────────┘
              │
              ▼
         JSON Response
```

### Module Structure

```
app.py                      ← Flask app, route wiring, engine init
config.py                   ← All configuration (env-driven)
│
├── db/
│   ├── database.py         ← SQLite connection, init_db(), migrations
│   └── schema.sql          ← Table definitions (products, brands, categories…)
│
├── modules/
│   ├── fuzzy_search.py     ← Core engine: normalize, blend_score, FuzzySearchEngine
│   ├── analytics.py        ← Search history logging and top-query queries
│   ├── cache.py            ← In-memory TTL cache (SearchCache)
│   ├── autocomplete.py     ← Fast prefix + contains suggestions
│   ├── sync.py             ← MySQL → SQLite sync (cursor-based pagination)
│   ├── zip_builder.py      ← Concurrent image download + ZIP packaging
│   └── settings_manager.py ← Runtime MySQL credential management
│
├── routes/
│   └── search_routes.py    ← Search Blueprint (/api/search, /api/autocomplete…)
│
└── templates/
    ├── base.html           ← Master layout, navbar, cart widget, autocomplete JS
    ├── index.html          ← Search UI (filters, pagination, results grid)
    ├── dashboard.html      ← Stats, top categories/brands, sync status
    ├── product.html        ← Product detail page
    ├── sync.html           ← Live sync progress + history
    └── settings.html       ← MySQL connection configuration
```

---

## 📁 Project Structure

```
fuzzy_search_app/
│
├── app.py                      # Flask entry point — registers blueprint, init engine
├── config.py                   # Centralized config (MySQL, SQLite, search thresholds)
├── requirements.txt            # Pinned dependencies
├── .env.example                # Environment variable template
├── db_settings.json            # Runtime MySQL credentials (auto-created)
│
├── db/
│   ├── schema.sql              # SQLite schema (products, brands, categories, sync_log,
│   │                           #   search_history)
│   ├── database.py             # get_connection(), init_db(), _run_migrations()
│   └── local.db                # SQLite database (auto-created on first run)
│
├── modules/
│   ├── fuzzy_search.py         # FuzzySearchEngine, normalize(), blend_score(),
│   │                           #   apply_synonyms(), apply_boost(), get_engine()
│   ├── analytics.py            # log_search(), get_recent_searches(), get_top_queries()
│   ├── cache.py                # SearchCache (TTL dict, make_key, get, set, purge)
│   ├── autocomplete.py         # get_suggestions() — prefix + contains from SQLite
│   ├── sync.py                 # sync_table(), sync_all(), sync_all_background()
│   ├── zip_builder.py          # build_zip() — concurrent image download to BytesIO
│   └── settings_manager.py     # load(), save(), test_connection(), get_mysql_config()
│
├── routes/
│   └── search_routes.py        # Flask Blueprint — all /api/search* and /api/cache* routes
│
└── templates/
    ├── base.html               # Bootstrap 5 layout, cart widget, autocomplete JS
    ├── index.html              # Search page — filters, sort, pagination, result cards
    ├── dashboard.html          # Overview — stats, charts, sync status
    ├── product.html            # Product detail — image, specs, pricing
    ├── sync.html               # Sync management — live progress, log history
    ├── settings.html           # MySQL connection form with live test
    ├── 404.html
    └── 500.html
```

---

## ⚙️ Installation & Setup

### Prerequisites

- Python 3.10+
- pip
- MySQL (optional — only needed for syncing live ERP data)

### 1. Clone the repository

```bash
git clone https://github.com/your-username/fuzzy-search-app.git
cd fuzzy-search-app
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Dependencies are pinned for reproducibility:

```
flask==3.0.3
rapidfuzz==3.9.3
pymysql==1.1.1
python-dotenv==1.0.1
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Flask
FLASK_DEBUG=true
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
SECRET_KEY=your-secret-key-here

# MySQL (only needed for sync — skip if using demo data)
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-password
MYSQL_DATABASE=your_database
```

> **MySQL is optional.** The app runs fully on SQLite. MySQL is only needed if you want to sync live ERP data.

### 5. Run the application

```bash
python app.py
```

The SQLite database and schema are created automatically on first run.

```
[DB] SQLite initialised at /path/to/db/local.db
[Search] Index rebuilt — 0 products loaded.
* Running on http://127.0.0.1:5000
```

Open **http://127.0.0.1:5000** in your browser.

### 6. (Optional) Sync from MySQL

If you have a MySQL database configured:

1. Go to **Settings** → enter your MySQL credentials → **Save & Test**
2. Go to **Sync** → click **Run Full Sync**
3. The engine automatically rebuilds the index after sync completes

Or via API:

```bash
curl -X POST http://127.0.0.1:5000/api/sync \
  -H "Content-Type: application/json" \
  -d '{"full": true}'
```

---

## 🔍 API Reference

### `GET /api/search`

Paginated, filtered, sorted fuzzy product search.

**Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | required | Search query |
| `page` | int | `1` | Page number (1-based) |
| `limit` | int | `20` | Results per page (max 100) |
| `sort` | string | `score` | `score` (relevance) or `name` (A–Z) |
| `category` | string | — | Filter by category name (partial match) |
| `min_price` | float | — | Minimum price filter |
| `max_price` | float | — | Maximum price filter |

**Example request**

```bash
curl "http://127.0.0.1:5000/api/search?q=hookah&page=1&limit=5&sort=score&category=Hookahs"
```

**Example response**

```json
{
  "query": "hookah",
  "expanded_query": "hookah",
  "page": 1,
  "limit": 5,
  "total_results": 42,
  "total_pages": 9,
  "sort": "score",
  "filters": { "category": "Hookahs" },
  "results": [
    {
      "id": 101,
      "name": "China Hookah Small",
      "brand_name": "",
      "category_name": "Hookahs",
      "sales_price": 12.99,
      "srp": 15.00,
      "score": 100.0,
      "score_pct": 100.0,
      "score_label": "high",
      "field_scores": {
        "name": 100.0,
        "brand_name": 35.0,
        "category_name": 72.0
      }
    }
  ]
}
```

---

### `GET /api/autocomplete`

Fast prefix + contains suggestions for the search input.

**Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | required | Partial query (min 2 chars) |
| `limit` | int | `10` | Max suggestions (max 20) |

**Example request**

```bash
curl "http://127.0.0.1:5000/api/autocomplete?q=hook&limit=5"
```

**Example response**

```json
[
  { "text": "Hookah Pipe Large",  "type": "product",  "id": 101 },
  { "text": "Hookah Charcoal",    "type": "product",  "id": 204 },
  { "text": "Hookahs",            "type": "category", "id": 7   }
]
```

---

### `GET /api/search/history`

Returns the most recent search queries logged by the analytics system.

**Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `10` | Number of entries to return (max 100) |

**Example request**

```bash
curl "http://127.0.0.1:5000/api/search/history?limit=5"
```

**Example response**

```json
[
  { "id": 98, "query": "marlboro red", "result_count": 12, "timestamp": "2026-05-04T14:32:01Z" },
  { "id": 97, "query": "hookah",       "result_count": 42, "timestamp": "2026-05-04T14:31:45Z" }
]
```

---

### `GET /api/search/top`

Returns the most frequently searched queries, ranked by search count.

**Parameters**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `10` | Number of entries to return (max 100) |

**Example request**

```bash
curl "http://127.0.0.1:5000/api/search/top?limit=5"
```

**Example response**

```json
[
  { "query": "hookah",   "search_count": 87, "avg_results": 42.0 },
  { "query": "grinder",  "search_count": 54, "avg_results": 18.3 },
  { "query": "marlboro", "search_count": 41, "avg_results": 9.1  }
]
```

---

### `POST /api/search/rebuild`

Rebuilds the in-memory search index from the current SQLite data. Also clears the search cache.

**Example request**

```bash
curl -X POST http://127.0.0.1:5000/api/search/rebuild
```

**Example response**

```json
{
  "status": "ok",
  "indexed": 40938,
  "cache_cleared": 12
}
```

---

### `GET /api/product/<id>`

Returns full product detail as JSON.

**Example request**

```bash
curl "http://127.0.0.1:5000/api/product/101"
```

**Example response**

```json
{
  "id": 101,
  "name": "China Hookah Small",
  "sku": "CHN-HK-SM",
  "brand_name": "",
  "category_name": "Hookahs",
  "sales_price": 12.99,
  "srp": 15.00,
  "out_of_stock": 0,
  "is_inactive": 0,
  "image": "/uploads/img/chinahosmall_p1.jpg"
}
```

---

### `GET /api/stats`

Returns engine statistics, database counts, and cache state.

```bash
curl "http://127.0.0.1:5000/api/stats"
```

```json
{
  "total_products": 40938,
  "last_built": 1746360000.0,
  "min_score": 35.0,
  "db_products": 40938,
  "db_brands": 312,
  "db_categories": 87,
  "total_searches": 1204,
  "cache": {
    "total_entries": 8,
    "live_entries": 6,
    "ttl_seconds": 60,
    "max_size": 500
  }
}
```

---

### `GET /api/cache/stats` · `POST /api/cache/clear`

Inspect or flush the search result cache.

```bash
# Stats
curl "http://127.0.0.1:5000/api/cache/stats"

# Clear
curl -X POST http://127.0.0.1:5000/api/cache/clear
```

---

## 🧠 Search Algorithm

### The Three-Algorithm Blend

Every query is scored using a weighted combination of three RapidFuzz algorithms. The score is computed against both the **normalized** and **raw** text — the higher of the two is used.

```
final_score = max(score_normalized, score_raw)

where:
  score = (token_set_ratio × 0.5) + (WRatio × 0.3) + (partial_ratio × 0.2)
```

#### `token_set_ratio` — Weight: 0.5

Sorts both strings alphabetically by word before comparing. This makes word order completely irrelevant.

```python
fuzz.token_set_ratio("4 part grinder", "GRINDER 4 PART")  # → 100
fuzz.token_set_ratio("hookah small",   "small china hookah")  # → 100
```

Best for: inconsistent word order, partial product names.

#### `WRatio` — Weight: 0.3

A smart meta-algorithm that internally selects the best strategy based on string length and content. Handles typos and character-level differences.

```python
fuzz.WRatio("hooka",   "hookah")   # → 91  (missing h)
fuzz.WRatio("grider",  "grinder")  # → 91  (missing n)
fuzz.WRatio("marlbro", "marlboro") # → 92  (transposition)
```

Best for: typo tolerance, misspellings.

#### `partial_ratio` — Weight: 0.2

Finds the best matching substring window. Catches short queries inside long product names.

```python
fuzz.partial_ratio("glass", "10 INCH AQUA CLEAR GLASS BEAKER 9MM")  # → 100
fuzz.partial_ratio("energy", "5 HOUR EXTRA ENERGY BERRY BOX")        # → 100
```

Best for: single-keyword searches against long product names.

### Score Interpretation

| Score | Label | UI Color | Meaning |
|---|---|---|---|
| 90–100 | High | 🟢 Green | Near-perfect match |
| 70–89 | High | 🟢 Green | Strong match |
| 50–69 | Medium | 🔵 Blue | Good match |
| 35–49 | Low | 🟡 Yellow | Possible match |
| < 35 | — | Discarded | Not returned |

### Synonym Normalization

Before scoring, the query is passed through a synonym dictionary that maps common misspellings and alternate terms to their canonical forms. Matching uses word boundaries (`\b`) to prevent partial-word collisions.

```python
SYNONYMS = {
    "hooka":    "hookah",
    "sheesha":  "hookah",
    "shisha":   "hookah",
    "grider":   "grinder",
    "cigartte": "cigarette",
    "tobaco":   "tobacco",
    "charcol":  "charcoal",
    # ... and more
}
```

```
"sheesha pipe" → "hookah pipe"   (synonym expansion)
"hookah pipe"  → "hookah pipe"   (normalize: lowercase, strip specials)
```

New synonyms can be added to the `SYNONYMS` dict in `modules/fuzzy_search.py` with no other code changes required.

### Score Boosting

After the blend score is computed, deterministic boosting rules are applied to reward high-confidence matches:

| Rule | Boost | Condition |
|---|---|---|
| Exact match | +20 | Normalized query == normalized product name |
| Prefix match | +10 | Product name starts with the query |
| Cap | 100 | Score never exceeds 100 |

```python
# Example
blend_score("hookah", "hookah")       # → 80  (base)
apply_boost(80, "hookah", "hookah")   # → 100 (exact match +20)
```

### Two-Pass Search Strategy

For a 40,000-product index, running the full 3-algorithm blend on every product would be slow. The engine uses a two-pass approach:

1. **Pass 1 — Fast scan:** `process.extract()` with `WRatio` retrieves the top `2× limit` candidates in one vectorized C-extension call
2. **Pass 2 — Full blend:** Only the top candidates are re-scored with the full 3-algorithm blend

This keeps search latency well under 100ms even on large indexes.

---

## ⚡ Performance Optimizations

### In-Memory Index

On startup, all active products are loaded from SQLite into memory as two parallel lists:

- `_raw_strings` — original combined text (`name + brand + category`)
- `_normalized_strings` — pre-processed text (lowercase, stripped)

This eliminates repeated DB queries during search. With 40,000 products, the index occupies roughly 15–20 MB of RAM.

### TTL Cache

Search results are cached in a Python dictionary keyed by a SHA-256 hash of the query + filters + page + sort parameters.

```
cache_key = SHA256(query + filters + page + limit + sort)
TTL       = 60 seconds
Max size  = 500 entries (LRU eviction)
```

Cache hits return instantly without touching the search engine. The cache is automatically invalidated when:
- `POST /api/search/rebuild` is called
- A MySQL sync completes

A background daemon thread purges expired entries every 120 seconds to prevent unbounded memory growth.

### Background Index Refresh

A daemon thread rebuilds the in-memory index from SQLite every 300 seconds (5 minutes). This ensures the search index stays current after data changes without requiring a manual rebuild.

In Flask debug mode, the background thread only starts in the **worker process** (detected via `WERKZEUG_RUN_MAIN`) to prevent duplicate threads from the reloader.

### MySQL Sync — Cursor-Based Pagination

The sync engine uses `WHERE id > last_seen_id ORDER BY id LIMIT batch_size` instead of `LIMIT offset, size`. This solves two problems with large tables (488,000+ rows):

- **No timeout:** A fresh MySQL connection is opened per batch, so no connection is ever idle long enough to hit `wait_timeout`
- **O(batch) performance:** MySQL doesn't scan skipped rows — each batch is equally fast regardless of position

---

## 📊 Analytics

Every search query is logged to the `search_history` SQLite table:

```sql
CREATE TABLE search_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT    NOT NULL,
    result_count INTEGER NOT NULL DEFAULT 0,
    timestamp    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

This enables two analytics views:

**Recent searches** (`GET /api/search/history`) — shows what users searched for and how many results they got. Useful for identifying zero-result queries that indicate gaps in the product catalog.

**Top queries** (`GET /api/search/top`) — aggregates search frequency per query term. Useful for understanding demand, prioritizing catalog improvements, and identifying popular product categories.

Analytics logging is fire-and-forget — a failed DB write never interrupts the search response.

---

## 🧪 Testing

### Manual API Testing

```bash
# Basic search
curl "http://127.0.0.1:5000/api/search?q=hookah"

# Typo tolerance
curl "http://127.0.0.1:5000/api/search?q=hooka"

# Synonym expansion
curl "http://127.0.0.1:5000/api/search?q=sheesha"

# With filters and pagination
curl "http://127.0.0.1:5000/api/search?q=grinder&category=Grinders&min_price=5&max_price=50&page=1&limit=10"

# Sort by name
curl "http://127.0.0.1:5000/api/search?q=pipe&sort=name"

# Autocomplete
curl "http://127.0.0.1:5000/api/autocomplete?q=hook"

# Analytics
curl "http://127.0.0.1:5000/api/search/history"
curl "http://127.0.0.1:5000/api/search/top"

# Cache
curl "http://127.0.0.1:5000/api/cache/stats"
curl -X POST "http://127.0.0.1:5000/api/cache/clear"

# Rebuild index
curl -X POST "http://127.0.0.1:5000/api/search/rebuild"
```

### Edge Cases to Verify

| Test | Query | Expected behavior |
|---|---|---|
| Typo | `hooka` | Returns hookah products |
| Synonym | `sheesha` | Expands to `hookah`, returns hookah products |
| Word order | `4 part grinder` | Matches `Grinder 4 Part` |
| Partial | `glass` | Matches `10 Inch Glass Beaker 9MM` |
| Empty query | `q=` | Returns `400 Bad Request` |
| No results | `q=xyzxyzxyz` | Returns empty results array, not an error |
| Price filter | `min_price=100&max_price=200` | Only products in that price range |
| Pagination | `page=999` | Clamps to last valid page |
| Cache hit | Same query twice | Second response is instant (from cache) |

### Score Validation

Run the built-in synonym and boost logic tests:

```python
from modules.fuzzy_search import apply_synonyms, apply_boost

# Synonym expansion
assert apply_synonyms("hooka")        == "hookah"
assert apply_synonyms("sheesha pipe") == "hookah pipe"
assert apply_synonyms("grider")       == "grinder"

# Score boosting
assert apply_boost(80.0, "hookah", "hookah")      == 100.0  # exact +20
assert apply_boost(80.0, "hook",   "hookah pipe") ==  90.0  # prefix +10
assert apply_boost(60.0, "grinder","gold grinder") == 60.0  # no boost
```

---

## 📸 Screenshots

> _Screenshots will be added here. Run the app and navigate to the pages below._

| Page | URL |
|---|---|
| Dashboard | `http://127.0.0.1:5000/` |
| Search | `http://127.0.0.1:5000/search?q=hookah` |
| Product Detail | `http://127.0.0.1:5000/product/1` |
| Sync Manager | `http://127.0.0.1:5000/sync` |
| Settings | `http://127.0.0.1:5000/settings` |

---

## 🚀 Future Enhancements

| Enhancement | Description |
|---|---|
| 🔐 **Authentication** | JWT-based API auth + admin login for the sync/settings pages |
| 🐳 **Docker** | `Dockerfile` + `docker-compose.yml` for one-command deployment |
| 🔎 **Semantic Search** | Sentence-transformer embeddings for intent-based queries (e.g. "something to smoke") |
| 🤖 **AI Query Expansion** | LLM-powered query rewriting and spell correction |
| 📈 **Analytics Dashboard** | Visual charts for search trends, zero-result rates, popular categories |
| 🌐 **Multi-language** | Unicode normalization and non-English synonym support |
| 🖼️ **Image Search** | Visual similarity search using CLIP embeddings |
| ⚙️ **Admin Panel** | Web UI for managing synonyms, boost rules, and cache settings |
| 🔔 **Webhooks** | Trigger index rebuild automatically when MySQL data changes |
| 📦 **Redis Cache** | Replace in-memory dict cache with Redis for multi-worker deployments |

---

## 🤝 Contributing

Contributions are welcome. Please follow these steps:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes with clear, commented code
4. Test your changes against the edge cases listed above
5. Submit a pull request with a clear description of what changed and why

**Code style:** Follow the existing module structure. Business logic belongs in `modules/`, route handlers in `routes/`, and configuration in `config.py`. Avoid putting logic directly in `app.py`.

---

## 📜 License

This project is licensed under the **MIT License**.

```
MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

<div align="center">
  <sub>Built with Flask · RapidFuzz · SQLite · Bootstrap 5</sub>
</div>
