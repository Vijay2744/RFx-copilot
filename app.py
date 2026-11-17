"""
Kill the Quote Spreadsheet - RFx co-pilot.
A buyer's journey in four steps: Request -> Responses -> Review -> Decide,
with an Overview that says in one screen what happened and what to do next.

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
OUTBOX.mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="RFx Co-pilot", page_icon="📦", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""
<style>
[data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
.block-container {padding-top: 1.6rem; max-width: 1250px;}
.hero {border-radius: 12px; padding: 16px 20px; margin: 4px 0 14px;}
.hero h3 {margin: 0 0 4px; font-size: 1.25rem;}
.hero p {margin: 0; font-size: 0.95rem;}
.hero.warn {background: #FFF6E0; border: 1px solid #F0C36D; color: #6B4A00;}
.hero.ok {background: #E8F6EE; border: 1px solid #8FD1AE; color: #0E5A35;}
.card {border: 1px solid #E4E4E0; border-radius: 12px; padding: 14px 16px; background: #fff; height: 100%;}
.card h4 {margin: 0 0 2px; font-size: 1.02rem;}
.muted {color: #6b6b66; font-size: 0.85rem;}
.pill {display: inline-block; padding: 2px 9px; border-radius: 99px; font-size: 0.78rem; margin: 2px 4px 2px 0;
       background: #EEF0F3; color: #333;}
.pill.ok {background: #E3F4EA; color: #0E5A35;} .pill.bad {background: #FDE7E7; color: #8A1F1F;}
.pill.warn {background: #FFF1D6; color: #6B4A00;}
.find {font-size: 0.88rem; margin: 3px 0;}
.step {font-size: 0.85rem; color: #6b6b66;}
</style>""", unsafe_allow_html=True)

COLORS = {"green": "#d9f2e3", "amber": "#fff1c2", "red": "#ffd6d6", "verified": "#d6e8ff"}
ICON = {"green": "🟢", "amber": "🟠", "red": "🔴", "verified": "✅"}
PAGES = ["Overview", "1 · Request", "2 · Responses", "3 · Review", "4 · Decide"]

# ------------------------------------------------------------------ state ----------
ss = st.session_state
ss.setdefault("page", "Overview")
ss.setdefault("overrides", json.loads(OVR.read_text()) if OVR.exists() else {})
ss.setdefault("analyst_log", [])
ss.setdefault("copilot_hist", [])
ss.setdefault("copilot_draft", None)
ss.setdefault("copilot_issues", [])
ss.setdefault("clarify", {})
ss.setdefault("fx", 88.0)
ss.setdefault("frt", 3.5)
ss.setdefault("fob", 6.0)


def save_overrides():
    OVR.write_text(json.dumps(ss.overrides, indent=1))


def go(page, vendor=None, line=None):
    ss.page = page
    if vendor:
        ss.sel_v = vendor
    if line:
        ss.sel_l = line


rfx = json.loads((DATA / "rfx.json").read_text())
last_year = pd.read_csv(DATA / "last_year_contract.csv")
rfx_lines = pd.DataFrame(rfx["lines"])
folders = sorted([p for p in RESP.iterdir() if p.is_dir()])
s = Settings(usd_inr=ss.fx, freight_extra_pct=ss.frt / 100, fob_port_pct=ss.fob / 100)


def run_extraction(only=None):
    targets = [f for f in folders if only is None or f.name == only]
    prog = st.progress(0.0)
    for i, f in enumerate(targets):
        prog.progress(i / len(targets), text=f"Reading {f.name.split('_', 1)[1].replace('_', ' ').title()}…")
        try:
            X.extract_vendor(rfx, f)
        except Exception as e:
            st.error(f"Couldn't read {f.name}: {e}")
    prog.progress(1.0, text="Done")


# ------------------------------------------------------------------ sidebar (setup only)
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
    if st.button("Re-read all vendor responses", width="stretch", disabled=not llm.has_key()):
        run_extraction(); st.rerun()
    if ss.overrides and st.button("Clear my review decisions", width="stretch"):
        ss.overrides = {}; save_overrides(); st.rerun()

