"""Tests for container management module."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from veritas.core.container import (
    is_docker_available,
    has_gpu,
    build_container_command,
    execute_in_container,
    _get_credential_mounts,
    _docker_path,
)


class TestIsDockerAvailable:
    def test_docker_available(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert is_docker_available() is True

    def test_docker_not_available(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert is_docker_available() is False

    def test_docker_not_running(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert is_docker_available() is False


class TestHasGpu:
    def test_gpu_detected_via_docker_info(self):
        def mock_run(cmd, **kwargs):
            if cmd == ["docker", "info"]:
                return MagicMock(returncode=0, stdout="Runtimes: nvidia runc\n", stderr="")
            # GPU probe succeeds
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            assert has_gpu() is True

    def test_no_gpu_when_nvidia_not_in_docker_info(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Runtimes: runc\n",
                stderr="",
            )
            assert has_gpu() is False

    def test_no_gpu_when_docker_not_available(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert has_gpu() is False

    def test_no_gpu_when_toolkit_installed_but_no_adapter(self):
        """Should return False when nvidia toolkit exists but GPU probe fails."""
        def mock_run(cmd, **kwargs):
            if cmd == ["docker", "info"]:
                return MagicMock(returncode=0, stdout="Runtimes: nvidia runc\n", stderr="")
            return MagicMock(returncode=125)

        with patch("subprocess.run", side_effect=mock_run):
            assert has_gpu() is False


class TestDockerPath:
    def test_no_backslashes(self, tmp_path):
        result = _docker_path(tmp_path / "repo")
        assert "\\" not in result

    def test_absolute(self, tmp_path):
        result = _docker_path(tmp_path / "repo")
        # Should be an absolute path (Unix / or Windows drive letter)
        assert result[0] == "/" or result[1] == ":"


class TestCredentialMounts:
    def test_mounts_existing_credential_files(self, tmp_path):
        """Should mount auth files that exist."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text('{}')
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "auth.json").write_text('{}')
        # .gemini doesn't exist — should be skipped

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        mount_str = " ".join(mounts)
        assert ".credentials.json" in mount_str
        assert "auth.json" in mount_str
        assert ".gemini" not in mount_str
        assert ":ro" in mount_str

    def test_no_credential_files(self, tmp_path):
        """Should return empty list when no auth files exist."""
        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()
        assert mounts == []

    def test_mounts_to_tmp_directory(self, tmp_path):
        """Auth files should mount under /tmp/<dirname>/ for entrypoint to copy."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text('{}')

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        mount_str = " ".join(mounts)
        assert "/tmp/.claude/.credentials.json" in mount_str

    def test_no_backslashes_in_mount_source(self, tmp_path):
        """Mount source path should use forward slashes."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text('{}')

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        for i, arg in enumerate(mounts):
            if arg == "-v":
                source = mounts[i + 1].split(":")[0]
                assert "\\" not in source

    def test_claude_mounts_only_credentials_file(self, tmp_path):
        """Should mount .claude/.credentials.json, not the entire .claude/ dir."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text('{"claudeAiOauth": {}}')

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        mount_str = " ".join(mounts)
        assert ".credentials.json" in mount_str
        assert ":/tmp/.claude:" not in mount_str

    def test_codex_mounts_only_auth_file(self, tmp_path):
        """Should mount .codex/auth.json, not the entire .codex/ dir."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "auth.json").write_text('{}')
        (codex_dir / "state_5.sqlite").write_text('junk')

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        mount_str = " ".join(mounts)
        assert "auth.json" in mount_str
        assert ":/tmp/.codex:" not in mount_str

    def test_gemini_mounts_only_auth_files(self, tmp_path):
        """Should mount oauth_creds.json and google_accounts.json only."""
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        (gemini_dir / "oauth_creds.json").write_text('{}')
        (gemini_dir / "google_accounts.json").write_text('{}')
        (gemini_dir / "settings.json").write_text('{}')

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        mount_str = " ".join(mounts)
        assert "oauth_creds.json" in mount_str
        assert "google_accounts.json" in mount_str
        assert "settings.json" not in mount_str

    def test_skips_missing_auth_files(self, tmp_path):
        """Should skip CLIs whose auth files don't exist."""
        (tmp_path / ".claude").mkdir()
        # .codex doesn't exist at all

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            mounts = _get_credential_mounts()

        assert mounts == []


