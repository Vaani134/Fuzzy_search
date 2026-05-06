"""
modules/cache.py
----------------
Search result cache with Redis backend and automatic in-memory fallback.

Backend selection
-----------------
Redis is used when ALL of the following are true:
  1. redis-py is installed  (pip install redis)
  2. REDIS_URL is set in the environment / .env file
  3. The Redis server is reachable at startup

If any condition fails, the cache silently falls back to the pure-Python
in-memory implementation that was used before.  Callers see no difference —
the public API is identical in both modes.

Public API  (unchanged from the original in-memory version)
-----------------------------------------------------------
  SearchCache(ttl, max_size)
  SearchCache.make_key(query, filters, page, limit, sort) → str
  cache.get(key)          → Any | None
  cache.set(key, data)    → None
  cache.invalidate(key)   → None
  cache.clear()           → int   (number of entries removed)
  cache.purge_expired()   → int   (in-memory only; Redis TTL is native)
  cache.stats()           → dict
  cache.start_cleanup_thread(interval)  → None  (in-memory only)

Redis key layout
----------------
  {REDIS_KEY_PREFIX}{sha256_key}
  e.g.  fzsearch:a3f2c1d4e5b6...

  Values are JSON-serialised and stored with Redis native TTL (SETEX).
  No separate timestamp field is needed — Redis handles expiry natively.

Thread safety
-------------
  Redis backend  : redis-py is thread-safe by default.
  In-memory backend : protected by threading.Lock (unchanged).

Configuration (environment variables)
--------------------------------------
  REDIS_URL         redis://[password@]host:port/db  (default: "")
  REDIS_KEY_PREFIX  namespace prefix for all keys    (default: "fzsearch:")
"""

import hashlib
import json
import os
import sys
import threading
import time
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Optional Redis import ──────────────────────────────────────────────────────
try:
    import redis as _redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


# ── Shared key builder (used by both backends) ─────────────────────────────────

