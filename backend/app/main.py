import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import llm, portfolio
from app.config import settings
from app.obs import SLOW_SECONDS, setup_logging, timed
from app.services import announcements, insights, prices, research, screener

setup_logging()

log = logging.getLogger("finn.api")

app = FastAPI(title="FINN API", version="0.1.0")


# What each API request means in plain words, matched by path (first match wins).
REQUEST_EXPLAINED = [
    ("GET", "/digest", "Brief screen: gathering your portfolio's filings and ranking them by importance."),
    ("GET", "/alerts", "Alerts screen: checking prices and filings against your alert thresholds."),
    ("GET", "/portfolio", "Portfolio: loading your holdings with their latest prices."),
    ("POST", "/portfolio", "Portfolio: adding a company to your holdings."),
    ("DELETE", "/portfolio", "Portfolio: removing a company from your holdings."),
    ("GET", "/settings", "Loading your alert thresholds."),
    ("PUT", "/settings", "Saving your new alert thresholds."),
    ("GET", "/filings/", "Opening a filing's detail card (results numbers or management change)."),
    ("GET", "/search", "Searching for a company by name or ticker."),
    ("GET", "/research/suggestions", "Loading suggested companies to research."),
    ("GET", "/numbers", "Research: loading the company's financials and computed metrics (no AI)."),
    ("POST", "/analysis", "Research: starting the AI analysis (or returning the saved one)."),
    ("GET", "/analysis", "Research: the app is checking whether the AI analysis has finished."),
    ("POST", "/ask", "Research: answering your question from the company's filings."),
]


def _explain_request(method: str, path: str) -> str | None:
    for m, fragment, text in REQUEST_EXPLAINED:
        if m == method and (path.startswith(fragment) or path.endswith(fragment)):
            return text
    return None


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One line per API request with its total time."""
    start = time.perf_counter()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    what = _explain_request(request.method, request.url.path)
    # The research page polls every 2s while the AI works; keep those quiet.
    quiet = (request.method == "GET" and request.url.path.endswith("/analysis")) or request.url.path == "/health"
    if what and not quiet:
        log.info("%s %s\n    ↳ %s", request.method, path, what)
    try:
        response = await call_next(request)
    except Exception:
        log.exception("%s %s crashed after %.2fs", request.method, path, time.perf_counter() - start)
        raise
    took = time.perf_counter() - start
    slow = took >= SLOW_SECONDS
    if not quiet or slow or response.status_code >= 400:
        log.log(
            logging.WARNING if slow else logging.INFO,
            "%s %s → %s in %.2fs%s",
            request.method, path, response.status_code, took, " SLOW" if slow else "",
        )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.frontend_origin.split(",")],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


def _book():
    items = portfolio.get_portfolio()
    return [i["symbol"] for i in items], {i["symbol"]: i["name"] for i in items}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ai": llm.available()}


# ---------- Portfolio & settings ----------

@app.get("/portfolio")
def get_portfolio() -> dict:
    items = portfolio.get_portfolio()
    s = portfolio.get_settings()
    snap = prices.snapshot([i["symbol"] for i in items], s["volume_lookback"])
    return {"items": [{**i, "market": snap.get(i["symbol"])} for i in items]}


class Holding(BaseModel):
    symbol: str
    name: str | None = None
    sector: str | None = None


@app.post("/portfolio")
def add_holding(h: Holding) -> dict:
    items = portfolio.get_portfolio()
    sym = h.symbol.upper().strip()
    if any(i["symbol"] == sym for i in items):
        return {"items": items}
    name = h.name
    if not name:
        d = screener.company(sym)
        if not d:
            raise HTTPException(404, f"Couldn't find {sym} on NSE.")
        name = d["name"]
    items.append({"symbol": sym, "name": name, "sector": h.sector or ""})
    return {"items": portfolio.set_portfolio(items)}


@app.delete("/portfolio/{symbol}")
def remove_holding(symbol: str) -> dict:
    items = [i for i in portfolio.get_portfolio() if i["symbol"] != symbol.upper()]
    return {"items": portfolio.set_portfolio(items)}


@app.get("/settings")
def get_settings() -> dict:
    return portfolio.get_settings()


@app.put("/settings")
def put_settings(patch: dict) -> dict:
    return portfolio.set_settings(patch)


# ---------- Push: digest, filing insights, alerts ----------

@app.get("/digest")
def digest(hours: int = 24) -> dict:
    usage = llm.track_usage()
    symbols, names = _book()
    out = announcements.digest(symbols, names, hours=hours)
    out["usage"] = usage.as_dict()
    return out


@app.get("/filings/{item_id}/insight")
def filing_insight(item_id: str) -> dict:
    usage = llm.track_usage()
    symbols, names = _book()
    with timed(log, "insight: find filing", id=item_id):
        item = announcements.find_item(item_id, symbols, names)
    if not item:
        raise HTTPException(404, "Filing not found.")
    with timed(log, f"insight: {item['insight']} card", symbol=item["symbol"], id=item_id) as t:
        if item["insight"] == "results":
            out = insights.results_card(item)
            t["source"] = out.get("source")
        elif item["insight"] == "management":
            out = insights.management_card(item)
            t["people"] = len(out.get("people") or [])
        else:
            raise HTTPException(400, "No deeper view for this filing.")
        t["model_calls"] = usage.calls
    return {**out, "usage": usage.as_dict()}


@app.get("/alerts")
def alerts(hours: int = 72) -> dict:
    s = portfolio.get_settings()
    symbols, names = _book()
    market = prices.unusual_moves(
        symbols, s["price_move_pct"], s["volume_multiple"], s["volume_lookback"], days=max(1, hours // 24 + 1),
    )
    for a in market:
        a["company"] = names.get(a["symbol"], a["symbol"])
    filings = [
        {**i, "kind": "filing"}
        for i in announcements.digest(symbols, names, hours=hours)["items"]
        if i["category"] in s["alert_categories"] and i["importance"] >= 50
    ]
    return {"settings": s, "market": market, "filings": filings}


# ---------- Pull: research ----------

@app.get("/search")
def search(q: str) -> list[dict]:
    return screener.search(q)


@app.get("/research/suggestions")
def suggestions() -> list[str]:
    return portfolio.RESEARCH_SUGGESTIONS


@app.get("/research/{symbol}/numbers")
def research_numbers(symbol: str) -> dict:
    out = research.numbers(symbol)
    if not out:
        raise HTTPException(404, f"No fundamentals found for {symbol.upper()}.")
    out["market"] = prices.snapshot([out["symbol"]]).get(out["symbol"])
    return out


@app.post("/research/{symbol}/analysis")
def research_start(symbol: str, refresh: bool = False) -> dict:
    return research.start(symbol, refresh=refresh)


@app.get("/research/{symbol}/analysis")
def research_status(symbol: str) -> dict:
    return research.status(symbol)


class Question(BaseModel):
    question: str


@app.post("/research/{symbol}/ask")
def research_ask(symbol: str, q: Question) -> dict:
    if not q.question.strip():
        raise HTTPException(400, "Ask a question.")
    return research.ask(symbol, q.question)
