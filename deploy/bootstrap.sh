#!/usr/bin/env bash
# ============================================
# Vooglaadija — Plug-n-Play VPS Bootstrap
# ============================================
# Provisions ANY VPS (Ubuntu/Debian or any systemd Linux with Docker support):
#   1. Installs Docker Engine + Compose plugin (if missing)
#   2. Installs Coolify (self-hosted PaaS) (if missing)
#   3. Switches Coolify's proxy to Caddy with Cloudflare DNS-01 so a wildcard
#      TLS certificate (*.your-domain.com) is issued and auto-renewed
#   4. Creates the Vooglaadija application from the public GitHub repo
#      (Docker Compose build pack, auto-deploy on push to main)
#   5. Injects generated secrets as environment variables
#   6. Assigns the domain and triggers the first deployment
#   7. Verifies https://<domain>/health
#
# It asks only two things:
#   - DEPLOY_DOMAIN          (e.g. app.example.com)
#   - CLOUDFLARE_API_TOKEN   (scoped token with Zone:DNS:Edit for the zone)
#
# Usage:
#   sudo ./deploy/bootstrap.sh                          # interactive
#   sudo DEPLOY_DOMAIN=app.example.com \
#        CLOUDFLARE_API_TOKEN=xxxx \
#        CLOUDFLARE_EMAIL=you@example.com \
#        ./deploy/bootstrap.sh --non-interactive
#
# Optional overrides:
#   GIT_REPOSITORY    public repo URL (default: this project's GitHub repo)
#   GIT_BRANCH        branch to track (default: main)
#   COOLIFY_API_TOKEN reuse an existing Coolify API token (skips auto-creation)
#   COOLIFY_UI_URL    Coolify dashboard URL (default: http://<server-ip>:8000)
# ============================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────
REPO_URL="${GIT_REPOSITORY:-https://github.com/tomkabel/vooglaadija.git}"
GIT_BRANCH="${GIT_BRANCH:-main}"
APP_NAME="vooglaadija"
ENVIRONMENT_NAME="production"
COOLIFY_PROXY_DIR="/data/coolify/proxy/caddy"
PROXY_COMPOSE="$COOLIFY_PROXY_DIR/docker-compose.yml"
NON_INTERACTIVE=false
FORCE=false

