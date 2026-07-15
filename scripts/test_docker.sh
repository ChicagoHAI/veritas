#!/usr/bin/env bash
# =============================================================================
# Veritas Docker image smoke test
# Asserts that the built image has the expected tools and veritas package.
# Run locally:  ./scripts/test_docker.sh [image_tag]
# Run in CI:    called by .github/workflows/docker-publish.yml
# =============================================================================

set -e

IMAGE="${1:-veritas:dev}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}[OK]${NC} $1"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; exit 1; }

assert_run() {
    local label="$1"; shift
    if docker run --rm --entrypoint "$1" "$IMAGE" "${@:2}" > /dev/null 2>&1; then
        pass "$label"
    else
        fail "$label"
    fi
}

echo -e "${BLUE}Smoke test: ${IMAGE}${NC}"

# Provider CLIs
assert_run "claude CLI responds"  claude  --version
assert_run "codex CLI responds"   codex   --version
assert_run "gemini CLI responds"  gemini  --version
assert_run "opencode CLI responds" opencode --version

# Report toolchain
assert_run "pandoc responds"      pandoc  --version
assert_run "pdflatex responds"    pdflatex --version

# MATLAB-code execution (GNU Octave)
assert_run "octave-cli responds"  octave-cli --no-gui --eval "disp(2+2)"

# Python + veritas
assert_run "python --version"     python  --version
assert_run "veritas --help"       veritas --help
assert_run "import veritas works" python  -c "import veritas; print(veritas.__name__)"

# Entrypoint banner should print when invoking the default CMD
if docker run --rm "$IMAGE" true 2>&1 | grep -q "Veritas Container Starting"; then
    pass "entrypoint banner prints"
else
    fail "entrypoint banner missing"
fi

echo -e "${GREEN}All smoke checks passed.${NC}"
