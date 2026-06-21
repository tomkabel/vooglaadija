# Code Boundaries

This project uses `core/` as the shared infrastructure package for code that must be used by both the API process and the worker process.

## Ownership Rules

- `core/` owns shared infrastructure: configuration, database setup, ORM models, metrics, Redis clients, queue helpers, logging, and base utilities.
- `core/` must not import from `app/` or `worker/`.
- `app/` may import from `core/` and internal `app/` modules.
- `app/` must not import from `worker/`.
- `worker/` may import from `core/`, internal `worker/` modules, and API-independent modules under `app/services/`.
- `worker/` must not import `app/api/`, `app/schemas/`, route dependencies, web-specific modules, ORM model shims, config shims, database shims, metrics shims, logging shims, or Redis-client shims.

## Removed Compatibility Shims

Story 1.6 removed the temporary re-export shims that existed during the `core/` extraction:

- `app/config.py`
- `app/database.py`
- `app/metrics.py`
- `app/logging_config.py`
- `app/services/redis_client.py`
- `app/models/__init__.py`

Do not recreate these modules. New and existing code must use the canonical `core.*` modules directly.

Run the boundary verifier before review:

```bash
python scripts/import_analysis.py
```
