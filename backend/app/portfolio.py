"""Default mock portfolio and user settings."""

from app import db

# 20 NSE companies across sectors and sizes.
DEFAULT_PORTFOLIO = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Conglomerate"},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking"},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "sector": "NBFC"},
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT Services"},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT Services"},
    {"symbol": "PERSISTENT", "name": "Persistent Systems", "sector": "IT Services"},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "sector": "Telecom"},
    {"symbol": "ITC", "name": "ITC", "sector": "FMCG"},
    {"symbol": "ASIANPAINT", "name": "Asian Paints", "sector": "Paints"},
    {"symbol": "TITAN", "name": "Titan Company", "sector": "Consumer"},
    {"symbol": "TRENT", "name": "Trent", "sector": "Retail"},
    {"symbol": "ETERNAL", "name": "Eternal (Zomato)", "sector": "Internet"},
    {"symbol": "MARUTI", "name": "Maruti Suzuki", "sector": "Auto"},
    {"symbol": "LT", "name": "Larsen & Toubro", "sector": "Infrastructure"},
    {"symbol": "HAL", "name": "Hindustan Aeronautics", "sector": "Defence"},
    {"symbol": "SUNPHARMA", "name": "Sun Pharma", "sector": "Pharma"},
    {"symbol": "POLYCAB", "name": "Polycab India", "sector": "Cables"},
    {"symbol": "DIXON", "name": "Dixon Technologies", "sector": "Electronics"},
    {"symbol": "DEEPAKNTR", "name": "Deepak Nitrite", "sector": "Chemicals"},
    {"symbol": "IRCTC", "name": "IRCTC", "sector": "Travel"},
]

# Suggested companies for the on-demand research demo (outside the portfolio).
RESEARCH_SUGGESTIONS = ["KAYNES", "PIDILITIND", "CDSL", "NESTLEIND", "TATAELXSI"]

MATERIAL_CATEGORIES = [
    "results", "M&A", "management_change", "litigation", "fund_raise", "credit_rating", "pledging",
]

DEFAULT_SETTINGS = {
    "price_move_pct": 3.0,       # alert when a stock moves more than ±X% in a day
    "volume_multiple": 2.0,      # alert when volume > N × its recent average
    "volume_lookback": 20,       # days in the "recent average"
    "alert_categories": MATERIAL_CATEGORIES,
}


def get_portfolio() -> list[dict]:
    return db.kv_get("portfolio", DEFAULT_PORTFOLIO)


def set_portfolio(items: list[dict]) -> list[dict]:
    db.kv_set("portfolio", items)
    return items


def get_settings() -> dict:
    return {**DEFAULT_SETTINGS, **db.kv_get("settings", {})}


def set_settings(patch: dict) -> dict:
    merged = {**get_settings(), **{k: v for k, v in patch.items() if k in DEFAULT_SETTINGS}}
    db.kv_set("settings", merged)
    return merged
