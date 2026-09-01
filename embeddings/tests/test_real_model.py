"""End-to-end test against the REAL intfloat/multilingual-e5-small ONNX
model -- opt-in only (set RUN_REAL_MODEL_TESTS=1), since it needs the
actual ~470MB model file, either already cached (point EMBEDDINGS_CACHE_DIR
at an existing HuggingFace cache directory) or downloaded fresh over the
network.

Not part of the default suite, for the same reason tests/conftest.py's
FakeEmbedder exists: keeping the default suite fast, hermetic, and
independent of network access (see that module's docstring). This file is
the one place that proves the hand-rolled onnxruntime + tokenizers pipeline
in app/services/encoder.py actually produces correctly-shaped,
prefix-sensitive, padding-invariant output against the real weights --
every other test in this suite only ever exercises the fake.

Run it with, e.g.:

    RUN_REAL_MODEL_TESTS=1 EMBEDDINGS_CACHE_DIR=.cache \\
        .venv/bin/python -m pytest tests/test_real_model.py -v
"""

import math
import os

import pytest

from app.core.config import Settings
from app.services.encoder import build_embedder

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_REAL_MODEL_TESTS') != '1',
    reason='set RUN_REAL_MODEL_TESTS=1 to run this (loads the real ~470MB ONNX model)',
)


@pytest.fixture(scope='module')
def real_embedder():
    settings = Settings(embeddings_cache_dir=os.environ.get('EMBEDDINGS_CACHE_DIR', ''))
    return build_embedder(settings)


def test_dimension_is_384(real_embedder):
    assert real_embedder.dimension == 384


def test_query_and_passage_prefixes_produce_different_vectors_for_same_text(real_embedder):
    text = 'Wie stelle ich den Drucker im zweiten Stock ein?'
    query_vec = real_embedder.encode([text], input_type='query').vectors[0]
    passage_vec = real_embedder.encode([text], input_type='passage').vectors[0]

    assert query_vec != passage_vec
    cosine = sum(a * b for a, b in zip(query_vec, passage_vec))
    # Same underlying text -> related but NOT bit-identical embeddings:
    # comfortably below 1.0, but nowhere near the ~0 an unrelated pair of
    # sentences would produce -- proves the prefix is doing real semantic
    # work, not just perturbing the vector randomly.
    assert 0.5 < cosine < 0.999


def test_vectors_are_l2_normalized(real_embedder):
    vec = real_embedder.encode(['ein kurzer Satz'], input_type='passage').vectors[0]
    norm = math.sqrt(sum(component * component for component in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5)


def test_embeddings_normalize_false_yields_non_unit_vectors():
    """EMBEDDINGS_NORMALIZE=false must actually skip the L2-normalization
    step (app/services/encoder.py:Embedder._encode_batch), not just be
    accepted and ignored -- mean-pooled BERT hidden states are not unit
    norm on their own, so a real, un-normalized vector's norm should land
    well away from 1.0."""
    settings = Settings(embeddings_cache_dir=os.environ.get('EMBEDDINGS_CACHE_DIR', ''), embeddings_normalize=False)
    embedder = build_embedder(settings)

    vec = embedder.encode(['ein kurzer Satz'], input_type='passage').vectors[0]
    norm = math.sqrt(sum(component * component for component in vec))
    assert not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3)


def test_padding_does_not_change_the_result(real_embedder):
    """Encoding one short text alone must produce the SAME vector as
    encoding it inside a batch alongside a much longer text -- proves mean
    pooling correctly excludes padded positions via the attention mask
    (app/services/encoder.py:Embedder._encode_batch), regardless of which
    token id is used for padding."""
    short = 'kurzer Text'
    long_text = (
        'Ein deutlich laengerer Text mit vielen zusaetzlichen Woertern, der '
        'das Padding im selben Batch spuerbar in die Laenge zieht.'
    )

    alone = real_embedder.encode([short], input_type='passage').vectors[0]
    batched = real_embedder.encode([short, long_text], input_type='passage').vectors[0]

    assert alone == pytest.approx(batched, abs=1e-6)


def test_batch_output_order_matches_input_order(real_embedder):
    texts = [f'Dokument Nummer {i} mit eigenem Inhalt' for i in range(5)]
    individually = [real_embedder.encode([text], input_type='passage').vectors[0] for text in texts]
    batched = real_embedder.encode(texts, input_type='passage').vectors

    for individual_vec, batched_vec in zip(individually, batched):
        assert individual_vec == pytest.approx(batched_vec, abs=1e-6)
