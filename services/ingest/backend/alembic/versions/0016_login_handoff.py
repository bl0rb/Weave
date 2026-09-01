"""add login_handoff_codes for the cross-service login handoff

Revision ID: 0016_login_handoff
Revises: 0015_webhook_collection_event
Create Date: 2026-09-01

sqlite-compatible on purpose (no postgres-only DDL) so
tests/test_migrations.py can drive it through real alembic against sqlite,
same as every migration since 0004_auth.

Backs app/models/models.LoginHandoffCode: the one-time code that hands an
identity authenticated here over to Weave-API, so the chat UI can use this
service's users, teams and OIDC connections instead of a second account
world of its own. CASCADE on user_id (not SET NULL, unlike the audit-ish
webhook_deliveries rows): a code is a live credential for exactly one
account and must die with it, not outlive it as a record.
"""

from alembic import op
import sqlalchemy as sa


revision = '0016_login_handoff'
down_revision = '0015_webhook_collection_event'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'login_handoff_codes',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name='fk_login_handoff_codes_user_id', ondelete='CASCADE'
        ),
    )
    # Unique: the lookup key. A collision would mean two accounts share one
    # credential, so let the database refuse it rather than trusting the
    # generator.
    op.create_index('ix_login_handoff_codes_code_hash', 'login_handoff_codes', ['code_hash'], unique=True)
    op.create_index('ix_login_handoff_codes_user_id', 'login_handoff_codes', ['user_id'])
    op.create_index('ix_login_handoff_codes_expires_at', 'login_handoff_codes', ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_login_handoff_codes_expires_at', table_name='login_handoff_codes')
    op.drop_index('ix_login_handoff_codes_user_id', table_name='login_handoff_codes')
    op.drop_index('ix_login_handoff_codes_code_hash', table_name='login_handoff_codes')
    op.drop_table('login_handoff_codes')
