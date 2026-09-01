"""Process-wide mutable state for the ONE embedding model this service
loads once at startup and serves for the rest of the process's life --
there is no per-request model switching or reloading.

A single module-level instance (`encoder_state`) rather than e.g. FastAPI's
`app.state`, so both app/main.py's lifespan (the writer) and tests (which
need to install a fake Embedder without going through the real, slow,
network-dependent startup path -- see tests/conftest.py) can import and
touch the exact same object directly.
"""

from dataclasses import dataclass

from app.services.encoder import Embedder


@dataclass
class EncoderState:
    embedder: Embedder | None = None
    warm: bool = False
    # Set if build_embedder() raised during the background load -- surfaced
    # by GET /health's `status` field (see app/main.py) so a deployment can
    # tell "still downloading" apart from "will never come up" without
    # digging through logs.
    load_error: str | None = None


encoder_state = EncoderState()
