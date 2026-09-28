# world-02：tkos.world/0.2 实验实例

在火山主机 `tokenhub-prod`（115.191.26.230，x86_64，Ubuntu 22.04，部署用户 tokenhub-deploy）上，与 world-lab（0.1 联调，8040）并排再起一套独立的 compose 项目，跑 `world/0.2` 分支的某个提交，scope 默认契约 `tkos.world/0.2`，对外走宿主机 Nginx 的 `world-02.tokenkingos.com`。它有自己的 PostgreSQL 与 MinIO 卷。0.2 锁版前迁移 0039 会重写（ADR-0009），所以这个实例**换版本即重建**：数据可以整体清空，不做原地升级。

复用 `deploy/offline-release/` 的 compose 与 `start-offline.sh`，只换 env、端口、项目名与镜像标签。主机不通 GitHub 与 Docker Hub（PyPI、daocloud 通），镜像在本机构建后传过去；三个基础镜像沿用主机上已有的 `tkos/offline-postgres-pgvector|offline-minio|offline-minio-mc:v0.5.0-amd64`，只自建 `tkos/ontology-runtime` 与 `tkos/ontology-worker` 两个。

下文 `C=<短提交>`（构建提交的前 7 位，本机 `C=$(git rev-parse origin/world/0.2 | cut -c1-7)`）、`APP=/srv/tokenhub/apps/tkos-world-02`、`SRC=$APP/releases/world-02-$C`（源码）、`LAB=$APP/world-02`（env 与输出）、`B=world-02-$C-amd64`（构建产物目录名）。

本目录的文件：`build_images.py`（本机构建两个镜像并打包）、`world-02.env.example`（env 模板）、`spec.example.json`（scope 的域、主体与角色）、`provision.py`（建 scope、域、主体、角色并生成 0.2 策略文件）、`provision-and-install.sh`（第 4–5 步整段，每个 scope 跑一遍，可重跑）、`smoke.py`（第 7 步：实验 scope 只探活，冒烟 scope 跑整条链，可重跑）、`nginx/world-02.conf`。

## 与 world-lab 的隔离

| | world-lab（0.1） | world-02（0.2） |
|-|-|-|
| 域名 → 端口 | `world-lab.tokenkingos.com` → 127.0.0.1:8040 | `world-02.tokenkingos.com` → 127.0.0.1:8050 |
| compose 项目（容器与卷的前缀） | `tkos-world-lab` | `tkos-world-02` |
| 应用目录 | `/srv/tokenhub/apps/tkos-world-lab` | `/srv/tokenhub/apps/tkos-world-02` |
| 源码与 MinIO 应用密钥（`private/`） | `releases/v0.5.0/deploy/offline-release/private` | `releases/world-02-<C>/deploy/offline-release/private` |
| 自建镜像 | `v0.5.0-amd64` | `world-02-<C>-amd64` |
| 库、角色、桶 | `tkos_worldlab*`、`tkos-worldlab-*` | `tkos_world02*`、`tkos-world02-*` |
| Nginx | `sites-available/world-lab.conf` | `sites-available/world-02.conf` |

三个基础镜像两边共用，只读，不要删。所有 compose 命令都带 `--env-file $LAB/.env`：项目名从它来，漏了它 compose 会落到默认项目 `tkos-ontology-runtime`，`down -v` 之类的命令就会打到别处。

## 0. 前提

- DNS：`world-02` 的 A 记录指向 115.191.26.230（已生效），第 6 步要用。
- 端口：8050（主机已用 22、80、443、1933、3120、3121、5435、8040、8080、8081）。
- 构建提交必须含本目录（合入 #58 之后的 `world/0.2`）：主机上的脚本来自源码包，`build_images.py` 对不含本目录的提交会警告（`deploy_scripts_included: false`）。

## 1. 本机构建并传包

```bash
git fetch origin
python3 deploy/world-02/build_images.py --commit origin/world/0.2 --arch amd64 --output-dir ~/tkos-world-02-build \
  [--wheel-cache <已有的 amd64 wheelhouse 目录>]
```

脚本用 `git archive` 取提交源码（不看工作区），按提交里的 `requirements.lock` 取第三方 wheel（`--wheel-cache` 里架构与哈希对得上的直接复用，缺的在 Docker 里用 pip 下载，与发布工具同一个镜像与平台标签）、从提交源码构建项目 wheel，再按提交里的 Dockerfile 构建两个镜像（标签 `world-02-<C>-amd64`，版本标签是包版本，修订标签是完整提交号）。构建后在镜像里核对 `/opt/tkos/docs` 下 0.2 的 profile、契约、登记与支持登记和提交源码逐字节一致、协议支持集合含 `tkos.world/0.2`。产物在 `~/tkos-world-02-build/$B/`：`world-02-$C-src.tar.gz`、`$B-images.tar.gz`、`build-manifest.json`、`SHA256SUMS`。输出目录已存在就拒绝。

