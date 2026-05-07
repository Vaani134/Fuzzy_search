# 🔍 Fuzzy Search App

> A production-grade intelligent product search engine built with Flask, SQLite, and RapidFuzz — featuring typo tolerance, DB-backed synonyms, AI synonym suggestions, composite ranking, query expansion, image search, real-time analytics, Redis caching, and background indexing.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0.3-black?logo=flask)](https://flask.palletsprojects.com)
[![RapidFuzz](https://img.shields.io/badge/RapidFuzz-3.9.3-orange)](https://github.com/maxbachmann/RapidFuzz)
[![SQLite](https://img.shields.io/badge/SQLite-local--cache-blue?logo=sqlite)](https://sqlite.org)
[![Redis](https://img.shields.io/badge/Redis-optional-red?logo=redis)](https://redis.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## 🚀 Project Overview

Fuzzy Search App is a full-stack search engine designed to simulate how real e-commerce platforms handle product discovery. It solves a core problem in retail search: **users rarely type product names perfectly**.

Whether a customer types `"hooka"` instead of `"hookah"`, `"grider"` instead of `"grinder"`, or uploads a photo of a product — the engine finds the right result every time.

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
| 🔤 **Synonyms** | DB-backed synonym table — add/delete via API, live without restart |
| 🤖 **AI Suggestions** | Synonym suggester: detects weak queries → proposes corrections → admin approves |
| 💡 **Did You Mean** | Spelling correction banner for low-confidence queries |
| 🔀 **Query Expansion** | Intent-phrase mapping — "smoking stuff" → searches hookah, pipe, cigarette |
| 🏆 **Composite Ranking** | Relevance-first: fuzzy × 0.85 + popularity × 0.10 + click_rate × 0.05 |
| 🛡️ **Relevance Gate** | Products with fuzzy score < 70 excluded — popularity can't rescue irrelevant results |
| 👆 **Click Tracking** | `POST /api/product/<id>/click` feeds the ranking signal in real time |
| 📄 **Pagination** | Page / limit controls with total result counts |
| 🔽 **Sorting** | Sort by relevance score or product name A–Z |
| 🎯 **Filtering** | Filter by category, min price, max price |
| ⚡ **Autocomplete** | Prefix + contains suggestions from products, brands, categories |
| 📊 **Analytics** | Search history with top_score, trending queries, zero-result detection |
| 📈 **Dashboard Charts** | Chart.js visualisations: top queries, zero-result queries, 24h trending |
| 🗄️ **Caching** | Redis cache with automatic in-memory fallback (60s TTL) |
| 🖼️ **Image Search** | Upload an image → extract labels → fuzzy search (MobileNet or heuristic) |
| 🔄 **Sync** | MySQL → SQLite sync with cursor-based pagination (handles 488k+ rows) |
| 🏗️ **Indexing** | In-memory product index rebuilt on startup and every 5 minutes |
| 📦 **Downloads** | Bulk product image download as ZIP |
| 🎨 **UI** | Responsive Bootstrap 5 interface with live autocomplete and image search |

---

## 🏗️ Architecture

### How Search Works

```
User Query (text or image)
    │
    ├─[image]─► POST /api/image-search
    │               │ MobileNet / heuristic label extraction
    │               ▼ query_generated = "hookah pipe"
    │
    ▼
┌─────────────────────────────┐
│  Query Expansion            │  "smoking stuff" → ["hookah","pipe","cigarette"]
│  expand_query(query)        │  Original query always searched first
└─────────────┬───────────────┘
              │  (each expansion term goes through the pipeline below)
              ▼
┌─────────────────────────────┐
│  Synonym Expansion          │  "sheesha" → "hookah"  (DB-backed, hot-reload)
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
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Pass 1: Fast WRatio Scan   │  process.extract() — top 2× candidates
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Pass 2: Full Blend Score   │  token_set_ratio × 0.5
│  blend_score()              │  WRatio          × 0.3
│                             │  partial_ratio   × 0.2
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Relevance Gate             │  fuzzy_score < 70 → EXCLUDED
│  FUZZY_MIN_THRESHOLD = 70   │  Irrelevant products never appear
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Score Boosting             │  Exact match  → +20
│  apply_boost()              │  Starts-with  → +10
│                             │  Substring    → +10  (capped at 100)
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Composite Ranking          │  Tie band (gap ≤ 10):
│  _composite_score()         │    0.85×fuzzy + 0.10×popularity + 0.05×click
│                             │  Clear winner (gap > 10):
│                             │    fuzzy only (popularity ignored)
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Merge (multi-term)         │  Results from all expansion terms merged
│  best score per product     │  by product ID — each product appears once
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Sort → Paginate → Cache    │  Redis (if configured) or in-memory dict
└─────────────┬───────────────┘
              │
              ▼
         JSON Response  +  top_score logged to search_history
```

### Module Structure

```
app.py                       ← Flask app, blueprint registration, engine init
config.py                    ← All configuration (env-driven, incl. Redis)
│
├── db/
│   ├── database.py          ← SQLite connection, init_db(), migrations (v1–v6)
│   └── schema.sql           ← All table definitions
│
├── modules/
│   ├── fuzzy_search.py      ← Engine, normalize, blend_score, apply_synonyms (DB-backed),
│   │                           reload_synonyms(), get_query_suggestion()
│   ├── analytics.py         ← log_search(query, count, top_score), trending, zero-results
│   ├── cache.py             ← SearchCache: Redis backend + in-memory fallback
│   ├── synonym_suggester.py ← is_valid_query(), generate_suggestions(), approve/reject
│   ├── autocomplete.py      ← Fast prefix + contains suggestions
│   ├── image_search.py      ← Image → labels → query pipeline (MobileNet / heuristic)
│   ├── sync.py              ← MySQL → SQLite cursor-based sync
│   ├── zip_builder.py       ← Concurrent image download + ZIP
│   └── settings_manager.py ← Runtime MySQL credential management
│
├── routes/
│   ├── search_routes.py     ← /api/search*, /api/autocomplete, /api/cache/*
│   ├── synonym_routes.py    ← /api/synonyms*, /api/synonyms/suggest, approve, reject
│   └── image_search_routes.py ← /api/image-search
│
└── templates/
    ├── base.html            ← Bootstrap 5 layout, cart, autocomplete, image search JS
    ├── index.html           ← Search UI: filters, sort, pagination, Did You Mean, image preview
    ├── dashboard.html       ← Stats, charts, sync status, rebuild button
    ├── product.html         ← Product detail
    ├── sync.html            ← Live sync progress + history
    └── settings.html        ← MySQL connection form
```

---

## 📁 Project Structure

```
fuzzy_search_app/
│
├── app.py                       # Flask entry point
├── config.py                    # Centralized config (MySQL, SQLite, Redis, search)
├── requirements.txt             # Pinned dependencies (incl. redis==5.0.8)
├── .env.example                 # Environment variable template
├── db_settings.json             # Runtime MySQL credentials (auto-created)
│
├── db/
│   ├── schema.sql               # Full SQLite schema (6 migration versions)
│   ├── database.py              # get_connection(), init_db(), _run_migrations()
│   └── local.db                 # SQLite database (auto-created on first run)
│
├── modules/
│   ├── fuzzy_search.py          # Core search engine + DB-backed synonyms
│   ├── analytics.py             # Search event logging with top_score
│   ├── cache.py                 # Redis + in-memory fallback cache
│   ├── synonym_suggester.py     # AI synonym suggestion pipeline
│   ├── autocomplete.py          # Fast SQLite-backed suggestions
│   ├── image_search.py          # Image-to-search pipeline
│   ├── sync.py                  # MySQL → SQLite sync
│   ├── zip_builder.py           # Bulk image ZIP download
│   └── settings_manager.py     # MySQL credential management
│
├── routes/
│   ├── search_routes.py         # All search + cache API endpoints
│   ├── synonym_routes.py        # Synonym CRUD + AI suggestion endpoints
│   └── image_search_routes.py  # Image search endpoint
│
└── templates/                   # Bootstrap 5 Jinja2 templates
```

---

## ⚙️ Installation & Setup

### Prerequisites

- Python 3.10+
- pip
- MySQL (optional — only needed for syncing live ERP data)
- Redis (optional — falls back to in-memory cache automatically)

### 1. Clone the repository

```bash
git clone https://github.com/your-username/fuzzy-search-app.git
cd fuzzy-search-app
```

### 2. Create a virtual environment

```bash
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # macOS / Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

```
flask==3.0.3
rapidfuzz==3.9.3
pymysql==1.1.1
python-dotenv==1.0.1
redis==5.0.8
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Flask
FLASK_DEBUG=true
SECRET_KEY=your-secret-key-here

# MySQL (optional — skip if not syncing)
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-password
MYSQL_DATABASE=your_database

# Redis (optional — leave blank to use in-memory cache)
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX=fzsearch:
```

### 5. Run the application

```bash
python app.py
```

```
[DB] SQLite initialised at db/local.db
[DB] Synonyms table ready (24 defaults seeded if new).
[Synonyms] Loaded 24 synonym(s) from DB.
[Cache] REDIS_URL not set — using in-memory cache.   ← or: Redis backend connected
[Search] Index rebuilt — 40938 products loaded.
* Running on http://127.0.0.1:5000
```

---

## 🔍 API Reference

### Search

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/search` | Paginated fuzzy search with filters, sort, suggestion, expansion |
| `GET` | `/api/search/history` | Recent queries with top_score, search_count |
| `GET` | `/api/search/top` | Most-searched queries |
| `GET` | `/api/search/zero-results` | Queries that returned no results |
| `GET` | `/api/search/trending` | Trending queries (last N hours) |
| `POST` | `/api/search/rebuild` | Rebuild index + clear cache |
| `GET` | `/api/autocomplete` | Fast prefix/contains suggestions |
| `GET` | `/api/cache/stats` | Cache stats (backend: redis\|memory) |
| `POST` | `/api/cache/clear` | Flush cache |

**`GET /api/search` parameters:**

| Parameter | Default | Description |
|---|---|---|
| `q` | required | Search query |
| `page` | `1` | Page number |
| `limit` | `20` | Results per page (max 100) |
| `sort` | `score` | `score` or `name` |
| `category` | — | Filter by category |
| `min_price` | — | Minimum price |
| `max_price` | — | Maximum price |

**Response includes:** `query`, `expanded_query`, `suggestion` (Did You Mean), `page`, `total_results`, `total_pages`, `results[]`

**Each result includes:**

```json
{
  "id": 101,
  "name": "China Hookah Small",
  "score": 89.5,
  "score_pct": 89.5,
  "score_label": "high",
  "fuzzy_score": 100.0,
  "popularity_score": 60.0,
  "click_score": 40.0,
  "expanded_from": null
}
```

| Field | Description |
|---|---|
| `score` | Final composite ranking score (0–100) |
| `fuzzy_score` | Raw fuzzy relevance component |
| `popularity_score` | Normalised sales-volume signal (0–100) |
| `click_score` | Normalised click-through signal (0–100) |
| `expanded_from` | Which expansion term found this result (`null` = original query) |

---

### Synonyms

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/synonyms` | List all active synonyms |
| `POST` | `/api/synonyms/add` | Add `{variant, canonical}` — live immediately |
| `DELETE` | `/api/synonyms/<id>` | Remove a synonym |
| `POST` | `/api/synonyms/suggest` | Run AI suggester on weak queries |
| `GET` | `/api/synonyms/suggestions` | List pending suggestions |
| `GET` | `/api/synonyms/suggestions/all` | All suggestions (any status) |
| `POST` | `/api/synonyms/approve/<id>` | Approve → inserts into synonyms, reloads |
| `POST` | `/api/synonyms/reject/<id>` | Reject a suggestion |

**Add synonym example:**
```bash
curl -X POST http://127.0.0.1:5000/api/synonyms/add \
  -H "Content-Type: application/json" \
  -d '{"variant": "marlbro", "canonical": "marlboro"}'
# → 201 {"status":"ok","id":25,"synonyms_loaded":25}
# search("marlbro") now returns marlboro results immediately
```

**Run AI suggester:**
```bash
curl -X POST http://127.0.0.1:5000/api/synonyms/suggest
# → {"new_suggestions": 7, "suggestions": [{"variant":"grdiner","canonical":"grinder","score":85.7}]}
```

---

### Image Search

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/image-search` | Upload image → labels → fuzzy search |

**Request:** `multipart/form-data` with field `image` (or `file`), optional `top_k`

**Response:**
```json
{
  "query_generated": "hookah glass pipe",
  "labels": ["hookah", "glass"],
  "extractor_used": "heuristic",
  "result_count": 12,
  "results": [...]
}
```

---

### Other

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/product/<id>` | Product detail JSON |
| `POST` | `/api/product/<id>/click` | Record a click-through (feeds ranking signal) |
| `GET` | `/api/stats` | Engine + DB + cache stats (includes `backend` field) |
| `POST` | `/api/sync` | Trigger MySQL → SQLite sync |
| `GET` | `/api/sync/live` | Real-time sync progress |
| `POST` | `/api/download-zip` | Download product images as ZIP |

---

## 🧠 Search Algorithm

### Three-Algorithm Blend

```
score = (token_set_ratio × 0.5) + (WRatio × 0.3) + (partial_ratio × 0.2)
final = max(score_normalized, score_raw)
```

| Algorithm | Weight | Best for |
|---|---|---|
| `token_set_ratio` | 0.5 | Word order irrelevant, partial overlap |
| `WRatio` | 0.3 | Typo tolerance, character-level errors |
| `partial_ratio` | 0.2 | Short query inside long product name |

### Score Interpretation

| Score | Label | Meaning |
|---|---|---|
| 90–100 | 🟢 High | Near-perfect match |
| 70–89 | 🟢 High | Strong match |
| 50–69 | 🔵 Medium | Good match |
| 35–49 | 🟡 Low | Possible match |
| < 35 | — | Discarded |

### Score Boosting

Applied on top of the blend score before composite ranking:

| Rule | Boost | Condition |
|---|---|---|
| Exact match | +20 | Normalized query == normalized product name |
| Prefix match | +10 | Product name starts with the query |
| Substring match | +10 | Query appears anywhere in the raw product name |
| Cap | 100 | Score never exceeds 100 |

### Relevance-First Composite Ranking

The final score combines three signals, but **relevance always wins**:

```
Fuzzy threshold gate:  fuzzy_score < 70  →  product EXCLUDED entirely

Tie band (gap ≤ 10 points between competing products):
  final = 0.85 × fuzzy_score + 0.10 × popularity + 0.05 × click_rate

Clear winner (gap > 10 points):
  final = fuzzy_score   (popularity and clicks ignored)
```

**Why this matters:** A popular product with fuzzy score 60 cannot outrank a relevant product with fuzzy score 85. Popularity only breaks ties between equally relevant results.

| Signal | Source | Weight |
|---|---|---|
| `fuzzy_score` | RapidFuzz blend + boost | 0.85 |
| `popularity` | `transaction_sell_lines` count, normalised 0–100 | 0.10 |
| `click_rate` | `product_clicks` count, normalised 0–100 | 0.05 |

Signals are normalised at index rebuild time — the most-sold product gets 100, all others scale linearly. This means weights are always meaningful regardless of catalog size.

### Query Expansion

Before scoring, the query is checked against `QUERY_EXPANSIONS` — a domain-specific intent map:

```python
"smoking stuff"  → ["hookah", "pipe", "cigarette", "tobacco", "cigar"]
"vaping"         → ["vape", "e-cigarette"]
"rolling"        → ["rolling paper", "blunt wrap", "grinder"]
```

Each expansion term is searched independently. Results are merged by product ID — each product appears once, keeping its best score. The original query is always searched first; expansions are additive.

```bash
# "smoking stuff" returns hookah, pipe, cigarette, and tobacco products
curl "http://127.0.0.1:5000/api/search?q=smoking+stuff"
```

### DB-Backed Synonyms

Synonyms are stored in the `synonyms` SQLite table and loaded into a compiled regex at startup. Changes via the API take effect **immediately** without a server restart.

```
apply_synonyms("sheesha pipe") → "hookah pipe"
apply_synonyms("grider")       → "grinder"
```

The regex uses word boundaries (`\b`) and a single-pass alternation to prevent double-replacement bugs (e.g. `"sheesha"` → `"hookah"` never re-matches `"hooka"` inside the result).

### AI Synonym Suggester

The suggester analyses `search_history` for weak queries (`top_score < 70`) and matches them against product keywords using RapidFuzz. Candidates in the 70–88 score band are stored as `pending` suggestions for admin review.

**Quality filters applied before any fuzzy work:**
- `is_valid_query()` rejects garbage (too short, > 3 tokens, no meaningful tokens ≥ 4 chars)
- Token-level matching (not full-string) for multi-word queries — `"glass pip"` → token `"pip"` → `"pipe"`
- Circular mapping detection (A→B and B→A blocked)
- No duplicate variants

**Why `top_score` instead of `result_count`:**
Fuzzy search always returns results — `"grdiner"` returns grinder products at score 62. `result_count` alone can't distinguish this from a correct query. `top_score` captures match confidence, not just result existence.

---

## ⚡ Performance

### Redis Cache

```
cache_key = SHA256(query + filters + page + limit + sort)
TTL       = 60 seconds
Backend   = Redis (if REDIS_URL set) or in-memory dict (fallback)
```

The cache backend is selected at startup and is transparent to all callers. `GET /api/cache/stats` shows `"backend": "redis"` or `"backend": "memory"`. The cache is automatically invalidated after index rebuilds and MySQL syncs.

### In-Memory Index

40,938 products → ~56 MB RAM. Rebuilt every 300s by a background thread. The two-pass search strategy (fast WRatio scan → full blend re-score) keeps latency under 100ms.

At rebuild time, two ranking signals are loaded and normalised to 0–100:
- **popularity** — count of `transaction_sell_lines` per product (real sales data)
- **click_rate** — count from `product_clicks` table (incremented via `POST /api/product/<id>/click`)

### Click Tracking

Every product detail page view fires a fire-and-forget POST to record the click:

```javascript
// Runs automatically on product.html page load
fetch(`/api/product/${pid}/click`, { method: "POST" }).catch(() => {});
```

The `product_clicks` table uses an upsert pattern — first click creates the row, subsequent clicks increment atomically. Click counts feed the composite ranking formula at the next index rebuild.

### MySQL Sync

Cursor-based pagination (`WHERE id > last_id`) instead of `LIMIT/OFFSET`. Fresh connection per batch prevents idle-timeout disconnects on 488k+ row tables.

---

## 📊 Analytics

`search_history` stores one row per unique query with:

| Column | Description |
|---|---|
| `query` | Normalised query string |
| `result_count` | Results returned |
| `top_score` | Best result's composite score (0–100) |
| `is_zero_result` | 1 if no results |
| `search_count` | Cumulative search count |
| `last_searched` | Timestamp of most recent search |

`top_score` is the key signal for the synonym suggester — it distinguishes `"grdiner"` (score 62, weak) from `"grinder"` (score 95, strong) even though both return results.

### Dashboard Charts

The dashboard includes three live Chart.js visualisations, each fetching data from the analytics API on page load:

| Chart | API | Description |
|---|---|---|
| **Top Queries** | `GET /api/search/top` | Horizontal bar — most-searched terms |
| **Zero-Result Queries** | `GET /api/search/zero-results` | Horizontal bar — catalog gaps (red) |
| **Trending (24h)** | `GET /api/search/trending` | Vertical bar — queries searched most in the last 24 hours |

Charts show empty-state messages when no data is available and display timestamps showing when data was last fetched.

---

## 🧪 Testing

### Manual API Testing

```bash
# ── Text search ────────────────────────────────────────────────────────────────
curl "http://127.0.0.1:5000/api/search?q=hookah"
curl "http://127.0.0.1:5000/api/search?q=hooka"          # typo → synonym
curl "http://127.0.0.1:5000/api/search?q=sheesha"        # synonym expansion
curl "http://127.0.0.1:5000/api/search?q=smoking+stuff"  # query expansion

# With filters and pagination
curl "http://127.0.0.1:5000/api/search?q=grinder&category=Grinders&min_price=5&max_price=50&page=1&limit=10"

# Sort by name
curl "http://127.0.0.1:5000/api/search?q=pipe&sort=name"

# ── Image search ───────────────────────────────────────────────────────────────
curl -X POST http://127.0.0.1:5000/api/image-search \
  -F "image=@hookah.jpg"

# ── Click tracking (feeds ranking signal) ─────────────────────────────────────
curl -X POST http://127.0.0.1:5000/api/product/101/click
# → {"status":"ok","product_id":101,"click_count":5}

# ── Synonyms ───────────────────────────────────────────────────────────────────
curl http://127.0.0.1:5000/api/synonyms
curl -X POST http://127.0.0.1:5000/api/synonyms/add \
  -H "Content-Type: application/json" \
  -d '{"variant":"marlbro","canonical":"marlboro"}'
curl -X POST http://127.0.0.1:5000/api/synonyms/suggest
curl http://127.0.0.1:5000/api/synonyms/suggestions
curl -X POST http://127.0.0.1:5000/api/synonyms/approve/1
curl -X POST http://127.0.0.1:5000/api/synonyms/reject/2

# ── Analytics ──────────────────────────────────────────────────────────────────
curl "http://127.0.0.1:5000/api/search/history"
curl "http://127.0.0.1:5000/api/search/top"
curl "http://127.0.0.1:5000/api/search/zero-results"
curl "http://127.0.0.1:5000/api/search/trending?hours=24"

# ── Cache ──────────────────────────────────────────────────────────────────────
curl "http://127.0.0.1:5000/api/cache/stats"
# → {"backend":"memory","total_entries":8,"live_entries":6,"ttl_seconds":60}
curl -X POST "http://127.0.0.1:5000/api/cache/clear"

# ── Stats ──────────────────────────────────────────────────────────────────────
curl "http://127.0.0.1:5000/api/stats"
# → includes "cache":{"backend":"redis"|"memory",...}

# ── Index rebuild ──────────────────────────────────────────────────────────────
curl -X POST "http://127.0.0.1:5000/api/search/rebuild"
# → {"status":"ok","indexed":40938,"cache_cleared":12}
```

### Ranking Validation

```bash
# Verify relevance-first ranking: "hookah pipe" should rank above "hookah charcoal"
# even if charcoal has more sales — they have the same fuzzy score so popularity
# breaks the tie, but a clearly more relevant product always wins.
curl "http://127.0.0.1:5000/api/search?q=hookah+pipe" | python -m json.tool

# Verify fuzzy threshold: products with score < 70 should not appear
# "Charcoal Natural" should NOT appear when searching "hookah"
curl "http://127.0.0.1:5000/api/search?q=hookah" | python -m json.tool
```

### Edge Cases to Verify

| Test | Query | Expected behavior |
|---|---|---|
| Typo | `hooka` | Returns hookah products (synonym) |
| Synonym | `sheesha` | Expands to `hookah`, returns hookah products |
| Expansion | `smoking stuff` | Returns hookah, pipe, cigarette, tobacco products |
| Word order | `4 part grinder` | Matches `Grinder 4 Part` |
| Partial | `glass` | Matches `10 Inch Glass Beaker 9MM` |
| Empty query | `q=` | Returns `400 Bad Request` |
| No results | `q=xyzxyzxyz` | Returns empty results array |
| Price filter | `min_price=100&max_price=200` | Only products in that range |
| Pagination | `page=999` | Clamps to last valid page |
| Cache hit | Same query twice | Second response is instant |
| Irrelevant popular | Popular product with low fuzzy score | Excluded by relevance gate |

---

## 🚀 Future Enhancements

| Enhancement | Description |
|---|---|
| 🔐 **Authentication** | JWT-based API auth + admin login for synonym management |
| 🐳 **Docker** | `Dockerfile` + `docker-compose.yml` with Redis service |
| 🔎 **Semantic Search** | Sentence-transformer embeddings for intent-based queries |
| 🤖 **LLM Query Rewriting** | GPT-powered query expansion beyond the static mapping dict |
| 🌐 **Multi-language** | Unicode normalization and non-English synonym support |
| 🔔 **Webhooks** | Auto-rebuild index when MySQL data changes |
| 📉 **Score Decay** | Time-decay on click signals so old clicks don't permanently boost products |
| 🧪 **A/B Testing** | Serve different ranking weights to different users and measure CTR |

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Follow the module structure: logic in `modules/`, routes in `routes/`, config in `config.py`
4. Submit a pull request with a clear description

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
  <sub>Built with Flask · RapidFuzz · SQLite · Bootstrap 5 · Redis (optional)</sub>
</div>
