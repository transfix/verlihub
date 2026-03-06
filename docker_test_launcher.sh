#!/bin/bash
# Verlihub Docker Test Launcher
#
# Comprehensive test runner that launches all necessary Docker containers
# and runs different test configurations.
#
# Usage:
#   ./docker_test_launcher.sh [command] [options]
#
# Commands:
#   all           Run all test suites
#   py-unit       Run verlihub-py unit tests (SQLite)
#   py-mysql      Run verlihub-py tests against MySQL
#   py-postgres   Run verlihub-py tests against PostgreSQL
#   py-all-db     Run verlihub-py tests against all databases
#   llm           Run LLM integration tests (Ollama + qwen2.5:1.5b)
#   original      Run original verlihub tests (MySQL only)
#   dual          Run both original and verlihub-py tests
#   sql-semantics Compare SQL semantics across databases
#   cleanup       Stop and remove all test containers
#   help          Show this help message
#
# Note: Original Verlihub only supports MySQL.
#       Verlihub-py supports SQLite (default), MySQL, and PostgreSQL.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Docker compose files
COMPOSE_TEST="docker/docker-compose.test.yml"
COMPOSE_DUAL="docker/docker-compose.dual-test.yml"
COMPOSE_LLM="docker/docker-compose.llm-test.yml"

show_help() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}Verlihub Docker Test Launcher${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""
    echo "Usage: $0 [command] [options]"
    echo ""
    echo -e "${YELLOW}Commands:${NC}"
    echo "  all           Run all test suites"
    echo "  py-unit       Run verlihub-py unit tests (SQLite, no Docker DB needed)"
    echo "  py-mysql      Run verlihub-py tests against MySQL"
    echo "  py-postgres   Run verlihub-py tests against PostgreSQL"
    echo "  py-all-db     Run verlihub-py tests against all databases"
    echo "  llm           Run LLM integration tests (Ollama + qwen2.5:1.5b, CPU)"
    echo "  original      Run original verlihub tests (MySQL only)"
    echo "  dual          Run both original and verlihub-py tests"
    echo "  sql-semantics Compare SQL semantics across databases"
    echo "  cleanup       Stop and remove all test containers"
    echo "  help          Show this help message"
    echo ""
    echo -e "${YELLOW}Options:${NC}"
    echo "  -v, --verbose   Verbose output"
    echo "  -k PATTERN      Only run tests matching PATTERN"
    echo "  --no-build      Skip building Docker images"
    echo "  --keep          Keep containers running after tests"
    echo "  --coverage      Collect pytest-cov coverage reports (HTML + XML)"
    echo ""
    echo -e "${YELLOW}Database Support:${NC}"
    echo "  Original Verlihub: MySQL only"
    echo "  Verlihub-py:       SQLite (default), MySQL, PostgreSQL"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 py-unit                  # Fast unit tests with SQLite"
    echo "  $0 py-mysql -v              # MySQL tests with verbose output"
    echo "  $0 py-all-db                # Test all database backends"
    echo "  $0 dual                     # Compare original vs verlihub-py"
    echo "  $0 cleanup                  # Clean up containers"
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running or you don't have permission"
        log_info "Try: sudo usermod -aG docker \$USER && newgrp docker"
        exit 1
    fi
    
    if ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not available"
        exit 1
    fi
}

wait_for_service() {
    local service=$1
    local max_wait=${2:-60}
    local wait_time=0
    
    log_info "Waiting for $service to be ready..."
    
    while [ $wait_time -lt $max_wait ]; do
        if docker compose -f "$COMPOSE_TEST" ps "$service" 2>/dev/null | grep -q "healthy\|running"; then
            log_success "$service is ready"
            return 0
        fi
        sleep 2
        wait_time=$((wait_time + 2))
        echo -n "."
    done
    
    echo ""
    log_error "$service failed to start within ${max_wait}s"
    return 1
}

build_images() {
    if [ "$NO_BUILD" = "1" ]; then
        log_info "Skipping image build (--no-build)"
        return 0
    fi
    
    log_info "Building Docker images..."
    cd "$PROJECT_DIR"
    
    docker compose -f "$COMPOSE_TEST" build --quiet
}

cleanup_containers() {
    log_info "Cleaning up test containers..."
    cd "$PROJECT_DIR"
    
    docker compose -f "$COMPOSE_TEST" down -v --remove-orphans 2>/dev/null || true
    docker compose -f "$COMPOSE_DUAL" down -v --remove-orphans 2>/dev/null || true
    docker compose -f "$COMPOSE_LLM" down -v --remove-orphans 2>/dev/null || true
    
    # Remove any dangling test images
    docker image prune -f --filter "label=verlihub-test" 2>/dev/null || true
    
    log_success "Cleanup complete"
}

