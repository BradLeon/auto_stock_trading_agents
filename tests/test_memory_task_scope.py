import pytest


def test_task_store_scope_uses_and_closes_a_distinct_connection(tmp_path, monkeypatch):
    db = tmp_path / "workflow.sqlite"
    data_db = tmp_path / "data.sqlite"
    monkeypatch.setenv("ATS_DB_PATH", str(db))
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(data_db))

    from ats.memory import get_store, reset_store_cache, task_store_scope

    reset_store_cache()
    cached = get_store()  # one explicit bootstrap/migration owner
    with task_store_scope() as task_store:
        assert task_store is get_store()
        assert task_store is not cached
        assert task_store.conn is not cached.conn
        task_store.conn.execute("SELECT 1").fetchone()
    assert task_store is not get_store()
    with pytest.raises(Exception):
        task_store.conn.execute("SELECT 1")

    # Avoid retaining the test-only connection in the module's path cache.
    cached.close()
    reset_store_cache()


def test_task_store_scope_rejects_isolated_memory_database():
    from ats.memory import task_store_scope

    with pytest.raises(ValueError, match="file-backed"):
        with task_store_scope(":memory:"):
            pass
