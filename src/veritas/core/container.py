"""Docker container management for replication."""

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional


def is_docker_available() -> bool:
    """Check if Docker is installed and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_gpu() -> bool:
    """Check if NVIDIA GPU is available via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_credential_mounts() -> List[str]:
    """Get volume mount flags for AI CLI credential directories.

    Mounts ~/.claude, ~/.codex, ~/.gemini as read-only so the AI tools
    inside the container can authenticate with their respective APIs.
    """
    home = Path.home()
    args = []
    for dirname in [".claude", ".codex", ".gemini"]:
        cred_dir = home / dirname
        if cred_dir.is_dir():
            args.extend(["-v", f"{cred_dir}:/home/replicator/{dirname}:ro"])
    return args


def build_container_command(
    repo_path: Path,
    output_dir: Path,
    image: str,
    provider_cmd: List[str],
    gpu: bool = False,
    templates_dir: Optional[Path] = None,
    env_file: Optional[Path] = None,
) -> List[str]:
    """Build the docker run command for replication."""
    cmd = ["docker", "run", "--rm", "-i"]

    # Volume mounts — repo is read-only to enforce no-modification rule
    cmd.extend(["-v", f"{repo_path.absolute()}:/workspace/repo:ro"])
    cmd.extend(["-v", f"{output_dir.absolute()}:/workspace/output"])

    if templates_dir:
        cmd.extend(["-v", f"{templates_dir.absolute()}:/workspace/templates:ro"])

    # AI CLI credential mounts (read-only)
    cmd.extend(_get_credential_mounts())

    # Environment file for API keys
    if env_file and env_file.exists():
        cmd.extend(["--env-file", str(env_file)])

    # GPU support
    if gpu:
        cmd.extend(["--gpus", "all"])

    # Image and command
    cmd.append(image)
    cmd.extend(provider_cmd)

    return cmd


def execute_in_container(
    cmd: List[str],
    session_instructions: str,
    log_path: Path,
    timeout: int = 3600,
    on_output: Optional[Callable[[str], None]] = None,
) -> int:
    """Execute a command in the container, piping session instructions via stdin.

    Streams stdout line-by-line to log file and optionally to a callback.
    Enforces timeout via a background thread — if the process exceeds
    the timeout, it is killed. Returns the process exit code, or -1 on timeout.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    process = None
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )

        # Send session instructions via stdin
        process.stdin.write(session_instructions)
        process.stdin.close()

        # Stream output in a background thread so we can enforce timeout
        def _stream_output():
            with open(log_path, "w", encoding="utf-8") as log_file:
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
                    if on_output:
                        on_output(line)

        reader = threading.Thread(target=_stream_output, daemon=True)
        reader.start()
        reader.join(timeout=timeout)

        if reader.is_alive():
            # Timeout — kill the process and wait for reader to finish
            process.kill()
            reader.join(timeout=10)
            return -1

        process.wait()
        return process.returncode

    except Exception:
        if process is not None:
            process.kill()
        return -1
