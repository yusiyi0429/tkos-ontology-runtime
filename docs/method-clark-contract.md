# Clark 接入契约草稿：M1A＋M1B Method API

本轮交付底座 API。Clark 页面、生产身份映射、迁移和部署另行安排；本文件不声称 Clark 已完成新协议接入。应用端页面与流程以 Clark 仓库为准。

## 责任分界

Clark 负责展示当前依据、草稿、窗口意见与可执行动作，并推进编排。Runtime 保存权威业务对象及不可变版本，验证当前身份、具体责任、精确依赖、状态、授权、原始证据与每次正式写入；最终以 Runtime 回执及读接口为准。

现有 Clark 的本地“批准并下达”、战略判断、任务或记忆对象，不因名称相似就等同于新 Method 的 CEO Agreement、正式战略更新、PCO/Mission 整组确认或执行授权。接入时需通过下面的 typed action 获得成功回执，再更新应用投影。应用层的临时 optimistic state 不能成为对外宣称正式生效的依据。

| Clark 功能意图 | Runtime 对应 | 必须展示或保留 |
| --- | --- | --- |
| 战略议题与研究工作区 | M1A Signal/PotentialIssue/StrategicIssue、研究与会议动作 | 研究 DRI、CEO/DRI Agent 身份、精确材料和阶段 |
| 预审、上会与会议纪要 | M1A 报告质量预审、MeetingRound、双纪要、差异核对 | 预审针对的报告版本；DRI 确认的最终纪要版本 |
| CEO 战略决定 | Agreement 确认、调整判断、更新方案审查、正式更新确认 | 三个独立结果：纪要确认、Agreement 确认、战略更新确认 |
| 战略地图 | 已生效 Strategy 的 `map.units[]` | 稳定 unit_id、所用 Strategy 版本、来源回执；没有独立 WarMap |
| 原子经营事实 | BusinessFact 写入／修正及查询 | 指标、时间、主体版本、值、单位、原始来源；修正保留历史 |
| 周期复盘与建议 | PeriodReview、LTCOReviewAdvice | Agent 分析标签、采用的目标和事实版本；无需设计复盘审批按钮 |
| CEO 审视长期目标 | LTCO 退回／修订／确认 | 当前有效版本与新草稿区别，CEO 反馈及个人 Agent 响应 |
| PCO＋Mission 共同核对 | ReviewWindow、人的 ReviewRecord、个人 Agent 分析 | 固定成员版本、当前参与资格、本人意见撤回／替代、窗口状态 |
| 正式收拢 | CandidateSet 及逐意见 disposition | 全部有效意见的取舍、未解决差异、候选精确版本集合 |
| CEO 定稿／再次核对 | confirm_candidates／reopen_candidates | 整组确认结果，或新窗口及重开原因；不等待旧 A2 全体 DRI 签认 |
| 下一周期分析 | PeriodReview 引用历史已确认目标和新事实 | 可追溯的报告代次，经验可作为分析输入但不会自动改目标 |
| DRI 执行入口 | 新 Mission 的下游承接投影 | 已确认 Mission 及 PCO；缺少 M2 责任约定时显示待承接，不自动发执行权 |

## 身份和调用

每个人与每个 Agent 使用各自的服务端身份。生产映射完成前可通过合成身份验证流程，但不能用一个共用服务 token 代替所有人的评论和正式决定。不能把用户 ID 作为 payload 自报字段绕过 bearer credential 身份。

所有正式调用包含 `contract_version: "tkos.method/0.1"`，并使用唯一幂等键。对象本身的协议来自服务端登记；声明新版本不能升级一个旧对象，省略版本也不能把新版对象降级到旧流程。

下面的适配函数将 prepare 和 commit 分开，便于应用在发送正式请求前持久化已准备的命令，从而在超时后重发同一份信封。调用者传入自己的 bearer token；示例不保存或输出 token。完整字段和响应以服务当前 `/openapi.json` 及冻结 API 文档为准。

