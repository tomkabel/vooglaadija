"""Queue seam: protocol + in-memory implementation for offline contract testing.

The production queue is ``core.queue`` (Redis-backed, module-level functions).
This seam exists so the app->worker payload contract (producers lpush
``str(job_id)`` on ``download_queue``; consumers rpop and normalize to UUID via
``worker.job_claimer.normalize_job_id``) can be verified in unit tests in
<100ms with no Redis instance (Feathers: architectural seam).
"""