```bash
(cd ~/tkos-world-02-build/$B && shasum -a 256 -c SHA256SUMS)
ssh tokenhub-prod "mkdir -p $APP/releases $APP/world-02"
rsync -a --partial --timeout=60 ~/tkos-world-02-build/$B tokenhub-prod:$APP/world-02/      # 断了重跑即续传
```

## 2. 主机上加载与配置

```bash
cd $LAB/$B && sha256sum -c SHA256SUMS
gunzip -c $B-images.tar.gz | docker load
docker images | grep -E "world-02-$C-amd64|offline-.*v0.5.0-amd64"     # 两个自建、三个基础
mkdir -p $SRC && tar -xzf world-02-$C-src.tar.gz -C $SRC
test -f $SRC/deploy/world-02/provision-and-install.sh

cp $SRC/deploy/world-02/world-02.env.example $LAB/.env && chmod 600 $LAB/.env
sed -i "s/__COMMIT__/$C/g" $LAB/.env
python3 - $LAB/.env <<'PY'
import re, secrets, sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = s.replace("CHANGE_ME_MINIO_ROOT_USER", secrets.token_hex(8))
p.write_text(re.sub(r"CHANGE_ME[A-Z_]*", lambda m: secrets.token_hex(24), s))
PY
grep -cE 'CHANGE_ME|__[A-Z_]+__' $LAB/.env          # 应为 0
cd $SRC/deploy/offline-release && mkdir -m 700 -p private
openssl rand -hex 16 > private/minio-app-access-key
openssl rand -hex 32 > private/minio-app-secret-key
sudo chown 10001:0 private/minio-app-* && sudo chmod 440 private/minio-app-*
cp $SRC/deploy/world-02/spec.example.json $LAB/spec.json      # 实验 scope；要改域或主体就改这份
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); s["tenant_id"]+="-smoke"; s["notes"]="world-02 冒烟 scope：只给 smoke.py 建对象，可随时清空重建"; json.dump(s,open(sys.argv[2],"w"),ensure_ascii=False,indent=1)' $SRC/deploy/world-02/spec.example.json $LAB/spec-smoke.json
```

MinIO 应用密钥文件必须是属主 10001、属组 0、权限 440：`minio-init` 以 root 运行但 `cap_drop: [ALL]`，只能按普通的 uid 0 / gid 0 判权限，API 与 Worker 以 uid 10001 运行；停在部署用户的 600 会让 `minio-init` 读不到（world-lab 2026-09-28 主机实测）。之后部署用户读这两个文件要 `sudo cat`。

一个实例供给两个 scope：**实验 scope**（`spec.json` → `out/`，tenant `tokenking-world-02`）给天枢与 E&O 用，冒烟一个对象都不在里面建；**冒烟 scope**（`spec-smoke.json` → `out-smoke/`，tenant `tokenking-world-02-smoke`，域与主体的键和实验 scope 一样）只给 `smoke.py` 建对象。两个 scope 的身份、凭证、对象互不可见（scope 外 404）。

`spec.json` 的键就是凭证文件名，`smoke.py` 按 `spec.example.json` 的键写（改键就改它的 `KEYS`）：公司域 `company`、责任单元 `eo`（E&O）与 `agents`（Agents）；人 `ceo`、`eo-dri`、`agents-dri`、`eo-owner`（E&O 的 Mission Owner，兼 IC）、`eo-ic`、`agents-ic`；Agent `tianshu`（天枢服务主体，在三个域都持 AGENT，用于代记与每周快照）、`eo-coagent`（E&O 的 Co-Agent）、`exec-agent`（执行 Agent，在 E&O 持 AGENT）。CEO 在三个域都持 CEO（确认单元里的周期目标、在单元域建责任单元要用）。

## 3. 启动

```bash
cd $SRC/deploy/offline-release && ./start-offline.sh $LAB/.env
```

依次起 PostgreSQL 与 MinIO、建桶与应用账号、建 owner 与 app 双角色、迁移到 0039、收紧授权、起 API 与 Worker，最后请求 `127.0.0.1:8050/v1/health`。重复执行幂等。

## 4–5. 供给、装 0.2、签发凭证（先实验 scope，再冒烟 scope）

