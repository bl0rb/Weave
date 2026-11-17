"""Version comparison: app/services/document_diff.py (pure) and GET
/api/v1/portal/documents/{job_id}/diff (app/api/document_freshness.py)."""

from app.models.models import Job
from app.services.document_diff import changed_text, diff_markdown, split_sections, strip_frontmatter
from tests.conftest import login_as
from tests.test_document_freshness import _db, _setup, _space, make_doc, markdown, release  # noqa: F401

BASE = """---
job_id: a
document_version: 1
---

Einleitung bleibt.

# Reisekosten

Allgemeine Regeln.

## Tagegeld

Inland: 24 Euro.
Ausland: nach Tabelle.
Antrag binnen 30 Tagen.

## Übernachtung

Bis 80 Euro pro Nacht.

# Alt

Wird entfernt.
"""

TARGET = """---
job_id: b
document_version: 2
---

Einleitung bleibt.

# Reisekosten

Allgemeine Regeln.

## Tagegeld

Inland: 28 Euro.
Ausland: nach Tabelle.
Antrag binnen 60 Tagen.
Belege digital einreichen.

## Übernachtung

Bis 80 Euro pro Nacht.

# Neu

Neuer Abschnitt.
"""


def test_strip_frontmatter_and_sections():
    assert strip_frontmatter('---\na: 1\n---\nText') == 'Text'
    assert strip_frontmatter('Kein Frontmatter') == 'Kein Frontmatter'
    sections = split_sections(BASE)
    assert [section.heading_path for section in sections] == [
        [], ['Reisekosten'], ['Reisekosten', 'Tagegeld'], ['Reisekosten', 'Übernachtung'], ['Alt'],
    ]
    assert sections[2].lines == ['Inland: 24 Euro.', 'Ausland: nach Tabelle.', 'Antrag binnen 30 Tagen.']


def test_headings_inside_code_fences_do_not_split():
    sections = split_sections('# A\n\n```\n# kein Titel\n```\n')
    assert [section.heading_path for section in sections] == [['A']]
    assert '# kein Titel' in sections[0].lines


def test_diff_markdown_sections_hunks_stats_and_summary():
    diff = diff_markdown(BASE, TARGET)
    changes = {tuple(section['heading_path']): section for section in diff['sections']}
    assert set(changes) == {('Reisekosten', 'Tagegeld'), ('Neu',), ('Alt',)}  # unchanged sections omitted
    tagegeld = changes[('Reisekosten', 'Tagegeld')]
    assert tagegeld['change'] == 'changed'
    assert (tagegeld['added_lines'], tagegeld['removed_lines']) == (3, 2)
    ops = [hunk['op'] for hunk in tagegeld['hunks']]
    assert ops == ['replace', 'equal', 'replace']
    assert tagegeld['hunks'][0] == {'op': 'replace', 'base': ['Inland: 24 Euro.'], 'target': ['Inland: 28 Euro.']}
    assert tagegeld['hunks'][2]['target'] == ['Antrag binnen 60 Tagen.', 'Belege digital einreichen.']
    assert changes[('Neu',)]['change'] == 'added' and changes[('Alt',)]['change'] == 'removed'
    assert diff['stats'] == {
        'added_lines': 4, 'removed_lines': 3, 'sections_added': 1, 'sections_removed': 1, 'sections_changed': 1,
    }
    assert diff['summary'] == [
        'Abschnitt „Reisekosten › Tagegeld“ geändert: 2 Zeilen ersetzt, 1 hinzugefügt',
        'Abschnitt „Neu“ hinzugefügt (1 Zeile)',
        'Abschnitt „Alt“ entfernt (1 Zeile)',
    ]
    texts = changed_text(diff)
    assert 'Inland: 28 Euro.' in texts and 'Inland: 24 Euro.' in texts and 'Tagegeld' in texts
    assert 'Bis 80 Euro pro Nacht.' not in texts


def test_equal_context_is_trimmed_to_two_lines():
    base = '# A\n\n' + '\n'.join(f'Zeile {i}' for i in range(10)) + '\n'
    target = base.replace('Zeile 5', 'Zeile fünf')
    hunks = diff_markdown(base, target)['sections'][0]['hunks']
    assert [hunk['op'] for hunk in hunks] == ['equal', 'replace', 'equal']
    assert hunks[0]['base'] == ['Zeile 3', 'Zeile 4'] and hunks[2]['base'] == ['Zeile 6', 'Zeile 7']


