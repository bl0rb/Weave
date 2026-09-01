"""Shape and behavioral contract of POST /v1/embeddings against the
FakeEmbedder (see tests/conftest.py for why real-model tests live
separately in test_real_model.py)."""

import math

from tests.conftest import AUTH_HEADERS, FAKE_MODEL_NAME, client, install_fake_embedder


def test_response_shape_matches_knowledge_client_contract():
    """Field-for-field the shape Weave-Knowledge's OpenAICompatibleProvider
    (backend/app/services/embeddings.py) actually parses:
    {object, data:[{object, index, embedding}], model, usage:{prompt_tokens,
    total_tokens}}."""
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings',
        headers=AUTH_HEADERS,
        json={'model': FAKE_MODEL_NAME, 'input': 'ein einzelner Text'},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body['object'] == 'list'
    assert body['model'] == FAKE_MODEL_NAME
    assert isinstance(body['data'], list) and len(body['data']) == 1
    item = body['data'][0]
    assert item['object'] == 'embedding'
    assert item['index'] == 0
    assert isinstance(item['embedding'], list) and len(item['embedding']) > 0
    assert set(body['usage'].keys()) == {'prompt_tokens', 'total_tokens'}
    assert body['usage']['prompt_tokens'] == body['usage']['total_tokens'] > 0


def test_string_input_returns_exactly_one_embedding():
    install_fake_embedder()
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'hallo welt'})
    assert resp.status_code == 200
    assert len(resp.json()['data']) == 1


def test_list_input_returns_one_embedding_per_item_same_order():
    fake = install_fake_embedder()
    texts = [f'Satz Nummer {i}' for i in range(5)]
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': texts})
    assert resp.status_code == 200
    data = resp.json()['data']
    assert len(data) == 5
    for i, item in enumerate(data):
        assert item['index'] == i
        assert item['embedding'] == fake._vector_for(texts[i], 'passage')


def test_index_order_correct_across_internal_batch_boundaries():
    """Forces multiple internal onnxruntime batches (batch_size=2, 7
    inputs -- see app/services/encoder.py:Embedder.encode) and checks that
    `index` and the returned vector both still line up with the ORIGINAL
    request position, not with the position inside whichever internal
    batch produced them."""
    fake = install_fake_embedder()
    texts = [f'Dokument {i}' for i in range(7)]
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': texts})
    assert resp.status_code == 200
    data = resp.json()['data']
    assert len(data) == 7
    for i, item in enumerate(data):
        assert item['index'] == i
        assert item['embedding'] == fake._vector_for(texts[i], 'passage')


def test_dimension_matches_health_reported_dimension():
    install_fake_embedder(dimension=384)
    health = client.get('/health').json()
    assert health['dimension'] == 384

    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'x'})
    embedding = resp.json()['data'][0]['embedding']
    assert len(embedding) == health['dimension']


def test_vectors_are_l2_normalized():
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': ['a', 'ein laengerer text']}
    )
    for item in resp.json()['data']:
        norm = math.sqrt(sum(c * c for c in item['embedding']))
        assert math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6)


def test_input_type_query_and_passage_yield_different_vectors_for_same_text():
    """Proves input_type actually reaches the encoder (through the HTTP
    layer, Pydantic model, and encode() call) rather than being ignored --
    the real-model equivalent of this property is verified separately
    against actual ONNX inference in test_real_model.py."""
    install_fake_embedder()
    text = 'Wie stelle ich den Drucker ein?'

    query_resp = client.post(
        '/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': text, 'input_type': 'query'}
    )
    passage_resp = client.post(
        '/v1/embeddings',
        headers=AUTH_HEADERS,
        json={'model': FAKE_MODEL_NAME, 'input': text, 'input_type': 'passage'},
    )

    query_vec = query_resp.json()['data'][0]['embedding']
    passage_vec = passage_resp.json()['data'][0]['embedding']
    assert query_vec != passage_vec


def test_missing_input_type_behaves_like_passage():
    fake = install_fake_embedder()
    text = 'ein Text ohne explizites input_type'

    no_input_type_resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': text})
    explicit_passage_resp = client.post(
        '/v1/embeddings',
        headers=AUTH_HEADERS,
        json={'model': FAKE_MODEL_NAME, 'input': text, 'input_type': 'passage'},
    )

    assert no_input_type_resp.json()['data'][0]['embedding'] == explicit_passage_resp.json()['data'][0]['embedding']
    # Both calls must have actually reached the encoder tagged as 'passage'
    # -- not merely produced equal output by coincidence.
    assert fake.calls[-2] == ([text], 'passage')
    assert fake.calls[-1] == ([text], 'passage')


def test_invalid_input_type_is_rejected():
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings',
        headers=AUTH_HEADERS,
        json={'model': FAKE_MODEL_NAME, 'input': 'x', 'input_type': 'not-a-real-value'},
    )
    assert resp.status_code == 422


def test_empty_list_input_is_rejected():
    install_fake_embedder()
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': []})
    assert resp.status_code == 422


def test_unknown_model_name_is_rejected():
    install_fake_embedder()
    resp = client.post(
        '/v1/embeddings', headers=AUTH_HEADERS, json={'model': 'some/other-model', 'input': 'x'}
    )
    assert resp.status_code == 404


def test_not_warm_yet_returns_503():
    # No install_fake_embedder() call -- encoder_state starts cold (see the
    # autouse reset fixture in conftest.py) exactly as it is immediately
    # after process startup, before the background loader thread finishes.
    resp = client.post('/v1/embeddings', headers=AUTH_HEADERS, json={'model': FAKE_MODEL_NAME, 'input': 'x'})
    assert resp.status_code == 503
