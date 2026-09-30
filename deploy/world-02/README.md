# world-02：tkos.world/0.2 实验实例

在火山主机 `tokenhub-prod`（115.191.26.230，x86_64，Ubuntu 22.04，部署用户 tokenhub-deploy）上，与 world-lab（0.1 联调，8040）并排再起一套独立的 compose 项目，跑 `world/0.2` 分支的某个提交，scope 默认契约 `tkos.world/0.2`，对外走宿主机 Nginx 的 `world-02.tokenkingos.com`。它有自己的 PostgreSQL 与 MinIO 卷。0.2 锁版前迁移 0039 会重写（ADR-0009），所以这个实例**换版本即重建**：数据可以整体清空，不做原地升级。

复用 `deploy/offline-release/` 的 compose 与 `start-offline.sh`，只换 env、端口、项目名与镜像标签。主机不通 GitHub 与 Docker Hub（PyPI、daocloud 通），镜像在本机构建后传过去；三个基础镜像沿用主机上已有的 `tkos/offline-postgres-pgvector|offline-minio|offline-minio-mc:v0.5.0-amd64`，只自建 `tkos/ontology-runtime` 与 `tkos/ontology-worker` 两个。

下文 `C=<短提交>`（构建提交的前 7 位，本机 `C=$(git rev-parse origin/world/0.2 | cut -c1-7)`）、`APP=/srv/tokenhub/apps/tkos-world-02`、`SRC=$APP/releases/world-02-$C`（源码）、`LAB=$APP/world-02`（env 与输出）、`B=world-02-$C-amd64`（构建产物目录名）。

