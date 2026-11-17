"""
Deterministic decision tools. The analyst agent calls these from generated code;
the LLM decides WHICH analysis to run and with WHAT parameters, but never does the
arithmetic itself.
"""
import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

USABLE = ("green", "amber", "verified")


def fmt_inr(x: float) -> str:
    if x is None or pd.isna(x):
        return "—"
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:,.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:,.2f} L"
    return f"₹{x:,.0f}"


def _eligible(quotes, vendors, eligible_vendors=None, quality_only=False, allowed_status=USABLE,
              lines=None, price_col="landed_inr"):
    q = quotes[quotes[price_col].notna() & quotes.status.isin(allowed_status)].copy()
    if eligible_vendors:
        q = q[q.vendor.isin(eligible_vendors)]
    if quality_only:
        ok = vendors[vendors.quality == "pass"].vendor
        q = q[q.vendor.isin(ok)]
    if lines:
        q = q[q.line_id.isin(lines)]
    return q


def optimize_award(quotes: pd.DataFrame, vendors: pd.DataFrame, eligible_vendors=None,
                   quality_only: bool = False, max_vendor_share: float | None = None,
                   allowed_status=USABLE, lines=None, price_col: str = "landed_inr",
                   max_vendors: int | None = None, discount_aware: bool = True) -> dict:
    """
    Award each RFx line to exactly one vendor, minimising total landed cost (MILP).
    - quality_only: only vendors whose questionnaire passed the quality gate
    - max_vendor_share: e.g. 0.4 -> no vendor gets more than 40% of awarded value
    - max_vendors: cap on number of distinct vendors used
    - discount_aware: model conditional volume discounts (e.g. 4% if PO > Rs 50L) in the objective
    - allowed_status: which confidence states may be awarded (red is never awarded by default)
    Returns dict(award, unawarded, total (net of discounts), gross, discounts, by_vendor, status)
    """
    q = _eligible(quotes, vendors, eligible_vendors, quality_only, allowed_status, lines, price_col)
    all_lines = sorted(set(lines) if lines else set(quotes.line_id))
    lines_ok = sorted(q.line_id.unique())
    unawarded = [l for l in all_lines if l not in lines_ok]
    empty = dict(award=pd.DataFrame(), unawarded=all_lines, total=0.0, gross=0.0, discounts=0.0,
                 by_vendor=pd.DataFrame())
    if q.empty:
        return {**empty, "status": "no eligible quotes"}
    q = q.reset_index(drop=True)
    q["value"] = q.qty * q[price_col]
    vals = q.value.values
    vend = sorted(q.vendor.unique())
    vinfo = vendors.set_index("vendor")
    disc_v = []
    if discount_aware and "discount_pct" in vinfo:
        for v in vend:
            pct = vinfo.discount_pct.get(v)
            if pct and not pd.isna(pct):
                thr = vinfo.discount_min_order_inr.get(v)
                disc_v.append((v, float(pct) / 100, 0.0 if thr is None or pd.isna(thr) else float(thr)))
    n, nz, nd = len(q), (len(vend) if max_vendors else 0), len(disc_v)
    N = n + nz + 2 * nd                      # x | z (vendor used) | d (discount earned) | y (discounted value)
    ix, iz, idd, iy = 0, n, n + nz, n + nz + nd
    c = np.zeros(N)
    c[:n] = vals
    for k, (_, pct, _) in enumerate(disc_v):
        c[iy + k] = -pct
    A_, lb, ub = [], [], []

    def add(row, lo, hi):
        A_.append(row); lb.append(lo); ub.append(hi)

    for l in lines_ok:
        row = np.zeros(N); row[:n][(q.line_id == l).values] = 1; add(row, 1, 1)
    if max_vendor_share:
        for v in vend:
            row = np.zeros(N); row[:n] = -max_vendor_share * vals
            m = (q.vendor == v).values
            row[:n][m] += vals[m]; add(row, -np.inf, 0)
    if max_vendors:
        for j, v in enumerate(vend):
            row = np.zeros(N); row[:n][(q.vendor == v).values] = 1; row[iz + j] = -len(lines_ok)
            add(row, -np.inf, 0)
        row = np.zeros(N); row[iz:iz + nz] = 1; add(row, 0, max_vendors)
    for k, (v, pct, thr) in enumerate(disc_v):
        m = (q.vendor == v).values
        M = float(vals[m].sum())
        row = np.zeros(N); row[iy + k] = 1; row[:n][m] = -vals[m]; add(row, -np.inf, 0)   # y <= value_v
        row = np.zeros(N); row[iy + k] = 1; row[idd + k] = -M; add(row, -np.inf, 0)       # y <= M*d
        row = np.zeros(N); row[idd + k] = thr; row[:n][m] = -vals[m]; add(row, -np.inf, 0)  # value_v >= thr*d
    integrality = np.r_[np.ones(n + nz + nd), np.zeros(nd)]
    ubounds = np.r_[np.ones(n + nz + nd), [float(vals[(q.vendor == v).values].sum()) for v, _, _ in disc_v]]
    res = milp(c, constraints=LinearConstraint(np.array(A_), lb, ub), integrality=integrality,
               bounds=Bounds(np.zeros(N), ubounds))
    if not res.success:
        return {**empty, "status": f"infeasible: {res.message}"}
    pick = q[np.round(res.x[:n]) == 1]
    award = pick[["line_id", "description", "vendor", "qty", price_col, "value", "status",
                  "source_ref"]].rename(columns={price_col: "unit_price"}).sort_values("line_id")
    by_vendor = evaluate_award(award, vendors)
    gross = float(award.value.sum())
    disc = float(by_vendor.discount_inr.sum()) if not by_vendor.empty else 0.0
    return dict(award=award.reset_index(drop=True), unawarded=unawarded, total=gross - disc, gross=gross,
                discounts=disc, by_vendor=by_vendor, status="optimal")


