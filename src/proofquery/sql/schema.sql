-- ProofQuery warehouse schema. This file is the SINGLE source of truth for
-- table and column names: dashboards, metrics, the agent's schema guard, and
-- evals all read column metadata from the live database, never from copies.

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS meta;

-- Raw rows exactly as landed from the CSV (all TEXT; casting is a pipeline
-- concern, tested in pipeline/quality.py, not a surprise at query time).
CREATE TABLE IF NOT EXISTS staging.complaints_raw (
    complaint_id          VARCHAR,
    product               VARCHAR,
    sub_product           VARCHAR,
    issue                 VARCHAR,
    sub_issue             VARCHAR,
    company               VARCHAR,
    state                 VARCHAR,
    zip_code              VARCHAR,
    tags                  VARCHAR,
    submitted_via         VARCHAR,
    date_received         VARCHAR,
    date_sent_to_company  VARCHAR,
    company_response      VARCHAR,
    timely                VARCHAR,
    company_public_response VARCHAR
);

-- Typed fact table. Rows that fail hard validity rules are quarantined, not
-- silently dropped: facts.quarantine keeps them with a reason code.
CREATE SCHEMA IF NOT EXISTS facts;

CREATE TABLE IF NOT EXISTS facts.complaints (
    complaint_id     BIGINT PRIMARY KEY,
    product          VARCHAR,
    sub_product      VARCHAR,
    issue            VARCHAR,
    sub_issue        VARCHAR,
    company          VARCHAR,
    company_norm     VARCHAR,           -- trimmed, upper-cased, suffix-normalised
    state            VARCHAR,
    zip3             VARCHAR,           -- first 3 ZIP digits (coarse geo, no PII)
    tags             VARCHAR,
    submitted_via    VARCHAR,
    date_received    DATE,
    date_sent        DATE,
    company_response VARCHAR,
    timely           BOOLEAN,           -- NULL when 'timely' missing (never coerced)
    -- CFPB withdraws consumer narratives from the public DB; the only real
    -- free text is what the COMPANY chose to publish (sparse by design).
    public_response  VARCHAR
    -- NB: no FK to staging.complaints_raw: DuckDB does not support
    -- cross-schema foreign keys (verified live 2026-11-12). Row linkage is
    -- guaranteed by construction (facts load FROM staging) and asserted by
    -- tests/test_warehouse.py (row conservation: staging = facts + quarantine).
);

CREATE TABLE IF NOT EXISTS facts.quarantine (
    complaint_id VARCHAR,
    reason       VARCHAR,               -- DQ code, see quality.py registry
    raw_line     VARCHAR
);

-- Run provenance: which bytes produced these numbers.
CREATE TABLE IF NOT EXISTS meta.dataset (
    source_url   VARCHAR,
    license      VARCHAR,
    sha256       VARCHAR,
    fetched_at   TIMESTAMP,
    row_count    BIGINT
);
