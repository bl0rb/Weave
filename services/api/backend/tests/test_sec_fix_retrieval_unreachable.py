"""Security-review regression for app/services/retrieval_client.py: a
transport error (internal host name, port, errno) never ends up in the
RetrievalClientError message, which GET /v1/collections, POST /v1/search and
the portal artifact proxy return verbatim as their 502 `detail` -- the same
rule app/services/runtime_client._unreachable already applies."""

import httpx
import pytest

from app.services import retrieval_client
from app.services.retrieval_client import RetrievalClientError

_RAW = '[Errno 111] Connection refused to retrieval.internal:8003'


class _Unreachable:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def get(self, *args, **kwargs):
        raise httpx.ConnectError(_RAW)

    def post(self, *args, **kwargs):
        raise httpx.ConnectError(_RAW)


@pytest.mark.parametrize(
    'call',
    [
        lambda: retrieval_client.list_collections(team='legal'),
        lambda: retrieval_client.release_collection('rel-1'),
        lambda: retrieval_client.search(query='q', allowed_collections=['c'], allowed_teams=[], top_k=None),
    ],
)
def test_transport_error_text_is_logged_not_returned(monkeypatch, caplog, call):
    monkeypatch.setattr(retrieval_client, '_client', lambda: _Unreachable())
    with caplog.at_level('WARNING', logger='app.services.retrieval_client'):
        with pytest.raises(RetrievalClientError) as info:
            call()
    assert str(info.value) == 'Weave-Retrieval unreachable'
    assert 'Errno' in caplog.text