本目录的文件：`build_images.py`（本机构建两个镜像并打包）、`world-02.env.example`（env 模板）、`spec.example.json`（冒烟 scope 的域、主体与角色）、`spec.eo.example.json`（实验 scope 的名单，第 11 节）、`provision.py`（建 scope、域、主体、角色并生成 0.2 策略文件）、`provision-and-install.sh`（第 4–5 步整段，每个 scope 跑一遍，可重跑）、`smoke.py`（第 7 步：实验 scope 只探活，冒烟 scope 跑整条链，可重跑）、`examples.py`（第 7.5 节：在冒烟 scope 上实跑、生成给天枢的请求与返回示例）、`seed-eo-2026-10.json` 与 `seed_eo.py`（第 11 节：实验 scope 的 E&O 十月起点，按人分段播种）、`nginx/world-02.conf`。

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
cp $SRC/deploy/world-02/spec.eo.example.json $LAB/spec.json && chmod 600 $LAB/spec.json   # 实验 scope 的名单（第 11 节），三个人的 display_name 在这里改成真名
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); s["tenant_id"]+="-smoke"; s["notes"]="world-02 冒烟 scope：只给 smoke.py 建对象，可随时清空重建"; json.dump(s,open(sys.argv[2],"w"),ensure_ascii=False,indent=1)' $SRC/deploy/world-02/spec.example.json $LAB/spec-smoke.json
```

MinIO 应用密钥文件必须是属主 10001、属组 0、权限 440：`minio-init` 以 root 运行但 `cap_drop: [ALL]`，只能按普通的 uid 0 / gid 0 判权限，API 与 Worker 以 uid 10001 运行；停在部署用户的 600 会让 `minio-init` 读不到（world-lab 2026-09-28 主机实测）。之后部署用户读这两个文件要 `sudo cat`。

一个实例供给两个 scope：**实验 scope**（`spec.json` → `out/`，tenant `tokenking-world-02`）给天枢与 E&O 用，冒烟一个对象都不在里面建；**冒烟 scope**（`spec-smoke.json` → `out-smoke/`，tenant `tokenking-world-02-smoke`，域与主体按 `spec.example.json`）只给 `smoke.py` 建对象。两个 scope 的身份、凭证、对象互不可见（scope 外 404）。

spec 的键就是凭证文件名。实验 scope 的名单见第 11 节。冒烟 scope 按 `spec.example.json`，完整冒烟按它的键写（改键就改 `smoke.py` 的 `KEYS`）：公司域 `company`、责任单元 `eo`（E&O）与 `agents`（Agents）；人 `ceo`、`eo-dri`、`agents-dri`、`eo-owner`（E&O 的 Mission Owner，兼 IC）、`eo-ic`、`agents-ic`；Agent `tianshu`（天枢服务主体，在三个域都持 AGENT，用于代记与每周快照）、`eo-coagent`（E&O 的 Co-Agent）、`exec-agent`（执行 Agent，在 E&O 持 AGENT）。CEO 在三个域都持 CEO（确认单元里的周期目标、在单元域建责任单元要用）。

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

只用标准库，只从给的目录读凭证、不打印。两种都先查：健康、`/openapi.json` 的 world 路由、无凭证 401、每枚凭证都能认证且读不存在（scope 外）的对象 404（探活按 `ids.json` 里实际有的主体逐个查，完整冒烟按 `KEYS`）。对实验 scope 只跑到这里（`--probe-only`），加上第 5 步末尾的 `ctl status`，就是不写库的确认：凭证与 scope 可用，里面没有冒烟对象。完整冒烟只认 tenant 以 `-smoke` 结尾的 scope，对 `out/` 跑会直接 FAIL。它经 prepare 与 commit 按 0.2 走一条链，每步断言回执、生命周期与读回：

- 骨架（一个 scope 一套）：CEO 建 Company 与 Strategy（责任结构块带 `eo`、`agents` 两个责任单元条目组件），CEO 建两个责任单元（`architecture_ref` 以组件引用指到各自的条目）并指派 DRI。
- 每跑一次新建（标题以「冒烟 <run>」开头）：公司级与 E&O 长期目标（CEO 确认）→ 周期目标（DRI 承诺、CEO 确认）→ Mission（DRI 指派 Owner、Owner 承诺、DRI 确认、Owner 开始）→ Task（Owner 指派 IC、IC 开始）→ Activity（IC 指派执行 Agent，Agent 带写入声明开始、交付，IC 验收）→ Task 交付、Owner 验收 → 天枢记一条外部事件（每周同步）、写引用它的执行状态快照（`source_event_refs`，进展条目组件），读回生成者与未经确认标记 → CEO 给天枢登记一小时的门委托（E&O 域）、天枢代 CEO 标核心战役（读回代记信息）、CEO 撤销委托 → 执行 Agent 从 Activity 取上下文，沿主干到 Company，Task 的验收条件以钉定的组件引用返回。

建好的 id 记在 `out-smoke/smoke-world-02.json`：骨架在 `skeleton`（一个 scope 只有一个 Company、一个域只有一个责任单元），重跑沿用；每次的对象在 `runs`。骨架用确定的幂等键建，即使这个文件丢了，重跑也会拿回同一套骨架（前提是骨架没被改过）。冒烟 scope 里的对象随时可以连同实验 scope 一起按第 9 节清掉。

`--mcp-cli`：链跑完后再以执行 Agent 的凭证、`TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2` 走命令行与 MCP：`tkos-world` 读 Activity、取上下文、`act world_record_event` 写一条外部事件；经 stdio 起 `tkos-world-mcp`，工具清单是 0.2 Agent 面的十三个（五读八写，#63 加了列对象，#61 加了提出问题、路由问题、退回形成），读 Activity、写一条外部事件（做法同 `acceptance/world_v02/agent_face.py`），运行日志不含凭证；两条事件经 HTTP 读回，记录者是执行 Agent。它要 `tkos-world`、`tkos-world-mcp` 与 MCP 客户端，主机上没有，在本机仓库检出里跑：`uv run python deploy/world-02/smoke.py https://world-02.tokenkingos.com <out-smoke> --mcp-cli`，`<out-smoke>` 是第 10 节归档里冒烟 scope 的那份（`ids.json`、凭证与 `smoke-world-02.json`）。

## 7.5 给天枢的实测示例

`examples.py` 在冒烟 scope 上按天枢的接入顺序真打一遍接口，生成 `docs/world-v02-tianshu-examples.md`（#72）。它只用标准库，同 `smoke.py --mcp-cli` 一样在本机仓库检出里经域名跑，凭证目录用第 10 节归档里冒烟 scope 的那份。先按第 7 节跑过完整冒烟，骨架就已经在了：

```bash
git clone https://github.com/TokenkingOS/tkos-secrets.git /tmp/tkos-secrets
S=/tmp/tkos-secrets/ontology-runtime/world-02/smoke U=https://world-02.tokenkingos.com
python3 deploy/world-02/examples.py run $U $S ~/tkos-world-02-examples --commit $C --doc docs/world-v02-tianshu-examples.md
rm -rf /tmp/tkos-secrets
```

