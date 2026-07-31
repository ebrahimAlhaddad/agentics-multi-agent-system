#!/bin/bash

set -e

# Help message
usage() {
  echo "Usage: $0 -n <tunnel_name> -d <app.yourdomain.com>"
  exit 1
}

# Parse CLI arguments
while [[ "$#" -gt 0 ]]; do
  case $1 in
    -n|--name) TUNNEL_NAME="$2"; shift ;;
    -d|--domain) FULL_DOMAIN="$2"; shift ;;
    *) echo "Unknown parameter passed: $1"; usage ;;
  esac
  shift
done

if [[ -z "$TUNNEL_NAME" || -z "$FULL_DOMAIN" ]]; then
  usage
fi

USER_HOME=$(eval echo ~$USER)
CONFIG_DIR="$USER_HOME/.cloudflared"
CONFIG_FILE="$CONFIG_DIR/config.yml"
CREDS_FILE="$CONFIG_DIR/${TUNNEL_NAME}.json"
CERT_FILE="$CONFIG_DIR/cert.pem"

echo "🌐 Tunnel name: $TUNNEL_NAME"
echo "🌐 Domain: $FULL_DOMAIN"

# Step 0: Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
  echo "❌ 'cloudflared' is not installed. Install it first: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install/"
  exit 1
fi

# Step 1: Authenticate with Cloudflare (skip if already logged in)
if [[ -f "$CERT_FILE" ]]; then
  echo "✅ Already logged into Cloudflare (cert.pem found)"
else
  echo "🔐 Logging into Cloudflare..."
  cloudflared tunnel login
fi

# Step 2: Create the tunnel if it doesn’t exist
if cloudflared tunnel list | grep -q "$TUNNEL_NAME"; then
  echo "✅ Tunnel '$TUNNEL_NAME' already exists."
else
  echo "🚧 Creating tunnel '$TUNNEL_NAME'..."
  cloudflared tunnel create "$TUNNEL_NAME"
fi

# Step 3: Create config.yml if missing
if [[ -f "$CONFIG_FILE" ]]; then
  echo "✅ Tunnel config file already exists at $CONFIG_FILE"
else
  echo "📝 Writing new config.yml..."
  mkdir -p "$CONFIG_DIR"
  cat > "$CONFIG_FILE" <<EOL
tunnel: $TUNNEL_NAME
credentials-file: $CREDS_FILE

ingress:
  - hostname: $FULL_DOMAIN
    service: http://localhost:3000
  - service: http_status:404
EOL
fi

# Step 4: Set up DNS routing if not already set
if cloudflared tunnel route lookup "$FULL_DOMAIN" 2>&1 | grep -q "$TUNNEL_NAME"; then
  echo "✅ DNS route already exists for $FULL_DOMAIN"
else
  echo "📡 Creating DNS route for $FULL_DOMAIN..."
  cloudflared tunnel route dns "$TUNNEL_NAME" "$FULL_DOMAIN"
fi

# Step 5: Start the tunnel
echo "🚀 Starting tunnel..."
cloudflared tunnel run "$TUNNEL_NAME"
