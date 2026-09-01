"""Shared TestClient wiring for the backend test suite.

Mirrors Weave-Retrieval's own tests/conftest.py: TEST_TOKEN is set on the
process-wide `settings` object BEFORE `app.main` is imported, so the app is
built with a real, non-empty RUNTIME_API_TOKEN and tests exercise the actual
require_service_token dependency (missing/wrong/right token) instead of one
bypassed via dependency_overrides. Settings is a plain (non-frozen) pydantic
model, and every module that does `from app.core.config import settings`
gets the same singleton instance -- so mutating an attribute here, before
anything else imports it, is visible everywhere for the rest of the process.

Unlike Weave-Retrieval, this service owns no database at all -- Weave-Runtime
is deliberately stateless (see README's "Nicht-Ziele") -- so there is no
get_db override here. What DOES need the same process-wide, import-time
treatment is `settings.bots_dir`: its default ('./bots') is relative to the
process's CWD (see app/core/config.py's own comment), which only resolves to
this repo's real bot roster when the process happens to be started from the
repo root. Pointing it at that directory by absolute path here means the
suite behaves identically no matter which directory `pytest` is invoked
from (the orchestrating task runs it from the repo root; a developer running
`pytest` from backend/ directly must see the same bots).
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import settings

TEST_TOKEN = 'test-service-token'
settings.runtime_api_token = TEST_TOKEN

# backend/tests/conftest.py -> parents[0]=backend/tests, [1]=backend, [2]=repo root
REPO_BOTS_DIR = Path(__file__).resolve().parents[2] / 'bots'
settings.bots_dir = str(REPO_BOTS_DIR)

from app.main import app  # noqa: E402

client = TestClient(app)

AUTH_HEADERS = {'Authorization': f'Bearer {TEST_TOKEN}'}
