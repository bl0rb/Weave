"""Website / intranet importer (app/services/website.py): URL scope rules,
main-content extraction, legal-status extraction, and the PageSource client
over a fake safe_fetch -- credentials stay on the connection's origin,
private targets are blocked unless released, linked documents become
attachments."""

from __future__ import annotations

import pytest

from app.models.models import ImportAuthType
from app.services import website
from app.services.confluence import ConfluenceError, Page
from app.services.safe_fetch import SafeFetchError, SafeFetchResponse

START = 'https://docs.example.com/handbuch/index.html'


@pytest.mark.parametrize('raw, expected', [
    ('HTTPS://Docs.Example.com:443/a/b?utm_source=x&id=2#frag', 'https://docs.example.com/a/b?id=2'),
    ('http://host:8080/x', 'http://host:8080/x'),
    ('mailto:someone@example.com', None),
    ('javascript:void(0)', None),
    ('https://user:pw@host/x', None),
    ('#top', None),
    ('../other.html', None),
])
def test_normalize_page_url(raw, expected):
    assert website.normalize_page_url(raw) == expected


def test_normalize_page_url_resolves_relative_against_base():
    assert website.normalize_page_url('../a.html', base='https://h/x/y/index.html') == 'https://h/x/a.html'
    # A relative link whose query contains '://' is still relative.
    assert website.normalize_page_url('/redirect?to=https://x', base='https://h/docs/') == 'https://h/redirect?to=https://x'
    assert website.normalize_page_url('https://other/x', base='https://h/docs/') == 'https://other/x'


@pytest.mark.parametrize('raw', ['http://[::1', 'http://host:abc/', 'http://h:99999/x'])
def test_normalize_page_url_tolerates_malformed_links(raw):
    assert website.normalize_page_url(raw, base='https://h/docs/') is None


def test_origin_of_ignores_an_explicit_default_port():
    assert website.same_origin('https://h:443/x', 'https://h/y')
    assert website.same_origin('http://h:80/x', 'http://h/y')
    assert not website.same_origin('https://h:8443/x', 'https://h/y')
    assert not website.same_origin('http://h/x', 'https://h/y')
    assert website.origin_of('http://[::1') == ('', '', None)


@pytest.mark.parametrize('url, scope, expected', [
    (START, 'page', True),
    ('https://docs.example.com/handbuch/kapitel-1.html', 'page', False),
    ('https://docs.example.com/handbuch/kapitel-1.html', 'prefix', True),
    ('https://docs.example.com/handbuch/sub/x.html', 'prefix', True),
    ('https://docs.example.com/andere/x.html', 'prefix', False),
    ('https://docs.example.com/andere/x.html', 'host', True),
    ('https://other.example.com/handbuch/x.html', 'host', False),
    ('https://docs.example.com:8443/handbuch/x.html', 'host', False),
    # No scheme downgrade from an https start page.
    ('http://docs.example.com/andere/x.html', 'host', False),
])
def test_in_crawl_scope(url, scope, expected):
    assert website.in_crawl_scope(url, start_url=START, crawl_scope=scope) is expected


def test_http_start_page_may_follow_https_links():
    assert website.in_crawl_scope('https://h/x.html', start_url='http://h/', crawl_scope='host')


def test_http_documents_of_an_https_site_are_not_fetched(monkeypatch):
    pages = dict(PAGES)
    pages[START] = '<html><title>Index</title><body><main><a href="http://docs.example.com/handbuch/alt.pdf">alt</a> <a href="anleitung.pdf">PDF</a></main></body></html>'
    monkeypatch.setattr(website, 'safe_fetch', FakeFetch(pages))
    client = _client()
    client.fetch_page(START)
    assert [item.filename for item in client.iter_attachments(START)] == ['anleitung.pdf']


def test_scope_prefix_variants():
    assert website.scope_prefix('https://h/a/b/index.html') == 'https://h/a/b/'
    assert website.scope_prefix('https://h/a/b/') == 'https://h/a/b/'
    assert website.scope_prefix('https://h/a/b') == 'https://h/a/b'
    assert website.in_crawl_scope('https://h/a/b/c', start_url='https://h/a/b', crawl_scope='prefix')
    assert not website.in_crawl_scope('https://h/a/bc', start_url='https://h/a/b', crawl_scope='prefix')


