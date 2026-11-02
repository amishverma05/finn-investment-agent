"""Corporate announcements from NSE, classified, scored and triaged into a digest."""

import datetime as dt
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from pydantic import BaseModel

from app import db, llm, net
from app.obs import timed
from app.services import classifier

log = logging.getLogger("finn.announcements")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NSE_URL = "https://www.nseindia.com/api/corporate-announcements"


MAX_DAYS = 30


# ---------- Where filings come from ----------
#
# 1. NSE's announcements API: complete 30-day history per stock. Blocked from many cloud
#    servers (NSE's firewall answers 403 or just hangs), so it gets a short time limit and,
#    once blocked, is skipped for a while.
# 2. NSE's RSS feed of all companies' announcements (on nsearchives, which isn't blocked):
#    only the last ~18 hours, so the scheduler saves what it sees into the `filings_store`
#    table and history builds up over time.
# 3. screener.in's "recent announcements" per company (5 latest, dates only): used once to
#    backfill the last few days when the API is blocked and nothing is saved yet.

NSE_TIMEOUT = 8.0          # seconds; a blocked request otherwise hangs for a minute
NSE_BLOCK_PAUSE = 30 * 60  # after a block, skip the API for this long
RSS_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"



def _nse_blocked() -> bool:
    return (db.kv_get("nse_blocked_until") or 0) > dt.datetime.now().timestamp()


def _mark_nse_blocked(reason: str) -> None:
    if not _nse_blocked():
        log.warning("NSE API is blocking this server (%s); using the RSS feed and saved filings for %d min\n"
                    "    ↳ NSE's firewall refuses many cloud servers. Filings still arrive via NSE's RSS feed.",
                    reason, NSE_BLOCK_PAUSE // 60)
    db.kv_set("nse_blocked_until", dt.datetime.now().timestamp() + NSE_BLOCK_PAUSE)


def _api_rows(symbol: str) -> list[dict] | None:
    """30 days from NSE's API, or None if it's unavailable."""
    if _nse_blocked():
        return None
    to = dt.datetime.now(IST).date()
    frm = to - dt.timedelta(days=MAX_DAYS)
    key = f"nse_ann:{symbol}:{to}"

    def load():
        import httpx

        try:
            with timed(log, "http json", url=NSE_URL, arg=symbol) as t:
                r = net.client.get(
                    NSE_URL,
                    params={"index": "equities", "symbol": symbol,
                            "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")},
                    headers={"Accept": "application/json", "Referer": "https://www.nseindia.com/"},
                    timeout=NSE_TIMEOUT,
                )
                t["status"] = r.status_code
            if r.status_code == 403:
                _mark_nse_blocked("403 Access Denied")
                return None
            r.raise_for_status()
            data = r.json()
        except httpx.TimeoutException:
            _mark_nse_blocked(f"no reply in {NSE_TIMEOUT:.0f}s")
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("NSE fetch failed for %s: %s", symbol, e)
            return None
        return data if isinstance(data, list) else []

    return db.cached(key, 10 * 60, load)


def _saved_rows(symbol: str) -> list[dict]:
    since = (dt.datetime.now(IST) - dt.timedelta(days=MAX_DAYS)).timestamp()
    import json

    return [json.loads(r[0]) for r in db.query(
        "SELECT row FROM filings_store WHERE symbol = ? AND time > ? ORDER BY time DESC", (symbol, since))]


def _save(symbol: str, row: dict) -> bool:
    import json

    t = _parse_time(row.get("an_dt"))
    if not t:
        return False
    return bool(db.execute(
        "INSERT OR IGNORE INTO filings_store (id, symbol, time, row) VALUES (?, ?, ?, ?)",
        (row["seq_id"], symbol, t.timestamp(), json.dumps(row)),
    ))


