# ADR-003: Enforce the agent's read-only guard at the connection layer, not the prompt

## Status
Accepted (guard factory exists; agent lane consumes it — `src/proofquery/agent/`, planned).

## Context
The NL→SQL analyst agent lets an LLM generate SQL against the warehouse. The
industry-default mitigation is prompt text ("you may only run SELECT
statements"), which is advisory: it fails against prompt injection, model
error, or a future developer who trims the system prompt. The dashboard and
reports need the same guarantee.

## Decision
There is exactly one connection factory: `src/proofquery/config.py:connect()`.
The agent, dashboard, and report lanes call `config.connect(read_only=True)`
and receive a DuckDB connection the engine itself refuses to write through;
only the warehouse builder uses `read_only=False`. No module hands the agent
a raw `duckdb.connect`, and no guard lives in prompt text.

## Consequences
**Gains.**
- Prompt injection can at most produce a read query — the write path is
  physically closed at the DuckDB level, so the "evaluate prompt injection"
  JD requirement is answered by an enforced invariant, not a claim.
- One chokepoint to test: a unit test asserting a write through
  `connect(read_only=True)` raises belongs in the agent lane's tests.

**Costs accepted.**
- The agent cannot materialize results ("save this as a table") — it must
  return rows; acceptable because report/trace files are written by Python,
  not SQL.
- Read-only still exposes reads of `staging` and `facts.quarantine`; the
  agent lane adds a schema allowlist on top of the connection guard
  (defense in depth, contracted in CONTRACTS.md).
- If anyone later needs agent-triggered writes (materialized views), this
  ADR must be revisited rather than worked around with a second connection.
