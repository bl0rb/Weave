"""HTTP-level tests for POST /api/v1/passage (app/api/search.py +
app/services/passage.py) and for SearchResult.source_job_id.

The passage endpoint must apply exactly the same visibility rules as
search (apply_filters(): status `indexed`, allowed_teams, allowed_collections
incl. NO_COLLECTION_SENTINEL) and answer every "not visible / not found"
case with the same 404 body, so these tests pair each passage case with the
corresponding search outcome where that matters.
"""

import uuid

import pytest

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus
from app.services.embeddings import FakeEmbeddingProvider
from tests.conftest import AUTH_HEADERS, TestingSessionLocal, client, make_chunk, make_document

NOT_FOUND = {'detail': 'Passage not found'}


@pytest.fixture(autouse=True)
def _clean_db():
    _cleanup()
    yield
    _cleanup()


def _cleanup() -> None:
    db = TestingSessionLocal()
    try:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
    finally:
        db.close()


def _seed(*, chunks: int = 5, **document_overrides) -> tuple[str, str, list[int]]:
    """One document with `chunks` chunks (chunk_index 0..n-1). Returns
    (document_id, source_job_id, chunk ids ordered by chunk_index)."""
    db = TestingSessionLocal()
    try:
        defaults = dict(
            status=DocumentStatus.INDEXED,
            original_filename='Reiserichtlinie.pdf',
            frontmatter={'title': 'Reiserichtlinie', 'source': 'reise.pdf'},
            document_version=3,
            collection_slug='hr',
        )
        defaults.update(document_overrides)
        document = make_document(**defaults)
        db.add(document)
        db.flush()
        rows = [
            make_chunk(
                document,
                chunk_index=index,
                text=f'Abschnitt {index} Tagegeld',
                heading_path=['Reisekosten', f'Teil {index}'],
                page_start=index + 1,
                page_end=index + 1,
                meta={
                    'team': document.team,
                    'source_kind': 'confluence',
                    'source_url': f'https://wiki.example/p/{index}',
                },
            )
            for index in range(chunks)
        ]
        db.add_all(rows)
        db.commit()
        return str(document.id), document.source_job_id, [row.id for row in rows]
    finally:
        db.close()


def _passage(body: dict):
    return client.post('/api/v1/passage', json=body, headers=AUTH_HEADERS)


# --- auth ----------------------------------------------------------------------


def test_passage_without_token_is_401():
    resp = client.post('/api/v1/passage', json={'document_id': str(uuid.uuid4()), 'chunk_id': 1})
    assert resp.status_code == 401


def test_passage_with_wrong_token_is_401():
    resp = client.post(
        '/api/v1/passage',
        json={'document_id': str(uuid.uuid4()), 'chunk_id': 1},
        headers={'Authorization': 'Bearer wrong'},
    )
    assert resp.status_code == 401


def test_passage_with_unconfigured_token_is_503(monkeypatch):
    monkeypatch.setattr(settings, 'retrieval_api_token', '')
    resp = _passage({'document_id': str(uuid.uuid4()), 'chunk_id': 1})
    assert resp.status_code == 503


# --- happy path ----------------------------------------------------------------


def test_passage_returns_target_and_neighbours():
    document_id, source_job_id, ids = _seed()

    resp = _passage({'document_id': document_id, 'chunk_id': ids[2], 'allowed_collections': ['hr']})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['document_id'] == document_id
    assert body['source_job_id'] == source_job_id
    assert body['title'] == 'Reiserichtlinie'
    assert body['original_filename'] == 'Reiserichtlinie.pdf'
    assert body['document_version'] == 3
    assert body['source_kind'] == 'confluence'
    assert body['source_url'] == 'https://wiki.example/p/2'
    assert body['collection'] == 'hr'
    assert body['target_chunk_id'] == ids[2]
    # default context=1: one neighbour on each side, ordered by chunk_index
    assert [c['chunk_index'] for c in body['chunks']] == [1, 2, 3]
    assert [c['chunk_id'] for c in body['chunks']] == ids[1:4]
    assert [c['is_target'] for c in body['chunks']] == [False, True, False]
    target = body['chunks'][1]
    assert target['text'] == 'Abschnitt 2 Tagegeld'
    assert target['heading_path'] == ['Reisekosten', 'Teil 2']
    assert target['page_start'] == 3
    assert target['page_end'] == 3


