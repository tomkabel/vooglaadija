<div align="center">

# Vooglaadija

_Pronounced voo-gla-tee-ya_ — Media Link Processor

Async video media extraction API with job queue and real-time status streaming.

[![Live Demo](https://img.shields.io/badge/Live%20Demo-youtube.tomabel.ee-FF0000?style=for-the-badge&logo=youtube&logoColor=white)](https://youtube.tomabel.ee)

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-GPLv3-A41E35?style=for-the-badge&logo=gnu&logoColor=white)](https://www.gnu.org/licenses/gpl-3.0.html)
[![Version](https://img.shields.io/badge/Version-1.0.0-22D3EE?style=for-the-badge)](https://github.com/tomkabel/vooglaadija)
[![FastAPI](https://img.shields.io/badge/FastAPI-26A69A?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io/)
[![yt--dlp](https://img.shields.io/badge/yt--dlp-F16729?style=for-the-badge&logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)

</div>

<div align="center">
  <img src="docs/images/vooglaadija_fin.png" alt="Vooglaadija" width="600" />
</div>

<div align="center">

**Built by**
[![GitHub](https://img.shields.io/badge/@tomkabel-181717?style=flat&logo=github)](https://github.com/tomkabel)
[![GitHub](https://img.shields.io/badge/@Kevindaman-181717?style=flat&logo=github)](https://github.com/Kevindaman)
[![GitHub](https://img.shields.io/badge/@triinum-181717?style=flat&logo=github)](https://github.com/triinum)

**Acknowledgements**
[![GitHub](https://img.shields.io/badge/@Migfive-181717?style=flat&logo=github)](https://github.com/Migfive)
[![GitHub](https://img.shields.io/badge/@DrWarpMan-181717?style=flat&logo=github)](https://github.com/DrWarpMan)
[![GitHub](https://img.shields.io/badge/@Snazzah-181717?style=flat&logo=github)](https://github.com/Snazzah)
[![GitHub](https://img.shields.io/badge/@wukko-181717?style=flat&logo=github)](https://github.com/wukko)
[![GitHub](https://img.shields.io/badge/@Blobadoodle-181717?style=flat&logo=github)](https://github.com/Blobadoodle)
[![GitHub](https://img.shields.io/badge/@nexpid-181717?style=flat&logo=github)](https://github.com/nexpid)

</div>

---

## Contents

- [Overview](#overview)
- [Features](#features)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Documentation](#documentation)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [License](#license)

---

## Overview

Vooglaadija is an async REST API for extracting media from video URLs. It uses
[yt-dlp](https://github.com/yt-dlp/yt-dlp) as the extraction engine and currently accepts YouTube
URLs. The architecture separates the FastAPI web layer from a Redis-backed worker process.

The system includes JWT authentication, CSRF protection, rate limiting, structured logging,
Prometheus metrics, OpenTelemetry tracing, and Sentry error tracking. A server-rendered web UI built
with HTMX and Tailwind CSS provides job management with real-time status updates via Server-Sent
Events.

### Legitimate Use Cases

Vooglaadija is a tool. Examples of legitimate uses include:

- Downloading your own YouTube content
- Archival or backup of content you have created or have explicit permission to download
- Downloading Creative Commons or public domain content
- Offline access to content you are authorized to view

### Legal Disclaimer

**You are responsible for ensuring you have the right to download any content before using
Vooglaadija.** Downloading copyrighted content without authorization may violate YouTube's Terms of
Service and applicable copyright law, including the DMCA §1201 anti-circumvention provisions in the
US.

Vooglaadija is open-source software provided under GPLv3. The operators of this service do not
endorse or encourage copyright infringement. This tool has substantial non-infringing uses and is
designed for lawful purposes only. Users should consult qualified legal counsel if unsure about the
legality of their use case.

See the [Terms of Service](/web/terms) for full details.

---

## Features

### Core Processing

- Media extraction via yt-dlp (YouTube-optimized)
- Async job queue with Redis-backed worker
- Job lifecycle: pending → processing → completed/failed
- Automatic retry with exponential backoff and jitter
- Time-limited download links (default 24h)
- Stale job reaper for orphaned processing jobs

### Security & Reliability

- JWT access/refresh tokens with bcrypt hashing
- CSRF token protection
- Per-IP and per-user rate limiting
- Content-Security-Policy headers
- Safe file serving with path traversal protection
- Circuit breaker for yt-dlp extraction failures
- Transactional outbox for crash-safe job creation
- Graceful worker shutdown with job draining

### Observability

- SSE real-time status streaming
- Prometheus metrics endpoint
- Structured JSON logging (structlog)
- OpenTelemetry tracing support
- Sentry error tracking

---

## Quick Start

### Production (any VPS — plug-n-play)

Pull the repo onto any VPS and run the bootstrap. It asks for your domain and a Cloudflare API
token, then provisions Docker + Coolify, auto-issues a wildcard TLS certificate (Let's Encrypt via
Cloudflare DNS-01, auto-renewed), deploys the production Docker Compose stack and wires up
continuous deployment from GitHub:

```bash
git clone https://github.com/tomkabel/vooglaadija.git
cd vooglaadija
sudo ./deploy/bootstrap.sh
```

See [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) for details.

### Local Development

```bash
git clone https://github.com/tomkabel/vooglaadija.git
cd vooglaadija

cp .env.example .env
# Minimum required:
#   DB_PASSWORD=<strong-password>
#   REDIS_PASSWORD=<strong-password>
#   SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Or without Docker:

```bash
hatch env create
hatch run db-migrate
hatch run dev              # API
python -m worker.main      # Worker (separate terminal)
```

### Access Points (local)

- Web Dashboard: <http://localhost:8000/web/downloads>
- Login: <http://localhost:8000/web/login>
- API Docs: <http://localhost:8000/docs>
- Grafana: <http://localhost:3000> (enable with `docker compose ... --profile monitoring up -d`)

---

## Usage

### Register

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "securepassword123"}'
```

### Login

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "password": "securepassword123"}'
```

### Create a download job

```bash
curl -X POST http://localhost:8000/api/v1/downloads \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"}'
```

**Note:** The current deployment accepts YouTube URLs via yt-dlp.

### Error example (422 Validation Error)

```bash
curl -X POST http://localhost:8000/api/v1/downloads \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "not-a-url"}'
```

Expected response:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed"
  },
  "details": {
    "validation_errors": [
      {
        "field": "url",
        "message": "Value error, Must be a valid YouTube URL",
        "type": "value_error"
      }
    ]
  }
}
```

See [docs/API.md](docs/API.md) for the full endpoint reference, request/response schemas, and status
codes.

---

## Documentation

| Document                                                       | Description                                                          |
| -------------------------------------------------------------- | -------------------------------------------------------------------- |
| [docs/API.md](docs/API.md)                                     | Full API reference with auth requirements, status codes, and schemas |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)                   | System architecture and component responsibilities                   |
| [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md) | Plug-n-play VPS deployment with wildcard TLS + Coolify CD            |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)                   | Development workflow, tests, and code standards                      |
| [docs/OPS.md](docs/OPS.md)                                     | Environment variables, deployment, and troubleshooting               |

---

## Tech Stack

| Technology     | Purpose                                    |
| -------------- | ------------------------------------------ |
| Python 3.12+   | Runtime                                    |
| FastAPI        | API framework                              |
| SQLAlchemy     | ORM                                        |
| PostgreSQL     | Database                                   |
| Redis          | Queue and cache                            |
| Docker         | Containerization                           |
| Caddy          | Reverse proxy + wildcard TLS (via Coolify) |
| Tailwind CSS   | Frontend styling                           |
| Prometheus     | Metrics                                    |
| sse-starlette  | Real-time updates                          |
| GitHub Actions | CI/CD                                      |

### Runtime Dependencies

- `python-jose[cryptography]` — JWT handling
- `passlib[bcrypt]` — Password hashing
- `yt-dlp` — Media extraction
- `sse-starlette` — Server-Sent Events
- `prometheus-client` — Metrics
- `slowapi` — Rate limiting
- `structlog` — Structured logging
- `orjson` — Fast JSON serialization
- `uvloop` — Async event loop
- `tenacity` — Retry logic
- `sentry-sdk` — Error tracking

### System Dependencies

- `Node.js` — yt-dlp JavaScript signature solving
- `ffmpeg` — Media merging and transcoding

---

## Architecture

```mermaid
flowchart TD
    Client([Client]) -->|HTTP/S| proxy[Caddy proxy<br/>(Coolify, wildcard TLS)]
    proxy -->|Proxy| api[FastAPI API]
    api -->|SQL| db[(PostgreSQL)]
    api -->|Queue| redis[(Redis)]
    redis -->|Consume| worker[Worker<br/>yt-dlp]
    worker -->|Files| storage[(Storage)]
    worker -->|Update| db
    api -.->|Metrics/Traces| otel[OpenTelemetry Collector]
    api -.->|Errors| sentry[Sentry]
```

The API server handles authentication, job management, HTMX rendering, SSE streaming, and
observability. The worker consumes jobs from Redis, extracts media via yt-dlp, and manages file
lifecycle. Production deployments run on any VPS via Coolify (see
[docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md)); its Caddy proxy terminates TLS with
an auto-renewed wildcard certificate. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full
diagram and component details.

---

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).

---

<div align="center">

[![GitHub](https://img.shields.io/badge/View_on-GitHub-181717?style=for-the-badge&logo=github)](https://github.com/tomkabel/vooglaadija)
[![Issues](https://img.shields.io/badge/Report_Issue-EE3B3B?style=for-the-badge&logo=github)](https://github.com/tomkabel/vooglaadija/issues)

</div>
