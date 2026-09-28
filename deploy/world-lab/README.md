# world-lab：给天枢联调的独立实例

在火山主机上，在 Clark 用的生产实例（`memory-api.tokenkingos.com` → 127.0.0.1:8030，版本 0.3.0，没有 world 路由）之外，再起一套独立的 compose 项目跑正式发布版（v0.5.0，含 tkos.world/0.1、迁移到 0038），对外走宿主机 Nginx 的 `world-lab.tokenkingos.com`。它有自己的 PostgreSQL 与 MinIO 卷，与生产实例互不相干；里面的 scope 是联调数据，可整体清空重建。

复用 `deploy/offline-release/` 的 compose 与启动脚本，只换 env、端口、项目名与镜像标签。主机是 `tokenhub-prod`（115.191.26.230，x86_64，Ubuntu 22.04，部署用户 tokenhub-deploy）。主机不通 GitHub 与 Docker Hub（PyPI 通、daocloud 镜像通），所以离线包从本机传、密钥归档从本机推。版本号只用发布号（如 `v0.5.0`）：镜像标签、发布目录、离线包文件名都从它派生，不另起名字。目录沿用主机上生产实例的约定：应用目录 `/srv/tokenhub/apps/tkos-world-lab`，源码放 `releases/<发布号>/`，env 与输出放 `/srv/tokenhub/apps/tkos-world-lab/world-lab/`。下文 `REL=v0.5.0`、`SRC=/srv/tokenhub/apps/tkos-world-lab/releases/$REL`、`LAB=/srv/tokenhub/apps/tkos-world-lab/world-lab`。

本目录的文件：`world-lab.env.example`（env 模板）、`spec.example.json`（联调 scope 的域、主体与角色）、`provision.py`（建 scope、域、主体、角色并生成策略文件）、`provision-and-install.sh`（第 4–5 步整段，可重跑）、`smoke.py`（第 7 步冒烟，可重跑）、`nginx/world-lab.conf`。

## 0. 前提

- 主机 Docker 29.1、compose 2.40，宿主机 Nginx 在用，certbot 已装，`memory-api.conf` 用的是 Let's Encrypt 证书（已核）。
- DNS：`tokenkingos.com` 托管在火山 DNS（ns1/ns2.volcengine-dns.com），在火山 DNS 控制台给 `world-lab` 加一条 A 记录指向 115.191.26.230；memory-api 就是这样解析的。第 6 步之前必须生效。
- 端口：world-lab 用 `127.0.0.1:8040`（已核空闲；8030 是生产，8080、8081 有别的服务，3130 空闲留给天枢的 edge）。
- 已占的域名：`mvp.tokenkingos.com` 在这台主机上已指向 127.0.0.1:3120，天枢定域名时避开。

## 1. 取离线包并加载

用 GitHub Release 的 amd64 离线包（主机是 linux/amd64）。包里有五个镜像（`tkos/ontology-runtime`、`tkos/ontology-worker`、`tkos/offline-postgres-pgvector`、`tkos/offline-minio`、`tkos/offline-minio-mc`，标签都是 `$REL-amd64`）与该版本的完整源码 `runtime-source.tar.gz`，本目录的脚本就在源码里。主机不通 GitHub，在本机下载后传过去（包约 330 MB，连接可能中途断，用 `rsync --partial` 可续传）：

```bash
REL=v0.5.0; PKG=tkos-ontology-runtime-$REL-linux-amd64
gh release download $REL -R yusiyi0429/tkos-ontology-runtime -p "$PKG.tar.gz" -p SHA256SUMS -D /tmp/$REL
(cd /tmp/$REL && grep " $PKG.tar.gz\$" SHA256SUMS | shasum -a 256 -c -)
ssh tokenhub-prod "mkdir -p /srv/tokenhub/apps/tkos-world-lab/releases/$REL /srv/tokenhub/apps/tkos-world-lab/world-lab"
rsync -a --partial --timeout=60 /tmp/$REL/$PKG.tar.gz tokenhub-prod:/srv/tokenhub/apps/tkos-world-lab/world-lab/
```

主机上：

```bash
cd $LAB && tar -xzf $PKG.tar.gz && cd $PKG
python3 deploy/offline-release/verify_bundle.py . --load      # 校验包内哈希、架构与五个镜像，加载进本机镜像库
tar -xzf runtime-source.tar.gz -C $SRC                         # 源码（含 deploy/offline-release 与本目录）
docker images | grep "$REL-amd64"                              # 应有五个镜像
```

