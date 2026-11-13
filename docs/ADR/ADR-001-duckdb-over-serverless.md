# ADR-001: Embedded DuckDB warehouse instead of a hosted serverless warehouse

## Status
Accepted.

## Context
The analytics layer needs a SQL engine over bounded slices of the CFPB
complaint data, with room to grow. The realistic options were: (a) embedded
DuckDB reading a repo-local CSV, (b) a hosted serverless warehouse
(BigQuery/Snowflake/Databricks SQL), (c) Postgres in a container. This is a
portfolio repo that must be clone-and-run by a reviewer, verified in CI, and
audited down to the bytes that produced each number.

## Decision
Use DuckDB in-process, one file: `data/proofquery.duckdb`, accessed only
through `src/proofquery/config.py:connect()`. Layered staging → facts → marts
inside that single file. No hosted warehouse, no DB daemon in the stack.

## Consequences
**Gains.**
- Reproducibility: CI and a reviewer's laptop run the identical engine and
  identical file bytes — no server version skew, no cold-start flakiness, no
  credentials to manage or leak. CI is a `pip install` plus `pytest`.
- Single-file provenance chain: the CSV's sha256 is recorded in
  `meta.dataset` at ingest (`warehouse.ingest`), so "which bytes produced
  this number" is one SQL query, not a cloud audit log.
- The read-only guard for the agent (ADR-003) is a one-line flag on the same
  connection factory that everything else uses.

**Costs accepted.**
- No multi-writer concurrency: two processes cannot build simultaneously
  (DuckDB locks the file). Acceptable — build is one idempotent command.
- No native partitioned/object-store lakehouse: the "lakehouse layers" here
  are schemas in one file, so claims must stay honest (README/SOURCES say
  "warehouse", the layering is staging/facts/marts, not S3+Iceberg).
- Scale ceiling around hundreds of millions of rows on laptop RAM; fine for
  governed slices, and the fetcher's "slice IS the query" design (ADR-004)
  keeps datasets deliberately bounded.
- Reviewers must have Python, not just a browser — mitigated by the Dockerfile.
