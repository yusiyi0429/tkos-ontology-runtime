# 看板本机部署与升级准备（阶段说明）

本文件只准备**本机五容器**环境（Clark、Runtime API、Runtime Worker、PostgreSQL、
MinIO）的构建与升级步骤。计划由监督方在评审后执行；本阶段未替换容器、未升级数据库、
未改动 Clark 端口/配置或数据卷。实际执行以当前 `deploy/offline-release/` 脚本的
`--help` 为准。

## 1. 构建可发布产物

```sh
# 前端 + 编译资源 + 构建清单（build_dashboard.sh 只接受 npm ci，不使用 npm install）
./scripts/build_dashboard.sh

# Python 包：sdist 含 workbench 源，wheel 内含 dashboard_dist 与 asset-manifest.json；
# Hatch build hook 会在打包前再次校验清单，前端源码改动未重新构建会直接失败。
uv build

# 归档逐文件 hash 校验（不是只看文件存在）
python3 scripts/verify_dashboard_assets.py \
  --archive dist/tkos_memory_service-<version>-py3-none-any.whl \
  --archive dist/tkos_memory_service-<version>.tar.gz
```

构建清单 `src/memory_service_app/dashboard_dist/asset-manifest.json` 记录全部前端
输入（`workbench/dashboard` 源码/配置/lock）与全部产出资源的 SHA256；Dockerfile 在
`pip install` 后按同一清单逐文件校验，缺失或 hash 不符即镜像构建失败。

## 2. 离线 wheelhouse 与镜像（现有脚本）

`prepare_wheelhouse.py` 会锁定依赖、下载目标架构 wheels，并在最后自行 `uv build`
项目 wheel（无需手工复制 `dist/*.whl`）。它要求工作树相对 `<source-ref>` 在
`pyproject.toml`、`README.md`、`src` 上没有未提交差异。

```sh
uv run python deploy/offline-release/prepare_wheelhouse.py \
  --arch amd64 --release v<version> --source-ref <40-hex commit> \
  --output <fresh wheelhouse 目录>

uv run python deploy/offline-release/prepare_images.py \
  --release v<version> --arch amd64 --source-ref <40-hex commit> \
  --source-dir . --wheelhouse <wheelhouse 目录> \
  --output <fresh images.json>

uv run python deploy/offline-release/build_bundle.py \
  --release v<version> --arch amd64 --source-ref <40-hex commit> \
  --image-set <images.json> --output-dir <fresh bundle 目录>
```

`prepare_images.py` 用 Dockerfile 的 `runtime` / `worker` target 构建 API 与 Worker
镜像（`--build-context wheelhouse=<dir>`），并校验镜像 label 与源码 ref；`images.json`
的 release/plan 需与本次发布一致。Dockerfile 现在同时包含 0.1/0.2/0.3 的 profile、
冻结契约与 registry 文件。

## 3. viewer 凭据（UID 10001 可读，仍保持私有）

运行镜像以 UID 10001 运行，宿主机 0600 的文件默认不可读。使用**命名卷 + 一次性
初始化**（不新增常驻服务，仍是五个容器）：

```sh
docker volume create tkos-clark-linked-20260914-dashboard-viewer

# token 只经 stdin 写入，不出现在命令行/环境/镜像层
printf '%s' "<current synthetic viewer bearer token>" | \
  docker run --rm -i -v tkos-clark-linked-20260914-dashboard-viewer:/secret alpine:3 sh -c \
  'umask 077; cat > /secret/viewer-token; chown 10001:10001 /secret/viewer-token; chmod 400 /secret/viewer-token'
```

在既有 compose 的 API 服务上追加（示例 override，保持端口与网络不变）：

```yaml
services:
  runtime-api:
    volumes:
      - dashboard-viewer:/run/tkos-dashboard:ro
    environment:
      TKOS_DASHBOARD_ENABLED: "1"
      TKOS_DASHBOARD_VIEWER_TOKEN_FILE: /run/tkos-dashboard/viewer-token
      TKOS_DASHBOARD_ALLOWED_HOSTS: 127.0.0.1:58802,localhost:58802
volumes:
  dashboard-viewer:
    external: true
    name: tkos-clark-linked-20260914-dashboard-viewer
```

facade 每个请求重新读取该文件并重新认证；文件缺失、mode 含 group/other 位或 token
无效时返回 503/401，不会回退到更高权限身份。浏览器端不接触 token。

## 4. 升级前 clone 验证（不碰运行库）

1. 用 `acceptance/dashboard_0_3/database.py` 创建新隔离库并迁移至 0025；重复迁移必须
   为 0，应用/迁移角色与授权符合预期。
2. 对 clone 运行 `acceptance/dashboard_0_1/run.py`（只读，含前后表计数 oracle），并
   复核既有对象、默认绑定与旧读兼容（`/v1/identity`、Strategy、LTCO、PCO、Mission、
   ReviewWindow）。
3. 迁移只允许新增 0023–0025 的表/索引/键表与必要的 SELECT/INSERT 授权；既有业务行
   不得被 UPDATE/DELETE 或改写，用前后行计数与内容 hash 证明（排除持续心跳类运行表）。
4. 记录源码 commit、wheel/sdist SHA256 与镜像 digest；保留旧镜像 tag 以便回滚。

## 5. 切换顺序（监督方执行）

1. 先停 Worker，再停 API（停止新写入；不删除容器与数据卷）。
2. 备份运行库（保留原卷，不覆盖旧备份）。
3. 由迁移 owner 身份单独应用 0023–0025；验证重复迁移为空、授权符合预期。
4. 用新镜像替换 API/Worker；保持 PostgreSQL/MinIO 数据卷、网络、Clark 端口与配置、
   旧对象与默认绑定不变；启动后校验 `/v1/health`、`/v1/identity` 与既有对象读取。
5. 确认私有 viewer 卷就绪后启用 `TKOS_DASHBOARD_ENABLED=1`，检查 `/dashboard/` 与
   `/dashboard/api/v1/overview`；`/v1` 认证行为不变。

## 6. 回滚

- 旧镜像与旧 tag 保留：回滚即把 API/Worker 指回旧镜像并重启，数据卷不重建。
- 迁移为 append-only：回滚旧镜像读取无需、也不允许自动做破坏性快照恢复；若必须回退
  迁移，先在隔离库用第 4 步备份演练并人工确认。
- 关闭 `TKOS_DASHBOARD_ENABLED` 或移除 viewer 卷即可停用看板，`/v1` 不受影响。

## 7. 边界

- 不改 Clark 镜像、访问码、数据卷或真实模型配置；不新增常驻容器。
- 不把 token 写入镜像、HTML/JS、日志或公开文档。
- 浏览器人工验收、业务同事理解度与真实模型收拢由监督方另行执行。
