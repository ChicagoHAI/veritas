#!/usr/bin/env bash
# =============================================================================
# Veritas Docker Runner
# Handles GPU passthrough, credential mounting, and path rewriting for
# containerized execution of the veritas pipeline.
# Invoked via the top-level `./veritas` wrapper.
# =============================================================================

set -e

# On Windows Git Bash (MINGW/MSYS), disable MSYS's automatic path translation
# for docker -v mount specs. Without this, MSYS mangles mount targets like
# /home/veritas/.claude into either C:/Program Files/Git/home/veritas/.claude
# (the MSYS /home tree) or \\home\veritas\.claude (UNC-style, if the source
# uses // to dodge earlier translation). Docker silently creates empty dirs
# at the wrong location and the credentials never reach the container.
# Docker Desktop on Windows accepts raw /c/Users/... Git-Bash-style paths
# just fine as mount sources.
if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
    export MSYS_NO_PATHCONV=1
fi

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
#
# On Windows Git Bash without winpty re-exec, [ -t 0 ] lies to us.
# In that case fall back to -i.
# The top-level ./veritas wrapper re-execs under winpty when available, so
# under normal use this fallback only kicks in when winpty is missing.
# -----------------------------------------------------------------------------
get_tty_flag() {
    if [ -t 0 ]; then
        # On MINGW/MSYS without winpty re-exec, [ -t 0 ] lies to us.
        if [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] \
            && [[ -z "$_VERITAS_WINPTY_REEXEC" ]]; then
            echo "-i"
        else
            echo "-it"
        fi
    else
        echo "-i"
    fi
}

# -----------------------------------------------------------------------------
# Auto-detect nvidia-container-toolkit AND actual GPU accessibility.
# Two-step check:
#   1. `docker info` mentions nvidia (toolkit installed)
#   2. A `docker run --gpus all ... nvidia-smi` probe succeeds
# The second step catches WSL / emulated envs where the toolkit is installed
# but no GPU adapter is reachable. Returns `--gpus all` only if both pass;
# empty string otherwise (with a stderr notice).
#
# Uses the veritas image itself for the probe when available (nvidia-smi
# ships in the nvidia/cuda runtime base it's built on). Falls back to the
# step-1 result if the image isn't pulled yet — at that point we haven't
# spent time to pull a 2GB probe image just to decide about a flag.
# -----------------------------------------------------------------------------
get_gpu_flags() {
    # Step 1: toolkit present?
    if ! docker info 2>/dev/null | grep -qi nvidia; then
        echo -e "${DIM}Running without GPU (nvidia-container-toolkit not configured)${NC}" >&2
        echo ""
        return
    fi

    # Step 2: GPU actually reachable? Probe with our image (nvidia-smi ships
    # in its nvidia/cuda base). If the image doesn't exist yet, trust step 1
    # — the user hasn't built yet, and a full probe would require pulling a
    # 2GB image just to decide about a flag.
    if ! docker image inspect "$IMAGE_NAME" &> /dev/null; then
        echo "--gpus all"
        return
    fi

    if docker run --rm --gpus all --entrypoint nvidia-smi "$IMAGE_NAME" \
            --query-gpu=name --format=csv,noheader &> /dev/null; then
        echo "--gpus all"
    else
        echo -e "${DIM}GPU toolkit detected but no GPU accessible (WSL/emulated?); running without --gpus all${NC}" >&2
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
# Extract --provider value from a subcommand's argv. Default: claude.
# Handles both `--provider X` and `--provider=X` forms.
# -----------------------------------------------------------------------------
extract_provider() {
    local provider="claude"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --provider) provider="$2"; shift 2 ;;
            --provider=*) provider="${1#*=}"; shift ;;
            *) shift ;;
        esac
    done
    echo "$provider"
}

