"""Deeper cards for the filings that deserve them:
- Results: Revenue / EBITDA / PBT / PAT vs last quarter and the same quarter last year.
- Management change: who is coming in, with a short professional bio.
"""

import logging

from pydantic import BaseModel

from app import db, llm
from app.services import documents, screener

log = logging.getLogger("finn.insights")


# ---------- Results ----------

class Period(BaseModel):
    label: str
    revenue: float | None = None
    other_income: float | None = None
    finance_costs: float | None = None
    depreciation: float | None = None
    pbt: float | None = None
    pat: float | None = None


class ResultsExtract(BaseModel):
    basis: str
    unit: str
    is_lender: bool
    current: Period
    previous_quarter: Period
    year_ago_quarter: Period


RESULTS_PROMPT = """Below are pages from an Indian listed company's quarterly results filing.
Extract figures from the statement of profit and loss for three columns:
the quarter just ended (current), the immediately preceding quarter (previous_quarter),
and the same quarter one year earlier (year_ago_quarter).
Prefer the CONSOLIDATED statement if present; otherwise standalone. Say which in `basis`.
- revenue = Revenue from operations (for banks/NBFCs: total income).
- other_income, finance_costs (interest/finance cost), depreciation (incl. amortisation).
- pbt = Profit before tax (after exceptional items). pat = Net profit for the period (attributable total).
- unit: the unit stated on the statement, e.g. "₹ crore", "₹ lakh", "₹ million".
- is_lender: true for banks, NBFCs and insurers.
Copy numbers exactly as printed (negative for losses). Use null if a figure is not present.
Labels like "Q2 FY27" or "Sep 2026".

{pages}"""


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def _ebitda(p: dict, lender: bool) -> float | None:
    if lender:
        return None
    if p.get("pbt") is None or p.get("depreciation") is None or p.get("finance_costs") is None:
        return None
    return round(p["pbt"] + p["finance_costs"] + p["depreciation"] - (p.get("other_income") or 0), 2)


def _results_table(cur: dict, prev: dict, yago: dict, lender: bool) -> list[dict]:
    rows = []
    for key, label in (("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")):
        c, p, y = cur.get(key), prev.get(key), yago.get(key)
        rows.append({
            "metric": label,
            "value": c,
            "prev": p,
            "year_ago": y,
            "qoq_pct": _pct(c, p),
            "yoy_pct": _pct(c, y),
            "na": key == "ebitda" and lender,
        })
    return rows


def _margin_line(cur: dict, yago: dict) -> str | None:
    """A one-line, number-only read of the quarter. No model involved."""
    rev_c, rev_y = cur.get("revenue"), yago.get("revenue")
    pat_c, pat_y = cur.get("pat"), yago.get("pat")
    if not (rev_c and rev_y and pat_c is not None and pat_y is not None):
        return None
    rg, pg = _pct(rev_c, rev_y), _pct(pat_c, pat_y)
    m_c, m_y = pat_c / rev_c * 100, pat_y / rev_y * 100
    bps = round((m_c - m_y) * 100)
    direction = "expanded" if bps > 0 else "compressed"
    return f"Revenue {rg:+.1f}% YoY, PAT {pg:+.1f}% YoY; net margin {direction} {abs(bps)} bps to {m_c:.1f}%."


def _from_screener(symbol: str) -> dict | None:
    """Fallback when the filing can't be read: the latest quarter from screener.in."""
    d = screener.company(symbol)
    q = d and d.get("quarters")
    if not q:
        return None
    rows, h = q["rows"], q["headers"]
    if len(h) < 5:
        return None

    def col(i: int) -> dict:
        def get(*names):
            for n in names:
                if n in rows and rows[n][i] is not None:
                    return rows[n][i]
            return None
        lender = "Financing Profit" in rows
        return {
            "label": h[i],
            "revenue": get("Sales", "Revenue"),
            "ebitda": None if lender else get("Operating Profit"),
            "pbt": get("Profit before tax"),
            "pat": get("Net Profit"),
        }

    lender = "Financing Profit" in rows
    cur, prev, yago = col(-1), col(-2), col(-5)
    return {
        "source": "screener.in (standardised from filings)",
        "basis": d["basis"],
        "unit": "₹ crore",
        "is_lender": lender,
        "periods": {"current": cur["label"], "previous": prev["label"], "year_ago": yago["label"]},
        "rows": _results_table(cur, prev, yago, lender),
        "read": _margin_line(cur, yago),
    }


