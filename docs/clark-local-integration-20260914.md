# Clark 本机 Runtime 联调环境

2026-09-14：网络及 Runtime 新接口就绪；旧叙述客户端不兼容 Method 对象，页面业务接线仍待伙伴完成。

## 实际服务

| 项目 | 当前配置 |
| --- | --- |
| Clark | `clark-ea686a9-model-test`，`ea686a9 / 0.142.0`，本机 `127.0.0.1:3401` |
| Runtime Compose 组 | `tkos-clark-linked-20260914` |
| Runtime API 容器 | `tkos-clark-linked-20260914-runtime-api-1` |
| Runtime Worker | `tkos-clark-linked-20260914-runtime-worker-1`，本轮源码重新构建的 `tkos/worker:clark-workspace-20260914` |
| PostgreSQL | `tkos-clark-linked-20260914-postgres-1`，本机端口 54350 |
| MinIO | `tkos-clark-linked-20260914-minio-1`，本机端口 54351 |
| Clark 到 Runtime 地址 | `http://runtime-api:8010`，专用网络 `tkos-clark-linked-20260914` |
| 本机 Runtime 地址 | `http://127.0.0.1:58802` |
| Runtime 镜像 | `tkos/runtime:clark-workspace-20260914`，ID `sha256:e106cb4b3294f0641791d6b3978c4ac55c6c74c4037134dcd3d82f98401b6d5e` |
| Runtime 来源 | 独立工作区 `codex/clark-method-workspace-runtime`，`1fb3226 / v0.2.1` 基线加未发布工作面扩展，重新打 wheel、构建镜像；不是正式 v0.2.1 发布镜像 |
| 数据库 | 新建 `tkos_a1_method_f305ce9fc4164c4a`，应用与迁移身份分离，含 0021、0022 迁移 |

清理整合后，五个服务全部由同一 Compose 组管理。本轮使用 PostgreSQL/MinIO 旧数据卷的停机物理副本创建本组专属卷 `tkos-clark-linked-20260914-postgres-data`、`tkos-clark-linked-20260914-minio-data`，保留数据库角色、历史数据、S3 版本 ID 和对象锁元数据，避免逻辑复制改变证据引用。旧卷未删除。API/Worker 通过容器内 `postgres:5432` 和 `minio:9000` 连接，不再依赖旧 acceptance 容器或 host.docker.internal。新组保留存量测试数据，但本轮只使用上述独立数据库和 scope。

Worker 使用独立 `clark_link_worker` 数据库登录，继承已有应用角色权限，不是 owner、superuser 或 BYPASSRLS 角色。API 与 Worker 数据库凭据不同。本轮不提供 M2 执行授权。

此前 `tkos-runtime-localprod-1788778834` 组实际 API 为 0.2.0，缺少身份及工作面接口，没有用于本次验收。经用户授权清理后，该组、旧 target-local、workbench-local、acceptance 容器及旧 Clark 配置备份容器已移除。旧 3400 入口已停用，新 Clark 仍使用 3401 和原本机访问码。

## 验证结果

- 新容器通过合法 M1A/Method 动作创建测试 Strategy、LTCO、PCO、Mission、ReviewWindow 和月度场景。窗口保持 open，无评论、无候选集合确认，不预置未来成功结果。
- 14 项检查通过：5 个独立人类/Agent 身份；CEO、两个 DRI 的窗口与月度场景读取；跨域不可见；DRI 不可读 CEO 工作面；Co-agent 授权 Context Pack 完整包含窗口、PCO、Mission。
- Runtime 重启并恢复健康后再次执行上述检查，通过。初次启动未就绪时连接断开不记为接口通过。
- 完成五服务整合并清理旧容器后再次执行上述 14 项检查，通过；旧 narrative 兼容项仍为 404，未将其计为通过。
- 新 Worker 实际执行 `system.noop`、`object_store.preflight`，均一次成功；重复提交返回同一任务。MinIO 版本控制及对象锁检查通过。停止 Worker 后入队的另一任务状态为 queued，重新启动后变为 succeeded，attempt=1。
- Clark 容器内经专用网络访问 `/v1/identity` 返回 200，身份为固定测试 CEO Agent。
- Clark 镜像保持 `sha256:ffc007e4f74fbb67a2f997d32b3b08c306038cb7682c58a38179acba75ea5d08`；访问码、数据卷和真实模型配置保留。健康接口 `ontology.configured=true` 仅表示配置存在。

## 已确认的接入缺口

Clark 已有 `src/lib/ontology/narrative.ts` 读取 `POST /v1/context-graph/narrative`。Runtime 的 `narrative_facts.collect_facts` 明确只投影 `legacy_v0_2` 对象，对 Method 对象返回排除原因 `protocol_interpretation_not_supported`。因此该隔离 Method scope 返回 HTTP 404 `NARRATIVE_EMPTY`。这是协议适配缺口，不能通过增加样例、改旧协议标签或放宽授权来伪装接通。

伙伴接线须使用：

1. 本人服务端会话绑定 `/v1/identity`，CEO/DRI 页面读取 `/v1/workspaces/{ceo|dri}` 和 `/v1/workspace-scenes`。
2. 独立 Agent 使用 `/v1/context-packs`，明确 `contract_version=tkos.method/0.1`；草稿审阅使用 `stage=review`、`purpose=analysis`、`include_drafts=true`。
3. 评论及 CEO 决定使用正式 `prepare → actions`；个人核对等使用场景事件接口。不得把当前共享演示访问码或固定 CEO Agent 凭据作为所有人的业务身份。

Clark CEO 战场和 `/dri` 暂无上述工作面调用代码，当前页面仍为样例。**本次不是 Clark 浏览器业务闭环，也未执行真实模型收拢。** 若需保留旧 narrative 客户端并读取 Method，应另做有明确版本、效力和授权语义的 Runtime 兼容投影；当前没有绕过已有协议边界。

## 本机复跑与交接

本工作区 `.runtime-acceptance/clark-linked-build/` 保存构建日志、源码清单、`checks.json` 和 `restart-check.log`。`private/` 下的 Compose、身份和环境文件含凭据，只用于本机，不提交或发送。

```sh
docker compose -f .runtime-acceptance/clark-linked-build/private/consolidated-compose.json up -d --wait --pull never
uv run python .runtime-acceptance/clark-linked-build/check.py
```

`check.py` 的旧叙述兼容项明确记录 `passed=false`，不能只看脚本退出码。`private/compose.json` 是整合前的历史配置，不再用于启动。

## 容器清理结果

移除了 16 个旧 TKOS/Clark 测试容器，机器上当前仅运行本组 5 个健康容器。其他项目原本已停止的容器保留，未跨项目清理。未删除镜像、数据卷，也未执行全局 prune。

精确删除清单见本工作区 `.runtime-acceptance/clark-linked-build/cleanup-result.json`。旧容器配置、镜像 ID、挂载信息和最近日志保存在 `private/container-cleanup-20260914/`，可以用原镜像与原卷重建；已删除容器本身不能直接 docker start，未单独归档容器可写层。新的 pg/minio 卷与保留的原卷是独立副本。旧其他数据库与 S3 内容仍保存在原卷中。

正式接线契约：[Clark 工作面接入 Runtime](clark-workspace-integration.md)。未修改或交付 Clark 源码补丁，未发送外部消息，未合并、发布或远程部署。
