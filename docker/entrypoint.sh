#!/bin/bash
# veritas-replicator container entrypoint
set -e

export PATH="/python/bin:/usr/local/bin:${PATH}"

# Handle arbitrary user (--user flag)
if [ ! -w "${HOME:-/}" ]; then
    export HOME=/tmp
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
