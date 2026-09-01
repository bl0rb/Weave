"""Per-chunk metadata enrichment: builds the denormalized `chunks.meta`
payload (see app/models/models.py's `Chunk.meta` docstring -- copied down
from the owning Document so Weave-Retrieval can filter chunks directly
without joining back to `documents`) and the breadcrumb-prefixed string
that actually gets sent to the embedding provider.

Kept separate from app/services/chunker.py on purpose: the chunker is pure
and frontmatter-agnostic (it only ever sees the body), while everything
here reads app/services/chunker.py's `Chunk` plus the frontmatter dict from
`parse_frontmatter`/`chunk_markdown` -- the two inputs only meet at this
layer.
"""

from __future__ import annotations

from app.services.chunker import Chunk

# Copied down from Document's own frontmatter-derived columns (team,
# department, ...) as-is -- see app/models/models.py's Document/Chunk
# docstrings. `tags` is handled separately below since it needs an
# emptiness check rather than a None check (frontmatter's default is `[]`,
# not absence).
#
# 'collection' is the slug (see contracts/frontmatter.schema.json /
# CONTRACT "Collections" point 2) -- the same string
# app/api/events.py denormalizes onto Document.collection_slug and
# SearchFilters.collection filters by, so a plain verbatim copy here is
# exactly what Weave-Retrieval's per-chunk filter needs; no separate
# `collection_name` in meta, same as team/department never carrying a
# second display-vs-slug pair.
_DOCUMENT_FIELDS = ('source', 'original_filename', 'team', 'department', 'document_version', 'engine', 'collection')

# Confluence-only frontmatter keys (see contracts/frontmatter.schema.json)
# -- present only for pages imported from Confluence, never for a plain
# upload. `depth` can legitimately be `0` (a space-root page has no
# ancestors), so this is checked with `is not None`, never truthiness.
_CONFLUENCE_FIELDS = ('space', 'confluence_path', 'parent_title', 'depth')


def _breadcrumb(frontmatter: dict, chunk: Chunk) -> str:
    """Confluence's own path (space root -> direct parent) when the source
    page came from Confluence, else this chunk's ATX heading path -- the
    same '>'-joined line either way, since both describe "where in the
    document/space this chunk sits", just from two different sources of
    structure.
    """
    confluence_path = frontmatter.get('confluence_path')
    if confluence_path:
        return ' > '.join(confluence_path)
    if chunk.heading_path:
        return ' > '.join(chunk.heading_path)
    return ''


def build_chunk_meta(frontmatter: dict, chunk: Chunk) -> dict:
    """Assemble the `meta` dict for one `Chunk` row.

    Only keys that actually have a value end up in the result -- an absent
    frontmatter field (or an empty tags/heading_path list) is left out
    entirely rather than stored as `None`/`[]`, so `chunks.meta` never
    accumulates null-value clutter Weave-Retrieval would otherwise have to
    filter back out.
    """
    meta: dict = {}

    for key in _DOCUMENT_FIELDS:
        value = frontmatter.get(key)
        if value is not None:
            meta[key] = value

    tags = frontmatter.get('tags')
    if tags:
        meta['tags'] = tags

    if chunk.page_start is not None:
        meta['page_start'] = chunk.page_start
    if chunk.page_end is not None:
        meta['page_end'] = chunk.page_end
    if chunk.heading_path:
        meta['heading_path'] = chunk.heading_path

    for key in _CONFLUENCE_FIELDS:
        value = frontmatter.get(key)
        if value is not None:
            meta[key] = value
    # `breadcrumb` is grouped with the Confluence fields above: it is only
    # ever added here when the page actually came from Confluence
    # (confluence_path present). embedding_input's own breadcrumb below
    # additionally falls back to heading_path for a non-Confluence
    # document -- that fallback is an embedding-input concern, not
    # something worth persisting into every chunk's stored metadata.
    if frontmatter.get('confluence_path'):
        meta['breadcrumb'] = ' > '.join(frontmatter['confluence_path'])

    return meta


def embedding_input(chunk: Chunk, frontmatter: dict) -> str:
    """The text actually sent to the embedding provider: a breadcrumb line
    (Confluence path if this came from Confluence, else the chunk's
    heading path) followed by a blank line and the chunk's stored text.

    `chunk.text` itself is never mutated -- the breadcrumb is a
    presentation concern for the embedding model, not part of what gets
    stored or shown back to a user (see chunker.Chunk's docstring).
    """
    breadcrumb = _breadcrumb(frontmatter, chunk)
    if not breadcrumb:
        return chunk.text
    return f'{breadcrumb}\n\n{chunk.text}'
