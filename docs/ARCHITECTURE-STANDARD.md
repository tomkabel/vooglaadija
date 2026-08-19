# Architecture Standard — Vooglaadija

The authoritative, _executable_ statement of how this repo is designed. Every rule below is a
fitness function (Ford et al., _Building Evolutionary Architectures_): it is enforced by a tool or
check in CI, not by policy text. Rules are tagged **gate** (blocks PR CI) or **measure** (weekly
advisory scan, ranked by hotspot score). Principles are cited from MIT 6.031
(`research/course/mit-6.031/README.md`) and the governing literature.

## Layering (gate)

```text
frontend/ ──► app/ ──► core/ ◄── worker/
  static       API       shared infra      process
```

| Rule                                                                                                                                                             | Enforcement                                                                | Rationale                                                         |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `core/` must not import `app/` or `worker/`                                                                                                                      | ruff `TID251` banned-api (`worker`) + `scripts/import_analysis.py`         | 6.031: representation independence; 6.033: modularity             |
| `app/` must not import `worker/`                                                                                                                                 | ruff `TID251` banned-api (`worker`) + `import_analysis.py`                 | dependency arrow points inward                                    |
| `worker/` must not import `app.api`, `app.schemas`, `app.models`, `app.config`, `app.database`, `app.metrics`, `app.logging_config`, `app.services.redis_client` | `scripts/import_analysis.py` (zone-aware AST check; TID251 is global-only) | worker is a separate process, not a second entry point to the API |
| `worker/` may import only `core/`, `worker/`, and API-independent `app.services.*`                                                                               | `scripts/import_analysis.py`                                               |                                                                   |
| No shim/re-export modules (`app/config.py`, `app/database.py`, …)                                                                                                | code review (import_analysis.py covers layering + the yt-dlp ACL only)     | CRITIQUE.md lesson: indirection layers hide real boundaries       |

## Anti-Corruption Layer — yt-dlp (gate)

- `yt_dlp` is importable **only** from `app/services/yt_dlp_service.py`.
- Everything else interacts with extraction through Vooglaadija types (Pydantic models,
  `DownloadResult`-style uniform results). Upstream yt-dlp breakage must change exactly one file.
- Enforcement: ruff `TID251` banned-api (`yt_dlp`) **and** an independent ACL rule in
  `scripts/import_analysis.py` — TID251's per-file-ignores for `worker/**` would otherwise lift the
  ban for worker code.
- Rationale: Khononov, _Learning Domain-Driven Design_; 6.031 interfaces reading.

## Complexity policy (measure → gate when backlog clears)

Deep modules are allowed to be long (Ousterhout). Hard LOC caps are forbidden as rules. The
following thresholds are measured weekly; when the backlog is cleared they promote to gates.

| Threshold              | Rule      | Target |
| ---------------------- | --------- | ------ |
| Cyclomatic complexity  | `C901`    | ≤ 10   |
| Too many arguments     | `PLR0913` | ≤ 6    |
| Too many branches      | `PLR0912` | ≤ 12   |
| Too many statements    | `PLR0915` | ≤ 50   |
| Too many returns       | `PLR0911` | ≤ 6    |
| Too many nested blocks | `PLR1702` | ≤ 4    |

- Measurement pass: `ruff check --config scripts/audit/ruff-complexity.toml …`
- Main config keeps these rules selected-but-ignored (legacy backlog).

## Hotspot & churn model (measure)

- Hotspot score = complexity violations × commits touching the file (90 days) — Tornhill, _Software
  Design X-Rays_.
- Cold stable files are NOT findings, even if large.
- Files with hotspot score > 0 AND coverage < 60% are top priority (Feathers: feedback loops).
- Temporal coupling: file pairs co-changed in ≥ 80% of commits touching either (min 5 co-commits)
  flag leaky abstractions — especially cross-layer pairs (`app/api` ↔ `frontend`, `app` ↔
  `worker`). Investigation should look for shared concepts implemented twice.

## Error definition (measure)

- Defensive code density (try/except per 100 LOC) is measured per module; the top modules are
  candidates for "define errors out of existence" (Ousterhout): replace scattered catch logic with
  uniform result objects (`status`, `payload`, `error_context`) at service boundaries.
