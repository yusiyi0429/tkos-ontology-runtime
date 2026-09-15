# 生命周期 0.2 增量验收报告

2026-09-15；Runtime 独立工作区 `codex/clark-method-workspace-runtime`，在 `1fb3226 / v0.2.1` 基础上实施。代码未合并、未发布；Clark 代码未改动，现有运行容器未替换。

## 实现范围

- 0.1 与 0.2 按确切版本注册和读取；绑定由服务端控制，跨版本引用拒绝，协议冻结覆盖两个版本。
- Signal 激活/归档、直接或复盘提案、CEO 正式立题、业务分类及战略图单元类型。
- 新 ResearchBrief；CEO 确认材料充分后由 DRI 开会。双 Agent 纪要、DRI 最终确认、CEO Agreement 延续原规则。
- ReviewWindow 独立截止时间；数据库时钟约束评论/替代/撤回，Co-agent 显式关窗，CEO 重开新窗口。
- 专用 Agent 研究 Context 与快照恢复，运行和授权重新核验；普通 Context 排除 Signal。
- 生命周期工作面、动作提示、PCO 对独立事实和复盘的授权聚合；不创建执行授权、交付验收或 Outcome 达成记录。

## 执行结果

| 检查 | 结果 | 范围/限制 |
|---|---|---|
| 生命周期 0.2 真实 HTTP、数据库与存储 | 22/22 通过 | 含双版本共存与冻结、评论/关窗锁竞争、候选部分写入回滚、双击幂等、截止前准备后过期提交、显式重开、重启、撤权、Context 和跨版本拒绝 |
| Python 回归 | 685 通过、1 跳过 | 跳过原因：隔离库没有 agent_service 健康检查夹具；未运行需要独立管理员建库夹具的 test_migrations.py |
| 独立 legacy narrative | 14/14 通过 | 使用隔离数据库及分离的 APP/MIGRATION 身份 |
| workbench Node 测试 | 61/61 通过 | 读取、分页、身份切换、迟到响应与展示行为 |
| 既有 Clark 场景 HTTP 回归 | 49/49 通过 | 本人意见、周材料、会议发布、权限、恢复；不是 Clark 浏览器验收 |
| 既有 Method 业务验收 | 93 项通过 | 完整 runner 的 98 项未全部完成；发布门槛缺少原历史/外部回归证据包，不能报告完整发布验收通过 |
| 新库迁移与重放 | 通过 | 从不可变 A3 基线迁移至 0024；重复迁移 applied=[]；未升级旧业务库 |
| 源码包与 wheel 构建 | 通过 | 包元数据仍为现有 0.2.1；仅验证构建，不发布新版本 |

新增独立数据库位于现有本地 PostgreSQL 容器，API 从当前源码启动临时 loopback 进程。未新增容器、未修改 Clark BFF、未调用真实模型。合成初始对象由合法动作创建；权限夹具可用 SQL，业务 SQL 只核对。

结构化摘要：[lifecycle-v02-summary.json](acceptance/lifecycle-v02-summary.json)。源码清单哈希见摘要。私有 HTTP 记录、命令和回执留在 `.runtime-acceptance/`，不提交原始验收数据或凭据。

## 交付与后续边界

- [五列生命周期契约](contracts/tkos-method-0.2.md)、[接入说明](runtime-lifecycle-v02-integration.md)、[OpenAPI](runtime-lifecycle-v02-openapi.json)。
- [复跑步骤](../acceptance/lifecycle_v02/README.md)。
- [TokenKing Wiki 契约](https://tokenking.feishu.cn/wiki/OR4GwPJONiQldPkJBGAc7nDKnuh)，已挂入 1.2.3 Engine & Ontology。

当前状态为 **Runtime 生命周期增量接口验收通过**。伙伴接线、Clark 浏览器闭环、真实模型收拢均未验证；本轮不覆盖生产身份、部署或 M2 执行授权。
