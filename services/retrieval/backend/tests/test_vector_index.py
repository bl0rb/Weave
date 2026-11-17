"""Tests for the optional S3 Vectors backend: app.services.vector_index
(build_filter/query) and the vector_backend='s3vectors' branch of
app.services.search.vector_search_traced() with its mandatory Postgres
recheck and pgvector fallback.

No AWS access at all: the S3 side is either an in-memory FakeS3Vectors
client (monkeypatched over vector_index.client) that records every call and
answers from a fixed hit list -- deliberately INCLUDING ids S3 might still
hold for out-of-scope, non-indexed or superseded chunks, which the recheck
must drop -- or a real boto3 client wired to botocore's Stubber (which
validates the request against the bundled s3vectors service model).
"""

import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.models.models import Chunk, Document, DocumentStatus
from app.schemas.search import SearchFilters, SearchRequest
from app.services import search as search_service
from app.services import vector_index
from app.services.embeddings import FakeEmbeddingProvider
from tests.conftest import TestingSessionLocal, make_chunk, make_document

_PROVIDER = FakeEmbeddingProvider()
_TEXT = 'Bitte geben Sie Ihre Kundennummer 4711 an, wenn Sie anrufen.'
_MODEL = {'embedding_model': {'$eq': 'fake-embed'}}


class FakeS3Vectors:
    """Records query_vectors() calls; answers with `hits` (key, distance)
    truncated to the requested topK, or raises `error`."""

    def __init__(self, hits=(), error: Exception | None = None):
        self.hits = list(hits)
        self.error = error
        self.calls: list[dict] = []

    def query_vectors(self, **params):
        self.calls.append(params)
        if self.error is not None:
            raise self.error
        return {
            'vectors': [{'key': key, 'distance': distance} for key, distance in self.hits[: params['topK']]],
            'distanceMetric': 'cosine',
        }


def _client_error():
    from botocore.exceptions import ClientError

    return ClientError({'Error': {'Code': 'ServiceUnavailableException', 'Message': 'down'}}, 'QueryVectors')


@pytest.fixture(autouse=True)
def _no_cooldown():
    vector_index.reset_cooldown()
    yield
    vector_index.reset_cooldown()


@pytest.fixture
def s3(monkeypatch):
    """Switch this process to vector_backend='s3vectors' and install a fake
    client; returns a setter so each test chooses its own fake."""
    monkeypatch.setattr(settings, 'vector_backend', 's3vectors')
    monkeypatch.setattr(settings, 's3vectors_bucket', 'weave-test')
    holder = {}

    def install(fake: FakeS3Vectors) -> FakeS3Vectors:
        holder['fake'] = fake
        return fake

    monkeypatch.setattr(vector_index, 'client', lambda: holder['fake'])
    install(FakeS3Vectors())
    return install


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.query(Chunk).delete()
        session.query(Document).delete()
        session.commit()
        session.close()


def _seed(db, *, embedding_model='fake-embed', **doc_overrides) -> Chunk:
    doc_overrides.setdefault('status', DocumentStatus.INDEXED)
    doc = make_document(**doc_overrides)
    db.add(doc)
    db.flush()
    chunk = make_chunk(doc, text=_TEXT, embedding=_PROVIDER.embed_query(_TEXT), embedding_model=embedding_model)
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk


def _query_vec():
    return _PROVIDER.embed_query(_TEXT)


# --- build_filter ------------------------------------------------------------------


def test_build_filter_unrestricted_only_constrains_model():
    assert vector_index.build_filter(None, None, None, 'fake-embed') == _MODEL


def test_build_filter_teams_apply_to_every_row_without_collections():
    assert vector_index.build_filter(['HR'], None, None, 'm') == {
        '$and': [{'embedding_model': {'$eq': 'm'}}, {'team': {'$in': ['HR']}}]
    }


def test_build_filter_empty_teams_is_empty_scope():
    assert vector_index.build_filter([], None, None, 'm') is None


def test_build_filter_empty_collections_is_empty_scope():
    assert vector_index.build_filter(None, [], None, 'm') is None
    assert vector_index.build_filter(['HR'], [], None, 'm') is None


