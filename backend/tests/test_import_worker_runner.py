from contextlib import contextmanager
import pytest
from app.import_worker_lock import import_worker_lock
from app import material_import_worker as worker


def test_worker_lock_prevents_duplicate_pollers_and_releases_after_error(tmp_path):
    path = tmp_path / 'worker.lock'
    with pytest.raises(RuntimeError):
        with import_worker_lock(path) as first:
            assert first
            with import_worker_lock(path) as second:
                assert not second
            raise RuntimeError('worker interrupted')
    with import_worker_lock(path) as restarted:
        assert restarted


def test_one_queue_failure_does_not_starve_other_queues(monkeypatch):
    calls = []
    sessions = []

    class PollSession:
        def __init__(self):
            self.closed=False
            self.rollbacks=0
            sessions.append(self)
        def rollback(self):self.rollbacks+=1
        def in_transaction(self):return True
        def close(self):self.closed=True

    def process(name, fail=False):
        def run(db, **kwargs):
            calls.append(f"{name}:{kwargs['lane']}" if kwargs.get('lane') else name)
            if fail:
                raise RuntimeError('queue unavailable')
            return []
        return run

    monkeypatch.setattr(worker, 'SessionLocal', PollSession)
    names = ['process_outbox_batch', 'process_pricing_jobs', 'process_rfq_import_jobs',
             'process_queued_material_imports', 'process_queued_supplier_imports', 'process_supplier_jobs']
    for i, name in enumerate(names):
        monkeypatch.setattr(worker, name, process(name, fail=i in (0, 4)))
    assert worker.process_once() is False
    assert [name for name in calls if not name.startswith('process_outbox_batch')] == names[1:]
    assert calls.count('process_outbox_batch') == 1
    assert calls.count('process_outbox_batch:matching') == 6  # Import preemption.
    assert len({id(db) for db in sessions}) == len(calls)
    assert all(db.closed and db.rollbacks>=1 for db in sessions)


def test_duplicate_dedicated_worker_exits_without_polling(monkeypatch, tmp_path):
    path = tmp_path / 'worker.lock'
    monkeypatch.setattr(worker, 'import_worker_lock', lambda _lane_path: import_worker_lock(path))
    monkeypatch.setattr('sys.argv', ['import-worker', '--once'])
    monkeypatch.setattr(worker, 'process_once', lambda: pytest.fail('duplicate worker polled'))
    with import_worker_lock(path) as acquired:
        assert acquired
        with pytest.raises(SystemExit) as exc:
            worker.main()
        assert exc.value.code == 75
