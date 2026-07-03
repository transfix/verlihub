#!/bin/sh
# Certbot entrypoint for Let's Encrypt certificate management.
#
# On first run: obtains a certificate via HTTP-01 or standalone challenge.
# Then sleeps and renews every 12 hours (certbot checks if renewal is needed).
#
# Environment variables:
#   LE_DOMAIN   — domain name (required)
#   LE_EMAIL    — email for registration (required)
#   LE_STAGING  — set to "1" for staging/test certs (optional)
#   CERT_DIR    — output directory for hub.crt/hub.key (default: /certs)

set -e

CERT_DIR="${CERT_DIR:-/certs}"
RENEWAL_INTERVAL="${RENEWAL_INTERVAL:-43200}"  # 12 hours

if [ -z "$LE_DOMAIN" ] || [ -z "$LE_EMAIL" ]; then
    echo "[certbot] ERROR: LE_DOMAIN and LE_EMAIL must be set"
    exit 1
fi

STAGING_FLAG=""
if [ "$LE_STAGING" = "1" ]; then
    STAGING_FLAG="--staging"
    echo "[certbot] Using Let's Encrypt STAGING environment"
fi

LIVE_DIR="/etc/letsencrypt/live/${LE_DOMAIN}"

# Copy certs from Let's Encrypt live dir to the shared volume
# in the format the TLS proxy expects.
copy_certs() {
    if [ -f "${LIVE_DIR}/fullchain.pem" ] && [ -f "${LIVE_DIR}/privkey.pem" ]; then
        cp -fL "${LIVE_DIR}/fullchain.pem" "${CERT_DIR}/hub.crt"
        cp -fL "${LIVE_DIR}/privkey.pem"   "${CERT_DIR}/hub.key"
        chmod 644 "${CERT_DIR}/hub.crt"
        chmod 600 "${CERT_DIR}/hub.key"
        echo "[certbot] Certificates copied to ${CERT_DIR}"
    else
        echo "[certbot] WARNING: Expected cert files not found in ${LIVE_DIR}"
    fi
}

# Initial certificate request (if not already obtained)
if [ ! -d "$LIVE_DIR" ]; then
    echo "[certbot] Requesting certificate for ${LE_DOMAIN}..."
    certbot certonly \
        --standalone \
        --non-interactive \
        --agree-tos \
        --email "$LE_EMAIL" \
        --domain "$LE_DOMAIN" \
        --preferred-challenges http \
        $STAGING_FLAG

    if [ $? -eq 0 ]; then
        echo "[certbot] Certificate obtained successfully"
        copy_certs
    else
        echo "[certbot] ERROR: Failed to obtain certificate"
        echo "[certbot] The TLS proxy will use self-signed certificates"
        exit 1
    fi
else
    echo "[certbot] Certificate already exists for ${LE_DOMAIN}"
    copy_certs
fi

# Renewal loop
echo "[certbot] Starting renewal loop (every ${RENEWAL_INTERVAL}s)..."
while true; do
    sleep "$RENEWAL_INTERVAL"
    echo "[certbot] Checking for renewal..."
    certbot renew --quiet
    copy_certs
done
