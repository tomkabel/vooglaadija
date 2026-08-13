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
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
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

# ── Phase 0: Pre-flight ───────────────────────────────────
preflight() {
  log_step "Phase 0: Pre-flight checks"

  for tool in curl openssl python3; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool '$tool' is missing. Install it (apt install -y $tool) and re-run."
  done

  if command -v dig >/dev/null 2>&1; then HAVE_DIG=true; else HAVE_DIG=false; fi

  # Detect public IP (distro-agnostic)
  PUBLIC_IP=""
  for endpoint in "https://api.ipify.org" "https://ifconfig.me/ip" "https://ipinfo.io/ip"; do
    PUBLIC_IP=$(curl -fsS4 --max-time 10 "$endpoint" 2>/dev/null | tr -d '[:space:]' | head -c 64 || true)
    [[ -n "$PUBLIC_IP" ]] && break
  done
  if [[ -z "$PUBLIC_IP" ]]; then
    prompt PUBLIC_IP "Could not detect public IP automatically. Enter the server's public IPv4"
  fi
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
    read -r -p "Optional: ACME account email for certificate expiry notices: " CLOUDFLARE_EMAIL
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

  local zone_resp zone_id
  zone_resp=$(cloudflare_api GET "/zones?name=${DEPLOY_DOMAIN}" || true)
  zone_id=$(printf '%s' "$zone_resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    zones = [z for z in d.get('result', []) if z.get('name') == sys.argv[1]]
    print(zones[0]['id'] if zones else '')
except Exception:
    print('')
" "$DEPLOY_DOMAIN")

  if [[ -z "$zone_id" ]]; then
    log_warn "Domain '$DEPLOY_DOMAIN' was not found in this Cloudflare account (or the token lacks Zone:Read)."
    log_warn "The DNS-01 challenge requires the zone to be managed by Cloudflare."
    log_warn "Add the domain to Cloudflare (dash.cloudflare.com → Add a site), set your A records, then re-run."
    confirm "Continue anyway?" || exit 1
    return 0
  fi

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
    log_info "Installing Docker Engine via the official convenience script..."
    curl -fsSL https://get.docker.com | sh
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
    log_info "Installing Coolify via the official installer (this takes a few minutes)..."
    curl -fsSL https://coolify.io/install | bash
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
    die "Coolify has no admin user yet. Open ${COOLIFY_UI_URL:-http://$PUBLIC_IP:8000} once in a browser to complete setup, then re-run."
  fi
  log_warn "Coolify is freshly installed and has no admin user yet."
  prompt _ "Open ${COOLIFY_UI_URL:-http://$PUBLIC_IP:8000} in your browser, complete the first-run setup (create the admin account), then press Enter"
}

switch_proxy_to_caddy() {
  log_step "Phase 5b: Switch Coolify proxy to Caddy (Cloudflare DNS-01)"

  # Programmatic switch via Laravel tinker; falls back to UI instructions.
  if ! docker inspect coolify-proxy >/dev/null 2>&1 || $FORCE; then
    local switch_result
    switch_result=$(docker exec coolify php artisan tinker --execute="\App\Models\Server::find(0)?->changeProxy('CADDY', false); echo 'OK';" 2>&1 | tail -n1 || true)
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
  chmod 600 "$PROXY_COMPOSE"
  log_info "Caddy proxy compose written (Cloudflare DNS-01 enabled)"

  (cd "$COOLIFY_PROXY_DIR" && docker compose up -d --force-recreate 2>&1 | tail -n 3 || true)

  for i in $(seq 1 36); do
    local status
    status=$(docker inspect --format '{{.State.Health.Status}}' coolify-proxy 2>/dev/null || echo "starting")
    [[ "$status" == "healthy" ]] && { log_info "Caddy proxy is healthy"; return 0; }
    [[ $((i % 6)) -eq 0 ]] && log_info "  waiting for Caddy proxy build/start (${i}/36)..."
    sleep 5
  done
  log_warn "Caddy proxy not healthy yet — check: docker logs coolify-proxy"
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
coolify_api() { # method path [data]
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS --max-time 30 -X "$method" "http://127.0.0.1:8000/api/v1${path}")
  args+=(-H "Authorization: Bearer ${COOLIFY_API_TOKEN}" -H "Content-Type: application/json")
  [[ -n "$data" ]] && args+=(-d "$data")
  curl "${args[@]}"
}

create_application() {
  log_step "Phase 7: Application creation in Coolify"

  local server_uuid
  server_uuid=$(coolify_api GET "/servers" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    servers = d.get('servers', d.get('data', d.get('result', [])))
    print(servers[0]['uuid'] if servers else '')
except Exception:
    print('')
")
  [[ -n "$server_uuid" ]] || die "Could not determine the Coolify server UUID. Check the API token permissions."

  # Project (reuse if it already exists)
  local project_uuid
  project_uuid=$(coolify_api GET "/projects" | python3 -c "
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
" || true)
  if [[ -z "$project_uuid" ]]; then
    project_uuid=$(coolify_api POST "/projects" "{\"name\":\"${APP_NAME}\",\"description\":\"Vooglaadija media link processor\"}" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('uuid', d.get('project_uuid', '')))
except Exception:
    print('')
" || true)
    [[ -n "$project_uuid" ]] || die "Failed to create the Coolify project. API response: $(coolify_api POST "/projects" "{\"name\":\"${APP_NAME}\"}" | head -c 300)"
  fi
  log_info "Project: ${APP_NAME} (${project_uuid})"

  # Environment (Coolify auto-creates 'production'; tolerate conflicts)
  coolify_api POST "/projects/${project_uuid}/environments" "{\"name\":\"${ENVIRONMENT_NAME}\"}" >/dev/null 2>&1 || true

  # Existing application?
  local app_uuid=""
  app_uuid=$(coolify_api GET "/applications" | python3 -c "
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
" || true)

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
    app_uuid=$(printf '%s' "$resp" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('uuid', ''))
except Exception:
    print('')
" || true)
    if [[ -z "$app_uuid" ]]; then
      die "Failed to create the application. API response: $(printf '%s' "$resp" | head -c 500)"
    fi
    log_info "Application created: ${app_uuid}"
  fi
  APP_UUID="$app_uuid"
}

# ── Phase 8: Environment variables ────────────────────────
set_environment() {
  log_step "Phase 8: Environment variables (generated secrets)"

  local db_password redis_password secret_key
  db_password=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  redis_password=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  secret_key=$(openssl rand -hex 32)

  local payload
  payload=$(python3 - "$db_password" "$redis_password" "$secret_key" "$DEPLOY_DOMAIN" <<'PY'
import json, sys
db_pw, redis_pw, secret, domain = sys.argv[1:5]
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
}
print(json.dumps({"data": [{"key": k, "value": v} for k, v in envs.items()]}))
PY
)
  local resp
  resp=$(coolify_api PATCH "/applications/${APP_UUID}/envs/bulk" "$payload")
  if printf '%s' "$resp" | grep -q '"message"'; then
    log_warn "Env bulk update response: $(printf '%s' "$resp" | head -c 300)"
  else
    log_info "Environment variables set (DB_PASSWORD, REDIS_PASSWORD, SECRET_KEY, CORS, ...)"
  fi
}

# ── Phase 9: Deploy + verify ──────────────────────────────
deploy_and_verify() {
  log_step "Phase 9: Deploy and verify"

  log_info "Triggering deployment..."
  coolify_api POST "/deploy" "{\"uuid\":\"${APP_UUID}\"}" >/dev/null 2>&1 || \
    coolify_api POST "/applications/${APP_UUID}/deploy" "" >/dev/null 2>&1 || \
    log_warn "Deploy trigger failed — start the deployment manually from the Coolify UI."

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
summary() {
  log_step "Deployment complete"
  echo -e "${GREEN}  Vooglaadija is live at:${NC} https://${DEPLOY_DOMAIN}"
  echo -e "${GREEN}  Coolify dashboard:${NC}    ${COOLIFY_UI_URL:-http://$PUBLIC_IP:8000}"
  echo ""
  echo "  Continuous deployment is enabled: every push to the '${GIT_BRANCH}' branch of"
  echo "  ${REPO_URL}"
  echo "  builds new images in GitHub Actions (GHCR) and Coolify redeploys automatically."
  echo ""
  echo "  TLS: wildcard certificate for ${DEPLOY_DOMAIN} and *.${DEPLOY_DOMAIN} is issued"
  echo "  via the Cloudflare DNS-01 challenge and renewed automatically by Caddy."
  echo ""
  echo "  Secrets (DB password, Redis password, JWT key) were generated and stored"
  echo "  encrypted in Coolify. Back them up! View them under the application's"
  echo "  Environment Variables in the Coolify UI."
  echo ""
  echo "  Optional extras (profiles in docker-compose.yml):"
  echo "    monitoring: Prometheus + Grafana"
  echo "    backup:     daily PostgreSQL dumps"
  echo "  To enable them, add the profile in Coolify's Docker Compose start command, e.g."
  echo "    up -d --pull always --remove-orphans --profile monitoring"
}

# ── Main ──────────────────────────────────────────────────
main() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║   Vooglaadija — Plug-n-Play VPS Bootstrap            ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
  echo ""

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
  deploy_and_verify || true
  summary
}

main "$@"
