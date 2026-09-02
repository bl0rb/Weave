"""Weave-Ingest markdown fetchers used by the index pipeline
(app/workers/tasks.py's index_document).

Released indexing downloads the immutable snapshot from
`GET /api/v1/portal/releases/{release_id}/download`, using a locally built
target and verifying its UTF-8 SHA-256 before chunking. The existing
job-based fetcher remains available for compatibility with other callers,
but released indexing never uses an event-supplied URL.

Kept DB-free and FastAPI-free by design, same shape as
app/services/embeddings.py's OpenAICompatibleProvider: a plain function
callers (index_document) depend on, easy to monkeypatch/mock in tests
without any ORM/session involved.

FINDING 3 fix (SSRF/path-confusion): earlier versions of fetch_markdown()
took the event's `markdown_url` field directly and only checked it started
with settings.weave_ingest_base_url. A same-origin path with '..' segments
('/api/v1/jobs/x/download/../../../admin') passes a naive prefix check
while still resolving to an arbitrary path under that origin -- exfiltrated
through this service's own Bearer service token. fetch_markdown() now takes
`job_id`, never a URL: the trusted download URL is built ENTIRELY from
this service's own settings.weave_ingest_base_url plus `job_id` (see
build_markdown_url() below), which by the time it reaches here has already
been UUID-pattern-validated by app/schemas/events.py's
DocumentProcessedEvent.job_id. The event's own `markdown_url` field is
still stored on the Document row for audit/display (see
app/api/events.py), but no longer used to build any outbound request.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_PATH_TEMPLATE = '/api/v1/jobs/{job_id}/download'
_RELEASE_DOWNLOAD_PATH_TEMPLATE = '/api/v1/portal/releases/{release_id}/download'
_UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_SHA256_RE = re.compile(r'^[a-f0-9]{64}$')


class FetchError(Exception):
    """Base class for a fetch_markdown() failure."""


class PermanentFetchError(FetchError):
    """Not retryable: an SSRF-guard rejection would already have raised a
    plain ValueError before any request was made (see fetch_markdown's
    docstring) -- this covers failures a real HTTP round-trip did produce,
    but where retrying an unchanged request against an unchanged endpoint
    would only get the same answer back: bad/expired token (401), no
    visibility (403), the job or its result no longer exists (404), or any
    other non-5xx status this module doesn't otherwise recognize.
    app/workers/tasks.py's index_document marks the document status='failed'
    immediately on this, no retry.
    """


class TransientFetchError(FetchError):
    """Retryable: a 5xx response, a request timeout, or any other
    transport-level failure (connection refused, DNS failure, ...) --
    Weave-Ingest itself may just be restarting or briefly overloaded.
    app/workers/tasks.py's index_document retries this with backoff, up to
    its own attempt limit.
    """


def _authorized_base_url() -> str:
    return settings.weave_ingest_base_url.rstrip('/')


def build_markdown_url(job_id: str) -> str:
    """Build the trusted `GET /api/v1/jobs/{job_id}/download` URL for
    `job_id` from THIS service's own settings.weave_ingest_base_url --
    never from a document.processed event's `markdown_url` field (see the
    module docstring's FINDING 3 note). `job_id` is expected to already be
    UUID-shaped by the time it gets here (app/schemas/events.py's
    DocumentProcessedEvent.job_id pattern-validates this at webhook-receipt
    time, and app/workers/tasks.py passes document.source_job_id, copied
    verbatim from that validated field), so the result can never contain a
    path-traversal or query/fragment component -- _validate_markdown_url
    below still checks it regardless, as defense-in-depth.
    """
    return f'{_authorized_base_url()}{_DOWNLOAD_PATH_TEMPLATE.format(job_id=job_id)}'


def build_released_markdown_url(release_id: str) -> str:
    """Build the only URL used for an approved release snapshot."""
    try:
        parsed_release_id = uuid.UUID(str(release_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError('release_id must be a UUID') from exc
    if not _UUID_RE.fullmatch(str(release_id)):
        raise ValueError('release_id must use canonical UUID syntax')
    return f'{_authorized_base_url()}{_RELEASE_DOWNLOAD_PATH_TEMPLATE.format(release_id=parsed_release_id)}'


def _is_within_base_url(markdown_url: str, base_url: str) -> bool:
    """True iff `markdown_url` is `base_url` itself or a path under it.

    A plain `.startswith(base_url)` would also accept
    'https://weave.local.evil.com/...' when base_url is
    'https://weave.local' -- requiring either an exact match or a '/'
    right after the prefix closes that confusable-prefix gap while still
    doing exactly what the SSRF guard asks for: reject anything that isn't
    actually Weave-Ingest's own configured origin.
    """
    return markdown_url == base_url or markdown_url.startswith(base_url + '/')


def _validate_markdown_url(markdown_url: str, base_url: str) -> None:
    """Hardened SSRF guard.

    fetch_markdown() itself no longer accepts an externally-supplied URL at
    all (see build_markdown_url() above) -- this check is kept as
    defense-in-depth for any future code path that constructs or forwards a
    markdown_url directly, so a re-introduction of the original bug doesn't
    silently reopen it. Beyond the original same-origin prefix check
    (_is_within_base_url), this also rejects:

    - a userinfo component in the authority ('user@host') -- not exploitable
      against the current exact-prefix check, but an explicit reject keeps
      it that way even if the prefix check is ever loosened
    - a query string or fragment -- the trusted download route never needs
      either, so their mere presence is already suspicious
    - '..' path-traversal segments, both literal and percent-encoded
      ('%2e%2e', any case) -- checked on the raw URL rather than only the
      parsed-and-normalized path, since normalizing first would silently
      collapse a traversal attempt into something that then passes the
      prefix check
    """
    parsed = urlsplit(markdown_url)
    if '@' in parsed.netloc:
        raise ValueError(f'markdown_url {markdown_url!r} contains a userinfo component')
    if parsed.query or parsed.fragment:
        raise ValueError(f'markdown_url {markdown_url!r} contains a query string or fragment')
    if '..' in markdown_url or '%2e' in markdown_url.lower():
        raise ValueError(f'markdown_url {markdown_url!r} contains a path traversal segment')
    if not _is_within_base_url(markdown_url, base_url):
        raise ValueError(
            f'markdown_url {markdown_url!r} does not start with the configured '
            f'WEAVE_INGEST_BASE_URL {settings.weave_ingest_base_url!r}'
        )


def fetch_markdown(job_id: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> str:
    """Download the markdown for `job_id`'s Weave-Ingest download route.

    Takes `job_id`, NOT a URL -- see the module docstring's FINDING 3 note
    for why. The trusted URL is built via build_markdown_url() from this
    service's own configured settings.weave_ingest_base_url, then checked
    again by _validate_markdown_url() as defense-in-depth. Raises
    ValueError with NO network call made if that check ever fails (should
    not happen in practice given a UUID-shaped job_id, but callers -- see
    app/workers/tasks.py's index_document -- treat this exactly like
    PermanentFetchError either way: mark the document failed, never retry.

    follow_redirects=False on purpose: a redirect off Weave-Ingest's own
    download route is unexpected and untrusted, so it is surfaced as an
    (unrecognized-status) PermanentFetchError rather than blindly followed.
    """
    base_url = _authorized_base_url()
    markdown_url = build_markdown_url(job_id)
    _validate_markdown_url(markdown_url, base_url)

    return _fetch_url(markdown_url, timeout=timeout)


def fetch_released_markdown(
    release_id: str, expected_sha256: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> str:
    """Download and verify an immutable approved release snapshot.

    The event's markdown_url is intentionally not an input. The target is
    constructed from trusted local settings and a UUID validated here, and
    the UTF-8 bytes are checked against the signed top-level hash before the
    caller can chunk the document.
    """
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise ValueError('expected_sha256 must be lowercase hexadecimal SHA-256')
    markdown_url = build_released_markdown_url(release_id)
    _validate_markdown_url(markdown_url, _authorized_base_url())
    markdown = _fetch_url(markdown_url, timeout=timeout)
    actual_sha256 = hashlib.sha256(markdown.encode('utf-8')).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PermanentFetchError(
            f'fetching {markdown_url!r} returned a body with SHA-256 {actual_sha256}, '
            f'expected {expected_sha256}'
        )
    return markdown


def _fetch_url(url: str, *, timeout: float) -> str:
    headers = {'Authorization': f'Bearer {settings.weave_ingest_api_token}'}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError as exc:
        # Covers httpx.TimeoutException (a subclass of HTTPError) as well as
        # every other transport-level failure (connection refused, DNS
        # failure, ...) -- none of these produced a real HTTP response, so
        # none of them can be classified by status code below.
        raise TransientFetchError(f'fetching {url!r} failed: {exc}') from exc

    if response.status_code == 200:
        raw_content = getattr(response, 'content', None)
        if isinstance(raw_content, bytes):
            try:
                return raw_content.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise PermanentFetchError(f'fetching {url!r} returned non-UTF-8 content') from exc
        return response.text
    if response.status_code in (401, 403, 404):
        raise PermanentFetchError(f'fetching {url!r} returned HTTP {response.status_code}')
    if response.status_code >= 500:
        raise TransientFetchError(f'fetching {url!r} returned HTTP {response.status_code}')
    # Any other status (a stray redirect since follow_redirects=False, or an
    # unexpected 4xx) -- not retryable, same "any other 4xx" reasoning as
    # OpenAICompatibleProvider in app/services/embeddings.py.
    raise PermanentFetchError(f'fetching {url!r} returned unexpected HTTP {response.status_code}')
