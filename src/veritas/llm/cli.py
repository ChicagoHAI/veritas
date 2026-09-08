"""CLI-backed agent execution, independent of pipeline configuration."""

import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

from veritas.llm.runtime import AgentCapabilities, AgentRequest, AgentResult
from veritas.utils.security import sanitize_text


@dataclass(frozen=True)
class CliSpec:
    name: str
    command: Tuple[str, ...]
    transcript_flags: Tuple[str, ...]
    permission_flags: Tuple[str, ...]
    stdin_args: Tuple[str, ...] = ()
    session_start_flag: Optional[str] = None
    session_resume_flag: Optional[str] = None


CLI_SPECS = {
    "claude": CliSpec(
        name="claude",
        command=("claude", "-p"),
        transcript_flags=("--verbose", "--output-format", "stream-json"),
        permission_flags=("--dangerously-skip-permissions",),
        session_start_flag="--session-id",
        session_resume_flag="--resume",
    ),
    "codex": CliSpec(
        name="codex",
        command=("codex", "exec"),
        transcript_flags=("--json",),
        # Replication needs network access for dependencies and datasets. The
        # container is the isolation boundary; phase directories may not be git repos.
        permission_flags=(
            "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
        ),
        stdin_args=("-",),
    ),
    "gemini": CliSpec(
        name="gemini",
        command=("gemini",),
        transcript_flags=("--output-format", "stream-json"),
        permission_flags=("--yolo", "--skip-trust"),
    ),
}


def _resolve_cli(name: str) -> str:
    """Resolve executable paths, including Windows npm .cmd shims."""
    resolved = shutil.which(name)
    if resolved is None:
        raise FileNotFoundError(f"{name} CLI not found on PATH")
    return resolved


class CliAgentBackend:
    def __init__(self, spec: CliSpec):
        self.spec = spec

    @property
    def capabilities(self) -> AgentCapabilities:
        # Only advertise the session operations verified for this adapter.
        return AgentCapabilities(
            supports_resume=bool(self.spec.session_start_flag and self.spec.session_resume_flag),
        )

    def build_command(self, request: AgentRequest) -> list[str]:
        session_args = ()
        if request.session is not None:
            if not self.capabilities.supports_resume:
                raise ValueError(f"{self.spec.name} adapter does not support sessions")
            flag = (
                self.spec.session_start_flag if request.session.operation == "start"
                else self.spec.session_resume_flag
            )
            session_args = (flag, request.session.session_id)
        return [
            _resolve_cli(self.spec.command[0]),
            *self.spec.command[1:],
            *self.spec.transcript_flags,
            *self.spec.permission_flags,
            *session_args,
            *self.spec.stdin_args,
        ]

    def invoke(self, request: AgentRequest) -> AgentResult:
        """Stream the sanitized native transcript; answers are written by the agent.

        Timeout first terminates the CLI, allowing it to flush resumable state
        on POSIX, then force-kills after five seconds. Windows termination is
        not catchable and cannot provide the same state-flushing guarantee.
        """
        try:
            command = self.build_command(request)
        except FileNotFoundError as exc:
            return self._failure(exc)

        request.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            process = subprocess.Popen(
                command,
                cwd=request.working_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=dict(request.env) if request.env is not None else None,
            )
        except Exception as exc:
            return self._failure(exc)

        timed_out = False
        watchdog = None
        force_kill_timer = None

        def force_kill() -> None:
            try:
                if process.poll() is None:
                    process.kill()
            except OSError:
                pass

        def terminate_on_timeout() -> None:
            nonlocal timed_out, force_kill_timer
            if process.poll() is not None:
                return
            timed_out = True
            try:
                process.terminate()
            except OSError:
                pass
            force_kill_timer = threading.Timer(5, force_kill)
            force_kill_timer.daemon = True
            force_kill_timer.start()

        try:
            if request.timeout is not None and request.timeout > 0:
                watchdog = threading.Timer(request.timeout, terminate_on_timeout)
                watchdog.daemon = True
                watchdog.start()

            # A failed launch must leave any prior attempt's transcript intact.
            mode = "a" if request.append else "w"
            with open(request.transcript_path, mode, encoding="utf-8") as log_f:
                process.stdin.write(request.prompt)
                process.stdin.close()
                for line in iter(process.stdout.readline, ""):
                    line = sanitize_text(line)
                    print(line, end="")
                    log_f.write(line)
                return_code = process.wait()
            if timed_out:
                print(f"  Timeout after {request.timeout}s")
            return AgentResult(
                success=return_code == 0,
                timed_out=timed_out,
                exit_code=return_code,
            )
        except (OSError, UnicodeError) as exc:
            force_kill()
            return self._failure(exc, timed_out=timed_out, exit_code=process.wait())
        finally:
            # Joining the watchdog first ensures it cannot create a new
            # force-kill timer after we have finished cancelling timers.
            if watchdog is not None:
                watchdog.cancel()
                watchdog.join()
            if force_kill_timer is not None:
                force_kill_timer.cancel()
                force_kill_timer.join()
            force_kill()
            process.wait()
            for pipe in (process.stdin, process.stdout):
                try:
                    pipe.close()
                except OSError:
                    pass

    def _failure(
        self, exc: Exception, timed_out: bool = False, exit_code: Optional[int] = None,
    ) -> AgentResult:
        error = sanitize_text(str(exc))
        print(f"  Error invoking {self.spec.name}: {error}")
        return AgentResult(False, timed_out=timed_out, exit_code=exit_code, error=error)
