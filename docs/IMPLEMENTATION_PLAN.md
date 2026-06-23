# Implementation Plan: Fix Critical Issues

## Executive Summary

This plan addresses 22 identified issues across the YouTube Link Processor project, ranging from
critical bugs that break core functionality to security and infrastructure concerns. The most urgent
issue is **#1 - the missing settings import in worker/queue.py**, which completely breaks the job
queue system.

---

## Issue Categorization

| Severity       | Count | Issues             |
| -------------- | ----- | ------------------ |
| 🔴 Critical    | 4     | #1, #2, #3, #4     |
| 🟠 Logic       | 4     | #5, #6, #7, #8     |
| 🟡 Data/Schema | 2     | #9, #10            |
| 🟢 Quality     | 4     | #11, #12, #13, #14 |
| 🔵 Security    | 3     | #15, #16, #17      |
| 🟣 Infra       | 3     | #18, #19, #20      |
| ⚪ Tests       | 2     | #21, #22           |

---

## Phase 1: Critical Bug Fixes (P0 - Must Fix First)

### Issue #1: worker/queue.py - Missing settings Import

**Status:** VERIFIED - Line 12 uses `settings.redis_url` but `settings` is never imported

**Impact:** Queue operations silently fall back to MagicMock

- Jobs saved to DB with status="pending" but never enqueued
- Worker calls rpop() on MagicMock which always returns None
- Download jobs never get processed

**Fix:**

```python
# Add at the top of worker/queue.py
from app.config import settings
```

**Test Verification:**

```python
# Test that jobs are actually enqueued
def test_enqueue_job_sends_to_redis(redis_mock):
    enqueue_job("test-job-id")
    redis_mock.lpush.assert_called_once_with("download_queue", "test-job-id")
```

---

### Issue #2 & #8: worker/processor.py - Deprecated datetime.utcnow()

**Status:** VERIFIED - Lines 46, 47, 57 use `datetime.utcnow()` (deprecated in Python 3.12+)

**Impact:**

- Deprecated API will be removed in future Python versions
- Creates naive datetime objects while DB expects timezone-aware

**Fix:**

```python
from datetime import UTC, datetime, timedelta

# Replace:
completed_at=datetime.utcnow()
expires_at=datetime.utcnow() + timedelta(...)

# With:
completed_at=datetime.now(UTC)
expires_at=datetime.now(UTC) + timedelta(...)
```

---

### Issue #3: worker/processor.py - Unused uuid Import

**Status:** VERIFIED - Line 2 imports uuid but never uses it

**Fix:** Remove `import uuid` from worker/processor.py

---

### Issue #4: tests/conftest.py - Test User ID Mismatch

**Status:** VERIFIED - Line 110 creates token for "test-user-id" but no corresponding user exists

**Fix Options:**

1. **Option A (Recommended):** Create the test user in the fixture
1. **Option B:** Use a fixture that creates user and returns headers

**Implementation:**

```python
@pytest.fixture
async def auth_headers(db_session) -> dict[str, str]:
    """Create a test user and return auth headers with valid token."""
    from app.auth import create_access_token
    from app.models.user import User
    from app.services.auth_service import hash_password

    user = User(
        id="test-user-id",
        email="test@example.com",
        password_hash=hash_password("password123"),
        is_active=True
    )
    db_session.add(user)
    await db_session.commit()

    token = create_access_token("test-user-id")
    return {"Authorization": f"Bearer {token}"}
```

---

## Phase 2: Logic & Error Handling (P1)

### Issue #6: yt-dlp Service Has No Error Handling

**Status:** VERIFIED - Lines 33-34 in yt_dlp_service.py have no try/except

**Fix:**

```python
async def extract_media_url(url: str, storage_path: str) -> tuple[str, str]:
    download_dir = os.path.join(storage_path, "downloads")

    try:
        os.makedirs(download_dir, exist_ok=True)
    except OSError as e:
        raise StorageError(f"Failed to create download directory: {e}") from e

    # ... rest of function with proper exception handling
```

