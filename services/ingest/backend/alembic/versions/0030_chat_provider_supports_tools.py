"""add chat_provider_config.supports_tools

Revision ID: 0030_chat_provider_supports_tools
Revises: 0029_technical_identities
Create Date: 2026-09-19

sqlite-compatible on purpose (plain op.add_column, no postgres-only DDL),
same discipline as 0013_webhooks -- see that migration's own docstring for
why (tests/test_migrations.py drives every migration through real alembic
against sqlite).

Revision id kept <= 32 chars on purpose -- see 0012's docstring for the
production CrashLoopBackOff a longer id caused once, since PostgreSQL's
alembic_version.version_num column is VARCHAR(32) where sqlite (the test
database) silently isn't.
"""

from alembic import op
import sqlalchemy as sa


revision = '0030_chat_provider_supports_'
down_revision = '0029_technical_identities'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'chat_provider_config',
        sa.Column('supports_tools', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('chat_provider_config', 'supports_tools')
