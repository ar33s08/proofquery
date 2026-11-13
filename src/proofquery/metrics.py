"""KPI registry: named business metrics, one SQL callable each.

Every KPI answers a one-line business question (its docstring) and runs
against the governed layer only -- marts.* where a mart already models it,
facts.complaints where the answer needs a filter the marts don't pre-aggregate.
Nothing here recomputes a definition the marts already own; DQ-driven
exclusions reference the codes in quality.py, never restate them.
"""

from __future__ import annotations


def _total_complaints(con, **_):
    """How many complaints landed in the warehouse in total?"""
    return con.sql("SELECT COUNT(*) AS total_complaints FROM facts.complaints").fetchall()


def _timely_rate(con, **_):
    """What share of complaints did companies resolve on time (overall)?

    Denominator excludes rows with a missing timely flag (DQ005), per the
    quality registry -- the flag is never coerced to yes/no.
    """
    return con.sql(
        """
        SELECT
            SUM(CASE WHEN timely THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS timely_rate,
            COUNT(*) AS n
        FROM facts.complaints
        WHERE timely IS NOT NULL  -- DQ005: unknown flags leave the denominator
        """
    ).fetchall()


def _monetary_relief_rate(con, **_):
    """What share of complaints ended with the consumer getting money back?"""
    return con.sql(
        """
        SELECT
            AVG(CASE WHEN company_response = 'Closed with monetary relief'
                     THEN 1.0 ELSE 0.0 END) AS monetary_relief_rate,
            COUNT(*) AS n
        FROM facts.complaints
        """
    ).fetchall()


def _avg_days_to_company(con, **_):
    """How long, on average, from receipt to forwarding it to the company?

    Rows whose date_sent precedes date_received (DQ003, negative handling
    time) are excluded from the duration, per the quality registry.
    """
    return con.sql(
        """
        SELECT
            AVG(CASE WHEN date_sent >= date_received  -- DQ003: negatives excluded
                     THEN DATE_DIFF('day', date_received, date_sent) END
            ) AS avg_days_to_company,
            COUNT(*) AS n
        FROM facts.complaints
        """
    ).fetchall()


def _monthly_volume_trend(con, **_):
    """Is complaint volume growing, month over month?"""
    return con.sql(
        """
        SELECT
            DATE_TRUNC('month', date_received) AS month,
            COUNT(*)                           AS complaints
        FROM facts.complaints
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()


def _top_companies(con, n=10, **_):
    """Which companies draw the most complaints?"""
    return con.sql(
        f"""
        SELECT company_norm, COUNT(*) AS complaints
        FROM facts.complaints
        WHERE company_norm IS NOT NULL  -- DQ004: unknown company
        GROUP BY company_norm
        ORDER BY complaints DESC, company_norm ASC
        LIMIT {int(n)}
        """
    ).fetchall()


def _product_mix(con, month=None, **_):
    """What product mix drove complaints in a month (or overall)?"""
    if month is None:
        return con.sql(
            """
            SELECT product, SUM(complaints) AS complaints,
                   SUM(complaints) * 1.0 / SUM(SUM(complaints)) OVER () AS share
            FROM marts.product_monthly
            GROUP BY product
            ORDER BY complaints DESC
            """
        ).fetchall()
    return con.sql(
        """
        SELECT product, complaints, product_share AS share
        FROM marts.product_monthly
        WHERE DATE_TRUNC('month', month) = DATE_TRUNC('month', CAST(? AS DATE))
        ORDER BY complaints DESC
        """,
        [str(month)],
    ).fetchall()


def _response_rate_by_company(con, n=25, **_):
    """Which companies answer on time least often (and how often is that)?"""
    return con.sql(
        f"""
        SELECT company_norm, n, timely_yes, timely_rate
        FROM marts.company_timely_stats
        ORDER BY timely_rate ASC, n DESC, company_norm ASC
        LIMIT {int(n)}
        """
    ).fetchall()


REGISTRY: dict = {
    fn.__name__.lstrip("_"): fn
    for fn in (
        _total_complaints,
        _timely_rate,
        _monetary_relief_rate,
        _avg_days_to_company,
        _monthly_volume_trend,
        _top_companies,
        _product_mix,
        _response_rate_by_company,
    )
}


def get(con, name: str, **params) -> list:
    """Run the named KPI and return its rows. Unknown name -> KeyError naming
    every valid KPI."""
    try:
        fn = REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown metric {name!r}; valid metrics: {', '.join(sorted(REGISTRY))}"
        ) from None
    return fn(con, **params)
