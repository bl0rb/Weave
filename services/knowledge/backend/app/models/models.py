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
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.db import Base


class VectorType(TypeDecorator):
    """A chunk's embedding, stored dialect-appropriately.

    On PostgreSQL this delegates to pgvector.sqlalchemy.Vector(dimension) --
    a real `vector(N)` column usable with pgvector's similarity operators
    and an HNSW ANN index (`ix_chunks_embedding_hnsw`, `vector_cosine_ops`,
    see alembic/versions/0001_init.py). SQLite (local dev,
    the pytest suite) has no pgvector extension at all, so the same column
    falls back to plain JSON there: a list of floats round-trips correctly
    for tests, but with no vector index or similarity search -- fine for a
    skeleton that doesn't query by similarity yet, wrong for production
    (which must run on the postgres path to mean anything).

    `dimension` must match settings.embedding_dimension and the migration's
    `vector(N)` column width; changing it is a schema change (new migration)
    plus a reindex of every existing chunk, not a config-only flip -- see
    Settings.embedding_dimension's docstring comment.
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
    """Local, read-only MIRROR of Weave-Ingest's collection registry (the
    canonical "Collections" contract -- Weave-Ingest owns `read_teams`, the
    actual access-control list, and is the only writer of it anywhere in the
    system). This table exists purely so a `document.processed` webhook
    (app/api/events.py) and Weave-Retrieval's read-only pass over this same
    database (see contracts/chunk-store.md) can resolve a collection slug to
    its display name/description/`read_teams` without either service ever
    calling Weave-Ingest synchronously on the hot path.

    Kept in sync by app/services/collection_sync.py, pulled either from the
    periodic `weave.knowledge.collection_sync_tick` Celery chain (see
    app/workers/collection_sync_tasks.py) or a one-off lazy reload the
    webhook triggers on an unknown slug (app/api/events.py) -- never written
    from anywhere else, and never the other way around (Weave-Knowledge
    never pushes a change back to Weave-Ingest).

    `slug` is the primary key (not a surrogate id): per the Collections
    contract it IS the identifying value everywhere -- Document.collection_
    slug, Chunk.meta['collection'], SearchFilters.collection all key off the
    same string, so a second synthetic id here would just be a column
    nothing else ever needs.

    No FK from Document.collection_slug to this table on purpose -- see
    that column's own docstring in this module: a mirror is allowed to lag
    the documents that reference it, and an FK would turn that normal,
    expected lag into a hard write failure.
    """

    __tablename__ = 'collections'

    slug: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Team slugs allowed to READ this collection; an empty list is the
    # contract's own "readable by everyone" sentinel, not "readable by
    # nobody" -- enforced by Weave-Retrieval, never interpreted here (this
    # service only mirrors the value, it never itself gates on it).
    read_teams: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Set to "now" on every upsert by sync_collections(), regardless of
    # whether any other field actually changed -- lets an operator (or a
    # future staleness check) tell a collection that is still being synced
    # apart from one Weave-Ingest has quietly stopped reporting.
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class Document(Base):
    """One released document snapshot, tracked through this service's own
    index lifecycle. Legacy rows from the pre-release workflow remain
    readable, but are never fetched or indexed by a new task.

    `source_job_id`/`previous_job_id` mirror Weave-Ingest's `job_id`/
    `previous_job_id` (see contracts/events/document.processed.schema.json)
    -- both are UUID strings there, e.g. Weave-Ingest's own `Job.id`
    (String(36)), never integers, so these columns are String(36) too rather
    than Integer despite being called "Job-ID" -- an Integer column could
    not hold a real Weave-Ingest job id at all. No FK to another service's
    table either way (ADR-0004: no cross-service DB joins), just a plain
    value copied out of the event.
    """

    __tablename__ = 'documents'

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_job_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    document_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Weave-Ingest job id this document supersedes (re-processing with a
    # different OCR profile, say) -- not this table's own PK, see class
    # docstring. NULL for an initial upload, same semantics as the event's
    # own `previous_job_id`.
    previous_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The event's markdown_url is retained for audit/display. Released
    # indexing builds its download target locally from release_id instead of
    # trusting this value. Nullable only because not
    # every Document row in this codebase is created through that webhook
    # (the ORM-level tests in tests/test_models.py, tests/test_embeddings.py,
    # tests/test_reindex_cli.py, ... build bare rows for unrelated columns);
    # app/api/events.py always sets it for a real ingested document.
    markdown_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # 'paddleocr' | 'mail-eml' | 'pypdf-fallback' | 'spreadsheet-fallback' |
    # 'openai_vision' -- plain String (not a native/CHECK enum), same
    # discipline Weave-Ingest uses for values it doesn't own the contract
    # for (see e.g. OpenWebUIPush.status in Weave-Ingest's models.py).
    engine: Mapped[str] = mapped_column(String(64), nullable=False)
    # 'A' | 'B' | 'C', or NULL if the event carried no quality-gate result
    # (see contracts/events/document.processed.md's quality.grade) --
    # consumers are told to treat NULL like 'warn', same as the event does.
    quality_grade: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quality_recommendation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The event's full parsed frontmatter object, verbatim -- see
    # contracts/frontmatter.schema.json. team/department/tags below are
    # denormalized out of this for indexed filtering; frontmatter itself
    # stays the source of truth for anything else a later stage needs.
    frontmatter: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Fetched from the immutable release snapshot and populated by the index
    # run -- NULL until then, which is
    # exactly what `status == 'pending'` means.
    markdown_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    team: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Denormalized out of `frontmatter.collection` (see app/api/events.py),
    # same "own column for indexed filtering, frontmatter stays the source
    # of truth" discipline as team/department above. Deliberately NO
    # ForeignKey to Collection.slug: the `collections` table is a
    # periodically-synced MIRROR of Weave-Ingest's own registry (see
    # app/services/collection_sync.py), not data this service owns, so it
    # can legitimately lag behind a document that already carries a
    # brand-new collection slug (the event arrives before the next sync
    # tick, or a one-off sync attempt failed -- see app/api/events.py's
    # lazy-reload-on-unknown-slug handling). An FK would make indexing that
    # document fail (or block on a synchronous registry fetch) purely
    # because the mirror hasn't caught up yet, which is exactly the
    # coupling this column must not introduce. `NULL` means "no collection"
    # (an unscoped/legacy document), never "unknown collection" -- see
    # contracts/chunk-store.md's Collections section for the full access
    # rule Weave-Retrieval applies to a NULL collection_slug.
    collection_slug: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    # Model id chunks were embedded with on the most recent successful index
    # run (see Chunk.embedding_model for the per-chunk value) -- lets a
    # later provider/model change be detected without joining to chunks.
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Consecutive transient app.services.ingest_client.fetch_released_markdown
    # failures for the current index-attempt chain -- reset to 0 on a
    # successful fetch, incremented by app/workers/tasks.py's
    # index_document on each transient failure, and checked against that
    # module's _MAX_ATTEMPTS before giving up and marking status='failed'.
    # Persisted on the row (mirrors Weave-Ingest's WebhookDelivery.attempts)
    # rather than threaded through as a Celery task argument, so a
    # redelivered task (acks_late) picks up exactly where the row says it
    # left off instead of restarting the backoff schedule from zero.
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

    chunks: Mapped[list['Chunk']] = relationship(back_populates='document', cascade='all, delete-orphan')


class Chunk(Base):
    """One structure-aware slice of a Document's markdown_body, plus its
    embedding once the index run has produced one.

    Unlike every table above, `id` is a plain autoincrement integer -- there
    is no cross-service reference to a chunk id (chunks are only ever looked
    up via their owning document), so a UUID buys nothing here and an
    integer keeps ORDER BY id a cheap, natural proxy for chunk_index order.
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
    # ATX heading breadcrumb this chunk falls under, e.g.
    # ['Setup', 'Installation', 'Docker'] -- empty list if the source has no
    # heading structure above this chunk.
    heading_path: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Denormalized filter metadata copied down from the owning Document
    # (team/department/tags/engine/...) so Weave-Retrieval can filter chunks
    # directly without joining back to documents on every search query.
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        VectorType(settings.embedding_dimension), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)

    document: Mapped[Document] = relationship(back_populates='chunks')


class IngestEvent(Base):
    """Idempotency ledger for inbound document.released webhooks.

    Weave-Ingest delivers with at-least-once semantics. Released events use
    `release:<release_id>` as their event_key. A webhook handler records this
    before indexing; a hit means "already processed" rather than re-indexing.
    """

    __tablename__ = 'ingest_events'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    # Stored rather than assumed so event types can share this ledger table;
    # document.processed deliberately never creates a ledger row.
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    # sha256 of the raw request body -- lets a replay with a byte-identical
    # payload be told apart from a same-key delivery whose payload actually
    # changed (which would indicate a Weave-Ingest bug, not a normal retry).
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
