"""
Analyst agent: natural-language questions over the normalized comparison.

The model plans the analysis and writes pandas code; the code runs against the REAL
extracted + normalized tables; the model answers ONLY from what the code printed.
No numbers come from the model's head.
"""
import contextlib
import io
import json
import traceback

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from . import llm
from . import analysis as A

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Execute Python against the procurement dataframes. Set `result` to a DataFrame / number / "
            "string to return it, set `fig` to a plotly figure to show a chart, and print() anything you "
            "need to see. State persists between calls."),
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
    },
}]

SYSTEM = """You are a senior procurement analyst inside an RFx comparison tool. The buyer is about to
award ~Rs 4 crore of corrugated packaging. Be precise, brief, and decision-oriented.

DATA (pandas DataFrames already loaded):
- rfx_lines: line_id, description, item_type, ply, dimensions_mm, weight_kg_per_pc, annual_qty_pcs
- quotes: one row per (line_id, vendor). Key columns: vendor, line_id, description, qty,
  raw_value, raw_currency, raw_basis, raw_gst (as the vendor wrote it);
  unit_inr_ex_gst (normalised Rs/pc ex-GST, before freight); freight_inr; landed_inr (Rs/pc landed
  at Hosur, ex-GST = THE comparison basis); status in {green, amber, red, verified};
  flag_codes, flag_text (why), chain (conversion steps), source_ref, source_snippet, is_inferred,
  vs_last_year_pct. NaN landed_inr = not quoted / unreadable.
- vendors: vendor, quality (pass/fail/unknown), iso_certificate_valid_until, payment_terms,
  payment_days, lead_time_days, validity, freight_terms, discount_pct, discount_min_order_inr,
  discount_text, lines_quoted, lines_green, lines_amber, lines_red, flag_text
- questionnaire: vendor, q_id, answer, value, source_snippet
- last_year: vendor, line_id, price_inr_per_pc (last year's contract, incumbent Om Packaging)
- my_award: the BUYER'S CURRENT award (may differ from the optimizer): line_id, description, qty, unit_price
  (blended if split), value, best_price, extra_vs_best, status, changed (vs recommendation), options
- my_po: purchase-order lines of the buyer's award: vendor, line_id, qty (allocated, splits included),
  unit_price, value, status
- my_award_total: net total of the buyer's award after conditional discounts (may be None)
When the buyer says "my award" / "current award" / "what I have", use my_award / my_po. For what-ifs
("drop Om", "split L05 70/30") compute from quotes (and optimize_award with eligible_vendors) and compare to
my_award_total.

HELPERS (deterministic, use them instead of re-implementing):
- optimize_award(quotes, vendors, eligible_vendors=None, quality_only=False, max_vendor_share=None,
  max_vendors=None, lines=None) -> dict(award, unawarded, total, by_vendor, status). Red cells are never
  awarded. by_vendor shows conditional discounts earned or MISSED.
- best_single_vendor(quotes, vendors, quality_only=False) -> DataFrame
- sensitivity(quotes, vendors, pct=0.10, **optimize_kwargs) -> dict(verdict, lines_changed, ...)
- last_year_cost(rfx_lines, last_year) -> float
- evaluate_award(award_df, vendors)
- fmt_inr(x) -> "Rs 3.84 Cr" style string
Libraries: pd, np, px (plotly.express), go.

RULES:
1. Every number in your answer must come from a run_python output. Never do arithmetic in your head.
2. Compare on landed_inr unless asked otherwise. Say so when it matters.
3. Trust: if an answer depends on amber/red data, say how much (e.g. "3 of the 30 awarded lines rest
   on assumptions") and name the biggest one. Never hide a red flag that affects the answer.
4. Money in Indian format: lakh (L) / crore (Cr). Use fmt_inr.
5. Tables: set `result` to a tidy DataFrame (<= 30 rows, human column names). Charts: set `fig`.
6. If the question is ambiguous, pick the most sensible reading, state it in one line, and answer.
7. Finish with a 2-5 sentence answer: the conclusion first, then the caveat that matters most.
"""


