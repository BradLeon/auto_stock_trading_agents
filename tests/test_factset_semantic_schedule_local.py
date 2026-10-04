from ats.runtime import scheduler


def test_factset_semantic_schedule_is_opt_in_idempotent_and_reversible(monkeypatch):
    calls = []
    seen = set()

    class Queue:
        def enqueue(self, **kwargs):
            key = (kwargs['trigger_ref'], kwargs['policy_fingerprint'])
            created = key not in seen
            seen.add(key)
            calls.append((kwargs, created))
            return f'task-{len(seen)}', created

        def run_one(self, **_kwargs):
            return None

        def get(self, _task_id):
            return {'status': 'queued'}

    monkeypatch.setattr('ats.data.persistent_queue.PersistentIngestionQueue', Queue)
    monkeypatch.setenv('ATS_FACTSET_SCHEDULE_SEMANTIC', '1')
    scheduler._factset_weekly_ingest()
    scheduler._factset_weekly_ingest()
    assert [created for _, created in calls] == [True, False]
    assert calls[0][0]['command'][2] == 'factset-semantic-refresh'
    monkeypatch.delenv('ATS_FACTSET_SCHEDULE_SEMANTIC')
    scheduler._factset_weekly_ingest()
    assert calls[-1][1]
    assert calls[-1][0]['command'][2] == 'factset-refresh'
