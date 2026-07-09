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
    digest=$(timeout 5 docker buildx imagetools inspect "$image" 2>/dev/null \
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
# Return a "name, VRAM" line per reachable GPU (semicolon-joined for a single
# env-var-safe line), or empty if none. This IS the GPU-availability signal
# passed to the prompts — its emptiness means "no GPU" (or unknown), its
# presence means "GPU available, and here's what it is." No separate
# available/unavailable boolean: a bare "yes" doesn't tell codegen whether
# the paper's methodology actually fits in the available VRAM, so there's no
# reason to carry both.
#
# Takes the already-computed $gpu_flags result (empty or "--gpus all") so it
# doesn't repeat get_gpu_flags's own toolkit/image-existence checks.
# -----------------------------------------------------------------------------
get_gpu_info() {
    local gpu_flags="$1"
    if [ -z "$gpu_flags" ]; then
        echo ""
        return
    fi
    docker run --rm --gpus all --entrypoint nvidia-smi "$IMAGE_NAME" \
        --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null \
        | tr '\n' ';' | sed 's/;$//'
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

    # Redirect the Claude CLI's config-file lookup into the mounted .claude
    # directory. Without this it looks for $HOME/.claude.json (sibling of the
    # mounted .claude/ dir, not inside it), doesn't find it, and prints a
    # "manually restore from backup" hint on every launch.
    mounts="$mounts -e CLAUDE_CONFIG_DIR=/home/veritas/.claude"

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
            # Codex mounts RO at .codex-host; entrypoint redirects CODEX_HOME
            # to a container-private copy. cmd_login keeps the RW path.
            if [ "$dir" = ".codex" ]; then
                mounts="$mounts -v \"$HOME/.codex:/home/veritas/.codex-host:ro\""
            else
                mounts="$mounts -v \"$HOME/$dir:/home/veritas/$dir\""
            fi
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
# .env helpers (shared input/edit primitives)
# -----------------------------------------------------------------------------

# Read the current value of an env var from .env (uncommented lines only).
get_env_value() {
    local var_name="$1"
    if [ -f "$PROJECT_ROOT/.env" ]; then
        grep -E "^${var_name}=" "$PROJECT_ROOT/.env" 2>/dev/null | head -1 | sed "s/^${var_name}=//"
    fi
}

# Mask a secret for display (first 4 + last 4 chars; values <=8 chars show ****).
mask_value() {
    local val="$1"
    local len=${#val}
    if [ "$len" -le 8 ]; then
        echo "****"
    else
        echo "${val:0:4}...${val:len-4:4}"
    fi
}

# Read input with masked display (shows * for each character typed).
# Uses stty rather than `read -s` so it works when stdin is a pipe.
read_masked() {
    local __resultvar="$1"
    local _input="" _char=""

    local old_stty
    old_stty=$(stty -g < /dev/tty 2>/dev/null)
    stty -echo < /dev/tty 2>/dev/null
    trap 'stty '"$old_stty"' < /dev/tty 2>/dev/null; trap - INT TERM' INT TERM

    while true; do
        IFS= read -r -n 1 _char < /dev/tty

        if [[ -z "$_char" ]]; then
            break
        fi

        if [[ "$_char" == $'\x7f' ]] || [[ "$_char" == $'\x08' ]]; then
            if [ ${#_input} -gt 0 ]; then
                _input="${_input%?}"
                echo -ne '\b \b' >&2
            fi
        else
            _input+="$_char"
            echo -ne '*' >&2
        fi
    done

    echo "" >&2

    stty "$old_stty" < /dev/tty 2>/dev/null
    trap - INT TERM

    printf -v "$__resultvar" '%s' "$_input"
}

# Return formatted status string for a config variable.
# Usage: format_status "VAR_NAME" [is_secret]
format_status() {
    local var_name="$1"
    local is_secret="${2:-true}"
    local val
    val=$(get_env_value "$var_name")
    if [ -n "$val" ]; then
        if [ "$is_secret" = "true" ]; then
            echo -e "${GREEN}[SET: $(mask_value "$val")]${NC}"
        else
            echo -e "${GREEN}[SET: $val]${NC}"
        fi
    else
        echo -e "${DIM}[NOT SET]${NC}"
    fi
}

# Write a value to .env: replace existing line, uncomment commented line, or append.
config_set_env() {
    local var_name="$1"
    local value="$2"
    if grep -q "^${var_name}=" "$PROJECT_ROOT/.env" 2>/dev/null; then
        sed_inplace "s|^${var_name}=.*|${var_name}=${value}|" "$PROJECT_ROOT/.env"
    elif grep -q "^# *${var_name}=" "$PROJECT_ROOT/.env" 2>/dev/null; then
        sed_inplace "s|^# *${var_name}=.*|${var_name}=${value}|" "$PROJECT_ROOT/.env"
    else
        echo "${var_name}=${value}" >> "$PROJECT_ROOT/.env"
    fi
}

# Prompt for a secret value (masked input). Writes to .env on success.
# Usage: prompt_secret "Label" "ENV_VAR" "required|optional" "validation_prefix" ["hint"]
prompt_secret() {
    local label="$1"
    local env_var="$2"
    local required="$3"
    local prefix="$4"

    if [ "$required" = "required" ]; then
        echo -e "    ${BOLD}$label${NC} (recommended)"
    else
        echo -e "    ${BOLD}$label${NC} (optional)"
    fi

    if [ -n "$5" ]; then
        echo -e "    ${DIM}$5${NC}"
    fi

    local value=""
    if [ "$required" = "optional" ]; then
        echo -ne "    > ${DIM}[Enter to skip]${NC} "
    else
        echo -ne "    > "
    fi
    read_masked value

    if [ -z "$value" ]; then
        echo -e "    ${DIM}[SKIP]${NC} $label skipped"
        return 1
    fi

    echo -e "    ${DIM}Entered: $(mask_value "$value") (${#value} chars)${NC}"

    if [ -n "$prefix" ] && [[ ! "$value" == $prefix* ]]; then
        echo -e "    ${YELLOW}[WARN]${NC} Expected value starting with '$prefix' — saving anyway"
    fi

    config_set_env "$env_var" "$value"

    echo -e "    ${GREEN}[OK]${NC} $env_var saved"
    return 0
}

# Prompt for a non-secret value with optional default. Sets REPLY.
prompt_text() {
    local label="$1"
    local hint="$2"
    local default_val="$3"

    echo -e "    ${BOLD}$label${NC} (optional)"
    if [ -n "$hint" ]; then
        echo -e "    ${DIM}$hint${NC}"
    fi

    if [ -n "$default_val" ]; then
        echo -ne "    > ${DIM}[Enter for '$default_val']${NC} "
    else
        echo -ne "    > ${DIM}[Enter to skip]${NC} "
    fi
    local value=""
    read value < /dev/tty

    if [ -z "$value" ]; then
        REPLY="$default_val"
    else
        REPLY="$value"
    fi
}

# Numbered menu prompt. Sets REPLY to the selected number (1-based).
prompt_choice() {
    local header="$1"
    shift
    local options=("$@")

    echo -e "    ${BOLD}$header${NC}"
    local i=1
    for opt in "${options[@]}"; do
        echo "      [$i] $opt"
        ((i++))
    done

    local selection=""
    while true; do
        echo -ne "    > "
        read selection < /dev/tty
        if [[ "$selection" =~ ^[0-9]+$ ]] && [ "$selection" -ge 1 ] && [ "$selection" -le "${#options[@]}" ]; then
            REPLY="$selection"
            return
        fi
        echo -e "    ${YELLOW}Please enter a number between 1 and ${#options[@]}${NC}"
    done
}

# Print a non-fatal warning if .env is missing. Called from cmd_replicate /
# cmd_shell so users running an LLM-based paper get an actionable hint
# without breaking workflows for papers that don't need keys.
check_env_file_warn() {
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        echo -e "${YELLOW}Note:${NC} no .env found at $PROJECT_ROOT/.env" >&2
        echo -e "      Papers that call LLM APIs (e.g. hypogenic, PaperBench) will fail without keys." >&2
        echo -e "      Run ${BOLD}./veritas setup${NC} to configure, or ${BOLD}cp .env.example .env${NC} to start manually." >&2
        echo "" >&2
    fi
}

# Restrict .env to owner-only since it holds raw API keys. Idempotent and
# silent on missing file — safe to call from any subcommand.
ensure_env_perms() {
    if [ -f "$PROJECT_ROOT/.env" ]; then
        chmod 600 "$PROJECT_ROOT/.env" 2>/dev/null || true
    fi
}

# Compute a comma-separated list of variable names defined in .env (uncommented
# `VAR=value` lines only). Echoed to stdout. Empty string if .env is absent or
# defines no keys. Used to build `VERITAS_ENV_FILE_KEYS` for the container.
compute_env_file_keys() {
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        echo ""
        return
    fi
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$PROJECT_ROOT/.env" 2>/dev/null \
        | sed 's/=.*//' \
        | tr '\n' ',' \
        | sed 's/,$//'
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
            if is_claude_configured; then
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

# Return 0 if Claude OAuth credentials are present on this host, 1 otherwise.
# On macOS, Claude Code stores credentials in the Keychain; on Linux/Windows
# they live in ~/.claude/.credentials.json. Check both.
is_claude_configured() {
    if [[ "$(uname)" == "Darwin" ]] \
        && security find-generic-password -s "Claude Code-credentials" -w &>/dev/null; then
        return 0
    fi
    if [ -s "$HOME/.claude/.credentials.json" ]; then
        return 0
    fi
    return 1
}

# -----------------------------------------------------------------------------
# Non-blocking notice: if a newer image is on GHCR, print it. Don't pull.
# -----------------------------------------------------------------------------
warn_if_outdated() {
    local local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE_NAME" 2>/dev/null | sed 's/.*@//')
    # Skip registry probe entirely for locally-built images (no RepoDigests).
    if [ -z "$local_digest" ]; then
        return
    fi
    local remote_digest=$(get_remote_digest "$REGISTRY_IMAGE")
    if [ -n "$remote_digest" ] && [ "$local_digest" != "$remote_digest" ]; then
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

    # Fast, local checks first so the dashboard is responsive even when the
    # network or GPU container probe is slow.

    if command -v docker &> /dev/null; then
        echo -e "    Docker .............. ${GREEN}[OK]${NC}"
    else
        echo -e "    Docker .............. ${RED}[MISSING]${NC}"
    fi

    # .env (replication API keys)
    if [ -f "$PROJECT_ROOT/.env" ]; then
        echo -e "    .env ................ ${GREEN}[OK]${NC}"
    else
        echo -e "    .env ................ ${DIM}[--]${NC} run: ./veritas setup (or cp .env.example .env)"
    fi

    # Credentials
    if is_claude_configured; then
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

    # GPU toolkit detection (fast `docker info` check). The slow GPU
    # container probe is deferred to the end of this function.
    local gpu_toolkit_present=false
    if docker info 2>/dev/null | grep -qi nvidia; then
        gpu_toolkit_present=true
    fi

    # Docker image — local inspect is fast; the registry digest comparison
    # is a network call (now bounded by `timeout 5` in get_remote_digest)
    # and is skipped entirely for locally-built images.
    if docker image inspect "$IMAGE_NAME" &> /dev/null; then
        local local_digest=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE_NAME" 2>/dev/null | sed 's/.*@//')
        if [ -z "$local_digest" ]; then
            # Locally-built image (no RepoDigests). Skip the registry comparison.
            echo -e "    Docker image ........ ${GREEN}[OK]${NC} (locally built)"
        else
            local remote_digest=$(get_remote_digest "$REGISTRY_IMAGE")
            if [ -n "$remote_digest" ] && [ "$local_digest" != "$remote_digest" ]; then
                echo -e "    Docker image ........ ${YELLOW}[UPDATE AVAILABLE]${NC}"
            else
                echo -e "    Docker image ........ ${GREEN}[OK]${NC}"
            fi
        fi
    else
        echo -e "    Docker image ........ ${YELLOW}[MISSING]${NC} run: ./veritas build (or let first run pull)"
    fi

    # Two-step GPU probe: toolkit + actual accessibility. The container spawn
    # is the slowest check in show_status, so it runs last.
    if [ "$gpu_toolkit_present" = true ]; then
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

    # VERITAS_LOGIN_ONLY=1 keeps the RW path on ~/.codex so OAuth tokens persist.
    # CLAUDE_CONFIG_DIR matches the value in get_cli_credential_mounts so the
    # .claude.json the CLI writes during auth lands inside the mounted dir.
    eval "docker run -it --rm \
        $platform_flag \
        $gpu_flags \
        -e VERITAS_LOGIN_ONLY=1 \
        -e CLAUDE_CONFIG_DIR=/home/veritas/.claude \
        -v \"$HOME/.claude:/home/veritas/.claude\" \
        -v \"$HOME/.codex:/home/veritas/.codex\" \
        -v \"$HOME/.gemini:/home/veritas/.gemini\" \
        \"$IMAGE_NAME\" \
        $provider"
}

# Print a one-page prereqs check. Exits with code 1 if any required tool is
# missing. Used by cmd_setup as Step 1.
check_prerequisites() {
    local all_ok=true

    if command -v docker &> /dev/null; then
        echo -e "    ${GREEN}[OK]${NC} docker found"
    else
        echo -e "    ${RED}[MISSING]${NC} docker not found — install Docker first"
        all_ok=false
    fi

    if command -v git &> /dev/null; then
        echo -e "    ${GREEN}[OK]${NC} git found"
    else
        echo -e "    ${YELLOW}[WARN]${NC} git not found (recommended for cloning paper repos)"
    fi

    if docker info 2>/dev/null | grep -qi nvidia; then
        echo -e "    ${GREEN}[OK]${NC} nvidia-container-toolkit (GPU support)"
    else
        echo -e "    ${DIM}[--]${NC} nvidia-container-toolkit not detected (GPU optional)"
    fi

    if [ "$all_ok" = false ]; then
        echo ""
        echo -e "    ${RED}Missing required tools.${NC} Install them and re-run ./veritas setup."
        exit 1
    fi
}

# Walk the user through editing each of the 6 keys non-interactively (no menu).
# Used as the .env step inside cmd_setup. Idempotent: skips a key if the user
# hits Enter without typing.
setup_env_interactive() {
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        if [ -f "$PROJECT_ROOT/.env.example" ]; then
            cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
        else
            touch "$PROJECT_ROOT/.env"
        fi
    fi

    echo -e "    ${DIM}Press Enter to skip any key. Run ./veritas config later to revisit.${NC}"
    echo ""

    prompt_secret "OpenAI API Key" "OPENAI_API_KEY" "optional" "sk-" \
        "GPT family — required by hypogenic, PaperBench, many ML papers" || true
    echo ""
    prompt_secret "Anthropic API Key" "ANTHROPIC_API_KEY" "optional" "sk-ant-" \
        "Claude API — independent of veritas's Claude Code OAuth" || true
    echo ""
    prompt_secret "Google API Key" "GOOGLE_API_KEY" "optional" "" \
        "Gemini API access" || true
    echo ""
    prompt_secret "OpenRouter API Key" "OPENROUTER_API_KEY" "optional" "sk-or-" \
        "Multi-model routing (https://openrouter.ai)" || true
    echo ""
    prompt_secret "Hugging Face Token" "HF_TOKEN" "optional" "hf_" \
        "Gated models / datasets (Llama-2, ImageNet)" || true
    echo ""
    prompt_secret "Weights & Biases API Key" "WANDB_API_KEY" "optional" "" \
        "Experiment tracking (https://wandb.ai)" || true

    ensure_env_perms
}

# -----------------------------------------------------------------------------
# Interactive .env edit menu
# -----------------------------------------------------------------------------
cmd_config() {
    # Create .env from template on first invocation
    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        if [ -f "$PROJECT_ROOT/.env.example" ]; then
            cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
            echo -e "  ${GREEN}[OK]${NC} Created .env from template"
        else
            touch "$PROJECT_ROOT/.env"
            echo -e "  ${GREEN}[OK]${NC} Created empty .env"
        fi
        ensure_env_perms
        echo ""
    fi

    while true; do
        echo ""
        echo -e "  ${BOLD}Replication API Keys${NC}"
        echo -e "  ${DIM}Select a key to edit, or 'q' to save and exit.${NC}"
        echo ""
        echo -e "  ${BOLD}LLM providers${NC}"
        echo -e "    ${BOLD}[1]${NC}  OpenAI API Key ......... $(format_status OPENAI_API_KEY true)"
        echo -e "    ${BOLD}[2]${NC}  Anthropic API Key ...... $(format_status ANTHROPIC_API_KEY true)"
        echo -e "    ${BOLD}[3]${NC}  Google API Key ......... $(format_status GOOGLE_API_KEY true)"
        echo -e "    ${BOLD}[4]${NC}  OpenRouter API Key ..... $(format_status OPENROUTER_API_KEY true)"
        echo ""
        echo -e "  ${BOLD}Data / experiment infrastructure${NC}"
        echo -e "    ${BOLD}[5]${NC}  Hugging Face Token ..... $(format_status HF_TOKEN true)"
        echo -e "    ${BOLD}[6]${NC}  Weights & Biases Key ... $(format_status WANDB_API_KEY true)"
        echo ""
        echo -e "    ${BOLD}[q]${NC}  Save & exit"
        echo ""
        echo -ne "  > "
        local choice=""
        read choice < /dev/tty

        case "$choice" in
            1)
                echo ""
                prompt_secret "OpenAI API Key" "OPENAI_API_KEY" "optional" "sk-" \
                    "GPT family — required by hypogenic, PaperBench, many ML papers" || true
                ensure_env_perms
                ;;
            2)
                echo ""
                prompt_secret "Anthropic API Key" "ANTHROPIC_API_KEY" "optional" "sk-ant-" \
                    "Claude API — independent of veritas's Claude Code OAuth" || true
                ensure_env_perms
                ;;
            3)
                echo ""
                prompt_secret "Google API Key" "GOOGLE_API_KEY" "optional" "" \
                    "Gemini API access" || true
                ensure_env_perms
                ;;
            4)
                echo ""
                prompt_secret "OpenRouter API Key" "OPENROUTER_API_KEY" "optional" "sk-or-" \
                    "Multi-model routing (https://openrouter.ai)" || true
                ensure_env_perms
                ;;
            5)
                echo ""
                prompt_secret "Hugging Face Token" "HF_TOKEN" "optional" "hf_" \
                    "Gated models / datasets (Llama-2, ImageNet)" || true
                ensure_env_perms
                ;;
            6)
                echo ""
                prompt_secret "Weights & Biases API Key" "WANDB_API_KEY" "optional" "" \
                    "Experiment tracking (https://wandb.ai)" || true
                ensure_env_perms
                ;;
            q|Q|"")
                echo ""
                echo -e "  ${GREEN}Saved to .env${NC}"
                echo ""
                return
                ;;
            *)
                echo -e "  ${YELLOW}Invalid choice. Enter 1-6 or q to exit.${NC}"
                ;;
        esac

        echo ""
        echo -ne "  ${DIM}Press Enter to continue...${NC}"
        read < /dev/tty
    done
}