def test_build_filter_real_slugs_ignore_teams():
    # ADR 0008: rows inside a collection are decided by allowed_collections alone.
    assert vector_index.build_filter(['HR'], ['a', 'b'], None, 'm') == {
        '$and': [{'embedding_model': {'$eq': 'm'}}, {'collection': {'$in': ['a', 'b']}}]
    }


def test_build_filter_sentinel_admits_legacy_rows_gated_by_team():
    assert vector_index.build_filter(['HR'], ['a', '__none__'], None, 'm') == {
        '$and': [
            {'embedding_model': {'$eq': 'm'}},
            {'$or': [
                {'collection': {'$in': ['a']}},
                {'$and': [{'collection': {'$eq': '__none__'}}, {'team': {'$in': ['HR']}}]},
            ]},
        ]
    }


def test_build_filter_sentinel_only_without_teams():
    assert vector_index.build_filter(None, ['__none__'], None, 'm') == {
        '$and': [{'embedding_model': {'$eq': 'm'}}, {'collection': {'$eq': '__none__'}}]
    }


def test_build_filter_sentinel_with_empty_teams_keeps_only_real_slugs():
    assert vector_index.build_filter([], ['a', '__none__'], None, 'm') == {
        '$and': [{'embedding_model': {'$eq': 'm'}}, {'collection': {'$in': ['a']}}]
    }
    assert vector_index.build_filter([], ['__none__'], None, 'm') is None


def test_build_filter_collection_refinement_is_anded():
    assert vector_index.build_filter(None, ['a', 'b'], SearchFilters(collection='a'), 'm') == {
        '$and': [
            {'embedding_model': {'$eq': 'm'}},
            {'collection': {'$in': ['a', 'b']}},
            {'collection': {'$eq': 'a'}},
        ]
    }
    # The sentinel is never a real collection_slug -> zero rows in Postgres.
    assert vector_index.build_filter(None, None, SearchFilters(collection='__none__'), 'm') is None


def test_build_filter_document_ids_keep_only_valid_uuids():
    doc_id = uuid.uuid4()
    result = vector_index.build_filter(
        None, None, SearchFilters(document_ids=['nope', str(doc_id).upper()]), 'm'
    )
    assert result == {'$and': [{'embedding_model': {'$eq': 'm'}}, {'document_id': {'$in': [str(doc_id)]}}]}
    assert vector_index.build_filter(None, None, SearchFilters(document_ids=['nope']), 'm') is None
    assert vector_index.build_filter(None, None, SearchFilters(document_ids=[]), 'm') is None


def test_build_filter_other_filters_are_left_to_the_recheck():
    filters = SearchFilters(department='X', source='a.pdf', tags=['t'])
    assert vector_index.build_filter(None, None, filters, 'm') == {'embedding_model': {'$eq': 'm'}}


def test_build_filter_team_refinement_is_anded():
    assert vector_index.build_filter(None, None, SearchFilters(team='HR', department='X'), 'm') == {
        '$and': [{'embedding_model': {'$eq': 'm'}}, {'team': {'$eq': 'HR'}}]
    }


# --- query() against the real s3vectors service model ---------------------------------


def test_query_request_matches_service_model(monkeypatch):
    import boto3
    from botocore.stub import Stubber

    monkeypatch.setattr(settings, 's3vectors_bucket', 'weave-test')
    real = boto3.client('s3vectors', region_name='eu-central-1', aws_access_key_id='x', aws_secret_access_key='y')
    stubber = Stubber(real)
    stubber.add_response(
        'query_vectors',
        {'vectors': [{'key': '7', 'distance': 0.25}, {'key': 'not-a-chunk', 'distance': 0.3}], 'distanceMetric': 'cosine'},
        {
            'vectorBucketName': 'weave-test',
            'indexName': 'weave-chunks',
            'queryVector': {'float32': [0.5, 1.0]},
            'topK': 3,
            'filter': _MODEL,
            'returnDistance': True,
            'returnMetadata': False,
        },
    )
    monkeypatch.setattr(vector_index, 'client', lambda: real)
    with stubber:
        assert vector_index.query([0.5, 1], 3, _MODEL) == [(7, 0.25)]
    stubber.assert_no_pending_responses()


