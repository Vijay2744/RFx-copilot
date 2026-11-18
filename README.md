# Kill the Quote Spreadsheet: RFx Co-pilot

A buyer talks an RFx into existence with an AI co-pilot. Five vendors reply however they like: Excel, a
letterhead PDF, a Word document, a phone photo of a rate card, a one-line email. The system reads every
reply, puts all of them on one basis (**₹ per piece, ex-GST, delivered**), marks every number it isn't sure
about and says why, and lets the buyer ask questions in plain English. The buyer then shapes the award
themselves (split orders, replace vendors, lock lines) and exports a defensible decision with a full audit
trail.

> **The thesis:** savings depend on trust. A buyer will only split ₹4 crore across vendors if they believe the
> normalized numbers. So the product isn't the extractor. It's the **trust layer** that makes it safe to act
> on what the extractor read, and the **control** that lets the buyer make the final call.

**Read next:**
[How it answers the brief, point by point](docs/PDF_MAPPING.md) ·
[Design decisions](docs/DESIGN_DECISIONS.md) ·
[Architecture](docs/ARCHITECTURE.md) ·
[The demo dataset](docs/DATASET.md) ·
[Demo script](docs/DEMO_SCRIPT.md) ·
[One-page note](NOTE.md)

---

## The problem, in one paragraph

A category buyer sends an RFx for 30 items to five vendors. Over nine days the replies come back in five
shapes, with the units, currency, tax and freight all different, some lines missing, and important terms
buried in footnotes. The buyer spends three days retyping everything into Excel, and still can't be sure the
comparison is right. Then the VP asks "what if we split it, cheapest per line, only among vendors who cleared
quality?" and another day goes. **The cost isn't the typing. It's a ₹4 crore decision made on numbers
nobody fully trusts**, which pushes buyers back to "give it all to the usual vendor" and leaves 2–5% savings
on the table.

## What the buyer does in the app

The screen has a **progress strip** on top and two halves: the **workspace** on the left and the
**conversation** on the right.

| Step | Left: workspace | Right: conversation |
|---|---|---|
| **① RFx drafted** | The draft RFx: lines, terms, questionnaire, and open points that would make replies messy | **Co-pilot.** Talk the RFx into existence; it insists on one unit, currency, GST basis, freight basis and a quality gate |
| **② Sent to vendors** | One click; invitations go to the outbox (email stubbed) | — |
| **③ Replies read** | One card per vendor: what they sent, lines read, quality ✓/✗, and what the AI found ("prices included GST", "discount in footnote", "certificate expired") | **Analyst** (available from here on) |
| **④ Compare and decide: Comparison** | 30 × 5 grid on one basis, coloured 🟢🟠🔴. Questionnaire answers, attached documents and terms in the same columns underneath. **Click any price** to see what the vendor wrote, where, the conversion steps and the flags; accept, correct or exclude it; draft a clarification email | **Analyst.** Plain-English questions → text, tables, charts, Excel export, and "show the work" (the code it ran) |
| **④ Compare and decide: Award and sign-off** | The recommended award, then **the buyer's own award**: change any line's vendor, **split a line between two vendors by %**, **untick a vendor to replace them**, lock lines and re-optimize the rest. Live totals vs recommendation and last year; purchase orders per vendor; conditional discounts re-checked; a reason needed for every change; export locked until blocked items and assumptions are dealt with | **Analyst** sees the buyer's current award ("what if I drop Om?") |

## Quick start

```bash
git clone https://github.com/Vijay2744/RFx-copilot.git && cd RFx-copilot
python3 -m pip install -r requirements.txt
echo "OPENAI_API_KEY=sk-..." > .env          # never commit this file
python3 -m streamlit run app.py
```

In the app: **③ Replies read → Read all replies with AI** (about 1–2 minutes), then **④ Compare and decide**.
The extraction is cached, so later runs open instantly.

