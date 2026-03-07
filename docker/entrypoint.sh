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

# Handle arbitrary user (--user flag).
if [ ! -w "${HOME:-/}" ]; then
    export HOME=/tmp/home
    mkdir -p "$HOME"
fi

# Copy credential directories from read-only mounts at /tmp/ to writable $HOME/.
# Credentials are mounted to /tmp/ by veritas with :ro to protect host files,
# but CLIs like Codex need write access to their config dirs.
for dir in .claude .codex .gemini; do
    if [ -d "/tmp/$dir" ]; then
        rm -rf "$HOME/$dir"
        cp -r "/tmp/$dir" "$HOME/$dir"
    fi
done

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
