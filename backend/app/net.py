"""HTTP helpers with browser-like headers and an on-disk cache for PDFs."""

import hashlib
import logging

import httpx

from app.config import DATA_DIR
from app.obs import short_url, timed

log = logging.getLogger("finn.net")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}

PDF_DIR = DATA_DIR / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)

client = httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True)

# PDFs: give up on a request that sends nothing for 7s and ask again once (2 tries in total).
# "read" is the longest gap between pieces of data, not the whole download, so a big
# annual report that keeps arriving steadily is never cut off.
PDF_TIMEOUT = httpx.Timeout(connect=10.0, read=7.0, write=10.0, pool=10.0)
PDF_ATTEMPTS = 2


def referer_for(url: str) -> str:
    if "nseindia" in url:
        return "https://www.nseindia.com/"
    if "bseindia" in url:
        return "https://www.bseindia.com/"
    return "https://www.screener.in/"


def get_json(url: str, params: dict | None = None, referer: str | None = None):
    label = params.get("symbol") or params.get("q") if params else None
    with timed(log, "http json", url=short_url(url, 70), arg=label) as t:
        r = client.get(url, params=params, headers={"Accept": "application/json", "Referer": referer or referer_for(url)})
        t["status"] = r.status_code
        t["kb"] = len(r.content) // 1024
        r.raise_for_status()
        return r.json()


def get_text(url: str) -> str:
    with timed(log, "http page", url=short_url(url)) as t:
        r = client.get(url, headers={"Referer": referer_for(url)})
        t["status"] = r.status_code
        t["kb"] = len(r.content) // 1024
        r.raise_for_status()
        return r.text


def get_pdf(url: str) -> bytes | None:
    """Download a PDF once and keep it on disk."""
    path = PDF_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".pdf")
    if path.exists():
        log.debug("pdf from disk %s", short_url(url))
        return path.read_bytes()
    with timed(log, "pdf download", url=short_url(url)) as t:
        r = None
        for attempt in range(1, PDF_ATTEMPTS + 1):
            try:
                r = client.get(url, headers={"Referer": referer_for(url)}, timeout=PDF_TIMEOUT)
                t["status"] = r.status_code
                t["mb"] = round(len(r.content) / 1e6, 2)
                r.raise_for_status()
                break
            except httpx.TimeoutException as e:
                # BSE sometimes stalls on a file (seen: ~21s for a 1 MB transcript that
                # normally takes 1s). A fresh request usually comes back at once.
                t["stalls"] = attempt
                if attempt == PDF_ATTEMPTS:
                    t["error"] = type(e).__name__
                    return None
                log.warning("pdf download stalled (no data for %.0fs), retrying (%d/%d): %s\n    ↳ The website went quiet on this file; asking again is usually instant.",
                            PDF_TIMEOUT.read, attempt + 1, PDF_ATTEMPTS, short_url(url))
            except httpx.HTTPError as e:
                t["error"] = type(e).__name__
                return None
        t["attempts"] = attempt
        if not r.content.startswith(b"%PDF"):
            t["error"] = "not a pdf"
            return None
    path.write_bytes(r.content)
    return r.content
