#!/bin/bash
# Integration test runner script
# Runs the full integration test suite against a Docker Verlihub instance

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Configuration
HUB_HOST="${HUB_HOST:-localhost}"
HUB_PORT="${HUB_PORT:-4111}"
API_PORT="${API_PORT:-30000}"
ADMIN_NICK="${ADMIN_NICK:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"
CORS_ORIGINS="${CORS_ORIGINS:-https://sublevels.net https://www.sublevels.net https://wintermute.sublevels.net}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}Verlihub Integration Test Suite${NC}"
echo -e "${YELLOW}========================================${NC}"

# Check if running in Docker or locally
if [ -n "$DOCKER_INTEGRATION_TEST" ]; then
    echo "Running inside Docker container"
else
    echo "Running locally - will use Docker Compose"
    
    # Start the Docker stack
    echo -e "\n${YELLOW}Starting Docker stack...${NC}"
    cd "$PROJECT_DIR"
    
    # Build if needed
    docker compose build
    
    # Start MySQL and Verlihub
    docker compose up -d mysql verlihub
    
    # Wait for services to be ready
    echo "Waiting for MySQL to be ready..."
    for i in {1..30}; do
        if docker compose exec mysql mysql -uverlihub -pverlihub -e "SELECT 1" &>/dev/null; then
            echo "MySQL is ready!"
            break
        fi
        echo "  Waiting... ($i/30)"
        sleep 2
    done
    
    echo "Waiting for Verlihub to be ready..."
    for i in {1..30}; do
        if nc -z localhost $HUB_PORT 2>/dev/null; then
            echo "Verlihub is ready!"
            break
        fi
        echo "  Waiting... ($i/30)"
        sleep 2
    done
    
    sleep 5  # Extra time for plugin loading
fi

# Install test dependencies
echo -e "\n${YELLOW}Installing test dependencies...${NC}"
pip3 install requests --quiet 2>/dev/null || pip install requests --quiet

# Run the integration tests
echo -e "\n${YELLOW}Running integration tests...${NC}"
cd "$SCRIPT_DIR"

python3 integration_test.py \
    --hub-host "$HUB_HOST" \
    --hub-port "$HUB_PORT" \
    --api-port "$API_PORT" \
    --admin-nick "$ADMIN_NICK" \
    --admin-pass "$ADMIN_PASS" \
    --cors-origins $CORS_ORIGINS \
    --output test_results.json

TEST_EXIT_CODE=$?

# Show results
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}ALL TESTS PASSED!${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo -e "\n${RED}========================================${NC}"
    echo -e "${RED}SOME TESTS FAILED${NC}"
    echo -e "${RED}========================================${NC}"
fi

# Cleanup if running locally
if [ -z "$DOCKER_INTEGRATION_TEST" ]; then
    echo -e "\n${YELLOW}Cleaning up Docker stack...${NC}"
    read -p "Stop Docker containers? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd "$PROJECT_DIR"
        docker compose down
    fi
fi

exit $TEST_EXIT_CODE
