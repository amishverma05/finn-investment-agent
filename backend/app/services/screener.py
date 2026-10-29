"""Fundamentals from screener.in: standardised financial tables plus links to
annual reports and earnings-call transcripts. Works for any listed company it covers."""

import logging
import re

from bs4 import BeautifulSoup

from app import db, net

log = logging.getLogger("finn.screener")
BASE = "https://www.screener.in"


def search(q: str) -> list[dict]:
    q = q.strip()
    if not q:
        return []

    def load():
        data = net.get_json(f"{BASE}/api/company/search/", params={"q": q, "v": 3, "fts": 1}, referer=BASE)
        out = []
        for d in data:
            m = re.match(r"/company/([^/]+)/", d.get("url") or "")
            if d.get("id") and m:
                out.append({"symbol": m.group(1), "name": d["name"]})
        return out

    try:
        return db.cached(f"scr_search:{q.lower()}", 24 * 3600, load) or []
    except Exception as e:  # noqa: BLE001
        log.warning("search failed: %s", e)
        return []


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").replace("%", "").replace("₹", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _table(soup: BeautifulSoup, section_id: str) -> dict | None:
    table = soup.select_one(f"section#{section_id} table.data-table")
    if not table:
        return None
    headers = [th.get_text(strip=True) for th in table.select("thead th")][1:]
    rows = {}
    for tr in table.select("tbody tr"):
        tds = tr.select("td")
        if not tds:
            continue
        label = re.sub(r"\s*\+\s*$", "", tds[0].get_text(" ", strip=True)).strip()
        if not label or label.lower().startswith("raw pdf"):
            continue
        rows[label] = [_num(td.get_text(strip=True)) for td in tds[1:]]
    return {"headers": headers, "rows": rows}


def _documents(soup: BeautifulSoup) -> dict:
    annual, concalls = [], []
    for blk in soup.select("section#documents .documents"):
        h = blk.select_one("h3")
        title = h.get_text(strip=True).lower() if h else ""
        for li in blk.select("li"):
            if "annual" in title:
                a = li.select_one("a")
                if a and a.get("href"):
                    m = re.search(r"(20\d\d)", a.get_text())
                    annual.append({"year": m.group(1) if m else a.get_text(strip=True), "url": a["href"]})
            elif "concall" in title:
                period_txt = re.match(r"([A-Z][a-z]{2} \d{4})", li.get_text(" ", strip=True))
                links = {a.get_text(strip=True).lower(): a.get("href") for a in li.select("a") if a.get("href")}
                transcript = next((v for k, v in links.items() if "transcript" in k), None)
                ppt = next((v for k, v in links.items() if k == "ppt"), None)
                concalls.append({
                    "period": period_txt.group(1) if period_txt else "",
                    "transcript": transcript,
                    "ppt": ppt,
                })
    return {"annual_reports": annual, "concalls": concalls}


def _parse(html: str, symbol: str, consolidated: bool) -> dict:
    soup = BeautifulSoup(html, "lxml")
    name_el = soup.select_one("h1")
    ratios = {}
    for li in soup.select("#top-ratios li"):
        n, v = li.select_one(".name"), li.select_one(".value")
        if n and v:
            ratios[n.get_text(strip=True)] = v.get_text(" ", strip=True).replace("\n", " ")
    sector = [a.get_text(strip=True) for a in soup.select("section#peers a[href*='/market/']")]
    return {
        "symbol": symbol,
        "name": name_el.get_text(strip=True) if name_el else symbol,
        "basis": "consolidated" if consolidated else "standalone",
        "sector": sector[:3],
        "ratios": ratios,
        "quarters": _table(soup, "quarters"),
        "pnl": _table(soup, "profit-loss"),
        "balance_sheet": _table(soup, "balance-sheet"),
        "cash_flow": _table(soup, "cash-flow"),
        "ratios_table": _table(soup, "ratios"),
        "documents": _documents(soup),
        "source_url": f"{BASE}/company/{symbol}/{'consolidated/' if consolidated else ''}",
    }


def _has_data(d: dict) -> bool:
    q = d.get("quarters")
    return bool(q and q["rows"] and any(v is not None for row in q["rows"].values() for v in row))


def company(symbol: str) -> dict | None:
    """Fundamentals for a symbol. Prefers consolidated numbers, falls back to standalone."""
    symbol = symbol.upper().strip()

    def load():
        for consolidated in (True, False):
            url = f"{BASE}/company/{symbol}/{'consolidated/' if consolidated else ''}"
            try:
                html = net.get_text(url)
            except Exception as e:  # noqa: BLE001
                log.info("screener %s failed: %s", url, e)
                continue
            d = _parse(html, symbol, consolidated)
            if _has_data(d):
                return d
        return None

    return db.cached(f"screener:{symbol}", 12 * 3600, load)
