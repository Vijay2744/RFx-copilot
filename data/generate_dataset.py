"""
Generates the demo dataset: one RFx (30 lines of corrugated packaging), last year's
contract, and five vendor responses in five deliberately ugly formats.

Run:  python data/generate_dataset.py

Every edge case below is intentional and documented, so the demo can show what the
system does when it ISN'T sure:

  V1 Shree Balaji  -> Excel that ignores our template, own item codes, shuffled order,
                      prices INCLUSIVE of 18% GST, one decimal-slip typo (L12).
  V2 Ganesh        -> PDF on letterhead, 4% discount buried in a footnote (conditional
                      on PO value > Rs 50 lakh), offers 5-ply instead of 7-ply on L28.
  V3 PackWorld     -> Word doc, commercials in paragraphs, quoted in USD, FOB port
                      (freight extra). Claims ISO 9001 but attached certificate EXPIRED.
                      Cheapest vendor on paper.
  V4 Sai Krupa     -> Angled phone photo of a printed rate card, prices PER 100 PCS,
                      quoted only 27 of 30 lines, no questionnaire returned.
  V5 Om Packaging  -> Incumbent. One email: "Rs 42/kg for 5-ply, 38 for 3-ply, rest same
                      as last year, freight extra." Needs per-kg -> per-pc conversion and
                      last year's contract to resolve.
"""
import json
import random
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from docx import Document
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

random.seed(42)
ROOT = Path(__file__).parent
OUT = ROOT / "vendor_responses"
OUT.mkdir(exist_ok=True)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_M = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

# ----------------------------------------------------------------------------------
# 1. RFx line items
# ----------------------------------------------------------------------------------
GRAMMAGE = {3: 0.56, 5: 0.95, 7: 1.38}       # kg per m2 of combined board (approx)
MARKET_RATE = {3: 39.0, 5: 43.5, 7: 47.5}    # Rs/kg converted board, ex-GST, delivered
CONVERSION = {3: 1.2, 5: 2.0, 7: 3.2}        # Rs/pc printing + conversion

specs = [
    # (type, ply, L, W, H, qty)
    ("RSC box", 3, 300, 200, 150, 60000), ("RSC box", 3, 320, 240, 180, 55000),
    ("RSC box", 3, 350, 250, 200, 50000), ("RSC box", 3, 380, 280, 220, 45000),
    ("RSC box", 3, 400, 300, 250, 40000), ("RSC box", 3, 260, 180, 120, 70000),
    ("RSC box", 3, 230, 160, 100, 80000), ("RSC box", 3, 450, 320, 260, 30000),
    ("Die-cut mailer", 3, 310, 220, 80, 50000), ("Die-cut mailer", 3, 250, 180, 60, 65000),
    ("Partition set", 3, 390, 290, 210, 25000), ("RSC box", 3, 420, 310, 280, 35000),
    ("RSC box", 5, 400, 300, 300, 30000), ("RSC box", 5, 450, 350, 300, 28000),
    ("RSC box", 5, 500, 400, 350, 25000), ("RSC box", 5, 550, 400, 400, 20000),
    ("RSC box", 5, 600, 400, 400, 18000), ("RSC box", 5, 480, 360, 320, 26000),
    ("RSC box", 5, 520, 420, 380, 22000), ("RSC box", 5, 580, 380, 360, 19000),
    ("RSC box", 5, 620, 450, 420, 15000), ("RSC box", 5, 460, 340, 280, 27000),
    ("RSC box", 5, 530, 430, 330, 21000), ("RSC box", 5, 650, 500, 450, 12000),
    ("Corrugated pad", 5, 600, 400, 0, 40000), ("Die-cut tray", 5, 440, 330, 120, 24000),
    ("RSC box", 5, 700, 500, 500, 10000),
    ("Heavy-duty RSC box", 7, 800, 600, 600, 6000),
    ("Heavy-duty RSC box", 7, 900, 700, 600, 5000),
    ("Heavy-duty RSC box", 7, 1000, 800, 700, 4000),
]


def area_m2(t, L, W, H):
    if t == "Corrugated pad":
        return L * W / 1e6
    if t == "Partition set":
        return (L * H * 2 + W * H * 3) / 1e6
    if "tray" in t or "mailer" in t:
        return (L * W * 2 + 2 * (L + W) * H) / 1e6 * 1.15
    return 2 * (L + W) * (W + H) / 1e6 * 1.05