def test_extract_main_html_keeps_content_wrapped_in_a_form():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup('<html><body><form id="aspnetForm"><main><h1>Intranet</h1><p>Inhalt</p><input name="x"></main></form></body></html>', 'html.parser')
    title, html = website.extract_main_html(soup)
    assert title == 'Intranet' and 'Inhalt' in html and '<input' not in html and '<form' not in html


def test_page_base_url_and_relative_links_in_markdown():
    assert website.page_base_url('https://h/a/b/x.html') == 'https://h/a/b'
    assert website.page_base_url('https://h/a/b/') == 'https://h/a/b'
    assert website.page_base_url('https://h/') == 'https://h'
    page = Page(id='https://h/vwvfg/__1.html', title='§ 1', version=1, html='<p><a href="__2.html">weiter</a> <img src="img/a.png"></p>', url='https://h/vwvfg/__1.html')
    conversion, _ = website.convert_web_page(page, import_run_id='r')
    assert '(https://h/vwvfg/__2.html)' in conversion.markdown
    assert 'https://h/vwvfg/img/a.png' in conversion.markdown
    assert '__1.html/__2.html' not in conversion.markdown


def test_extract_main_html_prefers_main_and_strips_chrome():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        '<html><head><title> Seite A </title><script>x()</script></head><body>'
        '<header>Logo</header><nav><a href="/x">Nav</a></nav>'
        '<main><h1>Inhalt</h1><p>Text</p><form><input></form></main>'
        '<footer>Impressum</footer></body></html>', 'html.parser',
    )
    title, html = website.extract_main_html(soup)
    assert title == 'Seite A'
    assert '<h1>Inhalt</h1>' in html and 'Text' in html
    assert 'Nav' not in html and 'Logo' not in html and 'Impressum' not in html and '<form' not in html


@pytest.mark.parametrize('text, expected', [
    ('§ 1 ...\nStand: Zuletzt geändert durch Art. 24 G v. 25.6.2021 I 2154\n§ 2', 'Zuletzt geändert durch Art. 24 G v. 25.6.2021 I 2154'),
    ('Rechtsstand: 01.01.2024\n', '01.01.2024'),
    ('Zuletzt geändert durch Art. 1 G v. 3.12.2020 I 2680\n', 'Zuletzt geändert durch Art. 1 G v. 3.12.2020 I 2680'),
    ('Keine Angabe hier. Stand der Technik ist gut.', None),
])
def test_extract_legal_status(text, expected):
    assert website.extract_legal_status(text) == expected


def test_convert_web_page_frontmatter_and_provenance():
    page = Page(id=START, title='Handbuch', version=1234, html='<h1>Handbuch</h1><p>Stand: 12.03.2024</p><p>Hallo</p>', url=START)
    conversion, provenance = website.convert_web_page(page, import_run_id='run-1', legal_source=True)
    assert conversion.markdown.startswith('---\n')
    assert 'title: Handbuch' in conversion.markdown
    assert f'source: {START}' in conversion.markdown
    assert 'site: docs.example.com' in conversion.markdown
    assert 'fetched_at: ' in conversion.markdown
    assert 'legal_status: 12.03.2024' in conversion.markdown
    assert provenance['source_kind'] == 'website'
    assert provenance['source_url'] == START
    assert provenance['legal_status'] == '12.03.2024'
    assert 'confluence_page_id' not in conversion.markdown
    # Not a legal source: nothing is guessed.
    conversion, provenance = website.convert_web_page(page, import_run_id='run-1', legal_source=False)
    assert 'legal_status' not in conversion.markdown and 'legal_status' not in provenance


# --- client over a fake safe_fetch ------------------------------------------------

PAGES = {
    START: (
        '<html><head><title>Index</title></head><body><nav><a href="/andere/x.html">Andere</a></nav><main>'
        '<a href="kapitel-1.html?utm_source=x">K1</a> <a href="/handbuch/kapitel-2.html#abs">K2</a> '
        '<a href="https://other.example.com/x.html">extern</a> <a href="anleitung.pdf">PDF</a> '
        '<a href="https://docs.example.com/downloads/formular.docx">DOCX</a> <a href="bild.png">img</a> '
        '<a href="mailto:a@b.c">mail</a></main></body></html>'
    ),
    'https://docs.example.com/handbuch/kapitel-1.html': '<html><title>K1</title><body><main><p>Eins</p></main></body></html>',
    'https://docs.example.com/robots.txt': 'User-agent: *\nDisallow: /geheim/\n',
    'https://docs.example.com/handbuch/anleitung.pdf': '%PDF-1.4 x',
    'https://docs.example.com/downloads/formular.docx': 'PK\x03\x04 x',
}


