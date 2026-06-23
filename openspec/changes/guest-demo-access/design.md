## Context

The login page is the first thing the audience sees during the demo. The current flow requires entering email+password, clicking submit, waiting for redirect. Every second of friction in a 3-minute slot is costly. The fix is a single "Guest Demo" button that logs into a pre-seeded demo account with zero typing. No database schema changes — the demo user is a real `User` row created by a seed script.

## Goals / Non-Goals

**Goals:**

- One-click login from the login page to the dashboard in under 2 seconds
- Pre-seeded demo account with 6 completed jobs from different platforms
- Seed script that can be run independently or as part of docker-compose startup
- Minimal code change — no new models, no nullable columns, no anonymous sessions

**Non-Goals:**

- Not a replacement for the registration flow — registration still required for real use
- No anonymous auth or guest user sessions — demo user is a persistent account
- No UI redesign — single button addition, not a new landing page

## Decisions

1. **Hardcoded demo account over anonymous sessions**: Anonymous sessions require nullable `user_id` on `DownloadJob`, complex cleanup logic, and risk data loss. A hardcoded `demo@vooglaadija.io` account uses existing models with zero schema changes. Alternative: in-memory demo mode — doesn't persist across page reloads. Decision: pre-seeded account.

1. **Seed script over migration**: Alembic data migrations are designed for schema changes, not demo data. A standalone `scripts/seed_demo_data.py` script is simpler and can be run independently. Alternative: Alembic data migration — would need versioned migration file. Decision: standalone script idempotent (skips if demo user exists).

1. **Direct route over service layer indirection**: The demo login route calls `create_access_token` and `set_token_cookies` directly, skipping the auth service. This is acceptable because the demo account has a known password and the code path is identical to normal login — just inlined. Alternative: call login service — adds indirection with no benefit. Decision: direct route (~15 lines).

## Risks / Trade-offs

- [Demo account password known] Anyone can log in as demo and see pre-seeded jobs. → Mitigation: demo account is for demonstration only; no real data. Password can be rotated per deployment.
- [Seed script not run before demo] Demo button returns login error. → Mitigation: seed script runs as part of docker-compose health check or documented in runbook.
- [Cookie configuration differs in demo] JWT cookie not set correctly. → Mitigation: same `set_token_cookies` function used by normal login; verified in rehearsals.
