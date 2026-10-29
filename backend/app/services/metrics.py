"""Deterministic financial analysis from screener.in tables. No model involved:
numbers are computed, never generated."""


def _row(table: dict | None, *names: str) -> list[float | None] | None:
    if not table:
        return None
    for n in names:
        for label, vals in table["rows"].items():
            if label == n or label.startswith(n):
                return vals
    return None


def _annual_idx(headers: list[str]) -> list[int]:
    return [i for i, h in enumerate(headers) if h.upper() != "TTM"]


def _cagr(first: float | None, last: float | None, years: int) -> float | None:
    if not first or not last or first <= 0 or last <= 0 or years <= 0:
        return None
    return round(((last / first) ** (1 / years) - 1) * 100, 1)


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def compute(d: dict) -> dict:
    lender = _row(d["quarters"], "Financing Profit") is not None

    # --- Quarterly series (last 8) ---
    q = d["quarters"]
    qh = q["headers"]
    q_rev = _row(q, "Sales", "Revenue") or []
    q_op = _row(q, "Operating Profit", "Financing Profit") or []
    q_opm = _row(q, "OPM %", "Financing Margin %") or []
    q_pat = _row(q, "Net Profit") or []
    quarters = []
    for i in range(max(0, len(qh) - 8), len(qh)):
        quarters.append({
            "period": qh[i],
            "revenue": q_rev[i] if i < len(q_rev) else None,
            "op_profit": q_op[i] if i < len(q_op) else None,
            "opm": q_opm[i] if i < len(q_opm) else None,
            "pat": q_pat[i] if i < len(q_pat) else None,
            "revenue_yoy": _pct(q_rev[i], q_rev[i - 4]) if i >= 4 and i < len(q_rev) else None,
            "pat_yoy": _pct(q_pat[i], q_pat[i - 4]) if i >= 4 and i < len(q_pat) else None,
        })

    # --- Annual trajectory ---
    p = d["pnl"]
    ph = p["headers"]
    idx = _annual_idx(ph)[-6:]
    rev = _row(p, "Sales", "Revenue") or []
    op = _row(p, "Operating Profit", "Financing Profit") or []
    opm = _row(p, "OPM %", "Financing Margin %") or []
    pat = _row(p, "Net Profit") or []
    years = [{
        "year": ph[i],
        "revenue": rev[i] if i < len(rev) else None,
        "op_profit": op[i] if i < len(op) else None,
        "opm": opm[i] if i < len(opm) else None,
        "pat": pat[i] if i < len(pat) else None,
    } for i in idx]
    ttm = None
    if ph and ph[-1].upper() == "TTM":
        ttm = {"revenue": rev[-1] if rev else None, "pat": pat[-1] if pat else None}

    def cagr(series, n):
        vals = [y[series] for y in years]
        if len(vals) <= n:
            return None
        return _cagr(vals[-1 - n], vals[-1], n)

    trajectory = {
        "years": years,
        "ttm": ttm,
        "revenue_cagr_3y": cagr("revenue", 3),
        "revenue_cagr_5y": cagr("revenue", 5),
        "pat_cagr_3y": cagr("pat", 3),
        "pat_cagr_5y": cagr("pat", 5),
    }

    # --- Balance sheet ---
    b = d["balance_sheet"]
    bh = b["headers"]
    bidx = list(range(max(0, len(bh) - 6), len(bh)))
    eq_cap = _row(b, "Equity Capital") or []
    reserves = _row(b, "Reserves") or []
    debt = _row(b, "Borrowings", "Borrowing") or []
    deposits = _row(b, "Deposits")
    bs_years = []
    for i in bidx:
        equity = (eq_cap[i] or 0) + (reserves[i] or 0) if i < len(eq_cap) else None
        dbt = debt[i] if i < len(debt) else None
        bs_years.append({
            "year": bh[i],
            "equity": round(equity, 1) if equity else None,
            "debt": dbt,
            "deposits": deposits[i] if deposits and i < len(deposits) else None,
            "de": round(dbt / equity, 2) if dbt is not None and equity else None,
        })
    rt = d.get("ratios_table") or {"headers": [], "rows": {}}
    rh = rt["headers"]
    ridx = list(range(max(0, len(rh) - 6), len(rh)))
    wc = []
    for i in ridx:
        def g(name):
            r = _row(rt, name)
            return r[i] if r and i < len(r) else None
        wc.append({
            "year": rh[i],
            "debtor_days": g("Debtor Days"),
            "inventory_days": g("Inventory Days"),
            "payable_days": g("Days Payable"),
            "ccc": g("Cash Conversion Cycle"),
            "roce": g("ROCE %"),
            "roe": g("ROE %"),
        })
    balance = {"years": bs_years, "working_capital": wc, "lender": lender}

    # --- Cash quality ---
    c = d["cash_flow"]
    ch = c["headers"]
    cfo = _row(c, "Cash from Operating Activity") or []
    fcf = _row(c, "Free Cash Flow")
    cash_years = []
    for i in range(max(0, len(ch) - 6), len(ch)):
        y = ch[i]
        pat_y = next((yy["pat"] for yy in years if yy["year"] == y), None)
        if pat_y is None and y in ph:
            j = ph.index(y)
            pat_y = pat[j] if j < len(pat) else None
        cash_years.append({
            "year": y,
            "cfo": cfo[i] if i < len(cfo) else None,
            "pat": pat_y,
            "fcf": fcf[i] if fcf and i < len(fcf) else None,
            "conversion": round(cfo[i] / pat_y, 2) if i < len(cfo) and cfo[i] is not None and pat_y and pat_y > 0 else None,
        })
    last5 = cash_years[-5:]
    sum_cfo = sum(x["cfo"] or 0 for x in last5)
    sum_pat = sum(x["pat"] or 0 for x in last5)
    cash = {
        "years": cash_years,
        "conversion_5y": round(sum_cfo / sum_pat, 2) if sum_pat > 0 else None,
        "fcf_5y": round(sum(x["fcf"] or 0 for x in last5), 1) if fcf else None,
    }

    return {
        "lender": lender,
        "quarters": quarters,
        "trajectory": trajectory,
        "balance": balance,
        "cash": cash,
        "flags": _flags(lender, quarters, trajectory, balance, cash),
    }