extractions = X.load_cached()
quotes, vendors, questionnaire = (normalize(rfx, last_year, extractions, s, ss.overrides)
                                  if extractions else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
ready = not quotes.empty

# ------------------------------------------------------------------ issues (grouped) --
TEMPL = {
    "NOT_QUOTED": ("red", "{v} didn't quote {n} line(s): {lines}.", "Ask vendor"),
    "OUTLIER": ("red", "{v} {lines}: price is far off every other vendor ({m}). Likely a typo or unit error.", "Check source"),
    "SPEC_DEVIATION": ("red", "{v} offers a different spec on {lines}. {m}", "Accept or exclude"),
    "DIMENSION_MISMATCH": ("red", "{v} {lines}: box size in the quote doesn't match the RFx.", "Check source"),
    "NO_PRICE": ("red", "{v} mentioned {lines} but no price could be read.", "Check source"),
    "UNKNOWN_UNIT": ("red", "{v}: price unit not understood on {lines}.", "Check source"),
    "UNKNOWN_CURRENCY": ("red", "{v}: currency unclear on {lines}.", "Check source"),
    "RULE_UNRESOLVED": ("red", "{v} said 'same as last year' but there's no record for {lines}.", "Ask vendor"),
    "DUPLICATE": ("red", "{v} quoted {lines} twice.", "Check source"),
    "PER_KG_CONVERSION": ("amber", "{v} quoted per kg, so {n} prices depend on the assumed box weight.", "Ask vendor"),
    "INFERRED_LAST_YEAR": ("amber", "{v} said 'same as last year' for {n} line(s). Prices taken from last year's contract.", "Ask vendor"),
    "FREIGHT_DOUBLE_COUNT_RISK": ("amber", "{v}: last year's prices included delivery, but now says 'freight extra'. Possible double charge on {n} line(s).", "Ask vendor"),
    "FX": ("amber", "{v} quoted in USD. {n} prices move with the exchange rate.", "Review assumption"),
    "FREIGHT_ESTIMATE": ("amber", "{v} didn't include delivery. Freight is estimated on {n} line(s).", "Ask vendor"),
    "GST_UNKNOWN": ("amber", "{v} didn't say whether GST is included ({n} lines).", "Ask vendor"),
    "READ_UNCERTAIN": ("amber", "{v} {lines}: the reading is uncertain (blurry or skewed source).", "Check source"),
    "MATCH_UNCERTAIN": ("amber", "{v} {lines}: not sure which RFx line this item matches.", "Check source"),
    "FAR_FROM_PEERS": ("amber", "{v} {lines}: price is unusually far from other vendors.", "Check source"),
}


def issues_list():
    out = []
    if not ready:
        return out
    for v in vendors.itertuples():
        for f in v.flag_list:
            if f.code in ("PAYMENT_TERMS", "AMBIGUITY", "NO_BATCH_TESTS", "NO_EVIDENCE"):
                lvl = "amber"
            else:
                lvl = f.level
            if lvl == "red" or f.code in ("NO_EVIDENCE",):
                out.append(dict(level=lvl, text=f"{v.vendor}: {f.msg}", action="View vendor", vendor=v.vendor,
                                line=None, n=1, code=f.code))
    q = quotes[quotes.status.isin(["red", "amber"])]
    rows = []
    for r in q.itertuples():
        for f in r.flag_list:
            rows.append(dict(vendor=r.vendor, line=r.line_id, code=f.code, level=f.level, msg=f.msg))
    if rows:
        df = pd.DataFrame(rows)
        for (v, c), g in df.groupby(["vendor", "code"]):
            lvl, tmpl, act = TEMPL.get(c, (g.level.iloc[0], "{v}: " + c.replace("_", " ").lower() + " on {lines}.", "Check source"))
            lines = sorted(g.line.unique())
            txt = tmpl.format(v=v, n=len(lines), lines=", ".join(lines[:6]) + ("…" if len(lines) > 6 else ""),
                              m=g.msg.iloc[0].split(" (")[0].rstrip("."))
            out.append(dict(level=lvl, text=txt, action=act, vendor=v, line=lines[0], n=len(lines), code=c))
    prio = ["PER_KG_CONVERSION", "FREIGHT_DOUBLE_COUNT_RISK", "INFERRED_LAST_YEAR", "READ_UNCERTAIN", "FX",
            "GST_UNKNOWN", "MATCH_UNCERTAIN", "FAR_FROM_PEERS", "NO_EVIDENCE", "FREIGHT_ESTIMATE"]
    out.sort(key=lambda d: (0 if d["level"] == "red" else 1,
                            prio.index(d["code"]) if d["code"] in prio else len(prio), -d["n"]))
    return out


def recommendation():
    res = A.optimize_award(quotes, vendors, quality_only=True)
    try:
        sens = A.sensitivity(quotes, vendors, pct=0.10, quality_only=True) if res["status"] == "optimal" else None
    except Exception as e:
        sens = dict(verdict=f"Stress test couldn't run: {e}", regret=0.0, amber_by_vendor={},
                    lines_changed=pd.DataFrame())
    return res, sens


# ------------------------------------------------------------------ header + nav -----
h1, h2 = st.columns([3, 2])
h1.markdown(f"### 📦 RFx Co-pilot  \n<span class='muted'>{rfx['rfx_id']} · {rfx['title']} · "
            f"{len(rfx_lines)} lines · {len(folders)} vendors invited</span>", unsafe_allow_html=True)
st.segmented_control("Navigate", PAGES, key="page", label_visibility="collapsed")
if any(e.get("_meta", {}).get("source") == "fixture" for e in extractions.values()):
    st.warning("Test data loaded (not AI output). Use 'Re-read all vendor responses' in the sidebar to run the AI.")
page = ss.page or "Overview"

# =========================================================== OVERVIEW ===============
if page == "Overview":
    if not ready:
        st.markdown("<div class='hero warn'><h3>5 vendors have replied, in 5 different formats</h3>"
                    "<p>Excel, a PDF on letterhead, a Word doc, a phone photo of a rate card, and a one-line "
                    "email. Let the AI read them all into one comparison.</p></div>", unsafe_allow_html=True)
        if st.button("Read all vendor responses", type="primary", disabled=not llm.has_key()):
            run_extraction(); st.rerun()
        if not llm.has_key():
            st.info("Add your OpenAI key in the sidebar (top-left arrow) first.")
    else:
        res, sens = recommendation()
        lyc = A.last_year_cost(rfx_lines, last_year)
        robust = bool(sens) and sens["verdict"].startswith("ROBUST")
        n_vend = res["by_vendor"].shape[0] if not res["by_vendor"].empty else 0
        head = (f"Recommended award: {A.fmt_inr(res['total'])} across {n_vend} vendors, "
                f"{(1 - res['total'] / lyc) * 100:.1f}% below last year")
        sub = ("Ready to sign: the decision holds even if every assumption is 10% wrong."
               if robust else
               f"Not ready to sign yet: {A.fmt_inr(sens.get('regret', 0))} of the decision rests on unconfirmed "
               f"assumptions ({', '.join(sens.get('amber_by_vendor', {}).keys()) or 'see Review'}). Clarify first.")
        st.markdown(f"<div class='hero {'ok' if robust else 'warn'}'><h3>{head}</h3><p>{sub}</p></div>",
                    unsafe_allow_html=True)

        passed = (vendors.quality == "pass").sum()
        failed = (vendors.quality == "fail").sum()
        unknown = (vendors.quality == "unknown").sum()
        c = st.columns(4)
        c[0].metric("Vendor responses read", f"{len(extractions)} of {len(folders)}", "5 formats", delta_color="off")
        c[1].metric("Prices compared", int(quotes.landed_inr.notna().sum()), "₹/pc, ex-GST, delivered",
                    delta_color="off")
        c[2].metric("Passed quality check", f"{passed} of {len(vendors)}",
                    f"{failed} failed, {unknown} unknown", delta_color="off")
        c[3].metric("Hidden discount captured", A.fmt_inr(res["discounts"]),
                    "found in small print" if res["discounts"] else "none", delta_color="off")

        st.markdown("#### Needs your attention")
        iss = issues_list()
        for i, it in enumerate(iss[:7]):
            a, b = st.columns([6, 1.3])
            a.markdown(f"{'🔴' if it['level'] == 'red' else '🟠'} {it['text']}")
            b.button(it["action"], key=f"ov_{i}", on_click=go, args=("3 · Review", it["vendor"], it["line"]),
                     width="stretch")
        if len(iss) > 7:
            st.button(f"See all {len(iss)} items in Review →", on_click=go, args=("3 · Review",))

        st.markdown("#### How this works")
        hw = st.columns(4)
        steps = [("1 · Request", "The buyer drafts the RFx with an AI co-pilot that insists on one unit, "
                                 "currency, GST and freight basis."),
                 ("2 · Responses", "Vendors reply however they like. The AI reads every format and copies "
                                   "numbers exactly as written."),
                 ("3 · Review", "Code converts everything to one basis and flags what it isn't sure about. "
                                "You confirm or fix."),
                 ("4 · Decide", "Ask questions in plain English, get a recommended award, stress-test it, "
                                "export with a full audit trail.")]
        for col, (t, d) in zip(hw, steps):
            col.markdown(f"<div class='card'><h4>{t}</h4><div class='step'>{d}</div></div>", unsafe_allow_html=True)
            col.button("Open", key=f"hw_{t}", on_click=go, args=(t,), width="stretch")

# =========================================================== 1 REQUEST ==============
elif page == "1 · Request":
    st.markdown("<div class='hero ok'><h3>A clear RFx means cleaner replies</h3><p>The co-pilot won't mark the RFx "
                "ready until the price unit, currency, GST, freight and quality check are explicit. Each one left "
                "vague becomes a doubt on every vendor reply later.</p></div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("**Draft with the co-pilot**")
        box = st.container(height=380)
        for m in ss.copilot_hist:
            box.chat_message(m["role"]).write(m["content"])
        prompt = st.chat_input("I need an annual contract for 30 corrugated box sizes for our Hosur plant…",
                               key="copilot_in")
        if prompt:
            if not llm.has_key():
                st.error("Add an OpenAI key in the sidebar.")
            else:
                from core.copilot import copilot_turn
                ss.copilot_hist.append({"role": "user", "content": prompt})
                with st.spinner("Drafting…"):
                    res_ = copilot_turn(ss.copilot_hist, ss.copilot_draft)
                ss.copilot_hist.append({"role": "assistant", "content": res_.get("reply", "")})
                ss.copilot_draft = res_.get("rfx") or ss.copilot_draft
                ss.copilot_issues = res_.get("open_issues", [])
                st.rerun()
    with c2:
        st.markdown("**The RFx that went out**")
        d = ss.copilot_draft or rfx
        st.markdown(f"**{d.get('rfx_id', 'Draft')}** · {d.get('title', '')}  \n"
                    f"Price basis: {d.get('price_basis', '—')}  \nDue: {d.get('due_date', '—')}")
        for i in ss.copilot_issues:
            st.warning(i, icon="⚠️")
        if d.get("lines"):
            st.dataframe(pd.DataFrame(d["lines"])[["line_id", "description", "annual_qty_pcs"]]
                         .rename(columns={"line_id": "Line", "description": "Item", "annual_qty_pcs": "Qty / year"}),
                         height=240, hide_index=True, width="stretch")
        if d.get("questionnaire"):
            with st.expander(f"Quality questionnaire ({len(d['questionnaire'])} questions)"):
                for q in d["questionnaire"]:
                    st.markdown(f"- {q['question']}")
                st.caption(f"Pass rule: {d.get('quality_gate', '')}")
        if st.button("Send to vendors (email stubbed)"):
            from core.copilot import vendor_invite_email
            for v in d.get("vendors_invited") or rfx["vendors_invited"]:
                (OUTBOX / f"invite_{v.replace(' ', '_')}.txt").write_text(vendor_invite_email(d, v))
            st.success("Invitations written to cache/outbox/. In production these go out by email.")

# =========================================================== 2 RESPONSES ============
elif page == "2 · Responses":
    st.markdown("<div class='hero ok'><h3>Nobody was forced into a template</h3><p>Here's what each vendor actually "
                "sent, and what the AI found in it.</p></div>", unsafe_allow_html=True)
    FMT = {".xlsx": "Excel", ".pdf": "PDF", ".docx": "Word", ".jpg": "Phone photo", ".eml": "Email", ".txt": "WhatsApp"}
    cols = st.columns(3)
    for i, f in enumerate(folders):
        ex = extractions.get(f.name)
        with cols[i % 3]:
            files = sorted(f.iterdir())
            name = (ex or {}).get("vendor_name") or f.name.split("_", 1)[1].replace("_", " ").title()
            pills = "".join(f"<span class='pill'>{FMT.get(p.suffix.lower(), p.suffix)}</span>" for p in files)
            finds = []
            if ex:
                dd = ex.get("document_defaults", {}) or {}
                vrow = vendors[vendors.vendor == name].iloc[0] if ready and (vendors.vendor == name).any() else None
                nq = len(ex.get("line_quotes", []))
                q_pill = ""
                if vrow is not None:
                    q_pill = {"pass": "<span class='pill ok'>Quality ✓</span>",
                              "fail": "<span class='pill bad'>Quality ✗</span>"}.get(vrow.quality,
                                                                                    "<span class='pill warn'>Quality ?</span>")
                pills += f"<span class='pill {'ok' if nq >= len(rfx_lines) else 'warn'}'>{nq}/{len(rfx_lines)} lines</span>{q_pill}"
                if dd.get("gst") == "included":
                    finds.append("Prices included GST. Removed to compare like for like.")
                if dd.get("currency") not in (None, "INR", "unknown"):
                    finds.append(f"Quoted in {dd['currency']}. Converted at the buyer's rate.")
                if dd.get("price_basis") in ("per_100_pcs", "per_1000_pcs", "per_dozen"):
                    finds.append(f"Priced {dd['price_basis'].replace('_', ' ')}. Converted to per piece.")
                if dd.get("price_basis") == "per_kg":
                    finds.append("Priced per kg. Converted using box weight from the RFx.")
                if dd.get("freight") in ("extra", "fob_port"):
                    finds.append("Delivery not included. Freight estimated.")
                for dsc in ex.get("discounts", []) or []:
                    finds.append(f"Found a discount: {dsc.get('description', '')} ({dsc.get('condition', '')}).")
                if ex.get("unquoted_line_ids"):
                    finds.append(f"Skipped {len(ex['unquoted_line_ids'])} lines: {', '.join(ex['unquoted_line_ids'])}.")
                if vrow is not None and isinstance(vrow.iso_certificate_valid_until, str) and vrow.iso_certificate_valid_until < s.eval_date:
                    finds.append(f"Claims ISO certified, but the attached certificate expired on {vrow.iso_certificate_valid_until}.")
                if any(q.get("price_rule") == "same_as_last_year" for q in ex.get("line_quotes", [])):
                    finds.append("Said 'same as last year' for some lines. Filled from last year's contract.")
            fhtml = "".join(f"<div class='find'>• {x}</div>" for x in finds[:5]) or \
                "<div class='find muted'>Not read yet.</div>"
            st.markdown(f"<div class='card'><h4>{name}</h4>{pills}<div style='margin-top:8px'>{fhtml}</div></div>",
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
                if st.button("Re-read with AI", key=f"rr_{f.name}", disabled=not llm.has_key()):
                    run_extraction(f.name); st.rerun()

# =========================================================== 3 REVIEW ===============
elif page == "3 · Review":
    if not ready:
        st.info("Read the vendor responses first (Overview).")
    else:
        k = quotes.status.value_counts()
        st.markdown(f"<div class='hero warn'><h3>{int(k.get('red', 0))} prices are blocked and "
                    f"{int(k.get('amber', 0))} rest on assumptions</h3><p>Everything below is grouped by cause. "
                    f"Fix or accept each one; the rest of the {len(quotes)} prices were read cleanly.</p></div>",
                    unsafe_allow_html=True)

        st.markdown("#### Needs your attention")
        for i, it in enumerate(issues_list()):
            a, b = st.columns([6, 1.3])
            a.markdown(f"{'🔴' if it['level'] == 'red' else '🟠'} {it['text']}")
            b.button("Inspect", key=f"rv_{i}", on_click=go, args=("3 · Review", it["vendor"], it["line"]),
                     width="stretch")

        vcols = sorted(quotes.vendor.unique())
        st.markdown("#### 🔎 Inspect a price")
        i1, i2 = st.columns([1, 1])
        if ss.get("sel_v") not in vcols:
            ss.sel_v = vcols[0]
        sel_v = i1.selectbox("Vendor", vcols, key="sel_v")
        order = {"red": 0, "amber": 1, "green": 2, "verified": 3}
        opts = list(quotes[quotes.vendor == sel_v].sort_values(by="status", key=lambda c: c.map(order)).line_id)
        if ss.get("sel_l") not in opts:
            ss.sel_l = opts[0]
        stat = quotes.pivot(index="line_id", columns="vendor", values="status")
        desc = rfx_lines.set_index("line_id").description
        sel_l = i2.selectbox("Line", opts, key="sel_l",
                             format_func=lambda l: f"{ICON[stat.loc[l, sel_v]]} {l} · {desc[l]}")
        r = quotes[(quotes.vendor == sel_v) & (quotes.line_id == sel_l)].iloc[0]
        cA, cB = st.columns([1.1, 1])
        with cA:
            st.markdown(f"### {ICON[r.status]} {inr(r.landed_inr)} per piece, delivered")
            st.markdown(f"**What the vendor wrote:** `{r.source_snippet or '—'}`  \n**Where:** {r.source_ref or '—'}")
            st.markdown(f"**How we converted it:** {r.chain}")
            for fl in r.flag_list:
                (st.error if fl.level == "red" else st.warning)(fl.msg)
            if r.override:
                st.info(r.override)
            key = f"{sel_v}|{sel_l}"
            a1, a2, a3 = st.columns([1, 1.4, 1])
            if a1.button("✅ Looks right", key="acc", width="stretch"):
                ss.overrides[key] = {"action": "accept", "note": "checked against source",
                                     "at": datetime.now().isoformat(timespec="seconds")}
                save_overrides(); st.rerun()
            newv = a2.number_input("Correct price ₹/pc", value=float(r.landed_inr) if pd.notna(r.landed_inr) else 0.0,
                                   key="edv")
            if a2.button("Save correction", key="edb", width="stretch"):
                ss.overrides[key] = {"action": "edit", "value": newv, "note": "buyer correction",
                                     "at": datetime.now().isoformat(timespec="seconds")}
                save_overrides(); st.rerun()
            if a3.button("🚫 Exclude", key="exc", width="stretch"):
                ss.overrides[key] = {"action": "exclude"}; save_overrides(); st.rerun()
            if key in ss.overrides and st.button("Undo my decision", key="undo"):
                ss.overrides.pop(key); save_overrides(); st.rerun()
        with cB:
            folder = RESP / r.vendor_key
            imgs = [p for p in folder.iterdir() if p.suffix.lower() in X.IMAGE_EXT] if folder.exists() else []
            if imgs:
                st.image(str(imgs[0]), caption=f"Original photo · look for {r.source_ref}", width="stretch")
            elif folder.exists():
                raw = X.raw_text_for(folder)
                snip = (r.source_snippet or "")[:25]
                pos = raw.find(snip) if snip else -1
                st.caption("Original document, around the source")
                st.code(raw[max(0, pos - 400): pos + 600] if pos >= 0 else raw[:1200], language=None)

        st.markdown("#### ✉️ Ask a vendor to clarify")
        cv = st.selectbox("Vendor", vcols, index=vcols.index(sel_v), key="clar_v")
        issues = [f"{x.line_id} ({x.description}): {x.flag_text}" for x in
                  quotes[(quotes.vendor == cv) & (quotes.status.isin(["red", "amber"]))].itertuples()]
        vrow = vendors[vendors.vendor == cv].iloc[0]
        if vrow.flag_text:
            issues.append("Vendor-level: " + vrow.flag_text)
        if st.button(f"Draft email to {cv} ({len(issues)} open points)", disabled=not (issues and llm.has_key())):
            from core.copilot import clarification_email
            with st.spinner("Drafting…"):
                ss.clarify[cv] = clarification_email(rfx, cv, issues[:25])
        if cv in ss.clarify:
            txt = st.text_area("Draft", ss.clarify[cv], height=240)
            if st.button("Send (email stubbed)"):
                (OUTBOX / f"clarify_{cv.replace(' ', '_')}.txt").write_text(txt)
                st.success("Saved to cache/outbox/. In production this is emailed and the reply is read automatically.")

        with st.expander("All prices side by side (₹ per piece, ex-GST, delivered)"):
            piv = quotes.pivot(index="line_id", columns="vendor", values="landed_inr")
            piv.insert(0, "Item", desc)
            piv.insert(1, "Qty", rfx_lines.set_index("line_id").annual_qty_pcs)
            piv.insert(2, "Last yr", last_year.set_index("line_id").price_inr_per_pc)
            best = piv[vcols].where(stat[vcols].isin(["green", "amber", "verified"])).idxmin(axis=1)

            def style(df):
                out = pd.DataFrame("", index=df.index, columns=df.columns)
                for v in vcols:
                    out[v] = stat[v].map(lambda x: f"background-color: {COLORS.get(x, '')}")
                    out.loc[best == v, v] += "; font-weight: 700; border: 2px solid #2e7d32"
                return out
            st.dataframe(piv.style.apply(style, axis=None).format(
                {**{v: lambda x: "not quoted" if pd.isna(x) else f"₹{x:,.2f}" for v in vcols},
                 "Last yr": "₹{:,.2f}", "Qty": "{:,}"}), height=520, width="stretch")
            st.caption("🟢 read cleanly · 🟠 depends on an assumption · 🔴 blocked · ✅ you checked it. "
                       "Bold border = cheapest usable price on that line.")
            vi = vendors.set_index("vendor")
            qp = questionnaire.pivot(index="q_id", columns="vendor", values="value")
            side = {"Quality check": {v: {"pass": "✅ pass", "fail": "❌ fail"}.get(vi.quality[v], "❓ unknown") for v in vcols},
                    "ISO certificate": {v: (f"valid till {vi.iso_certificate_valid_until[v]}"
                                            if pd.notna(vi.iso_certificate_valid_until[v])
                                            and vi.iso_certificate_valid_until[v] >= s.eval_date
                                            else f"⚠️ expired {vi.iso_certificate_valid_until[v]}"
                                            if pd.notna(vi.iso_certificate_valid_until[v]) else "none attached")
                                        for v in vcols}}
            for qd in rfx["questionnaire"]:
                side[qd["question"][:50]] = {v: str(qp.loc[qd["q_id"], v]) for v in vcols}
            side.update({"Payment terms": {v: vi.payment_terms[v] for v in vcols},
                         "Lead time (days)": {v: vi.lead_time_days[v] for v in vcols},
                         "Discount": {v: vi.discount_text[v] or "—" for v in vcols}})
            st.markdown("**Questionnaire, certificates and terms**")
            st.dataframe(pd.DataFrame(side).T[vcols].astype(str), width="stretch")

        with st.expander("Assumptions used for conversion (you own these)"):
            a, b, c = st.columns(3)
            a.number_input("USD → INR rate", step=0.5, key="fx")
            b.number_input("Freight estimate when 'extra' (% of price)", step=0.5, key="frt")
            c.number_input("FOB port → plant freight (%)", step=0.5, key="fob")
            st.caption("Any price that depends on these is marked 🟠 and shows the assumption in its working.")

# =========================================================== 4 DECIDE ===============
elif page == "4 · Decide":
    if not ready:
        st.info("Read the vendor responses first (Overview).")
    else:
        res, sens = recommendation()
        lyc = A.last_year_cost(rfx_lines, last_year)
        st.markdown("#### Ask anything about the quotes")
        st.caption("The analyst writes and runs real code on the extracted data. Open 'Show the work' to audit any number.")
        from core.analyst import Analyst
        tables = dict(quotes=quotes.drop(columns=["flag_list"]), vendors=vendors.drop(columns=["flag_list"]),
                      questionnaire=questionnaire, rfx_lines=rfx_lines, last_year=last_year)
        sig = (len(quotes), json.dumps(ss.overrides), ss.fx, ss.frt, ss.fob)
        if "analyst" not in ss or ss.get("analyst_sig") != sig:
            prev = ss.analyst.messages if "analyst" in ss else None
            ss.analyst = Analyst(tables)
            if prev:
                ss.analyst.messages = prev
            ss.analyst_sig = sig
        sugg = [
            "Split the award cheapest per line, but only among vendors who passed the quality check. How much "
            "does that save versus the best single vendor and versus last year?",
            "Which numbers am I least sure about, and does the award change if they're 10% wrong?",
            "Cap any single vendor at 40% of spend for supply risk. What does that cost me?",
            "Compare total delivered cost per vendor on the lines every vendor quoted. Chart it.",
            "Where is Shree Balaji more than 10% above the best quote? Draft a negotiation note.",
        ]
        q_click = None
        sc = st.columns(2)
        for i, sg in enumerate(sugg):
            if sc[i % 2].button(sg, key=f"sg{i}", width="stretch"):
                q_click = sg
        for i, turn in enumerate(ss.analyst_log):
            st.chat_message("user").write(turn["q"])
            with st.chat_message("assistant"):
                st.markdown(turn["answer"] or "")
                for j, stp in enumerate(turn["steps"]):
                    if stp["fig"] is not None:
                        st.plotly_chart(stp["fig"], width="stretch", key=f"fig{i}_{j}")
                    if isinstance(stp["result"], pd.DataFrame):
                        st.dataframe(stp["result"], hide_index=True, width="stretch")
                        st.download_button("Download CSV", stp["result"].to_csv(index=False),
                                           f"analysis_{i}_{j}.csv", key=f"dl{i}_{j}")
                with st.expander("Show the work (code and raw output)"):
                    for stp in turn["steps"]:
                        st.code(stp["code"], language="python")
                        st.code(stp["output"][:3000], language=None)
        q_in = st.chat_input("Ask about the quotes…", key="analyst_in") or q_click
        if q_in:
            if not llm.has_key():
                st.error("Add an OpenAI key in the sidebar.")
            else:
                with st.spinner("Analysing the real data…"):
                    out = ss.analyst.ask(q_in)
                ss.analyst_log.append(dict(q=q_in, **out))
                st.rerun()

        st.divider()
        st.markdown("#### Award")
        with st.expander("Change the rules (quality check, vendor cap, discounts, stress level)"):
            c1, c2, c3, c4, c5 = st.columns(5)
            qo = c1.checkbox("Quality-passed vendors only", value=True)
            cap = c2.slider("Max share per vendor (%)", 0, 100, 0, 5, help="0 = no cap") / 100 or None
            mv = c3.number_input("Max number of vendors (0 = any)", 0, 5, 0) or None
            da = c4.checkbox("Count conditional discounts", value=True)
            stress = c5.slider("Stress test (±%)", 0, 25, 10) / 100
        kw = dict(quality_only=qo, max_vendor_share=cap, max_vendors=mv, discount_aware=da)
        res = A.optimize_award(quotes, vendors, **kw)
        if res["status"] != "optimal":
            st.error(f"No award possible with these rules: {res['status']}")
        else:
            try:
                sens = A.sensitivity(quotes, vendors, pct=stress, **kw) if stress else None
            except Exception as e:
                sens = dict(verdict=f"Stress test couldn't run: {e}", regret=0.0, amber_by_vendor={})
            robust = sens is None or sens["verdict"].startswith("ROBUST")
            sv = A.best_single_vendor(quotes, vendors, quality_only=qo)
            full = sv[sv.covers_all]
            st.markdown(f"<div class='hero {'ok' if robust else 'warn'}'><h3>Recommended: {A.fmt_inr(res['total'])}, "
                        f"{(1 - res['total'] / lyc) * 100:.1f}% below last year ({A.fmt_inr(lyc)})</h3>"
                        f"<p>{sens['verdict'] if sens else ''}</p></div>", unsafe_allow_html=True)
            m = st.columns(3)
            if not full.empty:
                m[0].metric(f"vs best single vendor ({full.iloc[0].vendor})", A.fmt_inr(full.iloc[0].net_value),
                            f"split saves {A.fmt_inr(full.iloc[0].net_value - res['total'])}", delta_color="off")
            m[1].metric("Hidden discounts captured", A.fmt_inr(res["discounts"]))
            m[2].metric("Lines awarded", f"{len(res['award'])} of {len(rfx_lines)}")
            if res["unawarded"]:
                st.error(f"No acceptable quote for {', '.join(res['unawarded'])}. Resolve those in Review.")
            st.dataframe(res["by_vendor"].assign(value=lambda d: d.value.map(A.fmt_inr),
                                                 net_value=lambda d: d.net_value.map(A.fmt_inr),
                                                 discount_inr=lambda d: d.discount_inr.map(A.fmt_inr))
                         .rename(columns={"vendor": "Vendor", "lines": "Lines", "value": "Value",
                                          "share_pct": "Share %", "discount_inr": "Discount", "net_value": "Net",
                                          "note": "Note"}), hide_index=True, width="stretch")
            aw = res["award"]
            with st.expander("Line-by-line award"):
                st.dataframe(aw.style.apply(lambda c: [f"background-color: {COLORS.get(x, '')}" for x in c],
                                            subset=["status"]).format({"unit_price": "₹{:,.2f}", "value": "₹{:,.0f}",
                                                                       "qty": "{:,}"}),
                             hide_index=True, width="stretch", height=380)
            n_amber = int((aw.status == "amber").sum())
            n_red = int((aw.status == "red").sum())
            st.markdown("#### 🔒 Sign-off")
            ack = True
            if n_amber:
                ack = st.checkbox(f"I've reviewed the {n_amber} awarded prices that rest on assumptions, "
                                  f"or sent clarifications.")
            gate_ok = n_red == 0 and ack and not res["unawarded"]
            if not gate_ok:
                st.caption("Export unlocks when nothing blocked is awarded, every line has a quote, and assumptions "
                           "are acknowledged.")
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as xw:
                pd.DataFrame([{"rfx": rfx["rfx_id"], "generated": datetime.now().isoformat(timespec="seconds"),
                               "rules": json.dumps(kw), "award_total_net": res["total"], "last_year": lyc,
                               "stress_test": sens["verdict"] if sens else "", "fx_usd_inr": ss.fx,
                               "freight_extra_pct": ss.frt, "fob_pct": ss.fob,
                               "assumptions_acknowledged": ack}]).T.to_excel(xw, sheet_name="Summary")
                aw.to_excel(xw, sheet_name="Award", index=False)
                res["by_vendor"].to_excel(xw, sheet_name="By vendor", index=False)
                quotes.drop(columns=["flag_list"]).to_excel(xw, sheet_name="Audit trail", index=False)
                vendors.drop(columns=["flag_list"]).to_excel(xw, sheet_name="Vendors", index=False)
                pd.DataFrame([{"cell": k_, **v_} for k_, v_ in ss.overrides.items()]).to_excel(
                    xw, sheet_name="Buyer decisions", index=False)
            st.download_button("Export award pack (Excel, with audit trail)", buf.getvalue(),
                               f"{rfx['rfx_id']}_award.xlsx", disabled=not gate_ok, type="primary")
