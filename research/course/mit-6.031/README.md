# MIT 6.031 (Software Construction): Distilled Principles for Repo Architecture

MIT 6.031 (Spring 2022) teaches software construction around three "big properties": **safe from bugs** (correct today and in the unknown future), **easy to understand** (communicating clearly with future programmers, including future you), and **ready for change** (designed to accommodate change without rewriting). Every reading operationalizes these properties into concrete, reviewable practices: static checking and fail-fast before debugging, specifications as contracts with preconditions/postconditions, abstract data types with representation independence, interfaces that expose a contract and nothing more, and version control discipline for individuals and teams. Complemented by MIT 6.033's systems design principles — modularity, abstraction, and *enforced* modularity reduce complexity and "fate-sharing" — the course anchors repo architecture because each principle maps to an executable gate (type checker, linter, import-boundary verifier, test suite), turning architectural intent into CI-enforceable policy. This document distills the readings (full texts were fetched to scratch, not committed) and maps each principle to a concrete check for the Vooglaadija monorepo (FastAPI `app/` + worker process + shared `core/`, per `CODEBOUNDARIES.md`).

## MIT principle → executable check

| Principle | Reading | Repo check | Threshold / Gate |
|---|---|---|---|
| Static checking / type safety: "first defense: make bugs impossible" | 09 Avoiding Debugging | `mypy app/ core/` (pydantic plugin enabled; strict optional) | `uv run mypy` gate in CI; zero errors on `check` task |
| Fail fast: reveal bugs as early as possible; check preconditions at public boundaries | 04 Code Review; 09 Avoiding Debugging | Assertion/logging guidance: `assert` for internal invariants only (never external I/O or user input); boundary validators raise typed errors | PR review rule + test that invalid download requests return 4xx immediately, not 500 |
| Code review hygiene: good names, no magic numbers, one purpose per variable, no globals | 04 Code Review | ruff rules (`PLR2004` magic-value-in-constant, `PLW`), manual review checklist | `ruff check` zero findings; magic-number exemptions reviewed |
| DRY: no duplicated logic ("bug in both copies, fixed in one") | 04 Code Review | ruff `RUF`/duplication audit; shared download logic lives once in `core/` | Duplication review in PR; no copy-paste across `app/`/`worker/` |
| Comments: "why" and provenance, not transliteration; no commented-out code | 04 Code Review | ast-grep rule `commented-code` (`sg` query for `//`/`#` lines containing code); docs on copied/adapted code | Zero commented-out blocks in PR diff; provenance note required for vendored code |
| Readability & consistency: follow project conventions, whitespace, formatting | 04 Code Review | `ruff format --check` (Python) + Prettier `pnpm run format:check` (Markdown/YAML) + markdownlint | Format-check tasks in CI; failure blocks merge |
| Version control: meaningful commits, revert, "in version control" = pushed; no generated artifacts | 05 Version Control | Git hygiene checks: commit messages with why, `.gitignore` for build output, `git status` clean before merge | Commit contains only related changes; no committed `__pycache__`/`dist` artifacts |
| Specifications: precondition/postcondition contract; spec is a firewall decoupling client and implementer | 06 Specifications | Service boundary design rule: every service entry point declares typed in/out; "define errors out of existence" via uniform `DownloadResult` objects (status + error payload) instead of ad-hoc `None`/HTTP smuggling | Boundary types in `core/` are Pydantic models; no `None`-return error channels outside `core/` |
| Avoid null: null is ambiguous and a static-type hole; fail fast on it | 06 Specifications | mypy strict optional + Pydantic field validation rejecting `None` where unspecified | Strict-null typing on boundary schemas; `Optional` only where spec says so |
| Precondition or postcondition: public/exported functions should throw, not demand preconditions | 07 Designing Specifications | Public API handlers validate input and raise typed HTTP errors (postcondition-style); preconditions reserved for cheap local checks | API contract tests assert error semantics for every documented failure mode |
| Underdetermined/declarative specs; avoid over-specification | 07 Designing Specifications | YAGNI policy: specs promise only what is implemented and tested; no speculative generality in shared `core/` types | Review rule: over-strong postconditions (unused guarantees) rejected |
| Assertions: localize bugs, self-checks, assert side-effect-free | 09 Avoiding Debugging | Assertions in `core/` invariants; logging of assertion context; no side effects in asserted expressions | Tests enable `-O0` asserts; assert-expression purity reviewed |
| Abstract data types: representation independence, operations are the type, invariants | 10 Abstract Data Types | `core/` models + Pydantic schemas as boundary types; rep never leaks (no `core/` internals in routes); tests partition per operation | Schema/ORM model changes must not require edits outside `core/` + adapters |
| Interfaces: contract and nothing more; minimal, cohesive ops; multiple implementations; subtypes at least as strong | 12 Interfaces, Generics, & Enums | `app` ⇏ `worker` boundary per `CODEBOUNDARIES.md`; yt-dlp anti-corruption layer: adapter interface, `yt_dlp` import banned elsewhere (ruff `TID251` + `scripts/import_analysis.py` ACL rule) | `python scripts/import_analysis.py` passes; `TID251` zero findings |
| Team version control: communicate, small commits, never commit broken code, sync often; no branches for small teams | 29 Team Version Control | Trunk-based policy (work on `main`); temporal-coupling check: commit/PR touches only files of one concern | PRs < ~300 lines, single concern, no merge-conflict churn trend |
| Modularity & abstraction; enforced modularity reduces fate-sharing | 6.033 Lecture 1 (design principles) | `core/`/`app/`/`worker/` import rules enforced mechanically, not socially | Boundary verifier in CI; worker failure cannot corrupt API process (no shared mutable state) |
| Ready for change: accommodate change without rewriting | All readings (esp. 04, 05, 10) | Fitness-functions registry: architectural tests asserting boundary rules, schema contracts, and error semantics | Registry executes in CI; new invariants must be registered, not ad hoc |