# -----------------------------------------------------------------------------
# Interactive setup wizard. Single unified flow — each step is skippable.
# Sequence: prerequisites -> image -> provider login -> .env (optional) -> done.
# -----------------------------------------------------------------------------
cmd_setup() {
    show_banner

    echo -e "${BOLD}  Welcome to Veritas${NC}"
    echo -e "  ${DIM}This wizard will get you set up. Hit Ctrl+C any time to bail.${NC}"
    echo ""

    # Step 1: prerequisites
    echo -e "  ${BOLD}Step 1/4: Checking prerequisites${NC}"
    check_prerequisites
    echo ""

    # Step 2: docker image
    echo -e "  ${BOLD}Step 2/4: Docker image${NC}"
    ensure_image
    echo ""

    # Step 3: provider login (offer all three; default Claude)
    echo -e "  ${BOLD}Step 3/4: Log in to an AI provider${NC}"
    echo -e "    ${DIM}Each provider uses OAuth. Pick one or more — you can add more later.${NC}"
    echo ""

    local claude_status="" codex_status="" gemini_status=""
    if is_claude_configured; then
        claude_status=" ${GREEN}[already configured]${NC}"
    fi
    if [ -d "$HOME/.codex" ] && [ "$(ls -A "$HOME/.codex" 2>/dev/null)" ]; then
        codex_status=" ${GREEN}[already configured]${NC}"
    fi
    if [ -d "$HOME/.gemini" ] && [ "$(ls -A "$HOME/.gemini" 2>/dev/null)" ]; then
        gemini_status=" ${GREEN}[already configured]${NC}"
    fi

    echo -e "    ${BOLD}Which providers do you want to log in to?${NC}"
    echo -e "      [1] Claude (recommended)${claude_status}"
    echo -e "      [2] Codex${codex_status}"
    echo -e "      [3] Gemini${gemini_status}"
    echo "      [4] Skip"
    echo -e "    ${DIM}Enter one or more numbers, e.g. 1 2 or 1,2,3${NC}"
    echo -ne "    > "
    local login_input=""
    read login_input < /dev/tty
    login_input="${login_input//,/ }"

    if [ -z "$login_input" ]; then
        echo -e "    ${DIM}[SKIP]${NC} No selection — run ./veritas login <provider> later"
    else
        for choice in $login_input; do
            case "$choice" in
                1) cmd_login claude ;;
                2) cmd_login codex ;;
                3) cmd_login gemini ;;
                4) echo -e "    ${DIM}[SKIP]${NC} Login deferred — run ./veritas login <provider> later" ;;
                *) echo -e "    ${YELLOW}[WARN]${NC} Ignoring unknown choice '$choice'" ;;
            esac
        done
    fi
    echo ""

    # Step 4: .env (optional)
    echo -e "  ${BOLD}Step 4/4: Replication API keys (.env)${NC}"
    if [ -f "$PROJECT_ROOT/.env" ]; then
        echo -e "    ${GREEN}[OK]${NC} .env already exists at $PROJECT_ROOT/.env"
        echo -ne "    Reconfigure? [y/N] "
        local reconfigure=""
        read reconfigure < /dev/tty
        if [[ "$reconfigure" =~ ^[Yy] ]]; then
            echo ""
            setup_env_interactive
        else
            echo -e "    ${DIM}Keeping existing .env${NC}"
        fi
    else
        echo -e "    ${DIM}If you're replicating a paper that calls LLM APIs (e.g. hypogenic),${NC}"
        echo -e "    ${DIM}configure keys now. Otherwise hit Enter to skip every prompt.${NC}"
        echo -ne "    Configure now? [Y/n] "
        local configure_now=""
        read configure_now < /dev/tty
        if [[ ! "$configure_now" =~ ^[Nn] ]]; then
            echo ""
            setup_env_interactive
        else
            echo -e "    ${DIM}[SKIP]${NC} Run ./veritas config later to add keys"
        fi
    fi
    echo ""

    # Done
    echo -e "  ${GREEN}Setup complete.${NC}"
    echo ""
    echo -e "  ${BOLD}Next steps:${NC}"
    echo -e "    ${DIM}Run a replication:${NC}  ${BOLD}./veritas replicate --paper paper.pdf --repo ./my-project${NC}"
    echo -e "    ${DIM}Status check:${NC}     ${BOLD}./veritas status${NC}"
    echo -e "    ${DIM}Edit keys:${NC}        ${BOLD}./veritas config${NC}"
    echo ""
}

