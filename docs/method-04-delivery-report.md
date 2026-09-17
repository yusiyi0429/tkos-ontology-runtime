# tkos.method/0.4 ＋ tkos.workspace/0.2 交付报告（最终证据）

状态：**本地受控链已完成并经 root 独立验证**（浏览器人类链 + controlled Co-agent HTTP/DB）；迁移 replay PASS，该轮验收使用 `index-CvwLgGIZ.js`，后续地图整合见 [地图记录验收](acceptance/map-records.md)；真实业务模型与 Clark 未运行；**未发布、未部署**。仅 mock/fixture 断言不构成独立验收；受控 Agent 的 HTTP/DB 独立验收计入 Runtime API 证据，只不代表真实模型验收。

## 交付内容（B0–B5）

- **契约与地图**：`docs/contracts/tkos-method-0.4.md`、`docs/contracts/tkos-workspace-0.2.md`、`docs/method-04-acceptance-matrix.md`；B1 方法地图/业务定义视图与 44 项来源快照。
- **workspace/0.2**：无业务锚点独立来源场景、来源身份/版本/更正/撤回、精确分享无遍历、默认私有与来源围栏、不可变 Context 快照与当前授权重查、Agent 自建 Context/run、引用草稿与 draft decision、commit/retry/get 撤权不恢复正文与自由文本；迁移 `0026_workspace_sources_v02.sql` 与 `0028_workspace_v02_grant_repair.sql` 已应用（0028 修复授权镜像；编号重复 0028 与 pi#3 文件名不同，均保留）。
- **method/0.4**：CEO Agent 直接 issue/reframe/associate、Agreement 全体精确确认、正式更新链、Battlefield+Domain、M1B 多 PCO 候选/承诺/整组激活、State 与 PeriodReview、问题移交原子性；无执行/验收副作用。
- **工作台与伙伴材料**：会话 facade（prepare/get/list/commit/retry、原信封恢复、CSRF/Origin/no-store）、全部人类动作 typed 表单与选择器/完整预览、`docs/runtime-governance-openapi.json`、`docs/runtime-workspace-v02-openapi.json`、`docs/partner-ui-event-mapping.md`、`docs/workspace-v02-integration.md`。

## 最终独立证据（root 执行，API 与 Browser 分开）

### API

| 证据 | 结果 |
| --- | --- |
| `qa-final-workspace` | **64 passed**（2 真实私有来源、来源 ACL、内部 Method 引用围栏、Agent 自建 Context/run、creator-purpose 撤权、receipt 修复、member display、重启/丢响应、SQL 无副作用） |
| `qa-final-v03` | **35 passed**（含 `source_unchanged`） |
| `qa-final-v04` | **29 正向 + 39 负向 passed** |
| `qa-facade02` | **47/47 passed**（含 commit/retry/get 撤权后不恢复 segments/purpose/draft body/note） |
| root final Python | 套件 **919 pass / 16 skip** + 独立迁移 replay **1 pass** = **920 total**（16 skip 单独列出） |
| UI / 构建 | **165 pass / 21 files**，typecheck、`uv build`、manifest 72 inputs/8 outputs 均 PASS |
| 迁移 replay（root） | **PASS**：fresh database create → 全部 migrations → replay 0 → drop（`root-migration-replay-final.log`） |

16 项 skip 的原因（`pytest -rs`）：1× `tests/test_health.py:188` 隔离库无 `agent_service` 用户做 non-human viewer 检查；1× `tests/test_method_map.py:284` 0.4 已由 B3/B4 启用，fail-closed 由该增量覆盖；14× `tests/test_narrative_legacy.py` legacy 集成不在隔离验收库外 seed。无其他忽略边界。

### Browser（root 独立）

