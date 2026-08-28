#!/usr/bin/env bash
set -euo pipefail

# Storage directory ownership is already set at build time.
# Migrations are handled by the API service, skipping here.

echo "Starting Celery worker..."

# Forward the compose-provided command (e.g. `python -m worker.celery_main
# worker`); default to the Celery worker when the image is run bare.
if [ $# -eq 0 ]; then
  exec python -m worker.celery_main worker
fi

exec "$@"