# -----------------------------------------------------------------------------
# Interactive bash shell inside the container (cwd mounted at /workspace)
# -----------------------------------------------------------------------------
cmd_shell() {
    ensure_image
    warn_if_outdated

    check_env_file_warn
    ensure_env_perms

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local gpu_flags=$(get_gpu_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    local env_file_flag=""
    local env_keys_flag=""
    if [ -f "$PROJECT_ROOT/.env" ]; then
        env_file_flag="--env-file \"$PROJECT_ROOT/.env\""
        local keys
        keys=$(compute_env_file_keys)
        if [ -n "$keys" ]; then
            env_keys_flag="-e VERITAS_ENV_FILE_KEYS=$keys"
        fi
    fi

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $gpu_flags \
        $credential_mounts \
        $env_file_flag \
        $env_keys_flag \
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
# Usage: rewrite_paths --paper /h/foo.pdf --repo /h/bar --data /h/data --claims /h/c.json --output /h/out --provider claude
# -----------------------------------------------------------------------------
rewrite_paths() {
    MOUNTS=""
    ARGS=""
    local counter=0
    local saw_output=false
    local repo_host=""
    local paper_host=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --paper|-p)
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
                # Remember the first --paper host path for the output fallback
                # when --repo is absent (mode 2).
                if [ "$1" = "--paper" ] || [ "$1" = "-p" ]; then
                    if [ -z "$paper_host" ]; then
                        paper_host="$host_path"
                    fi
                fi
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
            --data)
                local host_path
                host_path=$(realpath "$2" 2>/dev/null || echo "$2")
                if [ ! -d "$host_path" ]; then
                    echo -e "${RED}--data must be a directory:${NC} $2" >&2
                    exit 1
                fi
                MOUNTS="$MOUNTS -v \"$host_path:/workspace/data:ro\""
                ARGS="$ARGS --data /workspace/data"
                shift 2
                ;;
            --claims)
                local host_path
                host_path=$(realpath "$2" 2>/dev/null || echo "$2")
                if [ ! -f "$host_path" ]; then
                    echo -e "${RED}--claims file not found:${NC} $2" >&2
                    exit 1
                fi
                local basename
                basename=$(basename "$host_path")
                counter=$((counter + 1))
                local container_path="/workspace/inputs/claims_${counter}_${basename}"
                MOUNTS="$MOUNTS -v \"$host_path:$container_path:ro\""
                ARGS="$ARGS --claims \"$container_path\""
                shift 2
                ;;
            --output|-o)
                saw_output=true
                local host_path
                host_path=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$2")
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

    # If the user didn't specify --output, pick a default on the host side
    # that lands on a writable mount. The --repo bind is read-only, so
    # letting the CLI compute /workspace/repo/replicate internally would
    # crash with "Read-only file system" when it tries to mkdir the subdir.
    #
    # Fallback chain: explicit --output (already handled above) > <repo>/replicate
    # > <paper-parent>/replicate. The Config layer requires at least one of
    # --paper or --repo, so one of these branches always fires.
    if [ "$saw_output" = false ]; then
        local default_output=""
        if [ -n "$repo_host" ]; then
            default_output="$repo_host/replicate"
        elif [ -n "$paper_host" ]; then
            default_output="$(dirname "$paper_host")/replicate"
        fi
        if [ -n "$default_output" ]; then
            mkdir -p "$default_output"
            chmod -R a+rwX "$default_output" 2>/dev/null || true
            MOUNTS="$MOUNTS -v \"$default_output:/workspace/output\""
            ARGS="$ARGS --output /workspace/output"
        fi
    fi
}

