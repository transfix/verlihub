#!/bin/bash
# Ledokol Integration Test Runner
#
# Exercises RoLex's ledokol Lua plugin through both legacy C++ Verlihub
# and the new verlihub-py Python hub.
#
# Usage:
#   ./run_ledokol_tests.sh              # Run both legacy and py tests
#   ./run_ledokol_tests.sh --legacy     # Legacy C++ hub + ledokol.lua only
#   ./run_ledokol_tests.sh --py         # Verlihub-py hub only
#   ./run_ledokol_tests.sh --no-build   # Skip Docker image rebuild
#   ./run_ledokol_tests.sh --no-cleanup # Keep containers running after tests
#
# Ledokol: https://github.com/Verlihub/ledokol

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker/docker-compose.ledokol-test.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Options
RUN_LEGACY=false
RUN_PY=false
RUN_ALL=true
NO_BUILD=false
CLEANUP=true
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --legacy)
            RUN_LEGACY=true
            RUN_ALL=false
            shift
            ;;
        --py)
            RUN_PY=true
            RUN_ALL=false
            shift
            ;;
        --no-build)
            NO_BUILD=true
            shift
            ;;
        --no-cleanup)
            CLEANUP=false
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            cat << 'EOF'
Ledokol Integration Test Runner

Exercises RoLex's ledokol Lua plugin (https://github.com/Verlihub/ledokol)
through NMDC protocol commands on both hub implementations.

Usage: ./run_ledokol_tests.sh [options]

Options:
  --legacy       Run legacy tests only (C++ hub + ledokol.lua)
  --py           Run verlihub-py tests only (Python hub)
  --no-build     Skip Docker image rebuild
  --no-cleanup   Keep containers running after tests
  --verbose, -v  Show full Docker output
  -h, --help     Show this help

Test suites:
  Legacy (50+ tests):
    - Loads real ledokol.lua into C++ Verlihub via !luaload
    - Sends admin/user commands (!say, !newsadd, +calculate, ...)
    - Validates responses via NMDC protocol

  Verlihub-py (50+ tests):
    - Tests ledokol-equivalent commands on Python hub
    - Includes multi-client interaction tests (PM, say)
    - Optional REST API validation
    - Graceful skip for unimplemented features

Commands tested include:
  Script:   !ledohelp, !ledostats, !ledoconf, !ledoset
  Chat:     !say, !clear, +calculate, +history, +showtopic
  News:     !newsadd, +hubnews, !newsdel
  Content:  !repladd, !respadd, !trigadd, !remadd (+ list/del)
  Security: !antiadd, !sefiadd, !protadd (+ list/del)
  Users:    !gagipadd, !userinfo, +nick, !reglist, !regfind
  Releases: !reladd, +rellist, !reldel
  Hubs:     !hubadd, +showhubs, !hubdel
  Ranks:    chat, share, op, search ranks
EOF
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if $RUN_ALL; then
    RUN_LEGACY=true
    RUN_PY=true
fi

# Track results
declare -A RESULTS
OVERALL_EXIT=0

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

