#!/usr/bin/env bash
set -euo pipefail

# error_exit prints an error message to standard error and exits with status 1.
error_exit() {
  echo "ERROR: $1" >&2
  exit 1
}

# Ensure storage directories exist
mkdir -p /app/storage/downloads /app/storage/temp || error_exit "Failed to create storage directories"

# Run migrations with distributed lock to prevent concurrent runs
echo "Running database migrations..."
/app/migrate.sh || error_exit "Migration failed"

echo "Starting application..."
exec "$@"
