"""
conftest.py
-----------
pytest automatically runs this file before collecting/running any
tests. We use it to set environment variables *before* app.config gets
imported anywhere - config.py reads os.environ at import time, so this
has to happen first.

This file living at the project root (next to app/ and requirements.txt)
is also what makes `from app.main import app` work inside tests/ - pytest
adds this directory to sys.path because there's no __init__.py here.
"""

import atexit
import os
import tempfile
import uuid

import pytest

# A fake-but-consistent API key so tests can authenticate against the
# require_api_key() dependency without needing a real .env file.
os.environ.setdefault("API_KEY", "test-api-key")

# Force mock mode on for the test suite, regardless of what's in a
# developer's local .env - tests should never make real network calls
# or depend on real API keys being set.
os.environ["MOCK_MODE"] = "true"

# These aren't used while MOCK_MODE is on, but config.py reads them at
# import time, so give them harmless placeholder values just in case.
os.environ.setdefault("ASSEMBLYAI_API_KEY", "test-assemblyai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")

# Point the SQLite job database at a throwaway file in the system's temp
# folder, instead of the real jobs.db a developer might have sitting in
# the project root. This means running `pytest` never reads or writes
# your real job history, and each test run starts from a clean, empty
# database (a fresh random filename every time).
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), f"mystery_shop_test_jobs_{uuid.uuid4().hex}.db")
os.environ["JOBS_DB_PATH"] = _TEST_DB_PATH


def _delete_test_db():
    """Clean up the temp database file once the test process exits."""
    try:
        os.remove(_TEST_DB_PATH)
    except OSError:
        pass  # already gone, or never got created - either way, nothing to do


atexit.register(_delete_test_db)

# A deliberately low, easy-to-verify limit for POST /shops so the
# dedicated rate-limit test (see tests/test_main.py) can trip it in a
# handful of requests instead of dozens. GET /shops/{id} gets a very
# generous limit so the *other* tests' polling loops never accidentally
# hit it - rate limiting itself is only exercised by that one dedicated
# test, not incidentally by every other test that happens to call these
# endpoints.
os.environ.setdefault("RATE_LIMIT_CREATE_SHOP", "3/minute")
os.environ.setdefault("RATE_LIMIT_GET_SHOP", "1000/minute")
os.environ.setdefault("RATE_LIMIT_SUPPORT_AGENT", "3/minute")


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """
    Resets slowapi's in-memory rate-limit counters before every test.

    Without this, tests would share state: TestClient requests all look
    like they come from the same "client," so one test's requests could
    count against another test's limit depending on execution order -
    autouse=True means this runs automatically before every test, so no
    individual test needs to remember to call it.
    """
    from app.main import limiter

    limiter.reset()
    yield
