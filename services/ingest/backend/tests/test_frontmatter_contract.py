"""Contract test: every YAML frontmatter block Weave-Ingest emits must satisfy
contracts/frontmatter.schema.json -- the shared data contract every RAG
consumer of these documents is written against.

No `jsonschema` dependency here: requirements.txt is hash-pinned and this
test must not grow that pinned set. `_validate_frontmatter` below is instead
a deliberately tiny draft-2020-12-subset validator -- just required-field /
type / enum / const / array-items / one-level-nested-object checking, i.e.
exactly the shapes contracts/frontmatter.schema.json actually uses. It is
not a general-purpose validator and isn't meant to become one.

Frontmatter is generated through the real production call sites wherever
that's possible without a live PaddleOCR model or vision endpoint:
  - mail-eml:             convert_to_markdown_with_details() on a real .eml
  - pypdf-fallback:       convert_to_markdown_with_details() on a real,
                          hand-built, text-bearing PDF, with PaddleOCR
                          reported unavailable
  - spreadsheet-fallback: the same real path via pandas, when pandas AND an
                          xlsx writer engine are installed; this repo's venv
                          currently ships neither (only xlrd, a read-only
                          .xls reader -- see _HAS_PANDAS/_HAS_XLSX_WRITER
                          below), so _build_rag_frontmatter is called
                          directly with the exact metadata shape
                          _fallback_convert_with_frontmatter builds
  - paddleocr / openai_vision: these need a real OCR model or a live vision
                          endpoint, which this suite deliberately never
                          runs -- _build_rag_frontmatter is called directly
                          with representative metadata instead, mirroring
                          the frontmatter_metadata convert_to_markdown_with_details
                          assembles right before calling it for each branch
"""

from __future__ import annotations

import ast
import importlib.util
import json
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path

import pytest
import yaml

from app.services import paddle_service

