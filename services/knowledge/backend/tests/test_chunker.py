"""Tests for app/services/chunker.py.

Fixtures mirror the exact envelope Weave-Ingest emits (see
contracts/frontmatter.schema.json and, in Weave-Ingest,
app/services/confluence_markdown.py's `render_frontmatter` /
app/services/paddle_service.py's `_prepend_frontmatter`):
`---\\n<yaml>\\n---\\n\\n<body>`, with `<!-- page:N -->` / `<!-- page:N/M -->`
markers inside the body (see paddle_service.py's `page_header`).

Most block-assembly tests call `chunk_body` directly with a small,
explicit `max_chars`/`overlap_chars` so the size-driven boundary behavior
is pinned down without depending on `settings.chunk_max_chars`.
"""

from __future__ import annotations

from app.services.chunker import chunk_body, chunk_markdown, parse_frontmatter

# --- parse_frontmatter --------------------------------------------------------


def test_parse_frontmatter_realistic_ingest_document():
    markdown = (
        '---\n'
        'source: doc.pdf\n'
        'original_filename: Handbuch.pdf\n'
        'pages: 1\n'
        'profile: PP-OCRv6 tiny det + rec\n'
        'profile_id: ppocrv6_tiny\n'
        'mode: single\n'
        'job_id: job-123\n'
        'document_version: 2\n'
        f'content_sha256: {"a" * 64}\n'
        "processed_at: '2026-08-04T12:00:00Z'\n"
        'engine: paddleocr\n'
        'team: Kundenservice\n'
        'department: Finance\n'
        'tags:\n'
        '  - rechnung\n'
        '  - q3\n'
        '---\n'
        '\n'
        '<!-- page:1/1 -->\n'
        '\n'
        '# Handbuch\n'
        '\n'
        'Ein Absatz Text.\n'
    )
    frontmatter, body = parse_frontmatter(markdown)

    assert frontmatter['source'] == 'doc.pdf'
    assert frontmatter['engine'] == 'paddleocr'
    assert frontmatter['document_version'] == 2
    assert frontmatter['tags'] == ['rechnung', 'q3']
    # The YAML block is gone from the body -- only the document content remains.
    assert 'source: doc.pdf' not in body
    assert '<!-- page:1/1 -->' in body
    assert '# Handbuch' in body


def test_parse_frontmatter_missing_returns_empty_dict_and_original_body():
    markdown = '# Just a heading\n\nAnd a paragraph, no frontmatter at all.\n'
    frontmatter, body = parse_frontmatter(markdown)
    assert frontmatter == {}
    assert body == markdown


def test_parse_frontmatter_broken_yaml_is_tolerated():
    markdown = (
        '---\n'
        'title: "unterminated\n'
        'engine: paddleocr\n'
        '---\n'
        '\n'
        'Body text survives.\n'
    )
    frontmatter, body = parse_frontmatter(markdown)
    assert frontmatter == {}
    assert 'Body text survives.' in body
    assert 'title:' not in body


def test_parse_frontmatter_unclosed_marker_is_treated_as_no_frontmatter():
    markdown = '---\nengine: paddleocr\nno closing marker below\n'
    frontmatter, body = parse_frontmatter(markdown)
    assert frontmatter == {}
    assert body == markdown


def test_parse_frontmatter_non_mapping_yaml_normalizes_to_empty_dict():
    markdown = '---\njust a scalar string\n---\n\nBody.\n'
    frontmatter, body = parse_frontmatter(markdown)
    assert frontmatter == {}
    assert body == '\nBody.\n'


# --- page marker tracking ------------------------------------------------------