lines = []
for i, (t, ply, L, W, H, qty) in enumerate(specs, start=1):
    w = round(area_m2(t, L, W, H) * GRAMMAGE[ply], 3)
    market = w * MARKET_RATE[ply] + CONVERSION[ply]
    dims = f"{L}x{W}" + (f"x{H}" if H else "")
    lines.append({
        "line_id": f"L{i:02d}",
        "description": f"{ply}-ply {t} {dims} mm",
        "item_type": t,
        "ply": ply,
        "dimensions_mm": dims,
        "weight_kg_per_pc": w,
        "annual_qty_pcs": qty,
        "_market": round(market, 2),
    })

# scale quantities so total value at market ~ Rs 4 crore
total = sum(l["_market"] * l["annual_qty_pcs"] for l in lines)
scale = 4.0e7 / total
for l in lines:
    l["annual_qty_pcs"] = int(round(l["annual_qty_pcs"] * scale, -2))

RFX = {
    "rfx_id": "RFQ-PKG-2026-114",
    "title": "Annual rate contract - corrugated packaging (FY 2026-27)",
    "buyer": "Kethe, Category Buyer - Packaging, Acme Consumer Goods Ltd",
    "issue_date": "2026-11-02",
    "due_date": "2026-11-11",
    "currency": "INR",
    "price_basis": "Rs per piece, excluding GST, delivered to Acme plant, Hosur (FOR destination)",
    "terms": {
        "payment": "60 days from invoice",
        "validity": "Prices firm for 12 months",
        "delivery": "Within 14 days of PO, to Hosur plant",
        "gst": "Quote ex-GST; GST @18% extra",
    },
    "questionnaire": [
        {"q_id": "Q1", "question": "Do you hold a valid ISO 9001 certificate? Attach a copy.", "type": "yes_no"},
        {"q_id": "Q2", "question": "Will you provide BCT/ECT test reports with every batch?", "type": "yes_no"},
        {"q_id": "Q3", "question": "Monthly converting capacity (metric tonnes)?", "type": "number"},
        {"q_id": "Q4", "question": "Lead time from PO to delivery (days)?", "type": "number"},
        {"q_id": "Q5", "question": "Do you accept 60-day payment terms?", "type": "yes_no"},
    ],
    "quality_gate": "Q1 = Yes with a valid (unexpired) certificate AND Q2 = Yes",
    "vendors_invited": ["Shree Balaji Packaging", "Ganesh Corrugators", "PackWorld Exports",
                        "Sai Krupa Box Industries", "Om Packaging Solutions"],
    "lines": [{k: v for k, v in l.items() if not k.startswith("_")} for l in lines],
}
(ROOT / "rfx.json").write_text(json.dumps(RFX, indent=2))

# ----------------------------------------------------------------------------------
# 2. Last year's contract (incumbent: Om Packaging, all lines)
# ----------------------------------------------------------------------------------
last_year = {}
with open(ROOT / "last_year_contract.csv", "w", newline="") as f:
    wr = csv.writer(f)
    wr.writerow(["vendor", "line_id", "description", "price_inr_per_pc", "basis", "contract_ref"])
    for l in lines:
        p = round(l["_market"] * random.uniform(0.95, 1.0), 2)
        last_year[l["line_id"]] = p
        wr.writerow(["Om Packaging Solutions", l["line_id"], l["description"], p,
                     "ex-GST, delivered Hosur", "RC-2025-PKG-031"])

# ----------------------------------------------------------------------------------
# 3. Vendor price "truth" (what each vendor actually intends to charge, per pc, ex-GST)
# ----------------------------------------------------------------------------------
def vprice(l, lo, hi):
    return l["_market"] * random.uniform(lo, hi)

truth = {}

