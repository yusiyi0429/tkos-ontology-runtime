#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file="${1:-${here}/.env}"
compose=(docker compose --env-file "${env_file}" -f "${here}/compose.yaml")

if [[ ! -f "${env_file}" ]]; then
  echo "missing environment file: ${env_file}" >&2
  exit 2
fi
if grep -Eq 'CHANGE_ME|__[A-Z_]+__' "${env_file}"; then
  echo "environment file still contains placeholders" >&2
  exit 2
fi
for secret in minio-app-access-key minio-app-secret-key; do
  path="${here}/private/${secret}"
  if [[ ! -s "${path}" ]]; then
    echo "missing private secret file: ${path}" >&2
    exit 2
  fi
done

"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-build --pull never --wait postgres minio
"${compose[@]}" run --rm --no-deps minio-init
"${compose[@]}" run --rm --no-deps db-admin prepare
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps db-admin grants
"${compose[@]}" run --rm --no-deps db-admin verify
"${compose[@]}" up -d --no-build --pull never --wait runtime-api runtime-worker
"${compose[@]}" ps

port="$(awk -F= '$1 == "RUNTIME_HTTP_PORT" {print $2}' "${env_file}" | tail -1)"
curl --fail --silent --show-error "http://127.0.0.1:${port:-8030}/v1/health"
printf '\nRuntime offline stack is healthy.\n'
