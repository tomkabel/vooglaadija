#!/bin/bash
# ===========================================
# Copy SSL Certificates to VPS
# Run this from project root after obtaining certificates
# ===========================================

set -e

# Configuration - modify these for your environment
VPS_HOST="${VPS_HOST:-ubuntu@37.114.46.226}"
VPS_PATH="${VPS_PATH:-/opt/vooglaadija}"
SSL_SOURCE="./infra/ssl"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }

# Check source files exist
if [[ ! -f "$SSL_SOURCE/fullchain.pem" ]]; then
    echo -e "${RED}[ERROR]${NC} Missing: $SSL_SOURCE/fullchain.pem"
    echo "Run phase 4 on VPS first, or copy certificates to $SSL_SOURCE"
    exit 1
fi

if [[ ! -f "$SSL_SOURCE/privkey.pem" ]]; then
    echo -e "${RED}[ERROR]${NC} Missing: $SSL_SOURCE/privkey.pem"
    exit 1
fi

# Verify certificates
log_info "Verifying certificate..."
openssl x509 -in "$SSL_SOURCE/fullchain.pem" -noout -subject -dates

log_info "Copying to VPS..."
scp "$SSL_SOURCE/fullchain.pem" "$SSL_SOURCE/privkey.pem" "$VPS_HOST:$VPS_PATH/infra/ssl/"

# Create symlinks and set permissions on VPS
ssh "$VPS_HOST" "cd $VPS_PATH/infra/ssl && \
  ln -sf fullchain.pem cert.pem && \
  ln -sf privkey.pem key.pem && \
  chmod 644 fullchain.pem cert.pem && \
  chmod 600 privkey.pem key.pem"

log_info "Certificates copied successfully!"
log_info "Verify on VPS: openssl x509 -in $VPS_PATH/infra/ssl/fullchain.pem -noout -enddate"
