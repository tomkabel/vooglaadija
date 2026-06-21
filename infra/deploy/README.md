# VPS Deployment Guide

**Target**: Ubuntu 25 VPS at `37.114.46.226` **Domain**: `youtube.tomabel.ee`

---

## Prerequisites

1. **DNS A record configured**:
  - Type: A
  - Name: `youtube`
  - Value: `37.114.46.226`
  - TTL: 300 (or lowest your provider allows)

1. **SSH access** to the VPS as a user with sudo privileges

1. **Project files** copied to the VPS (phase 3 handles this)

---

## Quick Start (Automated)

SSH to your VPS and run the deployment script:

```bash
# Full automated deployment (all 8 phases)
sudo bash deploy.sh all

# Or run phases individually for troubleshooting:
sudo bash deploy.sh 0   # Pre-flight checks (optional)
sudo bash deploy.sh 1   # System preparation
sudo bash deploy.sh 2   # DNS verification
sudo bash deploy.sh 3   # Directory setup (copies project files)
sudo bash deploy.sh 4   # SSL certificate acquisition
sudo bash deploy.sh 5   # Nginx configuration
sudo bash deploy.sh 6   # Environment setup
sudo bash deploy.sh 7   # Application deployment
sudo bash deploy.sh 8   # Verification
```

---

## Deployment Phases

### Phase 0: Pre-flight Checks

Verifies the deployment environment and required files.

### Phase 1: System Preparation

Installs Docker, Docker Compose, nginx, certbot, UFW firewall.

### Phase 2: DNS Verification

Confirms `youtube.tomabel.ee` resolves to `37.114.46.226`.

### Phase 3: Directory Setup

- Creates `/opt/vooglaadija/` directory structure
- Copies project files (excludes `.env`, `*.pem`, `storage/`)
- Sets up SSL directory at `infra/ssl/`

### Phase 4: SSL Certificate Acquisition

- Runs certbot to obtain Let's Encrypt certificate
- Stores certificates in `infra/ssl/`:
  - `fullchain.pem` (certificate + intermediates)
  - `privkey.pem` (private key)
  - Symlinks: `cert.pem` → `fullchain.pem`, `key.pem` → `privkey.pem`

### Phase 5: Nginx Configuration

- Deploys HTTPS configuration for `youtube.tomabel.ee`
- HTTP → HTTPS redirect on port 80
- TLS 1.2/1.3 with secure ciphers
- Security headers (HSTS, X-Frame-Options, etc.)
- Proxies to `http://127.0.0.1:8000` (API container)

### Phase 6: Environment Setup

Creates `.env` file with:

- Generated secure `SECRET_KEY` (32+ chars)
- Strong `DB_PASSWORD` and `REDIS_PASSWORD`
- `COOKIE_SECURE=True` (HTTPS required)
- `CORS_ORIGINS=https://youtube.tomabel.ee`

### Phase 7: Application Deployment

1. Builds Docker images
1. Starts `db` and `redis` containers
1. Waits for database health
1. Runs database migrations
1. Starts all services (`api`, `worker`, `nginx`, `certbot`)

### Phase 8: Verification

- Checks all containers are running
- Tests HTTP redirect (301)
- Tests HTTPS endpoint (200)
- Displays SSL certificate expiry

---

## Files Reference

| File                                | Purpose                                              |
| ----------------------------------- | ---------------------------------------------------- |
| `deploy.sh`                         | Automated deployment script (run on VPS)             |
| `docker-compose.yml`                | Base compose (8 services)                            |
| `docker-compose.production.yml`     | Production overrides (HTTPS, SSL, disabled services) |
| `infra/nginx/nginx.production.conf` | Nginx HTTPS configuration                            |
| `infra/ssl/`                        | SSL certificates directory                           |
| `infra/certbot/`                    | Certbot data and configuration                       |

### SSL Certificate Files

The `infra/ssl/` directory contains:

```text
infra/ssl/
├── fullchain.pem   # Certificate + intermediates (nginx reads this)
├── privkey.pem     # Private key (nginx reads this)
├── cert.pem        # Symlink → fullchain.pem
└── key.pem         # Symlink → privkey.pem
```

**Important**: nginx expects `fullchain.pem` and `privkey.pem`. The deploy script creates these from
Let's Encrypt's standard file names.

---

## Service Architecture

