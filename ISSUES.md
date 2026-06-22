## 💬 Standard Comments

**@coderabbitai** commented (2 hours ago):

<details>
<summary>📝 Walkthrough</summary>

## Summary by CodeRabbit

* **Documentation**
  * Removed internal agent skills and knowledge base documentation.

* **Configuration**
  * Added `.editorconfig` for consistent code formatting across file types.
  * Simplified `.env.example` template with essential variables only.
  * Updated `.gitignore` to exclude additional tool artifacts.

* **Issue Templates**
  * Enhanced GitHub issue templates with improved formatting and consistency.
  * Updated Discord community link guidance.

* **Chores**
  * Removed internal CI/CD workflow templates and auxiliary scripts.
  * Cleaned up legacy workflow configuration and skill definitions.

## Walkthrough

The PR adds `.editorconfig`, rewrites `.env.example` with a condensed structure, expands the CI lint job to cover JS/CSS/Markdown/YAML/shell tooling (Biome, markdownlint, Prettier, yamllint, shellcheck), normalizes YAML quoting across GitHub workflows and issue templates, updates `.gitignore`, and mass-deletes all content from `.agents/`, `.kilo/`, and `.kilocode/` skill/workflow/rules documentation directories.

## Changes

**CI and project tooling improvements**

| Layer / File(s) | Summary |
|---|---|
| **Editor config, gitignore, and expanded lint CI job** <br> `.editorconfig`, `.gitignore`, `.github/workflows/fastapi-test.yml`, `.github/workflows/deploy-production.yml` | Adds `.editorconfig` with global and per-filetype rules, adds `.gitignore` entries for AI agent artifacts and formatter caches, expands the lint job with pnpm/Node 22 running Biome/markdownlint/Prettier/yamllint/shellcheck, increases its timeout to 8 min, and updates the deploy gate's required check name to match. |
| **`.env.example` rewrite** <br> `.env.example` | Replaces the prior verbose template with a condensed version: required DB/Redis passwords and `SECRET_KEY`, commented optional fields, expanded `CORS_ORIGINS`, `FEATURE_TRACING_ENABLED=true`, and new `FEATURE_CHAOS_API_ENABLED=false`. |
| **GitHub workflow and issue template normalization** <br> `.github/workflows/codeql.yml`, `.github/workflows/fast-forward.yml`, `.github/ISSUE_TEMPLATE/*`, `.github/DAILY_STANDUP.md`, `.github/test.sh` | Normalizes double-to-single YAML quoting across workflows and issue templates, reformats multi-line YAML blocks in deploy and fast-forward workflows, improves bug-report placeholder text, updates Discord contact description, and removes trailing whitespace. |

**AI agent and skill documentation deletion**

| Layer / File(s) | Summary |
|---|---|
| **Mass deletion of agent skills, rules, workflows, and CI templates** <br> `.agents/skills/*`, `.kilo/agents/*`, `.kilo/plans/*`, `.kilocode/rules/*`, `.kilocode/skills/*`, `.kilocode/workflows/*` | Removes all content from agent skill definitions (gh-issue-management, htmx-expert, etc.), kilo agent prompts and plans, kilocode API/auth/db/docker/testing rules, all ci-cd GitHub Actions and GitLab CI template YAMLs and reference docs, redis-development rules, continuous-testing references, and workflow documentation files. |

## Estimated code review effort

🎯 2 (Simple) | ⏱️ ~10 minutes

## Possibly related PRs

