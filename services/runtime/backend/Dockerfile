# Build context is the REPO ROOT, not backend/ (unlike Weave-Retrieval's/
# Weave-Knowledge's/Weave-Ingest's Dockerfiles) -- `bots/` lives at the repo
# root, as a sibling of `backend/`, so it has to be reachable from the build
# context for the COPY below. Build/run from the repo root:
#
#   docker build -f backend/Dockerfile -t weave-runtime-backend .
#   docker run --rm -p 8000:8000 --env-file .env weave-runtime-backend
#
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# requirements.txt is a plain `pip freeze` (no --generate-hashes) for now --
# see requirements.in's header -- so this installs without --require-hashes,
# unlike Weave-Ingest's Dockerfile. Switch to --require-hashes once the lock
# is regenerated with pip-compile.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/app /app/app

# `bots/` ships baked into the image so the image is runnable standalone
# with the two example bots (see BOTS_DIR's default './bots', resolved
# against WORKDIR /app -- app/core/config.py's own comment), but a real
# deployment's bot roster is operator-maintained, not part of a release
# artifact: mount a real roster over this directory at runtime, e.g.
#   docker run -v /path/to/real/bots:/app/bots ...
# -- BOTS_DIR is re-read from disk on every call (see
# app/services/botconfig.py), so an updated mount takes effect without a
# restart.
COPY bots /app/bots

# Non-root runtime user, same discipline as Weave-Retrieval's/
# Weave-Knowledge's/Weave-Ingest's Dockerfiles.
RUN groupadd --gid 1000 weave-runtime \
	&& useradd --uid 1000 --gid 1000 --home-dir /home/weave-runtime --create-home weave-runtime \
	&& chown -R weave-runtime:weave-runtime /app /home/weave-runtime

USER weave-runtime

EXPOSE 8000

# No entrypoint script: like Weave-Retrieval, this service owns no schema
# and runs no Alembic migration at startup -- it is stateless (see README's
# "Nicht-Ziele"). Starting uvicorn directly is the whole startup sequence.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
