"""Unit tests for VERITAS_* config externalization (Phase 0).

Covers the typed env helpers' resolution (default when unset, env override when
set, bad value -> default + no crash) and that the migrated tunables
(GradingTolerances, TIER_WEIGHTS, Config.max_iters / timeouts) keep their code
defaults when no VERITAS_* var is set.
"""

import importlib

import pytest

from veritas.core import config_env
from veritas.core.config_env import (
    _env_bool,
    _env_float,
    _env_int,
    _env_opt_int,
    _env_str,
)


@pytest.fixture(autouse=True)
def _clean_veritas_env(monkeypatch):
    """Strip any VERITAS_* tunables and mark .env as already-loaded so the
    helpers read a clean os.environ and never auto-load a stray .env."""
    monkeypatch.setattr(config_env, "_DOTENV_LOADED", True)
    for key in list(__import__("os").environ):
        if key.startswith("VERITAS_") and key != "VERITAS_ENV_FILE_KEYS":
            monkeypatch.delenv(key, raising=False)


# -- typed helpers: default / override / bad-value ------------------------

def test_env_int_default_when_unset():
    assert _env_int("VERITAS_TEST_INT", 3) == 3


def test_env_int_override(monkeypatch):
    monkeypatch.setenv("VERITAS_TEST_INT", "7")
    assert _env_int("VERITAS_TEST_INT", 3) == 7


def test_env_int_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("VERITAS_TEST_INT", "not-a-number")
    assert _env_int("VERITAS_TEST_INT", 3) == 3  # no crash


def test_env_int_empty_falls_back(monkeypatch):
    monkeypatch.setenv("VERITAS_TEST_INT", "   ")
    assert _env_int("VERITAS_TEST_INT", 3) == 3


def test_env_float_default_and_override(monkeypatch):
    assert _env_float("VERITAS_TEST_FLOAT", 0.05) == 0.05
    monkeypatch.setenv("VERITAS_TEST_FLOAT", "0.10")
    assert _env_float("VERITAS_TEST_FLOAT", 0.05) == 0.10


def test_env_float_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("VERITAS_TEST_FLOAT", "abc")
    assert _env_float("VERITAS_TEST_FLOAT", 0.05) == 0.05


def test_env_str_default_and_override(monkeypatch):
    assert _env_str("VERITAS_TEST_STR", "x") == "x"
    monkeypatch.setenv("VERITAS_TEST_STR", "y")
    assert _env_str("VERITAS_TEST_STR", "x") == "y"


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_env_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("VERITAS_TEST_BOOL", raw)
    assert _env_bool("VERITAS_TEST_BOOL", not expected) is expected


def test_env_bool_default_and_bad(monkeypatch):
    assert _env_bool("VERITAS_TEST_BOOL", True) is True
    monkeypatch.setenv("VERITAS_TEST_BOOL", "maybe")
    assert _env_bool("VERITAS_TEST_BOOL", True) is True  # bad -> default


def test_env_opt_int_none_default_and_override(monkeypatch):
    assert _env_opt_int("VERITAS_TEST_OPT", None) is None
    monkeypatch.setenv("VERITAS_TEST_OPT", "600")
    assert _env_opt_int("VERITAS_TEST_OPT", None) == 600


def test_env_opt_int_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("VERITAS_TEST_OPT", "later")
    assert _env_opt_int("VERITAS_TEST_OPT", None) is None


# -- migrated tunables: defaults unchanged when unset ---------------------

def test_grading_tolerances_defaults_unchanged():
    from veritas.core.grading import GradingTolerances
    t = GradingTolerances()
    assert t.match_rel == 0.05
    assert t.partial_rel == 0.30
    assert t.sigma_match == 1.0
    assert t.sigma_partial == 2.0
    assert t.near_zero_abs == 1e-9
    assert t.match_abs == 1e-6
    assert t.range_overlap_match == 0.80


def test_grading_tolerances_env_override(monkeypatch):
    monkeypatch.setenv("VERITAS_GRADE_MATCH_REL", "0.12")
    monkeypatch.setenv("VERITAS_GRADE_SIGMA_PARTIAL", "3.0")
    from veritas.core.grading import GradingTolerances
    t = GradingTolerances()
    assert t.match_rel == 0.12
    assert t.sigma_partial == 3.0
    # untouched fields keep their defaults
    assert t.partial_rel == 0.30


def test_tier_weights_defaults_unchanged():
    # TIER_WEIGHTS is a module constant evaluated at import; re-import under a
    # clean env to confirm the code defaults.
    import veritas.core.models.paper_claims as pc
    importlib.reload(pc)
    assert pc.TIER_WEIGHTS == {"headline": 3.0, "supporting": 2.0, "setup": 1.0}


def test_tier_weights_env_override(monkeypatch):
    monkeypatch.setattr(config_env, "_DOTENV_LOADED", True)
    monkeypatch.setenv("VERITAS_TIER_WEIGHT_HEADLINE", "5")
    monkeypatch.setenv("VERITAS_TIER_WEIGHT_SETUP", "0.5")
    import veritas.core.models.paper_claims as pc
    importlib.reload(pc)
    assert pc.TIER_WEIGHTS["headline"] == 5.0
    assert pc.TIER_WEIGHTS["setup"] == 0.5
    assert pc.TIER_WEIGHTS["supporting"] == 2.0
    importlib.reload(pc)  # restore defaults for other tests


def test_config_max_iters_default_and_override(tmp_path, monkeypatch):
    from veritas.core.config import Config
    c = Config(repo_path=tmp_path, output_dir=tmp_path / "out")
    assert c.max_iters == 3
    monkeypatch.setenv("VERITAS_MAX_ITERS", "1")
    c2 = Config(repo_path=tmp_path, output_dir=tmp_path / "out")
    assert c2.max_iters == 1


def test_config_timeout_cli_beats_env(tmp_path, monkeypatch):
    from veritas.core.config import Config
    monkeypatch.setenv("VERITAS_ANALYZE_TIMEOUT", "900")
    # CLI flag absent -> env default applies
    c = Config(repo_path=tmp_path, output_dir=tmp_path / "out")
    assert c.analyze_timeout == 900
    # CLI flag present (explicit) -> wins over env
    c2 = Config(repo_path=tmp_path, output_dir=tmp_path / "out", analyze_timeout=120)
    assert c2.analyze_timeout == 120


def test_config_timeout_default_none(tmp_path):
    from veritas.core.config import Config
    c = Config(repo_path=tmp_path, output_dir=tmp_path / "out")
    assert c.analyze_timeout is None
    assert c.verify_timeout is None
