"""Shared fixtures for the contracts service tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from contracts import runner


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    """A Flask test client backed by a throwaway per-test data directory.

    Points the app's ``DATA_DIR`` at a tmp dir and resets the cached store so no
    test touches the live ``data/.apps/contracts`` state, Dropbox, or Notion (neither
    is configured under the tmp dir, so their calls no-op).
    """
    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(runner, "_store", None)
    runner.app.config.update(TESTING=True)
    with runner.app.test_client() as test_client:
        yield test_client