class FakeFetch:
    def __init__(self, pages=PAGES):
        self.pages = dict(pages)
        self.calls: list[tuple[str, dict]] = []
        self.private_hosts: list[frozenset] = []

    def __call__(self, url, *, headers=None, allowed_private_hosts=None, max_bytes=0, **kwargs):
        self.calls.append((url, dict(headers or {})))
        self.private_hosts.append(frozenset(allowed_private_hosts or ()))
        host = url.split('/')[2]
        if host.startswith('10.') and host not in (allowed_private_hosts or ()):
            raise SafeFetchError(f'{url!r} resolves to a private/blocked address')
        if url not in self.pages:
            return SafeFetchResponse(status_code=404, headers={'content-type': 'text/html'}, body=b'nope', final_url=url)
        body = self.pages[url]
        content_type = 'text/plain' if url.endswith('robots.txt') else 'application/pdf' if url.endswith('.pdf') else 'text/html; charset=utf-8'
        return SafeFetchResponse(status_code=200, headers={'content-type': content_type}, body=body.encode('utf-8'), final_url=url)


@pytest.fixture()
def fetch(monkeypatch):
    fake = FakeFetch()
    monkeypatch.setattr(website, 'safe_fetch', fake)
    return fake


def _client(**overrides) -> website.WebsiteClient:
    params = dict(
        base_url='https://docs.example.com', start_url=START, crawl_scope='prefix',
        auth_type=ImportAuthType.BEARER, credential='tok-123', include_linked_documents=True,
    )
    params.update(overrides)
    return website.create_website_client(**params)


def test_fetch_page_children_and_documents_follow_scope(fetch):
    client = _client()
    page = client.fetch_page(START)
    assert page.id == START and page.title == 'Index'
    assert 'K1' in page.html and 'Andere' not in page.html  # nav stripped from content
    assert isinstance(page.version, int) and 0 < page.version < 2**31
    children = list(client.iter_children(START))
    # prefix scope: both chapters (tracking param + fragment dropped), never
    # the other section, the other host, the mailto or the image.
    assert children == ['https://docs.example.com/handbuch/kapitel-1.html', 'https://docs.example.com/handbuch/kapitel-2.html']
    attachments = list(client.iter_attachments(START))
    assert [item.filename for item in attachments] == ['anleitung.pdf', 'formular.docx']
    assert attachments[0].fetch_bytes().startswith(b'%PDF')
    # Child fetch after the cached parent: no re-fetch of the parent.
    assert [call[0] for call in fetch.calls][:2] == ['https://docs.example.com/robots.txt', START]


def test_page_scope_follows_nothing(fetch):
    client = _client(crawl_scope='page')
    client.fetch_page(START)
    assert list(client.iter_children(START)) == []


def test_linked_documents_off_yields_no_attachments(fetch):
    client = _client(include_linked_documents=False)
    client.fetch_page(START)
    assert list(client.iter_attachments(START)) == []


def test_credential_sent_to_own_origin_only(fetch):
    client = _client(auth_type=ImportAuthType.HTTP_BASIC, auth_username='bob', credential='pw')
    client.fetch_page(START)
    _, headers = fetch.calls[-1]
    assert headers['Authorization'].startswith('Basic ')
    for other in ('https://other.example.com/x.html', 'http://docs.example.com/x.html', 'https://docs.example.com:8443/x.html'):
        with pytest.raises(ConfluenceError):
            client.fetch_page(other)
        _, headers = fetch.calls[-1]
        assert 'Authorization' not in headers


def test_redirect_out_of_scope_is_not_imported(monkeypatch):
    class RedirectingFetch(FakeFetch):
        def __call__(self, url, **kwargs):
            if url == 'https://docs.example.com/handbuch/alt.html':
                return SafeFetchResponse(status_code=200, headers={'content-type': 'text/html'}, body=b'<html><title>AGB</title><body><main>x</main></body></html>', final_url='https://docs.example.com/recht/agb.html')
            return super().__call__(url, **kwargs)

    monkeypatch.setattr(website, 'safe_fetch', RedirectingFetch())
    client = _client(crawl_scope='prefix')
    with pytest.raises(ConfluenceError) as info:
        client.fetch_page('https://docs.example.com/handbuch/alt.html')
    assert 'outside the import scope' in str(info.value)
    # The start page defines the scope: its own redirect is always followed.
    host_client = website.create_website_client(base_url='https://docs.example.com', start_url='https://docs.example.com/handbuch/alt.html', crawl_scope='page', respect_robots=False)
    monkeypatch.setattr(website, 'safe_fetch', RedirectingFetch())
    assert host_client.fetch_page('https://docs.example.com/handbuch/alt.html').url == 'https://docs.example.com/recht/agb.html'


