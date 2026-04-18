#!/usr/bin/env bash
# deploy/fly/deploy.sh
# One-command VoiceGateway deployment to Fly.io

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()    { echo -e "${CYAN}→${NC} $*"; }
log_success() { echo -e "${GREEN}✓${NC} $*"; }
log_warn()    { echo -e "${YELLOW}⚠${NC} $*"; }
log_error()   { echo -e "${RED}✗${NC} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.deploy"
IS_FIRST_DEPLOY=false
FROM_SOURCE=false

if [[ "${1:-}" == "--from-source" ]]; then
  FROM_SOURCE=true
  log_info "Building from source (slower but uses latest code)"
fi

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

check_flyctl() {
  if ! command -v flyctl >/dev/null 2>&1; then
    log_error "flyctl is not installed."
    echo ""
    echo "Install it:"
    echo "  macOS:   brew install flyctl"
    echo "  Linux:   curl -L https://fly.io/install.sh | sh"
    echo "  Windows: iwr https://fly.io/install.ps1 -useb | iex"
    echo ""
    echo "Then run this script again."
    exit 1
  fi
}

check_auth() {
  if ! flyctl auth whoami >/dev/null 2>&1; then
    log_warn "Not logged in to Fly.io."
    echo "Run: fly auth login"
    echo "Then run this script again."
    exit 1
  fi
  log_success "Authenticated as $(flyctl auth whoami 2>/dev/null)"
}

# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

generate_or_load_token() {
  if [[ -f "$ENV_FILE" ]] && grep -q "VOICEGW_MCP_TOKEN=" "$ENV_FILE"; then
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    log_info "Using existing MCP token from .env.deploy"
  else
    VOICEGW_MCP_TOKEN="voicegw_mcp_$(openssl rand -hex 32)"
    echo "VOICEGW_MCP_TOKEN=$VOICEGW_MCP_TOKEN" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log_success "Generated new MCP token (saved to .env.deploy)"
  fi
  export VOICEGW_MCP_TOKEN
}

# ---------------------------------------------------------------------------
# Deploy logic
# ---------------------------------------------------------------------------

is_first_time() {
  if [[ ! -f "$SCRIPT_DIR/fly.toml" ]]; then
    return 0
  fi
  if grep -q 'app = "CHANGEME"' "$SCRIPT_DIR/fly.toml"; then
    return 0
  fi
  return 1
}

first_time_deploy() {
  IS_FIRST_DEPLOY=true
  log_info "First-time deployment. Creating Fly app..."

  cd "$REPO_ROOT"

  # Launch with generated name, using our fly.toml as template
  flyctl launch \
    --no-deploy \
    --copy-config \
    --config "$SCRIPT_DIR/fly.toml" \
    --generate-name \
    --yes

  # Move generated fly.toml to deploy/fly/ if flyctl wrote to repo root
  if [[ -f "$REPO_ROOT/fly.toml" ]] && [[ "$REPO_ROOT/fly.toml" != "$SCRIPT_DIR/fly.toml" ]]; then
    mv "$REPO_ROOT/fly.toml" "$SCRIPT_DIR/fly.toml"
  fi

  local app_name
  app_name=$(grep -E '^app = ' "$SCRIPT_DIR/fly.toml" | cut -d'"' -f2)
  local region
  region=$(grep -E '^primary_region = ' "$SCRIPT_DIR/fly.toml" | cut -d'"' -f2)

  log_success "Created app: $app_name in region $region"

  # Switch to build-from-source if requested
  if [[ "$FROM_SOURCE" == "true" ]]; then
    sed -i.bak 's|^  image = .*|  # image = "mahimairaja/voicegateway:latest"|' "$SCRIPT_DIR/fly.toml"
    sed -i.bak 's|^  # dockerfile = .*|  dockerfile = "../../Dockerfile"|' "$SCRIPT_DIR/fly.toml"
    rm -f "$SCRIPT_DIR/fly.toml.bak"
    log_info "Switched fly.toml to build from source"
  fi

  # Create persistent volume
  log_info "Creating persistent volume for SQLite storage..."
  flyctl volumes create voicegw_data \
    --app "$app_name" \
    --region "$region" \
    --size 1 \
    --yes || log_warn "Volume may already exist (continuing)"

  # Set secrets
  log_info "Setting MCP bearer token as Fly secret..."
  flyctl secrets set \
    --app "$app_name" \
    "VOICEGW_MCP_TOKEN=$VOICEGW_MCP_TOKEN"

  # Deploy
  log_info "Deploying (this takes 2-3 minutes)..."
  flyctl deploy \
    --app "$app_name" \
    --config "$SCRIPT_DIR/fly.toml" \
    --remote-only
}

redeploy() {
  local app_name
  app_name=$(grep -E '^app = ' "$SCRIPT_DIR/fly.toml" | cut -d'"' -f2)
  log_info "Redeploying $app_name..."

  # Switch to build-from-source if requested
  if [[ "$FROM_SOURCE" == "true" ]]; then
    sed -i.bak 's|^  image = .*|  # image = "mahimairaja/voicegateway:latest"|' "$SCRIPT_DIR/fly.toml"
    sed -i.bak 's|^  # dockerfile = .*|  dockerfile = "../../Dockerfile"|' "$SCRIPT_DIR/fly.toml"
    rm -f "$SCRIPT_DIR/fly.toml.bak"
    log_info "Switched fly.toml to build from source"
  fi

  # Sync the MCP token to Fly secrets in case it was rotated locally
  log_info "Syncing MCP token to Fly secrets..."
  flyctl secrets set \
    --app "$app_name" \
    "VOICEGW_MCP_TOKEN=$VOICEGW_MCP_TOKEN"

  cd "$REPO_ROOT"
  flyctl deploy \
    --app "$app_name" \
    --config "$SCRIPT_DIR/fly.toml" \
    --remote-only
}

# ---------------------------------------------------------------------------
# Success output
# ---------------------------------------------------------------------------

print_success() {
  local app_name
  app_name=$(grep -E '^app = ' "$SCRIPT_DIR/fly.toml" | cut -d'"' -f2)
  local url="https://${app_name}.fly.dev"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log_success "VoiceGateway is live!"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  Dashboard:    $url"
  echo "  MCP endpoint: $url/mcp/sse"

  if [[ "$IS_FIRST_DEPLOY" == "true" ]]; then
    echo "  MCP token:    $VOICEGW_MCP_TOKEN"
  else
    echo "  MCP token:    (stored in $ENV_FILE)"
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "Next: add to Claude Code"
  echo ""

  if [[ "$IS_FIRST_DEPLOY" == "true" ]]; then
    echo "  claude mcp add voicegateway \\"
    echo "    --transport sse \\"
    echo "    --url $url/mcp/sse \\"
    echo "    --header \"Authorization: Bearer $VOICEGW_MCP_TOKEN\""
  else
    echo "  # Token is in: $ENV_FILE"
    echo "  # To view:  cat $ENV_FILE"
    echo "  claude mcp add voicegateway \\"
    echo "    --transport sse \\"
    echo "    --url $url/mcp/sse \\"
    echo "    --header \"Authorization: Bearer \$(grep VOICEGW_MCP_TOKEN $ENV_FILE | cut -d= -f2)\""
  fi

  echo ""
  echo "Then in Claude Code, try:"
  echo "  > List my voicegateway providers"
  echo "  > Add Deepgram with key <your-key>"
  echo "  > Create a test project"
  echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  echo ""
  log_info "VoiceGateway → Fly.io deployment"
  echo ""

  check_flyctl
  check_auth
  generate_or_load_token

  if is_first_time; then
    first_time_deploy
  else
    redeploy
  fi

  print_success
}

main "$@"
