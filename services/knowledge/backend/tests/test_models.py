import uuid
from datetime import datetime, timezone

from app.models.models import Chunk, Collection, Document, DocumentStatus
from tests.conftest import TestingSessionLocal


def _make_document(**overrides) -> Document:
    defaults = dict(
        source_job_id=str(uuid.uuid4()),
        content_sha256='a' * 64,
        engine='paddleocr',
        frontmatter={'source': 'x.pdf'},
        tags=['important', 'section-1'],
        processed_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Document(**defaults)


def test_document_crud_and_defaults():
    db = TestingSessionLocal()
    try:
        doc = _make_document()
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

        db.expire_all()
        fetched = db.get(Document, doc.id)
        assert fetched is not None
        assert fetched.content_sha256 == 'a' * 64
        assert fetched.tags == ['important', 'section-1']
        assert fetched.frontmatter == {'source': 'x.pdf'}
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_document_source_job_id_is_unique():
    db = TestingSessionLocal()
    try:
        job_id = str(uuid.uuid4())
        db.add(_make_document(source_job_id=job_id))
        db.commit()

        db.add(_make_document(source_job_id=job_id))
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


def test_chunk_cascade_delete_with_document():
    db = TestingSessionLocal()
    try:
        doc = _make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)

        chunk1 = Chunk(
            document_id=doc.id, chunk_index=0, text='first chunk',
            heading_path=['Intro'], char_count=11, meta={'team': 'Kundenservice'},
        )
        chunk2 = Chunk(
            document_id=doc.id, chunk_index=1, text='second chunk',
            heading_path=['Intro', 'Details'], char_count=12, meta={'team': 'Kundenservice'},
        )
        db.add_all([chunk1, chunk2])
        db.commit()

        assert db.query(Chunk).filter_by(document_id=doc.id).count() == 2

        # ORM-level cascade='all, delete-orphan' on Document.chunks --
        # deleting the parent must take its chunks with it even though this
        # test DB doesn't enable SQLite's PRAGMA foreign_keys (the DB-level
        # ON DELETE CASCADE from the migration is a postgres-only backstop
        # here, not what this assertion exercises).
        db.delete(doc)
        db.commit()

        assert db.query(Chunk).filter_by(document_id=doc.id).count() == 0
        assert db.get(Document, doc.id) is None
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_document_collection_slug_defaults_to_none():
    db = TestingSessionLocal()
    try:
        doc = _make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)
        assert doc.collection_slug is None
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


def test_document_collection_slug_has_no_foreign_key_to_collections():
    """The Collections contract is explicit that this column carries NO FK
    -- a document can legitimately reference a collection slug the local
    registry mirror hasn't synced yet (see app/models/models.py's
    Document.collection_slug docstring)."""
    db = TestingSessionLocal()
    try:
        doc = _make_document(collection_slug='not-yet-synced')
        db.add(doc)
        db.commit()  # must not raise despite no matching `collections` row
        db.refresh(doc)
        assert doc.collection_slug == 'not-yet-synced'
    finally:
        db.query(Document).delete()
        db.commit()
        db.close()


# --- Collection registry mirror -------------------------------------------------


def test_collection_crud_and_defaults():
    db = TestingSessionLocal()
    try:
        collection = Collection(slug='handbuch', name='Handbuch')
        db.add(collection)
        db.commit()
        db.refresh(collection)

        assert collection.slug == 'handbuch'
        assert collection.description is None
        assert collection.read_teams == []
        assert collection.synced_at is not None

        db.expire_all()
        fetched = db.get(Collection, 'handbuch')
        assert fetched is not None
        assert fetched.name == 'Handbuch'
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_collection_slug_is_the_primary_key():
    db = TestingSessionLocal()
    try:
        db.add(Collection(slug='dup-slug', name='First'))
        db.commit()

        db.add(Collection(slug='dup-slug', name='Second'))
        try:
            db.commit()
        except Exception:
            db.rollback()
        else:
            raise AssertionError('duplicate collection slug was not rejected')
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_collection_read_teams_empty_list_means_readable_by_everyone():
    """Not a behavioral assertion (Weave-Knowledge never itself enforces
    read_teams -- see Collection's docstring) -- just pins that an empty
    list round-trips as [] and not None, since [] vs. NULL is exactly the
    "everyone" vs. some-other-meaning distinction the contract relies on."""
    db = TestingSessionLocal()
    try:
        collection = Collection(slug='public-collection', name='Public', read_teams=[])
        db.add(collection)
        db.commit()
        db.refresh(collection)
        assert collection.read_teams == []
    finally:
        db.query(Collection).delete()
        db.commit()
        db.close()


def test_chunk_unique_document_id_chunk_index():
    db = TestingSessionLocal()
    try:
        doc = _make_document()
        db.add(doc)
        db.commit()
        db.refresh(doc)

        db.add(Chunk(document_id=doc.id, chunk_index=0, text='a', heading_path=[], char_count=1, meta={}))
        db.commit()

        db.add(Chunk(document_id=doc.id, chunk_index=0, text='b', heading_path=[], char_count=1, meta={}))
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
