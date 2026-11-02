# FINN: Financial Investment Agent

A mobile-first agent for investors in Indian listed companies. It filters the daily flood of
filings and price moves down to the few that matter, and researches any NSE company from its
own annual report and earnings calls.

- **Push:** what changed across your portfolio, ranked by importance, plus alerts on thresholds
  you set, delivered as phone/browser notifications.
- **Pull:** on-demand research on any NSE company, grounded only in primary sources, with
  "promised vs delivered" over the last four earnings calls.

**Live app:** https://finn-investment-agent-ten.vercel.app (open on a phone; on iPhone, use
*Share → Add to Home Screen* to get notifications)
**API:** https://finn-api-1xjc.onrender.com/health

## What's in it

| Screen | What it does |
|---|---|
| **Brief** | One sentence on the window you pick (24h / 3 days / 1 week / 30 days), a *Pulse* strip of the day's biggest movers, then filings in three tiers: **Needs attention**, **Worth knowing**, and **Routine** (collapsed). Rule-ranked filings appear in ~2s; AI one-line summaries fill in after. Results filings open into **Revenue / EBITDA / PBT / PAT, QoQ and YoY**, read from the actual results PDF. Management changes open into who's involved, with a short **bio of the incoming person**. Tap any card to research that company. |
| **Alerts** | Your rules: daily price move beyond ±X%, volume above N× its 20-day average, and which filing types matter. Each move is explained in plain words ("a conviction move" vs "thin volume") and linked to any filing from the same stock. **Push notifications** (even with the app closed), a badge on the tab, an in-app banner, "new since your last visit" marks, and a *Send me a test alert* button. |
| **Research** | Any NSE ticker. Snapshot, trajectory, balance sheet and cash quality are computed in code from reported financials. The AI adds **promised vs delivered** (last 4 calls), story vs numbers, current guidance and bull/bear, every claim tagged with its source (`[Aug 2026 call]`, `[AR 2026 p.34]`). The analysis **streams in** section by section as it's written. |
| **Ask FINN** | A floating chat (bottom-left, every screen) that answers questions about a company from its filings, with page citations and follow-up questions. |
| **Portfolio** | 20 holdings across sectors; add or remove any NSE company. |

## How it works

```
Sources                         FastAPI backend (Render)                          App
──────────────────────          ─────────────────────────────────────────         ─────────────────────
NSE announcements / RSS ──┐     rules: classify + score every filing (free)
Yahoo end-of-day prices ──┤     cheap model: one-liners, results PDFs,
screener.in financials ───┼──►               transcript notes                ──►  Next.js PWA (Vercel)
filing PDFs (NSE / BSE) ──┘     strong model: one synthesis per company            + push notifications
                                scheduler: filings, prices, alerts → push
                                storage: Neon Postgres (durable) + SQLite cache
```

### Push
- **Filings** are classified with keyword rules into the reference categories (results, M&A,
  management change, pledging…) and scored 0–100. Only vague or important filings go to the
  cheap model, batched 30 per call, for a one-line summary and a ±15 nudge to the score.
- **Results cards**: the 3 most relevant pages of the results PDF go to the cheap model, which
  copies the figures. EBITDA (PBT + finance cost + D&A − other income), QoQ and YoY are computed
  in code. Lenders get "not meaningful" instead of EBITDA.
- **Management changes**: the filing names the people; a grounded web search adds a short bio.
- **Scheduler** (inside the backend): new filings every 15 min in the day (30 min evenings, hourly
  overnight), end-of-day prices at 4:05 pm IST. Each alert is recorded once, so every event is
  pushed exactly once, bundled if several fire together, and held during quiet hours (10 pm–7 am).

### Pull
1. Financials from screener.in → metrics computed in Python (CAGR, margins, D/E, working-capital
   days, cash conversion, red flags). No model arithmetic.
2. Last 4 earnings-call transcripts → filtered to forward-looking passages → cheap model extracts
   management's guidance (one call per transcript, in parallel, cached forever).
