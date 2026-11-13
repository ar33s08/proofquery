"""proofquery.agent — the NL->SQL analyst agent.

Public interface (CONTRACTS.md):

    run(question, *, provider="double", max_steps=8, max_cost_usd=0.10)
        -> AgentResult(answer, sql, rows, trace_id, refused, refusal_reason)

``run`` lives in ``loop``; ``AgentResult`` is defined here so loop and
callers share one dataclass (imported by ``loop`` after this module body has
run, avoiding a circular-import hazard).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentResult:
    answer: str
    sql: list = field(default_factory=list)
    rows: int = 0
    trace_id: str = ""
    refused: bool = False
    refusal_reason: str | None = None


from .loop import run  # noqa: E402  (after AgentResult: loop imports it back)

__all__ = ["AgentResult", "run"]
