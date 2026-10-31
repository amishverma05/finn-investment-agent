import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import llm, portfolio, scheduler
from app.config import settings
from app.obs import SLOW_SECONDS, setup_logging, timed
from app.services import alerts as alert_service
from app.services import announcements, insights, prices, push, research, screener

setup_logging()

log = logging.getLogger("finn.api")

@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler.start()  # background jobs: filings, prices, alerts, push notifications
    yield
    scheduler.stop()


app = FastAPI(title="FINN API", version="0.1.0", lifespan=lifespan)


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
    ("POST", "/push/subscribe", "Notifications: this device turned on push alerts."),
    ("POST", "/push/unsubscribe", "Notifications: this device turned off push alerts."),
    ("POST", "/push/test", "Notifications: sending a test alert to this device."),
    ("POST", "/jobs/run", "Running background jobs now (filings / prices)."),
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
    quiet = (
        (request.method == "GET" and request.url.path.endswith("/analysis"))
        or request.url.path in ("/health", "/alerts/unread", "/push/key", "/jobs/status")
    )
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
    # Local dev (laptop or phone on the same Wi-Fi) and Vercel deployments of the frontend.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?|https://[a-z0-9-]+\.vercel\.app",
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
    out = {"items": portfolio.set_portfolio(items)}
    scheduler.run_soon("filings", "prices")  # start watching the new stock right away
    return out


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
def digest(hours: int = 24, fast: bool = False) -> dict:
    """fast=true: rule-ranked filings right away, using only AI summaries already saved.
    The app shows that, then calls again without fast to fill in any missing summaries."""
    usage = llm.track_usage()
    symbols, names = _book()
    out = announcements.digest(symbols, names, hours=hours, fast=fast)
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
    """Alerts in the window. Each is recorded the first time it's seen (for "new" marks and
    one push per event); `first_seen` (ms) on each says when FINN first noticed it."""
    cur = alert_service.current(hours)
    alert_service.record(cur)
    return alert_service.annotate(cur)


@app.get("/alerts/unread")
def alerts_unread(since: int = 0) -> dict:
    """For the Alerts tab badge and the in-app banner: alerts first seen after `since` (ms)."""
    return alert_service.unread(since)


# ---------- Push notifications ----------

@app.get("/push/key")
def push_key() -> dict:
    return {"public_key": push.public_key()}


class PushSubscription(BaseModel):
    endpoint: str
    keys: dict[str, str]


@app.post("/push/subscribe")
def push_subscribe(sub: PushSubscription) -> dict:
    push.subscribe(sub.model_dump())
    return {"ok": True}


class Endpoint(BaseModel):
    endpoint: str


@app.post("/push/unsubscribe")
def push_unsubscribe(e: Endpoint) -> dict:
    push.unsubscribe(e.endpoint)
    return {"ok": True}


@app.post("/push/test")
def push_test(e: Endpoint) -> dict:
    """Send a sample alert to this device so you can see what notifications look like."""
    sent = push.send(
        {
            "title": "FINN test alert",
            "body": "This is how alerts arrive: results, management changes and unusual moves in your stocks.",
            "url": "/alerts",
            "tag": "finn-test",
        },
        endpoint=e.endpoint,
    )
    if not sent:
        raise HTTPException(400, "Couldn't reach this device. Turn notifications off and on again.")
    return {"ok": True}


# ---------- Background jobs ----------

@app.get("/jobs/status")
def jobs_status() -> dict:
    return scheduler.status()


@app.post("/jobs/run")
def jobs_run(job: str = "filings") -> dict:
    scheduler.run_soon(*job.split(","))
    return {"queued": job.split(","), "note": "Runs within 30 seconds."}


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


class Turn(BaseModel):
    role: str  # "user" or "assistant"
    text: str


class Question(BaseModel):
    question: str
    history: list[Turn] = []


@app.post("/research/{symbol}/ask")
def research_ask(symbol: str, q: Question) -> dict:
    if not q.question.strip():
        raise HTTPException(400, "Ask a question.")
    return research.ask(symbol, q.question, [t.model_dump() for t in q.history])


# ---------- Diagnostics (temporary): can this server reach each data source? ----------

@app.get("/debug/sources")
def debug_sources() -> dict:
    """Try each upstream once from this server and report what came back.
    Used to diagnose hosting issues (e.g. a site blocking cloud IPs). Returns no secrets."""
    import datetime as dt

    from app import net

    today = dt.date.today()
    week_ago = today - dt.timedelta(days=7)
    checks = {
        "nse_announcements": ("https://www.nseindia.com/api/corporate-announcements",
                              {"index": "equities", "symbol": "RELIANCE",
                               "from_date": week_ago.strftime("%d-%m-%Y"), "to_date": today.strftime("%d-%m-%Y")},
                              "https://www.nseindia.com/"),
        "nse_homepage": ("https://www.nseindia.com/", None, "https://www.nseindia.com/"),
        "bse_announcements": ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
                              {"pageno": 1, "strCat": -1, "strPrevDate": week_ago.strftime("%Y%m%d"), "strScrip": "500325",
                               "strSearch": "P", "strToDate": today.strftime("%Y%m%d"), "strType": "C", "subcategory": -1},
                              "https://www.bseindia.com/"),
        "screener_search": ("https://www.screener.in/api/company/search/", {"q": "reliance", "v": 3, "fts": 1},
                            "https://www.screener.in/"),
        "yahoo_chart": ("https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS", {"range": "5d", "interval": "1d"},
                        "https://finance.yahoo.com/"),
        "egress_ip": ("https://api.ipify.org", {"format": "json"}, None),
    }
    out = {}
    for name, (url, params, referer) in checks.items():
        start = time.perf_counter()
        try:
            headers = {"Accept": "application/json, text/html;q=0.9"}
            if referer:
                headers["Referer"] = referer
                headers["Origin"] = referer.rstrip("/")
            r = net.client.get(url, params=params, headers=headers, timeout=20)
            body = r.text
            info = {
                "status": r.status_code,
                "content_type": r.headers.get("content-type"),
                "bytes": len(r.content),
                "server": r.headers.get("server"),
                "took_s": round(time.perf_counter() - start, 2),
                "start": body[:160].replace("\n", " "),
            }
            try:
                data = r.json()
                info["json"] = True
                info["items"] = len(data) if isinstance(data, list) else len(data.get("Table", [])) if isinstance(data, dict) else None
            except ValueError:
                info["json"] = False
            out[name] = info
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}", "took_s": round(time.perf_counter() - start, 2)}
    return out
