# Clark 工作面 Runtime 支撑能力：本地验收

2026-09-14，在 Runtime 独立工作区 `codex/clark-method-workspace-runtime` 完成开发和本地验收，基线 `1fb3226 / v0.2.1`。Clark 交互基线为 `ea686a9`，本次未修改两个 Clark 工作区，也未改动原 Runtime 工作区的未提交内容。本扩展尚未提交、合并、发布或部署。

## 交付结果

| 范围 | 结果 |
| --- | --- |
| Runtime 新接口独立验收 | **49/49 项通过**；257 条 HTTP 记录，另有 1 次真实响应丢失代理请求 |
| Python 回归 | **690 passed，1 skipped**；含 21 项新增场景契约检查 |
| 工作台回归 | **61/61 通过** |
| 数据库迁移 | 新隔离库从历史 A3 基线应用 0021＋0022，再次运行无待应用迁移；应用与 owner 分离 |
| 打包 | 离线 sdist／wheel 构建通过；这是开发源码构建，没有重新发布 v0.2.1 |
| 接口契约 | 完整 OpenAPI 导出与源码校验通过；旧 Method 契约和 49 个动作保持原义 |
| 伙伴接线 | **待完成** |
| Clark 真实浏览器闭环与截图 | **待伙伴接线后联合验收** |
| 真实模型收拢 | **待伙伴执行**；当前使用受控产物，未调用模型，不代表真实模型通过 |

场景验收使用真实 FastAPI 进程、PostgreSQL、MinIO 和两位独立 DRI／CEO／Agent 测试身份。Strategy 至 ReviewWindow 的初始业务对象均由合法 Method 动作生成，评论、候选和确认均在验收过程中写入。SQL 用于核对候选成员版本、场景事件／回执、RLS、应用角色权限和无执行对象等事实，不预置业务结果。

## 三阶段分别验证

**月度核对：** 读取固定 PCO/Mission 与缺失展示字段；评论字段定位、替代、撤回及本人历史；展示截止时间与业务周期分离；评论竞争导致旧关窗 CAS 拒绝；Co-agent 自身 Context 完整选入窗口和目标；缺失意见的收拢请求被拒绝；候选差异带精确前后版本；DRI 核对不确认候选，Agent 不能代 CEO 确认；CEO 整组确认后 DRI 读取正式 Mission 和待执行承接；显式重开使旧核对成为历史。

**周进展与事实：** 来源读取核验真实证据；周材料、问题回答和本人确认均持久化；答案撤回与材料更新使旧确认失效；刷新来源只记录请求；BusinessFact 修正保留原记录，PeriodReview 再生成仍为分析；周材料引用确切事实／分析版本；成员不因加入场景获得额外来源权限，最新材料不可读时不回退旧材料。

**会议与 CEO：** 已阅、请补充、带到会议、负责人开始／结束；原始转写及顺序校验；伪造引文拒绝；单份会议发布稿、行动 owner／负责人兜底、同域 CEO 判断分流；有权限的 CEO 可读原话、判断及路由，未获来源权限的成员看不到发布稿内容；撤回当前稿不恢复旧稿；发布稿不修改正式 Mission，也不声称消息已送达。

## 恢复与数据库观察

- 并发双击返回同一回执；同键修改内容被拒绝；两个不同请求基于同一场景版本时发生 CAS 冲突。
- 在场景事件写入之后、回执写入之前注入真实异常，验证整个事务回滚。
- loopback 故障代理在 Runtime 已提交并返回 200 后，丢弃全部响应并关闭客户端连接；独立 SQL 确认已提交。重启 Runtime 后重放原信封，获得同一回执且没有追加第二个事件。
- 场景材料、个人记录、单份发布稿和历史在重启后保持一致；撤权后不能读取场景、旧回执或重放旧请求。
- 每条场景事件都有同事务正式 ActionReceipt，所有外部 effect_task_ids 为空。候选集合每个成员对应实际持久化的精确版本及 hash。
- 未创建 ExecutionCommitment、ExecutionPlan、WorkItem 或 Deliverable；Mission handoff 的 execution_authority、delivery_accepted 和 outcome_achieved 保持空。
- 新表未设置 scope 时不可读；应用角色没有 UPDATE/DELETE，历史保留由追加式模型、RLS、权限及不可变触发器共同约束。

源码包含 132 个文件，验收开始和结束清单一致；清单摘要为 `7fbad9566af2ab2f23265a2ec8cc5f64aedacf3377995a135225dc3380129e8c`。机器结果、检查名称及 OpenAPI 摘要见 [脱敏验收摘要](acceptance/workspace-scenes-summary.json)。原始 HTTP／数据库／证据与进程日志留在忽略目录，不随文档提交。

## 范围限制与交接

Python 原有跳过项为 `tests/test_health.py::test_viewer_rejects_non_human`，原因是隔离数据库没有该测试要求的 `agent_service` 用户。`tests/test_migrations.py` 需要独立 CREATEDB 管理身份，未混入应用角色回归；本轮迁移与重放由隔离 bootstrap 实测。旧 Method 98 项完整环境门槛矩阵未重新执行，本报告的 Runtime 通过结论限于本次 49 项场景接口验收和上述回归。

伙伴按照 [交互、字段、权限与 Agent 接入契约](clark-workspace-integration.md) 和 [OpenAPI](runtime-workspace-openapi.json) 接线。复跑命令见 [验收入口](../acceptance/workspace_scenes/README.md)。后续联合验收需补真实 Clark 多身份页面、焦点／轮询刷新、迟到请求处理、完整浏览器截图，以及真实模型的 Context、意见取舍与候选修改一致性证据。正式企业 SSO、生产数据迁移、远程部署和 M2 执行授权不在本轮。
