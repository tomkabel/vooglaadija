## ADDED Requirements

### Requirement: System SHALL provide a pre-configured Grafana dashboard

A Grafana dashboard JSON model SHALL be provided at `monitoring/grafana-dashboard.json` with exactly 4 panels visible on a single row for projector-friendly presentation. The JSON SHALL include a `__inputs` section with a Prometheus datasource placeholder (`${DS_PROMETHEUS}`) that Grafana prompts for on import.

#### Scenario: Dashboard JSON imports with datasource prompt

- **WHEN** the dashboard JSON is imported into Grafana
- **THEN** it SHALL prompt the user to select a Prometheus datasource for `${DS_PROMETHEUS}`

#### Scenario: Dashboard displays 4 panels after import

- **WHEN** the dashboard JSON is imported and the datasource is configured
- **THEN** it SHALL display 4 panels: Circuit Breaker State, Queue Depth, Error Rate, Recovery Events

### Requirement: Circuit Breaker State panel SHALL show state transitions

The Circuit Breaker State panel SHALL query `ytprocessor_circuit_breaker_state{service="youtube_api"}` — a Prometheus gauge reflecting the current state (CLOSED=0, OPEN=1, HALF_OPEN=2). The panel SHALL use GREEN for CLOSED, RED for OPEN, YELLOW for HALF_OPEN. Panel title font SHALL be 48pt minimum for projector readability.

#### Scenario: Panel shows state changes in real time

- **WHEN** the circuit breaker transitions from CLOSED to OPEN
- **THEN** the panel SHALL change from GREEN to RED within one Prometheus scrape interval (15s)

### Requirement: Queue Depth panel SHALL show Redis queue length

The Queue Depth panel SHALL query `ytprocessor_queue_depth` — the existing Prometheus gauge for Redis queue length (`LLEN download_queue`). The panel SHALL show spikes when worker is unavailable. Panel title font SHALL be 48pt minimum.

#### Scenario: Panel shows queue depth spike

- **WHEN** jobs are submitted but the worker is not processing
- **THEN** the Queue Depth panel SHALL show an increasing value

### Requirement: Error Rate panel SHALL show failed request rate

The Error Rate panel SHALL query `rate(ytprocessor_http_requests_total{status_code=~"5..|429"}[1m])` — filtering the existing HTTP request counter for 5xx and 429 status codes. It SHALL spike on 429 simulation or timeout scenarios. Panel title font SHALL be 48pt minimum.

#### Scenario: Error rate spikes during 429 simulation

- **WHEN** a chaos inject triggers `circuit_breaker_open` (YouTube 429 sim)
- **THEN** the Error Rate panel SHALL show a spike within one Prometheus scrape interval

### Requirement: Recovery Events panel SHALL show recovery counter

The Recovery Events panel SHALL query `increase(ytprocessor_recoveries_total[1m])` — tracking recovery events per minute, broken down by `reason` label. It SHALL increment on each zombie sweeper recovery (reason: `zombie_sweep_recovery`) or circuit breaker half-open transition (reason: `circuit_breaker_recovery`). Panel title font SHALL be 48pt minimum.

#### Scenario: Recovery counter increments during demo

- **WHEN** the zombie sweeper reclaims a job
- **THEN** `ytprocessor_recoveries_total{reason="zombie_sweep_recovery"}` SHALL increment by 1

#### Scenario: Circuit breaker recovery visible on dashboard

- **WHEN** the circuit breaker transitions from OPEN → HALF_OPEN
- **THEN** `ytprocessor_recoveries_total{reason="circuit_breaker_recovery"}` SHALL increment by 1