def _fetch_symbol(symbol: str) -> list[dict]:
    """The last 30 days of filings for one stock: NSE's API when reachable (complete);
    otherwise the filings saved from the RSS feed and the screener backfill."""
    api = _api_rows(symbol)
    if api is not None:
        return api
    # The RSS feed only covers the last few hours, so anything published while the server
    # was asleep is caught by the screener backfill (it limits itself to every few hours).
    _backfill_from_screener(symbol)
    return _saved_rows(symbol)


def _norm_name(name: str) -> str:
    n = (name or "").lower().replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(r"\b(limited|ltd|the)\b", " ", n)
    return " ".join(n.split())


def _official_names() -> dict[str, str]:
    """NSE's official company name (normalised) -> symbol, refreshed daily."""

    def load():
        import csv
        import io

        r = net.client.get(EQUITY_LIST_URL, headers={"Referer": "https://www.nseindia.com/"}, timeout=20)
        r.raise_for_status()
        return {_norm_name(x["NAME OF COMPANY"]): x["SYMBOL"].strip() for x in csv.DictReader(io.StringIO(r.text))}

    try:
        return db.cached("nse_equity_names", 24 * 3600, load) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't load NSE company list: %s", e)
        return {}


def ingest_rss(symbols: list[str]) -> int:
    """Save new portfolio filings from NSE's RSS feed. Returns how many were new."""
    want = set(symbols)
    names = _official_names()
    try:
        with timed(log, "nse rss feed") as t:
            r = net.client.get(RSS_URL, headers={"Referer": "https://www.nseindia.com/"}, timeout=20)
            r.raise_for_status()
            items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
            t["items"] = len(items)
    except Exception as e:  # noqa: BLE001
        log.warning("NSE RSS feed failed: %s", e)
        return 0
    import html as htmllib

    def field(item: str, tag: str) -> str:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", item, re.S)
        return htmllib.unescape(m.group(1)).strip() if m else ""

    new = 0
    for it in items:
        title, link, desc, when = field(it, "title"), field(it, "link"), field(it, "description"), field(it, "pubDate")
        prefix = re.match(r".*/corporate/([A-Z0-9&-]+)_\d", link)
        symbol = names.get(_norm_name(title)) or (prefix.group(1) if prefix else None)
        if symbol not in want:
            continue
        text, _, subject = desc.partition("|SUBJECT:")
        row = {
            "seq_id": "rss-" + hashlib.sha1(link.encode()).hexdigest()[:12],
            "an_dt": when,
            "desc": subject.strip() or "General Updates",
            "attchmntText": text.strip(),
            "attchmntFile": link if link.lower().endswith(".pdf") else None,
            "sm_name": title,
            "source": "nse-rss",
        }
        new += _save(symbol, row)
    if new:
        log.info("NSE RSS: saved %d new filing(s) for your stocks", new)
    return new


def _screener_id(symbol: str) -> int | None:
    def load():
        data = net.get_json("https://www.screener.in/api/company/search/",
                            params={"q": symbol, "v": 3, "fts": 1}, referer="https://www.screener.in/")
        for d in data:
            if re.match(rf"/company/{re.escape(symbol)}/", d.get("url") or "", re.I) and d.get("id"):
                return d["id"]
        return None

    try:
        return db.cached(f"scr_id:{symbol}", 30 * 24 * 3600, load)
    except Exception:  # noqa: BLE001
        return None


