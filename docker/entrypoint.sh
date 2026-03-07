#!/bin/bash
# veritas-replicator container entrypoint
set -e

export PATH="/python/bin:/usr/local/bin:${PATH}"

# Ensure python is on PATH (uv installs to a versioned subdirectory)
if ! command -v python &> /dev/null; then
    PYTHON_BIN=$(uv python find 3.12 2>/dev/null)
    if [ -n "$PYTHON_BIN" ]; then
        export PATH="$(dirname "$PYTHON_BIN"):$PATH"
    fi
fi

# Handle arbitrary user (--user flag)
if [ ! -w "${HOME:-/}" ]; then
    export HOME=/tmp
fi

# Symlink credential directories from /tmp/ to $HOME/ so AI CLIs
# can find them regardless of which user the container runs as.
# (Credentials are mounted to /tmp/ by veritas for --user compatibility.)
if [ "$HOME" != "/tmp" ]; then
    for dir in .claude .codex .gemini; do
        if [ -d "/tmp/$dir" ]; then
            # Remove empty dir created by Dockerfile so symlink can replace it
            rm -rf "$HOME/$dir"
            ln -sf "/tmp/$dir" "$HOME/$dir"
        fi
    done
fi

echo "=== Veritas Replicator Container ==="

# Check GPU
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null \
        && echo "GPU available" || echo "GPU not accessible"
else
    echo "No GPU detected"
fi

echo "Python: $(python --version 2>&1)"
echo "uv: $(uv --version 2>&1)"
echo "Working directory: $(pwd)"
echo "==================================="
echo ""

exec "$@"
