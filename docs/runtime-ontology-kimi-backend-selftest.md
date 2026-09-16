# Runtime 本体视图：后端与规则文案自测报告（Kimi 会话）

日期：2026-09-16。范围：Python 后端（`src/memory_service_runtime/governed/dashboard.py`、`dashboard_routes.py`、`src/memory_service_app/dashboard.py`）、`tests/dashboard`、`workbench/dashboard/src/lib/ontology.ts` 及其专属语义测试。其余前端组件、App 接线、统一构建、真实 HTTP 与浏览器验收由另一会话与 Codex 负责，本报告不覆盖。

## 执行的验证

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| 后端逻辑测试 | `PYTHONPATH=src .venv/bin/python -m pytest tests/dashboard/test_dashboard_logic.py --confcutdir=tests/dashboard -q` | 56 passed |
| 后端全部看板测试 | `PYTHONPATH=src .venv/bin/python -m pytest tests/dashboard --confcutdir=tests/dashboard -q` | 100 passed / 1 failed（见"限制"） |
| 本体语义回归 | `cd workbench/dashboard && npx vitest run src/__tests__/ontology-semantics.test.ts` | 18 passed |
| 前端类型检查 | `cd workbench/dashboard && npx tsc -b` | 通过（ontology.ts 导出接口未变） |

## R1–R4 修复摘要

- **R1（目录版本号）**：`_catalog_item.object_version` 取自所选 revision（内容来源版本），不再取头指针；`basis_revision_id` 与之一致。针对性测试在 `test_dashboard_logic.py` 目录分页用例中。
- **R2（正式状态投影）**：`_formal_state` 重构。StrategicAgreement/MeetingMinutes/CandidateSet 已有确认分支保留并补 `human_approved`；新增 StrategicIssue（人工 `strategic_issue_confirmation` 与 0.3 CEO Agent `agent_issue_initiation` 区分，`formal` 与 `human_approved` 分离，`initiation` 字段）、ResearchBrief（`brief_sufficiency`）、StrategicJudgment（`strategy_update_confirmation` 经 `changed_refs` 覆盖）三个分支；通用 fallback 不再声称"无人工确认动作"，改为"无单独正式效力投影"并仍透出覆盖记录；历史版本（selected≠latest）一律 `status="historical"`，不借用当前对象 phase。`_covering_confirmations` 新增跟进 `potential_issue_ref`（0.1 的确认记录保存在候选议题上，经其 `strategic_issue_ref` 状态指针判定覆盖）。`CONFIRMATION_REVIEW_KINDS` 增加两个人工确认种类，`agent_issue_initiation` 刻意排除。
- **R3（全类型邻接）**：`DOWNSTREAM_FIELDS` 覆盖全部注册 payload 引用字段（MethodRun/EvidenceAsset 无引用字段，刻意不列）；检测测试改为结构化识别（`a2_models.ObjectRef` 与 `method_m1b_models.ExactRef` 双基类）+ 跨版本并集 + 动作写入字段白名单；SQL 测试断言绑定参数而非 SQL 文本（类型名为绑定参数，不内插）。
- **R4（业务文案）**：`ontology.ts` 按三版本真实 payload 与 action registry 修正：StrategicJudgment 改为更新提案确认产生的域内正式产物（关系方向反转为 提案→判断）；0.1 的 Signal/PotentialIssue/StrategicIssue/ReviewWindow 剥离 0.2+ 规则（激活/归档、来源类别、紧急度、反馈截止、直接创建）；0.3 立项主体为 CEO Agent；MethodRun 自主 intake 仅 0.3；LTCOReviewAdvice 生成主体为 Co-agent（`m1b_advise_ltco` 属 CO_AGENT 动作）；BusinessFact 改为记录/更正效力表述；OperatingState 补旧正式状态在新推荐未确认期间继续有效；补 PeriodReview→PotentialIssue（0.2/0.3）、OperatingState→LTCO（0.3）、StrategicArchitecture→Mission（0.3）、PeriodReview→LTCOReviewAdvice 关系；说明卡正文全面改用业务语言（正式内容版本、交付期限、上次状态建议、只记录协作过程、来源类别等）。语义回归断言 18 条独立于实现常量。

## 限制与未验证项

1. `tests/dashboard/test_dashboard_assets.py::test_real_tree_passes_manifest_verification` 失败：`DASHBOARD_ASSETS_STALE`，前端源码（含本任务必需的 lib 扩展）已改但未重建资产清单。按分工本会话不运行全量前端构建；待另一会话完成组件后由 Codex 统一 `build_dashboard.sh` 消除。非后端回归。
2. 未做真实数据库/真实 HTTP 验证：Codex 的独立观察器 `acceptance/ontology_views/run.py`（第一轮 43 passed / 1 failed，失败项即 R1）需在修复后由 Codex 重跑真实环境确认；本会话不读取其独立环境凭据。
3. `_formal_state` 的形状变化（新增 `human_approved`/`initiation`、`status` 词表补 `historical`/`superseded_confirmed`、fallback note 文案变更）已通过 `.runtime-acceptance/ontology-kimi/backend-needs.md` 通知前端会话。
4. 0.3 Signal 的"转化"：代码核实 0.3 的立项分支（`method_v03.py`）不再把来源 Signal 标记为已转化（仅 0.2 的 CEO 确认分支会），故 0.3 Signal 文案不含转化承诺；如监督方对 0.3 派生逻辑有不同解读，以真实环境复核为准。
5. 监督方文件（`acceptance/ontology_views/`、`.runtime-acceptance/ontology-kimi/review-findings.md`）未改动，断言未弱化。
