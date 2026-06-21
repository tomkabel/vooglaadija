# Structlog Integration Guide

This document shows how to migrate from standard logging to structlog for better observability.

## Quick Start

### Before (Standard Logging)

```python
import logging

logger = logging.getLogger(__name__)

# Basic logging - use extra parameter for structured data
logger.info("User logged in", extra={"user_id": 123})
logger.warning("High memory usage", extra={"memory_percent": 85})
logger.error("Database connection failed", extra={"error": "timeout"})

# With exception
try:
    risky_operation()
except Exception as e:
    logger.exception("Operation failed: %s", e)
```

### After (Structlog)

```python
from app.logging_config import get_logger

logger = get_logger(__name__)

# Basic logging - automatically includes context
logger.info("user_login", user_id=123)
logger.warning("high_memory_usage", memory_percent=85)
logger.error("database_connection_failed", error="timeout")

# With exception - structlog handles this automatically
try:
    risky_operation()
except Exception as e:
    logger.exception("operation_failed", error=str(e))
```

## Key Differences

| Standard Logging                     | Structlog                                 |
| ------------------------------------ | ----------------------------------------- |
| `logger.info("msg", a=1)`            | `logger.info("msg", a=1)` (same!)         |
| `logger.warning("High CPU", cpu=90)` | `logger.warning("high_cpu", cpu=90)`      |
| `logger.exception(e)`                | `logger.exception("error", error=str(e))` |
| JSON in production only              | JSON in production, pretty in dev         |

## Structured Logging Benefits

### 1. JSON Output (Production)

```json
{
  "event": "job_completed",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "duration_ms": 2345,
  "timestamp": "2026-04-15T10:30:00.000Z",
  "service": "vooglaadija",
  "level": "info"
}
```

### 2. Human-Readable Output (Development)

```text
2026-04-15 10:30:00.123 | INFO     | app.services.yt_dlp_service | job_completed | job_id=550e8400... duration_ms=2345
```

## Usage Patterns

### With Context (Context Variables)

```python
from app.logging_config import get_logger

# Bind context that will be included in all subsequent logs
logger = get_logger(__name__, user_id=user.id, request_id=request_id)

logger.info("processing_request")  # Includes user_id, request_id automatically
logger.info("database_query", query="SELECT * FROM users")  # Plus query
```

### In FastAPI Dependencies

```python
from fastapi import Request
from app.logging_config import get_logger

async def some_endpoint(request: Request):
    # Get logger with request context
    logger = get_logger(__name__, request_id=request.state.request_id)
    logger.info("endpoint_called", path=request.url.path)
```

### In Worker Jobs

```python
from app.logging_config import get_logger

async def process_download(job_id: UUID):
    logger = get_logger(__name__, job_id=str(job_id))

    logger.info("starting_download", url=job.url)
    try:
        result = await download_and_process(job)
        logger.info("download_complete", file_size=result.size)
    except Exception as e:
        logger.error("download_failed", error=str(e))
```

## Best Practices

### 1. Use kebab-case for Event Names

```python
# Good
logger.info("user-registration-complete", user_id=123)
logger.warning("queue-depth-high", depth=500)

# Avoid
logger.info("UserRegistrationComplete")
logger.info("User registration complete")
```

### 2. Include Relevant Context

```python
# Good - includes all relevant context
logger.info("job_completed", job_id=job.id, duration_ms=234, file_size=1024)

# Avoid - missing context
logger.info("Job completed")
```

### 3. Use Appropriate Log Levels

| Level    | When to Use                                      |
| -------- | ------------------------------------------------ |
| DEBUG    | Detailed debugging info, enter/exit of functions |
| INFO     | Normal operations, business events               |
| WARNING  | Something unexpected but handled                 |
| ERROR    | Operation failed but app continues               |
| CRITICAL | App is going down                                |

### 4. Sensitive Data Handling

```python
# Don't log sensitive data
logger.info("user_login", user_id=123)  # Good
logger.info("user_login", password="secret")  # BAD!

# If you must log sensitive data, mask it
def mask_sensitive(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return value[:2] + "****" + value[-2:]

logger.info("api_call", api_key=mask_sensitive(api_key))
```

## Migration Checklist

- [ ] Update imports from `logging` to `app.logging_config`
- [ ] Change `logging.getLogger(__name__)` to `get_logger(__name__)`
- [ ] Convert log message formatting to structured fields
- [ ] Remove string interpolation from log messages
- [ ] Update exception handling to use `logger.exception()`
- [ ] Add relevant context variables (user_id, request_id, job_id)
- [ ] Update tests to capture structured logs
- [ ] Verify JSON output in production logs

## Testing with Structlog

```python
import pytest
from structlog.testing import CapturingLogger

def test_job_processing():
    # Using structlog's testing utilities
    captured = []

    def capture_event(logger, method_name, event_dict):
        captured.append(event_dict)

    # Configure test logger
    import structlog
    structlog.configure(
        processors=[capture_event],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
    )

    # ... run test ...

    assert any(e["event"] == "job_completed" for e in captured)
```

## Integration with Other Tools

### Sentry (Error Tracking)

Structlog integrates with Sentry automatically when using `structlog.stdlib.BoundLogger`:

```python
try:
    risky_operation()
except Exception:
    # Sentry captures this automatically with structured context
    logger.exception("operation_failed", operation="risky")
```

### Prometheus (Metrics)

Use structured context for better metric labels:

```python
# Good - structured labels
logger.info("api_request_complete",
            endpoint="/api/v1/downloads",
            status_code=200,
            duration_ms=45)

# Prometheus can scrape structured logs for metrics
```

## Configuration Reference

See `app/logging_config.py` for full configuration options:

- `ENVIRONMENT`: Switches between production (JSON) and development (pretty)
- `LOG_LEVEL`: Controls minimum log level
- `LOG_FORMAT`: Additional format options

## Performance Considerations

Structlog is optimized for production use:

- JSON rendering adds ~0.1ms overhead per log call
- Context binding is cached efficiently
- Conditional rendering (only JSON in production)
- Compatible with standard library logging performance

For most applications, structlog overhead is negligible compared to I/O operations.
