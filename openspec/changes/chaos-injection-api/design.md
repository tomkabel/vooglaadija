## Context

Vooglaadija has production-grade resilience patterns: circuit breaker (in-memory state machine with configurable thresholds), zombie sweeper (`reset_stuck_jobs` in worker), transactional outbox (`sync_outbox_to_queue`), and graceful shutdown with 25s grace period. These are invisible during a live demo. The audience sees only happy-path behavior. We need a mechanism to trigger these code paths on demand during a 3-minute demo slot — without Docker chaos (too risky) and without faking it with a video.

The solution is a Chaos Injection API that sets Redis flags checked by the existing service layer. All triggered code paths are real production code.

## Goals / Non-Goals

**Goals:**

- Demonstrate circuit breaker CLOSED→OPEN→HALF_OPEN→CLOSED transition in real time
- Demonstrate zombie sweeper detecting and recovering an orphaned job
- Make all resilience patterns visible on a 4-panel Grafana dashboard
- Keep the demo 100% safe — no Docker daemon risk, no unrecoverable state
- Gate chaos endpoints behind `FEATURE_CHAOS_API_ENABLED` env var (following existing `FEATURE_*_ENABLED` convention) — `false` in production makes router physically unreachable

**Non-Goals:**

- Not a chaos engineering framework — only 3 specific scenarios for the demo script
- No new failure modes — only triggers existing code paths
- No anonymous auth — uses existing user model for the demo account
- No permanent changes to production behavior — feature flag gated

## Decisions

1. **Redis-backed flags with local TTL cache**: The chaos flags are read by both the API process and the worker process (different containers). Redis is the shared state layer already in the stack. To avoid a Redis round-trip on every circuit breaker check (hot path), the chaos override check uses a **local TTL cache** (1s expiry). When the cache is cold, Redis is queried; when warm, the cached result is used. This keeps latency overhead to at most 1 Redis call per second per process. Decision: Redis TTL keys for automatic cleanup + local in-memory cache for hot-path performance.

1. **Feature flag over auth gating** ([existing convention](docker-compose.yml:28-29)): Chaos endpoints are unauthenticated to keep the demo fast (no token passing in a presenter's background tab). Instead, `FEATURE_CHAOS_API_ENABLED=false` in production makes the router unreachable — following the existing `FEATURE_METRICS_ENABLED` / `FEATURE_TRACING_ENABLED` naming convention. Alternative: JWT auth with admin role — adds demo friction. Decision: env var gate with clear documentation.

1. **Four scenario slots, not seven**: The strategy analysis rejected 7 events in 60s as impossible, but 4 scenario slots fit the expanded narrative. The chaos API accepts any scenario string and sets a corresponding Redis key — it is intentionally unvalidated. Three scenarios are defined in this change: `circuit_breaker_open` (429 sim), `worker_crash` (zombie sweep), `db_failover` (connection error → retry chain). A fourth scenario `throttle_spike` is handled by the `ai-throttle-predictor` change, which reads the same `chaos:throttle_spike` Redis key set by this API. No hard dependency: the API sets keys, the service layer reads them.

1. **Grafana over CLI/docker output**: Grafana is already in the stack (docker-compose), supports 48pt fonts readable from the back row, and color-codes the GREEN→RED→GREEN story. Alternative: terminal output — not visible to audience. Decision: Grafana with pre-configured dashboard JSON export.

## Risks / Trade-offs

- [Flag not cleaned up] If chaos reset fails, CB stays forced open. → Mitigation: Redis TTL on the key (default matches `duration_seconds`). `/api/v1/chaos/reset` as first demo step. Local cache TTL ensures stale flags are rechecked within 1s.
- [New endpoint not reviewed] Chaos API bypasses normal auth. → Mitigation: `FEATURE_CHAOS_API_ENABLED=false` in production. Only reachable in dev/demo docker-compose profile.
- [Demo audience might not see Grafana] Projector resolution, seating distance. → Mitigation: 4 massive panels, 48pt title fonts, red/green color only, tested on actual projector before demo.
