FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# requirements.txt is a plain `pip freeze` (no --generate-hashes) for now --
# see requirements.in's header -- so this installs without --require-hashes,
# unlike Weave-Ingest's Dockerfile. Switch to --require-hashes once the lock
# is regenerated with pip-compile.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

# Non-root runtime user, same discipline as Weave-Knowledge's/Weave-Ingest's
# Dockerfiles.
RUN groupadd --gid 1000 weave-retrieval \
	&& useradd --uid 1000 --gid 1000 --home-dir /home/weave-retrieval --create-home weave-retrieval \
	&& chown -R weave-retrieval:weave-retrieval /app /home/weave-retrieval

USER weave-retrieval

EXPOSE 8000

# No entrypoint script: unlike Weave-Knowledge and Weave-Ingest, this service
# owns no schema and runs no Alembic migration at startup -- it is the
# read-only side of the shared chunk-store contract (see
# contracts/chunk-store.md), never the writer. Starting
# uvicorn directly is the whole startup sequence.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
