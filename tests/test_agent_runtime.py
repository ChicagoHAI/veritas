"""Agent execution contracts, exercised without provider CLIs or API calls."""

import io
import json
import os
import sys
from pathlib import Path

import pytest

from veritas.core.config import Config
from veritas.core.runner import ReplicationRunner
from veritas.llm import (
    SUPPORTED_PROVIDERS,
    AgentCapabilities,
    AgentRequest,
    AgentResult,
    SessionRequest,
    create_agent_backend,
)
from veritas.llm import cli as cli_mod
from veritas.llm.cli import CliAgentBackend, CliSpec


def request_for(tmp_path, **overrides):
    fields = {
        "prompt": "Read the evidence and write the verdict.",
        "working_dir": tmp_path,
        "transcript_path": tmp_path / "logs" / "transcript.jsonl",
        "timeout": None,
    }
    fields.update(overrides)
    return AgentRequest(**fields)


def python_backend(monkeypatch, script):
    monkeypatch.setattr(cli_mod, "_resolve_cli", lambda name: sys.executable)
    return CliAgentBackend(
        CliSpec(
            name="test-python",
            command=("python", "-u", "-c", script),
            transcript_flags=(),
            permission_flags=(),
        )
    )


@pytest.mark.parametrize("provider", ["claude", "codex", "gemini"])
def test_factory_does_not_require_an_installed_cli(monkeypatch, provider):
    def unexpected_resolution(name):
        pytest.fail("constructing a backend must not resolve its CLI")

    monkeypatch.setattr(cli_mod, "_resolve_cli", unexpected_resolution)
    backend = create_agent_backend(provider.upper())

    assert set(SUPPORTED_PROVIDERS) == {"claude", "codex", "gemini"}
    assert backend.capabilities.supports_resume is (provider == "claude")


def test_factory_rejects_unknown_provider():
    with pytest.raises(ValueError, match="[Uu]nknown provider"):
        create_agent_backend("unknown-provider")


@pytest.mark.parametrize(
    ("provider", "expected_args"),
    [
        (
            "claude",
            ["-p", "--verbose", "--output-format", "stream-json", "--dangerously-skip-permissions"],
        ),
        (
            "codex",
            ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-"],
        ),
        ("gemini", ["--output-format", "stream-json", "--yolo", "--skip-trust"]),
    ],
)
def test_existing_commands_and_stdin_conventions_are_preserved(tmp_path, monkeypatch, provider, expected_args):
    resolved = f"C:/provider tools/{provider}.cmd"
    monkeypatch.setattr(cli_mod, "_resolve_cli", lambda name: resolved)
    request = request_for(tmp_path)

    command = create_agent_backend(provider).build_command(request)

    assert command == [resolved, *expected_args]
    assert request.prompt not in command


@pytest.mark.parametrize("operation,flag", [("start", "--session-id"), ("resume", "--resume")])
def test_claude_maps_typed_session_operations_to_cli_flags(tmp_path, monkeypatch, operation, flag):
    monkeypatch.setattr(cli_mod, "_resolve_cli", lambda name: name)
    session = SessionRequest(session_id="existing-session", operation=operation)

    command = create_agent_backend("claude").build_command(request_for(tmp_path, session=session))

    assert command[-2:] == [flag, "existing-session"]


@pytest.mark.parametrize("provider", ["codex", "gemini"])
@pytest.mark.parametrize("operation", ["start", "resume"])
def test_unsupported_session_operations_fail_explicitly(tmp_path, monkeypatch, provider, operation):
    monkeypatch.setattr(cli_mod, "_resolve_cli", lambda name: name)
    request = request_for(tmp_path, session=SessionRequest("session-id", operation))

    with pytest.raises(ValueError):
        create_agent_backend(provider).build_command(request)


