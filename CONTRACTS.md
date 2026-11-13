# CONTRACTS — internal build contract (not shipped to users; delete at polish)

Shared facts every module codes against. The warehouse is built by the parent
at data/proofquery.duckdb (read-only for you); build your own scratch DB from
the committed slice per Environment below.

## Environment
- Repo: ~/portfolio/proofquery  (git main, no remote yet — DO NOT run git)
- Python: use `~/portfolio/proofquery/.venv/bin/python` (3.13, duckdb 2.0
  installed; `pip install -e .` may not have run — import via PYTHONPATH=src
  if `import proofquery` fails, and say so in your report rather than
  pip-installing anything).
- Tests: run with `.venv/bin/python -m pytest tests/<your files> -q`.
- Build the DB yourself ONLY into a scratch path:
  PROOFQUERY_DB=/tmp/pq_<yourname>.duckdb .venv/bin/python -m proofquery.warehouse
  Never write to data/proofquery.duckdb (shared read-only for you).

## Warehouse (authoritative; introspect, never hardcode column lists)
- facts.complaints: complaint_id BIGINT PK, product, sub_product, issue,
  sub_issue, company, company_norm, state, zip3, tags, submitted_via,
  date_received DATE, date_sent DATE, company_response, timely BOOLEAN (NULL
  = unknown, NEVER coerce), public_response (the ONLY free text; CFPB
  withdrew consumer narratives — see data/SOURCES.md).
- marts.complaints_daily: day, complaints, timely_rate, monetary_rate,
  avg_days_to_company, complaints_ma7, complaints_cumulative
- marts.product_monthly: month, product, complaints, product_share,
  timely_rate, monetary_rate
- marts.company_monthly: month, company_norm, complaints, timely_rate,
  monetary_rate, rank_in_month
- marts.issue_monthly: month, product, issue, complaints
- marts.company_timely_stats: company_norm, n, timely_yes, timely_rate
- facts.quarantine (complaint_id, reason, raw_line), meta.dataset
  (source_url, license, sha256, fetched_at, row_count)
- Quality codes live in src/proofquery/quality.py (Q001-Q002, DQ001-DQ006) —
  import from there, never restate.

## Style rules
- DuckDB SQL only (duckdb.connect via proofquery.config.connect).
- No new runtime dependencies. Dev extras (pytest, httpx) and ml extra
  (scikit-learn) may be assumed absent unless listed here; degrade gracefully.
- Every module you own gets tests under tests/ that pass offline, fast (<10s),
  deterministic, no network.
- Docstrings state the business question each metric answers (the JD signal).

## Interfaces agreed at dispatch time (2026-11-12)
- Agent: `proofquery.agent.run(question: str, *, provider: str = "double",
  max_steps: int = 8, max_cost_usd: float = 0.10) -> AgentResult` with
  fields: answer:str, sql:list[str], rows:int, trace_id:str,
  refused:bool, refusal_reason:str|None. provider "double" = deterministic
  offline pattern-matcher (no network) that MUST support: volume-by-month,
  top-companies, timely-rate, product-mix, and REFUSE out-of-schema questions
  (anything not answerable from facts/marts) and write attempts (DROP/UPDATE).
- CLI: `proofquery` console with subcommands: build | quality | metrics |
  anomalies | ask | report | serve. Shared flags: --json.
- Trace: `proofquery.tracing.TraceStore(db_path)` — sqlite, hash-chained
  (prev_hash + sha256(payload)), verify() returns list of broken links.