def test_links_resolve_against_the_redirect_target(monkeypatch):
    class RedirectingFetch(FakeFetch):
        def __call__(self, url, **kwargs):
            response = super().__call__(url, **kwargs)
            if url == 'https://docs.example.com/handbuch':
                return SafeFetchResponse(status_code=200, headers={'content-type': 'text/html'}, body=b'<html><title>H</title><body><main><a href="kapitel-1.html">K1</a></main></body></html>', final_url=START)
            return response

    monkeypatch.setattr(website, 'safe_fetch', RedirectingFetch())
    client = website.create_website_client(base_url='https://docs.example.com', start_url='https://docs.example.com/handbuch', crawl_scope='host', respect_robots=False)
    page = client.fetch_page('https://docs.example.com/handbuch')
    assert page.url == START
    assert list(client.iter_children('https://docs.example.com/handbuch')) == ['https://docs.example.com/handbuch/kapitel-1.html']


def test_private_hosts_for_binds_release_to_the_connection_host():
    assert website.private_hosts_for(kind='website', base_url='http://intranet.local:8080', allow_private_target=True, global_allowlist=['a']) == frozenset({'a', 'intranet.local:8080'})
    assert website.private_hosts_for(kind='website', base_url='http://intranet.local', allow_private_target=False, global_allowlist=[]) == frozenset()
    assert website.private_hosts_for(kind='confluence', base_url='http://intranet.local', allow_private_target=True, global_allowlist=[]) == frozenset()


def test_errors_carry_url_and_status_but_never_the_credential(fetch):
    client = _client()
    with pytest.raises(ConfluenceError) as info:
        client.fetch_page('https://docs.example.com/handbuch/fehlt.html')
    assert info.value.status_code == 404
    assert 'tok-123' not in str(info.value)
    assert client.fetch_page(START) is not None


def test_robots_disallow_blocks_page(fetch):
    client = _client(crawl_scope='host')
    with pytest.raises(ConfluenceError) as info:
        client.fetch_page('https://docs.example.com/geheim/x.html')
    assert 'robots.txt' in str(info.value)


def test_private_target_blocked_unless_released(monkeypatch):
    fake = FakeFetch({'http://10.0.0.5/intranet/': '<html><title>Intra</title><body><main>ok</main></body></html>'})
    monkeypatch.setattr(website, 'safe_fetch', fake)
    blocked = website.create_website_client(base_url='http://10.0.0.5', start_url='http://10.0.0.5/intranet/', respect_robots=False)
    with pytest.raises(ConfluenceError) as info:
        blocked.fetch_page('http://10.0.0.5/intranet/')
    assert 'private' in str(info.value)
    released = website.create_website_client(
        base_url='http://10.0.0.5', start_url='http://10.0.0.5/intranet/', respect_robots=False,
        allowed_private_hosts=frozenset({'10.0.0.5'}),
    )
    assert released.fetch_page('http://10.0.0.5/intranet/').title == 'Intra'
    assert fake.private_hosts[-1] == frozenset({'10.0.0.5'})


def test_probe_reports_reachability_without_credential(fetch):
    ok, detail = website.probe(
        'https://docs.example.com/handbuch/index.html', auth_type='bearer', auth_username='', credential='tok-123',
        allowed_private_hosts=frozenset(), timeout=5, max_bytes=1024,
    )
    assert ok and 'HTTP 200' in detail
    ok, detail = website.probe(
        'https://docs.example.com/nope', auth_type='none', auth_username='', credential='',
        allowed_private_hosts=frozenset(), timeout=5, max_bytes=1024,
    )
    assert not ok and 'HTTP 404' in detail and 'tok-123' not in detail
    ok, detail = website.probe(
        'http://10.1.1.1/', auth_type='none', auth_username='', credential='',
        allowed_private_hosts=frozenset(), timeout=5, max_bytes=1024,
    )
    assert not ok and 'administrator' in detail


def test_unsupported_auth_type_is_refused():
    with pytest.raises(ConfluenceError):
        website.auth_headers(ImportAuthType.PAT_BEARER, '', 'x')
    assert website.auth_headers(ImportAuthType.NONE, '', '') == {}
