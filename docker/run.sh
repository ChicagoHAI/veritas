#!/usr/bin/env bash
# =============================================================================
# Veritas Docker Runner
# Handles GPU passthrough, credential mounting, and path rewriting for
# containerized execution of the veritas pipeline.
# Invoked via the top-level `./veritas` wrapper.
# =============================================================================

set -e

# Locate the veritas project root (two levels up from this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGE_NAME="chicagohai/veritas:latest"
REGISTRY_IMAGE="ghcr.io/chicagohai/veritas:latest"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# -----------------------------------------------------------------------------
# Cross-platform sed helper
# macOS BSD sed requires an explicit empty-string backup argument.
# GNU sed on Linux does not. This wrapper handles both.
# -----------------------------------------------------------------------------
sed_inplace() {
    if [[ "$(uname)" == "Darwin" ]]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# -----------------------------------------------------------------------------
# Return the content digest of a remote registry image.
# Uses `docker buildx imagetools inspect` which reports the manifest-list
# digest (what docker stores in RepoDigests after a pull), not per-platform
# digests as `docker manifest inspect` would.
# -----------------------------------------------------------------------------
get_remote_digest() {
    local image="$1"
    local digest
    digest=$(docker buildx imagetools inspect "$image" 2>/dev/null \
        | awk '/^Digest:/{print $2; exit}')
    echo "$digest"
}

# -----------------------------------------------------------------------------
# Only allocate a pseudo-terminal when stdin is one. Allows veritas to be
# invoked as a subprocess without failing.
# -----------------------------------------------------------------------------
get_tty_flag() {
    if [ -t 0 ]; then
        echo "-it"
    else
        echo "-i"
    fi
}

# -----------------------------------------------------------------------------
# Auto-detect nvidia-container-toolkit. Returns --gpus all when available,
# empty string otherwise. Prints a notice on stderr if GPU is unavailable.
# -----------------------------------------------------------------------------
get_gpu_flags() {
    if docker info 2>/dev/null | grep -qi nvidia; then
        echo "--gpus all"
    else
        echo -e "${DIM}Running without GPU (nvidia-container-toolkit not configured)${NC}" >&2
        echo ""
    fi
}

# -----------------------------------------------------------------------------
# On macOS, force linux/amd64 because nvidia/cuda base images have no arm64
# build. Docker Desktop uses Rosetta emulation.
# -----------------------------------------------------------------------------
get_platform_flags() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "--platform linux/amd64"
    else
        echo ""
    fi
}

# -----------------------------------------------------------------------------
# Return docker mount flags for CLI credential directories.
# Claude/Codex/Gemini use OAuth; credentials live in ~/.{claude,codex,gemini}/.
# On macOS, Claude stores them in the Keychain — extract to file pre-mount.
# Dirs are mounted writable so in-container `./veritas login` persists tokens.
# -----------------------------------------------------------------------------
get_cli_credential_mounts() {
    local mounts=""
    local found_any=false

    echo -e "${BLUE}Checking CLI credentials...${NC}" >&2

    # macOS Keychain extraction for Claude
    if [[ "$(uname)" == "Darwin" ]]; then
        local creds_file="$HOME/.claude/.credentials.json"
        local keychain_creds
        keychain_creds=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null || true)
        if [ -n "$keychain_creds" ]; then
            mkdir -p "$HOME/.claude"
            echo "$keychain_creds" > "$creds_file"
            echo -e "  ${GREEN}[OK]${NC} Extracted Claude credentials from Keychain" >&2
        fi
    fi

    for dir in .claude .codex .gemini; do
        if [ -d "$HOME/$dir" ]; then
            mounts="$mounts -v \"$HOME/$dir:/home/veritas/$dir\""
            if [ "$(ls -A "$HOME/$dir" 2>/dev/null)" ]; then
                echo -e "  ${GREEN}[OK]${NC} Mounting $dir credentials" >&2
            else
                echo -e "  ${DIM}[--]${NC} Mounting $dir (empty — run: ./veritas login $dir)" >&2
            fi
            found_any=true
        fi
    done

    if [ "$found_any" = false ]; then
        echo -e "  ${YELLOW}[WARN]${NC} No CLI credentials found." >&2
        echo -e "         Run: ./veritas login claude" >&2
    fi

    echo "" >&2
    echo "$mounts"
}

# -----------------------------------------------------------------------------
# Make credential dirs readable/writable by any UID. The container runs as
# veritas (uid 1000) which may not match the host user.
# -----------------------------------------------------------------------------
ensure_credential_perms() {
    chmod -R a+rwX "$HOME/.claude" "$HOME/.codex" "$HOME/.gemini" 2>/dev/null || true
}

