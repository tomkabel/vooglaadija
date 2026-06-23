# NetData Integration & Observability Enhancement Plan

**Project:** Vooglaadija (YouTube Link Processor)  
**Author:** Implementation Team  
**Date:** 2026-04-15  
**Status:** ✅ IMPLEMENTED

---

## Executive Summary

This plan outlines the integration of **NetData** for real-time observability alongside existing
Prometheus metrics, plus implementation of recommended Python library enhancements to improve
logging, serialization, async performance, and database operations.

**Current State:**

- Prometheus metrics already configured (`app/metrics.py`)
- OpenTelemetry collector deployed but not actively used by application
- Standard library `logging` with JSON formatter
- `psycopg2-binary` for PostgreSQL
- Custom Redis queue implementation
- No distributed tracing

**Recommended Approach:**

1. **NetData Cloud** for real-time system/container monitoring (complements Prometheus)
1. **Keep Prometheus** for application-level metrics (existing implementation)
1. Implement enhancements in priority order

---

## Part 1: NetData Integration

### 1.1 Architecture Decision

```text
┌─────────────────────────────────────────────────────────────────┐
│                      Vooglaadija Stack                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐    │
│   │   NetData   │    │  Prometheus │    │  OpenTelemetry  │    │
│   │   Cloud     │    │  (existing) │    │   Collector     │    │
│   │   (NEW)     │    │             │    │   (idle)        │    │
│   └──────┬──────┘    └──────┬──────┘    └────────┬────────┘    │
│          │                   │                    │             │
│   Real-time system   Application metrics    Traces (future)     │
│   Container metrics  Job metrics           Logs                │
│   Infra health       HTTP metrics                                │
│                                                                 │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │                    Docker Hosts                          │  │
│   │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐     │  │
│   │  │   API   │  │ Worker  │  │   DB    │  │  Redis  │     │  │
│   │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘     │  │
│   │       │            │            │            │           │  │
│   │       └────────────┴────────────┴────────────┘           │  │
│   │              NetData Agents (per-container)              │  │
│   └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 NetData Deployment Options

| text Option               | Description                        | Best For                            |
| ------------------------- | ---------------------------------- | ----------------------------------- |
| **NetData Cloud (SaaS)**  | Free tier, 1-day retention         | Development, small deployments      |
| **NetData Cloud On-Prem** | Self-hosted, full data sovereignty | Production, compliance requirements |
| **Standalone Parents**    | Self-hosted central NetData        | Single server, no cloud             |

**Recommendation:** Start with **NetData Cloud (SaaS)** for easiest setup, migrate to On-Prem if
compliance requires.

---

## Part 2: Implementation Steps

### Phase 1: NetData Agent Installation (Week 1)

#### Step 1.1: Add NetData to Docker Compose

**File:** `docker-compose.monitoring.yml` (new file)

```yaml
# NetData monitoring configuration
# Usage: docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d

x-netdata-config: &netdata-config
  image: netdata/netdata:stable
  hostname: "{{.Service.Name}}"
  restart: unless-stopped
  cap_add:
    - SYS_PTRACE
    - SYS_ADMIN
  security_opt:
    - apparmor:unconfined
  environment:
    NETDATA_CLAIM_TOKEN: "${NETDATA_CLAIM_TOKEN:-}"
    NETDATA_CLAIM_URL: "${NETDATA_CLAIM_URL:-https://app.netdata.cloud}"
    NETDATA_CLAIM_ROOMS: "${NETDATA_CLAIM_ROOMS:-}"
  volumes:
    - netdata-config:/etc/netdata
    - netdata-lib:/var/lib/netdata
    - netdata-cache:/var/cache/netdata
    - /:/host/root:ro,rslave
    - /etc/passwd:/host/etc/passwd:ro
    - /etc/group:/host/etc/group:ro
    - /etc/localtime:/host/etc/localtime:ro
    - /proc:/host/proc:ro
    - /sys:/host/sys:ro
    - /var/log:/host/var/log:ro
    - /var/run/docker.sock:/var/run/docker.sock:ro
  volumes_from:
    - api
    - worker
  logging:
    driver: "json-file"
    options:
      max-size: "10m"
      max-file: "3"
  networks:
    - ytprocessor-network

