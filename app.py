"""
Kill the Quote Spreadsheet - RFx co-pilot -> any-format extraction -> trusted comparison
-> natural-language analyst -> gated award.

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

st.set_page_config(page_title="RFx Co-pilot · Kill the Quote Spreadsheet", page_icon="📦", layout="wide")

COLORS = {"green": "#d9f2e3", "amber": "#fff1c2", "red": "#ffd6d6", "verified": "#d6e8ff"}
ICON = {"green": "🟢", "amber": "🟠", "red": "🔴", "verified": "✅"}

# ------------------------------------------------------------------ state ----------
ss = st.session_state
ss.setdefault("overrides", json.loads(OVR.read_text()) if OVR.exists() else {})
ss.setdefault("analyst_log", [])
ss.setdefault("copilot_hist", [])
ss.setdefault("copilot_draft", None)
ss.setdefault("copilot_issues", [])
ss.setdefault("clarify", {})


def save_overrides():
    OVR.write_text(json.dumps(ss.overrides, indent=1))


rfx = json.loads((DATA / "rfx.json").read_text())
last_year = pd.read_csv(DATA / "last_year_contract.csv")
rfx_lines = pd.DataFrame(rfx["lines"])
folders = sorted([p for p in RESP.iterdir() if p.is_dir()])

# ------------------------------------------------------------------ sidebar --------
with st.sidebar:
    st.markdown("### 📦 RFx Co-pilot")
    st.caption(f"{rfx['rfx_id']} · {rfx['title']}")
    if not llm.has_key():
        k = st.text_input("OpenAI API key", type="password", help="Or put OPENAI_API_KEY in a .env file")
        if k:
            os.environ["OPENAI_API_KEY"] = k
            st.rerun()
    else:
        st.success("OpenAI key loaded", icon="🔑")
    llm.DEFAULT_MODEL = st.text_input("Model", value=llm.DEFAULT_MODEL,
                                      help="Must support vision + JSON mode + tool calls")
    st.markdown("**Normalization assumptions** (buyer-owned)")
    s = Settings(
        usd_inr=st.number_input("USD → INR", value=88.0, step=0.5),
        freight_extra_pct=st.number_input("'Freight extra' estimate (% of price)", value=3.5, step=0.5) / 100,
        fob_port_pct=st.number_input("FOB port → Hosur estimate (%)", value=6.0, step=0.5) / 100,
    )
    st.caption("Every number on screen that depends on these is marked 🟠 and shows the assumption.")
    st.divider()
    if st.button("⚡ Extract ALL vendor responses with AI", width="stretch", disabled=not llm.has_key()):
        prog = st.progress(0.0)
        for i, f in enumerate(folders):
            prog.progress(i / len(folders), text=f"Reading {f.name}…")
            try:
                X.extract_vendor(rfx, f)
            except Exception as e:
                st.error(f"{f.name}: {e}")
        prog.progress(1.0, text="Done")
        st.rerun()
    if ss.overrides and st.button("Clear all buyer overrides", width="stretch"):
        ss.overrides = {}
        save_overrides()
        st.rerun()

extractions = X.load_cached()
if any(e.get("_meta", {}).get("source") == "fixture" for e in extractions.values()):
    st.warning("⚠️ TEST FIXTURE LOADED: these extractions came from tests/make_fixture.py, not the AI. "
               "Click 'Extract ALL vendor responses with AI' to run the real pipeline.")

quotes, vendors, questionnaire = (normalize(rfx, last_year, extractions, s, ss.overrides)
                                  if extractions else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))

st.markdown("## Kill the Quote Spreadsheet")
st.caption("Draft the RFx → vendors reply in any format → AI reads everything → one trusted comparison → "
           "ask questions in plain English → defensible award.")

tabs = st.tabs(["① RFx co-pilot", "② Vendor inbox", "③ Comparison & review", "④ Ask the analyst", "⑤ Award"])

# =========================================================== ① RFx co-pilot ==========
with tabs[0]:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("#### Talk the RFx into existence")
        st.caption("The co-pilot won't mark the RFx ready until unit, currency, GST, freight and the quality gate "
                   "are explicit. Every one of those left vague becomes an amber flag on every response later.")
        box = st.container(height=420)
        for m in ss.copilot_hist:
            box.chat_message(m["role"]).write(m["content"])
        prompt = st.chat_input("e.g. I need an annual contract for 30 corrugated box sizes for our Hosur plant…",
                               key="copilot_in")
        if prompt:
            if not llm.has_key():
                st.error("Add an OpenAI key in the sidebar.")
            else:
                ss.copilot_hist.append({"role": "user", "content": prompt})
                with st.spinner("Drafting…"):
                    from core.copilot import copilot_turn
                    res = copilot_turn(ss.copilot_hist, ss.copilot_draft)
                ss.copilot_hist.append({"role": "assistant", "content": res.get("reply", "")})
                ss.copilot_draft = res.get("rfx") or ss.copilot_draft
                ss.copilot_issues = res.get("open_issues", [])
                st.rerun()
    with c2:
        st.markdown("#### Current draft")
        b1, b2 = st.columns(2)
        if b1.button("Load the sample RFx (RFQ-PKG-2026-114)"):
            ss.copilot_draft = rfx
            ss.copilot_issues = []
            st.rerun()
        d = ss.copilot_draft
        if d:
            st.write(f"**{d.get('rfx_id', 'draft')}** · {d.get('title', '')}")
            st.write(f"Price basis: `{d.get('price_basis', '—')}` · Currency: `{d.get('currency', '—')}`")
            st.json(d.get("terms", {}), expanded=False)
            if d.get("lines"):
                st.dataframe(pd.DataFrame(d["lines"]), height=220, hide_index=True)
            if d.get("questionnaire"):
                st.dataframe(pd.DataFrame(d["questionnaire"]), hide_index=True)
            for i in ss.copilot_issues:
                st.warning(i, icon="⚠️")
            if b2.button("📤 Send to vendors (stubbed email)", disabled=not d.get("lines")):
                from core.copilot import vendor_invite_email
                for v in d.get("vendors_invited") or rfx["vendors_invited"]:
                    (OUTBOX / f"invite_{v.replace(' ', '_')}.txt").write_text(vendor_invite_email(d, v))
                st.success(f"Wrote {len(d.get('vendors_invited') or rfx['vendors_invited'])} invitation emails "
                           f"to cache/outbox/ (SMTP stubbed).")
        else:
            st.info("Start chatting, or load the sample RFx used in this demo.")

# =========================================================== ② Vendor inbox ==========
with tabs[1]:
    st.markdown("#### What actually came back")
    st.caption("Nobody was forced into our template. This is the raw material.")
    for f in folders:
        ex = extractions.get(f.name)
        status = "✅ extracted" if ex else "⏳ not yet read"
        with st.expander(f"**{f.name}** · {', '.join(p.name for p in sorted(f.iterdir()))} · {status}"):
            cA, cB = st.columns([1, 1])
            with cA:
                for p in sorted(f.iterdir()):
                    st.markdown(f"**{p.name}**")
                    if p.suffix.lower() in X.IMAGE_EXT:
                        st.image(str(p), width="stretch")
                    elif p.suffix.lower() == ".xlsx":
                        for name, df in pd.read_excel(p, sheet_name=None, header=None).items():
                            st.caption(f"Sheet: {name}")
                            st.dataframe(df.astype(str), height=200)
                    else:
                        st.code(X.raw_text_for(f).split(f"=== FILE: {p.name} ===")[-1][:2500], language=None)
            with cB:
                if st.button(f"Re-run AI extraction for {f.name}", key=f"ex_{f.name}", disabled=not llm.has_key()):
                    with st.spinner("Reading…"):
                        X.extract_vendor(rfx, f)
                    st.rerun()
                if ex:
                    m = ex.get("_meta", {})
                    st.caption(f"Model: {m.get('model')} · {m.get('extracted_at')} · source: {m.get('source')}")
                    st.write(ex.get("response_summary", ""))
                    st.write("**Basis detected:**", ex.get("document_defaults"))
                    st.write(f"**Lines quoted:** {len(ex.get('line_quotes', []))} · "
                             f"**Not quoted:** {ex.get('unquoted_line_ids') or 'none'}")
                    if ex.get("discounts"):
                        st.write("**Discounts found:**", ex["discounts"])
                    for a in ex.get("ambiguities", []):
                        st.warning(a, icon="❓")
                    with st.popover("Raw extraction JSON"):
                        st.json(ex)

# =========================================================== ③ Comparison ==============
with tabs[2]:
    if quotes.empty:
        st.info("Run the extraction first (sidebar).")
    else:
        k = quotes.status.value_counts()
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Cells (30 lines × vendors)", len(quotes))
        m2.metric("🟢 Green", int(k.get("green", 0)))
        m3.metric("🟠 Amber (assumption)", int(k.get("amber", 0)))
        m4.metric("🔴 Red (blocked)", int(k.get("red", 0)))
        m5.metric("✅ Verified by you", int(k.get("verified", 0)))
        st.caption("All prices: **₹ per piece, ex-GST, landed at Hosur**. 🟢 read & consistent · 🟠 depends on an "
                   "assumption · 🔴 cannot be awarded until resolved · ✅ checked by buyer. Click any cell below "
                   "via the inspector to see the source.")

        piv = quotes.pivot(index="line_id", columns="vendor", values="landed_inr")
        stat = quotes.pivot(index="line_id", columns="vendor", values="status")
        piv.insert(0, "Item", rfx_lines.set_index("line_id").description)
        piv.insert(1, "Qty", rfx_lines.set_index("line_id").annual_qty_pcs)
        ly = last_year.set_index("line_id").price_inr_per_pc
        piv.insert(2, "Last yr", ly)
        vcols = [c for c in piv.columns if c not in ("Item", "Qty", "Last yr")]
        best = piv[vcols].where(stat[vcols].isin(["green", "amber", "verified"])).idxmin(axis=1)

        def style(df):
            out = pd.DataFrame("", index=df.index, columns=df.columns)
            for v in vcols:
                out[v] = stat[v].map(lambda x: f"background-color: {COLORS.get(x, '')}")
                out.loc[best == v, v] += "; font-weight: 700; border: 2px solid #2e7d32"
            return out

        st.dataframe(piv.style.apply(style, axis=None).format(
            {**{v: lambda x: "not quoted" if pd.isna(x) else f"₹{x:,.2f}" for v in vcols},
             "Last yr": "₹{:,.2f}", "Qty": "{:,}"}), height=560, width="stretch")
        st.caption("Bold border = cheapest usable quote on that line (red cells excluded).")

        st.markdown("#### Vendor scorecard")
        vs = vendors[["vendor", "quality", "lines_quoted", "lines_green", "lines_amber", "lines_red",
                      "iso_certificate_valid_until", "payment_terms", "lead_time_days", "freight_terms",
                      "discount_text", "flag_text"]].copy()
        vs["quality"] = vs.quality.map({"pass": "✅ pass", "fail": "❌ fail", "unknown": "❓ unknown"})
        st.dataframe(vs, hide_index=True, width="stretch")
        with st.expander("Questionnaire answers, side by side"):
            st.dataframe(questionnaire.pivot(index="q_id", columns="vendor", values="value")
                         .join(pd.DataFrame(rfx["questionnaire"]).set_index("q_id").question), width="stretch")

        st.markdown("#### 🔎 Inspect & verify a number (maker–checker)")
        i1, i2 = st.columns([1, 1])
        sel_v = i1.selectbox("Vendor", vcols)
        order = {"red": 0, "amber": 1, "green": 2, "verified": 3}
        opts = quotes[quotes.vendor == sel_v].sort_values(by="status", key=lambda c: c.map(order))
        sel_l = i2.selectbox("Line (worst first)", opts.line_id,
                             format_func=lambda l: f"{ICON[stat.loc[l, sel_v]]} {l} · "
                                                   f"{rfx_lines.set_index('line_id').description[l]}")
        r = quotes[(quotes.vendor == sel_v) & (quotes.line_id == sel_l)].iloc[0]
        cA, cB = st.columns([1.1, 1])
        with cA:
            st.markdown(f"### {ICON[r.status]} {inr(r.landed_inr)} / pc landed")
            st.write(f"**As the vendor wrote it:** `{r.source_snippet or '—'}`  \n**Where:** {r.source_ref or '—'}")
            st.write(f"**How we got here:** {r.chain}")
            if pd.notna(r.get("vs_last_year_pct")):
                st.write(f"**vs last year:** {r.vs_last_year_pct:+.1f}%")
            for fl in r.flag_list:
                (st.error if fl.level == "red" else st.warning)(f"**{fl.code}** · {fl.msg}")
            if r.override:
                st.info(r.override)
            a1, a2, a3, a4 = st.columns(4)
            key = f"{sel_v}|{sel_l}"
            if a1.button("✅ Accept", key="acc"):
                ss.overrides[key] = {"action": "accept", "note": "checked against source",
                                     "at": datetime.now().isoformat(timespec="seconds")}
                save_overrides(); st.rerun()
            newv = a2.number_input("Correct ₹/pc", value=float(r.landed_inr) if pd.notna(r.landed_inr) else 0.0,
                                   key="edv", label_visibility="collapsed")
            if a2.button("✏️ Save value", key="edb"):
                ss.overrides[key] = {"action": "edit", "value": newv, "note": "buyer correction",
                                     "at": datetime.now().isoformat(timespec="seconds")}
                save_overrides(); st.rerun()
            if a3.button("🚫 Exclude", key="exc"):
                ss.overrides[key] = {"action": "exclude"}; save_overrides(); st.rerun()
            if key in ss.overrides and a4.button("↩️ Undo", key="undo"):
                ss.overrides.pop(key); save_overrides(); st.rerun()
        with cB:
            folder = RESP / r.vendor_key
            imgs = [p for p in folder.iterdir() if p.suffix.lower() in X.IMAGE_EXT] if folder.exists() else []
            if imgs:
                st.image(str(imgs[0]), caption=f"Original evidence · look for: {r.source_ref}",
                         width="stretch")
            elif folder.exists():
                raw = X.raw_text_for(folder)
                snippet = (r.source_snippet or "")[:25]
                pos = raw.find(snippet) if snippet else -1
                st.caption("Original evidence (text around the source)")
                st.code(raw[max(0, pos - 400): pos + 600] if pos >= 0 else raw[:1200], language=None)

        st.markdown("#### ✉️ Close the gaps: ask the vendor")
        st.caption("Flagging uncertainty is passive. The bottleneck in the 9-day cycle is the back-and-forth, so "
                   "the system drafts the clarification for you.")
        cv = st.selectbox("Vendor to clarify with", vcols, key="clar_v")
        issues = [f"{x.line_id} ({x.description}): {x.flag_text}" for x in
                  quotes[(quotes.vendor == cv) & (quotes.status.isin(["red", "amber"]))].itertuples()]
        vrow = vendors[vendors.vendor == cv].iloc[0]
        if vrow.flag_text:
            issues.append("Vendor-level: " + vrow.flag_text)
        # collapse repetitive per-line issues so the email stays short
        st.write(f"{len(issues)} open points for {cv}.")
        if st.button("Draft clarification email", disabled=not (issues and llm.has_key())):
            from core.copilot import clarification_email
            with st.spinner("Drafting…"):
                ss.clarify[cv] = clarification_email(rfx, cv, issues[:25])
        if cv in ss.clarify:
            txt = st.text_area("Draft", ss.clarify[cv], height=260)
            if st.button("📤 Send (stubbed)"):
                (OUTBOX / f"clarify_{cv.replace(' ', '_')}.txt").write_text(txt)
                st.success("Saved to cache/outbox/. In production this goes out by email and the reply is "
                           "re-ingested automatically.")

# =========================================================== ④ Analyst ==================
with tabs[3]:
    if quotes.empty:
        st.info("Run the extraction first.")
    else:
        from core.analyst import Analyst
        tables = dict(quotes=quotes.drop(columns=["flag_list"]), vendors=vendors.drop(columns=["flag_list"]),
                      questionnaire=questionnaire, rfx_lines=rfx_lines, last_year=last_year)
        if "analyst" not in ss or ss.get("analyst_sig") != (len(quotes), json.dumps(ss.overrides), s.usd_inr,
                                                            s.freight_extra_pct, s.fob_port_pct):
            prev = ss.analyst.messages if "analyst" in ss else None
            ss.analyst = Analyst(tables)
            if prev:
                ss.analyst.messages = prev
            ss.analyst_sig = (len(quotes), json.dumps(ss.overrides), s.usd_inr, s.freight_extra_pct, s.fob_port_pct)
        st.caption("Ask anything. The analyst writes and runs real pandas code against the extracted data. "
                   "Open 'Show the work' to audit every number.")
        sugg = [
            "Compare total landed cost in INR for each vendor, only on lines every vendor quoted. Chart it.",
            "Split the award cheapest per line, but only among vendors who passed the quality questionnaire. "
            "How much does that save versus the best single-vendor award and versus last year?",
            "Which numbers am I least sure about, and does the award change if they're 10% wrong?",
            "Cap any single vendor at 40% of spend for supply risk. What does that diversification cost me?",
            "Where is Shree Balaji more than 10% above the best quote? Draft a negotiation note to them.",
        ]
        cols = st.columns(len(sugg))
        q_click = None
        for c, sg in zip(cols, sugg):
            if c.button(sg[:60] + "…", help=sg, width="stretch"):
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
                        st.download_button("⬇️ CSV", stp["result"].to_csv(index=False), f"analysis_{i}_{j}.csv",
                                           key=f"dl{i}_{j}")
                with st.expander("Show the work (code + raw output)"):
                    for stp in turn["steps"]:
                        st.code(stp["code"], language="python")
                        st.code(stp["output"][:3000], language=None)
        q_in = st.chat_input("Ask about the comparison…", key="analyst_in") or q_click
        if q_in:
            if not llm.has_key():
                st.error("Add an OpenAI key in the sidebar.")
            else:
                with st.spinner("Analysing on the real data…"):
                    res = ss.analyst.ask(q_in)
                ss.analyst_log.append(dict(q=q_in, **res))
                st.rerun()

# =========================================================== ⑤ Award ====================
with tabs[4]:
    if quotes.empty:
        st.info("Run the extraction first.")
    else:
        st.markdown("#### Build a defensible award")
        c1, c2, c3, c4, c5 = st.columns(5)
        qo = c1.checkbox("Only vendors passing quality gate", value=True)
        cap = c2.slider("Max share per vendor", 0, 100, 0, 5, help="0 = no cap") / 100 or None
        mv = c3.number_input("Max # vendors (0 = any)", 0, 5, 0) or None
        da = c4.checkbox("Model conditional discounts", value=True)
        stress = c5.slider("Stress test amber ±%", 0, 25, 10) / 100
        kw = dict(quality_only=qo, max_vendor_share=cap, max_vendors=mv, discount_aware=da)
        res = A.optimize_award(quotes, vendors, **kw)
        if res["status"] != "optimal":
            st.error(f"No feasible award: {res['status']}")
        else:
            lyc = A.last_year_cost(rfx_lines, last_year)
            sv = A.best_single_vendor(quotes, vendors, quality_only=qo)
            full = sv[sv.covers_all]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Award total (net, landed, ex-GST)", A.fmt_inr(res["total"]),
                      f"{(res['total'] / lyc - 1) * 100:+.1f}% vs last year", delta_color="inverse")
            m2.metric("Last year, same volumes", A.fmt_inr(lyc))
            if not full.empty:
                m3.metric(f"Best single vendor ({full.iloc[0].vendor})", A.fmt_inr(full.iloc[0].net_value),
                          f"split saves {A.fmt_inr(full.iloc[0].net_value - res['total'])}")
            m4.metric("Conditional discounts captured", A.fmt_inr(res["discounts"]))
            if res["unawarded"]:
                st.error(f"Lines with NO awardable quote: {', '.join(res['unawarded'])}. Resolve red flags or "
                         f"relax the constraints.")
            st.dataframe(res["by_vendor"].assign(value=lambda d: d.value.map(A.fmt_inr),
                                                 net_value=lambda d: d.net_value.map(A.fmt_inr),
                                                 discount_inr=lambda d: d.discount_inr.map(A.fmt_inr)),
                         hide_index=True, width="stretch")

            sens = A.sensitivity(quotes, vendors, pct=stress, **kw) if stress else None
            if sens:
                (st.success if sens["verdict"].startswith("ROBUST") else st.warning)(
                    f"**Stress test:** {sens['verdict']}")
                if not sens["lines_changed"].empty:
                    with st.expander("Lines that flip under stress"):
                        st.dataframe(sens["lines_changed"], hide_index=True)

            aw = res["award"]
            st.dataframe(aw.style.apply(lambda c: [f"background-color: {COLORS.get(x, '')}" for x in c],
                                        subset=["status"]).format({"unit_price": "₹{:,.2f}", "value": "₹{:,.0f}",
                                                                   "qty": "{:,}"}),
                         hide_index=True, width="stretch", height=380)

            # ---- maker-checker gate ------------------------------------------------------
            n_amber = int((aw.status == "amber").sum())
            n_red = int((aw.status == "red").sum())
            st.markdown("#### 🔒 Award gate")
            ack = True
            if n_amber:
                ack = st.checkbox(f"I have reviewed the {n_amber} awarded lines that rest on assumptions "
                                  f"(🟠) and accept them, or I have sent clarifications.")
            gate_ok = n_red == 0 and ack and not res["unawarded"]
            if not gate_ok:
                st.info("Export unlocks when no red line is awarded, every line has an awardable quote, and amber "
                        "assumptions are acknowledged.")
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as xw:
                pd.DataFrame([{"rfx": rfx["rfx_id"], "generated": datetime.now().isoformat(timespec="seconds"),
                               "constraints": json.dumps({k_: v_ for k_, v_ in kw.items()}),
                               "award_total_net": res["total"], "last_year": lyc,
                               "stress_test": sens["verdict"] if sens else "",
                               "fx_usd_inr": s.usd_inr, "freight_extra_pct": s.freight_extra_pct,
                               "fob_pct": s.fob_port_pct, "amber_acknowledged": ack}]).T.to_excel(xw, sheet_name="Summary")
                aw.to_excel(xw, sheet_name="Award", index=False)
                res["by_vendor"].to_excel(xw, sheet_name="By vendor", index=False)
                quotes.drop(columns=["flag_list"]).to_excel(xw, sheet_name="Audit trail", index=False)
                vendors.drop(columns=["flag_list"]).to_excel(xw, sheet_name="Vendors", index=False)
                pd.DataFrame([{"cell": k_, **v_} for k_, v_ in ss.overrides.items()]).to_excel(
                    xw, sheet_name="Buyer overrides", index=False)
            st.download_button("⬇️ Export award pack (Excel, with full audit trail)", buf.getvalue(),
                               f"{rfx['rfx_id']}_award.xlsx", disabled=not gate_ok, type="primary")