# -----------------------------------------------------------------------------
# Non-blocking notice: if a newer image is on GHCR, print it. Don't pull.
# -----------------------------------------------------------------------------
warn_if_outdated() {
    local local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE_NAME" 2>/dev/null | sed 's/.*@//')
    local remote_digest=$(get_remote_digest "$REGISTRY_IMAGE")

    if [ -n "$local_digest" ] && [ -n "$remote_digest" ] && [ "$local_digest" != "$remote_digest" ]; then
        echo -e "${YELLOW}Update available:${NC} newer image on registry. Run './veritas update' to pull."
        echo ""
    fi
}

# -----------------------------------------------------------------------------
# Pull-first, build-fallback: used on first invocation when image is missing.
# Declared before cmd_build (which ensure_image calls) — in bash, function
# resolution is at call time, so the forward reference is fine.
# -----------------------------------------------------------------------------
ensure_image() {
    if docker image inspect "$IMAGE_NAME" &> /dev/null; then
        return 0
    fi

    echo -e "${BLUE}Image $IMAGE_NAME not found locally; pulling from registry...${NC}"
    local platform_flag=$(get_platform_flags)
    if docker pull $platform_flag "$REGISTRY_IMAGE" 2>/dev/null; then
        docker tag "$REGISTRY_IMAGE" "$IMAGE_NAME"
        echo -e "  ${GREEN}[OK]${NC} Pulled from registry"
        return 0
    fi

    echo -e "  ${YELLOW}[WARN]${NC} Pull failed — falling back to local build (~10 min)"
    cmd_build
}

# -----------------------------------------------------------------------------
# Banner
# -----------------------------------------------------------------------------
show_banner() {
    echo -e "${BLUE}${BOLD}"
    echo '  __     __         _ _            '
    echo '  \ \   / /__ _ __(_) |_ __ _ ___ '
    echo '   \ \ / / _ \ '\''__| | __/ _` / __|'
    echo '    \ V /  __/ |  | | || (_| \__ \'
    echo '     \_/ \___|_|  |_|\__\__,_|___/'
    echo -e "${NC}"
    echo -e "  ${DIM}Replication Agent${NC}  ${DIM}github.com/ChicagoHAI/veritas${NC}"
    echo ""
}

# -----------------------------------------------------------------------------
# Status dashboard
# -----------------------------------------------------------------------------
show_status() {
    echo -e "  ${BOLD}Status:${NC}"

    if command -v docker &> /dev/null; then
        echo -e "    Docker .............. ${GREEN}[OK]${NC}"
    else
        echo -e "    Docker .............. ${RED}[MISSING]${NC}"
    fi

    if docker image inspect "$IMAGE_NAME" &> /dev/null; then
        local local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE_NAME" 2>/dev/null | sed 's/.*@//')
        local remote_digest=$(get_remote_digest "$REGISTRY_IMAGE")
        if [ -n "$local_digest" ] && [ -n "$remote_digest" ] && [ "$local_digest" != "$remote_digest" ]; then
            echo -e "    Docker image ........ ${YELLOW}[UPDATE AVAILABLE]${NC}"
        else
            echo -e "    Docker image ........ ${GREEN}[OK]${NC}"
        fi
    else
        echo -e "    Docker image ........ ${YELLOW}[MISSING]${NC} run: ./veritas build (or let first run pull)"
    fi

    if docker info 2>/dev/null | grep -qi nvidia; then
        echo -e "    GPU ................. ${GREEN}[OK]${NC} nvidia-container-toolkit"
    else
        echo -e "    GPU ................. ${DIM}[--]${NC} no toolkit (optional)"
    fi

    # Credentials
    local claude_ok=false
    if [[ "$(uname)" == "Darwin" ]]; then
        if security find-generic-password -s "Claude Code-credentials" -w &>/dev/null; then
            claude_ok=true
        fi
    elif [ -s "$HOME/.claude/.credentials.json" ]; then
        claude_ok=true
    fi
    if [ "$claude_ok" = true ]; then
        echo -e "    Claude credentials .. ${GREEN}[OK]${NC}"
    else
        echo -e "    Claude credentials .. ${DIM}[--]${NC} run: ./veritas login claude"
    fi

    if [ -d "$HOME/.codex" ] && [ "$(ls -A "$HOME/.codex" 2>/dev/null)" ]; then
        echo -e "    Codex credentials ... ${GREEN}[OK]${NC}"
    else
        echo -e "    Codex credentials ... ${DIM}[--]${NC}"
    fi

    if [ -d "$HOME/.gemini" ] && [ "$(ls -A "$HOME/.gemini" 2>/dev/null)" ]; then
        echo -e "    Gemini credentials .. ${GREEN}[OK]${NC}"
    else
        echo -e "    Gemini credentials .. ${DIM}[--]${NC}"
    fi

    echo ""
}
