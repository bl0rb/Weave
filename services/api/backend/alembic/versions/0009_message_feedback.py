"""Answer feedback (`message_feedback`) and the quality-signal outbox
(`quality_signal_outbox`) -- see app/models/models.py's MessageFeedback /
QualitySignalOutbox and app/services/quality_signals.py.

Revision ID: 0009_message_feedback
Revises: 0008_exchange_code_challenge
"""

from alembic import op
import sqlalchemy as sa


revision = '0009_message_feedback'
down_revision = '0008_exchange_code_challenge'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'message_feedback',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            'message_id', sa.Integer(),
            sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column(
            'user_id', sa.Uuid(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('rating', sa.String(length=16), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('message_id', 'user_id', name='uq_message_feedback_message_user'),
    )
    op.create_index('ix_message_feedback_message_id', 'message_feedback', ['message_id'])
    op.create_index('ix_message_feedback_user_id', 'message_feedback', ['user_id'])

    op.create_table(
        'quality_signal_outbox',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('signal_key', sa.String(length=128), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('forwarded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.String(length=500), nullable=True),
    )
    op.create_index('ix_quality_signal_outbox_signal_key', 'quality_signal_outbox', ['signal_key'], unique=True)
    op.create_index('ix_quality_signal_outbox_forwarded_at', 'quality_signal_outbox', ['forwarded_at'])


def downgrade() -> None:
    op.drop_table('quality_signal_outbox')
    op.drop_table('message_feedback')
