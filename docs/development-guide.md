# Development Guide

**Project:** Vooglaadija — Media Link Processor

---

## Prerequisites

- Python >= 3.12
- Node.js 20 (runtime), 22 (CI lint)
- pnpm 10+
- PostgreSQL 15+
- Redis 7+
- FFmpeg (for yt-dlp media processing)
- Hatch (Python build tool) + UV (installer)

## Environment Setup

```bash
# Clone and setup
git clone <repo>
cp .env.example .env    # Edit with your config

# Python environment
hatch env create

# Frontend dependencies
pnpm install

# Database
hatch run db-migrate    # Run migrations
```

## Running

```bash
# API Server (port 8000)
hatch run dev              # Hot reload enabled

# Worker (separate terminal)
python -m worker.main      # Consumes Redis queue

# Frontend CSS watch
cd frontend && pnpm run dev   # Rebuilds on changes
```

## Testing

```bash
# Unit tests (SQLite, excludes test_api/)
hatch run test:unit

# Integration tests (test_api/ — needs Postgres+Redis in CI)
hatch run test:integration

# All tests
hatch run test:all

# With coverage (HTML + XML + term)
hatch run test:cov
```

Tests use SQLite+aiosqlite (not PostgreSQL) by default. `conftest.py` sets `TESTING=1` and patches `core.config.settings.database_url` before any app import.

## Linting & Quality

```bash
# Python linting
hatch run lint:check           # ruff check
hatch run lint:format-check    # ruff format check

# JS/CSS/JSON linting
pnpm run lint:js             # biome

# Markdown linting
pnpm run lint:md             # markdownlint-cli2

# Type checking
hatch run type:check         # mypy app/ core/

# Security scanning
hatch run security:scan-bandit   # bandit
hatch run security:scan-safety   # safety

# All together
hatch run lint:lint-all
```

## Database Migrations

```bash
hatch run db-migrate          # Upgrade to latest
alembic revision --autogenerate -m "description"   # Create migration
hatch run db-rollback         # Rollback one step
```

## Pre-commit Hooks

Run on every commit (configured in `.pre-commit-config.yaml`):
- ruff check + format
- mypy (app/ and core/)
- biome (JS/CSS/JSON)
- shellcheck
- yamllint
- trailing-whitespace
- end-of-file-fixer

## CI Pipeline (GitHub Actions)

1. `lint` — ruff check, ruff format check, biome, markdownlint, prettier, yamllint, shellcheck
2. `type-check` — mypy (needs lint)
3. `unit-tests` — pytest with -n auto (needs lint, SQLite)
4. `integration-tests` — pytest with real Postgres+Redis (needs lint, CI_INTEGRATION=true)
5. `security` — bandit + safety (needs lint)
6. `build-check` — Docker build (needs all above)

## Docker

```bash
# Build image
docker build -t vooglaadija .

# Local environment
docker compose -f docker-compose.local.yml up

# Demo environment
docker compose -f docker-compose.demo.yml up
```

Multi-stage BuildKit builds. Non-root user (1000:1000). Images pushed to `ghcr.io`.
