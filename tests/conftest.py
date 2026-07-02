"""Shared fixtures: isolate tests from the developer's own .env settings."""

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch):
    for key in list(os.environ):
        if (key.startswith("VERITAS_") and key.endswith("_MODEL")) or key == "VERITAS_MODEL":
            monkeypatch.delenv(key, raising=False)
