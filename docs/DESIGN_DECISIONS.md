# Design decisions: what I chose, what I didn't, and why

The brief says a thousand decisions here have no correct answer. These are the ones that shaped the product.

---

## 1. The AI reads, code calculates

**Decision.** The extraction model copies every price *exactly as written* and labels it: value, unit
(per piece, per 100, per kg…), currency, GST basis, freight basis, plus the verbatim snippet and where it was
found. All conversion to ₹ per piece, ex-GST, delivered is done in deterministic Python.

**Alternative rejected.** Prompting the model to "return everything in INR per piece ex-GST". It's simpler,
but the model then does arithmetic silently. If it gets ₹42/kg × 0.838 kg wrong, nobody can see the mistake
and the buyer has nothing to check against.

**Why.** Wrong arithmetic is the one error a buyer never forgives. With this split, every rupee on screen has
a reproducible chain of steps, and the AI's job is narrow enough to verify.

## 2. Confidence comes from checks, not from the model's opinion

**Decision.** Every price is 🟢, 🟠 or 🔴 based on explicit checks:
- 🔴 **blocked:** not quoted, spec deviation, size mismatch, unknown unit or currency, more than 2.5× off the
  peer median, a vendor claim contradicted by its own attachment.
- 🟠 **assumption:** per-kg conversion, FX rate, freight estimate, price inferred from "same as last year",
  low-confidence reading of the photo, far from peers.
- 🟢 everything else.

**Alternative rejected.** Showing the model's self-reported confidence ("92%"). It's not calibrated, it can't
be explained, and a buyer can't act on it.

**Why.** A buyer can act on "this vendor quoted per kg, so this price depends on box weight". They can't act
on "87% confident".

## 3. Maker-checker, borrowed from banking

**Decision.** The AI is the maker, the buyer is the checker. Any price can be accepted, corrected or excluded;
every decision is timestamped and goes into the export. Blocked prices can't be awarded at all.

**Why.** It's how banks have handled high-value data entry for decades, and it's the honest answer to "would
a buyer act on this with ₹4 crore on the line?": they act on it because they checked the parts that needed
checking, and the system told them which parts those were.

## 4. Group the doubts by cause, not by cell

**Decision.** Dozens of amber cells become about a dozen plain-English items, e.g. "Om quoted per kg, so 27 prices depend
on the assumed box weight", sorted with blocked items first.

**Why.** 60 yellow cells is noise. The buyer has about six real questions, and each one is one email to one
vendor. The list says exactly that.

## 5. The comparison is the centre, the conversation is always beside it

**Decision.** One workspace: the comparison on the left, the conversation docked on the right, and a
progress strip on top (drafted → sent → read → compare). Earlier versions used tabs and a dashboard; they hid
the comparison and made the analyst a separate destination.

**Why.** The brief's central moment is "the buyer stops clicking and starts asking", which only works if the
thing you're asking about is in front of you while you ask.

## 6. Evidence beats claims

**Decision.** The quality gate reads attached certificates and checks their validity dates against the RFx
due date, rather than trusting the questionnaire answer.

**Why.** PackWorld says it's ISO certified. Its certificate expired in 2025. A system that takes the claim at
face value would recommend the cheapest vendor, who shouldn't be in the award at all.

## 7. The optimizer models real procurement terms

**Decision.** The award is a small mixed-integer program (scipy `milp`): one vendor per line, optional quality
filter, vendor share cap, vendor count cap, and **conditional volume discounts** (e.g. 4% if the PO exceeds
₹50 L).

**Why.** In the demo data, a naive "cheapest per line" split leaves Ganesh short of its ₹50 L threshold, misses
the footnote discount, and costs more than the discount-aware award. Real procurement maths has thresholds; the tool should too.

## 8. The optimizer proposes, the buyer decides

**Decision.** The recommendation is a starting point, not a verdict. In the Award view the buyer can change
the vendor on any line, **split a line between two vendors by %**, **untick a vendor to replace them** (only
their lines move), lock lines and re-optimize the rest. Every change is priced live against the
recommendation, and needs a one-line reason before export.

**Why.** Buyers always have reasons the data doesn't hold: capacity, relationships, dual-sourcing for supply
security, negotiation leverage. A tool that only offers "accept the machine's answer" doesn't get used. The
reason field keeps the decision defensible without blocking judgment.

## 9. The stress test measures money

**Decision.** "If every amber assumption is 10% wrong against us, how much would today's award cost, and
how much cheaper would a different award have been?" The Award view shows the value resting on assumptions
and the 10% exposure.

**Alternative rejected.** Counting how many lines would switch vendor. Near-ties flip constantly and mean
nothing; what matters is the money at risk.

## 10. The analyst writes code, not numbers

**Decision.** The analyst is an OpenAI tool-calling loop with one tool, `run_python`, over the real tables and
the deterministic helpers. Its answers may only quote numbers that came out of that code, and "Show the
work" exposes every line.

**Why.** The VP's question ("split cheapest per line, only vendors who cleared quality") is a constrained
optimization. Language models are bad at that in their heads and good at writing the code for it.

## 11. Upstream beats downstream

**Decision.** The RFx co-pilot won't mark the RFx ready until unit, currency, GST, freight and the quality gate
are explicit.

**Why.** Every vague term in the RFx turns into an amber flag on every reply. Fixing the request is cheaper
than cleaning five responses.

## 12. Plain tools, deliberately

**Decision.** Python, Streamlit, pandas, scipy, OpenAI. No agent framework, no vector store, no database.

**Why.** Each AI loop here is one well-specified call (extraction) or one tool-using loop (analyst). A
framework would add layers between the interviewer and the logic. Streamlit keeps the whole prototype in one
readable language; in production, the UI would be a proper front end over the same core modules.

---

## Deliberately left out

| Left out | Why | What it would take |
|---|---|---|
| Real email send and receive | Plumbing; the brief allows stubbing | SMTP/IMAP or Graph API; re-read replies on arrival |
| Auto re-read of clarification replies | Time | Thread the reply to the vendor bundle, re-run extraction, diff the changes |
| Bounding boxes on the photo | Time; the row reference plus the photo is enough to check | Ask the vision model for coordinates; draw them |
| A second independent extraction pass | Cost and time | Two reads that agree → green; disagreement → amber |
| Live FX and freight feeds | They're buyer-owned assumptions on purpose | FX API; lane-based freight table |
| Total cost of ownership (payment-terms cost of money, lead-time risk) | Terms are shown side by side but not priced | Discount future cash flows; penalise lead time |
| Negotiation automation | Out of scope | The analyst can already draft a negotiation note |
| Multi-user roles, approvals, auth | Plumbing | Standard |
