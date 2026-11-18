# How this prototype answers the brief, point by point

The brief is "Kill the Quote Spreadsheet" (Aerchain product take-home). Each section below quotes the ask
in short and says what was built, where to see it in the app, and where it lives in the code.

---

## 1. "A buyer talks an RFx into existence with an AI co-pilot: scope, line items, questionnaire, terms"

**Built.** Step ① of the progress strip. The right-hand panel is the co-pilot chat; the left shows the draft
RFx as it takes shape (lines, terms, questionnaire, open issues).

**The design choice:** the co-pilot doesn't only write the RFx; it refuses to mark it *ready to send* until
the five things that cause messy replies are explicit: price unit, currency, GST basis, freight basis, and a
quality gate with documents to attach. Each one left vague becomes a flag on every vendor reply nine days
later, so the cheapest place to fix it is upstream.

- App: progress strip → **① RFx drafted** (or "Load the demo RFx")
- Code: `core/copilot.py` (`COPILOT_SYSTEM`, `copilot_turn`)

## 2. "It goes out to vendors over a channel you choose"

**Stubbed, as the brief allows ("fake the SMTP server if you like").** "Send to 5 vendors" writes one
invitation email per vendor to `cache/outbox/`. Each invitation tells the vendor to reply in any format.

- Code: `core/copilot.py` (`vendor_invite_email`), `app.py` (send button)

## 3. "Vendors reply however they like; nobody is forced into your template"

**Built, with five deliberately different formats** (see [DATASET.md](DATASET.md)):

| Vendor | What they sent |
|---|---|
| Shree Balaji | Excel in their own layout and item codes, shuffled order |
| Ganesh | PDF on letterhead, discount in a footnote, questionnaire in an annexure, ISO certificate attached |
| PackWorld | Word document with commercials written in paragraphs, prices in USD, certificate attached |
| Sai Krupa | An angled phone photo of a printed rate card, forwarded from WhatsApp |
| Om (incumbent) | A one-line email: "₹42/kg for the 5-ply, 38 for the 3-ply, rest same as last year, freight extra" |

- App: progress strip → **③ Replies read** (vendor cards with "See what they sent")

## 4. "Your system reads every response, whatever shape it arrives in"

**Built, with real AI (OpenAI, vision for the photo). Nothing is hardcoded.** Each vendor's files are turned
into text with cell, page, paragraph or line references (Excel cells, PDF pages, Word paragraphs, email lines),
the photo is sent as an image, and the model returns structured JSON.

**The key rule: the AI reads, code calculates.** The model copies each price exactly as written, with its
unit, currency, GST and freight basis, plus the verbatim snippet and where it found it. It never converts
anything. See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md#1-the-ai-reads-code-calculates).

- Code: `core/extract.py` (`RULES`, `SCHEMA`, `extract_vendor`)

## 5. "…and lands them all in a single side-by-side comparison: same lines, same units, same currency"

**Built.** Step ④ → **Comparison**. 30 lines × 5 vendors, every price as **₹ per piece, ex-GST, delivered to
the plant**, coloured by confidence (🟢 read cleanly, 🟠 depends on an assumption, 🔴 blocked). The cheapest
usable price on each line has a bold border, and last year's price sits alongside for reference.

Every conversion is deterministic and shown as readable working, e.g.
`Read: INR 42 per kg → × 0.838 kg/pc (RFx nominal board weight) → + 3.5% est. freight → = ₹36.43/pc`.

- Code: `core/normalize.py`

## 6. "…with questionnaire answers and attached docs sitting alongside the numbers"

**Built.** Directly under the price grid, in the **same vendor columns**: lines quoted, quality check
result, attached documents, ISO certificate validity *as read from the attached document*, each
questionnaire answer, payment terms, lead time, freight, and discounts.

Attachments are evidence, not decoration: PackWorld *claims* ISO 9001, but its attached certificate
expired in August 2025, so it fails the quality check on the evidence.

## 7. "Then the buyer stops clicking and starts asking. Natural language, over the whole comparison"

