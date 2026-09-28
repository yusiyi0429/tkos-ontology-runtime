#!/bin/bash
# world-lab 第 4–5 步：供给 scope/域/主体，装 world 0.1，签发凭证。
# 用法：SRC=<源码目录> LAB=<实例目录> bash provision-and-install.sh
set -euo pipefail
: "${SRC:?SRC=源码目录}"; : "${LAB:?LAB=实例目录}"
cd "$SRC/deploy/offline-release"
# -T 与 </dev/null：这些一次性容器都不交互；不接 /dev/null 的话 compose run 会吃掉管道里的 stdin，下面的 while read 只走一轮。
dc() { docker compose --env-file "$LAB/.env" -f compose.yaml "$@"; }
dcrun() { dc run -T "$@" </dev/null; }

echo "== 4. provision"
mkdir -p "$LAB/out"
if [ -s "$LAB/out/ids.json" ]; then
  echo "ids.json 已存在，跳过供给（重建要先 compose down -v 并删掉 out/，否则会建出第二个 scope）"
else
  dcrun --rm --no-deps \
    -v "$SRC/deploy/world-lab/provision.py:/ops/provision.py:ro" \
    -v "$LAB/spec.json:/ops/spec.json:ro" \
    -v "$LAB/out:/ops/out" \
    --entrypoint python migrate /ops/provision.py /ops/spec.json /ops/out
fi
ls -l "$LAB/out"

echo "== 5. install world 0.1"
set -a; source "$LAB/.env"; set +a
OWNER="host=postgres port=5432 dbname=${POSTGRES_DB} user=${POSTGRES_OWNER_USER} password=${POSTGRES_OWNER_PASSWORD}"
ctl() { dcrun --rm --no-deps -e MIGRATION_DATABASE_URL="$OWNER" -v "$LAB/out:/ops/out" --entrypoint tkos-governed-control migrate "$@"; }
SCOPE=$(python3 -c "import json;print(json.load(open('$LAB/out/ids.json'))['scope_id'])")
R="world-lab 联调实例"
ctl install-profile --scope-id "$SCOPE" --reason "$R" \
  --profile-json /opt/tkos/docs/contracts/world-profile-0.1.json \
  --contract-file /opt/tkos/docs/contracts/tkos-world-0.1.md \
  --world-registry-file /opt/tkos/docs/contracts/world-registry-0.1.json
ctl install-policy --scope-id "$SCOPE" --content-json /ops/out/scope-policy.json --reason "$R"
ctl set-registry --scope-id "$SCOPE" --protocol-id tkos.world --contract-version tkos.world/0.1 \
  --content-json /opt/tkos/docs/runtime-world-support-0.1.json --reason "$R"
for d in $(python3 -c "import json;print(' '.join(json.load(open('$LAB/out/ids.json'))['domains'].values()))"); do
  ctl install-activation-policy --scope-id "$SCOPE" --domain-id "$d" --content-json "/ops/out/activation-$d.json" --reason "$R"
done
ctl status --scope-id "$SCOPE"

echo "== 5. issue credentials (90 天有效期，到期前用 rotate-credential 轮换)"
python3 -c "import json;ids=json.load(open('$LAB/out/ids.json'));[print(k,p['principal_id']) for k,p in ids['principals'].items()]" |
while read -r key pid; do
  [ -s "$LAB/out/$key.token" ] && { echo "$key.token 已存在，跳过"; continue; }
  ctl issue-credential --scope-id "$SCOPE" --principal-id "$pid" --label "$key" --expires-in-days 90 \
    --token-file "/ops/out/$key.token" --reason "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); print({k: d[k] for k in d if k in ('credential_id','principal_id','label','expires_at','token_file')})"
done
ls -l "$LAB"/out/*.token
echo "STEPS_4_5_DONE"
