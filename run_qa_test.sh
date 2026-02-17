#!/bin/bash
# Verlihub QA Test Runner
#
# Stands up PostgreSQL + verlihub-py hub + 16 NMDC load-test clients.
# The hub's NMDC port (4111) and API port (8000) are exposed on the host
# so you can connect a desktop DC++ client while the test runs.
#
# Usage:
#   ./run_qa_test.sh                    # Run load test then exit
#   ./run_qa_test.sh --keep-alive 300   # Keep hub alive 300s after test
#   ./run_qa_test.sh --interactive      # Keep hub alive indefinitely
#   ./run_qa_test.sh --down             # Tear everything down
#   ./run_qa_test.sh --logs             # Tail all logs
#   ./run_qa_test.sh --status           # Show container status

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker/docker-compose.qa-test.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ACTION="run"
KEEP_ALIVE=0
REBUILD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-alive|-k)
            KEEP_ALIVE="$2"
            shift 2
            ;;
        --interactive|-i)
            ACTION="interactive"
            shift
            ;;
        --down|--stop)
            ACTION="down"
            shift
            ;;
        --logs|-l)
            ACTION="logs"
            shift
            ;;
        --status|-s)
            ACTION="status"
            shift
            ;;
        --rebuild)
            REBUILD=true
            shift
            ;;
        -h|--help)
            echo "Verlihub QA Test Runner"
            echo ""
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --keep-alive, -k SEC  Keep hub alive N seconds after load test (default: 0)"
            echo "  --interactive, -i     Keep hub running until you Ctrl+C"
            echo "  --down, --stop        Tear down all QA containers"
            echo "  --logs, -l            Tail container logs"
            echo "  --status, -s          Show container status"
            echo "  --rebuild             Force rebuild Docker images"
            echo "  -h, --help            Show this help"
            echo ""
            echo "Connect a desktop client:  dc://localhost:4111"
            echo "Dashboard:                 http://localhost:8000/dashboard/spa"
            echo "API:                       http://localhost:8000/api/health"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

case "$ACTION" in
    down)
        echo -e "${BLUE}→ Tearing down QA environment...${NC}"
        docker compose -f "$COMPOSE_FILE" down -v
        echo -e "${GREEN}✓ QA environment removed${NC}"
        exit 0
        ;;
    logs)
        docker compose -f "$COMPOSE_FILE" logs -f
        exit 0
        ;;
    status)
        docker compose -f "$COMPOSE_FILE" ps
        exit 0
        ;;
esac

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           Verlihub QA Test Runner                      ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}║  PostgreSQL + verlihub-py + 16 NMDC load clients       ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# Build
BUILD_FLAGS=""
if [ "$REBUILD" = true ]; then
    BUILD_FLAGS="--build --no-cache"
else
    BUILD_FLAGS="--build"
fi

if [ "$ACTION" = "interactive" ]; then
    # Interactive mode: start hub + db, skip load test, keep alive
    echo -e "${BLUE}→ Starting QA hub in interactive mode...${NC}"
    echo -e "${YELLOW}  Hub will stay running until you press Ctrl+C${NC}"
    echo ""

    # Start only postgres and hub (not the load test)
    docker compose -f "$COMPOSE_FILE" up $BUILD_FLAGS qa-postgres qa-hub &
    COMPOSE_PID=$!

    # Wait for hub health
    echo -e "${BLUE}→ Waiting for hub to become healthy...${NC}"
    for i in $(seq 1 60); do
        if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
            echo ""
            echo -e "${GREEN}✓ Hub is ready!${NC}"
            echo ""
            echo -e "  ${GREEN}Connect desktop client:${NC}  dc://localhost:4111"
            echo -e "  ${GREEN}Dashboard:${NC}               http://localhost:8000/dashboard/spa"
            echo -e "  ${GREEN}API health:${NC}              http://localhost:8000/api/health"
            echo ""
            echo -e "${YELLOW}  Press Ctrl+C to stop${NC}"
            break
        fi
        echo -n "."
        sleep 2
    done

    # Wait for Ctrl+C
    wait $COMPOSE_PID 2>/dev/null || true

    echo ""
    echo -e "${BLUE}→ Stopping...${NC}"
    docker compose -f "$COMPOSE_FILE" down -v
    echo -e "${GREEN}✓ QA environment stopped${NC}"

else
    # Normal mode: full run including load test
    echo -e "${BLUE}→ Building and starting QA environment...${NC}"

    # Export KEEP_ALIVE so docker-compose picks it up
    export KEEP_ALIVE

    docker compose -f "$COMPOSE_FILE" up $BUILD_FLAGS --abort-on-container-exit --exit-code-from qa-load-test
    EXIT_CODE=$?

    if [ "$KEEP_ALIVE" -gt 0 ] 2>/dev/null; then
        echo ""
        echo -e "${YELLOW}→ Load test complete. Hub staying alive for ${KEEP_ALIVE}s...${NC}"
        echo -e "  ${GREEN}Connect desktop client:${NC}  dc://localhost:4111"
        echo -e "  ${GREEN}Dashboard:${NC}               http://localhost:8000/dashboard/spa"
        echo ""
        sleep "$KEEP_ALIVE"
    fi

    # Tear down
    echo -e "${BLUE}→ Tearing down QA environment...${NC}"
    docker compose -f "$COMPOSE_FILE" down -v

    if [ $EXIT_CODE -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✓ QA test PASSED${NC}"
    else
        echo ""
        echo -e "${RED}✗ QA test FAILED (exit code: $EXIT_CODE)${NC}"
    fi

    exit $EXIT_CODE
fi
