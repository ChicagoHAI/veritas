"""Shared fixtures: isolate tests from the developer's own .env settings."""

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch):
    # Also stop config_env from re-loading the developer's .env mid-test,
    # which would resurrect the deleted vars on first Config construction.
    monkeypatch.setattr("veritas.core.config_env._DOTENV_LOADED", True)
    for key in list(os.environ):
        if (key.startswith("VERITAS_") and key.endswith("_MODEL")) or key == "VERITAS_MODEL":
            monkeypatch.delenv(key, raising=False)
