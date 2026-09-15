# Clark 生命周期接入：Method 0.2

日期：2026-09-15。Runtime 独立工作区扩展 v0.2.1；Clark 页面、BFF、模型调用由伙伴维护。正式规则见 `contracts/tkos-method-0.2.md`；现有 0.1 不自动迁移。本文为 API 接入说明，不代表 Clark 已接线、真实模型通过或生产部署。

## 读取与事件映射

| 页面事件 | 请求 | 返回与刷新 |
|---|---|---|
| 加载身份 | GET /v1/identity | 当前 principal、类型、scope、任职；切换会话清除旧内容 |
| CEO / DRI 列表 | GET /v1/workspaces/{ceo或dri}?collection={collection} | 授权 items、精确版本、业务阶段；按 next_after 分页 |
| 生命周期资料 | collection=signals、potential-issues、strategic-issues、research-briefs、strategies | 无数据返回空列表；不推断优先级或进度 |
| 固定版本与协作历史 | GET /v1/objects/{object_id} | latest/effective revision、method_state、protocol |
| 月度核对 | collection=review-windows | 正式 feedback_deadline 与 business_period 分开；operations 提示当前限制 |
| DRI 正式 Mission | collection=missions | effective_revision、handoff、awaiting_execution_handoff；执行授权为空 |
| 评论/收拢/确认/重开 | POST /v1/actions/prepare → POST /v1/actions | 仅正式 committed 回执表示成功，立即刷新 |
| 页面阅读、核对、周复盘和会议发布 | 既有 /v1/workspace-scenes/events | 场景回执 formal_effect=none，不产生正式决定 |
| 对话 Context | POST /v1/context-packs，contract_version=tkos.method/0.2 | Signal 从显式与递归引用中剔除；不接受 purpose=research |
| 研究 Context | POST /v1/method/research-context-packs | Agent 自身会话、研究 run_ref、已绑定的根对象；可纳入授权 Signal |
| 研究快照恢复 | GET /v1/method/research-context-packs/{snapshot_id} | 同一 Agent、当前授权和 running 状态重新核验；普通快照入口拒绝研究快照 |

聚合和 Context 结果不是授权凭据。提交时重新核验角色、域、协议绑定、精确引用和 CAS。前端每五秒、重新聚焦与成功提交后刷新；旧会话的迟到响应由 BFF/页面取消或丢弃。服务端没有真实消息送达承诺。

PCO 复盘聚合：`GET /v1/method/pcos/{object_id}/review-materials` 返回当前身份有权读取、引用可见 PCO 精确版本的 BusinessFact 与 PeriodReview。它不写入目标正文。

## 写入格式和恢复

新动作统一声明 `contract_version: "tkos.method/0.2"`，采用现有 ActionRequest 信封。`target` 包含 object_id、revision_id、expected_version；无 target 的创建动作在 params 中提供 domain_id。准备请求获得 expected_versions，再将原请求加上该集合提交。精确业务引用是 object_id、revision_id、payload_hash；引用必须取自授权读取或回执。

示例 params（外层信封与现有 Method 相同）：

```json
{
  "domain_id": "<当前授权域 UUID>",
  "payload": {
    "title": "需要研究的业务问题",
    "summary": "CEO 直接提出的真实问题",
    "business_scope": "battlefield",
    "urgency": "red",
    "urgency_reason": "本周期存在明确决策期限",
    "confirmation_reason": "CEO 本人确认进入研究"
  }
}
```

上述 params 对应 `m1a_create_direct_issue`；不补造 Signal 或 PotentialIssue。DRI / Agent 提出问题使用 `m1a_open_potential_issue`，payload 指定 origin=direct、signal 或 period_review；仅 CEO 的 `m1a_confirm_strategic_issue` 产生正式议题。红色紧急度必须附理由。

ResearchBrief 的 payload 必须含 title、issue_ref、question、analysis、options、limitations、source_refs。CEO Agent 使用 `m1a_publish_brief`；CEO 以 `m1a_confirm_brief` 提交 brief_ref 与 reason。DRI 的 `m1a_open_meeting` 必须提供 title、objective、material_refs，以及 brief_ref 或 report_ref 二选一。材料修改后旧确认失效。会议纪要仍由双 Agent 分别记录并收拢、DRI 确认，Agreement 由 CEO 确认。

ReviewWindow 的 feedback_deadline 是带时区的时间，与 period 独立。0.2 开窗、重开必须指定未来截止时间。到时后评论、替代、撤回被拒绝；Co-agent 可明确关窗。重开必须提供 title、reason、feedback_deadline，并产生新窗口。模型只生成内容，未来 PCO/Mission 引用由 Runtime 在收拢事务内产生。

| 返回 | 页面处理 |
|---|---|
| committed | 保存 receipt_id/result 中精确引用并刷新 |
| 401 / FORBIDDEN | 重新获取本人身份；不以另一身份代提交 |
| 409 VERSION_CONFLICT / STALE_DEPENDENCY | 读取最新对象并重新核对，重新 prepare；不自动覆盖 |
| 409 REVIEW_DEADLINE_PASSED | 停止评论操作；显示已截止，等待显式关窗或 CEO 重开 |
| 409 PROTOCOL_BINDING_CONFLICT | 版本不一致；不得把 0.1 引用放入 0.2 命令 |
| 422 INVALID_REQUEST | 修正字段或缺失来源，不能补造业务依据 |
| 网络中断、结果不明 | 先查回执；重放同一 idempotency_key 和完整原信封，不重新生成命令 |

## Agent 与版本安装

CEO 使用 method_open_run 创建 M1A 运行，并以 method_attach_run 关联研究根对象。Agent 用自己的令牌读取研究 Context，不允许浏览器指定服务端 Agent 凭据。MethodRun 暂停后，已有研究快照也不能继续通过研究入口读取。

在新隔离 scope 安装 `contracts/method-profile-0.2.json`、精确主契约和 `runtime-method-registry-0.2.json`，再显式将新对象默认创建策略指向 0.2。同一 scope 的 0.1、0.2 注册按确切版本独立读取；协议级冻结同时关闭两个版本写入。历史 0.1 对象保持原绑定、原载荷规则、原回执。恢复原默认策略只影响未来创建，不能原地降级 0.2 对象或删除新历史。

## 验收入口

`uv run python -m acceptance.lifecycle_v02.run --env-file <私有隔离环境> --private <新私有目录> --output <新报告目录>`。

验收只通过合法动作准备业务数据；SQL 用于合成人员/授权夹具和只读物理核对。模型输出受控，不能代表真实模型验收。逐项通过范围和未完成项见验收报告。

对照表中的 Resolution 指收拢动作的完整参数与意见取舍记录，并非新增独立本体类型；本轮新增的独立对象类型是 ResearchBrief。
