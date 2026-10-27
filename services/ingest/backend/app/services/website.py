"""Generic website / intranet importer: a `PageSource` (see
app/services/confluence.py) over plain HTML pages, crawled by the very same
chunked `import_confluence` task as a Confluence space.

Page ids ARE the normalized page URLs (frontier, visited map, ImportPageState
and the job's `source_page_id` all carry the URL), so every existing
refresh/missing-page/withdraw path works unchanged. A page's `version` is a
31-bit hash of its extracted main content: a refresh run skips a page whose
hash is unchanged and chains a changed one onto its predecessor.

Network discipline: every request goes through safe_fetch (DNS pinning,
private-address block, redirect re-validation, size cap). An intranet host
is reachable only when the connection was explicitly released for it
(ImportSource.allow_private_target, admin-only) -- then exactly that host is
passed as `allowed_private_hosts`, never a wildcard. Credentials are sent to
the connection's own origin only, never appear in error messages, and the
stored HTML never contains them.

Scope (`crawl_scope`): 'page' = the start page only; 'prefix' = pages whose
URL starts with the start page's directory; 'host' = every page on the
start page's host. Links to other hosts are never followed. Linked documents
(PDF/Office) are returned as attachments so the existing attachment rules
(classification by magic bytes, size caps, optional OCR child jobs) apply.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from app.models.models import ImportAuthType
from app.services.confluence import AttachmentMeta, ConfluenceError, Page, PageContext
from app.services.confluence_markdown import ConversionResult, html_to_markdown, render_frontmatter
from app.services.safe_fetch import SafeFetchError, safe_fetch

logger = logging.getLogger(__name__)

USER_AGENT = 'Weave-Ingest/0.7 (+website import)'
CRAWL_SCOPES = ('page', 'prefix', 'host')
# Longer URLs are skipped: ImportRun/ImportSync.scope_value is 512 chars and
# ImportPageState.page_id 2048, and a URL this long is never a page worth
# importing.
# 512 chars * 4 bytes (worst-case UTF-8) stays inside Postgres' btree entry
# limit for the (source_id, collection_id, page_id) unique index.
MAX_PAGE_URL_CHARS = 512
MAX_START_URL_CHARS = 512
# Links discovered per page (link-bomb guard, in addition to the run's
# frontier cap in import_tasks).
MAX_LINKS_PER_PAGE = 500
_LINK_CACHE_SIZE = 64

DOCUMENT_EXTENSIONS = ('.pdf', '.docx', '.pptx', '.xlsx', '.xls')
_SKIP_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp', '.tif', '.tiff',
    '.zip', '.gz', '.tar', '.7z', '.rar', '.exe', '.dmg', '.msi', '.iso',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.ogg', '.wav', '.css', '.js', '.json', '.xml', '.rss', '.atom',
    '.doc', '.ppt', '.odt', '.ods', '.odp', '.rtf', '.csv', '.txt',
)
_HTML_CONTENT_TYPES = ('text/html', 'application/xhtml+xml')
_STRIP_TAGS = ('script', 'style', 'noscript', 'template', 'iframe', 'object', 'embed', 'svg', 'canvas', 'button', 'input', 'select', 'textarea')
# Unwrapped, not dropped: WebForms / classic SharePoint pages wrap the whole
# body in one <form>.
_UNWRAP_TAGS = ('form',)
_CHROME_TAGS = ('nav', 'header', 'footer', 'aside')
_CHROME_ROLES = ('navigation', 'banner', 'contentinfo', 'complementary', 'search')
_TRACKING_QUERY_KEYS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'fbclid', 'gclid', 'sessionid', 'jsessionid', 'phpsessid')

# "Rechtsstand" lines as printed by gesetze-im-internet.de and most official
# German legal sources. Conservative on purpose: only an explicit label with
# a date-ish value counts; anything else leaves legal_status unset.
_LEGAL_STATUS_PATTERNS = (
    re.compile(r'(?:Rechtsstand|Stand)\s*:\s*((?:zuletzt\s+ge(?:ä|ae)ndert\s+durch\s+)?[^\n]{4,200}?\d{4}[^\n]{0,80}?)(?:\n|$)', re.IGNORECASE),
    re.compile(r'(Zuletzt\s+ge(?:ä|ae)ndert\s+durch\s+[^\n]{4,200}?\d{4}[^\n]{0,80}?)(?:\n|$)', re.IGNORECASE),
)
_LEGAL_STATUS_MAX_CHARS = 240


def normalize_page_url(raw: str, *, base: str | None = None) -> str | None:
    """Absolute, fragment-free, tracking-parameter-free http(s) URL, or None
    when `raw` is not a page link (mailto:, javascript:, relative without
    base, embedded credentials, over-long)."""
    value = (raw or '').strip()
    if not value or value.startswith('#'):
        return None
    try:
        # urljoin leaves an absolute URL alone and resolves a relative one
        # (also one whose query happens to contain '://').
        if base:
            value = urljoin(base, value)
        parts = urlsplit(value)
        hostname, port = parts.hostname, parts.port  # both raise on malformed input
    except ValueError:
        return None
    if parts.scheme.lower() not in ('http', 'https') or not hostname:
        return None
    if parts.username or parts.password:
        return None
    netloc = hostname.lower()
    if port and not ((parts.scheme == 'http' and port == 80) or (parts.scheme == 'https' and port == 443)):
        netloc = f'{netloc}:{port}'
    path = parts.path or '/'
    query = '&'.join(
        item for item in parts.query.split('&')
        if item and item.split('=', 1)[0].lower() not in _TRACKING_QUERY_KEYS
    )
    normalized = urlunsplit((parts.scheme.lower(), netloc, path, query, ''))
    if len(normalized) > MAX_PAGE_URL_CHARS:
        return None
    return normalized


def _host_key(url: str) -> tuple[str, int | None]:
    parts = urlsplit(url)
    return (parts.hostname or '').lower(), parts.port


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or '').lower()


def origin_of(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) -- what a credential and a private-target
    release are bound to. A default port written out explicitly
    (https://h:443) is the same origin as one left implicit."""
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme.lower(), (parts.hostname or '').lower(), parts.port
    except ValueError:
        return '', '', None
    if (scheme == 'http' and port == 80) or (scheme == 'https' and port == 443):
        port = None
    return scheme, host, port


