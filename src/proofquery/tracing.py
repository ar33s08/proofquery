"""Evidence-locked run traces: an append-only, hash-chained sqlite log.

Every agent step is committed to `traces` with
``hash = sha256(prev_hash + canonical_json(payload))`` so that any edit to a
past row (payload, ordering, or omission) breaks the chain and is reported by
``verify()``. This is the differentiator: answers can be audited back to the
exact SQL and rows that produced them, and the audit trail itself is
tamper-evident.

Canonical JSON: ``json.dumps(payload, sort_keys=True, separators=(',', ':'))``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


class TraceStore:
    """Append-only hash-chained trace store (sqlite).

    Business question it answers: "prove that this answer came from these
    exact rows and queries, and that nobody edited the log since."
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    id         TEXT PRIMARY KEY,
                    ts         TEXT NOT NULL,
                    question   TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash  TEXT NOT NULL,
                    hash       TEXT NOT NULL
                )
                """
            )

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    def append(self, question: str, payload: dict) -> str:
        """Append one trace; returns its trace id."""
        canonical = canonical_json(payload)
        with self._conn() as con:
            row = con.execute(
                "SELECT hash FROM traces ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            prev_hash = row[0] if row else GENESIS
            trace_id = uuid.uuid4().hex
            con.execute(
                "INSERT INTO traces VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    datetime.now(timezone.utc).isoformat(),
                    question,
                    canonical,
                    prev_hash,
                    _hash(prev_hash, canonical),
                ),
            )
        return trace_id

    def verify(self) -> list[int]:
        """Return indices (0-based, insertion order) of rows that break the chain."""
        broken: list[int] = []
        with self._conn() as con:
            rows = con.execute(
                "SELECT payload_json, prev_hash, hash FROM traces ORDER BY rowid"
            ).fetchall()
        prev_hash = GENESIS
        for i, (payload_json, stored_prev, stored_hash) in enumerate(rows):
            if stored_prev != prev_hash or _hash(prev_hash, payload_json) != stored_hash:
                broken.append(i)
            # Continue from the *stored* hash so exactly one index is reported
            # per tampered row rather than flagging the entire tail.
            prev_hash = stored_hash
        return broken

    def get(self, trace_id: str):
        """Return {id, ts, question, prev_hash, hash, payload} or None."""
        with self._conn() as con:
            row = con.execute(
                "SELECT id, ts, question, payload_json, prev_hash, hash "
                "FROM traces WHERE id = ?",
                [trace_id],
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "ts": row[1],
            "question": row[2],
            "payload": json.loads(row[3]),
            "prev_hash": row[4],
            "hash": row[5],
        }