# -----------------------------------------------------------------------------
# Verify the requested provider has usable credentials on the host. Exits
# with a clear actionable error BEFORE launching the container, so users
# don't watch an LLM-call hang for several minutes before realising they
# forgot to log in.
#
# macOS note: Claude Code stores credentials in the Keychain rather than
# ~/.claude/.credentials.json, so on Darwin we also probe the Keychain.
# -----------------------------------------------------------------------------
check_provider_credentials() {
    local provider="$1"
    case "$provider" in
        claude)
            if [[ "$(uname)" == "Darwin" ]] \
                && security find-generic-password -s "Claude Code-credentials" -w &>/dev/null; then
                return 0
            fi
            if [ -s "$HOME/.claude/.credentials.json" ]; then
                return 0
            fi
            echo -e "${RED}Claude credentials not found.${NC}" >&2
            echo -e "Run: ${BOLD}./veritas login claude${NC}" >&2
            exit 1
            ;;
        codex)
            if [ -d "$HOME/.codex" ] && [ -n "$(ls -A "$HOME/.codex" 2>/dev/null)" ]; then
                return 0
            fi
            echo -e "${RED}Codex credentials not found.${NC}" >&2
            echo -e "Run: ${BOLD}./veritas login codex${NC}" >&2
            exit 1
            ;;
        gemini)
            if [ -d "$HOME/.gemini" ] && [ -n "$(ls -A "$HOME/.gemini" 2>/dev/null)" ]; then
                return 0
            fi
            echo -e "${RED}Gemini credentials not found.${NC}" >&2
            echo -e "Run: ${BOLD}./veritas login gemini${NC}" >&2
            exit 1
            ;;
        *)
            echo -e "${RED}Unknown provider:${NC} $provider (expected claude|codex|gemini)" >&2
            exit 1
            ;;
    esac
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

    # Two-step GPU probe: toolkit + actual accessibility
    if docker info 2>/dev/null | grep -qi nvidia; then
        if docker image inspect "$IMAGE_NAME" &> /dev/null && \
           docker run --rm --gpus all --entrypoint nvidia-smi "$IMAGE_NAME" \
               --query-gpu=name --format=csv,noheader &> /dev/null; then
            echo -e "    GPU ................. ${GREEN}[OK]${NC} accessible"
        elif ! docker image inspect "$IMAGE_NAME" &> /dev/null; then
            echo -e "    GPU ................. ${DIM}[?]${NC} toolkit present; rebuild image to probe"
        else
            echo -e "    GPU ................. ${YELLOW}[WARN]${NC} toolkit present but no GPU reachable (WSL/emulated?)"
        fi
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

# -----------------------------------------------------------------------------
# Build the container image locally
# -----------------------------------------------------------------------------
cmd_build() {
    echo -e "${BLUE}Building veritas container image...${NC}"
    local platform_flag=$(get_platform_flags)
    if [ -n "$platform_flag" ]; then
        echo -e "  ${DIM}macOS detected — building for linux/amd64 (Rosetta emulation)${NC}"
    fi

    docker build $platform_flag -t "$IMAGE_NAME" -f "$PROJECT_ROOT/docker/Dockerfile" "$PROJECT_ROOT"

    echo -e "${GREEN}Build complete:${NC} $IMAGE_NAME"
}

# -----------------------------------------------------------------------------
# Pull latest image from GHCR
# -----------------------------------------------------------------------------
cmd_update() {
    echo -e "${BLUE}Updating veritas image...${NC}"
    local platform_flag=$(get_platform_flags)

    if docker pull $platform_flag "$REGISTRY_IMAGE"; then
        docker tag "$REGISTRY_IMAGE" "$IMAGE_NAME"
        echo -e "  ${GREEN}[OK]${NC} Image updated"
    else
        echo -e "  ${RED}[FAIL]${NC} Pull failed — check network or run: ./veritas build"
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# Status dashboard
# -----------------------------------------------------------------------------
cmd_status() {
    show_banner
    show_status
}

# -----------------------------------------------------------------------------
# Launch interactive login shell for a provider
# -----------------------------------------------------------------------------
cmd_login() {
    local provider="${1:-claude}"
    case "$provider" in
        claude|codex|gemini) ;;
        *) echo -e "${RED}Unknown provider: $provider${NC} (expected claude|codex|gemini)"; exit 1 ;;
    esac

    mkdir -p "$HOME/.claude" "$HOME/.codex" "$HOME/.gemini"
    ensure_credential_perms

    # Skip login if credentials already exist
    local cred_file=""
    case "$provider" in
        claude) cred_file="$HOME/.claude/.credentials.json" ;;
        codex)  cred_file="$HOME/.codex/auth.json" ;;
        gemini) cred_file="$HOME/.gemini/settings.json" ;;
    esac
    if [ -s "$cred_file" ]; then
        echo -e "  ${GREEN}[OK]${NC} Already logged in to $provider (credentials found at $cred_file)"
        echo -e "         To force re-login, delete that file and run this command again."
        return 0
    fi

    ensure_image

    echo -e "${BLUE}Launching $provider login...${NC}"
    echo ""

    local platform_flag=$(get_platform_flags)
    local gpu_flags=$(get_gpu_flags)

    eval "docker run -it --rm \
        $platform_flag \
        $gpu_flags \
        -v \"$HOME/.claude:/home/veritas/.claude\" \
        -v \"$HOME/.codex:/home/veritas/.codex\" \
        -v \"$HOME/.gemini:/home/veritas/.gemini\" \
        \"$IMAGE_NAME\" \
        $provider"
}

