"""Config.claim_scope: CLI -> VERITAS_CLAIM_SCOPE -> "main" resolution and validation."""

import pytest

from veritas.core import config_env
from veritas.core.config import Config


def _cfg(tmp_path, **kwargs):
    return Config(repo_path=tmp_path, output_dir=tmp_path / "out", **kwargs)


def test_default_scope_is_main(tmp_path, monkeypatch):
    monkeypatch.setattr(config_env, "_DOTENV_LOADED", True)
    monkeypatch.delenv("VERITAS_CLAIM_SCOPE", raising=False)
    assert _cfg(tmp_path).claim_scope == "main"


def test_explicit_full(tmp_path):
    assert _cfg(tmp_path, claim_scope="full").claim_scope == "full"


def test_numeric_scope_accepted(tmp_path):
    assert _cfg(tmp_path, claim_scope="2").claim_scope == "2"


def test_scope_normalized(tmp_path):
    assert _cfg(tmp_path, claim_scope="  MAIN ").claim_scope == "main"


@pytest.mark.parametrize("bad", ["toy", "0", "-1", "1.5", ""])
def test_invalid_scope_rejected(tmp_path, bad, monkeypatch):
    monkeypatch.setattr(config_env, "_DOTENV_LOADED", True)
    monkeypatch.delenv("VERITAS_CLAIM_SCOPE", raising=False)
    if bad == "":
        # Empty string is falsy config noise -> resolves to the default.
        assert _cfg(tmp_path, claim_scope=bad).claim_scope == "main"
    else:
        with pytest.raises(ValueError, match="claim_scope"):
            _cfg(tmp_path, claim_scope=bad)


def test_env_var_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(config_env, "_DOTENV_LOADED", True)
    monkeypatch.setenv("VERITAS_CLAIM_SCOPE", "full")
    assert _cfg(tmp_path).claim_scope == "full"


def test_cli_beats_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_env, "_DOTENV_LOADED", True)
    monkeypatch.setenv("VERITAS_CLAIM_SCOPE", "full")
    assert _cfg(tmp_path, claim_scope="1").claim_scope == "1"
