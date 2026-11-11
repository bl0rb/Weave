"""XML upload -> Markdown body, rendered one of two ways:

- **Outline** (data-like XML): the root and its direct children become
  headings, anything deeper a nested bullet list, whitespace collapsed.
- **Content export** (any element text is HTML -- typically CDATA from a
  CMS or knowledge-base export): the HTML would otherwise land as one raw
  bullet line (inline styles, `&nbsp;` runs, layout tables). Instead every
  element holding HTML becomes an article:
  - `# Folder › Subfolder` per folder (the titled ancestors) and
    `## Article` per article, so the knowledge chunker (H1/H2 close a chunk,
    the heading path becomes the embedding breadcrumb) gets one article per
    chunk with its folder as context;
  - an article exported several times (same `id`, e.g. once per index
    entry) is rendered once; the other entry titles pointing to it are
    listed as `**Stichworte:**`, so synonyms stay findable;
  - plain-text siblings (date, author, ...) stay as `- **field**: value`;
  - the HTML becomes Markdown: one-column layout tables are unwrapped,
    custom elements with a header attribute (foldouts) become `###`
    sections, data tables become one record per row with the column
    headers repeated (rowspan/colspan expanded) so every record is
    self-contained and a long table can be split between rows;
  - links to another element of the same export (its `id` in the href) are
    resolved to that element's title, other non-http(s) links and images
    are dropped (link text kept), as are comments and scripts.

Titles come from a `title`/`name`/`label`/`heading`/`subject` attribute or
a child element of that name. The stdlib parser is used: expat's built-in
entity-expansion limits apply, external entities are never fetched.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdownify import MarkdownConverter

_TITLE_NAMES = ('title', 'name', 'label', 'heading', 'subject')
_STRIP_TAGS = ('script', 'style', 'iframe', 'form', 'object', 'embed', 'noscript')
_INVISIBLE_CHARS = dict.fromkeys(map(ord, '\u200b\u200c\u200d\u2060\ufeff'))
_HTML_MARKUP_RE = re.compile(r'<(?:p|div|span|table|ul|ol|li|br|strong|b|em|i|a|h[1-6]|font)\b[^>]*>', re.IGNORECASE)
_HEADER_ATTRIBUTE_RE = re.compile(r'(?:^|[-_])(?:header|heading|title|summary|label)$', re.IGNORECASE)
_GREY_RE = re.compile(r'background(?:-color)?\s*:\s*#([0-9a-f]{3}|[0-9a-f]{6})\b', re.IGNORECASE)
_LINK_TOKEN_RE = re.compile(r'[A-Za-z0-9_-]{8,}')
_PLACEHOLDER = 'WEAVEXMLTABLE{}X'
_PLACEHOLDER_RE = re.compile(r'WEAVEXMLTABLE\d+X')
_PLACEHOLDER_LINE_RE = re.compile(
    r'^(?P<prefix>[ \t]*(?:(?:[-*+]|\d+\.)[ \t]+)?)(?P<token>WEAVEXMLTABLE\d+X)[ \t]*$', re.MULTILINE
)
_EMPTY_BULLET_RE = re.compile(r'^[-*+]$')
# colspan/rowspan are attacker-controllable: capped so `colspan="1000000000"`
# cannot blow up the expanded grid (rowspan is capped at the rows left).
_MAX_COLSPAN = 50

_CONVERTER = MarkdownConverter(heading_style='ATX', bullets='-')

LinkResolver = Callable[[str], 'str | None']


def _clean_text(value: str | None) -> str:
    return ' '.join((value or '').translate(_INVISIBLE_CHARS).split())


def _fold(value: str) -> str:
    """Comparison key: letters and digits only ("Colon-Hydro-   Therapie"
    and "Colon-Hydro-Therapie" are the same title)."""
    return re.sub(r'\W+', '', _clean_text(value).casefold())


def _local_name(tag: object) -> str:
    name = str(tag)
    return name.rsplit('}', 1)[-1] if '}' in name else name


def _has_html(element) -> bool:
    return bool(_HTML_MARKUP_RE.search(element.text or ''))


# --- Outline (data-like XML) ------------------------------------------------------

def _xml_label(element) -> str:
    name = _local_name(element.tag)
    attributes = ', '.join(f'{_local_name(key)}={value}' for key, value in element.attrib.items())
    return f'{name} ({attributes})' if attributes else name


def _outline(element, depth: int, lines: list[str], label: Callable = _xml_label) -> None:
    """Render one element: the root and its direct children become headings
    (document-like XML stays navigable), anything deeper a nested bullet
    list (data-like XML stays compact). Text and tail whitespace is
    collapsed; comments/processing instructions are already dropped by
    ElementTree."""
    text = ' '.join((element.text or '').split())
    children = list(element)
    if depth <= 1:
        lines.append(f"{'#' * (depth + 1)} {label(element)}")
        lines.append('')
        if text:
            lines.append(text)
            lines.append('')
    else:
        indent = '  ' * (depth - 2)
        lines.append(f'{indent}- **{label(element)}**' + (f': {text}' if text else ''))
    for child in children:
        _outline(child, depth + 1, lines, label)
    if depth <= 1 and children and lines and lines[-1] != '':
        lines.append('')
    tail = ' '.join((element.tail or '').split())
    if tail:
        indent = '  ' * max(depth - 2, 0)
        lines.append(f'{indent}- {tail}' if depth >= 2 else tail)


# --- HTML fragment -> Markdown ------------------------------------------------------

def _span(cell: Tag, attribute: str, maximum: int) -> int:
    try:
        value = int(str(cell.get(attribute) or '1').strip())
    except ValueError:
        return 1
    return min(max(value, 1), maximum)


def _table_grid(table: Tag) -> list[list[Tag | None]]:
    """Rows x columns of cell references, rowspan/colspan expanded (a
    spanning cell sits in every slot it covers)."""
    rows = [row for row in table.find_all('tr') if row.find_parent('table') is table]
    grid: list[list[Tag | None]] = []
    pending: dict[int, list] = {}  # column -> [cell, rows still covered]
    for row_index, row in enumerate(rows):
        queue = row.find_all(['td', 'th'], recursive=False)
        line: list[Tag | None] = []
        column = 0
        while queue or any(col >= column for col in pending):
            if column in pending:
                cell, remaining = pending[column]
                line.append(cell)
                if remaining <= 1:
                    del pending[column]
                else:
                    pending[column][1] = remaining - 1
            elif queue:
                cell = queue.pop(0)
                rowspan = _span(cell, 'rowspan', len(rows) - row_index)
                colspan = _span(cell, 'colspan', _MAX_COLSPAN)
                line.extend([cell] * colspan)
                if rowspan > 1:
                    for offset in range(colspan):
                        pending[column + offset] = [cell, rowspan - 1]
                column += colspan
                continue
            else:
                line.append(None)
            column += 1
        grid.append(line)
    width = max((len(line) for line in grid), default=0)
    return [line + [None] * (width - len(line)) for line in grid]


def _distinct(cells: list[Tag | None]) -> list[tuple[int, Tag]]:
    """(column, cell) per cell of a row, a colspan counted once."""
    seen: list[tuple[int, Tag]] = []
    for column, cell in enumerate(cells):
        if cell is not None and not any(cell is other for _, other in seen):
            seen.append((column, cell))
    return seen


def _is_header_cell(cell: Tag) -> bool:
    """`<th>`, or a neutral grey background -- the usual WYSIWYG marking for
    header and label cells (white/transparent and coloured highlights do
    not count)."""
    if cell.name == 'th':
        return True
    match = _GREY_RE.search(cell.get('style') or '')
    if not match:
        return False
    digits = match.group(1)
    if len(digits) == 3:
        digits = ''.join(ch * 2 for ch in digits)
    red, green, blue = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
    return red == green == blue and red < 0xF0


def _expand_placeholders(markdown: str, rendered: dict[str, str]) -> str:
    def block(match: re.Match) -> str:
        prefix = match.group('prefix')
        pad = ' ' * len(prefix)
        lines = rendered.get(match.group('token'), '').split('\n')
        return prefix + lines[0] + ''.join('\n' + (pad + line if line else '') for line in lines[1:])

    markdown = _PLACEHOLDER_LINE_RE.sub(block, markdown)
    # A table placed inline (inside a span, ...) is rare; keep its content.
    return _PLACEHOLDER_RE.sub(lambda match: rendered.get(match.group(0), ''), markdown)


def _tidy(markdown: str) -> str:
    """Strip trailing whitespace, collapse space runs left by `&nbsp;`
    padding (outside code fences, keeping indentation), drop empty bullets
    and blank-line runs."""
    lines: list[str] = []
    in_fence = False
    for line in markdown.split('\n'):
        stripped = line.rstrip()
        body = stripped.lstrip(' ')
        if body.startswith(('```', '~~~')):
            in_fence = not in_fence
        if in_fence:
            lines.append(stripped)
            continue
        if _EMPTY_BULLET_RE.match(body):
            continue
        lines.append(stripped[: len(stripped) - len(body)] + re.sub(r'[ \t]{2,}', ' ', body))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class _FragmentRenderer:
    def __init__(self) -> None:
        self.rendered: dict[str, str] = {}

    def to_markdown(self, soup: BeautifulSoup) -> str:
        return _tidy(_expand_placeholders(_CONVERTER.convert_soup(soup), self.rendered))

    def cell_markdown(self, cell: Tag) -> str:
        return self.to_markdown(BeautifulSoup(cell.decode_contents(), 'html.parser'))

    def label(self, cell: Tag) -> str:
        return _clean_text(self.cell_markdown(cell).replace('**', '').replace('\n', ' '))

    def render_table(self, grid: list[list[Tag | None]]) -> str:
        """A data table as records: per row the first cell as a bold label,
        the other cells below it -- `- **Column:** value` when the table has
        a header row, plain blocks otherwise. Single-cell rows (captions,
        section dividers) stay a paragraph of their own."""
        cache: dict[int, str] = {}

        def content(cell: Tag) -> str:
            if id(cell) not in cache:
                cache[id(cell)] = re.sub(r'\n[ \t]*\n', '\n', self.cell_markdown(cell))
            return cache[id(cell)]

        rows = [_distinct(line) for line in grid]
        records: list[str] = []
        start = 0
        while start < len(rows) and len(rows[start]) <= 1:
            if rows[start] and (caption := self.label(rows[start][0][1])):
                records.append(f'**{caption}**')
            start += 1

        headers: dict[int, str] = {}
        if start < len(rows) - 1:
            first = rows[start]
            in_thead = first[0][1].find_parent('thead') is not None
            body_has_data = any(not _is_header_cell(cell) for row in rows[start + 1:] for _, cell in row[1:])
            if in_thead or (all(_is_header_cell(cell) for _, cell in first) and body_has_data):
                headers = {column: self.label(cell) for column, cell in first}
                start += 1

        def header_of(column: int) -> str:
            owner = max((col for col in headers if col <= column), default=None)
            return headers[owner] if owner is not None else ''

        previous: list[tuple[int, Tag]] = []
        for row in rows[start:]:
            # A row made only of cells spanning down from the row above
            # would repeat that record verbatim.
            carried = len(row) == len(previous) and all(a[1] is b[1] for a, b in zip(row, previous))
            previous = row
            if carried:
                continue
            if len(row) == 1:
                cell = row[0][1]
                if _is_header_cell(cell):
                    text = self.label(cell)
                    records.append(f'**{text}**' if text else '')
                else:
                    records.append(content(cell))
                continue
            lines: list[str] = []
            if row and (label := self.label(row[0][1])):
                lines.append(f'**{label}**')
            for column, cell in row[1:]:
                value = content(cell)
                key = header_of(column)
                if not value:
                    continue
                if not key:
                    lines.append(value)
                elif '\n' in value or value.startswith(('- ', '* ', '#')):
                    lines.append(f'- **{key}:**')
                    lines.extend(('  ' + line) if line else line for line in value.split('\n'))
                else:
                    lines.append(f'- **{key}:** {value}')
            records.append('\n'.join(lines))
        return '\n\n'.join(record for record in records if record)


def _prepare(soup: BeautifulSoup, resolve_link: LinkResolver | None) -> None:
    for element in soup.find_all(string=lambda value: isinstance(value, Comment)):
        element.extract()
    for element in soup.find_all(_STRIP_TAGS):
        element.decompose()
    for text in soup.find_all(string=True):
        if isinstance(text, NavigableString):
            cleaned = str(text).replace('\xa0', ' ').translate(_INVISIBLE_CHARS)
            if cleaned != str(text):
                text.replace_with(cleaned)
    for image in soup.find_all('img'):
        if urlsplit((image.get('src') or '').strip()).scheme.lower() not in ('http', 'https'):
            image.decompose()
    # Nested emphasis would render as `******x******`.
    for tag in soup.find_all(['strong', 'b', 'em', 'i']):
        same_kind = ['strong', 'b'] if tag.name in ('strong', 'b') else ['em', 'i']
        if tag.find_parent(same_kind) is not None or not tag.get_text(strip=True):
            tag.unwrap()
    # Headings inside an article stay below its `##` heading.
    for heading in soup.find_all(re.compile(r'^h[1-6]$')):
        heading.name = f'h{min(int(heading.name[1]) + 2, 6)}'
    for anchor in soup.find_all('a'):
        href = (anchor.get('href') or '').strip()
        title = resolve_link(href) if resolve_link and href else None
        if title:
            text = anchor.get_text(' ', strip=True)
            if _fold(title) != _fold(text):
                anchor.append(f' → {title}' if text else f'→ {title}')
            anchor.unwrap()
        elif urlsplit(href).scheme.lower() not in ('http', 'https'):
            anchor.unwrap()
        else:
            anchor.attrs = {'href': href}


def _section_header(element: Tag) -> str:
    for name, value in element.attrs.items():
        if isinstance(value, str) and _HEADER_ATTRIBUTE_RE.search(name) and _clean_text(value):
            return _clean_text(value)
    return ''


def html_fragment_to_markdown(html: str, resolve_link: LinkResolver | None = None) -> str:
    """Convert an HTML fragment (an exported article body) to Markdown; see
    the module docstring for the table/section/link handling."""
    soup = BeautifulSoup(html or '', 'html.parser')
    _prepare(soup, resolve_link)
    renderer = _FragmentRenderer()

    # Reverse document order visits nested tables before the table holding
    # them. Pass 1: one-column tables are layout -- unwrap them in the DOM.
    for table in reversed(soup.find_all('table')):
        grid = _table_grid(table)
        if grid and len(grid[0]) <= 1:
            wrapper = soup.new_tag('div')
            for line in grid:
                cell = line[0] if line else None
                if cell is not None and cell.parent is not None:
                    cell.name = 'div'
                    cell.attrs = {}
                    wrapper.append(cell.extract())
            table.replace_with(wrapper)

    # Custom elements (foldouts, tabs, ...) carrying a header attribute.
    for element in soup.find_all(lambda tag: '-' in tag.name):
        header = _section_header(element)
        if header:
            if element.find_parent(['td', 'th']) is not None:
                title = soup.new_tag('p')
                strong = soup.new_tag('strong')
                strong.string = header
                title.append(strong)
            else:
                title = soup.new_tag('h3')
                title.string = header
            element.insert_before(title)
        element.unwrap()

    # Pass 2: data tables -> records, swapped for a placeholder that the
    # enclosing conversion replaces with the rendered block (markdownify
    # would escape Markdown inserted as text).
    for table in reversed(soup.find_all('table')):
        token = _PLACEHOLDER.format(len(renderer.rendered))
        renderer.rendered[token] = renderer.render_table(_table_grid(table))
        placeholder = soup.new_tag('p')
        placeholder.string = token
        table.replace_with(placeholder)

    return renderer.to_markdown(soup)


# --- Content export (XML carrying HTML) ------------------------------------------

def _title_of(element):
    """(title, child element the title came from or None)."""
    attributes = {_local_name(key).lower(): value for key, value in element.attrib.items()}
    for name in _TITLE_NAMES:
        if value := _clean_text(attributes.get(name)):
            return value, None
    for child in element:
        if (
            _local_name(child.tag).lower() in _TITLE_NAMES
            and len(child) == 0
            and not _has_html(child)
            and (value := _clean_text(child.text))
        ):
            return value, child
    return '', None


def _title_label(element) -> str:
    return _title_of(element)[0] or _xml_label(element)


@dataclass
class _Section:
    path: tuple[str, ...]
    title: str | None  # None: folder-level fields, no article heading
    meta: list[str] = field(default_factory=list)
    bodies: list[tuple[str, str]] = field(default_factory=list)  # (subheading, html)
    aliases: list[str] = field(default_factory=list)


def _collect(root) -> tuple[list[_Section], dict[str, str]]:
    parents = {id(child): parent for parent in root.iter() for child in parent}
    with_html_below: set[int] = set()
    for element in root.iter():
        if _has_html(element):
            current = element
            while current is not None and id(current) not in with_html_below:
                with_html_below.add(id(current))
                current = parents.get(id(current))
    sections: list[_Section] = []
    articles: dict[tuple, _Section] = {}
    titles: dict[str, str] = {}

    def visit(element, path: tuple[str, ...]) -> None:
        own_title, title_child = _title_of(element)
        html_children = [child for child in element if _has_html(child)]
        others = [child for child in element if child is not title_child and not _has_html(child)]
        meta: list[str] = []
        for child in others:
            if id(child) not in with_html_below:
                _outline(child, 2, meta, _title_label)

        if html_children:
            key = tuple(child.get('id') or child.text for child in html_children)
            section = articles.get(key)
            if section is None:
                title = _title_of(html_children[0])[0] or own_title or _local_name(html_children[0].tag)
                section = articles[key] = _Section(path=path, title=title, meta=meta)
                multiple = len(html_children) > 1
                for child in html_children:
                    subheading = (_title_of(child)[0] or _local_name(child.tag)) if multiple else ''
                    section.bodies.append((subheading, child.text or ''))
                sections.append(section)
            if own_title and own_title not in section.aliases:
                section.aliases.append(own_title)
            for carrier in (element, *html_children):
                if carrier.get('id'):
                    titles[carrier.get('id').lower()] = section.title
        elif meta:
            sections.append(_Section(path=path + ((own_title,) if own_title else ()), title=None, meta=meta))

        child_path = path + ((own_title,) if own_title else ())
        for child in others:
            if id(child) in with_html_below:
                visit(child, child_path)

    if _has_html(root):
        wrapper = ElementTree.Element('document')
        wrapper.append(root)
        root = wrapper
    visit(root, ())
    for element in root.iter():
        if element.get('id') and (title := _title_of(element)[0]):
            titles.setdefault(element.get('id').lower(), title)
    return sections, titles


def _link_resolver(titles: dict[str, str]) -> LinkResolver:
    def resolve(href: str) -> str | None:
        if href.startswith('#') and href[1:].lower() in titles:
            return titles[href[1:].lower()]
        for token in reversed(_LINK_TOKEN_RE.findall(href)):
            if token.lower() in titles:
                return titles[token.lower()]
        return None

    return resolve


def _content_export_to_markdown(root) -> str:
    sections, titles = _collect(root)
    resolve = _link_resolver(titles)
    lines: list[str] = []
    path: tuple[str, ...] | None = None
    for section in sections:
        if section.path != path:
            path = section.path
            if path:
                lines += [f"# {' › '.join(path)}", '']
        if section.title is not None:
            lines += [f'## {section.title}', '']
        synonyms = [alias for alias in section.aliases if _fold(alias) != _fold(section.title or '')]
        if synonyms:
            lines += [f"**Stichworte:** {', '.join(synonyms)}", '']
        if section.meta:
            lines += section.meta + ['']
        for subheading, html in section.bodies:
            if subheading:
                lines += [f'### {subheading}', '']
            if body := html_fragment_to_markdown(html, resolve):
                lines += [body, '']
    return '\n'.join(lines).rstrip() + '\n'


def xml_to_markdown(text: str) -> str:
    root = ElementTree.fromstring(text)
    if any(_has_html(element) for element in root.iter()):
        return _content_export_to_markdown(root)
    lines: list[str] = []
    _outline(root, 0, lines)
    return '\n'.join(lines).rstrip() + '\n'
