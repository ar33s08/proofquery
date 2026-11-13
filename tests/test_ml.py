"""ML plumbing: separable fixture -> perfect-but-honest metrics, baseline shown."""

import csv

import pytest

from proofquery import config, ml, warehouse

CSV_HEADER = [
    "complaint_id", "product", "sub_product", "issue", "sub_issue", "company",
    "state", "zip_code", "tags", "submitted_via", "date_received",
    "date_sent_to_company", "company_response", "timely",
    "company_public_response",
]

# Degenerate but valid: 'Fraud or scam' ALWAYS gets monetary relief,
# 'Billing dispute' never does. A leakage-free model must hit 100% while the
# majority-class baseline sits at 50%.
PLAN = [
    ("Fraud or scam", "Yes"), ("Fraud or scam", "Yes"),
    ("Fraud or scam", "Yes"), ("Fraud or scam", "Yes"),
    ("Billing dispute", "No"), ("Billing dispute", "No"),
    ("Billing dispute", "No"), ("Billing dispute", "No"),
]


@pytest.fixture
def con(tmp_path, monkeypatch):
    csv_path = tmp_path / "in.csv"
    cid = 2000
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        for month in ("2024-01", "2024-02"):  # train Jan, test Feb
            for issue, relief in PLAN:
                cid += 1
                w.writerow([str(cid), "Debt collection", "", issue, "",
                            "Alpha Bank", "TX", "750", "", "Web",
                            f"{month}-10", f"{month}-12",
                            "Closed with monetary relief" if relief == "Yes"
                            else "Closed with explanation", "Yes", ""])
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.duckdb")
    warehouse.build(csv_path)
    c = config.connect()
    yield c
    c.close()


def test_predict_monetary_relief_beats_baseline(con):
    out = ml.predict_monetary_relief(con, test_months=["2024-02"])
    assert out["baseline_rate"] == pytest.approx(0.5)   # majority class, always reported
    assert out["accuracy"] == pytest.approx(1.0)        # separable fixture
    assert out["precision"] == pytest.approx(1.0)
    assert out["recall"] == pytest.approx(1.0)
    assert out["accuracy"] > out["baseline_rate"]


def test_model_card_contents(con):
    out = ml.predict_monetary_relief(con, test_months=["2024-02"])
    card = out["model_card"]
    assert card["train_months"] == ["2024-01"]
    assert card["test_months"] == ["2024-02"]
    assert card["n_train"] == 8 and card["n_test"] == 8  # PLAN x 2 months
    assert card["n_features"] > 0
    assert any("tfidf" in f for f in card["features"])
    assert hasattr(out["model"], "predict")


def test_bad_split_raises(con):
    with pytest.raises(ValueError):
        ml.predict_monetary_relief(con, test_months=["2024-02", "2024-01"])
    with pytest.raises(ValueError):
        ml.predict_monetary_relief(con, test_months=[])
