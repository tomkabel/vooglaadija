## 1. Chaos Injection API Route

- [x] 1.1 Create `app/api/routes/chaos.py` with 3 endpoints: `POST /api/v1/chaos/inject`, `POST /api/v1/chaos/reset`, `GET /api/v1/chaos/status`
- [x] 1.2 Implement Redis key management: set TTL keys under `chaos:*` namespace on inject, delete all on reset
- [x] 1.3 Implement status endpoint that returns active/inactive state for each scenario

## 2. Circuit Breaker Chaos Override

- [x] 2.1 Add chaos flag check in `app/services/circuit_breaker.py` — check `chaos:circuit_breaker_override` Redis key inside `can_execute()`. Implement local TTL cache (1s) to avoid Redis round-trip on every call. Cache is bypassed if chaos flag was previously active (poll every call until cleared).
- [x] 2.2 Modify `can_execute()` to return `False` (OPEN) if chaos override is active, regardless of actual failure count
- [x] 2.3 Add `ytprocessor_recoveries_total{reason="circuit_breaker_recovery"}` counter increment on OPEN→HALF_OPEN transition
- [x] 2.4 Add `ytprocessor_circuit_breaker_state{service="youtube_api"}` gauge that reflects current state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)

## 3. Worker DB Failover + Zombie Sweep Triggers

- [x] 3.0 Add `chaos:db_failover` check in `worker/processor.py` — if key exists after job claim, raise `sqlalchemy.exc.OperationalError("could not connect to server")` to trigger retry chain

## 4. Worker Zombie Sweep Trigger

- [x] 4.1 Modify `worker/processor.py` `process_next_job()` to check `chaos:zombie_job_trigger` Redis key
- [x] 4.2 When chaos trigger active, skip completion and leave job in "processing" state for zombie sweeper to reclaim
- [x] 4.3 Add `ytprocessor_recoveries_total{reason="zombie_sweep_recovery"}` counter increment in `reset_stuck_jobs()` when reclaiming orphaned jobs

## 5. Chaos Lab UI Page

- [x] 5.1 Create Jinja2 template for `/web/chaos-lab` with 4 scenario buttons (SIMULATE YOUTUBE 429, SIMULATE WORKER CRASH, SIMULATE DB FAILOVER, SIMULATE 429 SPIKE) and current status display
- [x] 5.2 Wire HTMX buttons to POST to `/api/v1/chaos/inject` with correct scenario payloads
- [x] 5.3 Add HTMX polling (5s interval) for real-time status updates on chaos flags

## 6. Router Registration and Feature Flag

- [x] 6.1 Add `feature_chaos_api_enabled: bool = False` to `app/config.py` settings model (follows existing `FEATURE_*_ENABLED` convention)
- [x] 6.2 Conditionally register chaos router in `app/main.py` — only when `settings.feature_chaos_api_enabled` is True
- [x] 6.3 Add `FEATURE_CHAOS_API_ENABLED=false` to `docker-compose.yml` `x-common-env` section as default
- [x] 6.4 Add `FEATURE_CHAOS_API_ENABLED=true` to `.env.example` with comment for demo usage

## 7. Grafana Dashboard

- [x] 7.1 Create `monitoring/grafana-dashboard.json` with 4 panels: Circuit Breaker State, Queue Depth, Error Rate, Recovery Events
- [x] 7.2 Configure panel queries for Prometheus metrics with 48pt title fonts and GREEN/RED color convention
- [x] 7.4 Ensure dashboard JSON includes `__inputs` section with `${DS_PROMETHEUS}` datasource template variable

## 8. Cross-Change Dependency: Docker Compose Integration

- [x] 8.1 Create `docker-compose.demo.yml` override that sets `FEATURE_CHAOS_API_ENABLED=true` and runs `scripts/seed_demo_data.py` on startup
- [x] 8.2 Add `.env.example` entry: `FEATURE_CHAOS_API_ENABLED=false` with comment explaining it must be `true` for demo

## 9. Tests

- [x] 9.1 Write unit tests for chaos API route (inject, reset, status)
- [x] 9.2 Write unit tests for circuit breaker chaos override with local TTL cache
- [x] 9.3 Write unit tests for worker db_failover trigger (retry chain)
- [x] 9.4 Write unit tests for worker zombie sweep trigger (orphan job + reclaim)
- [x] 9.5 Write integration test for full circuit breaker chaos flow: inject→CB opens→CB recovers
- [x] 9.6 Write integration test for full zombie sweep flow: inject worker_crash→job orphaned→zombie sweeper reclaims→ytprocessor_recoveries_total increments
