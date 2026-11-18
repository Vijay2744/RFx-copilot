# What I decided, and what I left out

When I first read the brief, I thought the problem was retyping. The more I thought about it, the more I felt
retyping is just the symptom. The real problem is that a buyer can't trust the numbers enough to act on them.
So when the stakes are ₹4 crore, they play safe and give the whole order to the usual vendor, and the savings
from a smarter split never happen. That's why I spent most of my time on making the numbers trustworthy,
rather than just on reading the documents.

## The main decisions I made

1. **I let the AI read, but not calculate.** The AI copies every price exactly as the vendor wrote it,
   along with where it found it. Plain code then converts everything to rupees per piece, without GST,
   delivered, and shows the working step by step. A wrong calculation that nobody can see is the one
   mistake a buyer would never forgive, so I kept the maths out of the AI's hands.
2. **The system tells you what to double-check, and why.** Instead of the AI saying "I'm 90% sure", every
   price is green, amber or red based on real checks. Is the line missing? Is it a different spec? Is it
   ten times off every other vendor? Does it depend on a guess, like box weight or an exchange rate?
   A buyer can act on "this vendor quoted per kg, so this price depends on box weight". They can't act on
   a percentage.
3. **The AI prepares, the buyer approves.** This comes from my time in banking, where nobody acts on a
   high-value number until someone has checked it. Every price links back to the vendor's own words, and
   the buyer can accept, correct or exclude it.
4. **I trusted documents over claims.** One vendor said they were ISO certified. Their attached
   certificate had expired in 2025, so they fail the quality check, even though they're the cheapest.
5. **The analyst answers by running code.** When you ask a question, it writes and runs code on the actual
   data, and you can see that code. Every number in the answer can be checked.
6. **The system suggests, the buyer decides.** It recommends the cheapest split and even catches a 4%
   discount that only applies above ₹50 lakh. But buyers know things the data doesn't, so they can split
   an order between two vendors, drop a vendor, or keep one they trust. Every change shows its cost and
   asks for a short reason, so the final decision is easy to defend.

## Where I think the bigger problem is

Reading quotes is becoming easy. Most AI models can read a PDF now. What I found more interesting is what
happens around it. A lot of the mess starts with the RFx itself. If it doesn't fix the unit, GST and freight
basis, every reply comes back different. So the co-pilot keeps asking until those are clear. And most of
the nine days actually goes on chasing vendors for clarifications. The prototype already drafts those emails.
Sending them and reading the replies automatically is what I'd build next.

## What I deliberately left out

- Real email, login and a database. The brief allowed faking the plumbing, so I spent the time on trust.
- Drawing boxes on the photo. Showing the photo next to the number was enough to check it.
- A second, independent AI read to cross-check prices. It's the next thing I'd add.
- Turning payment terms and lead times into rupees. They're shown side by side, but not priced yet.

## How I'd know it's working

The main thing I'd track is how long it takes to go from the last vendor reply to a decision the buyer is
comfortable signing. Alongside that: how often buyers have to correct a number, how much of the final
award still rests on guesses, and how much we save against last year.
