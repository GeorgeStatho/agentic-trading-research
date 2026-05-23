#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DOMAIN="${1:-dashboard.example.com}"
EMAIL="${2:-}"

echo "Setting up HTTPS for: $DOMAIN"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not available on PATH." >&2
  exit 1
fi

cd "$PROJECT_ROOT"

mkdir -p certbot/conf certbot/www

echo "Starting web container with bootstrap Nginx config..."
docker compose \
  -f docker-compose.yml \
  -f docker-compose.bootstrap.yml \
  up -d --build --no-deps web

echo "Requesting Let's Encrypt certificate..."

if [ -n "$EMAIL" ]; then
  EMAIL_ARGS=(--email "$EMAIL" --agree-tos)
else
  EMAIL_ARGS=(--register-unsafely-without-email --agree-tos)
fi

docker compose run --rm --entrypoint certbot certbot certonly \
  --webroot \
  -w /var/www/certbot \
  -d "$DOMAIN" \
  "${EMAIL_ARGS[@]}" \
  --non-interactive

echo "Restarting web container with production HTTPS Nginx config..."
docker compose up -d --build --no-deps web

echo "Starting all services..."
docker compose up -d

echo "Done."
echo "Your dashboard should be available at:"
echo "https://$DOMAIN/#/dashboard"
