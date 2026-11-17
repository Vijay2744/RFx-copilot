"""
OFFLINE TEST FIXTURE ONLY. Builds extraction JSON with simple rule-based parsers so the
normalization / optimizer / UI can be tested without an API key. The real app uses the LLM
extractor (core/extract.py). Fixture records are tagged _meta.source="fixture" and the UI
shows a warning banner whenever they are loaded.
"""
import json
import re
import sys
from pathlib import Path

import pdfplumber
from docx import Document
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data"
rfx = json.loads((D / "rfx.json").read_text())
gt = json.loads((D / "_ground_truth_for_tests.json").read_text())
by_dims = {l["dimensions_mm"]: l for l in rfx["lines"]}
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "fixtures"
out_dir.mkdir(parents=True, exist_ok=True)
META = {"source": "fixture"}


def q(lid, val, ref, snip, **kw):
    base = dict(rfx_line_id=lid, vendor_item_text=snip, match_confidence="high", match_reason="dims+ply",
                price_rule="stated", price_value=val, currency="", price_basis="", gst="", offered_ply=None,
                spec_deviation="", source_ref=ref, source_snippet=snip, read_confidence="high", notes="")
    base.update(kw)
    return base


def qa(*answers):
    return [dict(q_id=f"Q{i}", answer=a, value=v, source_ref="", source_snippet=v)
            for i, (a, v) in enumerate(answers, 1)]


# V1 ------------------------------------------------------------------------------------
ws = load_workbook(D / "vendor_responses/V1_shree_balaji/Shree_Balaji_Quotation.xlsx").active
lq = []
for r in ws.iter_rows(min_row=7):
    dims = str(r[3].value).replace(" ", "")
    l = by_dims[dims]
    lq.append(q(l["line_id"], r[6].value, f"Quotation!G{r[0].row}", f"{r[2].value} {r[3].value} {r[4].value} {r[6].value}",
                currency="INR", price_basis="per_pc", gst="included"))
json.dump(dict(vendor_name="Shree Balaji Packaging", response_summary="Excel in own format, GST-inclusive.",
               document_defaults=dict(currency="INR", price_basis="per_pc", gst="included", freight="included"),
               line_quotes=lq, unquoted_line_ids=[], discounts=[],
               commercial_terms=dict(payment_terms="45 days only", payment_days=45, lead_time_days=12,
                                     validity="", freight_terms="included"),
               questionnaire=qa(("yes", "valid till 30-Jun-2027"), ("yes", "BCT + moisture every lot"),
                                ("value", "650 MT"), ("value", "12 days"), ("no", "45 days only")),
               attachments=[], ambiguities=[], _meta=META),
          open(out_dir / "V1_shree_balaji.json", "w"), indent=1)

# V2 ------------------------------------------------------------------------------------
with pdfplumber.open(D / "vendor_responses/V2_ganesh/Ganesh_Corrugators_Offer.pdf") as p:
    txt = p.pages[0].extract_text()
lq = []
for m in re.finditer(r"^(\d+) (.+?) (\d{2,4}x\d{2,4}(?:x\d{2,4})?) ?\*? (\d|5 \(alt\.\)) [\d,]+ ([\d.]+)$", txt, re.M):
    l = by_dims[m.group(3)]
    extra = {}
    if "alt" in m.group(4):
        extra = dict(offered_ply=5, spec_deviation="Vendor recommends 5-ply in place of 7-ply (footnote *)")
    lq.append(q(l["line_id"], float(m.group(5)), f"page 1 table row {m.group(1)}", m.group(0),
                currency="INR", price_basis="per_pc", gst="excluded", **extra))
json.dump(dict(vendor_name="Ganesh Corrugators", response_summary="PDF offer on letterhead.",
               document_defaults=dict(currency="INR", price_basis="per_pc", gst="excluded", freight="included"),
               line_quotes=lq, unquoted_line_ids=[],
               discounts=[dict(description="Special trade discount 4% on basic value", percent=4,
                               condition="total annual PO value > Rs 50,00,000", min_order_value_inr=5000000,
                               source_ref="page 1 footnote **", source_snippet="special trade discount of 4%")],
               commercial_terms=dict(payment_terms="60 days", payment_days=60, lead_time_days=12,
                                     validity="90 days", freight_terms="included"),
               questionnaire=qa(("yes", "copy attached"), ("yes", "with each consignment"),
                                ("value", "900 MT"), ("value", "10-12 days"), ("yes", "Accepted")),
               attachments=[dict(file="Ganesh_ISO9001_Certificate.pdf", doc_type="ISO 9001:2015 certificate",
                                 holder="GANESH CORRUGATORS", reference_no="BAS/QMS/77120", valid_until="2028-02-15")],
               ambiguities=["Validity 90 days vs RFx 12 months"], _meta=META),
          open(out_dir / "V2_ganesh.json", "w"), indent=1)

