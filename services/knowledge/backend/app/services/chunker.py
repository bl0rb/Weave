"""Structure-aware chunking of Weave-Ingest markdown (see app/services --
this is the "strukturbewusstes Chunking" stage the README's Zweck section
names).

Pure and DB-free by design: every function here takes plain strings/dicts
and returns dataclasses, no ORM/session involved -- Document/Chunk rows are
assembled from this module's output one layer up (app/workers, once that
exists), same separation Weave-Ingest keeps between its converters
(app/services/*.py) and its persistence layer.

Pipeline:

1. `parse_frontmatter` -- splits Weave-Ingest's `---\\n<yaml>\\n---\\n\\n<body>`
   envelope (see contracts/frontmatter.schema.json and
   app/services/confluence_markdown.py's `render_frontmatter` in
   Weave-Ingest, the producer of this exact shape) into (frontmatter dict,
   body). Tolerant of missing or malformed frontmatter -- never raises.
2. `_segment_blocks` -- walks the body line-by-line, tracking the current
   page (from `<!-- page:N -->` / `<!-- page:N/M -->` markers, which are
   consumed and never appear in block text) and an ATX heading stack
   (level -> path), and groups lines into typed blocks: heading, table
   (contiguous GFM pipe-rows incl. divider), code (fenced ``` / ~~~), or
   paragraph (everything else, blank-line separated). Tables and code
   fences are atomic -- they are never split across chunks.
3. `_assemble_chunks` -- packs blocks sequentially into `Chunk`s bounded by
   `CHUNK_MAX_CHARS`, carrying a `CHUNK_OVERLAP_CHARS` prefix from the tail
   of a paragraph block into the next chunk when a chunk is closed purely
   because it hit the size limit (never across a heading-driven boundary,
   and never when the tail block was an atomic table/code block --
   duplicating half a table would corrupt it). An ATX heading at level 1 or
   2 always closes the current chunk first: that is a semantic boundary,
   not a size one, so no overlap crosses it.

`heading_path` is carried on every chunk so a later stage can prefix the
embedding input with a breadcrumb (see app/services/enrichment.py) without
the chunker needing to know anything about embeddings itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from app.core.config import settings

# `<!-- page:N -->` or `<!-- page:N/M -->` -- the total page count (M) is not
# needed here (page tracking only cares about the current page number), so
# it is matched but discarded.
_PAGE_MARKER_RE = re.compile(r'^<!--\s*page:\s*(\d+)(?:\s*/\s*\d+)?\s*-->$')
# ATX heading: 1-6 '#' followed by at least one space/tab. A hash run with
# no following whitespace ('#NoSpace') or seven+ hashes is not a heading --
# same rule test_render_block_content.py exercises for Weave-Ingest's own
# heading detection.
_ATX_HEADING_RE = re.compile(r'^(#{1,6})[ \t]+(.*)$')
# Opening code fence: a run of 3+ backticks or 3+ tildes. Captured (not just
# matched) because the closing fence must use the SAME character and be at
# least as long as the opening one (CommonMark's fence-matching rule) --
# e.g. a stray '```' inside a ```` ```` -fenced block must not end it early.
_CODE_FENCE_OPEN_RE = re.compile(r'^(`{3,}|~{3,})')
_DIVIDER_CELL_RE = re.compile(r'^:?-+:?$')


@dataclass
class Chunk:
    """One structure-aware slice of a document body, ready to be persisted
    as a `Chunk` row (see app/models/models.py) once embedded.

    `text` is the stored/retrieved chunk text -- NOT what gets embedded.
    The embedding input (breadcrumb-prefixed) is built separately by
    app/services/enrichment.py's `embedding_input`, since the breadcrumb
    depends on frontmatter this module never sees.
    """

    text: str
    chunk_index: int
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    char_count: int


@dataclass
class _Block:
    """One segmented unit of the body, before chunk assembly."""

    kind: str  # 'heading' | 'table' | 'code' | 'paragraph'
    text: str
    page: int | None
    heading_path: list[str] = field(default_factory=list)
    heading_level: int | None = None  # only set for kind == 'heading'


def parse_frontmatter(markdown: str) -> tuple[dict, str]:
    """Split a Weave-Ingest markdown document into (frontmatter, body).

    Mirrors the exact envelope `confluence_markdown.render_frontmatter` /
    `paddle_service._prepend_frontmatter` produce in Weave-Ingest: an
    opening `---\\n`, the YAML block, a closing `\\n---\\n`, then the body
    verbatim. Tolerant on both ends:

    - no frontmatter at all (`markdown` doesn't open with `---\\n`, or the
      opening marker is never closed) -- returns `({}, markdown)` unchanged;
    - a frontmatter block that fails to parse as YAML -- the (structurally
      recognizable) block is still stripped so the body stays clean, but
      the returned frontmatter is `{}` rather than raising;
    - a frontmatter block that parses to something other than a mapping
      (e.g. a bare scalar) -- also normalized to `{}`.
    """
    if not markdown.startswith('---\n'):
        return {}, markdown
    try:
        end = markdown.index('\n---\n', 3)
    except ValueError:
        return {}, markdown  # opening marker with no closing marker
    raw_yaml = markdown[4:end + 1]
    body = markdown[end + len('\n---\n'):]
    try:
        loaded = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        return {}, body
    if not isinstance(loaded, dict):
        loaded = {}
    return loaded, body


def _is_divider_row(line: str) -> bool:
    """True for a GFM table delimiter row, e.g. `| --- | :---: |` or
    `--- | ---` (outer pipes optional per the GFM spec)."""
    stripped = line.strip()
    if '-' not in stripped:
        return False
    cells = [cell.strip() for cell in stripped.strip('|').split('|')]
    if not cells:
        return False
    return all(_DIVIDER_CELL_RE.match(cell) for cell in cells)


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def _segment_blocks(body: str) -> list[_Block]:
    lines = body.split('\n')
    blocks: list[_Block] = []
    heading_stack: list[tuple[int, str]] = []
    current_page: int | None = None
    paragraph_lines: list[str] = []

    def heading_path() -> list[str]:
        return [text for _, text in heading_stack]

    def flush_paragraph() -> None:
        if not paragraph_lines:
            return
        text = '\n'.join(paragraph_lines).strip('\n')
        paragraph_lines.clear()
        if text.strip():
            blocks.append(_Block(kind='paragraph', text=text, page=current_page, heading_path=heading_path()))

    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        stripped = line.strip()

        marker_match = _PAGE_MARKER_RE.match(stripped)
        if marker_match:
            flush_paragraph()
            current_page = int(marker_match.group(1))
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            index += 1
            continue

        heading_match = _ATX_HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            blocks.append(_Block(
                kind='heading', text=line.rstrip(), page=current_page,
                heading_path=heading_path(), heading_level=level,
            ))
            index += 1
            continue

        fence_match = _CODE_FENCE_OPEN_RE.match(stripped)
        if fence_match:
            flush_paragraph()
            fence_marker = fence_match.group(1)
            fence_char = fence_marker[0]
            min_closing_len = len(fence_marker)
            fence_lines = [line]
            index += 1
            while index < total:
                candidate = lines[index].strip()
                fence_lines.append(lines[index])
                index += 1
                if candidate and len(candidate) >= min_closing_len and set(candidate) == {fence_char}:
                    break
            blocks.append(_Block(
                kind='code', text='\n'.join(fence_lines), page=current_page, heading_path=heading_path(),
            ))
            continue

        if _looks_like_table_row(line) and index + 1 < total and _is_divider_row(lines[index + 1]):
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < total and _looks_like_table_row(lines[index]):
                table_lines.append(lines[index])
                index += 1
            blocks.append(_Block(
                kind='table', text='\n'.join(table_lines), page=current_page, heading_path=heading_path(),
            ))
            continue

        paragraph_lines.append(line)
        index += 1

    flush_paragraph()
    return blocks


def _assemble_chunks(blocks: list[_Block], *, max_chars: int, overlap_chars: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[_Block] = []
    current_overlap = ''
    current_heading_path: list[str] = []

    def projected_text(extra: str | None = None) -> str:
        parts = ([current_overlap] if current_overlap else []) + [b.text for b in current]
        if extra is not None:
            parts.append(extra)
        return '\n\n'.join(parts)

    def close_current(*, allow_overlap: bool) -> None:
        nonlocal current, current_overlap
        if not current:
            current_overlap = ''
            return
        text = projected_text()
        pages = [b.page for b in current if b.page is not None]
        last_kind = current[-1].kind
        chunks.append(Chunk(
            text=text,
            chunk_index=len(chunks),
            heading_path=list(current_heading_path),
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            char_count=len(text),
        ))
        current = []
        wants_overlap = allow_overlap and overlap_chars > 0 and last_kind == 'paragraph'
        current_overlap = text[-overlap_chars:] if wants_overlap else ''

    for block in blocks:
        is_semantic_boundary = block.kind == 'heading' and block.heading_level in (1, 2)

        if is_semantic_boundary and current:
            close_current(allow_overlap=False)
        elif current and len(projected_text(block.text)) > max_chars:
            close_current(allow_overlap=True)

        if is_semantic_boundary and not current:
            # A heading boundary must never carry size-triggered overlap
            # from the chunk it just closed into the new section.
            current_overlap = ''

        if not current or block.kind == 'heading':
            # Captured when the chunk opens (whatever kind of block starts
            # it), and refreshed on every heading seen after that -- a
            # level 3+ heading never closes the chunk, but it does deepen
            # the context anything after it falls under (e.g. an H1/H2
            # opens the chunk, an H3 with no boundary of its own follows a
            # paragraph or two later: the chunk's heading_path must track
            # that H3 too, not stay pinned to the H1/H2 alone).
            current_heading_path = block.heading_path

        current.append(block)

    close_current(allow_overlap=False)
    return chunks


def chunk_body(body: str, *, max_chars: int | None = None, overlap_chars: int | None = None) -> list[Chunk]:
    """Structure-aware-chunk an already-frontmatter-stripped body. Use
    `chunk_markdown` instead when starting from a full Weave-Ingest document
    that still carries its YAML frontmatter."""
    effective_max = settings.chunk_max_chars if max_chars is None else max_chars
    effective_overlap = settings.chunk_overlap_chars if overlap_chars is None else overlap_chars
    blocks = _segment_blocks(body)
    return _assemble_chunks(blocks, max_chars=effective_max, overlap_chars=effective_overlap)


def chunk_markdown(
    markdown: str, *, max_chars: int | None = None, overlap_chars: int | None = None,
) -> tuple[dict, list[Chunk]]:
    """Parse frontmatter and chunk the remaining body in one call.

    Returns `(frontmatter, chunks)` -- callers needing both (e.g. to build
    per-chunk `meta` via app/services/enrichment.py) get the frontmatter
    dict back instead of having to call `parse_frontmatter` separately.
    """
    frontmatter, body = parse_frontmatter(markdown)
    return frontmatter, chunk_body(body, max_chars=max_chars, overlap_chars=overlap_chars)
