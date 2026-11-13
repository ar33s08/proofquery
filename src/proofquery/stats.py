"""Inferential statistics: is a difference real, or is it noise?

Pure functions over plain (date, value) series where possible, so the maths
is unit-testable on synthetic data without a warehouse; the one DB-bound
function reads success/try counts from marts.company_timely_stats and applies
the textbook normal-approximation two-proportion z-test (stdlib math only).
"""

from __future__ import annotations

import math

# Two-sided 95% normal quantile; kept as a module constant so tests and the
# CI math reference the same number.
Z_95 = 1.959963984540054


def _phi(x: float) -> float:
    """Standard normal CDF via the stdlib erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_proportion_z_test(con, company_a: str, company_b: str,
                          metric: str = "timely") -> dict:
    """Is company A's timely rate really lower than B's, or just noise?

    Two-sided two-proportion z-test (normal approximation, pooled SE for the
    z/p and unpooled SE for the 95% CI of the rate difference), reading
    successes/trials per company. For metric='timely' the counts come from
    marts.company_timely_stats (rows with a NULL flag are already out);
    metric='monetary' counts 'Closed with monetary relief' over all rows.
    Returns dict(z, p_value, ci_low, ci_high, n_a, n_b) where the CI covers
    rate(A) - rate(B).
    """
    if metric == "timely":
        q = ("SELECT n, timely_yes FROM marts.company_timely_stats "
             "WHERE company_norm = ?")
    elif metric == "monetary":
        q = ("SELECT COUNT(*) AS n, "
             "SUM(CASE WHEN company_response = 'Closed with monetary relief' "
             "THEN 1 ELSE 0 END) AS yes "
             "FROM facts.complaints WHERE company_norm = ?")
    else:
        raise ValueError(f"unsupported metric {metric!r}; use 'timely' or 'monetary'")

    counts = {}
    for name, comp in (("a", company_a), ("b", company_b)):
        row = con.execute(q, [comp]).fetchone()
        if row is None:
            raise ValueError(f"no rows for company {comp!r} (check exact company_norm spelling)")
        counts[name] = (int(row[0]), int(row[1]))

    n_a, y_a = counts["a"]
    n_b, y_b = counts["b"]
    if n_a == 0 or n_b == 0:
        raise ValueError("both companies need at least one non-null observation")
    p_a, p_b = y_a / n_a, y_b / n_b
    p_pool = (y_a + y_b) / (n_a + n_b)

    se_pooled = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n_a + 1.0 / n_b))
    if se_pooled == 0.0:
        # Both companies agree perfectly (e.g. both 0% or both 100%): no
        # detectable difference; z and p are defined as 0 / 1 by convention.
        return {"z": 0.0, "p_value": 1.0, "ci_low": p_a - p_b, "ci_high": p_a - p_b,
                "n_a": n_a, "n_b": n_b}

    z = (p_a - p_b) / se_pooled
    p_value = 2.0 * (1.0 - _phi(abs(z)))
    se_diff = math.sqrt(p_a * (1.0 - p_a) / n_a + p_b * (1.0 - p_b) / n_b)
    margin = Z_95 * se_diff
    diff = p_a - p_b
    return {"z": z, "p_value": p_value,
            "ci_low": diff - margin, "ci_high": diff + margin,
            "n_a": n_a, "n_b": n_b}


def weekly_aggregate(con) -> list[tuple]:
    """What does the weekly complaint-volume series look like?

    Returns [(week_start_date, complaints), ...] ordered by week -- the
    input shape for the pure anomaly detectors below.
    """
    return con.sql(
        """
        SELECT DATE_TRUNC('week', date_received) AS week, COUNT(*) AS complaints
        FROM facts.complaints
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()


def z_score_anomalies(series, k: float = 3.0) -> list[dict]:
    """Which points in a (date, value) series sit > k population-sigma from
    the series mean? Pure function; returns one dict per flagged point:
    {day, observed, expected, z} with expected = series mean.
    """
    values = [v for _, v in series]
    n = len(values)
    if n < 2:
        return []
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    sigma = math.sqrt(var)
    if sigma == 0.0:
        return []
    out = []
    for day, v in series:
        z = (v - mean) / sigma
        if abs(z) > k:
            out.append({"day": day, "observed": v, "expected": mean, "z": z})
    return out


def ewma_anomalies(series, span: int = 7, k: float = 2.5) -> list[dict]:
    """Which points break out of their own exponentially weighted baseline?

    Spikes that only look extreme against the *recent* level (a step change
    should not make every later day an anomaly, which global z-scores get
    wrong). EWMA mean m and variance v decay with alpha = 2/(span+1); a point
    is flagged when (x - m_prev) / sqrt(v_prev) > k. Pure function over a
    (date, value) list; returns {day, observed, expected, z} dicts.
    """
    if span < 1:
        raise ValueError("span must be >= 1")
    alpha = 2.0 / (span + 1.0)
    out = []
    mean = None
    var = 0.0
    for day, x in series:
        if mean is None:
            mean = float(x)
            continue
        if var > 0.0:
            sigma = math.sqrt(var)
            z = (x - mean) / sigma
            if z > k:
                out.append({"day": day, "observed": x, "expected": mean, "z": z})
        delta = x - mean
        mean += alpha * delta
        var = (1.0 - alpha) * (var + alpha * delta * delta)
    return out
