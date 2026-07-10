"""Static invariants on the bash wrappers that bash -n cannot catch."""

import os
import re
import shutil
import subprocess

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_BACKSLASH = chr(92)


def _bash_functions(wrapper: str, *functions: str) -> str:
    """Extract named function definitions from a wrapper for stub testing."""
    text = (REPO_ROOT / wrapper).read_text(encoding="utf-8")
    chunks = []
    for fn in functions:
        m = re.search(rf"^{fn}\(\) \{{.*?^\}}", text, re.M | re.S)
        assert m, f"{fn} not found in {wrapper}"
        chunks.append(m.group(0))
    return "\n".join(chunks)


def _find_bash():
    """A real bash: on Windows, System32's bash.exe is the WSL launcher, so
    prefer the one shipped with Git for Windows."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent
        for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
            candidate = root / rel
            if candidate.exists():
                return str(candidate)
    return None


def _run_bash(script: str, env: dict = None) -> str:
    bash = _find_bash()
    if bash is None:
        pytest.skip("bash not available")
    # Overlay on the (conftest-scrubbed) environment: bash needs the base
    # vars of its platform, and the model vars under test are controlled.
    full_env = dict(os.environ)
    full_env.update(env or {})
    result = subprocess.run(
        [bash, "-c", script], capture_output=True, text=True,
        env=full_env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_eval_strings_carry_no_literal_backslash_n():
    # A literal backslash-n inside an eval'd docker command line is
    # syntactically valid bash but injects a stray token at run time
    # (docker once parsed one as the image name). Continuations must be
    # real backslash-newline.
    pattern = re.compile(re.escape(_BACKSLASH + "n") + "[ \t]+[$]")
    for wrapper in ("docker/run.sh", "veritas-host"):
        text = (REPO_ROOT / wrapper).read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is None, (
            f"literal backslash-n token in {wrapper} at offset {match.start()}"
        )


@pytest.mark.parametrize("wrapper", ["docker/run.sh", "veritas-host"])
def test_bucket_provider_honors_env_var(wrapper):
    # The preflight must see the same engine the Python layer resolves: a
    # VERITAS_<BUCKET>_MODEL set only in the environment (e.g. via .env)
    # pins the bucket's provider just like the flag does.
    fns = _bash_functions(wrapper, "extract_provider", "bucket_spec_provider",
                          "extract_bucket_provider")
    script = fns + '\nextract_bucket_provider --evaluate-model ./run'
    out = _run_bash(script, env={"VERITAS_EVALUATE_MODEL": "openrouter:openai/gpt-5.5"})
    assert out == "openrouter"


@pytest.mark.parametrize("wrapper", ["docker/run.sh", "veritas-host"])
def test_bucket_provider_flag_beats_env_var(wrapper):
    fns = _bash_functions(wrapper, "extract_provider", "bucket_spec_provider",
                          "extract_bucket_provider")
    script = fns + '\nextract_bucket_provider --evaluate-model ./run --evaluate-model codex:o3'
    out = _run_bash(script, env={"VERITAS_EVALUATE_MODEL": "openrouter:openai/gpt-5.5"})
    assert out == "codex"


@pytest.mark.parametrize("wrapper", ["docker/run.sh", "veritas-host"])
def test_bucket_provider_bare_spec_falls_back_to_global(wrapper):
    fns = _bash_functions(wrapper, "extract_provider", "bucket_spec_provider",
                          "extract_bucket_provider")
    script = fns + '\nextract_bucket_provider --evaluate-model ./run --evaluate-model claude-opus-4-8 --provider gemini'
    out = _run_bash(script)
    assert out == "gemini"


def test_replicate_preflight_skips_global_when_all_buckets_pinned():
    # A run whose every bucket is pinned to its own provider never invokes
    # the global one; the wrapper must not demand its credentials.
    fns = _bash_functions("docker/run.sh", "extract_provider",
                          "bucket_spec_provider")
    text = (REPO_ROOT / "docker" / "run.sh").read_text(encoding="utf-8")
    m = re.search(r"^cmd_replicate\(\) \{.*?^\}", text, re.M | re.S)
    body = re.search(r"    local bucket_flag.*?^    fi$", m.group(0), re.M | re.S)
    assert body, "preflight block not found in cmd_replicate"
    script = (fns
              + '\ncheck_provider_credentials() { echo "CHECKED: $1"; }\n'
              + 'run_preflight() {\n' + body.group(0) + '\n}\n'
              + 'run_preflight --analyze-model openrouter:a --codegen-model openrouter:a '
              + '--replicate-model openrouter:a --assess-model openrouter:a '
              + '--verify-model openrouter:a --evaluate-model openrouter:a\n'
              + 'echo "---"\n'
              + 'run_preflight --analyze-model openrouter:a')
    out = _run_bash(script)
    assert out == "---\nCHECKED: claude"
