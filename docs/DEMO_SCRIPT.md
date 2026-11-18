# Demo script (6–7 minutes)

The brief asks for a recorded walkthrough **of the analyst conversation**, so most of the time goes there.

| # | Time | Where | What to show | What to say |
|---|---|---|---|---|
| 1 | 0:00 | Progress strip → ③ Replies | The five vendor cards | "Five vendors replied five ways: Excel, a letterhead PDF, a Word doc, a phone photo, a one-line email. Normally a buyer retypes this for three days. The real problem isn't typing; it's whether you can trust the numbers enough to split a ₹4 crore award." |
| 2 | 0:40 | ③ Replies | The cards' findings | "The AI read every one, including the photo. It found the GST hidden in Shree Balaji's prices, Ganesh's 4% discount in a footnote, and that PackWorld's ISO certificate expired last year." |
| 3 | 1:10 | ④ Comparison | Grid + banner | "Everything on one basis: rupees per piece, ex-GST, delivered. Green read cleanly, amber depends on an assumption, red is blocked." |
| 4 | 1:30 | Click Shree Balaji L12 | Inspector | "Ten times cheaper than everyone else. The system doesn't guess; it blocks it and shows me the cell it came from." |
| 5 | 1:50 | Click Om L01 | Inspector | "Om quoted per kg. Here's the working, step by step. It's amber because it depends on box weight. One click drafts the clarification to Om." |
| 6 | 2:20 | Questionnaire rows under the grid | | "Questionnaire answers and documents sit right next to the prices. PackWorld *says* ISO; the attached certificate says otherwise." |
| 7 | 2:40 | Chat → suggested Q1 | Answer + table, Show the work | "Now I stop clicking and start asking. This is the VP's question: cheapest per line, only vendors that passed quality. It answers by running code on the extracted data; here's the code." |
| 8 | 3:40 | Chat → suggested Q2 | | "Which numbers am I least sure about, and does it matter? It tells me how much of the decision rests on Om's one-line email." |
| 9 | 4:30 | Chat → Q3 or your own | | A what-if: "cap any vendor at 40%" or "what if I drop Om?" |
| 10 | 5:10 | ④ Award and sign-off | Award table | "The system recommends; I decide. I'll split L05 70/30 for supply security, untick Om to see the cost of replacing him, and every change is priced live. Changes need a reason, and the export stays locked until blocked items and assumptions are dealt with." |
| 11 | 6:10 | — | | "The interesting problem was upstream and in the back-and-forth. A vague RFx creates vague quotes, so the co-pilot insists on unit, currency, GST and freight before sending. And the bottleneck is clarifications, so the system drafts them. Auto re-reading the replies is next." |

## Before recording

- Run the extraction once so the cache is warm; the demo then needs no waiting.
- Zoom Chrome to 90% so all five vendor columns fit.
- Clear old chat: refresh the page.
