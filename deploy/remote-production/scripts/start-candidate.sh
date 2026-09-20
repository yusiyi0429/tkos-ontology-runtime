#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
compose=(docker compose --project-directory "$root" --env-file "$root/.env" -f "$root/compose.yaml")

test "$(id -u)" = "0"
test -s "$root/.env"
test -s "$root/private/merged.dump"
test ! -e "$root/restore-complete"

"${compose[@]}" config --quiet
"${compose[@]}" up -d --pull never --wait --wait-timeout 120 postgres minio
"${compose[@]}" --profile ops run --rm --no-deps db-admin prepare
"${compose[@]}" --profile ops run --rm --no-deps restore
install -m 400 /dev/null "$root/restore-complete"
"${compose[@]}" --profile ops run --rm --no-deps db-admin transfer
"${compose[@]}" --profile ops run --rm --no-deps migrate
"${compose[@]}" --profile ops run --rm --no-deps db-admin grants
"${compose[@]}" --profile ops run --rm --no-deps minio-init
"${compose[@]}" --profile ops run --rm --no-deps db-admin provision-narrative
"${compose[@]}" up -d --pull never --wait --wait-timeout 180 runtime-api runtime-worker
"${compose[@]}" --profile ops run --rm --no-deps db-admin verify

printf '%s\n' 'candidate=ready' 'bound=127.0.0.1' 'port=8030' 'cutover=not-yet'