`world-lab.env.example` 里的五个镜像标签就是 `v0.5.0-amd64`；换版本时连同 `REL` 一起改。

## 2. 配置

```bash
cp $SRC/deploy/world-lab/world-lab.env.example $LAB/.env && chmod 600 $LAB/.env
python3 - $LAB/.env <<'PY'
import re, secrets, sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = s.replace("CHANGE_ME_MINIO_ROOT_USER", secrets.token_hex(8))
p.write_text(re.sub(r"CHANGE_ME[A-Z_]*", lambda m: secrets.token_hex(24), s))
PY
grep -c CHANGE_ME $LAB/.env          # 应为 0
cd $SRC/deploy/offline-release && mkdir -m 700 -p private
openssl rand -hex 16 > private/minio-app-access-key
openssl rand -hex 32 > private/minio-app-secret-key
sudo chown 10001:0 private/minio-app-*          # 见下文：不能停在 600
sudo chmod 440 private/minio-app-*
cp $SRC/deploy/world-lab/spec.example.json $LAB/spec.json      # 要改域或主体就改这份
```

两个 MinIO 应用密钥文件由 compose 的文件型 secret 原样挂进容器，保留主机上的属主与权限。读它们的有两类进程：`minio-init` 以 root 运行但 `cap_drop: [ALL]`（没有 `CAP_DAC_OVERRIDE`，只能按普通的 uid 0 / gid 0 判权限），API 与 Worker 以 uid 10001 运行。所以文件要设成属主 10001、属组 0、权限 440；照 `deploy/offline-release/README.md` 停在部署用户的 600，`minio-init` 会报 `cat: /run/secrets/minio-app-access-key: Permission denied`（2026-09-28 主机实测；本机 Docker Desktop 不体现属主语义，预演没暴露）。生产实例的 `deploy/remote-production/configure.py` 同样把这两个文件交给 10001，只是它的 `minio-init` 从环境变量取密钥、不读文件。改成 440 之后部署用户自己读不了这两个文件，归档时用 `sudo cat`（见第 10 节）。

每个 `CHANGE_ME` 换成各自不同的随机十六进制串（只有字母数字，避免 `.env` 解析歧义）。`private/` 必须放在 `deploy/offline-release/` 下，`start-offline.sh` 只认这个位置；主机上若别的实例也用这个目录，先确认没有冲突。

## 3. 启动

```bash
cd $SRC/deploy/offline-release
./start-offline.sh $LAB/.env
```

脚本依次：起 PostgreSQL 与 MinIO、建桶与应用账号、建 owner 与 app 双角色、跑迁移（到 0038）、收紧授权、起 API 与 Worker，最后 `curl 127.0.0.1:8040/v1/health`。重复执行是幂等的。之后所有 compose 命令都带 `--env-file $LAB/.env`。

## 4–5. 建联调 scope、装 world 0.1、签发凭证

没有控制面命令建 scope、域、主体与角色，用本目录的 `provision.py` 以 owner 身份写库（与验收夹具、实验播种同一做法；这是联调环境，不是生产身份管理）。这一段与安装、签发一起放在 `provision-and-install.sh`：

```bash
mkdir -p $LAB/out && sudo chown 10001 $LAB/out      # 一次性容器以 uid 10001 运行（Dockerfile 建的 memory-service），要能写这个目录
SRC=$SRC LAB=$LAB bash $SRC/deploy/world-lab/provision-and-install.sh
sudo chown -R "$USER:$USER" $LAB/out                 # 收回给部署用户，后面读凭证、归档都不用 sudo
ls -l $LAB/out
```

脚本做的事：

