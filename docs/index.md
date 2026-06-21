# Vooglaadija — Documentation Index

Media Link Processor — Async API service for authenticated video download jobs.

## Quick Reference

| Part | Type | Key Tech | Root |
|------|------|----------|------|
| API Server | backend | FastAPI, SQLAlchemy 2.0 Async, JWT + bcrypt | `app/` |
| Worker | backend | Async Redis consumer, yt-dlp, CircuitBreaker | `worker/` |
| Frontend | web | Tailwind CSS 3.4, HTMX 1.9, Jinja2 | `frontend/` |
| Infrastructure | infra | Docker Compose, Nginx (SSL), Prometheus | `infra/` |

## Generated Documentation

### Project Overview

- [Project Overview](./project-overview.md) — Executive summary, tech stack, architecture classification
- [Source Tree Analysis](./source-tree-analysis.md) — Annotated directory tree with integration points

### Architecture

- [Architecture — API Server](./architecture-api.md) — FastAPI backend, services, auth, security
- [Architecture — Worker](./architecture-worker.md) — Job processing, circuit breaker, error handling
- [Architecture — Frontend](./architecture-frontend.md) — Tailwind design system, HTMX, SSE
- [Architecture — Infrastructure](./architecture-infra.md) — Docker, monitoring stack, security

### API & Data

- [API Contracts](./api-contracts-api.md) — All REST API, Web/HTMX, SSE, and health endpoints
- [Data Models](./data-models-api.md) — SQLAlchemy ORM models, migrations, DB configuration

### Components

- [Component Inventory — API Server](./component-inventory-api.md) — Routes, services, models, schemas
- [Component Inventory — Worker](./component-inventory-worker.md) — Queue structures, maintenance tasks

### Integration

- [Integration Architecture](./integration-architecture.md) — How parts communicate, data flow, shared dependencies

### Development

- [Development Guide](./development-guide.md) — Setup, running, testing, linting, CI/CD

## Existing Canonical Documentation

- [ARCHITECTURE](./ARCHITECTURE.md) — System component diagram and responsibilities
- [API](./API.md) — Full endpoint reference
- [OPS](./OPS.md) — Environment variables and deployment
- [CONTRIBUTING](./CONTRIBUTING.md) — Contribution guidelines
- [PRODUCTION_DEPLOYMENT](./PRODUCTION_DEPLOYMENT.md) — Production deployment details

## Research & Planning

- [RESEARCH](./RESEARCH.md) — Architectural research notes
- [DOCKER-MONITORING](./DOCKER-MONITORING.md) — Docker monitoring setup
- [IMPLEMENTATION_PLAN](./IMPLEMENTATION_PLAN.md) — Bug-fix implementation plan
- [NETDATA_INTEGRATION_PLAN](./NETDATA_INTEGRATION_PLAN.md) — NetData integration plan
- [PSYCOPG3_MIGRATION](./PSYCOPG3_MIGRATION.md) — psycopg3 migration notes
- [STRUCTLOG_INTEGRATION](./STRUCTLOG_INTEGRATION.md) — structlog integration
- [TOP1_DEMO_GUIDE](./TOP1_DEMO_GUIDE.md) — Demo presentation guide
- [TOP1_STRATEGY](./TOP1_STRATEGY.md) — Demo strategy
- [research/kilo-cli-deep-research.md](./research/kilo-cli-deep-research.md) — CLI research

## AI Agent Reference

- [AGENTS.md](../AGENTS.md) (repo root) — Authoritative agent instructions
- [guides/AGENTS.md](./guides/AGENTS.md) — Agent instructions guide

## Getting Started

```bash
cp .env.example .env
hatch env create
hatch run db-migrate
hatch run dev              # API on :8000
python -m worker.main      # Worker (separate terminal)
pnpm run frontend:deploy
```