run_py_unit_tests() {
    log_info "Running verlihub-py unit tests (SQLite)..."
    cd "$PROJECT_DIR"
    
    build_images
    
    local pytest_args="-v --tb=short"
    [ -n "$VERBOSE" ] && pytest_args="-vvs --tb=long"
    [ -n "$PATTERN" ] && pytest_args="$pytest_args -k '$PATTERN'"
    
    local cov_vol_args=()
    local cov_pytest_args=""
    if [ "$COVERAGE_MODE" = "1" ]; then
        cov_vol_args=(-v "${COVERAGE_DIR}:/app/coverage-reports")
        cov_pytest_args="--cov=verlihub --cov-config=python/pyproject.toml --cov-report=html:/app/coverage-reports/sqlite --cov-report=xml:/app/coverage-reports/sqlite-coverage.xml"
    fi

    docker compose -f "$COMPOSE_TEST" run --rm \
        -e VH_DB_BACKEND=sqlite \
        -e PYTEST_ARGS="$pytest_args" \
        "${cov_vol_args[@]}" \
        mysql-tests \
        pytest python/tests/ $pytest_args $cov_pytest_args \
            --ignore=python/tests/test_nmdc_stress.py \
            --ignore=python/tests/test_benchmarks.py
    
    log_success "Unit tests completed"
}

run_py_mysql_tests() {
    log_info "Running verlihub-py tests against MySQL..."
    cd "$PROJECT_DIR"
    
    build_images
    
    # Start MySQL
    docker compose -f "$COMPOSE_TEST" up -d mysql
    wait_for_service mysql 120
    
    # Run tests
    local exit_code=0
    if [ "$COVERAGE_MODE" = "1" ]; then
        docker compose -f "$COMPOSE_TEST" run --rm \
            -v "${COVERAGE_DIR}:/app/coverage-reports" \
            mysql-tests \
            pytest python/tests/ -v --tb=short \
                --ignore=python/tests/test_nmdc_stress.py \
                --ignore=python/tests/test_benchmarks.py \
                --cov=verlihub --cov-config=python/pyproject.toml \
                --cov-report=html:/app/coverage-reports/mysql \
                --cov-report=xml:/app/coverage-reports/mysql-coverage.xml \
            || exit_code=$?
    else
        docker compose -f "$COMPOSE_TEST" run --rm mysql-tests || exit_code=$?
    fi
    
    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_TEST" stop mysql
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_success "MySQL tests completed"
    else
        log_error "MySQL tests failed"
    fi
    
    return $exit_code
}

run_py_postgres_tests() {
    log_info "Running verlihub-py tests against PostgreSQL..."
    cd "$PROJECT_DIR"
    
    build_images
    
    # Start PostgreSQL
    docker compose -f "$COMPOSE_TEST" up -d postgres
    wait_for_service postgres 120
    
    # Run tests
    local exit_code=0
    if [ "$COVERAGE_MODE" = "1" ]; then
        docker compose -f "$COMPOSE_TEST" run --rm \
            -v "${COVERAGE_DIR}:/app/coverage-reports" \
            postgres-tests \
            pytest python/tests/ -v --tb=short \
                --ignore=python/tests/test_nmdc_stress.py \
                --ignore=python/tests/test_benchmarks.py \
                --cov=verlihub --cov-config=python/pyproject.toml \
                --cov-report=html:/app/coverage-reports/postgres \
                --cov-report=xml:/app/coverage-reports/postgres-coverage.xml \
            || exit_code=$?
    else
        docker compose -f "$COMPOSE_TEST" run --rm postgres-tests || exit_code=$?
    fi
    
    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_TEST" stop postgres
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_success "PostgreSQL tests completed"
    else
        log_error "PostgreSQL tests failed"
    fi
    
    return $exit_code
}

run_py_all_db_tests() {
    log_info "Running verlihub-py tests against all database backends..."
    cd "$PROJECT_DIR"
    
    build_images
    
    # Start both databases
    docker compose -f "$COMPOSE_TEST" up -d mysql postgres
    wait_for_service mysql 120
    wait_for_service postgres 120
    
    # Run comprehensive tests
    local exit_code=0
    docker compose -f "$COMPOSE_TEST" run --rm all-db-tests || exit_code=$?
    
    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_TEST" stop mysql postgres
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_success "All database tests completed"
    else
        log_error "Some database tests failed"
    fi
    
    return $exit_code
}

run_original_tests() {
    log_info "Running original verlihub tests (MySQL only)..."
    log_warning "Original Verlihub only supports MySQL"
    cd "$PROJECT_DIR"
    
    # Build original verlihub image
    docker compose -f "$COMPOSE_DUAL" build original-build
    
    # Start MySQL
    docker compose -f "$COMPOSE_DUAL" up -d mysql
    wait_for_service mysql 120
    
    # Run tests
    local exit_code=0
    docker compose -f "$COMPOSE_DUAL" run --rm original-tests || exit_code=$?
    
    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_DUAL" stop mysql
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_success "Original verlihub tests completed"
    else
        log_error "Original verlihub tests failed"
    fi
    
    return $exit_code
}

