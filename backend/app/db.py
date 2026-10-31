"""Storage: a local SQLite file, plus an optional Postgres database (e.g. Neon) for what must
survive restarts and redeploys.

Without DATABASE_URL everything lives in backend/data/finn.db, as before.

With DATABASE_URL:
- Postgres keeps the durable things: portfolio and settings, the alerts register, push
  subscriptions and signing keys, saved filings, and expensive AI results (research reports,
  transcript notes, filing one-liners, results/management cards).
- SQLite (local, wiped on redeploy) keeps cheap caches (prices, NSE pages, screener pages)
  and the scheduler's bookkeeping, so the remote database isn't hit every few seconds.
- Durable cache reads check the local copy first and fall back to Postgres on a miss.
"""

import json
import logging
import re
import sqlite3
import threading
import time
from typing import Any, Callable

from app.config import DATA_DIR, settings

log = logging.getLogger("finn.cache")

_DB_PATH = DATA_DIR / "finn.db"
_lock = threading.Lock()
_conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
_conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL)")
_conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
_conn.commit()

# Cache entries worth keeping across redeploys (AI work, which costs time and tokens).
DURABLE_CACHE = ("research:", "callnotes:", "triage:", "insight:results:", "insight:mgmt:", "scr_id:")
# Settings that are just this process's bookkeeping, kept locally.
LOCAL_KV = ("job:", "scheduler_lock", "nse_blocked_until")


# ---------- Postgres (optional) ----------

_pg = None
_pg_lock = threading.Lock()


def _pg_conn():
    """Open (or reopen) the Postgres connection. None if not configured."""
    global _pg
    if not settings.database_url:
        return None
    if _pg is None or _pg.closed:
        import psycopg

        # prepare_threshold=None: Neon's pooled connections don't keep prepared statements
        _pg = psycopg.connect(settings.database_url, autocommit=True, connect_timeout=15, prepare_threshold=None)
        log.info("connected to Postgres (%s)", settings.database_url.split("@")[-1].split("/")[0])
    return _pg


def _to_pg(sql: str) -> str:
    """Translate the small SQLite dialect used in this app to Postgres."""
    s = sql.replace("?", "%s")
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)  # Postgres REAL is 4-byte: too coarse for timestamps
    m = re.match(r"\s*INSERT OR (IGNORE|REPLACE) INTO (\w+) \(([^)]*)\)(.*)", s, re.S | re.I)
    if m:
        kind, table, cols, rest = m.groups()
        names = [c.strip() for c in cols.split(",")]
        s = f"INSERT INTO {table} ({cols}){rest}"
        if kind.upper() == "IGNORE":
            s += " ON CONFLICT DO NOTHING"
        else:  # first column is the primary key in every table here
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in names[1:])
            s += f" ON CONFLICT ({names[0]}) DO UPDATE SET {updates}"
    return s


def _pg_run(sql: str, params: tuple = (), fetch: bool = False):
    """Run on Postgres, reconnecting once if the connection dropped (e.g. Neon woke up)."""
    import psycopg

    for attempt in (1, 2):
        try:
            with _pg_lock:
                cur = _pg_conn().execute(_to_pg(sql), params)
                return cur.fetchall() if fetch else cur.rowcount
        except psycopg.OperationalError as e:
            global _pg
            _pg = None
            if attempt == 2:
                raise
            log.warning("Postgres connection lost (%s), reconnecting", str(e)[:120])


def use_pg() -> bool:
    return bool(settings.database_url)


# ---------- Generic access for the app's own tables (alerts, push_subs, filings_store) ----------

def execute(sql: str, params: tuple = ()) -> int:
    """Run a write on the durable store; returns the number of rows changed."""
    if use_pg():
        return _pg_run(sql, params)
    with _lock:
        cur = _conn.execute(sql, params)
        _conn.commit()
        return cur.rowcount


def query(sql: str, params: tuple = ()) -> list[tuple]:
    if use_pg():
        return [tuple(r) for r in _pg_run(sql, params, fetch=True)]
    with _lock:
        return _conn.execute(sql, params).fetchall()


