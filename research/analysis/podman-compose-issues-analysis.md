# Root Cause Analysis: podman-compose Startup Issues

> **Plan File:** `.kilo/plans/podman-compose-issues-analysis.md` (to be renamed from current)
> 
> This document analyzes root causes and proposed fixes for podman-compose startup failures.

## Executive Summary

Three distinct issues were identified when running `podman-compose up --build`. This document analyzes the root causes of each and proposes remediation steps.

---

## Issue 1: HEALTHCHECK Not Supported for OCI Image Format

### Error/Warning Output

```text
WARN[0027] HEALTHCHECK is not supported for OCI image format and will be ignored. Must use `docker` format
```

### Root Cause Analysis

**Technical Background:**

- Docker and Podman use different container image formats by default
- **Docker** uses a legacy "docker" format (Docker Image Specification v1/v2)
- **Podman** defaults to OCI (Open Container Initiative) format for images
- The OCI Image Specification does NOT include a `HEALTHCHECK` instruction
- `HEALTHCHECK` is a Docker-specific instruction that was never standardized in OCI

**Why This Happens:**

1. When podman-compose builds or pulls images, Podman uses OCI format by default
1. The `healthcheck` directive in `docker-compose.yml` translates to `HEALTHCHECK` in the Dockerfile
1. Podman cannot parse this instruction because OCI images don't support it
1. The warning indicates Podman will **ignore** the healthcheck entirely

**Impact:**

- `depends_on: condition: service_healthy` will NOT work correctly
- Services that depend on health checks will start immediately without waiting
- Container health is not monitored

**Podman GitHub Issue:** containers/podman#25454 (open, tracking this exact issue)

### Fix Options (in priority order)

| Option | Description | Effort |
|--------|-------------|--------|
| 1. Set `COMPOSE_FORMAT=docker` | Tell podman-compose to use Docker image format | Low |
| 2. Use `--format=docker` flag | Run podman-compose with docker format | Low |
| 3. Configure `/etc/containers/containers.conf` | Set global default for all Podman operations | Medium |
| 4. Wait for Podman OCI healthcheck support | No current timeline | N/A |

---

## Issue 2: DB Container - crun exec.fifo Error

### Error/Warning Output

```text
[db]     | cannot open `/run/user/1000/crun/748eed07e3b707e12fdfc2d55b5f0e23fce2401b1c4acae298dc0fe7f87f0319/exec.fifo`: No such file or directory
[db]     | Error: unable to start container 748eed07e3b707e12fdfc2d55b5f0e23fce2401b1c4acae298dc0fe7f87f0319: `/usr/bin/crun start 748eed07e3b707e12fdfc2d55b5f0e23fce2401b1c4acae298dc0fe7f87f0319` failed: exit status 1
```

### Root Cause Analysis

**Technical Background:**

- `crun` is a lightweight OCI container runtime written in C
- `exec.fifo` is a named pipe (FIFO) used for communication between the runtime and the container's init process
- The FIFO is created by crun before container start and must exist before the container can exec commands

**Root Causes (multiple potential factors):**

1. **crun Race Condition / Bug**
  - crun may be attempting to access the fifo before it's fully created
  - Known bug in certain crun versions when used with podman-compose
  - Referenced in: containers/podman-compose#1072 (open issue)

1. **Filesystem Timing Issue**
  - The directory `/run/user/1000/crun/` may not be fully populated before crun tries to use it
  - podman-compose doesn't wait for runtime initialization before starting next container
  - Common in environments with slower filesystem operations

1. **Podman/crun Version Mismatch**
  - Some Podman 4.x/5.x versions have regressions with crun
  - Particularly on systems with newer kernel/fuse combinations

1. **User Namespace Mapping Issue**
  - In rootless Podman, the exec.fifo path includes the user UID (1000)
  - If the UID mapping isn't established before crun runs, FIFO creation fails

**Impact:**

- Database container fails to start entirely
- All dependent services (api, worker) will fail due to `depends_on: condition: service_healthy`
- Complete application outage

**Known Issue:** containers/podman-compose#1072 (open since Nov 2024, no fix released)

### Fix Options (in priority order)

| Option | Description | Effort |
|--------|-------------|--------|
| 1. Switch to `runc` runtime | Use runc instead of crun via `~/.config/containers/containers.conf` | Low |
| 2. Update crun/podman | Ensure latest versions installed | Low |
| 3. Manual workaround | Create `/run/user/1000/crun` with correct permissions before startup | Medium |
| 4. Use Docker instead | Avoid podman-compose entirely | Medium |

