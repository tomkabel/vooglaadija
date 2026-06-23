## ADDED Requirements

### Requirement: PubSubService publishes progress updates on separate channel

The `PubSubService` SHALL provide a `publish_job_progress` method that publishes progress data to a Redis pub/sub channel distinct from the status-transition channel. The channel pattern SHALL be `job_progress:{user_id}`.

#### Scenario: publish_job_progress publishes to job_progress channel
- **WHEN** `publish_job_progress(user_id, job_data)` is called
- **THEN** the message SHALL be published to channel `job_progress:{user_id}`
- **THEN** the message SHALL be a JSON string containing at minimum `id`, `progress` dict

#### Scenario: Progress message format
- **WHEN** a progress message is published
- **THEN** the JSON payload SHALL contain: `"id"` (job UUID string), `"progress"` (dict with `percent`, `speed`, `eta`, `downloaded_bytes`, `total_bytes`)

#### Scenario: publish_job_progress returns subscriber count
- **WHEN** `publish_job_progress` completes
- **THEN** it SHALL return the number of subscribers that received the message (int)

#### Scenario: Progress channel does not interfere with status channel
- **WHEN** messages are published to `job_progress:{user_id}`
- **THEN** they SHALL NOT appear on the `job_status:{user_id}` channel subscription
- **THEN** the existing `publish_job_status` method SHALL remain unchanged
