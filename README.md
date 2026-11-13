# proofquery

**An AI analyst that can only say things it can prove.**

proofquery is an end-to-end analytics workbench over a real federal dataset
(5,500 CFPB consumer complaints, CC0): a governed DuckDB warehouse with a
quality/quarantine registry, inferential statistics and anomaly detection, a
supervised ML baseline with an honest majority-class comparison, a
natural-language analyst agent whose answers are **certified against the rows
it actually retrieved**, hash-chained run traces, a dashboard, and
eval-as-CI.

Every number is traceable three hops back to a byte range:
`answer → SQL result rows → facts.complaints → SHA-256-pinned CC0 source`.

```
CFPB bulk export (CC0, sha-pinned)          tools/fetch_data.py = the slice, as SQL
        │
        ▼
staging.complaints_raw ──Q00x──▶ facts.quarantine      (nothing drops silently)
        │
        ▼
facts.complaints ──DQ00x codes──▶ marts.*              (window functions, CTEs)
        │                              │
        ▼                              ▼
NL→SQL agent ──guard──▶ read-only SQL   KPIs · stats tests · EWMA anomalies · ML
        │                              │
        ▼                              ▼
hash-chained traces              dashboard · weekly memo · REST API
```

## Why this exists

The hiring screen for AI engineer / AI analyst roles is no longer "can you
call an API." It is: can you build pipelines that quarantine bad rows instead
of dropping them, run the statistics that separate signal from noise, ship an
LLM agent that refuses more often than it hallucinates, and prove the whole
thing in CI. This repo is each of those, on real data, executable in one
afternoon.

## What the data actually is

- 5,500 consumer complaints (2025-01 → 2026-10), deterministic md5-hash
  sample of 250/month from the official CFPB nightly bulk export (~366 MB
  zip, CC0). The slice is SQL in `tools/fetch_data.py`, not a hand-picked file.
- **No consumer narratives exist in the public data** (verified live; the
  free-text column CFPB used to publish was withdrawn). The only real
  free-text field is `company_public_response` (3,151 of 5,500 rows, 57%).
  proofquery builds its text lane on that truth instead of pretending
  otherwise — see `data/SOURCES.md` and `docs/AI-USE-LOG.md`.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev,ml,serve]'
.venv/bin/proofquery build          # land CSV -> staging -> facts -> marts
.venv/bin/proofquery quality --json # quality registry findings, coded
.venv/bin/proofquery metrics        # KPI registry (each names its business question)
.venv/bin/proofquery anomalies --method ewma
.venv/bin/proofquery ask "which companies had the most complaints in 2026?"
.venv/bin/proofquery report         # weekly analyst memo, provenance footered
.venv/bin/proofquery serve --port 8080   # + open http://localhost:8080/dashboard
.venv/bin/python -m proofquery.evals --gate   # eval-as-CI: fails on behavior regression
```

## What each part proves

| Layer | File | The claim it backs |
|---|---|---|
| Pipeline | `src/proofquery/warehouse.py` | Reproducible ingestion; row conservation invariant `staging = facts + quarantine`, tested |
| Data quality | `src/proofquery/quality.py` | Every anomaly has a code (Q001…, DQ001…); counts, not vibes |
| SQL modeling | `src/proofquery/sql/*.sql` | CTEs, window functions, conditional aggregation as governed marts |
| Statistics | `src/proofquery/stats.py` | Two-proportion z-tests + EWMA/z-score anomaly detection, stdlib-only |
| ML | `src/proofquery/ml.py` | LogisticRegression + TF-IDF baseline that always reports the majority-class bar next to itself |
| Agent | `src/proofquery/agent/` | SQL guard before execution, read-only connection, budgeted loop, refuses off-schema questions |
| Certified answers | `src/proofquery/agent/loop.py` | An answer's numbers must appear in the retrieved rows — or the agent refuses |
| Traces | `src/proofquery/tracing.py` | SHA-256 hash chain: a stored run can be proven untampered |
| Service | `src/proofquery/server.py` | REST surface + self-contained, offline-usable dashboard |
| Analysis delivery | `src/proofquery/report.py` | Decision-ready memo: movers, anomalies, caveats, provenance footer |
| Eval | `src/proofquery/evals.py` | Golden-question gate in CI; behavior regressions fail the build |

Design decisions and their costs: `docs/ADR/`. Where AI was used and where it
was wrong: `docs/AI-USE-LOG.md` (7 verified corrections during the build).

## Honest limitations

- It is a 5,500-row slice of a ~5.8 GB corpus — chosen so the whole
  warehouse is reproducible on a laptop in one command, not because 5.8 GB
  wouldn't fit; the slice query scales unchanged.
- The NL→SQL agent's deterministic offline provider ("double") exists so CI
  tests the plumbing without secrets; point `PROOFQUERY_LLM_BASE_URL` at any
  OpenAI-compatible endpoint to run a live model behind the same guard.
- `company_response` outcomes lag: the most recent weeks skew toward
  'In progress' — the memo says so rather than averaging through it.
- ML lane predicts outcome categories from structured features; with a
  narrative-free corpus, claiming richer NLP would be a lie.

MIT licensed. Data: CC0, U.S. Government work.
