"""A failed ingestion attempt keeps what the worker printed (2026-11-03): the queue used to store only a hash."""
from ats.data import persistent_queue
from ats.data.persistent_queue import PersistentIngestionQueue


def _queue(tmp_path, monkeypatch):
    monkeypatch.setattr(persistent_queue, 'FAILURE_LOG_DIR', tmp_path / 'logs')
    return PersistentIngestionQueue(tmp_path / 'queue.sqlite')


def test_a_failed_attempt_keeps_stderr_in_the_task_and_in_a_log_file(tmp_path, monkeypatch):
    queue = _queue(tmp_path, monkeypatch)
    task_id, _ = queue.enqueue(source_id='s', scope={}, trigger_kind='manual', trigger_ref='t',
                               command=['ats', 'data', 'ingest', '--source', 'no_such_source_for_test'], policy_fingerprint='p',
                               requested_as_of='2026-11-03T00:00:00+00:00', priority=20)
    result = queue.run_one(task_id=task_id)
    assert result['error'].startswith('exit=')
    stored = queue.get(task_id)['result']
    assert stored['stderr_tail'].strip()
    log = (tmp_path / 'logs' / f'{task_id}-attempt1.log').read_text()
    assert '--- stderr ---' in log and stored['stderr_tail'][:50] in log
    assert stored['output_log'].endswith(f'{task_id}-attempt1.log')
