"""Classify exchange filings into categories and score how much they matter.

Rules first (free, instant, predictable). Only filings the rules can't place confidently
go to the cheap model, batched into one call.
"""

import re

CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
    "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
    "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified",
]

LABELS = {
    "results": "Results", "M&A": "M&A", "fund_raise": "Fund raise", "management_change": "Management",
    "litigation": "Legal / regulatory", "insider_trading": "Insider trade", "earnings_call": "Earnings call",
    "investor_meetings": "Investor meet", "credit_rating": "Credit rating", "dividend": "Dividend",
    "bonus_split": "Bonus / split", "capex": "Capex", "press_release": "Update", "postal_ballot": "Postal ballot",
    "agm_egm": "AGM / EGM", "pledging": "Pledge", "unclassified": "Routine",
}

# Base importance out of 100 per category. Tiers: >=70 attention, 35-69 worth knowing, <35 routine.
BASE_SCORE = {
    "results": 90, "M&A": 80, "management_change": 72, "litigation": 72, "fund_raise": 70, "pledging": 70,
    "capex": 62, "credit_rating": 58, "insider_trading": 55, "bonus_split": 55, "press_release": 42,
    "dividend": 40, "earnings_call": 28, "postal_ballot": 22, "agm_egm": 20, "investor_meetings": 15,
    "unclassified": 5,
}

# (category, regex on the filing type, regex on the filing text or None)
# Order matters: the first match wins.
_RULES: list[tuple[str, str, str | None]] = [
    ("unclassified", r"certificate|newspaper|esop|esos|esps|trading window|loss of share|duplicate|"
                     r"monitoring agency|registrar|secretarial|brsr|esg|spurious|change in address|"
                     r"intimation of book closure|reg\. ?74", None),
    ("insider_trading", r"insider trading|sast|reg\. ?29|reg\. ?31|acquisition of shares|disclosure under reg", None),
    ("pledging", r"pledge|encumbrance", None),
    ("credit_rating", r"credit rating", None),
    ("results", r"financial result|integrated filing", None),
    ("results", r"outcome of board meeting|board meeting", r"financial results?|quarter.{0,30}ended"),
    ("results", r"investor presentation", None),
    ("earnings_call", r"con\.? ?call|analysts|institutional investor", r"transcript|audio|recording|earnings call|conference call"),
    ("investor_meetings", r"analysts|institutional investor|investor meet", None),
    ("M&A", r"acquisition|amalgamation|merger|demerger|scheme of arrangement|sale or disposal|joint venture|divest", None),
    ("fund_raise", r"buy ?back|rights issue|qualified institution|preferential|commercial paper|"
                   r"non.convertible|issue of securities|fund rais", None),
    ("fund_raise", r"allotment of securities", r"\b(ncd|debenture|qip|preferential|rights)"),
    ("unclassified", r"allotment of securities", None),
    ("management_change", r"change in management|resignation|appointment|change in director|cessation|"
                          r"key managerial|kmp|smp|retirement", None),
    ("litigation", r"insolvency|litigation|dispute|action\(s\) taken|orders? passed|penalt|show cause|"
                   r"regulatory|fraud|default|search|tax demand", None),
    ("dividend", r"dividend", None),
    ("dividend", r"record date", r"dividend"),
    ("bonus_split", r"bonus|split|sub-division|subdivision", None),
    ("capex", r"capex|capacity|commissioning|new plant|expansion", None),
    ("postal_ballot", r"postal ballot", None),
    ("agm_egm", r"shareholders meeting|general meeting|agm|egm|e-voting|scrutini", r"postal ballot"),
    ("postal_ballot", r"shareholders meeting", r"postal ballot"),
    ("agm_egm", r"shareholders meeting|general meeting|agm|egm|e-voting|scrutini", None),
    ("press_release", r"bagging|receiving of orders|order|contract", None),
    ("press_release", r"press release|media release|launch|clarification", None),
]

# Filing types too vague to trust rules alone: the model decides.
VAGUE = re.compile(r"^(general )?updates?$|outcome of board meeting|press release|committee meeting|^other", re.I)

_SENIOR = re.compile(r"\b(ceo|cfo|chief executive|chief financial|managing director|\bmd\b|chairman|chairperson|whole.time director)\b", re.I)
_DOWNGRADE = re.compile(r"downgrad|negative|watch with negative", re.I)
_ORDER = re.compile(r"bagging|receiving of orders|order|contract", re.I)


def classify(desc: str, text: str) -> tuple[str, bool]:
    """Return (category, confident). Not confident = worth asking the model."""
    d = (desc or "").lower()
    t = (text or "").lower()
    for cat, desc_re, text_re in _RULES:
        if re.search(desc_re, d) and (text_re is None or re.search(text_re, t)):
            confident = not VAGUE.search(d)
            return cat, confident
    # Vague filing type: look at the text body instead.
    head = t[:300]
    if re.search(r"transcript|audio recording|recording of|analyst presentation|earnings call|investor conference|analyst.{0,20}meet", head):
        return ("investor_meetings" if re.search(r"conference|meet", head) and "call" not in head else "earnings_call"), False
    for cat, desc_re, text_re in _RULES:
        if re.search(desc_re, head) and (text_re is None or re.search(text_re, t)):
            return cat, False
    # "Updates" with nothing routine in them are often real news: surface them, let the model judge.
    if VAGUE.search(d):
        return "press_release", False
    return "unclassified", False


def score(category: str, desc: str, text: str) -> int:
    s = BASE_SCORE.get(category, 10)
    blob = f"{desc} {text}"
    if category == "management_change":
        s = 82 if _SENIOR.search(blob) else (64 if re.search(r"director", blob, re.I) else 50)
        if re.search(r"re-?appoint", blob, re.I):
            s -= 20
    elif category == "results":
        if re.search(r"investor presentation", desc, re.I):
            s = 50
        elif re.search(r"(to consider|scheduled|intimation)", blob, re.I) and not re.search(r"submitted|approved", blob, re.I):
            s = 38  # results are coming, not out yet
    elif category == "credit_rating" and _DOWNGRADE.search(blob):
        s = 80
    elif category == "press_release" and _ORDER.search(desc):
        s = 55
    return max(0, min(100, s))


def tier(s: int) -> str:
    return "attention" if s >= 70 else ("notable" if s >= 35 else "routine")
