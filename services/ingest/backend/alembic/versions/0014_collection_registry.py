"""add collection identity/ACL fields (slug, description, read_teams) and
finally use collections.name

Revision ID: 0014_collection_registry
Revises: 0013_webhooks
Create Date: 2026-08-31

Part of the Collections contract shared with Weave-Knowledge and Weave-Retrieval
(see README's "Collections" section): `slug` becomes the collection's stable
cross-service identity -- written into every processed document's
`collection` frontmatter field (app/services/paddle_service.py's
_build_rag_frontmatter) and synced into Weave-Knowledge's `collections`
registry table via GET /collections/registry -- `read_teams` is the read ACL
Weave-Retrieval enforces from that same registry, and `name` (present since
0004_auth but never actually set by any code path) finally becomes the
display label alongside it.

sqlite-compatible on purpose (batch_alter_table, no postgres-only DDL) so
tests/test_migrations.py can drive it through real alembic against sqlite,
same as every migration since 0004_auth.

Backfill: pre-existing rows get a deterministic slug derived from `id`
('collection-<8 hex chars>', de-duplicated with a numeric suffix in the
vanishingly unlikely event two rows' ids share their first 8 characters) and
a name of `folder` (the closest thing to an existing human-readable label)
or the slug itself -- one UPDATE per row, since there is no single SQL
expression that derives a stable, globally-unique slug from `id` on both
sqlite and postgres. Only THEN do slug/name get their NOT NULL constraints,
so this runs cleanly against a table that already has rows (verified in
tests/test_migrations.py against a hand-inserted legacy row).
"""

from alembic import op
import sqlalchemy as sa


revision = '0014_collection_registry'
down_revision = '0013_webhooks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.add_column(sa.Column('slug', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        # Read ACL (team slugs); empty list = readable by everyone -- see
        # Collection's docstring. server_default keeps old raw-SQL/test
        # inserts that don't set it explicitly NOT NULL-safe, same reasoning
        # as webhook_connections.events in 0013_webhooks.
        batch_op.add_column(sa.Column('read_teams', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

    bind = op.get_bind()
    collections_table = sa.table(
        'collections',
        sa.column('id', sa.String),
        sa.column('name', sa.String),
        sa.column('folder', sa.String),
        sa.column('slug', sa.String),
    )
    rows = bind.execute(sa.select(collections_table.c.id, collections_table.c.name, collections_table.c.folder)).fetchall()
    used_slugs: set[str] = set()
    for row in rows:
        base_slug = f'collection-{row.id[:8]}'
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f'{base_slug}-{suffix}'
            suffix += 1
        used_slugs.add(slug)
        name = (row.name or row.folder or slug).strip() or slug
        bind.execute(
            collections_table.update().where(collections_table.c.id == row.id).values(slug=slug, name=name)
        )

    with op.batch_alter_table('collections') as batch_op:
        batch_op.alter_column('slug', existing_type=sa.String(length=255), nullable=False)
        batch_op.alter_column('name', existing_type=sa.String(length=255), nullable=False)
        batch_op.create_unique_constraint('uq_collections_slug', ['slug'])
    op.create_index('ix_collections_slug', 'collections', ['slug'], if_not_exists=True)


def downgrade() -> None:
    op.drop_index('ix_collections_slug', table_name='collections')
    with op.batch_alter_table('collections') as batch_op:
        batch_op.drop_constraint('uq_collections_slug', type_='unique')
        batch_op.alter_column('name', existing_type=sa.String(length=255), nullable=True)
        batch_op.drop_column('read_teams')
        batch_op.drop_column('description')
        batch_op.drop_column('slug')