```text
                    ┌─────────────────────────────────────────┐
                    │         nginx (port 80/443)             │
                    │   HTTPS + HTTP redirect + security       │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │              api (port 8000)             │
                    │         FastAPI + HTMX frontend         │
                    └──────────────────┬──────────────────────┘
                                       │
               ┌───────────────────────┼───────────────────────┐
               │                       │                       │
    ┌──────────▼──────────┐  ┌────────▼─────────┐  ┌─────────▼────────┐
    │   worker            │  │      db         │  │     redis        │
    │   (yt-dlp jobs)     │  │   (PostgreSQL)   │  │   (job queue)    │
    └─────────────────────┘  └──────────────────┘  └──────────────────┘
```

---

## Manual Deployment (Without Script)

If you prefer manual control:

```bash
# 1. System setup
sudo apt update && sudo apt install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx

# 2. DNS verification
dig +short youtube.tomabel.ee  # Should return 37.114.46.226

# 3. Copy project files
rsync -av --exclude='.git' --exclude='.env' ./ ubuntu@37.114.46.226:/opt/vooglaadija/

# 4. SSL certificates (on VPS)
sudo certbot certonly --webroot -w /var/www/certbot -d youtube.tomabel.ee
# Copy to project:
sudo cp /etc/letsencrypt/live/youtube.tomabel.ee/fullchain.pem /opt/vooglaadija/infra/ssl/
sudo cp /etc/letsencrypt/live/youtube.tomabel.ee/privkey.pem /opt/vooglaadija/infra/ssl/

# 5. Create .env
cd /opt/vooglaadija
cat > .env << 'EOF'
DB_USER=postgres
DB_PASSWORD=<generate strong password>
DB_NAME=ytprocessor
REDIS_PASSWORD=<generate strong password>
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_hex(32))">
CORS_ORIGINS=https://youtube.tomabel.ee
COOKIE_SECURE=True
EOF

# 6. Deploy
cd /opt/vooglaadija
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d

# 7. Verify
curl -I https://youtube.tomabel.ee
```

---

## Verification Commands

```bash
# Check all containers
cd /opt/vooglaadija
docker compose -f docker-compose.yml -f docker-compose.production.yml ps

# View logs
docker compose -f docker-compose.yml -f docker-compose.production.yml logs -f

# Check SSL certificate
openssl x509 -in infra/ssl/fullchain.pem -noout -enddate

# Test API health
curl https://youtube.tomabel.ee/api/v1/health
```

---

## Rollback

```bash
cd /opt/vooglaadija
docker compose -f docker-compose.yml -f docker-compose.production.yml down

# To remove everything (DANGEROUS - loses data):
docker compose -f docker-compose.yml -f docker-compose.production.yml down -v
docker volume rm ytprocessor-postgres-data ytprocessor-redis-data ytprocessor-storage 2>/dev/null || true
```

---

## Troubleshooting

### DNS not resolving

```bash
# On VPS, check:
dig +short youtube.tomabel.ee
# Should return 37.114.46.226

# If wrong, wait for DNS propagation (can take up to 48 hours)
```

### SSL certificate issues

```bash
# Check certificate exists:
ls -la /opt/vooglaadija/infra/ssl/

# Verify certificate:
openssl s_client -connect youtube.tomabel.ee:443 -servername youtube.tomabel.ee

# Re-run certbot if needed:
sudo certbot certonly --webroot -w /var/www/certbot -d youtube.tomabel.ee --force-renewal
```

### Container won't start

```bash
# Check logs:
docker compose -f docker-compose.yml -f docker-compose.production.yml logs api

# Common issues:
# - .env missing: Phase 6 not run
# - SSL certs missing: Phase 4 not run
# - Port conflicts: nginx or apache already using 80/443
```

### Database connection issues

```bash
# Wait for db to be healthy:
docker compose -f docker-compose.yml -f docker-compose.production.yml exec db pg_isready -U postgres -d ytprocessor

# Check connection from api:
docker compose -f docker-compose.yml -f docker-compose.production.yml exec api python -c "import os; print(os.environ.get('DATABASE_URL'))"
```

---

## Production Checklist

- [ ] DNS A record points to VPS IP
- [ ] SSL certificates valid and not expired
- [ ] `.env` file exists with strong passwords
- [ ] All containers running (`docker compose ps`)
- [ ] HTTPS returns 200 (`curl -I https://youtube.tomabel.ee`)
- [ ] API health check passes
- [ ] Login/Register pages accessible
- [ ] Download job creation works
- [ ] File download works
