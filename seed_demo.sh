#!/bin/bash
# Idempotent demo-account seeding.
#
# Runs the Python seed script that creates the demo user (demo@vooglaadija.io)
# and pre-seeded jobs if they don't already exist. Safe to invoke on every
# startup: it is a no-op when the demo user is present.
#
# Seeding failures are logged but do NOT abort startup — the primary API
# must come up even if seeding is temporarily unable to reach the DB.
set +e

echo "Ensuring demo account exists..."
python -m scripts.seed_demo_data
seed_result=$?

if [ $seed_result -eq 0 ]; then
  echo "Demo account seeding completed (idempotent — no-op if already present)."
else
  echo "WARNING: demo account seeding failed with exit code $seed_result. Continuing startup." >&2
fi
