"""Log sanitization — redacts API keys and sensitive tokens from text."""

import re
from pathlib import Path
from typing import List, Tuple

# Patterns for known API key formats and their replacements.
# Each tuple is (regex_pattern, replacement_string).
API_KEY_PATTERNS: List[Tuple[str, str]] = [
    # OpenAI / OpenRouter keys (order matters: more specific first)
    (r'sk-proj-[A-Za-z0-9_-]{20,}', '[REDACTED_OPENAI_PROJECT_KEY]'),
    (r'sk-or-v1-[A-Za-z0-9_-]{20,}', '[REDACTED_OPENROUTER_KEY]'),
    (r'sk-or-[A-Za-z0-9_-]{20,}', '[REDACTED_OPENAI_ORG_KEY]'),
    (r'sk-[A-Za-z0-9]{48,}', '[REDACTED_OPENAI_KEY]'),
    # Anthropic keys (also covers sk-ant-oat01- OAuth tokens from Claude Code)
    (r'sk-ant-[A-Za-z0-9_-]{20,}', '[REDACTED_ANTHROPIC_KEY]'),
    # GitHub tokens
    (r'ghp_[A-Za-z0-9]{36,}', '[REDACTED_GITHUB_PAT]'),
    (r'gho_[A-Za-z0-9]{36,}', '[REDACTED_GITHUB_OAUTH]'),
    (r'ghs_[A-Za-z0-9]{36,}', '[REDACTED_GITHUB_APP]'),
    (r'ghr_[A-Za-z0-9]{36,}', '[REDACTED_GITHUB_REFRESH]'),
    (r'github_pat_[A-Za-z0-9_]{20,}', '[REDACTED_GITHUB_FINE_GRAINED]'),
    # Google API keys
    (r'AIza[A-Za-z0-9_-]{35}', '[REDACTED_GOOGLE_KEY]'),
    # Google OAuth tokens — present in ~/.gemini/oauth_creds.json which veritas
    # mounts into the replication container. Access tokens look like
    # "ya29.a0Af..."; refresh tokens look like "1//09abc..." (always begin 1//0).
    (r'ya29\.[A-Za-z0-9_-]{20,}', '[REDACTED_GOOGLE_OAUTH_ACCESS]'),
    (r'1//0[A-Za-z0-9_-]{20,}', '[REDACTED_GOOGLE_OAUTH_REFRESH]'),
    # AWS access keys
    (r'AKIA[A-Z0-9]{16}', '[REDACTED_AWS_KEY]'),
    # HuggingFace tokens
    (r'hf_[A-Za-z0-9]{34,}', '[REDACTED_HF_TOKEN]'),
    # Bearer tokens in HTTP headers
    (r'(Authorization:\s*Bearer\s+)\S+', r'\1[REDACTED]'),
    # Generic env var assignments (catches echoed env vars)
    (r'(OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GOOGLE_API_KEY|GEMINI_API_KEY|OPENROUTER_API_KEY|OPENROUTER_KEY|HF_TOKEN|HUGGINGFACE_TOKEN|AWS_SECRET_ACCESS_KEY)=[^\s\n"\']+',
     r'\1=[REDACTED]'),
    (r'(export\s+)(OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GOOGLE_API_KEY|GEMINI_API_KEY|OPENROUTER_API_KEY|OPENROUTER_KEY|HF_TOKEN|HUGGINGFACE_TOKEN|AWS_SECRET_ACCESS_KEY)=[^\s\n"\']+',
     r'\1\2=[REDACTED]'),
]

_COMPILED_PATTERNS = [(re.compile(p), r) for p, r in API_KEY_PATTERNS]


def sanitize_text(text: str) -> str:
    """Redact known API key patterns from text."""
    result = text
    for pattern, replacement in _COMPILED_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def sanitize_log_file(file_path: Path) -> bool:
    """Sanitize a log file in-place. Returns True if any redactions were made."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    sanitized = sanitize_text(content)
    if sanitized != content:
        file_path.write_text(sanitized, encoding="utf-8")
        return True
    return False


def sanitize_logs_directory(logs_dir: Path) -> int:
    """Sanitize all log files in a directory tree. Returns number of files modified."""
    if not logs_dir.exists():
        return 0

    modified_count = 0
    log_patterns = ['*.log', '*.jsonl', '*.txt', '*.json', '*.md', '*.out', '*.err']

    for pattern in log_patterns:
        for log_file in logs_dir.rglob(pattern):
            if sanitize_log_file(log_file):
                modified_count += 1

    return modified_count
