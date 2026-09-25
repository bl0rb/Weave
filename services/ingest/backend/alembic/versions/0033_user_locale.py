"""add users.locale

Revision ID: 0033_user_locale
Revises: 0032_collection_visibility
Create Date: 2026-09-25

Per-user UI language preference ('de' | 'en'), NULL meaning "no explicit
choice" (client falls back to browser/Accept-Language). Plain nullable
VARCHAR(2) validated at the API layer (UserResponse / PATCH /auth/me / PUT
/auth/handoff/identity/{user_id}/locale) rather than a DB enum -- only two
real values, not worth the CREATE TYPE/DROP TYPE dance an Enum column
brings on postgres (see 0032_collection_visibility's own docstring for the
values-vs-names Enum pitfall this sidesteps entirely).

sqlite-compatible on purpose (plain op.add_column, no postgres-only DDL),
same discipline as 0030_chat_provider_supports_tools -- see that
migration's own docstring (tests/test_migrations.py drives every migration
through real alembic against sqlite).
"""

from alembic import op
import sqlalchemy as sa


revision = '0033_user_locale'
down_revision = '0032_collection_visibility'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('locale', sa.String(length=2), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'locale')
