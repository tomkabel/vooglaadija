# SSL Certificates for Vooglaadija

## Overview

This directory contains SSL certificates for HTTPS serving via nginx.

**Critical**: nginx configuration expects specific file names. Do NOT rename files arbitrarily.

---

## Expected Files

nginx reads these files directly:

| File            | Purpose                               | Permissions      |
| --------------- | ------------------------------------- | ---------------- |
| `fullchain.pem` | Server certificate + intermediate CAs | 644 (readable)   |
| `privkey.pem`   | Private key                           | 600 (owner only) |

For compatibility with various tools, symlinks are provided:

| Symlink    | Target          | Purpose            |
| ---------- | --------------- | ------------------ |
| `cert.pem` | `fullchain.pem` | Alternative naming |
| `key.pem`  | `privkey.pem`   | Alternative naming |

---

## Obtaining Certificates

### Option 1: Let's Encrypt (Recommended)

On the VPS, run:

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d example.com

# Copy to this directory:
sudo cp /etc/letsencrypt/live/example.com/fullchain.pem ./
sudo cp /etc/letsencrypt/live/example.com/privkey.pem ./

# Create compatibility symlinks:
ln -sf fullchain.pem cert.pem
ln -sf privkey.pem key.pem

# Set permissions:
sudo chmod 644 fullchain.pem cert.pem
sudo chmod 600 privkey.pem key.pem
```

### Option 2: Existing Certificates

If you have certificates from another source:

1. Copy `fullchain.pem` (or `cert.pem`) to this directory
1. Copy `privkey.pem` (or `key.pem`) to this directory
1. Ensure proper permissions

### Option 3: Pre-provision Locally

Copy certificates from a local machine to the VPS:

```bash
# Local machine
scp user@vps:/etc/letsencrypt/live/example.com/*.pem ./infra/ssl/

# Or use the deploy.sh script (Phase 4) which handles this automatically
```

---

## Let's Encrypt File Structure

Let's Encrypt creates these files in `/etc/letsencrypt/live/example.com/`:

```text
/etc/letsencrypt/live/example.com/
├── fullchain.pem   ← Use this as fullchain.pem
├── privkey.pem     ← Use this as privkey.pem
├── chain.pem      (intermediate CA)
└── cert.pem       (server certificate only - NOT the same as fullchain)
```

**Important**: `cert.pem` contains ONLY the server certificate, NOT the full chain. nginx needs
`fullchain.pem` (server + intermediates) for proper TLS.

---

## Security Warnings

### NEVER commit private keys

The `.gitignore` file excludes all `*.pem` and `*.key` files:

```gitignore
# SSL Certificates - Never commit private keys
*.pem
privkey.pem
key.pem
```

### File Permissions

Always set restrictive permissions:

```bash
chmod 644 fullchain.pem cert.pem   # Readable by nginx
chmod 600 privkey.pem key.pem       # Owner read/write only
```

### Production Best Practices

1. **Use Docker Secrets** or Kubernetes Secrets for certificate storage in production
1. **Mount certificates as read-only volumes** (`:ro`)
1. **Rotate certificates regularly** - Let's Encrypt certs expire every 90 days
1. **Enable auto-renewal** - The `certbot` container handles this
1. **Backup certificates** - Store a copy in a secure location

---

## Docker Integration

The `docker-compose.production.yml` mounts this directory:

```yaml
nginx:
  volumes:
    - ./infra/ssl:/etc/nginx/ssl:ro
```

nginx reads `fullchain.pem` and `privkey.pem` from this mount point.

---

## Verification

Check certificate is valid:

```bash
# View certificate details
openssl x509 -in fullchain.pem -noout -subject -issuer -dates

# Test nginx config
nginx -t

# Verify HTTPS is working
curl -I https://example.com
```

---

## Troubleshooting

### "SSL certificate not found" errors

1. Verify files exist: `ls -la infra/ssl/`
1. Check permissions: `ls -la infra/ssl/*.pem`
1. Ensure symlinks are valid: `ls -la infra/ssl/ | grep pem`

### Certificate expired

Re-run certbot to renew:

```bash
sudo certbot renew
# Or use deploy.sh phase 4
```

### Wrong certificate being served

Check that nginx is using the correct config:

```bash
docker compose logs nginx
openssl s_client -connect example.com:443 -servername example.com
```
