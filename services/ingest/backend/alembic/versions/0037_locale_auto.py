"""reset users.locale: language now follows the browser by default

Revision ID: 0037_locale_auto
Revises: 0036_bot_grants
Create Date: 2026-09-29

The language switch gains an Auto option (browser language via
Accept-Language) and Auto becomes the default. A stored account locale
still beats the browser wherever the account is used (weave-api mirrors
it), so this clears every stored choice once -- together with the cookie
rename in frontend/src/i18n/config.ts everyone starts on Auto and can
re-pick DE/EN explicitly.

downgrade() is a no-op: the previous choices are not recorded, so they
cannot be restored.
"""

from alembic import op
import sqlalchemy as sa


revision = '0037_locale_auto'
down_revision = '0036_bot_grants'
branch_labels = None
depends_on = None


_users = sa.table('users', sa.column('locale', sa.String))


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(_users.update().values(locale=None))


def downgrade() -> None:
    # No-op: the previous per-user choices are not recoverable (see the
    # module docstring) -- a downgrade cannot restore them.
    pass
