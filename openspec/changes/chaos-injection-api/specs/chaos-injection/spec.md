## ADDED Requirements

### Requirement: System SHALL support chaos injection via HTTP API

The system SHALL provide a `POST /api/v1/chaos/inject` endpoint that accepts a `scenario` (string) and `duration_seconds` (integer, default 30). The endpoint SHALL set a Redis key under the `chaos:` namespace with a TTL matching `duration_seconds`.

#### Scenario: Inject circuit_breaker_open scenario

- **WHEN** a POST request is sent to `/api/v1/chaos/inject` with body `{"scenario": "circuit_breaker_open", "duration_seconds": 30}`
- **THEN** the system SHALL set Redis key `chaos:circuit_breaker_override` with value `1` and TTL 30 seconds

#### Scenario: Inject worker_crash scenario

- **WHEN** a POST request is sent to `/api/v1/chaos/inject` with body `{"scenario": "worker_crash", "duration_seconds": 30}`
- **THEN** the system SHALL set Redis key `chaos:zombie_job_trigger` with value `1` and TTL 30 seconds

#### Scenario: Inject db_failover scenario

- **WHEN** a POST request is sent to `/api/v1/chaos/inject` with body `{"scenario": "db_failover", "duration_seconds": 30}`
- **THEN** the system SHALL set Redis key `chaos:db_failover` with value `1` and TTL 30 seconds

#### Scenario: Inject throttle_spike scenario (handled by ai-throttle-predictor)

- **WHEN** a POST request is sent to `/api/v1/chaos/inject` with body `{"scenario": "throttle_spike", "duration_seconds": 30}`
- **THEN** the system SHALL set Redis key `chaos:throttle_spike` with value `1` and TTL 30 seconds
- **NOTE:** This scenario is consumed by the throttle predictor service in the `ai-throttle-predictor` change, not by this change's code

### Requirement: System SHALL provide chaos reset endpoint

The system SHALL provide a `POST /api/v1/chaos/reset` endpoint that deletes all keys under the `chaos:*` namespace.

#### Scenario: Reset all chaos flags

- **WHEN** a POST request is sent to `/api/v1/chaos/reset`
- **THEN** the system SHALL delete all Redis keys matching `chaos:*`

### Requirement: System SHALL provide chaos status endpoint

The system SHALL provide a `GET /api/v1/chaos/status` endpoint that returns the current state of all chaos flags.

#### Scenario: Get chaos status

- **WHEN** a GET request is sent to `/api/v1/chaos/status`
- **THEN** the system SHALL return a JSON object with each scenario name as key and a boolean indicating if the flag is active

### Requirement: Circuit breaker SHALL check chaos override with local TTL cache

The `can_execute()` method SHALL check for `chaos:circuit_breaker_override` Redis key. To avoid Redis latency on every call, the result SHALL be cached locally with a 1-second TTL. If the key exists, the circuit breaker SHALL report its state as OPEN regardless of actual failure count.

#### Scenario: Chaos override forces circuit breaker open

- **WHEN** Redis key `chaos:circuit_breaker_override` exists and `can_execute()` is called
- **THEN** the circuit breaker SHALL return `False` from `can_execute()`

#### Scenario: Chaos override removed, circuit breaker returns to real state within 1s

- **WHEN** Redis key `chaos:circuit_breaker_override` is deleted (TTL expired or reset)
- **THEN** the circuit breaker SHALL return to its actual state based on failure count within 1 second

### Requirement: Worker processor SHALL simulate DB failover via retry chain

When `chaos:db_failover` Redis key exists, `process_next_job()` SHALL raise a transient `OperationalError` after claiming the job. This exercises the existing retry chain in the `except Exception` handler: the job SHALL be requeued with exponential backoff + jitter via the outbox pattern, demonstrating crash-safe recovery.

#### Scenario: DB failover triggers retry

