# Production Deployment Guide

Vooglaadija deploys to **any VPS** with a single plug-n-play bootstrap script. The script asks for
two things — your domain and a Cloudflare API token — then provisions everything: Docker, Coolify, a
Caddy reverse proxy with an auto-renewing **wildcard TLS certificate** (Let's Encrypt via the
Cloudflare DNS-01 challenge), the application stack, and continuous deployment from GitHub.

## How it works

```text
VPS (any provider)
├── Docker Engine + Compose plugin
├── Coolify (self-hosted PaaS, controls deployments, envs, rollbacks)
│   ├── Caddy proxy (port 80/443)
│   │   └── wildcard TLS *.your-domain.com  ← Cloudflare DNS-01, auto-renewed
│   └── vooglaadija application
│       └── docker-compose.yml (single source of truth)
│           ├── api      (ghcr.io/tomkabel/vooglaadija:latest)
│           ├── worker   (ghcr.io/tomkabel/vooglaadija:worker-latest)
│           ├── db       (PostgreSQL 15)
│           ├── redis    (Redis 7, AOF)
│           ├── storage-init + otel-collector
│           └── profiles: monitoring (Prometheus+Grafana), backup (pg_dump)
└── Continuous deployment: push to main → GitHub Actions builds images (GHCR)
    → Coolify redeploys automatically
```

The server **never builds images** — GitHub Actions builds multi-arch images with immutable SHA tags
in CI and pushes them to GHCR; Coolify only pulls and swaps containers.

## Prerequisites

- A VPS with **≥ 4 GB RAM** (2 GB minimum), Ubuntu/Debian or any systemd Linux distro
- A domain whose DNS is managed by **Cloudflare** (required for the DNS-01 challenge)
- A Cloudflare API token with **Zone → DNS → Edit** permission for that zone (create at
  <https://dash.cloudflare.com/profile/api-tokens>; the token only needs `Zone.Zone Read` +
  `Zone.DNS Edit`)

## Quick Start

```bash
# 1. Pull the repo onto the server (as root or with sudo)
git clone https://github.com/tomkabel/vooglaadija.git
cd vooglaadija

# 2. Run the bootstrap (interactive)
sudo ./deploy/bootstrap.sh
```

You will be asked for:

1. **Domain** — e.g. `app.example.com` (the script also provisions the `*.app.example.com` wildcard
   record and certificate)
2. **Cloudflare API token** — used to auto-create the DNS records and to issue/renew the wildcard
   certificate; stored only inside Coolify's encrypted config
3. (First run only) complete Coolify's one-time browser setup and optionally create a Coolify API
   token if the script cannot do it automatically

The script then:

1. Installs Docker Engine + Compose plugin
2. Installs Coolify
3. Switches Coolify's proxy to **Caddy** with the Cloudflare DNS-01 module (wildcard certs require
   DNS-01; Caddy issues and renews them automatically — no certbot, no cron)
4. Creates the `vooglaadija` application from the public GitHub repo (Docker Compose build pack,
   auto-deploy on push to `main`)
5. Generates and stores secrets (database password, Redis password, JWT signing key) plus the
   production CORS origin and secure-cookie settings
6. Assigns `https://<domain>` (+ wildcard) to the `api` service
7. Deploys and polls `https://<domain>/health` until healthy

Non-interactive (CI-friendly) mode — provide the values through environment variables (exported
before running):

```bash
export DEPLOY_DOMAIN=app.example.com
export CLOUDFLARE_EMAIL=ops@example.com
# export CLOUDFLARE_API_TOKEN=<your Cloudflare API token>
sudo ./deploy/bootstrap.sh --non-interactive
```

## Continuous deployment

| Step                 | Where                                           | What happens                                                                            |
| -------------------- | ----------------------------------------------- | --------------------------------------------------------------------------------------- |
| `git push` to `main` | GitHub                                          | CI runs lint, type checks, unit/integration tests, security scans                       |
| Image build          | GitHub Actions (`.github/workflows/docker.yml`) | Multi-arch `api` + `worker` images pushed to GHCR, tagged with SHA + version + `latest` |
| Redeploy             | Coolify webhook                                 | Pulls the new images (`up -d --pull always`), recreates `api`/`worker`, health-gated    |
| Rollback             | Coolify UI                                      | One click — re-deploy any previous deployment                                           |

Coolify also gives you: environment variable management (encrypted), deployment history and logs,
one-click rollback, and the ability to add staging/preview environments later.

## TLS / wildcard certificates

- Issued by Let's Encrypt via the **DNS-01 challenge** — no inbound port 80 requirement beyond
  normal operation, works with Cloudflare proxy (orange cloud) enabled.