def evaluate_award(award: pd.DataFrame, vendors: pd.DataFrame) -> pd.DataFrame:
    """Per-vendor value, and whether conditional discounts (e.g. 4% above Rs 50L) are earned."""
    if award.empty:
        return pd.DataFrame()
    g = award.groupby("vendor").agg(lines=("line_id", "count"), value=("value", "sum")).reset_index()
    g["share_pct"] = (g.value / g.value.sum() * 100).round(1)
    v = vendors.set_index("vendor")
    notes, disc_amt = [], []
    for _, r in g.iterrows():
        pct = v.discount_pct.get(r.vendor) if "discount_pct" in v else None
        thr = v.discount_min_order_inr.get(r.vendor) if "discount_min_order_inr" in v else None
        if pct and not pd.isna(pct):
            if thr is None or pd.isna(thr) or r.value >= thr:
                disc_amt.append(r.value * pct / 100)
                notes.append(f"{pct}% discount earned")
            else:
                disc_amt.append(0.0)
                notes.append(f"{pct}% discount MISSED: needs {fmt_inr(thr)}, awarded {fmt_inr(r.value)} "
                             f"(short by {fmt_inr(thr - r.value)})")
        else:
            disc_amt.append(0.0)
            notes.append("")
    g["discount_inr"] = disc_amt
    g["net_value"] = g.value - g.discount_inr
    g["note"] = notes
    return g.sort_values("value", ascending=False).reset_index(drop=True)


