#!/usr/bin/env bash
set -euo pipefail

# Storage directory ownership is already set at build time.
# Migrations are handled by the API service, skipping here.

echo "Starting worker..."
exec python -m worker.main
