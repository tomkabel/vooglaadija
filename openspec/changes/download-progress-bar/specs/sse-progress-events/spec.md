## ADDED Requirements

### Requirement: SSE endpoint subscribes to job_progress channel and emits progress_update events

The SSE `event_generator` function SHALL subscribe to both the `job_status:{user_id}` and `job_progress:{user_id}` Redis pub/sub channels. Events from the progress channel SHALL be emitted as SSE events with event type `progress_update`.

#### Scenario: Progress channel is subscribed on SSE connection
- **WHEN** a client connects to `/web/downloads/stream`
- **THEN** the system SHALL subscribe to both `job_status:{user_id}` and `job_progress:{user_id}` channels
- **THEN** progress events SHALL be emitted with `event: progress_update`

#### Scenario: progress_update event contains job_id and progress data
- **WHEN** a `progress_update` SSE event is emitted
- **THEN** the event data SHALL be JSON containing `"id"` and `"progress"`
- **THEN** the `"progress"` value SHALL be a dict with `percent`, `speed`, `eta`, `downloaded_bytes`, `total_bytes`

#### Scenario: Fallback polling does not emit progress
- **WHEN** the SSE system falls back to DB polling (after pub/sub failure)
- **THEN** no `progress_update` events SHALL be emitted (progress data is not persisted in DB)

#### Scenario: Progress events are not deduplicated by status key
- **WHEN** consecutive progress events arrive for the same job
- **THEN** each SHALL be emitted to the client (no deduplication by `updated_at`)
