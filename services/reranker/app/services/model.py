"""Loads BAAI/bge-reranker-v2-m3 (or whatever RERANKER_MODEL names) through
sentence-transformers' CrossEncoder and scores query/document pairs on CPU.

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

# bge-reranker-v2-m3's own tokenizer default -- see its HF model card and
# BAAI's FlagEmbedding reference implementation. A pair (query + document,
# jointly) longer than this is TRUNCATED by the tokenizer, never rejected:
# a cross-encoder still produces a usable, if degraded, relevance signal
# for a truncated pair, whereas rejecting the request would silently drop
# that document from the caller's search results entirely -- worse than a
# slightly-blind score. See README.md's "Kuerzung statt Ablehnung" and
# tests/test_rerank_content.py's truncation test for this in practice.
_MAX_SEQUENCE_LENGTH = 512


class RerankerModel:
    """Wraps a single CrossEncoder instance."""

    def __init__(self) -> None:
        self._model = None
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
        can take anywhere from a couple of seconds (warm HF cache) to
        several minutes (cold download of the ~2.3 GB model weights) the
        very first time a given RERANKER_CACHE_DIR is used.
        """
        # Imported here, not at module level: torch + sentence-transformers
        # together add roughly 1 GB to this service's dependency footprint
        # (see README.md's "Imagegroesse") and take a couple of seconds
        # just to import -- deferring the import to the loader thread keeps
        # a plain `import app.services.model` (e.g. from tests that
        # monkeypatch RerankerModel.score entirely, see tests/conftest.py)
        # cheap and network-free.
        import torch
        from sentence_transformers import CrossEncoder

        torch.set_num_threads(max(1, settings.reranker_threads))
        start = time.monotonic()
        logger.info(
            'loading reranker model %r (cache_folder=%r, threads=%d)',
            settings.reranker_model, settings.reranker_cache_dir, settings.reranker_threads,
        )
        self._model = CrossEncoder(
            settings.reranker_model,
            cache_folder=settings.reranker_cache_dir,
            device='cpu',
            max_length=_MAX_SEQUENCE_LENGTH,
        )
        self.load_seconds = time.monotonic() - start
        logger.info('reranker model %r loaded in %.1fs', settings.reranker_model, self.load_seconds)
        self._ready.set()

    def wait_until_warm(self, timeout: float) -> bool:
        """Blocks up to `timeout` seconds for a load() running on another
        thread to finish. Returns whether it's warm by the time this
        returns -- see app/api/rerank.py's own docstring for why POST
        /rerank (unlike GET /health) needs to wait at all.
        """
        return self._ready.wait(timeout=timeout)

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Scores `query` against every one of `documents`, in the same
        order they were given (result[i] <-> documents[i]) -- sorting
        descending and applying top_n is the caller's job
        (app/api/rerank.py), not this method's.

        Raises RuntimeError if called before `load()` has completed;
        callers are expected to check `warm` (or call `wait_until_warm`)
        first -- see app/api/rerank.py.
        """
        if self._model is None:
            raise RuntimeError('reranker model is not loaded yet -- call load() (or wait for it) first')
        pairs = [(query, document) for document in documents]
        scores = self._model.predict(pairs, batch_size=max(1, settings.reranker_batch_size))
        return [float(score) for score in scores]


# The one instance this whole process shares -- app/main.py starts its
# load() in a background thread at startup, app/api/rerank.py reads it,
# tests/conftest.py monkeypatches it.
reranker_model = RerankerModel()
