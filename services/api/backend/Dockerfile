FROM python:3.14-slim

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
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
COPY entrypoint.sh /app/entrypoint.sh
RUN tr -d '\r' < /app/entrypoint.sh > /app/entrypoint.sh.tmp && mv /app/entrypoint.sh.tmp /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Non-root runtime user, same discipline as every other Weave service's
# Dockerfile.
RUN groupadd --gid 1000 weave-api \
	&& useradd --uid 1000 --gid 1000 --home-dir /home/weave-api --create-home weave-api \
	&& chown -R weave-api:weave-api /app /home/weave-api

USER weave-api

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
