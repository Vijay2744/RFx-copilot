# Architecture

```
                 ┌──────────────────────────── app.py (Streamlit) ────────────────────────────┐
                 │  progress strip:  ① drafted → ② sent → ③ replies read → ④ compare & decide │
                 │  LEFT: workspace (draft / replies / comparison / award)   RIGHT: conversation│
                 └──────────────┬───────────────────────────────┬──────────────────────────────┘
                                │                               │
   ① co-pilot ── core/copilot.py (LLM, JSON)                    │ ④ analyst ── core/analyst.py
                                │                               │      (LLM tool-calling → run_python)
   ③ read ───── core/extract.py │                               │               │
      files → text with refs    │                               │               ▼
      + photo (vision) → LLM ──►│ cache/extractions/*.json      │      core/analysis.py (deterministic)
                                ▼                               │      optimize_award (MILP), sensitivity,
                       core/normalize.py (deterministic)        │      best_single_vendor, evaluate_award
                       units · FX · GST · freight · checks ─────┤               ▲
                       → quotes / vendors / questionnaire       │               │
                                │                               │      core/award.py (deterministic)
                                └──────────► comparison grid ◄──┘      buyer's plan: per-line vendor,
                                             + inspector                splits, replacement, locks,
                                             + award view ◄────────────  reasons, PO lines, discounts
```

## Modules

| File | Role | AI? |
|---|---|---|
| `core/llm.py` | OpenAI wrapper. Model from `OPENAI_MODEL` (default `gpt-4.1`). Retries without `temperature` for models that reject it. | — |
| `core/extract.py` | Turns each vendor's files into referenced text (Excel `Sheet!G14`, PDF `page 1`, Word `para 6`, email `line 8`), attaches photos as images, and asks the model for one JSON per vendor. Caches to `cache/extractions/`. | **Yes**: reading only |
| `core/normalize.py` | Converts every quote to ₹/pc ex-GST delivered, writes the conversion chain, runs all checks, assigns 🟢🟠🔴, applies buyer overrides. Also vendor-level facts: quality gate, certificate validity, terms, discounts. | No |
| `core/analysis.py` | MILP award optimizer (one vendor per line; quality filter, share cap, vendor cap, conditional discounts), single-vendor baseline, last-year cost, money-based stress test. | No |
| `core/award.py` | The buyer's award plan: per-line vendor, split %, second vendor, lock, reason. Prices it live, builds PO lines, re-evaluates discounts on the actual split, finds missing reasons. | No |
| `core/analyst.py` | Natural-language analyst: OpenAI tool-calling with a single `run_python` tool over the tables and helpers. Returns text + tables + charts + the code it ran. | **Yes**: planning and code-writing |
| `core/copilot.py` | RFx co-pilot (drafts and polices the RFx), invitation emails, clarification-email drafting. | **Yes** |
| `app.py` | The UI. | — |

## Data model

**`quotes`**: one row per (RFx line, vendor), 150 rows:
`raw_value, raw_currency, raw_basis, raw_gst` (as written) → `unit_inr_ex_gst`, `freight_inr`, `landed_inr`
(comparison basis) · `status` (green/amber/red/verified) · `flag_list` / `flag_text` / `flag_codes` · `chain`
(human-readable working) · `source_ref`, `source_snippet` (provenance) · `is_inferred`, `vs_last_year_pct`,
`override`.

**`vendors`**: quality (pass/fail/unknown), ISO validity read from the attachment, payment terms, lead time,
freight terms, discount % and threshold, coverage counts, vendor-level flags.

**`questionnaire`**: vendor × question: answer, value, source.

**Buyer plan** (`core/award.py`): line → `vendor_1`, `pct_1`, `vendor_2`, `locked`, `reason`.

## Flag catalogue

| Code | Level | Meaning |
|---|---|---|
| `NOT_QUOTED` | 🔴 | Vendor didn't price this line |
| `NO_PRICE` | 🔴 | Line mentioned but no number readable |
| `SPEC_DEVIATION` | 🔴 | Different ply or spec offered |
| `DIMENSION_MISMATCH` | 🔴 | Size in the vendor's text ≠ RFx size |
| `OUTLIER` | 🔴 | > 2.5× or < 0.4× the peer median: typo, unit or misread |
| `UNKNOWN_UNIT` / `UNKNOWN_CURRENCY` | 🔴 | Can't convert safely |
| `RULE_UNRESOLVED` | 🔴 | "Same as last year" with no contract record |
| `DUPLICATE` | 🔴 | Line quoted twice |
| `CLAIM_CONTRADICTS_EVIDENCE` | 🔴 vendor | Certificate expired despite claim |
| `NO_QUESTIONNAIRE` | 🔴 vendor | Questionnaire not returned |
| `PER_KG_CONVERSION` | 🟠 | Per-kg price × nominal weight |
| `FX` | 🟠 | Converted at the buyer-set rate |
| `FREIGHT_ESTIMATE` | 🟠 | Freight extra / FOB, estimated |
| `INFERRED_LAST_YEAR` | 🟠 | Filled from last year's contract |
| `FREIGHT_DOUBLE_COUNT_RISK` | 🟠 | Last year delivered + "freight extra" now |
| `GST_UNKNOWN` | 🟠 | GST basis not stated |
| `READ_UNCERTAIN` / `MATCH_UNCERTAIN` | 🟠 | Model unsure of reading or line match |
| `FAR_FROM_PEERS` | 🟠 | > 30% from peer median |

## Why the analyst is safe enough for a prototype, and what production needs

The analyst executes model-written Python in-process. That's acceptable for a local, single-user demo; in
production it would run in a sandbox (separate process, no network, CPU and time limits) with read-only data.
