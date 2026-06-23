## Why

Every standout team in the competitive analysis had an AI component. A bolted-on AI (content summary) would dilute the core resilience narrative. Instead, the AI integration is native to the story: predicting throttling before it happens. The throttle predictor counts 429 responses in a sliding window and flags pre-throttle state, allowing the system to pre-emptively rotate endpoints or slow request rate. This is demonstrable during the demo and signals AI-native architecture thinking.

## What Changes

- **New service** `app/services/throttle_predictor.py`: Sliding window counter that tracks HTTP 429 responses over a configurable time window (default 60s). Detection is via **yt-dlp stderr pattern matching** (yt-dlp is a subprocess and does not expose raw HTTP status codes)
- **Integration** with `yt_dlp_service.py`: After each extraction, parse yt-dlp's stderr output for pattern `HTTP Error 429`; if matched, increment the throttle counter
- **New Prometheus gauge** `ytprocessor_throttle_risk_score`: Exposes current throttle risk (0.0–1.0) for Grafana visualization — follows existing `ytprocessor_` metric naming prefix
- **New Redis sorted set** `throttle:window:<service>`: Stores Unix timestamps of recent 429 events; uses ZREMRANGEBYSCORE + ZCARD for efficient window maintenance
- **New 4th button** on chaos-lab UI (requires `chaos-injection-api` change): "Simulate 429 Spike" to rapidly fill the window and trigger pre-throttle state
- **Logging:** Structured warnings when throttle risk exceeds configurable threshold (default 0.7)

## Capabilities

### New Capabilities

- `throttle-prediction`: Sliding window 429 counter with configurable time window (default 60s), throttle risk score (0.0–1.0), and pre-throttle warning at configurable threshold (default 0.7)

### Modified Capabilities

- (No existing specs — this is a fresh capability)

## Impact

- **New file:** `app/services/throttle_predictor.py` (~80 lines)
- **Modified file:** `app/services/yt_dlp_service.py` (parse stderr for "HTTP Error 429" after extraction, call throttle predictor)
- **New Prometheus metric:** `ytprocessor_throttle_risk_score` gauge with `service` and `provider` labels (follows `ytprocessor_` prefix convention)
- **New Redis keyspace:** `throttle:*` namespace (sorted sets, not hashes)
- **New environment variable:** `THROTTLE_WINDOW_SECONDS` (default 60, max events to keep), `THROTTLE_RISK_THRESHOLD` (default 0.7, warning threshold)
- **No external AI API dependency** — purely local statistical model (sliding window counter)
- **Cross-change dependency:** Requires `chaos-injection-api` for the `throttle_spike` chaos scenario and the 4th chaos-lab button
