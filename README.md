# Kill the Quote Spreadsheet: RFx Co-pilot

A buyer drafts an RFx with an AI co-pilot. Five vendors reply however they like: Excel, PDF,
Word, a phone photo or a one-line email. The system reads every response, turns them all into
**₹ per piece, ex-GST, landed at site**, marks every number it isn't sure about, and lets the
buyer ask questions in plain English, all the way to a gated, auditable award.

> **The thesis:** savings depend on trust. A buyer will only split ₹4 Cr across vendors if they
> believe the normalized numbers. So the product isn't the extractor. It's the **trust layer**
> that makes it safe to act on what the extractor produced.

## Quick start (5 minutes)

```bash
git clone <your-repo> && cd rfx-copilot
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # paste your OPENAI_API_KEY
python data/generate_dataset.py                          # optional: regenerates the demo data
streamlit run app.py
```

In the app, click **⚡ Extract ALL vendor responses with AI** in the sidebar (about 1–2 min for 5
vendors). Then use the tabs from left to right.

*No API key yet?* Run `python tests/make_fixture.py cache/extractions` to load rule-based test
extractions. The UI shows a red banner when fixtures are loaded, so nobody mistakes them for
AI output.

## The flow

One workspace, built around the brief:

- **Progress strip:** RFx drafted → sent to 5 vendors → replies read → compare and decide.
- **Left: the comparison.** 30 lines × 5 vendors on one basis (₹ per piece, ex-GST, delivered), coloured
  🟢🟠🔴 by confidence. Questionnaire answers, attached documents (with certificate validity read from the
  document itself) and commercial terms sit directly underneath in the same vendor columns. Click any price
  to see what the vendor wrote, where, the conversion steps, and the flags; accept, correct or exclude it, or
  draft a clarification email to that vendor. A second view, **Award and sign-off**, has the recommended
  split, the stress test, and the export that stays locked until blocked items are resolved.
- **Right: the conversation.** Before sending, it's the RFx co-pilot (talk the RFx into existence). After the
  replies are read, it's the analyst: plain-English questions answered with text, tables, charts and Excel
  exports, each with "show the work".

## The ugly edges in the dataset (all deliberate)

| Vendor | Format | Trap | What the system does |
|---|---|---|---|
| Shree Balaji | Excel, own layout | Prices **include GST**; own item codes, shuffled order; **decimal slip** on L12 | Removes GST (exact), matches lines by dimensions, flags L12 🔴 as 0.1× the peer median |
| Ganesh | PDF letterhead | **4% discount in a footnote** (only above ₹50L); **5-ply offered instead of 7-ply** on L28 | Discount extracted and modelled in the optimizer; L28 🔴 for spec deviation |
| PackWorld | Word, paragraphs | **USD**, FOB port; says it has ISO but the **attached certificate expired** | FX and freight assumptions 🟠; quality gate fails on the evidence, not the claim |
| Sai Krupa | **Angled phone photo** | **Per 100 pcs**; **27 of 30 lines**; no questionnaire returned | ÷100 exact; 3 lines 🔴 not quoted; quality "unknown" |
| Om (incumbent) | One email | "₹42/kg for 5-ply, 38 for 3-ply, **rest same as last year**, freight extra" | Per-kg × RFx nominal weight 🟠; "last year" resolved from the contract file 🟠; flags the risk of double-counting freight |

## Design decisions (the short version, more in [NOTE.md](NOTE.md))

1. **The LLM reads; code calculates.** The extractor copies numbers exactly as written, with their
   unit, currency and tax basis. Every conversion is deterministic and written out as a readable
   chain, e.g. `₹42 per kg → × 0.838 kg/pc → + 3.5% est. freight = ₹36.43/pc`.
2. **Confidence comes from checks, not from the model's opinion of itself.** Unit math, spec
   match, dimension match, peer median, last year's price, and claim vs attached evidence. A
   model saying "92% confident" is theatre.
3. **Red cells cannot be awarded. Amber cells must be acknowledged.** That's the bank-style
   maker-checker: the AI makes, the buyer checks.
4. **The analyst never does arithmetic in its head.** It plans and writes code; the numbers come
   from running that code on the extracted data.
5. **The stress test measures money, not just flips.** It asks: if every amber assumption is 10%
   wrong against us, how much cheaper would a different award have been?

## Repo map

```
app.py                  Streamlit UI (5 tabs)
core/extract.py         file readers + LLM extraction (copy, don't calculate) + cache
core/normalize.py       unit/FX/GST/freight normalization, checks, flags, overrides
core/analysis.py        MILP award optimizer, discounts, single-vendor baseline, stress test
core/analyst.py         NL analyst: OpenAI tool-calling → pandas code → answers
core/copilot.py         RFx co-pilot + clarification-email drafter
core/llm.py             OpenAI wrapper (model via OPENAI_MODEL, default gpt-4.1)
data/generate_dataset.py   builds the RFx, last year's contract and the 5 messy responses
tests/make_fixture.py   offline rule-based fixtures (testing only, flagged in the UI)
```

## What's stubbed

Email in and out (invites and clarifications are written to `cache/outbox/`), auth, a database
(JSON plus cache files), multi-user workflow. The analyst runs model-written code locally: fine
for a single-user prototype, but it needs a sandbox in production.
