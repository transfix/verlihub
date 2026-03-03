#!/bin/bash
# Verlihub Integration Test Runner
# Run with: sg docker -c "./run_integration_tests.sh [options]"
#
# Examples:
#   ./run_integration_tests.sh              # Run all tests
#   ./run_integration_tests.sh --single     # Single interpreter tests only
#   ./run_integration_tests.sh --multi      # Multi interpreter tests only
#   ./run_integration_tests.sh --dispatcher # Dispatcher unit tests only
#   ./run_integration_tests.sh --unit       # C++ unit tests only
#   ./run_integration_tests.sh --no-build   # Skip rebuild

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default options
RUN_SINGLE=false
RUN_MULTI=false
RUN_DISPATCHER=false
RUN_UNIT=false
RUN_ALL=true
NO_BUILD=false
CLEANUP=true

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --single)
            RUN_SINGLE=true
            RUN_ALL=false
            shift
            ;;
        --multi)
            RUN_MULTI=true
            RUN_ALL=false
            shift
            ;;
        --dispatcher)
            RUN_DISPATCHER=true
            RUN_ALL=false
            shift
            ;;
        --unit)
            RUN_UNIT=true
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
        -h|--help)
            echo "Verlihub Integration Test Runner"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --single       Run single interpreter integration tests"
            echo "  --multi        Run multi interpreter integration tests"
            echo "  --dispatcher   Run dispatcher unit tests (no hub required)"
            echo "  --unit         Run C++ unit tests"
            echo "  --no-build     Skip Docker image rebuild"
            echo "  --no-cleanup   Don't stop containers after tests"
            echo "  -h, --help     Show this help message"
            echo ""
            echo "If no test type is specified, all tests will be run."
            echo ""
            echo "Examples:"
            echo "  sg docker -c \"$0\"              # Run all tests"
            echo "  sg docker -c \"$0 --single\"     # Single interpreter only"
            echo "  sg docker -c \"$0 --dispatcher\" # Quick dispatcher tests"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# If running all, enable all test types
if $RUN_ALL; then
    RUN_SINGLE=true
    RUN_MULTI=true
    RUN_DISPATCHER=true
fi

# Track test results
declare -A RESULTS
OVERALL_EXIT=0

log_header() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_failure() {
    echo -e "${RED}✗ $1${NC}"
}

log_info() {
    echo -e "${YELLOW}→ $1${NC}"
}

# Check if Docker daemon is reachable
docker_available() {
    docker info >/dev/null 2>&1
}

cleanup() {
    if $CLEANUP; then
        log_info "Cleaning up containers..."
        if docker_available; then
            docker compose down --remove-orphans 2>/dev/null || true
        fi
    fi
}

# Run dispatcher tests (no hub required)
# Falls back to running directly with Python if Docker is unavailable,
# since these are pure unit tests with no service dependencies.
run_dispatcher_tests() {
    log_header "Running Dispatcher Unit Tests"

    if docker_available; then
        if ! $NO_BUILD; then
            log_info "Building dispatcher test container..."
            docker compose build dispatcher-tests 2>&1 | tail -5
        fi

        log_info "Running tests (Docker)..."
        if docker compose --profile dispatcher-test run --rm dispatcher-tests; then
            RESULTS["dispatcher"]="PASSED"
            log_success "Dispatcher tests passed"
            return 0
        else
            RESULTS["dispatcher"]="FAILED"
            log_failure "Dispatcher tests failed"
            return 1
        fi
    else
        log_info "Docker not available — running dispatcher tests directly..."
        local test_script="${SCRIPT_DIR}/docker/tests/test_dispatcher.py"
        if [[ ! -f "$test_script" ]]; then
            RESULTS["dispatcher"]="FAILED"
            log_failure "Dispatcher test script not found: $test_script"
            return 1
        fi
        export SCRIPTS_PATH="${SCRIPT_DIR}/plugins/python/scripts"
        if python3 "$test_script"; then
            RESULTS["dispatcher"]="PASSED"
            log_success "Dispatcher tests passed (direct)"
            return 0
        else
            RESULTS["dispatcher"]="FAILED"
            log_failure "Dispatcher tests failed"
            return 1
        fi
    fi
}

