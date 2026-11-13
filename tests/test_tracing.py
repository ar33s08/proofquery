"""Trace chain tests: append, verify, and tamper detection."""

import sqlite3

from proofquery.tracing import TraceStore


def _store(tmp_path):
    return TraceStore(tmp_path / "traces.sqlite3")


def test_append_get_roundtrip(tmp_path):
    store = _store(tmp_path)
    tid = store.append("why?", {"kind": "step", "n": 1})
    rec = store.get(tid)
    assert rec is not None
    assert rec["question"] == "why?"
    assert rec["payload"] == {"kind": "step", "n": 1}
    assert len(rec["hash"]) == 64
    assert store.get("nope") is None


def test_chain_verifies(tmp_path):
    store = _store(tmp_path)
    for i in range(5):
        store.append("q", {"i": i})
    assert store.verify() == []


def test_tampered_payload_detected(tmp_path):
    store = _store(tmp_path)
    for i in range(4):
        store.append("q", {"i": i})
    con = sqlite3.connect(store.db_path)
    con.execute(
        "UPDATE traces SET payload_json = '{\"i\":999}' "
        "WHERE rowid = (SELECT MIN(rowid) FROM traces) + 2"
    )
    con.commit()
    con.close()
    assert store.verify() == [2]


def test_tampered_hash_detected(tmp_path):
    store = _store(tmp_path)
    for i in range(3):
        store.append("q", {"i": i})
    con = sqlite3.connect(store.db_path)
    con.execute(
        "UPDATE traces SET hash = ? WHERE rowid = (SELECT MIN(rowid) FROM traces)",
        ["f" * 64],
    )
    con.commit()
    con.close()
    broken = store.verify()
    # Editing a hash breaks its own check AND the next row's prev_hash link;
    # the FIRST broken index is the tampered row.
    assert broken[0] == 0


def test_deleted_row_detected(tmp_path):
    store = _store(tmp_path)
    for i in range(4):
        store.append("q", {"i": i})
    con = sqlite3.connect(store.db_path)
    con.execute(
        "DELETE FROM traces WHERE rowid = (SELECT MIN(rowid) FROM traces) + 1"
    )
    con.commit()
    con.close()
    # remaining rows 1 and 2 (orig indices 0, 2, 3): the survivor after the
    # hole now has a prev_hash that no longer matches its predecessor.
    broken = store.verify()
    assert broken == [1]


def test_canonical_ordering(tmp_path):
    store = _store(tmp_path)
    a = store.append("q", {"b": 1, "a": 2})
    b = store.append("q", {"a": 2, "b": 1})
    # Different key insertion order, identical canonical payload -> distinct
    # rows (append-only) but identical stored canonical json, chain verifies.
    con = sqlite3.connect(store.db_path)
    pa, pb = con.execute(
        "SELECT payload_json FROM traces WHERE id IN (?, ?) ORDER BY rowid",
        [a, b],
    ).fetchall()
    con.close()
    assert pa[0] == pb[0] == '{"a":2,"b":1}'
    assert store.verify() == []
