"""Dedicated, permission-safe Collection registry notifications."""

from app.core.config import settings
from app.workers import publication_tasks


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', 'https://knowledge.example')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', 'shared-secret')
    monkeypatch.setattr(settings, 'webhook_private_host_allowlist', [])
    monkeypatch.setattr(settings, 'publication_max_attempts', 5)


def test_notification_uses_internal_endpoint_and_never_sends_acl(monkeypatch) -> None:
    _configure(monkeypatch)
    captured = {}

    def send(url, payload, secret, allowed_hosts):
        captured.update(url=url, payload=payload, secret=secret, allowed_hosts=allowed_hosts)
        return 204, None

    monkeypatch.setattr(publication_tasks, 'send_webhook_request', send)
    publication_tasks.notify_collection_registry_changed.run('legal-guides')

    assert captured['url'] == 'https://knowledge.example/api/v1/events/ingest'
    assert captured['secret'] == 'shared-secret'
    assert captured['payload']['event'] == 'collection.updated'
    assert captured['payload']['slug'] == 'legal-guides'
    assert 'read_teams' not in captured['payload']
    assert 'name' not in captured['payload']


def test_retryable_notification_failure_reenqueues_with_backoff(monkeypatch) -> None:
    _configure(monkeypatch)
    calls = []
    monkeypatch.setattr(publication_tasks, 'send_webhook_request', lambda *args: (503, 'temporary'))
    monkeypatch.setattr(
        publication_tasks.celery_app,
        'send_task',
        lambda name, **kwargs: calls.append((name, kwargs)),
    )

    publication_tasks.notify_collection_registry_changed.run('legal-guides')

    assert calls == [
        (
            publication_tasks.COLLECTION_NOTIFICATION_TASK_NAME,
            {'args': ['legal-guides', 1], 'countdown': 30},
        )
    ]


def test_unconfigured_notification_stops_without_network(monkeypatch) -> None:
    monkeypatch.setattr(settings, 'portal_knowledge_base_url', '')
    monkeypatch.setattr(settings, 'portal_knowledge_webhook_secret', '')
    sent = []
    monkeypatch.setattr(publication_tasks, 'send_webhook_request', lambda *args: sent.append(args))

    publication_tasks.notify_collection_registry_changed.run('legal-guides')

    assert sent == []
