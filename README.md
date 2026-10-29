# FINN: Financial Investment Agent

A mobile-first agent for investors in Indian listed companies (NSE).

- **Push:** what changed across your portfolio, ranked by what matters, plus alerts on thresholds you set.
- **Pull:** on-demand research on any NSE company, grounded only in its annual report and earnings-call transcripts.

**Live demo:** _coming soon_

## What's in it

| Screen | What it does |
|---|---|
| **Brief** | One-sentence summary of the window (24h / 3d / 1w / 30d), a "Pulse" strip of biggest movers, then filings in three tiers: *Needs attention*, *Worth knowing*, and *Routine* (collapsed). Results filings open into a Revenue / EBITDA / PBT / PAT table (QoQ, YoY). Management changes open into who's involved, with a short bio of the incoming person. Every card has **Dig in →**, which opens research on that company. |
| **Alerts** | Thresholds you set (daily price move, volume vs 20-day average, filing types). Each market move is read in plain English (e.g. "conviction move" vs "thin volume") and linked to any filing from the same stock around that day. |
| **Research** | Any NSE company. Snapshot, trajectory, balance sheet and cash quality are computed from reported financials. The AI adds **promised vs delivered** over the last 4 calls, story vs numbers, bull/bear, and "ask the filings" Q&A, all with source tags. |
| **Portfolio** | 20 holdings across sectors; add or remove any NSE company. |

## How it works

```
NSE announcements ─┐                       ┌─ rules classify + score (free)
Yahoo EOD prices ──┼─► FastAPI backend ────┼─ cheap model: triage one-liners, read results PDFs, parse mgmt changes
screener.in ───────┘   (SQLite cache)      └─ strong model: one synthesis call per company
 (financials, AR + transcript links)
```

**Cost discipline**
- Keyword rules classify and rank filings first; the model only sees vague or important ones, batched into one call.
- Financial metrics (growth, margins, D/E, cash conversion, working capital) are computed in Python, never generated.
- Results PDFs: only the 3 most relevant pages go to the model. EBITDA and growth rates are computed in code.
- Transcripts are filtered to forward-looking passages before extraction. The annual report is searched with BM25, so only about 12 passages are sent, not 300 pages.
- Cheap model (Flash-Lite) for bulk extraction; stronger model (Flash) only for the final reasoning step.
- Everything is cached (SQLite + PDFs on disk). Each screen shows how many model calls and tokens it used.

**Grounding:** research uses only primary sources (annual report, earnings-call transcripts, and financials standardised from filings). No news, no broker notes. Claims carry source tags like `[Aug 2026 call]` or `[AR 2026 p.34]`.

**Works without an API key:** ranking, alerts, results numbers (from screener.in) and all computed research metrics run with no AI at all.

## Project structure

```
backend/app/
  main.py               API routes
  llm.py                Gemini wrapper: cheap/strong tiers, retries, token tracking
  portfolio.py          default portfolio + alert settings
  services/
    announcements.py    NSE filings → digest
    classifier.py       categories + importance rules
    insights.py         results table, management-change bios
    prices.py           EOD prices, unusual moves
    screener.py         fundamentals + document links
    metrics.py          computed financial analysis
    documents.py        PDF text, chunking, BM25
    research.py         research pipeline + Q&A
frontend/src/app/       Brief, Alerts, Research, Portfolio screens (Next.js)
```

## Run locally

### Backend
```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your GEMINI_API_KEY
cd backend
uvicorn app.main:app --reload # http://localhost:8000/docs
```

### Frontend
```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev                   # http://localhost:3000
```
