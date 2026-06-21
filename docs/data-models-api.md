# Data Models — API Server

**Part:** API Server & Worker (`app/models/`)
**Engine:** SQLAlchemy 2.0 Async with PostgreSQL
**Migration:** Alembic (4 migrations)

---

## Model: `User` → Table `users`

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | `UUID` | PK, default `uuid.uuid4` | Primary key |
| `username` | `String(64)` | Nullable | Optional display name |
| `email` | `String(255)` | NOT NULL, Indexed | Unique (conditional on `deleted_at IS NULL`) |
| `password_hash` | `String(255)` | NOT NULL | bcrypt-hashed |
| `is_active` | `Boolean` | NOT NULL, default=True | Soft-active flag |
| `deleted_at` | `DateTime(tz)` | Nullable, Indexed | Soft-delete timestamp |
| `token_version` | `Integer` | server_default=1 | JWT bulk invalidation counter |
| `created_at` | `DateTime(tz)` | server_default=`func.now()` | |
| `updated_at` | `DateTime(tz)` | server_default=`func.now()`, onupdate=`func.now()` | |

**Relationships:** Has many `DownloadJob` (CASCADE), has many `FailedJob` (CASCADE).

**Helper:** `not_deleted()` — filter `User.deleted_at.is_(None)`.

---

## Model: `DownloadJob` → Table `download_jobs`

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `user_id` | `UUID` | FK → `users.id` CASCADE | Owner |
| `url` | `Text` | NOT NULL | Source video URL |
| `status` | `String(20)` | NOT NULL, default="pending" | Lifecycle: pending → processing → completed/failed/deferred |
| `file_path` | `String(500)` | Nullable | Filesystem path to downloaded file |
| `title` | `String(255)` | Nullable | Resolved video title |
| `file_name` | `String(255)` | Nullable | Display filename |
| `error` | `Text` | Nullable | Error message if failed |
| `error_category` | `String(50)` | Nullable | Classified error category |
| `retry_count` | `Integer` | default=0 | Current retry attempt |
| `max_retries` | `Integer` | default=3 | Max retry attempts |
| `next_retry_at` | `DateTime(tz)` | Nullable | When to retry |
| `created_at` | `DateTime(tz)` | server_default=`func.now()` | |
| `updated_at` | `DateTime(tz)` | server_default=`func.now()`, onupdate=`func.now()` | |
| `completed_at` | `DateTime(tz)` | Nullable | When processing finished |
| `expires_at` | `DateTime(tz)` | Nullable, Indexed | TTL for cleanup |

**Status lifecycle:** `pending` → `processing` → `completed` | `failed` | `deferred`

---

## Model: `FailedJob` → Table `failed_jobs`

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `original_job_id` | `UUID` | FK → `download_jobs.id` SET NULL, Indexed | Link to original job |
| `user_id` | `UUID` | FK → `users.id` CASCADE, NOT NULL | Owner |
| `url` | `Text` | NOT NULL | Original URL |
| `error_category` | `String(50)` | NOT NULL, Indexed | e.g. rate_limited, blocked, transient |
| `retry_history` | `Text` | Nullable | JSON or text log of retry attempts |
| `final_error` | `Text` | NOT NULL | Last error message |
| `retry_count` | `Integer` | default=0 | How many retries were attempted |
| `max_retries_at_failure` | `Integer` | default=0 | Max retries configured at time of failure |
| `title` | `String(255)` | Nullable | Video title if available |
| `created_at` | `DateTime(tz)` | server_default=`func.now()` | |
| `failed_at` | `DateTime(tz)` | server_default=`func.now()` | When it permanently failed |
| `expires_at` | `DateTime(tz)` | Nullable, Indexed | TTL for cleanup |

---

## Model: `Outbox` → Table `outbox`

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `job_id` | `UUID` | NOT NULL, Indexed | Referenced DownloadJob ID |
| `event_type` | `String(50)` | NOT NULL | e.g. "enqueue_download" |
| `payload` | `Text` | Nullable | JSON payload |
| `status` | `String(20)` | NOT NULL, default="pending", Indexed | pending → processed |
| `created_at` | `DateTime(tz)` | server_default=`func.now()` | |
| `processed_at` | `DateTime(tz)` | Nullable | When worker consumed it |

**Purpose:** Transactional outbox pattern for crash-safe queue writes.

---

## Migration History

| Migration | Description |
|-----------|-------------|
| `001_initial` | Creates `users`, `download_jobs`, `outbox` tables |
| `002_add_title_to_download_jobs` | Adds `title` column to `download_jobs` |
| `003_add_error_category_and_failed_jobs` | Adds `error_category`, creates `failed_jobs` table |
| `004_add_token_version_to_users` | Adds `token_version` to `users` for JWT invalidation |

## Database Configuration

- **Engine:** Async SQLAlchemy (`create_async_engine`)
- **Pool:** `pool_size=10`, `max_overflow=5`, `pool_timeout=30`, `pool_recycle=1800`
- **Lazy initialization:** Engine not created until first use (`_EngineFactory` singleton)
- **Session:** `async_sessionmaker` with `expire_on_commit=False`
