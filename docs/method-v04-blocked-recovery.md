# Method 0.4 blocked states and recovery (B3+B4 backend)

本文件记录 **有意阻断** 的 0.4 情形与唯一合法恢复路径。阻断都发生在写事务内，失败不留下业务成功；
不存在“自动重开/自动解决/自动复用旧承诺”的隐式推断。

## 1. 完成的 issue round 不接受问题移交

- 阻断：`m1a_transfer_problem` 只允许 issue 处于 `issue_confirmed` 或 `agreement_formal`；
  `completed` round 返回 `INVALID_STATE`，原 Problem 保持 `open`＋`tracking=true`。
- 恢复：先由 CEO Agent `m1a_reframe_issue`（引用当前 Strategy/Architecture）产生新 round，
  再 `m1a_transfer_problem`。reframe 保留历史 `transferred_problems`/`source_refs`，历史链接仍可见。
- 语义：移交 ≠ 解决；completed round 也不会自动重开以承接跟踪。

## 2. 正式更新使待激活 candidate 失效

- 阻断：candidate 冻结确切的 Strategy/Architecture/LTCO basis。basis 被正式更新后，
  `m1b_commit_candidate`/`m1b_activate_candidates` 返回 `STALE_DEPENDENCY`；
  即使 activation 命令在更新前已 prepare，提交时也会被拒。
- 恢复：
  - 同一 basis 的 LTCO 仍然有效时，可 `m1b_reopen_candidates`（或 `m1b_reopen_window`）显式重开，
    重新 close/resolve/承诺/激活；
  - basis 已变化时，为新的 Strategy/Architecture/LTCO 建立新的 PCO/Mission 与 window 后激活。
- 历史：旧已生效目标保持原 effective revision，不改写；只记录 impact notice。

## 3. 承诺 assignment 被撤销或更换

- 阻断：activation 逐条复查承诺记录的 `assignment_id` 是否仍在当前任职且符合该责任的 DRI/Owner 角色。
  记录被撤销 → `INVALID_STATE`；同一人重新任职但**不重新承诺**也仍然 `INVALID_STATE`。
- 恢复：`m1b_reopen_candidates` → 新 window 绑定**当前** assignment（reopen 自动刷新名单为当前任命，
  或在参数中显式提供新名单）→ close → resolve → 具名 DRI/Owner 用当前 assignment 重新承诺 →
  CEO 整组激活。旧承诺作为历史保留。

## 4. Agreement 签署人/名单/正文变化

- 阻断：撤销签署人任职后正式化 → `FORBIDDEN`；正文/名单/证据变化 → 旧确认不计入。
- 恢复：`m1a_revise_agreement` 以同一 issue round 的当前精确依据重新提交名单/正文，
  全体重新确认；issue 已换 round 时先 `m1a_reframe_issue`，再起草新 Agreement 对象。
- 正式 Agreement 不可改写；历史正式记录不可变。

## 5. 关键未决分歧

- 阻断：candidate 的 `unresolved_differences` 含 `critical=true` 时 `m1b_activate_candidates`
  返回 `INVALID_STATE`（承诺齐全也不放行）。
- 恢复：CEO 显式 `m1b_reopen_candidates`/`m1b_reopen_window` 重开成员轮次，重新 resolve，
  在 candidate 中明确收敛该分歧后再承诺与激活。

## 6. PeriodReview 依据

- 阻断：0.4 本批不接受 `fact_refs`（BusinessFact 未启用）→
  `ACTION_NOT_SUPPORTED_FOR_PROTOCOL`；`target_refs` 与 canonical State 的 `subject_ref`
  必须对象/修订/hash 三元组精确一致，仅有 object_id 相同会被拒。
- 恢复：改用 canonical State refs＋evidence 路径；State 缺失时先 `method_propose_state`/
  `method_confirm_state` 形成 canonical State。

## 7. 私有 workspace/0.2 来源

- 阻断：被 0.2 私有来源链接的 EvidenceAsset 修订，即使调用方拥有 domain read 也不能在正式动作中引用，
  返回 `NOT_FOUND`。
- 恢复：来源 owner 对当前 scene member 执行精确 `source_share`；获得确切版本后再引用。
  单份分享只授权该精确修订，不扩散到其它修订或来源。

## 8. 其他阻断

- window 成员漏项/重复 → `INVALID_REQUEST`（完整性由服务器从同 basis/period 的 draft 集合推导）。
- 非责任人 propose/confirm/commit → `FORBIDDEN` 或 `NOT_FOUND`（后者用于不泄露对象存在性）。
- 陈旧 envelope：prepare 后 basis/对象变化 → `VERSION_CONFLICT`/`STALE_DEPENDENCY`；重新 prepare。
  原 idempotency key 的同 envelope replay 只在原回执仍授权时返回原回执。
- 无证据已知评级、Unknown 无缺口 → `INVALID_REQUEST`。