1. `provision.py`（在 `migrate` 服务的容器里跑，`DATABASE_URL` 就是 owner 连接串）按 `$LAB/spec.json` 建 scope、域、主体与角色指派，输出到 `out/`：`ids.json`（scope、域、主体的 id，交给天枢的清单就是它）、`scope-policy.json`、每个域一份 `activation-<域id>.json`（门动作按登记的门表，其余动作对全部角色开放，和验收一致）。`out/ids.json` 已存在就跳过，否则重跑会建出第二个 scope。
2. 用镜像里的 `tkos-governed-control`（安装材料在镜像的 `/opt/tkos/docs/`，不需要仓库检出；owner 连接串给 `MIGRATION_DATABASE_URL`）依次 `install-profile`（world 0.1 的 profile、契约、登记三份材料一起校验）、`install-policy`、`set-registry`、每个域一次 `install-activation-policy`，最后 `status`。重跑时 profile 报 `already_installed`，策略与登记各加一条同内容的新序号，无害。
3. 每个主体签一枚凭证，有效期 90 天，写进 `out/<主体键>.token`（0600，不进日志）；已有的文件跳过。到期前用 `rotate-credential --grace-minutes` 轮换，吊销用 `revoke-credential`。

天枢服务主体的那枚（`tianshu-coagent.token`）交给天枢；人的凭证给本人。一次性容器都以 `run -T … </dev/null` 起：不接 `/dev/null` 的话 `docker compose run` 会吃掉管道里的 stdin，签发循环只走一轮。

## 6. Nginx

DNS 生效后：先放一个只有 80 的临时 server，让 certbot 用 webroot 方式校验（与 memory-api 的证书同法：`/var/www/certbot`、ecdsa，续期也走 webroot，不动 Nginx 配置）；签到证书再换成正式配置。正式配置引用证书文件，证书没签之前 `nginx -t` 过不了，所以顺序不能反；正式配置的 80 server 保留 acme-challenge 路径，续期要用：

```bash
sudo tee /etc/nginx/sites-available/world-lab.conf >/dev/null <<'NG'
server {
    listen 80;
    server_name world-lab.tokenkingos.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 404; }
}
NG
sudo ln -sf /etc/nginx/sites-available/world-lab.conf /etc/nginx/sites-enabled/world-lab.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d world-lab.tokenkingos.com --key-type ecdsa --non-interactive
sudo cp $SRC/deploy/world-lab/nginx/world-lab.conf /etc/nginx/sites-available/world-lab.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot renew --cert-name world-lab.tokenkingos.com --dry-run      # 续期演练
```

`nginx -t` 不过就别 reload：删掉 `sites-enabled/world-lab.conf`（或换回临时配置）再查，同机还有生产与别的站点。

## 7. 验证

`smoke.py` 只从 `out/` 读凭证，不打印。它查：健康、`/openapi.json` 里的五条 world 路由、无凭证 401、天枢凭证读不存在的对象 404、CEO 凭证经 `prepare` 加 `actions` 建 Company 并提交、天枢凭证读回对象、事件与状态。Nginx 之前对本机口跑一次，之后对域名再跑一次：

```bash
python3 $SRC/deploy/world-lab/smoke.py http://127.0.0.1:8040 $LAB/out
python3 $SRC/deploy/world-lab/smoke.py https://world-lab.tokenkingos.com $LAB/out
```

一个 scope 只有一个 Company，冒烟建的就是这个 scope 的 Company（标题「词元云集（TokenKing）」，块里注明待补），之后由 CEO 用 `world_revise_object` 补内容；建好的 id 记在 `out/smoke-company.json`，重跑时沿用。内核的 `GET /v1/objects?domain_id=` 对 world 对象不可用（不带类型 404、带 `object_type=Company` 422），0.1 没有按域或类型列 world 对象的接口，对象 id 靠回执与 `ids.json`。

## 8. 交给天枢

- 地址 `https://world-lab.tokenkingos.com`，`/openapi.json` 可访问。
- `out/ids.json` 里的域 id、主体 id 与角色（对接说明 9.3 第 1、2 条要的清单）。
- `tianshu-coagent.token`（走安全渠道，不进文档、不进聊天）。
- 十月的周期目标与 Mission 由人经 HTTP 或工作台录入后，把对象 id 追加给他们。

## 8.5 换版本（已在跑的实例）

数据卷、scope、凭证与清单都不动，只换镜像。按第 1 节取新包、校验并加载、把源码解到新的 `releases/$REL/`，然后：