def _repo_root() -> Path:
    """Walk up until the directory holding the shared `contracts/` is found.

    Deliberately a search rather than a fixed number of `.parents[...]`
    hops: this file has already moved once (its own service became
    `services/ingest/` inside the monorepo) and a hard-coded depth breaks
    silently on the next move -- here it broke the whole test collection,
    not just this one test.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / 'contracts' / 'frontmatter.schema.json').exists():
            return candidate
    raise RuntimeError('monorepo root with contracts/ not found above ' + __file__)


SCHEMA_PATH = _repo_root() / 'contracts' / 'frontmatter.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text())

_HAS_PANDAS = importlib.util.find_spec('pandas') is not None
_HAS_XLSX_WRITER = (
    importlib.util.find_spec('openpyxl') is not None
    or importlib.util.find_spec('xlsxwriter') is not None
)


# --- minimal draft-2020-12-subset validator ----------------------------------

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    'string': str,
    'integer': int,
    'number': (int, float),
    'array': list,
    'object': dict,
    'boolean': bool,
}


def _check_type(value: object, expected: str, path: str) -> list[str]:
    """One JSON-Schema 'type' check. `bool` is excluded from 'integer' (a
    Python quirk: bool is an int subclass) so a stray True/False can never
    silently pass as a page count or version number."""
    py_type = _TYPE_MAP.get(expected)
    if py_type is None:
        return []
    if expected == 'integer' and isinstance(value, bool):
        return [f'{path}: expected integer, got bool']
    if not isinstance(value, py_type):
        return [f'{path}: expected {expected}, got {type(value).__name__}']
    return []


def _validate_value(value: object, schema: dict, path: str) -> list[str]:
    """Validate one value against one (sub-)schema: type, enum, const, array
    items, and one level of nested object properties/additionalProperties --
    exactly what contracts/frontmatter.schema.json uses (children_titles is
    the only nested-object property, hence "one level" being enough)."""
    errors: list[str] = []
    expected_type = schema.get('type')
    if expected_type:
        errors.extend(_check_type(value, expected_type, path))
        if errors:
            return errors  # wrong type -- enum/items checks below would be noise
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path}: {value!r} not in enum {schema["enum"]}')
    if 'const' in schema and value != schema['const']:
        errors.append(f'{path}: expected const {schema["const"]!r}, got {value!r}')
    if expected_type == 'array' and 'items' in schema:
        for index, item in enumerate(value):
            errors.extend(_validate_value(item, schema['items'], f'{path}[{index}]'))
    if expected_type == 'object':
        sub_props = schema.get('properties', {})
        additional = schema.get('additionalProperties')
        for key, sub_value in value.items():
            if key in sub_props:
                errors.extend(_validate_value(sub_value, sub_props[key], f'{path}.{key}'))
            elif isinstance(additional, dict):
                errors.extend(_validate_value(sub_value, additional, f'{path}.{key}'))
    return errors


def _validate_frontmatter(meta: dict) -> list[str]:
    """Required fields present, then every property that IS present is
    type/enum/const-checked. `additionalProperties: true` at the top level
    means an unknown key is never an error -- only a known key with the
    wrong shape is."""
    errors = [f"$: missing required field '{key}'" for key in SCHEMA['required'] if key not in meta]
    props = SCHEMA['properties']
    for key, value in meta.items():
        if key in props:
            errors.extend(_validate_value(value, props[key], f'${key}'))
    return errors


def _extract_frontmatter(markdown: str) -> dict:
    assert markdown.startswith('---\n'), 'markdown must open with a YAML frontmatter block'
    end = markdown.index('\n---\n', 4)
    meta = yaml.safe_load(markdown[4:end + 1])
    assert isinstance(meta, dict), 'frontmatter YAML must parse to a mapping'
    return meta


def _assert_contract(markdown: str) -> dict:
    """Parse `markdown`'s frontmatter and assert it satisfies SCHEMA; returns
    the parsed mapping so callers can additionally assert the specific
    engine/field values they meant to exercise."""
    meta = _extract_frontmatter(markdown)
    errors = _validate_frontmatter(meta)
    assert not errors, 'frontmatter violates contracts/frontmatter.schema.json:\n' + '\n'.join(errors)
    return meta


# Mirrors the fields app/workers/tasks.py's process_job assembles from the Job
# row before calling convert_to_markdown_with_details (see
# _build_rag_frontmatter's own docstring) -- this is what "representative
# metadata" means throughout this file.
_REALISTIC_JOB_METADATA: dict[str, object] = {
    'job_id': 'job-7c1e9c2a',
    'document_version': 2,
    'content_sha256': 'a' * 64,
    'previous_job_id': 'job-11111111',
    'uploaded_by': 'mathias',
    'team': 'Kundenservice',
    'tags': ['rechnung', 'q3'],
    'mode': 'single',
    'department': 'Finance',
}


# --- engine 1/5: mail-eml (real path) ----------------------------------------

def _build_eml_bytes(body_text: str) -> bytes:
    msg = MIMEText(body_text, 'plain')
    msg['Subject'] = 'Contract test mail'
    msg['From'] = 'sender@example.com'
    msg['To'] = 'recipient@example.com'
    return msg.as_bytes()


def test_mail_eml_frontmatter_satisfies_contract(tmp_path):
    eml_path = tmp_path / 'contract.eml'
    eml_path.write_bytes(_build_eml_bytes('Plain body text for the contract test.'))

    metadata = {**_REALISTIC_JOB_METADATA, 'original_filename': 'contract.eml'}
    markdown, details = paddle_service.convert_to_markdown_with_details(
        str(eml_path), profile_id='ppocrv6_tiny', metadata=metadata,
    )

    meta = _assert_contract(markdown)
    assert meta['engine'] == 'mail-eml'
    assert details['engine'] == 'mail-eml'


# --- engine 2/5: pypdf-fallback (real path, hand-built minimal PDF) ---------

def _minimal_text_pdf_bytes(text: str) -> bytes:
    """Hand-built, dependency-free single-page PDF whose content stream
    really contains extractable text -- reportlab et al. aren't in the
    pinned requirements, and PdfReader.extract_text() needs a genuine
    content stream, unlike the placeholder b'%PDF-1.4 test' bytes other
    tests in this suite get away with by monkeypatching PdfReader itself.
    Verified against the pinned pypdf==6.16.1.
    """
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    stream = f'BT /F1 12 Tf 20 150 Td ({text}) Tj ET'.encode()
    objects.append(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream')

    buf = BytesIO()
    buf.write(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f'{index} 0 obj\n'.encode())
        buf.write(obj)
        buf.write(b'\nendobj\n')
    xref_offset = buf.tell()
    count = len(objects) + 1
    buf.write(f'xref\n0 {count}\n'.encode())
    buf.write(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        buf.write(f'{offset:010d} 00000 n \n'.encode())
    buf.write(f'trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF'.encode())
    return buf.getvalue()


def test_pypdf_fallback_frontmatter_satisfies_contract(tmp_path, monkeypatch):
    pdf_path = tmp_path / 'contract.pdf'
    pdf_path.write_bytes(_minimal_text_pdf_bytes('Contract test PDF body text.'))

    monkeypatch.setattr(paddle_service, '_paddleocr_available', lambda: False)

    metadata = {**_REALISTIC_JOB_METADATA, 'original_filename': 'contract.pdf'}
    markdown, details = paddle_service.convert_to_markdown_with_details(
        str(pdf_path), profile_id='ppocrv6_tiny', metadata=metadata,
    )

    meta = _assert_contract(markdown)
    assert meta['engine'] == 'pypdf-fallback'
    assert meta['used_fallback'] is True
    assert details['engine'] == 'pypdf-fallback'


# --- engine 3/5: spreadsheet-fallback ----------------------------------------

@pytest.mark.skipif(
    not (_HAS_PANDAS and _HAS_XLSX_WRITER),
    reason='pandas + an xlsx writer engine (openpyxl/xlsxwriter) are not installed in this venv',
)
def test_spreadsheet_fallback_frontmatter_satisfies_contract_via_real_xlsx(tmp_path, monkeypatch):
    import pandas as pd  # noqa: PLC0415

    xlsx_path = tmp_path / 'contract.xlsx'
    pd.DataFrame({'Name': ['Alice'], 'Amount': [42]}).to_excel(xlsx_path, index=False)

    monkeypatch.setattr(paddle_service, '_paddleocr_available', lambda: False)

    metadata = {**_REALISTIC_JOB_METADATA, 'original_filename': 'contract.xlsx'}
    markdown, details = paddle_service.convert_to_markdown_with_details(
        str(xlsx_path), profile_id='ppocrv6_tiny', metadata=metadata,
    )

    meta = _assert_contract(markdown)
    assert meta['engine'] == 'spreadsheet-fallback'
    assert details['engine'] == 'spreadsheet-fallback'


def test_spreadsheet_fallback_frontmatter_satisfies_contract_direct():
    """`_fallback_spreadsheet_to_markdown` hard-requires pandas (its own
    module-scope `import pandas as pd`) even on the .xls/xlrd branch, and
    this venv's pinned requirements ship neither pandas nor an xlsx writer
    engine (only xlrd, a read-only .xls reader) -- see
    test_spreadsheet_fallback_frontmatter_satisfies_contract_via_real_xlsx
    above, which exercises the real path instead whenever those ARE
    installed. Until then: call _build_rag_frontmatter directly with the
    exact metadata shape _fallback_convert_with_frontmatter builds for this
    engine (profile_id + engine + used_fallback layered onto the job
    metadata -- see that function's frontmatter_metadata construction).
    """
    metadata: dict[str, object] = {
        **_REALISTIC_JOB_METADATA,
        'original_filename': 'contract.xlsx',
        'profile_id': 'ppocrv6_tiny',
        'engine': 'spreadsheet-fallback',
        'used_fallback': True,
    }
    frontmatter = paddle_service._build_rag_frontmatter(
        'contract.xlsx', 2, 'PP-OCRv6 tiny det + rec', metadata=metadata,
    )
    meta = _assert_contract(frontmatter)
    assert meta['engine'] == 'spreadsheet-fallback'
    assert meta['used_fallback'] is True


# --- engines 4/5: paddleocr, openai_vision -----------------------------------
# Both need a real model run / a live vision endpoint to reach through
# convert_to_markdown_with_details, so _build_rag_frontmatter is called
# directly with representative metadata instead.

def test_paddleocr_frontmatter_satisfies_contract_direct():
    metadata: dict[str, object] = {
        **_REALISTIC_JOB_METADATA,
        'original_filename': 'contract.pdf',
        'profile_id': 'ppocrv6_tiny',
        'engine': 'paddleocr',
        'used_fallback': False,
    }
    frontmatter = paddle_service._build_rag_frontmatter(
        'contract.pdf', 4, 'PP-OCRv6 tiny det + rec', metadata=metadata,
    )
    meta = _assert_contract(frontmatter)
    assert meta['engine'] == 'paddleocr'
    # used_fallback: false is never emitted (_build_rag_frontmatter only ever
    # writes the key when true; the schema mirrors that with "const": true).
    assert 'used_fallback' not in meta


def test_openai_vision_frontmatter_satisfies_contract_direct():
    """NOTE: today's convert_to_markdown_with_details actually stamps
    'engine': 'paddleocr' on every non-fallback pipeline alike --
    ppstructurev3, paddlevl, AND openai_vision -- so 'engine': 'openai_vision'
    is not currently reachable through the real conversion path (see the
    shared frontmatter_metadata block right before _convert_structure_to_markdown
    is called). This test keeps the schema's enum entry for it validated
    against _build_rag_frontmatter regardless, since it's the contract that
    entry is meant to describe."""
    metadata: dict[str, object] = {
        **_REALISTIC_JOB_METADATA,
        'original_filename': 'contract.pdf',
        'profile_id': 'openai_vision',
        'engine': 'openai_vision',
        'used_fallback': False,
    }
    frontmatter = paddle_service._build_rag_frontmatter(
        'contract.pdf', 1, 'OpenAI-compatible Vision API', metadata=metadata,
    )
    meta = _assert_contract(frontmatter)
    assert meta['engine'] == 'openai_vision'


# --- Collections contract: `collection`/`collection_name` --------------------
# See README.md's "Collections" section and app/api/routes.py's
# upload_document_to_collection/start_collection_processing, which stamp
# collection_slug/collection_name into processing_info.settings for
# app/workers/tasks.py's process_job to read back into this metadata dict.

def test_build_rag_frontmatter_includes_collection_fields_when_set():
    metadata: dict[str, object] = {
        **_REALISTIC_JOB_METADATA,
        'mode': 'collection',
        'profile_id': 'ppocrv6_tiny',
        'engine': 'paddleocr',
        'collection_slug': 'kundenservice-2026',
        'collection_name': 'Kundenservice 2026',
    }
    frontmatter = paddle_service._build_rag_frontmatter(
        'contract.pdf', 2, 'PP-OCRv6 tiny det + rec', metadata=metadata,
    )
    meta = _assert_contract(frontmatter)
    assert meta['collection'] == 'kundenservice-2026'
    assert meta['collection_name'] == 'Kundenservice 2026'


def test_build_rag_frontmatter_omits_collection_fields_when_unset():
    """_REALISTIC_JOB_METADATA (shared by every engine test above) carries no
    collection_slug/collection_name -- the ordinary single-upload case -- so
    neither field should appear at all, not even as an empty string."""
    metadata: dict[str, object] = {
        **_REALISTIC_JOB_METADATA,
        'profile_id': 'ppocrv6_tiny',
        'engine': 'paddleocr',
    }
    frontmatter = paddle_service._build_rag_frontmatter(
        'contract.pdf', 2, 'PP-OCRv6 tiny det + rec', metadata=metadata,
    )
    meta = _assert_contract(frontmatter)
    assert 'collection' not in meta
    assert 'collection_name' not in meta


# --- regression guards (task requirement: must fail on contract drift) ------

def test_build_rag_frontmatter_emits_every_required_field():
    """Direct guard for the required-field half of the drift check: fails
    immediately if a required field's assignment is ever removed from
    _build_rag_frontmatter, independent of which engine test happens to
    exercise it."""
    metadata: dict[str, object] = {
        **_REALISTIC_JOB_METADATA,
        'profile_id': 'ppocrv6_tiny',
        'engine': 'paddleocr',
    }
    frontmatter = paddle_service._build_rag_frontmatter(
        'contract.pdf', 3, 'PP-OCRv6 tiny det + rec', metadata=metadata,
    )
    meta = _assert_contract(frontmatter)
    missing = set(SCHEMA['required']) - meta.keys()
    assert not missing, f'missing required field(s): {missing}'


def _engine_literals_in_paddle_service() -> set[str]:
    """AST walk over paddle_service.py collecting every string literal that
    feeds the frontmatter 'engine' key -- either directly as a dict-literal
    value ('engine': 'foo') or, per function, via a local `engine` variable
    IF that same function actually uses it as a dict value under key
    'engine' (`'engine': engine`, the shape _fallback_convert_with_frontmatter
    uses to pick between the pypdf-fallback/spreadsheet-fallback ternary).

    The per-function gating matters: `_fallback_spreadsheet_to_markdown` also
    has a local variable named `engine` (`engine = 'xlrd' if suffix == '.xls'
    else None`), but that one is pandas' read_excel(engine=...) *pipeline*
    name, never written into the frontmatter -- without the gate this walk
    would misreport 'xlrd' as a frontmatter engine value.

    This is what lets the "new engine value" half of the drift check hold
    even for a branch no test above calls yet: a new
    `data['engine'] = 'new-engine'` or (in a function that already threads a
    local `engine` var into the frontmatter) `engine = 'new-engine'` line is
    caught here the moment it's added, before any conversion ever runs it.
    """
    def _string_literals(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.IfExp):  # ternary: body if test else orelse
            return _string_literals(node.body) + _string_literals(node.orelse)
        return []

    tree = ast.parse(Path(paddle_service.__file__).read_text())
    literals: set[str] = set()

    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_nodes = list(ast.walk(func))

        engine_dict_values = [
            value
            for node in body_nodes if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value == 'engine'
        ]
        for value in engine_dict_values:
            literals.update(_string_literals(value))

        feeds_frontmatter_engine = any(
            isinstance(value, ast.Name) and value.id == 'engine' for value in engine_dict_values
        )
        if feeds_frontmatter_engine:
            for node in body_nodes:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'engine':
                            literals.update(_string_literals(node.value))

    return literals


def test_no_engine_literal_in_paddle_service_is_missing_from_schema_enum():
    literals = _engine_literals_in_paddle_service()
    assert literals, 'no engine literals found in paddle_service.py -- the AST walk is likely stale'
    allowed = set(SCHEMA['properties']['engine']['enum'])
    unknown = literals - allowed
    assert not unknown, (
        f'paddle_service.py assigns engine value(s) {unknown} that '
        f'contracts/frontmatter.schema.json does not list in its engine enum'
    )