---

### Issue #7: File Deletion Errors Silently Swallowed

**Status:** VERIFIED - Lines 178-181 in downloads.py silently pass on OSError

**Fix:**

```python
import logging

logger = logging.getLogger(__name__)

# In delete_download:
if job.file_path:
    try:
        os.remove(job.file_path)
        logger.info(f"Deleted file: {job.file_path}")
    except OSError as e:
        logger.error(f"Failed to delete file {job.file_path}: {e}")
        # Still delete the DB record but notify user
```

---

## Phase 3: Database & Schema (P2)

### Issue #9: Missing Index on expires_at Column

**Status:** VERIFIED - DownloadJob.expires_at has no index

**Fix:** Create Alembic migration

```python
# alembic migration
from alembic import op
import sqlalchemy as sa

# In upgrade():
op.create_index(
    'ix_download_jobs_expires_at',
    'download_jobs',
    ['expires_at']
)
```

**Update Model:**

```python
class DownloadJob(Base):
    # ... other columns
    expires_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True  # Add index=True
    )
```

---

### Issue #10: No Cleanup Job for Expired Files

**Status:** VERIFIED - No scheduled cleanup mechanism

**Implementation Options:**

1. **Option A (Recommended):** Add cleanup to worker loop
1. **Option B:** Separate scheduled task/cron job

**Fix in worker/main.py:**

```python
import asyncio
from datetime import UTC, datetime, timedelta

async def cleanup_expired_jobs():
    """Delete expired jobs and their files."""
    async with AsyncSessionLocal() as db:
        expired_time = datetime.now(UTC) - timedelta(hours=settings.file_expire_hours)

        result = await db.execute(
            select(DownloadJob).where(
                DownloadJob.expires_at < expired_time,
                DownloadJob.status == "completed"
            )
        )
        expired_jobs = result.scalars().all()

        for job in expired_jobs:
            if job.file_path and os.path.exists(job.file_path):
                try:
                    os.remove(job.file_path)
                except OSError:
                    pass
            await db.delete(job)

        await db.commit()

async def main():
    cleanup_counter = 0
    while True:
        await process_next_job()

        # Run cleanup every 100 iterations (approximately every 100 seconds)
        cleanup_counter += 1
        if cleanup_counter >= 100:
            await cleanup_expired_jobs()
            cleanup_counter = 0

        await asyncio.sleep(1)
```

---

## Phase 4: Code Quality (P2)

### Issue #11: Duplicate get_db Functions