- 只认 tenant 以 `-smoke` 结尾的 scope，对实验 scope 在发出任何请求之前 FAIL。凭证只从目录读，不打印，也不写进任何输出。
- 十一步依次是：列对象与按外部引用查回、天枢写 Mission 的外部引用、每周同步（来源事件与执行状态快照）、会议事件、代记门（月度计划提交与任务卡提交带 Content Pact 新块的候选内容，确认接受后读回写回）、天枢写执行计划的计划条目、Task 的建与代记指派和生命周期、Co-Agent 再加一条计划条目、议题（提出、路由与代记承接、退回形成、处置）、取上下文、典型错误；末尾撤销本次登记的委托。每步断言返回码与关键字段，失败即停并以 1 退出，停之前尽力撤销已登记的委托。议题的代记要求服务含 #71（386f7f5）的议题族。
- 外部 id 按天枢定下的写法，`system` 一律 `tianshu`：Mission 是 `mission:demo-<run>`，执行事项是 `todo:<uuid>`（每跑一次新生成的真 uuid，进展条目、计划条目的组件 id 与 Task 的外部引用共用），E&O 责任单元是能力域 `capability:05`。单元的那一对由 E&O DRI 本人在准备阶段写（天枢改单元的外部引用是 403，列在典型错误里）。
- 每跑一次新建一套对象（标题以「示例 <run>」开头）；骨架里只重写 E&O 责任单元的外部引用，同一对原样再写。骨架已在时 `smoke-world-02.json` 不变，不用推回 tkos-secrets。
- 输出：原始记录 `~/tkos-world-02-examples/examples-<run>.json`（含真实 id，不含凭证，0600，不入库）；文档写到 `--doc`，不给时写在输出目录里。文档里的 id 按指向换成占位（`<mission-1>`、`<event-3>`、`<principal:tianshu>`、`<todo-uuid>`……），显示名换成冒烟名单的角色名，运行标记写作 `<run>`；有说不出指向什么的 uuid、残留的凭证或真名，就不写文档。
- 只重新渲染、不连服务：`python3 deploy/world-02/examples.py render ~/tkos-world-02-examples/examples-<run>.json --doc docs/world-v02-tianshu-examples.md --commit $C`。

实例按第 9 或 9.5 节重建、或契约与接口改动之后，在新实例上重跑一遍，提交更新后的文档。接口没变时，差异只在时刻、运行标记与占位编号。

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

密钥与清单放进私有仓库 `TokenkingOS/tkos-secrets`（明文、LF 行尾、只能靠轮换撤销）的 `ontology-runtime/world-02/`。主机不通 GitHub，先拉回本机再推：

