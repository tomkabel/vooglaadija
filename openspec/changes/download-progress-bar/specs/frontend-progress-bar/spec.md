## ADDED Requirements

### Requirement: Download row renders animated progress bar for processing jobs

The dashboard HTML SHALL render a progress bar inside each download row whose job is in `processing` status and has received a progress update. The bar SHALL animate smoothly to reflect the current percentage, and SHALL display speed and ETA alongside it.

#### Scenario: Progress bar appears when progress_update is received
- **WHEN** the browser receives a `progress_update` SSE event
- **THEN** the download row matching `data.id` SHALL display a progress bar
- **THEN** the bar width SHALL animate to `data.progress.percent`%

#### Scenario: Progress bar shows percentage, speed, and ETA
- **WHEN** a progress bar is visible
- **THEN** it SHALL display the percentage (e.g. "45.2%")
- **THEN** it SHALL display the download speed in human-readable format (e.g. "5.2 MB/s")
- **THEN** it SHALL display the ETA in human-readable format (e.g. "38s remaining")

#### Scenario: Progress bar hides on completion
- **WHEN** a `job_update` event arrives with `status: "completed"` for a job that had a progress bar
- **THEN** the progress bar SHALL be hidden
- **THEN** the download button SHALL be shown

#### Scenario: Progress bar handles unknown total_bytes
- **WHEN** `total_bytes` is null or zero in a progress update
- **THEN** the system SHALL use `total_bytes_estimate` if available
- **THEN** if neither is available, the bar SHALL show an indeterminate state (continuous animation)

#### Scenario: getRowHTML includes hidden progress bar container
- **WHEN** `getRowHTML(data)` is called
- **THEN** the output SHALL include a progress bar container with CSS class `download-progress` (initially hidden)
- **THEN** the container SHALL not interfere with existing row layout when hidden
