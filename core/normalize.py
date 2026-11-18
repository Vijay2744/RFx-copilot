"""
Normalization + trust layer. Deterministic, explainable, no LLM.

Every quote becomes: Rs per piece, ex-GST, landed at Hosur. Every step that changes the
number is written into a human-readable "conversion chain", and every assumption or
anomaly raises a flag:

  RED   = cannot be used for an award until a human resolves it
  AMBER = usable, but depends on an assumption the buyer should acknowledge
  GREEN = read with high confidence, exact conversion, consistent with peers

Confidence comes from CHECKS (unit math, spec match, peer/last-year comparison,
claim-vs-evidence), not from the model's opinion of itself.
"""
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

BASIS_DIVISOR = {"per_pc": 1, "per_100_pcs": 100, "per_1000_pcs": 1000, "per_dozen": 12}
RANK = {"green": 0, "amber": 1, "red": 2}


@dataclass
class Settings:
    usd_inr: float = 88.0               # buyer-set FX for comparison
    eur_inr: float = 102.0
    gst_rate: float = 0.18
    freight_extra_pct: float = 0.035    # "freight extra" with no amount: estimate as % of price
    fob_port_pct: float = 0.06          # FOB port -> Hosur inland freight + handling estimate
    peer_amber: float = 0.30            # >30% from peer median -> amber
    peer_red: float = 1.5               # >2.5x or <0.4x of median -> red (likely unit/decimal/misread)
    eval_date: str = "2026-11-11"       # RFx due date: certificates must be valid on this date


@dataclass
class Flag:
    level: str
    code: str
    msg: str


def _worst(flags):
    lvl = "green"
    for f in flags:
        if RANK[f.level] > RANK[lvl]:
            lvl = f.level
    return lvl


def _dims(text: str):
    m = re.findall(r"(\d{2,4})\s*[x×X*]\s*(\d{2,4})(?:\s*[x×X*]\s*(\d{2,4}))?", text or "")
    out = []
    for t in m:
        out.append("x".join([p for p in t if p]))
    return out


def canonical_vendor(raw: str, folder_key: str, invited: list[str]) -> str:
    """Map whatever name the vendor used ('SHREE BALAJI PACKAGING PVT LTD') to the name we invited them by."""
    def toks(x):
        return {t for t in re.split(r"[^a-z]+", (x or "").lower()) if len(t) >= 2 and t not in
                {"pvt", "ltd", "llp", "the", "and", "industries", "solutions", "packaging", "exports"}}
    cand = toks(raw) | toks(folder_key)
    best, score = None, 0
    for name in invited:
        sc = len(toks(name) & cand)
        if sc > score:
            best, score = name, sc
    return best or (raw or folder_key).title()


def inr(x):
    return "—" if x is None or pd.isna(x) else f"₹{x:,.2f}"


