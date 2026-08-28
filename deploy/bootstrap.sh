#!/usr/bin/env bash
# ============================================
# Vooglaadija — Plug-n-Play VPS Bootstrap (standalone, no Coolify)
# ============================================
# Provisions ANY VPS (Ubuntu/Debian or any systemd Linux with Docker support):
#   1. Installs Docker Engine + Compose plugin (if missing)
#   2. Verifies/creates the Cloudflare DNS records pointing at this server
#   3. Generates a local .env with random secrets (preserved on re-runs so the
#      already-initialized PostgreSQL/Redis volumes stay in sync)
#   4. Generates the Caddyfile + a self-signed origin certificate for the domain
#   5. Brings up the full stack with the standalone Caddy reverse proxy
#      (docker-compose.yml + docker-compose.local.yml + docker-compose.caddy.yml)
#   6. Verifies https://<domain>/health
#
# Caddy is the ONLY public entry point (ports 80/443) and reverse-proxies to
# the api service. Cloudflare's orange-cloud proxy forwards visitor traffic to
# this origin, so this is a zero-Coolify, zero-PaaS deployment.
#
# It asks only two things:
#   - DEPLOY_DOMAIN          (e.g. app.example.com)
#   - CLOUDFLARE_API_TOKEN   (scoped token with Zone:DNS:Edit for the zone; can
#                            be skipped with SKIP_DNS=1 if DNS is managed manually)
#
# Usage:
#   sudo ./deploy/bootstrap.sh                          # interactive
#   sudo DEPLOY_DOMAIN=app.example.com \
#        CLOUDFLARE_API_TOKEN=xxxx \
#        ./deploy/bootstrap.sh --non-interactive
#
# Optional overrides:
#   GIT_REPOSITORY    repo URL to clone when the script is run outside a checkout
#   GIT_BRANCH        branch to track (default: main)
#   SKIP_DNS=1        skip the Cloudflare DNS phase (records already exist)
# ============================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────
REPO_URL="${GIT_REPOSITORY:-https://github.com/tomkabel/vooglaadija.git}"
GIT_BRANCH="${GIT_BRANCH:-main}"
APP_NAME="vooglaadija"
NON_INTERACTIVE=false
FORCE=false
SKIP_DNS="${SKIP_DNS:-false}"

# Repo root = parent of the deploy/ directory this script lives in.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_TARGET="/opt/vooglaadija"

# ── Pinned installer checksums (CWE-494 hardening) ────────
# Regenerate when the official installer changes:
#   curl -fsSL https://get.docker.com | sha256sum
DOCKER_INSTALL_SHA256="e57f086075dd69dc7057c61d67a029acfbff649f6e394ac96e2123819516cd28"

# ── Colors ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step()  { echo -e "\n${BLUE}═══════════════════════════════════════════════${NC}"
              echo -e "${BLUE}  $1${NC}"
              echo -e "${BLUE}═══════════════════════════════════════════════${NC}"; }

die() { log_error "$1"; exit 1; }

# ── Argument parsing ──────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=true ;;
    --force) FORCE=true ;;
    --help|-h)
      sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "Unknown option: $arg" ;;
  esac
done

prompt() { # prompt variable default
  local var="$1" message="$2" default="${3:-}"
  local value
  if [[ -n "$default" ]]; then
    read -r -p "$message [$default]: " value
    value="${value:-$default}"
  else
    read -r -p "$message: " value
  fi
  printf -v "$var" '%s' "$value"
}

confirm() { # message -> true/false
  local response
  read -r -p "$1 [y/N]: " response
  [[ "$response" =~ ^[Yy]$ ]]
}

require_root() {
  [[ $EUID -eq 0 ]] || die "This script must be run as root. Use: sudo ./deploy/bootstrap.sh"
}

is_valid_ipv4() { # address -> true/false
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  IFS='.' read -r -a octets <<< "$1"
  local octet
  for octet in "${octets[@]}"; do
    ((octet <= 255)) || return 1
  done
  return 0
}

