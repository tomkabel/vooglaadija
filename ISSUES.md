## 💬 Standard Comments

**@coderabbitai** commented (1 day ago):

<details>
<summary>📝 Walkthrough</summary>

## Summary by CodeRabbit

* **Documentation**
  * Removed multiple internal skills, command/reference materials, and supporting rules/content.
* **Configuration**
  * Added `.editorconfig` for consistent formatting.
  * Reworked `.env.example` to be more focused and explicit.
  * Expanded `.gitignore` for additional tooling and generated artifacts.
* **Issue Templates**
  * Standardized quoting/formatting across issue forms.
  * Updated Discord contact guidance to note faster responses.
* **Chores / CI/CD**
  * Removed internal CI/CD templates and related helper scripts/workflows.
  * Expanded CI lint checks, adjusted production deploy prechecks/known-hosts handling, and pinned workflow actions/steps.

## Walkthrough

The PR modernizes project tooling and CI/CD infrastructure while performing a comprehensive cleanup of legacy skill and documentation artifacts. It introduces `.editorconfig` for consistent editor formatting, rewrites `.env.example` with a streamlined required/optional structure, expands the CI lint job to cover Python/JS/CSS/Markdown/YAML/shell, pins all GitHub Actions to immutable commit SHAs across workflows, hardens the production deploy flow with SSH validation and payload staging, updates integration service health checks, normalizes YAML quoting in templates and workflows, extends `.gitignore` for AI-tooling and formatter caches, and mass-deletes all content from `.agents/`, `.kilo/`, and `.kilocode/` skill documentation, rules, templates, and CI/CD pipeline definitions.

## Changes

**Repository and CI/CD infrastructure updates**

| Layer / File(s) | Summary |
|---|---|
| **Editor config and gitignore infrastructure** <br> `.editorconfig`, `.gitignore` | Adds `.editorconfig` with global UTF-8/LF/4-space defaults and per-filetype overrides (Markdown, YAML, JSON, HTML, Makefiles, Windows batch). Extends `.gitignore` with AI agent/dev-tooling artifacts, formatter/linter caches (Biome, markdownlint, yamllint, pre-commit, shellcheck), and patterns for `.agents/`, `.kilocode/skills/*`, and `.env.local`. |
| **Environment template modernization** <br> `.env.example` | Replaces verbose template with streamlined structure: marks `DB_PASSWORD`, `REDIS_PASSWORD`, `SECRET_KEY` as required; introduces `SECRET_KEY_PREVIOUS` for JWT rotation; adds test DB credentials; comments out optional DB/Redis/storage connection variables; adds `DEPLOY_DOMAIN` and multi-origin `CORS_ORIGINS`; enables `FEATURE_TRACING_ENABLED=true` and adds `FEATURE_CHAOS_API_ENABLED`; updates Netdata/Grafana demo credentials. |
| **Expanded lint pipeline and deploy gate alignment** <br> `.github/workflows/fastapi-test.yml`, `.github/workflows/deploy-production.yml` | Broadens lint job from ruff-only to "Lint (Python + JS + CSS + Markdown + YAML)"; adds Biome, markdownlint, Prettier, yamllint, shellcheck steps; increases timeout from 5 to 8 minutes; updates deploy-production pre-deploy required check name from "Lint with ruff" to match new lint output. |
| **GitHub Actions pinning and workflow hardening** <br> `.github/workflows/*.yml` | Pins `actions/checkout`, QEMU, Docker Buildx, container registry login, `astral-sh/setup-uv`, `docker/build-push-action`, and `sequoia-pgp/fast-forward` to immutable commit SHAs across all workflows; reformats CodeQL workflow triggers to include `push` and scheduled cron; adds `persist-credentials: false` to checkout steps; reformats fast-forward job condition into multi-line folded scalar. |
| **Deploy script and integration service hardening** <br> `.github/workflows/deploy-production.yml`, `.github/workflows/fastapi-test.yml` | Refactors deploy SSH known-hosts handling into multi-line validation script (checks `SSH_KNOWN_HOSTS` non-empty, ensures directory, idempotently appends); stages deploy payload locally, creates remote temp directory, copies files via `scp`, runs deploy with env vars, cleans up; updates integration service health checks to multi-line `--health-*` blocks; updates database initialization imports from `app.*` to `core.*`; updates post-deploy health check URL to use `${DEPLOY_DOMAIN}` from variables. |
| **YAML quoting and formatting standardization** <br> `.github/workflows/*.yml`, `.github/ISSUE_TEMPLATE/*`, `.github/DAILY_STANDUP.md`, `.github/test.sh` | Normalizes double-quoted YAML strings to single quotes across all workflow definitions and issue templates; improves bug-report.md with explicit prompt text, numbered steps (1–3), and structured spacing; adjusts feature_request.md and feature_request_request.md whitespace; reformats daily standup spacing and corrects "Ill" to "I'll"; updates Discord contact description with response speed note. |