def _backfill_from_screener(symbol: str) -> None:
    """Save a stock's latest few filings from screener.in (when NSE's API is blocked).
    Runs at most once every 3 hours per stock."""
    if db.cache_get(f"scr_backfill:{symbol}"):
        return
    db.cache_set(f"scr_backfill:{symbol}", True, ttl=3 * 3600)
    sid = _screener_id(symbol)
    if not sid:
        return
    try:
        page = net.get_text(f"https://www.screener.in/announcements/recent/{sid}/")
    except Exception as e:  # noqa: BLE001
        log.warning("screener announcements failed for %s: %s", symbol, e)
        return
    today = dt.datetime.now(IST).date()
    saved = 0
    for url, inner in re.findall(r'<a href="(https://www\.bseindia\.com/stockinfo/AnnPdfOpen[^"]+)"[^>]*>(.*?)</a>', page, re.S):
        sub = re.search(r'<div[^>]*>(.*?)</div>', inner, re.S)
        title = _clean(re.sub("<[^>]+>", " ", inner[: sub.start()] if sub else inner))
        detail = _clean(re.sub("<[^>]+>", " ", sub.group(1))) if sub else ""
        m = re.match(r"(\d{1,2} [A-Z][a-z]{2})(?: \d{4})?\s*-?\s*(.*)", detail)
        if not m:
            continue
        day = dt.datetime.strptime(f"{m.group(1)} {today.year}", "%d %b %Y").date()
        if day > today:
            day = day.replace(year=today.year - 1)
        row = {
            "seq_id": "scr-" + hashlib.sha1(url.encode()).hexdigest()[:12],
            "an_dt": f"{day.strftime('%d-%b-%Y')} 12:00:00",  # screener shows the date only
            "desc": title,
            "attchmntText": m.group(2) or title,
            "attchmntFile": url,
            "sm_name": symbol,
            "source": "screener",
        }
        saved += _save(symbol, row)
    log.info("screener backfill %s: saved %d filing(s)", symbol, saved)


def _parse_time(s: str) -> dt.datetime | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=IST)
        except (TypeError, ValueError):
            continue
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class TriageItem(BaseModel):
    id: str
    category: str
    importance: int
    summary: str


class TriageBatch(BaseModel):
    items: list[TriageItem]


TRIAGE_SYSTEM = (
    "You are the filter for a busy public-markets investor. For each exchange filing, decide what it is "
    "and how much it matters to someone who owns the stock. Be terse and concrete. Never invent facts "
    "not present in the filing text."
)


def _triage(items: list[dict], allow_new: bool = True) -> int:
    """Add a one-line summary and a judged importance to items, using one cheap-model call per batch.
    With allow_new=False only saved summaries are used (no AI call). Returns how many still need one."""
    todo = []
    for it in items:
        hit = db.cache_get(f"triage:{it['id']}")
        if hit:
            it["_triage"] = hit
        else:
            todo.append(it)
    if not llm.available():
        return 0
    if not allow_new:
        log.info("triage: %d saved, %d waiting for AI (fast view, no AI call now)", len(items) - len(todo), len(todo))
        return len(todo)
    log.info("triage: %d cached, %d new", len(items) - len(todo), len(todo))
    if not todo:
        return 0

    cats = ", ".join(classifier.CATEGORIES)
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        lines = [
            f"id={it['id']} | {it['company']} | type: {it['title']} | text: {it['text'][:500]}" for it in chunk
        ]
        prompt = (
            f"Categories: {cats}.\n"
            "For each filing return: id; category (one of the list); importance 0-100 "
            "(90+ = could move the stock today, e.g. results, big M&A, CEO/CFO exit, regulatory action; "
            "40-70 = worth knowing; under 30 = routine paperwork); summary = ONE line, max 18 words, "
            "saying what happened and the number that matters if there is one. No filler like "
            "'The company has informed'.\n\n" + "\n".join(lines)
        )
        out = llm.generate_json(prompt, TriageBatch, tier="cheap", system=TRIAGE_SYSTEM)
        if not out:
            continue
        by_id = {t.id: t.model_dump() for t in out.items}
        for it in chunk:
            t = by_id.get(it["id"])
            if t:
                db.cache_set(f"triage:{it['id']}", t)
                it["_triage"] = t
    return sum(1 for it in todo if "_triage" not in it)  # AI failed for these; they keep rule-based ranking