# ---------------- V1: Shree Balaji - Excel, own layout, GST-inclusive -------------
v1 = OUT / "V1_shree_balaji"
v1.mkdir(exist_ok=True)
wb = Workbook()
ws = wb.active
ws.title = "Quotation"
ws["A1"] = "SHREE BALAJI PACKAGING PVT LTD"
ws["A1"].font = Font(bold=True, size=14, color="FFFFFF")
ws["A1"].fill = PatternFill("solid", fgColor="1F4E78")
ws.merge_cells("A1:G1")
ws["A2"] = "Plot 44, Bommasandra Industrial Area, Bengaluru | GSTIN 29AABCS1234F1Z5"
ws["A3"] = "Ref: Your RFQ-PKG-2026-114   Date: 08-Nov-2026"
ws["A4"] = "All rates are INCLUSIVE of GST @18%. Delivery included to Hosur."
ws["A4"].font = Font(italic=True, color="C00000")
hdr = ["Sr", "Our Code", "Item", "Size (mm)", "Ply", "MOQ", "Rate/pc (incl. GST)"]
for c, h in enumerate(hdr, 1):
    cell = ws.cell(row=6, column=c, value=h)
    cell.font = Font(bold=True)
    cell.fill = PatternFill("solid", fgColor="DDEBF7")
order = lines[:]
random.shuffle(order)
truth["Shree Balaji Packaging"] = {}
for r, l in enumerate(order, start=7):
    p = vprice(l, 0.93, 1.05)
    truth["Shree Balaji Packaging"][l["line_id"]] = p
    shown = round(p * 1.18, 2)
    if l["line_id"] == "L12":          # decimal slip typo
        shown = round(shown / 10, 2)
    name = {"RSC box": "Carton", "Die-cut mailer": "DC Mailer", "Partition set": "Partition",
            "Corrugated pad": "Pad", "Die-cut tray": "DC Tray",
            "Heavy-duty RSC box": "HD Carton"}[l["item_type"]]
    ws.append([r - 6, f"SBP-{1000 + r * 7}", name, l["dimensions_mm"].replace("x", " x "),
               f"{l['ply']}P", 5000, shown])
for col, wdt in zip("ABCDEFG", [5, 12, 12, 18, 6, 8, 20]):
    ws.column_dimensions[col].width = wdt
q = wb.create_sheet("Compliance")
q.append(["Particular", "Response"])
q.append(["ISO 9001:2015", "Yes - certificate no. QMS/IN/2291, valid till 30-Jun-2027"])
q.append(["Batch test reports (BCT/ECT)", "Yes, BCT + moisture report with every lot"])
q.append(["Capacity", "650 MT per month"])
q.append(["Lead time", "12 days"])
q.append(["Credit period", "45 days only"])
q.column_dimensions["A"].width = 30
q.column_dimensions["B"].width = 60
wb.save(v1 / "Shree_Balaji_Quotation.xlsx")

# ---------------- V2: Ganesh - PDF letterhead, footnote discount, spec swap ----------
v2 = OUT / "V2_ganesh"
v2.mkdir(exist_ok=True)
truth["Ganesh Corrugators"] = {}
styles = getSampleStyleSheet()
small = ParagraphStyle("small", parent=styles["Normal"], fontSize=6.5, leading=8, textColor=colors.grey)
doc = SimpleDocTemplate(str(v2 / "Ganesh_Corrugators_Offer.pdf"), pagesize=A4,
                        topMargin=15 * mm, bottomMargin=15 * mm)
story = [
    Paragraph("<font size=16 color='#8B0000'><b>GANESH CORRUGATORS</b></font>", styles["Title"]),
    Paragraph("<font size=8>Survey No. 112, Chakan MIDC, Pune 410501 &nbsp;|&nbsp; ISO 9001:2015 &nbsp;|&nbsp; "
              "sales@ganeshcorr.example</font>", styles["Normal"]),
    Spacer(1, 6 * mm),
    Paragraph("To: Acme Consumer Goods Ltd, Hosur &nbsp;&nbsp; Kind Attn: Mr. Kethe", styles["Normal"]),
    Paragraph("Sub: Our best offer against RFQ-PKG-2026-114 &nbsp;&nbsp; Date: 09/11/2026", styles["Normal"]),
    Spacer(1, 4 * mm),
    Paragraph("Dear Sir, we thank you for the enquiry and are pleased to quote as under. "
              "Rates are per piece, exclusive of GST, inclusive of freight to your Hosur plant.",
              styles["Normal"]),
    Spacer(1, 4 * mm),
]
rows = [["S.No", "Description", "Ply", "Qty/yr", "Rate (Rs/pc)"]]
for n, l in enumerate(lines, 1):
    p = vprice(l, 0.95, 1.04)
    desc = f"{l['item_type']} {l['dimensions_mm']}"
    ply = str(l["ply"])
    if l["line_id"] == "L28":
        # offers 5-ply in place of 7-ply: cheaper, but not to spec
        p = p * 0.72
        desc += " *"
        ply = "5 (alt.)"
    truth["Ganesh Corrugators"][l["line_id"]] = p
    rows.append([str(n), desc, ply, f"{l['annual_qty_pcs']:,}", f"{p:.2f}"])
