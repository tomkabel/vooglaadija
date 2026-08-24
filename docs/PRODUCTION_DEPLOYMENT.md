# Production Deployment Guide

Vooglaadija deploys to **any VPS** with a single plug-n-play bootstrap script. The script asks for
your domain (and optionally a Cloudflare API token) and then provisions everything: Docker, a
standalone **Caddy** reverse proxy with a TLS origin certificate, and the full application stack —
**no Coolify, no PaaS**.

## How it works

```text
VPS (any provider)
├── Docker Engine + Compose plugin
├── caddy service (ports 80/443) — the ONLY public entry point
│   └── TLS origin cert for your domain (self-signed, presented to Cloudflare)
│       └── reverse_proxy api:8000
└── vooglaadija stack (docker-compose.yml + overrides)
    ├── api      (built locally from Dockerfile, target api)
    ├── worker   (built locally from Dockerfile, target worker)
    ├── browser-downloader
    ├── db       (PostgreSQL 15)
    ├── redis    (Redis 7, AOF)
    ├── storage-init + otel-collector
    └── profiles: monitoring (Prometheus+Grafana), backup (pg_dump)

Cloudflare (orange-cloud proxy) → Caddy :80/:443 → api:8000
```

Caddy is the **only** public entry point. It terminates TLS and forwards to the API at `api:8000`
over the default compose network. If Caddy is down, Cloudflare returns **HTTP 521**.

## Prerequisites

- A VPS with **≥ 4 GB RAM** (2 GB minimum), Ubuntu/Debian or any systemd Linux distro
- A domain you control, ideally DNS-managed by **Cloudflare** (the script can auto-create the
  A records with a token; otherwise set them manually)
- Optional: a Cloudflare API token with **Zone → DNS → Edit** permission for that zone
  (create at <https://dash.cloudflare.com/profile/api-tokens>; only needs `Zone.Zone Read` +
  `Zone.DNS Edit`) — only required for automatic DNS provisioning, not for the app itself

## Quick Start

```bash
# 1. Pull the repo onto the server (as root or with sudo)
git clone https://github.com/tomkabel/vooglaadija.git
cd vooglaadija

# 2. Run the bootstrap (interactive)
sudo ./deploy/bootstrap.sh
```

You will be asked for:

1. **Domain** — e.g. `app.example.com` (the script also provisions the `*.app.example.com`
   wildcard A record if you provide a Cloudflare token)
2. **Cloudflare API token** (optional) — used to auto-create the DNS records; leave empty to
   manage DNS manually