def test_page_markers_tracked_across_multiple_pages_and_never_leak_into_text():
    body = (
        '<!-- page:1/3 -->\n'
        '\n'
        '# Report\n'
        '\n'
        'First page paragraph.\n'
        '\n'
        '<!-- page:2/3 -->\n'
        '\n'
        '## Details\n'
        '\n'
        'Second page paragraph.\n'
        '\n'
        '| A | B |\n'
        '| --- | --- |\n'
        '| 1 | 2 |\n'
        '\n'
        '<!-- page:3/3 -->\n'
        '\n'
        'Third page paragraph, still under Details.\n'
    )
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)

    assert len(chunks) == 2
    for chunk in chunks:
        assert '<!-- page' not in chunk.text

    # chunk 0: the H1 plus its paragraph, entirely on page 1.
    assert chunks[0].heading_path == ['Report']
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1

    # chunk 1: opened by the H2 on page 2, but the un-headed paragraph that
    # follows the page:3 marker stays in the same chunk (no heading boundary
    # crossed) -- so its page range spans both pages it actually touched.
    assert chunks[1].heading_path == ['Report', 'Details']
    assert chunks[1].page_start == 2
    assert chunks[1].page_end == 3
    assert '| A | B |' in chunks[1].text
    assert 'Third page paragraph' in chunks[1].text


def test_bare_page_marker_without_total_is_recognized():
    body = '<!-- page:5 -->\n\nSome text on page five.\n'
    chunks = chunk_body(body, max_chars=1000, overlap_chars=0)
    assert len(chunks) == 1
    assert chunks[0].page_start == 5
    assert chunks[0].page_end == 5
    assert '<!-- page:5 -->' not in chunks[0].text


def test_blocks_before_any_page_marker_have_no_page():
    body = 'No marker has appeared yet.\n'
    chunks = chunk_body(body, max_chars=1000, overlap_chars=0)
    assert len(chunks) == 1
    assert chunks[0].page_start is None
    assert chunks[0].page_end is None


# --- heading paths: nesting, level jumps, refresh mid-chunk --------------------


def test_heading_path_reflects_nesting_even_when_deepened_mid_chunk():
    # H1 -> H2 -> H3 each followed by their own paragraph, generous max_chars
    # so only the semantic (level 1/2) boundaries split anything. The H3 does
    # NOT close the chunk it lands in, but its arrival must still deepen that
    # chunk's stored heading_path (regression guard for path staleness).
    body = (
        '# Guide\n\nIntro paragraph.\n\n'
        '## Setup\n\nSetup paragraph.\n\n'
        '### Docker\n\nDocker paragraph.\n'
    )
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)

    assert len(chunks) == 2
    assert chunks[0].heading_path == ['Guide']
    assert 'Intro paragraph.' in chunks[0].text

    assert chunks[1].heading_path == ['Guide', 'Setup', 'Docker']
    assert 'Setup paragraph.' in chunks[1].text
    assert 'Docker paragraph.' in chunks[1].text


def test_heading_path_level_jump_skips_missing_levels():
    # H1 straight to H3 (no H2 in between) -- the path is just what actually
    # exists on the stack, no synthetic gap-filling.
    body = '# Top\n\nText under top.\n\n### Deep\n\nText under deep.\n'
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)
    assert len(chunks) == 1
    assert chunks[0].heading_path == ['Top', 'Deep']


def test_heading_stack_pops_deeper_levels_on_sibling_heading():
    # Section -> Sub (level 3) -> a new same-level-2 Section: the level-3
    # entry must be popped off the stack, not retained as a stale ancestor.
    body = (
        '## Section\n\ntext1\n\n'
        '### Sub\n\ntext2\n\n'
        '## Section2\n\ntext3\n'
    )
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)
    assert len(chunks) == 2
    assert chunks[0].heading_path == ['Section', 'Sub']
    assert chunks[1].heading_path == ['Section2']


def test_non_heading_space_after_hash_is_not_a_heading():
    # '#NoSpace' has no whitespace after the hashes -- not a valid ATX
    # heading (same rule Weave-Ingest's own heading detection uses), so it
    # is ordinary paragraph text and must not affect the heading stack.
    body = '# Real Heading\n\n#NoSpace this stays a paragraph\n'
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)
    assert len(chunks) == 1
    assert '#NoSpace this stays a paragraph' in chunks[0].text
    assert chunks[0].heading_path == ['Real Heading']