def _flags(lender, quarters, traj, balance, cash) -> list[dict]:
    """Plain rules that turn numbers into things worth noticing."""
    f = []
    rc, pc = traj.get("revenue_cagr_3y"), traj.get("pat_cagr_3y")
    if rc is not None and pc is not None:
        if pc < rc - 5:
            f.append({"level": "warn", "area": "trajectory", "text": f"Profit growing slower than sales over 3 years ({pc}% vs {rc}% a year)."})
        elif pc > rc + 5:
            f.append({"level": "good", "area": "trajectory", "text": f"Profit outgrowing sales over 3 years ({pc}% vs {rc}% a year): operating leverage."})
    ys = traj["years"]
    if len(ys) >= 4 and ys[-1]["opm"] is not None and ys[-4]["opm"] is not None and not lender:
        diff = ys[-1]["opm"] - ys[-4]["opm"]
        if abs(diff) >= 3:
            f.append({"level": "good" if diff > 0 else "warn", "area": "trajectory",
                      "text": f"Operating margin {'up' if diff > 0 else 'down'} {abs(diff):.0f} pts in 3 years ({ys[-4]['opm']:.0f}% → {ys[-1]['opm']:.0f}%)."})
    if quarters:
        last = quarters[-1]
        if last["revenue_yoy"] is not None:
            rev_g, pat_g = last["revenue_yoy"], last["pat_yoy"]
            if rev_g < 3 or (pat_g is not None and pat_g < -10):
                lvl = "warn"
            elif rev_g >= 12 and (pat_g is None or pat_g >= 0):
                lvl = "good"
            else:
                lvl = "info"
            f.append({"level": lvl, "area": "snapshot", "text": f"Latest quarter ({last['period']}) revenue {last['revenue_yoy']:+.1f}% YoY, profit {last['pat_yoy']:+.1f}% YoY." if last["pat_yoy"] is not None else f"Latest quarter revenue {last['revenue_yoy']:+.1f}% YoY."})
    if not lender and balance["years"]:
        de = balance["years"][-1]["de"]
        if de is not None:
            if de > 1:
                f.append({"level": "warn", "area": "balance", "text": f"Debt is {de}× equity: highly leveraged."})
            elif de < 0.1:
                f.append({"level": "good", "area": "balance", "text": "Effectively debt-free."})
        first_de = balance["years"][0]["de"]
        if de is not None and first_de is not None and de > first_de + 0.3:
            f.append({"level": "warn", "area": "balance", "text": f"Leverage rising: D/E {first_de} → {de}."})
    wc = [w for w in balance["working_capital"] if w["ccc"] is not None]
    if len(wc) >= 4:
        d = wc[-1]["ccc"] - wc[-4]["ccc"]
        if d >= 20:
            f.append({"level": "warn", "area": "balance", "text": f"Cash conversion cycle lengthened {d:.0f} days in 3 years: more cash tied up."})
        elif d <= -20:
            f.append({"level": "good", "area": "balance", "text": f"Cash conversion cycle shortened {abs(d):.0f} days in 3 years."})
    conv = cash.get("conversion_5y")
    if conv is not None and not lender:
        if conv < 0:
            f.append({"level": "bad", "area": "cash", "text": "Operating cash flow was negative over 5 years despite reported profits."})
        elif conv < 0.7:
            f.append({"level": "bad", "area": "cash", "text": f"Only {conv:.0%} of 5-year profit turned into operating cash."})
        elif conv >= 1:
            f.append({"level": "good", "area": "cash", "text": f"Strong cash conversion: operating cash {conv:.0%} of profit over 5 years."})
    return f