- **Model:** any OpenAI model with vision, JSON mode and tool calling. Default `gpt-4.1`; set `OPENAI_MODEL`
  in `.env` to change it.
- **No key?** `python3 tests/make_fixture.py cache/extractions` loads rule-based test data so you can click
  around. The app shows a warning banner while it's loaded, so it's never mistaken for AI output.
- **Mac install trouble** (SSL or build errors): `python3 -m pip install --upgrade pip certifi`, then
  `export SSL_CERT_FILE=$(python3 -m certifi)`, then install again.

## The ugly edges, and what the buyer sees

| Edge (from the brief) | What happens | On screen |
|---|---|---|
| Angled photo | Read with vision; unsure rows marked | 🟠 "reading is uncertain", photo shown next to the number |
| Vendor quoted 27 of 30 | Missing lines are explicit | 🔴 "didn't quote 3 lines: L28, L29, L30" + draft clarification |
| Quoted in USD | Converted at a buyer-set rate | 🟠 with the rate in the working |
| "Per box" vs "per 100 pieces" | Unit captured as written, converted exactly | 🟢 with the ÷100 step shown |
| "₹42/kg … rest same as last year, freight extra" | × nominal box weight; last year's contract; freight estimated; double-charge risk flagged | 🟠 grouped into plain-English items |
| GST hidden in prices | ÷1.18 in code | 🟢 with the step shown |
| Discount in a footnote | Extracted with its ₹50 L condition, modelled in the optimizer | Shown on the vendor card and in the award |
| 5-ply offered for 7-ply | Spec deviation | 🔴 blocked until the buyer accepts or excludes it |
| Decimal typo | Peer-median check | 🔴 "0.10× the peer median" |
| ISO claimed, certificate expired | Claim checked against the attachment | Vendor fails the quality check, with the reason |

## Five decisions that matter most

1. **The AI reads; code calculates.** The model copies numbers exactly as written; every conversion is
   deterministic and shown. Wrong arithmetic is the one error a buyer never forgives.
2. **Confidence comes from checks, not the model's opinion.** Unit math, spec match, peer median, last year,
   claim vs evidence. "87% confident" isn't actionable; "quoted per kg, depends on box weight" is.
3. **Maker-checker.** The AI makes, the buyer checks. Blocked prices can't be awarded; every decision is
   logged and exported.
4. **The optimizer proposes; the buyer decides.** Split orders, vendor replacement, locks, with every change
   priced live and a reason recorded. Buyers have context the data doesn't.
5. **The analyst writes code, not numbers.** Every answer comes from code you can open.

Full reasoning, alternatives considered and what was left out: [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md).

## Repo map

```
app.py                      Streamlit UI: progress strip, workspace, conversation, award
core/extract.py             file readers with references + LLM extraction (copy, don't calculate) + cache
core/normalize.py           ₹/pc ex-GST delivered; checks; 🟢🟠🔴 flags; buyer overrides; vendor facts
core/analysis.py            MILP award optimizer (quality, caps, conditional discounts); stress test
core/award.py               the buyer's award: per-line vendor, split %, replacement, locks, reasons, PO lines
core/analyst.py             plain-English analyst: OpenAI tool-calling → runs pandas code → text/tables/charts
core/copilot.py             RFx co-pilot, vendor invitations, clarification emails
core/llm.py                 OpenAI wrapper
data/generate_dataset.py    builds the RFx, last year's contract and the five messy replies
data/vendor_responses/      the five replies (Excel, PDF, Word, photo, email) + attached certificates
tests/make_fixture.py       offline test data (flagged in the UI)
docs/                       brief mapping, decisions, architecture, dataset, demo script
NOTE.md                     one-page note: decisions and what was left out
```

## What's stubbed

Email send and receive (written to `cache/outbox/`), authentication, a database (JSON in `cache/`), and
multi-user approvals. The analyst runs model-written code locally: fine for a single-user prototype, but it
needs a sandbox in production.
