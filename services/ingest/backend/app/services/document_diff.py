"""Section-aware comparison of two Markdown versions of one document
('Versionen vergleichen').

Used by GET /api/v1/portal/documents/{job_id}/diff
(app/api/document_freshness.py) and by the change feed's summaries
(app/services/change_feed.py). Pure functions, no database access: the
caller supplies the Markdown of both versions (a release snapshot when the
version is released, else the canonical portal snapshot -- the same text
GET /portal/documents/{id} shows).

Algorithm:
1. Strip the YAML frontmatter (it changes with every version: job_id,
   document_version, processed_at ... -- noise, not content).
2. Split the body into sections at ATX headings (`#`..`######`, outside
   fenced code blocks). A section's `heading_path` is the stack of headings
   above and including it (["Reisekosten", "Tagegeld"]); text before the
   first heading is the root section (heading_path []). Repeated paths are
   numbered internally so every section has a unique key.
3. Align sections by heading path; a section of one version without a
   counterpart is `added`/`removed`. (Order is the fallback for duplicate
   paths: the n-th "Allgemeines" of one version pairs with the n-th of the
   other.)
4. `difflib.SequenceMatcher` per aligned pair, on lines (trailing whitespace
   ignored, blank lines dropped); `equal` hunks are trimmed to 2 lines of
   context on each side. Unchanged sections are omitted.

The German `summary` lines are what the portal and the change feed show,
e.g. „Abschnitt „Reisekosten › Tagegeld“ geändert: 2 Zeilen ersetzt, 1
hinzugefügt“.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

CONTEXT_LINES = 2
_HEADING_RE = re.compile(r'^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$')
_FENCE_RE = re.compile(r'^[ \t]{0,3}(```|~~~)')
_ROOT_LABEL = 'Einleitung'


def strip_frontmatter(markdown: str | None) -> str:
    """The body below a leading `---` YAML block (unchanged when there is
    none or it is unterminated)."""
    text = (markdown or '').replace('\r\n', '\n')
    if text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end >= 0:
            return text[end + len('\n---\n'):]
    return text


@dataclass
class Section:
    heading_path: list[str]
    lines: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, ...]:
        return tuple(self.heading_path)


def split_sections(markdown: str | None) -> list[Section]:
    """Sections of the body (see module docstring). The heading line itself
    belongs to its section's path, not to its lines, so a renamed heading
    shows up as one section removed and one added."""
    sections: list[Section] = [Section([])]
    stack: list[tuple[int, str]] = []
    in_fence = False
    for raw in strip_frontmatter(markdown).split('\n'):
        line = raw.rstrip()
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            level, title = len(match.group(1)), match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(Section([item[1] for item in stack]))
            continue
        if line.strip():
            sections[-1].lines.append(line)
    if not sections[0].lines:
        sections.pop(0)
    return sections


def _keyed(sections: list[Section]) -> dict[tuple, Section]:
    """Unique keys: (path..., occurrence) so duplicate paths pair by order."""
    seen: dict[tuple[str, ...], int] = {}
    result: dict[tuple, Section] = {}
    for section in sections:
        index = seen.get(section.key, 0)
        seen[section.key] = index + 1
        result[(*section.key, index)] = section
    return result


def _hunks(base: list[str], target: list[str]) -> tuple[list[dict], int, int]:
    matcher = difflib.SequenceMatcher(a=base, b=target, autojunk=False)
    opcodes = matcher.get_opcodes()
    hunks: list[dict] = []
    added = removed = 0
    for position, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == 'equal':
            lines = base[i1:i2]
            first, last = position == 0, position == len(opcodes) - 1
            if first and last:
                continue
            if first:
                lines = lines[-CONTEXT_LINES:]
            elif last:
                lines = lines[:CONTEXT_LINES]
            elif len(lines) > 2 * CONTEXT_LINES:
                lines = lines[:CONTEXT_LINES] + lines[-CONTEXT_LINES:]
            hunks.append({'op': 'equal', 'base': lines, 'target': lines})
            continue
        removed += i2 - i1
        added += j2 - j1
        hunks.append({'op': tag, 'base': base[i1:i2], 'target': target[j1:j2]})
    return hunks, added, removed


def _plural(count: int, singular: str, plural: str) -> str:
    return f'{count} {singular if count == 1 else plural}'


def _section_summary(section: dict) -> str:
    label = ' › '.join(section['heading_path']) or _ROOT_LABEL
    if section['change'] == 'added':
        return f'Abschnitt „{label}“ hinzugefügt ({_plural(section["added_lines"], "Zeile", "Zeilen")})'
    if section['change'] == 'removed':
        return f'Abschnitt „{label}“ entfernt ({_plural(section["removed_lines"], "Zeile", "Zeilen")})'
    replaced = inserted = deleted = 0
    for hunk in section['hunks']:
        if hunk['op'] == 'replace':
            common = min(len(hunk['base']), len(hunk['target']))
            replaced += common
            inserted += len(hunk['target']) - common
            deleted += len(hunk['base']) - common
        elif hunk['op'] == 'insert':
            inserted += len(hunk['target'])
        elif hunk['op'] == 'delete':
            deleted += len(hunk['base'])
    parts = []
    if replaced:
        parts.append(f'{_plural(replaced, "Zeile", "Zeilen")} ersetzt')
    if inserted:
        parts.append(f'{inserted} hinzugefügt')
    if deleted:
        parts.append(f'{deleted} entfernt')
    return f'Abschnitt „{label}“ geändert: ' + ', '.join(parts)


def diff_markdown(base_markdown: str | None, target_markdown: str | None) -> dict:
    """Compare two versions; returns {stats, summary, sections} in the
    DocumentDiff shape (without the base/target refs, which the caller
    adds). Sections follow the target's order, removed ones are appended
    in the base's order."""
    base_sections = _keyed(split_sections(base_markdown))
    target_sections = _keyed(split_sections(target_markdown))
    sections: list[dict] = []
    for key, target in target_sections.items():
        base = base_sections.get(key)
        if base is None:
            sections.append({
                'heading_path': target.heading_path, 'change': 'added',
                'added_lines': len(target.lines), 'removed_lines': 0,
                'hunks': [{'op': 'insert', 'base': [], 'target': list(target.lines)}] if target.lines else [],
            })
            continue
        hunks, added, removed = _hunks(base.lines, target.lines)
        if added or removed:
            sections.append({
                'heading_path': target.heading_path, 'change': 'changed',
                'added_lines': added, 'removed_lines': removed, 'hunks': hunks,
            })
    for key, base in base_sections.items():
        if key not in target_sections:
            sections.append({
                'heading_path': base.heading_path, 'change': 'removed',
                'added_lines': 0, 'removed_lines': len(base.lines),
                'hunks': [{'op': 'delete', 'base': list(base.lines), 'target': []}] if base.lines else [],
            })
    stats = {
        'added_lines': sum(section['added_lines'] for section in sections),
        'removed_lines': sum(section['removed_lines'] for section in sections),
        'sections_added': sum(1 for section in sections if section['change'] == 'added'),
        'sections_removed': sum(1 for section in sections if section['change'] == 'removed'),
        'sections_changed': sum(1 for section in sections if section['change'] == 'changed'),
    }
    summary = [_section_summary(section) for section in sections]
    if not summary:
        summary = ['Keine inhaltlichen Änderungen']
    return {'stats': stats, 'summary': summary, 'sections': sections}


def changed_text(diff: dict) -> list[str]:
    """Heading paths and inserted/replaced target lines of a diff -- the
    text a change-feed keyword must occur in (plus the removed lines, so a
    subscription also learns that e.g. „Tagegeld“ was deleted)."""
    texts: list[str] = []
    for section in diff.get('sections') or []:
        texts.extend(section['heading_path'])
        for hunk in section['hunks']:
            if hunk['op'] != 'equal':
                texts.extend(hunk['target'])
                texts.extend(hunk['base'])
    return texts
