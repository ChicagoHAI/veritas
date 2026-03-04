"""Tests for log sanitization / API key redaction."""

from veritas.utils.security import sanitize_text, sanitize_log_file


class TestSanitizeText:
    def test_redacts_openai_key(self):
        text = "key is sk-proj-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH"
        result = sanitize_text(text)
        assert "sk-proj-" not in result
        assert "[REDACTED_OPENAI_PROJECT_KEY]" in result

    def test_redacts_anthropic_key(self):
        text = "ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz"
        result = sanitize_text(text)
        assert "sk-ant-" not in result
        assert "[REDACTED" in result

    def test_redacts_github_pat(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
        result = sanitize_text(text)
        assert "ghp_" not in result
        assert "[REDACTED_GITHUB_PAT]" in result

    def test_redacts_env_var_assignment(self):
        text = "export OPENAI_API_KEY=sk-1234567890abcdef"
        result = sanitize_text(text)
        assert "OPENAI_API_KEY=[REDACTED]" in result

    def test_leaves_normal_text_unchanged(self):
        text = "This is just a normal log line with no secrets."
        assert sanitize_text(text) == text

    def test_handles_multiple_keys_in_one_text(self):
        text = "key1=sk-ant-aaaaaaaaaaaaaaaaaaaaaa key2=ghp_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        result = sanitize_text(text)
        assert "sk-ant-" not in result
        assert "ghp_" not in result

    def test_redacts_huggingface_token(self):
        text = "HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz12345678"
        result = sanitize_text(text)
        assert "hf_" not in result
        assert "[REDACTED" in result

    def test_redacts_aws_key(self):
        text = "aws key: AKIAIOSFODNN7EXAMPLE"
        result = sanitize_text(text)
        assert "AKIA" not in result
        assert "[REDACTED_AWS_KEY]" in result

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.some.token"
        result = sanitize_text(text)
        assert "eyJhbG" not in result
        assert "[REDACTED]" in result


class TestSanitizeLogFile:
    def test_sanitizes_file_in_place(self, tmp_path):
        log = tmp_path / "output.log"
        log.write_text("API key: sk-ant-abcdefghijklmnopqrstuvwxyz")
        changed = sanitize_log_file(log)
        assert changed is True
        assert "sk-ant-" not in log.read_text()
        assert "[REDACTED" in log.read_text()

    def test_returns_false_when_no_secrets(self, tmp_path):
        log = tmp_path / "clean.log"
        log.write_text("All good, no secrets here.")
        changed = sanitize_log_file(log)
        assert changed is False

    def test_returns_false_for_missing_file(self, tmp_path):
        log = tmp_path / "nonexistent.log"
        changed = sanitize_log_file(log)
        assert changed is False
