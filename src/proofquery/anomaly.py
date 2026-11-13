"""Glue: daily complaint volume -> anomaly report.

Turns marts.complaints_daily into the (date, value) series the pure
detectors in stats.py expect, so "which days were weird" is one call.
"""

from __future__ import annotations

from . import stats

_METHODS = {"ewma": stats.ewma_anomalies, "zscore": stats.z_score_anomalies}


def detect_volume_anomalies(con, method: str = "ewma", **kwargs) -> list[dict]:
    """Which days did complaint volume break out of its normal range?

    Reads the daily series from marts.complaints_daily and flags days via
    method='ewma' (adaptive recent baseline, default) or method='zscore'
    (global mean/sigma). Each hit: {day, observed, expected, z, method}.
    """
    if method not in _METHODS:
        raise ValueError(
            f"unknown method {method!r}; use one of: {', '.join(sorted(_METHODS))}"
        )
    series = con.sql(
        "SELECT day, complaints FROM marts.complaints_daily ORDER BY day"
    ).fetchall()
    hits = _METHODS[method](series, **kwargs)
    for hit in hits:
        hit["method"] = method
    return hits