**Skill and CI template content removal**

| Layer / File(s) | Summary |
|---|---|
| **Representative skill and template cleanup** <br> `.agents/skills/*`, `.kilo/agents/*`, `.kilocode/rules/*`, `.kilocode/skills/*`, `.kilocode/workflows/*` | Removes GitHub/project/issue/branch-linking skill files, kilo agent definitions and plans, kilocode API/auth/database/Docker/testing rules, continuous-testing/redis-development/fastapi skill documentation and references, ci-cd GitHub Actions/GitLab CI templates (docker-build, go-ci, node-ci, python-ci, security-scan, docker-compose), workflow documentation, all reference guides, helper scripts (ci_health.py, pipeline_analyzer.py, validate-pipeline.sh), and template assets. |

## Estimated code review effort

🎯 2 (Simple) | ⏱️ ~12 minutes

## Possibly related PRs

- [tomkabel/team21-vooglaadija#128](https://github.com/tomkabel/team21-vooglaadija/pull/128): Overlaps with `.github/workflows/deploy-production.yml` pre-deploy gating changes, SSH-based deploy flow refactoring, and `.env.example` configuration updates.

## Suggested reviewers

- Kevindaman

## Poem

> 🐇 Snip, snip, snip — the docs take flight,  
> Old workflows vanish in the night,  
> A tidy config now takes place,  
> With pinned actions keeping pace,  
> The rabbit cheers, a cleaner sight,  
> Infrastructure polished bright! ✨

</details>

<sub>✏️ Tip: You can configure your own custom pre-merge checks in the settings.</sub>

</details>

> [!WARNING]
> Billing warning: we have not been able to collect payment for this subscription for more than 72 hours. Please update the payment method or pay any pending invoices in Billing to avoid service interruption.

---

<sub>Comment `@coderabbitai help` to get the list of available commands and usage tips.</sub>

---

**@github-actions** commented (1 day ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (b825b6353ba74faa5f0df70ab159d20932c9e466).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit b825b6353ba74faa5f0df70ab159d20932c9e466 (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Sun Jun 21 12:24:29 2026 +0300

    chore: apply linter auto-fixes from pre-commit hooks
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (1 day ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (b825b6353ba74faa5f0df70ab159d20932c9e466).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit b825b6353ba74faa5f0df70ab159d20932c9e466 (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Sun Jun 21 12:24:29 2026 +0300

    chore: apply linter auto-fixes from pre-commit hooks
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (1 day ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (b825b6353ba74faa5f0df70ab159d20932c9e466).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit b825b6353ba74faa5f0df70ab159d20932c9e466 (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Sun Jun 21 12:24:29 2026 +0300

    chore: apply linter auto-fixes from pre-commit hooks
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (1 day ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (b4c0daeed0c0c88309bc568b3eb37b51776ac8ed).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit b4c0daeed0c0c88309bc568b3eb37b51776ac8ed (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Sun Jun 21 15:29:07 2026 +0300

    test(worker): avoid binding port in health cleanup test
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (1 day ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (b4c0daeed0c0c88309bc568b3eb37b51776ac8ed).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit b4c0daeed0c0c88309bc568b3eb37b51776ac8ed (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Sun Jun 21 15:29:07 2026 +0300

    test(worker): avoid binding port in health cleanup test
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (1 day ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (b4c0daeed0c0c88309bc568b3eb37b51776ac8ed).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit b4c0daeed0c0c88309bc568b3eb37b51776ac8ed (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Sun Jun 21 15:29:07 2026 +0300

    test(worker): avoid binding port in health cleanup test
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@kilo-code-bot** commented (1 day ago):

## Code Review Summary

**Status:** 10 Issues Found | **Recommendation:** Address before merge

### Overview
| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| WARNING | 9 |
| SUGGESTION | 0 |

<details>
<summary><b>Issue Details (click to expand)</b></summary>

#### CRITICAL
| File | Line | Issue |
|------|------|-------|
| `scripts/ensure_migration_chain.py` | 99 | Startup migration repair can stamp a stale schema to head without running missing DDL |

#### WARNING
| File | Line | Issue |
|------|------|-------|
| `app/api/routes/auth.py` | 319 | API logout now awaits token blacklist writes, but the new web logout path only clears cookies without revoking active tokens |
| `app/api/routes/downloads.py` | 320 | Failed-job list route precedence is now fixed by explicitly reordering static DLQ routes ahead of `/{job_id}` |
| `worker/outbox_relay.py` | 43 | Retry outbox recovery leaves stale pending rows behind when the Redis entry already exists |
| `worker/health.py` | 165 | Worker health checks no longer report worker-loop liveliness and can stay green while processing is wedged |
| `app/templates/base.html` | 58 | HTMX CSRF header listener registration is fixed, but the new skip link is rendered inside `<head>` |
| `docker-compose.demo.yml` | 84 | Demo Prometheus admin API is still enabled, though the port is now bound to localhost |
| `docker-compose.production.yml` | 144 | Certbot renewal no longer reloads nginx after replacing certificates |
| `app/api/routes/web/web_auth.py` | 140 | Demo login performs state-changing authentication on a CSRF-unprotected GET |
| `scripts/ensure_migration_chain.py` | 154 | Database connectivity failures are treated as a fresh database and reported as success |

</details>

<details>
<summary><b>Files Reviewed (10 files)</b></summary>

- `app/api/dependencies/__init__.py` - previous refresh-token auth issue resolved
- `app/api/routes/auth.py` - previous missing-await logout issue resolved; 1 new issue
- `app/api/routes/downloads.py` - previous `/{job_id}` shadowing issue resolved
- `worker/main.py` - previous shutdown bounding issue resolved
- `worker/processor.py` - previous circuit deferral and retry enqueue issues resolved
- `worker/outbox_relay.py` - 1 issue
- `worker/health.py` - 1 issue
- `app/templates/base.html` - previous CSRF listener issue resolved; 1 new issue
- `scripts/ensure_migration_chain.py` - 1 carried critical issue, 1 new warning
- `docker-compose.demo.yml` - carried warning remains, narrowed by localhost binding
- `docker-compose.production.yml` - 1 new issue
- `app/api/routes/web/web_auth.py` - 2 new issues

</details>

[Fix these issues in Kilo Cloud](https://app.kilo.ai/cloud-agent-fork/review/ffccbcb0-995d-4b82-8ac1-82dad0c9647e)

<details>
<summary><b>Previous Review Summary</b> (commit b4c0dae)</summary>

_Current summary above is authoritative. Previous snapshots are kept for context only._

### Previous review (commit b4c0dae)

**Status:** 10 Issues Found | **Recommendation:** Address before merge

### Overview
| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| WARNING | 9 |
| SUGGESTION | 0 |

<details>
<summary><b>Issue Details (click to expand)</b></summary>

#### CRITICAL
| File | Line | Issue |
|------|------|-------|
| `scripts/ensure_migration_chain.py` | 98 | Startup migration repair can stamp a stale schema to head without running missing DDL |

#### WARNING
| File | Line | Issue |
|------|------|-------|
| `app/api/dependencies/__init__.py` | 94 | Protected dependencies accept refresh tokens for normal API authorization |
| `app/api/routes/auth.py` | 316 | Logout calls async token blacklist writes without awaiting them |
| `app/api/routes/downloads.py` | 440 | Failed-job list route is shadowed by the dynamic `/{job_id}` route |
| `worker/main.py` | 305 | In-flight jobs are not bounded when shutdown starts after processing begins |
| `worker/processor.py` | 106 | Deferred jobs can be lost if Redis enqueue fails during circuit drain |
| `worker/processor.py` | 129 | Deferred queue may never drain because circuit timeout transition is not triggered |
| `worker/processor.py` | 783 | Retry outbox rows are deleted even when Redis enqueue returns failure |
| `app/templates/base.html` | 58 | HTMX CSRF header listener is registered before `document.body` exists |
| `docker-compose.demo.yml` | 73 | Demo Prometheus exposes the admin API on all host interfaces |

</details>

<details>
<summary><b>Files Reviewed (385 files)</b></summary>

- `app/api/dependencies/__init__.py` - 1 issue
- `app/api/routes/auth.py` - 1 issue
- `app/api/routes/downloads.py` - 1 issue
- `worker/main.py` - 1 issue
- `worker/processor.py` - 3 issues
- `app/templates/base.html` - 1 issue
- `scripts/ensure_migration_chain.py` - 1 issue
- `docker-compose.demo.yml` - 1 issue
- Remaining changed files - 0 new high-confidence issues

</details>

[Fix these issues in Kilo Cloud](https://app.kilo.ai/cloud-agent-fork/review/ec03e88d-f07d-4ced-b0c2-4c05c7911876)
</details>

---

<sub>Reviewed by gpt-5.4-2026-03-05 · Input: 104.2K · Output: 23.1K · Cached: 832K</sub>

---

**@github-actions** commented (25 minutes ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (70159c71068c9ab1c0a98ce47584b16052e843c1).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit 70159c71068c9ab1c0a98ce47584b16052e843c1 (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Mon Jun 22 16:07:51 2026 +0300

    fix(hooks): unblock pushes from eof fixer conflicts
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (22 minutes ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (70159c71068c9ab1c0a98ce47584b16052e843c1).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit 70159c71068c9ab1c0a98ce47584b16052e843c1 (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Mon Jun 22 16:07:51 2026 +0300

    fix(hooks): unblock pushes from eof fixer conflicts
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

**@github-actions** commented (10 minutes ago):

Triggered from https://github.com/tomkabel/team21-vooglaadija/pull/138#issuecomment-4761520640 by [@&ZeroWidthSpace;coderabbitai[bot]](https://github.com/coderabbitai[bot]).

Trying to  fast forward `main` (88f3dfb4fed0771e705b4984a86b3b11df20459a) to `dev/top1` (70159c71068c9ab1c0a98ce47584b16052e843c1).

Target branch (`main`):

```shell
commit 88f3dfb4fed0771e705b4984a86b3b11df20459a (HEAD -> main, origin/main)
Author: Tom Kristian Abel <191489531+tomkabel@users.noreply.github.com>
Date:   Fri May 1 19:33:11 2026 +0300

    feature(infra): CI/CD deployment (#128)
    
    * update outdated deploy script
    
    Signed-off-by: tomkabel <you@example.com>
    
    * feat: certbot via dns challenge
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix migrations
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fixes
    
    * ci: add automated production deployment workflow
    
    - Add deploy-production.yml with CI status gate, SSH deploy, and rollback
    - Add remote-deploy.sh for server-side atomic .env write, GHCR pull, migration, health check
    - Add SHA tags to docker.yml for immutable deployments
    
    Signed-off-by: tomkabel <you@example.com>
    
    * fix(deploy): migrate from Cloudflare global API key to scoped API token
    
    Replace CLOUDFLARE_API_KEY with CLOUDFLARE_API_TOKEN across the
    deployment config and scripts. Update the credentials file format
    to use dns_cloudflare_api_token instead of the email+key pair,
    which is the recommended certbot-dns-cloudflare plugin configuration.
    Make CLOUDFLARE_EMAIL optional since it is not required when using
    a scoped token. Fix the CF_CREDENTIALS_FILE path to point to the
    certbot directory (matching the docker-compose mount) instead of the
    data subdirectory. Tighten private key permissions to 600.
    
    * fix(certbot): make SSL renewal reload nginx via inline deploy-hook and aligned mounts
    
    Replace the shell function deploy_hook with a direct inline docker exec
    command in the --deploy-hook argument, so certbot can run it in its
    spawned subprocess where the function definition is not visible.
    Mount the Docker socket in the standalone certbot compose file so
    docker exec can reach the nginx container. Align the certbot Let's
    Encrypt volume mount (./infra/ssl -> ./infra/letsencrypt) with the
    nginx service mount so renewed certificates are immediately visible.
    
    * fix(deploy): correct health endpoint and remove unsafe rollback fallback
    
    Fix the health check endpoint from /api/v1/health to /health across
    the production deployment workflow and remote-deploy script, matching
    the FastAPI router mount (app.include_router(health.router) which
    registers at /health). Pin actions/checkout to v4 tag instead of an
    invalid 41-character SHA. Remove the unreachable fallback
    (${BACKUP_API:-${API_IMAGE}}) in the rollback override since earlier
    checks already exit when backup images are empty.
    
    * ci: trigger Docker image builds on push and release
    
    Add push (main branch) and release (published) triggers alongside
    workflow_dispatch so that ${{ github.sha }} image tags are built
    automatically and available for deployment workflows.
    
    * chore(docker): remove Postgres host port binding from base compose
    
    Remove the 127.0.0.1:5432:5432 port mapping from the base
    docker-compose.yml so production deployments no longer reserve
    the host port. The binding should be added in a local override
    when host access to the database is needed.
    
    * fix(deploy): harden SSH known_hosts handling and parameterize server IP
    
    - Replace append (>>) with overwrite (>) for known_hosts to prevent
      duplicate entries accumulating across workflow runs
    - Add mkdir -p ~/.ssh guard before writing known_hosts
    - Replace hardcoded server IP with secrets.SERVER_IP to avoid workflow
      breakage if the server address changes
    
    * fix(deploy): check known_hosts before appending to avoid duplicates
    
    Use grep -qF to test whether the SSH host key already exists in
    known_hosts before appending. This avoids duplicate entries (from
    unconditional >>) while preserving unrelated entries (from > overwrite).
    
    Applies the recommended pattern: check first, append only if missing.
    
    ---------
    
    Signed-off-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <you@example.com>
    Co-authored-by: tomkabel <email@address.com>
```

Pull request (`dev/top1`):

```shell
commit 70159c71068c9ab1c0a98ce47584b16052e843c1 (pull_request/dev/top1)
Author: tomkabel <191489531+tomkabel@users.noreply.github.com>
Date:   Mon Jun 22 16:07:51 2026 +0300

    fix(hooks): unblock pushes from eof fixer conflicts
```
curl: (3) bad range in URL position 85:
https://api.github.com/repos/tomkabel/team21-vooglaadija/collaborators/coderabbitai[bot]/permission
                                                                                    ^

---

## 📝 Review Summaries

**@github-advanced-security** left a review [**COMMENTED**] (

## 💻 Unresolved Inline Comments

**@kilo-code-bot** commented on `alembic/versions/003_add_error_category_and_failed_jobs.py:36`:

**CRITICAL:** `final_error_category` column can't be populated

The migration creates `failed_jobs.final_error_category` with `nullable=False`, but the runtime code never sets it. `_move_to_dlq` in `worker/processor.py` inserts `FailedJob` rows without providing this field, and the ORM model (`app/models/failed_job.py`) doesn't define it either. As soon as the worker tries to persist a failed job, SQLAlchemy will raise an `IntegrityError` because the NOT NULL column is missing. Either drop the column or add the field to the model and populate it before inserting so the DLQ path works.
---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `app/api/dependencies/__init__.py:outdated`:

**WARNING:** Protected API routes accept refresh tokens as access tokens

Both auth dependencies pass `expected_type=None` into token verification, so a bearer refresh token satisfies `CurrentUser` and can call protected API endpoints directly. Refresh tokens are longer-lived and should only be accepted by the refresh flow; require `ACCESS_TOKEN_TYPE` here (and in the cookie dependency) so only access tokens authorize normal routes.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `app/api/routes/auth.py:outdated`:

**WARNING:** Logout never awaits blacklist writes

`blacklist_token` is async, but this helper calls the injected `blacklist_fn` without awaiting it. Logout clears the cookies but never actually writes the token JTI to Redis, so a copied access or refresh token remains usable until it expires.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `app/api/routes/downloads.py:320`:

**WARNING:** Failed-job list route is shadowed by `/{job_id}`

`GET /failed` is registered after `GET /{job_id}`, and Starlette matches routes in declaration order. Requests to `/api/v1/downloads/failed` are captured as `job_id=failed` and return an invalid UUID error, so the new DLQ list endpoint is unreachable unless it is declared before the dynamic job route.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `worker/main.py:outdated`:

**WARNING:** In-flight jobs are not bounded when shutdown starts mid-processing

When no shutdown is pending before a job starts, this branch awaits `current_job_task` without a timeout. If SIGTERM arrives while extraction is running, the main loop stays blocked until the full job attempt finishes, so the worker can exceed the orchestrator grace period and get killed before the job is requeued.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `worker/processor.py:outdated`:

**WARNING:** Deferred jobs can be lost when Redis enqueue fails

The code removes members from `circuit_deferred_queue` before this call and then ignores the boolean returned by `push_to_download_queue()`. If Redis fails, the DB status is still committed back to `pending` with no queue entry or outbox row, leaving the job stranded.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `worker/processor.py:outdated`:

**WARNING:** Deferred jobs may never drain after the circuit timeout

`is_open` only reads the stored circuit state; the OPEN -> HALF_OPEN transition now happens inside `can_execute()`. If all jobs were deferred, no new extraction calls `can_execute()`, so this check keeps returning open forever and the deferred queue can remain stuck after `reset_timeout` has elapsed.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `worker/processor.py:outdated`:

**WARNING:** Retry outbox rows are deleted even when enqueue fails

`push_to_retry_queue()` catches Redis errors and returns `False`, but this caller does not check the result before deleting the outbox entry. A Redis outage can therefore leave the job marked `pending` with `next_retry_at` but no retry-queue entry and no outbox recovery path.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `app/templates/base.html:outdated`:

**WARNING:** CSRF header hook is registered before `document.body` exists

This script runs in `<head>`, where `document.body` is still `null`, so the `addEventListener` call throws and the HTMX `X-CSRF-Token` header is never installed. New HTMX actions without a hidden `csrf_token` field, such as the chaos lab reset button, will consistently fail CSRF validation.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `scripts/ensure_migration_chain.py:99`:

**CRITICAL:** Startup can stamp an out-of-date schema as current

When the database revision is missing from the filesystem, this script truncates `alembic_version` and inserts the current head before `alembic upgrade head` runs. That bypasses all intervening migration DDL, so production can be marked up-to-date while required columns/tables were never created, leading to runtime failures and schema drift.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `docker-compose.demo.yml:84`:

**WARNING:** Prometheus admin API is exposed by the demo override

This enables Prometheus' admin API while the service is also published as `9090:9090`, which binds to all host interfaces by default. If this override is run on a reachable host, unauthenticated network users can access destructive/admin endpoints; bind the port to localhost or leave the admin API disabled for demo stacks.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@aikido-pr-checks** commented on `uv.lock:outdated`:

**GHSA-4xgf-cpjx-pc3j in pydantic-settings** - medium severity
### Summary

`NestedSecretsSettingsSource` reads secret values from files in a configured `secrets_dir`. When `secrets_nested_subdir=True`, a directory entry inside `secrets_dir` that is a symbolic link pointing **outside** `secrets_dir` is followed, so files outside the configured directory are read into settings values. The same code path bypasses the documented `secrets_dir_max_size` protection. An attacker or lower-privileged component able to influence entries in the configured secrets directory (for example, a writable or shared secrets mount) can turn this into an unintended local file read into settings and can defeat the advertised loading-size cap. This report does not claim network reachability by itself.

### Details

`NestedSecretsSettingsSource` performed two passes over `secrets_dir` using two different, inconsistent directory-traversal implementations:

* The size check in `validate_secrets_path()` used `Path.glob('**/*')`, which does **not** descend into a symbolically-linked directory.
* The loader in `load_secrets()` used `glob.iglob(f'{path}/**/*', recursive=True)` followed by `read_text()`, which **does** follow symlinked directories and reads through the link target.

Because the two passes disagreed on symlinks, a symlinked directory inside `secrets_dir` whose target lives elsewhere was invisible to the size accounting (counted as 0 bytes) while still being fully read by the loader. This produces two distinct problems:

1. **Out-of-tree read (CWE-22 / CWE-59).** A symlinked directory (or file) inside `secrets_dir` that resolves outside it is followed, and the external file's contents are loaded into the corresponding settings field.
2. **`secrets_dir_max_size` bypass (CWE-400).** The size check never sees the out-of-tree content, so the documented size cap is neither respected nor able to reject the oversized external file. A related amplification exists for cyclic in-tree symlinks, which `glob.iglob(recursive=True)` re-traverses, inflating the size accounting and the number of loaded secrets.

#### Reproduction

In a clean Linux container, with a `secrets_dir` containing a symlink `secrets/db -> /path/outside` and an `outside/passwd` file of 512 bytes, while `secrets_dir_max_size=100`:

```python
from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    NestedSecretsSettingsSource,
)

class Db(BaseModel):
    passwd: str | None = None

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        secrets_dir='secrets',
        secrets_nested_subdir=True,
        secrets_dir_max_size=100,  # outside/passwd is 512 bytes
    )
    db: Db = Db()

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (NestedSecretsSettingsSource(file_secret_settings),)
```

On affected versions, `Settings().db.passwd` is populated with the 512-byte out-of-tree file and **no** `SettingsError` is raised, even though the file exceeds `secrets_dir_max_size`.

### Impact

Applications that opt into `NestedSecretsSettingsSource` with `secrets_nested_subdir=True` and load secrets from a directory whose entries can be influenced by an attacker or a lower-privileged component (for example, a writable or shared secrets mount, or a secrets directory partially populated from untrusted input) are affected. The impact is:

* **Confidentiality:** files outside the configured `secrets_dir` can be read into settings values (local file read).
* **Integrity / availability of the safeguard:** the advertised `secrets_dir_max_size` cap can be bypassed, and cyclic symlinks can inflate resource usage during loading.

The vulnerability requires the ability to place a symbolic link inside the configured secrets directory; it is not remotely reachable on its own. Applications that do not use `NestedSecretsSettingsSource`, or that point `secrets_dir` at a directory fully under the application's control, are not affected.

### Mitigation

Upgrade to **pydantic-settings 2.14.2**, which:

* walks the secrets directory explicitly and only descends into directories whose resolved path stays within `secrets_dir`, so symlinked directories pointing outside are never followed;
* uses a single, cycle-safe iterator for both the size check and the loader, so the size accounting and the loaded set are always consistent and each real directory is visited at most once;
* skips any file whose resolved path escapes `secrets_dir`, as defense in depth.

If upgrading is not immediately possible, ensure the configured `secrets_dir` is fully owned and controlled by the application (no writable or attacker-influenced entries), or avoid `secrets_nested_subdir=True`.

<details><summary>Details</summary>

**Remediation** Aikido suggests bumping this package to version 2.14.2 to resolve this issue

<sub>Reply `@AikidoSec ignore: [REASON]` to ignore this issue.</sub>
[More info](https://app.aikido.dev/repositories/1694931/pull_requests/138/latest?groupId=11274)
</details>

---
**@github-advanced-security** commented on `app/static/js/dashboard.js:605`:

## CodeQL / DOM text reinterpreted as HTML

[DOM text](1) is reinterpreted as HTML without escaping meta-characters.

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/106)

---
**@github-advanced-security** commented on `app/static/js/dashboard.js:613`:

## CodeQL / DOM text reinterpreted as HTML

[DOM text](1) is reinterpreted as HTML without escaping meta-characters.

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/107)

---
**@github-advanced-security** commented on `tests/test_story_3_6_main_decomposition.py:140`:

## CodeQL / Incomplete URL substring sanitization

The string [https://cdn.jsdelivr.net](1) may be at an arbitrary position in the sanitized URL.

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/109)

---
**@github-advanced-security** commented on `tests/test_story_3_6_main_decomposition.py:143`:

## CodeQL / Incomplete URL substring sanitization

The string [https://cdn.jsdelivr.net](1) may be at an arbitrary position in the sanitized URL.

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/110)

---
**@github-advanced-security** commented on `app/api/routes/web/web_downloads.py:148`:

## CodeQL / Information exposure through an exception

[Stack trace information](1) flows to this location and may be exposed to an external user.

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/111)

---
**@github-advanced-security** commented on `app/api/routes/web/web_helpers.py:104`:

## CodeQL / Construction of a cookie using user-supplied input

Cookie is constructed from a [user-supplied input](1).
Cookie is constructed from a [user-supplied input](2).
Cookie is constructed from a [user-supplied input](3).
Cookie is constructed from a [user-supplied input](4).
Cookie is constructed from a [user-supplied input](5).

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/108)

---
**@github-advanced-security** commented on `app/api/routes/web/web_helpers.py:178`:

## CodeQL / Information exposure through an exception

[Stack trace information](1) flows to this location and may be exposed to an external user.
[Stack trace information](2) flows to this location and may be exposed to an external user.

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/112)

---
**@kilo-code-bot** commented on `app/api/routes/web/web_auth.py:140`:

**WARNING:** State-changing demo login uses an unprotected GET

This endpoint mints auth cookies and rotates the CSRF token on a `GET`, so a third-party page can trigger a top-level navigation that silently logs a browser into the shared demo account. Login should require a deliberate `POST` protected by the existing CSRF checks instead of a side-effecting `GET`.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `app/api/routes/web/web_auth.py:165`:

**WARNING:** Web logout only clears cookies

Unlike `/api/v1/auth/logout`, this handler never blacklists the current access and refresh token JTIs before returning. Any copied JWT remains valid until expiry after a user logs out through the web UI, which weakens token revocation and makes the two logout paths behave inconsistently.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `worker/outbox_relay.py:43`:

**WARNING:** Duplicate retry entries never clear their pending outbox row

`push_to_retry_queue()` returns `False` both when Redis is unavailable and when the job is already present in `retry_queue`. After a crash between the original `ZADD` and the outbox delete, this relay will keep seeing the same pending row, treat the duplicate as a failure, and never remove it, so recovery leaves permanently stale retry outbox entries behind.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `worker/health.py:165`:

**WARNING:** Worker health now ignores worker-process liveliness

This endpoint returns `200` whenever Redis and the database respond, even if the worker loop is wedged and `_worker_state` has stopped advancing. Because the health server runs separately from the job-processing loop, the container can stay healthy while no jobs are actually being processed.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `docker-compose.production.yml:144`:

**WARNING:** Certbot renewal no longer reloads nginx

The deploy hook now only touches `.nginx-reload-required`, but nothing in this compose stack or the changed deploy scripts consumes that sentinel file. Renewed certificates will sit on disk until a manual reload or container restart, so the server can continue serving an expired certificate after auto-renewal.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@kilo-code-bot** commented on `scripts/ensure_migration_chain.py:154`:

**WARNING:** Connection failures are treated as a fresh database

`_get_db_revision()` returns `None` both for an unversioned database and for connection/query failures. This branch therefore reports success during startup when the database is unreachable or `alembic_version` cannot be read, masking the real operational error behind the fresh-database path.

---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
**@coderabbitai** commented on `.github/workflows/deploy-production.yml:160`:

**Health gate can report success even when dependencies are unhealthy.**

Line 159 only checks for 2xx. The `/health` handler returns `200` with `"status":"unhealthy"` when Redis/DB checks fail, so this can pass a bad deploy.

<details>
<summary>Suggested fix</summary>

```diff
-            if curl -sf --max-time 10 "https://${DEPLOY_DOMAIN}/health"; then
+            if curl -fsS --max-time 10 "https://${DEPLOY_DOMAIN}/health" | grep -q '"status":"healthy"'; then
               echo "Health check passed"
               exit 0
             fi
```
</details>

---
**@coderabbitai** commented on `app/api/routes/web/web_helpers.py:226`:

**Remove duplicate ID attribute; both containers have `id="download-rows"`.**

The filled state container (line 30) and empty state container (line 43) both define `id="download-rows"`. HTML IDs must be unique within a document. Since the template uses `{% if jobs %} ... {% else %} ... {% endif %}`, only one is rendered per request, so functionality may not break, but:

1. Violates HTML5 specification
2. Breaks CSS/JS selectors that depend on unique IDs
3. Confuses accessibility tools and screen readers

**Solution:** Keep the ID on the primary (filled) state (line 30) and remove it from the empty state (line 43), or use a different ID for the empty state.

<details>
<summary>🔧 Proposed fix</summary>

```diff
  {% else %}
  <div
    id="download-rows-empty"
    role="feed"
    aria-label="Downloads"
    aria-live="polite"
    aria-atomic="false"
    aria-busy="false"
  >
```
</details>

<details>
<summary>🧰 Tools</summary>

<details>
<summary>🪛 HTMLHint (1.9.2)</summary>

[error] 43-43: The id value [ download-rows ] must be unique.

(id-unique)

</details>

</details>

_Source: Linters/SAST tools_

---
**@coderabbitai** commented on `docs/architecture-worker.md:89`:

_🧹 Nitpick_ | _🔵 Trivial_ | _💤 Low value_

**Minor: Spell out time units for formal documentation.**

Lines 86–87 use abbreviated time units (`5min`, `15min`). For formal architecture documentation, prefer the expanded forms (`5 minutes`, `15 minutes`) to improve readability and professionalism.

<details>
<summary>📝 Suggested edits</summary>

```diff
- Periodic poll (every 5min) that finds jobs stuck in `processing` with `updated_at > 15min` cutoff
+ Periodic poll (every 5 minutes) that finds jobs stuck in `processing` with `updated_at > 15 minutes` cutoff
```
</details>

<details>
<summary>🧰 Tools</summary>

<details>
<summary>🪛 LanguageTool</summary>

[grammar] ~86-~86: Ensure spelling is correct
Context: ...## Zombie Sweeper  Periodic poll (every 5min) that finds jobs stuck in `processing` ...

(QB_NEW_EN_ORTHOGRAPHY_ERROR_IDS_1)

---

[grammar] ~86-~86: Ensure spelling is correct
Context: ...t finds jobs stuck in `processing` with `updated_at > 15min` cutoff and requeues them. Protected by ...

(QB_NEW_EN_ORTHOGRAPHY_ERROR_IDS_1)

</details>

</details>

_Source: Linters/SAST tools_

---
**@coderabbitai** commented on `frontend/css/src/styles.css:551`:

**Fix keyframe name to use kebab-case per CSS conventions.**

Line 542 defines `@keyframes successBoxFadeOut` using camelCase, but Stylelint enforces kebab-case naming for CSS keyframes (a common standard across projects). The current name should be `success-box-fade-out`.

This requires updating both the keyframe definition and its reference at line 235 (animation: successBoxFadeOut).

<details>
<summary>🔧 Proposed fix</summary>

```diff
- `@keyframes` successBoxFadeOut {
+ `@keyframes` success-box-fade-out {
    0% {
      opacity: 1;
      transform: translateY(0);
    }
    100% {
      opacity: 0;
      transform: translateY(-4px);
    }
  }
```

And update the reference:
```diff
  .success-box-exit {
-   animation: successBoxFadeOut 0.3s ease-out forwards;
+   animation: success-box-fade-out 0.3s ease-out forwards;
  }
```
</details>

<details>
<summary>🧰 Tools</summary>

<details>
<summary>🪛 Stylelint (17.13.0)</summary>

[error] 542-542: Expected keyframe name "successBoxFadeOut" to be kebab-case (keyframes-name-pattern)

(keyframes-name-pattern)

</details>

</details>

_Source: Linters/SAST tools_

---
**@coderabbitai** commented on `ISSUES.md:586`:

**Critical: Migration 003 has a NOT NULL column that runtime code never populates.**

The documented issue at line 581–585 flags that `final_error_category` was created with `nullable=False` in migration 003, but the ORM model and worker code don't set this field. This will cause `IntegrityError` when the worker tries to insert failed jobs. This concern is external to the files in this review batch but represents a genuine functional correctness issue that must be resolved in the migration and model definitions.

---