def results_card(item: dict) -> dict:
    def from_filing():
        pages = documents.pages(item["url"])
        picked = documents.best_pages(
            pages,
            ["profit before tax", "revenue from operations", "total income", "quarter ended", "three months ended",
             "finance cost", "depreciation", "net profit"],
            k=3,
            prefer="consolidated",
        )
        log.info("results %s: picked pages %s of %d", item["symbol"], [i + 1 for i, _ in picked], len(pages))
        data = None
        if picked and llm.available():
            text = "\n\n".join(f"[page {i + 1}]\n{t[:7000]}" for i, t in picked)
            data = llm.generate_json(RESULTS_PROMPT.format(pages=text), ResultsExtract, tier="cheap")
        if data and data.current.revenue is not None:
            d = data.model_dump()
            cur, prev, yago = d["current"], d["previous_quarter"], d["year_ago_quarter"]
            for p in (cur, prev, yago):
                p["ebitda"] = _ebitda(p, d["is_lender"])
            return {
                "source": "results filing",
                "basis": d["basis"],
                "unit": d["unit"],
                "is_lender": d["is_lender"],
                "periods": {"current": cur["label"], "previous": prev["label"], "year_ago": yago["label"]},
                "rows": _results_table(cur, prev, yago, d["is_lender"]),
                "read": _margin_line(cur, yago),
                "pages": [i + 1 for i, _ in picked],
            }
        return None

    # The filing is the primary source: once read, keep it. The screener fallback is cached briefly
    # so the filing gets read as soon as the model is available.
    out = db.cached(f"insight:results:{item['id']}", None, from_filing)
    if out is None:
        out = db.cached(f"insight:results_fallback:{item['symbol']}", 3600, lambda: _from_screener(item["symbol"]))
    return {"kind": "results", **(out or {"error": "Couldn't read the results from this filing."})}


# ---------- Management change ----------

class Person(BaseModel):
    name: str
    role: str
    change: str  # appointed | resigned | retired | reappointed | ceased | other
    effective_date: str | None = None
    reason: str | None = None
    profile_from_filing: str | None = None


class People(BaseModel):
    people: list[Person]


MGMT_PROMPT = """From this Indian exchange filing about a management change, list each person involved.
- change: one of appointed, resigned, retired, reappointed, ceased, other.
- reason: only if the filing states one (short).
- profile_from_filing: if the filing includes a brief profile, summarise it in max 30 words; else null.

Filing:
{text}"""

BIO_PROMPT = """Give a short professional background of {name}, who is joining {company} (India) as {role}.
3 to 4 bullet points, each under 18 words: previous roles with company names and years, education,
and anything notable. Use LinkedIn and official company sources where possible.
If you cannot confidently identify this person, reply with exactly: UNKNOWN"""


def management_card(item: dict) -> dict:
    def build():
        pages = documents.pages(item["url"], max_pages=6)
        text = "\n".join(pages)[:9000] or item["text"]
        if not llm.available():
            return None
        parsed = llm.generate_json(MGMT_PROMPT.format(text=text), People, tier="cheap")
        if not parsed:
            return None
        people = []
        for p in parsed.people[:4]:
            d = p.model_dump()
            d["bio"] = None
            d["sources"] = []
            if p.change in ("appointed",) and len([x for x in people if x.get("bio")]) < 2:
                res = llm.search_grounded(BIO_PROMPT.format(name=p.name, company=item["company"], role=p.role))
                if res and "UNKNOWN" not in res["text"]:
                    d["bio"] = res["text"].strip()
                    d["sources"] = res["sources"]
            people.append(d)
        return {"people": people}

    out = db.cached(f"insight:mgmt:{item['id']}", None, build)
    if out is None:
        return {"kind": "management", "error": "Add a Gemini API key to read management changes."
                if not llm.available() else "Couldn't read this filing."}
    return {"kind": "management", **out}