- Certificate covers `your-domain.com` **and** `*.your-domain.com`; renewed automatically by Caddy
  (30 days before expiry).
- If a certificate is ever missing (e.g. after Coolify upgrades reset the proxy config), re-apply
  the Caddy DNS-01 proxy configuration from
  [Coolify's docs](https://coolify.io/docs/knowledge-base/proxy/caddy/dns-challenge) or re-run
  `./deploy/bootstrap.sh --force`.

## Operations

### View status / logs

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
docker logs ytprocessor-api
```

Or use the Coolify UI (deployment logs, container logs, metrics).

### Update secrets or environment

Coolify UI → vooglaadija → Environment Variables. Restart the application afterward.

### Enable optional profiles

The compose file ships with optional profiles. Edit the application's _Docker Compose start command_
in Coolify (Advanced) to include a profile, e.g.:

```text
--profile monitoring up -d --pull always --remove-orphans
```

- `monitoring` — Prometheus (loopback :9090) + Grafana (loopback :3000, preloaded dashboards)
- `backup` — daily PostgreSQL dumps (`pg_dump -Fc`) with 7-day retention into
  `infra/backup/backup-data/`

### Database access

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml exec db psql -U postgres -d ytprocessor
```

### Migrations

Migrations run automatically on container startup (`entrypoint.sh`). To run manually:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml exec api python -m alembic upgrade head
```

### Backups

Enable the `backup` profile (see above). Dumps land in `infra/backup/backup-data/` and are retained
7 days (`BACKUP_RETENTION_DAYS`). Copy them off-server for real safety.

### Stop / restart

```bash
# Restart a service
docker compose -f docker-compose.yml -f docker-compose.local.yml restart api   # local stack
# Coolify-managed: restart via the Coolify UI
```

## Troubleshooting

| Symptom                                         | Fix                                                                                                                                                              |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `https://<domain>` returns 502/504              | Check the API container in Coolify logs; verify the domain is assigned to the `api` service                                                                      |
| Certificate not issued                          | Check `docker logs coolify-proxy`; verify the Cloudflare token has `Zone.DNS:Edit`; wait for DNS propagation                                                     |
| Wildcard subdomains don't resolve               | Create `*.domain` A record (bootstrap does this automatically when the token permits)                                                                            |
| Container stuck restarting                      | `docker compose -f docker-compose.yml -f docker-compose.local.yml logs api`; common cause: missing env vars (Coolify UI highlights required ones)                   |
| Deploy doesn't pick up new image                | The start command must include `--pull always` (bootstrap sets this); otherwise update it in Coolify → Advanced                                                  |
| Coolify proxy reverted to Traefik after upgrade | Re-run `./deploy/bootstrap.sh --force` or re-apply the Caddy DNS-01 config from [Coolify docs](https://coolify.io/docs/knowledge-base/proxy/caddy/dns-challenge) |

## Migrating an existing deployment

The previous SSH-based deployment flow (nginx + certbot on a fixed IP) is **not** auto-migrated. To
move an existing instance:

1. Back up the database from the old server (`pg_dump` or the old `backup` profile).
2. Run `./deploy/bootstrap.sh` on the new server with the same domain.
3. Restore inside the database container (use the `db` compose service; in a
   Coolify-managed stack, run the command inside the Coolify project directory
   or via the Coolify terminal):
   - plain SQL dump: `docker compose -f docker-compose.yml -f docker-compose.local.yml exec -i db psql -U postgres -d ytprocessor < dump.sql`
   - custom-format archive:
     `docker compose -f docker-compose.yml -f docker-compose.local.yml exec -i db pg_restore -U postgres -d ytprocessor --clean --if-exists < dump.dump`
4. Point the domain's DNS at the new server (bootstrap can do this if the token allows).

## Security notes

- Firewall: only ports 22, 80, 443 need to be open
  (`ufw allow ssh && ufw allow 80/tcp && ufw allow 443/tcp`). The Coolify dashboard listens on
  `:8000` with a default admin password — do **not** leave it exposed to the internet:
  - restrict it to your IP: `ufw allow from <your-ip> to any port 8000`, or
  - use an SSH tunnel instead: `ssh -L 8000:127.0.0.1:8000 user@server`, then open
    <http://127.0.0.1:8000> locally and keep port 8000 closed in the firewall.
- Containers run as non-root with dropped capabilities, read-only rootfs and `no-new-privileges`
  (see `x-base-service` in `docker-compose.yml`).
- Secrets live only in Coolify's encrypted store — never in the repo.
- Keep the VPS patched and Coolify updated (Coolify has a self-update scheduler).