def test_query_wraps_client_errors(s3):
    s3(FakeS3Vectors(error=_client_error()))
    with pytest.raises(vector_index.VectorIndexError):
        vector_index.query([1.0], 1, None)


def test_query_wraps_unexpected_errors(s3):
    # e.g. botocore's KeyError('credential_provider') session race, or any
    # other surprise -- must still become the documented VectorIndexError.
    s3(FakeS3Vectors(error=KeyError('credential_provider')))
    with pytest.raises(vector_index.VectorIndexError, match='KeyError'):
        vector_index.query([1.0], 1, None)


def test_query_wraps_client_construction_errors(monkeypatch):
    def broken():
        raise ImportError('No module named boto3')

    monkeypatch.setattr(vector_index, 'client', broken)
    with pytest.raises(vector_index.VectorIndexError, match='ImportError'):
        vector_index.query([1.0], 1, None)


def test_failure_starts_cooldown_that_skips_s3(s3, monkeypatch):
    fake = s3(FakeS3Vectors(error=_client_error()))
    with pytest.raises(vector_index.VectorIndexError):
        vector_index.query([1.0], 1, None)
    fake.error = None
    with pytest.raises(vector_index.VectorIndexError, match='cooling down'):
        vector_index.query([1.0], 1, None)
    assert len(fake.calls) == 1

    # After the window S3 is asked again.
    now = vector_index.time.monotonic()
    monkeypatch.setattr(vector_index.time, 'monotonic', lambda: now + vector_index._FAILURE_COOLDOWN_SECONDS + 1)
    assert vector_index.query([1.0], 1, None) == []
    assert len(fake.calls) == 2


