"""VectorType round-trip on SQLite (the JSON fallback path -- see
app/models/models.py's VectorType.load_dialect_impl). The pgvector.sqlalchemy
Vector path only exists on postgres, so it isn't exercised here at all; this
only proves the JSON fallback used by local dev / this test suite works,
same as Weave-Knowledge's own tests/test_vector_type.py.
"""

from app.models.models import Chunk, Document
from tests.conftest import TestingSessionLocal, make_chunk, make_document


def test_embedding_roundtrips_through_json_fallback():
    db = TestingSessionLocal()
    try:
        doc = make_document(content_sha256='c' * 64)
        db.add(doc)
        db.commit()
        db.refresh(doc)

        vector = [0.1, -0.25, 0.375, 1.0]
        chunk = make_chunk(doc, embedding=vector)
        db.add(chunk)
        db.commit()

        # Force an actual round trip through the DB, not just the identity map.
        db.expire_all()
        fetched = db.get(Chunk, chunk.id)
        assert fetched.embedding == vector
        assert fetched.embedding_model == 'fake-embed'
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()


def test_embedding_is_nullable():
    db = TestingSessionLocal()
    try:
        doc = make_document(content_sha256='d' * 64)
        db.add(doc)
        db.commit()
        db.refresh(doc)

        chunk = make_chunk(doc, embedding=None)
        db.add(chunk)
        db.commit()

        db.expire_all()
        fetched = db.get(Chunk, chunk.id)
        assert fetched.embedding is None
        assert fetched.embedding_model is None
    finally:
        db.query(Chunk).delete()
        db.query(Document).delete()
        db.commit()
        db.close()
