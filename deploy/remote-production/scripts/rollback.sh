#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?release id required}"
backup_root="/etc/tokenhub/runtime-cutover-backups"
clark_root="/srv/tokenhub/apps/clark-main"
clark_state="/srv/tokenhub/apps/clark-main/current.env"
clark_nginx="/etc/nginx/sites-available/clark-internal.conf"

test "$(id -u)" = "0"

memory_backup="$backup_root/memory-api.conf.$release_id.before"
if test -s "$memory_backup"; then
  cp -a "$memory_backup" /etc/nginx/sites-available/memory-api.conf
  chmod 644 /etc/nginx/sites-available/memory-api.conf
fi

systemctl enable --now aw-memory-api.service
docker start tkos-memory-memory-service-1 >/dev/null 2>&1 || true

# shellcheck disable=SC1090
source "$clark_state"
if test -n "${PREVIOUS_CONTAINER:-}" && test -n "${PREVIOUS_PORT:-}"; then
  docker stop --time 30 "${CURRENT_CONTAINER:-}" >/dev/null 2>&1 || true
  docker start "$PREVIOUS_CONTAINER" >/dev/null
  nginx_backup="$backup_root/clark-internal.conf.$release_id.before"
  test -s "$nginx_backup"
  cp -a "$nginx_backup" "$clark_nginx"
  chmod 644 "$clark_nginx"
  cat > "$clark_state" <<EOF
CURRENT_RELEASE='$PREVIOUS_RELEASE'
CURRENT_CONTAINER='$PREVIOUS_CONTAINER'
CURRENT_PORT='$PREVIOUS_PORT'
EOF
  chmod 600 "$clark_state"
  if test -d "$clark_root/releases/$PREVIOUS_RELEASE"; then
    ln -sfn "$clark_root/releases/$PREVIOUS_RELEASE" "$clark_root/current"
  fi
fi

clark_env_backup="$backup_root/clark-main.env.$release_id.before"
if test -s "$clark_env_backup"; then
  cp -a "$clark_env_backup" /etc/tokenhub/clark-main.env
  chmod 600 /etc/tokenhub/clark-main.env
fi

nginx -t >/dev/null
systemctl reload nginx
curl -fsS https://memory-api.tokenkingos.com/healthz >/dev/null
curl -fsS https://demo.tokenkingos.com/api/health >/dev/null

# The candidate remains intact for diagnosis; no volume, image, or data is deleted.
printf 'rollback=ok\nrelease=%s\nruntime_candidate=preserved\n' "$release_id"
