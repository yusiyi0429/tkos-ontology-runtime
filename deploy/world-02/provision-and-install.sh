#!/bin/bash
# world-02 第 4–5 步：按一份 spec 供给一个 scope（域、主体、角色），装 tkos.world/0.2，签发凭证。
# 可重跑：已供给就跳过，已有凭证文件就跳过。实验 scope 与冒烟 scope 各跑一遍，换 SPEC 与 OUT。
# 用法：SRC=<源码目录> LAB=<实例目录> [SPEC=<spec，默认 $LAB/spec.json>] [OUT=<输出目录，默认 $LAB/out>] \
#       bash provision-and-install.sh
set -euo pipefail
: "${SRC:?SRC=源码目录}"; : "${LAB:?LAB=实例目录}"
SPEC="${SPEC:-$LAB/spec.json}"; OUT="${OUT:-$LAB/out}"
cd "$SRC/deploy/offline-release"
# -T 与 </dev/null：这些一次性容器都不交互；不接 /dev/null 的话 compose run 会吃掉管道里的 stdin，下面的 while read 只走一轮。
dc() { docker compose --env-file "$LAB/.env" -f compose.yaml "$@"; }
dcrun() { dc run -T "$@" </dev/null; }

echo "== 4. provision ($SPEC -> $OUT)"
mkdir -p "$OUT"
if [ -s "$OUT/ids.json" ]; then
  echo "$OUT/ids.json 已存在，跳过供给（重建要先 compose down -v 并删掉两个输出目录，否则会建出第二个 scope）"
else
  dcrun --rm --no-deps \
    -v "$SRC/deploy/world-02/provision.py:/ops/provision.py:ro" \
    -v "$SPEC:/ops/spec.json:ro" \
    -v "$OUT:/ops/out" \
    --entrypoint python migrate /ops/provision.py /ops/spec.json /ops/out
fi
ls -l "$OUT"

echo "== 5. install world 0.2"
set -a; source "$LAB/.env"; set +a
OWNER="host=postgres port=5432 dbname=${POSTGRES_DB} user=${POSTGRES_OWNER_USER} password=${POSTGRES_OWNER_PASSWORD}"
ctl() { dcrun --rm --no-deps -e MIGRATION_DATABASE_URL="$OWNER" -v "$OUT:/ops/out" --entrypoint tkos-governed-control migrate "$@"; }
ids() { python3 -c "import json,sys; ids=json.load(open('$OUT/ids.json')); $1"; }
SCOPE=$(ids "print(ids['scope_id'])")
R="world-02（tkos.world/0.2）：$(ids "print(ids['tenant_id'])")"
D=/opt/tkos/docs
ctl install-profile --scope-id "$SCOPE" --reason "$R" \
  --profile-json $D/contracts/world-profile-0.2.json \
  --contract-file $D/contracts/tkos-world-0.2.md \
  --world-registry-file $D/contracts/world-registry-0.2.json
ctl install-policy --scope-id "$SCOPE" --content-json /ops/out/scope-policy.json --reason "$R"
ctl set-registry --scope-id "$SCOPE" --protocol-id tkos.world --contract-version tkos.world/0.2 \
  --content-json $D/runtime-world-support-0.2.json --reason "$R"
for d in $(ids "print(' '.join(ids['domains'].values()))"); do
  ctl install-activation-policy --scope-id "$SCOPE" --domain-id "$d" --content-json "/ops/out/activation-$d.json" --reason "$R"
done
ctl status --scope-id "$SCOPE"

echo "== 5. issue credentials (90 天有效期，到期前用 rotate-credential 轮换)"
ids "[print(k, p['principal_id']) for k, p in ids['principals'].items()]" |
while read -r key pid; do
  [ -s "$OUT/$key.token" ] && { echo "$key.token 已存在，跳过"; continue; }
  ctl issue-credential --scope-id "$SCOPE" --principal-id "$pid" --label "$key" --expires-in-days 90 \
    --token-file "/ops/out/$key.token" --reason "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); print({k: d[k] for k in d if k in ('credential_id','principal_id','label','expires_at','token_file')})"
done
ls -l "$OUT"/*.token
echo "STEPS_4_5_DONE"
