# Production Deployment Guide

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+ (or docker-compose plugin)
- SSL certificates in `infra/ssl/` directory

## Quick Start

### 1. Prepare Environment

```bash
# Copy and edit environment file
cp .env.production .env

# Generate strong passwords and keys
# DB_PASSWORD=<SET_STRONG_PASSWORD_MIN_32_CHARS>
# REDIS_PASSWORD=<SET_STRONG_REDIS_PASSWORD>
# SECRET_KEY=<GENERATE_AND_SET_32_CHAR_SECRET>
# CORS_ORIGINS=https://your-domain.com
```

### 2. Prepare SSL Certificates

Place your SSL certificates in `infra/ssl/`:

- `fullchain.pem` - Full certificate chain
- `privkey.pem` - Private key

### 3. Update Nginx Configuration

Edit `infra/nginx/nginx.production.conf` and replace `your-domain.com` with your actual domain:

```nginx
server_name your-domain.com;  # Replace with your actual domain
```

### 4. Deploy

```bash
# Build and start all services
docker-compose -f docker-compose.yml -f docker-compose.production.yml up -d --build

# View logs
docker-compose -f docker-compose.yml -f docker-compose.production.yml logs -f

# Check service status
docker-compose -f docker-compose.yml -f docker-compose.production.yml ps
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
docker-compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

- `docker-compose.yml` - Base configuration with health checks
- `docker-compose.production.yml` - Production-specific overrides (TLS, ports)

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
docker-compose -f docker-compose.yml -f docker-compose.production.yml restart api
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
docker-compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

### Stopping

```bash
docker-compose -f docker-compose.yml -f docker-compose.production.yml down
```

To also remove volumes (WARNING: deletes all data):

```bash
docker-compose -f docker-compose.yml -f docker-compose.production.yml down -v
```

## Security Notes

- Change default passwords in `.env`
- Use HTTPS in production (redirect from HTTP is configured)
- Review CORS_ORIGINS for your domain
- Keep Docker and images updated
