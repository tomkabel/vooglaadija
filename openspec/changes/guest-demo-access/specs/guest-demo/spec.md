## ADDED Requirements

### Requirement: System SHALL provide a one-click demo login

The system SHALL provide a `GET /web/demo-login` route that authenticates as a pre-seeded demo user (`demo@vooglaadija.io`) and redirects to `/web/downloads` with valid JWT cookies set.

#### Scenario: Demo login succeeds

- **WHEN** a GET request is sent to `/web/demo-login`
- **THEN** the system SHALL set `access_token` and `refresh_token` cookies for the demo user and redirect to `/web/downloads`

#### Scenario: Demo user does not exist

- **WHEN** a GET request is sent to `/web/demo-login` and the demo user does not exist in the database
- **THEN** the system SHALL return a 500 error with a clear message to run the seed script

### Requirement: Login page SHALL display "Guest Demo" button

The login page template SHALL display a "Guest Demo" button visually distinct from the login form. Clicking it SHALL redirect to `/web/demo-login`.

#### Scenario: Guest Demo button is visible on login page

- **WHEN** a user visits `/web/login`
- **THEN** the page SHALL display a "Guest Demo" button

#### Scenario: Guest Demo button navigates to demo-login

- **WHEN** a user clicks the "Guest Demo" button
- **THEN** the browser SHALL navigate to `/web/demo-login`

### Requirement: Seed script SHALL create demo user and pre-seeded jobs

The system SHALL provide `scripts/seed_demo_data.py` that creates a demo user (`demo@vooglaadija.io`) and 6 pre-seeded download jobs in `completed` status, one from each supported platform. The script SHALL be idempotent (skip if demo user already exists).

#### Scenario: Seed script creates demo user

- **WHEN** `scripts/seed_demo_data.py` is run against an empty database
- **THEN** it SHALL create a user with email `demo@vooglaadija.io` and a known password

#### Scenario: Seed script is idempotent

- **WHEN** `scripts/seed_demo_data.py` is run twice
- **THEN** it SHALL not create duplicate users or jobs

#### Scenario: Seed script creates multi-platform jobs

- **WHEN** `scripts/seed_demo_data.py` completes successfully
- **THEN** the demo user SHALL have 6 completed download jobs from YouTube, Vimeo, Dailymotion, Twitch, TikTok, and Instagram
