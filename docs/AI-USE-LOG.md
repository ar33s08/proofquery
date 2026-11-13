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
| 3 | 2026-11-12 | Sampling design | Propose sampling strategy for the 22-month window | Random N-of-all-rows sampling was the AI's first suggestion; monthly stratification (first 250 per month, `created_date_asc`) chosen instead to keep the sample deterministic with no stored seed and preserve time coverage | Design review against reproducibility goal (ADR-004); re-fetch reproduces identical rows | rejected in favor of stratification — verified live against API; later refined by entry #7 |
| 4 | 2026-11-12 | Load 366 MB CFPB bulk zip in DuckDB | Assume `read_csv('complaints.csv.zip')` decompresses transparently | DuckDB auto-decompresses gzip/zstd but NOT zip containers: the sniffer reported "0 columns" on the archive | `unzip -l` proved a single valid CSV inside; after extraction the identical query ran | corrected — verified live; extract step is now explicit in `tools/fetch_data.py` |
| 5 | 2026-11-12 | Design the free-text (NLP) lane | Schema included a `complaint_what_happened` narrative column (present in CFPB docs from ~2017, and in the AI's memory of the dataset) | Live probes: absent from the 2026 bulk export header; `null`/0-length from the API across 2016 and 2025 windows and direct complaint-id lookups — CFPB withdrew narratives | corrected — verified live; text lane rebuilt on `company_public_response` (57% coverage) + structured-outcome ML; documented in data/SOURCES.md |
| 6 | 2026-11-12 | Fetch reliability at scale | Search API as the dataset source | API gateway returned 504 for 15-field×100-row queries under load (identical narrow query returned in 9.7 s); a 66-request fetch kept dying mid-run | Live: 3 fetch attempts failed on API timeouts; bulk CDN export returned 200 with byte-exact size from HEAD probe | corrected — slice moved from API pagination to SQL over the pinned bulk export |
| 7 | 2026-11-12 | Slice quality gate | "250 oldest per month" (entry #3's design) preserves time coverage | It preserves month coverage but not DAY coverage: build report showed `marts.complaints_daily` had 22 rows for 664 calendar days — daily trends/anomaly detection would have had almost no signal | Build report + `MIN(day)/MAX(day)` check; md5-hash sampling inside each month restored 664 daily rows, still seedless-deterministic | corrected — verified live via the two build reports |
| 8 | 2026-11-13 | Dockerfile packaging | Wrote `COPY src` + data but no `COPY dashboards` | Container smoke (`docker build` + `docker run` + curl) returned 501 on `/dashboard` — the server's honest "file missing" guard rather than a silent 404 | Live: rebuild after the fix returns 200 | corrected — verified live in the container |

## Open entries to fill as the build continues

| # | Date | Task | AI role | Claim/suggestion | Verification | Outcome |
|---|------|------|---------|------------------|--------------|---------|
|   |      | SQL: mart window functions | Draft rolling MA7 / rank SQL | | |
|   |      | Stats: two-proportion test | Draft normal-approx vs Fisher choice | | |
|   |      | NL→SQL agent: generated SQL | Schema-grounded query drafts | evals evidence gate | |
|   |      | Report prose | Draft memo sentences | numbers cross-checked against marts queries | |
