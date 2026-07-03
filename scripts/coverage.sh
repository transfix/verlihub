#!/usr/bin/env bash
#
# Combined C++ and Python coverage report generator for Verlihub
#
# Usage:
#   ./scripts/coverage.sh [--cpp-only | --python-only | --all]
#
# Prerequisites:
#   C++: gcov, lcov, genhtml (apt install lcov)
#   Python: pytest-cov (pip install pytest-cov)
#
# The script will:
#   1. Build the C++ project with coverage instrumentation
#   2. Run CTest (GTest-based core tests)
#   3. Collect gcov data with lcov
#   4. Run Python tests with pytest-cov
#   5. Generate HTML reports in build/coverage_html/{cpp,python}/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build"
COVERAGE_DIR="${BUILD_DIR}/coverage_html"
PYTHON_DIR="${PROJECT_ROOT}/python"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ ER ]${NC} $*"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
MODE="all"
case "${1:-}" in
    --cpp-only)   MODE="cpp" ;;
    --python-only) MODE="python" ;;
    --all)        MODE="all" ;;
    -h|--help)
        echo "Usage: $0 [--cpp-only | --python-only | --all]"
        exit 0
        ;;
    "")           MODE="all" ;;
    *)            err "Unknown option: $1"; exit 1 ;;
esac

mkdir -p "${COVERAGE_DIR}"

# ---------------------------------------------------------------------------
# C++ coverage
# ---------------------------------------------------------------------------
run_cpp_coverage() {
    info "=== C++ Code Coverage ==="

    # Check prerequisites
    for tool in gcov lcov genhtml; do
        if ! command -v "$tool" &>/dev/null; then
            err "$tool not found. Install with: sudo apt install lcov"
            exit 1
        fi
    done

    # Configure with coverage enabled
    info "Configuring CMake with COVERAGE=ON..."
    cd "${BUILD_DIR}"
    cmake "${PROJECT_ROOT}" \
        -DCOVERAGE=ON \
        -DDEFINE_DEBUG=ON \
        -DBUILD_CORE_TESTS=ON \
        -DBUILD_PYTHON_BINDINGS=OFF \
        2>&1 | tail -5

    # Build
    info "Building with coverage instrumentation..."
    make -j"$(nproc)" 2>&1 | tail -3

    # Clear any old coverage data
    info "Clearing previous coverage data..."
    lcov --zerocounters --directory "${BUILD_DIR}" --quiet 2>/dev/null || true
    find "${BUILD_DIR}" -name '*.gcda' -delete 2>/dev/null || true

    # Common lcov flags for GCC 13 compatibility
    local LCOV_COMPAT="--ignore-errors mismatch,gcov,negative,unused"

    # Capture baseline (zero counters)
    info "Capturing baseline coverage..."
    lcov --capture --initial \
        --directory "${BUILD_DIR}" \
        --output-file "${COVERAGE_DIR}/cpp_base.info" \
        $LCOV_COMPAT \
        --quiet 2>/dev/null || true

    # Run tests
    info "Running C++ tests (CTest)..."
    cd "${BUILD_DIR}"
    if ctest --output-on-failure --label-regex "core" -j"$(nproc)" 2>&1; then
        ok "All C++ tests passed"
    else
        warn "Some C++ tests failed (coverage still collected)"
    fi

    # Capture test coverage
    info "Capturing test coverage data..."
    lcov --capture \
        --directory "${BUILD_DIR}" \
        --output-file "${COVERAGE_DIR}/cpp_test.info" \
        $LCOV_COMPAT \
        --quiet

    # Combine baseline + test
    if [ -f "${COVERAGE_DIR}/cpp_base.info" ]; then
        lcov --add-tracefile "${COVERAGE_DIR}/cpp_base.info" \
            --add-tracefile "${COVERAGE_DIR}/cpp_test.info" \
            --output-file "${COVERAGE_DIR}/cpp_combined.info" \
            $LCOV_COMPAT \
            --quiet
    else
        cp "${COVERAGE_DIR}/cpp_test.info" "${COVERAGE_DIR}/cpp_combined.info"
    fi

    # Filter: keep only project sources, exclude SWIG-generated code and
    # system/third-party headers
    info "Filtering coverage data (excluding SWIG boilerplate, system headers)..."
    lcov --remove "${COVERAGE_DIR}/cpp_combined.info" \
        '/usr/*' \
        '*/build/*' \
        '*/swig/*verlihub_core_wrap*' \
        '*/test_*' \
        --output-file "${COVERAGE_DIR}/cpp_filtered.info" \
        $LCOV_COMPAT \
        --quiet

    # Extract only our source directories
    lcov --extract "${COVERAGE_DIR}/cpp_filtered.info" \
        "*/src/*.cpp" \
        "*/src/*.h" \
        "*/src/core/*.cpp" \
        "*/src/core/*.h" \
        "*/plugins/*/*.cpp" \
        "*/plugins/*/*.h" \
        --output-file "${COVERAGE_DIR}/cpp_final.info" \
        $LCOV_COMPAT \
        --quiet 2>/dev/null || cp "${COVERAGE_DIR}/cpp_filtered.info" "${COVERAGE_DIR}/cpp_final.info"

    # Generate HTML report
    info "Generating C++ HTML coverage report..."
    genhtml "${COVERAGE_DIR}/cpp_final.info" \
        --output-directory "${COVERAGE_DIR}/cpp" \
        --title "Verlihub C++ Coverage" \
        --legend \
        --branch-coverage \
        --function-coverage \
        --demangle-cpp \
        --prefix "${PROJECT_ROOT}" \
        $LCOV_COMPAT \
        --quiet

    # Print summary
    echo ""
    ok "C++ coverage report: ${COVERAGE_DIR}/cpp/index.html"
    echo ""
    lcov --summary "${COVERAGE_DIR}/cpp_final.info" $LCOV_COMPAT 2>&1 | grep -E "lines|functions|branches"
    echo ""
}