3. Latest annual report → chunked and searched with BM25 → ~12 relevant passages (not 400 pages).
4. One strong-model call compares promises with what later quarters delivered, streamed to the page.

**Grounding:** research uses only primary sources: annual report, transcripts, and financials
standardised from filings. No news or broker notes, and the model is told not to use outside
knowledge. Push-side bios are the one place web search is used (as the brief allows).

### Cost and speed discipline
- **Model routing:** Gemini Flash-Lite for bulk extraction, Flash only for the final reasoning
  step. Fallbacks: backup model → **Groq** (`gpt-oss`, a different provider, so Google's busy
  spells don't stop the app) → the other tier. Several Gemini keys can be rotated.
- **Time limits everywhere:** 20s/40s per AI call, one quick retry on "model busy", PDF downloads
  that stall for 7s are retried once. Nothing can hang a screen.
- **Caching:** AI results (research, transcript notes, one-liners, cards) are kept in Postgres and
  never paid for twice; prices are kept until the next market close; PDFs and text on disk.
- **Every screen shows its model calls and tokens.** The Brief and Alerts work with no AI at all.

### Hosting notes
- NSE's and BSE's announcement APIs refuse cloud servers (Akamai 403 / silent hang). The backend
  detects this, stops calling them for 30 min, and takes new filings from **NSE's RSS feed**
  (matched to symbols via NSE's official company list). The feed only keeps a few hours, so each
  company is also re-checked on screener.in every 3 hours to catch anything published while the
  server slept. Locally the full NSE API is used. Filing PDFs download fine from the cloud.
- If the last 24 hours have no filings, the Brief opens on the last 3 days instead of an empty screen.
- Render's free plan sleeps after 15 idle minutes; a GitHub Actions job pings `/health` every
  5 minutes so the scheduler keeps running.

## Project structure

```
backend/app/
  main.py               API routes
  scheduler.py          background jobs: filings, prices, alerts → push
  llm.py                model routing: cheap/strong tiers, fallbacks, Groq, key rotation, streaming
  db.py                 SQLite cache + optional Postgres (Neon) for durable data
  net.py, obs.py        HTTP helpers, timed logging with plain-English explanations
  portfolio.py          default portfolio + alert settings
  services/
    announcements.py    NSE API / RSS feed / screener backfill → ranked digest
    classifier.py       categories + importance rules
    alerts.py           alert register (new / pushed), unread counts
    push.py             web push (VAPID), bundling, quiet hours
    insights.py         results table, management-change bios
    prices.py           EOD prices, unusual moves
    screener.py         fundamentals + document links
    metrics.py          computed financial analysis
    documents.py        PDF text, chunking, BM25
    research.py         research pipeline, streaming analysis, chat answers
frontend/src/
  app/                  Brief, Alerts, Research, Portfolio screens (Next.js 16, React 19, Tailwind)
  components/           filing cards, chat widget, bottom nav (badge + banner)
  public/sw.js          service worker: shows notifications, opens the right screen
render.yaml             Render blueprint (backend)
.github/workflows/      keep-awake ping
```

## Run locally

### Backend
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add GEMINI_API_KEY (optional: GROQ_API_KEY, DATABASE_URL)
cd backend
python -m uvicorn app.main:app --reload   # http://localhost:8000/docs
```

### Frontend
```bash
cd frontend
cp .env.example .env.local        # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev                       # http://localhost:3000
```

### Configuration (`.env`)
| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | One key, or several separated by commas (a random one per call) |
| `GROQ_API_KEY` | Optional backup provider when Gemini is busy |
| `DATABASE_URL` | Optional Postgres (e.g. Neon) so saved data survives redeploys; otherwise SQLite |
| `FRONTEND_ORIGIN` | Frontend URL allowed to call the API |
| `SCHEDULER_ENABLED` | `false` to turn off background jobs |
| `LOG_LEVEL`, `SLOW_SECONDS` | Logging detail; steps slower than this are flagged `SLOW` |

Logs go to the terminal and `backend/data/logs/finn.log`, one line per step with timings and a
plain-English note on what's happening. `GET /jobs/status` shows the scheduler.
