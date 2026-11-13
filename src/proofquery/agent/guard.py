"""SQL guard: the last line of defense before any SQL touches DuckDB.

Business question it answers: "can a generated (or injected) query ever do
anything besides read the certified tables?" Three gates, all static:

1. single statement (semicolons inside the text are a hard reject);
2. read-only verbs only (SELECT / WITH ... SELECT), with a keyword blacklist
   for mutations, DDL, session/extension changes, and DuckDB file-reading
   table functions (read_csv, read_parquet, ...);
3. a table allow-list parsed from FROM/JOIN: only facts.complaints,
   facts.quarantine, marts.*, meta.dataset — plus WITH-defined CTE names.
"""

from __future__ import annotations

import re

# Verbs / statements that must never appear, even inside a subquery.
_FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "attach",
    "detach", "pragma", "copy", "install", "load", "set", "truncate",
    "vacuum", "grant", "revoke", "call", "execute", "prepare", "deallocate",
    "begin", "commit", "rollback", "checkpoint", "export", "import",
    "nextval", "unresolved_type",
)

# DuckDB table/scan functions that reach outside the warehouse.
_FILE_FUNCTIONS = (
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_text", "read_ndjson", "read_blob", "parquet_scan", "json_scan",
    "iceberg_scan", "delta_scan", "glob", "query", "duckdb_settings",
    "duckdb_tables", "duckdb_columns", "sqlite_scan", "postgres_scan",
    "mysql_scan",
)

# The certified surface. Anything else is out of schema.
_ALLOWED_TABLES = {
    "facts.complaints",
    "facts.quarantine",
    "meta.dataset",
}
_ALLOWED_PREFIXES = ("marts.",)

_START_RE = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
_FROM_JOIN_RE = re.compile(rf"\b(?:from|join)\s+({_IDENT})", re.IGNORECASE)
_CTE_RE = re.compile(rf"\b({_IDENT})\s+as\s*\(", re.IGNORECASE)
_COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)


def _strip_comments(sql: str) -> str:
    return _COMMENT_RE.sub(" ", sql)


def sql_guard(sql: str) -> tuple[bool, str | None]:
    """Return (ok, reason). ok=False means: never execute this SQL."""
    if not sql or not sql.strip():
        return False, "empty statement"

    text = _strip_comments(sql)
    stripped = text.strip().rstrip(";").strip()
    if not stripped:
        return False, "empty statement"

    # 1. Single statement only. (A trailing ';' was removed above.)
    if ";" in stripped:
        return False, "multiple statements are not allowed"

    # 2a. Read-only verbs to start.
    if not _START_RE.match(stripped):
        return False, "statement must start with SELECT or WITH"

    lowered = stripped.lower()

    # 2b. Blacklisted keywords, matched as whole words. The lookbehind
    # keeps schema-qualified names like meta.dataset from tripping "set".
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"(?<![\w.]){kw}(?![\w])", lowered):
            return False, f"forbidden keyword: {kw.upper()}"

    # 2c. File/system table functions.
    for fn in _FILE_FUNCTIONS:
        if re.search(rf"\b{re.escape(fn)}\s*\(", lowered):
            return False, f"file function not allowed: {fn}"

    # String literals are now the only place an attacker can hide tokens;
    # literals cannot create tables, so table parsing on the raw text is safe
    # except that a literal could spoof a FROM — strip literals before parse.
    no_literals = re.sub(r"'(?:[^']|'')*'", "''", stripped)

    # 3. Table allow-list from FROM/JOIN, with CTE names exempted.
    ctes = {c.lower() for c in _CTE_RE.findall(no_literals)}
    for m in _FROM_JOIN_RE.finditer(no_literals):
        name = m.group(1).lower()
        if name in ctes:
            continue
        if name in _ALLOWED_TABLES:
            continue
        if name.startswith(_ALLOWED_PREFIXES):
            continue
        return False, f"table not in allow-list: {name}"

    return True, None