run_dual_tests() {
    log_info "Running dual-build tests (Original vs Verlihub-py)..."
    log_warning "Original Verlihub: MySQL only"
    log_info "Verlihub-py: Testing with MySQL for comparison"
    cd "$PROJECT_DIR"
    
    # Build both images
    docker compose -f "$COMPOSE_DUAL" build
    
    # Start MySQL (required for original verlihub)
    docker compose -f "$COMPOSE_DUAL" up -d mysql
    wait_for_service mysql 120
    
    # Run tests
    local exit_code=0
    docker compose -f "$COMPOSE_DUAL" up --abort-on-container-exit test-results || exit_code=$?
    
    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_DUAL" down
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_success "Dual-build tests completed"
    else
        log_error "Dual-build tests failed"
    fi
    
    return $exit_code
}

run_sql_semantics_tests() {
    log_info "Running SQL semantics comparison tests..."
    cd "$PROJECT_DIR"
    
    build_images
    
    # Start both databases
    docker compose -f "$COMPOSE_TEST" up -d mysql postgres
    wait_for_service mysql 120
    wait_for_service postgres 120
    
    # Run semantics tests
    local exit_code=0
    docker compose -f "$COMPOSE_TEST" run --rm sql-semantics-tests || exit_code=$?
    
    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_TEST" stop mysql postgres
    fi
    
    if [ $exit_code -eq 0 ]; then
        log_success "SQL semantics tests completed"
    else
        log_error "SQL semantics tests found differences"
    fi
    
    return $exit_code
}

run_llm_tests() {
    log_info "Running LLM integration tests (Ollama + qwen2.5:1.5b)..."
    log_info "This pulls ~1.2 GB model on first run — subsequent runs use cache"
    cd "$PROJECT_DIR"

    # Build images
    if [ "$NO_BUILD" != "1" ]; then
        log_info "Building Docker images..."
        docker compose -f "$COMPOSE_LLM" build --quiet
    fi

    # Run tests (ollama-pull runs automatically via depends_on)
    local exit_code=0
    docker compose -f "$COMPOSE_LLM" up \
        --build --abort-on-container-exit llm-tests \
        || exit_code=$?

    if [ "$KEEP_RUNNING" != "1" ]; then
        docker compose -f "$COMPOSE_LLM" down --remove-orphans
    fi

    if [ $exit_code -eq 0 ]; then
        log_success "LLM integration tests completed"
    else
        log_error "LLM integration tests failed"
    fi

    return $exit_code
}

run_all_tests() {
    log_info "Running all test suites..."
    
    local failed=0
    
    # Unit tests (SQLite)
    run_py_unit_tests || failed=1
    
    # MySQL tests
    run_py_mysql_tests || failed=1
    
    # PostgreSQL tests
    run_py_postgres_tests || failed=1
    
    # SQL semantics
    run_sql_semantics_tests || failed=1
    
    # Dual build (if requested)
    if [ "$RUN_ORIGINAL" = "1" ]; then
        run_original_tests || failed=1
    fi
    
    if [ $failed -eq 0 ]; then
        log_success "All test suites passed!"
    else
        log_error "Some test suites failed"
    fi
    
    return $failed
}

# Parse arguments
COMMAND="${1:-help}"
shift || true

VERBOSE=""
PATTERN=""
NO_BUILD=""
KEEP_RUNNING=""
RUN_ORIGINAL=""
COVERAGE_MODE=""
COVERAGE_DIR=""

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
        --no-build)
            NO_BUILD=1
            shift
            ;;
        --keep)
            KEEP_RUNNING=1
            shift
            ;;
        --with-original)
            RUN_ORIGINAL=1
            shift
            ;;
        --coverage)
            COVERAGE_MODE=1
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Set up coverage directory if coverage mode is enabled
if [ "$COVERAGE_MODE" = "1" ]; then
    COVERAGE_DIR="${PROJECT_DIR}/build/coverage-reports"
    mkdir -p "$COVERAGE_DIR"
    log_info "Coverage mode enabled — reports will be saved to $COVERAGE_DIR"
fi

# Check Docker availability
check_docker

# Change to project directory
cd "$PROJECT_DIR"

# Execute command
case $COMMAND in
    all)
        run_all_tests
        ;;
    py-unit)
        run_py_unit_tests
        ;;
    py-mysql)
        run_py_mysql_tests
        ;;
    py-postgres)
        run_py_postgres_tests
        ;;
    py-all-db)
        run_py_all_db_tests
        ;;
    llm)
        run_llm_tests
        ;;
    original)
        run_original_tests
        ;;
    dual)
        run_dual_tests
        ;;
    sql-semantics)
        run_sql_semantics_tests
        ;;
    cleanup)
        cleanup_containers
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $COMMAND"
        show_help
        exit 1
        ;;
esac

echo ""
log_info "Done!"