**Status:** VERIFIED - app/database.py:12 and app/api/dependencies/**init**.py:18

**Decision:** Consolidate to app/database.py

**Implementation:**

1. Remove `get_db` from `app/api/dependencies/__init__.py`
1. Import from `app.database` instead
1. Update all imports throughout codebase

**Files to update:**

- `app/api/dependencies/__init__.py` - Remove get_db, keep import
- `app/api/routes/auth.py` - Already uses DbSession, no change needed
- `app/api/routes/downloads.py` - Already imports from app.database, no change needed

---

### Issue #12: Dead Code in app/api/dependencies/**init**.py

**Status:** VERIFIED - get_db in dependencies is never used

**Fix:** Remove the duplicate get_db function from dependencies/**init**.py

---

### Issue #13: Inconsistent Type Hints in downloads.py

**Status:** VERIFIED - Some endpoints use `db: AsyncSession = Depends(get_db)` others would use
DbSession

**Fix:** Standardize to use DbSession from dependencies

```python
from app.api.dependencies import CurrentUser, DbSession

@router.post("", response_model=DownloadResponse)
async def create_download(
    data: DownloadCreate,
    current_user: CurrentUser,
    db: DbSession,  # Use annotated type
) -> DownloadResponse:
```

---

### Issue #14: Hardcoded bcrypt rounds

**Status:** VERIFIED - auth_service.py uses bcrypt\_\_rounds=12

**Fix:** Make configurable via settings

```python
# app/config.py
class Settings(BaseSettings):
    # ... existing settings
    bcrypt_rounds: int = 12  # Default for development

# app/services/auth_service.py
from app.config import settings

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.bcrypt_rounds
)
```

---

## Phase 5: Security (P1)

### Issue #15: No Rate Limiting on Auth Endpoints

**Status:** VERIFIED - Auth rules specify 5 requests/minute but no implementation

**Implementation:**

```python
# app/middleware/rate_limit.py
from fastapi import Request, HTTPException
import redis
import time

class RateLimiter:
    def __init__(self, redis_client, max_requests: int, window: int):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window = window

    async def is_allowed(self, key: str) -> bool:
        now = time.time()
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - self.window)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, self.window)
        _, current_count, _, _ = pipe.execute()
        return current_count < self.max_requests

# app/api/routes/auth.py
from fastapi import Depends, Request
from app.middleware.rate_limit import RateLimiter

rate_limiter = RateLimiter(redis_client, max_requests=5, window=60)

@router.post("/register")
async def register(
    request: Request,
    user_data: UserCreate,
    db: DbSession
) -> UserResponse:
    client_ip = request.client.host
    if not await rate_limiter.is_allowed(f"auth:register:{client_ip}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    # ... rest of handler
```

---

### Issue #16: JWT Secret Key Validation

**Status:** VERIFIED - Default is "change-me", no entropy validation

**Fix:** Add validation to settings

```python
# app/config.py
import secrets

class Settings(BaseSettings):
    # ... existing settings

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if v == "change-me":
            raise ValueError(
                "SECRET_KEY must be changed from default value. "
                "Generate a secure key with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters for security")
        return v
```

---

### Issue #17: CORS Allowed by Default

**Status:** VERIFIED - cors_origins: str = "\*"

**Fix:** Change default and add validation

```python
# app/config.py
class Settings(BaseSettings):
    # ... existing settings
    cors_origins: str = "http://localhost:3000"  # Safer default

    @field_validator("cors_origins")
    @classmethod
    def validate_cors(cls, v: str) -> str:
        if v == "*":
            import warnings
            warnings.warn(
                "CORS_ORIGINS is set to '*', allowing all origins. "
                "This is insecure for production.",
                stacklevel=2
            )
        return v

# app/main.py
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

app = FastAPI(title="YouTube Link Processor")

# Configure CORS properly
origins = settings.cors_origins.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
```

---

## Phase 6: Infrastructure (P2)

### Issue #18: ffmpeg Dependency

**Status:** PARTIALLY ADDRESSED - ffmpeg is in Dockerfiles but should also be in pyproject.toml

**Note:** ffmpeg is a system dependency, not a Python package. The current Dockerfile setup is
correct. This issue can be closed as already addressed.

---

### Issue #19: No Health Check for Worker

**Status:** VERIFIED - docker-compose.yml has no healthcheck for worker service

**Fix:**

```yaml
# docker-compose.yml
worker:
  # ... existing config
  healthcheck:
    test: ["CMD", "python", "-c", "import redis; r=redis.from_url('redis://redis:6379'); r.ping()"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 10s
```

**Alternative:** Create a proper health check script

```python
# worker/health.py
import asyncio
import sys
import redis
from app.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def health_check():
    # Check Redis
    try:
        r = redis.from_url(settings.redis_url)
        r.ping()
    except Exception as e:
        print(f"Redis check failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Check Database
    try:
        engine = create_async_engine(settings.database_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        print(f"Database check failed: {e}", file=sys.stderr)
        sys.exit(1)

    print("Worker health check passed")
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(health_check())
```

---

### Issue #20: Nginx Missing Critical Configurations

**Status:** VERIFIED - No timeouts, buffering, or WebSocket support

**Fix:**

```nginx
# infra/nginx/default.conf
server {
    listen 80;
    server_name _;

    # Timeouts for long-running downloads
    proxy_connect_timeout 60s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;

    # Disable buffering for file downloads
    proxy_buffering off;
    proxy_request_buffering off;

    location / {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support (for future use)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # File download size limit (adjust as needed)
    client_max_body_size 500M;
}
```

---

## Phase 7: Testing Improvements (P2)

### Issue #21: auth_headers Fixture Creates Invalid Tokens

**Status:** VERIFIED - Covered under Issue #4

## Already addressed in Phase 1

---

### Issue #22: Tests May Pollute Each Other

**Status:** VERIFIED - No transaction rollback in fixtures

**Current State:** Tests use `drop_all`/`create_all` pattern

**Improvement:** Use transaction rollback for faster tests

```python
# tests/conftest.py - Alternative fixture using transactions
@pytest.fixture
async def db_session():
    """Provide a transactional scope around a series of operations."""
    async with TestingSessionLocal() as session:
        async with session.begin():
            yield session
            await session.rollback()  # Rollback instead of drop_all
```

---

## Implementation Order

### Sprint 1: Critical (Day 1)

1. **Issue #1** - Fix missing settings import in worker/queue.py
1. **Issue #2 & #8** - Replace datetime.utcnow() with datetime.now(UTC)
1. **Issue #3** - Remove unused uuid import
1. **Issue #4** - Fix test user ID mismatch

### Sprint 2: Security & Logic (Days 2-3)

1. **Issue #15** - Add rate limiting
1. **Issue #16** - Add JWT secret validation
1. **Issue #17** - Fix CORS defaults
1. **Issue #6** - Add error handling to yt-dlp service
1. **Issue #7** - Log file deletion errors

### Sprint 3: Data & Schema (Days 4-5)

1. **Issue #9** - Add index on expires_at
1. **Issue #10** - Add cleanup job
1. **Issue #5** - Add job recovery mechanism

### Sprint 4: Code Quality & Infra (Days 6-7)

1. **Issue #11 & #12** - Consolidate get_db functions
1. **Issue #13** - Standardize type hints
1. **Issue #14** - Make bcrypt rounds configurable
1. **Issue #19** - Add worker health check
1. **Issue #20** - Improve nginx config
1. **Issue #22** - Improve test isolation

---

## Testing Strategy

### Unit Tests

```bash
# Run specific test modules
pytest tests/test_services/test_yt_dlp.py -v
pytest tests/test_api/test_auth.py -v
```

### Integration Tests

```bash
# Start services
docker-compose up -d db redis

# Run integration tests
pytest tests/test_api/ -v --integration
```

### Manual Verification

```bash
# Test the queue system
curl -X POST http://localhost:8000/api/v1/downloads \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# Check job status
curl http://localhost:8000/api/v1/downloads/{job_id} \
  -H "Authorization: Bearer $TOKEN"
```

---

## Verification Checklist

### Critical Fixes

- [ ] worker/queue.py imports settings
- [ ] Jobs are enqueued to actual Redis (not MagicMock)
- [ ] Worker processes jobs from queue
- [ ] datetime.utcnow() replaced with datetime.now(UTC)
- [ ] Tests create valid users for auth fixtures

### Security

- [ ] Rate limiting returns 429 when exceeded
- [ ] Default SECRET_KEY raises error
- [ ] CORS validation shows warning for "\*"

### Infrastructure

- [ ] Worker health check endpoint works
- [ ] Nginx config includes timeouts
- [ ] File downloads work through nginx

---

## Rollback Plan

Each change should be:

1. Made in a separate commit
1. Tested individually
1. Documented in commit message

If issues arise:

```bash
# Revert specific commit
git revert <commit-hash>

# Or reset to last known good state
git reset --hard <last-known-good>
```

---

## Post-Implementation Monitoring

After deployment, monitor:

1. Queue depth (should not grow unbounded)
1. Job processing rate
1. Failed job count
1. Expired job cleanup rate
1. Authentication error rate
1. Rate limiting triggers

---

_Plan Version: 1.0_  
_Last Updated: 2026-03-25_
