# Method 0.4 backend status (pi #3 / B3+B4)

状态：**运行时可验收 — 真实 HTTP/PG 0.4 链已在隔离库跑通 happy path＋negative 矩阵（run-26 全 PASS）；等待 Codex/root 在稳定 source checkpoint 上独立复跑与浏览器人工验收。B0–B5 整体不算完成：B1 UI/catalog 集成、B5 工作台/OpenAPI、最终 source manifest 证据仍待交付。**

范围：`tkos.method/0.4` Method 后端（M1A＋Architecture＋Agreement＋M1B＋State＋Problem transfer），
不含 dashboard map/UI（B1）与 workspace/0.2 业务实现（B2，reading fence 已协作接入）。

## 已交付

- **契约与注册**：`method_v04_profile.py`（SHA 绑定 `docs/contracts/tkos-method-0.4.md`）、
  `docs/contracts/method-profile-0.4.json`、`docs/runtime-method-registry-0.4.json`（41 actions / 15 object types 含 EvidenceAsset）。
- **严格模型** `method_v04_models.py`：Agreement 全体签署、Battlefield **与** Domain 均可作 Primary
  Scope（各自可带显式 `auth_domain_id`/`current_dri_principal_id` 映射；Required Capability 仍只归 Domain）、
  成对 Strategy/Architecture（未变对象显式保留 revision＋applicability rationale）、多 PCO/Mission
  Complete window（`ltco_refs` 精确集合）、CandidateSet、State Unknown、Problem transfer。
- **执行链** `method_v04.py`：
  - CEO Agent 直接 issue/reframe/associate；CEO 人类设置 participants/research。
  - Agreement：Agent 只起草；每个当前签署人确认同一精确 revision；名单/正文/依据变化清空待确认；
    正式化复查全部当前任职并推进 issue round 为 `agreement_formal`。
  - 正式更新：Agreement 锁定、当前 basis CAS、未变对象保留、变更对象原子新 revision；
    proposal/Agreement 过期拒绝。
  - M1B：window 冻结完整 PCO＋Mission 成员；同一成员可对多个冻结目标发表多份有效意见
    （replace/withdraw 只移除所选记录，其余保留）；resolve 恰好覆盖每份冻结意见一次；
    DRI/Owner 本人承诺；**激活时复查当前 Strategy/Architecture/LTCO basis、成员状态和每份承诺的
    assignment 当前性**；被撤销的承诺 assignment 即使同一人重新任职也不能复用，必须显式 reopen 重承诺。
  - Strategic Problem transfer 只允许活跃 issue round；completed round 必须先显式 reframe；
    失败回滚保持 tracking；历史转移链接在 reframe 后保留可见。
  - PeriodReview：`fact_refs` 在 0.4 明确不支持（BusinessFact 未启用），拒绝而非静默；
    `target_refs` 必须与 canonical State 的 subject_ref **精确三元组**一致。
  - State 责任实时解析：LTCO=当前 CEO、PCO=Primary Scope（Domain/Battlefield）映射的当前 DRI、
    Mission=Owner；无证据只能 Unknown。
- **协作与授权**：`method_access` 接入 workspace/0.2 私有来源 fence（domain read 不再绕过
  `workspace_v02_guard`），0.4 participants/owner 的 scoped 授权不扩大公司角色；映射不隐式授权。
- **迁移**（root 管理编号，均已应用隔离库）：`0027_method_v04.sql`（`gov_method_commitments`＋0.4 binding gate）、
  `0028_method_v04_contract_repin.sql`（契约文档状态段落更新后的重绑）。执行链本身不需要额外 0029：
  未引入新的 object/status（沿用既有 lifecycle status＋method_state phase）。
- **验收脚本** `acceptance/method_v04/`：
  - `run.py`：happy path 26 项＋negative/rollback/replay/权限矩阵 37 项真实 HTTP/PG；每步 API，
    SQL 仅独立断言；`src` 变化时保留原异常并标记 source_changed（run-27 即此类诊断）。
  - `browser_fixture.py`：两个 fresh 0.4 scope（agreement_pending / candidate_pending），
    身份与 token 私密文件（0600），业务状态全部 HTTP，保留 API 供人工浏览器确认；不宣称浏览器验收。
    已自测：browser-fixture-2 两个 scope 成功暂存，`browser_acceptance=not_run`、`business_sql_used=false`。
- `docs/method-v04-blocked-recovery.md`：全部有意阻断与唯一恢复路径（完成 round 移交、stale
  candidate、承诺 assignment 撤销/重新任职、Agreement 失效、关键分歧、PeriodReview 依据、
  私有来源、完整性与陈旧 envelope）。

## B5 人工 UI 接管（本批）