# ── Phase 0: Pre-flight ───────────────────────────────────
preflight() {
  log_step "Phase 0: Pre-flight checks"

  for tool in curl openssl python3 sha256sum; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool '$tool' is missing. Install it (apt install -y $tool) and re-run."
  done

  if command -v dig >/dev/null 2>&1; then HAVE_DIG=true; else HAVE_DIG=false; fi

  # Detect public IP (distro-agnostic) and validate it. PUBLIC_IP can be
  # provided via the environment (useful for --non-interactive runs).
  PUBLIC_IP="${PUBLIC_IP:-}"
  if ! is_valid_ipv4 "$PUBLIC_IP"; then
    for endpoint in "https://api.ipify.org" "https://ifconfig.me/ip" "https://ipinfo.io/ip"; do
      PUBLIC_IP=$(curl -fsS4 --max-time 10 "$endpoint" 2>/dev/null | tr -d '[:space:]' | head -c 64 || true)
      is_valid_ipv4 "$PUBLIC_IP" && break
      PUBLIC_IP=""
    done
  fi
  while [[ -z "$PUBLIC_IP" ]] || ! is_valid_ipv4 "$PUBLIC_IP"; do
    if $NON_INTERACTIVE; then
      die "Could not detect a valid public IPv4 address automatically. Set PUBLIC_IP=<server-ipv4> and re-run."
    fi
    prompt PUBLIC_IP "Could not detect public IP automatically. Enter the server's public IPv4"
    if [[ -n "$PUBLIC_IP" ]] && ! is_valid_ipv4 "$PUBLIC_IP"; then
      log_error "Invalid IPv4 address: $PUBLIC_IP"
      PUBLIC_IP=""
    fi
  done
  log_info "Detected public IP: $PUBLIC_IP"

  # Swap: 2GB recommended for Docker workloads
  if ! swapon --show 2>/dev/null | grep -q .; then
    log_warn "No swap detected. Adding a 2GB swapfile (recommended for Docker builds/runs)..."
    if confirm "Create 2GB swapfile at /swapfile?"; then
      fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
      chmod 600 /swapfile
      mkswap /swapfile >/dev/null
      swapon /swapfile
      grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
      log_info "Swapfile created and enabled"
    fi
  fi
}

