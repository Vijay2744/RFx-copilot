"""
The buyer's award plan: the optimizer proposes, the buyer decides.

A plan is one row per RFx line:
    line_id, vendor_1, pct_1, vendor_2, locked, reason
vendor_2 / (100 - pct_1) is the optional second source (a split order).

Everything here is deterministic. The buyer can:
  - change the vendor on any line           (manual award)
  - split a line between two vendors by %   (partial / dual-source order)
  - switch a vendor off entirely            (replacement: their lines move to the next best)
  - lock lines and re-optimize the rest
and every change is priced live against the recommendation, with conditional
discounts (e.g. 4% above Rs 50 L) re-evaluated on the buyer's actual split.
"""
import numpy as np
import pandas as pd

from . import analysis as A

USABLE = ("green", "amber", "verified")
NONE = "—"
RANK = {"green": 0, "verified": 0, "amber": 1, "red": 2, None: 2}


def price_tables(quotes: pd.DataFrame):
    usable = quotes[quotes.status.isin(USABLE) & quotes.landed_inr.notna()]
    price = {(r.line_id, r.vendor): float(r.landed_inr) for r in usable.itertuples()}
    status = {(r.line_id, r.vendor): r.status for r in quotes.itertuples()}
    return price, status


def recommended_plan(quotes, vendors, enabled, locked_plan: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Optimizer's award among enabled vendors; locked rows of locked_plan are kept as they are."""
    lines = sorted(quotes.line_id.unique())
    keep = pd.DataFrame()
    free = lines
    if locked_plan is not None and not locked_plan.empty:
        keep = locked_plan[locked_plan.locked & locked_plan.vendor_1.isin(enabled)].copy()
        free = [l for l in lines if l not in set(keep.line_id)]
    res = A.optimize_award(quotes, vendors, eligible_vendors=list(enabled), lines=free, discount_aware=True)
    rows = []
    pick = dict(zip(res["award"].line_id, res["award"].vendor)) if not res["award"].empty else {}
    for l in free:
        rows.append(dict(line_id=l, vendor_1=pick.get(l, NONE), pct_1=100, vendor_2=NONE, locked=False, reason=""))
    plan = pd.concat([keep[["line_id", "vendor_1", "pct_1", "vendor_2", "locked", "reason"]], pd.DataFrame(rows)]) \
        if not keep.empty else pd.DataFrame(rows)
    return plan.sort_values("line_id").reset_index(drop=True), res


def clean(plan: pd.DataFrame) -> pd.DataFrame:
    p = plan.copy()
    p["pct_1"] = pd.to_numeric(p.pct_1, errors="coerce").fillna(100).clip(1, 100).round().astype(int)
    p["vendor_2"] = p.vendor_2.fillna(NONE).replace("", NONE)
    p["vendor_1"] = p.vendor_1.fillna(NONE).replace("", NONE)
    same = p.vendor_2 == p.vendor_1
    p.loc[same, "vendor_2"] = NONE
    p.loc[p.vendor_2 == NONE, "pct_1"] = 100
    p.loc[(p.vendor_2 != NONE) & (p.pct_1 == 100), "pct_1"] = 70     # picking a 2nd vendor implies a split
    p["locked"] = p.locked.fillna(False).astype(bool)
    p["reason"] = p.reason.fillna("").astype(str)
    return p


def evaluate(plan: pd.DataFrame, quotes, vendors, rfx_lines: pd.DataFrame, recommended: pd.DataFrame,
             enabled) -> dict:
    """Price the buyer's plan. Returns line table, PO lines, per-vendor totals with discounts, and headline numbers."""
    price, status = price_tables(quotes)
    qty = rfx_lines.set_index("line_id").annual_qty_pcs
    desc = rfx_lines.set_index("line_id").description
    rec = dict(zip(recommended.line_id, recommended.vendor_1))
    rows, po = [], []
    for r in plan.itertuples():
        q = int(qty[r.line_id])
        parts = [(r.vendor_1, r.pct_1 / 100)]
        if r.vendor_2 != NONE:
            parts.append((r.vendor_2, (100 - r.pct_1) / 100))
        unit, value, st_, problems = 0.0, 0.0, "green", []
        for v, share in parts:
            p = price.get((r.line_id, v))
            s_ = status.get((r.line_id, v))
            if v == NONE or p is None:
                problems.append(f"{v if v != NONE else 'No vendor'} has no usable quote")
                st_ = "red"
                continue
            if RANK.get(s_, 2) > RANK.get(st_, 0):
                st_ = s_
            alloc = int(round(q * share))
            unit += p * share
            value += p * alloc
            po.append(dict(vendor=v, line_id=r.line_id, description=desc[r.line_id], qty=alloc, unit_price=p,
                           value=p * alloc, status=s_))
        opts = sorted([(price[(r.line_id, v)], v) for v in enabled if (r.line_id, v) in price])
        best = opts[0][0] if opts else np.nan
        changed = (r.vendor_1 != rec.get(r.line_id)) or (r.vendor_2 != NONE)
        rows.append(dict(line_id=r.line_id, description=desc[r.line_id], qty=q, unit_price=unit if not problems else np.nan,
                         value=value if not problems else np.nan, best_price=best,
                         extra_vs_best=(unit - best) * q if not problems and opts else np.nan,
                         status="red" if problems else st_, changed=changed, problem="; ".join(problems),
                         options=" · ".join(f"{v.split()[0].title()} ₹{p_:,.2f}" for p_, v in opts)))
    lines = pd.DataFrame(rows)
    po = pd.DataFrame(po)
    by_vendor = A.evaluate_award(po, vendors) if not po.empty else pd.DataFrame()
    gross = float(po.value.sum()) if not po.empty else 0.0
    disc = float(by_vendor.discount_inr.sum()) if not by_vendor.empty else 0.0
    # exposure: if every assumption-based (amber) price in this plan is 10% worse
    amber_val = float(po[po.status == "amber"].value.sum()) if not po.empty else 0.0
    return dict(lines=lines, po=po, by_vendor=by_vendor, gross=gross, discounts=disc, total=gross - disc,
                amber_value=amber_val, exposure_10pct=0.10 * amber_val,
                n_changed=int(lines.changed.sum()), n_red=int((lines.status == "red").sum()),
                n_amber=int((lines.status == "amber").sum()),
                missing_reasons=[r.line_id for r, c in zip(plan.itertuples(), lines.changed) if c and not r.reason.strip()])
