# Podman Compatibility Guide

This document covers known issues and workarounds when using Podman instead of Docker with this project.

## Known Issues

### 1. HEALTHCHECK Warning

**Symptom:**

```text
WARN[0027] HEALTHCHECK is not supported for OCI image format and will be ignored.
```

**Cause:** Podman defaults to OCI image format, which doesn't support Docker's HEALTHCHECK instruction.

**Fix:** Set `COMPOSE_FORMAT=docker` environment variable before running podman-compose:

```bash
export COMPOSE_FORMAT=docker
podman-compose up --build
```

This has been added to `.env` file for convenience.

---

### 2. DB Container - crun exec.fifo Error

**Symptom:**

```text
[db] | cannot open `/run/user/1000/crun/.../exec.fifo`: No such file or directory
[db] | Error: unable to start container ... /usr/bin/crun start ... failed: exit status 1
```

**Cause:** Bug or misconfiguration in crun runtime when creating container exec FIFOs.

**Fix Options (in order of preference):**

#### Option A: Use runc instead of crun (Recommended)

Create `~/.config/containers/containers.conf`:

```toml
[runtime]
runtime = "/usr/bin/runc"
```

Or run podman-compose with explicit runtime:

```bash
podman --runtime /usr/bin/runc-compose up --build
```

#### Option B: Fix crun installation

```bash
# Reinstall crun
sudo dnf/apt install --reinstall crun

# Or update to latest version
sudo dnf/apt update && sudo dnf/apt upgrade crun
```

#### Option C: Create crun directory manually

```bash
mkdir -p /run/user/1000/crun
chmod 700 /run/user/1000/crun
```

---

### 3. General Podman-compose Usage

For best results, use Docker instead of Podman when possible:

```bash
# Instead of:
podman-compose up --build

# Use:
docker-compose up --build
```

If you must use Podman:

```bash
# Ensure COMPOSE_FORMAT is set
export COMPOSE_FORMAT=docker

# Use podman-compose
podman-compose up --build

# Or use docker-compose with podman backend
DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock docker-compose up --build
```

---

## Quick Start with Podman

```bash
# 1. Set environment
export COMPOSE_FORMAT=docker

# 2. Fix crun if needed (see above)

# 3. Build and start
podman-compose up --build

# 4. View logs
podman-compose logs -f
```

---

## Verification

After applying fixes, verify containers start correctly:

```bash
podman-compose ps
```

All containers should show "Running" status, not "Error" or "Exited".
