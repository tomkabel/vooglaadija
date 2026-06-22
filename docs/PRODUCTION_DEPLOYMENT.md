# Production Deployment Guide

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2 plugin (`docker compose`)
- SSL certificates in `infra/ssl/` directory

## Quick Start

### 1. Prepare Environment

```bash
# Copy and edit environment file
cp .env.example .env

# Generate strong passwords and keys
# DEPLOY_DOMAIN=your-domain.com
# DB_PASSWORD=<SET_STRONG_PASSWORD_MIN_32_CHARS>
# REDIS_PASSWORD=<SET_STRONG_REDIS_PASSWORD>
# SECRET_KEY=<GENERATE_AND_SET_32_CHAR_SECRET>
# CORS_ORIGINS=https://your-domain.com
# FEATURE_METRICS_ENABLED=true
# FEATURE_TRACING_ENABLED=true
```

### 2. Prepare SSL Certificates

Place your SSL certificates in `infra/ssl/`:

- `fullchain.pem` - Full certificate chain
- `privkey.pem` - Private key

### 3. Confirm Nginx Domain Configuration

`infra/nginx/nginx.production.conf` is a Compose template. The production override mounts it as
`default.conf.template` and substitutes `${DEPLOY_DOMAIN}` at container startup. Do not hardcode the
domain in the nginx file; set `DEPLOY_DOMAIN` in `.env`.

### 4. Deploy

```bash
# Build and start all services
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build

# View logs
docker compose -f docker-compose.yml -f docker-compose.production.yml logs -f

# Check service status
docker compose -f docker-compose.yml -f docker-compose.production.yml ps
```

## Service Architecture

```text
Internet → Nginx (443) → API (8000) → PostgreSQL (5432)
                              ↓
                           Redis (6379)
```

### Key Services

| Service | Port    | Description              |
| ------- | ------- | ------------------------ |
| nginx   | 80, 443 | Reverse proxy with TLS   |
| api     | 8000    | FastAPI application      |
| worker  | -       | Background job processor |
| db      | 5432    | PostgreSQL database      |
| redis   | 6379    | Redis for queue/caching  |

## Important Notes

### Docker Compose File Override

**CRITICAL**: Production deployment requires BOTH compose files:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

- `docker-compose.yml` - Base configuration with health checks
- `docker-compose.production.yml` - Production-specific overrides (TLS, ports, production CORS,
  metrics/tracing defaults, and worker DB pool sizing)

Using only `docker-compose.production.yml` will result in 502 errors because the API health check
dependency is not included.

### Health Checks

The API service has a TCP-based health check on port 8000. Nginx will not start routing traffic
until the API is healthy.

### Troubleshooting

#### Check container health

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

#### View API logs

```bash
docker logs ytprocessor-api
```

#### Test API connectivity from nginx

```bash
docker exec ytprocessor-nginx wget -qO- http://api:8000/api/v1/health
```

#### View nginx error logs

```bash
docker exec ytprocessor-nginx cat /var/log/nginx/error.log
```

#### Restart a specific service

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml restart api
```

### Database Migrations

Migrations run automatically on container startup via `entrypoint.sh`. To run manually:

```bash
docker exec ytprocessor-api python -m alembic upgrade head
```

### Updating

```bash
# Pull latest code
git pull origin main

# Rebuild and restart
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

### Stopping

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml down
```

To also remove volumes (WARNING: deletes all data):

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml down -v
```

## Security Notes

- Change default passwords in `.env`
- Use HTTPS in production (redirect from HTTP is configured)
- Review CORS_ORIGINS for your domain
- Keep `FEATURE_METRICS_ENABLED=true` and `FEATURE_TRACING_ENABLED=true` unless deliberately
  disabling observability for a controlled test.
- Tune worker DB pool sizing with `WORKER_DB_POOL_SIZE` and `WORKER_DB_MAX_OVERFLOW` instead of
  changing application code.
- Keep Docker and images updated
