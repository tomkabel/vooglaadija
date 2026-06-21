## ADDED Requirements

### Requirement: Circuit breaker threads progress_callback through to extraction

The `extract_media_with_circuit_breaker` function SHALL accept an optional `progress_callback` parameter and pass it through the call chain to `extract_media_url`, enabling progress reporting without altering the circuit breaker's failure/retry behavior.

#### Scenario: progress_callback is passed through the chain
- **WHEN** `extract_media_with_circuit_breaker(url, storage_path, progress_callback=cb)` is called
- **THEN** it SHALL pass `progress_callback` to `_extract_media_url_internal`
- **THEN** `_extract_media_url_internal` SHALL pass it to `extract_media_url`
- **THEN** the function signature SHALL remain backward-compatible (callback is optional)

#### Scenario: circuit breaker does not interpret progress data
- **WHEN** progress callbacks are invoked during extraction
- **THEN** the circuit breaker SHALL NOT count them as success or failure indicators
- **THEN** the circuit breaker's state SHALL only change on extraction result, not on progress

#### Scenario: No callback is a no-op
- **WHEN** `extract_media_with_circuit_breaker` is called without `progress_callback`
- **THEN** extraction SHALL proceed exactly as before, without progress reporting
- **THEN** the return type `tuple[str, str]` SHALL remain unchanged