def _make_cache_key(
    query: str,
    filters: Optional[dict] = None,
    page: int = 1,
    limit: int = 20,
    sort: str = "score",
) -> str:
    """
    Build a deterministic 32-character SHA-256 hex key from search parameters.
    Safe to use as a Redis key or dict key regardless of query content.
    """
    payload = {
        "q":       query.strip().lower(),
        "filters": filters or {},
        "page":    page,
        "limit":   limit,
        "sort":    sort,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ══════════════════════════════════════════════════════════════════════════════
# Redis backend
# ══════════════════════════════════════════════════════════════════════════════

class _RedisCache:
    """
    Redis-backed cache.  Values are JSON-serialised; TTL is handled natively
    by Redis (SETEX), so no timestamp bookkeeping is needed.

    This class is only instantiated when Redis is confirmed reachable.
    All errors during get/set are caught and logged — a Redis failure never
    propagates to the caller.
    """

    def __init__(self, ttl: int, max_size: int, url: str, prefix: str):
        self._ttl    = ttl
        self._max_size = max_size   # Redis doesn't enforce this, but kept for stats
        self._prefix = prefix
        self._client = _redis_lib.from_url(
            url,
            decode_responses=True,   # all responses are str, not bytes
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        # Verify connectivity — raises if Redis is unreachable
        self._client.ping()
        print(f"[Cache] Redis backend connected: {url}")

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._client.get(self._full_key(key))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            print(f"[Cache] Redis GET error: {exc}")
            return None

    def set(self, key: str, data: Any) -> None:
        try:
            serialised = json.dumps(data, ensure_ascii=False)
            # SETEX stores the value with a native TTL — no timestamp needed
            self._client.setex(self._full_key(key), self._ttl, serialised)
        except Exception as exc:
            print(f"[Cache] Redis SET error: {exc}")

    def invalidate(self, key: str) -> None:
        try:
            self._client.delete(self._full_key(key))
        except Exception as exc:
            print(f"[Cache] Redis DEL error: {exc}")

    def clear(self) -> int:
        """
        Delete all keys matching the prefix pattern.
        Returns the number of keys deleted.
        """
        try:
            pattern = f"{self._prefix}*"
            keys    = self._client.keys(pattern)
            if keys:
                return self._client.delete(*keys)
            return 0
        except Exception as exc:
            print(f"[Cache] Redis CLEAR error: {exc}")
            return 0

    def purge_expired(self) -> int:
        """
        No-op for Redis — TTL expiry is handled natively by the server.
        Returns 0 to satisfy the interface.
        """
        return 0

    def stats(self) -> dict:
        try:
            pattern = f"{self._prefix}*"
            keys    = self._client.keys(pattern)
            count   = len(keys)
            info    = self._client.info("server")
            return {
                "backend":         "redis",
                "total_entries":   count,
                "live_entries":    count,   # Redis only keeps live keys
                "expired_entries": 0,       # expired keys are gone already
                "ttl_seconds":     self._ttl,
                "max_size":        self._max_size,
                "redis_version":   info.get("redis_version", "unknown"),
            }
        except Exception as exc:
            return {
                "backend":   "redis",
                "error":     str(exc),
                "ttl_seconds": self._ttl,
            }

    def start_cleanup_thread(self, interval: int = 120) -> None:
        """No-op for Redis — server handles expiry natively."""
        pass


# ══════════════════════════════════════════════════════════════════════════════
# In-memory backend  (unchanged from original implementation)
# ══════════════════════════════════════════════════════════════════════════════

class _InMemoryCache:
    """
    Pure-Python dict cache with TTL expiry and LRU eviction.
    Thread-safe via threading.Lock.
    """

    def __init__(self, ttl: int, max_size: int):
        self._ttl      = ttl
        self._max_size = max_size
        self._store: dict[str, dict] = {}
        self._lock  = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.time() - entry["timestamp"] > self._ttl:
                del self._store[key]
                return None
            return entry["data"]

    def set(self, key: str, data: Any) -> None:
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest = min(self._store, key=lambda k: self._store[k]["timestamp"])
                del self._store[oldest]
            self._store[key] = {"timestamp": time.time(), "data": data}

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> int:
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._store.items()
                       if now - v["timestamp"] > self._ttl]
            for k in expired:
                del self._store[k]
            return len(expired)

    def stats(self) -> dict:
        with self._lock:
            now   = time.time()
            total = len(self._store)
            live  = sum(1 for v in self._store.values()
                        if now - v["timestamp"] <= self._ttl)
            return {
                "backend":         "memory",
                "total_entries":   total,
                "live_entries":    live,
                "expired_entries": total - live,
                "ttl_seconds":     self._ttl,
                "max_size":        self._max_size,
            }

    def start_cleanup_thread(self, interval: int = 120) -> None:
        def _worker():
            while True:
                time.sleep(interval)
                removed = self.purge_expired()
                if removed:
                    print(f"[Cache] Purged {removed} expired in-memory entries.")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()


# ══════════════════════════════════════════════════════════════════════════════
# Public facade — identical interface regardless of backend
# ══════════════════════════════════════════════════════════════════════════════

class SearchCache:
    """
    Search result cache with Redis backend and automatic in-memory fallback.

    Instantiation tries Redis first.  If Redis is unavailable (not installed,
    REDIS_URL not set, or connection refused), it silently falls back to the
    in-memory implementation.  All callers use the same API either way.

    Parameters
    ----------
    ttl      : int   Time-to-live in seconds.  Default: 60.
    max_size : int   Max entries (in-memory only).  Default: 500.
    """

    def __init__(self, ttl: int = 60, max_size: int = 500):
        self._ttl      = ttl
        self._max_size = max_size
        self._backend  = self._init_backend(ttl, max_size)

    # ── Backend selection ──────────────────────────────────────────────────────

    @staticmethod
    def _init_backend(ttl: int, max_size: int):
        """
        Try to connect to Redis.  Fall back to in-memory on any failure.

        Failure modes handled gracefully:
          - redis-py not installed
          - REDIS_URL not set or empty
          - Redis server unreachable (connection refused, timeout)
          - Authentication failure
        """
        if not _REDIS_AVAILABLE:
            print("[Cache] redis-py not installed — using in-memory cache.")
            return _InMemoryCache(ttl, max_size)

        try:
            from config import REDIS_URL, REDIS_KEY_PREFIX
        except ImportError:
            REDIS_URL        = os.getenv("REDIS_URL", "")
            REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "fzsearch:")

        if not REDIS_URL or not REDIS_URL.strip():
            print("[Cache] REDIS_URL not set — using in-memory cache.")
            return _InMemoryCache(ttl, max_size)

        try:
            backend = _RedisCache(ttl, max_size, REDIS_URL.strip(), REDIS_KEY_PREFIX)
            return backend
        except Exception as exc:
            print(f"[Cache] Redis unavailable ({exc}) — falling back to in-memory cache.")
            return _InMemoryCache(ttl, max_size)

    # ── Public API (identical to original SearchCache) ─────────────────────────

    @staticmethod
    def make_key(
        query: str,
        filters: Optional[dict] = None,
        page: int = 1,
        limit: int = 20,
        sort: str = "score",
    ) -> str:
        """
        Build a deterministic cache key from search parameters.
        Static method — same behaviour regardless of backend.
        """
        return _make_cache_key(query, filters, page, limit, sort)

    def get(self, key: str) -> Optional[Any]:
        """Return cached data for *key*, or None if missing / expired."""
        return self._backend.get(key)

    def set(self, key: str, data: Any) -> None:
        """Store *data* under *key* with TTL."""
        self._backend.set(key, data)

    def invalidate(self, key: str) -> None:
        """Remove a specific key from the cache."""
        self._backend.invalidate(key)

    def clear(self) -> int:
        """Remove all entries.  Returns the number of entries cleared."""
        return self._backend.clear()

    def purge_expired(self) -> int:
        """
        Remove expired entries.  Returns the number removed.
        No-op for Redis (server handles expiry natively).
        """
        return self._backend.purge_expired()

    def stats(self) -> dict:
        """Return cache statistics including backend type."""
        return self._backend.stats()

    def start_cleanup_thread(self, interval: int = 120) -> None:
        """
        Start background cleanup thread (in-memory only).
        No-op for Redis.
        """
        self._backend.start_cleanup_thread(interval)

    @property
    def backend_name(self) -> str:
        """Return 'redis' or 'memory' — useful for health checks."""
        return "redis" if isinstance(self._backend, _RedisCache) else "memory"


# ── Module-level singleton ─────────────────────────────────────────────────────
# Shared across the entire Flask app.
# TTL = 60 s, max 500 entries (in-memory mode).
# Redis TTL is set per-key via SETEX.
search_cache = SearchCache(ttl=60, max_size=500)
search_cache.start_cleanup_thread(interval=120)
