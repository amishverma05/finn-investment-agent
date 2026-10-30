"""Pull side: on-demand research grounded only in primary sources
(annual report + earnings-call transcripts + financials standardised from filings).

Pipeline
  1. screener.in tables -> deterministic metrics (no model)
  2. last 4 transcripts -> guidance statements, one cheap-model call each (keyword-filtered text)
  3. latest annual report -> BM25 picks the relevant chunks (no model)
  4. one strong-model call -> promised vs delivered, narrative vs numbers, bull/bear
"""

import datetime as dt
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from pydantic import BaseModel, Field

from app import db, llm
from app.obs import SLOW_SECONDS, timed
from app.services import documents, metrics, screener

log = logging.getLogger("finn.research")

GUIDANCE_KW = re.compile(
    r"guid|expect|target|outlook|aim|aspir|going forward|next year|next quarter|medium term|"
    r"fy ?\d\d|double.digit|margin|growth|capex|capacity|ebitda|volume|order book|pipeline|%",
    re.I,
)


# ---------- Schemas ----------

class Statement(BaseModel):
    topic: str = Field(description="revenue, margin, capex, volume, orders, debt or other")
    statement: str = Field(description="what management said they expect, max 25 words")
    quote: str = Field(description="short verbatim quote, max 25 words")
    horizon: str = Field(description="period it refers to, e.g. FY27, next quarter, medium term")
    quantified: bool


class CallNotes(BaseModel):
    guidance: list[Statement]
    tone: str = Field(description="management tone in max 12 words")
    analyst_concerns: list[str] = Field(description="up to 3 things analysts pressed on, max 15 words each")


class Segment(BaseModel):
    name: str
    share_pct: float | None = None
    note: str


class Promise(BaseModel):
    made_in: str = Field(description="which call, e.g. Aug 2026")
    metric: str
    promised: str
    delivered: str = Field(description="what the numbers show, citing the period")
    verdict: str = Field(description="delivered, partly, missed, pending or unclear")


class Divergence(BaseModel):
    claim: str = Field(description="management's claim, with source tag")
    numbers_say: str
    verdict: str = Field(description="agrees, diverges or unclear")


class Analysis(BaseModel):
    one_liner: str = Field(description="the single most important takeaway, max 25 words")
    business: str = Field(description="what the company does and how it makes money, max 60 words")
    segments: list[Segment]
    promises: list[Promise]
    narrative_vs_numbers: list[Divergence]
    current_guidance: list[str]
    bull: list[str]
    bear: list[str]
    watch: list[str] = Field(description="2-3 things to check next quarter")


# ---------- Steps ----------

def _filter_transcript(pages: list[str], limit: int = 28000) -> str:
    text = "\n".join(pages)
    paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.?!])\s{2,}", text) if len(p.strip()) > 60]
    kept = [p for p in paras if GUIDANCE_KW.search(p)]
    out = "\n".join(kept)
    return out[:limit] if out else text[:limit]


CALL_PROMPT = """This is an earnings-call transcript ({period}) of {name}, filtered to forward-looking passages.
Extract management's explicit guidance and growth commentary on headline numbers: revenue/volume growth,
margins, capex, order book, debt. Only what management said, not analysts. Skip vague platitudes.
Max 8 statements.

{text}"""


def _call_notes(name: str, call: dict) -> dict | None:
    url = call.get("transcript")
    if not url:
        return None

    def build():
        with timed(log, "transcript → guidance", call=call["period"]) as t:
            pages = documents.pages(url)
            if not pages:
                t["error"] = "no text"
                return None
            text = _filter_transcript(pages)
            t["pages"] = len(pages)
            t["kept_chars"] = len(text)
            out = llm.generate_json(
                CALL_PROMPT.format(period=call["period"], name=name, text=text),
                CallNotes, tier="cheap",
            )
            t["statements"] = len(out.guidance) if out else 0
            return {"period": call["period"], "url": url, **out.model_dump()} if out else None

    return db.cached(f"callnotes:{url}", None, build)


AR_QUERIES = [
    "business segments revenue share products customers geography",
    "strategy outlook priorities growth drivers capacity expansion",
    "risks concerns challenges competition raw material",
    "management discussion analysis performance during the year",
]


