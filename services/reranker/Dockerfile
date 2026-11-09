FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Default cache location INSIDE the container -- deliberately mounted as a
# volume at runtime (see README's "Docker starten") so the model download
# (~341 MB for the default int8 ONNX file) survives container
# restarts/recreations instead of re-fetching every time.
# RERANKER_CACHE_DIR overrides this if a deployment wants the cache
# somewhere else.
ENV RERANKER_CACHE_DIR=/data/model-cache

# requirements.txt is a plain `pip freeze` (no --generate-hashes) for now
# -- see requirements.in's header -- so this installs without
# --require-hashes, same discipline as Weave-Tools' own backend/Dockerfile.
# onnxruntime + tokenizers only, no torch (see app/services/model.py).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

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