# ---------------------------------------------------------------------------
# Python coverage
# ---------------------------------------------------------------------------
run_python_coverage() {
    info "=== Python Code Coverage ==="

    cd "${PYTHON_DIR}"

    # Determine Python executable
    local PYTHON="${PROJECT_ROOT}/.venv/bin/python"
    if [ ! -x "$PYTHON" ]; then
        PYTHON="$(command -v python3)"
    fi

    info "Using Python: $PYTHON"

    # Run pytest with coverage
    info "Running Python tests with coverage..."
    "$PYTHON" -m pytest tests/ \
        --cov=verlihub \
        --cov-report=term-missing \
        --cov-report="html:${COVERAGE_DIR}/python" \
        --cov-config=pyproject.toml \
        -q 2>&1

    echo ""
    ok "Python coverage report: ${COVERAGE_DIR}/python/index.html"
    echo ""
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    echo ""
    info "============================================"
    info "         Coverage Reports Summary"
    info "============================================"
    echo ""

    if [ "$MODE" = "all" ] || [ "$MODE" = "cpp" ]; then
        if [ -f "${COVERAGE_DIR}/cpp/index.html" ]; then
            ok "C++ report:    file://${COVERAGE_DIR}/cpp/index.html"
        fi
    fi

    if [ "$MODE" = "all" ] || [ "$MODE" = "python" ]; then
        if [ -f "${COVERAGE_DIR}/python/index.html" ]; then
            ok "Python report: file://${COVERAGE_DIR}/python/index.html"
        fi
    fi

    echo ""
    info "Serve reports locally:  cd ${COVERAGE_DIR} && python3 -m http.server 8765"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
case "$MODE" in
    cpp)
        run_cpp_coverage
        ;;
    python)
        run_python_coverage
        ;;
    all)
        run_cpp_coverage
        run_python_coverage
        ;;
esac

print_summary
