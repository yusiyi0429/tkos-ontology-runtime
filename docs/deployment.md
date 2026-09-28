# 远程部署边界

Runtime 可以通过 HTTP 供远程 Clark/其他应用调用。当前已完成的是本地独立 Runtime 验收；本仓库尚未交付经过目标服务器验收的完整部署包。

## 最小试点拓扑

应用后端 → Runtime API → PostgreSQL/pgvector；Runtime API 另访问版本化 S3/MinIO，Worker 消费 PostgreSQL 任务并调用固定外部接收器。API 与 Worker 使用同一代码版本。首先部署一个业务域，不要求新增图数据库、向量服务或 LLM 平台。

Dockerfile 已提供两个构建目标。构建有两个前提：

- 一个 wheelhouse 目录，里面有本版本的 `tkos-memory-service` wheel 与 `requirements.lock` 的全部依赖；
- 显式传入与那个 wheel 相同的 `VERSION`。

官方离线包的做法见 `deploy/offline-release/`：先用 `prepare_wheelhouse.py` 准备 wheelhouse，再用 `prepare_images.py` 构建。`prepare_images.py` 只接受与 `deploy/offline-release/images.json` 一致的三段式发布号（现为 v0.5.0），它是切正式发布用的。

开发版本（如 `0.6.0.dev0`）不走 `prepare_images.py`：用 `prepare_wheelhouse.py --release v<版本>` 准备 wheelhouse，再按下面的命令手工构建，`acceptance/delivery_candidate/` 就是这样做的。正式发布时先把版本改成三段式、更新 `images.json` 的发布号，再走官方流程。

```bash
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
docker buildx build --load --build-context wheelhouse=/path/to/wheelhouse \
  --build-arg VERSION="$VERSION" --target runtime -t tkos-ontology-runtime:local .
docker buildx build --load --build-context wheelhouse=/path/to/wheelhouse \
  --build-arg VERSION="$VERSION" --target worker -t tkos-ontology-worker:local .
```

镜像里的 `/opt/tkos/docs/` 带着本版本支持的全部协议安装材料：Method 0.1–0.5 与 world 0.1 的 profile、它们钉定的契约字节、本体与 world 登记，以及支持登记。所以用 `tkos-governed-control install-profile`/`set-registry` 装任一协议，都不需要仓库检出。

这些是构建命令，不是已发布的镜像或已验证的远程部署。首次目标环境需要单独验证其 CPU 架构、镜像构建及运行。

## 远程试点落地条件

1. 建立独立数据目录/卷及部署项目名。PostgreSQL 和 S3 数据端口仅在所需私网内开放；应用通过受控入口调用 API。
2. 单独设置 migration owner 与受限 application role。API/Worker 不得使用表 owner、superuser 或 BYPASSRLS 身份。迁移之外还要初始化域、主体、角色、策略和凭据；验收 fixture 不是生产身份管理工具。
3. 初始化版本化证据 bucket 和带 Object Lock 的快照 bucket，配置最小权限的应用 S3 身份。`.env.example` 只是配置项参考，不能原样使用其中的占位值。
4. 为 `GOVERNED_EFFECT_URL` 配置可信固定接收器，并配置 `GOVERNED_EFFECT_TOKEN`（或 `GOVERNED_EFFECT_TOKEN_FILE`）。
   - **认证**：派发时带 `Authorization: Bearer <凭证>`，接收器按常量时间比对；返回 401/403 记为认证失败，不重试。
   - **本机以外的接收器**：必须用 https；没配凭证时不派发，任务按配置错误失败（`governance_effect_credential_missing`）。
   - **参考接收器**：`acceptance/runtime/receiver.py --token-file` 演示接收端的做法；令牌文件每行一个，任一相符即通过。
   - **去重**：接收器还必须持久去重 `Idempotency-Key`，或另外完成对账与补偿设计。
   - **凭证轮换**：交替进行。先让接收器同时接受新旧两个凭证，再把派发端换成新凭证，确认派发成功后从接收器删掉旧凭证。
     两端同时换会留下空档：空档里的派发收到 401，按认证失败直接进入失败终态，而旧效果路径的失败终态没有恢复动作（Codex 评审 R06，#32 未纳入）。
5. 配置启动顺序、健康检查、日志、备份及同版本恢复，并在目标机器复跑业务闭环、权限拒绝、重试与重启持久化验收。

独立验收中的备份恢复是同一个 PostgreSQL 实例内的新数据库加独立 MinIO 卷；它没有证明整机丢失后的灾难恢复。长任务续租、容量与高可用也需另行验证。

## Clark 对接

Clark 由伙伴团队维护。本仓库保留原有只读兼容接口，当前 Native 契约以 `contracts/openapi.json` 为准。应用端新接口或写操作应先逐项对齐契约，再完成联合验收。不能仅修改 URL 就推定新版应用已兼容。

`deploy/legacy-memory/compose.yaml` 仅供追溯历史 Memory 部署，不包含本期治理 Runtime 的完整权限、身份、对象存储及接收端初始化。
