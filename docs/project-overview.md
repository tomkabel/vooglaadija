# Vooglaadija — Project Overview

**Media Link Processor** — Async API service for authenticated video download jobs.

---

## Executive Summary

Vooglaadija is a full-stack application that accepts YouTube video URLs, processes download jobs asynchronously via a background worker, and serves the results through a web dashboard. It features resilient job processing with circuit breaker protection, intelligent error classification with retry policies, real-time status updates via Server-Sent Events, and a comprehensive monitoring stack.

## Tech Stack Summary

| Component | Technology | Purpose |
|-----------|------------|---------|
| API | FastAPI + Uvicorn | Async HTTP API server |
| Database | PostgreSQL 15 + SQLAlchemy 2.0 Async | Primary data store |
| Queue | Redis 7 | Job queue, retry, Pub/Sub |
| Worker | Python 3.12 async + yt-dlp | Background job processing |
| Frontend | Jinja2 + HTMX + Tailwind CSS 3.4 | Server-rendered web UI |
| Monitoring | Prometheus + Grafana + NetData | Metrics and observability |
| Logging | structlog + Sentry | Structured logging and error tracking |
| Infra | Docker Compose + Nginx | Container orchestration and reverse proxy |

## Architecture Type

Multi-part monorepo: API Server (backend) + Worker (backend) + Frontend (web) + Infrastructure (infra).

## Repository Structure

- **4 parts** in a single repository
- **3 database tables** (users, download_jobs, failed_jobs, outbox)
- **7 route modules** with 30+ HTTP endpoints
- **10+ business services** with layered architecture
- **3 queue data structures** in Redis for job lifecycle management

## Detailed Documentation

- [Architecture — API Server](./architecture-api.md)
- [Architecture — Worker](./architecture-worker.md)
- [Architecture — Frontend](./architecture-frontend.md)
- [Architecture — Infrastructure](./architecture-infra.md)
- [API Contracts](./api-contracts-api.md)
- [Data Models](./data-models-api.md)
- [Source Tree Analysis](./source-tree-analysis.md)
- [Development Guide](./development-guide.md)
- [Integration Architecture](./integration-architecture.md)

## Existing Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) — Original system component diagram
- [API.md](./API.md) — Original endpoint reference
- [OPS.md](./OPS.md) — Environment variables and deployment
- [CONTRIBUTING.md](./CONTRIBUTING.md) — Contribution guidelines
- [AGENTS.md](../AGENTS.md) — Authoritative agent instructions
