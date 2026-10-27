"""website connections + source catalog

Revision ID: 0056_source_catalog_website
Revises: 0055_managed_bot_intake

- import_sources.kind ('confluence' | 'website') and allow_private_target
  (admin-released intranet target for website connections).
- import_page_states.page_id widened: a website page is keyed by its URL.
- source_catalog_entries: the shared, admin-curated source catalog, seeded
  with a few public legal sources (gesetze-im-internet.de). Entries are
  templates only -- importing one still needs a knowledge space the user
  controls and a confirmed scope.
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '0056_source_catalog_website'
down_revision = '0055_managed_bot_intake'
branch_labels = None
depends_on = None

_GII = 'https://www.gesetze-im-internet.de'
_SEED_ENTRIES = [
    {
        'id': 'c0a80001-0000-4000-8000-000000000001',
        'title': 'Verwaltungsverfahrensgesetz (VwVfG)',
        'description': 'Gesamtausgabe des VwVfG auf „Gesetze im Internet“. Der Umfang bleibt auf das Gesetz begrenzt.',
        'category': 'legal', 'publisher': 'Bundesministerium der Justiz / juris',
        'start_url': f'{_GII}/vwvfg/', 'crawl_scope': 'prefix', 'legal_source': True, 'interval_seconds': 604800, 'max_pages': 150,
    },
    {
        'id': 'c0a80001-0000-4000-8000-000000000002',
        'title': 'Informationsfreiheitsgesetz (IFG)',
        'description': 'Gesetz zur Regelung des Zugangs zu Informationen des Bundes, Gesamtausgabe auf „Gesetze im Internet“.',
        'category': 'legal', 'publisher': 'Bundesministerium der Justiz / juris',
        'start_url': f'{_GII}/ifg/', 'crawl_scope': 'prefix', 'legal_source': True, 'interval_seconds': 604800, 'max_pages': 50,
    },
    {
        'id': 'c0a80001-0000-4000-8000-000000000003',
        'title': 'Onlinezugangsgesetz (OZG)',
        'description': 'Gesetz zur Verbesserung des Onlinezugangs zu Verwaltungsleistungen, Gesamtausgabe auf „Gesetze im Internet“.',
        'category': 'legal', 'publisher': 'Bundesministerium der Justiz / juris',
        'start_url': f'{_GII}/ozg/', 'crawl_scope': 'prefix', 'legal_source': True, 'interval_seconds': 604800, 'max_pages': 50,
    },
    {
        'id': 'c0a80001-0000-4000-8000-000000000004',
        'title': 'Bundesdatenschutzgesetz (BDSG)',
        'description': 'Gesamtausgabe des BDSG auf „Gesetze im Internet“.',
        'category': 'legal', 'publisher': 'Bundesministerium der Justiz / juris',
        'start_url': f'{_GII}/bdsg_2018/', 'crawl_scope': 'prefix', 'legal_source': True, 'interval_seconds': 604800, 'max_pages': 150,
    },
]


def upgrade() -> None:
    with op.batch_alter_table('import_sources') as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=16), nullable=False, server_default='confluence'))
        batch_op.add_column(sa.Column('allow_private_target', sa.Boolean(), nullable=False, server_default='0'))
    with op.batch_alter_table('import_page_states') as batch_op:
        batch_op.alter_column('page_id', existing_type=sa.String(length=64), type_=sa.String(length=2048), existing_nullable=False)

    table = op.create_table(
        'source_catalog_entries',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('publisher', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='website'),
        sa.Column('start_url', sa.String(length=512), nullable=False),
        sa.Column('crawl_scope', sa.String(length=16), nullable=False, server_default='prefix'),
        sa.Column('include_linked_documents', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('max_pages', sa.Integer(), nullable=True),
        sa.Column('interval_seconds', sa.Integer(), nullable=True),
        sa.Column('legal_source', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(table, [
        {
            'kind': 'website', 'include_linked_documents': False, 'created_by': None,
            'created_at': now, 'updated_at': now, **entry,
        }
        for entry in _SEED_ENTRIES
    ])


def downgrade() -> None:
    op.drop_table('source_catalog_entries')
    with op.batch_alter_table('import_page_states') as batch_op:
        batch_op.alter_column('page_id', existing_type=sa.String(length=2048), type_=sa.String(length=64), existing_nullable=False)
    with op.batch_alter_table('import_sources') as batch_op:
        batch_op.drop_column('allow_private_target')
        batch_op.drop_column('kind')
