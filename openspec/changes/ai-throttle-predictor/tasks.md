## 1. Throttle Predictor Service

- [x] 1.1 Create `app/services/throttle_predictor.py` with sliding window logic using Redis sorted sets (ZREMRANGEBYSCORE + ZCARD)
- [x] 1.2 Implement `record_response(service: str, status_code: int)` — records Unix timestamp to sorted set `throttle:window:<service>`, prunes entries older than `THROTTLE_WINDOW_SECONDS`
- [x] 1.3 Implement `get_risk_score(service: str) -> float` — computes `min(cardinality / MAX_EXPECTED_429S, 1.0)` where MAX_EXPECTED_429S defaults to 10
- [x] 1.4 Implement `risk_check_and_warn(service: str)` — logs structlog warning `throttle_risk_high` if risk exceeds `THROTTLE_RISK_THRESHOLD` (default 0.7)

## 2. Prometheus Metric

- [x] 2.1 Add `ytprocessor_throttle_risk_score` Gauge metric in `app/metrics.py` with `service` and `provider` labels (follows existing `ytprocessor_` prefix convention)
- [x] 2.2 Wire throttle predictor to update `ytprocessor_throttle_risk_score` gauge on each `record_response()` call
- [x] 2.3 Verify metric appears on `/metrics` endpoint as `ytprocessor_throttle_risk_score{service="youtube",provider="yt-dlp"}`

## 3. yt_dlp_service Integration

- [x] 3.1 Modify `app/services/yt_dlp_service.py` to capture yt-dlp stderr output after each extraction
- [x] 3.2 Parse stderr with regex `HTTP Error 429` (case-insensitive) — yt-dlp does not expose raw HTTP status codes; 429 detection must happen via stderr pattern matching
- [x] 3.3 If 429 pattern matched, call `throttle_predictor.record_response("youtube", 429)`; otherwise, no-op

## 4. Chaos Integration ([requires chaos-injection-api])

- [x] 4.1 Add `throttle_spike` scenario handler to chaos injection — when triggered, adds 15 429 timestamps to the sliding window (pushes `ytprocessor_throttle_risk_score` to 1.0 with default settings)
- [x] 4.2 Wire "SIMULATE 429 SPIKE" button on chaos-lab UI to `{"scenario": "throttle_spike", "duration_seconds": 30}`

## 5. Environment Variables

- [x] 5.1 Add `THROTTLE_WINDOW_SECONDS=60` and `THROTTLE_RISK_THRESHOLD_SCALE=10` and `THROTTLE_RISK_THRESHOLD=0.7` to `app/config.py` settings model
- [x] 5.2 Add entries to `.env.example`

## 6. Tests

- [x] 6.1 Write unit tests for sliding window recording, pruning, and TTL-based expiration
- [x] 6.2 Write unit tests for risk score computation at 0/5/10/15 counts (0.0, 0.5, 1.0, 1.0 capped)
- [x] 6.3 Write test for warning log emission at threshold exceedance
- [x] 6.4 Write unit test for yt-dlp stderr 429 pattern matching (mock stderr containing "HTTP Error 429")
- [x] 6.5 Write unit test confirming non-429 stderr (e.g., "HTTP Error 403") does not trigger counter
- [x] 6.6 Write integration test for throttle spike scenario (requires chaos-injection-api runtime)
