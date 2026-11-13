"""The agent loop: propose -> guard -> execute -> (re-propose | answer).

Business question it answers: "given a natural-language question about the
certified CFPB warehouse, return an answer whose every number is traceable to
rows a read-only SQL query actually returned -- or refuse."

Invariants enforced here:
* the DuckDB connection is opened read_only via proofquery.config.connect;
* every statement passes sql_guard BEFORE execution (a rejection becomes a
  finding fed back to the provider and still counts against the step budget);
* an answer is accepted only if every number in it appears in the rendered
  rows of a query that actually ran, else the run refuses;
* step and cost budgets bound the run; every step is committed to the
  hash-chained TraceStore, so the whole trajectory is auditable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .. import config
from ..tracing import TraceStore
from . import AgentResult
from .guard import sql_guard
from .provider import TOOLS, DoubleProvider, OpenAIProvider, ProviderUnavailable

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# Rough $/propose estimates, used only for the max_cost_usd budget.
_STEP_COST = {"double": 0.0, "openai": 0.01}


def _traces_path() -> Path:
    env = os.environ.get("PROOFQUERY_TRACES")
    return Path(env) if env else config.REPO_ROOT / "data" / "traces.sqlite3"


def _render_rows(rows: list) -> str:
    return "\n".join(" | ".join(str(c) for c in row) for row in rows)


def _grounded(answer: str, rendered: str) -> bool:
    """Every number in the answer must appear in the rows that came back."""
    return all(tok in rendered for tok in _NUMBER_RE.findall(answer))


def _make_provider(name: str):
    if name == "double":
        return DoubleProvider()
    if name == "openai":
        return OpenAIProvider()
    raise ValueError(f"unknown provider: {name!r}")


def run(question: str, *, provider: str = "double", max_steps: int = 8,
        max_cost_usd: float = 0.10) -> AgentResult:
    prov = _make_provider(provider)
    store = TraceStore(_traces_path())
    step_cost = _STEP_COST.get(prov.name, 0.0)

    messages: list[dict] = [
        {"role": "system",
         "content": "You answer questions about a certified DuckDB warehouse of "
                    "CFPB complaints using ONLY read-only SELECTs over "
                    "facts.complaints, facts.quarantine, marts.* and meta.dataset."},
        {"role": "user", "content": question},
    ]

    executed_sql: list[str] = []
    rendered_rows = ""
    last_row_count = 0
    steps_log: list[dict] = []
    cost = 0.0

    con = config.connect(read_only=True)
    try:
        for step in range(1, max_steps + 1):
            if cost + step_cost > max_cost_usd:
                return _finish(store, question, steps_log, AgentResult(
                    answer="", sql=executed_sql, rows=last_row_count,
                    trace_id="", refused=True,
                    refusal_reason="cost budget exceeded"))
            try:
                prop = prov.propose(messages, TOOLS)
            except ProviderUnavailable as exc:
                return _finish(store, question, steps_log, AgentResult(
                    answer="", sql=executed_sql, rows=last_row_count,
                    trace_id="", refused=True,
                    refusal_reason=f"provider unavailable: {exc}"))
            cost += step_cost
            tool = prop.get("tool")
            args = prop.get("args") or {}
            step_rec = {"step": step, "tool": tool, "args": args}
            messages_by_tool = messages

            if tool == "refuse":
                step_rec["outcome"] = "refused"
                steps_log.append(step_rec)
                return _finish(store, question, steps_log, AgentResult(
                    answer="", sql=executed_sql, rows=last_row_count,
                    trace_id="", refused=True,
                    refusal_reason=args.get("reason", "provider refused")))

            if tool == "answer":
                answer = str(args.get("answer", ""))
                grounded = bool(rendered_rows) and _grounded(answer, rendered_rows)
                step_rec["outcome"] = "answered" if grounded else "ungrounded"
                steps_log.append(step_rec)
                if grounded:
                    return _finish(store, question, steps_log, AgentResult(
                        answer=answer, sql=executed_sql, rows=last_row_count,
                        trace_id="", refused=False, refusal_reason=None))
                return _finish(store, question, steps_log, AgentResult(
                    answer="", sql=executed_sql, rows=last_row_count,
                    trace_id="", refused=True,
                    refusal_reason="answer contained numbers not present in query rows"))

            if tool == "query":
                sql = str(args.get("sql", ""))
                ok, reason = sql_guard(sql)
                if not ok:
                    step_rec["outcome"] = f"guard_rejected: {reason}"
                    steps_log.append(step_rec)
                    messages_by_tool.append({
                        "role": "tool",
                        "content": f"GUARD REJECTION: {reason}. Propose a "
                                   "read-only SELECT over the allow-listed tables.",
                    })
                    continue
                try:
                    rows = con.execute(sql).fetchall()
                except Exception as exc:  # duckdb error -> finding, keep looping
                    step_rec["outcome"] = f"sql_error: {exc}"
                    steps_log.append(step_rec)
                    messages_by_tool.append({
                        "role": "tool",
                        "content": f"SQL ERROR: {exc}. Propose a corrected query.",
                    })
                    continue
                rendered_rows = _render_rows(rows)
                last_row_count = len(rows)
                executed_sql.append(sql)
                step_rec["outcome"] = f"executed rows={len(rows)}"
                steps_log.append(step_rec)
                messages_by_tool.append({
                    "role": "tool",
                    "content": rendered_rows,
                    "rows": [[str(c) for c in r] for r in rows[:200]],
                })
                continue

            step_rec["outcome"] = f"unknown_tool: {tool}"
            steps_log.append(step_rec)
            messages_by_tool.append({
                "role": "tool",
                "content": "Unknown tool. Use query, answer, or refuse.",
            })
    finally:
        con.close()

    return _finish(store, question, steps_log, AgentResult(
        answer="", sql=executed_sql, rows=last_row_count,
        trace_id="", refused=True, refusal_reason="step budget exhausted"))


def _finish(store: TraceStore, question: str, steps_log: list[dict],
            result: AgentResult) -> AgentResult:
    """Commit every step to the chain, then a summary; return with trace_id."""
    for rec in steps_log:
        store.append(question, {"kind": "step", **rec})
    trace_id = store.append(question, {
        "kind": "summary",
        "refused": result.refused,
        "refusal_reason": result.refusal_reason,
        "answer": result.answer,
        "sql": result.sql,
        "rows": result.rows,
        "steps": steps_log,
    })
    result.trace_id = trace_id
    return result
