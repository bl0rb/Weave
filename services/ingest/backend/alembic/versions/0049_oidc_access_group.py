"""Optional OIDC access group per login provider, with its denial message

Revision ID: 0049_oidc_access_group
Revises: 0048_oidc_role_groups

sqlite-compatible on purpose (plain add_column), see 0041's docstring.
"""

from alembic import op
import sqlalchemy as sa


revision = '0049_oidc_access_group'
down_revision = '0048_oidc_role_groups'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('auth_providers', sa.Column('access_group', sa.String(255), nullable=True))
    op.add_column('auth_providers', sa.Column('access_denied_message', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('auth_providers', 'access_denied_message')
    op.drop_column('auth_providers', 'access_group')
