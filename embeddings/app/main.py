"""FastAPI app for Weave's OpenAI-compatible embedding server.

Startup does NOT block on loading the model. This matters for one concrete
operational reason (see the task this service was built against, and
README's "Erster Start"): the very first start of a fresh deployment has to
download ~470MB of ONNX weights from HuggingFace, which can take anywhere
from a few seconds to a couple of minutes depending on the network. If that
download ran inline during ASGI startup, a container orchestrator's
healthcheck (Docker's own HEALTHCHECK, a Kubernetes readiness probe, ...)
would see nothing answer on the port at all during that window and, on a
short probe timeout, conclude the container is unhealthy and restart it --
which would abandon the in-progress download and restart it from scratch,
potentially forever.

Instead: `lifespan` starts a daemon background thread that calls
build_embedder() and returns immediately, so uvicorn starts accepting
connections right away. GET /health answers 200 immediately, with
`warm=false` (and `status="starting"`) until that thread finishes -- a
healthcheck that only checks for "the process answers HTTP at all" (which
is the right thing for a liveness probe to check) never sees a blocked
port, and one that specifically wants readiness can poll `warm`/`status`
instead of the HTTP status code. POST /v1/embeddings answers 503 for the
same window (see create_embeddings below) -- the model genuinely isn't
ready yet, and 503 is the correct way to say so to a client rather than
hanging the request until it is.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from starlette.concurrency import run_in_threadpool

from app.core.auth import require_api_token
from app.core.config import settings
from app.schemas.embeddings import (
    EmbeddingItem,
    EmbeddingsRequest,
    EmbeddingsResponse,
    HealthResponse,
    ModelInfo,
    ModelsResponse,
    Usage,
)
from app.services import encoder as encoder_module
from app.services.state import encoder_state

logger = logging.getLogger(__name__)


def _load_model_in_background() -> None:
    """Run on a daemon thread started by `lifespan` below. Looks up
    `encoder_module.build_embedder` (a module attribute, not a value bound
    as this function's own default argument) EVERY time it's called, so
    tests can monkeypatch `app.main.encoder_module.build_embedder` (or
    patch the reference this module holds) with a fast fake before
    triggering startup, without needing to touch this function itself --
    see tests/test_startup.py.
    """
    try:
        encoder_state.embedder = encoder_module.build_embedder(settings)
        encoder_state.warm = True
    except Exception as exc:  # noqa: BLE001 -- any failure here must set load_error, not crash a daemon thread silently
        encoder_state.load_error = str(exc)
        logger.exception('failed to load embeddings model %r', settings.embeddings_model)


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=_load_model_in_background, name='embeddings-model-loader', daemon=True)
    thread.start()
    yield
    # Nothing to release on shutdown: the onnxruntime session and tokenizer
    # are freed when the process exits, and the loader thread is a daemon
    # (never blocks process exit even mid-download).


app = FastAPI(title=settings.app_name, lifespan=lifespan)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')


@app.get('/health', response_model=HealthResponse)
def health() -> HealthResponse:
    embedder = encoder_state.embedder
    if encoder_state.load_error is not None:
        health_status = 'error'
    elif encoder_state.warm:
        health_status = 'ok'
    else:
        health_status = 'starting'

    return HealthResponse(
        status=health_status,
        model=settings.embeddings_model,
        dimension=embedder.dimension if embedder is not None else None,
        threads=embedder.threads if embedder is not None else encoder_module.resolve_thread_count(settings.embeddings_threads),
        warm=encoder_state.warm,
    )


@app.get('/v1/models', response_model=ModelsResponse, dependencies=[Depends(require_api_token)])
def list_models() -> ModelsResponse:
    return ModelsResponse(data=[ModelInfo(id=settings.embeddings_model)])


@app.post('/v1/embeddings', response_model=EmbeddingsResponse, dependencies=[Depends(require_api_token)])
async def create_embeddings(payload: EmbeddingsRequest) -> EmbeddingsResponse:
    if not encoder_state.warm or encoder_state.embedder is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='model is still loading')

    embedder = encoder_state.embedder

    # A single-model server: `model` is accepted (and echoed back verbatim
    # below, per the chunk-store contract's "the model name stays exactly
    # the same in request and response" -- see app/services/encoder.py's
    # module docstring) but must name the one model actually loaded, the
    # same way a self-hosted single-model OpenAI-compatible server (vLLM,
    # Ollama's compat mode) rejects a request for a model it doesn't have.
    if payload.model != embedder.model_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'unknown model {payload.model!r}; this deployment serves {embedder.model_name!r}',
        )

    texts = payload.input if isinstance(payload.input, list) else [payload.input]
    if len(texts) > settings.embeddings_max_inputs:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f'input contains {len(texts)} item(s), exceeding '
                f'EMBEDDINGS_MAX_INPUTS={settings.embeddings_max_inputs}'
            ),
        )

    # Missing input_type behaves exactly like "passage" -- this is where
    # that default is actually applied (the request schema itself leaves
    # it as None so a caller's absence-of-opinion is observable), which is
    # what keeps an ordinary OpenAI client (which has never heard of
    # input_type) working unchanged: see app/schemas/embeddings.py and this
    # service's README.
    input_type = payload.input_type or 'passage'

    # onnxruntime inference is a blocking, CPU-bound call -- running it
    # inline in this `async def` handler would block the whole event loop
    # (and therefore every other in-flight request, including /health) for
    # its entire duration. run_in_threadpool hands it to a worker thread
    # instead, the same tool Starlette's own sync-route support uses
    # internally.
    result = await run_in_threadpool(embedder.encode, texts, input_type=input_type)

    data = [EmbeddingItem(index=index, embedding=vector) for index, vector in enumerate(result.vectors)]
    total_tokens = sum(result.token_counts)
    return EmbeddingsResponse(
        data=data,
        model=payload.model,
        usage=Usage(prompt_tokens=total_tokens, total_tokens=total_tokens),
    )