t = Table(rows, colWidths=[12 * mm, 80 * mm, 18 * mm, 25 * mm, 25 * mm])
t.setStyle(TableStyle([
    ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2DCDB")),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
]))
story += [t, Spacer(1, 3 * mm),
          Paragraph("Payment: 60 days. Lead time: 10-12 days. Validity: 90 days.", styles["Normal"]),
          Spacer(1, 6 * mm),
          Paragraph("Thanking you, for GANESH CORRUGATORS, (Authorised Signatory)", styles["Normal"]),
          Spacer(1, 10 * mm),
          Paragraph("* Item 28: We recommend 5 ply (BS 32 kg) in place of 7 ply for this size based on "
                    "our experience; 7 ply available at +38% on request. "
                    "&nbsp;&nbsp;** A special trade discount of 4% on basic value will be extended "
                    "if the total annual PO value placed on us exceeds Rs. 50,00,000/-. "
                    "&nbsp;&nbsp;E&amp;OE. Subject to Pune jurisdiction.", small),
          PageBreak(),
          Paragraph("<b>Annexure A - Supplier Questionnaire</b>", styles["Heading3"]),
          Paragraph("1. ISO 9001 certificate: Yes, copy attached (valid till 15-Feb-2028).", styles["Normal"]),
          Paragraph("2. BCT/ECT reports: Yes, provided with each consignment.", styles["Normal"]),
          Paragraph("3. Capacity: 900 MT/month across 2 plants.", styles["Normal"]),
          Paragraph("4. Lead time: 10-12 days from PO.", styles["Normal"]),
          Paragraph("5. 60-day payment: Accepted.", styles["Normal"]),
          ]
doc.build(story)


def cert_pdf(path, holder, number, issued, expiry, body="Bureau Assurance Services Pvt Ltd"):
    d = SimpleDocTemplate(str(path), pagesize=A4)
    st = [Spacer(1, 30 * mm),
          Paragraph("<para align=center><font size=22><b>CERTIFICATE</b></font></para>", styles["Title"]),
          Spacer(1, 10 * mm),
          Paragraph(f"<para align=center>This is to certify that the Quality Management System of<br/><br/>"
                    f"<font size=14><b>{holder}</b></font><br/><br/>has been assessed and found to conform to"
                    f"<br/><br/><b>ISO 9001:2015</b><br/><br/>Scope: Manufacture of corrugated boxes and "
                    f"packaging materials</para>", styles["Normal"]),
          Spacer(1, 15 * mm),
          Paragraph(f"<para align=center>Certificate No: {number}<br/>Date of issue: {issued}<br/>"
                    f"<b>Valid until: {expiry}</b><br/><br/>{body}</para>", styles["Normal"])]
    d.build(st)


cert_pdf(v2 / "Ganesh_ISO9001_Certificate.pdf", "GANESH CORRUGATORS", "BAS/QMS/77120",
         "16-Feb-2025", "15-Feb-2028")

# ---------------- V3: PackWorld - Word, paragraphs, USD, FOB, expired ISO ------------
v3 = OUT / "V3_packworld"
v3.mkdir(exist_ok=True)
FX_TRUE = 88.0
truth["PackWorld Exports"] = {}
d = Document()
d.styles["Normal"].font.size = Pt(10.5)
d.add_heading("PackWorld Exports LLP", level=1)
d.add_paragraph("Export Oriented Unit, Sriperumbudur SEZ, Tamil Nadu | www.packworld.example")
d.add_paragraph("Date: 10 November 2026")
d.add_paragraph("Dear Mr. Kethe,")
d.add_paragraph(
    "Thank you for including us in RFQ-PKG-2026-114. As an export-oriented unit our price list is "
    "maintained in US Dollars, and we are pleased to extend the same to Acme. All prices below are in "
    "USD per piece, FOB Chennai port, exclusive of GST. Inland freight from Chennai port to your Hosur "
    "plant will be charged extra at actuals.")
