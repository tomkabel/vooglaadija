## Why

In the final demo, every team shows the happy path — their app working normally. No team demonstrates resilience under failure. Vooglaadija already has battle-tested resilience patterns (circuit breaker, zombie sweeper, transactional outbox, graceful shutdown), but they are invisible during a live demo. We need a controlled, demo-safe way to trigger failure scenarios and project the system's recovery onto a Grafana dashboard. This creates a unique narrative: "We built it to survive what kills other apps."

## What Changes

- **New Chaos Injection API** (`POST /api/v1/chaos/inject`): Controlled endpoint that sets scenario flags in Redis, triggering real production code paths (circuit breaker open, worker crash simulation, DB failover simulation)
- **New Chaos Lab UI page** (`/web/chaos-lab`): Hidden HTMX page with 3 buttons for triggering scenarios during the demo
- **Modify** `services/circuit_breaker.py`: Add Redis-backed chaos override check so circuit breaker can be forced OPEN
- **Modify** `worker/processor.py`: Add chaos trigger so zombie sweeper can detect artificially orphaned jobs
- **New Grafana dashboard JSON** (`monitoring/grafana-dashboard.json`): 4-panel dashboard (Circuit Breaker State, Queue Depth, Error Rate, Recovery Events) pre-configured for the demo with `${DS_PROMETHEUS}` datasource input
- **Modify** `app/main.py`: Conditionally register chaos router based on `FEATURE_CHAOS_API_ENABLED` flag
- **New** `FEATURE_CHAOS_API_ENABLED` environment variable: `false` in production, `true` in demo/docker-compose override — follows existing `FEATURE_*_ENABLED` convention

## Capabilities

### New Capabilities

- `chaos-injection`: Controlled failure simulation via HTTP API — circuit breaker open, worker crash, DB failover scenarios with configurable duration
- `chaos-lab-ui`: Hidden web page with HTMX buttons to trigger chaos scenarios during live demo
- `chaos-dashboard`: 4-panel Grafana dashboard showing circuit breaker state transitions, queue depth spikes, error rate spikes, and recovery event counters

### Modified Capabilities

- (No existing specs — this is a fresh capability)

## Impact

- **New dependency:** Redis key namespace `chaos:*` for scenario flags (no new infrastructure)
- **API surface:** `POST /api/v1/chaos/inject`, `POST /api/v1/chaos/reset`, `POST /api/v1/chaos/status`
- **Code changes:** ~60 lines new route, ~20 lines in circuit breaker, ~15 lines in processor
- **Gating:** `FEATURE_CHAOS_API_ENABLED=false` blocks all chaos routes in production — physically unreachable
- **Security:** Chaos endpoints not authenticated (design choice for demo speed), but fully gated by env var
- **Monitoring:** New Prometheus metrics: `ytprocessor_recoveries_total` (counter), `ytprocessor_circuit_breaker_state` (gauge with state label)