def test_client_is_fail_fast_private_session_and_built_once(monkeypatch):
    import threading

    monkeypatch.setattr(vector_index, '_client_cache', {})
    monkeypatch.setattr(settings, 's3vectors_region', 'eu-central-1')
    monkeypatch.setattr(settings, 's3vectors_access_key_id', 'x')
    monkeypatch.setattr(settings, 's3vectors_secret_access_key', 'y')
    built = []
    real_build = vector_index._build_client

    def counting_build(*key):
        built.append(key)
        return real_build(*key)

    monkeypatch.setattr(vector_index, '_build_client', counting_build)
    import boto3

    def shared_session_client(*args, **kwargs):
        raise AssertionError('must not use boto3.client() on the shared DEFAULT_SESSION')

    monkeypatch.setattr(boto3, 'client', shared_session_client)
    clients = []
    threads = [threading.Thread(target=lambda: clients.append(vector_index.client())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(built) == 1
    assert len({id(c) for c in clients}) == 1
    config = clients[0].meta.config
    assert config.connect_timeout + config.read_timeout <= 3
    assert config.retries['total_max_attempts'] == 1


# --- vector_search with vector_backend='s3vectors' ------------------------------------


def test_recheck_drops_ids_postgres_rejects_and_keeps_s3_order(db, s3):
    good_a = _seed(db, team='Kundenservice')
    good_b = _seed(db, team='Kundenservice')
    other_team = _seed(db, team='Engineering')
    superseded = _seed(db, team='Kundenservice', status=DocumentStatus.SUPERSEDED)
    blocked = _seed(db, team='Kundenservice', status=DocumentStatus.BLOCKED)
    wrong_model = _seed(db, team='Kundenservice', embedding_model='other-model')
    fake = s3(FakeS3Vectors([
        (str(superseded.id), 0.01),
        (str(good_b.id), 0.1),
        (str(other_team.id), 0.15),
        (str(blocked.id), 0.2),
        (str(wrong_model.id), 0.25),
        ('999999', 0.3),  # vector of a chunk that no longer exists
        (str(good_a.id), 0.4),
    ]))

    results, backend = search_service.vector_search_traced(db, _query_vec(), None, ['Kundenservice'], None, 10)

    assert backend == 's3vectors'
    assert [(chunk.id, round(score, 6)) for chunk, score in results] == [(good_b.id, 0.9), (good_a.id, 0.6)]
    assert fake.calls[0]['filter'] == {'$and': [_MODEL, {'team': {'$in': ['Kundenservice']}}]}


def test_recheck_enforces_collection_scope_and_legacy_team_gate(db, s3):
    in_scope = _seed(db, collection_slug='hr-docs', team='Engineering')
    other_collection = _seed(db, collection_slug='finance', team='Kundenservice')
    legacy_ok = _seed(db, collection_slug=None, team='Kundenservice')
    legacy_wrong_team = _seed(db, collection_slug=None, team='Engineering')
    fake = s3(FakeS3Vectors([
        (str(other_collection.id), 0.05),
        (str(legacy_ok.id), 0.1),
        (str(legacy_wrong_team.id), 0.15),
        (str(in_scope.id), 0.2),
    ]))

    results = search_service.vector_search(
        db, _query_vec(), None, ['Kundenservice'], ['hr-docs', '__none__'], 10
    )

    # Inside a collection only allowed_collections decides (team irrelevant);
    # legacy rows additionally need an allowed team (ADR 0008).
    assert [c.id for c, _ in results] == [legacy_ok.id, in_scope.id]
    assert fake.calls[0]['filter'] == vector_index.build_filter(
        ['Kundenservice'], ['hr-docs', '__none__'], None, 'fake-embed'
    )


def test_recheck_drops_legacy_rows_when_sentinel_not_allowed(db, s3):
    in_scope = _seed(db, collection_slug='hr-docs')
    legacy = _seed(db, collection_slug=None)
    s3(FakeS3Vectors([(str(legacy.id), 0.05), (str(in_scope.id), 0.1)]))
    results = search_service.vector_search(db, _query_vec(), None, None, ['hr-docs'], 10)
    assert [c.id for c, _ in results] == [in_scope.id]


def test_recheck_enforces_collection_and_document_id_refinements(db, s3):
    a1 = _seed(db, collection_slug='a')
    a2 = _seed(db, collection_slug='a')
    b1 = _seed(db, collection_slug='b')
    s3(FakeS3Vectors([(str(b1.id), 0.05), (str(a2.id), 0.1), (str(a1.id), 0.2)]))

    by_collection = search_service.vector_search(db, _query_vec(), SearchFilters(collection='a'), None, ['a', 'b'], 10)
    assert [c.id for c, _ in by_collection] == [a2.id, a1.id]

    by_document = search_service.vector_search(
        db, _query_vec(), SearchFilters(document_ids=[str(a1.document_id)]), None, None, 10
    )
    assert [c.id for c, _ in by_document] == [a1.id]


def test_recheck_enforces_team_refinement(db, s3):
    hr = _seed(db, team='HR')
    other = _seed(db, team='Engineering')
    fake = s3(FakeS3Vectors([(str(other.id), 0.05), (str(hr.id), 0.1)]))
    results = search_service.vector_search(db, _query_vec(), SearchFilters(team='HR'), None, None, 10)
    assert [c.id for c, _ in results] == [hr.id]
    assert {'team': {'$eq': 'HR'}} in fake.calls[0]['filter']['$and']


def test_recheck_applies_meta_filters_on_sqlite(db, s3):
    chunk = _seed(db)
    chunk.meta = {'source': 'a.pdf'}
    db.commit()
    s3(FakeS3Vectors([(str(chunk.id), 0.1)]))
    assert search_service.vector_search(db, _query_vec(), SearchFilters(source='b.pdf'), None, None, 5) == []
    assert len(search_service.vector_search(db, _query_vec(), SearchFilters(source='a.pdf'), None, None, 5)) == 1


def test_overfetch_and_top_k_cap(db, s3, monkeypatch):
    chunks = [_seed(db) for _ in range(3)]
    fake = s3(FakeS3Vectors([(str(c.id), 0.1 * i) for i, c in enumerate(chunks)]))

    results = search_service.vector_search(db, _query_vec(), None, None, None, 2)
    assert fake.calls[-1]['topK'] == 8  # 2 * default overfetch 4
    assert [c.id for c, _ in results] == [chunks[0].id, chunks[1].id]  # trimmed to limit

    monkeypatch.setattr(settings, 's3vectors_overfetch', 10)
    monkeypatch.setattr(settings, 's3vectors_max_top_k', 15)
    search_service.vector_search(db, _query_vec(), None, None, None, 2)
    assert fake.calls[-1]['topK'] == 15


def test_empty_scope_never_calls_s3(db, s3):
    fake = s3(FakeS3Vectors([('1', 0.0)]))
    assert search_service.vector_search_traced(db, _query_vec(), None, None, [], 5) == ([], 's3vectors')
    assert search_service.vector_search(db, _query_vec(), None, [], None, 5) == []
    assert search_service.vector_search(
        db, _query_vec(), SearchFilters(document_ids=['bad']), None, None, 5
    ) == []
    assert fake.calls == []


def test_error_falls_back_to_pgvector(db, s3):
    chunk = _seed(db)
    s3(FakeS3Vectors(error=_client_error()))
    results, backend = search_service.vector_search_traced(db, _query_vec(), None, None, None, 5)
    assert backend == 'pgvector-fallback'
    assert [c.id for c, _ in results] == [chunk.id]


def test_error_without_fallback_leaves_vector_leg_empty(db, s3, monkeypatch):
    _seed(db)
    monkeypatch.setattr(settings, 's3vectors_fallback_pgvector', False)
    s3(FakeS3Vectors(error=_client_error()))
    assert search_service.vector_search_traced(db, _query_vec(), None, None, None, 5) == ([], 's3vectors-error')

    # Fulltext still answers the request.
    results, trace = search_service.search(db, SearchRequest(query=_TEXT), refresh=False)
    assert trace.vector_backend == 's3vectors-error'
    assert trace.vector_candidates == 0
    assert trace.fulltext_candidates == 1
    assert len(results) == 1


def test_search_trace_reports_s3vectors(db, s3):
    chunk = _seed(db)
    s3(FakeS3Vectors([(str(chunk.id), 0.0)]))
    results, trace = search_service.search(db, SearchRequest(query=_TEXT), refresh=False)
    assert trace.vector_backend == 's3vectors'
    assert results[0].scores.vector == pytest.approx(1.0)


# --- default backend unchanged ---------------------------------------------------------


def test_default_backend_is_pgvector_and_never_touches_s3(db, monkeypatch):
    assert settings.vector_backend == 'pgvector'

    def boom():
        raise AssertionError('S3 client must not be built in pgvector mode')

    monkeypatch.setattr(vector_index, 'client', boom)
    chunk = _seed(db)
    results, trace = search_service.search(db, SearchRequest(query=_TEXT), refresh=False)
    assert trace.vector_backend == 'pgvector'
    assert [r.chunk_id for r in results] == [chunk.id]


# --- settings validation ---------------------------------------------------------------------


def test_settings_reject_unknown_backend():
    with pytest.raises(ValidationError, match='VECTOR_BACKEND'):
        Settings(vector_backend='qdrant')


def test_settings_require_bucket_for_s3vectors():
    with pytest.raises(ValidationError, match='S3VECTORS_BUCKET'):
        Settings(vector_backend='s3vectors')
    assert Settings(vector_backend=' S3Vectors ', s3vectors_bucket='b').vector_backend == 's3vectors'


def test_settings_overfetch_bounds():
    with pytest.raises(ValidationError):
        Settings(s3vectors_overfetch=21)
    with pytest.raises(ValidationError):
        Settings(s3vectors_overfetch=0)


def test_blank_vector_backend_means_pgvector():
    """Deploy files pass optional settings as `${VAR:-}` / "" -- blank is the
    default backend, exactly like Weave-Knowledge, never a startup error."""
    from app.core.config import Settings

    assert Settings(vector_backend='').vector_backend == 'pgvector'
    assert Settings(vector_backend='  S3Vectors ', s3vectors_bucket='b').vector_backend == 's3vectors'
