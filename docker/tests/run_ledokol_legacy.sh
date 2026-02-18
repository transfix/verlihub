#!/bin/bash
# Run ledokol legacy test suite inside the test container
# Called by docker-compose.ledokol-test.yml

set -e

echo "=============================================="
echo "  Ledokol Legacy Test Suite"
echo "  Hub: ${HUB_HOST:-verlihub-legacy}:${HUB_PORT:-4111}"
echo "=============================================="
echo ""

HUB_HOST="${HUB_HOST:-verlihub-legacy}"
HUB_PORT="${HUB_PORT:-4111}"
ADMIN_NICK="${ADMIN_NICK:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"

# Wait for the hub to become reachable
echo "Waiting for legacy hub to start..."
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

# Give the hub time to finish Lua plugin init
echo "Waiting 15s for Lua plugin initialization..."
sleep 15

# Load the ledokol script via admin chat
echo "Loading ledokol.lua via !luaload..."
python3 -c "
import sys, time
try:
    from verlihub.client.nmdc import NMDCClient
except ImportError:
    sys.path.insert(0, '/python')
    from verlihub.client.nmdc import NMDCClient
try:
    c = NMDCClient('${HUB_HOST}', ${HUB_PORT}, '${ADMIN_NICK}', '${ADMIN_PASS}')
    c.connect(timeout=30)
except Exception as e:
    print(f'ERROR: Could not connect to hub: {e}')
    sys.exit(1)
time.sleep(2)
c.send_chat('!luaload ledokol.lua')
time.sleep(3)
msgs = c.wait_for_response(timeout=5)
for m in msgs:
    print('  hub> ' + str(m))
c.close()
print('ledokol.lua load command sent')
" || echo "WARNING: ledokol load might have failed"

echo ""
echo "Starting ledokol legacy tests..."
echo ""

python3 test_ledokol_legacy.py \
    --hub-host "${HUB_HOST}" \
    --hub-port "${HUB_PORT}" \
    --admin-nick "${ADMIN_NICK}" \
    --admin-pass "${ADMIN_PASS}" \
    --output /tmp/ledokol_legacy_results.json

EXIT_CODE=$?
echo ""
echo "Legacy test exit code: ${EXIT_CODE}"

# Print results summary
if [ -f /tmp/ledokol_legacy_results.json ]; then
    python3 -c "
import json
with open('/tmp/ledokol_legacy_results.json') as f:
    r = json.load(f)
print('Passed: %d  Failed: %d  Skipped: %d' % (r.get('passed', 0), r.get('failed', 0), r.get('skipped', 0)))
" 2>/dev/null || true
fi

exit ${EXIT_CODE}
