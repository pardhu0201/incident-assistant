"""Test fixtures.

The app builds its settings/engine at import time, so the test database and
a fresh OTel provider must be set up before any `app` module is imported.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.gettempdir()) / "incident_assistant_test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["ANTHROPIC_API_KEY"] = ""  # force deterministic demo mode in CI
os.environ["SEED_ON_STARTUP"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["EMBEDDING_PROVIDER"] = "hashing"
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""


@pytest.fixture(scope="session", autouse=True)
def _database():
    if TEST_DB.exists():
        TEST_DB.unlink()

    from app.db.init_db import initialise
    from app.telemetry import configure_telemetry

    initialise(seed=True)
    configure_telemetry()
    yield
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(TEST_DB) + suffix)
        if candidate.exists():
            with contextlib.suppress(PermissionError):
                candidate.unlink()


@pytest.fixture
def db():
    from app.db.base import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
