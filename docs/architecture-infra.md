# Architecture — Infrastructure

**Part:** Infrastructure (`infra/`)
**Type:** Docker Compose + monitoring stack

---

## Technology Stack

| Category | Technology | Notes |
|----------|------------|-------|
| Container | Docker + BuildKit | Multi-arch (amd64 + arm64) |
| Orchestration | Docker Compose | 5 compose files for different environments |
| Registry | GitHub Container Registry (ghcr.io) | |
| Reverse Proxy | Nginx | SSL termination, static file serving |
| Monitoring | Prometheus + Grafana | Metrics collection and visualization |
| Monitoring | NetData | Real-time system monitoring |
| Logging | OpenTelemetry Collector | OTLP export |
| SSL | Let's Encrypt + Certbot | Auto-renewal |

## Compose Files

| File | Environment | Services |
|------|-------------|----------|
| `docker-compose.local.yml` | Local development | api + db + redis + worker |
| `docker-compose.demo.yml` | Demo/staging | Full stack + nginx + netdata |
| `docker-compose.production.yml` | Production | Full stack + monitoring |
| `docker-compose.monitoring.yml` | Monitoring add-on | Prometheus + Grafana + NetData |
| `docker-compose.test.yml` | CI testing | api + db + redis + worker |

## Dockerfile

Multi-stage build:
1. **Builder stage** — Install Python deps, build Tailwind CSS
2. **Runtime stage** — Non-root user (1000:1000), `cap_drop: [ALL]`, `read_only: true`, `no-new-privileges=true`

Image tags: commit SHA + version tag.

## Security

- Non-root container user
- Dropped Linux capabilities
- Read-only root filesystem
- No new privileges
- Health checks on all services
- Resource limits configured