services:
  # NetData agent for API container monitoring
  netdata-api:
    <<: *netdata-config
    container_name: ytprocessor-netdata-api
    environment:
      <<: *netdata-config
      NETDATA_CLAIM_TOKEN: "${NETDATA_CLAIM_TOKEN:-}"
    hostname: vooglaadija-api

  # NetData agent for Worker container monitoring
  netdata-worker:
    <<: *netdata-config
    container_name: ytprocessor-netdata-worker
    environment:
      <<: *netdata-config
      NETDATA_CLAIM_TOKEN: "${NETDATA_CLAIM_TOKEN:-}"
    hostname: vooglaadija-worker

  # NetData parent for standalone (no cloud)
  netdata-parent:
    <<: *netdata-config
    container_name: ytprocessor-netdata-parent
    ports:
      - "19999:19999"
    hostname: vooglaadija-parent
    volumes:
      - netdata-lib:/var/lib/netdata
      - netdata-cache:/var/cache/netdata

volumes:
  netdata-config:
    name: ytprocessor-netdata-config
  netdata-lib:
    name: ytprocessor-netdata-lib
  netdata-cache:
    name: ytprocessor-netdata-cache
```

#### Step 1.2: Update Environment Configuration

**File:** `.env.example` (add new variables)

```bash
# NetData Configuration
NETDATA_CLAIM_TOKEN=           # Claim token from app.netdata.cloud
NETDATA_CLAIM_URL=https://app.netdata.cloud
NETDATA_CLAIM_ROOM=            # Room ID for organizing nodes
```

#### Step 1.3: Create Installation Script

**File:** `scripts/install-netdata.sh`

```bash
#!/bin/bash
# NetData Installation Script for Vooglaadija
# Usage: ./scripts/install-netdata.sh

set -euo pipefail

echo "Installing NetData..."

# Option 1: Install on host (recommended for bare metal)
if [[ "$1" == "--host" ]]; then
    echo "Installing NetData on host..."
    curl -fsSL https://get.netdata.io/ | bash -s -- --claim-token="${NETDATA_CLAIM_TOKEN}" --claim-url="${NETDATA_CLAIM_URL}"
fi

# Option 2: Docker-based (use with docker-compose.monitoring.yml)
if [[ "$1" == "--docker" ]]; then
    echo "Setting up NetData Docker agents..."
    # Generate claim token instructions
    echo "To claim nodes:"
    echo "1. Go to app.netdata.cloud"
    echo "2. Create a Space and Room"
    echo "3. Get the claim token from the UI"
    echo "4. Set NETDATA_CLAIM_TOKEN in your .env"
    echo "5. Run: docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d"
fi

echo "NetData installation complete!"
echo "Dashboard available at: http://localhost:19999 (if using host install)"
echo "Or via NetData Cloud: https://app.netdata.cloud"
```

#### Step 1.4: Configure NetData Collectors

**File:** `infra/netdata/go.d.conf` (create directory structure)

```text
# NetData Collector Configuration
# Location: /etc/netdata/go.d.conf or mounted volume

modules:
  - docker
  - nginx
  - redis
  - postgres
  - systemd-journal
  - cgroups
  - cpu
  - disk
  - mem
  - net
  - processes
  - services
  - users
```

#### Step 1.5: Configure NetData Alerts

**File:** `infra/netdata/health.d/app-alerts.conf`

```conf
# Vooglaadija Application Alerts

template: api_response_time_high
    on: http.webgo.smooth_response_time
    os: linux
    hosts: *
    families: *
    lookup: average -5m percentageforeach eguide
    units: ms
    every: 10s
    warn: $this > 500
    crit: $this > 2000
    info: API response time is elevated
    to: sysadmin

template: worker_queue_depth_high
    on: redis.queued_commands
    os: linux
    hosts: *
    families: *
    lookup: average -5m after
    units: jobs
    every: 30s
    warn: $this > 100
    crit: $this > 500
    info: Worker queue depth is elevated
    to: sysadmin

template: container_cpu_usage_high
    on: docker.cpu_usage_percent
    os: linux
    hosts: *
    families: *
    lookup: average -5m percentageforeach
    units: %
    every: 10s
    warn: $this > 80
    crit: $this > 95
    info: Container CPU usage is high
    to: sysadmin

template: disk_space_low
    on: disk.space_usage
    os: linux
    hosts: *
    families: *
    lookup: average -10m at -5m percentageforeach
    units: %
    every: 60s
    warn: $this > 80
    crit: $this > 90
    info: Disk space is running low
    to: sysadmin
