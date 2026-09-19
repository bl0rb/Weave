"""add technical_identities + technical_identity_audit (Schritt 5)

Revision ID: 0029_technical_identities
Revises: 0028_managed_bot_agent_config
Create Date: 2026-09-19

sqlite-compatible on purpose (plain op.create_table, no postgres-only DDL),
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


revision = '0029_technical_identities'
down_revision = '0028_managed_bot_agent_config'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'technical_identities',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        # Default '[]' = no knowledge access at all until an admin grants
        # collections -- see app/models/models.py's TechnicalIdentity docstring.
        sa.Column('allowed_collections', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('token_prefix', sa.String(length=12), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        if_not_exists=True,
    )
    op.create_index(
        'ix_technical_identities_token_hash', 'technical_identities', ['token_hash'], unique=True, if_not_exists=True
    )
    op.create_index(
        'ix_technical_identities_created_by', 'technical_identities', ['created_by'], if_not_exists=True
    )

    op.create_table(
        'technical_identity_audit',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column(
            'identity_id',
            sa.String(length=36),
            sa.ForeignKey('technical_identities.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('event', sa.String(length=32), nullable=False),
        sa.Column('actor', sa.String(length=255), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        if_not_exists=True,
    )
    op.create_index(
        'ix_technical_identity_audit_identity_id', 'technical_identity_audit', ['identity_id'], if_not_exists=True
    )
    op.create_index(
        'ix_technical_identity_audit_created_at', 'technical_identity_audit', ['created_at'], if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index('ix_technical_identity_audit_created_at', table_name='technical_identity_audit')
    op.drop_index('ix_technical_identity_audit_identity_id', table_name='technical_identity_audit')
    op.drop_table('technical_identity_audit')

    op.drop_index('ix_technical_identities_created_by', table_name='technical_identities')
    op.drop_index('ix_technical_identities_token_hash', table_name='technical_identities')
    op.drop_table('technical_identities')