# --- tables stay atomic ---------------------------------------------------------


def test_table_stays_atomic_even_larger_than_max_chars():
    table = '| Name | Value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |'
    body = f'Intro.\n\n{table}\n'
    assert len(table) > 20
    chunks = chunk_body(body, max_chars=20, overlap_chars=0)

    # The table never gets split across a chunk boundary or truncated.
    table_chunk = next(c for c in chunks if 'alpha' in c.text)
    assert table_chunk.text == table
    assert 'beta' in table_chunk.text


def test_table_isolated_into_its_own_chunk_when_it_alone_exceeds_max():
    table = '| A | B |\n| --- | --- |\n| 1 | 2 |'
    body = f'{table}\n\nfollowing paragraph\n'
    chunks = chunk_body(body, max_chars=10, overlap_chars=0)

    assert len(chunks) == 2
    assert chunks[0].text == table
    assert chunks[1].text == 'following paragraph'


def test_pipe_character_in_prose_without_divider_is_not_a_table():
    body = 'The vertical bar | is just punctuation here, not a table start.\n'
    chunks = chunk_body(body, max_chars=1000, overlap_chars=0)
    assert len(chunks) == 1
    assert chunks[0].text == body.strip('\n')


# --- code fences stay atomic -----------------------------------------------------


def test_code_fence_stays_atomic_even_larger_than_max_chars():
    code = '```python\n' + '\n'.join(f'line_{i} = {i}' for i in range(10)) + '\n```'
    body = f'Intro.\n\n{code}\n'
    assert len(code) > 30
    chunks = chunk_body(body, max_chars=30, overlap_chars=0)

    code_chunk = next(c for c in chunks if 'line_0' in c.text)
    assert code_chunk.text == code
    assert 'line_9' in code_chunk.text


def test_unterminated_code_fence_consumes_to_end_of_body():
    body = '```\nno closing fence below\nstill inside the block\n'
    chunks = chunk_body(body, max_chars=1000, overlap_chars=0)
    assert len(chunks) == 1
    # No closing fence is ever found, so the block runs verbatim to the end
    # of the body -- including the trailing newline `split('\n')`/`'\n'.join`
    # round-trips exactly, unlike a paragraph block (which is stripped).
    assert chunks[0].text == body


# --- overlap ---------------------------------------------------------------------


def test_overlap_prefixes_next_chunk_with_tail_of_paragraph_predecessor():
    para1 = 'A' * 40
    para2 = 'B' * 40
    body = f'{para1}\n\n{para2}\n'
    chunks = chunk_body(body, max_chars=50, overlap_chars=10)

    assert len(chunks) == 2
    assert chunks[0].text == para1
    assert chunks[1].text == ('A' * 10) + '\n\n' + para2
    assert chunks[1].text.startswith('A' * 10)


def test_overlap_disabled_when_predecessor_closed_on_a_table():
    table = '| A | B |\n| --- | --- |\n| 1 | 2 |'
    para = 'X' * 40
    body = f'{table}\n\n{para}\n'
    chunks = chunk_body(body, max_chars=50, overlap_chars=10)

    assert len(chunks) == 2
    assert chunks[0].text == table
    # No table fragment leaks into the next chunk as a false "overlap".
    assert chunks[1].text == para
    assert '|' not in chunks[1].text


def test_overlap_disabled_when_predecessor_closed_on_a_code_block():
    code = '```\nsome code\n```'
    para = 'Y' * 40
    body = f'{code}\n\n{para}\n'
    chunks = chunk_body(body, max_chars=40, overlap_chars=10)

    assert len(chunks) == 2
    assert chunks[0].text == code
    assert chunks[1].text == para


def test_no_overlap_when_overlap_chars_is_zero():
    para1 = 'A' * 40
    para2 = 'B' * 40
    body = f'{para1}\n\n{para2}\n'
    chunks = chunk_body(body, max_chars=50, overlap_chars=0)
    assert chunks[1].text == para2


# --- level 1/2 heading boundary ---------------------------------------------------


