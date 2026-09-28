---
status: accepted
date: 2026-09-24
---

# 门动作把目标类型编进动作名，不改激活策略的判权结构

激活策略按动作类型查允许角色，不认目标类型；而 tkos.world 的承诺与确认按目标类型换角色（长期目标与周期目标由 CEO 确认，Mission 由 DRI 确认，核心战役再加 CEO）。我们决定不改内核判权：门动作按目标类型拆成独立动作名（`world_commit_period_goal`、`world_confirm_mission`、`world_confirm_mission_core_battle` 等），角色仍全部写在策略里；创建、修订、刷新状态、记录事件、指派、建关系保持通用动作，「只有责任人能改」这类规则写在服务代码里。

## Considered Options

- 扩展策略内容，按（动作，目标类型）二维判权：被否，要改内核判权与所有已冻结协议共用的策略结构。
- 一个通用 `world_confirm` 在代码里按类型分角色：被否，违反「门的判权写在策略里，不写在代码里」。

## Consequences

- 门的种类变化就是动作清单变化，随协议版本发布。
- 登记里的门表与策略里的 `action_roles` 一一对应，验收矩阵按动作名逐条打。
