"""POST /api/v1/auth/handoff/exchange must answer a non-ASCII shared-secret
header with the same 401 as any other wrong secret.

`hmac.compare_digest` on two `str` raises TypeError as soon as either holds
a non-ASCII character, so comparing the raw header text turned a garbage
header into a 500. The endpoint now compares bytes, like its sibling
handoff endpoints already did."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.security import rate_limiter
from conftest import BROWSER_HEADERS

HANDOFF_SECRET = 'shared-handoff-secret'


@pytest.fixture(autouse=True)
def _handoff_enabled(monkeypatch):
    rate_limiter.reset()
    monkeypatch.setattr(settings, 'handoff_callback_url', 'http://weave-api.test/v1/auth/ingest/callback')
    monkeypatch.setattr(settings, 'handoff_secret', HANDOFF_SECRET)
    yield


@pytest.mark.parametrize('header_value', ['geheim-ä'.encode('utf-8'), b'\xff\xfe', 'säcret'.encode('latin-1')])
def test_non_ascii_handoff_secret_is_a_401_not_a_500(header_value: bytes) -> None:
    client = TestClient(app, headers=BROWSER_HEADERS)

    resp = client.post(
        '/api/v1/auth/handoff/exchange',
        json={'code': 'whatever'},
        headers={'X-Weave-Handoff-Secret': header_value},
    )

    assert resp.status_code == 401
    assert resp.json()['detail'] == 'Invalid handoff credentials'


def test_correct_ascii_secret_still_passes_the_secret_check() -> None:
    client = TestClient(app, headers=BROWSER_HEADERS)

    resp = client.post(
        '/api/v1/auth/handoff/exchange',
        json={'code': 'no-such-code'},
        headers={'X-Weave-Handoff-Secret': HANDOFF_SECRET},
    )

    # Past the secret check, rejected on the (unknown) code instead.
    assert resp.status_code == 401
    assert resp.json()['detail'] == 'Invalid or expired handoff code'
