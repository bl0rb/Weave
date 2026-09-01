#!/usr/bin/env python3
"""Exports the backend's OpenAPI schema to contracts/openapi.json.

This is the frozen HTTP contract Weave-API and Weave-Knowledge program
against instead of importing backend code directly (see contracts/README.md).
Regenerate it after any route/schema change:

    .venv/bin/python scripts/export_openapi.py

and commit the resulting contracts/openapi.json alongside the change so the
diff is reviewable like any other contract change.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = SERVICE_ROOT / 'backend'


def _repo_root() -> Path:
    """The monorepo root, found by looking for the shared `contracts/`.

    A fixed number of `.parent` hops used to land on this service's own
    directory; after the move into `services/ingest/` that wrote the export
    to a path nobody reads, without any error to show for it.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / 'contracts' / 'frontmatter.schema.json').exists():
            return candidate
    raise RuntimeError('monorepo root with contracts/ not found')


OUTPUT_PATH = _repo_root() / 'contracts' / 'openapi.json'

# `app.main` (and everything it pulls in transitively -- app.core.config,
# app.database.session, the routers, ...) lives under backend/ as a plain
# `app.*` package, not `backend.app.*`. backend/pytest.ini's `pythonpath = .`
# makes that work for the test suite because pytest is run with cwd=backend/
# (see the run instructions in backend/tests). This script is invoked from
# the repo root instead, so backend/ has to go on sys.path by hand before
# the `from app.main import app` below can resolve.
sys.path.insert(0, str(BACKEND_DIR))

# backend/tests/conftest.py imports `app.main.app` directly with zero env
# vars set beforehand and that just works: every Settings field in
# app/core/config.py has a default, database_url falls back to a local
# sqlite file when unset, and secret_key auto-resolves to a fixed
# dev-only value whenever database_url is sqlite (see _resolve_secret_key).
# Nothing on the app.main import path -- routers, services, models -- talks
# to a database, Redis, or an external API at import time: SQLAlchemy's
# create_engine() doesn't connect until first use, and the module-level
# `_redis_client` in app/services/security.py starts out as plain `None`.
#
# So strictly nothing needs to be set here. We pin these two anyway,
# overriding whatever the invoking shell happens to export (e.g. a real
# DATABASE_URL for docker-compose), so the exported contract is reproducible
# regardless of the environment the script runs in -- not a reflection of
# whichever backing store a given machine is currently configured for. Both
# are harmless placeholders in the same spirit as config.py's own
# _DEV_ONLY_SQLITE_SECRET_KEY: a sqlite DB nothing ever queries, and a
# secret key that only signs cookies no request in this script sends.
#
# Must be a *file* sqlite URL, not 'sqlite:///:memory:': SQLAlchemy only
# picks QueuePool (which understands pool_size/max_overflow) for file-based
# sqlite. In-memory sqlite gets SingletonThreadPool instead, and
# app/workers/log_capture.py -- pulled in transitively via
# app.api.routes -> app.workers.celery_app -- creates its module-level
# engine with pool_size=2, max_overflow=2, which SingletonThreadPool
# rejects outright. Nothing here ever actually connects (create_engine() is
# lazy and app.openapi() touches no database), so the path just needs to
# resolve, not exist; tempfile keeps it out of the repo tree.
os.environ['DATABASE_URL'] = f'sqlite:///{Path(tempfile.gettempdir()) / "weave-ingest-openapi-export.db"}'
os.environ['SECRET_KEY'] = 'dummy-openapi-export-secret-key-not-for-runtime-use'

from app.main import app  # noqa: E402 -- must follow the sys.path/env setup above


def main() -> None:
    schema = app.openapi()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys + indent=2 + a fixed trailing newline: byte-for-byte stable
    # output, so `git diff` on contracts/openapi.json only ever shows an
    # actual contract change, never key-ordering noise from FastAPI's
    # internal schema construction (which otherwise follows
    # router-registration order in app/main.py).
    serialized = json.dumps(schema, sort_keys=True, indent=2) + '\n'
    OUTPUT_PATH.write_text(serialized, encoding='utf-8')

    path_count = len(schema.get('paths', {}))
    print(f'Wrote {OUTPUT_PATH.relative_to(_repo_root())} ({path_count} paths)')


if __name__ == '__main__':
    main()
