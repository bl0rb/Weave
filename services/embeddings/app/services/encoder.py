"""CPU inference for intfloat/multilingual-e5-small, run directly through
onnxruntime -- deliberately WITHOUT fastembed.

Why not fastembed
------------------
The task this service was built against pins the embedding model to
exactly `intfloat/multilingual-e5-small` and requires checking, before
writing any code, whether fastembed (the library every other Weave-Tools
model server in this repo uses) actually supports it:

    >>> from fastembed import TextEmbedding
    >>> [m['model'] for m in TextEmbedding.list_supported_models() if 'e5' in m['model']]
    ['intfloat/multilingual-e5-large']

fastembed==0.8.0 (the version pinned in requirements.txt) lists 30 dense
text models; `intfloat/multilingual-e5-small` is not one of them, under any
name or prefix -- only the larger `intfloat/multilingual-e5-large` (1024
dimensions, 2.24GB) is present. Per the task's own instruction, an
unsupported PINNED model is not a license to silently substitute a
different one fastembed DOES support (e.g. e5-large, or
paraphrase-multilingual-MiniLM-L12-v2) -- it falls back to
sentence-transformers or optimum/ONNX for that one model instead, with the
choice justified.

This module takes the ONNX branch, hand-rolled rather than through the
`optimum` package, for a concrete, verified reason: installing
`optimum[onnxruntime]` (2.1.0, alongside transformers 4.57.6) pulled in
`torch` 2.13.0 (a 111MB wheel) as a transitive dependency even though
optimum's ORTModel classes only need torch-free onnxruntime at inference
time for a model that already ships pre-exported ONNX weights (verified by
installing it into this service's own .venv and inspecting the dependency
resolution -- see this repo's README "Modellwahl" section). Carrying torch
into a CPU-only embedding server's image just to load a tokenizer class and
a config object contradicts this service's own "CPU-only, load once"
brief, especially next to the sibling `reranker/` service in this repo,
which stays on the same onnxruntime+tokenizers footprint fastembed itself
uses internally. sentence-transformers has the identical problem (it's
built on top of transformers+torch).

So: onnxruntime (already a fastembed dependency, already in this service's
.venv) + tokenizers (ditto) + huggingface_hub, loading
`intfloat/multilingual-e5-small`'s own `onnx/model.onnx` +
`onnx/tokenizer.json` -- files intfloat themselves publish in that model's
HF repo, not a third-party conversion -- and reimplementing, by hand, the
exact mean-pooling-then-L2-normalize recipe fastembed runs internally for
every BERT-family model in its own supported list (see fastembed's
`PoolingType.MEAN` and the "now uses mean pooling instead of CLS embedding"
warning `TextEmbedding.__init__` raises for e5-large and three other
models, in fastembed/text/text_embedding.py). This is architecturally the
same thing fastembed would do if it listed this model -- just without
fastembed's own model registry entry for it.

The "query: " / "passage: " prefix contract
--------------------------------------------
intfloat/multilingual-e5-* models are trained with an explicit
natural-language-style prefix ahead of every input: "query: " for a search
query, "passage: " for an indexed passage (see the model's own HF README:
'Each input text should start with "query: " or "passage: ", even for
non-English texts.'). This is NOT applied automatically by fastembed even
for the one e5 model it DOES support out of the box
(`intfloat/multilingual-e5-large`) -- verified by reading
fastembed/text/text_embedding_base.py: `TextEmbeddingBase.passage_embed()`
and `.query_embed()` are both thin, model-agnostic wrappers that call plain
`.embed()` unchanged ("# This is model-specific, so that different models
can have specialized implementations" -- but no dense text model in this
fastembed version actually overrides either method to inject one), and
`OnnxTextEmbedding` (fastembed/text/onnx_embedding.py) never overrides them
either. So even had fastembed listed multilingual-e5-small, calling its
`query_embed()`/`embed()` would NOT have added these prefixes for us -- we
would still be prepending them by hand, exactly as this module's `encode()`
does via `_PREFIXES` below. The prefix is controlled by this service's
`input_type` request field (see app/schemas/embeddings.py), not by which
onnx endpoint/method a caller reaches -- there is only one `/v1/embeddings`
endpoint, per the task's chunk-store contract (a second endpoint would mean
two different model names reaching Weave-Knowledge's per-chunk
`embedding_model` column, which Weave-Retrieval's search matches by exact
string -- see that field's docstring in Weave-Knowledge's
app/services/embeddings.py).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from app.core.config import Settings

logger = logging.getLogger(__name__)

InputType = Literal['query', 'passage']

# The exact string prepended to a raw input text for each input_type, per
# intfloat/multilingual-e5-small's own model card. Missing input_type is
# handled by the request schema/API layer (defaults to 'passage', see
# app/schemas/embeddings.py and app/main.py) -- this dict only ever sees an
# already-resolved value.
_PREFIXES: dict[InputType, str] = {
    'query': 'query: ',
    'passage': 'passage: ',
}

# Candidate pad-token spellings, tried in order, to resolve the tokenizer's
# OWN pad token id rather than assuming one. multilingual-e5-small ships an
# XLM-RoBERTa-style tokenizer whose special tokens are <s>=0, <pad>=1,
# </s>=2, <unk>=3 -- notably NOT 0, even though this model's own
# onnx/config.json (a generic BertModel config template carried over from
# the export tooling) claims `"pad_token_id": 0`. That field describes the
# wrong tokenizer family and is not used here; asking the tokenizer itself
# for the id of the literal "<pad>" token is what's actually correct, and
# checking a couple of common spellings keeps this working if
# EMBEDDINGS_MODEL is pointed at a BERT-style repo using "[PAD]" instead.
_PAD_TOKEN_CANDIDATES = ('<pad>', '[PAD]')


@dataclass
class EncodeResult:
    """Output of Embedder.encode(): parallel lists, one entry per input
    text, in the SAME order the caller passed them in -- app/main.py's
    /v1/embeddings handler assigns `index = position in this list` when
    building the response, so callers never need to reorder anything
    (fastembed/onnxruntime batching happens internally here and never
    changes this order; see Embedder.encode's docstring).
    """

    vectors: list[list[float]]
    token_counts: list[int]  # real (non-padding) token count per input, for the response's `usage`


def resolve_thread_count(configured: int) -> int:
    """EMBEDDINGS_THREADS==0 means 'let onnxruntime decide' -- but we still
    resolve that to a concrete number (os.cpu_count(), the same default
    onnxruntime itself would pick) rather than leaving the ONNX
    SessionOptions field unset, purely so GET /health can report the actual
    thread count in use instead of a bare 0 that says nothing. Called both
    by build_embedder (to configure the ONNX session) and by the /health
    handler (which needs a number even before the model has finished
    loading).
    """
    if configured > 0:
        return configured
    return os.cpu_count() or 1


def _resolve_pad_token(tokenizer: Tokenizer) -> tuple[int, str]:
    for candidate in _PAD_TOKEN_CANDIDATES:
        token_id = tokenizer.token_to_id(candidate)
        if token_id is not None:
            return token_id, candidate
    # Falling back to id 0 would silently reuse whatever token id 0
    # actually means for this vocabulary (often a BOS/CLS token, not a
    # pad) -- refusing to guess is safer than serving embeddings computed
    # against a wrong padding scheme, even though (see this module's own
    # test/probe notes) mean-pooling over the attention mask makes the
    # actual pad id mostly cosmetic in practice.
    raise ValueError(
        f'could not resolve a pad token id from tokenizer (tried {_PAD_TOKEN_CANDIDATES!r}) -- '
        'this model is not compatible with Embedder as written'
    )


class Embedder:
    """Loaded, ready-to-serve embedding model: an onnxruntime
    InferenceSession plus its matching `tokenizers.Tokenizer`. Constructed
    exactly once at process startup by build_embedder() (see app/main.py's
    lifespan) and reused for the process's entire life -- there is no
    per-request model reload.
    """

    def __init__(
        self,
        *,
        model_name: str,
        session: ort.InferenceSession,
        tokenizer: Tokenizer,
        threads: int,
        batch_size: int,
        normalize: bool,
    ) -> None:
        self._model_name = model_name
        self._session = session
        self._tokenizer = tokenizer
        self._threads = threads
        self._batch_size = max(1, batch_size)
        self._normalize = normalize
        self._output_name = session.get_outputs()[0].name
        # Resolved lazily on first encode() call (see that method) rather
        # than trusted from the ONNX graph's declared output shape, which
        # can carry a symbolic/dynamic axis instead of a concrete int.
        self._dimension: int | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int | None:
        return self._dimension

    @property
    def threads(self) -> int:
        return self._threads

    def encode(self, texts: list[str], *, input_type: InputType) -> EncodeResult:
        """Embed `texts` (already-raw, unprefixed) as either search queries
        or indexed passages, per `input_type` -- see this module's
        docstring for why the prefix is applied here rather than relying on
        a library to do it. Internally batches at self._batch_size ONNX
        forward passes; batching never changes output order (each batch's
        results are appended to the running list in the same order its
        inputs were sliced from the caller's original list), so
        EncodeResult.vectors[i] always corresponds to texts[i].
        """
        if not texts:
            return EncodeResult(vectors=[], token_counts=[])

        prefix = _PREFIXES[input_type]
        prefixed = [prefix + text for text in texts]

        vectors: list[list[float]] = []
        token_counts: list[int] = []
        for start in range(0, len(prefixed), self._batch_size):
            batch = prefixed[start : start + self._batch_size]
            batch_vectors, batch_counts = self._encode_batch(batch)
            vectors.extend(batch_vectors)
            token_counts.extend(batch_counts)

        if self._dimension is None:
            self._dimension = len(vectors[0])

        return EncodeResult(vectors=vectors, token_counts=token_counts)

    def _encode_batch(self, batch: list[str]) -> tuple[list[list[float]], list[int]]:
        encodings = self._tokenizer.encode_batch(batch)
        input_ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.array([encoding.attention_mask for encoding in encodings], dtype=np.int64)
        # multilingual-e5-small is a single-segment encoder (no
        # sentence-pair task) -- every position gets segment id 0.
        token_type_ids = np.zeros_like(input_ids)

        (last_hidden,) = self._session.run(
            [self._output_name],
            {'input_ids': input_ids, 'attention_mask': attention_mask, 'token_type_ids': token_type_ids},
        )

        # Mean pooling over real (non-padding) tokens: multiply by the
        # attention mask before summing so padded positions (whatever
        # value the underlying model computed for them -- they still ran
        # through every transformer layer, just without being attended TO)
        # contribute nothing to the pooled vector, then divide by the
        # per-example count of real tokens rather than the padded sequence
        # length. Confirmed empirically (see README) that embedding the
        # same text alone vs. padded inside a longer batch produces
        # bit-identical output under this scheme.
        mask = attention_mask[:, :, None].astype(np.float32)
        summed = (last_hidden * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
        pooled = summed / counts

        if self._normalize:
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = pooled / np.clip(norm, a_min=1e-12, a_max=None)

        token_counts = attention_mask.sum(axis=1).astype(int).tolist()
        return pooled.tolist(), token_counts


def build_embedder(settings: Settings) -> Embedder:
    """Download (if not already cached) and load settings.embeddings_model,
    then run one warm-up encode() call before returning -- the first real
    onnxruntime inference after session creation pays a one-time
    graph-optimization/allocator-warmup cost; paying it here means the
    first actual client request doesn't. Called exactly once, from a
    background thread started by app/main.py's lifespan (never from the
    request-handling event loop, and never blocking app startup -- see
    that module's docstring for why).
    """
    start = time.monotonic()
    cache_dir = settings.embeddings_cache_dir or None
    logger.info(
        "loading embeddings model %r (cache_dir=%r) -- first run downloads the model and can take a while",
        settings.embeddings_model,
        cache_dir,
    )

    tokenizer_path = hf_hub_download(
        repo_id=settings.embeddings_model, filename='onnx/tokenizer.json', cache_dir=cache_dir
    )
    model_path = hf_hub_download(repo_id=settings.embeddings_model, filename='onnx/model.onnx', cache_dir=cache_dir)

    tokenizer = Tokenizer.from_file(tokenizer_path)
    pad_id, pad_token = _resolve_pad_token(tokenizer)
    tokenizer.enable_padding(pad_id=pad_id, pad_token=pad_token)
    tokenizer.enable_truncation(max_length=512)

    threads = resolve_thread_count(settings.embeddings_threads)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    # Pinned to 1 rather than left automatic: this is a single ONNX graph
    # with no independent parallel subgraphs to schedule concurrently, so
    # more than one inter-op thread only adds scheduling overhead without
    # doing any additional useful work -- the actual parallelism comes from
    # intra_op_num_threads above.
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(model_path, sess_options=options, providers=['CPUExecutionProvider'])

    embedder = Embedder(
        model_name=settings.embeddings_model,
        session=session,
        tokenizer=tokenizer,
        threads=threads,
        batch_size=settings.embeddings_batch_size,
        normalize=settings.embeddings_normalize,
    )
    embedder.encode(['startup warm-up'], input_type='passage')

    elapsed = time.monotonic() - start
    logger.info(
        'embeddings model %r ready in %.1fs (dimension=%d, threads=%d)',
        embedder.model_name,
        elapsed,
        embedder.dimension,
        threads,
    )
    return embedder
