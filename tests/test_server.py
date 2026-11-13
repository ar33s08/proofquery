"""Server tests against a scratch DB built from a tiny CSV in tmp_path.

The DB is built through the real warehouse pipeline with PROOFQUERY_DB pointed
at tmp; nothing here touches data/proofquery.duckdb or needs network.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

CSV_FIELDS = (
    "complaint_id product sub_product issue sub_issue company state zip_code "
    "tags submitted_via date_received date_sent_to_company company_response "
    "timely company_public_response"
).split()

# 10 rows land in staging (dupe id removed by ingest): 8 valid facts + 1 Q001
# (id 'abc') + 1 Q002 (unparseable date). Row 6: negative handling time
# (DQ003) + blank zip (DQ006); row 7: timely unknown (DQ005).
ROW_TUPLES = [
    ("1", "Debt collection", "Medical debt", "Attempts to collect debt not owed", "", "ABLE AGENCY INC", "TX", "75001", "", "Web", "2025-01-03", "2025-01-05", "Closed with monetary relief", "Yes", "Company corrects the information"),
    ("2", "Credit reporting", "Credit reporting", "Incorrect information on your report", "", "TRANSUNION INC", "NY", "30001", "", "Web", "2025-01-10", "2025-01-15", "Closed with explanation", "Yes", ""),
    ("3", "Debt collection", "Credit card debt", "Trust me", "", "CHASE NA", "NY", "10001", "", "Phone", "2025-01-20", "2025-01-22", "Closed with non-monetary relief", "No", ""),
    ("4", "Student loan", "Federal student loan", "Loan servicing", "", "SERVICER LLC", "CA", "90001", "", "Web", "2025-02-02", "2025-02-09", "Closed with explanation", "Yes", ""),
    ("5", "Debt collection", "Medical debt", "Timing of debt", "", "ABLE AGENCY INC", "GA", "30301", "", "Web", "2025-02-11", "2025-02-12", "Closed with monetary relief", "Yes", ""),
    ("6", "Credit reporting", "Credit reporting", "Incorrect information on your report", "", "TRANSUNION INC", "NY", "", "", "Web", "2025-02-18", "2025-01-30", "Closed with explanation", "No", ""),
    ("7", "Vehicle loan or lease", "", "Billing", "", "AUTO CO", "TX", "73301", "", "Web", "2025-03-04", "2025-03-06", "Closed with explanation", "", ""),
    ("8", "Debt collection", "", "Communication tactics", "", "OKAY BANK", "FL", "33101", "", "Email", "2025-03-14", "2025-03-20", "Closed with monetary relief", "Yes", "Company responds with explanation"),
    ("abc", "Credit reporting", "", "Other", "", "", "Web", "2025-01-05", "", "Web", "2025-01-05", "", "Unknown", "", ""),
    ("9001", "Debt collection", "", "Other", "", "LATE INC", "ZZ", "11111", "", "Web", "not-a-date", "2025-04-01", "Closed with explanation", "Yes", ""),
    ("1", "Debt collection", "Medical debt", "Attempts to collect debt not owed", "", "ABLE AGENCY INC", "TX", "75001", "", "Web", "2025-01-04", "2025-01-05", "Closed with monetary relief", "Yes", ""),
]


def _quote(field: str) -> str:
    return '"' + field.replace('"', '""') + '"'


def csv_text() -> str:
    """Quoted CSV, byte-for-byte in the style of the committed real slice."""
    lines = [",".join(_quote(f) for f in CSV_FIELDS)]
    lines += [",".join(_quote(f) for f in row) for row in ROW_TUPLES]
    assert all(",".join(_quote(f) for f in r).count('"') == 30 for r in ROW_TUPLES)
    return "\n".join(lines) + "\n"


def write_tiny_csv(tmp: Path) -> Path:
    csv = tmp / "tiny.csv"
    csv.write_text(csv_text(), encoding="utf-8")
    return csv


@pytest.fixture(scope="module")
def server_db(tmp_path_factory):
    from proofquery import config, warehouse

    tmp = tmp_path_factory.mktemp("srvdb")
    csv = write_tiny_csv(tmp)
    db = tmp / "srv.duckdb"
    old = os.environ.get("PROOFQUERY_DB")
    os.environ["PROOFQUERY_DB"] = str(db)
    importlib.reload(config)
    try:
        report = warehouse.build(csv)
        assert report.fact_rows == 8
        from fastapi.testclient import TestClient

        from proofquery.server import app

        with TestClient(app) as client:
            yield client
    finally:
        if old is None:
            os.environ.pop("PROOFQUERY_DB", None)
        else:
            os.environ["PROOFQUERY_DB"] = old
        importlib.reload(config)


def test_healthz_shape(server_db):
    r = server_db.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    from proofquery import __version__

    assert body["version"] == __version__
    assert body["row_count"] == 10  # staging row_count from meta.dataset
    assert len(body["source_sha256"]) == 12
    assert body["fetched_at"]


def test_unknown_metric_404(server_db):
    assert server_db.get("/metrics/definitely_not_a_metric").status_code == 404


def test_dashboard_serves_html(server_db):
    r = server_db.get("/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "ProofQuery" in r.text
    assert 'id="chart"' in r.text
    assert "cdn" not in r.text.lower()


def test_anomalies_bad_method_422(server_db):
    assert server_db.get("/anomalies?method=bogus").status_code == 422


def test_ask_double_provider(server_db):
    pytest.importorskip("proofquery.agent")
    r = server_db.post("/ask?provider=double", json={"question": "top companies"})
    assert r.status_code == 200
    body = r.json()
    for field in ("answer", "sql", "rows", "trace_id", "refused", "refusal_reason"):
        assert field in body


def test_ask_rejects_empty(server_db):
    pytest.importorskip("proofquery.agent")
    assert server_db.post("/ask", json={"question": ""}).status_code == 422
