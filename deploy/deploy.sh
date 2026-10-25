#!/bin/sh
# Usage: sh deploy/deploy.sh <staging|prod> <git sha> [registry]
# Runs on the host from /opt/techhub-<env>, a checkout of this repo whose .env holds that environment's settings.
set -eu

env=$1
# CI passes the registry it pushed to (ghcr.io/<owner>/<repo>, which follows a repo rename)
export TAG="$2" DEPLOY_ENV="$1" REGISTRY="${3:-ghcr.io/ajemars/techhub}"

# Older .env files predate the service token: give the host its own instead of failing guest orders
grep -q '^INTERNAL_SERVICE_TOKEN=.' .env || echo "INTERNAL_SERVICE_TOKEN=$(openssl rand -hex 32)" >> .env

compose="docker compose -p techhub-$env -f docker-compose.yaml -f docker-compose.prod.yaml"
$compose pull --quiet
$compose up -d --no-build --remove-orphans --wait --wait-timeout 300
docker image prune -f >/dev/null
