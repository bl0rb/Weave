FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# requirements.txt is a plain `pip freeze` (no --generate-hashes) for now --
# see requirements.in's header -- so this installs without --require-hashes,
# same discipline as Weave-Retrieval's own Dockerfile.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

# Non-root runtime user, same discipline as every other Weave service's
# Dockerfile.
RUN groupadd --gid 1000 weave-tools \
	&& useradd --uid 1000 --gid 1000 --home-dir /home/weave-tools --create-home weave-tools \
	&& chown -R weave-tools:weave-tools /app /home/weave-tools

USER weave-tools

EXPOSE 8000

# This image bundles TWO independent ASGI apps -- see app/mcp_server.py's
# own module docstring for why they are two separate uvicorn processes
# rather than one: the REST mirror (app/main.py, /health + /api/v1/tools/*)
# and the MCP server (app/mcp_server.py, streamable-HTTP transport at
# /mcp). Both are stateless and read Weave-API/Weave-Retrieval fresh on
# every call (app/services/scope.py), so running either one alone, or both
# side by side as two containers from this SAME image on different ports,
# is equally correct.
#
# CMD below starts the REST mirror. Start the MCP server instead (e.g. a
# second container/replica from this same image) by overriding CMD:
#
#   docker run ... weave-tools \
#     uvicorn app.mcp_server:mcp_app --host 0.0.0.0 --port 8000
#
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
