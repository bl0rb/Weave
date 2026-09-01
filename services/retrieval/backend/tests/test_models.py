"""Proves the read models (app/models/models.py) map the same
`documents`/`chunks` shape Weave-Knowledge's own migrations create --
built and read back via a fresh sqlite DB created from THESE (copied)
model definitions, exactly as tests/conftest.py sets up for the whole
suite (see contracts/chunk-store.md for the field-by-field
contract these models are a copy of).
"""

import uuid

from app.models.models import Chunk, Collection, Document, DocumentStatus
from tests.conftest import TestingSessionLocal, make_chunk, make_collection, make_document


def test_document_crud_and_defaults():
    db = TestingSessionLocal()
    try:
        doc = make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)

        assert doc.id is not None
        # Python-side/server-side defaults, not values we set explicitly.
        assert doc.document_version == 1
        assert doc.status == DocumentStatus.PENDING
        assert doc.chunk_count == 0
        assert doc.markdown_body is None
        assert doc.indexed_at is None
        assert doc.created_at is not None
        assert doc.updated_at is not None

        db.expire_all()
        fetched = db.get(Document, doc.id)
        assert fetched is not None
        assert fetched.content_sha256 == 'a' * 64
        assert fetched.team == 'Kundenservice'
        assert fetched.department == 'Support'
        assert fetched.frontmatter == {'source': 'x.pdf'}
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_document_source_job_id_is_unique():
    db = TestingSessionLocal()
    try:
        job_id = str(uuid.uuid4())
        db.add(make_document(source_job_id=job_id))
        db.commit()

        db.add(make_document(source_job_id=job_id))
        try:
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError('duplicate source_job_id was not rejected')
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_chunk_maps_to_owning_document():
    db = TestingSessionLocal()
    try:
        doc = make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)

        chunk1 = make_chunk(doc, chunk_index=0, heading_path=['Intro'])
        chunk2 = make_chunk(doc, chunk_index=1, heading_path=['Intro', 'Details'])
        db.add_all([chunk1, chunk2])
        db.commit()

        fetched = db.query(Chunk).filter_by(document_id=doc.id).order_by(Chunk.chunk_index).all()
        assert [c.heading_path for c in fetched] == [['Intro'], ['Intro', 'Details']]
        assert all(c.document_id == doc.id for c in fetched)
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_chunk_unique_document_id_chunk_index():
    db = TestingSessionLocal()
    try:
        doc = make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)

        db.add(make_chunk(doc, chunk_index=0))
        db.commit()

        db.add(make_chunk(doc, chunk_index=0))
        try:
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError('duplicate (document_id, chunk_index) was not rejected')
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_document_collection_slug_defaults_to_none():
    """A document built without a `collection_slug` override round-trips as
    `NULL` -- the Collections contract's own "unscoped/legacy" meaning (see
    that column's docstring in app/models/models.py), not an error and not
    some other implicit default."""
    db = TestingSessionLocal()
    try:
        doc = make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)
        assert doc.collection_slug is None

        db.expire_all()
        fetched = db.get(Document, doc.id)
        assert fetched.collection_slug is None
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_document_collection_slug_round_trips():
    db = TestingSessionLocal()
    try:
        doc = make_document(collection_slug='support-docs')
        db.add(doc)
        db.commit()
        db.refresh(doc)

        db.expire_all()
        fetched = db.get(Document, doc.id)
        assert fetched.collection_slug == 'support-docs'
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_collection_crud_and_defaults():
    """One row of the registry mirror -- see app/models/models.py's
    Collection docstring for why this service reads it but never writes it
    outside a test fixture."""
    db = TestingSessionLocal()
    try:
        collection = make_collection(slug='eng-docs', name='Engineering Docs', read_teams=['Engineering'])
        db.add(collection)
        db.commit()

        db.expire_all()
        fetched = db.get(Collection, 'eng-docs')
        assert fetched is not None
        assert fetched.name == 'Engineering Docs'
        assert fetched.description is None
        assert fetched.read_teams == ['Engineering']
        assert fetched.synced_at is not None
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_collection_read_teams_defaults_to_empty_list():
    """An empty `read_teams` is the contract's own "readable by everyone"
    sentinel (see app/services/collections.py:readable_collections()), and
    the column's own Python-side default when a caller doesn't set it."""
    db = TestingSessionLocal()
    try:
        db.add(make_collection(slug='public-docs'))
        db.commit()

        db.expire_all()
        fetched = db.get(Collection, 'public-docs')
        assert fetched.read_teams == []
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()