log_banner() {
    echo ""
    echo -e "${CYAN}${BOLD}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║  $1${NC}"
    echo -e "${CYAN}${BOLD}╚════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

log_header() {
    echo ""
    echo -e "${BLUE}══════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}══════════════════════════════════════════${NC}"
}

log_ok() {
    echo -e "${GREEN}  ✓ $1${NC}"
}

log_fail() {
    echo -e "${RED}  ✗ $1${NC}"
}

log_info() {
    echo -e "${YELLOW}  → $1${NC}"
}

log_step() {
    echo -e "${BOLD}  ▸ $1${NC}"
}

cleanup_containers() {
    if $CLEANUP; then
        log_info "Cleaning up containers..."
        docker compose -f "$COMPOSE_FILE" \
            --profile ledokol-legacy --profile ledokol-py \
            down --remove-orphans --volumes 2>/dev/null || true
    fi
}

# -----------------------------------------------------------------------
# Legacy Tests — C++ Verlihub + ledokol.lua
# -----------------------------------------------------------------------

run_legacy_tests() {
    log_header "Ledokol Legacy Tests (C++ Hub + Lua)"

    # Build
    if ! $NO_BUILD; then
        log_step "Building legacy hub image..."
        if $VERBOSE; then
            docker compose -f "$COMPOSE_FILE" --profile ledokol-legacy build
        else
            docker compose -f "$COMPOSE_FILE" --profile ledokol-legacy build 2>&1 | tail -3
        fi
    fi

    # Clean previous run
    docker compose -f "$COMPOSE_FILE" --profile ledokol-legacy \
        down --remove-orphans --volumes 2>/dev/null || true

    # Start MySQL + legacy hub
    log_step "Starting MySQL + legacy Verlihub..."
    docker compose -f "$COMPOSE_FILE" --profile ledokol-legacy up -d mysql verlihub-legacy

    # Wait for hub
    log_step "Waiting for hub to initialize (up to 90s)..."
    local ready=false
    for i in $(seq 1 90); do
        if docker compose -f "$COMPOSE_FILE" exec -T verlihub-legacy \
            bash -c 'nc -z 127.0.0.1 4111 2>/dev/null' 2>/dev/null; then
            log_ok "Hub listening on port 4111 after ${i}s"
            ready=true
            break
        fi
        sleep 1
    done

    if ! $ready; then
        log_fail "Hub did not start within 90s"
        log_info "Hub logs:"
        docker logs ledokol-legacy-hub --tail=30 2>&1 || true
        RESULTS["legacy"]="FAILED (hub timeout)"
        return 1
    fi

    # Run test container
    log_step "Running ledokol legacy test suite..."
    echo ""

    if docker compose -f "$COMPOSE_FILE" --profile ledokol-legacy \
        run --rm ledokol-test-legacy; then
        RESULTS["legacy"]="PASSED"
        log_ok "Legacy ledokol tests passed"
    else
        RESULTS["legacy"]="FAILED"
        log_fail "Legacy ledokol tests failed"
        echo ""
        log_info "Hub logs (last 40 lines):"
        docker logs ledokol-legacy-hub --tail=40 2>&1 || true
    fi

    # Stop legacy services
    docker compose -f "$COMPOSE_FILE" --profile ledokol-legacy \
        down --remove-orphans --volumes 2>/dev/null || true
}

# -----------------------------------------------------------------------
# Verlihub-py Tests — Python Hub
# -----------------------------------------------------------------------

run_py_tests() {
    log_header "Ledokol Verlihub-py Tests (Python Hub)"

    # Build
    if ! $NO_BUILD; then
        log_step "Building verlihub-py hub image..."
        if $VERBOSE; then
            docker compose -f "$COMPOSE_FILE" --profile ledokol-py build
        else
            docker compose -f "$COMPOSE_FILE" --profile ledokol-py build 2>&1 | tail -3
        fi
    fi

    # Clean previous run
    docker compose -f "$COMPOSE_FILE" --profile ledokol-py \
        down --remove-orphans --volumes 2>/dev/null || true

    # Start MySQL + py hub
    log_step "Starting MySQL + verlihub-py..."
    docker compose -f "$COMPOSE_FILE" --profile ledokol-py up -d mysql verlihub-py

    # Wait for hub
    log_step "Waiting for hub to initialize (up to 90s)..."
    local ready=false
    for i in $(seq 1 90); do
        if docker compose -f "$COMPOSE_FILE" exec -T verlihub-py \
            bash -c 'nc -z 127.0.0.1 4111 2>/dev/null' 2>/dev/null; then
            log_ok "Hub listening on port 4111 after ${i}s"
            ready=true
            break
        fi
        sleep 1
    done

    if ! $ready; then
        log_fail "Hub did not start within 90s"
        log_info "Hub logs:"
        docker logs ledokol-py-hub --tail=30 2>&1 || true
        RESULTS["py"]="FAILED (hub timeout)"
        return 1
    fi

    # Run test container
    log_step "Running ledokol py test suite..."
    echo ""

    if docker compose -f "$COMPOSE_FILE" --profile ledokol-py \
        run --rm ledokol-test-py; then
        RESULTS["py"]="PASSED"
        log_ok "Verlihub-py ledokol tests passed"
    else
        RESULTS["py"]="FAILED"
        log_fail "Verlihub-py ledokol tests failed"
        echo ""
        log_info "Hub logs (last 40 lines):"
        docker logs ledokol-py-hub --tail=40 2>&1 || true
    fi

    # Stop py services
    docker compose -f "$COMPOSE_FILE" --profile ledokol-py \
        down --remove-orphans --volumes 2>/dev/null || true
}

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------

print_summary() {
    log_header "Ledokol Test Summary"

    for name in "${!RESULTS[@]}"; do
        result="${RESULTS[$name]}"
        if [[ "$result" == "PASSED" ]]; then
            log_ok "$name: $result"
        else
            log_fail "$name: $result"
            OVERALL_EXIT=1
        fi
    done

    echo ""
    if [ $OVERALL_EXIT -eq 0 ]; then
        echo -e "${GREEN}${BOLD}  All ledokol tests passed!${NC}"
    else
        echo -e "${RED}${BOLD}  Some ledokol tests failed.${NC}"
    fi
    echo ""
}

# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

main() {
    log_banner "Ledokol Integration Test Runner"

    echo -e "  Ledokol:  ${CYAN}https://github.com/Verlihub/ledokol${NC}"
    echo -e "  Author:   ${CYAN}RoLex${NC}"
    echo -e "  Protocol: ${CYAN}NMDC${NC}"
    echo ""

    trap cleanup_containers EXIT

    if $RUN_LEGACY; then
        run_legacy_tests || true
    fi

    if $RUN_PY; then
        run_py_tests || true
    fi

    print_summary
    exit $OVERALL_EXIT
}

main
