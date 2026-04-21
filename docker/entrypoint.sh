#!/bin/bash
# veritas-replicator container entrypoint
set -e

# Make all files created by the container world-readable/writable so that any
# host user can manage (read/delete) workspace files regardless of UID mismatch.
# The container runs as replicator (UID from --build-arg) which maps to a different user on
# most host systems, so without this, only that mapped user (or root) could
# delete generated output.
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

echo "=== Veritas Replicator Container ==="

# Check GPU
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null \
        && echo "GPU: available" || echo "GPU: not accessible"
else
    echo "GPU: not detected"
fi

echo "Python: $(python --version 2>&1)"
echo "uv: $(uv --version 2>&1)"
echo "Working directory: $(pwd)"

# Validate credentials
echo ""
echo "Credentials:"
for dir in .claude .codex .gemini; do
    if [ -d "$HOME/$dir" ] && [ "$(ls -A "$HOME/$dir" 2>/dev/null)" ]; then
        echo "  $dir: OK"
    else
        echo "  $dir: not found"
    fi
done

echo "==================================="
echo ""

exec "$@"
