"""Tests for app/services/ingest_client.py: build_markdown_url()'s
job_id -> trusted-URL construction, fetch_markdown()'s Bearer auth +
follow_redirects=False, the 401/403/404 -> permanent / 5xx+transport ->
transient classification, and _validate_markdown_url()'s hardened
defense-in-depth checks (confusable-prefix host, dot-segments, userinfo,
query/fragment).

FINDING 3 (SSRF/path-confusion): fetch_markdown() used to accept a
document.processed event's `markdown_url` field directly and only checked
it started with settings.weave_ingest_base_url -- a same-origin path with
'..' segments passed that check while still resolving to an arbitrary path
under the origin. fetch_markdown() now takes `job_id` only; the actual URL
is always built locally via build_markdown_url(), never from any
externally-supplied value. _validate_markdown_url() is kept and tested
directly (it's no longer reachable with attacker-controlled input through
fetch_markdown() itself) as defense-in-depth for any future direct-URL
caller -- see app/workers/tasks.py's test_index_task.py for an end-to-end
test that a Document's stored (informative-only) markdown_url is never the
thing actually fetched.
"""

from unittest.mock import patch

import httpx
import pytest

from app.core.config import settings
from app.services import ingest_client
from app.services.ingest_client import (
    PermanentFetchError,
    TransientFetchError,
    build_markdown_url,
    fetch_markdown,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = '') -> None:
        self.status_code = status_code
        self.text = text


# --- build_markdown_url ---------------------------------------------------------


def test_build_markdown_url_from_base_and_job_id(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    assert build_markdown_url('abc-123') == 'https://weave.local/api/v1/jobs/abc-123/download'


def test_build_markdown_url_tolerates_trailing_slash_in_base(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local/')
    assert build_markdown_url('abc-123') == 'https://weave.local/api/v1/jobs/abc-123/download'


# --- fetch_markdown takes job_id only, never a URL -------------------------------


def test_fetch_markdown_rejects_a_job_id_that_would_embed_a_traversal_segment(monkeypatch):
    """fetch_markdown()'s whole signature is job_id-only precisely so a
    spoofed/tampered event's markdown_url can never reach it (see FINDING 3
    and the module docstring). Even if some future caller passed something
    other than a clean, UUID-validated job_id, _validate_markdown_url still
    catches a '..' segment before any request is made."""
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get') as mock_get:
        with pytest.raises(ValueError):
            fetch_markdown('../../../etc/passwd')
    mock_get.assert_not_called()


def test_successful_fetch_sends_bearer_token_and_disables_redirects(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    monkeypatch.setattr(settings, 'weave_ingest_api_token', 'pd_service_token')

    with patch(
        'app.services.ingest_client.httpx.get',
        return_value=_FakeResponse(200, '---\nfoo: bar\n---\n\nBody'),
    ) as mock_get:
        result = fetch_markdown('abc', timeout=5.0)

    assert result == '---\nfoo: bar\n---\n\nBody'
    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert args[0] == 'https://weave.local/api/v1/jobs/abc/download'
    assert kwargs['headers']['Authorization'] == 'Bearer pd_service_token'
    assert kwargs['follow_redirects'] is False
    assert kwargs['timeout'] == 5.0


# --- Permanent classification -----------------------------------------------------


@pytest.mark.parametrize('status_code', [401, 403, 404])
def test_auth_and_not_found_statuses_are_permanent(monkeypatch, status_code):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get', return_value=_FakeResponse(status_code)):
        with pytest.raises(PermanentFetchError):
            fetch_markdown('job-x')


def test_unexpected_4xx_is_permanent(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get', return_value=_FakeResponse(400)):
        with pytest.raises(PermanentFetchError):
            fetch_markdown('job-x')


def test_redirect_status_is_permanent_not_followed(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get', return_value=_FakeResponse(302)):
        with pytest.raises(PermanentFetchError):
            fetch_markdown('job-x')


# --- Transient classification -----------------------------------------------------


@pytest.mark.parametrize('status_code', [500, 502, 503])
def test_5xx_statuses_are_transient(monkeypatch, status_code):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get', return_value=_FakeResponse(status_code)):
        with pytest.raises(TransientFetchError):
            fetch_markdown('job-x')


def test_timeout_is_transient(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get', side_effect=httpx.ReadTimeout('timed out')):
        with pytest.raises(TransientFetchError):
            fetch_markdown('job-x')


def test_connection_error_is_transient(monkeypatch):
    monkeypatch.setattr(settings, 'weave_ingest_base_url', 'https://weave.local')
    with patch('app.services.ingest_client.httpx.get', side_effect=httpx.ConnectError('refused')):
        with pytest.raises(TransientFetchError):
            fetch_markdown('job-x')


# --- _validate_markdown_url: hardened defense-in-depth checks --------------------


def test_validate_accepts_clean_url_under_base():
    ingest_client._validate_markdown_url('https://weave.local/api/v1/jobs/x/download', 'https://weave.local')


def test_validate_rejects_foreign_host():
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url('https://evil.example.com/steal', 'https://weave.local')


def test_validate_rejects_confusable_prefix_host():
    """'https://weave.local.evil.com' must not pass a naive
    .startswith('https://weave.local') check."""
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url(
            'https://weave.local.evil.com/api/v1/jobs/x/download', 'https://weave.local'
        )


def test_validate_rejects_dot_segment_path():
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url(
            'https://weave.local/api/v1/jobs/x/download/../../../admin', 'https://weave.local'
        )


def test_validate_rejects_percent_encoded_dot_segment():
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url(
            'https://weave.local/api/v1/jobs/x/download/%2e%2e/%2e%2e/admin', 'https://weave.local'
        )


def test_validate_rejects_userinfo():
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url(
            'https://attacker@weave.local/api/v1/jobs/x/download', 'https://weave.local'
        )


def test_validate_rejects_query_string():
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url(
            'https://weave.local/api/v1/jobs/x/download?redirect=evil.example.com', 'https://weave.local'
        )


def test_validate_rejects_fragment():
    with pytest.raises(ValueError):
        ingest_client._validate_markdown_url(
            'https://weave.local/api/v1/jobs/x/download#fragment', 'https://weave.local'
        )
