## 1. Seed Script

- [x] 1.1 Create `scripts/seed_demo_data.py` with async session factory and idempotent user creation
- [x] 1.2 Add demo user with email `demo@vooglaadija.io`, known password, `is_active=true`
- [x] 1.3 Add 6 pre-seeded completed download jobs: YouTube (3), Vimeo (1), Twitch (1), TikTok (1)
- [x] 1.4 Make script idempotent — check if demo user exists before creating

## 2. Demo Login Route

- [x] 2.1 Add `GET /web/demo-login` route in `app/api/routes/web.py`
- [x] 2.2 Implement demo login: fetch demo user, create JWT tokens, set cookies, redirect to `/web/downloads`
- [x] 2.3 Handle error case when demo user doesn't exist (clear 500 error to run seed script)

## 3. Login Page UI

- [x] 3.1 Add "Guest Demo" button to login page template visually distinct from email/password form
- [x] 3.2 Wire button to navigate to `/web/demo-login`
- [x] 3.3 Style button with distinct color (e.g., secondary/outline style) so it's clearly different from the login form

## 4. Docker Compose Integration

- [x] 4.1 Add seed script execution to `docker-compose.demo.yml` as a one-shot init container that runs before the API starts
- [x] 4.2 Ensure demo user is created on first deploy and skipped on subsequent runs (idempotent)

## 5. Tests

- [x] 5.1 Write test for demo login route returning correct redirect and setting cookies
- [x] 5.2 Write test for seed script idempotency (running twice doesn't create duplicates)
- [x] 5.3 Write test that demo user can access `/web/downloads` after demo login