```bash
OLD=/srv/tokenhub/apps/tkos-world-lab/releases/<旧发布号>
cp -p $LAB/.env $LAB/.env.bak-$(date +%Y%m%d)
sed -i -E "s#^((RUNTIME_API|RUNTIME_WORKER|POSTGRES|MINIO|MINIO_MC)_IMAGE=tkos/[a-z-]+):.*#\1:$REL-amd64#" $LAB/.env
sudo mkdir -m 700 -p $SRC/deploy/offline-release/private
sudo cp -a $OLD/deploy/offline-release/private/minio-app-* $SRC/deploy/offline-release/private/   # 带属主 10001:0 与 440
cd $SRC/deploy/offline-release && ./start-offline.sh $LAB/.env                                   # 迁移只补新的；容器按新镜像重建
python3 $SRC/deploy/world-lab/smoke.py https://world-lab.tokenkingos.com $LAB/out
```

Nginx 配置不随版本变时不用动（`cmp` 一下 `$SRC/deploy/world-lab/nginx/world-lab.conf`）。`.env` 改了镜像标签，归档里的 `compose.env` 也要同步。回滚：换回备份的 `.env`，用旧发布目录的 `start-offline.sh` 重跑（旧镜像留在本机镜像库里）。

## 9. 清空重建

联调数据要清时，停掉并删卷，删掉 `out/`，从第 3 步重来（只对 world-lab 这个项目做，生产实例不受影响）：

```bash
cd $SRC/deploy/offline-release && docker compose --env-file $LAB/.env -f compose.yaml down -v
sudo rm -rf $LAB/out
```

## 10. 归档到 tkos-secrets

联调实例的密钥与清单统一放进私有仓库 `VanillaCoca/tkos-secrets`（明文、LF 行尾、只能靠轮换撤销）的 `ontology-runtime/world-lab/`。主机不通 GitHub，所以先把文件拉回本机再推：

```bash
git clone https://github.com/VanillaCoca/tkos-secrets.git /tmp/tkos-secrets
D=/tmp/tkos-secrets/ontology-runtime/world-lab; mkdir -p "$D"
scp tokenhub-prod:$LAB/.env "$D/compose.env"
for k in minio-app-access-key minio-app-secret-key; do ssh tokenhub-prod "sudo cat $SRC/deploy/offline-release/private/$k" > "$D/$k"; done   # 这两个文件属主是 10001，部署用户要 sudo
scp "tokenhub-prod:$LAB/spec.json" "tokenhub-prod:$LAB/out/ids.json" "tokenhub-prod:$LAB/out/*.token" "$D/"
cd /tmp/tkos-secrets && git add -A && git commit -m "world-lab: 联调实例的 compose.env、MinIO 应用密钥、scope 清单与各主体凭证" && git push
```

天枢只拿 `tianshu-coagent.token` 与 `ids.json`，走安全渠道单独给，不给整个目录。仓库里已有 `ontology-runtime/world-lab/README.md` 说明每个文件是什么。

## 已核与未验证

已核（2026-09-28 主机只读检查）：主机架构与工具、8040 与 3130 空闲、离线基础镜像标签、certbot 与 Let's Encrypt、生产实例的目录约定、主机不通 GitHub 与 Docker Hub。

已核（2026-09-28 本机预演）：用同一份 compose、同标签的三个基础镜像、本文的 env 模板与两个脚本，在 amd64 仿真下跑通第 3 步、第 4–5 步（连跑两遍，第二遍全部跳过或报已安装）与第 7 步冒烟（对 127.0.0.1 端口）。

已核（2026-09-28 主机部署，先用本机按提交 a759c64 自建的两个镜像与 v0.3.0 离线包的三个基础镜像起栈；同日 16:50 按第 1 节与 8.5 换成 v0.5.0 离线包：包摘要与 Release 一致、`verify_bundle.py --load` 通过、迁移应用 0 个、数据卷沿用、对域名冒烟全过，容器镜像为 `v0.5.0-amd64`、版本 0.5.0、源码提交 72897d2）：第 1–5 步与第 7 步本机口冒烟（10 项全过）；uid 10001 写 `out/` 与 chown 回收正常；MinIO 应用密钥文件必须 10001:0、440（见第 2 步）。scp 传 76 MB 镜像包途中断过一次，改用 `rsync --partial` 续传。

已核（2026-09-28 主机，第 6–7 步）：证书以 webroot 签发（Let's Encrypt，ecdsa，到期 2026-12-27），`certbot renew --dry-run` 成功；HTTP 301 到 HTTPS；对域名冒烟全过；从外网看 `/v1/health` 200、`/openapi.json` 含五条 world 路由；同机 memory-api 仍 200。