@pytest.mark.parametrize(
    'context,target,expected',
    [
        (0, 2, [2]),
        (3, 2, [0, 1, 2, 3, 4]),
        (2, 0, [0, 1, 2]),  # clipped at the start
        (2, 4, [2, 3, 4]),  # clipped at the end
    ],
)
def test_passage_context_window(context, target, expected):
    document_id, _, ids = _seed()
    resp = _passage({'document_id': document_id, 'chunk_id': ids[target], 'context': context})
    assert resp.status_code == 200
    assert [c['chunk_index'] for c in resp.json()['chunks']] == expected


@pytest.mark.parametrize('context', [-1, 4, 'x'])
def test_passage_rejects_out_of_range_context(context):
    resp = _passage({'document_id': str(uuid.uuid4()), 'chunk_id': 1, 'context': context})
    assert resp.status_code == 422


def test_passage_rejects_missing_fields():
    assert _passage({'chunk_id': 1}).status_code == 422
    assert _passage({'document_id': str(uuid.uuid4())}).status_code == 422


def test_passage_neighbours_never_cross_documents():
    document_id, _, ids = _seed(chunks=2)
    _seed(chunks=3, collection_slug='hr')  # another document, same collection
    resp = _passage({'document_id': document_id, 'chunk_id': ids[0], 'context': 3})
    assert resp.status_code == 200
    assert [c['chunk_id'] for c in resp.json()['chunks']] == ids


def test_passage_without_optional_metadata():
    document_id, source_job_id, ids = _seed(chunks=1, frontmatter={}, original_filename=None, collection_slug=None)
    db = TestingSessionLocal()
    try:
        db.get(Chunk, ids[0]).meta = {}
        db.commit()
    finally:
        db.close()
    resp = _passage({'document_id': document_id, 'chunk_id': ids[0]})
    assert resp.status_code == 200
    body = resp.json()
    assert body['title'] is None
    assert body['original_filename'] is None
    assert body['source_kind'] is None
    assert body['source_url'] is None
    assert body['collection'] is None
    assert body['source_job_id'] == source_job_id


# --- not found / no existence leak ---------------------------------------------


def test_passage_malformed_document_id_is_404():
    _, _, ids = _seed()
    resp = _passage({'document_id': 'not-a-uuid', 'chunk_id': ids[0]})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_passage_unknown_document_is_404():
    _, _, ids = _seed()
    resp = _passage({'document_id': str(uuid.uuid4()), 'chunk_id': ids[0]})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_passage_unknown_chunk_is_404():
    document_id, _, ids = _seed()
    resp = _passage({'document_id': document_id, 'chunk_id': max(ids) + 1000})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


def test_passage_chunk_of_another_document_is_404():
    document_id, _, _ = _seed()
    _, _, other_ids = _seed()
    resp = _passage({'document_id': document_id, 'chunk_id': other_ids[0]})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


@pytest.mark.parametrize(
    'doc_status',
    [DocumentStatus.SUPERSEDED, DocumentStatus.PENDING, DocumentStatus.FAILED, DocumentStatus.BLOCKED],
)
def test_passage_non_indexed_document_is_404(doc_status):
    document_id, _, ids = _seed(status=doc_status)
    resp = _passage({'document_id': document_id, 'chunk_id': ids[0]})
    assert resp.status_code == 404
    assert resp.json() == NOT_FOUND


# --- visibility: identical to search's apply_filters() -------------------------


def test_passage_collection_outside_allowed_collections_is_404():
    document_id, _, ids = _seed(collection_slug='finance')
    for allowed in (['hr'], [], ['__none__']):
        resp = _passage({'document_id': document_id, 'chunk_id': ids[0], 'allowed_collections': allowed})
        assert resp.status_code == 404, allowed
        assert resp.json() == NOT_FOUND


