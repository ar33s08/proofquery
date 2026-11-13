"""Exact-value tests for the KPI registry on a hand-built warehouse."""

import csv
from datetime import date

import pytest

from proofquery import config, metrics, warehouse

CSV_HEADER = [
    "complaint_id", "product", "sub_product", "issue", "sub_issue", "company",
    "state", "zip_code", "tags", "submitted_via", "date_received",
    "date_sent_to_company", "company_response", "timely",
    "company_public_response",
]

# 7 rows become facts; one duplicate id (deduped), one bad id and one bad
# date (quarantined) ride along to prove the pipeline gates are respected.
ROWS = [
    # 101,102 same day -> daily volume spike; 2024-01
    ("101", "P1", "", "Deposit problem", "", "Alpha Bank Inc.", "TX", "750", "", "Web",
     "2024-01-05", "2024-01-07", "Closed with monetary relief", "Yes", ""),
    ("102", "P1", "", "Billing dispute", "", "Alpha Bank", "TX", "750", "", "Web",
     "2024-01-05", "2024-01-09", "Closed with explanation", "No", ""),
    ("103", "P2", "", "Fraud or scam", "", "Beta Corp", "CA", "900", "", "Phone",
     "2024-01-06", "2024-01-06", "Closed with monetary relief", "", ""),   # DQ005 null timely
    ("104", "P2", "", "Billing dispute", "", "Beta Corp", "CA", "902", "", "Web",
     "2024-01-05", "2024-01-08", "Closed with explanation", "Yes", ""),
    # 2024-02
    ("105", "P1", "", "Billing dispute", "", "Gamma LLC", "NY", "100", "", "Web",
     "2024-02-05", "2024-02-10", "Closed with explanation", "No", ""),
    ("106", "P1", "", "Fraud or scam", "", "Gamma", "NY", "100", "", "Postal",
     "2024-02-01", "2024-01-20", "Closed with monetary relief", "Yes", ""),  # DQ003 negative days
    ("107", "P3", "", "Unclear", "", "", "ZZ1", "12", "", "Referral",
     "2024-02-03", "2024-02-04", "Closed with explanation", "Yes", ""),      # DQ004 no company
]
DIRT = [
    ("abc", "P1", "", "x", "", "Alpha Bank", "TX", "750", "", "Web",
     "2024-01-10", "", "Closed", "Yes", ""),                                  # Q001
    ("901", "P1", "", "x", "", "Alpha Bank", "TX", "750", "", "Web",
     "", "", "Closed", "Yes", ""),                                             # Q002
    ("101", "P1", "", "late dupe", "", "Alpha Bank", "TX", "750", "", "Web",
     "2024-03-01", "", "Closed", "Yes", ""),                                   # dedup keeps 01-05
]


@pytest.fixture
def con(tmp_path, monkeypatch):
    csv_path = tmp_path / "in.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        w.writerows(ROWS + DIRT)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.duckdb")
    warehouse.build(csv_path)
    c = config.connect()
    yield c
    c.close()


def test_total_complaints_exact(con):
    assert metrics.get(con, "total_complaints") == [(7,)]


def test_timely_rate_excludes_null_flag(con):
    (rate, n) = metrics.get(con, "timely_rate")[0]
    assert n == 6                    # row 103 (DQ005) is out of the denominator
    assert rate == pytest.approx(4 / 6)  # spec-shaped 2/3


def test_monetary_relief_rate_exact(con):
    (rate, n) = metrics.get(con, "monetary_relief_rate")[0]
    assert (n, rate) == (7, pytest.approx(3 / 7))


def test_avg_days_excludes_negative_dq003(con):
    # eligible: 2,4,0,3,5,1 days (row 106's negative excluded), 15/6
    (avg, n) = metrics.get(con, "avg_days_to_company")[0]
    assert n == 7
    assert avg == pytest.approx(15 / 6)


def test_monthly_volume_trend(con):
    assert metrics.get(con, "monthly_volume_trend") == [
        (date(2024, 1, 1), 4),
        (date(2024, 2, 1), 3),
    ]


def test_top_companies_normalized_and_limited(con):
    top = metrics.get(con, "top_companies", n=2)
    assert top == [("ALPHA BANK", 2), ("BETA CORP", 2)]  # ties break by name


def test_product_mix_for_month(con):
    mix = metrics.get(con, "product_mix", month="2024-01-15")
    assert mix == [("P1", 2, pytest.approx(0.5)), ("P2", 2, pytest.approx(0.5))]


def test_response_rate_by_company(con):
    rows = {(r[0], r[1], r[2]): r[3] for r in metrics.get(con, "response_rate_by_company")}
    assert rows[("ALPHA BANK", 2, 1)] == pytest.approx(0.5)  # 103's null flag already out
    assert rows[("GAMMA", 2, 1)] == pytest.approx(0.5)


def test_unknown_metric_lists_valid_names(con):
    with pytest.raises(KeyError) as excinfo:
        metrics.get(con, "nope")
    msg = str(excinfo.value)
    for name in ("total_complaints", "timely_rate", "top_companies", "nope"):
        assert name in msg


def test_registry_has_business_questions():
    assert set(metrics.REGISTRY) == {
        "total_complaints", "timely_rate", "monetary_relief_rate",
        "avg_days_to_company", "monthly_volume_trend", "top_companies",
        "product_mix", "response_rate_by_company",
    }
    for fn in metrics.REGISTRY.values():
        assert fn.__doc__ and fn.__doc__.strip()