# ── Pinned installer checksums (CWE-494 hardening) ────────
# Regenerate when the official installers change:
#   curl -fsSL https://get.docker.com | sha256sum
#   curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sha256sum
DOCKER_INSTALL_SHA256="e57f086075dd69dc7057c61d67a029acfbff649f6e394ac96e2123819516cd28"
COOLIFY_INSTALL_SHA256="73203a9b8b626554c8f24d839fbd5b91d46a8ef282800395ee33e68b23a0e7cf"

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
      sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
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
  log_step "Phase 1: Domain and Cloudflare credentials"

  if [[ -z "${DEPLOY_DOMAIN:-}" ]]; then
    if $NON_INTERACTIVE; then die "DEPLOY_DOMAIN is required in --non-interactive mode."; fi
    prompt DEPLOY_DOMAIN "Enter the domain for this deployment (e.g. app.example.com)"
  fi
  DEPLOY_DOMAIN="${DEPLOY_DOMAIN,,}"
  [[ "$DEPLOY_DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "Invalid domain: $DEPLOY_DOMAIN"
  log_info "Domain: $DEPLOY_DOMAIN"

  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    if $NON_INTERACTIVE; then die "CLOUDFLARE_API_TOKEN is required in --non-interactive mode."; fi
    read -r -s -p "Cloudflare API token (Zone.DNS:Edit for $DEPLOY_DOMAIN): " CLOUDFLARE_API_TOKEN
    echo ""
  fi
  [[ -n "$CLOUDFLARE_API_TOKEN" ]] || die "Cloudflare API token must not be empty."

  if [[ -z "${CLOUDFLARE_EMAIL:-}" ]] && ! $NON_INTERACTIVE; then
    read -r -p "Optional: admin/ACME email for Coolify and certificate expiry notices: " CLOUDFLARE_EMAIL
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
    log_warn "The DNS-01 challenge requires the zone to be managed by Cloudflare."
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

# ── Phase 4: Install Coolify ──────────────────────────────
install_coolify() {
  log_step "Phase 4: Coolify installation"
  if docker inspect coolify >/dev/null 2>&1; then
    log_info "Coolify already installed"
  else
    local installer="$SCRATCH_DIR/coolify-install.sh"
    log_info "Downloading the official Coolify installer (CDN)..."
    curl -fsSL --max-time 120 -o "$installer" https://cdn.coollabs.io/coolify/install.sh
    echo "$COOLIFY_INSTALL_SHA256  $installer" | sha256sum -c - \
      || die "Coolify installer checksum mismatch. Update COOLIFY_INSTALL_SHA256 in deploy/bootstrap.sh after reviewing the script."
    log_info "Checksum verified — installing Coolify (this takes a few minutes)..."
    # Pre-provision the admin account so the first-run browser step is skipped.
    if [[ -n "${CLOUDFLARE_EMAIL:-}" ]]; then
      COOLIFY_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 24)
      export ROOT_USERNAME="admin"
      export ROOT_USER_EMAIL="$CLOUDFLARE_EMAIL"
      export ROOT_USER_PASSWORD="$COOLIFY_ADMIN_PASSWORD"
      log_info "Coolify admin will be created automatically (see summary for the password)"
    fi
    bash "$installer"
  fi
  wait_coolify_ready
}

wait_coolify_ready() {
  log_info "Waiting for Coolify to become healthy..."
  local i
  for i in $(seq 1 60); do
    if docker inspect coolify >/dev/null 2>&1; then
      local status
      status=$(docker inspect --format '{{.State.Health.Status}}' coolify 2>/dev/null || echo "starting")
      if [[ "$status" == "healthy" ]]; then
        log_info "Coolify is healthy"
        return 0
      fi
    fi
    [[ $((i % 10)) -eq 0 ]] && log_info "  still starting (${i}/60)..."
    sleep 5
  done
  log_warn "Coolify container is not reporting healthy yet. Check: docker logs coolify"
  return 0
}

# ── Phase 5: Caddy proxy + Cloudflare DNS-01 (wildcard TLS) ──
ensure_root_user_exists() {
  log_step "Phase 5a: Coolify first-run setup"
  local users
  users=$(docker exec coolify php artisan tinker --execute="echo \App\Models\User::count();" 2>/dev/null | tail -n1 || echo "ERR")
  if [[ "$users" =~ ^[0-9]+$ ]] && [[ "$users" -ge 1 ]]; then
    log_info "Coolify admin user exists"
    return 0
  fi
  if $NON_INTERACTIVE; then
    die "Coolify has no admin user yet. Re-run with CLOUDFLARE_EMAIL set (auto-creates the admin), or open ${COOLIFY_UI_URL:-http://$PUBLIC_IP:8000} once in a browser to complete setup."
  fi
  log_warn "Coolify is freshly installed and has no admin user yet."
  prompt _ "Open ${COOLIFY_UI_URL:-http://$PUBLIC_IP:8000} in your browser, complete the first-run setup (create the admin account), then press Enter"
}

switch_proxy_to_caddy() {
  log_step "Phase 5b: Switch Coolify proxy to Caddy (Cloudflare DNS-01)"

  # Programmatic switch via Laravel tinker; falls back to UI instructions.
  if ! docker inspect coolify-proxy >/dev/null 2>&1 || $FORCE; then
    local switch_result
    switch_result=$(docker exec coolify php artisan tinker --execute="\$s = \App\Models\Server::find(0); if (\$s) { \$s->changeProxy('CADDY', false); echo 'OK'; }" 2>&1 | tail -n1 || true)
    if [[ "$switch_result" == *OK* ]]; then
      log_info "Proxy switched to Caddy"
    else
      log_warn "Could not switch the proxy programmatically (Coolify internals changed or tinker unavailable)."
      if ! $NON_INTERACTIVE; then
        prompt _ "In the Coolify UI (Servers → localhost → Proxy), change the proxy type to 'Caddy', wait for it to start, then press Enter"
      fi
    fi
  fi

  local i
  for i in $(seq 1 30); do
    if docker inspect coolify-proxy >/dev/null 2>&1; then
      break
    fi
    [[ $((i % 6)) -eq 0 ]] && log_warn "  waiting for coolify-proxy container (${i}/30)..."
    sleep 5
  done
  docker inspect coolify-proxy >/dev/null 2>&1 || die "coolify-proxy container not found after proxy switch."

  # Overwrite the generated Caddy proxy compose with the DNS-01 build
  # (official Coolify docs: https://coolify.io/docs/knowledge-base/proxy/caddy/dns-challenge)
  mkdir -p "$COOLIFY_PROXY_DIR"
  # Create the file with 0600 BEFORE the Cloudflare token is written (CWE-732).
  install -m 600 /dev/null "$PROXY_COMPOSE"
  cat > "$PROXY_COMPOSE" <<EOF
name: coolify-proxy
networks:
  coolify:
    external: true
services:
  caddy:
    container_name: coolify-proxy
    image: 'lucaslorentz/caddy-docker-proxy:2.8-alpine'
    build:
      dockerfile_inline: |
        FROM caddy:2.11-builder AS builder
        RUN xcaddy build --with github.com/lucaslorentz/caddy-docker-proxy/v2@v2.8.0 --with github.com/caddy-dns/cloudflare@v0.4.0
        FROM caddy:2.11-alpine
        COPY --from=builder /usr/bin/caddy /usr/bin/caddy
        CMD ["caddy", "docker-proxy"]
    restart: unless-stopped
    extra_hosts:
      - 'host.docker.internal:host-gateway'
    environment:
      - CADDY_DOCKER_POLLING_INTERVAL=5s
      - CADDY_DOCKER_CADDYFILE_PATH=/dynamic/Caddyfile
      - CF_API_TOKEN=${CLOUDFLARE_API_TOKEN}
    networks:
      - coolify
    ports:
      - '80:80'
      - '443:443'
      - '443:443/udp'
    labels:
      - coolify.managed=true
      - coolify.proxy=true
      - caddy.acme_dns=cloudflare {env.CF_API_TOKEN}
    volumes:
      - '/var/run/docker.sock:/var/run/docker.sock:ro'
      - '/data/coolify/proxy/caddy/dynamic:/dynamic'
      - '/data/coolify/proxy/caddy/config:/config'
      - '/data/coolify/proxy/caddy/data:/data'
EOF
  log_info "Caddy proxy compose written (Cloudflare DNS-01 enabled, file mode 600)"

  # --build ensures the inline Dockerfile (with the Cloudflare DNS module) is
  # built even when a stale image of the same name exists.
  (cd "$COOLIFY_PROXY_DIR" && docker compose up -d --build --force-recreate 2>&1 | tail -n 3 || true)

  local module_ok=false
  for i in $(seq 1 42); do
    local status
    status=$(docker inspect --format '{{.State.Health.Status}}' coolify-proxy 2>/dev/null || echo "starting")
    if [[ "$status" == "healthy" ]]; then
      if docker exec coolify-proxy caddy list-modules 2>/dev/null | grep -q 'dns.providers.cloudflare'; then
        log_info "Caddy proxy is healthy with the Cloudflare DNS-01 module"
        module_ok=true
        break
      fi
      log_warn "Caddy is healthy but missing the Cloudflare DNS module — rebuilding the image..."
      (cd "$COOLIFY_PROXY_DIR" && docker compose build 2>&1 | tail -n 2 || true)
      (cd "$COOLIFY_PROXY_DIR" && docker compose up -d --force-recreate 2>&1 | tail -n 2 || true)
    fi
    [[ $((i % 6)) -eq 0 ]] && log_info "  waiting for Caddy proxy build/start (${i}/42)..."
    sleep 5
  done

  if ! $module_ok; then
    log_warn "Caddy proxy did not become healthy with the Cloudflare DNS module."
    log_warn "Check: docker logs coolify-proxy"
  fi
}

# ── Phase 6: Coolify API token ────────────────────────────
ensure_api_token() {
  log_step "Phase 6: Coolify API token"
  if [[ -n "${COOLIFY_API_TOKEN:-}" ]]; then
    log_info "Using provided COOLIFY_API_TOKEN"
    return 0
  fi

  local token
  token=$(docker exec coolify php artisan tinker --execute="
    \App\Models\InstanceSettings::get()->update(['is_api_enabled' => true]);
    \$user = \App\Models\User::first();
    if (!\$user) { echo 'NO_USER'; }
    else {
      \$t = \$user->createToken('bootstrap-' . date('Ymd'), ['read', 'write', 'deploy'], now()->addDays(365));
      echo \$t->plainTextToken;
    }
  " 2>/dev/null | grep -oE '[0-9]+\|[A-Za-z0-9]{40,}' | tail -n1 || true)

  if [[ -n "$token" ]]; then
    COOLIFY_API_TOKEN="$token"
    log_info "API token created automatically"
    return 0
  fi

  if $NON_INTERACTIVE; then
    die "Could not create a Coolify API token automatically. Set COOLIFY_API_TOKEN and re-run."
  fi
  prompt COOLIFY_API_TOKEN "Create an API token in the Coolify UI (Settings → Keys & Tokens → Create API token, permissions: read + write + deploy) and paste it here"
}

# ── Phase 7: Application creation ─────────────────────────
# Status-aware Coolify API helper: prints the response body and leaves the
# HTTP status in COOLIFY_API_HTTP_CODE (e.g. "201").
coolify_api() { # method path [data]
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS --max-time 30 -X "$method" "http://127.0.0.1:8000/api/v1${path}")
  args+=(-H "Authorization: Bearer ${COOLIFY_API_TOKEN}" -H "Content-Type: application/json")
  [[ -n "$data" ]] && args+=(-d "$data")
  local resp
  resp=$(curl "${args[@]}" -w $'\n%{http_code}' 2>/dev/null || true)
  COOLIFY_API_HTTP_CODE=$(printf '%s' "$resp" | tail -n1)
  [[ "$COOLIFY_API_HTTP_CODE" =~ ^[0-9]+$ ]] || COOLIFY_API_HTTP_CODE="000"
  printf '%s' "$resp" | sed '$d'
}

http_ok() { # status -> true/false (2xx)
  [[ "$1" =~ ^[0-9]+$ ]] || return 1
  [[ "$1" -ge 200 && "$1" -lt 300 ]]
}

create_application() {
  log_step "Phase 7: Application creation in Coolify"

  local server_resp
  server_resp=$(coolify_api GET "/servers")
  local server_uuid=""
  if http_ok "$COOLIFY_API_HTTP_CODE"; then
    server_uuid=$(printf '%s' "$server_resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    servers = d.get('servers', d.get('data', d.get('result', [])))
    print(servers[0]['uuid'] if servers else '')
except Exception:
    print('')
")
  fi
  [[ -n "$server_uuid" ]] || die "Could not determine the Coolify server UUID (HTTP ${COOLIFY_API_HTTP_CODE}). Check the API token permissions."

  # Project (reuse if it already exists)
  local project_resp project_uuid=""
  project_resp=$(coolify_api GET "/projects")
  if http_ok "$COOLIFY_API_HTTP_CODE"; then
    project_uuid=$(printf '%s' "$project_resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    projects = d.get('projects', d.get('data', d.get('result', [])))
    for p in projects:
        if p.get('name') == '${APP_NAME}':
            print(p['uuid'])
            break
except Exception:
    print('')
")
  fi
  if [[ -z "$project_uuid" ]]; then
    project_resp=$(coolify_api POST "/projects" "{\"name\":\"${APP_NAME}\",\"description\":\"Vooglaadija media link processor\"}")
    if http_ok "$COOLIFY_API_HTTP_CODE"; then
      project_uuid=$(printf '%s' "$project_resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('uuid', d.get('project_uuid', '')))
except Exception:
    print('')
")
    fi
    if [[ -z "$project_uuid" ]]; then
      die "Failed to create the Coolify project (HTTP ${COOLIFY_API_HTTP_CODE}): $(printf '%s' "$project_resp" | head -c 300)"
    fi
  fi
  log_info "Project: ${APP_NAME} (${project_uuid})"

  # Environment (Coolify auto-creates 'production'; tolerate conflicts)
  coolify_api POST "/projects/${project_uuid}/environments" "{\"name\":\"${ENVIRONMENT_NAME}\"}" >/dev/null 2>&1 || true

  # Existing application?
  local app_uuid=""
  app_resp=$(coolify_api GET "/applications")
  if http_ok "$COOLIFY_API_HTTP_CODE"; then
    app_uuid=$(printf '%s' "$app_resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    apps = d.get('applications', d.get('data', d.get('result', [])))
    for a in apps:
        if a.get('git_repository', '').rstrip('/').endswith('${REPO_URL}'.rstrip('/').rsplit('/', 1)[-1]):
            print(a['uuid'])
            break
except Exception:
    print('')
")
  fi

  if [[ -n "$app_uuid" ]] && ! $FORCE; then
    log_info "Application already exists (${app_uuid}) — reusing it."
  else
    local payload
    payload=$(python3 - "$REPO_URL" "$GIT_BRANCH" "$server_uuid" "$project_uuid" "$ENVIRONMENT_NAME" "$DEPLOY_DOMAIN" <<'PY'
import json, sys
repo, branch, server, project, env_name, domain = sys.argv[1:7]
body = {
    "name": "vooglaadija",
    "project_uuid": project,
    "server_uuid": server,
    "environment_name": env_name,
    "git_repository": repo,
    "git_branch": branch,
    "build_pack": "dockercompose",
    "ports_exposes": "8000",
    "docker_compose_location": "docker-compose.yml",
    "docker_compose_domains": [
        {"name": "api", "domain": f"https://{domain},https://*.{domain}"}
    ],
    "docker_compose_custom_start_command": "up -d --pull always --remove-orphans",
    "is_auto_deploy_enabled": True,
    "instant_deploy": False,
    "health_check_enabled": True,
    "health_check_path": "/health",
    "health_check_port": "8000",
    "health_check_return_code": 200,
    "is_force_https_enabled": True,
}
print(json.dumps(body))
PY
)
    local resp
    resp=$(coolify_api POST "/applications/public" "$payload")
    app_uuid=""
    if http_ok "$COOLIFY_API_HTTP_CODE"; then
      app_uuid=$(printf '%s' "$resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('uuid', ''))
except Exception:
    print('')
")
    fi
    if [[ -z "$app_uuid" ]]; then
      die "Failed to create the application (HTTP ${COOLIFY_API_HTTP_CODE}): $(printf '%s' "$resp" | head -c 500)"
    fi
    log_info "Application created: ${app_uuid}"
  fi
  APP_UUID="$app_uuid"
}

# ── Phase 8: Environment variables ────────────────────────
set_environment() {
  log_step "Phase 8: Environment variables (generated secrets)"

  local db_password redis_password secret_key grafana_password
  db_password=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  redis_password=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  secret_key=$(openssl rand -hex 32)
  grafana_password=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 24)

  local payload
  payload=$(python3 - "$db_password" "$redis_password" "$secret_key" "$grafana_password" "$DEPLOY_DOMAIN" <<'PY'
import json, sys
db_pw, redis_pw, secret, gf_pw, domain = sys.argv[1:6]
envs = {
    "DB_USER": "postgres",
    "DB_PASSWORD": db_pw,
    "DB_NAME": "ytprocessor",
    "REDIS_PASSWORD": redis_pw,
    "SECRET_KEY": secret,
    "SECRET_KEY_PREVIOUS": "",
    "CORS_ORIGINS": f"https://{domain}",
    "COOKIE_SECURE": "True",
    "ENVIRONMENT": "production",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "15",
    "REFRESH_TOKEN_EXPIRE_DAYS": "7",
    "FILE_EXPIRE_HOURS": "24",
    "FEATURE_METRICS_ENABLED": "true",
    "FEATURE_TRACING_ENABLED": "true",
    "IMAGE_TAG": "latest",
    "GF_SECURITY_ADMIN_USER": "admin",
    "GF_SECURITY_ADMIN_PASSWORD": gf_pw,
}
print(json.dumps({"data": [{"key": k, "value": v} for k, v in envs.items()]}))
PY
)
  local resp
  resp=$(coolify_api PATCH "/applications/${APP_UUID}/envs/bulk" "$payload")
  if http_ok "$COOLIFY_API_HTTP_CODE"; then
    log_info "Environment variables set (HTTP ${COOLIFY_API_HTTP_CODE}): DB_PASSWORD, REDIS_PASSWORD, SECRET_KEY, CORS, Grafana admin, ..."
  else
    log_warn "Env bulk update failed (HTTP ${COOLIFY_API_HTTP_CODE}): $(printf '%s' "$resp" | head -c 300)"
    log_warn "Set the missing variables manually in the Coolify UI before the first deployment."
  fi
}

# ── Phase 9: Deploy + verify ──────────────────────────────
deploy_and_verify() {
  log_step "Phase 9: Deploy and verify"

  log_info "Triggering deployment..."
  local trigger_resp
  trigger_resp=$(coolify_api POST "/deploy" "{\"uuid\":\"${APP_UUID}\"}")
  if http_ok "$COOLIFY_API_HTTP_CODE"; then
    log_info "Deployment triggered (HTTP ${COOLIFY_API_HTTP_CODE})"
  else
    log_warn "Primary deploy trigger failed (HTTP ${COOLIFY_API_HTTP_CODE}). Trying the start endpoint..."
    trigger_resp=$(coolify_api POST "/applications/${APP_UUID}/start" "")
    if http_ok "$COOLIFY_API_HTTP_CODE"; then
      log_info "Deployment started via /start (HTTP ${COOLIFY_API_HTTP_CODE})"
    else
      log_warn "Deploy trigger failed (HTTP ${COOLIFY_API_HTTP_CODE}): $(printf '%s' "$trigger_resp" | head -c 300)"
      log_warn "Start the deployment manually from the Coolify UI."
    fi
  fi

  log_info "Waiting for https://${DEPLOY_DOMAIN}/health (first deployment builds/pulls images and issues the TLS certificate; this can take several minutes)..."
  local i
  for i in $(seq 1 60); do
    local code body
    code=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "https://${DEPLOY_DOMAIN}/health" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      body=$(curl -fsS --max-time 10 "https://${DEPLOY_DOMAIN}/health" 2>/dev/null || true)
      if printf '%s' "$body" | grep -q '"healthy"'; then
        log_info "✓ Health check passed: https://${DEPLOY_DOMAIN}/health"
        return 0
      fi
    fi
    [[ $((i % 6)) -eq 0 ]] && log_info "  waiting for the service to become healthy (${i}/60, HTTP $code)..."
    sleep 10
  done
  log_warn "Health check did not pass within 10 minutes. Check deployment logs in the Coolify UI."
  return 1
}

# ── Summary ───────────────────────────────────────────────
summary() { # deploy_ok: "ok" | "failed"
  local deploy_ok="${1:-failed}"
  log_step "Bootstrap finished"

  if [[ "$deploy_ok" == "ok" ]]; then
    echo -e "${GREEN}  Vooglaadija is live at:${NC} https://${DEPLOY_DOMAIN}"
  else
    log_warn "The health check did not pass — the deployment may still be starting or it failed."
    echo -e "${YELLOW}  Application URL (not verified yet):${NC} https://${DEPLOY_DOMAIN}"
  fi
  echo -e "${GREEN}  Coolify dashboard:${NC}    ${COOLIFY_UI_URL:-http://$PUBLIC_IP:8000}"
  if [[ -n "${COOLIFY_ADMIN_PASSWORD:-}" ]]; then
    echo -e "${YELLOW}  Coolify admin (auto-created):${NC} ${ROOT_USERNAME:-admin} / ${COOLIFY_ADMIN_PASSWORD}"
    echo "    Change it after the first login (Coolify UI → Settings → User)."
  fi
  echo ""
  echo "  Continuous deployment is enabled: every push to the '${GIT_BRANCH}' branch of"
  echo "  ${REPO_URL}"
  echo "  builds new images in GitHub Actions (GHCR) and Coolify redeploys automatically."
  echo ""
  echo "  TLS: wildcard certificate for ${DEPLOY_DOMAIN} and *.${DEPLOY_DOMAIN} is issued"
  echo "  via the Cloudflare DNS-01 challenge and renewed automatically by Caddy."
  echo ""
  echo "  Secrets (DB password, Redis password, JWT key, Grafana admin password) were"
  echo "  generated and stored encrypted in Coolify. Back them up! View them under the"
  echo "  application's Environment Variables in the Coolify UI."
  echo ""
  echo "  Security: the Coolify dashboard listens on port 8000. Restrict it in your"
  echo "  firewall to your IP (ufw allow from <your-ip> to any port 8000) or access it"
  echo "  via an SSH tunnel (ssh -L 8000:127.0.0.1:8000 user@server)."
  echo ""
  echo "  Optional extras (profiles in docker-compose.yml):"
  echo "    monitoring: Prometheus + Grafana"
  echo "    backup:     daily PostgreSQL dumps"
  echo "  To enable them, add the profile in Coolify's Docker Compose start command, e.g."
  echo "    --profile monitoring up -d --pull always --remove-orphans"
}

# ── Main ──────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║   Vooglaadija — Plug-n-Play VPS Bootstrap            ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""

  SCRATCH_DIR="$(mktemp -d)"
  trap 'rm -rf "$SCRATCH_DIR"' EXIT

  require_root
  preflight
  gather_inputs
  dns_setup
  install_docker
  install_coolify
  ensure_root_user_exists
  switch_proxy_to_caddy
  ensure_api_token
  create_application
  set_environment
  if deploy_and_verify; then
    summary ok
  else
    summary failed
  fi
}

main "$@"