class TestUserFlag:
    @patch("veritas.core.container.platform.system", return_value="Linux")
    @patch("veritas.core.container.os.getuid", return_value=1001, create=True)
    @patch("veritas.core.container.os.getgid", return_value=1001, create=True)
    def test_adds_user_flag_on_linux(self, mock_gid, mock_uid, mock_system, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )
        assert "--user" in cmd
        assert "1001:1001" in cmd

    @patch("veritas.core.container.platform.system", return_value="Darwin")
    @patch("veritas.core.container.os.getuid", return_value=501, create=True)
    @patch("veritas.core.container.os.getgid", return_value=20, create=True)
    def test_adds_user_flag_on_macos(self, mock_gid, mock_uid, mock_system, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )
        assert "--user" in cmd
        assert "501:20" in cmd

    @patch("veritas.core.container.platform.system", return_value="Windows")
    def test_no_user_flag_on_windows(self, mock_system, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )
        assert "--user" not in cmd


class TestDirectoryPreCreation:
    def test_creates_output_subdirectories(self, tmp_path):
        """build_container_command should pre-create output subdirs."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        # Note: output dir does NOT exist yet

        build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )

        assert output.exists()
        assert (output / "replication").exists()


class TestTtyFlag:
    def test_always_uses_dash_i_not_dash_it(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )
        assert "-i" in cmd
        assert "-it" not in cmd


class TestBuildContainerCommand:
    def test_basic_command(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude", "-p"],
        )
        assert "docker" in cmd
        assert "veritas:latest" in cmd
        assert "claude" in cmd

    def test_gpu_flag(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"], gpu=True,
        )
        assert "--gpus" in cmd
        assert "all" in cmd

    def test_no_gpu_flag(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"], gpu=False,
        )
        assert "--gpus" not in cmd

    def test_working_directory_set_to_output(self, tmp_path):
        """Should set -w /workspace/output (writable) not /workspace/repo (read-only)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["codex"],
        )
        assert "-w" in cmd
        w_idx = cmd.index("-w")
        assert cmd[w_idx + 1] == "/workspace/output"

    def test_volume_mounts_present(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )
        cmd_str = " ".join(cmd)
        assert "/workspace/repo" in cmd_str
        assert "/workspace/output" in cmd_str

    def test_volume_mount_sources_no_backslashes(self, tmp_path):
        """Volume mount source paths should use forward slashes."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
        )
        for i, arg in enumerate(cmd):
            if arg == "-v":
                source = cmd[i + 1].split(":")[0]
                assert "\\" not in source, f"Backslash in mount source: {source}"

    def test_env_file_passed(self, tmp_path):
        """Should include --env-file when .env exists."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=test")

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
            env_file=env_file,
        )
        assert "--env-file" in cmd
        assert str(env_file) in cmd

    def test_env_file_missing_not_passed(self, tmp_path):
        """Should not include --env-file when file doesn't exist."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()
        env_file = tmp_path / ".env"  # does not exist

        cmd = build_container_command(
            repo_path=repo, output_dir=output,
            image="veritas:latest", provider_cmd=["claude"],
            env_file=env_file,
        )
        assert "--env-file" not in cmd

    def test_credential_mounts_included(self, tmp_path):
        """Should include credential file mounts in command."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text('{}')

        with patch("veritas.core.container.Path.home", return_value=tmp_path):
            cmd = build_container_command(
                repo_path=repo, output_dir=output,
                image="veritas:latest", provider_cmd=["claude"],
            )

        cmd_str = " ".join(cmd)
        assert ".credentials.json" in cmd_str
        assert ":ro" in cmd_str


class TestExecuteInContainer:
    def test_execute_streams_output(self, tmp_path):
        log_path = tmp_path / "log.txt"

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = iter(["line 1\n", "line 2\n"])
        mock_process.returncode = 0

        with patch("subprocess.Popen", return_value=mock_process):
            code = execute_in_container(
                cmd=["docker", "run", "test"],
                session_instructions="do stuff",
                log_path=log_path,
            )

        assert code == 0
        assert log_path.exists()
        content = log_path.read_text()
        assert "line 1" in content
        assert "line 2" in content

    def test_execute_timeout(self, tmp_path):
        log_path = tmp_path / "log.txt"
        import time

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()

        # Simulate a process that hangs — stdout blocks forever
        def blocking_iter():
            time.sleep(10)
            return
            yield  # make this a generator

        mock_process.stdout = blocking_iter()
        mock_process.returncode = None

        with patch("subprocess.Popen", return_value=mock_process):
            code = execute_in_container(
                cmd=["docker", "run", "test"],
                session_instructions="do stuff",
                log_path=log_path,
                timeout=1,
            )

        assert code == -1
        mock_process.kill.assert_called_once()

    def test_on_output_callback(self, tmp_path):
        """Should call on_output for each line."""
        log_path = tmp_path / "log.txt"

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = iter(["hello\n", "world\n"])
        mock_process.returncode = 0

        captured = []

        with patch("subprocess.Popen", return_value=mock_process):
            code = execute_in_container(
                cmd=["docker", "run", "test"],
                session_instructions="do stuff",
                log_path=log_path,
                on_output=lambda line: captured.append(line),
            )

        assert code == 0
        assert captured == ["hello\n", "world\n"]
