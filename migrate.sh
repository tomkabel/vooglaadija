#!/bin/bash
set -e

# Migrator script that uses Redis lock to ensure migrations run only once
# Usage: ./migrate.sh

REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:-}"
export REDISCLI_AUTH="${REDIS_PASSWORD}" # Export for redis-cli to use
MIGRATION_LOCK_KEY="vooglaadija:migration:lock"
MIGRATION_LOCK_PX=120000 # 120 seconds in milliseconds - longer TTL for safety margin

# Helper to build redis-cli command
# REDISCLI_AUTH is exported and redis-cli automatically uses it when set
redis_cmd() {
  redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$@"
}

acquire_lock() {
  # Use PX for millisecond precision and return the result
  redis_cmd SET "$MIGRATION_LOCK_KEY" "$$" NX PX "$MIGRATION_LOCK_PX"
}

# Safe release: only delete if we own the lock (compare $$ to stored value)
# Suppresses errors to prevent exit on lock already expired
release_lock() {
  # Use EVAL Lua script for atomic check-and-delete
  # Returns 1 if lock not owned (already expired), 0 if deleted
  redis_cmd EVAL "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end" 1 "$MIGRATION_LOCK_KEY" "$$" 2>/dev/null || true
}

# Renew lock timeout while holding it - loops until lock is lost or parent exits
renew_lock() {
  local sleep_interval=$((MIGRATION_LOCK_PX / 2 / 1000)) # Half of lock timeout in seconds
  while true; do
    # Check if parent (migration process) is still running
    # If the parent PID changed (we're orphaned), exit immediately
    if ! kill -0 "$$" 2>/dev/null; then
      echo "Parent process died, exiting renew_lock"
      exit 0
    fi

    # Check if we still own the lock
    current_holder=$(redis_cmd GET "$MIGRATION_LOCK_KEY" 2>/dev/null)
    if [ "$current_holder" != "$$" ]; then
      # Lock lost or different owner - we've been preempted
      echo "Lock lost or preempted by another process"
      exit 0
    fi

    # Refresh the lock expiration
    result=$(redis_cmd EVAL "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end" 1 "$MIGRATION_LOCK_KEY" "$$" "$MIGRATION_LOCK_PX" 2>/dev/null)

    if [ "$result" != "1" ]; then
      # Failed to renew - lock lost
      echo "Failed to renew lock (result: $result)"
      exit 0
    fi

    sleep "$sleep_interval"
  done
}

wait_for_lock_release() {
  max_wait=120
  waited=0
  while [ $waited -lt $max_wait ]; do
    # Use subshell to capture output while protecting from set -e
    holder=$(redis_cmd GET "$MIGRATION_LOCK_KEY" 2>/dev/null)

    if [ -z "$holder" ] || [ "$holder" = "(nil)" ]; then
      # Lock expired or released - try to acquire it ourselves
      echo "Lock released, attempting to acquire..."
      acquire_result=$(acquire_lock 2>/dev/null)
      if [ "$acquire_result" = "OK" ]; then
        echo "Successfully acquired migration lock"
        return 0 # We now own the lock
      fi
      # Someone else got it first - continue waiting
      echo "Lock acquired by another process, continuing to wait..."
    else
      if [ "$holder" = "$$" ]; then
        # We already own the lock somehow (shouldn't happen but handle it)
        echo "Already own the lock (PID $$)"
        return 0
      fi
    fi

    echo "Waiting for migration lock to be released by PID $holder..."
    sleep 2
    waited=$((waited + 2))
  done

  echo "Timeout waiting for migration lock"
  return 1
}

run_migrations_and_verify() {
  # Start lock renewal in background
  renew_lock &
  RENEW_PID=$!

  # Register trap to release lock on EXIT only (not ERR - don't mark done on failure)
  trap 'kill $RENEW_PID 2>/dev/null; wait $RENEW_PID 2>/dev/null || true; release_lock' EXIT

  # Run migrations (don't fail on error - capture the exit code)
  echo "Running database migrations..."
  set +e # Temporarily disable errexit to capture alembic exit code
  python -m alembic -c /app/alembic.ini upgrade head 2>&1
  migrations_result=$?
  set -e # Re-enable errexit

  if [ $migrations_result -eq 0 ]; then
    echo "Migrations completed successfully"

    # Verify migrations are at head using alembic's built-in check
    verify_output=$(python -m alembic -c /app/alembic.ini current --check-heads 2>&1)
    verify_result=$?
    if [ $verify_result -ne 0 ]; then
      echo "ERROR: current migration does not match head. Schema is out of date!"
      echo "Verification output: $verify_output"
      migrations_result=1
    else
      echo "Verified: migrations at head"
    fi
  else
    echo "Migration failed with exit code $migrations_result"
  fi

  # The EXIT trap will handle cleanup
  return $migrations_result
}

echo "Running database migrations..."

# Validate migration chain before attempting upgrade. A broken chain must
# stop startup; stamping head without running missing DDL is unsafe.
python /app/scripts/ensure_migration_chain.py

# Try to acquire lock
lock_result=$(acquire_lock 2>/dev/null)
if [ "$lock_result" = "OK" ]; then
  echo "Acquired migration lock, running migrations..."
  run_migrations_and_verify
else
  echo "Migration lock held by another process, waiting..."
  wait_for_lock_release
  wait_result=$?

  if [ $wait_result -ne 0 ]; then
    echo "Failed to wait for lock release"
    exit 1
  fi

  # We now own the lock (wait_for_lock_release acquired it for us)
  echo "Running migrations with acquired lock..."
  run_migrations_and_verify
fi