**Built.** From step ③ onwards, the right-hand panel is the analyst, always visible next to the comparison.
It answers by **writing and running pandas code** on the extracted tables (plus the buyer's current award),
not from memory. Suggested questions are there for the demo; any question works.

- Code: `core/analyst.py` (OpenAI tool-calling with a `run_python` tool), `core/analysis.py` (deterministic
  helpers the analyst calls: MILP award optimizer, single-vendor baseline, stress test)

## 8. "Text answers, tables, charts, exports"

**Built.** Each answer can carry a written conclusion, tables, Plotly charts, an **Export to Excel** button
(all tables in that answer), and **Show the work** (the exact code that ran and its raw output).

## 9. "Real analysis on real extracted data, all the way to a defensible award decision"

**Built.** Step ④ → **Award and sign-off**:

1. **The system recommends** an award: quality-passed vendors, cheapest per line, *with conditional
   discounts modelled* (Ganesh's 4% above ₹50 L is captured, not missed).
2. **The buyer decides.** Change the vendor on any line, **split a line between two vendors by %**
   (partial / dual-source orders), **untick a vendor to replace them** (their lines move to the next best
   and nothing else changes), lock lines and re-optimize the rest. Every change is priced live against the
   recommendation and last year.
3. **Defensible by construction:** any deviation from the recommendation needs a one-line reason; blocked
   prices can't be awarded; assumption-based prices must be acknowledged; the export is locked until all
   three hold. The export contains purchase-order lines per vendor, the award by line, every buyer
   decision, and the full audit trail (source snippet and location for every number).

- Code: `core/award.py`, `app.py` (`render_award`)

## 10. "Build the demo at a size where it actually hurts: five vendors, thirty line items, a questionnaire, attached documents"

**Built.** 5 vendors × 30 lines (≈ ₹4 crore annual value), a 5-question questionnaire with a pass rule, and
three attached ISO certificates (one expired). Generated by `data/generate_dataset.py`; see
[DATASET.md](DATASET.md).

## 11. "Stub the plumbing, but the AI loops must be real"

- **Real:** extraction (including vision on the photo), the RFx co-pilot, the analyst, clarification-email
  drafting.
- **Stubbed:** email send and receive, authentication, the database (JSON files in `cache/`).
- **Not hardcoded:** no demo answer exists anywhere in the code. `tests/make_fixture.py` builds rule-based
  test data *only* for offline testing, and the app shows a warning banner whenever it's loaded.

---

## What they said they're actually looking at

### "The ugly edges… what does your system do, and what does it show the buyer, when it isn't sure?"

| Edge | What the system does | What the buyer sees |
|---|---|---|
| The angled photo | Read with vision; rows the model can't read confidently are marked `read_confidence: low` | 🟠 "reading is uncertain", with the photo shown next to the number |
| Vendor quoted 27 of 30 | Missing lines become explicit "not quoted" cells | 🔴 "Sai Krupa didn't quote 3 lines: L28, L29, L30" + one-click clarification email |
| Quoted in USD | Converted at a buyer-set rate (sidebar) | 🟠 "prices move with the exchange rate"; the rate is in the working |
| "Per box" vs "per 100 pieces" | Unit captured as written, divided exactly in code | 🟢 when exact (÷100); working shows the step |
| "₹42/kg" | × the RFx nominal box weight | 🟠 "27 prices depend on the assumed box weight" |
| "Rest same as last year" | Resolved from last year's contract file | 🟠 "filled from last year's contract" + 🟠 freight double-count risk |
| GST included in price | ÷ 1.18 in code | 🟢 with the step shown |
| Discount in a footnote | Extracted with its condition (> ₹50 L) and modelled in the optimizer | Shown on the vendor card and in the award |
| 5-ply offered for a 7-ply line | Spec deviation detected | 🔴 cannot be awarded until the buyer accepts or excludes it |
| Decimal typo (a price 10× too low) | Compared with peer median | 🔴 "0.10× the peer median: likely a typo or unit error" |
| ISO claimed but certificate expired | Claim checked against the attached document | Vendor fails the quality check, with the reason |

### "Trust. Would a buyer with ₹4 crore on the line act on what's on your screen?"

What was built to earn that:
1. **Provenance on every number:** the vendor's own words, where they were found, and the conversion steps.
2. **Confidence from checks, not from the model's opinion:** unit math, spec match, size match, peer median,
   last year's price, claim vs attached evidence.
3. **Maker-checker:** the AI is the maker and the buyer is the checker. Accept, correct or exclude any
   number; every decision is logged and exported.
4. **Hard gates:** blocked prices can't be awarded, and the export stays locked until reasons and
   acknowledgements are in.
5. **The analyst shows its work:** every answer comes from code you can open and read.
6. **Money-based stress test:** "if every assumption is 10% off against you, this award costs ₹X more".

### "Judgment and taste"

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md): every major choice, the alternative considered, and why.

### "If you think the interesting problem was actually somewhere else, tell us"

Yes, two places. In short: (1) **upstream**: most normalization pain is created by a vague RFx, so the
co-pilot insists on unit, currency, GST, freight and a quality gate before sending; (2) **downstream**: the
bottleneck in the nine days is the back-and-forth with vendors, not the reading. The system drafts the
clarification email for every flagged item; auto-send and auto-re-read of the reply is the next build. Full
argument in [NOTE.md](../NOTE.md).
