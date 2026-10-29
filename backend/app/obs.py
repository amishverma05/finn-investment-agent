"""Logging setup and a `timed` helper that logs how long each step takes.

Every step logs a start line (→) and an end line (←) with its duration.
Steps slower than SLOW_SECONDS are logged as warnings, tagged SLOW, so they stand out.
Set LOG_LEVEL=DEBUG in .env to also see cache hits.
"""

import contextlib
import logging
import time
from logging.handlers import RotatingFileHandler

from app.config import DATA_DIR, settings

SLOW_SECONDS = settings.slow_seconds


LOG_FILE = DATA_DIR / "logs" / "finn.log"


def setup_logging() -> None:
    """Log to the terminal and to backend/data/logs/finn.log (rotates at 5 MB, keeps 3 old files)."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s.%(msecs)03d %(levelname)-5s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), file_handler],
        force=True,
    )
    logging.getLogger("finn").info("logging to terminal and %s", LOG_FILE)
    # Our own logs cover these; their per-request logs would just duplicate them.
    for noisy in ("httpx", "httpcore", "yfinance", "peewee", "watchfiles", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _fmt(fields: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)


# Plain-English explanation printed under each step's start line.
# {placeholders} are filled from the step's fields.
STEP_EXPLAINED = {
    "http json": "Asking a website's data API for information ({arg}).",
    "http page": "Downloading a web page (screener.in) to read the company's financial tables and document links.",
    "pdf download": "Downloading a PDF document (filing, transcript or annual report). Saved to disk so it's only fetched once.",
    "pdf text extract": "Pulling the text out of a {mb} MB PDF so the app can search and read it.",
    "bm25 index build": "Building a keyword index over {chunks} text chunks so we can find the relevant passages quickly.",
    "yahoo prices download": "Getting 6 months of daily closing prices and traded volume from Yahoo Finance for {tickers} stock(s).",
    "digest: fetch NSE filings": "Collecting the last 30 days of NSE announcements for your {stocks} stocks, then keeping the last {hours} hours.",
    "digest: AI triage": "Asking the cheap AI model to summarise new filings in one line and judge how much each one matters.",
    "transcript → guidance": "Reading the {call} earnings-call transcript and pulling out what management promised (growth, margins, capex).",
    "annual report → passages": "Reading the {year} annual report and picking the few passages about business, strategy and risks.",
    "ask: gather documents": "Loading this company's transcripts and annual report to search them for your question.",
    "insight: find filing": "Looking up which filing you tapped.",
    "insight: results card": "Reading the results filing to build the Revenue / EBITDA / PBT / PAT table for {symbol}.",
    "insight: management card": "Reading the management-change filing for {symbol} and looking up who the new person is.",
}

LLM_EXPLAINED = {
    "TriageBatch": "AI is summarising filings in one line each and scoring their importance.",
    "ResultsExtract": "AI is reading the results pages and copying out revenue, costs and profit for 3 quarters.",
    "People": "AI is reading the filing to find who joined or left, their role and when.",
    "web search (bio)": "AI is searching the web for the new executive's professional background.",
    "CallNotes": "AI is listing management's promises and guidance from one earnings call.",
    "Analysis": "AI is comparing promises against actual results and writing the full research view.",
    "Answer": "AI is answering your question using passages from the filings.",
}


def _explain(name: str, fields: dict) -> str | None:
    if name == "http json":
        url = fields.get("url") or ""
        if "nseindia" in url:
            return f"Fetching {fields.get('arg')}'s recent company announcements from the NSE website."
        if "screener" in url:
            return f"Searching screener.in for companies matching '{fields.get('arg')}'."
    if name == "llm call":
        text = LLM_EXPLAINED.get(fields.get("purpose", ""))
        tier = "cheap" if fields.get("tier") == "cheap" else "strong"
        return f"{text} ({tier} model)" if text else None
    text = STEP_EXPLAINED.get(name)
    if not text:
        return None
    try:
        return text.format(**fields)
    except (KeyError, IndexError):
        return text


@contextlib.contextmanager
def timed(logger: logging.Logger, name: str, **fields):
    """Log start, end and duration of a block. Add result details to the yielded dict:

        with timed(log, "download", url=u) as t:
            ...
            t["kb"] = 120
    """
    result: dict = {}
    start = time.perf_counter()
    logger.info("→ %s %s", name, _fmt(fields))
    what = _explain(name, fields)
    if what:
        logger.info("    ↳ %s", what)
    status = "ok"
    try:
        yield result
    except Exception as e:
        status = f"FAILED {type(e).__name__}: {str(e)[:200]}"
        raise
    finally:
        took = time.perf_counter() - start
        slow = took >= SLOW_SECONDS
        logger.log(
            logging.WARNING if slow or status != "ok" else logging.INFO,
            "← %s %.2fs%s %s %s",
            name,
            took,
            " SLOW" if slow else "",
            status,
            _fmt({**fields, **result}),
        )


def short_url(url: str, n: int = 90) -> str:
    return url if len(url) <= n else url[: n - 3] + "..."
