"""Automatic restart of Confluence import runs: the periodic stale-run reaper
(app/workers/tasks.py's reap_stale_import_runs), transient-failure deferral
and per-page retries in app/workers/import_tasks.py, and the continuation
send that must never fail a run. Reuses test_import_tasks' fake client and
DB helpers."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.models.models import ImportRun, ImportRunStatus
from app.services.confluence import ConfluenceError, is_transient
from app.services.safe_fetch import SafeFetchError
from app.workers import import_tasks, publication_tasks
from app.workers import tasks as worker_tasks
from app.workers.celery_app import celery_app
from app.workers.import_tasks import import_confluence
from conftest import TestingSessionLocal
from test_import_tasks import (  # noqa: F401 -- `sent`/`client_holder` are fixtures
    _db,
    _drain_continuations,
    _get_run,
    _make_owner,
    _make_run,
    _make_source,
    _three_page_tree,
    client_holder,
    sent,
)


def _stale() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=settings.import_stale_run_seconds * 2)


def _set_run(run_id: str, **values) -> None:
    db = _db()
    try:
        db.execute(update(ImportRun).where(ImportRun.id == run_id).values(**values))
        db.commit()
    finally:
        db.close()


def _drain_run(sent: list, run_id: str, max_rounds: int = 20) -> None:
    """Replay only this run's captured continuations (other test modules
    leave stale runs of their own in the shared DB, which the reaper picks
    up too)."""
    rounds = 0
    while True:
        mine = [entry for entry in sent if entry[0] == 'import_confluence' and entry[1][0] == run_id]
        if not mine:
            return
        sent.remove(mine[0])
        rounds += 1
        assert rounds <= max_rounds, 'continuation loop did not terminate'
        import_confluence(*mine[0][1])


@pytest.fixture(autouse=True)
def _no_stale_runs_left_behind():
    """Every test here leaves deliberately stale runs in the shared DB; a
    later module calling requeue_running_jobs_after_restart (test_api) would
    otherwise re-send them through the real broker."""
    yield
    db = _db()
    try:
        db.execute(
            update(ImportRun)
            .where(ImportRun.status.in_([ImportRunStatus.PENDING, ImportRunStatus.RUNNING]))
            .values(status=ImportRunStatus.CANCELLED)
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def sent_with_kwargs(monkeypatch):
    """Like `sent`, but keeps the send_task kwargs (countdown)."""
    monkeypatch.setattr(import_tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(worker_tasks, 'SessionLocal', TestingSessionLocal)
    captured: list[tuple[str, list, dict]] = []
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: captured.append((name, list(args or []), kw)))
    return captured


# --- classification -------------------------------------------------------------


def test_transient_classification() -> None:
    assert is_transient(ConfluenceError('x', status_code=429))
    assert is_transient(ConfluenceError('x', status_code=503))
    assert is_transient(ConfluenceError('x', transient=True))
    assert not is_transient(ConfluenceError('x', status_code=404))
    assert not is_transient(ConfluenceError('x'))
    assert not is_transient(ValueError('x'))
    assert SafeFetchError('request failed', transient=True).transient
    assert not SafeFetchError('blocked').transient


# --- reaper ---------------------------------------------------------------------


def test_reaper_requeues_stale_runs_and_skips_live_and_in_flight_ones(sent) -> None:
    owner = _make_owner()
    source = _make_source(owner.id)
    stale_running = _make_run(owner.id, source)
    _set_run(stale_running, status=ImportRunStatus.RUNNING, chunk_seq=2, updated_at=_stale())
    stale_pending = _make_run(owner.id, source)
    _set_run(stale_pending, status=ImportRunStatus.PENDING, chunk_seq=0, updated_at=_stale())
    live = _make_run(owner.id, source)
    _set_run(live, status=ImportRunStatus.RUNNING, chunk_seq=3, updated_at=datetime.now(timezone.utc))
    in_flight = _make_run(owner.id, source)
    _set_run(
        in_flight, status=ImportRunStatus.RUNNING, chunk_seq=2, updated_at=_stale(),
        state={'frontier': [], 'visited': {}, 'errors': [], 'requeued_at': datetime.now(timezone.utc).isoformat()},
    )

    mine = {stale_running, stale_pending, live, in_flight}

    def _my_sends() -> list[tuple[str, list]]:
        # Other test modules leave stale runs of their own in the shared DB;
        # only this test's runs are asserted on.
        return [entry for entry in sent if entry[1][0] in mine]

    assert worker_tasks.reap_stale_import_runs() >= 2

    assert sorted(_my_sends()) == sorted([('import_confluence', [stale_running, 1]), ('import_confluence', [stale_pending, 0])])
    requeued = _get_run(stale_running)
    assert requeued.status == ImportRunStatus.RUNNING
    assert requeued.state['requeued_at']
    assert 'recoveries' not in requeued.state  # the reaper never counts; the reclaim does
    # updated_at must NOT be refreshed: the replay relies on the run still
    # looking stale for import_confluence's lease reclaim.
    assert requeued.updated_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) - timedelta(
        seconds=settings.import_stale_run_seconds
    )
    assert 'requeued_at' not in _get_run(live).state
    assert _get_run(in_flight).state['requeued_at'] != requeued.state['requeued_at']

    # The runs just requeued are now "in flight" -- a second tick leaves them alone.
    sent.clear()
    worker_tasks.reap_stale_import_runs()
    assert _my_sends() == []


def test_reaper_skips_a_run_that_moved_under_it(sent, monkeypatch) -> None:
    # A slow-but-alive worker commits between the reaper's read and its
    # UPDATE: the guarded UPDATE must not clobber the newer state, and no
    # replay is sent for that run. Simulated by handing the reaper rows that
    # are one chunk BEHIND what the database holds.
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))
    _set_run(
        run_id, status=ImportRunStatus.RUNNING, chunk_seq=3, updated_at=_stale(),
        state={'frontier': [], 'visited': {'100': None}, 'errors': []},
    )

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    def _session_with_stale_reads():
        db = TestingSessionLocal()
        real_scalars = db.scalars

        def scalars(statement, *args, **kwargs):
            rows = real_scalars(statement, *args, **kwargs).all()
            for row in rows:
                if isinstance(row, ImportRun) and row.id == run_id:
                    db.expunge(row)
                    row.chunk_seq -= 1  # what the reaper "read" before the worker moved on
            return _Rows(rows)

        db.scalars = scalars
        return db

    monkeypatch.setattr(worker_tasks, 'SessionLocal', _session_with_stale_reads)
    sent.clear()
    worker_tasks.reap_stale_import_runs()

    assert [entry for entry in sent if entry[1][0] == run_id] == []
    run = _get_run(run_id)
    assert run.chunk_seq == 3
    assert run.state['visited'] == {'100': None}
    assert 'requeued_at' not in run.state


def test_reclaim_fails_a_run_beyond_max_consecutive_lost_workers(sent, client_holder) -> None:
    client_holder['client'] = _three_page_tree()
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))
    _set_run(
        run_id, status=ImportRunStatus.RUNNING, chunk_seq=1, updated_at=_stale(), error_message='boom (automatic restart pending)',
        state={'frontier': [['100', 0]], 'visited': {}, 'errors': [], 'recoveries': settings.import_max_recoveries},
    )

    assert worker_tasks.reap_stale_import_runs() >= 1
    _drain_run(sent, run_id)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FAILED
    assert f'worker was lost {settings.import_max_recoveries} times in a row' in run.error_message
    assert 'last error: boom' in run.error_message
    assert run.finished_at is not None


def test_lost_worker_budget_counts_consecutive_losses_only(sent, client_holder) -> None:
    client_holder['client'] = _three_page_tree()
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))
    _set_run(
        run_id, status=ImportRunStatus.RUNNING, chunk_seq=1, updated_at=_stale(),
        state={'frontier': [['100', 0]], 'visited': {}, 'errors': [], 'recoveries': settings.import_max_recoveries - 1},
    )

    assert worker_tasks.reap_stale_import_runs() >= 1
    _drain_run(sent, run_id)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FINISHED
    assert run.state['recoveries'] == 0  # the chunk got through -> budget reset


def test_reaper_replay_resumes_a_lost_run_to_completion(sent, client_holder) -> None:
    client_holder['client'] = _three_page_tree()
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))
    # The worker claimed chunk 0 (seq -> 1) and died before importing anything.
    _set_run(run_id, status=ImportRunStatus.RUNNING, chunk_seq=1, started_at=_stale(), updated_at=_stale())

    assert worker_tasks.reap_stale_import_runs() >= 1
    _drain_run(sent, run_id)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FINISHED
    assert run.pages_imported == 3
    assert run.state['recoveries'] == 0  # counted at the reclaim, reset once the chunk completed
    assert 'requeued_at' in run.state


def test_housekeeping_runs_both_reapers(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(worker_tasks, 'reap_stale_running_jobs', lambda: calls.append('jobs'))
    monkeypatch.setattr(worker_tasks, 'reap_stale_import_runs', lambda: calls.append('runs'))
    monkeypatch.setattr(publication_tasks.object_store, 'collect_garbage', lambda db: None)

    publication_tasks._housekeeping()

    assert calls == ['jobs', 'runs']


# --- transient setup failures ---------------------------------------------------


def test_transient_setup_failure_defers_the_chunk_with_backoff(sent_with_kwargs, monkeypatch) -> None:
    def _unavailable(**kwargs):
        raise ConfluenceError('Confluence API request returned HTTP 503', status_code=503)

    monkeypatch.setattr(import_tasks, 'create_client', _unavailable)
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))

    import_confluence(run_id, 0)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.RUNNING
    assert run.state['transient_retries'] == 1
    assert 'automatic retry in 60s' in run.error_message
    # Replayed with the CURRENT seq (an ordinary continuation, claimable
    # right away despite the fresh heartbeat) after the first backoff step.
    assert sent_with_kwargs == [('import_confluence', [run_id, 1], {'countdown': 60})]

    # Second failure doubles the backoff; once Confluence is back, the
    # replayed message proceeds and the run finishes.
    import_confluence(*sent_with_kwargs.pop()[1])
    assert _get_run(run_id).state['transient_retries'] == 2
    assert sent_with_kwargs[-1][2] == {'countdown': 120}
    monkeypatch.setattr(import_tasks, 'create_client', lambda **kwargs: _three_page_tree())
    name, args, _ = sent_with_kwargs.pop()
    import_confluence(*args)
    while sent_with_kwargs:
        name, args, _ = sent_with_kwargs.pop(0)
        if name == 'import_confluence':
            import_confluence(*args)
    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FINISHED
    assert run.pages_imported == 3
    assert run.error_message is None
    assert run.state['transient_retries'] == 0  # reset by the first successful page fetch


def test_transient_setup_failure_gives_up_after_max_recoveries(sent_with_kwargs, monkeypatch) -> None:
    def _unavailable(**kwargs):
        raise ConfluenceError('Confluence API request returned HTTP 429', status_code=429)

    monkeypatch.setattr(import_tasks, 'create_client', _unavailable)
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))
    _set_run(
        run_id,
        state={'frontier': [['100', 0]], 'visited': {}, 'errors': [], 'transient_retries': settings.import_max_recoveries},
    )

    import_confluence(run_id, 0)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FAILED
    assert 'gave up after' in run.error_message
    assert sent_with_kwargs == []


def test_non_transient_setup_failure_still_fails_immediately(sent, monkeypatch) -> None:
    def _broken(**kwargs):
        raise ConfluenceError('unknown auth type')

    monkeypatch.setattr(import_tasks, 'create_client', _broken)
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))

    import_confluence(run_id, 0)

    assert _get_run(run_id).status == ImportRunStatus.FAILED
    assert sent == []


# --- transient per-page failures ------------------------------------------------


def test_transient_page_failure_requeues_the_page_and_defers_the_chunk(sent_with_kwargs, client_holder) -> None:
    client = _three_page_tree()
    failures = {'101': 2}

    def _flaky(page_id: str) -> None:
        if failures.get(page_id, 0) > 0:
            failures[page_id] -= 1
            raise ConfluenceError('Confluence API request returned HTTP 429', status_code=429)

    client.on_fetch = _flaky
    client_holder['client'] = client
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))

    import_confluence(run_id, 0)
    # The chunk ended at the first transient failure with a BACKED-OFF
    # continuation (never an immediate re-pop of the same page).
    assert sent_with_kwargs[-1][2] == {'countdown': 60}
    mid = _get_run(run_id)
    assert mid.status == ImportRunStatus.RUNNING
    assert mid.state['frontier'][-1][0] == '101'
    assert 'automatic retry in 60s' in mid.error_message

    countdowns: list[int | None] = []
    while sent_with_kwargs:
        name, args, kwargs = sent_with_kwargs.pop(0)
        if name == 'import_confluence':
            countdowns.append(kwargs.get('countdown'))
            import_confluence(*args)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FINISHED
    assert run.pages_imported == 3
    assert run.pages_failed == 0
    assert run.state['page_retries'] == {'101': 2}
    assert run.state['errors'] == []
    assert run.error_message is None
    # Retried at the END of the frontier, each time after a backoff; the
    # successful import of 102 in between reset the deferral budget, so the
    # second deferral starts over at 60s.
    assert client.fetched == ['100', '101', '102', '101', '101']
    assert countdowns[:2] == [60, 60]


def test_transient_page_failure_counts_as_failed_after_the_retry_budget(sent, client_holder) -> None:
    client = _three_page_tree()

    def _always_down(page_id: str) -> None:
        if page_id == '102':
            raise ConfluenceError('Confluence API request returned HTTP 502', status_code=502)

    client.on_fetch = _always_down
    client_holder['client'] = client
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))

    import_confluence(run_id, 0)
    _drain_continuations(sent)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FINISHED
    assert run.pages_imported == 2
    assert run.pages_failed == 1
    assert run.state['page_retries'] == {'102': import_tasks._PAGE_TRANSIENT_RETRIES}
    assert [entry['page_id'] for entry in run.state['errors']] == ['102']
    assert client.fetched.count('102') == import_tasks._PAGE_TRANSIENT_RETRIES + 1


# --- lost continuation / retryable crash -----------------------------------------


def test_continuation_send_failure_leaves_the_run_running_for_the_reaper(sent, client_holder, monkeypatch) -> None:
    client_holder['client'] = _three_page_tree()
    monkeypatch.setattr(settings, 'import_chunk_pages', 1)
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))

    def _broker_down(name, args=None, **kw):
        raise RuntimeError('broker unreachable')

    monkeypatch.setattr(celery_app, 'send_task', _broker_down)
    import_confluence(run_id, 0)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.RUNNING  # never failed by a lost continuation
    assert run.pages_imported == 1
    assert run.state['frontier']

    # Once its heartbeat is stale the reaper replays it, and the run finishes.
    _set_run(run_id, updated_at=_stale())
    monkeypatch.setattr(celery_app, 'send_task', lambda name, args=None, **kw: sent.append((name, list(args or []))))
    assert worker_tasks.reap_stale_import_runs() >= 1
    _drain_run(sent, run_id)
    run = _get_run(run_id)
    assert run.status == ImportRunStatus.FINISHED
    assert run.pages_imported == 3


def test_retryable_crash_leaves_the_run_running_instead_of_failing_it(sent, client_holder, monkeypatch) -> None:
    client_holder['client'] = _three_page_tree()
    owner = _make_owner()
    run_id = _make_run(owner.id, _make_source(owner.id))

    def _db_gone(*args, **kwargs):
        raise OperationalError('COMMIT', {}, Exception('server closed the connection unexpectedly'))

    monkeypatch.setattr(import_tasks, '_finalize_run', _db_gone)
    before = _get_run(run_id)
    import_confluence(run_id, 0)

    run = _get_run(run_id)
    assert run.status == ImportRunStatus.RUNNING
    assert 'server closed the connection' in run.error_message
    assert run.error_message.endswith('(automatic restart pending)')
    # The note must not refresh the heartbeat the reaper waits for.
    assert run.updated_at == _get_run(run_id).updated_at


def test_local_os_errors_are_not_retryable() -> None:
    assert not import_tasks._is_retryable_failure(PermissionError('read-only tmp dir'))
    assert not import_tasks._is_retryable_failure(FileNotFoundError('gone'))
    assert import_tasks._is_retryable_failure(ConnectionResetError('peer reset'))
    assert import_tasks._is_retryable_failure(TimeoutError('timed out'))
    assert import_tasks._is_retryable_failure(ConfluenceError('x', status_code=503))
