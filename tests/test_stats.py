"""Two-proportion z-test vs hand-computed values + pure anomaly detectors."""

import csv
import math
from datetime import date, timedelta

import pytest

from proofquery import config, stats, warehouse

CSV_HEADER = [
    "complaint_id", "product", "sub_product", "issue", "sub_issue", "company",
    "state", "zip_code", "tags", "submitted_via", "date_received",
    "date_sent_to_company", "company_response", "timely",
    "company_public_response",
]


def _rows():
    rows = []
    cid = 1000
    # ALPHA: 100 rows, 80 timely=Yes. BETA: 100 rows, 60 timely=Yes.
    for company, yes in (("Alpha Bank", 80), ("Beta Corp", 60)):
        for i in range(100):
            cid += 1
            day = date(2024, 1, 1) + timedelta(days=i % 28)
            rows.append((str(cid), "P1", "", "issue", "", company, "TX", "750",
                         "", "Web", day.isoformat(), day.isoformat(),
                         "Closed with monetary relief" if company == "Alpha Bank" and i < 5
                         else "Closed with explanation",
                         "Yes" if i < yes else "No", ""))
    # one NULL-timely ALPHA row: must NOT enter company_timely_stats
    rows.append(("9999", "P1", "", "issue", "", "Alpha Bank", "TX", "750",
                 "", "Web", "2024-01-02", "2024-01-03", "Closed", "", ""))
    return rows


@pytest.fixture
def con(tmp_path, monkeypatch):
    csv_path = tmp_path / "in.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        w.writerows(_rows())
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.duckdb")
    warehouse.build(csv_path)
    c = config.connect()
    yield c
    c.close()


def test_z_test_matches_hand_computation(con):
    out = stats.two_proportion_z_test(con, "ALPHA BANK", "BETA CORP")
    # p1=.8 p2=.6, n=100 each: pooled p=.7
    z_exp = 0.2 / math.sqrt(0.7 * 0.3 * (1 / 100 + 1 / 100))
    p_exp = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z_exp / math.sqrt(2.0))))
    se_diff = math.sqrt(0.8 * 0.2 / 100 + 0.6 * 0.4 / 100)
    assert out["n_a"] == 100 and out["n_b"] == 100   # NULL-timely row excluded
    assert out["z"] == pytest.approx(z_exp, abs=1e-9)
    assert out["p_value"] == pytest.approx(p_exp, abs=1e-9)
    assert out["ci_low"] == pytest.approx(0.2 - 1.959963984540054 * se_diff, abs=1e-9)
    assert out["ci_high"] == pytest.approx(0.2 + 1.959963984540054 * se_diff, abs=1e-9)


def test_z_test_monetary_and_errors(con):
    out = stats.two_proportion_z_test(con, "ALPHA BANK", "BETA CORP", metric="monetary")
    # ALPHA: 5/101 monetary (null-timely row counted here), BETA: 0/100
    assert out["n_a"] == 101 and out["n_b"] == 100
    p_pool = 5 / 201
    z_exp = (5 / 101 - 0) / math.sqrt(p_pool * (1 - p_pool) * (1 / 101 + 1 / 100))
    assert out["z"] == pytest.approx(z_exp, abs=1e-9)
    with pytest.raises(ValueError):
        stats.two_proportion_z_test(con, "ALPHA BANK", "NOPE CORP")
    with pytest.raises(ValueError):
        stats.two_proportion_z_test(con, "ALPHA BANK", "BETA CORP", metric="bogus")


def test_weekly_aggregate(con):
    series = stats.weekly_aggregate(con)
    assert all(isinstance(d, date) and isinstance(v, int) for d, v in series)
    assert sum(v for _, v in series) == 201
    assert [d for d, _ in series] == sorted(d for d, _ in series)


def _synthetic(values, start=date(2024, 1, 1)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_z_score_anomalies_flags_planted_spike():
    series = _synthetic([10] * 20 + [50])
    hits = stats.z_score_anomalies(series, k=3.0)
    assert len(hits) == 1
    assert hits[0]["observed"] == 50
    assert hits[0]["z"] == pytest.approx((50 - 250 / 21) /
                                         (sum((v - 250 / 21) ** 2 for v in [10] * 20 + [50]) / 21) ** 0.5)


def test_z_score_clean_series_is_quiet():
    assert stats.z_score_anomalies(_synthetic([7] * 30), k=3.0) == []
    assert stats.z_score_anomalies([], k=3.0) == []


def test_ewma_catches_planted_spike():
    series = _synthetic([10, 11] * 8 + [40] + [10, 11] * 4)
    hits = stats.ewma_anomalies(series, span=7, k=2.5)
    assert [h["observed"] for h in hits] == [40]     # exactly one hit, the spike
    assert hits[0]["z"] > 2.5
    assert 9.0 < hits[0]["expected"] < 12.0  # baseline tracks the ~10.5 level, not the spike


def test_ewma_flat_series_is_quiet():
    assert stats.ewma_anomalies(_synthetic([5] * 20), span=7, k=2.5) == []