- `governed/governance.py`（core projection/availability，归本 pi）：
  - `PHASE_RULES['m1b_confirm_ltco']` 修正为 `draft`（与 core `_collect_confirm_ltco` 一致）。
  - `m1b_activate_candidates` availability 复用 core 只读 `method_v04.activation_blockers`，
    返回 `missing_commitment` / `critical_difference` / `stale_basis` / `member_state_changed` /
    `commitment_assignment_revoked` / `commitment_assignment_mismatch`；不再只给通用 phase 理由。
  - `method_object_actions` 为每个 human action 附带 `payload`、`method_state`、`options`：
    comment/replace 的冻结目标与本人有效意见、commit 的本人责任 exact refs、problem 的本人任职/
    canonical State/依据、participants 的当前人类与任职、`m1a_set_participants` 行选项。
  - `method_tasks` 保留实际可用任务；对 CEO 额外展示 pending CandidateSet 的被阻断 activation
    （`allowed=false` + 具体 reason），任务携带 `payload`、CandidateSet 的 `members`/`commitments`
    供完整内容复核。人类动作仍严格限于 `method_v04_models.HUMAN_ACTIONS`。
- `workbench/dashboard/src/MethodActions.tsx`（归本 pi）：
  - Agreement/proposal/candidate/problem/state 完整内容预览；确认前可见正文、名单与确认状态、
    候选成员与现有承诺、未决差异（关键项标红）。
  - 所有 exact refs 只能从本人可读对象的下拉/多选派生（目标、意见、责任、任职、依据、State），
    不再有 object_id/revision_id/payload_hash 输入框。
  - 被阻断的 activation 按钮 disabled 并显示具体原因；State 修正提供 `unknown`；Problem 表单
    只给合法层级/本人任职/可选依据。
  - 前端 `BROWSER_ACTIONS` 白名单二次过滤，Agent-only 动作即使被投影泄露也不渲染。
- 验证：`MethodActions.test.tsx` 15/15；`vitest run` 全部 20 文件 141/141；
  `tests/governance` + method 单测 97 passed；真实 fixture 投影检查：agreement_pending 显示
  `m1a_confirm_agreement`，candidate_pending 对 CEO 显示 `m1b_activate_candidates=false, missing_commitment`，
  四个 DRI/Owner 各自看到 1 条本人 `m1b_commit_candidate` 选项；未替用户 seed 任何确认。
- 已知：B2 的 `src/__tests__/SourceScenes.test.tsx(264,3)` 有既有 typecheck 错误（`releaseA?.()`
  被推断为 never）；本批文件在排除该文件后 `tsc --noEmit` 通过。全量 `tsc -b` 待 B2 修复。
- pi1 负责 `memory_service_app/governance*.py` facade；前端需要的 core 只读路径：
  `GET /v1/governance/method/tasks?after&limit` 与 `GET /v1/governance/objects/{id}/actions`；
  facade 应原样透传 `items[].payload/method_state/members/commitments/actions[]`，不做规则二次推断。
- Root browser fixture（`method-browser-report/browser-fixture.json` + `catalog-browser/accounts.json`）
  已就绪：agreement 待三家确认、candidate 待四位具名承诺＋CEO 激活；本批不代替用户确认。
  旧 fixture 的 proposal 在本次 core 修复前已确认，其 `phase` 仍为 `reviewed`；重复确认会被 core
  以 basis 过期拒绝；如需干净展示可由 root 重新暂存 fixture。

## 已验证

- **Root 独立 `qa-final-v04`：29 happy + 39 negative 全部通过**（最终源码，稳定 checkpoint）。
- `run-26`（隔离库）：happy 26/26、negative 37/37 全 PASS；`run-27`：29/29＋39/39 全 PASS
  但运行中其它进程改动 `src`，按守卫规则标记 source_changed 诊断；`run-28` 同样因并发编辑
  标记 source_changed。最终独立证据以 root 的 `qa-final-v04` 为准。
- `browser_fixture.py` 自测：browser-fixture-2 两个 scope 成功暂存（agreement_pending /
  candidate_pending），accounts 文件 0600，`browser_acceptance=not_run`、`business_sql_used=false`。

## 待办 / 边界

- **B5（工作台/OpenAPI/UI 集成）仍阻塞发布**：本批只交付 B3+B4 后端与验证；不声称整个计划完成。
- 浏览器人工验收与 Clark 真实模型：由 root/Codex 使用 `browser_fixture.py` 驱动；本批不宣称。
- **最终 source manifest 证据（policy / table types / Context / readers / business actions）**
  需在 B1/B2/B5 UI 集成完成后，由 root/Codex 在稳定 checkpoint 上统一生成；需覆盖：
  protocol 支持注册表与 0.4 binding gate、`gov_method_*` 表与 RLS/权限、Context 选择的 0.4
  state keys、readers 的 `method_v0_4` 状态与 private-source fence、以及 41 个 0.4 actions 的
  ActionRequest 分发；本批 run 证据只覆盖 B3+B4 后端。
- `browser_fixture.py` 暂存的两个 scope 与 `acceptance/method_v04/run.py` 的验收 scope 均为
  synthetic 隔离 scope，不写入公司数据。
- B1 的 catalog/map 尚未把已编译的 `tkos.method/0.4` 加进 `dashboard.ONTOLOGY_CONTRACT_VERSIONS`
  （B1/Codex 集成项；method map 目前仍报 `documented_not_compiled`）。
- `fact_refs` 在 0.4 明确不支持；若未来启用 BusinessFact，需要新的契约 pin/迁移由 root 执行。
- 本批不产生执行/验收/Outcome 副作用；0.1–0.3 历史行为保持冻结。
