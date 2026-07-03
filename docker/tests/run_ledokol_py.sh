#!/bin/bash
# Run ledokol verlihub-py test suite inside the test container
# Called by docker-compose.ledokol-test.yml

set -e

echo "=============================================="
echo "  Ledokol Verlihub-py Test Suite"
echo "  Hub: ${HUB_HOST:-verlihub-py}:${HUB_PORT:-4111}"
echo "=============================================="
echo ""

HUB_HOST="${HUB_HOST:-verlihub-py}"
HUB_PORT="${HUB_PORT:-4111}"
API_PORT="${API_PORT:-30000}"
ADMIN_NICK="${ADMIN_NICK:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"

# Install verlihub Python package for the full-featured NMDC client
echo "Installing verlihub Python package..."
pip install --quiet -e /python 2>/dev/null || \
    pip install --quiet /python 2>/dev/null || \
    echo "WARNING: Could not install verlihub package — will fall back to standalone client"

# Wait for the hub to become reachable
echo "Waiting for verlihub-py hub to start..."
for i in $(seq 1 90); do
    if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${HUB_HOST}', ${HUB_PORT}))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        echo "Hub is reachable after ${i}s"
        break
    fi
    sleep 1
done

# Give the hub time to finish plugin init
echo "Waiting 10s for plugin initialization..."
sleep 10

echo ""
echo "Starting ledokol py tests..."
echo ""

python3 test_ledokol_py.py \
    --hub-host "${HUB_HOST}" \
    --hub-port "${HUB_PORT}" \
    --admin-nick "${ADMIN_NICK}" \
    --admin-pass "${ADMIN_PASS}" \
    --api-url "http://${HUB_HOST}:${API_PORT}" \
    --output /tmp/ledokol_py_results.json

EXIT_CODE=$?
echo ""
echo "Py test exit code: ${EXIT_CODE}"

# Print results summary
if [ -f /tmp/ledokol_py_results.json ]; then
    python3 -c "
import json
with open('/tmp/ledokol_py_results.json') as f:
    r = json.load(f)
print('Passed: %d  Failed: %d  Skipped: %d' % (r.get('passed', 0), r.get('failed', 0), r.get('skipped', 0)))
" 2>/dev/null || true
fi

exit ${EXIT_CODE}
