"""
Extraction: turn whatever a vendor sent (xlsx / pdf / docx / photo / email) into
structured quote JSON, WITH provenance for every number.

Design choice: the LLM only READS. It copies numbers exactly as written and labels
their unit / currency / tax basis. It never converts or calculates. All conversion
happens in normalize.py in deterministic code, so every rupee on screen has a
reproducible audit trail.
"""
import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path

from docx import Document
from openpyxl import load_workbook

from . import llm

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "extractions"
CACHE.mkdir(parents=True, exist_ok=True)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


# ---------------------------------------------------------------- file readers --
def read_xlsx(p: Path) -> str:
    wb = load_workbook(p, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"### Sheet '{ws.title}'")
        for row in ws.iter_rows():
            cells = [f"{c.coordinate}={c.value!r}" for c in row if c.value is not None]
            if cells:
                out.append(" | ".join(cells))
    return "\n".join(out)


def read_pdf(p: Path) -> str:
    """pdfplumber if available (better layout), else pypdf (pure Python, installs anywhere)."""
    out = []
    try:
        import pdfplumber
        with pdfplumber.open(p) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                out.append(f"### Page {i}\n{page.extract_text() or '(no text layer)'}")
    except ImportError:
        from pypdf import PdfReader
        for i, page in enumerate(PdfReader(str(p)).pages, 1):
            out.append(f"### Page {i}\n{page.extract_text() or '(no text layer)'}")
    return "\n".join(out)


def read_docx(p: Path) -> str:
    d = Document(p)
    out = [f"[para {i}] {para.text}" for i, para in enumerate(d.paragraphs, 1) if para.text.strip()]
    for ti, t in enumerate(d.tables, 1):
        for ri, row in enumerate(t.rows, 1):
            out.append(f"[table {ti} row {ri}] " + " | ".join(c.text for c in row.cells))
    return "\n".join(out)


def read_text(p: Path) -> str:
    return "\n".join(f"[line {i}] {ln}" for i, ln in enumerate(p.read_text(errors="ignore").splitlines(), 1))


def load_bundle(folder: Path):
    """Return (text_parts, image_paths) for every file the vendor sent."""
    texts, images = [], []
    for f in sorted(folder.iterdir()):
        ext = f.suffix.lower()
        if ext in IMAGE_EXT:
            images.append(f)
            texts.append(f"=== FILE: {f.name} (image, attached below - read it visually) ===")
            continue
        reader = {".xlsx": read_xlsx, ".pdf": read_pdf, ".docx": read_docx}.get(ext, read_text)
        texts.append(f"=== FILE: {f.name} ===\n{reader(f)}")
    return texts, images


# ---------------------------------------------------------------- prompt --------
SCHEMA = """
Return ONE JSON object with exactly these keys:
{
  "vendor_name": str,
  "response_summary": str,                       // 1-2 sentences, what they sent
  "document_defaults": {
     "currency": "INR" | "USD" | "EUR" | "unknown",
     "price_basis": "per_pc" | "per_100_pcs" | "per_1000_pcs" | "per_dozen" | "per_kg" | "unknown",
     "gst": "included" | "excluded" | "unknown",
     "freight": "included" | "extra" | "fob_port" | "unknown",
     "basis_snippet": str,                        // verbatim text that states the basis
     "basis_source_ref": str
  },
  "line_quotes": [ {
     "rfx_line_id": "L01".."L30" | null,
     "vendor_item_text": str,                     // vendor's own description, verbatim
     "match_confidence": "high" | "medium" | "low",
     "match_reason": str,
     "price_rule": "stated" | "same_as_last_year",
     "price_value": number | null,                // EXACTLY as written; null if price_rule=same_as_last_year
     "currency": str, "price_basis": str, "gst": str,   // same enums as defaults; repeat defaults if line has no override
     "offered_ply": int | null,
     "spec_deviation": str,                        // "" if the vendor offers exactly the RFx spec
     "source_ref": str,                            // e.g. "Quotation!G14", "page 1 table row 7", "photo row 12", "email line 6", "para 6"
     "source_snippet": str,                        // verbatim text you read the number from
     "read_confidence": "high" | "medium" | "low", // how sure you are you READ it correctly (blur, alignment, handwriting)
     "notes": str
  } ],
  "unquoted_line_ids": [str],                      // RFx lines the vendor did not price at all
  "discounts": [ { "description": str, "percent": number | null, "condition": str,
                   "min_order_value_inr": number | null, "source_ref": str, "source_snippet": str } ],
  "commercial_terms": { "payment_terms": str, "payment_days": int | null, "lead_time_days": int | null,
                        "validity": str, "freight_terms": str, "other": [str] },
  "questionnaire": [ { "q_id": "Q1".."Q5", "answer": "yes" | "no" | "partial" | "not_answered" | "value",
                       "value": str, "source_ref": str, "source_snippet": str } ],
  "attachments": [ { "file": str, "doc_type": str, "holder": str, "reference_no": str,
                     "valid_until": "YYYY-MM-DD" | null } ],
  "ambiguities": [str]                             // anything a careful buyer would want to double-check
}
"""

