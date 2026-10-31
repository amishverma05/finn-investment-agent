"""End-of-day price and volume from Yahoo Finance, plus unusual-move detection."""

import datetime as dt
import logging
import math
import warnings
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from app import db
from app.obs import timed

log = logging.getLogger("finn.prices")
warnings.filterwarnings("ignore", module="yfinance")

IST = ZoneInfo("Asia/Kolkata")
# ~40 trading days: enough for the 20-day volume average, the 1-month change and the 30-day chart.
HISTORY_PERIOD = "2mo"
# NSE closes at 3:30 pm; by 4 pm the day's final close and volume are on Yahoo.
EOD_HOUR = 16


def _num(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _download(symbols: tuple[str, ...]) -> dict:
    tickers = [s + ".NS" for s in symbols]
    with timed(log, "yahoo prices download", tickers=len(tickers)) as t:
        df = yf.download(tickers, period=HISTORY_PERIOD, interval="1d", progress=False, auto_adjust=False, group_by="ticker", threads=True)
        t["rows"] = len(df)
    out = {}
    for s in symbols:
        try:
            sub = df[s + ".NS"] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            continue
        out[s] = {
            "dates": [d.strftime("%Y-%m-%d") for d in sub.index],
            "close": [round(float(v), 2) for v in sub["Close"]],
            "volume": [int(v) if not math.isnan(v) else 0 for v in sub["Volume"]],
        }
    return out


def seconds_until_next_close(now: dt.datetime | None = None) -> float:
    """Seconds until the next 4 pm IST on a weekday: when end-of-day prices next change."""
    now = now or dt.datetime.now(IST)
    target = now.replace(hour=EOD_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target += dt.timedelta(days=1)
    while target.weekday() >= 5:  # Saturday, Sunday
        target += dt.timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def history(symbols: list[str]) -> dict:
    """Daily close/volume series for each symbol. Prices are end-of-day, so they're kept
    until the next 4 pm IST close instead of being fetched again on every visit."""
    key = "prices:" + ",".join(sorted(symbols))
    return db.cached(key, seconds_until_next_close(), lambda: _download(tuple(sorted(symbols))) or None) or {}


def snapshot(symbols: list[str], lookback: int = 20) -> dict[str, dict]:
    """Latest EOD stats per symbol: change %, volume vs its recent average, a 30-day sparkline."""
    out = {}
    for s, h in history(symbols).items():
        close, vol, dates = h["close"], h["volume"], h["dates"]
        if len(close) < 2:
            continue
        prior_vols = [v for v in vol[-(lookback + 1):-1] if v]
        avg_vol = sum(prior_vols) / len(prior_vols) if prior_vols else None
        out[s] = {
            "symbol": s,
            "date": dates[-1],
            "close": close[-1],
            "prev_close": close[-2],
            "change_pct": round((close[-1] / close[-2] - 1) * 100, 2),
            "volume": vol[-1],
            "avg_volume": round(avg_vol) if avg_vol else None,
            "volume_ratio": round(vol[-1] / avg_vol, 2) if avg_vol else None,
            "change_1m_pct": round((close[-1] / close[-22] - 1) * 100, 2) if len(close) > 22 else None,
            "spark": close[-30:],
        }
    return out


def unusual_moves(symbols: list[str], price_move_pct: float, volume_multiple: float, lookback: int, days: int = 5) -> list[dict]:
    """Scan the last `days` sessions for moves beyond the user's thresholds."""
    alerts = []
    for s, h in history(symbols).items():
        close, vol, dates = h["close"], h["volume"], h["dates"]
        for i in range(max(1, len(close) - days), len(close)):
            chg = (close[i] / close[i - 1] - 1) * 100
            prior = [v for v in vol[max(0, i - lookback):i] if v]
            avg = sum(prior) / len(prior) if prior else None
            ratio = vol[i] / avg if avg else None
            hit_price = abs(chg) >= price_move_pct
            hit_vol = ratio is not None and ratio >= volume_multiple
            if not (hit_price or hit_vol):
                continue
            alerts.append({
                "kind": "market",
                "symbol": s,
                "date": dates[i],
                "change_pct": round(chg, 2),
                "volume_ratio": round(ratio, 2) if ratio else None,
                "close": close[i],
                "price_trigger": hit_price,
                "volume_trigger": hit_vol,
            })
    alerts.sort(key=lambda a: (a["date"], abs(a["change_pct"])), reverse=True)
    return alerts


def refresh(symbols: list[str]) -> dict:
    """Fetch prices again now (used by the scheduler right after the 4 pm close)."""
    key = "prices:" + ",".join(sorted(symbols))
    db.cache_delete(key)
    return history(symbols)
