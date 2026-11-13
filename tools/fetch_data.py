"""Build data/raw/cfpb_complaints.csv from CFPB's official bulk export (CC0).

History (docs/AI-USE-LOG.md carries the receipts): v1 paged the search API;
its gateway 504s under load for wide-field queries, so the slice moved OFF
the wire and INTO SQL against the CDN-served nightly bulk export. v2 assumed
a `complaint_what_happened` narrative column; verified live 2026-11-12, CFPB
publishes NO narrative in the bulk export OR the API (null everywhere) --
consumer narratives were withdrawn from the public database. The only
free-text field left is `company_public_response` (what the company chose to
say publicly). This repo states that honestly instead of faking a text lane.

The slice IS the SQL below: complaints received 2025-01-01..2026-10-31,
250 per month across 22 monthly windows, sampled by md5(complaint_id) so the
pick is deterministic (md5 is pure) yet spread across every day of the month
(a '250 oldest per month' first cut concentrated the corpus in 22 days and
starved the daily time-series marts -- hash-ordering fixed it).

Usage:
    python tools/fetch_data.py [path/to/complaints.csv.zip]
    # with no argument, downloads the nightly export first (~366 MB zip)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import duckdb

BULK_URL = "https://files.consumerfinance.gov/ccdb/complaints.csv.zip"
DATE_MIN = "2025-01-01"
DATE_MAX = "2026-10-31"
PER_MONTH = 250

# Bulk-export header, in file order. Passing columns= disables DuckDB's
# dialect sniffer, which misdetects the dialect on the zip sample (verified
# live 2026-11-12). Header spellings live here and in the SELECT list only.
EXPORT_COLUMNS = {
    "Date received": "VARCHAR",
    "Product": "VARCHAR",
    "Sub-product": "VARCHAR",
    "Issue": "VARCHAR",
    "Sub-issue": "VARCHAR",
    "Company public response": "VARCHAR",
    "Company": "VARCHAR",
    "State": "VARCHAR",
    "ZIP code": "VARCHAR",
    "Tags": "VARCHAR",
    "Submitted via": "VARCHAR",
    "Date sent to company": "VARCHAR",
    "Company response to consumer": "VARCHAR",
    "Timely response?": "VARCHAR",
    "Complaint ID": "VARCHAR",
}

# Bulk-export headers are pretty-printed ("Date received"); this repo's
# warehouse columns are snake_case. The mapping lives in exactly one place:
# the SELECT list below.
SLICE_SQL = """
COPY (
    WITH typed AS (
        SELECT
            "Complaint ID"                       AS complaint_id,
            "Product"                            AS product,
            "Sub-product"                        AS sub_product,
            "Issue"                              AS issue,
            "Sub-issue"                          AS sub_issue,
            "Company"                            AS company,
            "Company public response"            AS company_public_response,
            "State"                              AS state,
            "ZIP code"                           AS zip_code,
            "Tags"                               AS tags,
            "Submitted via"                      AS submitted_via,
            try_cast("Date received" AS DATE)    AS date_received,
            try_cast("Date sent to company" AS DATE) AS date_sent_to_company,
            "Company response to consumer"       AS company_response,
            "Timely response?"                   AS timely,
            row_number() OVER (
                PARTITION BY date_trunc('month', try_cast("Date received" AS DATE))
                ORDER BY md5("Complaint ID"), "Complaint ID"
            )                                    AS rn
        FROM read_csv(?, columns=?, header=true, ignore_errors=false,
                      delim=',', quote='"', escape='"', max_line_size=100000000)
        WHERE try_cast("Date received" AS DATE)
              BETWEEN DATE '2025-01-01' AND DATE '2026-10-31'
    )
    SELECT complaint_id, product, sub_product, issue, sub_issue, company,
           company_public_response, state, zip_code, tags, submitted_via,
           date_received, date_sent_to_company, company_response, timely
    FROM typed
    WHERE rn <= {per_month}
    ORDER BY try_cast(complaint_id AS BIGINT)
) TO ? (HEADER, FORCE_QUOTE *)
"""


def main() -> int:
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "data/raw/complaints.csv.zip"
    )
    if not zip_path.exists():
        print(f"downloading {BULK_URL} -> {zip_path}", file=sys.stderr)
        subprocess.run(
            ["curl", "-s", "--fail", "--max-time", "1500", "-o", str(zip_path),
             BULK_URL],
            check=True,
        )
    csv_path = zip_path.with_name("complaints.csv")
    if not csv_path.exists():
        # DuckDB does not transparently decompress ZIP containers (verified
        # live: it "sniffs 0 columns"); extract first. ~5.8 GB, git-ignored.
        print(f"extracting {zip_path} -> {csv_path}", file=sys.stderr)
        subprocess.run(["unzip", "-q", "-o", str(zip_path), "-d",
                        str(zip_path.parent)], check=True)
    out_path = Path("data/raw/cfpb_complaints.csv")
    tmp_path = out_path.with_suffix(".tmp.csv")
    con = duckdb.connect()
    con.execute(
        SLICE_SQL.replace("{per_month}", str(PER_MONTH)),
        [str(csv_path), EXPORT_COLUMNS, str(tmp_path)],
    )
    import os

    os.replace(tmp_path, out_path)  # atomic swap; readers never see a partial file
    n = con.sql(
        f"SELECT COUNT(*) FROM read_csv('{out_path}', header=true)"
    ).fetchall()[0][0]
    print(f"wrote {n} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
