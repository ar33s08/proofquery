"""Guard tests: nothing but allow-listed reads ever reaches DuckDB."""

from proofquery.agent.guard import sql_guard


def test_select_one_ok():
    assert sql_guard("SELECT 1") == (True, None)


def test_stack_semigang_dropped():
    ok, reason = sql_guard("SELECT 1; DROP TABLE facts.complaints")
    assert not ok
    assert reason


def test_read_csv_file_function_rejected():
    ok, reason = sql_guard(
        "SELECT * FROM read_csv('/Users/me/secrets.csv')")
    assert not ok and reason
    assert "read_csv" in reason


def test_read_parquet_rejected():
    assert not sql_guard("WITH x AS (SELECT 1) SELECT * FROM read_parquet('a')")[0]


def test_writes_rejected():
    for sql in (
        "DELETE FROM facts.complaints",
        "UPDATE facts.complaints SET timely = TRUE",
        "INSERT INTO facts.complaints VALUES (1)",
        "DROP TABLE facts.complaints",
        "ATTACH 'x.db' AS x",
        "PRAGMA database_list",
        "COPY facts.complaints TO '/tmp/x.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET memory_limit='1GB'",
    ):
        ok, reason = sql_guard(sql)
        assert not ok, sql
        assert reason


def test_allow_list_accepts_certified_tables():
    for sql in (
        "SELECT * FROM facts.complaints",
        "SELECT * FROM facts.quarantine",
        "SELECT * FROM marts.complaints_daily",
        "SELECT * FROM marts.product_monthly WHERE complaints > 5",
        "SELECT * FROM meta.dataset",
        "WITH x AS (SELECT day FROM marts.complaints_daily) SELECT * FROM x",
        "SELECT a.product FROM facts.complaints a JOIN marts.product_monthly b "
        "ON a.product = b.product",
    ):
        ok, reason = sql_guard(sql)
        assert ok, (sql, reason)


def test_allow_list_rejects_off_schema_tables():
    for sql in (
        "SELECT * FROM secrets.table",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM staging.complaints_raw",
        "SELECT * FROM information_schema.tables",
    ):
        assert not sql_guard(sql)[0], sql


def test_meta_dataset_not_tripped_by_set_keyword():
    # regression: 'set' inside 'meta.dataset' must not look like SET
    assert sql_guard("SELECT sha256 FROM meta.dataset")[0]


def test_comment_smuggling_rejected():
    assert not sql_guard("SELECT 1 -- x\n; DROP TABLE facts.complaints")[0]


def test_empty_rejected():
    assert not sql_guard("")[0]
    assert not sql_guard("   ;  ")[0]
