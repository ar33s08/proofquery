# Data provenance

## Consumer Complaint Database (the only dataset in this repo)

| Field | Value |
|---|---|
| Source | Consumer Financial Protection Bureau (US federal agency) |
| Artifact | Official nightly bulk export: https://files.consumerfinance.gov/ccdb/complaints.csv.zip (~366 MB, updated daily) |
| License | CC0 1.0 — public domain dedication (CFPB data portal) |
| Slice | Complaints received 2025-01-01 … 2026-10-31; 250 per month across the 22 monthly windows, deterministic md5-hash sample within each month (spread across all days, not the month's earliest hours) |
| Slice definition | `tools/fetch_data.py` — the slice IS the SQL in that file; re-running against the same export reproduces the same rows |
| Rows | `meta.dataset.row_count` in the warehouse, echoed by `proofquery build` |
| Source digest | SHA-256 of the export zip recorded in `meta.dataset` at build time |
| Committed slice | `data/raw/cfpb_complaints.csv` (the sliced 15-column CSV is committed; the 366 MB zip is not) |

Why the bulk export and not the search API: the API's gateway 504s under
load for wide-field queries (verified live 2026-11-12 — see
`docs/AI-USE-LOG.md`), which makes a multi-request fetch a coin-flip against
someone else's live service. The CDN-served bulk export is stable, and the
slice becomes deterministic SQL instead of fragile pagination.

Notes:
- **No consumer narratives exist in CFPB's public data** (verified live
  2026-11-12 on both the bulk export and the API: `complaint_what_happened`
  is absent from the export and null from the API). The only published
  free-text field is `company_public_response` — what a company chose to say
  publicly, present on a minority of rows. This repo builds its text lane on
  that truth instead of pretending narratives exist.
- This repo stores no PII beyond what CFPB publishes; ZIP codes are
  truncated to 3 digits in `facts.complaints.zip3`.
- **CFPB withdrew consumer narratives**: the free-text column
  `complaint_what_happened` no longer exists in the export and returns null
  from the API (probed across 2016 and 2025 windows plus direct id lookups,
  all zero-length). The pivot: text lane = `company_public_response`
  (genuinely free-text, sparse by design) + supervised ML on the
  structured issue/outcome space. The withdrawn-narrative finding is a
  documented data point, not a bug.
- Company names arrive with inconsistent legal suffixes (`INC.`, `L.L.C.`,
  `N.A.`); `warehouse.normalize_company` canonicalises them for grouping.
- This is **real production data, not a synthetic demo**: real null patterns,
  real duplicate complaint_ids (deduped in staging, counted by the Q/DQ
  registry), real negative handling times (DQ003), and a source that changed
  schema under our feet (narratives withdrawn) — handled by pinning the
  export's SHA-256, not by hoping the API is stable.
