# One-page note: what I decided, and what I left out

**The problem, restated.** Retyping quotes is the symptom. The real problem is that a buyer can't trust or
compare messy quotes fast enough to make a defensible award, so they fall back to "give it all to the usual
vendor" and the savings of a smarter split never happen. **Savings depend on trust**, so I built the trust
layer, and kept the final decision with the buyer.

## What I decided

1. **The AI reads; code calculates.** The model copies every price exactly as written (unit, currency, GST,
   freight) with where it found it. All conversion to ₹ per piece, ex-GST, delivered is plain code, shown as
   step-by-step working. Silent arithmetic errors are the one mistake a buyer never forgives.
2. **Confidence comes from checks, not the model's opinion.** Red = blocked (not quoted, wrong spec, wrong
   size, 2.5× off other vendors, a claim contradicted by its own attachment). Amber = rests on a stated
   assumption (per-kg weight, exchange rate, freight estimate, "same as last year", an unclear photo row).
   Green = everything else. Dozens of ambers are grouped into about a dozen plain-English issues.
3. **Maker-checker, from banking.** Every price links to its source; the buyer accepts, corrects or
   excludes it, and every decision is logged.
4. **Evidence beats claims.** One vendor says it's ISO certified; its attached certificate expired in 2025,
   so it fails the quality check even though it's the cheapest.
5. **The analyst writes code, not numbers.** Questions in plain English are answered by code run on the
   extracted data, with "show the work" on every answer.
6. **The optimizer proposes; the buyer decides.** The recommendation models real terms (a 4% discount only
   above ₹50 L). The buyer can split a line between vendors, replace a vendor, or lock lines, with every
   change priced live and a reason recorded. Export stays locked until blocked items and assumptions are
   dealt with.

## The better problem

Reading quotes is becoming a commodity. The harder problems sit around it. **Upstream:** a vague RFx creates
vague quotes, so the co-pilot won't mark an RFx ready until unit, currency, GST, freight and the quality check
are explicit. **Downstream:** most of the nine days goes on chasing vendors for clarifications. The prototype
drafts every clarification; sending them and re-reading the replies automatically is the next build.

## Deliberately left out

- Real email in and out, login, a database (the brief allowed stubbing the plumbing).
- Boxes drawn on the photo: the photo plus the row reference is enough to check a number.
- A second, independent AI read to cross-check prices: the next trust upgrade, cut for time.
- Pricing payment terms and lead time into total cost: shown side by side, not yet converted to rupees.

## How I'd measure it

North star: **time from the last vendor reply to a defensible award.** Also: share of prices that need no
correction, buyer override rate, share of award value resting on assumptions at sign-off, and savings versus
the best single vendor and last year.
