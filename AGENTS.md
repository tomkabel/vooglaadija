# Vooglaadija — Agent Instructions

## Two processes

- **API**: `hatch run dev` (uvicorn `app.main:app` on :8000). Entry: `app/main.py`
- **Worker**: `python -m worker.main` (separate process, consumes Redis queue via BRPOP). Entry: `worker/main.py`
- Worker runs an internal health server on port 8082.

## Commands (all via `hatch`)

```bash
hatch run dev              # Start API with hot reload
python -m worker.main      # Start worker (separate terminal)
hatch run db-migrate       # alembic upgrade head

hatch run test:unit        # Unit tests (--ignore=tests/test_api, -n auto, skips slow)
hatch run test:integration # Integration tests (tests/test_api/, -n auto)
hatch run test:all         # All tests (-n auto, skips slow by default)
hatch run test:cov         # With XML + HTML + term coverage

# Run full suite including slow regression tests (CI behavior)
pytest -m '' -n auto       # all markers
pytest -m slow             # only story regression tests

hatch run lint:check       # ruff check
hatch run lint:format-check # ruff format --check
hatch run type:check       # mypy app/
```

## Testing quirks

- Tests use **SQLite+aiosqlite**, not PostgreSQL. Per-xdist-worker DB files `test_gw*.db`.
- `conftest.py` sets `TESTING=1`, `BCRYPT_ROUNDS=4`, patches `database_url` **before** any app import.
- Tables created/dropped **per xdist worker** (session-scoped `setup_database` fixture). Tests isolated via UUID-prefixed emails.
- `create_test_user_and_login(client)` helper in `conftest.py` for auth tests.
- Integration tests (`CI_INTEGRATION=true`) hit real postgres/redis — only run in CI.
- **Default test run skips `@pytest.mark.slow` tests** (230 regression story tests). Run `pytest -m slow` or `hatch run test:all --no-cov -m ''` for full suite.
- **Fast feedback loop**: pytest runs last-failed tests first (`--lf --ff`), then passes. Delete `.pytest_cache/` to reset.

## Frontend

- Tailwind CSS source: `frontend/css/src/styles.css` → built to `frontend/css/dist/styles.css` → deployed to `app/static/css/styles.css`
- Build: `pnpm run deploy` (from repo root: `pnpm run frontend:deploy`; from `frontend/`: `pnpm run deploy`)
- Dev watch: `pnpm run dev` (from `frontend/`)
- Uses **pnpm** (not npm). Install: `pnpm install --frozen-lockfile`
- Templates are Jinja2 in `app/templates/`, server-rendered with HTMX and SSE.

## Database

- Async SQLAlchemy 2.0 + PostgreSQL. Migrations via Alembic (async env in `alembic/env.py`).
- Settings auto-construct `DATABASE_URL` from `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` env vars if `DATABASE_URL` not set directly.
- Run migrations: `hatch run db-migrate`. Create migration: `alembic revision --autogenerate -m "description"`.

## Linting & quality

- **Python**: ruff (lint+format), mypy
- **JS/CSS/JSON**: biome
- **Markdown**: markdownlint-cli2
- **YAML**: yamllint
- **Shell**: shellcheck
- **Formatting**: prettier for md/yaml
- Pre-commit hooks run ruff, mypy, biome, shellcheck, yamllint, and various checks.

## CI pipeline order (GitHub Actions)

1. `lint` — ruff check, ruff format check, biome, markdownlint, prettier, yamllint, shellcheck
2. `type-check` — mypy (needs lint)
3. `unit-tests` — pytest with -n auto (needs lint, SQLite)
4. `integration-tests` — pytest with real Postgres+Redis (needs lint, CI_INTEGRATION=true)
5. `security` — bandit + safety (needs lint)
6. `build-check` — Docker build (needs all above)

## Key conventions (detailed in `.kilocode/rules/*.md`)

- **API**: `/api/v1/` prefix, snake_case JSON, standardized error format with `error.code`/`error.message`
- **Auth**: JWT (15min access, 7d refresh), bcrypt passwords, rate-limited auth endpoints
- **DB**: Async SQLAlchemy 2.0 style, UUID PKs, `created_at`/`updated_at` timestamps, Alembic migrations
- **Testing**: pytest markers `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.slow`
- **Docker**: Multi-stage builds, non-root user (1000:1000), health checks, resource limits

## Security gotchas

- `SECRET_KEY` must be >= 32 chars with entropy >= 2.9 bits/char (enforced in `app/config.py`)
- CSP headers with nonce on every response
- CSRF tokens on state-changing HTMX routes
- File serving has path traversal protection
- `rm storage/*` is denied in `kilo.json` permissions

## Old docs

- `docs/ARCHITECTURE.md` — system component diagram and responsibilities (accurate)
- `docs/API.md` — full endpoint reference
- `docs/OPS.md` — env vars and deployment
- `docs/AGENTS.md` — stale, replaced by this file

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes_tool` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context_tool` | Need source snippets for review — token-efficient |
| `get_impact_radius_tool` | Understanding blast radius of a change |
| `get_affected_flows_tool` | Finding which execution paths are impacted |
| `query_graph_tool` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool` | Finding functions/classes by name or keyword |
| `get_architecture_overview_tool` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.
