#!/bin/bash
# Verlihub Test Runner Script
#
# This script provides convenient shortcuts for running different test configurations.
# Can run tests either locally (if venv/dependencies exist) or via Docker.
#
# Usage:
#   ./run_tests.sh [command] [options]
#
# Commands:
#   unit          Run unit tests (SQLite, fast) - local or Docker
#   integration   Run integration tests (requires built plugins) - local
#   mysql         Run tests against MySQL (Docker)
#   postgres      Run tests against PostgreSQL (Docker)
#   all-db        Run tests against all database backends (Docker)
#   dual          Run dual-build tests (original + verlihub-py) (Docker)
#   full          Run full integration tests (Docker, requires running hubs)
#   sql-semantics Compare SQL semantics across databases (Docker)
#   llm           Run LLM integration tests (Ollama + qwen2.5:0.5b) (Docker)
#   docker        Run all tests via Docker (no local dependencies needed)
#   help          Show this help message
#
# Note: Commands marked (Docker) launch containers automatically.
#       Commands marked (local) require built verlihub and Python dependencies.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

show_help() {
    echo -e "${BLUE}Verlihub Test Runner${NC}"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo "Commands:"
    echo "  unit          Run unit tests (SQLite, fast)"
    echo "  integration   Run integration tests (requires built plugins)"
    echo "  mysql         Run tests against MySQL (Docker)"
    echo "  postgres      Run tests against PostgreSQL (Docker)"
    echo "  all-db        Run tests against all database backends (Docker)"
    echo "  dual          Run dual-build tests (original + verlihub-py)"
    echo "  full          Run full integration tests (Docker, requires running hubs)"
    echo "  sql-semantics Compare SQL semantics across databases (Docker)"
    echo "  llm           Run LLM integration tests (Ollama + qwen2.5:0.5b) (Docker)"
    echo "  bot-chat      Run NMDC bot chat LLM tests (PM + main chat via NMDC) (Docker)"
    echo "  playwright    Run Playwright E2E tests for dashboard (Docker)"
    echo "  docker        Run all tests via Docker (no local deps needed)"
    echo "  help          Show this help message"
    echo ""
    echo "Options:"
    echo "  -v, --verbose   Verbose output"
    echo "  -k PATTERN      Only run tests matching PATTERN"
    echo "  --docker        Force Docker mode for unit/integration tests"
    echo "  --headed        Run Playwright tests in headed mode (visible browser)"
    echo "  --base-url URL  Base URL for Playwright tests (default: http://localhost:30000)"
    echo ""
    echo "Examples:"
    echo "  $0 unit                    # Run fast unit tests"
    echo "  $0 mysql -v                # Run MySQL tests with verbose output"
    echo "  $0 unit -k 'test_user'     # Run tests matching 'test_user'"
    echo "  $0 dual                    # Build & test both editions"
    echo "  $0 playwright              # Run dashboard E2E tests"
    echo "  $0 playwright --headed     # Run E2E tests with visible browser"
    echo "  $0 docker                  # Run everything via Docker"
}

check_local_deps() {
    # Check if we can run tests locally
    if [ -f "$PROJECT_DIR/build/python/verlihub/_verlihub_core.so" ] || \
       [ -d "$PROJECT_DIR/.venv" ] || \
       command -v pytest &> /dev/null; then
        return 0
    fi
    return 1
}

setup_local_env() {
    # Set up Python path for local testing
    if [ -d "$PROJECT_DIR/build/python" ]; then
        export PYTHONPATH="$PROJECT_DIR/build/python:$PYTHONPATH"
    fi
    
    # Activate venv if it exists
    if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
        source "$PROJECT_DIR/.venv/bin/activate"
    fi
}

run_unit_tests() {
    echo -e "${YELLOW}Running unit tests (SQLite)...${NC}"
    cd "$PROJECT_DIR"
    
    local pytest_args="-v --tb=short"
    [ -n "$VERBOSE" ] && pytest_args="-vvs --tb=long"
    [ -n "$PATTERN" ] && pytest_args="$pytest_args -k '$PATTERN'"
    
    if [ "$FORCE_DOCKER" = "1" ] || ! check_local_deps; then
        echo -e "${BLUE}Using Docker for tests...${NC}"
        docker compose -f docker/docker-compose.test.yml run --rm \
            -e VH_DB_BACKEND=sqlite \
            mysql-tests pytest python/tests/ $pytest_args \
                --ignore=python/tests/test_nmdc_stress.py \
                --ignore=python/tests/test_benchmarks.py
    else
        setup_local_env
        PYTHONPATH=build/python pytest python/tests/ $pytest_args \
            --ignore=python/tests/test_nmdc_stress.py \
            --ignore=python/tests/test_benchmarks.py
    fi
}

