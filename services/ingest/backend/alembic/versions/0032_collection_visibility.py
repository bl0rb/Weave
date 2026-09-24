"""add collection visibility + per-person read ACL (read_users)

Revision ID: 0032_collection_visibility
Revises: 0031_backup_runs
Create Date: 2026-09-24

Knowledge-space sharing gains a third grant alongside the existing
`read_teams` ACL: specific individual people (`read_users`, a JSON list of
Weave-Ingest user ids as strings -- same "list of strings" idiom
`read_teams` already uses, just user ids instead of team names). `visibility`
becomes the single authoritative public/restricted flag everywhere in the
system -- see app/models/models.py's Collection docstring -- replacing the
previous implicit contract that an EMPTY `read_teams` meant "public" (see
0014_collection_registry's own docstring and the Collections contract). A
RESTRICTED collection with neither teams nor users configured is readable
only by its owner/managers/admins (fail closed) from here on -- nothing in
THIS migration changes that enforcement, it only adds the column the rest
of the stack reads instead of inferring it from `read_teams` alone.

Backfill: `visibility = 'public'` for every row where `read_teams` was
already empty (preserves each existing collection's actual current
readability byte-for-byte), else `'restricted'` -- one UPDATE per
non-empty-`read_teams` row, since (like 0014's slug backfill) there is no
single SQL expression for "is this JSON array non-empty" that is portable
across both sqlite and postgres. `read_users` backfills to `[]` for every
row via a plain server_default, same as `read_teams` itself got in 0014 --
there is no existing data to derive it from.

sqlite-compatible on purpose (batch_alter_table, no postgres-only DDL,
native_enum=False on the new `visibility` enum -- same reasoning as
0004_auth's `role` column: plain VARCHAR + CHECK constraint on every
dialect, no CREATE TYPE/DROP TYPE step needed on postgres either), so
tests/test_migrations.py can drive it through real alembic against sqlite.

downgrade drops both new columns; the pre-existing `read_teams` ACL data is
untouched.
"""

from alembic import op
import sqlalchemy as sa


revision = '0032_collection_visibility'
down_revision = '0031_backup_runs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.add_column(sa.Column(
            'visibility',
            sa.Enum('public', 'restricted', name='collection_visibility', native_enum=False, validate_strings=True),
            nullable=False,
            server_default='public',
        ))
        batch_op.add_column(sa.Column('read_users', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

    # Flip only the rows that were never actually public under 0014's own
    # contract (a non-empty `read_teams`) to 'restricted' -- every other row
    # keeps the 'public' column default just added above.
    bind = op.get_bind()
    collections_table = sa.table(
        'collections',
        sa.column('id', sa.String),
        sa.column('read_teams', sa.JSON),
        sa.column('visibility', sa.String),
    )
    rows = bind.execute(sa.select(collections_table.c.id, collections_table.c.read_teams)).fetchall()
    for row in rows:
        if row.read_teams:
            bind.execute(
                collections_table.update().where(collections_table.c.id == row.id).values(visibility='restricted')
            )


def downgrade() -> None:
    with op.batch_alter_table('collections') as batch_op:
        batch_op.drop_column('read_users')
        batch_op.drop_column('visibility')