# -----------------------------------------------------------------------------
# Interactive bash shell inside the container (cwd mounted at /workspace)
# -----------------------------------------------------------------------------
cmd_shell() {
    ensure_image
    warn_if_outdated

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local gpu_flags=$(get_gpu_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $gpu_flags \
        $credential_mounts \
        -v \"$PWD:/workspace\" \
        -w /workspace \
        \"$IMAGE_NAME\" \
        bash"
}

# -----------------------------------------------------------------------------
# Path-rewriting helper for veritas subcommands.
# Given a list of --flag/value pairs (where some flags carry host paths),
# emit a rewritten arg list plus bind-mount flags.
#
# Sets two globals for the caller:
#   MOUNTS  — string of -v flags
#   ARGS    — the rewritten argv list
#
# Usage: rewrite_paths --paper /h/foo.pdf --repo /h/bar --output /h/out --provider claude
# -----------------------------------------------------------------------------
rewrite_paths() {
    MOUNTS=""
    ARGS=""
    local counter=0
    local saw_output=false
    local repo_host=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --paper|-p|--plan)
                local flag="$1"
                local host_path
                host_path=$(realpath "$2" 2>/dev/null || echo "$2")
                if [ ! -e "$host_path" ]; then
                    echo -e "${RED}File not found:${NC} $2" >&2
                    exit 1
                fi
                local basename
                basename=$(basename "$host_path")
                counter=$((counter + 1))
                local container_path="/workspace/inputs/file${counter}_${basename}"
                MOUNTS="$MOUNTS -v \"$host_path:$container_path:ro\""
                ARGS="$ARGS $flag \"$container_path\""
                shift 2
                ;;
            --repo|-r)
                local host_path
                host_path=$(realpath "$2" 2>/dev/null || echo "$2")
                if [ ! -d "$host_path" ]; then
                    echo -e "${RED}Repo not found:${NC} $2" >&2
                    exit 1
                fi
                repo_host="$host_path"
                MOUNTS="$MOUNTS -v \"$host_path:/workspace/repo:ro\""
                ARGS="$ARGS --repo /workspace/repo"
                shift 2
                ;;
            --output|-o)
                saw_output=true
                local host_path
                host_path=$(realpath -m "$2")
                mkdir -p "$host_path"
                # Container runs as veritas (UID 1000). If the host dir is
                # owned by another UID (e.g. root on a VM), UID 1000 can't
                # write into it — phases that mkdir subdirs like replication/
                # fail with "Permission denied". Make it world-writable.
                chmod -R a+rwX "$host_path" 2>/dev/null || true
                MOUNTS="$MOUNTS -v \"$host_path:/workspace/output\""
                ARGS="$ARGS --output /workspace/output"
                shift 2
                ;;
            *)
                # Pass through unchanged, quoting each token once
                ARGS="$ARGS \"$1\""
                shift
                ;;
        esac
    done

    # If the user didn't specify --output, default to <repo>/evaluation on
    # the host side. Matches the Python CLI's default intent but lands on
    # a writable mount — the --repo bind is read-only, so letting the CLI
    # compute /workspace/repo/evaluation internally would crash with
    # "Read-only file system" when it tries to mkdir the subdir.
    if [ "$saw_output" = false ] && [ -n "$repo_host" ]; then
        local default_output="$repo_host/evaluation"
        mkdir -p "$default_output"
        chmod -R a+rwX "$default_output" 2>/dev/null || true
        MOUNTS="$MOUNTS -v \"$default_output:/workspace/output\""
        ARGS="$ARGS --output /workspace/output"
    fi
}