run_integration_tests() {
    echo -e "${YELLOW}Running integration tests...${NC}"
    cd "$PROJECT_DIR"
    
    local pytest_args="-v --tb=short"
    [ -n "$VERBOSE" ] && pytest_args="-vvs --tb=long"
    [ -n "$PATTERN" ] && pytest_args="$pytest_args -k '$PATTERN'"
    
    if [ "$FORCE_DOCKER" = "1" ] || ! check_local_deps; then
        echo -e "${BLUE}Using Docker for tests...${NC}"
        docker compose -f docker/docker-compose.test.yml run --rm \
            -e VH_INTEGRATION_TESTS=1 \
            -e VH_DB_BACKEND=sqlite \
            mysql-tests pytest python/tests/ $pytest_args \
                --ignore=python/tests/test_nmdc_stress.py \
                --ignore=python/tests/test_benchmarks.py
    else
        setup_local_env
        VH_INTEGRATION_TESTS=1 PYTHONPATH=build/python \
            pytest python/tests/ $pytest_args \
            --ignore=python/tests/test_nmdc_stress.py \
            --ignore=python/tests/test_benchmarks.py
    fi
}

run_mysql_tests() {
    echo -e "${YELLOW}Running MySQL tests (Docker)...${NC}"
    cd "$PROJECT_DIR"
    
    local pytest_args=""
    [ -n "$VERBOSE" ] && pytest_args="-v"
    [ -n "$PATTERN" ] && pytest_args="$pytest_args -k '$PATTERN'"
    
    PYTEST_ARGS="$pytest_args" \
        docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit mysql-tests
}

run_postgres_tests() {
    echo -e "${YELLOW}Running PostgreSQL tests (Docker)...${NC}"
    cd "$PROJECT_DIR"
    
    local pytest_args=""
    [ -n "$VERBOSE" ] && pytest_args="-v"
    [ -n "$PATTERN" ] && pytest_args="$pytest_args -k '$PATTERN'"
    
    PYTEST_ARGS="$pytest_args" \
        docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit postgres-tests
}

run_all_db_tests() {
    echo -e "${YELLOW}Running all database tests (Docker)...${NC}"
    cd "$PROJECT_DIR"
    
    docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit all-db-tests
}

run_dual_tests() {
    echo -e "${YELLOW}Running dual-build tests (Docker)...${NC}"
    cd "$PROJECT_DIR"
    
    docker compose -f docker/docker-compose.dual-test.yml up --build
}

run_full_integration() {
    echo -e "${YELLOW}Running full integration tests (Docker)...${NC}"
    cd "$PROJECT_DIR"
    
    docker compose -f docker/docker-compose.dual-test.yml --profile full-integration up --build
}

run_sql_semantics() {
    echo -e "${YELLOW}Running SQL semantics comparison (Docker)...${NC}"
    cd "$PROJECT_DIR"
    
    docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit sql-semantics-tests
}

run_llm_tests() {
    echo -e "${YELLOW}Running LLM integration tests (Ollama + qwen2.5:0.5b)...${NC}"
    cd "$PROJECT_DIR"

    docker compose -f docker/docker-compose.llm-test.yml up \
        --build --abort-on-container-exit llm-tests
    local exit_code=$?
    docker compose -f docker/docker-compose.llm-test.yml down --remove-orphans 2>/dev/null || true
    return $exit_code
}

run_bot_chat_tests() {
    echo -e "${YELLOW}Running NMDC bot chat LLM integration tests...${NC}"
    cd "$PROJECT_DIR"

    docker compose -f docker/docker-compose.bot-chat-test.yml up \
        --build --abort-on-container-exit bot-tests
    local exit_code=$?
    docker compose -f docker/docker-compose.bot-chat-test.yml down --remove-orphans 2>/dev/null || true
    return $exit_code
}