> **DNS precondition (no token / `SKIP_DNS=1`):** the A record for the domain must already
> exist and resolve to this server before the bootstrap's public `https://<domain>/health`
> check can pass. For that check to verify cleanly, the record should be **proxied**
> (orange cloud, so Cloudflare's public edge certificate is presented) — a grey-clouded
> record pointing straight at the server will expose the self-signed origin certificate,
> and the TLS verification in the final health check will reject it.

The script then:

1. Installs Docker Engine + Compose plugin
2. Verifies/creates the Cloudflare DNS records (skipped without a token)
3. Generates `./.env` with random secrets (DB password, Redis password, JWT key, Grafana admin) —
   existing secrets are **preserved** on re-runs so the initialized PostgreSQL/Redis volumes
   stay in sync
4. Generates the `Caddyfile` and a self-signed TLS origin certificate for the domain
5. Brings up the stack with the standalone Caddy reverse proxy
   (`docker-compose.yml` + `docker-compose.local.yml` + `docker-compose.caddy.yml`)
6. Polls `https://<domain>/health` until healthy (also verifies the local `:443` path first)

Non-interactive (CI-friendly) mode — provide the values through environment variables (exported
before running):

```bash
export DEPLOY_DOMAIN=app.example.com
export CLOUDFLARE_API_TOKEN=<your Cloudflare API token>   # optional
sudo ./deploy/bootstrap.sh --non-interactive
```

## TLS / certificates

- Caddy serves a **self-signed origin certificate** for `your-domain.com` so the
  Cloudflare → origin leg is encrypted. Set Cloudflare SSL/TLS mode to **Full** (the self-signed
  cert is enough; use "Full (strict)" only with a Cloudflare Origin CA cert).
- Plain HTTP `:80` **redirects to HTTPS** (Caddy `redir`), so credentials are never accepted
  over cleartext. Cloudflare "Flexible" mode is **not** supported — use "Full".
- If you later grey-cloud the DNS (direct traffic, no Cloudflare), change the `tls` line in the
  `Caddyfile` to `tls internal` and Caddy will auto-issue a real Let's Encrypt certificate.

## Operations

### View status / logs

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml ps
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml logs -f api
```

### Update secrets or environment

Edit `./.env` (mode 600) and restart the affected services:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml up -d
```

### Enable optional profiles

The compose file ships with optional profiles; pass `--profile` to the compose command:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml --profile monitoring up -d
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
docker compose -f docker-compose.yml -f docker-compose.local.yml restart api
# Bring the whole stack up cleanly (safe after a host reboot)
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml up -d
```

## Update the app after a new build

```bash
cd /root/vooglaadija          # or wherever the checkout lives
git pull
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml up -d --build
```

Images are built locally from the checkout (no GHCR pull required).

## Troubleshooting

| Symptom                                         | Fix                                                                                                                                                              |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `https://<domain>` returns 502/504              | Check the API container: `docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml logs api`                                  |
| Cloudflare returns **HTTP 521** (web server down) | The Caddy container isn't running or 80/443 aren't listening on this origin. Check `docker ps --filter name=caddy`; if missing, start with the standalone command above (`-f docker-compose.caddy.yml`). Verify `ss -tlnp \| grep -E ':80\|:443'`. |
| Certificate not accepted by Cloudflare          | Set Cloudflare SSL/TLS mode to **Full** (self-signed origin cert). For "Full (strict)", use a Cloudflare Origin CA cert instead.                                  |
| Wildcard subdomains don't resolve               | DNS only: create `*.domain` A record pointing at this server (bootstrap does this automatically when the token permits). The generated Caddyfile routes only the exact domain; wildcard hosts that *do* resolve are not proxied by Caddy. |
| Container stuck restarting                      | `docker compose -f docker-compose.yml -f docker-compose.local.yml logs api`; common cause: missing env vars in `.env` (DB_PASSWORD, REDIS_PASSWORD, SECRET_KEY)  |
| Deploy doesn't pick up new code                 | Rebuild: `docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml up -d --build`                                            |

## Migrating an existing deployment

The previous SSH-based deployment flow (nginx + certbot on a fixed IP) is **not** auto-migrated. To
move an existing instance:

1. Back up the database from the old server (`pg_dump` or the old `backup` profile).
2. Run `./deploy/bootstrap.sh` on the new server with the same domain.
3. Restore inside the database container:
   - plain SQL dump:
     `docker compose -f docker-compose.yml -f docker-compose.local.yml exec -i db psql -U postgres -d ytprocessor < dump.sql`
   - custom-format archive:
     `docker compose -f docker-compose.yml -f docker-compose.local.yml exec -i db pg_restore -U postgres -d ytprocessor --clean --if-exists < dump.dump`
4. Point the domain's DNS at the new server (bootstrap can do this if the token allows).

## Security notes

- Firewall: only ports 22, 80, 443 need to be open
  (`ufw allow ssh && ufw allow 80/tcp && ufw allow 443/tcp`). Everything else is internal to the
  compose network; debug ports from `docker-compose.local.yml` are bound to loopback only.
- Containers run as non-root with dropped capabilities, read-only rootfs and `no-new-privileges`
  (see `x-base-service` in `docker-compose.yml`).
- Secrets live only in `./.env` (mode 600) — never commit it to version control.
- Keep the VPS patched.