# V3 ------------------------------------------------------------------------------------
doc = Document(D / "vendor_responses/V3_packworld/PackWorld_Proposal.docx")
lq = []
for i, para in enumerate(doc.paragraphs, 1):
    for m in re.finditer(r"([a-z\- ]+?) (\d{2,4}x\d{2,4}(?:x\d{2,4})?) at USD (\d+\.\d+)", para.text):
        l = by_dims[m.group(2)]
        lq.append(q(l["line_id"], float(m.group(3)), f"para {i}", m.group(0).strip(),
                    currency="USD", price_basis="per_pc", gst="excluded"))
json.dump(dict(vendor_name="PackWorld Exports", response_summary="Word proposal, USD, FOB Chennai.",
               document_defaults=dict(currency="USD", price_basis="per_pc", gst="excluded", freight="fob_port"),
               line_quotes=lq, unquoted_line_ids=[], discounts=[],
               commercial_terms=dict(payment_terms="30 days from B/L; 50% advance first order", payment_days=30,
                                     lead_time_days=21, validity="6 months, kraft index clause",
                                     freight_terms="FOB Chennai, inland freight extra"),
               questionnaire=qa(("yes", "certificate attached"), ("partial", "on request at additional cost"),
                                ("value", "1,200 MT"), ("value", "21 days"), ("no", "cannot accept 60 days")),
               attachments=[dict(file="PackWorld_ISO_Certificate.pdf", doc_type="ISO 9001:2015 certificate",
                                 holder="PACKWORLD EXPORTS LLP", reference_no="IQC/9001/40518", valid_until="2025-08-14")],
               ambiguities=["Price revision clause if kraft index moves >5%"], _meta=META),
          open(out_dir / "V3_packworld.json", "w"), indent=1)

# V4 (photo: fixture uses ground truth x100, as a perfect OCR would) ----------------------
lq = []
for n, (lid, p) in enumerate(gt["Sai Krupa Box Industries"].items(), 1):
    l = next(x for x in rfx["lines"] if x["line_id"] == lid)
    lq.append(q(lid, round(p * 100), f"photo row {n}", f"{l['dimensions_mm']} {l['ply']}P {round(p * 100):,}",
                currency="INR", price_basis="per_100_pcs", gst="excluded",
                read_confidence="low" if n == 25 else "high",
                notes="row 25 price may belong to adjacent row (skewed photo)" if n == 25 else ""))
json.dump(dict(vendor_name="Sai Krupa Box Industries", response_summary="Phone photo of rate card via WhatsApp.",
               document_defaults=dict(currency="INR", price_basis="per_100_pcs", gst="excluded", freight="included"),
               line_quotes=lq, unquoted_line_ids=["L28", "L29", "L30"], discounts=[],
               commercial_terms=dict(payment_terms="30 days", payment_days=30, lead_time_days=None,
                                     validity="", freight_terms="door delivery"),
               questionnaire=qa(*[("not_answered", "")] * 5), attachments=[],
               ambiguities=["Generic rate card, not RFx-specific"], _meta=META),
          open(out_dir / "V4_sai_krupa.json", "w"), indent=1)

# V5 ------------------------------------------------------------------------------------
lq = []
for l in rfx["lines"]:
    if l["ply"] in (3, 5):
        rate = 42 if l["ply"] == 5 else 38
        lq.append(q(l["line_id"], rate, "email line 8",
                    "Rs 42/kg for the 5-ply, 38 for the 3-ply", currency="INR", price_basis="per_kg", gst="unknown",
                    vendor_item_text=f"{l['ply']}-ply (class rate)"))
    else:
        lq.append(q(l["line_id"], None, "email line 8", "rest same as last year", price_rule="same_as_last_year",
                    currency="INR", price_basis="per_pc", gst="excluded", vendor_item_text="rest"))
json.dump(dict(vendor_name="Om Packaging Solutions", response_summary="One-line email.",
               document_defaults=dict(currency="INR", price_basis="per_kg", gst="unknown", freight="extra"),
               line_quotes=lq, unquoted_line_ids=[], discounts=[],
               commercial_terms=dict(payment_terms="60 days ok", payment_days=60, lead_time_days=10,
                                     validity="", freight_terms="extra"),
               questionnaire=qa(("yes", "attached, valid till Mar 2027"), ("yes", "every batch"),
                                ("value", "~700 MT"), ("value", "10 days"), ("yes", "60 days ok")),
               attachments=[dict(file="Om_ISO9001.pdf", doc_type="ISO 9001:2015 certificate",
                                 holder="OM PACKAGING SOLUTIONS", reference_no="TQ-9001-55821", valid_until="2027-03-31")],
               ambiguities=["GST basis not stated"], _meta=META),
          open(out_dir / "V5_om_packaging.json", "w"), indent=1)
print("fixtures written to", out_dir, [len(json.load(open(f))["line_quotes"]) for f in sorted(out_dir.glob("*.json"))])
