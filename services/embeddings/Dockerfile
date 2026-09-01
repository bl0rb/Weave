FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# requirements.txt is a plain `pip freeze` (no --generate-hashes) for now --
# see requirements.in's header -- so this installs without --require-hashes,
# same as Weave-Retrieval's/Weave-Knowledge's own Dockerfiles.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

# Non-root runtime user, same discipline as every other Weave service's
# Dockerfile in this org (Weave-Ingest, Weave-Knowledge, Weave-Retrieval).
RUN groupadd --gid 1000 weave-embeddings \
	&& useradd --uid 1000 --gid 1000 --home-dir /home/weave-embeddings --create-home weave-embeddings

# Model weights are NEVER baked into this image -- they're downloaded on
# first start (see README's "Erster Start") into EMBEDDINGS_CACHE_DIR,
# which defaults to /models here specifically so a real deployment can
# mount a volume at that one, well-known path and have the ~470MB ONNX
# download survive container recreation instead of re-fetching it every
# time. A deployment that overrides EMBEDDINGS_CACHE_DIR to somewhere else
# should mount its volume there instead; this default is just a sane
# out-of-the-box path to mount.
ENV EMBEDDINGS_CACHE_DIR=/models
RUN mkdir -p /models && chown -R weave-embeddings:weave-embeddings /app /models /home/weave-embeddings
VOLUME ["/models"]

USER weave-embeddings

EXPOSE 8000

# /health always answers 200 (see app/main.py's own docstring) -- even
# while `warm=false` during the first-run model download, which can take
# a while. This HEALTHCHECK therefore only ever tests liveness ("does the
# process answer HTTP at all"), never readiness, so it can never itself
# trigger a restart loop during a slow first download; --start-period is
# generous purely as additional insurance against a container runtime that
# treats the pre-start-period grace differently. A caller that needs
# readiness (not just liveness) should poll GET /health's own `warm`/
# `status` fields instead, e.g. before routing real traffic to a fresh
# replica.
HEALTHCHECK --interval=10s --timeout=5s --start-period=300s --retries=6 \
	CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# No entrypoint script: this service owns no schema/migration and reads no
# external database -- starting uvicorn directly is the whole startup
# sequence, same as Weave-Retrieval's own Dockerfile.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
