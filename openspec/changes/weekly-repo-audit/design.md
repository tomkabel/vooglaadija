## Context

The repo grew a `core/`/`app/`/`worker/` split (CODEBOUNDARIES.md) but enforcement stopped at
one AST script (`scripts/import_analysis.py`) with no scheduled cadence. Lint scope excluded the
worker; complexity rules were globally ignored; nothing measured duplication, churn, or
co-change coupling; the yt-dlp boundary was convention-only.

## Goals / Non-Goals

**Goals:**
- Architecture as executable fitness functions (gates) with shift-left local tasks
- Weekly behavioral deep scan (hotspots, temporal coupling) with trend tracking
- Mechanical bloat (unused imports, formatting) removed by an automated PR, not issues
- Zero new runtime dependencies; audit tooling in existing dev groups

**Non-Goals:**
- Hard LOC caps (deep modules allowed — Ousterhout)
- LLM summarization in the cron (requires API keys)
- Executing Strangler Fig migrations for `circuit_breaker.py`/`sse.py` (maintainer backlog;
  the audit only schedules and ranks them)
- Adopting a JS bundler

## Decisions

1. **TID251 is global-only**: zone bans (worker ⇏ `app.api|app.schemas|…`) cannot be expressed in
   ruff's `banned-api`; they stay in `scripts/import_analysis.py` (AST, zone-aware, already
   tested). TID251 enforces the global bans: `worker` (app/core) and `yt_dlp` (ACL).
2. **Complexity is measure, not gate, initially**: the legacy backlog sits above thresholds;
   gating would fail every PR. The strict config (`scripts/audit/ruff-complexity.toml`) measures;
   promotion to gates is documented in the standard as a ratchet.
3. **Auto-fix PR scope is deliberately narrow**: safe ruff fixes (F401/F841) + `ruff format`
   only. Judgment findings (complexity, coupling, duplication) stay in the advisory report —
   machines apply mechanics, humans apply judgment.
4. **Baseline churn control**: `.github/audit-baseline.json` is committed once and updated only
   via maintainer PR, so the repo never sees weekly report commits (report lives in the issue +
   artifact).
5. **Queue seam is additive**: `core/queue.py` (Redis) is untouched; `MemoryQueue` mirrors its
   semantics for offline contract tests.

## Design

### Fitness function gates (PR CI, `fastapi-test.yml` lint job)

```
ruff check (TID251 + F401/F841 + all existing rules)   # config-driven
python scripts/import_analysis.py                      # zone-aware boundary
uv lock --check                                        # one-version rule
deptry .                                               # unused/missing deps
vulture app core worker scripts alembic                # dead code (whitelist auto-detected)
```

### Weekly deep scan (`scripts/audit_report.py --weekly`)

Measurements: boundary, F401/F841, TID251, vulture, deptry, lockfile, secrets delta (gates
section) + complexity (strict config), hotspot score (complexity × 90-day churn), temporal
coupling (≥80% co-change, min 5 co-commits), defensive density (try/except per 100 LOC),
jscpd clones (min-tokens 50), commented-out code, `window.*` globals, TODO markers (measures
section). Output: `audit-report.md` + `.json`, trend vs baseline. Exit 0 unless internal error.

### Auto-fix (`scripts/audit_report.py --fix`)

Runs `ruff check --fix --select F401,F841` + `ruff format`; prints a `CHANGED:` manifest.
Workflow opens/updates `[AUTO-BOT] Weekly cleanup` PR from branch `chore/auto-bot/weekly-cleanup`
when the manifest is non-empty. Only safe fixes — never judgment changes.

### Workflow topology

```
repo-audit.yml (cron 0 6 * * 1 + workflow_dispatch)
├─ auto-fix job: audit:fix → manifest → PR create/update (contents+PR write)
└─ deep-scan job: unit tests (coverage) → audit:weekly → artifact → issue update (repo-audit)
```

## Risks / Mitigations

- vulture/deptry false positives → whitelist + documented deptry exclusions; advisory-first
- jscpd noise from alembic boilerplate → migration clones acknowledged, exempt
- Scheduled token read-only on forks → issue step degrades to artifact-only
- `uv lock --check` fails on stale lockfile → gate with explicit `uv lock` remediation command
- Expanding lint/type scope to `worker/` → verified clean before merge (mypy: 78 files, 0 errors)
