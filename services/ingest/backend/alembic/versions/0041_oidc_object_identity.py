"""Store verified Entra object identities for MCP OAuth.

Revision ID: 0041_oidc_object_identity
Revises: 0040_space_scoped_page_states
"""

from alembic import op
import sqlalchemy as sa

revision = '0041_oidc_object_identity'
down_revision = '0040_space_scoped_page_states'
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
        batch.add_column(sa.Column('oidc_object_id', sa.String(36), nullable=True))
        batch.create_unique_constraint('uq_users_oidc_provider_object', ['oidc_provider_id', 'oidc_object_id'])
    _restore_email_index()


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint('uq_users_oidc_provider_object', type_='unique')
        batch.drop_column('oidc_object_id')
    _restore_email_index()
