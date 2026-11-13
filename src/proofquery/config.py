"""Paths and constants. Everything else derives from here."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PROOFQUERY_DATA_DIR", REPO_ROOT / "data"))
RAW_CSV = DATA_DIR / "raw" / "cfpb_complaints.csv"
DB_PATH = Path(os.environ.get("PROOFQUERY_DB", DATA_DIR / "proofquery.duckdb"))
SQL_DIR = Path(__file__).resolve().parent / "sql"

SOURCE_URL = (
    "https://www.consumerfinance.gov/data-research/consumer-complaints/"
    "search/api/v1/"
)
SOURCE_LICENSE = "CC0 1.0 (Consumer Financial Protection Bureau)"


def sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """One connection factory for the whole repo.

    read_only=True is what the analyst agent and the dashboard use; the
    agent's read-only guard is enforced at this layer, not by convention.
    """
    return duckdb.connect(str(DB_PATH), read_only=read_only)
