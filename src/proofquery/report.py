"""Weekly analyst memo: the whole warehouse, one markdown page.

Business question this answers: "what changed since last month, what looks
weird, and which numbers come with data-quality asterisks?" Every claim cites
the marts it came from; caveats cite the DQ/Q codes in quality.py, never a
restated label.
"""

from __future__ import annotations

import importlib

from .quality import DQ_CODES, QUARANTINE_CODES, run_quality_suite


def _provenance(con) -> dict:
    row = con.execute(
        "SELECT source_url, license, sha256, fetched_at, row_count FROM meta.dataset"
    ).fetchone()
    return {} if row[0] is None else {
        "source_url": row[0], "license": row[1], "sha256": row[2],
        "fetched_at": row[3], "row_count": row[4],
    }


def _headline(con) -> dict:
    total, dmin, dmax = con.execute(
        "SELECT COUNT(*), min(date_received), max(date_received) FROM facts.complaints"
    ).fetchone()
    timely, t_n = con.execute(
        "SELECT AVG(CASE WHEN timely IS NOT NULL "
        "THEN CASE WHEN timely THEN 1.0 ELSE 0.0 END END), COUNT(timely) "
        "FROM facts.complaints"
    ).fetchone()
    monetary = con.execute(
        "SELECT AVG(CASE WHEN company_response = 'Closed with monetary relief' "
        "THEN 1.0 ELSE 0.0 END) FROM facts.complaints"
    ).fetchone()[0]
    # DQ003: negative handling times excluded from duration metrics.
    avg_days = con.execute(
        "SELECT AVG(DATE_DIFF('day', date_received, date_sent)) "
        "FROM facts.complaints WHERE date_sent >= date_received"
    ).fetchone()[0]
    return {"total": total, "first": dmin, "last": dmax, "timely_rate": timely,
            "timely_n": t_n, "monetary_rate": monetary, "avg_days": avg_days}


def _movers(con, limit: int = 5) -> list[dict]:
    """Product mix: biggest month-over-month swings, last two months on record."""
    rows = con.execute(
        """
        WITH months AS (
            SELECT DISTINCT month FROM marts.product_monthly
            ORDER BY month DESC LIMIT 2
        )
        SELECT
            product,
            SUM(CASE WHEN month = (SELECT MAX(month) FROM months)
                     THEN complaints ELSE 0 END) AS cur,
            SUM(CASE WHEN month = (SELECT MIN(month) FROM months)
                     THEN complaints ELSE 0 END) AS prev
        FROM marts.product_monthly
        WHERE month IN (SELECT month FROM months)
        GROUP BY product
        ORDER BY abs(cur - prev) DESC NULLS LAST
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [{"product": p, "current": c, "previous": v, "delta": c - v}
            for p, c, v in rows]


def _anomaly_section(con) -> str:
    """Delegates to the anomaly lane (built in parallel); pending is not an error."""
    try:
        mod = importlib.import_module("proofquery.anomaly")
    except ImportError:
        return "(anomaly lane pending)\n"
    fn = (getattr(mod, "detect_volume_anomalies", None)
          or getattr(mod, "detect_anomalies", None) or getattr(mod, "detect", None)
          or getattr(mod, "get_anomalies", None))
    if fn is None:
        return "(anomaly lane pending)\n"
    try:
        result = fn(con, method="ewma")
    except TypeError:
        result = fn(con, "ewma")
    items = result.get("anomalies", []) if isinstance(result, dict) else result
    if not items:
        return "No anomalies flagged (ewma).\n"
    lines = []
    for a in items if isinstance(items, list) else [items]:
        d = a if isinstance(a, dict) else vars(a)
        day = d.get("day") or d.get("date") or d.get("date_received") or "?"
        exp = d.get("expected")
        act = d.get("actual") or d.get("value") or d.get("observed")
        extra = f", expected {exp:,.0f}" if isinstance(exp, (int, float)) else ""
        act_s = f"{act:,.0f}" if isinstance(act, (int, float)) else str(act)
        lines.append(f"- **{day}**: {act_s} complaints{extra}")
    return "\n".join(lines) + "\n"


def _fmt_date(d) -> str:
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)


def weekly_analyst_memo(con) -> str:
    """Markdown memo: headline numbers, MoV movers, anomalies, DQ caveats, provenance."""
    prov = _provenance(con)
    h = _headline(con)
    findings = run_quality_suite(con).as_dict()

    out = ["# ProofQuery weekly analyst memo", ""]
    out += [
        "## Headline numbers",
        "",
        f"- **{h['total']:,}** complaints received "
        f"{_fmt_date(h['first'])} to {_fmt_date(h['last'])}",
        f"- Timely response rate: **{h['timely_rate']:.1%}** "
        f"(denominator {h['timely_n']:,}; rows with unknown timeliness excluded, DQ005)",
        f"- Closed with monetary relief: **{h['monetary_rate']:.1%}**",
        f"- Avg days to company: **{h['avg_days']:.1f}** "
        "(negative handling times excluded, DQ003)",
        "",
        "## Biggest month-over-month movers (by product)",
        "",
    ]
    movers = _movers(con)
    if movers:
        months = con.execute(
            "SELECT DISTINCT month FROM marts.product_monthly "
            "ORDER BY month DESC LIMIT 2"
        ).fetchall()
        cur_m, prev_m = _fmt_date(months[0][0])[:7], _fmt_date(months[1][0])[:7]
        out.append(f"| Product | {cur_m} | {prev_m} | Delta |")
        out.append("|---|---:|---:|---:|")
        for m in movers:
            out.append(f"| {m['product']} | {m['current']:,} | {m['previous']:,} "
                       f"| {m['delta']:+,} |")
    else:
        out.append("_Not enough months on record for a month-over-month view._")

    out += ["", "## Anomalies", "", _anomaly_section(con).rstrip(), "",
            "## Data-quality caveats", ""]
    caveats = []
    for code, info in findings["quarantine"].items():
        caveats.append(f"- **{code}** ({info['count']:,} rows): "
                       f"{QUARANTINE_CODES[code]}")
    for code, info in findings["dq"].items():
        caveats.append(f"- **{code}** ({info['count']:,} rows): {DQ_CODES[code]}")
    out += caveats or ["- No open findings."]

    out += ["", "---", ""]
    if prov:
        out += [
            f"*Provenance: {prov['row_count']:,} staging rows from `{prov['source_url']}` "
            f"({prov['license']}), SHA-256 `{prov['sha256'][:12]}...`, "
            f"fetched {_fmt_date(prov['fetched_at'])}. Every figure recomputes from "
            "facts.complaints + marts in this DuckDB file.*",
        ]
    else:
        out.append("*Provenance: meta.dataset is empty — run `proofquery build`.*")
    return "\n".join(out) + "\n"
