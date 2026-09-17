# Runtime 本机治理工作台（M1B）

Runtime 负责权限、版本、正式动作及回执；Clark 继续负责业务交互、模型与 Agent 编排。工作台提供有权人员检查及办理 M1B 事项的独立入口。没有数据库任意编辑器，也没有 Agent 身份选择器。

## 功能与正式效力

| 页面操作 | 保存内容 | 效力 |
|---|---|---|
| 我的待办 | 按当前身份读取窗口及可办理动作 | 不产生决定 |
| 方法事项（0.4） | 全部人工白名单动作表单：参与人提名、Agreement 本人确认、正式更新最终确认、LTCO 确认、候选本人承诺与 CEO 整组激活、候选/窗口重开、经营状态确认（含 summary/rag 修正）、问题登记/修订/关闭 | 各动作按规则产生确认/提交记录；精确引用由已授权对象选择器派生，提交前展示完整预览 |
| 评论、替代、撤回 | 既有 Method 评论及历史 | 本人意见 |
| 评论字段定位 | 月度场景记录，引用已保存的正式评论 | 不修改评论或目标 |
| 记录核对完成 | 本人及精确候选版本 | 不代替 CEO 确认 |
| 整组确认 | 原有 `m1b_confirm_candidates` | CEO 确认完整 PCO/Mission 集合 |
| 重开 | 理由、期限及合法依据 | 依 Method 规则重新核对 |
| 正式 Mission | 有效版本及 handoff | 待执行承接；无新增执行授权或验收 |
| 我的提交 | 原始信封、状态与回执 | 可查询及原请求恢复 |

写入按版本化人工动作白名单收口：`tkos.method/0.3` 的 M1B 人工动作、`tkos.method/0.4` 在实现契约中声明的人工动作（`method_v04_models.HUMAN_ACTIONS`，含清单外动作显式拒绝）、以及 `tkos.workspace/0.2` 的人工事件（`agent_run` 等 Agent 运行记录不在白名单）。旧版本继续按原规则只读。缺失来源不补样例；权限每次读取及提交重新检查。本体地图、业务关系图和对象列表沿用现有投影；白名单内人类动作均有已验证表单与完整预览（精确引用、哈希由已授权对象选择器派生，不手填）；白名单外的 Agent 专属动作只提供对象详情的只读链接，不生成通用载荷编辑器。

## 启用与个人身份

默认关闭 `TKOS_GOVERNANCE_WORKBENCH_ENABLED`，关闭时保留原只读看板。
本机单实例开启时，同时设置 `TKOS_DASHBOARD_ENABLED=1`、`TKOS_GOVERNANCE_ACCOUNTS_FILE`、`TKOS_GOVERNANCE_COMMANDS_DIR` 及允许的 Host/Origin。绑定地址使用回环地址。本轮不提供公网身份方案。

使用 `python -m memory_service_app.governance_accounts --help` 建立账号。工具读取已有 human 的 `/v1/identity`，绑定 scope、principal 和服务器私有 token 文件，生成个人随机登录码。账号文件和 token 文件须为 0600，任务目录须为 0700。重新 provision 会重置登录码并使旧会话失效。浏览器仅持有 HttpOnly、SameSite=Strict 会话及 CSRF 标记，不接触 Runtime token。

会话最长 8 小时，闲置 30 分钟失效；重启要求重新登录。每次读取核验当前任职及账号映射。正式业务数据在 PostgreSQL，原信封恢复日志位于配置的私有目录；两者都需保留。工作台是单进程设计，不能把内存会话当作跨实例会话存储。

## 请求与恢复

同源 `/dashboard/api/v1/session` 登录。写入须同时携带同源 Origin、会话 Cookie、`X-CSRF-Token`。

1. `/commands/prepare` 接受白名单内的 Method Action（含 0.4 人工动作）或 workspace 命令（0.1 月度、0.2 来源场景与 Context）；保存精确对象、CAS 和幂等键。
2. 展示业务预览及完整候选，用户再次确认。
3. `/commands/{id}/commit` 提交保存的信封；不接受浏览器临时替换信封。
4. 结果不明时查询 `/commands/{id}`；显式 `/retry` 重放同一信封。业务冲突返回拒绝，刷新重新准备；不自动改 CAS 后重试。

只读 Bearer 接口位于 `/v1/governance/tasks`、`/v1/governance/method/tasks`、`/review-windows/{id}`、`/objects/{id}/actions`。本机 facade 增补个人会话、正式 Mission、依据、授权 Context 和私有提交查询，并新增 `governance/method-tasks`（别名 `method/tasks`）、`governance/sources`、`governance/sources/{scene_id}`、`governance/sources/contexts/{context_id}` 与 `governance/objects/{object_id}/actions` 会话读取。响应禁止缓存。详见 [OpenAPI](runtime-governance-openapi.json)。UI 事件到接口、结果与恢复的映射见 [伙伴接线映射](partner-ui-event-mapping.md)。

## 验证边界（只引用已执行证据）

- API：`qa-final-workspace` = 64，`qa-final-v03` = 35，`qa-final-v04` = 29+39，`qa-facade02` = 47/47；root final Python **920 total（919 套件 + 1 迁移 replay）/ 16 skip 另列**；UI **165 pass / 21 files** + typecheck/uv build/manifest PASS。
- 浏览器（root 独立）：同一案例本地受控全链已完成（DRI A/B 评论含撤回、controlled Co-agent close/resolve 3 份有效意见一次、4 承诺→CEO 整组激活→Owner 正式 Mission）；final DB check：confirmed、2 正式 Mission、4 承诺、5 评论含 1 撤回、0 执行/0 work receipt。
- 迁移 replay 已 PASS；root 标记 source frozen（`index-CvwLgGIZ.js`）；未发布/未部署。受控 Agent 的 HTTP/DB 独立验收计入 Runtime API 证据；真实业务模型/Clark 未运行；伙伴未验证。
- 本工作台生成的 OpenAPI 与示例已核对：无 example 实体、无私有验收路径、无凭据，仅合成占位符；method-tasks 别名 operationId 已由 pi#1 修复。

[复跑步骤](../acceptance/governance_workbench/README.md)与[本轮验收报告](acceptance/runtime-governance-workbench.md)分别说明环境和验证边界。