class Analyst:
    def __init__(self, tables: dict):
        self.ns = {"pd": pd, "np": np, "px": px, "go": go, **tables,
                   "optimize_award": A.optimize_award, "best_single_vendor": A.best_single_vendor,
                   "sensitivity": A.sensitivity, "evaluate_award": A.evaluate_award,
                   "last_year_cost": A.last_year_cost, "fmt_inr": A.fmt_inr}
        names = sorted(tables["quotes"].vendor.unique()) if "quotes" in tables and len(tables["quotes"]) else []
        ctx = ("\nEXACT VENDOR NAMES (use these strings exactly, or match case-insensitively with "
               "str.contains on a distinctive word): " + "; ".join(names) +
               "\nLINE IDS: L01..L30. Comparison column: landed_inr. Usable prices: status in "
               "['green','amber','verified'].")
        self.messages = [{"role": "system", "content": SYSTEM + ctx}]

    def _run(self, code: str):
        self.ns.pop("result", None)
        self.ns.pop("fig", None)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(code, self.ns)  # local prototype only: trusted single-user sandbox
            err = None
        except Exception:
            err = traceback.format_exc(limit=3)
        result, fig = self.ns.get("result"), self.ns.get("fig")
        out = buf.getvalue()[-4000:]
        if err:
            out += "\nERROR:\n" + err
        if isinstance(result, pd.DataFrame):
            if result.empty:
                out += ("\nresult is an EMPTY DataFrame. Your filter matched nothing: check vendor names "
                        "(exact strings above), column names, and status values before trying again.")
            else:
                out += "\nresult (DataFrame):\n" + result.head(40).to_string(max_colwidth=60)
        elif isinstance(result, dict):
            out += "\nresult (dict keys): " + ", ".join(result.keys())
        elif result is not None:
            out += f"\nresult: {result}"
        if fig is not None:
            out += "\n(figure created and shown to user)"
        return out[-6000:], result, fig, err

    @staticmethod
    def sanitize(messages):
        """Drop half-finished tool exchanges (e.g. a question interrupted by a page rerun)."""
        out, i = [], 0
        while i < len(messages):
            m = messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids = {tc["id"] for tc in m["tool_calls"]}
                j, replies = i + 1, []
                while j < len(messages) and messages[j].get("role") == "tool":
                    replies.append(messages[j]); j += 1
                if ids == {r["tool_call_id"] for r in replies}:
                    out += [m] + replies
                i = j
                continue
            if m.get("role") != "tool":
                out.append(m)
            i += 1
        return out

    def ask(self, question: str, max_steps: int = 12):
        """Returns dict(answer, steps=[{code, output, result, fig, error}]).
        Works on a copy of the conversation and only commits it when the answer is complete,
        so an interrupted question can never leave a broken history behind."""
        committed = self.sanitize(self.messages)
        self.messages = committed + [{"role": "user", "content": question}]
        try:
            return self._ask(max_steps)
        except Exception:
            self.messages = committed
            raise

    def _ask(self, max_steps):
        steps = []
        for _ in range(max_steps):
            resp = llm.chat_tools(self.messages, TOOLS)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                self.messages.append({"role": "assistant", "content": msg.content})
                return dict(answer=msg.content, steps=steps)
            self.messages.append({"role": "assistant", "content": msg.content or "",
                                  "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                try:
                    code = json.loads(tc.function.arguments).get("code", "")
                except json.JSONDecodeError:
                    code = ""
                out, result, fig, err = self._run(code)
                steps.append(dict(code=code, output=out, result=result, fig=fig, error=err))
                self.messages.append({"role": "tool", "tool_call_id": tc.id, "content": out})
        # out of steps: answer from what was computed, never from memory
        self.messages.append({"role": "user", "content": "Stop running code. Answer the question now using ONLY "
                              "numbers that appeared in the tool outputs above. If something couldn't be "
                              "computed, say what and why in one line."})
        final = llm.chat_text(self.messages)
        self.messages.append({"role": "assistant", "content": final})
        steps = [s_ for s_ in steps if not (isinstance(s_["result"], pd.DataFrame) and s_["result"].empty)]
        return dict(answer=final, steps=steps)
