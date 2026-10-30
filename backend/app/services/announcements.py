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


def _fetch_symbol(symbol: str) -> list[dict]:
    """The last 30 days of filings for one stock. Every time window filters this same list."""
    to = dt.datetime.now(IST).date()
    frm = to - dt.timedelta(days=MAX_DAYS)
    key = f"nse_ann:{symbol}:{to}"

    def load():
        try:
            data = net.get_json(NSE_URL, params={
                "index": "equities", "symbol": symbol,
                "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y"),
            })
        except Exception as e:  # noqa: BLE001
            log.warning("NSE fetch failed for %s: %s", symbol, e)
            return None
        return data if isinstance(data, list) else []

    return db.cached(key, 10 * 60, load) or []


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
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
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
