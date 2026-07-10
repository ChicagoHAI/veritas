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


def _docker_preflight_script(args: str) -> str:
    fns = _bash_functions("docker/run.sh", "extract_provider",
                          "bucket_spec_provider", "active_bucket_flags")
    text = (REPO_ROOT / "docker" / "run.sh").read_text(encoding="utf-8")
    m = re.search(r"^cmd_replicate\(\) \{.*?^\}", text, re.M | re.S)
    body = re.search(r"    local bucket_flag.*?^    fi$", m.group(0), re.M | re.S)
    assert body, "preflight block not found in cmd_replicate"
    return (fns
            + '\ncheck_provider_credentials() { echo "CHECKED: $1"; }\n'
            + 'run_preflight() {\n' + body.group(0) + '\n}\n'
            + 'run_preflight ' + args)


def test_replicate_preflight_checks_each_pinned_provider():
    # All active buckets pinned to openrouter: openrouter (and only it)
    # still gets a credential check. Skipping every check would leave the
    # login-based providers ungated — the pipeline's own preflight covers
    # openrouter alone.
    script = _docker_preflight_script(
        '--repo ./r --analyze-model openrouter:a --replicate-model openrouter:a '
        '--assess-model openrouter:a --verify-model openrouter:a')
    out = _run_bash(script, env={"VERITAS_MAX_ITERS": ""})
    assert out == "CHECKED: openrouter"


def test_replicate_preflight_env_only_engines_are_seen():
    # Engines set only via VERITAS_<BUCKET>_MODEL (the .env route) drive
    # the preflight the same way flags do.
    script = _docker_preflight_script('--repo ./r')
    out = _run_bash(script, env={
        "VERITAS_MAX_ITERS": "",
        "VERITAS_ANALYZE_MODEL": "openrouter:a",
        "VERITAS_REPLICATE_MODEL": "openrouter:a",
        "VERITAS_ASSESS_MODEL": "openrouter:a",
        "VERITAS_VERIFY_MODEL": "openrouter:a",
    })
    assert out == "CHECKED: openrouter"


def test_replicate_preflight_checks_global_when_any_bucket_unpinned():
    script = _docker_preflight_script('--repo ./r --analyze-model openrouter:a')
    out = _run_bash(script, env={"VERITAS_MAX_ITERS": ""})
    assert out == "CHECKED: openrouter\nCHECKED: claude"


def test_replicate_preflight_ignores_inert_buckets():
    # codegen never runs in repo-only mode and no evaluation knob is on:
    # engines pinned on those buckets must not demand credentials.
    script = _docker_preflight_script(
        '--repo ./r --codegen-model gemini:g --evaluate-model gemini:g '
        '--analyze-model openrouter:a --replicate-model openrouter:a '
        '--assess-model openrouter:a --verify-model openrouter:a')
    out = _run_bash(script, env={"VERITAS_MAX_ITERS": ""})
    assert out == "CHECKED: openrouter"


@pytest.mark.parametrize("wrapper", ["docker/run.sh", "veritas-host"])
@pytest.mark.parametrize("args,env,evaluate_active", [
    # The manager loop -- and with it every evaluate-bucket call -- engages
    # only above 1, so a single-pass run must not preflight that engine.
    # Mirrors Config.max_iters / ReplicationRunner._active_buckets.
    ("--repo ./r", {}, False),
    ("--repo ./r --max-iters 1", {}, False),
    ("--repo ./r --max-iters=1", {}, False),
    ("--repo ./r --max-iters 3", {}, True),
    ("--repo ./r --max-iters=3", {}, True),
    ("--repo ./r", {"VERITAS_MAX_ITERS": "1"}, False),
    ("--repo ./r", {"VERITAS_MAX_ITERS": "3"}, True),
    # A flag beats the env var, the way Config resolves it.
    ("--repo ./r --max-iters 1", {"VERITAS_MAX_ITERS": "3"}, False),
    # Unparseable env value: Config falls back to its default of 3 (loop on).
    ("--repo ./r", {"VERITAS_MAX_ITERS": "abc"}, True),
    # The explicit evaluation knobs activate the bucket regardless.
    ("--repo ./r --evaluate", {}, True),
    ("--repo ./r --check-citations", {}, True),
])
def test_active_bucket_flags_gates_evaluate_on_the_loop_engaging(
        wrapper, args, env, evaluate_active):
    fns = _bash_functions(wrapper, "active_bucket_flags")
    out = _run_bash(fns + f"\nactive_bucket_flags {args}",
                    env={"VERITAS_MAX_ITERS": "", **env})
    assert ("--evaluate-model" in out.split()) is evaluate_active


