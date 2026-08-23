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

# Seed the demo account (idempotent; no-op if already present).
# Keeps /web/demo-login working after every fresh deploy or DB wipe
# without a separate one-off container.
echo "Seeding demo account..."
/app/seed_demo.sh || echo "WARNING: demo seeding step returned non-zero." >&2

echo "Starting application..."
exec "$@"