```

---

### Phase 2: Python Library Enhancements (Weeks 2-3)

#### Step 2.1: Add Dependencies to pyproject.toml

**File:** `pyproject.toml` (update)

```toml
[project]
name = "vooglaadija"
version = "1.0.0"
# ... existing dependencies ...

dependencies = [
    # Existing core dependencies
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "sqlalchemy[asyncio]>=2.0.25",
    "asyncpg>=0.29.0",
    "redis>=5.0.0",
    # ... other existing deps ...

    # HIGH PRIORITY - New additions
    "structlog>=24.0.0",          # Structured logging
    "orjson>=3.9.0",              # Fast JSON serialization
    "uvloop>=0.19.0; sys_platform != 'win32'",  # Async performance
    "psycopg[binary]>=3.1.0",     # PostgreSQL driver (psycopg3)

    # MEDIUM PRIORITY
    "tenacity>=8.2.0",            # Retry logic
    "rq>=1.15.0",                 # Redis Queue (optional replacement)
    "sentry-sdk>=1.40.0",         # Error tracking

    # LOW PRIORITY
    "pre-commit>=3.6.0",          # Git hooks
    "factory-boy>=3.3.0",         # Test fixtures
]

[project.optional-dependencies]
dev = [
    # ... existing dev deps ...
    "hypothesis>=6.90.0",          # Property-based testing
]
```

#### Step 2.2: structlog Integration

**File:** `app/logging_config.py` (refactor)

```python
"""Structured logging configuration using structlog."""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from app.config import settings


