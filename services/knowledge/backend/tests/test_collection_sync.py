"""Tests for app/services/collection_sync.py: registry upsert (create +
update-in-place), removal of a collection that vanished upstream, and error
classification (CollectionSyncError on a transport/status/shape failure).

httpx.get is mocked at the app.services.collection_sync seam, same
convention as tests/test_ingest_client.py mocking app.services.ingest_client.
httpx.get -- no real Weave-Ingest HTTP call happens anywhere here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.models.models import Collection
from app.services.collection_sync import CollectionSyncError, sync_collections
from tests.conftest import TestingSessionLocal


class _FakeResponse:
    def __init__(self, status_code: int, json_body=None) -> None:
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        if isinstance(self._json_body, Exception):
            raise self._json_body
        return self._json_body


def _db():
    return TestingSessionLocal()


def _cleanup():
    db = _db()
    try:
        db.query(Collection).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _around():
    _cleanup()
    yield
    _cleanup()


# --- URL construction -------------------------------------------------------------


def test_sync_requests_the_registry_endpoint_under_configured_base_url(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    monkeypatch.setattr(settings, 'weave_ingest_api_token', 'svc-token')

    with patch(
        'app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])
    ) as mock_get:
        db = _db()
        try:
            sync_collections(db)
        finally:
            db.close()

    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert args[0] == 'https://weave.local/api/v1/collections/registry'
    assert kwargs['headers']['Authorization'] == 'Bearer svc-token'
    assert kwargs['follow_redirects'] is False


# --- Canonical response envelope ({'items': [...]}) -----------------------------
#
# Weave-Ingest's actual GET /api/v1/collections/registry response is NOT a
# bare JSON list -- it is `CollectionRegistryResponse` (see its
# backend/app/schemas/jobs.py), which wraps a list of
# `CollectionRegistryEntry` rows under an `items` key, exactly like every
# other list-returning endpoint over there (CollectionListResponse,
# JobListResponse, ...). The payload below is copied field-for-field off
# those two pydantic models, not reconstructed from memory:
#
#   class CollectionRegistryEntry(BaseModel):
#       slug: str
#       name: str
#       description: str | None = None
#       read_teams: list[str] = Field(default_factory=list)
#
#   class CollectionRegistryResponse(BaseModel):
#       items: list[CollectionRegistryEntry] = Field(default_factory=list)


def test_sync_accepts_the_canonical_items_envelope(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    payload = {
        'items': [
            {'slug': 'handbuch', 'name': 'Handbuch', 'description': 'Interne Doku', 'read_teams': ['support']},
            {'slug': 'public', 'name': 'Public', 'description': None, 'read_teams': []},
        ]
    }

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        db = _db()
        try:
            count = sync_collections(db)
        finally:
            db.close()

    assert count == 2
    db = _db()
    try:
        handbuch = db.get(Collection, 'handbuch')
        assert handbuch is not None
        assert handbuch.name == 'Handbuch'
        assert handbuch.read_teams == ['support']
        assert db.get(Collection, 'public') is not None
    finally:
        db.close()


def test_sync_accepts_an_empty_items_envelope(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    db = _db()
    try:
        db.add(Collection(slug='stale', name='Stale'))
        db.commit()
    finally:
        db.close()

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, {'items': []})):
        db = _db()
        try:
            count = sync_collections(db)
        finally:
            db.close()

    assert count == 0
    db = _db()
    try:
        assert db.query(Collection).count() == 0
    finally:
        db.close()


def test_sync_rejects_an_object_payload_without_an_items_list(monkeypatch):
    """An object payload is only valid in the canonical `{'items': [...]}`
    shape -- an object missing (or misshaping) `items` is still a malformed
    response, not silently treated as an empty registry."""
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, {'not': 'items'})):
        db = _db()
        try:
            with pytest.raises(CollectionSyncError):
                sync_collections(db)
        finally:
            db.close()


# --- Upsert behavior ----------------------------------------------------------------


def test_sync_creates_new_collections(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    payload = [
        {'slug': 'handbuch', 'name': 'Handbuch', 'description': 'Interne Doku', 'read_teams': ['support']},
        {'slug': 'public', 'name': 'Public', 'description': None, 'read_teams': []},
    ]

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        db = _db()
        try:
            count = sync_collections(db)
        finally:
            db.close()

    assert count == 2

    db = _db()
    try:
        handbuch = db.get(Collection, 'handbuch')
        assert handbuch is not None
        assert handbuch.name == 'Handbuch'
        assert handbuch.description == 'Interne Doku'
        assert handbuch.read_teams == ['support']
        assert handbuch.synced_at is not None

        public = db.get(Collection, 'public')
        assert public is not None
        assert public.read_teams == []
    finally:
        db.close()


def test_sync_updates_an_existing_collection_in_place(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    db = _db()
    try:
        db.add(Collection(
            slug='handbuch', name='Old Name', description='Old', read_teams=['old-team'],
            synced_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    payload = [{'slug': 'handbuch', 'name': 'New Name', 'description': 'New', 'read_teams': ['new-team']}]
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        db = _db()
        try:
            sync_collections(db)
        finally:
            db.close()

    db = _db()
    try:
        rows = db.query(Collection).all()
        assert len(rows) == 1  # updated in place, not duplicated
        handbuch = rows[0]
        assert handbuch.name == 'New Name'
        assert handbuch.description == 'New'
        assert handbuch.read_teams == ['new-team']
        assert handbuch.synced_at.replace(tzinfo=timezone.utc) > datetime(2020, 1, 1, tzinfo=timezone.utc)
    finally:
        db.close()


def test_sync_removes_a_collection_that_vanished_upstream(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    db = _db()
    try:
        db.add(Collection(slug='still-here', name='Still Here'))
        db.add(Collection(slug='deleted-upstream', name='Gone'))
        db.commit()
    finally:
        db.close()

    payload = [{'slug': 'still-here', 'name': 'Still Here', 'description': None, 'read_teams': []}]
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        db = _db()
        try:
            sync_collections(db)
        finally:
            db.close()

    db = _db()
    try:
        assert db.get(Collection, 'still-here') is not None
        assert db.get(Collection, 'deleted-upstream') is None
    finally:
        db.close()


def test_sync_with_empty_registry_removes_every_local_collection(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    db = _db()
    try:
        db.add(Collection(slug='only-one', name='Only One'))
        db.commit()
    finally:
        db.close()

    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, [])):
        db = _db()
        try:
            count = sync_collections(db)
        finally:
            db.close()

    assert count == 0
    db = _db()
    try:
        assert db.query(Collection).count() == 0
    finally:
        db.close()


def test_sync_missing_optional_fields_fall_back_to_defaults(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    payload = [{'slug': 'minimal'}]
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        db = _db()
        try:
            sync_collections(db)
        finally:
            db.close()

    db = _db()
    try:
        minimal = db.get(Collection, 'minimal')
        assert minimal is not None
        assert minimal.name == 'minimal'  # falls back to the slug
        assert minimal.description is None
        assert minimal.read_teams == []
    finally:
        db.close()


# --- Error classification -----------------------------------------------------------


def test_sync_raises_collection_sync_error_on_non_200_status(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(500)):
        db = _db()
        try:
            with pytest.raises(CollectionSyncError):
                sync_collections(db)
        finally:
            db.close()


def test_sync_raises_collection_sync_error_on_transport_failure(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.collection_sync.httpx.get', side_effect=httpx.ConnectError('refused')):
        db = _db()
        try:
            with pytest.raises(CollectionSyncError):
                sync_collections(db)
        finally:
            db.close()


def test_sync_raises_collection_sync_error_on_non_list_payload(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, {'not': 'a list'})):
        db = _db()
        try:
            with pytest.raises(CollectionSyncError):
                sync_collections(db)
        finally:
            db.close()


def test_sync_raises_collection_sync_error_on_entry_missing_slug(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    payload = [{'name': 'No Slug Here'}]
    with patch('app.services.collection_sync.httpx.get', return_value=_FakeResponse(200, payload)):
        db = _db()
        try:
            with pytest.raises(CollectionSyncError):
                sync_collections(db)
        finally:
            db.close()
