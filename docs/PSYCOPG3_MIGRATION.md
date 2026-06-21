# Psycopg3 Migration Guide

This document describes how to migrate from `asyncpg` to `psycopg` (psycopg3) for PostgreSQL async
operations.

## Current State

The project currently uses:

- `asyncpg` - Pure Python async PostgreSQL driver
- `sqlalchemy[asyncio]` with `asyncpg` driver

## Why Migrate to Psycopg3?

**Benefits:**

1. **C Extension Backend**: psycopg3 uses libpq via C extensions for better performance
1. **Server-Side Cursors**: Built-in support for large result sets
1. **Pipeline Mode**: Batch multiple queries for reduced round-trips
1. **COPY Support**: Native PostgreSQL COPY command support
1. **Prepared Statements**: Better handling of prepared statements
1. **Connection Pooling**: Improved connection pool management

**Performance Comparison:**

- psycopg3 with binary mode is 10-20% faster than asyncpg for typical workloads
- Significantly faster for bulk data operations

## Migration Steps

### 1. Update Dependencies

**pyproject.toml** (already updated):

```toml
dependencies = [
    # Change from:
    "asyncpg>=0.29.0",
    # To:
    "psycopg[binary]>=3.1.0",
]
```

### 2. Update Database URL Scheme

**app/config.py** - Update database URL construction:

```python
# Current (asyncpg)
DATABASE_URL = "postgresql+asyncpg://user:pass@host:5432/db"

# After migration (psycopg)
DATABASE_URL = "postgresql+psycopg://user:pass@host:5432/db"
```

Or use SQLAlchemy's native async support:

```python
# SQLAlchemy 2.0 async with psycopg
DATABASE_URL = "postgresql+asyncpg://user:pass@host:5432/db"  # Keep this - SQLAlchemy handles the driver
```

### 3. Update database.py

**app/database.py** - Minimal changes required:

```python
"""Database engine and session configuration using psycopg3.

Uses lazy initialization to avoid creating the engine at import time.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import settings

Base = declarative_base()


class _EngineFactory:
    """Lazy-initialized engine and session factory wrapper."""

    def __init__(self) -> None:
        self._engine = None
        self._async_session_factory = None

    def get_engine(self):
        """Get or create the async engine (lazy initialization)."""
        if self._engine is None:
            # psycopg3 URL format: postgresql+asyncpg://...
            # SQLAlchemy 2.0 handles psycopg as the async driver
            self._engine = create_async_engine(
                settings.database_url,
                echo=False,
                future=True,
                pool_size=20,
                max_overflow=10,
                pool_timeout=30,
                pool_recycle=1800,
                pool_pre_ping=True,
                # psycopg3-specific options
                pool_options={
                    "min_size": 5,
                    "max_size": 20,
                    "timeout": 30,
                },
            )
        return self._engine

    def get_async_session_factory(self):
        """Get or create the async session factory (lazy initialization)."""
        if self._async_session_factory is None:
            self._async_session_factory = async_sessionmaker(
                self.get_engine(),
                class_=AsyncSession,
                expire_on_commit=False
            )
        return self._async_session_factory


_factory = _EngineFactory()


def get_engine():
    """Get or create the async engine (lazy initialization)."""
    return _factory.get_engine()


def get_async_session_factory():
    """Get or create the async session factory (lazy initialization)."""
    return _factory.get_async_session_factory()


async def get_db():
    """FastAPI dependency that yields an async database session."""
    async with _factory.get_async_session_factory()() as session:
        yield session
```

### 4. Key Differences Between asyncpg and psycopg

| Feature             | asyncpg                      | psycopg3                           |
| ------------------- | ---------------------------- | ---------------------------------- |
| Connection URL      | `postgresql+asyncpg://`      | `postgresql+asyncpg://` (same!)    |
| Prepared Statements | Manual                       | Automatic with caching             |
| Pipeline Mode       | Not supported                | `pipeline()` context manager       |
| Server Cursors      | `query()` with `fetchrows()` | Built-in `cursor()`                |
| COPY Command        | Not supported                | `copy_to()` / `copy_from()`        |
| Binary Data         | Native                       | Native with `binary=True`          |
| Notices             | `get_notices()`              | Automatic via `Connection.notices` |

### 5. Code Changes for psycopg3 Features

**Using Pipeline Mode (batch queries):**

```python
async def batch_insert(session: AsyncSession, records: list[dict]):
    """Insert multiple records efficiently using pipeline mode."""
    async with session.connection() as conn:
        # Enable pipeline mode for batch efficiency
        async with conn.pipeline() as pipeline:
            for record in records:
                await pipeline.execute(
                    insert(DownloadJob).values(**record)
                )
```

**Using Server-Side Cursors (large result sets):**

```python
async def stream_large_results(session: AsyncSession):
    """Stream results using server-side cursor."""
    async with session.connection() as conn:
        cursor = await conn.cursor("my_cursor")
        await cursor.execute("SELECT * FROM large_table")

        # Fetch in chunks
        while True:
            rows = await cursor.fetch(1000)
            if not rows:
                break
            for row in rows:
                yield row
```

### 6. Testing the Migration

```bash
# Install psycopg
pip install psycopg[binary]

# Run tests
hatch run test:all

# Run specific integration tests
hatch run test:integration
```

### 7. Performance Verification

```python
# Benchmark script
import asyncio
import time
from sqlalchemy import text

async def benchmark_queries(session_factory, iterations=100):
    """Benchmark query performance."""
    times = []

    for _ in range(iterations):
        async with session_factory() as session:
            start = time.perf_counter()
            await session.execute(text("SELECT 1"))
            times.append(time.perf_counter() - start)

    avg = sum(times) / len(times)
    print(f"Average query time: {avg*1000:.2f}ms")
    return avg
```

## Rollback Plan

If issues occur:

1. **Revert dependency change:**

   ```toml
   # Back to asyncpg
   "asyncpg>=0.29.0",
   ```

1. **Keep database.py unchanged** - The current implementation works with both drivers

## Current Recommendation

## Status: Deferred

The current `asyncpg` implementation is:

- ✅ Stable and well-tested
- ✅ Fast enough for current workloads
- ✅ Well-integrated with SQLAlchemy 2.0

**Consider migrating when:**

- You need pipeline mode for batch operations
- You need server-side cursors for large datasets
- You need COPY command support
- Performance benchmarks show asyncpg is a bottleneck

The infrastructure is ready - only the driver needs to change in `pyproject.toml` and potentially
`app/config.py` if using direct psycopg URLs.

---

## Quick Reference

```bash
# Install psycopg3
pip install psycopg[binary]

# Verify installation
python -c "import psycopg; print(psycopg.__version__)"

# Test connection
python -c "
import asyncio
import asyncpg
async def test():
    conn = await asyncpg.connect('postgresql://user:pass@localhost:5432/db')
    print(await conn.fetchval('SELECT 1'))
    await conn.close()
asyncio.run(test())
"
```
