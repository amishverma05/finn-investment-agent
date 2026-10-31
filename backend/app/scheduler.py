"""Background scheduler: keeps filings, prices and alerts fresh, and sends push notifications.

Runs as one thread inside the backend. Every 30 seconds it checks which jobs are due:

  filings  every 15 min 8 am-6 pm IST, every 30 min 6-10 pm, hourly otherwise
           -> fetch new NSE filings, AI one-liners for new ones, record any new alerts
  prices   once a weekday at 4:05 pm IST (after the close), and once at startup
           -> fetch end-of-day prices, record price/volume alerts
  push     every tick -> send notifications for alerts not yet pushed (not 10 pm-7 am)

Job times are saved in the database, so a restart picks up where it left off. A lock row
makes sure only one copy runs (e.g. with --reload, or several workers).
"""

import datetime as dt
import logging
import os
import threading
import time
import uuid

from app import db, llm, portfolio
from app.config import settings
from app.obs import timed
from app.services import alerts, announcements, prices, push

log = logging.getLogger("finn.scheduler")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TICK = 30          # seconds between checks
LOCK_TTL = 90      # a lock not renewed for this long is considered abandoned
ME = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"

_stop = threading.Event()
_thread: threading.Thread | None = None
_run_now: set[str] = set()


# ---------- When each job runs next ----------

def _next_filings(now: dt.datetime) -> dt.datetime:
    h = now.hour
    minutes = 15 if 8 <= h < 18 else 30 if 18 <= h < 22 else 60
    return now + dt.timedelta(minutes=minutes)


def _next_prices(now: dt.datetime) -> dt.datetime:
    return now + dt.timedelta(seconds=prices.seconds_until_next_close(now) + 5 * 60)


# ---------- The jobs ----------

def _symbols() -> list[str]:
    return [i["symbol"] for i in portfolio.get_portfolio()]


def _job_filings(_: dt.datetime) -> dict:
    usage = llm.track_usage()
    # Save new filings from NSE's RSS feed first: the only live source on servers NSE blocks.
    saved = announcements.ingest_rss(_symbols())
    db.cache_set("rss_ingested", True, ttl=10 * 60)
    new = alerts.check(hours=72)
    return {"rss_saved": saved, "new_alerts": len(new), "ai_calls": usage.calls}


def _job_prices(now: dt.datetime) -> dict:
    state = db.kv_get("job:prices") or {}
    # At startup, reuse today's saved prices; at the scheduled 4:05 pm run, fetch fresh ones.
    if state.get("last_run"):
        prices.refresh(_symbols())
    else:
        prices.history(_symbols())
    new = alerts.check(hours=72)
    return {"new_alerts": len(new)}


JOBS = {
    "filings": (_job_filings, _next_filings,
                "Checking NSE for new filings from your stocks and recording any new alerts."),
    "prices": (_job_prices, _next_prices,
               "Getting end-of-day prices and checking them against your price and volume rules."),
}


def _run(name: str, now: dt.datetime) -> None:
    fn, next_fn, what = JOBS[name]
    state = db.kv_get(f"job:{name}") or {}
    start = time.perf_counter()
    with timed(log, f"job: {name}") as t:
        log.info("    ↳ %s", what)
        try:
            info = fn(now)
            t.update(info)
            state.update(ok=True, error=None, info=info)
        except Exception as e:  # noqa: BLE001 - one bad run must not stop the scheduler
            log.exception("job %s failed", name)
            state.update(ok=False, error=str(e)[:300])
    state.update(last_run=now.isoformat(timespec="seconds"), took=round(time.perf_counter() - start, 2),
                 next_run=next_fn(dt.datetime.now(IST)).isoformat(timespec="seconds"))
    db.kv_set(f"job:{name}", state)


def _push_pending() -> None:
    waiting = alerts.pending()
    if waiting:
        done = push.flush(waiting)
        if done:
            alerts.mark_pushed(done)


# ---------- Lock + loop ----------

def _hold_lock() -> bool:
    """Take or renew the lock. Only the holder runs jobs."""
    now = time.time()
    lock = db.kv_get("scheduler_lock") or {}
    if lock.get("owner") not in (None, ME) and lock.get("until", 0) > now:
        return False
    db.kv_set("scheduler_lock", {"owner": ME, "until": now + LOCK_TTL})
    return True


def _loop() -> None:
    log.info("scheduler started (%s)", ME)
    while not _stop.is_set():
        try:
            if _hold_lock():
                now = dt.datetime.now(IST)
                for name in JOBS:
                    state = db.kv_get(f"job:{name}") or {}
                    due = state.get("next_run")
                    if name in _run_now or not due or dt.datetime.fromisoformat(due) <= now:
                        _run_now.discard(name)
                        _run(name, now)
                _push_pending()
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        _stop.wait(TICK)
    lock = db.kv_get("scheduler_lock") or {}
    if lock.get("owner") == ME:
        db.kv_set("scheduler_lock", {})
    log.info("scheduler stopped")


def start() -> None:
    global _thread
    if not settings.scheduler_enabled:
        log.info("scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=5)


def run_soon(*names: str) -> None:
    """Ask the scheduler to run these jobs on its next tick (within 30s)."""
    _run_now.update(n for n in names if n in JOBS)


def status() -> dict:
    return {
        "enabled": settings.scheduler_enabled,
        "running": bool(_thread and _thread.is_alive()),
        "jobs": {name: db.kv_get(f"job:{name}") or {} for name in JOBS},
        "push_devices": push.count(),
        "quiet_hours_now": push.quiet_now(),
    }