- [tomkabel/team21-vooglaadija#52](https://github.com/tomkabel/team21-vooglaadija/pull/52): Also modifies `.env.example` with overlapping DB/SECRET_KEY/CORS variables.
- [tomkabel/team21-vooglaadija#74](https://github.com/tomkabel/team21-vooglaadija/pull/74): Added the `.agents/skills/htmx-expert` skill files (hooks, schemas, scripts) that this PR deletes.
- [tomkabel/team21-vooglaadija#128](https://github.com/tomkabel/team21-vooglaadija/pull/128): Directly overlaps with edits to `.github/workflows/deploy-production.yml` and `.env.example`.

## Suggested labels

`type-feature`, `type-infra`, `area-architecture`, `security`, `type-refactor`

## Suggested reviewers

- Kevindaman

## Poem

> 🐇 Snip, snip, snip — the docs are gone,
> Old skill files swept away at dawn,
> A tidy `.editorconfig` in place,
> Lint checks now run with style and grace,
> The rabbit hops through cleaner code,
> No stale markdown to bear the load! ✨

</details>

<sub>✏️ Tip: You can configure your own custom pre-merge checks in the settings.</sub>

</details>

</details>

> [!WARNING]
> Billing warning: we have not been able to collect payment for this subscription for more than 72 hours. Please update the payment method or pay any pending invoices in Billing to avoid service interruption.

---

<sub>Comment `@coderabbitai help` to get the list of available commands and usage tips.</sub>

---

**@github-actions** commented (2 hours ago):

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

**@github-actions** commented (2 hours ago):

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

**@github-actions** commented (1 hour ago):

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

## 📝 Review Summaries

**@github-advanced-security** left a review [**COMMENTED**] (

## 💻 Unresolved Inline Comments

**@github-advanced-security** commented on `.github/workflows/fastapi-test.yml:50`:

## CodeQL / Unpinned tag for a non-immutable Action in workflow or composite action

Unpinned 3rd party Action 'FastAPI REST API Tests' step [Uses Step](1) uses 'pnpm/action-setup' with ref 'v4', not a pinned commit hash

[Show more details](https://github.com/tomkabel/team21-vooglaadija/security/code-scanning/104)

---
**@coderabbitai** commented on `.env.example:45`:

**Fix the apostrophe in the standup template.**

`What Ill work on` reads like a typo in the four "Today" placeholders; it should be `What I'll work on`.

<details>
<summary>Suggested fix</summary>

```diff
- [Team Member 1]: What Ill work on
+ [Team Member 1]: What I'll work on
```

</details>

---
**@coderabbitai** commented on `.github/workflows/codeql.yml:41`:

**Keep the redirect attached to `grep`.**

Splitting this one-liner across YAML lines detaches `2>/dev/null` from `grep`, so the `|| ...` fallback no longer runs when `known_hosts` is empty. Fresh runners can fail here.

<details>
<summary>🔧 Proposed fix</summary>

```diff
-      - name: Add known hosts
-        run:
-          mkdir -p ~/.ssh && grep -qF "${{ secrets.SSH_KNOWN_HOSTS }}" ~/.ssh/known_hosts
-          2>/dev/null || echo "${{ secrets.SSH_KNOWN_HOSTS }}" >> ~/.ssh/known_hosts
+      - name: Add known hosts
+        run: |
+          mkdir -p ~/.ssh
+          grep -qF "${{ secrets.SSH_KNOWN_HOSTS }}" ~/.ssh/known_hosts 2>/dev/null || \
+            printf '%s
' "${{ secrets.SSH_KNOWN_HOSTS }}" >> ~/.ssh/known_hosts
```
</details>

<details>
<summary>🧰 Tools</summary>

<details>
<summary>🪛 zizmor (1.25.2)</summary>

[warning] 100-100: code injection via template expansion (template-injection): may expand into attacker-controllable code

(template-injection)

---

[warning] 101-101: code injection via template expansion (template-injection): may expand into attacker-controllable code

(template-injection)

</details>

</details>

---
**@coderabbitai** commented on `.github/workflows/fastapi-test.yml:58`:

_🧹 Nitpick_ | _🔵 Trivial_

**Consider pinning actions to commit SHAs for consistency.**

`pnpm/action-setting@v4` and `actions/setup-node@v4` use mutable version tags. While the repository does pin some actions to commit SHAs in other workflows (e.g., `astral-sh/setup-uv@a0b52019...`, `codecov/codecov-action@57e3a136...`), this practice is not consistently enforced throughout—the same file uses mutable tags elsewhere (`actions/checkout@v4`, `docker/build-push-action@v6`). Pinning to SHAs mitigates supply-chain risk from unexpected upstream changes.

<details>
<summary>🧰 Tools</summary>

<details>
<summary>🪛 GitHub Check: CodeQL</summary>

[warning] 50-50: Unpinned tag for a non-immutable Action in workflow or composite action
Unpinned 3rd party Action 'FastAPI REST API Tests' step [Uses Step](1) uses 'pnpm/action-setup' with ref 'v4', not a pinned commit hash

</details>
<details>
<summary>🪛 zizmor (1.25.2)</summary>

[error] 50-50: unpinned action reference (unpinned-uses): action is not pinned to a hash (required by blanket policy)

(unpinned-uses)

---

[error] 55-55: unpinned action reference (unpinned-uses): action is not pinned to a hash (required by blanket policy)

(unpinned-uses)

</details>

</details>

_Source: Linters/SAST tools_

---
**@coderabbitai** commented on `.github/workflows/fastapi-test.yml:79`:

**Don't suppress ShellCheck failures.**

`|| true` makes the new shell-lint step pass even when ShellCheck reports problems, so the lint job no longer enforces the check it advertises.

<details>
<summary>🔧 Proposed fix</summary>

```diff
-      - name: Run ShellCheck
-        run: shellcheck --severity=warning scripts/*.sh entrypoint.sh migrate.sh || true
+      - name: Run ShellCheck
+        run: shellcheck --severity=warning scripts/*.sh entrypoint.sh migrate.sh
```
</details>

---
**@kilo-code-bot** commented on `alembic/versions/003_add_error_category_and_failed_jobs.py:36`:

**CRITICAL:** `final_error_category` column can't be populated

The migration creates `failed_jobs.final_error_category` with `nullable=False`, but the runtime code never sets it. `_move_to_dlq` in `worker/processor.py` inserts `FailedJob` rows without providing this field, and the ORM model (`app/models/failed_job.py`) doesn't define it either. As soon as the worker tries to persist a failed job, SQLAlchemy will raise an `IntegrityError` because the NOT NULL column is missing. Either drop the column or add the field to the model and populate it before inserting so the DLQ path works.
---
Reply with `@kilocode-bot fix it` to have Kilo Code address this issue.

---