run_playwright_tests() {
    echo -e "${YELLOW}Running Playwright E2E tests for dashboard...${NC}"
    cd "$PROJECT_DIR"
    
    local pytest_args="-v --tb=short"
    [ -n "$VERBOSE" ] && pytest_args="-vvs --tb=long"
    [ -n "$PATTERN" ] && pytest_args="$pytest_args -k '$PATTERN'"
    [ -n "$HEADED" ] && pytest_args="$pytest_args --headed"
    [ -n "$BASE_URL" ] && pytest_args="$pytest_args --base-url $BASE_URL"
    
    if [ "$FORCE_DOCKER" = "1" ]; then
        echo -e "${BLUE}Using Docker for Playwright tests...${NC}"
        # Run playwright tests in Docker container with browser
        docker compose -f docker/docker-compose.test.yml run --rm \
            -e DASHBOARD_URL="${BASE_URL:-http://host.docker.internal:30000}" \
            mysql-tests sh -c "pip install pytest-playwright && playwright install chromium && pytest docker/tests/test_dashboard_playwright.py $pytest_args -p docker/tests/conftest_playwright"
    else
        # Check if playwright is installed locally
        if ! python -c "import playwright" 2>/dev/null; then
            echo -e "${BLUE}Installing playwright dependencies...${NC}"
            pip install pytest-playwright
            playwright install chromium
        fi
        
        setup_local_env
        PYTHONPATH=build/python pytest docker/tests/test_dashboard_playwright.py $pytest_args \
            -p docker/tests/conftest_playwright \
            --base-url "${BASE_URL:-http://localhost:30000}"
    fi
}

# Parse arguments
COMMAND="${1:-help}"
shift || true

VERBOSE=""
PATTERN=""
run_docker_tests() {
    echo -e "${YELLOW}Running all tests via Docker...${NC}"
    cd "$PROJECT_DIR"
    
    # Run unit tests with SQLite
    echo -e "${BLUE}Step 1/3: Unit tests (SQLite)${NC}"
    docker compose -f docker/docker-compose.test.yml run --rm \
        -e VH_DB_BACKEND=sqlite \
        mysql-tests pytest python/tests/ -v --tb=short \
            --ignore=python/tests/test_nmdc_stress.py \
            --ignore=python/tests/test_benchmarks.py || true
    
    # Run MySQL tests
    echo -e "${BLUE}Step 2/3: MySQL tests${NC}"
    docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit mysql-tests || true
    
    # Run PostgreSQL tests
    echo -e "${BLUE}Step 3/3: PostgreSQL tests${NC}"
    docker compose -f docker/docker-compose.test.yml up --build --abort-on-container-exit postgres-tests || true
    
    # Cleanup
    docker compose -f docker/docker-compose.test.yml down
}

# Parse arguments
COMMAND="${1:-help}"
shift || true

VERBOSE=""
PATTERN=""
FORCE_DOCKER=""
HEADED=""
BASE_URL=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE=1
            shift
            ;;
        -k)
            PATTERN="$2"
            shift 2
            ;;
        --docker)
            FORCE_DOCKER=1
            shift
            ;;
        --headed)
            HEADED=1
            shift
            ;;
        --base-url)
            BASE_URL="$2"
            shift 2
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            show_help
            exit 1
            ;;
    esac
done

# Execute command
case $COMMAND in
    unit)
        run_unit_tests
        ;;
    integration)
        run_integration_tests
        ;;
    mysql)
        run_mysql_tests
        ;;
    postgres)
        run_postgres_tests
        ;;
    all-db)
        run_all_db_tests
        ;;
    dual)
        run_dual_tests
        ;;
    full)
        run_full_integration
        ;;
    sql-semantics)
        run_sql_semantics
        ;;
    llm)
        run_llm_tests
        ;;
    bot-chat)
        run_bot_chat_tests
        ;;
    playwright)
        run_playwright_tests
        ;;
    docker)
        run_docker_tests
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "${RED}Unknown command: $COMMAND${NC}"
        show_help
        exit 1
        ;;
esac

echo -e "\n${GREEN}Done!${NC}"