def same_origin(url: str, other: str) -> bool:
    return origin_of(url) == origin_of(other)


def page_base_url(page_url: str) -> str:
    """The directory a page's relative links resolve against, without the
    trailing slash html_to_markdown's _prepare appends itself
    (https://h/a/b/x.html -> https://h/a/b)."""
    return urljoin(page_url, '.').rstrip('/')


def private_hosts_for(*, kind: str, base_url: str, allow_private_target: bool, global_allowlist) -> frozenset[str]:
    """safe_fetch's allowed_private_hosts for one connection: the global
    allowlist plus, for a website connection an admin released for an
    internal target, exactly that connection's host (host:port when the
    base URL names a port). Shared by the API's /test and the worker."""
    hosts = set(global_allowlist)
    if kind == 'website' and allow_private_target:
        host, port = _host_key(base_url)
        if host:
            hosts.add(f'{host}:{port}' if port else host)
    return frozenset(hosts)


def scope_prefix(start_url: str) -> str:
    """The URL prefix a 'prefix' scope allows: the start page's directory
    (https://h/a/b/ for https://h/a/b/index.html and https://h/a/b/, and
    https://h/a/b for an extension-less https://h/a/b, which also matches
    https://h/a/b/...)."""
    parts = urlsplit(start_url)
    path = parts.path or '/'
    if not path.endswith('/'):
        head, _, last = path.rpartition('/')
        path = f'{head}/' if '.' in last else path
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def in_crawl_scope(url: str, *, start_url: str, crawl_scope: str) -> bool:
    if crawl_scope == 'page':
        return url == start_url
    if _host_key(url) != _host_key(start_url):
        return False
    if crawl_scope == 'host':
        return True
    prefix = scope_prefix(start_url)
    if url == start_url:
        return True
    if prefix.endswith('/'):
        return url.startswith(prefix)
    return url.startswith(prefix + '/') or url.startswith(prefix + '?')


def _path_extension(url: str) -> str:
    path = urlsplit(url).path
    last = path.rsplit('/', 1)[-1]
    dot = last.rfind('.')
    return last[dot:].lower() if dot != -1 else ''


def content_version(main_html: str) -> int:
    """31-bit content hash: fits ImportPageState.page_version (Integer) on
    every dialect; a refresh re-imports a page whose hash changed."""
    digest = hashlib.sha256(main_html.encode('utf-8')).digest()
    return int.from_bytes(digest[:4], 'big') & 0x7FFFFFFF