def _ar_excerpts(ar: dict | None, k_per_query: int = 3) -> list[dict]:
    if not ar:
        return []
    with timed(log, "annual report → passages", year=ar["year"]) as t:
        pages = documents.pages(ar["url"])
        if not pages:
            t["error"] = "no text"
            return []
        idx = documents.Index(documents.chunk(pages, source=f"AR {ar['year']}"))
        seen, out = set(), []
        for q in AR_QUERIES:
            for c in idx.search(q, k=k_per_query):
                key = (c["page"], c["text"][:50])
                if key not in seen:
                    seen.add(key)
                    out.append(c)
        t["pages"] = len(pages)
        t["passages"] = len(out)
        return out


SYNTH_SYSTEM = """You are a sceptical buy-side analyst. You analyse management; you don't summarise them.
Ground every statement ONLY in the material provided: financial tables, earnings-call notes and annual-report
excerpts. Do not use outside knowledge, news or broker views about this company. If the material doesn't
support a point, leave it out or mark it unclear. Tag claims with their source in square brackets,
e.g. [Aug 2026 call], [AR 2026 p.34], [Financials]. Be concrete and use numbers. No filler."""

SYNTH_PROMPT = """Company: {name} ({symbol}). Figures in ₹ crore. {lender_note}

FINANCIALS (computed from filings)
Quarterly (oldest → latest):
{quarters}
Annual:
{annual}
3y revenue CAGR {rc3}%, 3y PAT CAGR {pc3}%. 5y cash conversion (CFO/PAT): {conv}.
Flags: {flags}

EARNINGS-CALL NOTES (latest last)
{calls}

ANNUAL-REPORT EXCERPTS
{ar}

Tasks:
1. one_liner, business, segments (with revenue share only if stated).
2. promises: for each quantified guidance in the calls, compare with what later quarters actually delivered
   (use the quarterly table). If the period hasn't reported yet, verdict = pending. 4 to 8 items, most important first.
3. narrative_vs_numbers: 2 to 4 places where management's story agrees or diverges with the numbers.
4. current_guidance: the latest explicit guidance, short bullets.
5. bull and bear: 3 tight points each, grounded in the material.
6. watch: 2 to 3 things to check next quarter."""


def _fmt_quarters(qs: list[dict]) -> str:
    return "\n".join(
        f"{q['period']}: revenue {q['revenue']}, op profit {q['op_profit']}, OPM {q['opm']}%, PAT {q['pat']}, "
        f"revenue YoY {q['revenue_yoy']}%, PAT YoY {q['pat_yoy']}%"
        for q in qs
    )


def _fmt_annual(ys: list[dict]) -> str:
    return "\n".join(f"{y['year']}: revenue {y['revenue']}, OPM {y['opm']}%, PAT {y['pat']}" for y in ys)


def _synthesise(d: dict, m: dict, calls: list[dict], ar_chunks: list[dict], on_partial=None) -> Analysis | None:
    calls_txt = "\n\n".join(
        f"[{c['period']} call] tone: {c['tone']}\n"
        + "\n".join(f"- ({s['topic']}, {s['horizon']}) {s['statement']}" for s in c["guidance"])
        + ("\nAnalysts pressed on: " + "; ".join(c["analyst_concerns"]) if c["analyst_concerns"] else "")
        for c in calls
    ) or "No transcripts available."
    ar_txt = "\n\n".join(f"[{c['source']} p.{c['page']}]\n{c['text'][:1400]}" for c in ar_chunks) or "No annual report available."
    prompt = SYNTH_PROMPT.format(
        name=d["name"], symbol=d["symbol"],
        lender_note="This is a lender: 'revenue' is interest income, 'op profit' is financing profit." if m["lender"] else "",
        quarters=_fmt_quarters(m["quarters"]),
        annual=_fmt_annual(m["trajectory"]["years"]),
        rc3=m["trajectory"]["revenue_cagr_3y"], pc3=m["trajectory"]["pat_cagr_3y"],
        conv=m["cash"]["conversion_5y"],
        flags="; ".join(f["text"] for f in m["flags"]) or "none",
        calls=calls_txt, ar=ar_txt,
    )
    # Streamed: the page shows each section as soon as the model has written it.
    return llm.stream_json(prompt, Analysis, tier="strong", system=SYNTH_SYSTEM, on_partial=on_partial)


# ---------- Public API ----------

