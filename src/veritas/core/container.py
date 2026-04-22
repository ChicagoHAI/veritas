"""Docker container management for replication."""

import hashlib
import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional


DOCKERFILE_HASH_LABEL = "veritas.dockerfile_sha256"


def get_dockerfile_path() -> Optional[Path]:
    """Return the repo's Dockerfile path, or None if it isn't on disk.

    Walks up from this module to the source tree root and looks for
    `docker/Dockerfile` — the layout of an editable install. Returns
    None for non-editable/wheel installs where the Dockerfile wasn't
    shipped; callers should treat that as "no local build to validate".
    """
    candidate = Path(__file__).parent.parent.parent.parent / "docker" / "Dockerfile"
    return candidate if candidate.is_file() else None


def compute_dockerfile_hash(dockerfile: Optional[Path] = None) -> str:
    """Return the SHA256 of the Dockerfile contents.

    Raises FileNotFoundError if no Dockerfile path is supplied and the
    default location isn't on disk.
    """
    if dockerfile is None:
        dockerfile = get_dockerfile_path()
        if dockerfile is None:
            raise FileNotFoundError("Dockerfile not found in source tree")
    return hashlib.sha256(dockerfile.read_bytes()).hexdigest()


class PreflightError(RuntimeError):
    """Raised when a preflight check fails in a way that should block launch."""


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

# Maps each provider to (credential dirname, login command hint).
_PROVIDER_CREDENTIALS = {
    "claude": (".claude", "claude"),
    "codex": (".codex", "codex"),
    "gemini": (".gemini", "gemini"),
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


def check_image_staleness(image: str) -> None:
    """Verify the Docker image exists and matches the current Dockerfile.

    Raises PreflightError if the image is missing, or if it was built
    from a different Dockerfile than the one on disk. A missing hash
    label (older image) is treated as stale.

    If no local Dockerfile is available (e.g. a wheel install with no
    source tree), the hash comparison is skipped with a warning and
    only the image-existence check runs.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             f"{{{{index .Config.Labels \"{DOCKERFILE_HASH_LABEL}\"}}}}", image],
            capture_output=True,
            timeout=10,
            encoding="utf-8",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise PreflightError(f"Failed to inspect Docker image '{image}': {e}")

    if result.returncode != 0:
        raise PreflightError(
            f"Docker image '{image}' not found. "
            f"Build it with: uv run veritas build-image"
        )

    dockerfile = get_dockerfile_path()
    if dockerfile is None:
        print(
            "  [preflight] No local Dockerfile found; skipping image-hash check. "
            "Image existence confirmed."
        )
        return

    stored_hash = (result.stdout or "").strip()
    current_hash = compute_dockerfile_hash(dockerfile)

    if not stored_hash or stored_hash == "<no value>":
        raise PreflightError(
            f"Docker image '{image}' has no build metadata and is likely stale. "
            f"Rebuild it with: uv run veritas build-image"
        )

    if stored_hash != current_hash:
        raise PreflightError(
            f"Docker image '{image}' was built from a different Dockerfile "
            f"(image: {stored_hash[:12]}, current: {current_hash[:12]}). "
            f"Rebuild it with: uv run veritas build-image"
        )


def check_credentials(provider: str) -> None:
    """Verify credentials for the selected provider exist on the host.

    Raises PreflightError naming the specific missing file and the
    login command to fix it.

    macOS note: Claude Code stores credentials in the Keychain rather
    than `~/.claude/.credentials.json`.
    """
    try:
        dirname, login_cmd = _PROVIDER_CREDENTIALS[provider.lower()]
    except KeyError:
        raise PreflightError(f"Unknown provider: {provider}")

    if provider.lower() == "claude" and platform.system() == "Darwin":
        try:
            subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                check=True,
                capture_output=True,
                timeout=5,
            )
            return  # Keychain entry exists — logged in.
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass  # fall through to the file check below.

    filenames = _CREDENTIAL_FILES[dirname]
    home = Path.home()
    cred_dir = home / dirname

    missing = [f for f in filenames if not (cred_dir / f).is_file()]
    if len(missing) == len(filenames):
        # No credential files found at all — provider is not logged in.
        expected = cred_dir / filenames[0]
        raise PreflightError(
            f"{provider.capitalize()} credentials not found at {expected}. "
            f"Run '{login_cmd}' on the host to log in first."
        )


def check_gpu_requested() -> None:
    """Verify GPU is actually available when the user requested --gpu.

    Raises PreflightError if NVIDIA Container Toolkit is missing or a
    GPU is not accessible from Docker.
    """
    if not has_gpu():
        raise PreflightError(
            "GPU requested (--gpu) but not available. "
            "Ensure the NVIDIA Container Toolkit is installed and "
            "`nvidia-smi` works inside a Docker container. "
            "GPU support is Linux-only."
        )


def preflight_checks(
    image: str,
    provider: str,
    gpu_requested: bool,
    use_docker: bool,
) -> None:
    """Run all preflight checks before any provider or container work.

    Called by the runner up-front so users learn about misconfigurations
    in seconds instead of minutes — before any API costs are incurred.
    Credentials are always checked (the analyze phase also needs them);
    Docker-specific checks only run when Docker is enabled.

    Raises PreflightError on the first failure.
    """
    check_credentials(provider)
    if use_docker and is_docker_available():
        check_image_staleness(image)
    if gpu_requested:
        check_gpu_requested()


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