def add_timestamp(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Add ISO timestamp to log entries."""
    import datetime
    event_dict["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return event_dict


def add_service_context(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Add service context to log entries."""
    event_dict["service"] = "vooglaadija"
    event_dict["environment"] = settings.ENVIRONMENT.value
    return event_dict


def configure_logging() -> None:
    """Configure structlog for the application.

    Production: JSON structured logs
    Development: Human-readable colored output
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        add_timestamp,
        add_service_context,
    ]

    if settings.ENVIRONMENT.value == "production":
        # Production: JSON output
        shared_processors.append(structlog.processors.format_exc_info)

        structlog.configure(
            processors=shared_processors + [
                structlog.processors.JSONRenderer()
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Development: Pretty console output
        shared_processors.append(
            structlog.dev.ConsoleRenderer(colors=True, exception_formatter=structlog.dev.plain_traceback)
        )

        structlog.configure(
            processors=shared_processors,
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.value.upper(), logging.INFO),
    )

    # Suppress noisy third-party loggers
    for logger_name in ["uvicorn.access", "httpx", "httpcore", "aiohttp"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str | None = None, **kwargs: Any) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)
        **kwargs: Additional context to bind to all log messages

    Returns:
        Configured structlog logger

    Example:
        logger = get_logger(__name__, user_id=user_id)
        logger.info("user_action", action="login", result="success")
    """
    logger = structlog.get_logger(name)
    if kwargs:
        return logger.bind(**kwargs)
    return logger


# Backward compatibility alias
LoggerAdapter = structlog.stdlib.BoundLogger
```

**File:** `app/main.py` (update imports)

```python
# Before
from app.logging_config import get_logger, setup_logging

# After
from app.logging_config import get_logger, configure_logging as setup_logging
```

**File:** `worker/main.py` (update imports)

```python
# Add to worker/main.py
from app.logging_config import get_logger, configure_logging as setup_logging

# Replace all logging.getLogger with get_logger
logger = get_logger(__name__)
```

#### Step 2.3: orjson Integration

**File:** `app/main.py` (add ORJSON middleware)

```python
"""FastAPI application with ORJSON for fast JSON serialization."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import orjson
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse


class ORJSONResponse(ORJSONResponse):
    """Custom ORJSON response with proper content-type."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure we set the correct content-type for FastAPI
        self.media_type = "application/json"


# In main.py app creation:
app = FastAPI(
    title="YouTube Link Processor API",
    version="0.1.0",
    default_response_class=ORJSONResponse,  # Use ORJSON for all responses
)

# For explicit JSON serialization (Pydantic models)
def orjson_dumps(obj: Any, *, default: Any = None) -> str:
    """ORJSON.dumps wrapper for Pydantic."""
    return orjson.dumps(obj, default=default).decode()


# Update any json.dumps/json.loads calls to use orjson
import orjson
def json_dumps(obj: Any) -> bytes:
    return orjson.dumps(obj)

def json_loads(data: bytes | str) -> Any:
    return orjson.loads(data)
```

#### Step 2.4: uvloop Integration

**File:** `app/main.py` (update entry point)

```python
"""Application entry point with uvloop for async performance."""

import asyncio
from contextlib import asynccontextmanager

import uvloop
from fastapi import FastAPI

# Install uvloop as the default event loop policy
uvloop.install()

# Rest of imports...

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    setup_logging()
    init_metrics()
    yield
    # Shutdown
    # Cleanup if needed

app = FastAPI(lifespan=lifespan)

# Run with: uvicorn app.main:app --loop uvloop
```

**File:** `entrypoint.sh` (update for uvloop)

```bash
#!/bin/bash
set -e

# Install uvloop for better async performance
pip install --no-cache-dir uvloop

# Run with uvloop
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --loop uvloop \
    --workers 1
```

#### Step 2.5: psycopg (PostgreSQL Driver) Migration

**File:** `app/database.py` (update)

```python
"""Async database configuration using psycopg3 (asyncpg compatibility)."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


class _EngineFactory:
    """Lazy engine factory with psycopg3 (via asyncpg driver)."""

    _engine: AsyncEngine | None = None

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        if cls._engine is not None:
            return cls._engine

        # Use psycopg3 with asyncpg driver (compatible with asyncpg API)
        # The 'psycopg' package provides both sync and async support
        # asyncpg is still used as the driver under the hood for asyncpg-like API

        engine_url = settings.database_url.replace(
            "postgresql+asyncpg://",
            "postgresql+psycopg://"  # Use psycopg3
        ).replace(
            "postgresql://",
            "postgresql://"
        )

        cls._engine = create_async_engine(
            engine_url,
            echo=settings.ENVIRONMENT.value == "development",
            pool_size=20,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
            # psycopg3 specific options
            pool_options={
                "min_size": 5,
                "max_size": 20,
                "timeout": 30,
            },
        )
        return cls._engine


def get_engine() -> AsyncEngine:
    """Get or create the database engine."""
    return _EngineFactory.get_engine()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for database sessions."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_async_session_factory():
    """Get the async session factory for use outside of FastAPI dependencies."""
    engine = get_engine()
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
```

---

### Phase 3: Medium Priority Enhancements (Weeks 3-4)

#### Step 3.1: tenacity for Retry Logic

**File:** `worker/processor.py` (refactor retry logic)

```python
"""Job processor with tenacity retry logic."""

import asyncio
import logging
from typing import Optional
from uuid import UUID

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    retry_if_result,
)

from app.config import settings
from app.database import get_async_session_factory
from app.models.download_job import DownloadJob
from app.services.yt_dlp_service import extract_media_url
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Custom exceptions for retry handling
class TransientError(Exception):
    """Error that should trigger a retry."""
    pass


class PermanentError(Exception):
    """Error that should NOT trigger a retry."""
    pass


class FormatUnavailableError(PermanentError):
    """YouTube format is not available - non-retryable."""
    pass


@retry(
    retry=retry_if_exception_type(TransientError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def download_with_retry(
    db: AsyncSession,
    job_id: UUID,
    url: str,
    storage_path: str,
) -> tuple[str, str]:
    """Download media with automatic retry for transient errors.

    Uses tenacity for battle-tested retry logic with exponential backoff.
    """
    try:
        file_path, file_name = await extract_media_url(url, storage_path)
        return file_path, file_name

    except Exception as e:
        error_str = str(e).lower()

        # These are permanent failures - don't retry
        if "format is not available" in error_str:
            raise FormatUnavailableError(f"Format unavailable: {e}")

        if "video is unavailable" in error_str:
            raise PermanentError(f"Video unavailable: {e}")

        # Everything else is transient - will retry
        raise TransientError(f"Transient error: {e}")


async def process_job_with_retry(
    job_id: UUID,
    session_factory,
) -> bool:
    """Process a single job with tenacity retry support."""

    async with session_factory() as db:
        # Fetch job
        result = await db.execute(
            select(DownloadJob).where(DownloadJob.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            return False

        try:
            # Use tenacity-wrapped download
            file_path, file_name = await download_with_retry(
                db, job_id, job.url, settings.storage_path
            )

            # Success - update job
            from datetime import datetime, timedelta, UTC
            await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id)
                .values(
                    status="completed",
                    file_path=file_path,
                    file_name=file_name,
                    completed_at=datetime.now(UTC),
                    expires_at=datetime.now(UTC) + timedelta(hours=settings.file_expire_hours),
                )
            )
            await db.commit()
            return True

        except FormatUnavailableError as e:
            # Non-retryable - mark as failed
            await db.execute(
                update(DownloadJob)
                .where(DownloadJob.id == job_id)
                .values(
                    status="failed",
                    error=str(e),
                    completed_at=datetime.now(UTC),
                )
            )
            await db.commit()
            return False

        except Exception as e:
            # Log and re-raise for the main processor to handle
            logger.error(f"Job {job_id} failed: {e}")
            raise
```

#### Step 3.2: Sentry Integration

**File:** `app/main.py` (add Sentry)

```python
"""FastAPI application with Sentry error tracking."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse

from app.config import settings

# Sentry initialization
if settings.ENVIRONMENT.value == "production":
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlAlchemyIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,  # Add to settings
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlAlchemyIntegration(),
            RedisIntegration(),
        ],
        traces_sample_rate=0.1,  # 10% of transactions for performance
        profiles_sample_rate=0.1,
        environment=settings.ENVIRONMENT.value,
        release="vooglaadija@1.0.0",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)


