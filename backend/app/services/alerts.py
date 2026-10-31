"""Triggered alerts: price/volume moves beyond your thresholds and material filings.

Every alert is recorded once in the `alerts` table the first time FINN sees it. That gives:
- "new since your last visit" (badge, dots) from `first_seen`
- exactly one push notification per event (the `notify` column)
"""

import datetime as dt
import logging
import time

from app import db, portfolio
from app.services import announcements, prices

log = logging.getLogger("finn.alerts")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# Only push events this fresh; older ones (e.g. surfaced because you loosened a threshold) are listed, not pushed.
PUSH_IF_NEWER_THAN = 18 * 3600


def _book():
    items = portfolio.get_portfolio()
    return [i["symbol"] for i in items], {i["symbol"]: i["name"] for i in items}


def _move_text(a: dict) -> str:
    v = a.get("volume_ratio") or 1
    word = "up" if a["change_pct"] >= 0 else "down"
    if a["price_trigger"] and a["volume_trigger"]:
        return f"{word} {abs(a['change_pct']):.1f}% on {v}× normal volume"
    if a["price_trigger"]:
        return f"{word} {abs(a['change_pct']):.1f}% (volume {v}× normal)"
    return f"volume {v}× normal, price {a['change_pct']:+.1f}%"


def current(hours: int = 72) -> dict:
    """Compute alerts now from the latest prices and filings, using your settings."""
    s = portfolio.get_settings()
    symbols, names = _book()
    market = prices.unusual_moves(
        symbols, s["price_move_pct"], s["volume_multiple"], s["volume_lookback"], days=max(1, hours // 24 + 1),
    )
    for a in market:
        a["company"] = names.get(a["symbol"], a["symbol"])
        a["alert_id"] = f"mkt:{a['symbol']}:{a['date']}"
        close = dt.datetime.fromisoformat(a["date"]).replace(hour=15, minute=30, tzinfo=IST)
        a["event_time"] = close.timestamp()
    filings = []
    for i in announcements.digest(symbols, names, hours=hours)["items"]:
        if i["category"] in s["alert_categories"] and i["importance"] >= 50:
            filings.append({
                **i, "kind": "filing", "alert_id": f"fil:{i['id']}",
                "event_time": dt.datetime.fromisoformat(i["time"]).timestamp(),
            })
    return {"settings": s, "market": market, "filings": filings}


def record(cur: dict) -> list[dict]:
    """Remember alerts we haven't seen before; returns the new ones.
    The very first time (empty table) everything is recorded silently, so a fresh
    install doesn't push a week of history at once."""
    first_run = not db.query("SELECT 1 FROM alerts LIMIT 1")
    now = time.time()
    new = []
    rows = []
    for a in cur["market"]:
        rows.append((a, "market", f"{a['symbol']} {a['change_pct']:+.1f}%",
                     f"{a['company']}: {_move_text(a)}.", f"/alerts?focus={a['alert_id']}"))
    for f in cur["filings"]:
        body = f.get("summary") or f.get("title") or ""
        rows.append((f, "filing", f"{f['symbol']} · {f.get('category_label', 'Filing')}",
                     body[:160], f"/alerts?focus={f['alert_id']}"))
    for a, kind, title, body, url in rows:
        fresh = now - a["event_time"] < PUSH_IF_NEWER_THAN
        notify = -1 if (first_run or not fresh) else 0
        changed = db.execute(
            "INSERT OR IGNORE INTO alerts (id, kind, symbol, event_time, first_seen, title, body, url, notify) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (a["alert_id"], kind, a["symbol"], a["event_time"], now, title, body, url, notify),
        )
        if changed:
            new.append({"id": a["alert_id"], "title": title, "notify": notify})
    if new:
        log.info("alerts: %d new (%d to push)%s", len(new), sum(1 for n in new if n["notify"] == 0),
                 " — first run, recorded silently" if first_run else "")
    return new


def annotate(cur: dict) -> dict:
    """Add first_seen (ms) to each alert so the app can mark what's new to you."""
    ids = [a["alert_id"] for a in cur["market"] + cur["filings"]]
    if ids:
        marks = ",".join("?" * len(ids))
        seen = dict(db.query(f"SELECT id, first_seen FROM alerts WHERE id IN ({marks})", tuple(ids)))
        for a in cur["market"] + cur["filings"]:
            a["first_seen"] = int(seen.get(a["alert_id"], time.time()) * 1000)
    return cur


def check(hours: int = 72) -> list[dict]:
    """Compute + record. Used by the scheduler."""
    return record(current(hours))


def unread(since_ms: int) -> dict:
    """How many alerts appeared since `since_ms`, and the latest one (for the badge and banner)."""
    since = since_ms / 1000
    count = db.query("SELECT COUNT(*) FROM alerts WHERE first_seen > ? AND notify != -1", (since,))[0][0]
    latest = db.query(
        "SELECT id, title, body, url, first_seen FROM alerts WHERE first_seen > ? AND notify != -1 "
        "ORDER BY first_seen DESC, event_time DESC LIMIT 1",
        (since,),
    )
    out = {"count": count, "latest": None}
    if latest:
        i, title, body, url, fs = latest[0]
        out["latest"] = {"id": i, "title": title, "body": body, "url": url, "first_seen": int(fs * 1000)}
    return out


def pending() -> list[dict]:
    """Alerts waiting to be pushed (fresh ones only)."""
    rows = db.query(
        "SELECT id, title, body, url FROM alerts WHERE notify = 0 AND first_seen > ? ORDER BY event_time DESC",
        (time.time() - 24 * 3600,),
    )
    return [{"id": r[0], "title": r[1], "body": r[2], "url": r[3]} for r in rows]


def mark_pushed(ids: list[str]) -> None:
    for i in ids:
        db.execute("UPDATE alerts SET notify = 1 WHERE id = ?", (i,))
