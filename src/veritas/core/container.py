"""Docker container management for replication."""

import os
import platform
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
    """Check if Docker can actually provide GPU access.

    Two-step check:
    1. `docker info` must mention nvidia (toolkit installed)
    2. A quick `docker run --gpus all` probe must succeed (GPU accessible)

    This catches Windows/WSL setups where the toolkit is installed
    but no GPU adapter is available.
    """
    try:
        # Step 1: Check if nvidia-container-toolkit is installed
        info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
            encoding="utf-8",
        )
        if info.returncode != 0:
            return False
        output = (info.stdout or "") + (info.stderr or "")
        if "nvidia" not in output.lower():
            return False

        # Step 2: Verify a GPU is actually accessible
        probe = subprocess.run(
            ["docker", "run", "--rm", "--gpus", "all",
             "nvidia/cuda:12.5.1-runtime-ubuntu22.04",
             "nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            timeout=30,
        )
        return probe.returncode == 0

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _docker_path(path: Path) -> str:
    """Convert a path to a Docker-compatible mount source.

    On Windows, converts backslashes to forward slashes so Docker
    can parse the volume mount. On other platforms, returns as-is.
    """
    return str(path.absolute()).replace("\\", "/")


# Maps each CLI to its auth-only files.
# We mount individual files (not full dirs) to avoid copying
# OS-specific config, history, plugins, and multi-MB caches.
_CREDENTIAL_FILES = {
    ".claude": [".credentials.json"],
    ".codex": ["auth.json"],
    ".gemini": ["oauth_creds.json", "google_accounts.json"],
}


def _get_credential_mounts() -> List[str]:
    """Get volume mount flags for AI CLI credential files.

    Mounts only the minimal auth files needed for each CLI.
    Each file is mounted read-only to /tmp/<dirname>/<filename>.
    The entrypoint copies them into writable $HOME/<dirname>/.
    """
    home = Path.home()
    args = []
    for dirname, filenames in _CREDENTIAL_FILES.items():
        for filename in filenames:
            cred_file = home / dirname / filename
            if cred_file.is_file():
                args.extend([
                    "-v",
                    f"{_docker_path(cred_file)}:/tmp/{dirname}/{filename}:ro",
                ])
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
    # Ensure output directory and subdirectories exist on the host.
    # Docker volume mounts overlay the image's directories, so any
    # subdirs created in the Dockerfile won't be visible unless they
    # also exist on the host.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "replication").mkdir(exist_ok=True)

    cmd = ["docker", "run", "--rm", "-i"]

    # Run as host user on Linux/macOS so output files have correct ownership.
    # On Windows, Docker Desktop handles permissions automatically.
    if platform.system() != "Windows":
        cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

    # Volume mounts — repo is read-only to enforce no-modification rule
    cmd.extend(["-v", f"{_docker_path(repo_path)}:/workspace/repo:ro"])
    cmd.extend(["-v", f"{_docker_path(output_dir)}:/workspace/output"])

    if templates_dir:
        cmd.extend(["-v", f"{_docker_path(templates_dir)}:/workspace/templates:ro"])

    # AI CLI credential mounts (read-only)
    cmd.extend(_get_credential_mounts())

    # Environment file for API keys
    if env_file and env_file.exists():
        cmd.extend(["--env-file", str(env_file)])

    # GPU support
    if gpu:
        cmd.extend(["--gpus", "all"])

    cmd.extend(["-w", "/workspace/output"])

    # Image and command
    cmd.append(image)
    cmd.extend(provider_cmd)

    return cmd


def execute_in_container(
    cmd: List[str],
    session_instructions: str,
    log_path: Path,
    timeout: Optional[int] = None,
    on_output: Optional[Callable[[str], None]] = None,
) -> int:
    """Execute a command in the container, piping session instructions via stdin.

    Streams stdout line-by-line to log file and optionally to a callback.
    If `timeout` is set, kills the process after that many seconds and returns
    -1; `None` disables the timeout. Otherwise returns the process exit code.
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

    except Exception as e:
        print(f"  [container] Error: {e}")
        if process is not None:
            process.kill()
        return -1
