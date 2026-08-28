#!/bin/bash
# ============================================
# Vooglaadija Docker Preflight Check
# ============================================
# Detects common Docker Compose issues before starting the stack:
#   1. Missing required .env variables
#   2. Port conflicts with running containers
#   3. Orphaned volumes from previous deployments
#
# Usage:
#   ./scripts/preflight-check.sh           # Check + interactive fix
#   ./scripts/preflight-check.sh --fix     # Check + auto-fix (non-interactive)
#   ./scripts/preflight-check.sh --check   # Check only, no fixes
#   ./scripts/preflight-check.sh --help    # Show help
# ============================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ── State ───────────────────────────────────────────────
AUTO_FIX=false
CHECK_ONLY=false
EXIT_CODE=0
COMPOSE_FILE="-f docker-compose.yml -f docker-compose.local.yml"

# ── Helpers ──────────────────────────────────────────────
log_info()  { echo -e "${GREEN}[✓]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; EXIT_CODE=1; }
log_step()  { echo -e "\n${BLUE}═══ $1 ═══${NC}"; }
log_fix()   { echo -e "    ${GREEN}→${NC} $1"; }

confirm() {
  if $AUTO_FIX; then return 0; fi
  local prompt="${1:-Continue?} [y/N] "
  read -r -p "$prompt" response
  [[ "$response" =~ ^[Yy]$ ]]
}

# ── Checks ──────────────────────────────────────────────
check_env_file() {
  log_step "Environment: .env file"

  if [[ -f .env ]]; then
    log_info ".env file exists."
    # Check required variables (without revealing values)
    local required_vars=("DB_PASSWORD" "REDIS_PASSWORD" "SECRET_KEY")
    local missing=()
    for var in "${required_vars[@]}"; do
      if grep -q "^${var}=" .env 2>/dev/null || grep -q "^export ${var}=" .env 2>/dev/null; then
        : # OK
      else
        missing+=("$var")
      fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
      log_error "Missing required variables in .env: ${missing[*]}"
      echo -e "    ${YELLOW}Add them to .env before running docker compose.${NC}"
      echo -e "    Example:"
      echo -e "      echo 'DB_PASSWORD=my-secret-password' >> .env"
      echo -e "      echo 'REDIS_PASSWORD=my-redis-password' >> .env"
      echo -e "      echo 'SECRET_KEY=my-secret-key-change-me' >> .env"
    else
      log_info "All required .env variables are present."
    fi
  else
    log_warn "No .env file found."
    echo -e "    ${YELLOW}Create one from the template or set environment variables.${NC}"
    echo -e "    Required: DB_PASSWORD, REDIS_PASSWORD, SECRET_KEY"
    echo -e "    For production, use: sudo ./deploy/bootstrap.sh"
  fi
}

check_port_conflicts() {
  log_step "Ports: checking for conflicts"

  # Local override binds these to loopback; Caddy owns 80/443 in prod.
  local ports=("3000:Grafana" "9090:Prometheus" "8000:API" "5432:Postgres" "6380:Redis" "8082:Worker")
  local conflict_found=false

  # Resolve this Compose project's name so we only skip ports published by
  # containers that belong to THIS project. Ports held by unrelated containers
  # (or non-Docker processes) are real conflicts and must be reported.
  local compose_project=""
  compose_project=$(docker compose -f docker-compose.yml config --format json 2>/dev/null | \
    python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))" 2>/dev/null || true)

  # Build a set of ports already used by THIS project's containers
  local docker_ports=()
  if [[ -n "$compose_project" ]]; then
    local cid
    while IFS= read -r cid; do
      [[ -z "$cid" ]] && continue
      local project_label ports
      project_label=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$cid" 2>/dev/null || true)
      [[ "$project_label" != "$compose_project" ]] && continue
      ports=$(docker port "$cid" 2>/dev/null | grep -oP '-> .*:\K\d+$' | sort -u || true)
      while IFS= read -r p; do
        [[ -n "$p" ]] && docker_ports+=("$p")
      done <<< "$ports"
    done < <(docker container ls -q 2>/dev/null || true)
  fi

  for entry in "${ports[@]}"; do
    local port="${entry%%:*}"
    local service="${entry#*:}"

    # Skip if this port is already occupied by a container of THIS compose project
    local is_project_docker=false
    for dp in "${docker_ports[@]}"; do
      if [[ "$dp" == "$port" ]]; then
        is_project_docker=true
        break
      fi
    done
    $is_project_docker && continue

    if ss -tlnp "sport = :$port" 2>/dev/null | grep -q LISTEN; then
      local owner
      owner=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'users:\(\K[^)]+' || echo "unknown")
      log_warn "Port $port ($service) is in use by a non-Docker process or another container: $owner"
      echo -e "    This may prevent the container from starting on that port."
      conflict_found=true
    fi
  done

  # Ports 80/443: warn if occupied by a non-Caddy process (Caddy needs them for TLS).
  # Merely having a Caddy container is not proof of ownership — verify the Caddy
  # container actually publishes the specific port before suppressing the warning.
  for port in 80 443; do
    if ss -tlnp "sport = :$port" 2>/dev/null | grep -q LISTEN; then
      local caddy_owns=false cid
      while IFS= read -r cid; do
        [[ -z "$cid" ]] && continue
        if docker port "$cid" "$port/tcp" >/dev/null 2>&1; then
          caddy_owns=true
          break
        fi
      done < <(docker ps --filter "name=caddy" --filter "status=running" --format '{{.ID}}' 2>/dev/null || true)
      if ! $caddy_owns; then
        log_warn "Port $port is in use — Caddy will need it for TLS."
      fi
    fi
  done

  if ! $conflict_found; then
    log_info "No port conflicts detected."
  fi
}

