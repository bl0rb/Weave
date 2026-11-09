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
    # Hugging Face repo the ONNX graph and tokenizer.json are downloaded
    # from (app/services/model.py). The default is onnx-community's export
    # of Alibaba-NLP/gte-multilingual-reranker-base (Apache-2.0, 306M,
    # multilingual incl. German); the original repo ships no ONNX. Any
    # cross-encoder repo with a single-logit ONNX graph and a root
    # tokenizer.json works, e.g. onnx-community/bge-reranker-v2-m3-ONNX.
    reranker_model: str = 'onnx-community/gte-multilingual-reranker-base'
    # Path of the ONNX file inside that repo. The int8 export is ~341 MB
    # instead of ~1.26 GB (onnx/model.onnx) and several times faster on CPU.
    reranker_onnx_file: str = 'onnx/model_int8.onnx'
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
    # Pairs per onnxruntime forward pass (app/services/model.py). Larger
    # batches amortize per-call overhead a little, but the model is
    # CPU-bound on the forward pass itself either way.
    reranker_batch_size: int = 16
    # onnxruntime intra_op_num_threads (app/services/model.py). Left well under
    # this host's full core count on purpose: one reranker process sharing
    # a box with other Weave services (or several reranker replicas on one
    # box) should not each claim every core for itself.
    reranker_threads: int = 4

    # --- The one guardrail that actually matters for a cross-encoder: it
    # scores query+document PAIRS one at a time, so cost is O(n) in the
    # document count with no way to batch it away like a bi-encoder
    # embedding call can (see README's measured-latency table).
    reranker_max_documents: int = 50


settings = Settings()
