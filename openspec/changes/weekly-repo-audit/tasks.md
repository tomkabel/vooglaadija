# Weekly Repo Audit — Tasks

## 1. Research & Standard

- [x] 1.1 Download MIT 6.031 readings (8) + 6.033 to scratch; analyze
- [x] 1.2 Write `research/course/mit-6.031/README.md` (principle → executable check map)
- [x] 1.3 Write `docs/ARCHITECTURE-STANDARD.md` (fitness-function registry)
- [x] 1.4 Write OpenSpec proposal/design/tasks
- [x] 1.5 Update `CODEBOUNDARIES.md` to reference the standard + TID251

## 2. Fitness Gates (ruff config + scope)

- [x] 2.1 Add TID251 select + banned-api (`worker`, `yt_dlp`) + per-file-ignores
- [x] 2.2 Extend ruff/mypy scope to `worker/`, `scripts/`, `alembic/` (verified clean)
- [x] 2.3 Add `starlette` as direct dependency (deptry DEP003)
- [x] 2.4 Add `[tool.deptry]` per_rule_ignores + `package_module_name_map` (0 findings)
- [x] 2.5 Add `audit` dependency group + hatch audit env scripts (weekly/quick/fix/complexity/dead-code/boundary)
- [x] 2.6 Pin ruff/deptry/vulture to locked versions (deterministic gates)

## 3. Audit Tooling

- [x] 3.1 `scripts/audit_report.py` (--weekly/--fix/--baseline; single file)
- [x] 3.2 `scripts/audit/ruff-complexity.toml` (strict thresholds)
- [x] 3.3 `scripts/audit/vulture_whitelist.py` (framework-contract names, documented)
- [x] 3.4 Baseline generated: `.github/audit-baseline.json`
- [x] 3.5 Queue seam: `core/interfaces/queue.py` (JobQueue + MemoryQueue) + `tests/test_queue_seam.py` (5 tests, offline)

## 4. Automation

- [x] 4.1 `.github/workflows/repo-audit.yml` (cron + dispatch; auto-fix PR + deep-scan issue)
- [x] 4.2 `fastapi-test.yml` lint job gates: boundary, lock-check, deptry, vulture

## 5. Validation & Docs

- [x] 5.1 Run `hatch run audit:weekly` + `audit:fix` locally; verify report structure
- [x] 5.2 Full test suite green (791 passed); lint + format + mypy clean on extended scope
- [x] 5.3 `docs/OPS.md` audit section (run/fix commands, baseline procedure, triage)
- [x] 5.4 README mention of the weekly audit