# Global exception handler for unhandled exceptions
@app.exception_handler(Exception)
async def sentry_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
    """Handle exceptions and send to Sentry."""
    logger = logging.getLogger(__name__)
    logger.error(f"Unhandled exception: {exc}", exc_info=True)

    # Sentry will automatically capture the exception
    # Here we return a generic error response
    return ORJSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc) if settings.ENVIRONMENT.value == "development" else None}
    )
```

**File:** `app/config.py` (add SENTRY_DSN)

```python
class Settings(BaseSettings):
    # ... existing fields ...

    SENTRY_DSN: str | None = None  # Add this
```

#### Step 3.3: RQ (Redis Queue) Migration (Optional)

**File:** `worker/rq_setup.py` (new file - optional replacement for custom queue)

```python
"""RQ (Redis Queue) setup for job processing.

This is an OPTIONAL replacement for the custom Redis queue implementation.
Use this if you need more robust job management, monitoring, and reliability.

For simple use cases, the existing custom implementation is sufficient.
"""

import os
from typing import Optional

from redis import Redis
from rq import Queue, Worker
from rq.decorators import job

# Redis connection
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_conn = Redis.from_url(redis_url, decode_responses=True)

# Create queues
default_queue = Queue("default", connection=redis_conn)
download_queue = Queue("downloads", connection=redis_conn)
retry_queue = Queue("retry", connection=redis_conn)


@job(download_queue, result_ttl=86400)
def process_download_job(job_id: str) -> dict:
    """Process a download job using RQ.

    This replaces the custom queue implementation with RQ.
    Benefits:
    - Built-in job persistence
    - Automatic retry with configurable backoff
    - Failed job tracking and handling
    - Web UI for monitoring (rq-dashboard)
    - Worker management
    """
    import asyncio
    from worker.processor import process_next_job
    from uuid import UUID

    # RQ jobs run synchronously, so we need to run the async processor
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(process_next_job(UUID(job_id)))
        return {"status": "success" if result else "skipped", "job_id": job_id}
    finally:
        loop.close()


# Worker startup
def start_worker(queue_names: list[str] = None):
    """Start RQ workers."""
    if queue_names is None:
        queue_names = ["default", "downloads", "retry"]

    queues = [Queue(name, connection=redis_conn) for name in queue_names]
    worker = Worker(queues, connection=redis_conn)
    worker.work()
```

**File:** `docker-compose.rq.yml` (optional - for RQ dashboard)

```yaml
# RQ Dashboard for monitoring queues
services:
  rq-dashboard:
    image: rq/rq-dashboard:latest
    container_name: ytprocessor-rq-dashboard
    ports:
      - "9181:9181"
    environment:
      RQ_REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - ytprocessor-network
```

---

### Phase 4: Testing & Validation (Week 4)

#### Step 4.1: Create Integration Tests

**File:** `tests/test_netdata_integration.py`

```python
"""Integration tests for NetData monitoring."""