## Industry complement

- **John Ousterhout**, *A Philosophy of Software Design* — "deep modules": small interfaces hiding large implementations; 6.031's "interface = contract and nothing more" (Reading 12) is the same idea, and `core/` should be deep, not wide.
- **Adam Tornhill**, *Your Code as a Crime Scene* / *Software Design X-Rays* — hotspots and temporal coupling: code that changes together should live together; 6.031's team-version-control reading points the same way, and the temporal-coupling PR check operationalizes it.
- **Martin Fowler**, *Refactoring* (Rule of Three) and YAGNI — wait for the third duplication before abstracting, and never build speculative generality; this is the operational twin of Reading 7's "avoid over-specification".
- **Winters, Manshreck & Wright**, *Software Engineering at Google* — shift-left (find problems as early as possible, echoing Reading 9's "fail fast") and one-version rule; Google's code-review-before-main process is explicitly cited in Reading 4.
- **Neal Ford, Rebecca Parsons & Patrick Kua**, *Building Evolutionary Architectures* — fitness functions: automated, CI-executed checks that guard architectural characteristics; these are how "ready for change" gets a gate in this repo.
- **Michael Feathers**, *Working Effectively with Legacy Code* — seams are places where behavior can be altered without modification; the `core/` boundary types and the yt-dlp adapter interface are exactly such seams.
- **Vladik Khononov**, *Domain Modeling Made Functional / Learning Domain-Driven Design* — an anti-corruption layer protects the domain from an external API's model; the yt-dlp adapter (Reading 12's interface discipline applied to a third-party dependency) is the concrete instance.

## Source permalinks

Fetched into scratch (`/tmp/agent_.../mit-6.031/*.txt`, truncated <60 KB each; not committed). MIT 6.031 readings (Spring 2022):

- https://web.mit.edu/6.031/www/sp22/classes/04-code-review/
- https://web.mit.edu/6.031/www/sp22/classes/05-version-control/
- https://web.mit.edu/6.031/www/sp22/classes/06-specifications/
- https://web.mit.edu/6.031/www/sp22/classes/07-designing-specs/
- https://web.mit.edu/6.031/www/sp22/classes/09-avoiding-debugging/
- https://web.mit.edu/6.031/www/sp22/classes/10-abstract-data-types/
- https://web.mit.edu/6.031/www/sp22/classes/12-interfaces-generics-enums/
- https://web.mit.edu/6.031/www/sp22/classes/29-team-version-control/

MIT 6.033 (Computer System Engineering, Spring 2018; the requested `.../6-033-computer-systems-engineering-fall-2018/` URL now 404s — Spring 2018 is the live OCW equivalent):

- https://ocw.mit.edu/courses/6-033-computer-system-engineering-spring-2018/ (course overview)
- https://ocw.mit.edu/courses/6-033-computer-system-engineering-spring-2018/pages/week-1/lecture-1-outline/ (design principles: modularity, abstraction, enforced modularity, fate-sharing)