# ── Phase 1: Gather inputs ────────────────────────────────
gather_inputs() {
  log_step "Phase 1: Domain (and optional Cloudflare token)"

  if [[ -z "${DEPLOY_DOMAIN:-}" ]]; then
    if $NON_INTERACTIVE; then die "DEPLOY_DOMAIN is required in --non-interactive mode."; fi
    prompt DEPLOY_DOMAIN "Enter the domain for this deployment (e.g. app.example.com)"
  fi
  DEPLOY_DOMAIN="${DEPLOY_DOMAIN,,}"
  [[ "$DEPLOY_DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "Invalid domain: $DEPLOY_DOMAIN"
  log_info "Domain: $DEPLOY_DOMAIN"

  # The Cloudflare token is only needed for automatic DNS provisioning.
  # SKIP_DNS=1 or an interactive 'no' allows a fully manual DNS setup.
  if ! $SKIP_DNS; then
    if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
      if $NON_INTERACTIVE; then
        log_warn "CLOUDFLARE_API_TOKEN not set — skipping automatic DNS provisioning (set SKIP_DNS=1 to silence)."
        SKIP_DNS=true
      else
        read -r -s -p "Cloudflare API token (Zone.DNS:Edit for $DEPLOY_DOMAIN; empty to skip DNS): " CLOUDFLARE_API_TOKEN
        echo ""
        [[ -n "$CLOUDFLARE_API_TOKEN" ]] || { log_warn "No token — skipping automatic DNS provisioning."; SKIP_DNS=true; }
      fi
    fi
  fi
}

# ── Phase 2: DNS verification + auto-provisioning ─────────
cloudflare_api() { # method path [data]
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS --max-time 20 -X "$method" "https://api.cloudflare.com/client/v4${path}")
  args+=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json")
  [[ -n "$data" ]] && args+=(-d "$data")
  curl "${args[@]}"
}

dns_setup() {
  $SKIP_DNS && { log_info "Skipping DNS provisioning (SKIP_DNS). Ensure A records for $DEPLOY_DOMAIN and *.$DEPLOY_DOMAIN point to $PUBLIC_IP."; return 0; }
  log_step "Phase 2: DNS verification (Cloudflare)"

  # Resolve the Cloudflare zone for DEPLOY_DOMAIN. The domain is often a
  # subdomain (e.g. app.example.com) whose zone is the parent (example.com),
  # so match by suffix and prefer the longest matching zone name.
  local zone_info zone_id zone_name
  # Walk a few pages so accounts with many zones still resolve correctly.
  zone_info=""
  for page in 1 2 3; do
    zone_info=$(cloudflare_api GET "/zones?per_page=100&page=${page}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    domain = sys.argv[1]
    zones = [z for z in d.get('result', [])
             if domain == z.get('name') or domain.endswith('.' + z.get('name', ''))]
    if zones:
        zones.sort(key=lambda z: len(z.get('name', '')), reverse=True)
        print(zones[0]['id'] + '|' + zones[0]['name'])
except Exception:
    print('')
" "$DEPLOY_DOMAIN" || true)
    [[ -n "$zone_info" ]] && break
  done
  zone_id="${zone_info%%|*}"
  zone_name="${zone_info#*|}"

  if [[ -z "$zone_id" ]]; then
    log_warn "No Cloudflare zone found for '$DEPLOY_DOMAIN' (or the token lacks Zone:Read)."
    log_warn "Add the domain to Cloudflare (dash.cloudflare.com → Add a site), set your A records, then re-run."
    confirm "Continue anyway?" || exit 1
    return 0
  fi

  log_info "Matched Cloudflare zone: ${zone_name:-$DEPLOY_DOMAIN}"

  # Ensure apex + wildcard A records point to this server (create if missing)
  for name in "$DEPLOY_DOMAIN" "*.$DEPLOY_DOMAIN"; do
    local records
    records=$(cloudflare_api GET "/zones/${zone_id}/dns_records?type=A&name=${name}" || true)
    local matches
    matches=$(printf '%s' "$records" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    hits = [r for r in d.get('result', []) if r.get('content') == sys.argv[1]]
    print(len(hits))
except Exception:
    print('0')
" "$PUBLIC_IP")
    if [[ "$matches" -ge 1 ]]; then
      log_info "DNS OK: $name → $PUBLIC_IP"
    else
      log_warn "No A record found for '$name' pointing to $PUBLIC_IP."
      if confirm "Create A record '$name' → $PUBLIC_IP in Cloudflare?"; then
        local created
        created=$(cloudflare_api POST "/zones/${zone_id}/dns_records" \
          "{\"type\":\"A\",\"name\":\"${name}\",\"content\":\"${PUBLIC_IP}\",\"ttl\":120,\"proxied\":false}" || true)
        if printf '%s' "$created" | grep -q '"success":true'; then
          log_info "Created DNS record: $name → $PUBLIC_IP"
        else
          log_warn "Failed to create DNS record for '$name':"
          printf '%s\n' "$created" | head -c 400
          echo ""
        fi
      fi
    fi
  done

  if $HAVE_DIG; then
    local resolved
    resolved=$(dig +short A "$DEPLOY_DOMAIN" 2>/dev/null | tail -n1 || true)
    if [[ -n "$resolved" && "$resolved" != "$PUBLIC_IP" ]]; then
      log_warn "Public DNS for $DEPLOY_DOMAIN currently resolves to $resolved (expected $PUBLIC_IP)."
      log_warn "This is normal while records propagate or when Cloudflare proxy (orange cloud) is enabled."
    fi
  fi
}

# ── Phase 3: Install Docker ───────────────────────────────
install_docker() {
  log_step "Phase 3: Docker installation"
  if command -v docker >/dev/null 2>&1; then
    log_info "Docker already installed ($(docker --version | tr -d '\n'))"
  else
    local installer="$SCRATCH_DIR/get-docker.sh"
    log_info "Downloading the official Docker convenience script..."
    curl -fsSL --max-time 120 -o "$installer" https://get.docker.com
    echo "$DOCKER_INSTALL_SHA256  $installer" | sha256sum -c - \
      || die "get.docker.com checksum mismatch. Update DOCKER_INSTALL_SHA256 in deploy/bootstrap.sh after reviewing the script."
    log_info "Checksum verified — installing Docker Engine..."
    bash "$installer"
    systemctl enable docker
    systemctl start docker
  fi
  docker compose version >/dev/null 2>&1 || die "Docker Compose plugin (v2) is required."
  log_info "Compose plugin: $(docker compose version 2>/dev/null | tr -d '\n')"
}

# ── Phase 4: Repository checkout ──────────────────────────
# Run from a checkout (default) or clone the public repo into /opt/vooglaadija
# when the script is executed from elsewhere (e.g. a downloaded copy).
setup_repo() {
  log_step "Phase 4: Repository checkout"

  if git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    log_info "Using existing checkout: $REPO_DIR"
    # Keep the deployed branch current when invoked from a git checkout.
    if [[ -z "${GIT_REPOSITORY:-}" && -n "${GIT_BRANCH:-}" ]] && $FORCE; then
      git -C "$REPO_DIR" fetch origin "$GIT_BRANCH" >/dev/null 2>&1 && \
        git -C "$REPO_DIR" reset --hard "origin/$GIT_BRANCH" && \
        log_info "Updated to origin/$GIT_BRANCH" || log_warn "Could not update checkout (re-run with --force to retry)."
    fi
    return 0
  fi

  # Not a git checkout — clone the repo into a stable location.
  if [[ -d "$DEPLOY_TARGET/.git" ]]; then
    log_info "Using existing clone: $DEPLOY_TARGET"
    REPO_DIR="$DEPLOY_TARGET"
    return 0
  fi
  if $NON_INTERACTIVE; then
    log_info "Cloning $REPO_URL (branch $GIT_BRANCH) into $DEPLOY_TARGET..."
  else
    log_info "This script is running outside a git checkout."
    confirm "Clone $REPO_URL into $DEPLOY_TARGET?" || die "Aborted. Run the script from a checkout of the repository."
  fi
  mkdir -p "$(dirname "$DEPLOY_TARGET")"
  git clone --branch "$GIT_BRANCH" --depth 1 "$REPO_URL" "$DEPLOY_TARGET" \
    || die "Clone failed. Check GIT_REPOSITORY / network access."
  REPO_DIR="$DEPLOY_TARGET"
  log_info "Cloned to $DEPLOY_TARGET"
}

# ── Phase 5: Environment (.env) ───────────────────────────
# Generates a production .env from .env.example with random secrets. On
# re-runs the existing values are PRESERVED so the already-initialized
# PostgreSQL volume (which only honors its password at first init) and the
# Redis instance stay in sync.
setup_environment() {
  log_step "Phase 5: Environment variables (.env)"
  local env_file="$REPO_DIR/.env"

  if [[ -f "$env_file" ]]; then
    log_info "Existing .env found — preserving secrets (DB_PASSWORD, REDIS_PASSWORD, SECRET_KEY)."
    # Ensure the domain is current for CORS / Caddy regardless of re-runs.
    if grep -q '^DEPLOY_DOMAIN=' "$env_file"; then
      sed -i "s|^DEPLOY_DOMAIN=.*|DEPLOY_DOMAIN=${DEPLOY_DOMAIN}|" "$env_file"
    else
      echo "DEPLOY_DOMAIN=${DEPLOY_DOMAIN}" >> "$env_file"
    fi
    if grep -q '^CORS_ORIGINS=' "$env_file"; then
      sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://${DEPLOY_DOMAIN}|" "$env_file"
    else
      echo "CORS_ORIGINS=https://${DEPLOY_DOMAIN}" >> "$env_file"
    fi
    return 0
  fi

  log_info "Generating .env with random secrets..."
  [[ -f "$REPO_DIR/.env.example" ]] || die ".env.example not found in $REPO_DIR."
  cp "$REPO_DIR/.env.example" "$env_file"

  local db_password redis_password secret_key grafana_password
  db_password=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  redis_password=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  secret_key=$(openssl rand -hex 32)
  grafana_password=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 24)

  sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=${db_password}|" "$env_file"
  sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${redis_password}|" "$env_file"
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${secret_key}|" "$env_file"
  sed -i "s|^GF_SECURITY_ADMIN_PASSWORD=.*|GF_SECURITY_ADMIN_PASSWORD=${grafana_password}|" "$env_file"

  # Production settings: HTTPS cookies, domain CORS, prod environment.
  sed -i "s|^COOKIE_SECURE=.*|COOKIE_SECURE=True|" "$env_file"
  # CORS_ORIGINS and ENVIRONMENT are commented out in .env.example — uncomment
  # or append them so production uses the real domain (not the localhost default).
  if grep -q '^CORS_ORIGINS=' "$env_file"; then
    sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=https://${DEPLOY_DOMAIN}|" "$env_file"
  else
    echo "CORS_ORIGINS=https://${DEPLOY_DOMAIN}" >> "$env_file"
  fi
  if grep -q '^ENVIRONMENT=' "$env_file"; then
    sed -i "s|^ENVIRONMENT=.*|ENVIRONMENT=production|" "$env_file"
  else
    echo "ENVIRONMENT=production" >> "$env_file"
  fi
  sed -i "s|^DEPLOY_DOMAIN=.*|DEPLOY_DOMAIN=${DEPLOY_DOMAIN}|" "$env_file"

  # Never leave the file world-readable: it holds DB/Redis/JWT secrets.
  chmod 600 "$env_file"
  log_info ".env written (mode 600): $env_file"
}

# ── Phase 6: Caddy reverse proxy ──────────────────────────
# Generates the Caddyfile for DEPLOY_DOMAIN and a self-signed origin
# certificate. Caddy presents the cert to Cloudflare (SSL/TLS mode "Full"),
# so the origin leg is encrypted without needing a public CA. If the domain
# is ever grey-clouded (direct traffic), switch the tls line to `tls internal`
# and Caddy will issue a real Let's Encrypt cert automatically.
setup_caddy() {
  log_step "Phase 6: Caddy reverse proxy (TLS origin cert)"

  local cert_dir="$REPO_DIR/certs"
  mkdir -p "$cert_dir"
  local cert_file="$cert_dir/${DEPLOY_DOMAIN}.crt"
  local key_file="$cert_dir/${DEPLOY_DOMAIN}.key"

  if [[ -f "$cert_file" && -f "$key_file" ]]; then
    log_info "Existing certificate found — reusing $cert_file"
  else
    log_info "Generating self-signed certificate for $DEPLOY_DOMAIN (1 year)..."
    openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
      -keyout "$key_file" -out "$cert_file" \
      -subj "/CN=${DEPLOY_DOMAIN}" \
      -addext "subjectAltName=DNS:${DEPLOY_DOMAIN},DNS:*.${DEPLOY_DOMAIN}" \
      >/dev/null 2>&1 || die "openssl certificate generation failed."
    chmod 600 "$key_file"
    log_info "Certificate written: $cert_file"
  fi

  cat > "$REPO_DIR/Caddyfile" <<EOF
# Caddy reverse proxy for ${DEPLOY_DOMAIN} (behind Cloudflare proxy)
#
# ${DEPLOY_DOMAIN} is on Cloudflare. Caddy serves a TLS cert Cloudflare accepts
# so the connection from Cloudflare -> this origin is encrypted. Set Cloudflare
# SSL/TLS mode to "Full" (or "Full (strict)" with a Cloudflare Origin CA cert —
# this self-signed cert is enough for "Full"). Plain HTTP :80 is also served so
# "Flexible" mode works too.
#
# (If you later drop the Cloudflare proxy / grey-cloud the DNS, change the tls
#  block to \`tls internal\` and Caddy will auto-issue a real Let's Encrypt cert.)

${DEPLOY_DOMAIN} {
	encode gzip zstd

	# Present the origin cert to Cloudflare (and direct visitors).
	tls /etc/caddy/certs/${DEPLOY_DOMAIN}.crt /etc/caddy/certs/${DEPLOY_DOMAIN}.key

	reverse_proxy api:8000

	log {
		output stdout
		format console
	}
}

# Plain HTTP listener: Cloudflare Flexible mode tunnels through :80.
# Direct IP visitors or health checks that hit http://:80 reach the app here.
:80 {
	reverse_proxy api:8000
}
EOF
  log_info "Caddyfile written for ${DEPLOY_DOMAIN}"
}

# ── Phase 7: Deploy + verify ──────────────────────────────
deploy_and_verify() {
  log_step "Phase 7: Deploy and verify"

  local compose=(docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml)
  cd "$REPO_DIR"

  log_info "Starting the stack (this builds the api/worker images on first run; can take several minutes)..."
  if ! "${compose[@]}" up -d --build 2>&1 | tail -n 5; then
    log_error "docker compose up failed. Diagnostics:"
    "${compose[@]}" ps 2>&1 || true
    "${compose[@]}" logs --tail=50 api 2>&1 || true
    return 1
  fi

  # Local health first (decouples verification from Cloudflare/DNS propagation).
  log_info "Waiting for local health (http://127.0.0.1/health via Caddy)..."
  local i code
  for i in $(seq 1 36); do
    code=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1/health 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      log_info "✓ Local health check passed (HTTP 200)"
      break
    fi
    [[ $((i % 6)) -eq 0 ]] && log_info "  waiting for the stack (${i}/36, HTTP $code)..."
    sleep 5
  done
  if [[ "$code" != "200" ]]; then
    log_warn "Local health check did not pass. Check: docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml logs api"
    return 1
  fi

  # Public check (through Cloudflare when proxied).
  log_info "Waiting for https://${DEPLOY_DOMAIN}/health (TLS cert + Cloudflare; can take a minute)..."
  for i in $(seq 1 30); do
    local body
    code=$(curl -sSk --max-time 10 -o /dev/null -w '%{http_code}' "https://${DEPLOY_DOMAIN}/health" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
        body=$(curl -fsSk --max-time 10 "https://${DEPLOY_DOMAIN}/health" 2>/dev/null || true)
      if printf '%s' "$body" | grep -q '"healthy"'; then
        log_info "✓ Public health check passed: https://${DEPLOY_DOMAIN}/health"
        return 0
      fi
    fi
    [[ $((i % 6)) -eq 0 ]] && log_info "  waiting for public health (${i}/30, HTTP $code)..."
    sleep 10
  done
  log_warn "Public health check did not pass within 5 minutes (local check passed, so the app is up — check DNS/Cloudflare)."
  return 1
}

# ── Summary ───────────────────────────────────────────────
summary() { # deploy_ok: "ok" | "failed"
  local deploy_ok="${1:-failed}"
  log_step "Bootstrap finished"

  if [[ "$deploy_ok" == "ok" ]]; then
    echo -e "${GREEN}  Vooglaadija is live at:${NC} https://${DEPLOY_DOMAIN}"
  else
    log_warn "The health check did not pass — the stack may still be starting or it failed."
    echo -e "${YELLOW}  Application URL (not verified yet):${NC} https://${DEPLOY_DOMAIN}"
  fi
  echo ""
  echo "  Stack: api, worker, db (PostgreSQL), redis, otel-collector, browser-downloader,"
  echo "         and caddy — all managed by docker compose in $REPO_DIR."
  echo ""
  echo "  Manage the stack:"
  echo "    cd $REPO_DIR"
  echo "    docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml ps"
  echo "    docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml logs -f api"
  echo "    docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml up -d --build   # update"
  echo ""
  echo "  TLS: self-signed origin cert for ${DEPLOY_DOMAIN} presented to Cloudflare (SSL/TLS mode"
  echo "  must be 'Full'). Ports 80/443 are the only public entry points (Caddy)."
  echo ""
  echo "  Secrets were generated and stored in $REPO_DIR/.env (mode 600)."
  echo "  Back them up! They are required to talk to the existing PostgreSQL/Redis volumes."
  echo ""
  echo "  Firewall: only 22, 80, 443 need to be open"
  echo "    (ufw allow ssh && ufw allow 80/tcp && ufw allow 443/tcp)."
  echo ""
  echo "  Updates: images are built locally from this checkout. To update:"
  echo "    git -C $REPO_DIR pull && ${compose_update_cmd:-docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.caddy.yml up -d --build}"
  echo ""
  echo "  Optional extras (profiles in docker-compose.yml):"
  echo "    monitoring: docker compose --profile monitoring up -d"
  echo "    backup:     docker compose --profile backup up -d"
}

# ── Main ──────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║   Vooglaadija — Plug-n-Play VPS Bootstrap            ║${NC}"
  echo -e "${BLUE}║   (standalone Caddy deployment — no Coolify)         ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""

  SCRATCH_DIR="$(mktemp -d)"
  trap 'rm -rf "$SCRATCH_DIR"' EXIT

  require_root
  preflight
  gather_inputs
  dns_setup
  install_docker
  setup_repo
  setup_environment
  setup_caddy
  if deploy_and_verify; then
    summary ok
  else
    summary failed
    exit 1
  fi
}

# Run main only when executed directly (not when sourced for testing).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
