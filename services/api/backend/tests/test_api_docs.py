from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import api_docs_urls
from tests.conftest import client

DOCS_PATHS = ('/docs', '/redoc', '/openapi.json')


def test_api_docs_are_served_by_default():
    for path in DOCS_PATHS:
        assert client.get(path).status_code == 200, path


def test_api_docs_disabled_answer_404():
    disabled = TestClient(FastAPI(**api_docs_urls(False)))
    for path in DOCS_PATHS:
        assert disabled.get(path).status_code == 404, path
