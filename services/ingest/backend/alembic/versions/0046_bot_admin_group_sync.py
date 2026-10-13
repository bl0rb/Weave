"""bot_admin role and OIDC group -> team sync

Revision ID: 0046_bot_admin_group_sync
Revises: 0045_llm_endpoints

`users.role` was created by 0004 as a non-native enum, i.e. VARCHAR sized
to its longest value ('admin'); 'bot_admin' needs a wider column.

sqlite-compatible on purpose (batch_alter_table), see 0041's docstring.
"""

from alembic import op
import sqlalchemy as sa


revision = '0046_bot_admin_group_sync'
down_revision = '0045_llm_endpoints'
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
    with op.batch_alter_table('users') as batch:
        batch.alter_column('role', existing_type=sa.String(5), type_=sa.String(16), existing_nullable=False)
    _restore_email_index()
    op.add_column('auth_providers', sa.Column('sync_groups', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column(
        'auth_providers', sa.Column('groups_claim', sa.String(255), nullable=False, server_default='groups')
    )
    with op.batch_alter_table('teams') as batch:
        batch.add_column(sa.Column('oidc_provider_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('oidc_group', sa.String(255), nullable=True))
        batch.create_foreign_key(
            'fk_teams_oidc_provider_id', 'auth_providers', ['oidc_provider_id'], ['id'], ondelete='SET NULL'
        )
        batch.create_unique_constraint('uq_teams_oidc_provider_group', ['oidc_provider_id', 'oidc_group'])
    with op.batch_alter_table('user_teams') as batch:
        batch.add_column(sa.Column('oidc_provider_id', sa.String(36), nullable=True))
        batch.create_foreign_key(
            'fk_user_teams_oidc_provider_id', 'auth_providers', ['oidc_provider_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('user_teams') as batch:
        batch.drop_constraint('fk_user_teams_oidc_provider_id', type_='foreignkey')
        batch.drop_column('oidc_provider_id')
    with op.batch_alter_table('teams') as batch:
        batch.drop_constraint('uq_teams_oidc_provider_group', type_='unique')
        batch.drop_constraint('fk_teams_oidc_provider_id', type_='foreignkey')
        batch.drop_column('oidc_group')
        batch.drop_column('oidc_provider_id')
    op.drop_column('auth_providers', 'groups_claim')
    op.drop_column('auth_providers', 'sync_groups')
    op.execute("UPDATE users SET role = 'user' WHERE role = 'bot_admin'")
    with op.batch_alter_table('users') as batch:
        batch.alter_column('role', existing_type=sa.String(16), type_=sa.String(5), existing_nullable=False)
    _restore_email_index()
