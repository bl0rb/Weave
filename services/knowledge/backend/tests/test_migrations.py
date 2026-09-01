"""Verifies the 0001_init migration's upgrade/downgrade round-trip on
SQLite. Unlike Weave-Ingest's own test_migrations.py, there is only one
migration so far -- no pre-existing-schema scaffolding or chained
stamp/upgrade sequence is needed, `alembic upgrade head` from empty just
works, on both dialects the migration itself branches on.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, insert, select

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    # Built programmatically (not Config('alembic.ini')) so it doesn't
    # depend on the process cwd -- alembic.ini's `script_location = alembic`
    # is only correct relative to the backend/ directory, and tests may run
    # from the repo root. Mirrors Weave-Ingest's own test_migrations.py.
    cfg = Config()
    cfg.set_main_option('script_location', str(BACKEND_DIR / 'alembic'))
    return cfg


def test_0001_init_migration_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / 'migration_scratch.db'
    db_url = f'sqlite:///{db_path}'
    monkeypatch.setattr(settings, 'database_url', db_url)

    cfg = _alembic_config()

    # --- upgrade from empty: all three tables should exist ---
    command.upgrade(cfg, 'head')

    engine = create_engine(db_url, future=True)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for expected in ('documents', 'chunks', 'ingest_events'):
        assert expected in tables, f'{expected} missing after upgrade'

    document_columns = {c['name'] for c in insp.get_columns('documents')}
    assert {
        'id', 'source_job_id', 'content_sha256', 'document_version', 'previous_job_id',
        'original_filename', 'engine', 'quality_grade', 'quality_recommendation', 'frontmatter',
        'markdown_body', 'team', 'department', 'tags', 'processed_at', 'indexed_at',
        'chunk_count', 'embedding_model', 'status', 'error', 'created_at', 'updated_at',
    } <= document_columns

    chunk_columns = {c['name'] for c in insp.get_columns('chunks')}
    assert {
        'id', 'document_id', 'chunk_index', 'text', 'heading_path', 'page_start', 'page_end',
        'char_count', 'meta', 'embedding', 'embedding_model',
    } <= chunk_columns
    # SQLite has no tsvector/GENERATED-column support -- the migration must
    # skip that whole block on this dialect rather than fail.
    assert 'tsv' not in chunk_columns

    event_columns = {c['name'] for c in insp.get_columns('ingest_events')}
    assert {'id', 'event_key', 'event_type', 'received_at', 'payload_sha256'} <= event_columns

    chunk_fks = insp.get_foreign_keys('chunks')
    assert any(
        fk['referred_table'] == 'documents' and fk['constrained_columns'] == ['document_id']
        for fk in chunk_fks
    ), chunk_fks

    chunk_uniques = insp.get_unique_constraints('chunks')
    assert any(
        uc['name'] == 'uq_chunks_document_id_chunk_index'
        and set(uc['column_names']) == {'document_id', 'chunk_index'}
        for uc in chunk_uniques
    ), chunk_uniques

    document_indexes = {ix['name'] for ix in insp.get_indexes('documents')}
    assert {
        'ix_documents_content_sha256', 'ix_documents_previous_job_id', 'ix_documents_team',
        'ix_documents_department', 'ix_documents_status',
    } <= document_indexes

    event_uniques = insp.get_unique_constraints('ingest_events')
    assert any('event_key' in uc['column_names'] for uc in event_uniques) or any(
        ix['unique'] and ix['column_names'] == ['event_key'] for ix in insp.get_indexes('ingest_events')
    )

    # --- downgrade: everything should disappear ---
    command.downgrade(cfg, 'base')
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for removed in ('documents', 'chunks', 'ingest_events'):
        assert removed not in tables, f'{removed} still present after downgrade'

    # --- re-upgrade: should cleanly re-apply from empty ---
    command.upgrade(cfg, 'head')
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for expected in ('documents', 'chunks', 'ingest_events'):
        assert expected in tables


def test_0003_collections_migration_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    """Roundtrip for 0003_collections, same shape as the 0001 test above:
    upgrade from empty -> collections table + documents.collection_slug
    exist -> downgrade removes exactly those (not the 0001/0002 schema) ->
    a clean re-upgrade still works.
    """
    db_path = tmp_path / 'migration_scratch_0003.db'
    db_url = f'sqlite:///{db_path}'
    monkeypatch.setattr(settings, 'database_url', db_url)

    cfg = _alembic_config()

    command.upgrade(cfg, 'head')
    engine = create_engine(db_url, future=True)
    insp = inspect(engine)

    assert 'collections' in insp.get_table_names()
    collection_columns = {c['name'] for c in insp.get_columns('collections')}
    assert {'slug', 'name', 'description', 'read_teams', 'synced_at'} <= collection_columns

    document_columns = {c['name'] for c in insp.get_columns('documents')}
    assert 'collection_slug' in document_columns

    document_indexes = {ix['name'] for ix in insp.get_indexes('documents')}
    assert 'ix_documents_collection_slug' in document_indexes

    # slug is the primary key -- no separate surrogate id column.
    pk = insp.get_pk_constraint('collections')
    assert pk['constrained_columns'] == ['slug']

    # --- downgrade to just before 0003: collections gone, documents.collection_slug gone,
    # but the 0001/0002 schema (documents.markdown_url etc.) must still be intact.
    command.downgrade(cfg, '0002_document_index_fields')
    insp = inspect(engine)
    assert 'collections' not in insp.get_table_names()
    document_columns = {c['name'] for c in insp.get_columns('documents')}
    assert 'collection_slug' not in document_columns
    assert 'markdown_url' in document_columns  # 0002's own column survives

    # --- re-upgrade: should cleanly re-apply from the 0002 state.
    command.upgrade(cfg, 'head')
    insp = inspect(engine)
    assert 'collections' in insp.get_table_names()
    assert 'collection_slug' in {c['name'] for c in insp.get_columns('documents')}


def test_0003_collections_migration_preserves_existing_document_rows(tmp_path, monkeypatch):
    """0003_collections only ADDS a nullable `documents.collection_slug`
    column and a new `collections` table -- it must never touch existing
    `documents` rows. Regression test for a migration that, in practice,
    only ever gets exercised against an empty test database: this seeds a
    real row at the pre-0003 (0002) schema, upgrades, and asserts the row
    is untouched (collection_slug NULL, every other 0002 field intact) and
    survives the downgrade round-trip too.
    """
    db_path = tmp_path / 'migration_scratch_0003_data.db'
    db_url = f'sqlite:///{db_path}'
    monkeypatch.setattr(settings, 'database_url', db_url)

    cfg = _alembic_config()

    # --- seed a row at the pre-0003 schema (0002: no collection_slug yet) ---
    command.upgrade(cfg, '0002_document_index_fields')
    engine = create_engine(db_url, future=True)
    documents = Table('documents', MetaData(), autoload_with=engine)

    doc_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(insert(documents).values(
            id=doc_id, source_job_id='job-preexisting', content_sha256='b' * 64, engine='paddleocr',
            frontmatter={'team': 'Kundenservice'}, tags=['pre-existing'],
            processed_at=now, created_at=now, updated_at=now,
        ))
    engine.dispose()

    # --- upgrade to head (applies 0003): the row must survive, untouched ---
    command.upgrade(cfg, 'head')
    engine = create_engine(db_url, future=True)
    documents = Table('documents', MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        row = conn.execute(select(documents).where(documents.c.id == doc_id)).mappings().one()

    assert row['source_job_id'] == 'job-preexisting'
    assert row['content_sha256'] == 'b' * 64
    assert row['frontmatter'] == {'team': 'Kundenservice'}
    assert row['tags'] == ['pre-existing']
    assert row['collection_slug'] is None  # new column, backfilled to NULL, not dropped/errored on
    engine.dispose()

    # --- downgrade past 0003: the row (minus collection_slug) is still there ---
    command.downgrade(cfg, '0002_document_index_fields')
    engine = create_engine(db_url, future=True)
    documents = Table('documents', MetaData(), autoload_with=engine)
    assert 'collection_slug' not in documents.c
    with engine.begin() as conn:
        row = conn.execute(select(documents).where(documents.c.id == doc_id)).mappings().one()

    assert row['source_job_id'] == 'job-preexisting'
    assert row['content_sha256'] == 'b' * 64
    assert row['frontmatter'] == {'team': 'Kundenservice'}
    assert row['tags'] == ['pre-existing']
    engine.dispose()


def test_migration_history_has_a_single_head():
    """Guards the shape of the migration chain: two heads makes every
    `upgrade head` fail. Only one migration exists today, but this keeps
    holding as more are added (same guard Weave-Ingest's own
    test_migrations.py has)."""
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f'alembic history has diverged into {len(heads)} heads: {heads}'


def test_migration_creates_hnsw_index_and_reads_embedding_dimension_from_settings():
    """Static source check (no Postgres available in this test environment):
    the migration must create the pgvector HNSW ANN index
    app/models/models.py's VectorType docstring already claims exists, and
    must read the vector column width from settings.embedding_dimension
    rather than a hardcoded literal -- so a future change to
    app/core/config.py's embedding_dimension can't silently desync from
    what this migration actually creates."""
    source = (BACKEND_DIR / 'alembic' / 'versions' / '0001_init.py').read_text()
    assert 'ix_chunks_embedding_hnsw' in source
    assert 'hnsw' in source.lower()
    assert 'vector_cosine_ops' in source
    assert 'from app.core.config import settings' in source
    assert 'settings.embedding_dimension' in source
    assert 'Vector(1536)' not in source


def test_migration_revision_ids_fit_alembic_version_column():
    """alembic_version.version_num is a VARCHAR(32) in Postgres; SQLite
    doesn't enforce that, so an over-long revision id would pass every test
    here and only blow up against a real Postgres deployment."""
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    too_long = [
        rev.revision
        for rev in ScriptDirectory.from_config(cfg).walk_revisions()
        if len(rev.revision) > 32
    ]
    assert not too_long, f'revision ids exceed alembic_version VARCHAR(32): {too_long}'
