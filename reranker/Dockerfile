FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Default cache location INSIDE the container -- deliberately mounted as a
# volume at runtime (see README's "Docker starten") so the ~2.3 GB model
# download survives container restarts/recreations instead of re-fetching
# every time. RERANKER_CACHE_DIR overrides this if a deployment wants the
# cache somewhere else.
ENV RERANKER_CACHE_DIR=/data/model-cache

# requirements.txt is a plain `pip freeze` (no --generate-hashes) for now
# -- see requirements.in's header -- so this installs without
# --require-hashes, same discipline as Weave-Tools' own backend/Dockerfile.
#
# torch is installed FIRST, explicitly, from the CPU wheel index --
# otherwise a plain `pip install -r requirements.txt` resolves the `torch`
# line against default PyPI, which serves the CUDA build (several GB
# larger, entirely wasted on a CPU-only deployment). Installing it first
# means the later `-r requirements.txt` pass finds torch==2.13.0 already
# satisfied and never re-resolves it against the wrong index. See
# README.md's "Imagegroesse" for the actual size difference this makes.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 \
	&& pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

# Non-root runtime user, same discipline as every other Weave service's
# Dockerfile.
RUN groupadd --gid 1000 weave-reranker \
	&& useradd --uid 1000 --gid 1000 --home-dir /home/weave-reranker --create-home weave-reranker \
	&& mkdir -p /data/model-cache \
	&& chown -R weave-reranker:weave-reranker /app /home/weave-reranker /data

USER weave-reranker

EXPOSE 8000

# GET /health answers immediately even before the model has finished
# loading (app/main.py) -- an orchestrator's own startup probe should poll
# /health for "warm": true before routing real traffic here, not rely on
# container-start latency to mean "ready".
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
