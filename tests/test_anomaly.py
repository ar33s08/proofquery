"""Glue tests: marts.complaints_daily -> anomaly hits, both methods run."""

import csv
from datetime import date

import pytest

from proofquery import anomaly, config, warehouse

CSV_HEADER = [
    "complaint_id", "product", "sub_product", "issue", "sub_issue", "company",
    "state", "zip_code", "tags", "submitted_via", "date_received",
    "date_sent_to_company", "company_response", "timely",
    "company_public_response",
]

# Daily volumes: 2024-01-05: 3 (planted spike), then four quiet single days.
DAYS = ["2024-01-05", "2024-01-05", "2024-01-05", "2024-01-06", "2024-01-08",
        "2024-01-10", "2024-01-12", "2024-01-14"]


@pytest.fixture
def con(tmp_path, monkeypatch):
    csv_path = tmp_path / "in.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        for i, day in enumerate(DAYS):
            w.writerow([str(500 + i), "P1", "", "issue", "", "Alpha Bank",
                        "TX", "750", "", "Web", day, day,
                        "Closed with explanation", "Yes", ""])
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.duckdb")
    warehouse.build(csv_path)
    c = config.connect()
    yield c
    c.close()


def test_zscore_flags_planted_spike_day(con):
    hits = anomaly.detect_volume_anomalies(con, method="zscore", k=1.5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit["day"] == date(2024, 1, 5)
    assert hit["observed"] == 3
    assert hit["expected"] == pytest.approx(8 / 6)  # mean of [3,1,1,1,1,1]
    assert hit["z"] > 1.5
    assert hit["method"] == "zscore"


def test_ewma_method_runs_and_shapes_hits(con):
    hits = anomaly.detect_volume_anomalies(con, method="ewma")
    assert isinstance(hits, list)
    for hit in hits:
        assert set(hit) == {"day", "observed", "expected", "z", "method"}
        assert hit["method"] == "ewma"


def test_unknown_method_raises(con):
    with pytest.raises(ValueError, match="zscore"):
        anomaly.detect_volume_anomalies(con, method="prophet")
