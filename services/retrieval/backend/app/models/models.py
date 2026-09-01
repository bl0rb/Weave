"""Read model of the weave_knowledge schema -- contract:
contracts/chunk-store.md; single writer is Weave-Knowledge.

This module deliberately mirrors the column-for-column shape of
Weave-Knowledge's own `app/models/models.py` (`Document`, `Chunk`,
`Collection`, `VectorType`) so this service's SQLAlchemy layer maps the
exact same `documents`/`chunks`/`collections` tables Weave-Knowledge's
Alembic migrations create and write to -- see the contract doc above for
the full field table, index list, and the read-only DB-role grant this
service is expected to connect with in production. It is NOT a copy kept in
sync by hand-editing both repos independently: any change to
Weave-Knowledge's schema that isn't purely additive (rename, drop, type
change) must update the contract doc and this file together (see the
contract's "Versionierungsregel").

Deliberately thin compared to the writer-side model: no `relationship()`
between Document and Chunk (this service never needs cascade-delete or
ORM-managed collection loading -- the search service selects/joins
`chunks`/`documents` explicitly with plain `select()` calls, see
app/services/search.py) and no `IngestEvent` table at all (that's
Weave-Knowledge's own webhook-idempotency ledger, irrelevant to a reader).
`Collection` (below) is also read-only here, same as everything else in
this module -- see its own docstring for why this service is nonetheless
the designated READ AUTHORITY over what that table means for a given team,
despite writing none of it.
"""

import enum
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import Base


class VectorType(TypeDecorator):
    """A chunk's embedding, mapped dialect-appropriately -- copied from
    Weave-Knowledge's own app/models/models.py so this service decodes the
    exact same column the same way that service encodes it.

    On PostgreSQL this delegates to pgvector.sqlalchemy.Vector(dimension) --
    the real `vector(N)` column Weave-Knowledge's migration created, usable
    with pgvector's similarity operators and its HNSW ANN index
    (`ix_chunks_embedding_hnsw`, `vector_cosine_ops` -- see the chunk-store
    contract's "Indexe" section). SQLite (local dev, the pytest suite) has
    no pgvector extension at all, so the same column falls back to plain
    JSON there: a list of floats round-trips correctly for tests, but with
    no vector index or similarity search -- fine for a skeleton whose
    /search route is still a 501 placeholder, wrong for any real query
    (which must run on the postgres path to mean anything).

    `dimension` must match settings.embedding_dimension, which in turn must
    match the width Weave-Knowledge's own settings.embedding_dimension (and
    thus its `vector(N)` column) is configured with -- see that setting's
    docstring in app/core/config.py.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dimension: int, *args, **kwargs) -> None:
        self.dimension = dimension
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(Vector(self.dimension))
        return dialect.type_descriptor(JSON())


class DocumentStatus(str, enum.Enum):
    PENDING = 'pending'
    INDEXED = 'indexed'
    BLOCKED = 'blocked'
    SUPERSEDED = 'superseded'
    FAILED = 'failed'


class Collection(Base):
    """One row of Weave-Knowledge's `collections` table -- see the
    chunk-store contract's "Tabelle `collections`" section for the
    authoritative field table and the "Sync-Richtung" diagram for how a
    value actually gets here.

    This is a mirror of a mirror: Weave-Ingest owns every field below
    (`slug`, `name`, `description`, `read_teams` -- the Collections
    contract's canonical registry), Weave-Knowledge's own
    app/services/collection_sync.py periodically pulls a full copy into its
    `collections` table, and THIS class is Weave-Retrieval's own read-only
    SQLAlchemy view of that same table -- same column-for-column mirroring
    discipline as Document/Chunk above, not a second copy kept in sync by
    hand. Weave-Retrieval never writes a row here either; it is the
    Collections contract's designated READ AUTHORITY for "which collections
    may team X read" (see app/services/collections.py), which is a read-only
    role, not a writer one.

    `slug` is the primary key (matches `Document.collection_slug` and
    `Chunk.meta['collection']`) -- no separate surrogate id, same reasoning
    as Weave-Knowledge's own Collection model.
    """

    __tablename__ = 'collections'

    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Team slugs allowed to READ this collection; an empty list is the
    # contract's own "readable by everyone" sentinel, not "readable by
    # nobody" -- see app/services/collections.py's readable_collections(),
    # the one place this service actually evaluates the field rather than
    # just mirroring it.
    read_teams: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class Document(Base):
    """One row of Weave-Knowledge's `documents` table -- see the chunk-store
    contract's field table for the authoritative description of every
    column below; comments here only call out what a reader (as opposed to
    the writer) actually cares about.

    Weave-Retrieval never creates, updates, or deletes a Document row --
    it only ever runs SELECTs, whether against a real read-only DB role in
    production or its own sqlite copy of this schema in tests (see
    tests/conftest.py).
    """

    __tablename__ = 'documents'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_job_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    previous_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    markdown_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    engine: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_grade: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quality_recommendation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    frontmatter: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    markdown_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalized filter columns Weave-Retrieval's metadata-based access
    # control (README's "Metadaten-basierte Zugriffskontrolle") filters on
    # directly -- see app/schemas/search.py's SearchFilters/allowed_teams.
    team: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Denormalized out of `frontmatter.collection` by Weave-Knowledge (see
    # that service's own Document.collection_slug docstring) -- same
    # column-for-column shape, deliberately no ForeignKey to Collection.slug
    # here either: the `collections` mirror is synced independently and may
    # legitimately lag behind a document that already carries a brand-new
    # slug. `NULL` means "no collection" (an unscoped/legacy document),
    # never "unknown collection" -- see apply_filters()'s own docstring in
    # app/services/search.py for the exact access rule this service applies
    # to a NULL collection_slug (Collections contract point 5).
    collection_slug: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    index_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name='document_status', native_enum=False, validate_strings=True),
        default=DocumentStatus.PENDING,
        server_default=DocumentStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class Chunk(Base):
    """One row of Weave-Knowledge's `chunks` table -- the actual unit a
    search result is built from (see app/schemas/search.py's SearchResult).
    See the chunk-store contract's field table for the authoritative
    description of every column.
    """

    __tablename__ = 'chunks'
    __table_args__ = (
        UniqueConstraint('document_id', 'chunk_index', name='uq_chunks_document_id_chunk_index'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalized filter metadata copied down from the owning Document
    # (team/department/tags/engine/...) -- Weave-Retrieval's own filtering
    # reads THIS column directly rather than joining back to `documents` on
    # every search query, same reasoning as on the writer side.
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        VectorType(settings.embedding_dimension), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # No mapped `tsv` column: it only exists on postgres (a GENERATED
    # tsvector column, see the contract's "Indexe" section) and SQLAlchemy
    # never needs to read/write it as a Python value -- the fulltext leg of
    # the search pipeline (a later stage) queries it with a raw
    # `to_tsquery(...)` expression against `chunks.tsv`, not through this
    # ORM model.
