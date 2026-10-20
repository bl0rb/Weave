"""add managed_bots.config_extras for guard fields without a column

Revision ID: 0053_managed_bot_config_extras
Revises: 0052_markdown_objects

Runtime's GuardConfig clarify_max_options/clarify_question/
clarify_ambiguous_margin/clarify_ambiguous_question, stored as one JSON
object holding only the keys that were set (see
app/schemas/managed_bots.py's BotConfigExtras). NULL = Runtime defaults,
which is what every existing row keeps.
"""

from alembic import op
import sqlalchemy as sa

revision = '0053_managed_bot_config_extras'
down_revision = '0052_markdown_objects'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('managed_bots', sa.Column('config_extras', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('managed_bots') as batch_op:
        batch_op.drop_column('config_extras')
