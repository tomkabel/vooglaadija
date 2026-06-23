## ADDED Requirements

### Requirement: _extract_via_subprocess streams stdout line-by-line

The `_extract_via_subprocess` function SHALL read the subprocess stdout as an async stream of lines rather than waiting for `process.communicate()` to return, and SHALL invoke a `progress_callback` for each line identified as a progress update.

#### Scenario: Progress lines are extracted and passed to callback
- **WHEN** a stdout line parses as JSON and contains `"progress": true`
- **THEN** the function SHALL call the `progress_callback` (if provided) with the parsed dict
- **THEN** the function SHALL continue reading subsequent lines from stdout

#### Scenario: Non-progress JSON lines are treated as the final result
- **WHEN** a stdout line parses as JSON and does NOT contain `"progress": true`
- **THEN** the function SHALL treat it as the extraction result and stop reading stdout
- **THEN** the function SHALL return the parsed result dict

#### Scenario: Function signature accepts optional progress_callback
- **WHEN** `_extract_via_subprocess` is called
- **THEN** it SHALL accept `progress_callback: typing.Callable[[dict], typing.Awaitable[None]] | None = None`

#### Scenario: Timeout still works with streaming
- **WHEN** the subprocess exceeds the YT_DLP_TIMEOUT
- **THEN** the function SHALL kill the process group, clean up, and raise TimeoutError
- **THEN** the `wait_for` timeout SHALL wrap the async iteration, not `communicate()`

#### Scenario: extract_media_url propagates the callback
- **WHEN** `extract_media_url` is called
- **THEN** it SHALL accept and forward `progress_callback` to `_extract_via_subprocess`
- **THEN** the return type `tuple[str, str]` SHALL remain unchanged
