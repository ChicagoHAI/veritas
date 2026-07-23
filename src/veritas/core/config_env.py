"""Typed ``VERITAS_*`` environment-variable helpers.

Design principle: *no hardcoded configs*. Tunables a user rarely changes (retry
caps, grading tolerances, tier weights) must be overridable without touching
code.

The design is deliberately simple — two sources over the code default, both
already-familiar, with the highest-wins resolution:

    CLI flag (where one exists) → ``VERITAS_*`` env var → code default

These helpers cover the env-var tier. Each reads ``os.environ``, falls back to
the supplied default when the var is unset, and **tolerates bad values** by
logging a warning and using the default (never crashing on a typo in ``.env``).

``.env`` loading: the ``./veritas`` / ``./veritas-host`` wrappers already source
``.env`` into the process environment, so ``VERITAS_*`` vars defined there are
visible to ``os.environ`` in both docker and host runs. For a direct CLI run
(``veritas ...`` without a wrapper) ``load_dotenv_once()`` performs a minimal,
no-override load so the same vars work. API-key handling is unchanged: keys are
still scoped via ``VERITAS_ENV_FILE_KEYS`` in ``runner.py``.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Set once we have attempted to load .env (idempotent guard).
_DOTENV_LOADED = False


def load_dotenv_once() -> None:
    """Best-effort, idempotent load of the project ``.env`` into ``os.environ``.

    Only fills vars that are *not already set* (``override=False``), so the
    wrappers' own ``set -a; . .env`` (and any real shell exports) always win.
    This exists so ``VERITAS_*`` vars in ``.env`` also work for a direct
    ``veritas ...`` CLI invocation that bypasses the bash wrappers. It does not
    change how replication API keys are handled.

    Silently does nothing if python-dotenv is unavailable or no ``.env`` is
    found; missing config is never fatal here.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    # VERITAS_REPO is exported by the wrappers; otherwise walk up from CWD.
    candidates = []
    repo_env = os.environ.get("VERITAS_REPO")
    if repo_env:
        candidates.append(Path(repo_env) / ".env")
    cwd = Path.cwd()
    candidates.extend(p / ".env" for p in (cwd, *cwd.parents))

    dotenv_path = next((p for p in candidates if p.is_file()), None)
    if dotenv_path is None:
        return

    try:
        from dotenv import load_dotenv
    except Exception:  # python-dotenv missing — env vars still work if exported
        logger.debug("python-dotenv unavailable; skipping .env auto-load")
        return

    load_dotenv(dotenv_path=dotenv_path, override=False)


def _env_str(name: str, default: str) -> str:
    """Return ``os.environ[name]`` if set (and non-empty), else ``default``."""
    load_dotenv_once()
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


def _env_int(name: str, default: int) -> int:
    """Typed int env read; bad value -> warn + default (no crash)."""
    load_dotenv_once()
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        logger.warning(
            "Invalid int for %s=%r; using default %r", name, raw, default
        )
        return default


def _env_float(name: str, default: float) -> float:
    """Typed float env read; bad value -> warn + default (no crash)."""
    load_dotenv_once()
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except (ValueError, TypeError):
        logger.warning(
            "Invalid float for %s=%r; using default %r", name, raw, default
        )
        return default


_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}


def _env_bool(name: str, default: bool) -> bool:
    """Typed bool env read; bad value -> warn + default (no crash)."""
    load_dotenv_once()
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    norm = raw.strip().lower()
    if norm in _TRUE:
        return True
    if norm in _FALSE:
        return False
    logger.warning(
        "Invalid bool for %s=%r; using default %r", name, raw, default
    )
    return default


def _env_opt_int(name: str, default):
    """Optional-int env read used for timeouts (default may be ``None``).

    ``VERITAS_*_TIMEOUT`` unset -> ``default`` (typically ``None`` = no timeout).
    A bad value warns and falls back to ``default``.
    """
    load_dotenv_once()
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except (ValueError, TypeError):
        logger.warning(
            "Invalid int for %s=%r; using default %r", name, raw, default
        )
        return default
