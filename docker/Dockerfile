# syntax=docker/dockerfile:1.5
# veritas: Docker image for running the full replication pipeline
# Adapted from idea-explorer's (NeuriCo) proven multi-stage build

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

# -----------------------------------------------------------------------------
# Copy veritas source and build its venv into /app/.venv
# -----------------------------------------------------------------------------
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY templates/ ./templates/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv \
    && uv sync --frozen --no-dev \
    && uv pip install --python /app/.venv/bin/python weasyprint
    # WeasyPrint renders the styled HTML report to PDF (kept out of the frozen
    # lockfile so host installs aren't burdened; report_generator falls back to
    # pandoc/LaTeX when it's unavailable). Runtime system libs are installed in
    # the runtime stage's "LaTeX + pandoc" apt block below.

# === Stage 2: Runtime ===
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu${UBUNTU_VERSION} AS runtime

LABEL org.opencontainers.image.title="veritas"
LABEL org.opencontainers.image.description="Replication agent for scientific reproducibility"
LABEL org.opencontainers.image.source="https://github.com/ChicagoHAI/veritas"
LABEL maintainer="ChicagoHAI"

ENV DEBIAN_FRONTEND=noninteractive TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl git git-lfs openssh-client ca-certificates \
    libcudnn8 \
    && rm -rf /var/lib/apt/lists/*

# LaTeX + pandoc for PDF report generation (pandoc is the fallback PDF path).
# libpangocairo + gdk-pixbuf are WeasyPrint's runtime deps for the primary
# HTML->PDF path (libcairo/glib come transitively; harfbuzz is installed with R).
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-latex-recommended \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-science \
    lmodern \
    cm-super \
    latexmk \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi8 \
    && rm -rf /var/lib/apt/lists/*

# R toolchain for CORE-Bench (R-language capsules: ~23 of 45 in CB-Hard).
# Without this, each R cell spends ~10 min bootstrapping R via micromamba.
# The base R + tidyverse + common stats / rmarkdown stack lets the agent
# start running .Rmd / .R immediately.
#
# Why not stock Ubuntu's r-base? Jammy ships R 4.1.2 (2021-11), which is
# too old for several CB capsules' deps (e.g. papaja's S3 dispatch fixes
# require R >= 4.2). We register the official CRAN apt repo so r-base
# tracks the current stable R (4.x), and we install **all** R packages
# via Rscript afterwards (NOT via r-cran-* apt packages) so the entire
# package set is built against the same R headers and same xfun ABI.
# Mixing apt-old + CRAN-new R packages produces "object 'attr' is not
# exported by 'namespace:xfun'"-style errors.
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common dirmngr wget gnupg ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && wget -qO- https://cloud.r-project.org/bin/linux/ubuntu/marutter_pubkey.asc \
        | gpg --dearmor -o /etc/apt/keyrings/cran.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/cran.gpg] https://cloud.r-project.org/bin/linux/ubuntu jammy-cran40/" \
        > /etc/apt/sources.list.d/cran.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        r-base r-base-dev \
        libcurl4-openssl-dev libxml2-dev libssl-dev libfontconfig1-dev \
        libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev \
        libv8-dev libcairo2-dev libgdal-dev libudunits2-dev \
        libnlopt-dev cmake \
    && rm -rf /var/lib/apt/lists/*

# All R packages installed via Rscript from CRAN, against the system R
# (so /usr/local/lib/R/site-library is shared by every user). Order matters
# for compilation time: install the heaviest deps first; failures here
# fail the build loudly (we want to know at image-build time, not at
# cell-run time, if a package is broken on this R version).
#
# Coverage chosen from CB-capsule env survey (top extras across the 23
# R capsules in CB-Hard).
RUN R -e "options(repos=c(CRAN='https://cloud.r-project.org'), Ncpus=4); \
    install.packages(c( \
        'tidyverse', 'rmarkdown', 'knitr', \
        'devtools', 'remotes', \
        'lme4', 'lmerTest', 'psych', 'BayesFactor', 'afex', 'effects', \
        'scales', 'gridExtra', 'xtable', 'patchwork', 'kableExtra', 'ggpubr', \
        'RColorBrewer', 'magrittr', 'lubridate', 'plyr', 'readxl', \
        'estimatr', 'beeswarm', 'jpeg', 'circlize', 'rvest', 'RCurl', \
        'tinytex', 'BiocManager', 'yarrr', 'papaja' \
    ))" \
    && R -e "for (p in c('tidyverse','rmarkdown','papaja','yarrr','BayesFactor','afex','kableExtra')) { \
        ok <- requireNamespace(p, quietly=TRUE); \
        cat(sprintf('%-15s %s\n', p, if (ok) 'OK' else 'FAIL')); \
        if (!ok) stop('R toolchain validation failed for: ', p) \
    }"

# GNU Octave for executing MATLAB (.m) code during replication runs. Octave is
# GPL/free (no license server, unlike MATLAB which would require a paid per-user
# license — a non-starter for a redistributable tool) and runs headless via
# `octave-cli`. We bake in the most common Octave-Forge packages so the
# replication agent can run typical numerical paper code immediately; core
# numerical/matrix functions need no `pkg load`. Invoke headless with e.g.
# `cd <dir> && octave-cli --no-gui --norc script.m`. (Placed after the R block
# so the cached R layer is not invalidated by this addition.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    octave \
    liboctave-dev \
    octave-statistics \
    octave-optim \
    octave-signal \
    octave-image \
    octave-io \
    octave-control \
    octave-parallel \
    && rm -rf /var/lib/apt/lists/* \
    && octave-cli --no-gui --eval "disp(['Octave ' version]); disp('octave OK')"

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
    && ln -sf /usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js /usr/bin/gemini

# uv + Python
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
COPY --from=builder /python /python

ENV PATH="/app/.venv/bin:/python/bin:/usr/local/bin:${PATH}"
ENV VIRTUAL_ENV="/app/.venv"
ENV PYTHONPATH="/app"
ENV VERITAS_WORKSPACE=/workspace
ENV UV_PYTHON_INSTALL_DIR=/python UV_PYTHON_PREFERENCE=only-managed
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Bypass Gemini CLI's trusted-folders check for headless container runs.
ENV GEMINI_CLI_TRUST_WORKSPACE=true

# Entrypoint (before USER switch so we can write to /usr/local/bin)
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# Non-root user
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} veritas \
    && useradd -u ${UID} -g veritas -m -s /bin/bash veritas

# Workspace directory
RUN mkdir -p /workspace/repo /workspace/output \
    && chown -R veritas:veritas /workspace

# Copy veritas source and venv from builder, owned by the new user
COPY --from=builder --chown=veritas:veritas /app /app

# Stage the scientific-skills catalog at /workspace/veritas-skills/.
# This is a peer of /workspace/{repo,data,output} — a read-only resource
# directory veritas owns, separate from any provider-CLI credentials at
# /home/veritas/.{claude,codex,gemini}/. The agent's session prompts
# reference this path explicitly across all phases; we deliberately do
# not rely on any provider CLI's auto-discovery, so the same path works
# uniformly for claude, codex, and gemini. Baselines (which don't use
# veritas's prompts) never receive the "review skills" instruction and
# therefore have no incentive to look in this directory, preserving the
# baseline-vs-veritas isolation even when both run in this image.
RUN cp -a /app/templates/skills /workspace/veritas-skills \
    && chown -R veritas:veritas /workspace/veritas-skills

USER veritas
WORKDIR /workspace

# Git config
RUN git config --global user.email "noreply@veritas.dev" \
    && git config --global user.name "Veritas" \
    && git config --global init.defaultBranch main

# CLI credential directories.
# .codex-host is the RO mount target for the host's ~/.codex; .codex-container
# is the runtime CODEX_HOME the entrypoint copies into.
RUN mkdir -p ~/.claude ~/.codex ~/.codex-container ~/.codex-host ~/.gemini ~/.cache/uv

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