RULES = """
You are a meticulous procurement analyst extracting a vendor's quotation. Rules:
1. COPY, DON'T CALCULATE. Report each price exactly as written, with the unit, currency and tax basis
   it was quoted in. Never convert per-100 to per-piece, never convert currency, never remove GST,
   never apply discounts. Deterministic code does all of that downstream.
2. One line_quotes entry per RFx line the vendor priced. Match vendor items to RFx lines by
   dimensions + ply + item type. Vendors use their own names and order. If a match is not certain,
   set match_confidence accordingly and explain.
3. If the vendor states a rule instead of a number (e.g. "rest same as last year"), emit an entry for
   EACH RFx line the rule covers with price_rule="same_as_last_year", price_value=null.
4. If a vendor prices one rate per kg for a whole class (e.g. "Rs 42/kg for 5-ply"), emit an entry for
   each RFx line in that class with price_value=42, price_basis="per_kg".
5. If the vendor offers something different from the RFx spec (other ply, other material), record
   offered_ply and spec_deviation. Do not silently treat it as compliant.
6. Read footnotes, annexures and small print. Discounts, freight and tax terms often hide there.
7. For photos: read row by row. If a row's price could belong to the neighbouring row (skew, blur),
   set read_confidence="low" and say so in notes. Never guess silently.
8. Questionnaire: answer only from what the vendor actually said. For certificates attached as files,
   fill "attachments" from the document itself (the attachment is evidence; the claim is not).
9. Anything ambiguous goes in "ambiguities". Being honestly unsure is better than being confidently wrong.
"""


def build_messages(rfx: dict, vendor_label: str, texts, images):
    rfx_lines = "\n".join(
        f"{l['line_id']}: {l['description']} | ply {l['ply']} | qty {l['annual_qty_pcs']} pcs"
        for l in rfx["lines"])
    qn = "\n".join(f"{q['q_id']}: {q['question']}" for q in rfx["questionnaire"])
    header = (f"RFx {rfx['rfx_id']} - requested price basis: {rfx['price_basis']}\n\n"
              f"RFx LINES:\n{rfx_lines}\n\nQUESTIONNAIRE:\n{qn}\n\n"
              f"VENDOR RESPONSE BUNDLE ({vendor_label}):\n\n" + "\n\n".join(texts))
    content = [{"type": "text", "text": header}]
    for img in images:
        mime = mimetypes.guess_type(img.name)[0] or "image/jpeg"
        b64 = base64.b64encode(img.read_bytes()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}})
    return [{"role": "system", "content": RULES + "\n" + SCHEMA},
            {"role": "user", "content": content}]


def extract_vendor(rfx: dict, folder: Path, model: str | None = None) -> dict:
    texts, images = load_bundle(folder)
    data = llm.chat_json(build_messages(rfx, folder.name, texts, images), model=model)
    data["_meta"] = {
        "folder": folder.name,
        "files": [f.name for f in sorted(folder.iterdir())],
        "model": model or llm.DEFAULT_MODEL,
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "source": "llm",
    }
    (CACHE / f"{folder.name}.json").write_text(json.dumps(data, indent=2))
    return data


def load_cached() -> dict:
    return {p.stem: json.loads(p.read_text()) for p in sorted(CACHE.glob("*.json"))}


def raw_text_for(folder: Path) -> str:
    texts, _ = load_bundle(folder)
    return "\n\n".join(texts)