```bash
git clone https://github.com/TokenkingOS/tkos-secrets.git /tmp/tkos-secrets
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

## 11. 实验 scope 的名单与十月起点播种

实验 scope 只建公司域 `company` 与 E&O 域 `eo`（Agents 单元这次不参与），名单按 `spec.eo.example.json`：

| 键 | 是谁 | 角色 |
|-|-|-|
| `ceo` | CEO | company、eo：CEO |
| `eo-dri` | E&O DRI（兼 Mission Owner 与 IC） | eo：DOMAIN_DRI、OWNER、IC |
| `eo-owner` | E&O Mission Owner（兼 IC） | eo：OWNER、IC |
| `tianshu` | 天枢服务主体 | company、eo：AGENT |
| `eo-coagent` | E&O Co-Agent | eo：AGENT |
| `exec-agent` | 执行 Agent | eo：AGENT |

E&O DRI 持 OWNER，是因为三个 Mission 里有一个的 Owner 是他本人：被指派为 Mission Owner 的人须在该域持 OWNER。

第 2 步由它生成主机上的 `spec.json`（构建提交要含本节的文件），三个人的 `display_name` 在主机上改成真名。真名只出现在主机的 `spec.json`、库里的主体与事件、tkos-secrets 的归档里；仓库里的样例、计划与测试一律用占位（「CEO」「E&O DRI」「E&O Mission Owner」）。已经按旧名单供给过的实验 scope（`spec.example.json` 的，或 E&O DRI 还没有 OWNER 的）随第 9 或 9.5 节的重建换名单；不要在同一个库里删掉 `out/` 重新供给，那样旧 scope 与它的凭证还留在库里。

**播种计划** `seed-eo-2026-10.json`（17 步）只写主体与域的键，id 与显示名运行时取 `ids.json`。2026-09-29 E&O 定：本体的 Mission 以天枢里的个人任务为准（#73）。E&O 的三个个人任务分属两张任务卡，都还没在天枢里确认，十月月度计划也没签发，所以周期目标与 Mission 都建成未确认，门留给天枢按委托代记。

- 公司层照旧：Company、Strategy（草稿，责任结构块只有 E&O 一个责任单元条目组件）、E&O 责任单元（`architecture_ref` 以组件引用指到那个条目）、公司级与 E&O 长期目标，两个长期目标由 CEO 确认。正文取自 `experiments/world_v01/seed.json`（E&O 九月回放），2026-09-30 按方法侧 Content Pact 的块与组件重排（#81，映射表 `docs/world-v02-content-pact-mapping.md`）：原有正文按意思放进最合适的新组件，不另写内容，没有原文的组件留空，每步的 `note` 写明怎么放、哪些留空。同日（#67）Company 与 Strategy 的正文换成公司知识库《总体战略定位与阶段路径》（2026-07）的原句，逐条原句与页码在 `experiments/world_v02/strategy_material.json`，两步的 `note` 逐条写了页码；材料第 10 页待管理层确认的六个问题都以问句放进组件，不写成已定。组件的 id 与放法和实验 E 的主干（`experiments/world_v02/b_source-2026-10.json`）一致；表里「id：类型」的写法是组件 id 与类型不同时。原稿的 `feishu.example` 占位链接不带。

  | 对象 | 新块｛有原文的组件｝ | 留空的组件 |
  |-|-|-|
  | Company | `identity`｛`long-term-identity`：`business_definition`（第 10 页问题 1，问句）、`vision`（第 2 页长期愿景）、`token-principle`：`values_principles`（9/23 会议的「不追求 Token 用量最大化」，不在材料里，保留 0.1 原文）、`core-principle`：`values_principles`（第 2 页核心原则）｝ | `corporate_purpose` |
  | Strategy | `strategy_core`｛六条 `strategic_thesis`（含 `main-line`：第 2 页阶段打法、`path`：第 8 页阶段路径）、三条 `value_proposition`（第 3 页三层）、七条 `competitive_advantage`（含 `foundation`：第 10 页问题 3，第 4 页五个控制点各一条）、三条 `go_to_market`（第 8 页三段）、三条 `trade_offs`｝；`business_logic`｛八条 `business_model`、四条 `value_logic`、`token-traction`：`assumption`（第 7 页关键判断）、两条 `strategy_constraint`｝；`responsibility_structure`（原样，取自战场图 V1，带 `eo` 条目） | `target_customers`、`target_markets` |
  | E&O 责任单元 | `definition`｛`contribution`（支撑哪些 Mission）、`mandate`（能力域与它主导的事）、`boundary`：`scope_boundary`（原边界）、`method-frozen` 与 `no-graph-db`：两条 `key_constraint`（原约束按分号拆开）｝ | 无 |
  | 公司级与 E&O 长期目标 | `target`｛`outcome`｝ | `success_criterion`、`realization_logic`；`alignment` 整块 |
- E&O 责任单元建时就带外部引用：system `tianshu`，id `capability:05`。责任单元没有门，天枢也不是它的责任人，不能以 Agent 身份修订它（#72 实测 403），所以由播种写好；这个编号不带人名。
- E&O 十月周期目标（2026-10，`goal_ref` 指 E&O 长期目标，不带 `review_ref`）：正文沿用原草案，结果与验收标准各三条，组件 id 不变，同在目标定义块 `target`；`alignment` 整块与 `target` 的 `time_boundary`、`realization_logic` 没有原文，留空。只建，不承诺、不确认。
- 三个 Mission，`goal_ref` 指十月周期目标，内容取天枢里 E&O 的个人任务（标题、目标与验收，人名换成角色名）：目标写成战役定义块 `definition` 里的战役结果组件（id `outcome`），每条验收一个成功 / 验收标准组件（id 为 `ac-1`、`ac-2` 等），同在战役定义块；`contribution`、`time_boundary`、`scope_boundary` 与 Mission 计划块 `mission_plan` 没有原文，留空，由任务卡提交时天枢代记的候选写入。只建并指派 Owner，不承诺、不确认。

  | 键 | 标题 | Owner | 验收条件 |
  |-|-|-|-|
  | `mission_grounding` | Ontology & Data Grounding | `eo-owner` | 4 条 |
  | `mission_blueprint` | ENO / Engine Blueprint | `eo-owner` | 3 条 |
  | `mission_context` | 可信 Context / Memory 与真实 Agent 读写闭环 | `eo-dri` | 4 条 |

- 不建 Task：执行事项由天枢先写成 Mission 执行计划块里的计划条目（组件 id 为 `todo:<天枢里的 id>`，`responsible` 写某个人），E&O 再在 Mission 下建 Task，`external_refs` 带同一个 `todo:<id>`。
- 三个人各给天枢登记一条委托：门、指派、生命周期与议题（议题族是 #71 加的，含承接、处置与退回形成），域是他持角色的域（CEO 为公司域与 E&O 域，另两人为 E&O 域），有效期至 2026-10-31T23:59:59+08:00。
- Mission 的外部引用不预写：天枢的个人任务编号带人名拼音，不进仓库。由天枢第一批自己以修订写入，写法是 system `tianshu`、id `mission:<编号>`。Mission 有门，但外部引用是活动属性，天枢以 Agent 身份带写入声明就能直接修订。

换任务卡之前的原计划（33 步：三个 Mission 下 8 个 Task，周期目标与 Mission 都过门）另存为 `experiments/world_v02/b_source-2026-10.json`（正文与顺序原样，块形状 9/30 按 Content Pact 改过，#82；Company 与 Strategy 的正文同日换成战略材料原句，与本计划相同，#67），是对照实验 B 的源数据，实验 E 的主干也照它核对；实例不再用它。

**门与之后的事由天枢代记**：播种之后，天枢以自己的凭证带 `on_behalf_of`（被代记的人、天枢里的记录 id、外部确认时刻）代记：

- 月度计划签发：代 E&O DRI 承诺十月周期目标（形成锚定：E&O 长期目标已确认；scope 里还没有已确认的公司复盘，不带 `review_ref` 放行），代 CEO 确认。
- 任务卡确认：每个 Mission 代 Owner 承诺、代 E&O DRI 确认（守卫：十月周期目标已确认）。`mission_context` 的 Owner 就是 E&O DRI，承诺与确认都代他本人记。
- 之后 Task 的指派（代 Mission Owner）、开始与交付（代执行人）、打回与验收（代 Mission Owner），以及议题的承接与处置（代承接人）。议题的提出与路由由天枢以自己的身份记，不是代记。

**按人分段**：播种会让记录挂在真人名下，所以 `seed_eo.py` 每一段只用一个人的凭证（`<out>/<键>.token`，不打印），只做他名下前置已满足、还没做过的步骤；要等别人时停下，打印状态视图（每步谁做、做没做、本人执行还是代录、在等谁）与「下一步：谁 做 什么」。每一段由本人在场执行，或经本人同意代为执行（代录，见下）。五段（CEO 7 步、DRI 9 步、Owner 1 步）：

| 段 | 谁 | 做什么 |
|-|-|-|
| 1 | ceo | 建 Company、Strategy、E&O 责任单元（带 `capability:05`）、公司级长期目标 |
| 2 | eo-dri | 建 E&O 长期目标（草稿） |
| 3 | ceo | 确认公司级与 E&O 长期目标；登记委托（company、eo） |
| 4 | eo-dri | 建十月周期目标；建三个 Mission 并各自指派 Owner；登记委托（eo） |
| 5 | eo-owner | 登记委托（eo） |

计划的前置按这五段写，比 0.2 本身严。0.2 只要求：建对象时主干上一级已存在、建的人是主干上某一级的责任人；被指派为 Mission Owner 的人在 E&O 域持 OWNER；登记委托时 scope 里已有 Company。Owner 那一段挂在两个 Mission 指派给他之后。责任单元的 DRI 按角色解析，不用指派。

**命令**：主机没有仓库检出，脚本只用标准库，在本机仓库检出里经域名跑（同 `smoke.py --mcp-cli`）。`<out>` 用第 10 节归档里实验 scope 的那份（`ids.json` 与凭证）；状态文件 `seed-eo-state.json` 写在同一目录，回执、事件与对象 id 都在里面，重跑沿用，每段跑完推回 tkos-secrets（段与段隔了时间就每段重新克隆、跑完删掉）。幂等键固定为 `world-02-eo-seed:<步骤>`；提交前请求先记进状态文件，中断后重跑原样重发，已提交的拿回原回执。实验 scope 重建后，旧状态文件随第 10 节的整目录替换一起作废（脚本拒绝另一个 scope 的状态文件）。

状态文件丢了不要直接重跑，先从 tkos-secrets 取回。直接重跑时不会建出第二个对象，但会多记一条代录说明：CEO 第一段里 Company 与 Strategy 拿回原回执，E&O 责任单元在 prepare 被拒（`INVALID_STATE`：外部引用 `capability:05` 已指向那个单元），这一段停下；停下前脚本按新的状态文件为拿回的两步补记一条代录说明（本机预演实测）。

```bash
git clone https://github.com/TokenkingOS/tkos-secrets.git /tmp/tkos-secrets
O=/tmp/tkos-secrets/ontology-runtime/world-02 U=https://world-02.tokenkingos.com P=deploy/world-02/seed-eo-2026-10.json
python3 deploy/world-02/seed_eo.py $U $O $P --status                                    # 只看状态，不连服务
python3 deploy/world-02/seed_eo.py $U $O $P --as ceo --proxy-operator eo-dri --dry-run   # 先列出这一段会做什么，不写
python3 deploy/world-02/seed_eo.py $U $O $P --as ceo --proxy-operator eo-dri             # 1 CEO 段：代录，operator eo-dri
python3 deploy/world-02/seed_eo.py $U $O $P --as eo-dri                                  # 2 DRI 段：本人执行
python3 deploy/world-02/seed_eo.py $U $O $P --as ceo --proxy-operator eo-dri             # 3
python3 deploy/world-02/seed_eo.py $U $O $P --as eo-dri                                  # 4
python3 deploy/world-02/seed_eo.py $U $O $P --as eo-owner --proxy-operator eo-dri        # 5 Owner 段：代录，operator eo-dri
python3 deploy/world-02/smoke.py $U $O --probe-only
cd /tmp/tkos-secrets && git add ontology-runtime/world-02/seed-eo-state.json && git commit -m "world-02: E&O 十月起点播种状态" && git push
rm -rf /tmp/tkos-secrets
```

**代录**：`--proxy-operator <键>` 表示这一段仍用 `--as` 那个人的凭证写，实际由 operator 代为录入；只在本人同意时用。CEO 两段与 Owner 一段按上面的命令代录（operator eo-dri），DRI 两段本人执行。记录里如实写明：

- 收内容的门动作（这份计划里只有 CEO 确认两个长期目标）：`content.text` 是「E&O 十月起点播种。由<operator>代<本人>录入，待本人复核」，显示名取 `ids.json`；`--proxy-note` 换后一句的模板（可用 `{operator}`、`{person}`、`{date}`）。本人执行时只有「E&O 十月起点播种」。
- 建对象、指派、登记委托不收内容，所以一段代录跑完后，以 operator 自己的凭证补记一条外部事件（category `other`，operator 是人不带写入声明）作代录说明：主体是这一段涉及的对象（对象形式，钉到当前版本；委托的主体是 Company）；正文「以下<本人>名下的记录由<operator>于 <日期> 代为录入，待本人复核：」之后逐条列出这一段写下的事件 id 与动作名，门事件也列；`content.refs` 是这些事件的 `event:<id>` 引用；发生时刻取这一段最后一张回执的记录时刻。幂等键 `world-02-eo-seed:proxy-note:<本人键>:<步骤集合的摘要>`，重跑不重复记；一段中途失败，已写下的也补记。按上面的命令共 3 条代录说明：CEO 两段各一条，分别列 4 条与 3 条事件；Owner 一段一条，列 1 条。
- 状态视图与状态文件标出每步是本人执行还是代录、由谁代录。

真名与代录说明里的姓名只出现在主机（库里的主体与事件）与 tkos-secrets 的数据里，不进仓库、文档与聊天。

## 已核与未验证

已核（2026-09-28 本机）：用 `build_images.py` 对 49abd15 构建 amd64 与 arm64、对 7c58d9e（合入 #57）构建 arm64，镜像内 0.2 安装材料与提交源码一致、协议支持集合含 0.2，镜像包 `docker load` 可加载。本机预演（项目 `tkos-world-02-rehearsal`、端口 8051、7c58d9e 的 arm64 镜像、同一份 compose 与本目录的脚本，`spec-smoke.json` 用第 2 步那行命令生成）：第 3 步；第 4–5 步两个 scope 各两遍（第一遍各装 profile、策略、登记与三个域的激活策略、签 9 枚凭证；第二遍供给与凭证跳过、profile 报已安装）；实验 scope 的 `--probe-only` 13 项全过，对它跑完整冒烟在建任何对象之前 FAIL；冒烟 scope 的完整冒烟两遍（94 项、92 项全过），第二遍沿用骨架并带 `--mcp-cli`（CLI 三项、MCP 四项与两条事件的 HTTP 读回全过）。收尾时库里两个 scope，实验 scope 的对象、事件、回执都是 0，冒烟 scope 18 个对象、62 条事件、62 张回执。预演栈已 `down -v`。

主机（2026-09-29，会话代跑）：先按 88d765e 走完第 1–8 步，#63 合入后按第 9.5 节换到 c003b03 重建，再按第 10 节归档（tkos-secrets 998d0d0）。c003b03 上：两个 scope 各签 9 枚凭证（90 天）；实验 scope 的 `--probe-only` 在本机口与域名都过；冒烟 scope 的完整冒烟本机口 94 项、域名 83 项全过（第二遍沿用骨架）；本机仓库检出对域名跑 `--mcp-cli` 92 项全过（CLI 三项、MCP 四项，工具清单十个）；库里有 `ix_gov_world_external_refs`，列对象经域名返回 200、未知参数 422。证书 webroot、ecdsa，到期 2026-12-27，续期演练成功。world-lab 全程 200。

2026-09-29 P2 全部合入后按第 9.5 节换到 f7afcb2 重建（#65 的 8f4bdc1 只加验收代码，镜像不变）：冒烟本机口 94 项、域名 83 项、本机 `--mcp-cli` 92 项全过（MCP 工具清单十三个），归档见第 10 节。

第 11 节的本机预演（2026-09-29，#69 合入之后）：`build_images.py` 对 15f6ff8（a66f9c9 加上第 11 节的名单与播种）构建 arm64，项目 `tkos-world-02-rehearsal`、端口 8051，迁移到重写后的 0039。实验 scope 按 `spec.eo.example.json` 供给、签 6 枚凭证；九段按 ceo → eo-dri → ceo → eo-dri → ceo → eo-dri → eo-owner → eo-dri → eo-owner 跑完（CEO 与 Owner 各段代录、operator eo-dri，DRI 本人），33 步全部提交，每段的「下一步」都对，最后「全部完成」；代录说明 5 条（CEO 4、2、2 条，Owner 3、9 条）。读回：两个长期目标与十月周期目标已确认，三个 Mission 已成立、Owner 是 eo-owner，8 个 Task 按 3、3、2 挂在各自的 Mission 下、未指派，Strategy 仍是草稿，三条委托在 E&O 域生效（CEO 那条另覆盖公司域）；33 条事件的记录者都是本人的主体，代录的门事件带代录句；每条代录说明由 eo-dri 记，refs 与主体正好覆盖那一段的事件与对象，不是迟记。九段整体重跑一步不做、不补记，库里仍是 17 个对象、38 条事件、38 张回执；删掉状态文件再跑前三段，建对象与代录说明拿回原回执，第一个门步骤在 prepare 被拒（INVALID_STATE），库里没多一条。之后 `smoke.py --probe-only` 对实验 scope 10 项全过（6 个主体），对它跑完整冒烟在读凭证之前 FAIL；另供给冒烟 scope 跑完整冒烟两遍都过，实验 scope 的计数不变。预演栈已 `down -v`，含密钥的目录已删。

2026-09-29 #69 与第 11 节合入后按第 9.5 节换到 c29e7d5 重建，实验 scope 改按真名名单（6 个主体、公司域与 E&O 域）供给：实验 scope 的 `--probe-only` 本机口与域名都过；冒烟 scope 完整冒烟本机口 94 项、域名 83 项，本机 `--mcp-cli` 92 项全过。随后在本机经域名按九段跑完十月起点播种：33 步全部提交，其中代录 20 步，代录说明 5 条；再跑一段不做任何事。用天枢凭证读回：17 个对象，两个长期目标与十月周期目标已确认，三个 Mission 已成立，8 个 Task 未指派，`period=2026-10` 列出 12 个，周期目标的 `identity.delegations` 有 3 条。归档见第 10 节（含 `seed-eo-state.json`）。

2026-09-29 #66–#68 与 #70 合入后，按第 9.5 节换到 55c46d3 重建（#70 改了契约，默认预算 10 万字符，镜像里契约的 sha256 为 fb260b28…）。同时删掉了对照实验 B 冒烟用的两个输出目录 `out-b-*`。重建后：

- 实验 scope 按真名名单重新供给。本机口与域名的 `--probe-only` 都通过，域名 10 项。
- 冒烟 scope 的完整冒烟：本机口通过，域名 83 项通过；本机 `--mcp-cli` 92 项通过。
- 十月起点经用户同意按九段重播：33 步全部提交，其中代录 20 步，代录说明 5 条；各段重跑都不再做事。
- 读回 17 个对象：8 个 Task 未指派，3 个 Mission 已成立。
- 归档：tkos-secrets 7bb3023，仓库已迁到 TokenkingOS/tkos-secrets，旧地址会重定向。
- 旧的 c29e7d5 镜像与发布目录已删。world-lab 全程 200。

2026-09-29 晚，#71、#72、#73 合入后按第 9.5 节换到 24e6202 重建（#71 改了契约与登记，镜像里契约 sha256 9e6d1c6c…、登记 2fc36f4f…）。主机的 `spec.json` 给 E&O DRI 加了 OWNER。重建后：

- 实验 scope 与冒烟 scope 重新供给；实验 scope 的 `--probe-only` 本机口与域名都过（域名 10 项）；冒烟 scope 完整冒烟本机口过、域名 83 项；本机 `--mcp-cli` 92 项全过。
- 十月起点按新计划五段播完（用户同意第三次代录）：17 步，代录 8 步，代录说明 3 条。读回 9 个对象：两个长期目标已确认；周期目标与三个 Mission 是草稿，Owner 分别是 Mission Owner、Mission Owner、E&O DRI；单元带 `capability:05`，按外部引用能查回；三条委托各含四族。
- `examples.py` 在冒烟 scope 上经域名跑完，准备到第 11 步与收尾全部通过（127 项），生成 `docs/world-v02-tianshu-examples.md`。
- 密钥整目录替换，见 tkos-secrets 29002c5；旧的 55c46d3 镜像与发布目录已删。

主机上踩到的坑：经 `ssh … 'bash -s' <<EOS` 远程跑脚本时，`docker compose exec -T` 同样会吃掉 stdin，把后面的脚本当输入读走，要接 `</dev/null`（`run -T` 已在脚本里这样做）；但别给管道的下游加：`gunzip -c … | docker load </dev/null` 会让 `docker load` 读到空输入、什么都不加载，之后 compose 转去 Docker Hub 拉镜像超时。远程脚本里先解到文件再 `docker load -i`。本机对域名跑冒烟时遇到过一次连接超时，重跑即过。

2026-09-30 分支 `world/0.2-pact`（#81，按 Content Pact 替换块与组件）在本机隔离验收栈上预演，实例没有重建：`acceptance/method_v05/database.py` 新建一个库并升级到本分支源码（含 #79 重钉的 0039），经 owner SQL 与控制面 CLI 供给实验 scope（`spec.eo.example.json`）与冒烟 scope（`spec.example.json`，tenant 以 `-smoke` 结尾），起真 API。十月起点按第 11 节五段播完（CEO 两段与 Owner 一段代录、operator eo-dri）：17 步全部提交，代录 8 步，代录说明 3 条，再跑一段不做事；读回 9 个对象的块与组件与计划一致，两个长期目标已确认，周期目标与三个 Mission 是草稿，Owner 照计划，单元带 `capability:05`。实验 scope 的 `--probe-only` 10 项全过；冒烟 scope 完整冒烟 94 项、第二遍带 `--mcp-cli` 92 项全过；`examples.py` 准备到第 11 步与收尾 129 项全过，重生成 `docs/world-v02-tianshu-examples.md`。

未验证：并发写下的外部引用唯一性；大数据量下列对象的性能；天枢凭这三条委托实际代记；Content Pact 新块在主机实例上的播种与冒烟（要等 10 月 8 日定下切换、按第 9.5 节重建）。