def extract_legal_status(text: str) -> str | None:
    """The 'Rechtsstand' line of a legal text, when the page states one
    explicitly; None otherwise (never guessed)."""
    sample = text[:40000]
    for pattern in _LEGAL_STATUS_PATTERNS:
        match = pattern.search(sample)
        if match:
            value = re.sub(r'\s+', ' ', match.group(1)).strip(' .;')
            if 4 <= len(value) <= _LEGAL_STATUS_MAX_CHARS:
                return value
    return None


def extract_main_html(soup: BeautifulSoup) -> tuple[str, str]:
    """(title, main-content HTML) of a parsed page: scripts/forms/page chrome
    (nav, header, footer, aside and their ARIA roles) are dropped, then the
    <main>/[role=main]/<article> element wins over the whole <body>."""
    title = ''
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    for element in soup.find_all(_STRIP_TAGS):
        element.decompose()
    for element in soup.find_all(_UNWRAP_TAGS):
        element.unwrap()
    for element in soup.find_all(_CHROME_TAGS):
        element.decompose()
    for element in soup.find_all(attrs={'role': _CHROME_ROLES}):
        element.decompose()
    main = soup.find('main') or soup.find(attrs={'role': 'main'}) or soup.find('article') or soup.body or soup
    if not title:
        heading = main.find(['h1', 'h2']) if main is not None else None
        title = heading.get_text(' ', strip=True) if heading is not None else ''
    html = main.decode_contents() if hasattr(main, 'decode_contents') else str(main)
    return title, html


def auth_headers(auth_type: ImportAuthType | str, auth_username: str, credential: str) -> dict[str, str]:
    kind = ImportAuthType(auth_type)
    if kind is ImportAuthType.NONE:
        return {}
    if not credential:
        raise ConfluenceError('connection has no credential')
    if kind is ImportAuthType.HTTP_BASIC:
        token = base64.b64encode(f'{auth_username}:{credential}'.encode()).decode('ascii')
        return {'Authorization': f'Basic {token}'}
    if kind is ImportAuthType.BEARER:
        return {'Authorization': f'Bearer {credential}'}
    raise ConfluenceError(f'authentication method {kind.value!r} is not supported for websites')


def _explain_status(status_code: int) -> str:
    if status_code in (401, 407):
        return 'authentication required or credential rejected'
    if status_code == 403:
        return 'access denied by the server'
    if status_code in (404, 410):
        return 'page not found'
    if status_code == 429:
        return 'rate limited by the server'
    if status_code >= 500:
        return 'server error'
    return 'unexpected response'


