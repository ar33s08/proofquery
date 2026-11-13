"""HTTP surface for ProofQuery: health, governed metrics, anomalies, NL ask, dashboard.

Business question this answers: "can a hiring manager poke at the numbers
without opening a terminal?" Every response is served from a read-only DuckDB
connection (config.connect(read_only=True)) so no HTTP request can ever mutate
the evidence-locked warehouse.

Endpoints
    GET  /healthz           status, version, row_count + provenance from meta.dataset
    GET  /metrics/{name}    governed metric (404 unknown name, 503 lane pending)
    GET  /anomalies         ?method=ewma|zscore
    POST /ask               ?provider=double -> proofquery.agent.run -> AgentResult
    GET  /dashboard         the self-contained dashboards/dashboard.html
"""

from __future__ import annotations

import dataclasses
import importlib
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from . import __version__, config

app = FastAPI(
    title="ProofQuery",
    description="Evidence-locked analytics over the CFPB complaints warehouse.",
    version=__version__,
)

DASHBOARD_PATH = config.DASHBOARD_HTML

# Names the dashboard fetches. The metrics lane owns the implementations and a
# REGISTRY dict; that registry is authoritative when present. This list is the
# offline fallback for deciding what is NOT a 404.
KNOWN_METRICS = (
    "total_complaints",
    "timely_rate",
    "monetary_relief_rate",
    "avg_days_to_company",
    "monthly_volume_trend",
    "top_companies",
    "product_mix",
    "response_rate_by_company",
)


def get_con():
    """One read-only DuckDB connection per request, always closed in finally."""
    con = config.connect(read_only=True)
    try:
        yield con
    finally:
        con.close()


def _lazy(module: str):
    """Import a parallel-lane module if it exists yet; None while pending."""
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _first_callable(mod, names):
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return fn
    return None


@app.get("/healthz")
def healthz(con=Depends(get_con)):
    """Liveness + provenance: which pinned bytes produced these numbers."""
    row = con.execute(
        "SELECT COALESCE(MAX(row_count), 0), MAX(sha256), MAX(fetched_at) "
        "FROM meta.dataset"
    ).fetchone()
    return {
        "status": "ok",
        "version": __version__,
        "row_count": int(row[0] or 0),
        "source_sha256": (row[1] or "")[:12],
        "fetched_at": row[2].isoformat() if row[2] else None,
    }


@app.get("/metrics/{name}")
def metric(name: str, con=Depends(get_con)):
    """A governed metric. Docstring-per-metric lives in proofquery.metrics."""
    known = KNOWN_METRICS
    mod = _lazy("proofquery.metrics")
    if mod is not None:
        listing = _first_callable(mod, ("metric_names", "list_metrics"))
        if listing:
            known = tuple(listing())
        elif isinstance(getattr(mod, "METRICS", None), dict):
            known = tuple(mod.METRICS)
        elif isinstance(getattr(mod, "REGISTRY", None), dict):
            known = tuple(mod.REGISTRY)
    if name not in known:
        raise HTTPException(status_code=404, detail=f"unknown metric: {name}")
    if mod is None:
        raise HTTPException(status_code=503, detail="metrics lane pending")
    fn = _first_callable(mod, ("get", "get_metric", "metric", "compute"))
    if fn is None:
        raise HTTPException(status_code=503, detail="metrics lane exposes no get_metric()")
    try:
        value = _call_metric(fn, name, con)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"unknown metric: {name}") from exc
    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)
    return {"name": name, "value": value}


def _call_metric(fn, name: str, con):
    """Lane contract 2026-11-12: metrics.get(con, name). Older drafts took
    (name, con); try that order only if the canonical one raises TypeError."""
    try:
        return fn(con, name)
    except TypeError:
        return fn(name, con)


@app.get("/anomalies")
def anomalies(
    method: Literal["ewma", "zscore"] = Query("ewma"),
    con=Depends(get_con),
):
    """Anomalies in daily volume: has the stream gone weird?"""
    mod = _lazy("proofquery.anomaly")
    if mod is None:
        raise HTTPException(status_code=503, detail="anomaly lane pending")
    fn = _first_callable(
        mod, ("detect_volume_anomalies", "detect_anomalies", "detect", "get_anomalies"))
    if fn is None:
        raise HTTPException(status_code=503, detail="anomaly lane exposes no detect()")
    try:
        result = fn(con, method=method)
    except TypeError:
        result = fn(con, method)
    if isinstance(result, dict):
        items = result.get("anomalies", [result])
    else:
        items = result
    if dataclasses.is_dataclass(items):
        items = dataclasses.asdict(items)
    return {"method": method, "anomalies": items}


class _NoBody:  # placeholder to keep the signature below honest
    pass


@app.post("/ask")
def ask(
    request_payload: dict,
    provider: str = Query("double"),
):
    """Natural-language question -> agent -> AgentResult (read-only by design).

    "double" is the deterministic offline pattern-matcher; it refuses anything
    not answerable from facts/marts, and refuses writes.
    """
    question = str(request_payload.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=422, detail="{'question': '...'} required")
    mod = _lazy("proofquery.agent")
    if mod is None:
        raise HTTPException(status_code=503, detail="agent lane pending")
    result = mod.run(question, provider=provider)
    if dataclasses.is_dataclass(result):
        return dataclasses.asdict(result)
    if isinstance(result, dict):
        return result
    return {f: getattr(result, f, None) for f in
            ("answer", "sql", "rows", "trace_id", "refused", "refusal_reason")}


@app.get("/dashboard")
def dashboard():
    """The one-page analyst dashboard (self-contained HTML, no CDN)."""
    if not DASHBOARD_PATH.exists():
        raise HTTPException(status_code=501, detail="dashboard file missing")
    return FileResponse(DASHBOARD_PATH, media_type="text/html")
