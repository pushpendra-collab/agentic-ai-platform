#!/bin/sh
# DEVELOPMENT ONLY: a self-signed certificate for the local gateway (Phase 8).
#
#   sh deploy/gateway/dev-certs.sh            # writes deploy/gateway/certs/tls.{crt,key}
#
# The certs/ directory is git- and docker-ignored. Production certificates come from the
# platform (ACME/Let's Encrypt, a cloud certificate manager, cert-manager, or the
# organisation's CA) and are mounted at /etc/nginx/certs; never commit a private key.
set -eu
dir="$(dirname "$0")/certs"
mkdir -p "$dir"
if [ -f "$dir/tls.key" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "certs exist in $dir (FORCE=1 to replace)"; exit 0
fi
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes -days 30 \
    -subj "/CN=localhost/O=agentic-platform development" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -keyout "$dir/tls.key" -out "$dir/tls.crt" 2>/dev/null
chmod 600 "$dir/tls.key" 2>/dev/null || true
echo "wrote $dir/tls.crt and $dir/tls.key (self-signed, 30 days, localhost only)"