# -----------------------------------------------------------------------------
# Replicate (the primary subcommand)
# -----------------------------------------------------------------------------
cmd_replicate() {
    ensure_image
    warn_if_outdated

    # Fast-fail if the requested provider has no credentials on the host.
    # Beats waiting for an inside-container LLM call to hang or error out.
    local provider=$(extract_provider "$@")
    check_provider_credentials "$provider"

    # Surface a non-fatal hint if .env is missing — papers that need API keys
    # for their own LLM calls will fail without it.
    check_env_file_warn
    ensure_env_perms

    rewrite_paths "$@"

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local gpu_flags=$(get_gpu_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    # Surface actual GPU model/VRAM per device as a fact prompt_generator.py
    # can thread into codegen/plan/replicate prompts (issue #92: codegen
    # previously defaulted to CPU-only code blind to whether a GPU would even
    # be present at replicate time). Empty when get_gpu_flags found none.
    local gpu_info_flag=""
    local gpu_info
    gpu_info=$(get_gpu_info "$gpu_flags")
    if [ -n "$gpu_info" ]; then
        gpu_info_flag="-e VERITAS_GPU_INFO=\"$gpu_info\""
    fi

    # Replication API keys: pass .env into the container only on subcommands
    # that run paper code. The key-name list lets the Python layer scope
    # visibility to the replicate phase (see runner.py::_invoke_provider).
    local env_file_flag=""
    local env_keys_flag=""
    if [ -f "$PROJECT_ROOT/.env" ]; then
        env_file_flag="--env-file \"$PROJECT_ROOT/.env\""
        local keys
        keys=$(compute_env_file_keys)
        if [ -n "$keys" ]; then
            env_keys_flag="-e VERITAS_ENV_FILE_KEYS=$keys"
        fi
    fi

    # Forward an explicitly-set model override into the container so every
    # phase's provider CLI pins the same model. Passed as a direct -e (not via
    # .env / VERITAS_ENV_FILE_KEYS) so it is visible to all phases, not just
    # replicate. No-op unless the host exports ANTHROPIC_MODEL.
    local model_flag=""
    if [ -n "$ANTHROPIC_MODEL" ]; then
        model_flag="-e ANTHROPIC_MODEL=$ANTHROPIC_MODEL"
    fi

    # Polite-pool contact for the citation resolver's metadata requests when
    # --check-citations runs inline. No-op unless the host exports it.
    local contact_flag=""
    if [ -n "$VERITAS_CONTACT_EMAIL" ]; then
        contact_flag="-e VERITAS_CONTACT_EMAIL=$VERITAS_CONTACT_EMAIL"
    fi

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $gpu_flags \
        $gpu_info_flag \
        $credential_mounts \
        $env_file_flag \
        $env_keys_flag \
        $model_flag \
        $contact_flag \
        $MOUNTS \
        -w /workspace \
        \"$IMAGE_NAME\" \
        veritas replicate $ARGS"
}

# -----------------------------------------------------------------------------
# Regenerate report from existing replication output dir
# -----------------------------------------------------------------------------
cmd_report() {
    ensure_image

    local eval_dir="$1"; shift
    local host_eval_dir
    host_eval_dir=$(realpath "$eval_dir" 2>/dev/null || echo "$eval_dir")
    if [ ! -d "$host_eval_dir" ]; then
        echo -e "${RED}Replication output dir not found:${NC} $eval_dir" >&2
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
# Evaluate: run the evaluation manager + report on an existing replication dir
# (the product layer, decoupled from replicate). Needs provider credentials
# because the manager is an LLM pass; reads everything from the mounted output
# dir, so the original paper/repo need not be re-supplied.
# -----------------------------------------------------------------------------
cmd_evaluate() {
    ensure_image
    warn_if_outdated
    check_provider_credentials "$(extract_provider "$@")"

    local eval_dir="$1"; shift
    local host_eval_dir
    host_eval_dir=$(realpath "$eval_dir" 2>/dev/null || echo "$eval_dir")
    if [ ! -d "$host_eval_dir" ]; then
        echo -e "${RED}Replication output dir not found:${NC} $eval_dir" >&2
        exit 1
    fi

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    # Forward an explicitly-set model override so the evaluation manager's LLM
    # pass pins the same model as replication. Passed as a direct -e. No-op
    # unless the host exports ANTHROPIC_MODEL.
    local model_flag=""
    if [ -n "$ANTHROPIC_MODEL" ]; then
        model_flag="-e ANTHROPIC_MODEL=$ANTHROPIC_MODEL"
    fi

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $credential_mounts \
        $model_flag \
        -v \"$host_eval_dir:/workspace/eval\" \
        -w /workspace \
        \"$IMAGE_NAME\" \
        veritas evaluate /workspace/eval $@"
}

# -----------------------------------------------------------------------------
# Citation check: reference integrity + faithfulness on an existing replication
# dir (the post-hoc twin of `replicate --check-citations`). Advisory: never
# changes the Replication Score. Needs provider credentials because the
# extraction and faithfulness passes are LLM calls. A --paper given here is
# mounted read-only and its path rewritten; without it the CLI falls back to
# the paper path saved in the run's config, which for containerized runs is a
# container path that no longer resolves.
# -----------------------------------------------------------------------------
cmd_check_citations() {
    if [ $# -eq 0 ]; then
        echo -e "${RED}Usage:${NC} ./veritas check-citations <replicate-dir> [--paper <pdf>] [flags...]" >&2
        exit 1
    fi

    ensure_image
    warn_if_outdated
    check_provider_credentials "$(extract_provider "$@")"

    local eval_dir="$1"; shift
    local host_eval_dir
    host_eval_dir=$(realpath "$eval_dir" 2>/dev/null || echo "$eval_dir")
    if [ ! -d "$host_eval_dir" ]; then
        echo -e "${RED}Replication output dir not found:${NC} $eval_dir" >&2
        exit 1
    fi
    # The container (UID 1000) writes evaluation/ and report/ into this dir;
    # normalize permissions like rewrite_paths does for --output.
    chmod -R a+rwX "$host_eval_dir" 2>/dev/null || true

    # --paper is the subcommand's only path flag; mount it read-only and
    # rewrite the argument. Everything else passes through unchanged.
    local paper_mount=""
    local args=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --paper=*)
                # Normalize the equals form onto the space-form arm below.
                set -- --paper "${1#--paper=}" "${@:2}"
                continue
                ;;
            --paper)
                local host_paper
                host_paper=$(realpath "$2" 2>/dev/null || echo "$2")
                if [ ! -f "$host_paper" ]; then
                    echo -e "${RED}File not found:${NC} $2" >&2
                    exit 1
                fi
                local basename
                basename=$(basename "$host_paper")
                paper_mount="-v \"$host_paper:/workspace/inputs/$basename:ro\""
                args="$args --paper \"/workspace/inputs/$basename\""
                shift 2
                ;;
            *)
                args="$args \"$1\""
                shift
                ;;
        esac
    done

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    # Crossref/OpenAlex polite-pool contact for the resolver's requests.
    # No-op unless the host exports VERITAS_CONTACT_EMAIL.
    local contact_flag=""
    if [ -n "$VERITAS_CONTACT_EMAIL" ]; then
        contact_flag="-e VERITAS_CONTACT_EMAIL=$VERITAS_CONTACT_EMAIL"
    fi

    eval "docker run $tty_flag --rm \
        $platform_flag \
        $credential_mounts \
        $contact_flag \
        $paper_mount \
        -v \"$host_eval_dir:/workspace/eval\" \
        -w /workspace \
        \"$IMAGE_NAME\" \
        veritas check-citations /workspace/eval $args"
}

