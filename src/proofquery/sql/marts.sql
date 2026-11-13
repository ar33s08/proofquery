-- ProofQuery marts: governed business metrics, written with the SQL the JDs
-- screen for (CTEs, window functions, conditional aggregation). Every mart is
-- a full rebuild from facts.complaints, so the whole warehouse is reproducible
-- from the CSV in one command.

CREATE SCHEMA IF NOT EXISTS marts;

-- Daily volume + response-quality KPIs, with rolling 7-day means for the
-- dashboard trend lines.
CREATE OR REPLACE TABLE marts.complaints_daily AS
WITH daily AS (
    SELECT
        date_received                                    AS day,
        COUNT(*)                                         AS complaints,
        AVG(CASE WHEN timely IS NOT NULL
                 THEN CASE WHEN timely THEN 1.0 ELSE 0.0 END END) AS timely_rate,
        AVG(CASE WHEN company_response = 'Closed with monetary relief'
                 THEN 1.0 ELSE 0.0 END)                  AS monetary_rate,
        AVG(CASE WHEN date_sent >= date_received
                 THEN DATE_DIFF('day', date_received, date_sent) END) AS avg_days_to_company
    FROM facts.complaints
    GROUP BY date_received
)
SELECT
    day,
    complaints,
    timely_rate,
    monetary_rate,
    avg_days_to_company,
    AVG(complaints) OVER (
        ORDER BY day ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS complaints_ma7,
    SUM(complaints) OVER (ORDER BY day) AS complaints_cumulative
FROM daily;

-- Product x month: mix shift detection lives here.
CREATE OR REPLACE TABLE marts.product_monthly AS
SELECT
    DATE_TRUNC('month', date_received)                   AS month,
    product,
    COUNT(*)                                             AS complaints,
    COUNT(*) * 1.0 / SUM(COUNT(*)) OVER (
        PARTITION BY DATE_TRUNC('month', date_received)
    )                                                    AS product_share,
    AVG(CASE WHEN timely THEN 1.0 ELSE 0.0 END)          AS timely_rate,
    AVG(CASE WHEN company_response = 'Closed with monetary relief'
             THEN 1.0 ELSE 0.0 END)                      AS monetary_rate
FROM facts.complaints
GROUP BY 1, 2;

-- Company x month, ranked within month (share-of-voice for the memo).
CREATE OR REPLACE TABLE marts.company_monthly AS
WITH agg AS (
    SELECT
        DATE_TRUNC('month', date_received)               AS month,
        company_norm,
        COUNT(*)                                         AS complaints,
        AVG(CASE WHEN timely THEN 1.0 ELSE 0.0 END)      AS timely_rate,
        AVG(CASE WHEN company_response = 'Closed with monetary relief'
                 THEN 1.0 ELSE 0.0 END)                  AS monetary_rate
    FROM facts.complaints
    GROUP BY 1, 2
)
SELECT
    month,
    company_norm,
    complaints,
    timely_rate,
    monetary_rate,
    RANK() OVER (PARTITION BY month ORDER BY complaints DESC) AS rank_in_month
FROM agg;

-- Issue x month for the free-text themes lane.
CREATE OR REPLACE TABLE marts.issue_monthly AS
SELECT
    DATE_TRUNC('month', date_received)                   AS month,
    product,
    issue,
    COUNT(*)                                             AS complaints
FROM facts.complaints
GROUP BY 1, 2, 3;

-- Company head-to-head inputs for the two-proportion stats module:
-- timely-rate comparisons need per-company successes/tries, not rates.
CREATE OR REPLACE TABLE marts.company_timely_stats AS
SELECT
    company_norm,
    COUNT(*)                          AS n,
    SUM(CASE WHEN timely THEN 1 ELSE 0 END) AS timely_yes,
    AVG(CASE WHEN timely THEN 1.0 ELSE 0.0 END) AS timely_rate
FROM facts.complaints
WHERE timely IS NOT NULL
GROUP BY company_norm;
