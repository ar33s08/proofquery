"""CLI tests: build, quality, metrics (+ anomalies/report) against a scratch DB.

Same pattern as test_server: tiny CSV in tmp_path, PROOFQUERY_DB pointed there,
exit code 0 and parseable --json output required. Parallel-lane modules
(metrics/anomaly/agent) may be absent; the CLI must still exit 0 and, with
--json, print valid JSON (status=lane_pending).
"""

from __future__ import annotations

import importlib
import json
import os

import pytest
from test_server import write_tiny_csv  # shared fixture builder, same tiny slice


@pytest.fixture(scope="module")
def cli_db(tmp_path_factory):
    from proofquery import config, warehouse

    tmp = tmp_path_factory.mktemp("clidb")
    csv = write_tiny_csv(tmp)
    old = os.environ.get("PROOFQUERY_DB")
    os.environ["PROOFQUERY_DB"] = str(tmp / "cli.duckdb")
    importlib.reload(config)
    try:
        warehouse.build(csv)
        yield csv
    finally:
        if old is None:
            os.environ.pop("PROOFQUERY_DB", None)
        else:
            os.environ["PROOFQUERY_DB"] = old
        importlib.reload(config)


def run(argv):
    from proofquery.cli import main

    return main(argv)


def test_cli_build_exit0_and_json(cli_db, capsys):
    assert run(["build", str(cli_db), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["staging_rows"] == 10
    assert out["fact_rows"] == 8
    assert out["sha256"]
    assert "complaints_daily" in out["marts"]


def test_cli_quality_finds_quarantine_codes(cli_db, capsys):
    assert run(["quality", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["quarantine"]["Q001"]["count"] == 1
    assert out["quarantine"]["Q002"]["count"] == 1
    assert out["dq"]["DQ006"]["count"] >= 1  # blank zip on row 6


def test_cli_metrics_json_always_parseable(cli_db, capsys):
    assert run(["metrics", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("status") == "lane_pending" or "metrics" in out
    assert run(["metrics", "--name", "volume", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("status") == "lane_pending" or "metrics" in out


def test_cli_anomalies_json_always_parseable(cli_db, capsys):
    assert run(["anomalies", "--method", "zscore", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("status") == "lane_pending" or out.get("method") == "zscore"


def test_cli_report_markdown(cli_db, capsys):
    assert run(["report", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    md = out["markdown"]
    assert "weekly analyst memo" in md
    assert "Provenance" in md
    assert "mover" in md.lower()
    assert "Anomalies" in md  # section exists even while the lane is pending
