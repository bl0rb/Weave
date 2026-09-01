"""The one file in this suite that loads the REAL BAAI/bge-reranker-v2-m3
model (via RerankerModel.load(), pointed at RERANKER_CACHE_DIR) and checks
actual semantic behaviour -- every test in test_rerank_api.py monkeypatches
reranker_model.score() instead, to stay fast and network-free.

This is deliberately slow the first time a given cache dir is used (a cold
download of ~2.3 GB of weights) and still takes several seconds once
cached (module-scoped fixture below loads it exactly once for this whole
file). Skippable via RERANKER_SKIP_MODEL_TESTS for a CI lane that
intentionally has no access to the model download and no interest in
paying its CPU cost on every commit.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    bool(os.environ.get('RERANKER_SKIP_MODEL_TESTS')),
    reason='RERANKER_SKIP_MODEL_TESTS is set',
)


@pytest.fixture(scope='module')
def loaded_model():
    from app.services.model import RerankerModel

    model = RerankerModel()
    model.load()
    return model


def test_relevant_german_document_scores_higher_than_irrelevant(loaded_model):
    # The task's own required check: an obviously matching German document
    # must outscore an obviously unrelated one for the same query -- this
    # is the one test in the whole suite that says anything about whether
    # the model actually reranks BY MEANING rather than just running
    # without crashing.
    query = 'Wie lange ist die Kuendigungsfrist fuer einen Mietvertrag?'
    relevant = (
        'Die gesetzliche Kuendigungsfrist fuer einen unbefristeten Mietvertrag betraegt '
        'drei Monate zum Monatsende, sofern im Vertrag nichts anderes vereinbart wurde.'
    )
    irrelevant = (
        'Der Eiffelturm in Paris wurde 1889 anlaesslich der Weltausstellung fertiggestellt '
        'und ist rund 330 Meter hoch.'
    )

    # irrelevant listed FIRST on purpose -- proves the ordering comes from
    # the model's score, not from input order.
    scores = loaded_model.score(query, [irrelevant, relevant])

    assert scores[1] > scores[0]


def test_long_document_is_truncated_not_rejected(loaded_model):
    # Documents far longer than the model's 512-token limit
    # (app/services/model.py:_MAX_SEQUENCE_LENGTH) must still produce a
    # plain float score -- truncated by the tokenizer, never rejected. See
    # README.md's "Kuerzung statt Ablehnung".
    query = 'kuendigungsfrist'
    long_document = 'kuendigungsfrist wochen mietvertrag ' * 2000  # far beyond 512 tokens

    scores = loaded_model.score(query, [long_document])

    assert len(scores) == 1
    assert isinstance(scores[0], float)
