# One-page note: what I decided, and what I left out

**Problem, restated.** Retyping quotes is the symptom. The real problem is that buyers can't
**trust or compare** messy quotes fast enough to make a **defensible** award. So they fall back
to "give it all to the usual vendor", and the savings from a smarter split never happen. On a
₹4 Cr event, a quality-gated split on correctly normalized data is worth roughly 2–5%
(₹8–20 L), an order of magnitude more than the 3 days of retyping. **Savings depend on trust**,
so I built the trust layer.

## Decisions

1. **The LLM reads; deterministic code calculates.** The model copies each price exactly as
   written, with its unit, currency, tax and freight basis. Every conversion (÷100, × kg/pc,
   × FX, ÷1.18, + freight) runs in code and is shown as a chain the buyer can read. Wrong
   arithmetic is the one mistake a buyer never forgives.
2. **Three states, defined by checks.** 🔴 means it can't be awarded: not quoted, spec deviation,
   dimension mismatch, more than 2.5× off the peer median, or a claim that contradicts the
   evidence. 🟠 means usable but resting on an assumption: per-kg conversion, FX, a freight
   estimate, a price inferred from last year, a low-confidence reading of the photo. 🟢 means
   everything else. I did **not** use model self-reported confidence scores.
3. **Maker-checker, borrowed from banking.** The AI is the maker, the buyer is the checker.
   Every cell links to its source. The buyer can accept, edit or exclude, and every action is
   logged. The award export stays locked while any 🔴 line is awarded or 🟠 assumptions haven't
   been acknowledged.
4. **Evidence beats claims.** PackWorld says "ISO certified". The attached certificate expired
   in 2025. The quality gate reads the attachment, not the claim.
5. **The analyst writes code, not numbers.** The VP's question ("split cheapest per line, only
   vendors who cleared quality") is a constrained optimization. It runs as a MILP, not in the
   model's head, and every answer has a "show the work" panel.
6. **Real procurement nuance in the optimizer.** It models Ganesh's footnote discount
   (4% above ₹50 L). A naive cheapest-per-line split misses it. The discount-aware award captures
   it.
7. **A stress test in money, not vibes.** "If every amber assumption is 10% wrong against us,
   how much cheaper would another award have been?" In the demo, the answer is that the award
   hinges on the incumbent's one-line per-kg email. **The action is to clarify with Om, then
   award**, and the tool drafts that email.

## The better problem (what I'd tell you I found)

Extraction is becoming a commodity; every frontier model can read a PDF. Two things aren't:

- **Upstream: a vague RFx produces vague quotes.** Most normalization pain is created when the
  RFx goes out without one fixed unit, freight term and GST basis. The co-pilot blocks "ready to
  send" until these are explicit. Fixing the RFx removes flags before they exist.
- **Downstream: the bottleneck is the back-and-forth with vendors, not the reading.** Of the
  9-day cycle, most is spent waiting on clarifications. Flagging is passive; the system should
  close the loop by drafting the question, sending it and re-ingesting the reply. I built the
  draft step. Auto-send and re-ingest are next.

## Deliberately left out

- Real SMTP/IMAP (stubbed to `cache/outbox/`), auth, a database, multi-user roles.
- Bounding-box highlighting on the photo. I show the photo and the row reference instead.
- A second independent extraction pass for cross-checking. It's the next trust upgrade (two
  reads agree means green), and I cut it for time and cost.
- Live FX and freight feeds. These are buyer-owned settings, deliberately visible.
- Negotiation automation. The analyst can draft a negotiation note; it doesn't negotiate.
- Total cost of ownership (payment-term cost of money, lead-time risk). I show the terms side by
  side but don't price them yet.

## How I'd measure it

North star: **time from the last vendor reply to a defensible award**. Guardrails: the share of
quote lines that land without retyping and stay unedited (extraction precision), the buyer
override rate, the share of awarded value resting on 🟠 assumptions at sign-off, and savings
versus best single vendor and versus last year.