groups = [("3-ply", [l for l in lines if l["ply"] == 3]),
          ("5-ply", [l for l in lines if l["ply"] == 5]),
          ("7-ply heavy-duty", [l for l in lines if l["ply"] == 7])]
for gname, gl in groups:
    parts = []
    for l in gl:
        p = vprice(l, 0.84, 0.93)
        truth["PackWorld Exports"][l["line_id"]] = p
        parts.append(f"{l['item_type'].lower()} {l['dimensions_mm']} at USD {p / FX_TRUE:.3f}")
    d.add_paragraph(
        f"For the {gname} range, our offer is as follows: " + "; ".join(parts) +
        ". These rates assume full-truckload call-offs.")
d.add_paragraph(
    "Commercial terms: payment 30 days from date of bill of lading; 50% advance for the first order. "
    "Lead time is 21 days from PO. Prices are valid for 6 months and are subject to revision if the "
    "kraft paper index moves by more than 5%.")
d.add_paragraph(
    "Regarding your questionnaire - we are an ISO 9001:2015 certified organisation (certificate "
    "attached for your reference). Test reports can be provided on request at additional cost. Our "
    "monthly capacity is 1,200 MT. We regret we cannot accept 60-day terms for domestic supplies.")
d.add_paragraph("Warm regards,\nR. Venkatesh\nHead - Domestic Sales")
d.save(v3 / "PackWorld_Proposal.docx")
cert_pdf(v3 / "PackWorld_ISO_Certificate.pdf", "PACKWORLD EXPORTS LLP", "IQC/9001/40518",
         "15-Aug-2022", "14-Aug-2025", body="InterQual Certification Ltd")

