# Fuzzy Search App

Flask + SQLite + RapidFuzz product search engine with MySQL sync.

## Project Structure

```
fuzzy_search_app/
├── app.py                  # Flask app & routes
├── config.py               # All configuration (MySQL, SQLite, search settings)
├── seed_demo.py            # Insert sample data (no MySQL needed)
├── requirements.txt
├── .env.example            # Copy to .env and fill in credentials
│
├── db/
│   ├── schema.sql          # SQLite table definitions (mirrors MySQL schema)
│   ├── database.py         # SQLite connection helper
│   └── local.db            # Created automatically on first run
│
├── modules/
│   ├── sync.py             # Module 1 — MySQL → SQLite sync
│   └── fuzzy_search.py     # Module 2 & 3 — search engine
│
└── templates/
    ├── base.html
    ├── index.html          # Search UI
    ├── product.html        # Product detail
    ├── sync.html           # Sync management UI
    ├── 404.html
    └── 500.html
```

## Quick Start

```bash
# 1. Install dependencies
pip install flask==3.0.3 rapidfuzz==3.9.3 pymysql==1.1.1 python-dotenv==1.0.1

# 2. Seed demo data (no MySQL needed)
python seed_demo.py

# 3. Run the app
python app.py
```

Open http://127.0.0.1:5000

## MySQL Sync

1. Copy `.env.example` to `.env` and fill in your MySQL credentials.
2. Go to http://127.0.0.1:5000/sync and click **Run Full Sync**.
3. Or call the API: `POST /api/sync`

## The 3 Algorithms

| Algorithm | Weight | Purpose |
|---|---|---|
| `token_set_ratio` | 0.5 | Word order irrelevant, partial overlap |
| `WRatio` | 0.3 | Typo tolerance |
| `partial_ratio` | 0.2 | Short query inside long product name |

Each query is scored against both the **normalised** and **raw** text; the higher score wins.

## API Endpoints

| Method | URL | Description |
|---|---|---|
| GET | `/api/search?q=hookah&k=20` | JSON search results |
| GET | `/api/product/<id>` | Product detail JSON |
| POST | `/api/sync` | Trigger MySQL → SQLite sync |
| GET | `/api/sync/status` | Sync log |
| POST | `/api/search/rebuild` | Rebuild in-memory index |
| GET | `/api/stats` | Engine & DB stats |

## Score Interpretation

| Score | Label | Colour |
|---|---|---|
| 70–100 | High | Green |
| 50–69 | Medium | Blue |
| 35–49 | Low | Yellow |
| < 35 | Discarded | — |
