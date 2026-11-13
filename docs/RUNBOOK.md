# RUNBOOK — exact commands, in order

All commands run from the repo root (`~/portfolio/proofquery`). `PY` below
means the project interpreter, `.venv/bin/python` (3.13; duckdb, pytest, ruff,
httpx, scikit-learn, fastapi installed). Steps marked **(pending lane)** name
the contracted command from CONTRACTS.md but do not exist on disk yet — they
will fail until that lane lands.

## 0. One-time setup
```bash
.venv/bin/python -m pip install -e '.[dev,ml,serve]'
```

## 1. Fetch the dataset (network required)
```bash
.venv/bin/python tools/fetch_data.py            # -> data/raw/cfpb_complaints.csv
```
Resumable: per-month progress checkpoints to `data/raw/.checkpoint.jsonl`
and an interrupted run resumes where it stopped. The slice definition (date
range, 250/month, `created_date_asc`, field projection) lives in the file —
the query IS the dataset (ADR-004). Takes several minutes over the CFPB API.

## 2. Build the warehouse
```bash
.venv/bin/python -m proofquery.warehouse        # -> data/proofquery.duckdb
```
Prints a JSON BuildReport: staging/fact row counts, per-code quarantine
counts, CSV sha256, and row counts for all five marts. Idempotent — safe to
re-run. To build without touching the shared DB:
```bash
PROOFQUERY_DB=/tmp/pq_scratch.duckdb .venv/bin/python -m proofquery.warehouse
```

## 3. Quality suite (over the built warehouse)
```bash
.venv/bin/python -c "from proofquery import config, quality; \
import json; print(json.dumps(quality.run_quality_suite(config.connect(read_only=True)).as_dict(), indent=2))"
```
Prints every non-zero Q001–Q002 / DQ001–DQ006 finding with its label
(code registry: `src/proofquery/quality.py`).

## 4. Metrics & anomalies (pending lane)
```bash
.venv/bin/python -m proofquery.metrics          # (pending lane) src/proofquery/metrics.py
.venv/bin/python -m proofquery.anomaly          # (pending lane) src/proofquery/anomaly.py
```
Stats helpers land in `src/proofquery/stats.py` (two-proportion tests, EWMA/
z-score) reading `marts.company_timely_stats` / `marts.complaints_daily`.
ML theme baseline: `.venv/bin/python -m proofquery.ml.themes` (pending lane).

## 5. Ask the analyst agent (pending lane)
```bash
proofquery ask "which product's timely rate moved most last quarter?"   # (pending lane) src/proofquery/agent/
proofquery report                                                       # (pending lane) src/proofquery/report.py
```
The agent connects only via `config.connect(read_only=True)` (ADR-003) and
each answer carries a hash-chained evidence trace.

## 6. Serve + dashboard
```bash
.venv/bin/python -m uvicorn proofquery.server:app --port 8000   # (pending lane) src/proofquery/server.py
```
Then open http://127.0.0.1:8000/ ; the static view is
`dashboards/dashboard.html` (pending lane) and reports land in
`docs/`/`out/` from `proofquery report` (pending lane).

## 7. Evals / evidence gate (pending lane)
```bash
.venv/bin/python -m proofquery.evals --gate     # (pending lane) evals/
```
Same command CI runs; it exits non-zero when an answer loses its evidence
lock or a metric regresses.

## 8. Tests & lint (always available once `tests/` exists)
```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q                   # offline, deterministic, <10s by contract
```

## 9. Docker (once the server lane lands)
```bash
docker build -t proofquery .
docker run -p 8000:8000 proofquery              # HEALTHCHECK hits /healthz
```
The image builds the warehouse from the bundled CSV at image-build time, so
the container answers the moment uvicorn is up.

## Verification queries (any time after step 2)
```sql
SELECT * FROM meta.dataset;                                  -- which bytes produced these numbers
SELECT COUNT(*) FROM facts.complaints;                       -- + facts.quarantine  == staging count (ADR-002)
SELECT reason, COUNT(*) FROM facts.quarantine GROUP BY reason;
```
