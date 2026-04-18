# syntax=docker/dockerfile:1.5
# veritas-replicator: Docker image for running code replication
# Adapted from idea-explorer's proven multi-stage build

ARG CUDA_VERSION=12.5.1
ARG UBUNTU_VERSION=22.04

# === Stage 1: Builder ===
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION} AS builder

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl wget git ca-certificates gnupg \
    libssl-dev libffi-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libncurses5-dev libncursesw5-dev xz-utils tk-dev \
    libxml2-dev libxmlsec1-dev liblzma-dev zlib1g-dev \
    git-lfs openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# uv + Python 3.12
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/python UV_PYTHON_PREFERENCE=only-managed
RUN uv python install 3.12

# Claude Code via native installer (more reliable than npm; auto-updates in background)
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp /root/.local/bin/claude /usr/local/bin/claude

# Codex and Gemini via npm (no native Linux installer available)
RUN npm install -g @openai/codex \
    && npm install -g @google/gemini-cli \
    && echo "npm global root: $(npm root -g)"

# === Stage 2: Runtime ===
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION} AS runtime

ENV DEBIAN_FRONTEND=noninteractive TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git git-lfs openssh-client ca-certificates make \
    libcudnn8 \
    && rm -rf /var/lib/apt/lists/*

# Copy Node.js from builder
COPY --from=builder /usr/bin/node /usr/bin/
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
RUN ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx

# Copy Claude Code native binary from builder
COPY --from=builder /usr/local/bin/claude /usr/local/bin/claude

# Symlink Codex and Gemini CLI tools
# (COPY dereferences symlinks, breaking Node.js relative imports, so we re-symlink here)
RUN ln -sf /usr/lib/node_modules/@openai/codex/bin/codex.js /usr/bin/codex \
    && ln -sf /usr/lib/node_modules/@google/gemini-cli/dist/index.js /usr/bin/gemini

# uv + Python
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
COPY --from=builder /python /python

ENV PATH="/python/bin:/usr/local/bin:${PATH}"
ENV UV_PYTHON_INSTALL_DIR=/python UV_PYTHON_PREFERENCE=only-managed
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Entrypoint (before USER switch so we can write to /usr/local/bin)
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Non-root user
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} replicator \
    && useradd -u ${UID} -g replicator -m -s /bin/bash replicator

# Workspace directory
RUN mkdir -p /workspace/repo /workspace/output \
    && chown -R replicator:replicator /workspace

USER replicator
WORKDIR /workspace

# Git config
RUN git config --global user.email "replicator@veritas" \
    && git config --global user.name "Veritas Replicator"

# CLI credential directories
RUN mkdir -p ~/.claude ~/.codex ~/.gemini ~/.cache/uv

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