# -----------------------------------------------------------------------------
# Estimate compute/cost for a paper without running replication
# -----------------------------------------------------------------------------
cmd_estimate() {
    ensure_image
    warn_if_outdated
    check_provider_credentials "$(extract_provider "$@")"
    rewrite_paths "$@"

    local tty_flag=$(get_tty_flag)
    local platform_flag=$(get_platform_flags)
    local credential_mounts=$(get_cli_credential_mounts)
    ensure_credential_perms

    eval "docker run $tty_flag --rm \\
        $platform_flag \\
        $credential_mounts \\
        $MOUNTS \\
        -w /workspace \\
        \"$IMAGE_NAME\" \\
        veritas estimate $ARGS"
}

# -----------------------------------------------------------------------------
# Help
# -----------------------------------------------------------------------------
show_help() {
    show_banner
    echo -e "${BOLD}Usage:${NC} ./veritas <command> [args...]"
    echo ""
    echo -e "${BOLD}Commands:${NC}"
    echo "  setup         One-shot onboarding (prereqs, image, login, .env)"
    echo "  full          Full pipeline: replicate + evaluate + report (the default)"
    echo "                  e.g. ./veritas --paper p.pdf --repo ./myrepo"
    echo "  replicate     Replication only, through verify (for benchmarking)"
    echo "  estimate      Estimate compute/cost without running replication"
    echo "                  e.g. ./veritas replicate --paper p.pdf --repo ./myrepo"
    echo "                  add --data ./my-data to pre-position datasets (read-only)"
    echo "  evaluate      Run the evaluation manager + report on an existing"
    echo "                  replication dir (replicate once, evaluate later)"
    echo "                  e.g. ./veritas evaluate ./myrepo/replicate"
    echo "  report        Regenerate a report from an existing replication output dir"
    echo "  check-citations  Verify the paper's references on an existing replication"
    echo "                  dir (advisory; use --paper <pdf> to re-supply the paper)"
    echo "  shell         Interactive bash inside the container (cwd mounted as /workspace)"
    echo "  config        Edit replication API keys (.env) interactively"
    echo "  login         Log in to an AI provider (claude|codex|gemini)"
    echo "  build         Build the image locally"
    echo "  update        Pull the latest image from GHCR"
    echo "  status        Show status dashboard (Docker, image, GPU, credentials, .env)"
    echo "  help          Show this help"
    echo ""
    echo -e "${BOLD}First-time setup:${NC}"
    echo "  1. ./veritas setup"
    echo "  2. ./veritas replicate --paper <your-paper.pdf> --repo <your-repo>"
    echo ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
main() {
    local cmd="${1:-help}"

    # Bare invocation that starts with an option (e.g. `./veritas --paper p.pdf
    # --repo ./r`) means the full pipeline: replicate + evaluate + report.
    if [[ "$cmd" == -* ]]; then
        cmd_replicate --evaluate "$@"
        return
    fi
    shift || true

    case "$cmd" in
        full)          cmd_replicate --evaluate "$@" ;;
        replicate)     cmd_replicate "$@" ;;
        estimate)      cmd_estimate "$@" ;;
        evaluate)      cmd_evaluate "$@" ;;
        report)        cmd_report "$@" ;;
        check-citations) cmd_check_citations "$@" ;;
        shell)         cmd_shell "$@" ;;
        setup)         cmd_setup "$@" ;;
        config)        cmd_config "$@" ;;
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
