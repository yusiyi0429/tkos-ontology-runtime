# Method 0.5（本体 v0.7 对齐）实施与独立验收状态

日期：2026-09-23。分支：`codex/method-05-ontology-v07`。仅修改 Runtime 与治理工作台，未修改 Clark。

## 依据

《TKOS 本体结构 v0.7（M1 范围）｜CEO 对齐稿 2026-09-22》（docx KuU8d7rq6oozKDxtZqkcHI0nnJa）、本体登记 tkos.ontology-registry 0.7.1（docs/contracts/ontology-registry-0.7.json，SHA256 4f44c759d26db4e6812c60b11664add106697a1abf69935b9a9ca316e62220f8）、契约 docs/contracts/tkos-method-0.5.md（SHA256 d2ea113231533862fb2aed1611608b54c00fe66db0bbb99fe904ec6c69ed6d6f）。

## 已交付

| 批次 | 内容 |
| --- | --- |
| 契约与钉定 | 0.5 契约、profile 双重钉定（契约字节 + 本体登记字节）、迁移 0029、注册表 44 动作 / 16 对象类型 |
| Constraint | 对象类型、登记 / 修订 / 按范围确认、LTCO / PCO / Mission 的约束引用 |
| LTCO 与复盘 | 审视结论（首次确立 / 修订 / 维持；已确认的 LTCO 由 CEO_AGENT 修订出新草稿，CEO 以 `revised` 确认后成为新的正式版本）、CEO 确认复盘、下期 PCO 承接 |
| Mission 与承诺 | 贡献 / 依赖 / 资源需求、责任域 DRI 唯一承诺、Owner 生效记录 |
| Operating State | 起止时间、生成即正式、下钻引用 |
| 读取 | 公司集合视图、确认记录投影、review effect、五个集合列表 |
| 工作台 | 0.5 规则版本、Constraint 读法、三个人工门表单（LTCO 确认带结论；待确认的 0.5 复盘与 Constraint 进入 Method 任务页）、0.5 State 的经营问题入口、0.5 复盘 / Constraint / State 的正式状态投影；Constraint 登记 / 修订不做浏览器表单（计划 D7） |

## 本体登记对照（tests/test_ontology_registry.py）

一级对象 runtime_0_5：exists 9 / partial 7 / missing 4；关系：exists 27 / partial 4 / missing 2；人工门 4 / 4。

## 独立验收结果

- Python：1052 passed / 2 skipped（应用角色）；迁移到 0029 与空重放 15 passed（owner 角色）。
- 前端：终审修复轮未改前端；复跑 23 个文件、193 项通过，typecheck 与资产清单校验（scripts/verify_dashboard_assets.py）通过；构建最近一次在 Task 11/12 执行，此后前端输入未变。
- HTTP：acceptance/method_v05 27 项检查通过（含主链末尾的 LTCO 修订与 `revised` 确认 3 项），`runtime_method_v05_api_accepted: true`；acceptance/method_v04 复跑 29 项正向 + 39 项负向 = 68 项通过，`runtime_method_v04_api_accepted: true`，与 0.4 最终 QA 一致（docs/method-v04-backend-status.md 记的 26 + 37 早于后续新增的 5 项检查，该文档不改）。首次真实 HTTP/PG 独立验收发现三处实现缺陷，已在 5cf9b91、103baa9、eabf58d 修复：某 scope/mission Constraint 的确认人原本读不到也确认不了它；随后收窄该读取授权，使其不再覆盖 company 级 Constraint。

## 未验证

真实业务模型、Clark 接线、浏览器人类链、生产迁移与部署均未运行。CEO 对 Mission 最终确认人的裁决未定，本版按 DRI。

## 已知限制与待决

1. 同一主 Scope 可存在两个正式 0.5 LTCO（写入侧不阻止）；公司集合视图只保留其中一个（按 object_id 排序），是否在写入时强制唯一待产品决定。
2. 迁移 0029 的部分索引 `ix_gov_revisions_primary_scope` 当前无查询使用；日后按主 Scope 取值过滤的查询须同时带 `payload ? 'primary_scope_id'` 条件才能用上它。
3. 工作台下游关系投影未包含 Mission→Mission 依赖与 Constraint→Mission 适用两类 0.5 边。
4. 工作台默认规则版本与页眉仍为 0.4。
5. 确认人读不到 Constraint 的某条证据时无法确认该 Constraint（返回 NOT_FOUND），是否放宽待产品决定。
6. 候选 Mission 的 Owner 永久离任时，该候选集合无法激活（沿用 0.4 行为），只能恢复其任职。
7. Constraint 动作引用了错误类型的 Architecture / Mission 时，现返回 NOT_FOUND（原为 INVALID_REQUEST）。
8. 共享验收建库工具 `acceptance/method_independent/database.py` 与 `acceptance/execution_a3_independent/database.py` 仍固定端口 54350、只接受升级到 0021；0.5 验收使用 `acceptance/method_v05/database.py`。
9. Mission 的激活记录列在其 CandidateSet 的确认记录投影里；Mission 通过 `owner_effective_from` 指向它。
10. Scope DRI 经按范围回退本人登记的 Constraint 落在调用人所选的 `domain_id`（与 0.4 State 的回退同一模式），0.5 不约束该域。
11. CEO 确认复盘时会按 `v4._canonical_state` 重新解析每个被引用 State 的 DRI / Owner；任一席位空缺，确认返回 FORBIDDEN，须先补任。
12. 已确认的复盘被重新生成后，在 CEO 重新确认之前不能起草下一期 PCO（刻意从严，T7-c）。
13. 在任何复盘确认之前起草的候选 PCO，复盘确认后不能直接收拢，须重开窗口、修订该 PCO 引用已确认复盘后再收拢（刻意从严，T7-d）。
14. `acceptance/method_v05/database.py` 要求 Docker 上下文为 `desktop-linux`（仅 Docker Desktop），其他环境会被拒绝。
15. 在真实 scope 启用 0.5 前，须把 `m1b_record_constraint`、`m1b_revise_constraint`、`m1b_confirm_constraint`、`m1b_confirm_review` 四个动作名写入 `gov_activation_policies.action_roles`：company 域给 CEO 与 CO_AGENT；各授权域给 DOMAIN_DRI（Constraint 按范围确认与本人登记 / 修订的回退在授权域重新判权）。