- Guidance: catch at boundaries, not in every caller; do not duplicate the same exception handling
  across REST routes, web routes, and the worker.

## Duplication (measure)

- Rule of Three (Fowler): refactor at the 3rd instance; 2-instance clones are findings only when
  high-churn or cross-layer.
- jscpd (min-tokens 50) reports clone pairs; migration boilerplate (alembic versions) is
  acknowledged and exempt from remediation.
- Known single-implementation rule: one canonical implementation per concept (path validation, retry
  jitter, Redis URL construction) — see CRITIQUE.md history.

## Frontend (gate)

- ES modules (`<script type="module">`); **zero new `window.*` globals** (measured weekly; existing
  globals must be migrated incrementally, never added to).
- Repeated markup (status badges, error blocks) lives in partials, not inline duplicates.
- No bundler: HTMX + Tailwind stays dependency-free (out of scope: adopting a bundler).

## One-Version Rule (gate)

- `uv lock --check` must pass: `pyproject.toml` and `uv.lock` in sync; app/worker/scripts share one
  dependency tree (Winters et al., _Software Engineering at Google_).
- pnpm lockfiles stay in sync via `pnpm install --frozen-lockfile` in CI.

## Queue seam (gate — test)

- `core/interfaces/queue.py` defines the `JobQueue` protocol; `MemoryQueue` mirrors Redis LPUSH/RPOP
  semantics.
- Payload contract: producers enqueue `str(job_id)`; consumers normalize via
  `worker.job_claimer.normalize_job_id` — verified offline in `tests/test_queue_seam.py` (Feathers:
  seams; no Redis needed).

## Over-engineering policy (measure + review)

- YAGNI: no speculative abstraction; no shim/indirection modules; thin service layer.
- New dependencies require justification (and must be directly imported — deptry DEP002 exclusions
  are documented in `pyproject.toml`).
- Hotspot refactors follow the Strangler Fig protocol (Fowler): slim stable facade → frozen
  interface → incremental extraction of private deep modules; never arbitrary splitting to pass a
  linter.

## Registry summary

| Fitness function                      | Tool / check                   | Threshold               | Status  |
| ------------------------------------- | ------------------------------ | ----------------------- | ------- |
| Layering + no shims                   | TID251 + `import_analysis.py`  | 0 violations            | gate    |
| yt-dlp ACL                            | TID251 + `import_analysis.py`  | 0 violations            | gate    |
| Unused imports/vars                   | ruff F401/F841                 | 0                       | gate    |
| Dead code                             | vulture ≥80% + whitelist       | 0                       | gate    |
| Unused deps                           | deptry (exclusions documented) | 0                       | gate    |
| Lockfile                              | `uv lock --check`              | in sync                 | gate    |
| Secrets                               | detect-secrets baseline        | 0 delta                 | gate    |
| Queue payload contract                | `tests/test_queue_seam.py`     | pass                    | gate    |
| Complexity                            | strict ruff config             | C901 ≤10, PLR0913 ≤6, … | measure |
| Hotspots                              | churn × complexity             | rank top-10             | measure |
| Temporal coupling                     | co-change ≥80%                 | flag                    | measure |
| Defensive density                     | try/except per 100 LOC         | rank top-10             | measure |
| Duplication                           | jscpd min-tokens 50            | report                  | measure |
| Commented-out code / window.\* / TODO | regex scan                     | report                  | measure |

## Sources

- MIT 6.031 readings analysis: `research/course/mit-6.031/README.md`
- Ousterhout, _A Philosophy of Software Design_ (deep modules, error definition)
- Tornhill, _Software Design X-Rays_ (hotspots, temporal coupling)
- Winters et al., _Software Engineering at Google_ (shift-left, one-version rule, LSCs)
- Ford/Parsons/Kua, _Building Evolutionary Architectures_ (fitness functions)
- Fowler, _Refactoring_ + essays (Rule of Three, YAGNI, Strangler Fig)
- Feathers, _Working Effectively with Legacy Code_ (seams, feedback loops)
- Khononov, _Learning Domain-Driven Design_ (anti-corruption layer)