check_orphan_volumes() {
  log_step "Volumes: checking for orphans"

  local expected_volumes=(
    "ytprocessor-postgres-data"
    "ytprocessor-redis-data"
    "ytprocessor-storage"
    "ytprocessor-prometheus-data"
    "ytprocessor-grafana-data"
  )
  local orphans=()

  for vol in "${expected_volumes[@]}"; do
    if docker volume inspect "$vol" &>/dev/null; then
      # Volume exists — check if any container uses it
      if ! docker ps -a --filter "volume=$vol" --format '{{.ID}}' | grep -q .; then
        orphans+=("$vol")
      fi
    fi
  done

  if [[ ${#orphans[@]} -gt 0 ]]; then
    log_warn "Orphaned volumes found (not used by any container):"
    for vol in "${orphans[@]}"; do
      echo -e "    - $vol"
    done
    echo ""
    if ! $CHECK_ONLY && confirm "Remove orphaned volumes? (WARNING: destroys data)"; then
      for vol in "${orphans[@]}"; do
        docker volume rm "$vol" && log_fix "Removed $vol" || log_error "Failed to remove $vol"
      done
    fi
  else
    log_info "No orphaned volumes detected."
  fi
}

check_grafana_dashboards() {
  log_step "Grafana: checking dashboard UID uniqueness"

  local dashboard_dir="monitoring"
  local uids
  uids=$(grep -h '"uid"' "$dashboard_dir"/grafana-dashboard-*.json 2>/dev/null | \
    sed 's/.*"uid": *"\(.*\)".*/\1/' | sort)

  local dupes
  dupes=$(echo "$uids" | uniq -d)

  if [[ -n "$dupes" ]]; then
    log_error "Duplicate Grafana dashboard UID(s) found:"
    for uid in $dupes; do
      local files
      files=$(grep -l "\"uid\": *\"$uid\"" "$dashboard_dir"/grafana-dashboard-*.json 2>/dev/null | tr '\n' ' ')
      echo -e "    UID '$uid' appears in: $files"
    done
    echo -e "    ${YELLOW}Fix: Give each dashboard a unique 'uid' field in its JSON.${NC}"
    echo -e "    Each dashboard must have a globally unique UID within the Grafana instance."
  else
    log_info "All dashboard UIDs are unique."
  fi
}

# ── Main ────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║     Vooglaadija Docker Preflight Check     ║${NC}"
  echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
  echo ""

  check_env_file
  check_port_conflicts
  check_orphan_volumes
  check_grafana_dashboards

  echo ""
  if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║  All checks passed! Ready to compose up.  ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BLUE}docker compose ${COMPOSE_FILE} up -d --build${NC}"
    echo -e "  ${BLUE}Production:   sudo ./deploy/bootstrap.sh${NC}"
    echo ""
  else
    echo -e "${YELLOW}╔═══════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  Some issues found. Review warnings above.       ║${NC}"
    echo -e "${YELLOW}╚═══════════════════════════════════════════════════╝${NC}"
    echo ""
    exit $EXIT_CODE
  fi
}

# ── Entry ────────────────────────────────────────────────
case "${1:-}" in
  --fix)
    AUTO_FIX=true
    main
    ;;
  --check)
    CHECK_ONLY=true
    main
    ;;
  --help|-h)
    head -20 "$0" | grep -v '^#!/bin/bash' | sed 's/^# \?//'
    exit 0
    ;;
  "")
    main
    ;;
  *)
    echo "Unknown option: $1"
    echo "Usage: $0 [--fix|--check|--help]"
    exit 1
    ;;
esac