# -----------------------------------------------------------------------------
# Evaluate (the primary subcommand)
# -----------------------------------------------------------------------------
cmd_evaluate() {
    ensure_image
    warn_if_outdated

    # Fast-fail if the requested provider has no credentials on the host.
    # Beats waiting for an inside-container LLM call to hang or error out.
    local provider=$(extract_provider "$@")
    check_provider_credentials "$provider"

    rewrite_paths "$@"

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local gpu_flags=$(get_gpu_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $gpu_flags \
        $credential_mounts \
        $MOUNTS \
        -w /workspace \
        \"$IMAGE_NAME\" \
        veritas evaluate $ARGS"
}

# -----------------------------------------------------------------------------
# Extract plan from a paper
# -----------------------------------------------------------------------------
cmd_extract_plan() {
    ensure_image

    # extract-plan currently uses the default provider (claude) internally.
    # Pre-flight that credential so the LLM call doesn't hang mid-run.
    check_provider_credentials claude

    # extract-plan takes a positional paper argument — rewrite it like --paper
    local paper="$1"; shift
    local host_paper
    host_paper=$(realpath "$paper" 2>/dev/null || echo "$paper")
    if [ ! -f "$host_paper" ]; then
        echo -e "${RED}Paper not found:${NC} $paper" >&2
        exit 1
    fi
    local basename=$(basename "$host_paper")
    local container_paper="/workspace/inputs/$basename"

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    # Handle --output specially (other args pass through)
    rewrite_paths "$@"

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $credential_mounts \
        -v \"$host_paper:$container_paper:ro\" \
        $MOUNTS \
        -w /workspace \
        \"$IMAGE_NAME\" \
        veritas extract-plan \"$container_paper\" $ARGS"
}

# -----------------------------------------------------------------------------
# Regenerate report from existing evaluation dir
# -----------------------------------------------------------------------------
cmd_report() {
    ensure_image

    local eval_dir="$1"; shift
    local host_eval_dir
    host_eval_dir=$(realpath "$eval_dir" 2>/dev/null || echo "$eval_dir")
    if [ ! -d "$host_eval_dir" ]; then
        echo -e "${RED}Evaluation dir not found:${NC} $eval_dir" >&2
        exit 1
    fi

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)

    eval "docker run $tty_flag --rm \
        $platform_flag \
        -v \"$host_eval_dir:/workspace/eval\" \
        -w /workspace \
        \"$IMAGE_NAME\" \
        veritas report /workspace/eval $@"
}

# -----------------------------------------------------------------------------
# Help
# -----------------------------------------------------------------------------
show_help() {
    show_banner
    echo -e "${BOLD}Usage:${NC} ./veritas <command> [args...]"
    echo ""
    echo -e "${BOLD}Commands:${NC}"
    echo "  evaluate      Run the full replication pipeline"
    echo "                  e.g. ./veritas evaluate --paper p.pdf --repo ./myrepo"
    echo "  extract-plan  Extract a structured plan from a paper PDF"
    echo "  report        Regenerate a report from an existing evaluation dir"
    echo "  shell         Interactive bash inside the container (cwd mounted as /workspace)"
    echo "  login         Log in to an AI provider (claude|codex|gemini)"
    echo "  build         Build the image locally"
    echo "  update        Pull the latest image from GHCR"
    echo "  status        Show status dashboard (Docker, image, GPU, credentials)"
    echo "  help          Show this help"
    echo ""
    echo -e "${BOLD}First-time setup:${NC}"
    echo "  1. ./veritas login claude"
    echo "  2. ./veritas evaluate --paper <your-paper.pdf> --repo <your-repo>"
    echo ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        evaluate)      cmd_evaluate "$@" ;;
        extract-plan)  cmd_extract_plan "$@" ;;
        report)        cmd_report "$@" ;;
        shell)         cmd_shell "$@" ;;
        login)         cmd_login "$@" ;;
        build)         cmd_build ;;
        update)        cmd_update ;;
        status)        cmd_status ;;
        help|-h|--help) show_help ;;
        *)
            echo -e "${RED}Unknown command:${NC} $cmd"
            echo "Run: ./veritas help"
            exit 1
            ;;
    esac
}

main "$@"
