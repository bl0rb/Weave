"""managed bots: retrieval-guard tuning columns

Revision ID: 0047_bot_guard_tuning
Revises: 0046_bot_admin_group_sync

min_score / clarify_below / weak_context_below (rerank-score thresholds,
0..1), query_rewrite (tri-state: NULL = runtime default) and
uncertainty_hint -- each projected verbatim into Weave-Runtime's
BotConfig.guard / BotConfig.retrieval. Plain add_column, sqlite-safe.
"""
from alembic import op
import sqlalchemy as sa

revision = '0047_bot_guard_tuning'
down_revision = '0046_bot_admin_group_sync'
branch_labels = None
depends_on = None

_TABLE = 'managed_bots'


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column('min_score', sa.Float(), nullable=True))
    op.add_column(_TABLE, sa.Column('clarify_below', sa.Float(), nullable=True))
    op.add_column(_TABLE, sa.Column('weak_context_below', sa.Float(), nullable=True))
    op.add_column(_TABLE, sa.Column('query_rewrite', sa.Boolean(), nullable=True))
    op.add_column(_TABLE, sa.Column('uncertainty_hint', sa.Boolean(), nullable=False, server_default='1'))


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        for name in ('uncertainty_hint', 'query_rewrite', 'weak_context_below', 'clarify_below', 'min_score'):
            batch.drop_column(name)
