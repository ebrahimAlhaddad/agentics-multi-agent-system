"""Test environment.

Settings are validated at import time, and any module that touches logger
pulls them in transitively. These defaults let the suite run with no environment
configured at all — no .env, no containers, no credentials.

Unit tests never open a connection, so the values only have to exist. They are
set to match docker-compose rather than to dummies, so that tests/integration/
can reach a real Postgres when one happens to be running and skip cleanly when it
is not. Settings is a module-level singleton evaluated on first import, so these
have to be in place before anything under app/ is imported — which is why they
live in the root conftest rather than in a fixture.
"""

import os

_DEFAULTS = {
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "postgres",
    "POSTGRES_DB": "agentics",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "LLM_MODEL": "gpt-4o",
    "MAX_TOKENS": "8000",
    "BACKEND_HOST": "0.0.0.0",
    "BACKEND_PORT": "8001",
    "DEFAULT_AWS_REGION": "us-west-2",
}

for _key, _value in _DEFAULTS.items():
    os.environ.setdefault(_key, _value)