def normalize(rfx: dict, last_year: pd.DataFrame, extractions: dict, settings: Settings,
              overrides: dict | None = None):
    overrides = overrides or {}
    lines = {l["line_id"]: l for l in rfx["lines"]}
    rows, vendor_rows, q_rows = [], [], []

    for key, ex in extractions.items():
        vname = canonical_vendor(ex.get("vendor_name") or key, key, rfx.get("vendors_invited", []))
        d = ex.get("document_defaults", {}) or {}
        quoted = {}
        for q in ex.get("line_quotes", []) or []:
            lid = q.get("rfx_line_id")
            if lid in lines:
                quoted.setdefault(lid, q)       # first match wins; duplicates flagged below
                if quoted[lid] is not q:
                    quoted[lid].setdefault("_dupes", []).append(q)

        for lid, line in lines.items():
            q = quoted.get(lid)
            flags, chain = [], []
            rec = dict(vendor=vname, vendor_key=key, line_id=lid, description=line["description"],
                       qty=line["annual_qty_pcs"], raw_value=None, raw_currency=None, raw_basis=None,
                       raw_gst=None, unit_inr_ex_gst=None, freight_inr=None, landed_inr=None,
                       source_ref="", source_snippet="", vendor_item_text="", is_inferred=False)
            if q is None:
                flags.append(Flag("red", "NOT_QUOTED", "Vendor did not quote this line."))
                rec.update(status="red", flag_list=flags, chain="Not quoted.")
                rows.append(rec)
                continue

            cur = (q.get("currency") or d.get("currency") or "unknown").upper()
            basis = q.get("price_basis") or d.get("price_basis") or "unknown"
            gst = q.get("gst") or d.get("gst") or "unknown"
            freight = d.get("freight") or "unknown"
            rec.update(source_ref=q.get("source_ref", ""), source_snippet=q.get("source_snippet", ""),
                       vendor_item_text=q.get("vendor_item_text", ""), raw_currency=cur,
                       raw_basis=basis, raw_gst=gst)

            # ---- value: stated or resolved from a rule ------------------------------------
            val = q.get("price_value")
            if q.get("price_rule") == "same_as_last_year":
                rec["is_inferred"] = True
                ly = last_year[(last_year.vendor.str.lower().str[:6] == vname.lower()[:6]) &
                               (last_year.line_id == lid)]
                if ly.empty:
                    flags.append(Flag("red", "RULE_UNRESOLVED",
                                      "Vendor said 'same as last year' but we have no contract record "
                                      "for this vendor/line."))
                    rec.update(status="red", flag_list=flags, chain="Rule could not be resolved.")
                    rows.append(rec)
                    continue
                val = float(ly.iloc[0].price_inr_per_pc)
                cur, basis, gst = "INR", "per_pc", "excluded"
                chain.append(f"Vendor rule 'same as last year' → contract {ly.iloc[0].contract_ref}: "
                             f"{inr(val)}/pc (ex-GST, delivered)")
                flags.append(Flag("amber", "INFERRED_LAST_YEAR",
                                  "Price inferred from last year's contract, not stated in this quote."))
                if freight == "extra":
                    flags.append(Flag("amber", "FREIGHT_DOUBLE_COUNT_RISK",
                                      "Last year's price was DELIVERED, but the vendor now says 'freight "
                                      "extra'. Confirm whether freight is on top of last year's price."))
            else:
                chain.append(f"Read: {cur} {val} {basis.replace('_', ' ')} "
                             f"(GST {gst}) from {q.get('source_ref', '?')}")
            rec["raw_value"] = val
            if val is None:
                flags.append(Flag("red", "NO_PRICE", "Line mentioned but no price could be read."))
                rec.update(status="red", flag_list=flags, chain=" → ".join(chain))
                rows.append(rec)
                continue
            v = float(val)

            # ---- unit -----------------------------------------------------------------------
            if basis in BASIS_DIVISOR:
                if BASIS_DIVISOR[basis] != 1:
                    v = v / BASIS_DIVISOR[basis]
                    chain.append(f"÷ {BASIS_DIVISOR[basis]} ({basis.replace('_', ' ')} → per pc)")
            elif basis == "per_kg":
                w = line["weight_kg_per_pc"]
                v = v * w
                chain.append(f"× {w} kg/pc (RFx nominal board weight)")
                flags.append(Flag("amber", "PER_KG_CONVERSION",
                                  f"Quoted per kg; converted using RFx nominal weight {w} kg/pc. "
                                  "Actual box weight varies with board grade."))
            else:
                flags.append(Flag("red", "UNKNOWN_UNIT", f"Price unit '{basis}' not understood."))

            # ---- currency -------------------------------------------------------------------
            if cur == "USD":
                v = v * settings.usd_inr
                chain.append(f"× {settings.usd_inr} USD/INR (buyer-set rate)")
                flags.append(Flag("amber", "FX", f"Quoted in USD; FX assumption {settings.usd_inr}. "
                                                 "Rupee cost moves with the exchange rate."))
            elif cur == "EUR":
                v = v * settings.eur_inr
                chain.append(f"× {settings.eur_inr} EUR/INR")
                flags.append(Flag("amber", "FX", "Quoted in EUR; FX assumption applied."))
            elif cur != "INR":
                flags.append(Flag("red", "UNKNOWN_CURRENCY", f"Currency '{cur}' unclear."))

            # ---- GST --------------------------------------------------------------------------
            if gst == "included":
                v = v / (1 + settings.gst_rate)
                chain.append(f"÷ {1 + settings.gst_rate} (remove GST included in price)")
            elif gst == "unknown":
                flags.append(Flag("amber", "GST_UNKNOWN", "Not clear if GST is included; assumed excluded."))

            rec["unit_inr_ex_gst"] = round(v, 4)

            # ---- freight → landed -------------------------------------------------------------
            if freight == "included":
                fr = 0.0
            elif freight == "fob_port":
                fr = v * settings.fob_port_pct
                chain.append(f"+ {settings.fob_port_pct:.1%} est. inland freight (FOB port → Hosur)")
                flags.append(Flag("amber", "FREIGHT_ESTIMATE",
                                  "FOB port price; inland freight is a buyer-set estimate."))
            else:
                fr = v * settings.freight_extra_pct
                chain.append(f"+ {settings.freight_extra_pct:.1%} est. freight ('{freight}')")
                flags.append(Flag("amber", "FREIGHT_ESTIMATE",
                                  f"Freight '{freight}' with no amount; estimated at "
                                  f"{settings.freight_extra_pct:.1%} of price."))
            rec["freight_inr"] = round(fr, 4)
            rec["landed_inr"] = round(v + fr, 4)
            chain.append(f"= {inr(v + fr)}/pc landed, ex-GST")

            # ---- spec / match / read checks -------------------------------------------------
            if q.get("offered_ply") and int(q["offered_ply"]) != line["ply"]:
                flags.append(Flag("red", "SPEC_DEVIATION",
                                  f"Offers {q['offered_ply']}-ply vs RFx {line['ply']}-ply. "
                                  f"{q.get('spec_deviation', '')}".strip()))
            elif q.get("spec_deviation"):
                flags.append(Flag("red", "SPEC_DEVIATION", q["spec_deviation"]))
            vd = _dims(q.get("vendor_item_text", "") + " " + q.get("source_snippet", ""))
            if vd and line["dimensions_mm"] not in vd and not q.get("price_rule") == "same_as_last_year":
                flags.append(Flag("red", "DIMENSION_MISMATCH",
                                  f"Vendor text shows {vd[0]} but RFx line is {line['dimensions_mm']}."))
            if q.get("match_confidence") == "low":
                flags.append(Flag("amber", "MATCH_UNCERTAIN", f"Line match uncertain: {q.get('match_reason', '')}"))
            if q.get("read_confidence") == "low":
                flags.append(Flag("amber", "READ_UNCERTAIN",
                                  f"Extraction unsure of the reading: {q.get('notes', '') or 'low legibility'}"))
            if q.get("_dupes"):
                flags.append(Flag("red", "DUPLICATE", "Vendor appears to quote this line twice with different text."))

            rec.update(flag_list=flags, chain=" → ".join(chain))
            rows.append(rec)

        # ---- vendor-level facts -------------------------------------------------------------
        ct = ex.get("commercial_terms", {}) or {}
        disc = [x for x in (ex.get("discounts") or []) if x.get("percent")]
        qa = {x.get("q_id"): x for x in (ex.get("questionnaire") or [])}
        att = ex.get("attachments") or []
        iso_claim = (qa.get("Q1", {}).get("answer") or "not_answered")
        certs = [a for a in att if "9001" in (a.get("doc_type", "") + a.get("reference_no", "")).upper()
                 or "ISO" in a.get("doc_type", "").upper()]
        valid_until = max([a["valid_until"] for a in certs if a.get("valid_until")], default=None)
        vflags = []
        if all((x.get("answer") or "not_answered") == "not_answered" for x in qa.values()):
            iso_ok, quality = None, "unknown"
            vflags.append(Flag("red", "NO_QUESTIONNAIRE", "Questionnaire not returned."))
        else:
            iso_ok = iso_claim == "yes"
            if iso_ok and valid_until:
                if valid_until < settings.eval_date:
                    iso_ok = False
                    vflags.append(Flag("red", "CLAIM_CONTRADICTS_EVIDENCE",
                                       f"Says ISO 9001 certified, but attached certificate expired on "
                                       f"{valid_until}."))
            elif iso_ok and not certs:
                vflags.append(Flag("amber", "NO_EVIDENCE", "ISO claimed but no certificate attached."))
            bct_ok = (qa.get("Q2", {}).get("answer") == "yes")
            if not bct_ok:
                vflags.append(Flag("amber", "NO_BATCH_TESTS",
                                   f"Batch test reports: {qa.get('Q2', {}).get('value') or 'not committed'}"))
            quality = "pass" if (iso_ok and bct_ok) else "fail"
        if (ct.get("payment_days") or 60) < 60:
            vflags.append(Flag("amber", "PAYMENT_TERMS", f"Payment terms: {ct.get('payment_terms')}"))
        for a in ex.get("ambiguities") or []:
            vflags.append(Flag("amber", "AMBIGUITY", a))

        vendor_rows.append(dict(
            vendor=vname, vendor_key=key, quality=quality,
            iso_certificate_valid_until=valid_until, payment_terms=ct.get("payment_terms"),
            payment_days=ct.get("payment_days"), lead_time_days=ct.get("lead_time_days"),
            validity=ct.get("validity"), freight_terms=ct.get("freight_terms") or d.get("freight"),
            discount_pct=(disc[0]["percent"] if disc else None),
            discount_min_order_inr=(disc[0].get("min_order_value_inr") if disc else None),
            discount_text=(disc[0].get("description", "") + " — " + disc[0].get("condition", "") if disc else ""),
            discount_source=(disc[0].get("source_snippet") if disc else ""),
            flag_list=vflags, summary=ex.get("response_summary", ""), source=ex.get("_meta", {}).get("source", "llm")))
        for qid in [x["q_id"] for x in rfx["questionnaire"]]:
            a = qa.get(qid, {})
            q_rows.append(dict(vendor=vname, q_id=qid, answer=a.get("answer", "not_answered"),
                               value=a.get("value", ""), source_ref=a.get("source_ref", ""),
                               source_snippet=a.get("source_snippet", "")))

    quotes = pd.DataFrame(rows)
    vendors = pd.DataFrame(vendor_rows)
    questionnaire = pd.DataFrame(q_rows)
    if quotes.empty:
        return quotes, vendors, questionnaire

    # ---- peer + history checks (needs all vendors) --------------------------------------------
    ly_map = last_year.groupby("line_id").price_inr_per_pc.first().to_dict()
    new_flags = [list(x) for x in quotes["flag_list"]]
    vs_ly = [None] * len(quotes)
    for i, r in quotes.iterrows():
        if r.landed_inr is None or pd.isna(r.landed_inr):
            continue
        peers = quotes[(quotes.line_id == r.line_id) & (quotes.vendor != r.vendor)].landed_inr.dropna()
        ref = peers.median() if len(peers) >= 2 else ly_map.get(r.line_id)
        if ref:
            ratio = r.landed_inr / ref
            fl = new_flags[i]
            if ratio > 1 + settings.peer_red or ratio < 1 / (1 + settings.peer_red):
                fl.append(Flag("red", "OUTLIER", f"{ratio:.2f}× the peer median ({inr(ref)}). Looks like a unit, "
                                                 "decimal or misread error, not a real price."))
            elif abs(ratio - 1) > settings.peer_amber:
                fl.append(Flag("amber", "FAR_FROM_PEERS", f"{ratio:.2f}× the peer median ({inr(ref)})."))
        if r.line_id in ly_map:
            vs_ly[i] = round((r.landed_inr / ly_map[r.line_id] - 1) * 100, 1)
    quotes["flag_list"] = new_flags
    quotes["vs_last_year_pct"] = vs_ly

    quotes["status"] = quotes["flag_list"].apply(_worst)
    quotes = pd.DataFrame([_apply_override(r, overrides) for r in quotes.to_dict("records")])
    quotes["flag_text"] = quotes["flag_list"].apply(lambda fl: "; ".join(f"[{f.level.upper()}] {f.msg}" for f in fl))
    quotes["flag_codes"] = quotes["flag_list"].apply(lambda fl: ",".join(sorted({f.code for f in fl})))
    vendors["flag_text"] = vendors["flag_list"].apply(lambda fl: "; ".join(f"[{f.level.upper()}] {f.msg}" for f in fl))
    coverage = quotes[quotes.landed_inr.notna()].groupby("vendor").line_id.nunique()
    vendors["lines_quoted"] = vendors.vendor.map(coverage).fillna(0).astype(int)
    vendors["lines_green"] = vendors.vendor.map(quotes[quotes.status.isin(["green", "verified"])]
                                                .groupby("vendor").size()).fillna(0).astype(int)
    vendors["lines_amber"] = vendors.vendor.map(quotes[quotes.status == "amber"].groupby("vendor").size()).fillna(0).astype(int)
    vendors["lines_red"] = vendors.vendor.map(quotes[quotes.status == "red"].groupby("vendor").size()).fillna(0).astype(int)
    return quotes, vendors, questionnaire


def _apply_override(rec: dict, overrides: dict) -> dict:
    """Maker-checker: the AI is the maker, the buyer is the checker."""
    rec = dict(rec)
    rec.setdefault("status", _worst(rec.get("flag_list", [])))
    o = overrides.get(f"{rec['vendor']}|{rec['line_id']}")
    rec["override"] = ""
    if not o:
        return rec
    if o["action"] == "accept":
        rec["status"] = "verified"
        rec["override"] = f"Accepted by buyer: {o.get('note', '')}".strip(": ")
    elif o["action"] == "edit":
        rec["landed_inr"] = float(o["value"])
        rec["status"] = "verified"
        rec["override"] = f"Edited by buyer to {inr(float(o['value']))}: {o.get('note', '')}".strip(": ")
        rec["chain"] = (rec.get("chain") or "") + f" → BUYER OVERRIDE {inr(float(o['value']))}"
    elif o["action"] == "exclude":
        rec["status"] = "red"
        rec["override"] = "Excluded by buyer"
    return rec
