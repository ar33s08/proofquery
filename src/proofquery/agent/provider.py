"""Query proposers. Two implementations behind one interface:

``propose(messages, tools) -> {"tool": str, "args": dict}``

* ``DoubleProvider`` — deterministic, offline regex pattern-matcher over the
  question. It NEVER touches the network and is what tests and CI run.
  Supported intents: volume-by-month, top-companies, timely-rate,
  product-mix. Anything else (including prompt-injection / write attempts)
  is refused, not guessed.
* ``OpenAIProvider`` — stdlib-urllib call to any OpenAI-compatible endpoint
  (env PROOFQUERY_LLM_BASE_URL / PROOFQUERY_LLM_API_KEY). Raises
  ``ProviderUnavailable`` when env is missing so the loop can fall back.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


class ProviderUnavailable(RuntimeError):
    """No usable LLM backend (missing env, network, or bad response)."""


# The tools the loop understands. Providers may only propose these.
TOOLS = [
    {
        "name": "query",
        "description": "Run a read-only SQL SELECT over the warehouse.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "intent": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "answer",
        "description": "Final answer; every number must come from rows already returned.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    },
    {
        "name": "refuse",
        "description": "Decline to answer.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

_OUT_OF_SCHEMA = "question not answerable from the certified warehouse schema"
_WRITE_RE = re.compile(
    r"\b(drop|delete|update|insert|alter|truncate|attach|pragma|install|load"
    r"|grant|revoke|vacuum|copy)\b|ignore (all |any )?(previous|prior|above|earlier)"
    r"|disregard .{0,20}instructions",
    re.IGNORECASE,
)


class DoubleProvider:
    """Deterministic offline NL->SQL matcher (no network, ever)."""

    name = "double"

    # intent -> (detector regex, sql). Order matters: most specific first.
    _PATTERNS = [
        (
            "timely_rate",
            re.compile(
                r"timely|on time|response tim[ei]|how (fast|quick)|responsiveness"
                r"|responded? (in|within)",
                re.IGNORECASE,
            ),
            "SELECT strftime('%Y-%m', date_trunc('month', day)) AS month,"
            " round(100.0 * SUM(timely_rate * complaints) / SUM(complaints), 1)"
            " AS timely_rate_pct"
            " FROM marts.complaints_daily"
            " WHERE timely_rate IS NOT NULL"
            " GROUP BY 1 ORDER BY 1",
        ),
        (
            "product_mix",
            re.compile(r"product[ -]?mix|which product|product breakdown|mix of products"
                       r"|share of products", re.IGNORECASE),
            "SELECT product, strftime('%Y-%m', month) AS month, complaints,"
            " round(100.0 * product_share, 1) AS product_share_pct"
            " FROM marts.product_monthly"
            " WHERE month = (SELECT MAX(month) FROM marts.product_monthly)"
            " ORDER BY complaints DESC",
        ),
        (
            "top_companies",
            re.compile(
                r"top (companies|firms|lenders|banks)|most complain"
                r"|worst (companies|firms)|companies.*(most|complain|rank)"
                r"|ranked?\b.*compan|biggest complain",
                re.IGNORECASE,
            ),
            "SELECT company_norm, complaints, rank_in_month"
            " FROM marts.company_monthly"
            " WHERE month = (SELECT MAX(month) FROM marts.company_monthly)"
            " AND rank_in_month <= 10"
            " ORDER BY rank_in_month",
        ),
        (
            "volume_by_month",
            re.compile(
                r"volume|how many complaints|complaints? (per|a|each) month"
                r"|monthly|trend|over time",
                re.IGNORECASE,
            ),
            "SELECT strftime('%Y-%m', date_trunc('month', day)) AS month,"
            " SUM(complaints) AS complaints"
            " FROM marts.complaints_daily"
            " GROUP BY 1 ORDER BY 1",
        ),
    ]

    def propose(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        question = self._question(messages)

        # An injection/write attempt is refused outright, never proposed as SQL.
        if _WRITE_RE.search(question):
            return {"tool": "refuse",
                    "args": {"reason": "write attempts and instruction overrides are refused"}}

        # After rows have come back, answer from them (loop verifies numbers).
        obs = self._last_rows_message(messages)
        if obs is not None:
            intent_sql = self._detect(question)
            text = self._fmt(intent_sql[0], obs) if intent_sql else None
            if text is not None:
                return {"tool": "answer", "args": {"answer": text}}
            return {"tool": "refuse", "args": {"reason": _OUT_OF_SCHEMA}}

        intent_sql = self._detect(question)
        if intent_sql is None:
            return {"tool": "refuse", "args": {"reason": _OUT_OF_SCHEMA}}
        intent, sql = intent_sql
        return {"tool": "query", "args": {"sql": sql, "intent": intent}}

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _question(messages: list[dict]) -> str:
        for m in messages:
            if m.get("role") == "user":
                return str(m.get("content", ""))
        return ""

    @staticmethod
    def _last_rows_message(messages: list[dict]):
        for m in reversed(messages):
            if m.get("role") == "tool" and isinstance(m.get("rows"), list):
                return m["rows"]
        return None

    def _detect(self, question: str):
        for intent, rx, sql in self._PATTERNS:
            if rx.search(question):
                return intent, sql
        return None

    @staticmethod
    def _fmt(intent: str, rows: list) -> str | None:
        if not rows:
            return "The certified warehouse returned no rows for this question."
        if intent == "volume_by_month":
            parts = ", ".join(f"{r[0]}: {r[1]}" for r in rows)
            return f"Complaint volume by month: {parts}."
        if intent == "top_companies":
            parts = ", ".join(f"{r[0]} ({r[1]})" for r in rows)
            return f"Top companies by complaints in the latest month: {parts}."
        if intent == "timely_rate":
            parts = ", ".join(f"{r[0]}: {r[1]}%" for r in rows)
            return f"Timely response rate by month: {parts}."
        if intent == "product_mix":
            parts = ", ".join(f"{r[0]}: {r[2]} complaints ({r[3]}%)" for r in rows)
            return f"Product mix in the latest month: {parts}."
        return None


class OpenAIProvider:
    """OpenAI-compatible chat endpoint via stdlib urllib (no new deps)."""

    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini"):
        self.base_url = os.environ.get("PROOFQUERY_LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("PROOFQUERY_LLM_API_KEY", "")
        self.model = os.environ.get("PROOFQUERY_LLM_MODEL", model)
        if not self.base_url or not self.api_key:
            raise ProviderUnavailable(
                "PROOFQUERY_LLM_BASE_URL / PROOFQUERY_LLM_API_KEY not set"
            )

    def propose(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        tools = tools or TOOLS
        body = json.dumps({
            "model": self.model,
            "messages": messages,
            "tools": [{"type": "function", "function": t} for t in tools],
            "tool_choice": "auto",
            "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            # 90s: agentic turns with big schemas are slow on small backends.
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable(f"LLM endpoint failed: {exc}") from exc
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ProviderUnavailable("malformed LLM response") from exc
        calls = msg.get("tool_calls") or []
        if calls:
            fn = calls[0]["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise ProviderUnavailable("malformed tool arguments") from exc
            return {"tool": fn["name"], "args": args}
        if msg.get("content"):
            return {"tool": "answer", "args": {"answer": msg["content"]}}
        raise ProviderUnavailable("LLM proposed neither a tool call nor an answer")
