"""Runtime configuration for the reranker service, read from the process
environment (optionally via a local .env file) through pydantic-settings.

Every RERANKER_* variable here is deployment-level, not request-level -- a
setting a caller can influence per request (top_n, the `model` name a
HttpReranker-shaped caller happens to send, ...) lives in the request/
response schemas (app/schemas/rerank.py) instead.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    # --- Model identity & where its weights are cached on disk ------------
    # The one model Matze prescribed. NOT available in fastembed 0.8.0's
    # TextCrossEncoder.list_supported_models() (checked experimentally --
    # that call lists exactly 6 cross-encoders: Xenova/ms-marco-MiniLM-L-6/
    # 12-v2, BAAI/bge-reranker-base, and three jinaai/jina-reranker-*
    # models; bge-reranker-v2-m3 is absent), so this service loads it
    # through sentence-transformers' CrossEncoder (a thin wrapper over a
    # plain HF transformers XLM-RoBERTa-large-class sequence-classification
    # model) instead of fastembed's ONNX runtime. See README.md's "Warum
    # nicht fastembed" for the full reasoning and the image-size/latency
    # tradeoff that fallback costs.
    reranker_model: str = 'BAAI/bge-reranker-v2-m3'
    reranker_cache_dir: str = './.cache'

    # --- Incoming request auth ---------------------------------------------
    # Fail-closed, same discipline as every other Weave service's own
    # static service token (Weave-Retrieval's RETRIEVAL_API_TOKEN,
    # Weave-Tools' TOOLS_API_TOKEN, ...): left empty, POST /rerank answers
    # 503 for every request rather than silently accepting any -- or no --
    # Authorization header as a match. GET /health is deliberately exempt
    # (see app/main.py) -- a liveness probe carries no bearer token.
    reranker_api_token: str = ''

    # --- CPU inference tuning -----------------------------------------------
    # Passed straight to CrossEncoder.predict(batch_size=...)
    # (app/services/model.py). Larger batches amortize per-call Python/
    # tokenizer overhead a little, but bge-reranker-v2-m3 is CPU-bound on
    # the transformer forward pass itself either way -- see README's
    # measured-latency table before raising this much past the default.
    reranker_batch_size: int = 16
    # torch.set_num_threads(...) (app/services/model.py). Left well under
    # this host's full core count on purpose: one reranker process sharing
    # a box with other Weave services (or several reranker replicas on one
    # box) should not each claim every core for itself.
    reranker_threads: int = 4

    # --- The one guardrail that actually matters for a cross-encoder: it
    # scores query+document PAIRS one at a time, so cost is O(n) in the
    # document count with no way to batch it away like a bi-encoder
    # embedding call can (see README's "Warum nicht fastembed" and its
    # measured-latency table for the numbers this default is based on).
    reranker_max_documents: int = 50


settings = Settings()
