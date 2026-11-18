"""
Kill the Quote Spreadsheet - RFx co-pilot.

One workspace, matching the brief:
  - a progress strip: RFx drafted -> sent -> replies read -> comparison ready
  - LEFT: the single side-by-side comparison (same lines, units, currency) with
    questionnaire answers and attached documents sitting alongside the numbers;
    click any price to see where it came from
  - RIGHT: the conversation. First you talk the RFx into existence; then you stop
    clicking and start asking, getting text, tables, charts and exports back.

Run:  streamlit run app.py
"""
import io
import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import analysis as A
from core import extract as X
from core import llm
from core.normalize import Settings, normalize, inr

ROOT = Path(__file__).parent
DATA = ROOT / "data"
RESP = DATA / "vendor_responses"
CACHE = ROOT / "cache"
OUTBOX = CACHE / "outbox"
OVR = CACHE / "overrides.json"
SENT = CACHE / "sent.json"
OUTBOX.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="RFx Co-pilot", page_icon="📦", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
[data-testid="stToolbar"], [data-testid="stDecoration"] {display:none;}
header[data-testid="stHeader"] {background: transparent; height: 2.2rem;}
.block-container {padding-top: 3rem; max-width: 1500px;}
.card, .card h4, .card .find {color:#1f1f1c;}
.banner {border-radius: 10px; padding: 10px 14px; margin: 0 0 10px;}
.banner b {font-size: 1.02rem;}
.banner.warn {background:#FFF6E0; border:1px solid #F0C36D; color:#5C4000;}
.banner.ok {background:#E8F6EE; border:1px solid #8FD1AE; color:#0E5A35;}
.banner.info {background:#EEF3FB; border:1px solid #B5CCEB; color:#1D3D6B;}
.card {border:1px solid #E4E4E0; border-radius:12px; padding:12px 14px; background:#fff;}
.card h4 {margin:0 0 4px; font-size:1rem;}
.pill {display:inline-block; padding:1px 8px; border-radius:99px; font-size:0.76rem; margin:2px 4px 2px 0; background:#EEF0F3;}
.pill.ok {background:#E3F4EA; color:#0E5A35;} .pill.bad {background:#FDE7E7; color:#8A1F1F;} .pill.warn {background:#FFF1D6; color:#5C4000;}
.find {font-size:0.86rem; margin:2px 0;}
.muted {color:#6b6b66; font-size:0.85rem;}
.legend {font-size:0.82rem; color:#555;}
</style>""", unsafe_allow_html=True)

COLORS = {"green": "#dff3e7", "amber": "#fff1c2", "red": "#ffd6d6", "verified": "#d6e8ff"}
ICON = {"green": "🟢", "amber": "🟠", "red": "🔴", "verified": "✅"}

# ------------------------------------------------------------------ state ------------
ss = st.session_state
ss.setdefault("overrides", json.loads(OVR.read_text()) if OVR.exists() else {})
ss.setdefault("sent", SENT.exists())
ss.setdefault("chat", [])
ss.setdefault("copilot_hist", [])
ss.setdefault("copilot_draft", None)
ss.setdefault("copilot_issues", [])
ss.setdefault("clarify", {})
ss.setdefault("sel", None)
ss.setdefault("fx", 88.0)
ss.setdefault("frt", 3.5)
ss.setdefault("fob", 6.0)


def save_overrides():
    OVR.write_text(json.dumps(ss.overrides, indent=1))


rfx = json.loads((DATA / "rfx.json").read_text())
last_year = pd.read_csv(DATA / "last_year_contract.csv")
rfx_lines = pd.DataFrame(rfx["lines"])
folders = sorted([p for p in RESP.iterdir() if p.is_dir()])
s = Settings(usd_inr=ss.fx, freight_extra_pct=ss.frt / 100, fob_port_pct=ss.fob / 100)
extractions = X.load_cached()
quotes, vendors, questionnaire = (normalize(rfx, last_year, extractions, s, ss.overrides)
                                  if extractions else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
ready = not quotes.empty
if ready:
    ss.sent = True
ss.setdefault("view", "compare" if ready else "draft")


def vendor_label(folder: Path) -> str:
    ex = extractions.get(folder.name)
    return (ex or {}).get("vendor_name") or folder.name.split("_", 1)[1].replace("_", " ").title()


def run_extraction(only=None):
    targets = [f for f in folders if only is None or f.name == only]
    bar = st.progress(0.0)
    for i, f in enumerate(targets):
        bar.progress(i / len(targets), text=f"Reading {vendor_label(f)}…")
        try:
            X.extract_vendor(rfx, f)
        except Exception as e:
            st.error(f"Couldn't read {f.name}: {e}")
    bar.progress(1.0, text="Done")


def set_view(v):
    ss.view = v


def inspect(vendor, line):
    ss.sel = (vendor, line)
    ss.view = "compare"
    ss.cmp_mode = "Comparison"


# ------------------------------------------------------------------ grouped issues --
TEMPL = {
    "NOT_QUOTED": "{v} didn't quote {n} line(s): {lines}.",
    "OUTLIER": "{v} {lines}: price is way off every other vendor ({m}). Likely a typo or unit error.",
    "SPEC_DEVIATION": "{v} offers a different spec on {lines}: {m}.",
    "DIMENSION_MISMATCH": "{v} {lines}: box size in the quote doesn't match the RFx.",
    "NO_PRICE": "{v} mentioned {lines} but no price could be read.",
    "UNKNOWN_UNIT": "{v}: price unit not understood on {lines}.",
    "UNKNOWN_CURRENCY": "{v}: currency unclear on {lines}.",
    "RULE_UNRESOLVED": "{v} said 'same as last year' but there's no record for {lines}.",
    "DUPLICATE": "{v} quoted {lines} twice.",
    "PER_KG_CONVERSION": "{v} quoted per kg, so {n} prices depend on the assumed box weight.",
    "INFERRED_LAST_YEAR": "{v} said 'same as last year' for {n} line(s); filled from last year's contract.",
    "FREIGHT_DOUBLE_COUNT_RISK": "{v}: last year's prices included delivery, now says 'freight extra'. Possible double charge ({n} lines).",
    "FX": "{v} quoted in USD; {n} prices move with the exchange rate.",
    "FREIGHT_ESTIMATE": "{v} didn't include delivery; freight estimated on {n} line(s).",
    "GST_UNKNOWN": "{v} didn't say whether GST is included ({n} lines).",
    "READ_UNCERTAIN": "{v} {lines}: reading is uncertain (blurry or skewed source).",
    "MATCH_UNCERTAIN": "{v} {lines}: not sure which RFx line this item matches.",
    "FAR_FROM_PEERS": "{v} {lines}: price unusually far from other vendors.",
}
PRIO = ["PER_KG_CONVERSION", "FREIGHT_DOUBLE_COUNT_RISK", "INFERRED_LAST_YEAR", "READ_UNCERTAIN", "FX", "GST_UNKNOWN",
        "MATCH_UNCERTAIN", "FAR_FROM_PEERS", "FREIGHT_ESTIMATE"]


def issues_list():
    out = []
    if not ready:
        return out
    for v in vendors.itertuples():
        for f in v.flag_list:
            if f.level == "red":
                out.append(dict(level="red", text=f"{v.vendor}: {f.msg}", vendor=v.vendor, line=None, n=1, code=f.code))
    rows = [dict(vendor=r.vendor, line=r.line_id, code=f.code, level=f.level, msg=f.msg)
            for r in quotes[quotes.status.isin(["red", "amber"])].itertuples() for f in r.flag_list]
    if rows:
        for (v, c), g in pd.DataFrame(rows).groupby(["vendor", "code"]):
            tmpl = TEMPL.get(c, "{v}: " + c.replace("_", " ").lower() + " on {lines}.")
            lines = sorted(g.line.unique())
            out.append(dict(level=g.level.iloc[0], vendor=v, line=lines[0], n=len(lines), code=c,
                            text=tmpl.format(v=v, n=len(lines), lines=", ".join(lines[:6]) + ("…" if len(lines) > 6 else ""),
                                             m=g.msg.iloc[0].split(" (")[0].rstrip("."))))
    out.sort(key=lambda d: (0 if d["level"] == "red" else 1, PRIO.index(d["code"]) if d["code"] in PRIO else 99, -d["n"]))
    return out


def safe_sensitivity(pct, **kw):
    try:
        return A.sensitivity(quotes, vendors, pct=pct, **kw)
    except Exception as e:
        return dict(verdict=f"Stress test couldn't run: {e}", regret=0.0, amber_by_vendor={}, lines_changed=pd.DataFrame())


# ------------------------------------------------------------------ award plan (buyer-controlled)
from core import award as AW  # noqa: E402

COLMAP = {"Vendor": "vendor_1", "Share %": "pct_1", "2nd vendor": "vendor_2", "Lock": "locked", "Reason": "reason"}


def award_state():
    """Keep the buyer's plan in session; (re)build it when the data or the vendor set changes."""
    vlist = sorted(quotes.vendor.unique())
    for v in vlist:
        ss.setdefault(f"en_{v}", bool((vendors.vendor == v).any() and vendors.set_index("vendor").quality[v] == "pass"))
    enabled = [v for v in vlist if ss[f"en_{v}"]]
    default_enabled = [v for v in vlist if vendors.set_index("vendor").quality[v] == "pass"] or vlist
    sys_plan, sys_res = AW.recommended_plan(quotes, vendors, default_enabled)
    rec_plan, _ = AW.recommended_plan(quotes, vendors, enabled) if enabled else (sys_plan, sys_res)
    sig = (len(quotes), json.dumps(ss.overrides, sort_keys=True), ss.fx, ss.frt, ss.fob)
    ss.setdefault("ed_ver", 0)
    if "plan" not in ss or ss.get("plan_sig") != sig:
        ss.plan, ss.plan_sig = rec_plan.copy(), sig
    mode = ss.pop("replan", None)
    if mode == "replace":
        # only the lines of a removed vendor move; every other choice the buyer made stays
        cur = AW.clean(ss.plan)
        cur.loc[~cur.vendor_2.isin(enabled + [AW.NONE]), ["vendor_2", "pct_1"]] = [AW.NONE, 100]
        keep_mask = cur.vendor_1.isin(enabled)
        tmp = cur.assign(locked=keep_mask)
        new_plan = AW.recommended_plan(quotes, vendors, enabled, tmp)[0] if enabled else cur
        new_plan["locked"] = new_plan.line_id.map(dict(zip(cur.line_id, cur.locked))).fillna(False)
        moved = sorted(set(cur.line_id[~keep_mask]))
        ss.plan, ss.moved_msg = new_plan, (f"Moved {len(moved)} line(s) to the next-best vendor: {', '.join(moved[:10])}"
                                           if moved else "")
        ss.ed_ver += 1
    elif mode == "reopt":
        ss.plan = AW.recommended_plan(quotes, vendors, enabled, ss.plan)[0]
        ss.ed_ver += 1
    ss.plan = AW.clean(ss.plan)
    return vlist, enabled, default_enabled, sys_plan, rec_plan


def on_vendor_toggle():
    ss.replan = "replace"          # replacement: lines of a removed vendor move to the next best


def apply_edits(key):
    ed = ss.get(key, {}).get("edited_rows", {})
    plan = ss.plan.copy()
    for ri, changes in ed.items():
        for col, val in changes.items():
            if col in COLMAP:
                plan.at[int(ri), COLMAP[col]] = val
    ss.plan = AW.clean(plan)
    ss.ed_ver += 1


def reset_plan(plan):
    ss.plan = plan.copy()
    ss.ed_ver += 1


def reoptimize():
    ss.replan = "reopt"


def render_award():
    vlist, enabled, default_enabled, sys_plan, rec_plan = award_state()
    ev = AW.evaluate(ss.plan, quotes, vendors, rfx_lines, rec_plan, enabled)
    sys_ev = AW.evaluate(sys_plan, quotes, vendors, rfx_lines, sys_plan, default_enabled)
    ss.award_eval = ev
    lyc = A.last_year_cost(rfx_lines, last_year)
    vi = vendors.set_index("vendor")

    diff = ev["total"] - sys_ev["total"]
    tone = "ok" if ev["n_red"] == 0 else "warn"
    st.markdown(
        f"<div class='banner {tone}'><b>Your award: {A.fmt_inr(ev['total'])} · "
        f"{(1 - ev['total'] / lyc) * 100:.1f}% below last year ({A.fmt_inr(lyc)})</b><br>"
        f"System recommendation (quality-passed vendors, cheapest per line, discounts counted): "
        f"{A.fmt_inr(sys_ev['total'])}. "
        f"{'Your choices match it.' if abs(diff) < 1 else ('Your choices cost ' + A.fmt_inr(diff) + ' more.' if diff > 0 else 'Your choices save ' + A.fmt_inr(-diff) + '.')}"
        f"</div>", unsafe_allow_html=True)

    m = st.columns(4)
    m[0].metric("Lines you changed", ev["n_changed"], f"{int((ss.plan.vendor_2 != AW.NONE).sum())} split orders",
                delta_color="off")
    m[1].metric("Conditional discounts earned", A.fmt_inr(ev["discounts"]))
    m[2].metric("Value resting on assumptions", A.fmt_inr(ev["amber_value"]),
                f"+{A.fmt_inr(ev['exposure_10pct'])} if 10% off", delta_color="off")
    m[3].metric("Blocked lines in award", ev["n_red"], "must be 0 to sign off", delta_color="off")

    st.markdown("**1 · Which vendors are in this award?** Untick a vendor to replace them: their lines move to the "
                "next-best vendor automatically.")
    tc = st.columns(len(vlist))
    for col, v in zip(tc, vlist):
        q = vi.quality[v]
        tag = {"pass": "quality ✓", "fail": "quality ✗"}.get(q, "quality ?")
        col.checkbox(f"{' '.join(v.title().split()[:2])} ({tag})", key=f"en_{v}", on_change=on_vendor_toggle)
    if ss.get("moved_msg"):
        st.info(ss.pop("moved_msg"))
    off_bad = [v for v in enabled if vi.quality[v] != "pass"]
    if off_bad:
        st.warning(f"You've included {', '.join(off_bad)}, which didn't pass the quality check. Give a reason on "
                   f"their lines.")

    st.markdown("**2 · Line by line.** Change the vendor on any line, or pick a 2nd vendor and a share to split the "
                "order. Lock lines you've decided, then re-optimize the rest.")
    b = st.columns([1, 1, 2])
    b[0].button("Re-optimize unlocked lines", on_click=reoptimize, width="stretch")
    b[1].button("Reset to recommendation", on_click=reset_plan, args=(rec_plan,), width="stretch")

    L = ev["lines"].set_index("line_id")
    disp = pd.DataFrame({
        "Line": ss.plan.line_id,
        "Item": ss.plan.line_id.map(L.description),
        "Qty": ss.plan.line_id.map(L.qty),
        "Vendor": ss.plan.vendor_1,
        "Share %": ss.plan.pct_1,
        "2nd vendor": ss.plan.vendor_2,
        "Unit ₹": ss.plan.line_id.map(L.unit_price).round(2),
        "Line value ₹": ss.plan.line_id.map(L.value).round(0),
        "Extra vs cheapest ₹": ss.plan.line_id.map(L.extra_vs_best).round(0),
        "Status": ss.plan.line_id.map(lambda l: ("🔴 " + L.problem[l]) if L.status[l] == "red"
                                       else ICON.get(L.status[l], "") + (" changed" if L.changed[l] else "")),
        "Lock": ss.plan.locked,
        "Reason": ss.plan.reason,
        "All usable prices": ss.plan.line_id.map(L.options),
    })
    key = f"award_ed_{ss.ed_ver}"
    opts = [AW.NONE] + enabled
    st.data_editor(
        disp, key=key, on_change=apply_edits, args=(key,), hide_index=True, width="stretch", height=460,
        disabled=["Line", "Item", "Qty", "Unit ₹", "Line value ₹", "Extra vs cheapest ₹", "Status", "All usable prices"],
        column_config={
            "Vendor": st.column_config.SelectboxColumn(options=enabled, required=True, width="medium"),
            "Share %": st.column_config.NumberColumn(min_value=1, max_value=100, step=5,
                                                     help="Share of this line's quantity going to the first vendor"),
            "2nd vendor": st.column_config.SelectboxColumn(options=opts, width="medium",
                                                           help="Pick a second vendor to split the order"),
            "Unit ₹": st.column_config.NumberColumn(format="₹%.2f"),
            "Line value ₹": st.column_config.NumberColumn(format="₹%d"),
            "Extra vs cheapest ₹": st.column_config.NumberColumn(format="₹%d",
                                                                 help="What this choice costs over the cheapest usable quote"),
            "Lock": st.column_config.CheckboxColumn(help="Keep this line as is when re-optimizing"),
            "Reason": st.column_config.TextColumn(help="Required when you differ from the recommendation", width="medium"),
            "All usable prices": st.column_config.TextColumn(width="large"),
        })

    st.markdown("**3 · Purchase orders this creates**")
    if not ev["by_vendor"].empty:
        st.dataframe(ev["by_vendor"].assign(value=lambda d_: d_.value.map(A.fmt_inr),
                                            net_value=lambda d_: d_.net_value.map(A.fmt_inr),
                                            discount_inr=lambda d_: d_.discount_inr.map(A.fmt_inr))
                     .rename(columns={"vendor": "Vendor", "lines": "PO lines", "value": "Order value",
                                      "share_pct": "Share %", "discount_inr": "Discount", "net_value": "Net",
                                      "note": "Note"}), hide_index=True, width="stretch")
    sv_ = A.best_single_vendor(quotes, vendors, quality_only=True)
    full = sv_[sv_.covers_all]
    if not full.empty:
        st.caption(f"For reference: giving everything to the best single vendor ({full.iloc[0].vendor}) would cost "
                   f"{A.fmt_inr(full.iloc[0].net_value)}.")

    st.markdown("**4 · Sign off**")
    blockers = []
    if ev["n_red"]:
        blockers.append(f"{ev['n_red']} line(s) are awarded to a vendor with no usable quote")
    if ev["missing_reasons"]:
        blockers.append(f"give a reason for the lines you changed: {', '.join(ev['missing_reasons'][:8])}")
    ack = True
    if ev["n_amber"]:
        ack = st.checkbox(f"I've reviewed the {ev['n_amber']} awarded lines whose prices rest on assumptions "
                          f"({A.fmt_inr(ev['amber_value'])}), or sent clarifications.")
        if not ack:
            blockers.append("acknowledge the assumption-based prices")
    for b_ in blockers:
        st.caption(f"🔒 To export: {b_}.")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame([{"rfx": rfx["rfx_id"], "generated": datetime.now().isoformat(timespec="seconds"),
                       "award_total_net": ev["total"], "system_recommendation": sys_ev["total"],
                       "last_year": lyc, "discounts": ev["discounts"], "vendors_in_award": ", ".join(enabled),
                       "value_on_assumptions": ev["amber_value"], "fx_usd_inr": ss.fx,
                       "freight_extra_pct": ss.frt, "fob_pct": ss.fob, "assumptions_acknowledged": ack}]).T \
            .to_excel(xw, sheet_name="Summary")
        ev["po"].to_excel(xw, sheet_name="Purchase order lines", index=False)
        ev["by_vendor"].to_excel(xw, sheet_name="By vendor", index=False)
        ev["lines"].merge(ss.plan, on="line_id").to_excel(xw, sheet_name="Award by line", index=False)
        quotes.drop(columns=["flag_list"]).to_excel(xw, sheet_name="Audit trail", index=False)
        vendors.drop(columns=["flag_list"]).to_excel(xw, sheet_name="Vendors", index=False)
        pd.DataFrame([{"cell": k_, **v_} for k_, v_ in ss.overrides.items()]).to_excel(
            xw, sheet_name="Buyer decisions", index=False)
    st.download_button("Export award pack (POs, award by line, full audit trail)", buf.getvalue(),
                       f"{rfx['rfx_id']}_award.xlsx", disabled=bool(blockers), type="primary")


# ------------------------------------------------------------------ sidebar: setup only
with st.sidebar:
    st.markdown("**Setup**")
    if not llm.has_key():
        k = st.text_input("OpenAI API key", type="password")
        if k:
            os.environ["OPENAI_API_KEY"] = k
            st.rerun()
    else:
        st.caption("OpenAI key loaded")
    llm.DEFAULT_MODEL = st.text_input("Model", value=llm.DEFAULT_MODEL)
    st.markdown("**Conversion assumptions**")
    st.number_input("USD → INR", step=0.5, key="fx")
    st.number_input("Freight estimate when 'extra' (%)", step=0.5, key="frt")
    st.number_input("FOB port → plant freight (%)", step=0.5, key="fob")
    if ss.overrides and st.button("Clear my review decisions", width="stretch"):
        ss.overrides = {}; save_overrides(); st.rerun()

# ------------------------------------------------------------------ header + progress strip
st.markdown(f"#### 📦 {rfx['title']}  \n<span class='muted'>{rfx['rfx_id']} · {len(rfx_lines)} line items · "
            f"{len(folders)} vendors · due {rfx['due_date']}</span>", unsafe_allow_html=True)
drafted = ss.copilot_draft is not None or ss.sent
n_read = len(extractions)
steps = [("draft", f"{'✅' if drafted else '①'} RFx drafted", "draft"),
         ("send", f"{'✅' if ss.sent else '②'} Sent to {len(folders)} vendors", "draft"),
         ("replies", f"{'✅' if n_read == len(folders) else '③'} Replies read ({n_read}/{len(folders)})", "replies"),
         ("compare", f"{'✅' if ready else '④'} Compare and decide", "compare")]
sc = st.columns(4)
for col, (key, label, target) in zip(sc, steps):
    col.button(label, key=f"step_{key}", on_click=set_view, args=(target,),
               type="primary" if ss.view == key else "secondary", width="stretch")
if any(e.get("_meta", {}).get("source") == "fixture" for e in extractions.values()):
    st.warning("Test data loaded, not AI output. Re-read the replies with AI in step ③.")

left, right = st.columns([1.55, 1], gap="large")

# =========================================================== LEFT: workspace ==========
with left:
    # ---------------- ① draft / ② send --------------------------------------------------
    if ss.view == "draft":
        d = ss.copilot_draft
        if d is None:
            st.markdown("<div class='banner info'><b>Start by talking the RFx into existence.</b><br>Describe what you "
                        "need in the chat on the right. The co-pilot asks for anything that would make vendor replies "
                        "hard to compare: unit, currency, GST, freight, quality check.</div>", unsafe_allow_html=True)
            if st.button("Or load the demo RFx (30 lines of corrugated packaging)"):
                ss.copilot_draft = rfx
                st.rerun()
        else:
            ready_to_send = not ss.copilot_issues
            st.markdown(f"<div class='banner {'ok' if ready_to_send else 'warn'}'><b>{d.get('rfx_id', 'Draft RFx')} · "
                        f"{d.get('title', '')}</b><br>"
                        f"{'Ready to send.' if ready_to_send else str(len(ss.copilot_issues)) + ' open point(s) will make replies messy. Fix them in the chat, or send anyway.'}"
                        f"</div>", unsafe_allow_html=True)
            for i in ss.copilot_issues:
                st.warning(i, icon="⚠️")
            c1, c2 = st.columns(2)
            c1.markdown(f"**Price basis:** {d.get('price_basis', '—')}  \n**Currency:** {d.get('currency', '—')}  \n"
                        f"**Due:** {d.get('due_date', '—')}")
            t = d.get("terms", {}) or {}
            c2.markdown("  \n".join(f"**{k_.title()}:** {v_}" for k_, v_ in t.items()) or "—")
            if d.get("lines"):
                st.dataframe(pd.DataFrame(d["lines"]).rename(columns={
                    "line_id": "Line", "description": "Item", "annual_qty_pcs": "Qty / year",
                    "weight_kg_per_pc": "Kg / pc", "ply": "Ply", "item_type": "Type", "dimensions_mm": "Size (mm)"}),
                    hide_index=True, height=300, width="stretch")
            if d.get("questionnaire"):
                st.markdown("**Quality questionnaire**  \n" + "  \n".join(f"{q['q_id']}. {q['question']}"
                                                                         for q in d["questionnaire"]))
                st.caption(f"Pass rule: {d.get('quality_gate', '')}")
            if st.button(f"Send to {len(folders)} vendors", type="primary"):
                from core.copilot import vendor_invite_email
                for v in d.get("vendors_invited") or rfx["vendors_invited"]:
                    (OUTBOX / f"invite_{v.replace(' ', '_')}.txt").write_text(vendor_invite_email(d, v))
                SENT.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds")}))
                ss.sent = True
                ss.view = "replies"
                st.rerun()
            st.caption("Email is stubbed: invitations are written to cache/outbox/. In this demo the 5 vendor replies "
                       "answer RFQ-PKG-2026-114.")

    # ---------------- ③ replies ------------------------------------------------------------
    elif ss.view == "replies":
        st.markdown(f"<div class='banner info'><b>{len(folders)} vendors replied, each in their own format.</b><br>"
                    f"Nobody was forced into a template. The AI reads each reply and copies every number exactly as "
                    f"written; code does the conversions.</div>", unsafe_allow_html=True)
        b1, b2 = st.columns([1, 2])
        if b1.button("Read all replies with AI" if n_read < len(folders) else "Re-read all replies with AI",
                     type="primary" if n_read < len(folders) else "secondary", disabled=not llm.has_key()):
            run_extraction()
            ss.view = "compare"
            st.rerun()
        if not llm.has_key():
            b2.caption("Add your OpenAI key in the sidebar (arrow, top-left).")
        FMT = {".xlsx": "Excel", ".pdf": "PDF", ".docx": "Word", ".jpg": "Phone photo", ".eml": "Email", ".txt": "WhatsApp"}
        cols = st.columns(2)
        for i, f in enumerate(folders):
            ex = extractions.get(f.name)
            name = vendor_label(f)
            files = sorted(f.iterdir())
            pills = "".join(f"<span class='pill'>{FMT.get(p.suffix.lower(), p.suffix)}</span>" for p in files)
            finds = []
            if ex:
                dd = ex.get("document_defaults", {}) or {}
                nq = len([q for q in ex.get("line_quotes", []) if q.get("rfx_line_id")])
                pills += f"<span class='pill {'ok' if nq >= len(rfx_lines) else 'warn'}'>{nq}/{len(rfx_lines)} lines</span>"
                if ready and (vendors.vendor == name).any():
                    vq = vendors[vendors.vendor == name].iloc[0]
                    pills += {"pass": "<span class='pill ok'>Quality ✓</span>",
                              "fail": "<span class='pill bad'>Quality ✗</span>"}.get(vq.quality, "<span class='pill warn'>Quality ?</span>")
                    vu = vq.iso_certificate_valid_until
                    if isinstance(vu, str) and vu < s.eval_date:
                        finds.append(f"Claims ISO certified, but the attached certificate expired on {vu}.")
                if dd.get("gst") == "included":
                    finds.append("Prices included GST; removed to compare like for like.")
                if dd.get("currency") not in (None, "INR", "unknown"):
                    finds.append(f"Quoted in {dd['currency']}; converted at your rate.")
                if dd.get("price_basis") in ("per_100_pcs", "per_1000_pcs", "per_dozen"):
                    finds.append(f"Priced {dd['price_basis'].replace('_', ' ')}; converted to per piece.")
                if dd.get("price_basis") == "per_kg":
                    finds.append("Priced per kg; converted using box weight from the RFx.")
                if dd.get("freight") in ("extra", "fob_port"):
                    finds.append("Delivery not included; freight estimated.")
                for dsc in ex.get("discounts", []) or []:
                    finds.append(f"Found a discount: {dsc.get('description', '')} ({dsc.get('condition', '')}).")
                if ex.get("unquoted_line_ids"):
                    finds.append(f"Skipped {len(ex['unquoted_line_ids'])} lines: {', '.join(ex['unquoted_line_ids'])}.")
                if any(q.get("price_rule") == "same_as_last_year" for q in ex.get("line_quotes", [])):
                    finds.append("Said 'same as last year' for some lines; filled from last year's contract.")
            body = "".join(f"<div class='find'>• {x}</div>" for x in finds[:5]) or "<div class='find muted'>Not read yet.</div>"
            with cols[i % 2]:
                st.markdown(f"<div class='card'><h4>{name}</h4>{pills}<div style='margin-top:6px'>{body}</div></div>",
                            unsafe_allow_html=True)
                with st.expander("See what they sent"):
                    for p in files:
                        st.caption(p.name)
                        if p.suffix.lower() in X.IMAGE_EXT:
                            st.image(str(p), width="stretch")
                        elif p.suffix.lower() == ".xlsx":
                            for nm, df in pd.read_excel(p, sheet_name=None, header=None).items():
                                st.dataframe(df.astype(str), height=180)
                        else:
                            st.code(X.raw_text_for(f).split(f"=== FILE: {p.name} ===")[-1][:1800], language=None)
                    if st.button("Re-read this one", key=f"rr_{f.name}", disabled=not llm.has_key()):
                        run_extraction(f.name); st.rerun()

    # ---------------- ④ compare and decide ------------------------------------------------
    else:
        if not ready:
            st.info("No replies read yet. Go to step ③ and read the replies.")
        else:
            k = quotes.status.value_counts()
            ss.setdefault("cmp_mode", "Comparison")
            mode = st.segmented_control("View", ["Comparison", "Award and sign-off"], key="cmp_mode",
                                        label_visibility="collapsed") or "Comparison"
            if mode == "Comparison":
                st.markdown(f"<div class='banner info'><b>All {len(folders)} replies on one basis: ₹ per piece, ex-GST, "
                            f"delivered to Hosur.</b><br>{int(k.get('green', 0)) + int(k.get('verified', 0))} prices read "
                            f"cleanly · {int(k.get('amber', 0))} rest on an assumption · {int(k.get('red', 0))} blocked "
                            f"until you resolve them. Click any price to see where it came from.</div>",
                            unsafe_allow_html=True)
                iss = issues_list()
                with st.expander(f"What needs your attention ({len(iss)})", expanded=False):
                    for i, it in enumerate(iss):
                        a, b = st.columns([5, 1.2])
                        a.markdown(f"{'🔴' if it['level'] == 'red' else '🟠'} {it['text']}")
                        if it["line"]:
                            b.button("Show", key=f"is_{i}", on_click=inspect, args=(it["vendor"], it["line"]),
                                     width="stretch")

                vcols = sorted(quotes.vendor.unique())
                piv = quotes.pivot(index="line_id", columns="vendor", values="landed_inr")[vcols]
                stat = quotes.pivot(index="line_id", columns="vendor", values="status")[vcols]
                desc = rfx_lines.set_index("line_id").description
                grid = piv.copy()
                grid.insert(0, "Item", desc)
                grid.insert(1, "Qty", rfx_lines.set_index("line_id").annual_qty_pcs)
                grid.insert(2, "Last year", last_year.set_index("line_id").price_inr_per_pc)
                best = piv.where(stat.isin(["green", "amber", "verified"])).idxmin(axis=1)

                def style(df):
                    out = pd.DataFrame("", index=df.index, columns=df.columns)
                    for v in vcols:
                        out[v] = stat[v].map(lambda x: f"background-color: {COLORS.get(x, '')}")
                        out.loc[best == v, v] += "; font-weight: 700; border: 2px solid #2e7d32"
                    return out

                short = {v: " ".join(v.title().split()[:2]) for v in vcols}   # "Shree Balaji", "Om Packaging"
                back = {sv_: v for v, sv_ in short.items()}
                grid.index.name = "Line"
                styled = grid.style.apply(style, axis=None).format(
                    {**{v: (lambda x: "not quoted" if pd.isna(x) else f"₹{x:,.2f}") for v in vcols},
                     "Last year": "₹{:,.2f}", "Qty": "{:,}"}).relabel_index(
                    ["Item", "Qty", "Last year"] + [short[v] for v in vcols], axis=1)
                try:
                    ev = st.dataframe(styled, height=430, width="stretch", on_select="rerun",
                                      selection_mode="single-cell", key="grid",
                                      column_config={"Item": st.column_config.TextColumn(width="medium")})
                    cells = ev.selection.get("cells", []) if ev else []
                    if cells:
                        rpos, col = cells[0]
                        sig = (rpos, col)
                        col = back.get(col, col)
                        if col in vcols and ss.get("grid_sig") != sig:
                            ss.grid_sig = sig
                            ss.sel = (col, grid.index[rpos])
                except Exception:   # older Streamlit without cell selection
                    st.dataframe(styled, height=430, width="stretch")
                st.markdown("<span class='legend'>🟢 read cleanly · 🟠 depends on an assumption · 🔴 blocked · "
                            "✅ checked by you · bold border = cheapest usable price on the line</span>",
                            unsafe_allow_html=True)

                # inspector (right under the grid, where the eye already is)
                vi = vendors.set_index("vendor")
                if ss.sel and ss.sel[0] in vcols:
                    sv, sl = ss.sel
                    r = quotes[(quotes.vendor == sv) & (quotes.line_id == sl)].iloc[0]
                    with st.container(border=True):
                        st.markdown(f"**{ICON[r.status]} {sv} · {sl} · {desc[sl]}**")
                        cA, cB = st.columns([1.1, 1])
                        with cA:
                            st.markdown(f"**{inr(r.landed_inr)}** per piece, delivered, ex-GST  \n"
                                        f"**Vendor wrote:** `{r.source_snippet or '—'}`  \n**Where:** {r.source_ref or '—'}  \n"
                                        f"**Working:** {r.chain}")
                            for fl in r.flag_list:
                                (st.error if fl.level == "red" else st.warning)(fl.msg)
                            if r.override:
                                st.info(r.override)
                            key = f"{sv}|{sl}"
                            a1, a2, a3 = st.columns(3)
                            if a1.button("✅ Looks right", key="acc", width="stretch"):
                                ss.overrides[key] = {"action": "accept", "note": "checked against source",
                                                     "at": datetime.now().isoformat(timespec="seconds")}
                                save_overrides(); st.rerun()
                            if a3.button("🚫 Exclude", key="exc", width="stretch"):
                                ss.overrides[key] = {"action": "exclude"}; save_overrides(); st.rerun()
                            newv = a2.number_input("Correct ₹/pc",
                                                   value=float(r.landed_inr) if pd.notna(r.landed_inr) else 0.0,
                                                   key=f"edv_{key}", label_visibility="collapsed")
                            if a2.button("Save correction", key="edb", width="stretch"):
                                ss.overrides[key] = {"action": "edit", "value": newv, "note": "buyer correction",
                                                     "at": datetime.now().isoformat(timespec="seconds")}
                                save_overrides(); st.rerun()
                            if key in ss.overrides and st.button("Undo my decision", key="undo"):
                                ss.overrides.pop(key); save_overrides(); st.rerun()
                        with cB:
                            folder = RESP / r.vendor_key
                            imgs = [p for p in folder.iterdir() if p.suffix.lower() in X.IMAGE_EXT]
                            if imgs:
                                st.image(str(imgs[0]), caption=f"Original · {r.source_ref}", width="stretch")
                            else:
                                raw = X.raw_text_for(folder)
                                snip = (r.source_snippet or "")[:25]
                                pos = raw.find(snip) if snip else -1
                                st.code(raw[max(0, pos - 350): pos + 550] if pos >= 0 else raw[:1000], language=None)
                        open_pts = [f"{x.line_id} ({x.description}): {x.flag_text}" for x in
                                    quotes[(quotes.vendor == sv) & (quotes.status.isin(["red", "amber"]))].itertuples()]
                        if vi.flag_text[sv]:
                            open_pts.append("Vendor-level: " + vi.flag_text[sv])
                        if open_pts and st.button(f"✉️ Draft a clarification email to {sv} ({len(open_pts)} open points)",
                                                  disabled=not llm.has_key()):
                            from core.copilot import clarification_email
                            with st.spinner("Drafting…"):
                                ss.clarify[sv] = clarification_email(rfx, sv, open_pts[:25])
                        if sv in ss.clarify:
                            txt = st.text_area("Clarification draft", ss.clarify[sv], height=220)
                            if st.button("Send (email stubbed)"):
                                (OUTBOX / f"clarify_{sv.replace(' ', '_')}.txt").write_text(txt)
                                st.success("Saved to cache/outbox/. In production it's emailed and the reply is read "
                                           "automatically.")
                else:
                    st.caption("Click a price in the grid, or 'Show' on an attention item, to see its source and working.")

                # questionnaire answers + attached documents, aligned to the same vendor columns
                qp = questionnaire.pivot(index="q_id", columns="vendor", values="value")
                qa = questionnaire.pivot(index="q_id", columns="vendor", values="answer")

                def docs(v):
                    fdir = RESP / vi.vendor_key[v]
                    names = [p.name for p in sorted(fdir.iterdir())
                             if "certif" in p.name.lower() or "iso" in p.name.lower()] if fdir.exists() else []
                    return ", ".join(names) or "none"

                def iso(v):
                    vu = vi.iso_certificate_valid_until[v]
                    if not isinstance(vu, str):
                        return "none attached"
                    return f"⚠️ expired {vu}" if vu < s.eval_date else f"valid till {vu}"

                def ans(qid, v):
                    if qid not in qa.index or v not in qa.columns:
                        return "—"
                    a_, val = str(qa.loc[qid, v]), str(qp.loc[qid, v])
                    if a_ == "not_answered":
                        return "not answered"
                    return val if a_ == "value" else f"{a_}{': ' + val if val and val != 'nan' else ''}"

                side = {"Lines quoted": {v: f"{vi.lines_quoted[v]} / {len(rfx_lines)}" for v in vcols},
                        "Quality check": {v: {"pass": "✅ pass", "fail": "❌ fail"}.get(vi.quality[v], "❓ not answered")
                                          for v in vcols},
                        "Attached documents": {v: docs(v) for v in vcols},
                        "ISO certificate (read from the document)": {v: iso(v) for v in vcols}}
                for qd in rfx["questionnaire"]:
                    side[f"{qd['q_id']}. {qd['question']}"] = {v: ans(qd["q_id"], v) for v in vcols}
                side.update({"Payment terms": {v: str(vi.payment_terms[v]) for v in vcols},
                             "Lead time (days)": {v: str(vi.lead_time_days[v]) for v in vcols},
                             "Freight": {v: str(vi.freight_terms[v]) for v in vcols},
                             "Discount": {v: vi.discount_text[v] or "—" for v in vcols}})
                st.markdown("**Questionnaire answers and documents, same vendors**")
                st.dataframe(pd.DataFrame(side).T[vcols].astype(str), width="stretch", height=330)

            # ---------------- award and sign-off: the buyer decides -----------------------
            else:
                render_award()

# =========================================================== RIGHT: conversation ========
with right:
    if ss.view == "draft":
        st.markdown("**Co-pilot: draft the RFx**")
        box = st.container(height=560)
        if not ss.copilot_hist:
            box.chat_message("assistant").write(
                "Tell me what you need to buy: items, quantities, delivery site and timeline. I'll draft the RFx and "
                "pin down the unit, currency, GST, freight and quality check, so the replies come back comparable.")
        for m in ss.copilot_hist:
            box.chat_message(m["role"]).write(m["content"])
        prompt = st.chat_input("e.g. Annual contract for 30 corrugated box sizes for our Hosur plant…", key="copilot_in")
        if prompt:
            if not llm.has_key():
                st.error("Add your OpenAI key in the sidebar.")
            else:
                from core.copilot import copilot_turn
                ss.copilot_hist.append({"role": "user", "content": prompt})
                with st.spinner("Drafting…"):
                    out = copilot_turn(ss.copilot_hist, ss.copilot_draft)
                ss.copilot_hist.append({"role": "assistant", "content": out.get("reply", "")})
                ss.copilot_draft = out.get("rfx") or ss.copilot_draft
                ss.copilot_issues = out.get("open_issues", [])
                st.rerun()
    else:
        st.markdown("**Ask about the quotes**")
        if not ready:
            st.info("Once the replies are read, ask anything here: comparisons, splits, risks, what-ifs.")
        else:
            from core.analyst import Analyst
            aw_ev = ss.get("award_eval")
            tables = dict(quotes=quotes.drop(columns=["flag_list"]), vendors=vendors.drop(columns=["flag_list"]),
                          questionnaire=questionnaire, rfx_lines=rfx_lines, last_year=last_year,
                          my_award=aw_ev["lines"] if aw_ev else pd.DataFrame(),
                          my_po=aw_ev["po"] if aw_ev else pd.DataFrame(),
                          my_award_total=aw_ev["total"] if aw_ev else None)
            sig = (len(quotes), json.dumps(ss.overrides), ss.fx, ss.frt, ss.fob,
                   json.dumps(ss.plan.to_dict("records")) if "plan" in ss else "")
            if "analyst" not in ss or ss.get("analyst_sig") != sig:
                prev = ss.analyst.messages if "analyst" in ss else None
                ss.analyst = Analyst(tables)
                if prev:
                    ss.analyst.messages = prev
                ss.analyst_sig = sig
            sugg = ["Split the award cheapest per line, only among vendors who passed the quality check. "
                    "How much does that save vs the best single vendor and vs last year?",
                    "Which numbers am I least sure about, and does the award change if they're 10% wrong?",
                    "Cap any single vendor at 40% of spend. What does that diversification cost?",
                    "Chart total delivered cost per vendor on the lines every vendor quoted.",
                    "Where is Shree Balaji more than 10% above the best quote? Draft a negotiation note."]
            q_click = None
            box = st.container(height=600)
            if not ss.chat:
                box.chat_message("assistant").write(
                    "I've read all 5 replies and put them on one basis. Ask me anything. I answer by running code on "
                    "the extracted data, so every number can be checked. Try one of these:")
                for i, sg in enumerate(sugg):
                    if box.button(sg, key=f"sg{i}", width="stretch"):
                        q_click = sg
            for i, turn in enumerate(ss.chat):
                box.chat_message("user").write(turn["q"])
                with box.chat_message("assistant"):
                    st.markdown(turn["answer"] or "")
                    tbls = []
                    for j, stp in enumerate(turn["steps"]):
                        if stp["fig"] is not None:
                            st.plotly_chart(stp["fig"], width="stretch", key=f"fig{i}_{j}")
                        if isinstance(stp["result"], pd.DataFrame):
                            st.dataframe(stp["result"], hide_index=True, width="stretch")
                            tbls.append(stp["result"])
                    if tbls:
                        xb = io.BytesIO()
                        with pd.ExcelWriter(xb, engine="openpyxl") as xw:
                            for t_i, t in enumerate(tbls):
                                t.to_excel(xw, sheet_name=f"Table {t_i + 1}", index=False)
                        st.download_button("Export to Excel", xb.getvalue(), f"answer_{i + 1}.xlsx", key=f"xl{i}")
                    with st.expander("Show the work"):
                        for stp in turn["steps"]:
                            st.code(stp["code"], language="python")
                            st.code(stp["output"][:2500], language=None)
            if ss.chat:
                with st.popover("Suggested questions"):
                    for i, sg in enumerate(sugg):
                        if st.button(sg, key=f"sgp{i}", width="stretch"):
                            q_click = sg
            q_in = st.chat_input("Ask about the quotes…", key="analyst_in") or q_click
            if q_in:
                if not llm.has_key():
                    st.error("Add your OpenAI key in the sidebar.")
                else:
                    with st.spinner("Analysing the extracted data…"):
                        out = ss.analyst.ask(q_in)
                    ss.chat.append(dict(q=q_in, **out))
                    st.rerun()