def numbers(symbol: str) -> dict | None:
    """Fast part: fundamentals and computed metrics. No model calls."""
    d = screener.company(symbol)
    if not d:
        return None
    return {
        "symbol": d["symbol"],
        "name": d["name"],
        "basis": d["basis"],
        "sector": d["sector"],
        "ratios": d["ratios"],
        "metrics": metrics.compute(d),
        "documents": {
            "annual_reports": d["documents"]["annual_reports"][:3],
            "concalls": d["documents"]["concalls"][:4],
        },
        "source_url": d["source_url"],
    }


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _run(symbol: str, job: dict) -> None:
    usage = llm.track_usage()
    started = time.perf_counter()
    step_start: dict[int, float] = {}
    log.info("research %s: started", symbol)

    def step(i: int, state: str, label: str | None = None):
        job["steps"][i]["state"] = state
        if label:
            job["steps"][i]["label"] = label
        name = job["steps"][i]["label"]
        if state == "running":
            step_start[i] = time.perf_counter()
            log.info("research %s: step %d started — %s", symbol, i + 1, name)
        elif state == "done":
            took = time.perf_counter() - step_start.get(i, started)
            log.log(logging.WARNING if took >= SLOW_SECONDS else logging.INFO,
                    "research %s: step %d done in %.2fs%s — %s", symbol, i + 1, took, " SLOW" if took >= SLOW_SECONDS else "", name)

    try:
        step(0, "running")
        d = screener.company(symbol)
        if not d:
            raise ValueError(f"No data found for {symbol}.")
        m = metrics.compute(d)
        step(0, "done")

        calls_meta = [c for c in d["documents"]["concalls"] if c.get("transcript")][:4]
        ar = d["documents"]["annual_reports"][0] if d["documents"]["annual_reports"] else None

        step(1, "running", f"Reading {len(calls_meta)} earnings calls")
        step(2, "running", f"Reading annual report {ar['year']}" if ar else "No annual report found")
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=5) as pool:
            call_futs = [pool.submit(ctx.copy().run, _call_notes, d["name"], c) for c in calls_meta]
            ar_fut = pool.submit(ctx.copy().run, _ar_excerpts, ar)
            calls = [f.result() for f in call_futs]
            ar_chunks = ar_fut.result()
        calls = [c for c in calls if c][::-1]  # oldest first
        step(1, "done", f"Read {len(calls)} earnings calls")
        step(2, "done", f"Picked {len(ar_chunks)} passages from annual report {ar['year']}" if ar else "No annual report found")

        step(3, "running")
        analysis = _synthesise(d, m, calls, ar_chunks, on_partial=lambda p: job.update(partial=p))
        job.pop("partial", None)
        step(3, "done")
        if not analysis:
            raise ValueError("The model didn't return an analysis. Try again in a minute.")

        result = {
            "analysis": analysis.model_dump(),
            "calls": calls,
            "sources": {
                "annual_report": ar,
                "transcripts": [{"period": c["period"], "url": c["url"]} for c in calls],
                "ar_pages": sorted({c["page"] for c in ar_chunks}),
            },
            "usage": usage.as_dict(),
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        db.cache_set(f"research:{symbol}", result, ttl=24 * 3600)
        job.update(status="done", result=result)
        u = usage.as_dict()
        log.info("research %s: finished in %.2fs — %d model calls, %d tokens in, %d out",
                 symbol, time.perf_counter() - started, u["calls"], u["input_tokens"], u["output_tokens"])
    except Exception as e:  # noqa: BLE001
        log.exception("research failed for %s after %.2fs", symbol, time.perf_counter() - started)
        job.update(status="error", error=str(e))
        for s in job["steps"]:
            if s["state"] == "running":
                s["state"] = "error"


def start(symbol: str, refresh: bool = False) -> dict:
    symbol = symbol.upper()
    if not llm.available():
        return {"status": "no_ai"}
    if not refresh:
        hit = db.cache_get(f"research:{symbol}")
        if hit:
            return {"status": "done", "result": hit, "steps": []}
    with _jobs_lock:
        job = _jobs.get(symbol)
        if job and job["status"] == "running":
            return job
        job = {
            "status": "running",
            "steps": [
                {"label": "Reading financials", "state": "pending"},
                {"label": "Reading earnings calls", "state": "pending"},
                {"label": "Reading annual report", "state": "pending"},
                {"label": "Checking promises against results", "state": "pending"},
            ],
        }
        _jobs[symbol] = job
    threading.Thread(target=_run, args=(symbol, job), daemon=True, name=f"research-{symbol}").start()
    return job


def status(symbol: str) -> dict:
    symbol = symbol.upper()
    job = _jobs.get(symbol)
    if job:
        return job
    hit = db.cache_get(f"research:{symbol}")
    if hit:
        return {"status": "done", "result": hit, "steps": []}
    return {"status": "idle"}


# ---------- Ask (follow-up questions) ----------

class Answer(BaseModel):
    answer: str = Field(description="direct answer, max 120 words, with [source] tags")
    confidence: str = Field(description="high, medium or low")


ASK_SYSTEM = SYNTH_SYSTEM + (
    "\nYou are answering in a chat. If the material doesn't answer the question, say so plainly."
    "\nUse the conversation so far only to understand follow-ups (e.g. 'what about margins?'); "
    "facts must still come from the excerpts and financials."
)

# Search index per company, kept in memory: building it means chunking ~500 pages,
# so follow-up questions reuse it. Keyed by the document links, so new documents rebuild it.
_indexes: dict[str, tuple[tuple, documents.Index]] = {}
_indexes_lock = threading.Lock()


def _company_index(symbol: str, d: dict) -> documents.Index:
    calls = [c for c in d["documents"]["concalls"] if c.get("transcript")][:4]
    ar = d["documents"]["annual_reports"][0] if d["documents"]["annual_reports"] else None
    fingerprint = tuple(c["transcript"] for c in calls) + ((ar["url"],) if ar else ())
    with _indexes_lock:
        hit = _indexes.get(symbol)
    if hit and hit[0] == fingerprint:
        log.info("ask %s: reusing search index (%d chunks)", symbol, len(hit[1].chunks))
        return hit[1]
    with timed(log, "ask: gather documents", symbol=symbol) as t:
        chunks = []
        for c in calls:
            chunks += documents.chunk(documents.pages(c["transcript"]), source=f"{c['period']} call")
        if ar:
            chunks += documents.chunk(documents.pages(ar["url"]), source=f"AR {ar['year']}")
        t["chunks"] = len(chunks)
    idx = documents.Index(chunks)
    with _indexes_lock:
        if len(_indexes) >= 8:  # keep memory bounded
            _indexes.pop(next(iter(_indexes)))
        _indexes[symbol] = (fingerprint, idx)
    return idx


def ask(symbol: str, question: str, history: list[dict] | None = None) -> dict:
    """Answer a question from the company's filings. `history` is the recent chat
    ([{role: user|assistant, text}]) so follow-up questions make sense."""
    symbol = symbol.upper()
    if not llm.available():
        return {"answer": "Add a Gemini API key to ask questions.", "confidence": "low", "sources": []}
    history = [h for h in (history or []) if h.get("text")][-6:]
    # Standalone questions are cached; follow-ups depend on the conversation, so they aren't.
    key = f"ask:{symbol}:{question.strip().lower()}" if not history else None
    if key:
        hit = db.cache_get(key)
        if hit:
            return hit

    d = screener.company(symbol)
    if not d:
        return {"answer": f"No data found for {symbol}.", "confidence": "low", "sources": []}
    usage = llm.track_usage()
    m = metrics.compute(d)
    # For follow-ups, search with the previous question too ("and margins?" alone finds little).
    prev_q = next((h["text"] for h in reversed(history) if h.get("role") == "user"), "")
    hits = _company_index(symbol, d).search(f"{question} {prev_q}".strip(), k=8)
    context = "\n\n".join(f"[{c['source']} p.{c['page']}]\n{c['text'][:1500]}" for c in hits)
    convo = "\n".join(
        f"{'User' if h.get('role') == 'user' else 'You'}: {h['text'][:600]}" for h in history
    )
    prompt = (
        f"Company: {d['name']}. Figures in ₹ crore.\n"
        f"Quarterly financials:\n{_fmt_quarters(m['quarters'])}\nAnnual:\n{_fmt_annual(m['trajectory']['years'])}\n\n"
        f"Excerpts:\n{context}\n\n"
        + (f"Conversation so far:\n{convo}\n\n" if convo else "")
        + f"Question: {question}"
    )
    out = llm.generate_json(prompt, Answer, tier="strong", system=ASK_SYSTEM)
    if not out:
        return {"answer": "Couldn't answer right now. Try again in a minute.", "confidence": "low", "sources": []}
    res = {
        **out.model_dump(),
        "sources": [{"source": c["source"], "page": c["page"]} for c in hits[:5]],
        "usage": usage.as_dict(),
    }
    if key:
        db.cache_set(key, res, ttl=24 * 3600)
    return res
