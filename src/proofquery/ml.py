"""Supervised ML baseline on the structured issue/outcome space.

Honest-metrics rule: the majority-class baseline is always reported next to
model metrics — if the model can't beat "predict the common outcome", the
answer is noise, not signal.
"""

from __future__ import annotations


def _month_key(value) -> str:
    return str(value)[:7]  # 'YYYY-MM-DD...' -> 'YYYY-MM'


def predict_monetary_relief(con, test_months) -> dict:
    """Can we predict which complaints end in monetary relief, from the
    structured issue/product/company fields alone?

    Logistic regression on TF-IDF (word-level) over issue + sub_issue +
    product concatenated, plus one-hot indicators for the top-20 companies by
    training volume. Label: company_response LIKE '%monetary relief%'.
    test_months (['YYYY-MM', ...]) are held out; everything else trains.
    Returns accuracy, precision, recall, baseline_rate (majority-class rate
    on the test split -- the number the model must beat), a model_card, and
    the fitted model.
    """
    try:
        import numpy as np
        from scipy import sparse
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, precision_score, recall_score
    except ImportError as exc:  # pragma: no cover - env without the ml extra
        raise ImportError(
            "predict_monetary_relief needs scikit-learn: "
            "pip install 'proofquery[ml]'"
        ) from exc

    test_keys = {_month_key(m) for m in test_months}
    if not test_keys:
        raise ValueError("test_months must name at least one month")

    rows = con.sql(
        """
        SELECT
            coalesce(issue, '') || ' ' || coalesce(sub_issue, '') || ' '
                || coalesce(product, '')      AS text,
            company_norm,
            CASE WHEN company_response ILIKE '%monetary relief%'
                 THEN 1 ELSE 0 END            AS y,
            strftime(DATE_TRUNC('month', date_received), '%Y-%m') AS month
        FROM facts.complaints
        """
    ).fetchall()
    train_idx = [i for i, r in enumerate(rows) if r[3] not in test_keys]
    test_idx = [i for i, r in enumerate(rows) if r[3] in test_keys]
    if not train_idx or not test_idx:
        raise ValueError(
            f"test_months {sorted(test_keys)} must leave data on both sides "
            "of the split"
        )

    texts = [r[0] for r in rows]
    labels = np.array([r[2] for r in rows])

    # Top-20 companies by TRAINING volume only (no test leakage).
    train_companies = [rows[i][1] for i in train_idx]
    counts: dict = {}
    for c in train_companies:
        counts[c] = counts.get(c, 0) + 1
    top20 = [c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:20]]
    top20_pos = {c: j for j, c in enumerate(top20)}

    vectorizer = TfidfVectorizer(lowercase=True, token_pattern=r"[a-zA-Z0-9']+",
                                 sublinear_tf=True)
    X_text = vectorizer.fit_transform(texts)

    X_cat = sparse.lil_matrix((len(rows), len(top20)), dtype=np.float64)
    for i, r in enumerate(rows):
        j = top20_pos.get(r[1])
        if j is not None:
            X_cat[i, j] = 1.0

    X = sparse.hstack([X_text, X_cat.tocsr()], format="csr")
    model = LogisticRegression(max_iter=1000, C=1.0)
    model.fit(X[train_idx], labels[train_idx])
    preds = model.predict(X[test_idx])

    y_test = labels[test_idx]
    baseline_rate = float(max(y_test.mean(), 1.0 - y_test.mean()))
    train_months = sorted({_month_key(rows[i][3]) for i in train_idx})
    return {
        "accuracy": float(accuracy_score(y_test, preds)),
        "precision": float(precision_score(y_test, preds, zero_division=0)),
        "recall": float(recall_score(y_test, preds, zero_division=0)),
        "baseline_rate": baseline_rate,  # majority-class baseline, always shown
        "model_card": {
            "model": "LogisticRegression(C=1.0, max_iter=1000)",
            "features": [
                "tfidf(word-level) over issue + sub_issue + product",
                f"one-hot top-{len(top20)} company_norm by training volume",
            ],
            "label": "company_response LIKE '%monetary relief%'",
            "train_months": train_months,
            "test_months": sorted(test_keys),
            "n_train": len(train_idx),
            "n_test": len(test_idx),
            "n_features": int(X.shape[1]),
        },
        "model": model,
    }


def X_csr_like(m):
    """lil -> csr without importing scipy at module level."""
    return m.tocsr()
