## Why

The demo script requires the presenter to go from cold start to a completed download in under 5 seconds. The current registration flow adds friction — the audience waits through email/password entry, page transitions, and login redirects. A one-click "Guest Demo" button eliminates this friction entirely. No database schema changes needed — just a pre-seeded demo account and a single redirect route.

## What Changes

- **New route** `GET /web/demo-login`: Redirect to `/web/downloads` with JWT cookies set for a pre-seeded `demo@vooglaadija.io` account
- **New demo account** in seed data: `demo@vooglaadija.io` with a known password
- **New seed script** `scripts/seed_demo_data.py`: Alembic-compatible data migration or standalone script that creates the demo user and pre-seeds 6 download jobs (from YouTube, Vimeo, Twitch, TikTok, Dailymotion, Instagram) in `completed` status
- **Modify** login page template: Add "Guest Demo" button next to the login form with visual distinction

## Capabilities

### New Capabilities

- `guest-demo`: One-click demo authentication via pre-seeded demo account, no registration required. Includes seed data management for the demo user and multi-platform completed jobs.

### Modified Capabilities

- (No existing specs — this is a fresh capability)

## Impact

- **No DB schema change** — uses existing `User` and `DownloadJob` models
- **No nullable columns** — demo user is a real user with `email`, `password_hash`, `is_active=true`
- **New file:** `scripts/seed_demo_data.py` (idempotent — skips if demo user exists)
- **Modified file:** `app/api/routes/web.py` (add 1 route)
- **Modified template:** login template for the "Guest Demo" button
- **Non-breaking:** All existing auth flows remain unchanged
- **Cross-change dependency:** Seed data creates jobs with URLs from 6 platforms. The `multi-platform-validator` change must be implemented first, OR the seed script must write directly to the DB (bypassing API validation). Current design: direct DB writes via SQLAlchemy — no validation dependency at the DB level. However, the URLs must be valid formats that humans recognize during the demo.