def best_single_vendor(quotes: pd.DataFrame, vendors: pd.DataFrame, quality_only=False,
                       allowed_status=USABLE, price_col="landed_inr") -> pd.DataFrame:
    """Total cost if ONE vendor got everything; only meaningful for vendors covering every line."""
    q = _eligible(quotes, vendors, None, quality_only, allowed_status, None, price_col)
    nlines = quotes.line_id.nunique()
    out = []
    for v, g in q.groupby("vendor"):
        val = float((g.qty * g[price_col]).sum())
        aw = g.assign(value=g.qty * g[price_col])
        ev = evaluate_award(aw, vendors)
        out.append(dict(vendor=v, usable_lines=g.line_id.nunique(), covers_all=g.line_id.nunique() == nlines,
                        value=val, discount_inr=float(ev.discount_inr.sum()),
                        net_value=val - float(ev.discount_inr.sum())))
    return pd.DataFrame(out).sort_values(["covers_all", "net_value"], ascending=[False, True]).reset_index(drop=True)


def last_year_cost(rfx_lines: pd.DataFrame, last_year: pd.DataFrame) -> float:
    m = rfx_lines.merge(last_year[["line_id", "price_inr_per_pc"]], on="line_id")
    return float((m.annual_qty_pcs * m.price_inr_per_pc).sum())


def sensitivity(quotes: pd.DataFrame, vendors: pd.DataFrame, pct: float = 0.10, **opt_kwargs) -> dict:
    """
    Stress test: does the award survive if every AMBER (assumption-based) price is wrong by `pct`
    in the worst direction? Awarded amber prices go UP by pct, competing amber prices go DOWN.
    """
    base = optimize_award(quotes, vendors, **opt_kwargs)
    if base["award"].empty:
        return dict(verdict="No base award", base=base)
    chosen = set(zip(base["award"].line_id, base["award"].vendor))
    q2 = quotes.copy()
    amber = q2.status == "amber"
    is_chosen = q2.apply(lambda r: (r.line_id, r.vendor) in chosen, axis=1)
    q2.loc[amber & is_chosen, "landed_inr"] *= (1 + pct)
    q2.loc[amber & ~is_chosen, "landed_inr"] *= (1 - pct)
    stressed = optimize_award(q2, vendors, **opt_kwargs)
    # cost of KEEPING today's award if the assumptions turn out wrong
    keep = base["award"][["line_id", "vendor", "qty"]].merge(q2[["line_id", "vendor", "landed_inr"]],
                                                            on=["line_id", "vendor"])
    keep["value"] = keep.qty * keep.landed_inr
    ev = evaluate_award(keep, vendors)
    keep_total = float(keep.value.sum() - ev.discount_inr.sum())
    regret = keep_total - stressed["total"]          # what a better award would have saved in that world
    exposure = keep_total - base["total"]            # how much today's award could overrun
    m = base["award"][["line_id", "vendor"]].merge(stressed["award"][["line_id", "vendor"]], on="line_id",
                                                   how="outer", suffixes=("_base", "_stressed"))
    changed = m[m.vendor_base != m.vendor_stressed]
    awarded_amber = int((amber & is_chosen).sum())
    amber_by_vendor = q2[amber & is_chosen].vendor.value_counts().to_dict()
    rel = regret / base["total"] if base["total"] else 0
    if rel < 0.005:
        verdict = (f"ROBUST: if every amber assumption is {pct:.0%} wrong against us, today's award costs "
                   f"{fmt_inr(exposure)} more, but no other award would have been more than {fmt_inr(regret)} "
                   f"({rel:.2%}) cheaper. Decision holds; {len(changed)} near-tie lines could swap vendor.")
    else:
        verdict = (f"FRAGILE: if amber assumptions are {pct:.0%} off, a different award would be "
                   f"{fmt_inr(regret)} ({rel:.1%}) cheaper. Awarded amber lines by vendor: {amber_by_vendor}. "
                   f"Resolve those assumptions (send clarifications) before awarding.")
    return dict(verdict=verdict, lines_changed=changed.reset_index(drop=True), base_total=base["total"],
                stressed_total=stressed["total"], keep_total_under_stress=keep_total, regret=regret,
                exposure=exposure, awarded_amber_lines=awarded_amber, amber_by_vendor=amber_by_vendor, base=base)