### Configuration Fix for runc

Create `~/.config/containers/containers.conf`:

```toml
[runtime]
runtime = "/usr/bin/runc"
```

Or set via environment:

```bash
podman --runtime /usr/bin/runc-compose up --build
```

---

## Issue 3: Redis Configuration Parsing Error

### Error/Warning Output

```text
[redis]  |
[redis]  | *** FATAL CONFIG FILE ERROR (Redis 7.4.8) ***
[redis]  | Reading the configuration file, at line 2
[redis]  | >>> 'requirepass "${REDIS_PASSWORD:?REDIS_PASSWORD" "is" "required}"'
[redis]  | wrong number of arguments
```

### Root Cause Analysis

**Technical Background:**

- Docker Compose performs **variable interpolation** (not shell expansion)
- `${VAR:?message}` is **shell syntax** for "throw error if VAR is unset"
- Docker Compose's interpolation is **NOT shell expansion**
- The syntax `${VAR:?message}` is being passed LITERALLY to Redis

**What Actually Happened:**

Looking at the original `docker-compose.yml`:

```yaml
redis:
  command: >
    redis-server
    --requirepass ${REDIS_PASSWORD:?REDIS_PASSWORD is required}
    ...
```

1. Docker Compose interpolates `${REDIS_PASSWORD:?REDIS_PASSWORD is required}` with the actual password value from `.env` (e.g., `redispass123`)
1. The shell expansion syntax `${VAR:?message}` is NOT interpreted - it's passed as literal text
1. Redis receives: `requirepass "redispass123" "is" "required}"` (malformed)
1. Redis interprets this as `requirepass` command with **4 arguments** instead of 1

**Why This Is Confusing:**

The error message shows the **pre-interpolation** syntax:

```text
'requirepass "${REDIS_PASSWORD:?REDIS_PASSWORD" "is" "required}"'
```

This suggests the interpolation may have partially worked but the shell-like syntax confused the parser. The actual issue is that Docker Compose does **variable interpolation only**, not shell parameter expansion.

**Impact:**

- Redis container fails to start
- All services depending on Redis will fail

### Fix Options

| Option | Description | Effort |
|--------|-------------|--------|
| 1. Use exec form (JSON array) | `command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]` | Low |
| 2. Remove shell syntax | Just use `${REDIS_PASSWORD}` without `:?message` | Low |
| 3. Use separate env_file | Pass password via environment instead of command | Medium |

**Recommended Fix (already applied):**

```yaml
redis:
  command:
    - redis-server
    - --requirepass
    - "${REDIS_PASSWORD}"
    - --appendonly
    - "yes"
    - --maxmemory
    - "256mb"
    - --maxmemory-policy
    - allkeys-lru
```

**Additional Fix for healthcheck:**
Changed `test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]` to:

```yaml
test: ["CMD-SHELL", "redis-cli -a ${REDIS_PASSWORD} ping"]
```

The `CMD-SHELL` form properly invokes a shell for the inline command.

---

## Summary Table

| Issue | Severity | Root Cause | Fix Complexity |
|-------|----------|------------|----------------|
| HEALTHCHECK ignored | Medium | OCI format doesn't support Docker HEALTHCHECK instruction | Low (COMPOSE_FORMAT=docker) |
| crun exec.fifo failure | Critical | crun bug/race condition in podman-compose | Medium (use runc instead) |
| Redis requirepass error | Critical | Shell syntax used with Docker Compose interpolation | Low (use exec form) |

---

## Proposed Implementation Plan

### Step 1: Fix Redis Configuration (Critical - Immediate)

- **File:** `docker-compose.yml` lines 77-97
- **Change:** Use exec form JSON array for `command`
- **Rationale:** Properly handles env var interpolation without shell syntax

### Step 2: Fix HEALTHCHECK Warning (Medium)

- **File:** `.env`
- **Change:** Add `COMPOSE_FORMAT=docker`
- **Rationale:** Tells podman-compose to use Docker image format supporting healthchecks

### Step 3: Document crun/runc Workaround (Medium)

- **File:** `PODMAN.md` (create new)
- **Change:** Document runtime switch to runc
- **Rationale:** Known bug with no upstream fix available

### Step 4: Verify Fixes (Testing)

- Run `podman-compose down -v` to clean up
- Run `podman-compose up --build`
- Verify all containers start successfully

---

## References

1. Podman HEALTHCHECK issue: containers/podman#25454
1. crun exec.fifo bug: containers/podman-compose#1072
1. Docker Compose variable interpolation: docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/
1. Redis docker-library issue: docker-library/redis#261