def test_identical_and_frontmatter_only_changes_report_nothing():
    diff = diff_markdown(BASE, BASE.replace('job_id: a', 'job_id: z'))
    assert diff['sections'] == [] and diff['summary'] == ['Keine inhaltlichen Änderungen']
    assert diff['stats']['added_lines'] == 0


def test_duplicate_headings_pair_by_order_and_root_label():
    base = 'Vorwort alt.\n\n# FAQ\n\neins\n\n# FAQ\n\nzwei\n'
    target = 'Vorwort neu.\n\n# FAQ\n\neins\n\n# FAQ\n\nzwei!\n'
    diff = diff_markdown(base, target)
    assert [section['heading_path'] for section in diff['sections']] == [[], ['FAQ']]
    assert diff['summary'][0].startswith('Abschnitt „Einleitung“ geändert')


# --- endpoint ---------------------------------------------------------------------


def _chain(space, client):
    v1 = make_doc(space, '# Reisekosten\n\nTagegeld 24 Euro.\n\n![Plan](artifacts/plan.png)\n')
    release(client, v1)
    v2 = make_doc(space, '# Reisekosten\n\nTagegeld 28 Euro.\n\n![Plan](artifacts/plan.png)\n', previous=v1, version=2)
    release(client, v2)
    v3 = make_doc(space, '# Reisekosten\n\nTagegeld 30 Euro.\n\n![Plan](artifacts/plan.png)\n', previous=v2, version=3)
    return v1, v2, v3


def test_diff_endpoint_default_and_explicit_base():
    space = _space()
    client = login_as(space.member.username)
    v1, v2, v3 = _chain(space, client)
    response = client.get(f'/api/v1/portal/documents/{v2}/diff')
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['base']['job_id'], body['base']['document_version']) == (v1, 1)
    assert (body['target']['job_id'], body['target']['document_version']) == (v2, 2)
    # Release snapshots' absolute image URLs differ per release: no noise.
    assert body['stats']['sections_changed'] == 1 and body['stats']['added_lines'] == 1
    assert body['summary'] == ['Abschnitt „Reisekosten“ geändert: 1 Zeile ersetzt']
    assert body['sections'][0]['hunks'][0] == {'op': 'replace', 'base': ['Tagegeld 24 Euro.'], 'target': ['Tagegeld 28 Euro.']}

    # Unreleased target: canonical snapshot; explicit older base.
    explicit = client.get(f'/api/v1/portal/documents/{v3}/diff', params={'base': v1}).json()
    assert explicit['base']['job_id'] == v1
    assert explicit['sections'][0]['hunks'][0]['target'] == ['Tagegeld 30 Euro.']


def test_diff_endpoint_errors_and_visibility():
    space = _space()
    client = login_as(space.owner.username)
    v1, v2, _v3 = _chain(space, client)
    assert client.get(f'/api/v1/portal/documents/{v1}/diff').status_code == 409  # no previous version
    assert client.get(f'/api/v1/portal/documents/{v2}/diff', params={'base': v2}).status_code == 422
    other = make_doc(space, filename='anderes.pdf')
    assert client.get(f'/api/v1/portal/documents/{v2}/diff', params={'base': other}).status_code == 404
    other_space = _space()
    foreign = make_doc(other_space)
    assert client.get(f'/api/v1/portal/documents/{v2}/diff', params={'base': foreign}).status_code == 404
    assert client.get(f'/api/v1/portal/documents/{v2}/diff', params={'base': 'missing'}).status_code == 404
    for user in (space.reader, space.outsider):
        assert login_as(user.username).get(f'/api/v1/portal/documents/{v2}/diff').status_code == 404


def test_diff_endpoint_without_chain_link_is_409():
    space = _space()
    client = login_as(space.owner.username)
    v1 = make_doc(space)
    v2 = make_doc(space, '# Neu\n', previous=v1, version=2)
    with _db() as db:
        db.get(Job, v2).previous_job_id = None
        db.commit()
    assert client.get(f'/api/v1/portal/documents/{v2}/diff').status_code == 409
    assert markdown('x').startswith('---\n')
