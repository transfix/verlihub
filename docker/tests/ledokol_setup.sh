#!/bin/bash
# Ledokol setup script for legacy Verlihub container
# Clones ledokol.lua from GitHub and starts the hub

set -e

SCRIPTS_DIR="/usr/local/share/verlihub/scripts"
LEDOKOL_REPO="https://github.com/Verlihub/ledokol.git"

if [ ! -f "${SCRIPTS_DIR}/ledokol.lua" ]; then
    echo "[ledokol] Cloning ledokol repository..."
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1 || true
    cd /tmp
    git clone --depth 1 "${LEDOKOL_REPO}" 2>/dev/null || true

    if [ -f /tmp/ledokol/ledokol.lua ]; then
        cp /tmp/ledokol/ledokol.lua "${SCRIPTS_DIR}/ledokol.lua"
        echo "[ledokol] ledokol.lua installed to ${SCRIPTS_DIR}"
    else
        echo "[ledokol] WARNING: Could not clone ledokol.lua — tests may fail"
    fi
    rm -rf /tmp/ledokol
else
    echo "[ledokol] ledokol.lua already present"
fi

# Hand off to the normal entrypoint
exec /entrypoint.sh verlihub
