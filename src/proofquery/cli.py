"""`proofquery` console: build | quality | metrics | anomalies | ask | report | serve.

Business question this answers: "can an analyst reproduce every number from a
terminal in one command?" Everything routes through the same modules the HTTP
server uses, so CLI and dashboard can never disagree.

Sibling lanes (metrics, anomaly, agent) are imported lazily; while a lane is
still being built, the command prints "(lane pending)" and exits 0.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


def _print(obj: dict, as_json: bool, human_lines: list[str]) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        for line in human_lines:
            print(line)


def _lazy(module: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _metric_call(mod, name: str, con):
    for fn_name in ("get", "get_metric", "metric", "compute"):
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            try:
                return fn(con, name)  # lane contract: metrics.get(con, name)
            except TypeError:
                return fn(name, con)
    raise AttributeError("metrics lane exposes no get_metric()")


def _metric_names(mod) -> list[str]:
    listing = getattr(mod, "metric_names", None) or getattr(mod, "list_metrics", None)
    if callable(listing):
        return list(listing())
    for attr in ("METRICS", "REGISTRY"):
        if isinstance(getattr(mod, attr, None), dict):
            return list(getattr(mod, attr))
    return []


def cmd_build(args) -> int:
    from .warehouse import build

    csv_path = Path(args.csv) if args.csv else None
    report = build(csv_path)
    d = report.as_dict()
    _print(
        d,
        args.json,
        [
            f"staging rows : {d['staging_rows']}",
            f"fact rows    : {d['fact_rows']}",
            f"sha256       : {d['sha256'][:12]}...",
            f"quarantine   : {d['quarantine'] or 'clean'}",
            "marts        : " + ", ".join(f"{k}={v}" for k, v in d["marts"].items()),
        ],
    )
    return 0


def cmd_quality(args) -> int:
    from . import config
    from .quality import run_quality_suite

    con = config.connect(read_only=True)
    try:
        findings = run_quality_suite(con).as_dict()
    finally:
        con.close()
    lines = []
    for group in ("quarantine", "dq"):
        for code, info in findings[group].items():
            lines.append(f"{code} [{info['count']}] {info['label']}")
    if not lines:
        lines = ["no findings: warehouse is clean"]
    _print(findings, args.json, lines)
    return 0


def cmd_metrics(args) -> int:
    from . import config

    mod = _lazy("proofquery.metrics")
    if mod is None:
        _print({"status": "lane_pending", "module": "proofquery.metrics"},
               args.json, ["(lane pending)"])
        return 0
    con = config.connect(read_only=True)
    try:
        names = [args.name] if args.name else _metric_names(mod)
        out = {}
        for name in names:
            try:
                out[name] = _metric_call(mod, name, con)
            except Exception as exc:  # lane still settling; keep CLI friendly
                out[name] = {"error": f"{type(exc).__name__}: {exc}"}
        _print({"metrics": out}, args.json,
               [f"{k}: {json.dumps(v, default=str)}" for k, v in out.items()]
               or ["pass --name (metrics lane exposes no listing)"])
    finally:
        con.close()
    return 0


def cmd_anomalies(args) -> int:
    from . import config

    mod = _lazy("proofquery.anomaly")
    if mod is None:
        _print({"status": "lane_pending", "module": "proofquery.anomaly"},
               args.json, ["(lane pending)"])
        return 0
    con = config.connect(read_only=True)
    try:
        fn = (getattr(mod, "detect_volume_anomalies", None)
              or getattr(mod, "detect_anomalies", None) or getattr(mod, "detect", None)
              or getattr(mod, "get_anomalies", None))
        if fn is None:
            _print({"status": "lane_pending", "module": "proofquery.anomaly"},
                   args.json, ["(lane pending)"])
            return 0
        try:
            result = fn(con, method=args.method)
        except TypeError:
            result = fn(con, args.method)
        payload = result if isinstance(result, (dict, list)) else vars(result)
        _print({"method": args.method, "anomalies": payload}, args.json,
               [json.dumps(payload, indent=2, default=str)])
    finally:
        con.close()
    return 0


def cmd_ask(args) -> int:
    mod = _lazy("proofquery.agent")
    if mod is None:
        _print({"status": "lane_pending", "module": "proofquery.agent"},
               args.json, ["(lane pending)"])
        return 0
    result = mod.run(args.question, provider=args.provider)
    import dataclasses

    d = (dataclasses.asdict(result) if dataclasses.is_dataclass(result)
         else result if isinstance(result, dict)
         else {f: getattr(result, f, None) for f in
               ("answer", "sql", "rows", "trace_id", "refused", "refusal_reason")})
    _print(d, args.json, [d.get("answer", "")] + [s for s in d.get("sql") or []])
    return 0


def cmd_report(args) -> int:
    from . import config
    from .report import weekly_analyst_memo

    con = config.connect(read_only=True)
    try:
        md = weekly_analyst_memo(con)
    finally:
        con.close()
    _print({"markdown": md}, args.json, [md])
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .server import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proofquery")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("build", parents=[common], help="rebuild facts + marts from the CSV")
    p.add_argument("csv", nargs="?", help="optional CSV path (default: data/raw/cfpb_complaints.csv)")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("quality", parents=[common], help="quality findings (Q/DQ codes)")
    p.set_defaults(fn=cmd_quality)

    p = sub.add_parser("metrics", parents=[common], help="governed metrics")
    p.add_argument("--name", help="one metric name (default: all the lane lists)")
    p.set_defaults(fn=cmd_metrics)

    p = sub.add_parser("anomalies", parents=[common], help="anomalies in daily volume")
    p.add_argument("--method", choices=("ewma", "zscore"), default="ewma")
    p.set_defaults(fn=cmd_anomalies)

    p = sub.add_parser("ask", parents=[common], help="ask a question in plain English")
    p.add_argument("question")
    p.add_argument("--provider", default="double")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("report", parents=[common], help="weekly analyst memo (markdown)")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("serve", parents=[common], help="run the FastAPI service")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
