## ADDED Requirements

### Requirement: System SHALL detect 429 responses from yt-dlp stderr

The system SHALL detect HTTP 429 (rate limit) responses from YouTube by parsing yt-dlp's stderr output. yt-dlp runs as a subprocess and outputs error text to stderr. The system SHALL match the regex pattern `HTTP Error 429` (case-insensitive) against the stderr text after each extraction completes.

#### Scenario: 429 detected from yt-dlp stderr

- **WHEN** yt_dlp_service completes extraction and yt-dlp stderr contains "HTTP Error 429 Too Many Requests"
- **THEN** the system SHALL call `throttle_predictor.record_response("youtube", 429)`

#### Scenario: Non-429 errors are ignored

- **WHEN** yt-dlp stderr contains "HTTP Error 403" or "ERROR: Unsupported URL"
- **THEN** the system SHALL NOT increment the throttle counter

### Requirement: System SHALL track 429 timestamps in sliding window

The system SHALL maintain a sliding window counter for 429 responses. Timestamps SHALL be stored in a Redis sorted set under the key `throttle:window:<service>` with Unix timestamps as scores. Old entries outside the configurable window SHALL be pruned on each `record_response()` call.

#### Scenario: 429 timestamp recorded in Redis

- **WHEN** `record_response("youtube", 429)` is called
- **THEN** the system SHALL add the current Unix timestamp to Redis sorted set `throttle:window:youtube` and remove timestamps older than `THROTTLE_WINDOW_SECONDS`

### Requirement: System SHALL compute throttle risk score

The system SHALL compute a `ytprocessor_throttle_risk_score` metric using the formula: `risk = min(count_in_window / MAX_EXPECTED_429S, 1.0)`, where `MAX_EXPECTED_429S` defaults to 10 (configurable). The score SHALL be a float between 0.0 and 1.0.

#### Scenario: Risk score is 0.0 with no 429s

- **WHEN** no 429 responses have been recorded in the current window
- **THEN** `ytprocessor_throttle_risk_score` SHALL be 0.0

#### Scenario: Risk score increases proportionally with 429 density

- **WHEN** 5 429 responses are recorded within the window and `MAX_EXPECTED_429S=10`
- **THEN** `ytprocessor_throttle_risk_score` SHALL be 0.5

#### Scenario: Risk score caps at 1.0

- **WHEN** 15 429 responses are recorded within the window and `MAX_EXPECTED_429S=10`
- **THEN** `ytprocessor_throttle_risk_score` SHALL be 1.0 (capped)

### Requirement: System SHALL expose throttle risk as Prometheus gauge

The system SHALL expose `ytprocessor_throttle_risk_score` as a Prometheus Gauge metric with labels `service` (e.g., "youtube") and `provider` (e.g., "yt-dlp"). This follows the existing `ytprocessor_` metric naming prefix used by all other metrics (`ytprocessor_jobs_created_total`, `ytprocessor_queue_depth`, etc.).

#### Scenario: Metric is scrapable

- **WHEN** Prometheus scrapes the `/metrics` endpoint
- **THEN** the response SHALL include `ytprocessor_throttle_risk_score{service="youtube",provider="yt-dlp"}`

### Requirement: System SHALL warn at high throttle risk

The system SHALL emit a structured warning log when `ytprocessor_throttle_risk_score` exceeds `THROTTLE_RISK_THRESHOLD` (default 0.7). The log SHALL use the structlog event `throttle_risk_high` and include the current score and service name.

#### Scenario: Warning log at high risk

- **WHEN** `throttle_risk_score` exceeds 0.7
- **THEN** the system SHALL log a warning with `throttle_risk_high` event and the current score

### Requirement: Chaos injection SHALL support 429 simulation ([requires chaos-injection-api])

The chaos injection API SHALL support a `throttle_spike` scenario that rapidly fills the sliding window to trigger high throttle risk for demo purposes. This requires the `chaos-injection-api` change to be implemented first — the throttle predictor service is independent, but the demo injection trigger depends on the chaos API.

#### Scenario: Throttle spike simulation

- **WHEN** chaos inject receives `{"scenario": "throttle_spike", "duration_seconds": 30}`
- **THEN** the system SHALL add sufficient 429 timestamps to the sliding window to push `ytprocessor_throttle_risk_score` above 0.7

#### Scenario: Throttle spike clears after duration

- **WHEN** the chaos `throttle_spike` flag expires (TTL elapsed)
- **THEN** the throttle predictor sliding window SHALL return to normal 429 counting
