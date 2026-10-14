"""Admin / bot-admin role derived from OIDC groups

Revision ID: 0048_oidc_role_groups
Revises: 0047_bot_guard_tuning

sqlite-compatible on purpose (batch_alter_table), see 0041's docstring.
"""

from alembic import op
import sqlalchemy as sa


revision = '0048_oidc_role_groups'
down_revision = '0047_bot_guard_tuning'
branch_labels = None
depends_on = None


def _restore_email_index() -> None:
    # On sqlite the batch rebuild drops the expression index from 0004_auth
    # (SQLAlchemy cannot reflect lower(email)), see 0011_login_lockout.
    op.create_index(
        'ix_users_email_lower',
        'users',
        [sa.text('lower(email)')],
        unique=True,
        if_not_exists=True,
    )


def upgrade() -> None:
    op.add_column('auth_providers', sa.Column('admin_group', sa.String(255), nullable=True))
    op.add_column('auth_providers', sa.Column('bot_admin_group', sa.String(255), nullable=True))
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('role_from_oidc', sa.Boolean(), nullable=False, server_default='0'))
    _restore_email_index()


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('role_from_oidc')
    _restore_email_index()
    op.drop_column('auth_providers', 'bot_admin_group')
    op.drop_column('auth_providers', 'admin_group')
