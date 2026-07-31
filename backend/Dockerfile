# Built from the repository root (docker-compose sets context: .), so every path
# here is repo-relative. Copying backend/Pipfile as ./Pipfile is what a
# backend-relative path would silently get wrong.
FROM python:3.11-slim AS base

WORKDIR /app

# libpq is needed by psycopg, which the LangGraph checkpointer uses.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pipenv

# Both files: --deploy refuses to run without a lock that matches the Pipfile.
COPY backend/Pipfile backend/Pipfile.lock ./

# --system installs into the image's own Python rather than a virtualenv, so the
# sandbox subprocess (which runs `python -m external.sandbox_runner`) resolves
# the same interpreter and packages as the server.
RUN pipenv install --deploy --system

COPY backend/ ./

# Everything above is shared. The two leaves below differ only in what they run,
# so the dependency install and the source copy are built and cached once.

# ---------------------------------------------------------------------- api
FROM base AS api

EXPOSE 8001

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8001"]

# ----------------------------------------------------------------- consumer
# No port and no ASGI server: a consumer polls a queue and serves nothing. Which
# loop it runs is a runtime choice — compose overrides `command` per service, so
# one image backs the orchestrator and the task worker both.
FROM base AS consumer

CMD ["python", "-m", "workers.orchestrator"]
