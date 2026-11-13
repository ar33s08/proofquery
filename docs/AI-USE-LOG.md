# AI Use Log

Where an LLM was used in building or running this analysis, what it got
right, and — the part worth more — where it was wrong and how it was
caught. Entries describe *verified* events; "verified live" means confirmed
against the real tool/API in-session, not recalled.

## Template (copy for each entry)

| # | Date | Task | AI role | Claim/suggestion | Verification | Outcome |
|---|------|------|---------|------------------|--------------|---------|
|   |      |      |         |                  |              |         |

Outcome vocabulary: `kept` (held up under verification), `corrected` (AI was
wrong; fixed and how), `rejected` (not used, why).

## Log

| # | Date | Task | AI role | Claim/suggestion | Verification | Outcome |
|---|------|------|---------|------------------|--------------|---------|
| 1 | 2026-11-12 | Fetch CFPB slice via Search API | Propose API parameters | `sort=complaint_id_asc` for a deterministic earliest-first slice | Live API call rejected the param; the valid earliest-first sort key is `created_date_asc`, now hardcoded in `tools/fetch_data.py` | corrected — verified live |
| 2 | 2026-11-12 | Fetch reliability | Plain `urllib` fetch per request | Python urllib (HTTP/1.1) stalls indefinitely against consumerfinance.gov; identical query returns in ~10s via curl (HTTP/2) — `fetch_data.get()` shells out to curl for this reason | Live A/B of both paths on the same URL (HTTP/1.1 stall vs curl 9.7s) | corrected — verified live |
| 3 | 2026-11-12 | Sampling design | Propose sampling strategy for the 22-month window | Random N-of-all-rows sampling was the AI's first suggestion; monthly stratification (first 250 per month, `created_date_asc`) chosen instead to keep the sample deterministic with no stored seed and preserve time coverage | Design review against reproducibility goal (ADR-004); re-fetch reproduces identical rows | rejected in favor of stratification — verified live against API |

## Open entries to fill as the build continues

| # | Date | Task | AI role | Claim/suggestion | Verification | Outcome |
|---|------|------|---------|------------------|--------------|---------|
|   |      | SQL: mart window functions | Draft rolling MA7 / rank SQL | | |
|   |      | Stats: two-proportion test | Draft normal-approx vs Fisher choice | | |
|   |      | NL→SQL agent: generated SQL | Schema-grounded query drafts | evals evidence gate | |
|   |      | Report prose | Draft memo sentences | numbers cross-checked against marts queries | |