import pytest
import httpx


class TestNetDataIntegration:
    """Test NetData agent and metrics collection."""

    @pytest.mark.integration
    async def test_netdata_agent_running(self):
        """Verify NetData agent is accessible."""
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:19999/api/v1/info")
            assert response.status_code == 200
            data = response.json()
            assert "version" in data

    @pytest.mark.integration
    async def test_netdata_docker_collector(self):
        """Verify Docker container metrics are being collected."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:19999/api/v1/allmetrics",
                params={"format": "json", "chart": "docker.cpu_usage_percent"}
            )
            assert response.status_code == 200

    @pytest.mark.integration
    async def test_netdata_redis_collector(self):
        """Verify Redis metrics are being collected."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://localhost:19999/api/v1/allmetrics",
                params={"format": "json", "chart": "redis.connected_clients"}
            )
            assert response.status_code == 200
```

#### Step 4.2: Update CI/CD Pipeline

**File:** `.github/workflows/monitoring-validation.yml`

```yaml
name: Monitoring Validation

on:
  push:
    branches: [main, develop]
  schedule:
    - cron: "0 */6 * * *" # Every 6 hours

jobs:
  netdata-health:
    runs-on: ubuntu-latest
    steps:
      - name: Check NetData Agent
        run: |
          curl -s http://localhost:19999/api/v1/info | jq '.version'

  prometheus-metrics:
    runs-on: ubuntu-latest
    steps:
      - name: Check Prometheus Metrics
        run: |
          curl -s http://localhost:8000/metrics | head -20
```

---

## Part 3: Configuration Reference

### A. NetData Cloud Setup

1. **Create Account:** <https://app.netdata.cloud>
1. **Create Space:** Organize by environment (dev/staging/prod)
1. **Create Room:** Specific room for vooglaadija
1. **Claim Nodes:** Use claim token to connect agents

### B. Environment Variables

```bash
# .env.production additions

# NetData (Cloud)
NETDATA_CLAIM_TOKEN=your-claim-token-here
NETDATA_CLAIM_URL=https://app.netdata.cloud
NETDATA_CLAIM_ROOM=your-room-id

# Optional: NetData On-Prem (if self-hosting)
NETDATA_PARENT_HOST=parent.netdata.local
NETDATA_PARENT_PORT=19999

# Sentry (Error Tracking)
SENTRY_DSN=https://xxx@sentry.io/xxx
```

### C. Docker Compose Overrides

```bash
# Start with NetData monitoring
docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d

# Start with RQ dashboard (optional)
docker-compose -f docker-compose.yml -f docker-compose.monitoring.yml -f docker-compose.rq.yml up -d
```

---

## Part 4: Rollout Timeline

| Week | Phase                    | Tasks                                             | Deliverables                     |
| ---- | ------------------------ | ------------------------------------------------- | -------------------------------- |
| 1    | **NetData Installation** | Install agents, configure collectors, claim nodes | Real-time dashboards operational |
| 2    | **structlog + orjson**   | Update logging, configure ORJSON                  | Structured logs, faster JSON     |
| 3    | **uvloop + psycopg**     | Configure uvloop, migrate psycopg                 | Better async performance         |
| 4    | **tenacity + Sentry**    | Add retry logic, configure Sentry                 | Robust error handling            |
| 5    | **Testing & Validation** | Integration tests, load testing                   | All systems validated            |

---

## Part 5: Troubleshooting

### NetData Common Issues

**Node not appearing in Cloud:**

```bash
# Check NetData logs
docker logs ytprocessor-netdata-api

# Verify claim token
cat /var/lib/netdata/cloud.d/cloud_link.txt

# Reclaim node
netdata-claim.sh -token=TOKEN -url=URL -rooms=ROOM
```

**Missing Docker metrics:**

```bash
# Verify Docker socket is mounted correctly
docker exec ytprocessor-netdata-api ls -la /var/run/docker.sock

# Check cgroup plugin
docker exec ytprocessor-netdata-api cat /etc/netdata/go.d.conf
```

**High memory usage:**

```bash
# Reduce retention
docker exec ytprocessor-netdata-api sed -i 's/history=3600/history=600/g' /etc/netdata/netdata.conf

