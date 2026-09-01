"""Shared TestClient wiring for the backend test suite.

Deliberately does NOT let the app's real `lifespan` run for most tests:
`with TestClient(app) as client:` would start app/main.py's real background
loader thread, which downloads/loads the actual ~470MB ONNX model over the
network -- exactly the "slow, network-dependent, non-hermetic" cost
Weave-Knowledge's own FakeEmbeddingProvider (backend/app/services/
embeddings.py) is built to avoid for the identical reason. Every test in
this file that isn't specifically about the startup sequence itself
(test_startup.py is) uses the module-level `client` below (a bare
TestClient(app), lifespan never triggered) and manually drives
`encoder_state` via `install_fake_embedder()`.

FakeEmbedder mirrors Weave-Knowledge's FakeEmbeddingProvider almost exactly
(sha256-seeded PRNG -> deterministic unit vector), with one addition: the
resolved input_type ("query"/"passage") is hashed in ALONGSIDE the text, so
the same text produces two different (but each individually
reproducible) vectors depending on input_type. That is the one property
these tests actually need a fake for: it proves the HTTP layer
(app/main.py's create_embeddings) really passes `input_type` through to the
encoder instead of dropping it on the floor, without touching the real
model or the network. Whether the REAL model's own "query: "/"passage: "
prefixes produce different vectors for the same underlying text is a
separate, already-answered question -- see this repo's README/this
service's task notes for the empirical result -- and is re-verified
end-to-end (against the real ONNX model, opt-in, not part of the default
suite) in test_real_model.py.
"""

import hashlib
import math
import random

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings

TEST_TOKEN = 'test-embeddings-token'
settings.embeddings_api_token = TEST_TOKEN

from app.main import app  # noqa: E402
from app.services.encoder import EncodeResult  # noqa: E402
from app.services.state import encoder_state  # noqa: E402

AUTH_HEADERS = {'Authorization': f'Bearer {TEST_TOKEN}'}

FAKE_MODEL_NAME = settings.embeddings_model  # must match, or create_embeddings' 404 model-mismatch check fires
FAKE_DIMENSION = 384  # matches the real intfloat/multilingual-e5-small, so dimension-consistency tests are meaningful


class FakeEmbedder:
    """Deterministic, dependency-free stand-in for the real
    app.services.encoder.Embedder -- see this module's own docstring."""

    def __init__(self, *, dimension: int = FAKE_DIMENSION, threads: int = 1) -> None:
        self.model_name = FAKE_MODEL_NAME
        self.dimension = dimension
        self.threads = threads
        self.calls: list[tuple[list[str], str]] = []  # recorded for tests that assert on what reached the encoder

    def encode(self, texts: list[str], *, input_type: str) -> EncodeResult:
        self.calls.append((list(texts), input_type))
        vectors = [self._vector_for(text, input_type) for text in texts]
        token_counts = [max(1, len(text.split())) for text in texts]
        return EncodeResult(vectors=vectors, token_counts=token_counts)

    def _vector_for(self, text: str, input_type: str) -> list[float]:
        digest = hashlib.sha256(f'{input_type}:{text}'.encode('utf-8')).digest()
        seed = int.from_bytes(digest[:8], 'big')
        rng = random.Random(seed)
        vector = [rng.uniform(-1.0, 1.0) for _ in range(self.dimension)]
        norm = math.sqrt(sum(component * component for component in vector)) or 1.0
        return [component / norm for component in vector]


def install_fake_embedder(**kwargs) -> FakeEmbedder:
    """Puts a FakeEmbedder into encoder_state as if the real background
    loader had just finished successfully. Returns the fake so a test can
    inspect `.calls` afterwards."""
    fake = FakeEmbedder(**kwargs)
    encoder_state.embedder = fake
    encoder_state.warm = True
    encoder_state.load_error = None
    return fake


def reset_encoder_state() -> None:
    encoder_state.embedder = None
    encoder_state.warm = False
    encoder_state.load_error = None


client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_encoder_state_between_tests():
    """encoder_state is process-wide, and every test module in this suite
    imports the SAME singleton (see this module's docstring) -- without
    this, a test that calls install_fake_embedder() would leave the
    encoder warm for whichever test happens to run next, regardless of
    file. Runs after every test, not before: a test's own explicit setup
    (install_fake_embedder(), or deliberately leaving state cold) stays in
    effect for its own duration either way.
    """
    yield
    reset_encoder_state()
