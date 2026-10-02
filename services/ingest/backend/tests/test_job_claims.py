"""Job ownership across several workers (app/workers/tasks.py): the claim
token, the heartbeat-based staleness rule, the fenced result write, the
lost-job reaper and the profile-downgrade stop after repeated worker loss.

The task body runs directly (not via `.delay`) against the shared sqlite test
database, with the OCR conversion stubbed out.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.models.models import Job, JobStatus
from app.workers import tasks
from conftest import TestingSessionLocal, stored_upload


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stale() -> datetime:
    return _now() - timedelta(seconds=settings.job_stale_seconds + 10)


@pytest.fixture(autouse=True)
def _worker_env(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, 'SessionLocal', TestingSessionLocal)
    monkeypatch.setattr(settings, 'worker_tmp_dir', tmp_path / 'work')
    # Other modules leave RUNNING rows behind; the reaper would see them.
    with TestingSessionLocal() as db:
        db.query(Job).filter(Job.status == JobStatus.RUNNING).delete()
        db.commit()


@pytest.fixture
def events(monkeypatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        tasks.webhook_tasks, 'dispatch_job_event', lambda db, job, event: sent.append((job.id, event))
    )
    return sent


def _add_job(job_id: str, status: JobStatus, **fields) -> None:
    upload_object_id = stored_upload(b'%PDF-1.4 claim test')
    with TestingSessionLocal() as db:
        db.query(Job).filter(Job.id == job_id).delete()
        db.commit()  # release the write lock before stored_upload()
        db.add(Job(
            id=job_id,
            original_filename=f'{job_id}.pdf',
            upload_path=f'{job_id}.pdf',
            upload_object_id=upload_object_id,
            upload_mime_type='application/pdf',
            upload_size_bytes=19,
            status=status,
            processing_info={'settings': {'storage_folder': 'inbox', 'profile_id': fields.pop('profile_id', None)}},
            **fields,
        ))
        db.commit()


def _get(job_id: str) -> Job:
    with TestingSessionLocal() as db:
        job = db.get(Job, job_id)
        assert job is not None
        return job


def test_duplicate_delivery_of_a_live_job_is_a_no_op(monkeypatch, events) -> None:
    _add_job('claim-live', JobStatus.RUNNING, heartbeat_at=_now(), claim_token='other-worker')
    calls: list[str] = []
    monkeypatch.setattr(tasks, 'convert_to_markdown_with_details', lambda *a, **k: calls.append('x') or ('# x', {}))

    tasks.process_job('claim-live')

    assert calls == []
    job = _get('claim-live')
    assert job.status == JobStatus.RUNNING
    assert job.claim_token == 'other-worker'
    assert events == []


def test_stale_job_is_reclaimed_outside_any_transaction_and_finished(monkeypatch, events) -> None:
    _add_job('claim-stale', JobStatus.RUNNING, heartbeat_at=_stale(), claim_token='lost-worker')
    sessions = []

    def session_factory():
        session = TestingSessionLocal()
        sessions.append(session)
        return session

    monkeypatch.setattr(tasks, 'SessionLocal', session_factory)
    observed: dict = {}

    def convert(*args, **kwargs):
        # The task's own session must not sit in a transaction while OCR runs.
        observed['in_transaction'] = sessions[0].in_transaction()
        with TestingSessionLocal() as db:
            job = db.get(Job, 'claim-stale')
            observed['recovery_count'] = job.recovery_count
            observed['token'] = job.claim_token
        return '# fresh', {'page_count': 1}

    monkeypatch.setattr(tasks, 'convert_to_markdown_with_details', convert)

    tasks.process_job('claim-stale')

    assert observed['in_transaction'] is False
    assert observed['recovery_count'] == 1
    assert observed['token'] not in (None, 'lost-worker')
    job = _get('claim-stale')
    assert job.status == JobStatus.FINISHED
    assert job.result_markdown == '# fresh'
    assert job.recovery_count == 0
    assert ('claim-stale', 'job.finished') in events


def test_result_of_a_taken_over_attempt_is_discarded(monkeypatch, events) -> None:
    _add_job('claim-fenced', JobStatus.PENDING)

    def convert(*args, **kwargs):
        # Another worker reclaims the job while this attempt is still converting.
        with TestingSessionLocal() as db:
            db.execute(update(Job).where(Job.id == 'claim-fenced').values(claim_token='newer-attempt'))
            db.commit()
        return '# late result', {'page_count': 1}

    monkeypatch.setattr(tasks, 'convert_to_markdown_with_details', convert)

    tasks.process_job('claim-fenced')

    job = _get('claim-fenced')
    assert job.status == JobStatus.RUNNING
    assert job.claim_token == 'newer-attempt'
    assert job.result_markdown is None
    assert events == []


def test_failure_of_a_taken_over_attempt_does_not_fail_the_job(monkeypatch, events) -> None:
    _add_job('claim-fenced-fail', JobStatus.PENDING)

    def convert(*args, **kwargs):
        with TestingSessionLocal() as db:
            db.execute(update(Job).where(Job.id == 'claim-fenced-fail').values(claim_token='newer-attempt'))
            db.commit()
        raise RuntimeError('converter crashed')

    monkeypatch.setattr(tasks, 'convert_to_markdown_with_details', convert)

    tasks.process_job('claim-fenced-fail')

    job = _get('claim-fenced-fail')
    assert job.status == JobStatus.RUNNING
    assert job.error_message is None
    assert events == []


@pytest.mark.parametrize(('prior_recoveries', 'expected'), [(0, JobStatus.FINISHED), (1, JobStatus.FAILED)])
def test_repeated_worker_loss_on_a_heavy_profile_stops_for_manual_downgrade(
    monkeypatch, events, prior_recoveries, expected
) -> None:
    job_id = f'claim-downgrade-{prior_recoveries}'
    _add_job(job_id, JobStatus.RUNNING, heartbeat_at=_stale(), recovery_count=prior_recoveries)
    monkeypatch.setattr(tasks, 'convert_to_markdown_with_details', lambda *a, **k: ('# ok', {'page_count': 1}))

    tasks.process_job(job_id, 'ppocrv6_medium')

    job = _get(job_id)
    assert job.status == expected
    assert job.recovery_count == 0
    if expected == JobStatus.FAILED:
        assert job.processing_info['execution']['suggested_profile_id'] == 'ppocrv6_tiny'
        assert (job_id, 'job.failed') in events


def test_reaper_requeues_lost_jobs_and_stops_repeat_offenders(monkeypatch, events) -> None:
    _add_job('reap-lost', JobStatus.RUNNING, heartbeat_at=_stale(), claim_token='t1', profile_id='ppocrv6_tiny')
    _add_job('reap-live', JobStatus.RUNNING, heartbeat_at=_now(), claim_token='t2')
    _add_job(
        'reap-poison', JobStatus.RUNNING, heartbeat_at=_stale(), claim_token='t3',
        recovery_count=settings.job_max_recoveries,
    )
    delayed: list[tuple] = []
    monkeypatch.setattr(tasks.process_job, 'delay', lambda *args: delayed.append(args))

    assert tasks.reap_stale_running_jobs() == 1

    assert [entry[0] for entry in delayed] == ['reap-lost']
    assert delayed[0][1] == 'ppocrv6_tiny'
    lost = _get('reap-lost')
    assert lost.status == JobStatus.PENDING
    assert lost.recovery_count == 1
    assert lost.claim_token is None
    live = _get('reap-live')
    assert live.status == JobStatus.RUNNING
    assert live.claim_token == 't2'
    poison = _get('reap-poison')
    assert poison.status == JobStatus.FAILED
    assert poison.recovery_count == 0
    assert ('reap-poison', 'job.failed') in events


def test_active_running_job_ids_follow_the_heartbeat() -> None:
    _add_job('active-fresh', JobStatus.RUNNING, heartbeat_at=_now())
    _add_job('active-stale', JobStatus.RUNNING, heartbeat_at=_stale())
    _add_job('active-finished', JobStatus.FINISHED, heartbeat_at=_now())

    with TestingSessionLocal() as db:
        active = tasks.active_running_job_ids(db)

    assert 'active-fresh' in active
    assert 'active-stale' not in active
    assert 'active-finished' not in active


def test_scratch_dirs_of_dead_task_processes_are_purged(monkeypatch, tmp_path) -> None:
    import os

    root = tmp_path / 'work'
    monkeypatch.setattr(settings, 'worker_tmp_dir', root)
    dead = root / 'job-999999-abc'  # no such pid
    alive = root / f'job-{os.getpid()}-def'
    unrelated = root / 'keep-me'
    for path in (dead, alive, unrelated):
        path.mkdir(parents=True)

    tasks._purge_orphaned_task_dirs()

    assert not dead.exists()
    assert alive.exists()
    assert unrelated.exists()