# ---------------- V4: Sai Krupa - angled photo, per 100 pcs, 27 of 30 ---------------
v4 = OUT / "V4_sai_krupa"
v4.mkdir(exist_ok=True)
truth["Sai Krupa Box Industries"] = {}
W_, H_ = 1500, 2000
img = Image.new("RGB", (W_, H_), (250, 247, 238))
dr = ImageDraw.Draw(img)
fb = ImageFont.truetype(FONT_B, 44)
f = ImageFont.truetype(FONT, 26)
fm = ImageFont.truetype(FONT_M, 25)
dr.text((W_ // 2, 70), "SAI KRUPA BOX INDUSTRIES", font=fb, fill=(20, 20, 90), anchor="mm")
dr.text((W_ // 2, 125), "Hosur Road, Attibele  |  Ph: 98450 XXXXX", font=f, fill=(40, 40, 40), anchor="mm")
dr.text((W_ // 2, 180), "RATE CARD 2026-27   (Rates per 100 pcs, GST extra, Door delivery)",
        font=ImageFont.truetype(FONT_B, 27), fill=(150, 0, 0), anchor="mm")
y = 240
dr.line((80, y, W_ - 80, y), fill=(0, 0, 0), width=3)
dr.text((100, y + 12), "No  Item / Size (mm)                     Ply    Rs/100 pcs", font=fm, fill=(0, 0, 0))
y += 55
dr.line((80, y, W_ - 80, y), fill=(0, 0, 0), width=2)
n = 0
for l in lines:
    if l["ply"] == 7:          # does not make 7-ply -> 27 of 30
        continue
    n += 1
    p = vprice(l, 0.90, 1.02)
    truth["Sai Krupa Box Industries"][l["line_id"]] = p
    short = {"RSC box": "BOX", "Die-cut mailer": "DC MAILER", "Partition set": "PARTITION",
             "Corrugated pad": "PAD", "Die-cut tray": "DC TRAY"}[l["item_type"]]
    item = f"{short} {l['dimensions_mm']}"
    dr.text((100, y + 12), f"{n:>2}  {item:<36} {l['ply']}P   {p * 100:>10,.0f}", font=fm, fill=(10, 10, 10))
    y += 54
dr.line((80, y + 8, W_ - 80, y + 8), fill=(0, 0, 0), width=2)
dr.text((100, y + 30), "* 7 ply not available.  Min order 2000 pcs per size.  Payment 30 days.",
        font=f, fill=(60, 60, 60))
# phone-photo treatment: perspective skew, rotate, vignette-ish background, blur
bg = Image.new("RGB", (W_ + 500, H_ + 500), (92, 78, 64))
bg.paste(img, (250, 250))
coeffs_src = [(250, 250), (W_ + 250, 250), (W_ + 250, H_ + 250), (250, H_ + 250)]
coeffs_dst = [(290, 270), (W_ + 230, 245), (W_ + 270, H_ + 280), (225, H_ + 235)]


def find_coeffs(pa, pb):
    import numpy as np
    matrix = []
    for p1, p2 in zip(pa, pb):
        matrix.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0] * p1[0], -p2[0] * p1[1]])
        matrix.append([0, 0, 0, p1[0], p1[1], 1, -p2[1] * p1[0], -p2[1] * p1[1]])
    A = np.array(matrix, dtype=float)
    B = np.array(pb, dtype=float).reshape(8)
    return np.linalg.solve(A, B).tolist()


warped = bg.transform(bg.size, Image.PERSPECTIVE, find_coeffs(coeffs_dst, coeffs_src), Image.BICUBIC)
warped = warped.rotate(-1.2, resample=Image.BICUBIC, fillcolor=(92, 78, 64))
# uneven lighting
shade = Image.new("L", warped.size, 0)
sd = ImageDraw.Draw(shade)
for i in range(warped.size[0]):
    sd.line((i, 0, i, warped.size[1]), fill=int(60 * i / warped.size[0]))
dark = Image.new("RGB", warped.size, (0, 0, 0))
warped = Image.composite(dark, warped, shade)
warped = warped.filter(ImageFilter.GaussianBlur(0.9)).resize((1300, 1615))
warped.save(v4 / "SaiKrupa_RateCard_photo.jpg", quality=80)
(v4 / "whatsapp_message.txt").write_text(
    "From: Sai Krupa Box Industries (WhatsApp, forwarded to procurement inbox)\n"
    "Date: 11-Nov-2026 19:42\n\n"
    "Sir pls find our rate card photo. Same rates for Acme. Thank you.\n")

# ---------------- V5: Om Packaging - one email, per kg, "rest same as last year" -----
v5 = OUT / "V5_om_packaging"
v5.mkdir(exist_ok=True)
truth["Om Packaging Solutions"] = {}
for l in lines:
    if l["ply"] == 5:
        truth["Om Packaging Solutions"][l["line_id"]] = 42 * l["weight_kg_per_pc"]
    elif l["ply"] == 3:
        truth["Om Packaging Solutions"][l["line_id"]] = 38 * l["weight_kg_per_pc"]
    else:
        truth["Om Packaging Solutions"][l["line_id"]] = last_year[l["line_id"]]
(v5 / "email.eml").write_text(
    "From: Ramesh Agarwal <ramesh@ompackaging.example>\n"
    "To: kethe@acme.example\n"
    "Date: Tue, 11 Nov 2026 22:17:05 +0530\n"
    "Subject: RE: RFQ-PKG-2026-114 - corrugated boxes annual contract\n\n"
    "Hi Kethe ji,\n\n"
    "Rs 42/kg for the 5-ply, 38 for the 3-ply, rest same as last year, freight extra.\n"
    "ISO cert attached (valid till Mar 2027), test reports with every batch as always. "
    "60 days ok. Lead time 10 days, capacity not an issue (~700 MT/month).\n\n"
    "Regards\nRamesh\nOm Packaging Solutions\n")
cert_pdf(v5 / "Om_ISO9001.pdf", "OM PACKAGING SOLUTIONS", "TQ-9001-55821", "01-Apr-2024", "31-Mar-2027",
         body="TrustQual Registrars")

# ground truth for offline tests ONLY (never read by the app)
(ROOT / "_ground_truth_for_tests.json").write_text(json.dumps(
    {v: {k: round(p, 2) for k, p in d_.items()} for v, d_ in truth.items()}, indent=2))

tot = sum(l["_market"] * l["annual_qty_pcs"] for l in lines)
print(f"RFx: {len(lines)} lines, market value ~Rs {tot / 1e7:.2f} Cr")
for v, d_ in truth.items():
    print(f"  {v:28s} quoted {len(d_):2d} lines")
