from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Weave Embeddings'

    # --- Model identity --------------------------------------------------
    # The USER-chosen embedding model, not fastembed's own name for it --
    # fastembed==0.8.0's TextEmbedding.list_supported_models() does not
    # list intfloat/multilingual-e5-small at all (only the larger
    # intfloat/multilingual-e5-large is present among the e5 family; see
    # this repo's README "Modellwahl" section for the full experiment).
    # app/services/encoder.py therefore never touches fastembed for this
    # model: it downloads THIS repo's own official ONNX export
    # (huggingface.co/intfloat/multilingual-e5-small, `onnx/model.onnx` +
    # `onnx/tokenizer.json`, authored by intfloat themselves) and runs it
    # directly through onnxruntime, replicating fastembed's own
    # mean-pooling-then-normalize recipe for BERT-family models by hand.
    # Swapping this to a DIFFERENT model only works if that model's HF repo
    # follows the same layout (a BERT-architecture encoder with a
    # `last_hidden_state` output under an `onnx/` subfolder, an
    # XLMRoberta/BERT-style tokenizer.json, and the same "query: "/
    # "passage: " prefix convention) -- e.g. intfloat/multilingual-e5-base
    # or -large. A structurally different model needs code changes in
    # app/services/encoder.py, not just this setting.
    embeddings_model: str = 'intfloat/multilingual-e5-small'

    # --- Incoming service auth -------------------------------------------
    # Same fail-closed discipline as every other Weave service's own static
    # service token (Weave-Tools' TOOLS_API_TOKEN, Weave-Retrieval's
    # RETRIEVAL_API_TOKEN, Weave-Runtime's RUNTIME_API_TOKEN, ...): left
    # empty, app/core/auth.py's require_api_token answers 503 to every
    # request that would otherwise need it, rather than silently treating
    # an unset token as "auth disabled". Sent the same way any OpenAI
    # client already sends its own API key -- `Authorization: Bearer
    # <token>` -- so an off-the-shelf OpenAI SDK pointed at this service's
    # base_url needs no special-casing beyond the usual api_key parameter.
    embeddings_api_token: str = ''

    # --- Serving/runtime tuning -------------------------------------------
    # How many (prefixed) texts go into a single onnxruntime forward pass.
    # A request with more inputs than this is split into successive
    # internal batches of at most this size -- response order (by `index`)
    # is unaffected either way, see app/services/encoder.py:Embedder.encode.
    embeddings_batch_size: int = 32

    # onnxruntime intra-op thread count. 0 means "automatic": resolved at
    # load time to os.cpu_count() (see app/services/encoder.py's
    # resolve_thread_count), which is what onnxruntime itself would pick by
    # default -- resolving it ourselves (rather than leaving the ONNX
    # SessionOptions field unset) means /health can report the actual
    # number being used instead of a bare "0".
    embeddings_threads: int = 0

    # Where huggingface_hub caches downloaded model files. '' defers to
    # huggingface_hub's own default resolution (HF_HOME / ~/.cache/
    # huggingface) -- a real deployment should point this at a mounted
    # volume (see Dockerfile/README) so the ~470MB ONNX weights survive a
    # container recreation instead of re-downloading on every restart.
    embeddings_cache_dir: str = ''

    # Hard cap on how many texts a single /v1/embeddings request may carry
    # in its `input` list (a single string input is always exactly 1 and
    # never rejected by this limit). Exceeding it is a 413, not a 400 --
    # this is a size limit on the request, not a malformed request.
    embeddings_max_inputs: int = 256

    # Whether returned vectors are L2-normalized (norm 1) before being sent
    # back. The multilingual-e5 model card recommends normalizing before
    # computing cosine similarity/dot products; pgvector's own `<=>`
    # cosine-distance operator (what Weave-Retrieval's search actually
    # uses, see that repo's app/services/search.py) is scale-invariant and
    # would work either way, but normalized vectors keep a plain dot
    # product equivalent to cosine similarity too, which is the safer
    # default for any consumer that doesn't specifically use `<=>`.
    embeddings_normalize: bool = True


settings = Settings()