def test_real_subprocess_receives_prompt_directory_and_environment(tmp_path, monkeypatch, capsys):
    script = (
        "import json, os, sys\n"
        "sys.stdin.reconfigure(encoding='utf-8')\n"
        "print(json.dumps({'prompt': sys.stdin.read(), 'cwd': os.getcwd(), "
        "'setting': os.environ.get('VERITAS_RUNTIME_TEST')}))\n"
        "print('provider stderr', file=sys.stderr)\n"
    )
    backend = python_backend(monkeypatch, script)
    work = tmp_path / "work directory"
    work.mkdir()
    request = request_for(
        tmp_path,
        prompt="Measure \u03b1 and write results.\nSecond line.",
        working_dir=work,
        env={**os.environ, "VERITAS_RUNTIME_TEST": "explicit-environment"},
    )

    result = backend.invoke(request)

    assert result.success is True
    assert result.timed_out is False
    assert result.exit_code == 0
    transcript = request.transcript_path.read_text(encoding="utf-8")
    event = next(json.loads(line) for line in transcript.splitlines() if line.startswith("{"))
    assert event["prompt"] == request.prompt
    assert Path(event["cwd"]).resolve() == work.resolve()
    assert event["setting"] == "explicit-environment"
    assert "provider stderr" in transcript
    assert transcript in capsys.readouterr().out


@pytest.mark.parametrize("append", [False, True])
def test_real_subprocess_preserves_transcript_append_policy(tmp_path, monkeypatch, append):
    backend = python_backend(monkeypatch, "import sys; sys.stdin.read(); print('new event')")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("previous event\n", encoding="utf-8")

    result = backend.invoke(request_for(tmp_path, transcript_path=transcript, append=append))

    assert result.success
    expected = "previous event\nnew event\n" if append else "new event\n"
    assert transcript.read_text(encoding="utf-8") == expected


def test_real_subprocess_output_is_redacted_before_logging(tmp_path, monkeypatch, capsys):
    key = "sk-or-v1-" + "a" * 32
    script = f"import sys; sys.stdin.read(); print({key!r})"
    request = request_for(tmp_path)

    result = python_backend(monkeypatch, script).invoke(request)

    assert result.success
    transcript = request.transcript_path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert key not in transcript
    assert key not in output
    assert "[REDACTED_OPENROUTER_KEY]" in transcript
    assert "[REDACTED_OPENROUTER_KEY]" in output


def test_real_subprocess_nonzero_exit_is_not_a_timeout(tmp_path, monkeypatch):
    backend = python_backend(monkeypatch, "import sys; sys.stdin.read(); sys.exit(7)")

    result = backend.invoke(request_for(tmp_path))

    assert result.success is False
    assert result.timed_out is False
    assert result.exit_code == 7


def test_timeout_does_not_carry_into_a_later_startup_failure(tmp_path, monkeypatch):
    backend = python_backend(monkeypatch, "import sys, time; sys.stdin.read(); time.sleep(30)")
    timed_out = backend.invoke(request_for(tmp_path, timeout=0.2))

    assert timed_out.success is False
    assert timed_out.timed_out is True
    assert timed_out.exit_code is not None

    def missing_cli(name):
        raise FileNotFoundError("test CLI is unavailable")

    monkeypatch.setattr(cli_mod, "_resolve_cli", missing_cli)
    failed = backend.invoke(request_for(tmp_path))

    assert failed.success is False
    assert failed.timed_out is False
    assert failed.exit_code is None
    assert "unavailable" in failed.error


def test_process_startup_error_is_reported_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_mod, "_resolve_cli", lambda name: sys.executable)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("previous invocation\n", encoding="utf-8")

    def cannot_start(*args, **kwargs):
        raise PermissionError("cannot start test process")

    monkeypatch.setattr(cli_mod.subprocess, "Popen", cannot_start)

    result = create_agent_backend("claude").invoke(request_for(tmp_path, transcript_path=transcript))

    assert result.success is False
    assert result.timed_out is False
    assert result.exit_code is None
    assert "cannot start" in result.error
    assert transcript.read_text(encoding="utf-8") == "previous invocation\n"


def test_missing_working_directory_preserves_existing_transcript(tmp_path, monkeypatch):
    backend = python_backend(monkeypatch, "import sys; sys.stdin.read()")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("previous invocation\n", encoding="utf-8")

    result = backend.invoke(request_for(
        tmp_path,
        transcript_path=transcript,
        working_dir=tmp_path / "missing directory",
    ))

    assert result.success is False
    assert result.timed_out is False
    assert result.exit_code is None
    assert result.error
    assert transcript.read_text(encoding="utf-8") == "previous invocation\n"


