"""Tests for app/services/enrichment.py."""

from __future__ import annotations

from app.services.chunker import Chunk
from app.services.enrichment import build_chunk_meta, embedding_input


def _chunk(**overrides) -> Chunk:
    defaults = dict(
        text='chunk body text', chunk_index=0, heading_path=[],
        page_start=None, page_end=None, char_count=16,
    )
    defaults.update(overrides)
    return Chunk(**defaults)


# --- build_chunk_meta: non-Confluence (plain upload) frontmatter ---------------


def test_build_chunk_meta_non_confluence_frontmatter():
    frontmatter = {
        'source': 'doc.pdf',
        'original_filename': 'Handbuch.pdf',
        'team': 'Kundenservice',
        'department': 'Finance',
        'tags': ['rechnung', 'q3'],
        'document_version': 2,
        'engine': 'paddleocr',
        # No Confluence keys at all.
    }
    chunk = _chunk(heading_path=['Setup', 'Docker'], page_start=1, page_end=2)

    meta = build_chunk_meta(frontmatter, chunk)

    assert meta == {
        'source': 'doc.pdf',
        'original_filename': 'Handbuch.pdf',
        'team': 'Kundenservice',
        'department': 'Finance',
        'tags': ['rechnung', 'q3'],
        'document_version': 2,
        'engine': 'paddleocr',
        'page_start': 1,
        'page_end': 2,
        'heading_path': ['Setup', 'Docker'],
    }
    assert 'space' not in meta
    assert 'confluence_path' not in meta
    assert 'parent_title' not in meta
    assert 'depth' not in meta
    assert 'breadcrumb' not in meta
    assert 'collection' not in meta


# --- build_chunk_meta: Collections (contract point 5) ---------------------------


def test_build_chunk_meta_copies_collection_slug_from_frontmatter():
    frontmatter = {'source': 'doc.pdf', 'engine': 'paddleocr', 'collection': 'handbuch'}
    meta = build_chunk_meta(frontmatter, _chunk())
    assert meta['collection'] == 'handbuch'


def test_build_chunk_meta_omits_collection_when_frontmatter_has_none():
    frontmatter = {'source': 'doc.pdf', 'engine': 'paddleocr'}
    meta = build_chunk_meta(frontmatter, _chunk())
    assert 'collection' not in meta


def test_build_chunk_meta_never_stores_collection_name_only_the_slug():
    """'collection_name' is a display-only frontmatter field (Collections
    contract point 2) -- it must never leak into chunks.meta, same as
    team/department never carry a second slug-vs-display pair."""
    frontmatter = {
        'source': 'doc.pdf', 'engine': 'paddleocr',
        'collection': 'handbuch', 'collection_name': 'Handbuch (Anzeige)',
    }
    meta = build_chunk_meta(frontmatter, _chunk())
    assert meta['collection'] == 'handbuch'
    assert 'collection_name' not in meta


def test_build_chunk_meta_omits_missing_and_empty_fields():
    # No team/department/tags in frontmatter, no heading/page info on the
    # chunk -- none of that should show up as None/[] clutter.
    frontmatter = {'source': 'plain.txt', 'engine': 'pypdf-fallback'}
    chunk = _chunk(heading_path=[], page_start=None, page_end=None)

    meta = build_chunk_meta(frontmatter, chunk)

    assert meta == {'source': 'plain.txt', 'engine': 'pypdf-fallback'}
    assert 'tags' not in meta
    assert 'team' not in meta
    assert 'department' not in meta
    assert 'document_version' not in meta
    assert 'page_start' not in meta
    assert 'page_end' not in meta
    assert 'heading_path' not in meta


# --- build_chunk_meta: Confluence frontmatter -----------------------------------


def test_build_chunk_meta_confluence_frontmatter_adds_confluence_fields_and_breadcrumb():
    frontmatter = {
        'source': 'https://acme.atlassian.net/wiki/spaces/DOCS/pages/555/Deploying',
        'engine': 'mail-eml',  # arbitrary but present, to prove it's still copied
        'space': 'DOCS',
        'confluence_path': ['Handbook', 'Ops'],
        'parent_title': 'Ops',
        'depth': 2,
    }
    chunk = _chunk(heading_path=['Deploying'])

    meta = build_chunk_meta(frontmatter, chunk)

    assert meta['space'] == 'DOCS'
    assert meta['confluence_path'] == ['Handbook', 'Ops']
    assert meta['parent_title'] == 'Ops'
    assert meta['depth'] == 2
    assert meta['breadcrumb'] == 'Handbook > Ops'
    # heading_path is still carried too -- Confluence fields are additive,
    # not a replacement for the chunk's own ATX structure.
    assert meta['heading_path'] == ['Deploying']


def test_build_chunk_meta_confluence_depth_zero_is_kept_not_dropped():
    # depth=0 (a space-root page has no ancestors) is a real, present value
    # -- must survive the "only present fields" filter, not be treated like
    # a missing/falsy field.
    frontmatter = {'space': 'DOCS', 'confluence_path': ['Root'], 'depth': 0}
    meta = build_chunk_meta(frontmatter, _chunk())
    assert meta['depth'] == 0
    assert 'depth' in meta


def test_build_chunk_meta_space_without_confluence_path_omits_breadcrumb():
    frontmatter = {'space': 'DOCS'}
    meta = build_chunk_meta(frontmatter, _chunk(heading_path=[]))
    assert meta['space'] == 'DOCS'
    assert 'breadcrumb' not in meta
    assert 'confluence_path' not in meta


# --- embedding_input -------------------------------------------------------------


def test_embedding_input_uses_confluence_path_breadcrumb_when_present():
    frontmatter = {'confluence_path': ['Handbook', 'Ops']}
    chunk = _chunk(text='Deploy steps go here.', heading_path=['Deploying'])

    result = embedding_input(chunk, frontmatter)

    assert result == 'Handbook > Ops\n\nDeploy steps go here.'
    # The stored chunk text itself is untouched by this call.
    assert chunk.text == 'Deploy steps go here.'


def test_embedding_input_falls_back_to_heading_path_without_confluence():
    frontmatter = {'source': 'doc.pdf'}
    chunk = _chunk(text='Body content.', heading_path=['Setup', 'Docker'])

    result = embedding_input(chunk, frontmatter)

    assert result == 'Setup > Docker\n\nBody content.'


def test_embedding_input_confluence_path_takes_priority_over_heading_path():
    frontmatter = {'confluence_path': ['Handbook']}
    chunk = _chunk(text='Text.', heading_path=['Some', 'ATX', 'Path'])

    result = embedding_input(chunk, frontmatter)

    assert result.startswith('Handbook\n\n')
    assert 'Some > ATX > Path' not in result


def test_embedding_input_no_breadcrumb_available_returns_text_unchanged():
    result = embedding_input(_chunk(text='Just the text.', heading_path=[]), {})
    assert result == 'Just the text.'
