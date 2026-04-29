#!/bin/bash
# veritas container entrypoint
set -e

# Make container-created files world-rw so host users (any UID) can
# manage workspace outputs after the container exits. NeuriCo pattern.
umask 000

export PATH="/python/bin:/usr/local/bin:${PATH}"

# Ensure python is on PATH (uv installs to a versioned subdirectory)
if ! command -v python &> /dev/null; then
    PYTHON_BIN=$(uv python find 3.12 2>/dev/null)
    if [ -n "$PYTHON_BIN" ]; then
        export PATH="$(dirname "$PYTHON_BIN"):$PATH"
    fi
fi

# Handle arbitrary user (--user flag).
if [ ! -w "${HOME:-/}" ]; then
    export HOME=/tmp/home
    mkdir -p "$HOME"
fi

# Codex requires owned, 0600 session files; copy from the read-only host
# mount into a container-private CODEX_HOME. Login keeps the RW path so
# OAuth tokens persist to the host.
if [ "${VERITAS_LOGIN_ONLY:-0}" != "1" ] && [ -d "$HOME/.codex-host" ]; then
    mkdir -p "$HOME/.codex-container"
    for f in auth.json config.toml; do
        if [ -f "$HOME/.codex-host/$f" ]; then
            cp "$HOME/.codex-host/$f" "$HOME/.codex-container/$f" 2>/dev/null || true
            chmod 600 "$HOME/.codex-container/$f" 2>/dev/null || true
        fi
    done
    export CODEX_HOME="$HOME/.codex-container"
fi

# Set up credential files from read-only mounts at /tmp/.
# Individual auth files are mounted (not full dirs) to avoid
# OS-specific config and bloat. We copy them into writable $HOME/.
for dir in .claude .codex .gemini; do
    if [ -d "/tmp/$dir" ] && [ "$(ls -A /tmp/$dir 2>/dev/null)" ]; then
        mkdir -p "$HOME/$dir"
        # Use find instead of glob — bash * doesn't match dotfiles like .credentials.json
        find "/tmp/$dir" -maxdepth 1 -type f -exec cp {} "$HOME/$dir/" \;
    fi
done

echo "========================================"
echo "  Veritas Container Starting"
echo "========================================"

# veritas version
if command -v veritas &> /dev/null; then
    echo "Veritas: $(veritas --help 2>&1 | grep -m1 'Usage:')"
fi

# Check GPU
if command -v nvidia-smi &> /dev/null; then
    if nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null; then
        echo "GPU: available"
    else
        echo "GPU: not accessible (need --gpus flag)"
    fi
else
    echo "GPU: not detected"
fi

echo "Python: $(python --version 2>&1)"
echo "uv:     $(uv --version 2>&1)"
echo "Pandoc: $(pandoc --version 2>&1 | head -1)"
echo "Working directory: $(pwd)"

# Validate credentials
echo ""
echo "Credentials:"
for dir in .claude .codex .gemini; do
    check_dir="$dir"
    if [ "$dir" = ".codex" ] && [ "${VERITAS_LOGIN_ONLY:-0}" != "1" ]; then
        check_dir=".codex-container"
    fi
    if [ -d "$HOME/$check_dir" ] && [ "$(ls -A "$HOME/$check_dir" 2>/dev/null)" ]; then
        echo "  $dir: OK"
    else
        echo "  $dir: not found"
    fi
done

echo "==================================="
echo ""

exec "$@"
