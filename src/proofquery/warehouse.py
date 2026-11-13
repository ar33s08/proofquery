"""Warehouse build: CSV -> staging -> facts (typed, quarantining) -> marts.

Idempotent: `build()` can be re-run any time and rebuilds facts/marts from
whatever is in staging. The ONLY sanctioned way to land new data is
`ingest()`, which records provenance (sha256 + fetch time + row count) in
meta.dataset before anything else happens.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .quality import QUARANTINE_CODES, scan_staging

NORMALIZE_SUFFIXES = (
    (", INC.", ""),
    (", INC", ""),
    (", LLC", ""),
    (" INC.", ""),
    (" INC", ""),
    (" L.L.C.", ""),
    (" LLC", ""),
    (" N.A.", ""),
    (" NA", ""),
    (" THE", ""),
)


def normalize_company(name: str) -> str:
    out = " ".join((name or "").upper().split())
    for suffix, repl in NORMALIZE_SUFFIXES:
        out = out.replace(suffix, repl)
    return out.strip(" ,.")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class BuildReport:
    staging_rows: int = 0
    fact_rows: int = 0
    quarantine: dict = field(default_factory=dict)
    sha256: str = ""
    marts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "staging_rows": self.staging_rows,
            "fact_rows": self.fact_rows,
            "quarantine": self.quarantine,
            "sha256": self.sha256,
            "marts": self.marts,
        }


def ingest(con, csv_path: Path) -> str:
    """Land the CSV into staging (deduped by complaint_id) + record provenance."""
    digest = sha256_of(csv_path)
    con.execute("DELETE FROM staging.complaints_raw")
    con.execute("DELETE FROM meta.dataset")
    con.execute(
        """
        INSERT INTO staging.complaints_raw
        SELECT complaint_id, product, sub_product, issue, sub_issue, company,
               state, zip_code, tags, submitted_via, date_received,
               date_sent_to_company, company_response, timely,
               company_public_response
        FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY complaint_id ORDER BY date_received
            ) AS rn
            FROM read_csv(?, header=true, all_varchar=true,
                          union_by_name=false, ignore_errors=false)
        ) WHERE rn = 1
        """,
        [str(csv_path)],
    )
    n = con.execute("SELECT COUNT(*) FROM staging.complaints_raw").fetchone()[0]
    con.execute(
        "INSERT INTO meta.dataset VALUES (?, ?, ?, current_timestamp, ?)",
        [config.SOURCE_URL, config.SOURCE_LICENSE, digest, n],
    )
    return digest


def _load_facts(con) -> dict:
    """staging -> facts, with typed casts and quarantine of invalid rows."""
    con.execute("DELETE FROM facts.complaints")
    con.execute("DELETE FROM facts.quarantine")
    findings = scan_staging(con)
    for code, count in findings.items():
        con.executemany(
            "INSERT INTO facts.quarantine VALUES (?, ?, '')",
            [(row[0], code) for row in findings[code].rows],
        )
    keep = """
        complaint_id IS NOT NULL
        AND regexp_matches(complaint_id, '^[0-9]+$')
        AND try_cast(date_received AS DATE) IS NOT NULL
    """
    con.execute(
        f"""
        INSERT INTO facts.complaints
        SELECT
            try_cast(complaint_id AS BIGINT),
            nullif(product, ''),
            nullif(sub_product, ''),
            nullif(issue, ''),
            nullif(sub_issue, ''),
            nullif(company, ''),
            NULLIF(normalize_company_udf(company), ''),
            nullif(state, ''),
            CASE WHEN length(zip_code) >= 3 THEN left(zip_code, 3) END,
            nullif(tags, ''),
            nullif(submitted_via, ''),
            try_cast(date_received AS DATE),
            try_cast(date_sent_to_company AS DATE),
            nullif(company_response, ''),
            CASE lower(coalesce(timely, ''))
                WHEN 'yes' THEN TRUE WHEN 'no' THEN FALSE ELSE NULL END,
            nullif(company_public_response, '')
        FROM staging.complaints_raw
        WHERE {keep}
        """
    )
    return findings


def _register_udf(con) -> None:
    con.execute(
        """
        CREATE OR REPLACE MACRO normalize_company_udf(s) AS
            regexp_replace(
                regexp_replace(upper(trim(s)),
                               '(,? ?(INC|LLC|N\\.A\\.|L\\.L\\.C\\.)\\.?)+$', '', 'gi'),
                '^THE +', '', 'gi')
        """
    )


def build(csv_path: Path | None = None) -> BuildReport:
    csv_path = csv_path or config.RAW_CSV
    con = config.connect()
    report = BuildReport()
    try:
        con.execute(config.sql("schema.sql"))
        report.sha256 = ingest(con, csv_path)
        _register_udf(con)
        _load_facts(con)
        con.execute(config.sql("marts.sql"))
        report.staging_rows = con.execute(
            "SELECT COUNT(*) FROM staging.complaints_raw"
        ).fetchone()[0]
        report.fact_rows = con.execute("SELECT COUNT(*) FROM facts.complaints").fetchone()[0]
        for code, label in QUARANTINE_CODES.items():
            n = con.execute(
                "SELECT COUNT(*) FROM facts.quarantine WHERE reason = ?", [code]
            ).fetchone()[0]
            if n:
                report.quarantine[code] = n
        report.marts = {
            t: con.execute(f"SELECT COUNT(*) FROM marts.{t}").fetchone()[0]
            for t in (
                "complaints_daily",
                "product_monthly",
                "company_monthly",
                "issue_monthly",
                "company_timely_stats",
            )
        }
    finally:
        con.close()
    return report


if __name__ == "__main__":
    import json
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(build(path).as_dict(), indent=2))
