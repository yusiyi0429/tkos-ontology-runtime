# 远程部署边界

Runtime 可以通过 HTTP 供远程 Clark/其他应用调用。当前已完成的是本地独立 Runtime 验收；本仓库尚未交付经过目标服务器验收的完整部署包。

## 最小试点拓扑

应用后端 → Runtime API → PostgreSQL/pgvector；Runtime API 另访问版本化 S3/MinIO，Worker 消费 PostgreSQL 任务并调用固定外部接收器。API 与 Worker 使用同一代码版本。首先部署一个业务域，不要求新增图数据库、向量服务或 LLM 平台。

Dockerfile 已提供两个构建目标：

```bash
docker build --target runtime -t tkos-ontology-runtime:local .
docker build --target worker -t tkos-ontology-worker:local .
```

这些是构建命令，不是已发布的镜像或已验证的远程部署。首次目标环境需要单独验证其 CPU 架构、镜像构建及运行。

## 远程试点落地条件

1. 建立独立数据目录/卷及部署项目名。PostgreSQL 和 S3 数据端口仅在所需私网内开放；应用通过受控入口调用 API。
2. 单独设置 migration owner 与受限 application role。API/Worker 不得使用表 owner、superuser 或 BYPASSRLS 身份。迁移之外还要初始化域、主体、角色、策略和凭据；验收 fixture 不是生产身份管理工具。
3. 初始化版本化证据 bucket 和带 Object Lock 的快照 bucket，配置最小权限的应用 S3 身份。`.env.example` 只是配置项参考，不能原样使用其中的占位值。
4. 为 `GOVERNED_EFFECT_URL` 配置可信固定接收器。当前 HTTP handler 验证匹配的 `effect_key`，但未提供完整的服务间认证接入；实际对接需补齐受控通信与认证。接收器必须持久去重 `Idempotency-Key`，或另外完成对账/补偿设计。
5. 配置启动顺序、健康检查、日志、备份及同版本恢复，并在目标机器复跑业务闭环、权限拒绝、重试与重启持久化验收。

独立验收中的备份恢复是同一个 PostgreSQL 实例内的新数据库加独立 MinIO 卷；它没有证明整机丢失后的灾难恢复。长任务续租、容量与高可用也需另行验证。

## Clark 对接

Clark 由伙伴团队维护。本仓库保留原有只读兼容接口，当前 Native 契约以 `contracts/openapi.json` 为准。应用端新接口或写操作应先逐项对齐契约，再完成联合验收。不能仅修改 URL 就推定新版应用已兼容。

`deploy/legacy-memory/compose.yaml` 仅供追溯历史 Memory 部署，不包含本期治理 Runtime 的完整权限、身份、对象存储及接收端初始化。
