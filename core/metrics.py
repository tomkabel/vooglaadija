"""Prometheus metrics shared by API and worker processes."""

from prometheus_client import Counter, Gauge, Histogram, Info

APP_INFO = Info("ytprocessor", "YouTube media processor")

JOBS_CREATED = Counter(
    "ytprocessor_jobs_created_total",
    "Total number of download jobs created",
    ["status"],
)

JOBS_COMPLETED = Counter(
    "ytprocessor_jobs_completed_total",
    "Total number of download jobs completed",
    ["status"],
)

JOB_DURATION_SECONDS = Histogram(
    "ytprocessor_job_duration_seconds",
    "Time spent processing a job",
    buckets=[2, 5, 10, 30, 60, 120, 300, 600],
)

QUEUE_DEPTH = Gauge(
    "ytprocessor_queue_depth",
    "Number of jobs waiting in the queue",
)

OUTBOX_PENDING = Gauge(
    "ytprocessor_outbox_pending",
    "Number of pending outbox entries",
)

HTTP_REQUESTS = Counter(
    "ytprocessor_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "ytprocessor_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
)

WORKER_STATUS = Gauge(
    "ytprocessor_worker_status",
    "Worker health status (1=healthy, 0=unhealthy)",
)

DOWNLOAD_SIZE_BYTES = Histogram(
    "ytprocessor_download_size_bytes",
    "Size of downloaded files in bytes",
    buckets=[1e6, 5e6, 10e6, 50e6, 100e6, 500e6],
)

RECOVERIES = Counter(
    "ytprocessor_recoveries_total",
    "Total recovery events",
    ["reason"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "ytprocessor_circuit_breaker_state",
    "Current circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)",
    ["service"],
)

THROTTLE_RISK_SCORE = Gauge(
    "ytprocessor_throttle_risk_score",
    "Current throttle risk score (0.0-1.0)",
    ["service", "provider"],
)

RETRIES_TOTAL = Counter(
    "ytprocessor_retries_total",
    "Total number of job retries by error category",
    ["category"],
)

ERROR_CLASSIFICATION = Counter(
    "ytprocessor_error_classification_total",
    "Error classification breakdown",
    ["category"],
)

DLQ_DEPTH = Gauge(
    "ytprocessor_dlq_depth",
    "Number of entries in the dead letter queue (failed_jobs table)",
)

CIRCUIT_DEFERRED_DEPTH = Gauge(
    "ytprocessor_circuit_deferred_depth",
    "Number of jobs deferred due to open circuit breaker",
)

RETRY_BUDGET_RATIO = Gauge(
    "ytprocessor_retry_budget_ratio",
    "Current retry budget ratio (retries / total requests)",
)


def init_metrics() -> None:
    """Initialize application metrics.

    Prometheus requires at least one observation for a labeled counter/gauge
    to appear in the /metrics output. Zero-value calls below ensure every
    metric label combination used in Grafana panels is visible from startup,
    preventing "No data" before the first real event occurs.

    Gauges without labels (QUEUE_DEPTH, WORKER_STATUS, OUTBOX_PENDING) and
    Histograms without labels (JOB_DURATION_SECONDS, DOWNLOAD_SIZE_BYTES)
    auto-register with value 0 at module level and do NOT need init here.
    """
    APP_INFO.info({"version": "1.0.0", "service": "ytprocessor"})

    # -- Label-parameterized COUNTERS --
    JOBS_COMPLETED.labels(status="success").inc(0)
    JOBS_COMPLETED.labels(status="failed").inc(0)
    JOBS_COMPLETED.labels(status="deferred").inc(0)
    RECOVERIES.labels(reason="circuit_breaker_recovery").inc(0)
    RECOVERIES.labels(reason="zombie_sweep_recovery").inc(0)

    # -- Label-parameterized GAUGES --
    # CIRCUIT_BREAKER_STATE is also set to 0 in CircuitBreaker.__init__, but
    # the singleton is lazily constructed (only on first job processing).
    # Initialize here so the gauge exists from API server startup regardless
    # of whether the circuit breaker is ever constructed.
    CIRCUIT_BREAKER_STATE.labels(service="youtube_api").set(0)

    # THROTTLE_RISK_SCORE only appears after the throttle predictor runs
    # during job processing. Initialize here for immediate dashboard visibility.
    THROTTLE_RISK_SCORE.labels(service="youtube", provider="youtube").set(0)

    # New resilience metrics
    for cat in [
        "rate_limited",
        "transient",
        "blocked",
        "not_found",
        "format_unavailable",
        "timeout",
        "storage",
        "unknown",
    ]:
        RETRIES_TOTAL.labels(category=cat).inc(0)
        ERROR_CLASSIFICATION.labels(category=cat).inc(0)
    DLQ_DEPTH.set(0)
    CIRCUIT_DEFERRED_DEPTH.set(0)
    RETRY_BUDGET_RATIO.set(0)


__all__ = [
    "APP_INFO",
    "CIRCUIT_BREAKER_STATE",
    "CIRCUIT_DEFERRED_DEPTH",
    "DLQ_DEPTH",
    "DOWNLOAD_SIZE_BYTES",
    "ERROR_CLASSIFICATION",
    "HTTP_REQUESTS",
    "HTTP_REQUEST_DURATION",
    "JOBS_COMPLETED",
    "JOBS_CREATED",
    "JOB_DURATION_SECONDS",
    "OUTBOX_PENDING",
    "QUEUE_DEPTH",
    "RECOVERIES",
    "RETRIES_TOTAL",
    "RETRY_BUDGET_RATIO",
    "THROTTLE_RISK_SCORE",
    "WORKER_STATUS",
    "init_metrics",
]
