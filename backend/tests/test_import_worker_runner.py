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

    @contextmanager
    def session():
        db = object()
        sessions.append(db)
        yield db

    def process(name, fail=False):
        def run(db):
            calls.append(name)
            if fail:
                raise RuntimeError('queue unavailable')
            return []
        return run

    monkeypatch.setattr(worker, 'SessionLocal', session)
    names = ['process_queued_material_imports', 'process_queued_supplier_imports',
             'process_supplier_jobs', 'process_pricing_jobs', 'process_rfq_import_jobs']
    for i, name in enumerate(names):
        monkeypatch.setattr(worker, name, process(name, fail=i in (0, 4)))
    assert worker.process_once() is False
    assert calls == names
    assert len({id(db) for db in sessions}) == 5


def test_duplicate_dedicated_worker_exits_without_polling(monkeypatch, tmp_path):
    path = tmp_path / 'worker.lock'
    monkeypatch.setattr(worker, 'import_worker_lock', lambda: import_worker_lock(path))
    monkeypatch.setattr('sys.argv', ['import-worker', '--once'])
    monkeypatch.setattr(worker, 'process_once', lambda: pytest.fail('duplicate worker polled'))
    with import_worker_lock(path) as acquired:
        assert acquired
        with pytest.raises(SystemExit) as exc:
            worker.main()
        assert exc.value.code == 75
