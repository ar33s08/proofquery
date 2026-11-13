# ADR-002: Quarantine invalid rows with a reason code; never silently drop

## Status
Accepted.

## Context
Real CFPB rows arrive with missing/non-numeric `complaint_id`, unparseable
`date_received`, empty free text, invalid state codes, and `date_sent` earlier
than `date_received`. A `WHERE`-clause filter in the ETL would make the marts
look clean while making the totals quietly wrong — an analyst cannot answer
"how much of the data did you throw away, and why?" after a silent `DROP`.

## Decision
Two tiers, both registered in `src/proofquery/quality.py`:
- Unusable rows (Q001 bad id, Q002 unparseable date) are never discarded —
  every one lands in `facts.quarantine` with its code (`warehouse._load_facts`).
- Usable-but-degraded rows (DQ001–DQ006: no text, bad state, negative
  handling time, etc.) stay in `facts.complaints`; each metric that must
  exclude them does so explicitly, with a comment naming the code.
Invariant enforced by construction: `staging` row count =
`facts.complaints` + `facts.quarantine`.

## Consequences
**Gains.**
- Trust accounting: `python -m proofquery.warehouse` prints a per-code
  quarantine report; any total can be reconciled to the raw row count.
- Metric denominators are auditable (e.g. `timely_rate` excludes NULL-timely
  rows because DQ005 says so), which is exactly what a reviewer probes.

**Costs accepted.**
- Every metric query carries an explicit exclusion — more verbose SQL than a
  pre-filtered fact table, and a discipline requirement CI tests partially
  cover.
- `facts.quarantine` accumulates raw identifiers and needs the same
  governance as facts; it is never joined into marts by accident because
  marts read only `facts.complaints`.
- Staging dedup (first row per `complaint_id` by `date_received` in
  `warehouse.ingest`) happens *before* quarantine, so duplicate ids are
  excluded from the conservation math by design — stated, not hidden.