# Restart
docker restart ytprocessor-netdata-api
```

---

## Part 6: Maintenance

### Daily

- Check NetData Cloud dashboard for alerts
- Review failed jobs in Prometheus metrics

### Weekly

- Review NetData health checklist
- Verify backup procedures
- Check disk space on monitoring volumes

### Monthly

- Review NetData retention settings
- Update NetData agents
- Analyze performance trends

---

## Appendix A: Quick Reference

### Key Commands

```bash
# View NetData real-time dashboard
open http://localhost:19999

# Check NetData logs
docker logs ytprocessor-netdata-api -f

# Manually trigger alert
docker exec ytprocessor-netdata-api netdata-alarm \
  -a -n test_alert -v 100 -t 1

# View all metrics via API
curl -s http://localhost:19999/api/v1/allmetrics?format=json | jq

# Check claimed nodes
curl -s http://localhost:19999/api/v1/info | jq '.cloud_enabled'
```

### Useful Links

- NetData Docs: <https://learn.netdata.cloud/docs/>
- Integrations: <https://www.netdata.cloud/integrations/>
- GitHub: <https://github.com/netdata/netdata>
- Community: <https://community.netdata.cloud/>

---

## Appendix B: Enhancement Priority Summary

| Enhancement | Priority | Effort | Impact | Status                           |
| ----------- | -------- | ------ | ------ | -------------------------------- |
| NetData     | HIGH     | Low    | High   | ✅ COMPLETED                     |
| structlog   | HIGH     | Low    | Medium | ✅ COMPLETED                     |
| orjson      | HIGH     | Low    | Medium | ✅ COMPLETED                     |
| uvloop      | HIGH     | Low    | Medium | ✅ COMPLETED                     |
| psycopg     | HIGH     | Medium | High   | 📋 DEFERRED (asyncpg works well) |
| tenacity    | MEDIUM   | Low    | Medium | ✅ COMPLETED                     |
| RQ          | MEDIUM   | Medium | Medium | ⏭️ OPTIONAL                      |
| Sentry      | MEDIUM   | Low    | High   | ✅ COMPLETED                     |
| strawberry  | LOW      | Medium | Low    | ⏭️ DEFERRED                      |
| pre-commit  | LOW      | Low    | Low    | ⏭️ NICE-TO-HAVE                  |
| factory_boy | LOW      | Low    | Low    | ⏭️ NICE-TO-HAVE                  |
| dynaconf    | LOW      | Medium | Low    | ⏭️ SKIP                          |
| hypothesis  | LOW      | Medium | Low    | ⏭️ NICE-TO-HAVE                  |

---

## Appendix C: Scripts Reference

### Cleanup Scripts

```bash
# Clean Docker disk space
./scripts/cleanup-docker.sh --dry-run        # Preview what would be cleaned
./scripts/cleanup-docker.sh --build-cache    # Clean build cache (~41GB)
./scripts/cleanup-docker.sh --volumes        # Clean unused volumes (WARNING)
./scripts/cleanup-docker.sh --all            # Clean everything
```

### Monitoring Scripts

```bash
# NetData monitoring (via hatch)
hatch run monitoring:netdata-status    # Check NetData agent info
hatch run monitoring:netdata-metrics    # View all metrics as JSON
hatch run monitoring:netdata-charts     # View available charts
hatch run monitoring:netdata-alarms    # View active alarms

# Direct curl commands
curl -s http://localhost:19999/api/v1/info | jq .
```

### NetData Cloud Claiming

```bash
# Claim all NetData agents to NetData Cloud
NETDATA_CLAIM_TOKEN=your-token ./scripts/claim-netdata.sh

# Claim specific agents
./scripts/claim-netdata.sh --token abc123 --api
./scripts/claim-netdata.sh --token abc123 --worker
```

---

## Appendix D: Known Issues

### Disk Space (RESOLVED)

- **Issue**: Root partition at 98.5% capacity
- **Root Cause**: Docker build cache (41GB) and unused volumes (11GB)
- **Resolution**: Use `./scripts/cleanup-docker.sh --build-cache --volumes`

### Nginx Health Check (RESOLVED)

- **Issue**: nginx container marked unhealthy
- **Root Cause**: `/health` endpoint not configured
- **Resolution**: Added `/health` location to `infra/nginx/default.conf`

---

_Document Version: 2.0_  
_Last Updated: 2026-04-15_
