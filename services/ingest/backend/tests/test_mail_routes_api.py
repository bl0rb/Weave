"""The dedicated mail API is retired.

Mail files remain usable through the normal document upload routes; the
former ``/api/v1/mail`` surface must not remain reachable as a second ingest
path.
"""

import pytest

from conftest import client


@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('get', '/api/v1/mail/messages'),
        ('post', '/api/v1/mail/messages'),
        ('get', '/api/v1/mail/messages/message-id'),
        ('get', '/api/v1/mail/messages/message-id/body'),
        ('get', '/api/v1/mail/messages/message-id/raw'),
        ('get', '/api/v1/mail/messages/message-id/parts/0/content'),
        ('get', '/api/v1/mail/messages/message-id/export.json'),
        ('delete', '/api/v1/mail/messages/message-id'),
    ],
)
def test_retired_mail_api_paths_are_unavailable(method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 404