- **本地受控链已完成（root 独立验证）**：DRI A 浏览器 publish+replace+second opinion；DRI B 浏览器 publish+withdraw+new comment；controlled Co-agent 经 HTTP close/resolve，对 3 份有效意见恰好处置一次（unresolved、正文不变）；四位 DRI/Owner 浏览器各自承诺 → CEO 浏览器整组激活 → Owner 正式 Mission；map04 label 与定义→2 条已授权记录导航 PASS。
- **final DB check（window-browser-report）**：confirmed；2 个正式 Mission；4 份承诺；5 条评论历史含 1 次撤回；0 执行授权、0 work receipt。
- 来源 2 会话（合成来源界面）：re-share → peer Context available → owner unshare → peer manualRefresh 后 Context withheld 且 canary absent；自动 ID 读取同样 withheld；真实私有正文不出现在截图中。
- **剩余**：无未完成的本地受控链步骤；迁移 replay 已 PASS（见上表）；真实业务模型与 Clark 仍未运行。

## 公开截图（仅合成/canary，不含真实私有转写）

- [method04-formal-mission.png](acceptance/assets/method04/method04-formal-mission.png) — 正式 Mission 字段（why/requirements/period/parentPCO/Scope/evidence）可见
- [method04-source-revoked.png](acceptance/assets/method04/method04-source-revoked.png) — 来源撤权后 Context withheld + canary absent（合成来源界面；真实私有正文不出现）
- [method04-comment-recovery.png](acceptance/assets/method04/method04-comment-recovery.png) — 0.4 评论并发 409 → 刷新 prepare → receipt
- [method04-candidate-review.png](acceptance/assets/method04/method04-candidate-review.png) — 候选承诺/整组激活
- [method04-business-definitions.png](acceptance/assets/method04/method04-business-definitions.png) — 44 项业务定义视图
- [method04-ontology-map.png](acceptance/assets/method04/method04-ontology-map.png) — 0.4 label + 定义→2 条已授权记录导航
- [method04-full-chain-mission.png](acceptance/assets/method04/method04-full-chain-mission.png) — 同一案例受控链（评论→收拢→承诺→激活→正式 Mission）

清单与 SHA-256：[acceptance/assets/method04/README.md](acceptance/assets/method04/README.md)。截图仅证明合成场景的浏览器可见状态，不代表真实模型、Clark 接线或完整发布验收。

## 恢复与效力语义（已测试）

- 命令用原信封保存；结果未知时查询命令/回执后对同一 `command_id` retry，不换幂等键、不新建命令；明确业务冲突才刷新重提。
- Context 是真实快照而非回执：返回顶层 `context_id`/`complete`/`items`；每次读取按当前成员与逐条来源授权重检，撤权后 `purpose=null`、正文不可恢复；commit/retry 返回当前授权视图。
- 场景流 CAS（`expected_version`）与场景 `version`、对象 `object_version`、不可变 revision、来源版本指针相互独立。
- 受控/合成 Agent 运行 `model` 一律标注 `controlled-fixture`；Runtime 不调用模型。

## 边界与未声称项

- 真实业务模型、Clark 接线、飞书 ACL 同步、跨实例会话、公网身份方案：未运行/未验证。
- 本文记录本地验收，不代表发布或部署；后续提交与合并以 Git 记录为准。迁移 replay 已 PASS；真实模型验收仍需真实模型运行。
- 本机仅保留 58805 本地审阅服务；root fixture API 与浏览器已关停，旧服务未动。
- 生成的 OpenAPI/示例仅合成占位符：无 example 实体、无私有验收路径、无凭据。0.4 逐动作参数 schema 以 `docs/runtime-method-registry-0.4.json` 与 `docs/contracts/method-profile-0.4.json` 为准（governance OpenAPI 的 `/commands/prepare` 为通用 governed envelope）。
- 迁移仅在隔离验收库；不修改既有正式库；运行时使用独立 app 角色，grant 级别与 0.1 表镜像。

## 相关文档

- 契约：[method 0.4](contracts/tkos-method-0.4.md)、[workspace 0.2](contracts/tkos-workspace-0.2.md)
- 接入：[workspace 0.2 集成](workspace-v02-integration.md)、[工作台](runtime-governance-workbench.md)、[UI 事件映射](partner-ui-event-mapping.md)
- 验收：[验收矩阵](method-04-acceptance-matrix.md)、[workspace 0.2 runner 说明](../acceptance/workspace_v02/README.md)