- **WHEN** Redis key `chaos:db_failover` exists and `process_next_job()` has claimed a job
- **THEN** the processor SHALL raise `sqlalchemy.exc.OperationalError("could not connect to server")`

#### Scenario: Retry chain fires on db_failover

- **WHEN** an `OperationalError` is raised due to db_failover chaos
- **THEN** the existing retry logic SHALL increment `retry_count` and schedule a retry via outbox (same code path as any transient DB failure)

### Requirement: Worker zombie sweeper SHALL respond to chaos trigger

The `process_next_job()` function SHALL check for `chaos:zombie_job_trigger` Redis key before marking a job complete. If the key exists, the function SHALL skip the completion update, leaving the job in "processing" state. On the next `reset_stuck_jobs()` tick, the zombie sweeper SHALL detect the orphaned job and reclaim it, demonstrating automatic recovery.

#### Scenario: Chaos trigger creates orphaned job

- **WHEN** Redis key `chaos:zombie_job_trigger` exists and `process_next_job()` is about to mark a job complete
- **THEN** the processor SHALL skip the status update, leaving the job in "processing" state

#### Scenario: Zombie sweeper reclaims orphaned job

- **WHEN** `reset_stuck_jobs()` runs and finds a job stuck in "processing" that matches the chaos orphan timeout
- **THEN** it SHALL mark the job as "failed" with error "Chaos simulation: worker crash" and increment `ytprocessor_recoveries_total`

### Requirement: Chaos routes SHALL be gated by FEATURE_CHAOS_API_ENABLED env var

The chaos router SHALL only be registered when `FEATURE_CHAOS_API_ENABLED` is `true`. This follows the existing `FEATURE_METRICS_ENABLED` / `FEATURE_TRACING_ENABLED` naming convention (positive boolean). In production, `FEATURE_CHAOS_API_ENABLED=false` SHALL make the router unregistered (routes return 404).

#### Scenario: Chaos routes disabled in production

- **WHEN** `FEATURE_CHAOS_API_ENABLED=false` and any chaos endpoint is requested
- **THEN** the API SHALL return 404 Not Found

#### Scenario: Chaos routes enabled in demo

- **WHEN** `FEATURE_CHAOS_API_ENABLED=true` and a chaos endpoint is requested
- **THEN** the API SHALL process the request normally

### Requirement: Recovery events SHALL be exposed as Prometheus counter

The system SHALL expose `ytprocessor_recoveries_total` as a Prometheus Counter with `reason` label (`circuit_breaker_recovery` / `zombie_sweep_recovery`). It SHALL increment each time the zombie sweeper reclaims a job or the circuit breaker transitions from OPEN → HALF_OPEN.

#### Scenario: Circuit breaker recovery increments counter

- **WHEN** the circuit breaker transitions from OPEN → HALF_OPEN
- **THEN** `ytprocessor_recoveries_total{reason="circuit_breaker_recovery"}` SHALL increment

#### Scenario: Zombie sweeper recovery increments counter

- **WHEN** the zombie sweeper reclaims an orphaned job
- **THEN** `ytprocessor_recoveries_total{reason="zombie_sweep_recovery"}` SHALL increment

### Requirement: Circuit breaker state SHALL be exposed as Prometheus gauge

The system SHALL expose `ytprocessor_circuit_breaker_state` as a Prometheus Gauge with values: CLOSED=0, OPEN=1, HALF_OPEN=2. The gauge SHALL have a `service` label (e.g., `"youtube_api"`). This enables the Grafana panel to visualize state transitions in real time.

#### Scenario: State gauge reflects current circuit breaker state

- **WHEN** the circuit breaker is in CLOSED state
- **THEN** `ytprocessor_circuit_breaker_state{service="youtube_api"}` SHALL be 0
- **WHEN** the circuit breaker transitions to OPEN
- **THEN** `ytprocessor_circuit_breaker_state{service="youtube_api"}` SHALL be 1
