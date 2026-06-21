## ADDED Requirements

### Requirement: System SHALL provide a hidden chaos lab page

The system SHALL provide a `/web/chaos-lab` page rendered with Jinja2/HTMX that contains 4 buttons for triggering chaos scenarios. The page SHALL only be accessible when `FEATURE_CHAOS_API_ENABLED` is `true`.

#### Scenario: Chaos lab page shows 4 scenario buttons

- **WHEN** a GET request is sent to `/web/chaos-lab`
- **THEN** the response SHALL be an HTML page with 4 buttons labeled "SIMULATE YOUTUBE 429", "SIMULATE WORKER CRASH", "SIMULATE DB FAILOVER", and "SIMULATE 429 SPIKE"

#### Scenario: Chaos lab button triggers POST to chaos inject

- **WHEN** a user clicks the "SIMULATE YOUTUBE 429" button
- **THEN** an HTMX POST request SHALL be sent to `/api/v1/chaos/inject` with `{"scenario": "circuit_breaker_open", "duration_seconds": 30}`

#### Scenario: Chaos lab shows current active scenarios with auto-refresh

- **WHEN** the chaos lab page loads
- **THEN** it SHALL display the current state of all chaos flags (active/inactive)
- **WHEN** a chaos flag is set or cleared
- **THEN** the status display SHALL update within 5 seconds via HTMX polling

#### Scenario: 429 Spike button triggers throttle predictor spike

- **WHEN** a user clicks the "SIMULATE 429 SPIKE" button
- **THEN** an HTMX POST request SHALL be sent to `/api/v1/chaos/inject` with `{"scenario": "throttle_spike", "duration_seconds": 30}`
- **THEN** the throttle predictor sliding window SHALL fill with 429 timestamps, pushing `ytprocessor_throttle_risk_score` above 0.7