# Run single interpreter integration tests
run_single_tests() {
    log_header "Running Single Interpreter Integration Tests"

    if ! docker_available; then
        RESULTS["single"]="FAILED"
        log_failure "Docker is not available — single interpreter tests require Docker (MySQL + hub containers)"
        return 1
    fi

    if ! $NO_BUILD; then
        log_info "Building single interpreter containers..."
        docker compose build verlihub 2>&1 | tail -5
    fi
    
    # Stop any existing containers first
    docker compose down --remove-orphans 2>/dev/null || true
    
    log_info "Starting MySQL and Verlihub (single interpreter mode)..."
    docker compose up -d mysql verlihub
    
    log_info "Waiting for hub to start (30 seconds)..."
    sleep 30
    
    log_info "Running integration tests..."
    if docker compose --profile integration run --rm integration-tests; then
        RESULTS["single"]="PASSED"
        log_success "Single interpreter tests passed"
        docker compose down --remove-orphans 2>/dev/null || true
        return 0
    else
        RESULTS["single"]="FAILED"
        log_failure "Single interpreter tests failed"
        # Show hub logs on failure
        echo ""
        log_info "Hub logs:"
        docker logs verlihub-server --tail=50 2>&1 || true
        docker compose down --remove-orphans 2>/dev/null || true
        return 1
    fi
}

# Run multi interpreter integration tests
run_multi_tests() {
    log_header "Running Multi Interpreter Integration Tests"

    if ! docker_available; then
        RESULTS["multi"]="FAILED"
        log_failure "Docker is not available — multi interpreter tests require Docker (MySQL + hub containers)"
        return 1
    fi

    if ! $NO_BUILD; then
        log_info "Building multi interpreter containers..."
        docker compose --profile multi build verlihub-multi 2>&1 | tail -5
    fi
    
    # Stop any existing containers first
    docker compose down --remove-orphans 2>/dev/null || true
    
    log_info "Starting MySQL and Verlihub (multi interpreter mode)..."
    docker compose --profile multi up -d mysql verlihub-multi
    
    log_info "Waiting for hub to start (45 seconds)..."
    sleep 45
    
    log_info "Running integration tests..."
    if docker compose --profile multi --profile integration-multi run --rm integration-tests-multi; then
        RESULTS["multi"]="PASSED"
        log_success "Multi interpreter tests passed"
        docker compose --profile multi down --remove-orphans 2>/dev/null || true
        return 0
    else
        RESULTS["multi"]="FAILED"
        log_failure "Multi interpreter tests failed"
        # Show hub logs on failure
        echo ""
        log_info "Hub logs:"
        docker logs verlihub-server-multi --tail=50 2>&1 || true
        docker compose --profile multi down --remove-orphans 2>/dev/null || true
        return 1
    fi
}

# Run C++ unit tests
run_unit_tests() {
    log_header "Running C++ Unit Tests"

    if ! docker_available; then
        RESULTS["unit"]="FAILED"
        log_failure "Docker is not available — C++ unit tests require Docker (MySQL + test-builder container)"
        return 1
    fi

    if ! $NO_BUILD; then
        log_info "Building test runner container..."
        docker compose --profile test build test-runner 2>&1 | tail -5
    fi
    
    # Stop any existing containers and start fresh mysql
    docker compose down --remove-orphans 2>/dev/null || true
    docker compose up -d mysql
    
    log_info "Waiting for MySQL..."
    sleep 10
    
    log_info "Running unit tests..."
    if docker compose --profile test run --rm test-runner; then
        RESULTS["unit"]="PASSED"
        log_success "C++ unit tests passed"
        return 0
    else
        RESULTS["unit"]="FAILED"
        log_failure "C++ unit tests failed"
        return 1
    fi
}

# Print summary
print_summary() {
    log_header "Test Summary"
    
    for test_name in "${!RESULTS[@]}"; do
        result="${RESULTS[$test_name]}"
        if [ "$result" == "PASSED" ]; then
            log_success "$test_name: $result"
        else
            log_failure "$test_name: $result"
            OVERALL_EXIT=1
        fi
    done
    
    echo ""
    if [ $OVERALL_EXIT -eq 0 ]; then
        echo -e "${GREEN}All tests passed!${NC}"
    else
        echo -e "${RED}Some tests failed!${NC}"
    fi
}

# Main execution
main() {
    log_header "Verlihub Integration Test Runner"
    
    # Set up cleanup trap
    trap cleanup EXIT
    
    # Run requested tests
    if $RUN_DISPATCHER; then
        run_dispatcher_tests || true
    fi
    
    if $RUN_SINGLE; then
        run_single_tests || true
    fi
    
    if $RUN_MULTI; then
        run_multi_tests || true
    fi
    
    if $RUN_UNIT; then
        run_unit_tests || true
    fi
    
    # Print summary
    print_summary
    
    exit $OVERALL_EXIT
}

main