# ---------- Cache with expiry ----------

def _durable(key: str) -> bool:
    return use_pg() and key.startswith(DURABLE_CACHE)


def _local_get(key: str):
    with _lock:
        return _conn.execute("SELECT value, expires FROM cache WHERE key = ?", (key,)).fetchone()


def _local_set(key: str, value_json: str, expires: float | None) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO cache (key, value, expires) VALUES (?, ?, ?)", (key, value_json, expires))
        _conn.commit()


def cache_get(key: str) -> Any | None:
    row = _local_get(key)
    if not row and _durable(key):
        try:
            rows = _pg_run("SELECT value, expires FROM cache WHERE key = ?", (key,), fetch=True)
        except Exception as e:  # noqa: BLE001 - a database hiccup must not break the request
            log.warning("Postgres read failed for %s: %s", key[:60], str(e)[:120])
            rows = []
        if rows:
            row = tuple(rows[0])
            _local_set(key, row[0], row[1])  # keep a local copy for next time
    if not row:
        return None
    value, expires = row
    if expires and expires < time.time():
        return None
    return json.loads(value)


def cache_set(key: str, value: Any, ttl: float | None = None) -> None:
    expires = time.time() + ttl if ttl else None
    value_json = json.dumps(value, default=str)
    _local_set(key, value_json, expires)
    if _durable(key):
        try:
            _pg_run("INSERT OR REPLACE INTO cache (key, value, expires) VALUES (?, ?, ?)", (key, value_json, expires))
        except Exception as e:  # noqa: BLE001
            log.warning("Postgres write failed for %s: %s", key[:60], str(e)[:120])


def cache_delete(key: str) -> None:
    with _lock:
        _conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        _conn.commit()
    if _durable(key):
        _pg_run("DELETE FROM cache WHERE key = ?", (key,))


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


# ---------- Settings (key/value) ----------

def _kv_durable(key: str) -> bool:
    return use_pg() and not key.startswith(LOCAL_KV)


# Durable settings read from Postgres are kept in memory: this backend is their only writer
# (one process), so the copy can't go stale, and every request saves a database round trip.
_kv_memo: dict[str, Any] = {}
_MISSING = object()


def kv_get(key: str, default: Any = None) -> Any:
    if _kv_durable(key):
        if key not in _kv_memo:
            rows = _pg_run("SELECT value FROM kv WHERE key = ?", (key,), fetch=True)
            _kv_memo[key] = json.loads(rows[0][0]) if rows else _MISSING
        value = _kv_memo[key]
        # Always hand out a copy, so callers can't change the stored value (or a default) by accident.
        return json.loads(json.dumps(default if value is _MISSING else value))
    with _lock:
        row = _conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else json.loads(json.dumps(default))


def kv_set(key: str, value: Any) -> None:
    if _kv_durable(key):
        _pg_run("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        _kv_memo[key] = json.loads(json.dumps(value))
        return
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
        _conn.commit()


# ---------- Tables ----------

_TABLES = [
    "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL)",
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY,
        kind TEXT, symbol TEXT,
        event_time REAL,
        first_seen REAL,
        title TEXT, body TEXT, url TEXT,
        notify INTEGER
    )""",
    # alerts: one row per event (mkt:<symbol>:<date> or fil:<filing id>); event_time = when it
    # happened, first_seen = when FINN noticed it; notify 0 = push pending, 1 = pushed, -1 = never push
    "CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, sub TEXT, created REAL)",
    "CREATE TABLE IF NOT EXISTS filings_store (id TEXT PRIMARY KEY, symbol TEXT, time REAL, row TEXT)",
]

for _sql in _TABLES:
    if use_pg():
        try:
            _pg_run(_sql)
        except Exception as e:  # noqa: BLE001
            log.error("couldn't prepare Postgres tables (%s); check DATABASE_URL", str(e)[:200])
            raise
    with _lock:  # local copies too (used when DATABASE_URL isn't set)
        _conn.execute(_sql)
        _conn.commit()
