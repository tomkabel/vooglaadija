# Weekly Repo Audit

## Why

The repo has no periodic governance: `worker/` and `scripts/` were outside lint/type-check
scope, complexity rules were ignored outright, dead code and dependency drift accumulate between
manual cleanups, and the yt-dlp anti-corruption layer was only a convention. Industry practice
(Ford: fitness functions; SWE at Google: shift-left + automated large-scale changes; Tornhill:
behavioral code analysis) says architecture must be enforced by executable checks and remediated
by machines, not by advisory documents and manual sweep weeks.

## What Changes

- `docs/ARCHITECTURE-STANDARD.md`: executable architecture standard (fitness-function registry)
  anchored on MIT 6.031 research (`research/course/mit-6.031/README.md`)
- ruff: TID251 gates for layering (`worker` ban) and the yt-dlp ACL; lint/format/mypy scope
  extended to `worker/`, `scripts/`, `alembic/`
- `scripts/audit_report.py`: single-file weekly deep scan — hotspots (complexity × churn),
  temporal coupling (≥80% co-change), defensive-code density, commented-out code, `window.*`
  globals, secrets delta, lockfile check; emits `audit-report.md/.json` with trend vs
  `.github/audit-baseline.json`
- `scripts/audit/ruff-complexity.toml`: strict complexity measurement pass (C901/PLR091x/PLR1702)
- `scripts/audit/vulture_whitelist.py`: documented dead-code exclusions (framework contracts)
- `core/interfaces/queue.py` + `tests/test_queue_seam.py`: offline app→worker payload contract
  (MemoryQueue seam, no Redis)
- `.github/workflows/repo-audit.yml`: weekly (Mon 06:00 UTC) — `[AUTO-BOT]` cleanup PR for
  mechanical fixes (ruff safe fixes + formatting) and a `repo-audit` issue with the deep-scan
  report ranked by hotspot score
- `fastapi-test.yml` lint job: boundary verifier, `uv lock --check`, deptry, vulture as gates

## Capabilities

### New Capabilities
- `weekly-repo-audit`: scheduled deep scan producing hotspot-ranked advisory report with trend
- `auto-bot-cleanup-pr`: scheduled mechanical fix PR (safe ruff fixes + formatting only)
- `queue-seam`: protocol + in-memory queue for offline producer/consumer contract testing
- `complexity-measurement`: strict ruff config for advisory complexity thresholds
- `yt-dlp-acl`: TID251 ban confining `yt_dlp` imports to `app/services/yt_dlp_service.py`

### Modified Capabilities
- `lint-gates`: ruff/mypy scope extended; TID251 + boundary + lockfile + deptry + vulture added
  to PR CI
- `deptry-config`: documented DEP002 exclusions for runtime-not-imported and CVE-pinned deps

## Impact

- `pyproject.toml` (ruff config, deptry config, hatch audit env, `starlette` direct dep)
- `scripts/` (new audit tooling), `core/interfaces/` (new), `tests/` (queue seam tests)
- `.github/workflows/repo-audit.yml` (new), `fastapi-test.yml` (lint job gates)
- `docs/ARCHITECTURE-STANDARD.md`, `CODEBOUNDARIES.md`, `research/course/mit-6.031/README.md`
- No runtime behavior changes; no new runtime dependencies
