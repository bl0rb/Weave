"""Loads Alibaba-NLP/gte-multilingual-reranker-base (or whatever
RERANKER_MODEL names) as an ONNX cross-encoder through onnxruntime +
tokenizers -- no torch -- and scores query/document pairs on CPU.

Same footprint and loading pattern as services/embeddings
(app/services/encoder.py there): hf_hub_download the ONNX graph and the
tokenizer.json from the model repo, one onnxruntime InferenceSession on
CPUExecutionProvider. The default repo is onnx-community's export of the
Apache-2.0 Alibaba-NLP model (the original repo ships safetensors only and
needs trust_remote_code); RERANKER_ONNX_FILE picks the variant inside it,
by default the dynamically quantized `onnx/model_int8.onnx` (~341 MB
instead of ~1.26 GB for `onnx/model.onnx`).

One `RerankerModel` instance (the module-level `reranker_model` below) lives
for the whole process lifetime -- it is loaded once, in a background thread
started at app startup (see app/main.py's lifespan handler, and that
module's docstring for why GET /health must not wait on it), and never
reloaded or replaced.
"""

import logging
import threading
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

# A pair (query + document, jointly) longer than this is TRUNCATED by the
# tokenizer, never rejected: a cross-encoder still produces a usable, if
# degraded, relevance signal for a truncated pair, whereas rejecting the
# request would silently drop that document from the caller's search
# results entirely -- worse than a slightly-blind score. gte-multilingual
# itself accepts up to 8192 tokens, but cost grows with sequence length and
# 512 covers Weave's chunk sizes. See README.md's "Kuerzung statt
# Ablehnung" and tests/test_rerank_content.py's truncation test.
_MAX_SEQUENCE_LENGTH = 512

# Same lookup as services/embeddings: ask the tokenizer for its own pad id
# instead of trusting a config field.
_PAD_TOKEN_CANDIDATES = ('<pad>', '[PAD]')


def _sigmoid(values):
    import numpy as np

    return 1.0 / (1.0 + np.exp(-values))


class RerankerModel:
    """Wraps a single onnxruntime session plus its tokenizer."""

    def __init__(self) -> None:
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._ready = threading.Event()
        self.load_seconds: float | None = None

    @property
    def warm(self) -> bool:
        """True once `load()` has finished -- see GET /health
        (app/main.py) and POST /rerank's own wait (app/api/rerank.py).
        """
        return self._ready.is_set()

    def load(self) -> None:
        """Blocking model load. Call this from a background thread at
        startup (see app/main.py) -- never from a request handler, since it
        can take anywhere from about a second (warm HF cache) to a few
        minutes (cold download of the ~341 MB int8 weights) the very first
        time a given RERANKER_CACHE_DIR is used.
        """
        # Imported here, not at module level, so a plain
        # `import app.services.model` (e.g. from tests that monkeypatch
        # RerankerModel.score entirely, see tests/conftest.py) stays cheap
        # and network-free.
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        start = time.monotonic()
        logger.info(
            'loading reranker model %r (%s, cache_dir=%r, threads=%d)',
            settings.reranker_model, settings.reranker_onnx_file, settings.reranker_cache_dir, settings.reranker_threads,
        )
        cache_dir = settings.reranker_cache_dir or None
        tokenizer_path = hf_hub_download(repo_id=settings.reranker_model, filename='tokenizer.json', cache_dir=cache_dir)
        model_path = hf_hub_download(
            repo_id=settings.reranker_model, filename=settings.reranker_onnx_file, cache_dir=cache_dir
        )

        tokenizer = Tokenizer.from_file(tokenizer_path)
        pad_id, pad_token = self._resolve_pad_token(tokenizer)
        # tokenizer.json ships with fixed-length padding to 512; pad to the
        # longest pair of each batch instead, which is what keeps a batch of
        # short chunks cheap.
        tokenizer.enable_padding(pad_id=pad_id, pad_token=pad_token)
        tokenizer.enable_truncation(max_length=_MAX_SEQUENCE_LENGTH)

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, settings.reranker_threads)
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(model_path, sess_options=options, providers=['CPUExecutionProvider'])

        self._tokenizer = tokenizer
        self._session = session
        self._input_names = {model_input.name for model_input in session.get_inputs()}
        # First inference pays onnxruntime's one-time warm-up; do it here,
        # not in the first real request.
        self._score_batch('warm-up', ['warm-up'])

        self.load_seconds = time.monotonic() - start
        logger.info('reranker model %r loaded in %.1fs', settings.reranker_model, self.load_seconds)
        self._ready.set()

    @staticmethod
    def _resolve_pad_token(tokenizer) -> tuple[int, str]:
        for candidate in _PAD_TOKEN_CANDIDATES:
            token_id = tokenizer.token_to_id(candidate)
            if token_id is not None:
                return token_id, candidate
        raise ValueError(
            f'could not resolve a pad token id from tokenizer (tried {_PAD_TOKEN_CANDIDATES!r}) -- '
            'this model is not compatible with RerankerModel as written'
        )

    def wait_until_warm(self, timeout: float) -> bool:
        """Blocks up to `timeout` seconds for a load() running on another
        thread to finish. Returns whether it's warm by the time this
        returns -- see app/api/rerank.py's own docstring for why POST
        /rerank (unlike GET /health) needs to wait at all.
        """
        return self._ready.wait(timeout=timeout)

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        import numpy as np

        encodings = self._tokenizer.encode_batch([(query, document) for document in documents])
        input_ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
        feeds = {
            'input_ids': input_ids,
            'attention_mask': np.array([encoding.attention_mask for encoding in encodings], dtype=np.int64),
        }
        # Only BERT-style exports declare token_type_ids; XLM-R-style ones
        # (gte-multilingual, bge-reranker-v2-m3) ignore segments anyway.
        if 'token_type_ids' in self._input_names:
            feeds['token_type_ids'] = np.array([encoding.type_ids for encoding in encodings], dtype=np.int64)
        (logits,) = self._session.run(None, feeds)
        # One relevance logit per pair, mapped to 0..1 like
        # sentence-transformers' CrossEncoder did for this service before:
        # Weave-Retrieval and the runtime's score thresholds expect 0..1.
        return [float(score) for score in _sigmoid(logits.reshape(len(documents), -1)[:, 0])]

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Scores `query` against every one of `documents`, in the same
        order they were given (result[i] <-> documents[i]) -- sorting
        descending and applying top_n is the caller's job
        (app/api/rerank.py), not this method's.

        Raises RuntimeError if called before `load()` has completed;
        callers are expected to check `warm` (or call `wait_until_warm`)
        first -- see app/api/rerank.py.
        """
        if self._session is None:
            raise RuntimeError('reranker model is not loaded yet -- call load() (or wait for it) first')
        batch_size = max(1, settings.reranker_batch_size)
        scores: list[float] = []
        for start in range(0, len(documents), batch_size):
            scores.extend(self._score_batch(query, documents[start : start + batch_size]))
        return scores


# The one instance this whole process shares -- app/main.py starts its
# load() in a background thread at startup, app/api/rerank.py reads it,
# tests/conftest.py monkeypatches it.
reranker_model = RerankerModel()