def digest(symbols: list[str], names: dict[str, str], hours: int = 24, use_ai: bool = True, fast: bool = False) -> dict:
    """Filings across the portfolio for the last `hours`, ranked by importance.
    fast=True: no new AI calls, only summaries already saved, so it returns in ~2s.
    `ai_pending` says how many filings still need an AI summary (the app then asks again without fast)."""
    hours = max(1, min(hours, MAX_DAYS * 24))
    now = dt.datetime.now(IST)
    since = now - dt.timedelta(hours=hours)

    # Keep the saved filings topped up from NSE's RSS feed (at most every 10 minutes).
    if not db.cache_get("rss_ingested"):
        db.cache_set("rss_ingested", True, ttl=10 * 60)
        ingest_rss(symbols)

    with timed(log, "digest: fetch NSE filings", stocks=len(symbols), hours=hours):
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda s: ctx.copy().run(_fetch_symbol, s), symbols))

    items, seen = [], set()
    for symbol, rows in zip(symbols, results):
        for a in rows:
            t = _parse_time(a.get("an_dt") or a.get("sort_date"))
            if not t or t < since:
                continue
            text = _clean(a.get("attchmntText"))
            title = _clean(a.get("desc"))
            dedupe_key = (symbol, text[:160].lower())
            url = a.get("attchmntFile")
            # The same filing can come from the API and the RSS feed: match on text or PDF link.
            if dedupe_key in seen or (url and url in seen):
                continue
            seen.add(dedupe_key)
            if url:
                seen.add(url)
            cat, confident = classifier.classify(title, text)
            items.append({
                "id": str(a.get("seq_id") or hashlib.sha1(repr(dedupe_key).encode()).hexdigest()[:12]),
                "symbol": symbol,
                "company": names.get(symbol) or a.get("sm_name") or symbol,
                "title": title,
                "text": text,
                "time": t.isoformat(),
                "url": a.get("attchmntFile"),
                "category": cat,
                "confident": confident,
                "importance": classifier.score(cat, title, text),
            })

    ai_pending = 0
    if use_ai:
        to_triage = [it for it in items if not it["confident"] or it["importance"] >= 35]
        if fast:
            ai_pending = _triage(to_triage, allow_new=False)
        else:
            with timed(log, "digest: AI triage", filings=len(items), sent_for_triage=len(to_triage)):
                ai_pending = _triage(to_triage)

    for it in items:
        t = it.pop("_triage", None)
        it["summary"] = None
        if t:
            it["summary"] = t["summary"]
            if not it["confident"] and t["category"] in classifier.CATEGORIES:
                it["category"] = t["category"]
                it["importance"] = classifier.score(t["category"], it["title"], it["text"])
            # Rules set the baseline; the model can nudge it by up to 15 points.
            delta = max(-15, min(15, int(t["importance"]) - it["importance"]))
            it["importance"] = max(0, min(100, it["importance"] + delta))
        it["category_label"] = classifier.LABELS.get(it["category"], it["category"])
        it["tier"] = classifier.tier(it["importance"])
        it["insight"] = _insight_kind(it)
        it.pop("confident", None)

    items.sort(key=lambda x: (x["importance"], x["time"]), reverse=True)
    counts = {k: sum(1 for i in items if i["tier"] == k) for k in ("attention", "notable", "routine")}
    return {
        "since": since.isoformat(),
        "until": now.isoformat(),
        "hours": hours,
        "counts": counts,
        "items": items,
        "ai": llm.available() and use_ai,
        "ai_pending": ai_pending,
    }


def _insight_kind(it: dict) -> str | None:
    """Which deeper card this filing gets when opened."""
    if it["category"] == "results" and it["importance"] >= 70 and it.get("url"):
        return "results"
    if it["category"] == "management_change" and it.get("url"):
        return "management"
    return None


def find_item(item_id: str, symbols: list[str], names: dict[str, str]) -> dict | None:
    """Find a filing by id across the last 30 days (served from cache)."""
    for it in digest(symbols, names, hours=24 * 30, use_ai=False)["items"]:
        if it["id"] == item_id:
            return it
    return None