```python
from uuid import uuid4
import httpx


def prepare_method_action(base_url, token, action_type, params, *, target=None,
                          idempotency_key=None, request_reason="Clark workflow",
                          run_ref=None, step_key=None):
    envelope = {
        "action_type": action_type,
        "contract_version": "tkos.method/0.1",
        "idempotency_key": idempotency_key or str(uuid4()),
        "reason": request_reason,
        "params": params,
        "expected_versions": [],
        "target": target,  # null for a root-creation action
    }
    if run_ref is not None:
        envelope["run_ref"] = run_ref
    if step_key is not None:
        envelope["step_key"] = step_key
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        prepared = client.post("/v1/actions/prepare", json=envelope)
        prepared.raise_for_status()
        versions = prepared.json()
    envelope["expected_versions"] = versions["expected_versions"]
    envelope["target"] = versions["target"]
    return envelope  # Persist this exact envelope before calling commit.


def commit_method_action(base_url, token, envelope):
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        result = client.post("/v1/actions", json=envelope)
        result.raise_for_status()
        return result.json()

```

网络超时后的幂等重试必须重发**同一份已提交 envelope**，包括幂等键和 CAS 条件；不能重新 prepare 后套用旧幂等键。Runtime 可以返回原回执，但仍检查当前读取及相关权限。明确得到版本冲突或业务拒绝后，读取最新上下文，必要时提示人重新判断，并使用新幂等键启动新尝试。

`prepare` 只解析权限与当前依赖，不预约资格，也不替人批准。`expected_version` 是对象 CAS 版本；它可因评论或状态转换增长，不能用内容 revision_id 的序号代替。

## M1B 最小调用顺序

1. CEO Agent `m1b_propose_ltco`，CEO `m1b_confirm_ltco`。首次建立 LTCO 可没有历史复盘；已有运行周期使用 `m1b_generate_review → m1b_advise_ltco` 并让 CEO 审视。
2. Co-agent `m1b_draft_pco`，随后一个或多个 `m1b_draft_mission`；Mission 使用刚获得的 PCO 精确版本。
3. Co-agent `m1b_open_window`，指定一个 PCO 和完整 Mission 集合的精确引用，以及 `{principal_id, assignment_id, personal_agent_id?}` 参与列表。
4. 人以各自身份调用 `m1b_comment`；可在 `replaces_record_id` 传自己的有效意见 ID，或调用 `m1b_withdraw_comment`。个人 Agent 使用 `m1b_assist_review`，不代人发意见。
5. Co-agent `m1b_close_window` 获得 `frozen_opinion_ids`，然后 `m1b_resolve_window` 对每个 ID 提交一次 disposition，返回 CandidateSet 及原子生成的完整成员版本。
6. CEO `m1b_confirm_candidates` 确认整个集合；或 `m1b_reopen_candidates` 返回新 ReviewWindow，再从评论阶段继续。

初次创建对象的结果提供 `{object_id, revision_id, payload_hash}`，适合作为后续业务正文中的精确引用。对有目标的动作还要从当前读接口获得 `expected_version`；`prepare` 返回完整依赖 CAS 集合。窗口动作返回 `review_record_id` 或 `frozen_opinion_ids`；这些协作记录 ID 不能误作业务内容 revision_id。

## 变化、恢复和页面表现

- 最新 Strategy、工作草稿与已确认经营目标可以同时存在。旧已确认目标继续有效；显示影响提示及其原战略版本，避免把新战略自动替换到旧目标正文中。
- 尚未确认的集合采用旧战略时，Runtime 拒绝确认。CEO 先确认新的 LTCO，再带成对的 `rebase_strategy_ref/rebase_ltco_ref` 重开窗口，明确组织重新核对。
- 窗口关闭后，编辑框应跟随拒绝结果刷新；不能把迟到意见强行追加到已封存的有效意见集合中。
- 运行关联记录支持暂停、恢复、步骤尝试和查错。新建业务根对象可在动作信封中提供精确 `run_ref` 和可选 `step_key`；`method_open_run` 自身必须是独立根，不接受这两个字段；目标对象后续写入继承其已有运行。历史来源引用不会使下一周期复盘自动归属旧运行，也不会因旧运行暂停而阻止独立分析。显式归属的运行暂停时，该报告的再生成等写入被拒绝。应用须以正式回执判断已完成步骤，避免进程重启后重复生成候选或重复确认。
- Context Pack 应按当前 actor、用途和阶段获取。窗口参与资格只覆盖必要目标，不使其他域的原始证据自动可读。对排除项显示“当前用途或权限不适用”，不在错误提示中泄露内容。
- 交付通过、Outcome 达成和 MF 关闭保持分别判断。新版 Mission 定稿不能直接映射为旧交付验收成功、Outcome 达成或 MF 关闭。

本轮交接应随附真实 HTTP 调用样例、受控身份清单、独立验收结果、OpenAPI 快照及已知限制。页面和正式身份上线后，再做 Clark 端到端验收。