class WebsiteClient:
    """PageSource over one website connection (base_url + auth) and one run's
    scope (start_url + crawl_scope). Created per run by
    `create_website_client`; the worker never imports this class directly."""

    def __init__(
        self,
        *,
        base_url: str,
        start_url: str,
        crawl_scope: str = 'prefix',
        auth_type: ImportAuthType | str = ImportAuthType.NONE,
        auth_username: str = '',
        credential: str = '',
        allowed_private_hosts: frozenset[str] = frozenset(),
        timeout: float = 30.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        max_attachment_bytes: int = 20 * 1024 * 1024,
        include_linked_documents: bool = False,
        respect_robots: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        start = normalize_page_url(start_url)
        if start is None:
            raise ConfluenceError('start URL is not a valid http(s) page address')
        self.start_url = start
        self.crawl_scope = crawl_scope if crawl_scope in CRAWL_SCOPES else 'prefix'
        self._headers = auth_headers(auth_type, auth_username, credential)
        self._origin = _host_key(self.base_url)
        self._origin_full = origin_of(self.base_url)
        self.allowed_private_hosts = allowed_private_hosts
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_attachment_bytes = max_attachment_bytes
        self.include_linked_documents = include_linked_documents
        self.respect_robots = respect_robots
        self._robots: RobotFileParser | None = None
        self._robots_loaded = False
        # page url -> (child page urls, document urls), for iter_children /
        # iter_attachments right after fetch_page (the worker calls them in
        # that order; a miss re-fetches, which is correct, just slower).
        self._links: OrderedDict[str, tuple[list[str], list[str]]] = OrderedDict()

    # --- transport ---------------------------------------------------------

    def _request_headers(self, url: str, *, accept: str) -> dict[str, str]:
        headers = {'User-Agent': USER_AGENT, 'Accept': accept}
        # The credential belongs to the connection's origin only.
        if self._headers and origin_of(url) == self._origin_full:
            headers.update(self._headers)
        return headers

    def _fetch(self, url: str, *, accept: str, max_bytes: int):
        try:
            return safe_fetch(
                url,
                headers=self._request_headers(url, accept=accept),
                timeout=self.timeout,
                max_bytes=max_bytes,
                allowed_private_hosts=self.allowed_private_hosts,
            )
        except SafeFetchError as exc:
            # SafeFetchError messages carry the URL and the policy reason,
            # never request headers.
            raise ConfluenceError(f'fetching {url} failed: {exc}', transient=exc.transient) from exc

    def _robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        if not self._robots_loaded:
            self._robots_loaded = True
            parts = urlsplit(self.start_url)
            robots_url = urlunsplit((parts.scheme, parts.netloc, '/robots.txt', '', ''))
            try:
                response = self._fetch(robots_url, accept='text/plain', max_bytes=256 * 1024)
                if response.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(response.body.decode('utf-8', errors='replace').splitlines())
                    self._robots = parser
            except ConfluenceError:
                self._robots = None  # unreadable robots.txt: fail open, like browsers do
        if self._robots is None:
            return True
        return self._robots.can_fetch(USER_AGENT, url) and self._robots.can_fetch('*', url)

    # --- PageSource ----------------------------------------------------------

    def fetch_page(self, page_id: str) -> Page:
        url = normalize_page_url(page_id) or page_id
        if not self._robots_allows(url):
            raise ConfluenceError(f'{url} is excluded by the site\'s robots.txt', status_code=403)
        response = self._fetch(url, accept='text/html,application/xhtml+xml;q=0.9,*/*;q=0.5', max_bytes=self.max_response_bytes)
        if response.status_code != 200:
            raise ConfluenceError(
                f'{url} returned HTTP {response.status_code} ({_explain_status(response.status_code)})',
                status_code=response.status_code,
            )
        content_type = (response.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
        if content_type and content_type not in _HTML_CONTENT_TYPES:
            raise ConfluenceError(f'{url} is not an HTML page (content type {content_type})', status_code=415)
        if _host_key(response.final_url) != _host_key(url):
            raise ConfluenceError(f'{url} redirected to another host ({host_of(response.final_url)}); not imported')
        # Relative links resolve against where the page actually came from
        # (a same-host redirect may have moved it to another directory) --
        # and a page that was redirected out of the crawl scope is not
        # imported, except the start page itself, which defines the scope.
        final_url = normalize_page_url(response.final_url) or url
        if final_url != url and url != self.start_url and not in_crawl_scope(final_url, start_url=self.start_url, crawl_scope=self.crawl_scope):
            raise ConfluenceError(f'{url} redirected outside the import scope ({final_url}); not imported')

        soup = BeautifulSoup(response.body, 'html.parser')
        child_urls, document_urls = self._collect_links(soup, page_url=final_url)
        self._links[url] = (child_urls, document_urls)
        while len(self._links) > _LINK_CACHE_SIZE:
            self._links.popitem(last=False)
        title, main_html = extract_main_html(soup)
        return Page(
            id=url,
            title=(title or url)[:512],
            version=content_version(main_html),
            html=main_html,
            url=final_url,
        )

    def _collect_links(self, soup: BeautifulSoup, *, page_url: str) -> tuple[list[str], list[str]]:
        pages: list[str] = []
        documents: list[str] = []
        seen: set[str] = set()
        for anchor in soup.find_all('a', href=True):
            if len(pages) + len(documents) >= MAX_LINKS_PER_PAGE:
                break
            target = normalize_page_url(anchor['href'], base=page_url)
            if target is None or target in seen:
                continue
            seen.add(target)
            extension = _path_extension(target)
            if extension in DOCUMENT_EXTENSIONS:
                # Documents: same host as the connection, any path.
                if self.include_linked_documents and _host_key(target) == self._origin:
                    documents.append(target)
                continue
            if extension in _SKIP_EXTENSIONS:
                continue
            if self.crawl_scope != 'page' and in_crawl_scope(target, start_url=self.start_url, crawl_scope=self.crawl_scope):
                pages.append(target)
        return pages, documents

    def _links_for(self, page_id: str) -> tuple[list[str], list[str]]:
        url = normalize_page_url(page_id) or page_id
        cached = self._links.get(url)
        if cached is None:
            self.fetch_page(url)
            cached = self._links.get(url, ([], []))
        return cached

    def fetch_context(self, page_id: str) -> PageContext:
        # No hierarchy on a plain website; the host is recorded as `site`
        # by convert_web_page instead of a Confluence space key.
        return PageContext(space_key=None, ancestor_titles=[])

    def iter_children(self, page_id: str) -> Iterator[str]:
        if self.crawl_scope == 'page':
            return iter(())
        return iter(list(self._links_for(page_id)[0]))

    def iter_attachments(self, page_id: str) -> Iterator[AttachmentMeta]:
        if not self.include_linked_documents:
            return iter(())
        documents = self._links_for(page_id)[1]
        return iter([self._attachment(page_id, url) for url in documents])

    def _attachment(self, page_id: str, url: str) -> AttachmentMeta:
        filename = unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or 'document'

        def _download() -> bytes:
            response = self._fetch(url, accept='*/*', max_bytes=self.max_attachment_bytes)
            if response.status_code != 200:
                raise ConfluenceError(
                    f'{url} returned HTTP {response.status_code} ({_explain_status(response.status_code)})',
                    status_code=response.status_code,
                )
            return response.body

        return AttachmentMeta(
            id=url, filename=filename, media_type='', size_bytes=None, download_url=url, page_id=page_id, _fetch=_download,
        )

    def resolve_space_root(self, space_key: str) -> str:
        raise ConfluenceError('a website import starts from a page URL; there is no space to resolve')

    def resolve_page_by_title(self, space_key: str, title: str) -> str:
        raise ConfluenceError('a website import starts from a page URL; titles cannot be resolved')


def create_website_client(**kwargs) -> WebsiteClient:
    return WebsiteClient(**kwargs)


def probe(
    base_url: str,
    *,
    auth_type: ImportAuthType | str,
    auth_username: str,
    credential: str,
    allowed_private_hosts: frozenset[str],
    timeout: float,
    max_bytes: int,
) -> tuple[bool, str]:
    """Connection test for a website source: one GET of the base URL. Returns
    (ok, human-readable detail without any credential)."""
    try:
        client = WebsiteClient(
            base_url=base_url, start_url=base_url, crawl_scope='page', auth_type=auth_type,
            auth_username=auth_username, credential=credential, allowed_private_hosts=allowed_private_hosts,
            timeout=timeout, max_response_bytes=max_bytes, respect_robots=False,
        )
        response = client._fetch(client.start_url, accept='text/html,*/*;q=0.5', max_bytes=max_bytes)
    except ConfluenceError as exc:
        message = str(exc)
        if 'private' in message or 'blocked' in message or 'loopback' in message or 'link-local' in message:
            message += ' -- an internal address must be released by an administrator on the connection'
        return False, message
    if response.status_code >= 400:
        return False, f'{base_url} returned HTTP {response.status_code} ({_explain_status(response.status_code)})'
    content_type = (response.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
    if content_type and content_type not in _HTML_CONTENT_TYPES:
        return True, f'Reachable (HTTP {response.status_code}); note: the base address is not an HTML page ({content_type})'
    return True, f'Reachable (HTTP {response.status_code})'


def convert_web_page(
    page: Page,
    *,
    import_run_id: str,
    legal_source: bool = False,
    fetched_at: datetime | None = None,
) -> tuple[ConversionResult, dict]:
    """Markdown + frontmatter for a fetched website page. Returns the
    conversion and the provenance dict (source_url, fetched_at,
    legal_status) the worker stores on the job for release-time citations.
    Images keep their absolute URLs (no artifact capture) and links stay
    absolute links to the live site -- unlike Confluence pages, website
    cross-links are not rewritten to the imported copies at finalize."""
    body, images, _linked = html_to_markdown(page.html, base_url=page_base_url(page.url), capture_attachments=False)
    moment = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = moment.strftime('%Y-%m-%dT%H:%M:%SZ')
    meta: dict = {
        'title': page.title,
        'source': page.url,
        'site': host_of(page.url),
        'fetched_at': stamp,
        'content_hash': page.version,
        'import_run': import_run_id,
    }
    provenance: dict = {'source_url': page.url, 'fetched_at': stamp, 'source_kind': 'website'}
    if legal_source:
        legal_status = extract_legal_status(body)
        if legal_status:
            meta['legal_status'] = legal_status
            provenance['legal_status'] = legal_status
    markdown = render_frontmatter(meta) + body + '\n'
    return ConversionResult(markdown=markdown, images=images, linked_page_ids=[]), provenance
