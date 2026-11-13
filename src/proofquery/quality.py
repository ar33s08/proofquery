"""Data-quality findings with a code registry (README-as-contract friendly).

Two classes:
  * QUARANTINE codes -- rows that cannot become facts (bad id/date). They land
    in facts.quarantine with the code, never silently dropped.
  * DQ codes -- rows that are usable but degraded (missing text, bad state
    code, negative handling time). They stay in facts, and the metric layer
    excludes them explicitly with a comment pointing at the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

QUARANTINE_CODES = {
    "Q001": "complaint_id missing or not numeric",
    "Q002": "date_received missing or unparseable",
}

DQ_CODES = {
    "DQ001": "company_public_response missing (the only free-text field; text lane excludes row)",
    "DQ002": "state missing or not a 2-letter code",
    "DQ003": "date_sent earlier than date_received (negative handling time; excluded from duration metrics)",
    "DQ004": "company missing (company_norm metrics exclude row)",
    "DQ005": "timely flag missing (timely_rate denominator excludes row)",
    "DQ006": "zip_code missing or shorter than 3 digits",
}


@dataclass
class Findings:
    counts: dict = field(default_factory=dict)
    samples: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "quarantine": {
                code: {"label": QUARANTINE_CODES[code], "count": self.counts.get(code, 0)}
                for code in QUARANTINE_CODES
                if self.counts.get(code, 0)
            },
            "dq": {
                code: {"label": DQ_CODES[code], "count": self.counts.get(code, 0)}
                for code in DQ_CODES
                if self.counts.get(code, 0)
            },
        }


_QUARANTINE_PROBES = {
    "Q001": """
        SELECT complaint_id FROM staging.complaints_raw
        WHERE complaint_id IS NULL OR NOT regexp_matches(complaint_id, '^[0-9]+$')
    """,
    "Q002": """
        SELECT complaint_id FROM staging.complaints_raw
        WHERE try_cast(date_received AS DATE) IS NULL
    """,
}

_DQ_PROBES = {
    "DQ001": "SELECT complaint_id FROM facts.complaints WHERE public_response IS NULL",
    "DQ002": """
        SELECT complaint_id FROM facts.complaints
        WHERE state IS NULL OR NOT regexp_matches(state, '^[A-Z]{2}$')
    """,
    "DQ003": """
        SELECT complaint_id FROM facts.complaints
        WHERE date_sent IS NOT NULL AND date_sent < date_received
    """,
    "DQ004": "SELECT complaint_id FROM facts.complaints WHERE company_norm IS NULL",
    "DQ005": "SELECT complaint_id FROM facts.complaints WHERE timely IS NULL",
    "DQ006": "SELECT complaint_id FROM facts.complaints WHERE zip3 IS NULL",
}


class _Result:
    def __init__(self, rows):
        self.rows = rows


def scan_staging(con) -> dict:
    """Quarantine findings keyed by code -> _Result(rows)."""
    out = {}
    for code, q in _QUARANTINE_PROBES.items():
        rows = con.sql(q).fetchall()
        if rows:
            out[code] = _Result(rows)
    return out


def run_quality_suite(con) -> Findings:
    """Full quality report over the built warehouse (facts must exist)."""
    f = Findings()
    for code in _QUARANTINE_PROBES:
        f.counts[code] = con.execute(
            "SELECT COUNT(*) FROM facts.quarantine WHERE reason = ?", [code]
        ).fetchone()[0]
    for code, q in _DQ_PROBES.items():
        f.counts[code] = con.execute(q.replace("SELECT complaint_id", "SELECT COUNT(*)")).fetchone()[0]
    return f
