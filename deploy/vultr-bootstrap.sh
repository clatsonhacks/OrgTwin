#!/usr/bin/env bash
# Run once on a fresh Vultr Cloud Compute VM (Ubuntu 24.04) as root:
#   curl -fsSL <raw url of this file> | bash      or      bash deploy/vultr-bootstrap.sh
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/clatsonhacks/Orgtwin.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/orgtwin}"

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
apt-get install -y git ufw >/dev/null
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

if [ ! -d "$APP_DIR" ]; then
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
mkdir -p secrets
[ -f .env ] || { cp .env.example .env; echo ">> Edit $PWD/.env with your keys, then re-run this script."; }

docker pull "$(grep -E '^SANDBOX_IMAGE=' .env | cut -d= -f2 || echo python:3.12-alpine)"
docker compose up -d --build
docker compose ps
echo ">> OrgTwin is up on http://$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