@pytest.mark.parametrize("finish", ["normal", "graceful", "forced"])
def test_timeout_termination_and_timer_cleanup(tmp_path, monkeypatch, finish):
    timers = []

    class ControlledTimer:
        def __init__(self, interval, function):
            self.interval = interval
            self.function = function
            self.cancelled = False
            self.joined = False
            self.started = False
            timers.append(self)

        def start(self):
            self.started = True

        def fire(self):
            self.function()

        def cancel(self):
            self.cancelled = True

        def join(self):
            self.joined = True

    class ControlledOutput(io.StringIO):
        def __init__(self):
            super().__init__('{"type":"assistant"}\n')
            self.finished = False

        def readline(self, *args):
            if not self.finished:
                self.finished = True
                if finish == "normal":
                    process.returncode = 0
                else:
                    timers[0].fire()
                    if finish == "forced":
                        timers[1].fire()
            return super().readline(*args)

    class ControlledProcess:
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = ControlledOutput()
            self.returncode = None
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True
            if finish == "graceful":
                self.returncode = 0

        def kill(self):
            self.killed = True
            self.returncode = -9

        def poll(self):
            return self.returncode

        def wait(self):
            assert self.returncode is not None, "wait called before the process exited"
            return self.returncode

    process = ControlledProcess()
    monkeypatch.setattr(cli_mod, "_resolve_cli", lambda name: name)
    monkeypatch.setattr(cli_mod.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(cli_mod.threading, "Timer", ControlledTimer)

    result = create_agent_backend("claude").invoke(request_for(tmp_path, timeout=30))

    assert result.timed_out is (finish != "normal")
    assert result.success is (finish != "forced")
    assert process.terminated is (finish != "normal")
    assert process.killed is (finish == "forced")
    assert [timer.interval for timer in timers] == ([30] if finish == "normal" else [30, 5])
    assert all(timer.started and timer.cancelled and timer.joined for timer in timers)
    assert process.stdin.closed
    assert process.stdout.closed


class RecordingBackend:
    capabilities = AgentCapabilities()

    def __init__(self, result):
        self.requests = []
        self.result = result

    def invoke(self, request):
        self.requests.append(request)
        return self.result


@pytest.mark.parametrize("expose_api_keys", [False, True])
def test_runner_preserves_paper_credential_environment_policy(tmp_path, monkeypatch, expose_api_keys):
    monkeypatch.setenv("VERITAS_ENV_FILE_KEYS", "OPENROUTER_API_KEY, PAPER_TOKEN")
    monkeypatch.setenv("OPENROUTER_API_KEY", "paper-model-key")
    monkeypatch.setenv("PAPER_TOKEN", "paper-data-token")
    monkeypatch.setenv("VERITAS_RUNTIME_TEST", "preserved")
    runner = ReplicationRunner(Config(repo_path=tmp_path, output_dir=tmp_path / "out"))
    backend = RecordingBackend(AgentResult(success=True, exit_code=0))
    runner._agent_backend = backend

    success = runner._invoke_provider(
        prompt="Inspect the artifacts.",
        working_dir=tmp_path,
        log_path=tmp_path / "transcript.jsonl",
        timeout=120,
        append=True,
        expose_api_keys=expose_api_keys,
    )

    assert success is True
    request = backend.requests[0]
    assert request.append is True
    assert request.timeout == 120
    if expose_api_keys:
        assert request.env is None
    else:
        assert "OPENROUTER_API_KEY" not in request.env
        assert "PAPER_TOKEN" not in request.env
        assert request.env["VERITAS_RUNTIME_TEST"] == "preserved"
    assert os.environ["OPENROUTER_API_KEY"] == "paper-model-key"
    assert os.environ["PAPER_TOKEN"] == "paper-data-token"


def test_runner_boolean_interface_does_not_treat_a_failed_result_as_truthy(tmp_path):
    runner = ReplicationRunner(Config(repo_path=tmp_path, output_dir=tmp_path / "out"))
    runner._agent_backend = RecordingBackend(AgentResult(success=False, exit_code=9))

    success = runner._invoke_provider(
        prompt="Inspect the artifacts.",
        working_dir=tmp_path,
        log_path=tmp_path / "transcript.jsonl",
        timeout=None,
    )

    assert success is False
