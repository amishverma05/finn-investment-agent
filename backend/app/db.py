"""Tiny SQLite store: a key/value cache with expiry, plus user settings."""

import json
import logging
import sqlite3
import threading
import time
from typing import Any, Callable

from app.config import DATA_DIR

log = logging.getLogger("finn.cache")

_DB_PATH = DATA_DIR / "finn.db"
_lock = threading.Lock()
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
_conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL)")
_conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
_conn.commit()


def cache_get(key: str) -> Any | None:
    with _lock:
        row = _conn.execute("SELECT value, expires FROM cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    value, expires = row
    if expires and expires < time.time():
        return None
    return json.loads(value)


def cache_set(key: str, value: Any, ttl: float | None = None) -> None:
    expires = time.time() + ttl if ttl else None
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), expires),
        )
        _conn.commit()


def cached(key: str, ttl: float | None, fn: Callable[[], Any]) -> Any:
    """Return the cached value for key, or compute, store and return it.
    None results are not cached, so failures are retried next time."""
    hit = cache_get(key)
    if hit is not None:
        log.debug("cache hit %s", key[:100])
        return hit
    start = time.time()
    value = fn()
    log.info(
        "cache miss %s computed in %.2fs%s\n    ↳ %s",
        key[:100],
        time.time() - start,
        "" if value is not None else " (empty, not cached)",
        "First time for this data, so it was fetched fresh. Saved, so next time is instant."
        if value is not None
        else "Nothing came back, so nothing was saved. It will be tried again next time.",
    )
    if value is not None:
        cache_set(key, value, ttl)
    return value


def kv_get(key: str, default: Any = None) -> Any:
    with _lock:
        row = _conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def kv_set(key: str, value: Any) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        _conn.commit()
