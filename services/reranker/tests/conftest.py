"""Shared fixtures for the reranker service's test suite.

`client` gives every fast contract test (test_rerank_api.py) a FastAPI
TestClient with a valid RERANKER_API_TOKEN configured and the model marked
warm WITHOUT loading the real ~2.3 GB BAAI/bge-reranker-v2-m3 weights --
each test still monkeypatches `reranker_model.score` itself to control
exactly what scores come back. test_rerank_content.py deliberately does
NOT use this fixture: it needs the REAL model to make its point.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.model import reranker_model

AUTH_HEADERS = {'Authorization': 'Bearer test-token'}


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, 'reranker_api_token', 'test-token')
    was_warm = reranker_model.warm
    reranker_model._ready.set()
    try:
        yield TestClient(app)
    finally:
        if not was_warm:
            reranker_model._ready.clear()
