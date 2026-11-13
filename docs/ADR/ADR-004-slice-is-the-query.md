# ADR-004: The slice IS the query — datasets are fetch-program text, sha-pinned

## Status
Accepted.

## Context
Analytics repos rot when the dataset is an opaque CSV someone downloaded once
from a dashboard export: nobody can re-derive it, and hand-edits (fix a date,
delete two rows) silently desynchronize the data from its documentation. This
repo's dataset is a deliberately bounded slice of the CFPB Consumer Complaint
Database (CC0).

## Decision
The dataset is defined by executable text: `tools/fetch_data.py` encodes the
full slice (date range 2025-01-01…2026-10-31, 22 monthly windows, first 250
complaints per month by `created_date_asc`, explicit field projection,
cross-window dedup). Re-running it reproduces the same rows; re-running the
builder over the same bytes reproduces the same warehouse. No hand-edited CSVs
ever enter `data/raw/`: `warehouse.ingest` hashes the file (sha256) and pins
the digest, source URL, license, fetch time, and row count into
`meta.dataset` before any transformation happens.

## Consequences
**Gains.**
- Any reviewer can re-derive or resize the dataset from one file and one
  command; changing the slice means changing code that is under version
  control — the diff *is* the dataset changelog.
- "Which bytes produced this number" is `SELECT * FROM meta.dataset`, and CI
  can assert the on-disk CSV matches the pinned digest.
- Monthly stratification (instead of random sampling) keeps the sample
  reproducible with no stored seed and preserves time coverage.

**Costs accepted.**
- The slice is *not* a uniform sample of all complaints — early-in-month,
  earliest-created rows are over-represented; every inference-from-sample
  claim must state that (documented in `data/SOURCES.md`).
- The API's mutable retention could in principle change a re-fetch even for a
  frozen window; the sha256 pin in `meta.dataset` makes such drift loud rather
  than silent.
- A fetch costs live network calls to the CFPB API; offline reproduction works
  from the pinned CSV, and CI never touches the network (tests are offline by
  CONTRACTS.md).