def test_level_1_heading_change_closes_chunk_regardless_of_size():
    body = '# Alpha\n\nAlpha body.\n\n# Beta\n\nBeta body.\n'
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)
    assert len(chunks) == 2
    assert chunks[0].heading_path == ['Alpha']
    assert 'Alpha body.' in chunks[0].text
    assert chunks[1].heading_path == ['Beta']
    assert 'Beta body.' in chunks[1].text
    # A semantic boundary never carries size-driven overlap across it.
    assert 'Alpha body.' not in chunks[1].text


def test_level_2_heading_change_closes_chunk():
    body = '## Section A\n\ntext a\n\n## Section B\n\ntext b\n'
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)
    assert len(chunks) == 2
    assert chunks[0].heading_path == ['Section A']
    assert chunks[1].heading_path == ['Section B']


def test_level_3_heading_does_not_close_chunk():
    body = '## Section\n\nintro\n\n### Sub\n\nsub text\n'
    chunks = chunk_body(body, max_chars=10_000, overlap_chars=0)
    assert len(chunks) == 1
    assert 'intro' in chunks[0].text
    assert 'sub text' in chunks[0].text


# --- chunk_index sequencing -------------------------------------------------------


def test_chunk_index_is_sequential_and_deterministic():
    body = '\n\n'.join(f'Paragraph number {i} with some filler text.' for i in range(10))
    chunks = chunk_body(body, max_chars=60, overlap_chars=5)
    assert len(chunks) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    # Re-running on the identical input produces the identical sequence.
    again = chunk_body(body, max_chars=60, overlap_chars=5)
    assert [c.text for c in again] == [c.text for c in chunks]


def test_char_count_matches_len_of_text():
    body = 'Just one short paragraph.\n'
    chunks = chunk_body(body, max_chars=1000, overlap_chars=0)
    assert chunks[0].char_count == len(chunks[0].text)


# --- realistic combined fixture (Ingest-style, multi-page, mixed blocks) ----------


def test_realistic_ingest_style_document_end_to_end():
    markdown = (
        '---\n'
        'source: contract.pdf\n'
        'original_filename: Vertrag.pdf\n'
        'pages: 2\n'
        'profile: PP-OCRv6 tiny det + rec\n'
        'profile_id: ppocrv6_tiny\n'
        'mode: single\n'
        'job_id: job-abc\n'
        'document_version: 1\n'
        f'content_sha256: {"b" * 64}\n'
        "processed_at: '2026-08-04T12:00:00Z'\n"
        'engine: paddleocr\n'
        'team: Rechtsabteilung\n'
        '---\n'
        '\n'
        '<!-- page:1/2 -->\n'
        '\n'
        '# Versicherungsvertrag\n'
        '\n'
        'Dies ist die Einleitung des Vertrags mit ein paar Sätzen Kontext.\n'
        '\n'
        '## Deckungsumfang\n'
        '\n'
        '| Position | Betrag |\n'
        '| --- | --- |\n'
        '| Selbstbehalt | 500 EUR |\n'
        '| Deckungssumme | 100000 EUR |\n'
        '\n'
        '<!-- page:2/2 -->\n'
        '\n'
        '```text\n'
        'Klausel 4.2: Ausschluss bei grober Fahrlaessigkeit.\n'
        '```\n'
        '\n'
        'Abschliessender Absatz nach der Klausel.\n'
    )
    frontmatter, chunks = chunk_markdown(markdown, max_chars=3000, overlap_chars=200)

    assert frontmatter['engine'] == 'paddleocr'
    assert frontmatter['team'] == 'Rechtsabteilung'
    assert len(chunks) >= 1
    for chunk in chunks:
        assert '<!-- page' not in chunk.text
        assert 'source: contract.pdf' not in chunk.text

    all_text = '\n'.join(c.text for c in chunks)
    assert '| Selbstbehalt | 500 EUR |' in all_text
    assert 'Klausel 4.2' in all_text
    assert chunks[0].heading_path == ['Versicherungsvertrag']
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
