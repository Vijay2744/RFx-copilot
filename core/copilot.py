"""
RFx co-pilot (upstream) and clarification drafter (downstream).

Key product bet: most normalization pain is created upstream by a vague RFx. The
co-pilot refuses to let the RFx go out without a fixed price unit, currency, GST basis,
freight term and a quality questionnaire, because each missing one becomes an amber flag
on every vendor response nine days later.
"""
import json

from . import llm

COPILOT_SYSTEM = """You are an RFx co-pilot for an Indian enterprise category buyer. You help them talk an
RFx into existence: scope, line items, questionnaire, terms. Be brief and practical.

Before the RFx can be marked ready, these MUST be explicit (ask for any that are missing; propose a
sensible default the buyer can accept with one word):
- price unit (per piece / per 100 / per kg) - insist on ONE unit for all vendors
- currency (default INR) and that FX quotes will be converted at a stated rate
- GST basis (ex-GST recommended) and freight basis (delivered to site recommended)
- quantity and full spec per line (for corrugated: ply, dimensions L x W x H mm, item type)
- quality questionnaire with a pass/fail gate, and documents vendors must attach
- payment terms, validity, delivery lead time, response due date

Always reply with JSON:
{"reply": "<your message to the buyer, <= 120 words>",
 "rfx": {<the full current draft: rfx_id, title, currency, price_basis, terms{payment,validity,delivery,gst},
          questionnaire[{q_id,question,type}], quality_gate, vendors_invited[], due_date,
          lines[{line_id,description,item_type,ply,dimensions_mm,weight_kg_per_pc,annual_qty_pcs}]}>,
 "open_issues": ["<each thing that will cause messy vendor responses if left unfixed>"],
 "ready_to_send": true|false}
Keep previous draft content unless the buyer changes it. Estimate weight_kg_per_pc if not given and say
it is an estimate."""


def copilot_turn(history: list, draft: dict | None) -> dict:
    msgs = [{"role": "system", "content": COPILOT_SYSTEM}]
    if draft:
        msgs.append({"role": "system", "content": "CURRENT DRAFT:\n" + json.dumps(draft)[:12000]})
    msgs += history
    return llm.chat_json(msgs)


def vendor_invite_email(rfx: dict, vendor: str) -> str:
    lines = "\n".join(f"  {l['line_id']}  {l['description']}  qty {l['annual_qty_pcs']:,}" for l in rfx["lines"])
    qn = "\n".join(f"  {q['q_id']}. {q['question']}" for q in rfx["questionnaire"])
    return (f"To: {vendor}\nSubject: {rfx['rfx_id']} - {rfx['title']}\n\n"
            f"Dear {vendor} team,\n\nPlease quote for the items below by {rfx.get('due_date')}.\n"
            f"Price basis: {rfx['price_basis']}.\nReply in any format you like - Excel, PDF, or just email.\n\n"
            f"{lines}\n\nQuestionnaire:\n{qn}\n\nTerms: {json.dumps(rfx.get('terms', {}))}\n\nRegards,\n{rfx.get('buyer', 'Buyer')}\n")


CLARIFY_SYSTEM = """You draft short, polite, specific clarification emails from an Indian procurement buyer
to a vendor. List each open point as a numbered question the vendor can answer in one line. Quote the
vendor's own words where helpful. No fluff. Plain text, with a Subject line first."""


def clarification_email(rfx: dict, vendor: str, issues: list[str]) -> str:
    body = (f"RFx {rfx['rfx_id']} ({rfx['title']}). Vendor: {vendor}.\n"
            f"Our requested basis: {rfx['price_basis']}.\nOpen points found in their response:\n- "
            + "\n- ".join(issues))
    return llm.chat_text([{"role": "system", "content": CLARIFY_SYSTEM}, {"role": "user", "content": body}])