def test_passage_collection_grant_ignores_uploader_team():
    """ADR 0008: inside a space, allowed_collections alone decides -- the
    uploader's team must not hide the document (same as search)."""
    document_id, _, ids = _seed(collection_slug='hr', team='Finance')
    resp = _passage(
        {'document_id': document_id, 'chunk_id': ids[0], 'allowed_collections': ['hr'], 'allowed_teams': ['Other']}
    )
    assert resp.status_code == 200


def test_passage_legacy_document_needs_sentinel():
    document_id, _, ids = _seed(collection_slug=None, team='Kundenservice')
    base = {'document_id': document_id, 'chunk_id': ids[0]}
    assert _passage({**base, 'allowed_collections': ['hr']}).status_code == 404
    assert _passage({**base, 'allowed_collections': ['hr', '__none__']}).status_code == 200
    # the sentinel still applies the team gate to legacy rows
    assert (
        _passage({**base, 'allowed_collections': ['__none__'], 'allowed_teams': ['Other']}).status_code == 404
    )
    assert (
        _passage({**base, 'allowed_collections': ['__none__'], 'allowed_teams': ['Kundenservice']}).status_code
        == 200
    )


def test_passage_allowed_teams_without_collections():
    document_id, _, ids = _seed(collection_slug=None, team='Kundenservice')
    base = {'document_id': document_id, 'chunk_id': ids[0]}
    assert _passage({**base, 'allowed_teams': ['Other']}).status_code == 404
    assert _passage({**base, 'allowed_teams': []}).status_code == 404
    assert _passage({**base, 'allowed_teams': ['Kundenservice']}).status_code == 200
    # None/None = unrestricted service caller (same as search)
    assert _passage(base).status_code == 200


def test_passage_visibility_matches_search(monkeypatch):
    """For each scope, a chunk is returned by /passage exactly when /search
    (same allowed_teams/allowed_collections) can return it."""
    monkeypatch.setattr(settings, 'embedding_model', FakeEmbeddingProvider().model_name)
    provider = FakeEmbeddingProvider()
    db = TestingSessionLocal()
    try:
        docs = {
            'hr': make_document(status=DocumentStatus.INDEXED, collection_slug='hr', team='A'),
            'legacy': make_document(status=DocumentStatus.INDEXED, collection_slug=None, team='A'),
            'old': make_document(status=DocumentStatus.SUPERSEDED, collection_slug='hr', team='A'),
        }
        db.add_all(docs.values())
        db.flush()
        chunks = {}
        for name, document in docs.items():
            text = f'Tagegeld Regel {name}'
            chunk = make_chunk(document, text=text, embedding=provider.embed_query(text))
            db.add(chunk)
            chunks[name] = chunk
        db.commit()
        refs = {name: (str(docs[name].id), chunks[name].id) for name in docs}
    finally:
        db.close()

    scopes = [
        {},
        {'allowed_collections': ['hr']},
        {'allowed_collections': ['__none__'], 'allowed_teams': ['A']},
        {'allowed_collections': ['__none__'], 'allowed_teams': ['B']},
        {'allowed_teams': ['B']},
        {'allowed_collections': []},
    ]
    for scope in scopes:
        search = client.post(
            '/api/v1/search', json={'query': 'Tagegeld Regel', 'top_k': 50, 'final_k': 50, **scope}, headers=AUTH_HEADERS
        )
        assert search.status_code == 200
        found = {r['chunk_id'] for r in search.json()['results']}
        for name, (document_id, chunk_id) in refs.items():
            resp = _passage({'document_id': document_id, 'chunk_id': chunk_id, **scope})
            assert (resp.status_code == 200) == (chunk_id in found), (scope, name)


# --- SearchResult.source_job_id --------------------------------------------------


def test_search_result_carries_source_job_id(monkeypatch):
    monkeypatch.setattr(settings, 'embedding_model', FakeEmbeddingProvider().model_name)
    document_id, source_job_id, _ = _seed(chunks=1)
    resp = client.post('/api/v1/search', json={'query': 'Tagegeld'}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    results = resp.json()['results']
    assert results
    assert all(r['source_job_id'] == source_job_id for r in results if r['document_id'] == document_id)