```bash
mkdir -p $LAB/out $LAB/out-smoke && sudo chown 10001 $LAB/out $LAB/out-smoke   # 一次性容器以 uid 10001 运行，要能写
SRC=$SRC LAB=$LAB bash $SRC/deploy/world-02/provision-and-install.sh                                        # 实验：spec.json -> out/
SRC=$SRC LAB=$LAB SPEC=$LAB/spec-smoke.json OUT=$LAB/out-smoke bash $SRC/deploy/world-02/provision-and-install.sh   # 冒烟
sudo chown -R "$USER:$USER" $LAB/out $LAB/out-smoke   # 收回给部署用户；以后要重跑本步，先再 chown 给 10001
ls -l $LAB/out $LAB/out-smoke
```

脚本一次供给一个 scope：`SPEC` 默认 `$LAB/spec.json`、`OUT` 默认 `$LAB/out`，两遍各自按下面的幂等规则跑，下文的 `out/` 对冒烟那一遍就是 `out-smoke/`。

1. `provision.py`（在 `migrate` 服务的容器里跑，`DATABASE_URL` 是 owner 连接串）先校验 spec，再在一个事务里建 scope、域、主体与角色指派，并写 `out/ids.json`（scope、域、主体 id 与角色）、`out/scope-policy.json`（默认协议 `tkos.world`、默认契约 `tkos.world/0.2`、钉 0.2 profile）、每个域一份 `out/activation-<域id>.json`。激活策略与 0.2 验收（`acceptance/world_v02/fixture.py`）同一规则：只开支持登记里已实现的动作，门动作按登记的门表（周期目标的承诺 DOMAIN_DRI、确认 CEO，Mission 的承诺 OWNER、确认 DOMAIN_DRI，长期目标确认与关注标记 CEO），其余对全部 world 角色开放，谁能做什么由服务代码的责任人规则拦下。`out/ids.json` 已存在就跳过（否则会建出第二个 scope）。
2. 镜像里的 `tkos-governed-control`（安装材料在 `/opt/tkos/docs/`，owner 连接串给 `MIGRATION_DATABASE_URL`）依次 `install-profile`（0.2 的 profile、契约、登记三份一起校验钉定）、`install-policy`、`set-registry`（0.2 支持登记）、每个域一次 `install-activation-policy`，最后 `status`。重跑时 profile 报 `already_installed`，策略与登记各加一条同内容的新序号，无害。
3. 每个主体签一枚 90 天的凭证写进 `out/<键>.token`（0600，不进日志）；已有的文件跳过。到期前 `rotate-credential --grace-minutes` 轮换，吊销 `revoke-credential`。

一次性容器都以 `run -T … </dev/null` 起：不接 `/dev/null` 的话 `docker compose run` 会吃掉管道里的 stdin，签发循环只走一轮。

## 6. Nginx 与证书

先放一个只有 80 的临时 server 让 certbot 用 webroot 校验（与 memory-api、world-lab 同法：`/var/www/certbot`、ecdsa，续期也走 webroot），签到证书再换正式配置。正式配置引用证书文件，证书没签之前 `nginx -t` 过不了，顺序不能反；正式配置的 80 server 保留 acme-challenge 路径给续期：

```bash
sudo tee /etc/nginx/sites-available/world-02.conf >/dev/null <<'NG'
server {
    listen 80;
    server_name world-02.tokenkingos.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 404; }
}
NG
sudo ln -sf /etc/nginx/sites-available/world-02.conf /etc/nginx/sites-enabled/world-02.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d world-02.tokenkingos.com --key-type ecdsa --non-interactive
sudo cp $SRC/deploy/world-02/nginx/world-02.conf /etc/nginx/sites-available/world-02.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --cert-name world-02.tokenkingos.com --dry-run
```

`nginx -t` 不过就别 reload：删掉 `sites-enabled/world-02.conf` 再查，同机还有 world-lab、memory-api 与别的站点。

## 7. 冒烟

```bash
python3 $SRC/deploy/world-02/smoke.py http://127.0.0.1:8050 $LAB/out --probe-only        # 实验 scope：不写库
python3 $SRC/deploy/world-02/smoke.py http://127.0.0.1:8050 $LAB/out-smoke               # 冒烟 scope：整条链
# Nginx 之后把地址换成 https://world-02.tokenkingos.com 各跑一次
```

只用标准库，只从给的目录读凭证、不打印。两种都先查：健康、`/openapi.json` 的 world 路由、无凭证 401、每枚凭证都能认证且读不存在（scope 外）的对象 404。对实验 scope 只跑到这里（`--probe-only`），加上第 5 步末尾的 `ctl status`，就是不写库的确认：凭证与 scope 可用，里面没有冒烟对象。完整冒烟只认 tenant 以 `-smoke` 结尾的 scope，对 `out/` 跑会直接 FAIL。它经 prepare 与 commit 按 0.2 走一条链，每步断言回执、生命周期与读回：

