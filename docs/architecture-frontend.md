# Architecture — Frontend

**Part:** Frontend (`frontend/`)
**Type:** Server-rendered web with Tailwind CSS + HTMX

---

## Executive Summary

The frontend uses server-rendered Jinja2 templates with HTMX for dynamic partial updates and SSE for real-time job status streaming. The Tailwind CSS build pipeline processes source CSS from `frontend/css/src/` and deploys the output to `app/static/css/`.

## Technology Stack

| Category | Technology | Version |
|----------|------------|---------|
| CSS | Tailwind CSS | 3.4.19 |
| HTML | Jinja2 | 3.1.6 |
| Dynamic | HTMX | 1.9.12 |
| Real-time | SSE-Starlette | 3.3.4 |
| Build | PostCSS + Autoprefixer | 8.5.10 / 10.4.20 |
| Manager | pnpm | 10.33.0 |

## Design System

Custom dark theme with four accent color families:

| Color | Role | Usage |
|-------|------|-------|
| Amber (#f59e0b) | Primary accent | Buttons, active states, logo |
| Coral (#f43f5e) | Error/danger | Error states, destructive actions |
| Jade (#10b981) | Success | Completed states, confirmations |
| Warm (#a855f7) | Info/neutral | Informational states |

**Typography:**
- Display: Outfit (headings)
- Body: DM Sans (body text)
- Mono: JetBrains Mono (code/metrics)

**Custom animations:** fade-in, slide-up, slide-in-right, glow-pulse, grain, float.

## Build Pipeline

```
frontend/css/src/styles.css
    → (Tailwind + PostCSS build)
    → frontend/css/dist/styles.css
    → (copy)
    → app/static/css/styles.css
```

**Commands:**
- `pnpm run dev` — Watch mode (from `frontend/`)
- `pnpm run deploy` — Production build + copy to `app/static/`
- `pnpm run frontend:deploy` — Same, from repo root

## HTMX Integration

All state-changing operations use HTMX partial responses:
- Form submissions return HTML fragments (not JSON)
- `/web/downloads/stream` provides SSE for real-time dashboard updates
- Error responses return 429 with HTML for HTMX requests (prevents DOM corruption)

## Templates

Jinja2 templates in `app/templates/` with:
- CSP nonces generated per-request
- Template inheritance for consistent layout
- HTMX attributes for dynamic behavior