def test_host_preflight_sees_env_file_engines(tmp_path):
    # veritas-host loads .env before the bucket scan, so an engine pinned
    # only in .env drives the tool preflight (openrouter -> opencode)
    # instead of demanding the unused global provider's CLI.
    fns = _bash_functions("veritas-host", "extract_provider",
                          "bucket_spec_provider", "active_bucket_flags",
                          "load_env_file", "require_provider_tools")
    (tmp_path / ".env").write_text(
        "\n".join(f"VERITAS_{b}_MODEL=openrouter:m" for b in
                  ("ANALYZE", "REPLICATE", "ASSESS", "VERIFY")) + "\n",
        encoding="utf-8")
    text = (REPO_ROOT / "veritas-host").read_text(encoding="utf-8")
    m = re.search(r"^cmd_replicate\(\) \{.*?^\}", text, re.M | re.S)
    body = re.search(r"    load_env_file.*?^    fi$", m.group(0), re.M | re.S)
    assert body, "env-aware preflight block not found in veritas-host"
    script = (f'VERITAS_REPO="{tmp_path.as_posix()}"\n' + fns
              + '\nrequire_tools() { echo "TOOLS: $*"; }\n'
              + 'run_preflight() {\n' + body.group(0) + '\n}\n'
              + 'run_preflight --repo ./r')
    out = _run_bash(script, env={"VERITAS_MAX_ITERS": ""})
    assert "opencode" in out
    assert "claude" not in out


def test_get_env_value_strips_carriage_return(tmp_path):
    # Discriminating on Linux only: MSYS grep strips the CR itself, which
    # is exactly what masked a broken strip here before.
    fns = _bash_functions("docker/run.sh", "get_env_value")
    (tmp_path / ".env").write_bytes(b"MYKEY=abc123\r\n")
    script = (f'PROJECT_ROOT="{tmp_path.as_posix()}"\n' + fns
              + '\nv=$(get_env_value MYKEY)\n'
              + '[ "$v" = "abc123" ] && echo OK || printf "BAD(%s)" "$v"')
    assert _run_bash(script) == "OK"


def _shell_var_list(name: str) -> set:
    text = (REPO_ROOT / "docker" / "run.sh").read_text(encoding="utf-8")
    m = re.search(rf'^{name}="([^"]*)"', text, re.M)
    assert m, f"{name} not found in docker/run.sh"
    return set(m.group(1).split())


def test_shell_var_lists_match_python():
    # The wrapper's forward lists and the Python strip/resolve tables are
    # the same contract in two languages; drift is silent and asymmetric
    # (a var added only to the shell is forwarded but never scoped).
    from veritas.core.config import Config, PROVIDER_NATIVE_MODEL_VARS
    from veritas.core.runner import PROVIDER_AUTH_VARS

    python_auth = {var for vars_ in PROVIDER_AUTH_VARS.values() for var in vars_}
    assert _shell_var_list("PROVIDER_AUTH_VARS") == python_auth

    python_engine = set(Config._MODEL_ENV_VARS.values()) | {
        "VERITAS_CITATION_FAITHFULNESS_SCOPE"}
    assert _shell_var_list("FORWARDED_ENGINE_VARS") == python_engine

    python_config = set(PROVIDER_NATIVE_MODEL_VARS.values()) | {
        "VERITAS_CONTACT_EMAIL"}
    assert _shell_var_list("FORWARDED_CONFIG_VARS") == python_config


def test_docker_wrapper_loads_max_iters_from_env_file(tmp_path):
    # The host wrapper loads .env before the bucket scan, so a .env-set
    # VERITAS_MAX_ITERS reaches active_bucket_flags there; the docker
    # wrapper must agree, or the evaluate engine's preflight depends on
    # which runtime you use.
    fns = _bash_functions("docker/run.sh", "get_env_value",
                          "load_provider_auth_env")
    (tmp_path / ".env").write_text("VERITAS_MAX_ITERS=3\n", encoding="utf-8")
    lists = 'eval "$(grep -E \'^(PROVIDER_AUTH_VARS|FORWARDED_ENGINE_VARS|FORWARDED_CONFIG_VARS|PREFLIGHT_ONLY_VARS)=\' "$RUN_SH")"'
    script = (f'PROJECT_ROOT="{tmp_path.as_posix()}"\n'
              f'RUN_SH="{(REPO_ROOT / "docker" / "run.sh").as_posix()}"\n'
              + lists + "\n" + fns
              + '\nload_provider_auth_env\nprintf "%s" "$VERITAS_MAX_ITERS"')
    assert _run_bash(script, env={"VERITAS_MAX_ITERS": ""}) == "3"