- 骨架（一个 scope 一套）：CEO 建 Company 与 Strategy（责任结构块带 `eo`、`agents` 两个责任单元条目组件），CEO 建两个责任单元（`architecture_ref` 以组件引用指到各自的条目）并指派 DRI。
- 每跑一次新建（标题以「冒烟 <run>」开头）：公司级与 E&O 长期目标（CEO 确认）→ 周期目标（DRI 承诺、CEO 确认）→ Mission（DRI 指派 Owner、Owner 承诺、DRI 确认、Owner 开始）→ Task（Owner 指派 IC、IC 开始）→ Activity（IC 指派执行 Agent，Agent 带写入声明开始、交付，IC 验收）→ Task 交付、Owner 验收 → 天枢记一条外部事件（每周同步）、写引用它的执行状态快照（`source_event_refs`，进展条目组件），读回生成者与未经确认标记 → CEO 给天枢登记一小时的门委托（E&O 域）、天枢代 CEO 标核心战役（读回代记信息）、CEO 撤销委托 → 执行 Agent 从 Activity 取上下文，沿主干到 Company，Task 的验收条件以钉定的组件引用返回。

建好的 id 记在 `out-smoke/smoke-world-02.json`：骨架在 `skeleton`（一个 scope 只有一个 Company、一个域只有一个责任单元），重跑沿用；每次的对象在 `runs`。骨架用确定的幂等键建，即使这个文件丢了，重跑也会拿回同一套骨架（前提是骨架没被改过）。冒烟 scope 里的对象随时可以连同实验 scope 一起按第 9 节清掉。

`--mcp-cli`：链跑完后再以执行 Agent 的凭证、`TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2` 走命令行与 MCP：`tkos-world` 读 Activity、取上下文、`act world_record_event` 写一条外部事件；经 stdio 起 `tkos-world-mcp`，工具清单是 0.2 Agent 面的十个（五读五写，#63 加了列对象），读 Activity、写一条外部事件（做法同 `acceptance/world_v02/agent_face.py`），运行日志不含凭证；两条事件经 HTTP 读回，记录者是执行 Agent。它要 `tkos-world`、`tkos-world-mcp` 与 MCP 客户端，主机上没有，在本机仓库检出里跑：`uv run python deploy/world-02/smoke.py https://world-02.tokenkingos.com <out-smoke> --mcp-cli`，`<out-smoke>` 是第 10 节归档里冒烟 scope 的那份（`ids.json`、凭证与 `smoke-world-02.json`）。

## 8. 交付

只交实验 scope（`out/`）的东西，冒烟 scope 的凭证与清单不给任何人：

- 地址 `https://world-02.tokenkingos.com`，`/openapi.json` 可访问；清单 `out/ids.json`（域、主体 id 与角色）。
- `out/tianshu.token` 给天枢，人的凭证给本人，Agent 的凭证给运行它的人，都走安全渠道，不进文档与聊天。

## 9. 清空重建

同一版本下清数据：停掉并删卷，删掉 `out/` 与 `out-smoke/`，从第 3 步重来（只动 `tkos-world-02` 这个项目）。`.env` 与 MinIO 应用密钥可以沿用：新卷按它们初始化。

```bash
cd $SRC/deploy/offline-release && docker compose --env-file $LAB/.env -f compose.yaml down -v
sudo rm -rf $LAB/out $LAB/out-smoke
```

两个 scope 的凭证全部换新，第 10 节的归档要整目录替换。

## 9.5 换版本即重建

锁版前 0039 会重写，已应用旧 0039 的库再迁移会报「已应用的迁移文件被改动过」，所以换提交一律重建，不原地升级。新提交记作 `C2`：

```bash
# 本机：按第 1 节构建 C2 并传包；主机：按第 2 节校验、加载、解到 releases/world-02-$C2（记作 SRC2）
cd $SRC/deploy/offline-release && docker compose --env-file $LAB/.env -f compose.yaml down -v   # 用旧发布目录停并删卷
sudo rm -rf $LAB/out $LAB/out-smoke
sudo cp -a $SRC/deploy/offline-release/private $SRC2/deploy/offline-release/                  # 带属主 10001:0 与 440
sed -i -E "s#(tkos/ontology-(runtime|worker)):world-02-[0-9a-f]+-amd64#\1:world-02-$C2-amd64#" $LAB/.env
cd $SRC2/deploy/offline-release && ./start-offline.sh $LAB/.env
# 之后按第 4–5 节（SRC=$SRC2）与第 7 节，再按第 10 节重新归档
```

