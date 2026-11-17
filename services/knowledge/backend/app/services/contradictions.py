"""Contradiction *candidates* between documents of one knowledge space.

Run by app/workers/tasks.py's index pipeline right after a document's index
commit (see `_report_contradiction_candidates` there); the result is reported
best-effort to Weave-Ingest's `POST /api/v1/quality/contradictions`
(app/services/ingest_client.py's `report_contradictions`), which turns each
unordered document pair into a `contradiction` review task for the space's
knowledge owners.

THIS IS A HEURISTIC, NOT A FACT CHECKER. It only flags *possible*
contradictions for a human to look at -- it never edits, blocks or ranks
anything itself, and false positives (and misses) are expected. It is kept
deliberately conservative so the review inbox is not flooded:

1. Scope: only chunks of OTHER documents with status `indexed` in the SAME
   `collection_slug` (documents without a collection are never checked),
   embedded with the SAME embedding model (cosine across models is
   meaningless), and never the document's own lineage (earlier or later
   versions linked through `previous_job_id`) -- a new version is supposed
   to differ from the old one.
2. Semantic closeness: cosine similarity of the two chunk embeddings must be
   >= settings.contradiction_min_similarity (default 0.86). Postgres asks
   pgvector (`<=>`, HNSW) for the top `_TOP_K_PER_CHUNK` neighbours per
   chunk; SQLite (local dev, the pytest suite) computes cosine in Python
   over at most `_SQLITE_MAX_CANDIDATE_CHUNKS` candidate chunks.
3. Fact tokens: both chunks must contain "fact tokens" (see
   `extract_facts`): dates (`1.4.2026`, `01.04.26`, `2026-04-01`), money
   (`50 €`, `EUR 1.200,50`), percentages (`19 %`, `19 Prozent`), durations
   (`30 Tage`, `6 Wochen`, `2 days`), a few measures (`km`, `kg`, `GB`, ...)
   and bare numbers with at least two digits. Single digits, list markers
   and dotted section numbers (`3.2.1`) are ignored.
4. Conflict: a fact in one chunk and a fact in the other are a conflicting
   pair when they are of the same kind and unit, have different normalized
   values, neither value occurs (same kind/unit) anywhere in the other chunk,
   AND the words right around them (`_CONTEXT_CHARS` on each side, stop
   words and unit words removed, umlauts folded, light suffix stemming)
   share at least one word -- "Kündigungsfrist ... 30 Tage" vs.
   "Kündigungsfrist ... 14 Tage" qualifies, "Lieferzeit 3 Tage" vs.
   "Kündigungsfrist 14 Tage" does not.

At most settings.contradiction_max_reports_per_document reports (default 10)
are produced per run, highest similarity first, at most one per chunk pair
and at most _MAX_REPORTS_PER_OTHER_DOCUMENT per other document.

Read-only: never writes or commits. DB-free helpers (`extract_facts`,
`conflicting_facts`) are exposed so the heuristic itself is testable without
any ORM.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import Float, bindparam, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import Chunk, Document, DocumentStatus

logger = logging.getLogger(__name__)

# Nearest neighbours asked from pgvector per chunk of the new document.
_TOP_K_PER_CHUNK = 3
# SQLite fallback only: upper bound on candidate chunks loaded for the
# in-Python cosine pass (newest first). Keeps a local/dev run bounded.
_SQLITE_MAX_CANDIDATE_CHUNKS = 2000
# Hard safety cap on lineage walks (previous_job_id chain) in either direction.
_MAX_LINEAGE_STEPS = 100
# Characters on each side of a fact token searched for context words.
_CONTEXT_CHARS = 60
# Characters on each side of the first conflicting fact kept in an excerpt.
_EXCERPT_CHARS = 160
# Facts per side listed in one report.
_MAX_FACTS_PER_REPORT = 10
# Reports per other document (Ingest keeps up to 5 excerpt pairs per job pair).
_MAX_REPORTS_PER_OTHER_DOCUMENT = 5

# --- fact token extraction ---------------------------------------------------

# Unit words -> (kind, canonical unit). English spellings map onto the same
# canonical unit so a German and an English document can still be compared.
_UNITS: dict[str, tuple[str, str]] = {}


def _register(kind: str, canonical: str, *spellings: str) -> None:
    for spelling in spellings:
        _UNITS[spelling] = (kind, canonical)


_register('duration', 'arbeitstag', 'arbeitstage', 'arbeitstagen', 'arbeitstag')
_register('duration', 'werktag', 'werktage', 'werktagen', 'werktag')
_register('duration', 'kalendertag', 'kalendertage', 'kalendertagen', 'kalendertag')
_register('duration', 'tag', 'tage', 'tagen', 'tag', 'days', 'day')
_register('duration', 'woche', 'wochen', 'woche', 'weeks', 'week')
_register('duration', 'monat', 'monaten', 'monate', 'monat', 'months', 'month')
_register('duration', 'jahr', 'jahren', 'jahre', 'jahr', 'years', 'year')
_register('duration', 'stunde', 'stunden', 'stunde', 'std', 'hours', 'hour', 'h')
_register('duration', 'minute', 'minuten', 'minute', 'min', 'minutes')
_register('money', 'eur', '€', 'eur', 'euro')
_register('money', 'usd', '$', 'usd', 'dollar')
_register('money', 'chf', 'chf')
_register('money', 'cent', 'cent', 'ct')
_register('percent', '%', '%', 'prozent', 'percent')
_register('measure', 'km', 'km', 'kilometer')
_register('measure', 'kg', 'kg', 'kilogramm')
_register('measure', 'kb', 'kb')
_register('measure', 'mb', 'mb')
_register('measure', 'gb', 'gb')
_register('measure', 'tb', 'tb')
_register('measure', 'kwh', 'kwh')

_NUMBER = r'\d+(?:[.,]\d+)*'
# Longest spelling first so 'arbeitstagen' wins over 'tag', 'kwh' over 'h'.
_UNIT_ALT = '|'.join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_FACT_RE = re.compile(
    r'(?P<iso>(?<![\d.])\d{4}-\d{2}-\d{2}(?![\d-]))'
    # A single trailing period (end of sentence) is allowed after a date or
    # number; only a period followed by a digit continues the token.
    r'|(?P<dmy>(?<![\d.])\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?!\d|\.\d))'
    # dotted section numbers ("3.2.1"), but not German thousands ("1.234.567")
    r'|(?P<dotted>(?<![\d.,])(?!\d{1,3}(?:\.\d{3})+(?!\d|\.\d))\d+(?:\.\d+){2,}(?!\d))'
    rf'|(?P<prefix>(?:€|\$|\bEUR\b|\bCHF\b|\bUSD\b))\s?(?P<pnum>{_NUMBER})'
    rf'|(?<![\w.,])(?P<num>{_NUMBER})(?:\s?(?P<unit>{_UNIT_ALT})(?![\w]))?',
    re.IGNORECASE,
)
_THOUSANDS_DOT_RE = re.compile(r'^\d{1,3}(?:\.\d{3})+(?:,\d+)?$')


@dataclass(frozen=True)
class Fact:
    """One fact token: `kind`/`unit`/`value` are the normalized comparison
    key, `text` the surface form shown to reviewers, `start`/`end` its span
    in the chunk text, `context` the stemmed words around it."""

    kind: str
    unit: str
    value: str
    text: str
    start: int
    end: int
    context: frozenset[str] = field(default_factory=frozenset)

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.unit)


def _normalize_number(raw: str) -> str | None:
    """Canonical string for a German/English number. German conventions win
    on ambiguity (comma = decimal separator, `1.000` = one thousand) --
    an ambiguous English `1,500` therefore reads as 1.5; worst case a
    spurious candidate a human dismisses."""
    if _THOUSANDS_DOT_RE.match(raw):
        raw = raw.replace('.', '')
    raw = raw.replace(',', '.')
    if raw.count('.') > 1:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return str(int(value)) if value.is_integer() else repr(value)


def _date_value(day: int, month: int, year: int) -> str | None:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f'{year:04d}-{month:02d}-{day:02d}'


def _fold(text: str) -> str:
    text = text.lower()
    for src, dst in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue'), ('ß', 'ss')):
        text = text.replace(src, dst)
    return unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')


_STOPWORDS = frozenset(_fold(word) for word in (
    # German
    'aber', 'alle', 'allen', 'als', 'also', 'am', 'an', 'auch', 'auf', 'aus', 'bei', 'beim', 'bis', 'bzw',
    'da', 'dann', 'das', 'dass', 'dem', 'den', 'der', 'des', 'die', 'dies', 'diese', 'diesem', 'diesen',
    'dieser', 'dieses', 'doch', 'durch', 'ein', 'eine', 'einem', 'einen', 'einer', 'eines', 'für', 'gegen',
    'hat', 'haben', 'ist', 'im', 'in', 'innerhalb', 'ja', 'je', 'jede', 'jeden', 'jeder', 'kann', 'kein',
    'keine', 'max', 'maximal', 'mehr', 'mindestens', 'mit', 'muss', 'nach', 'nicht', 'noch', 'nur', 'oder',
    'ohne', 'pro', 'seit', 'sich', 'sind', 'so', 'soll', 'sowie', 'über', 'um', 'und', 'unter', 'vom',
    'von', 'vor', 'wann', 'was', 'wenn', 'werden', 'wird', 'wie', 'zu', 'zum', 'zur', 'zwischen', 'ab',
    'etwa', 'ca', 'beträgt', 'betragen', 'gilt', 'gelten', 'liegt', 'sein', 'wurde', 'wurden', 'sowohl',
    # English
    'the', 'and', 'for', 'are', 'was', 'were', 'with', 'from', 'this', 'that', 'these', 'those', 'has',
    'have', 'had', 'not', 'but', 'within', 'per', 'up', 'to', 'of', 'at', 'by', 'or', 'on', 'is', 'be',
    'than', 'least', 'most', 'about', 'after', 'before', 'until', 'all', 'any', 'each', 'its', 'will',
)) | frozenset(_UNITS)

_WORD_RE = re.compile(r'[^\W\d_]{3,}', re.UNICODE)
_SUFFIXES = ('ungen', 'en', 'er', 'es', 'e', 'n', 's')


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _context_words(text: str, start: int, end: int) -> frozenset[str]:
    # Widen the window to whole words so a word cut at the edge never turns
    # into a bogus fragment ("Versio") that matches by accident.
    left = max(0, start - _CONTEXT_CHARS)
    while left > 0 and not text[left - 1].isspace() and start - left < 2 * _CONTEXT_CHARS:
        left -= 1
    right = min(len(text), end + _CONTEXT_CHARS)
    while right < len(text) and not text[right].isspace() and right - end < 2 * _CONTEXT_CHARS:
        right += 1
    window = text[left:start] + ' ' + text[end:right]
    words = set()
    for match in _WORD_RE.finditer(window):
        folded = _fold(match.group(0))
        if folded in _STOPWORDS:
            continue
        words.add(_stem(folded))
    return frozenset(words)


def extract_facts(text: str) -> list[Fact]:
    """All fact tokens in `text`, in order of appearance (see the module
    docstring, step 3, for what counts)."""
    facts: list[Fact] = []
    for match in _FACT_RE.finditer(text or ''):
        kind = unit = value = None
        if match.group('iso'):
            year, month, day = (int(part) for part in match.group('iso').split('-'))
            kind, unit, value = 'date', '', _date_value(day, month, year)
        elif match.group('dmy'):
            day_s, month_s, year_s = match.group('dmy').split('.')
            year = int(year_s) + (2000 if len(year_s) == 2 else 0)
            kind, unit, value = 'date', '', _date_value(int(day_s), int(month_s), year)
        elif match.group('dotted'):
            continue  # section numbers / version strings, never a fact
        elif match.group('prefix'):
            kind, unit = _UNITS[match.group('prefix').lower()]
            value = _normalize_number(match.group('pnum'))
        else:
            raw_number = match.group('num')
            unit_word = match.group('unit')
            if unit_word:
                kind, unit = _UNITS[unit_word.lower()]
            else:
                if len(re.sub(r'\D', '', raw_number)) < 2:
                    continue  # single digits: list markers, counts, noise
                kind, unit = 'number', ''
            value = _normalize_number(raw_number)
        if value is None:
            continue
        facts.append(Fact(
            kind=kind, unit=unit, value=value, text=match.group(0).strip(),
            start=match.start(), end=match.end(),
            context=_context_words(text, match.start(), match.end()),
        ))
    return facts


def conflicting_facts(facts: list[Fact], other_facts: list[Fact]) -> list[tuple[Fact, Fact]]:
    """Fact pairs that *may* contradict each other (module docstring, step
    4). Empty when the two fact sets agree or share no context."""
    values = {(f.kind, f.unit, f.value) for f in facts}
    other_values = {(f.kind, f.unit, f.value) for f in other_facts}
    if values == other_values:
        return []
    pairs = []
    for fact in facts:
        if (fact.kind, fact.unit, fact.value) in other_values:
            continue
        for other in other_facts:
            if other.key != fact.key or other.value == fact.value:
                continue
            if (other.kind, other.unit, other.value) in values:
                continue
            if fact.context & other.context:
                pairs.append((fact, other))
    return pairs


# --- candidate search ----------------------------------------------------------


def _is_postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == 'postgresql'


def _cosine_similarity(a, b) -> float:
    a = [float(x) for x in a]
    b = [float(x) for x in b]
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _lineage_job_ids(db: Session, document: Document) -> set[str]:
    """`source_job_id`s of every version linked to `document` through the
    `previous_job_id` chain, in both directions (bounded walk)."""
    lineage = {document.source_job_id}
    previous = document.previous_job_id
    for _ in range(_MAX_LINEAGE_STEPS):
        if not previous or previous in lineage:
            break
        lineage.add(previous)
        previous = db.execute(
            select(Document.previous_job_id).where(Document.source_job_id == previous)
        ).scalar_one_or_none()
    frontier = set(lineage)
    for _ in range(_MAX_LINEAGE_STEPS):
        newer = set(db.execute(
            select(Document.source_job_id).where(Document.previous_job_id.in_(frontier))
        ).scalars()) - lineage
        if not newer:
            break
        lineage |= newer
        frontier = newer
    return lineage


def _candidate_filter(stmt, document: Document, lineage: set[str]):
    return (
        stmt.join(Document, Chunk.document_id == Document.id)
        .where(Document.collection_slug == document.collection_slug)
        .where(Document.status == DocumentStatus.INDEXED)
        .where(Document.id != document.id)
        .where(Document.source_job_id.not_in(lineage))
        .where(Chunk.embedding.is_not(None))
        .where(Chunk.embedding_model == document.embedding_model)
    )


def _neighbours_postgres(db, document, lineage, chunk: Chunk) -> list[tuple[Chunk, str, float]]:
    # Same binding discipline as Weave-Retrieval's vector_search: the query
    # vector is typed as the column's own VectorType, and return_type=Float
    # keeps pgvector's result processor away from the distance value.
    query_vec = bindparam('query_vec', chunk.embedding, type_=Chunk.embedding.type)
    distance = Chunk.embedding.op('<=>', return_type=Float)(query_vec)
    stmt = _candidate_filter(select(Chunk, Document.source_job_id, distance.label('distance')), document, lineage)
    stmt = stmt.order_by(distance.asc(), Chunk.id.asc()).limit(_TOP_K_PER_CHUNK)
    return [(other, job_id, 1.0 - float(dist)) for other, job_id, dist in db.execute(stmt).all()]


def _excerpt(text: str, fact: Fact) -> str:
    start = max(0, fact.start - _EXCERPT_CHARS)
    end = min(len(text), fact.end + _EXCERPT_CHARS)
    snippet = ' '.join(text[start:end].split())
    if start > 0:
        snippet = '…' + snippet
    if end < len(text):
        snippet = snippet + '…'
    return snippet


def _unique_texts(facts) -> list[str]:
    seen: list[str] = []
    for fact in facts:
        if fact.text not in seen:
            seen.append(fact.text)
    return seen[:_MAX_FACTS_PER_REPORT]


def find_candidates(db: Session, document: Document) -> list[dict]:
    """Possible contradictions between `document` (just indexed) and other
    documents of its knowledge space, as report dicts in the shape of
    Weave-Ingest's `POST /api/v1/quality/contradictions` `reports[]` items.
    Highest similarity first, capped at
    settings.contradiction_max_reports_per_document. Read-only."""
    if not document.collection_slug or document.status != DocumentStatus.INDEXED:
        return []
    if not document.embedding_model:
        return []
    limit = max(0, settings.contradiction_max_reports_per_document)
    if limit == 0:
        return []
    threshold = settings.contradiction_min_similarity

    own_chunks = db.execute(
        select(Chunk).where(Chunk.document_id == document.id).where(Chunk.embedding.is_not(None))
        .order_by(Chunk.chunk_index)
    ).scalars().all()
    # Cheap pre-filter: a chunk without any fact token can never qualify,
    # so it never costs a vector query.
    own = [(chunk, facts) for chunk in own_chunks if (facts := extract_facts(chunk.text))]
    if not own:
        return []

    lineage = _lineage_job_ids(db, document)
    postgres = _is_postgres(db)
    sqlite_candidates: list[tuple[Chunk, str]] = []
    if not postgres:
        stmt = _candidate_filter(select(Chunk, Document.source_job_id), document, lineage)
        stmt = stmt.order_by(Chunk.id.desc()).limit(_SQLITE_MAX_CANDIDATE_CHUNKS)
        sqlite_candidates = [(other, job_id) for other, job_id in db.execute(stmt).all()]
        if not sqlite_candidates:
            return []

    found: list[tuple[float, dict]] = []
    seen_pairs: set[tuple[int, int]] = set()
    other_facts_cache: dict[int, list[Fact]] = {}
    for chunk, facts in own:
        if postgres:
            neighbours = _neighbours_postgres(db, document, lineage, chunk)
        else:
            scored = [
                (other, job_id, _cosine_similarity(chunk.embedding, other.embedding))
                for other, job_id in sqlite_candidates
            ]
            scored.sort(key=lambda item: (-item[2], item[0].id))
            neighbours = scored[:_TOP_K_PER_CHUNK]
        for other, other_job_id, similarity in neighbours:
            if similarity < threshold or (chunk.id, other.id) in seen_pairs:
                continue
            if other.id not in other_facts_cache:
                other_facts_cache[other.id] = extract_facts(other.text)
            pairs = conflicting_facts(facts, other_facts_cache[other.id])
            if not pairs:
                continue
            seen_pairs.add((chunk.id, other.id))
            found.append((similarity, {
                'collection_slug': document.collection_slug,
                'source_job_id': document.source_job_id,
                'other_source_job_id': other_job_id,
                'similarity': round(float(similarity), 4),
                'heading_path': list(chunk.heading_path or []),
                'other_heading_path': list(other.heading_path or []),
                'excerpt': _excerpt(chunk.text, pairs[0][0]),
                'other_excerpt': _excerpt(other.text, pairs[0][1]),
                'facts': _unique_texts(fact for fact, _ in pairs),
                'other_facts': _unique_texts(other_fact for _, other_fact in pairs),
            }))
    # Ingest folds all reports of one document pair into a single review task
    # that keeps _MAX_REPORTS_PER_OTHER_DOCUMENT excerpt pairs, so a near-copy
    # must not use up the whole cap and hide conflicts with other documents.
    found.sort(key=lambda item: -item[0])
    per_other: dict[str, int] = {}
    reports: list[dict] = []
    for _, report in found:
        if len(reports) >= limit:
            break
        other_job_id = report['other_source_job_id']
        if per_other.get(other_job_id, 0) >= _MAX_REPORTS_PER_OTHER_DOCUMENT:
            continue
        per_other[other_job_id] = per_other.get(other_job_id, 0) + 1
        reports.append(report)
    return reports