Nginx 配置不随版本变时不用动（`cmp $SRC2/deploy/world-02/nginx/world-02.conf /etc/nginx/sites-available/world-02.conf`）。旧镜像与旧发布目录确认不用回退后再删（`docker image rm tkos/ontology-{runtime,worker}:world-02-$C-amd64`，旧 `private/` 是密钥副本）。

## 10. 归档到 tkos-secrets

密钥与清单放进私有仓库 `VanillaCoca/tkos-secrets`（明文、LF 行尾、只能靠轮换撤销）的 `ontology-runtime/world-02/`。主机不通 GitHub，先拉回本机再推：

```bash
git clone https://github.com/VanillaCoca/tkos-secrets.git /tmp/tkos-secrets
D=/tmp/tkos-secrets/ontology-runtime/world-02; rm -rf "$D"; mkdir -p "$D"      # 重建后整目录替换
scp tokenhub-prod:$LAB/.env "$D/compose.env"
for k in minio-app-access-key minio-app-secret-key; do ssh tokenhub-prod "sudo cat $SRC/deploy/offline-release/private/$k" > "$D/$k"; done
scp "tokenhub-prod:$LAB/spec.json" "tokenhub-prod:$LAB/out/ids.json" "tokenhub-prod:$LAB/out/*.token" "$D/"             # 实验 scope
mkdir -p "$D/smoke" && scp "tokenhub-prod:$LAB/spec-smoke.json" "tokenhub-prod:$LAB/out-smoke/ids.json" \
  "tokenhub-prod:$LAB/out-smoke/smoke-world-02.json" "tokenhub-prod:$LAB/out-smoke/*.token" "$D/smoke/"             # 冒烟 scope
# 另写 $D/README.md 列出每个文件是什么（照 ontology-runtime/world-lab/README.md），注明 smoke/ 只给冒烟用、不交付；
# 并在仓库根 README 的目录表里加一行
cd /tmp/tkos-secrets && git add -A && git commit -m "world-02: 0.2 实验实例的 compose.env、MinIO 应用密钥、两个 scope 的清单与凭证" && git push
rm -rf /tmp/tkos-secrets
```

## 已核与未验证

已核（2026-09-28 本机）：用 `build_images.py` 对 49abd15 构建 amd64 与 arm64、对 7c58d9e（合入 #57）构建 arm64，镜像内 0.2 安装材料与提交源码一致、协议支持集合含 0.2，镜像包 `docker load` 可加载。本机预演（项目 `tkos-world-02-rehearsal`、端口 8051、7c58d9e 的 arm64 镜像、同一份 compose 与本目录的脚本，`spec-smoke.json` 用第 2 步那行命令生成）：第 3 步；第 4–5 步两个 scope 各两遍（第一遍各装 profile、策略、登记与三个域的激活策略、签 9 枚凭证；第二遍供给与凭证跳过、profile 报已安装）；实验 scope 的 `--probe-only` 13 项全过，对它跑完整冒烟在建任何对象之前 FAIL；冒烟 scope 的完整冒烟两遍（94 项、92 项全过），第二遍沿用骨架并带 `--mcp-cli`（CLI 三项、MCP 四项与两条事件的 HTTP 读回全过）。收尾时库里两个 scope，实验 scope 的对象、事件、回执都是 0，冒烟 scope 18 个对象、62 条事件、62 张回执。预演栈已 `down -v`。

主机（2026-09-29，会话代跑）：先按 88d765e 走完第 1–8 步，#63 合入后按第 9.5 节换到 c003b03 重建，再按第 10 节归档（tkos-secrets 998d0d0）。c003b03 上：两个 scope 各签 9 枚凭证（90 天）；实验 scope 的 `--probe-only` 在本机口与域名都过；冒烟 scope 的完整冒烟本机口 94 项、域名 83 项全过（第二遍沿用骨架）；本机仓库检出对域名跑 `--mcp-cli` 92 项全过（CLI 三项、MCP 四项，工具清单十个）；库里有 `ix_gov_world_external_refs`，列对象经域名返回 200、未知参数 422。证书 webroot、ecdsa，到期 2026-12-27，续期演练成功。world-lab 全程 200。

主机上踩到的坑：经 `ssh … 'bash -s' <<EOS` 远程跑脚本时，`docker compose exec -T` 同样会吃掉 stdin，把后面的脚本当输入读走，要接 `</dev/null`（`run -T` 已在脚本里这样做）。本机对域名跑冒烟时遇到过一次连接超时，重跑即过。

未验证：并发写下的外部引用唯一性；大数据量下列对象的性能。
