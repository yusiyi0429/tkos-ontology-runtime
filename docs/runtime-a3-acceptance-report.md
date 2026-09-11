# Runtime A3：执行责任交接本地验收记录

日期：2026-09-11。**A3 正常 DRI–IC 交接已实现并通过本地验收，A2 与旧交付兼容回归通过。** 结论限于实验 Profile、合成数据和本地 API／PostgreSQL／MinIO；不表示已接入真实企业系统、已完成 Clark A3 页面或已发布部署。

基线 HEAD：`e7d0d6b821f6ca94672c73610a3da85aa5ce6577`；工作分支：`codex/runtime-a3-execution-handover`。本记录生成时尚未提交或推送。Kimi Code 完成初始模型、事件评估及部分模块；额度耗尽后，经用户明确授权，由 Codex 接手实现、审查和验证。独立验收指实现前冻结的契约、矩阵与外部 HTTP／SQL 判定，不声称最终 Codex 编写的代码另经独立开发者复核。

## 完成的业务路径

真实 A2 公司组合激活 → 指定 DRI 创建 ExecutionCommitment → DRI 与 IC 对同一精确版本签认 → DRI 释放执行授权并建立独立验收任命 → DRI 下达 WorkItem → IC 接收 → IC 发布／修改 ExecutionPlan → 提交 v1 → 验收人退回 → IC 回应该退回提交 v2 → 有权人验收通过。

- 公司批准、IC 执行授权、独立验收任命分别建立。DRI、IC、验收人是三位不同自然人；换角色也不能自验或绕过真实证据共同作者限制。
- What 绑定实际生效 Mission／DomainCommitment 的精确版本。Plan 必需且只表达 How，不能借修改计划更换责任、标准、期限或外部依赖，也不增加计划批准步骤。
- 一个 WorkItem 使用同一个 Deliverable 身份，v1／v2 分别保留不可变版本、原始证据 hash、服务器推导的作者和独立评审。v2 必须回应本任务当前的退回记录。
- IC 执行权失效会拒绝后续执行；对已提交内容的验收只受独立任命约束。实际验证了 IC 被撤权或执行窗口到期后，仍有效的验收人可以正常评审，历史作者不改变。
- 交付通过不自动实现 Outcome，也不关闭 MF。P01 中交付已通过，但真实上传的合成事件仅证明 2／3 个客户合格，CEO 单独判断 `not_achieved`；P02 使用新周期、新目标和新证据证明 3／3，单独判断 `achieved`。两次评估都未改写交付及历史 MF 状态。

API 字段、权限和错误约定见 [A3 接口说明](runtime-a3-api.md)。工程边界见 [实现前冻结的工程映射](runtime-a3-engineering.md)。

## A3 完整验收

最终结论来自 `run-full-r2` **同一次完整运行**：14／14 类、55／55 项必验检查、19／19 场景、8／8 项额外质量门禁全部通过；没有把早期局部通过拼接为总通过。

| 编号 | 重点 | 通过检查 |
|---|---|---:|
| A3-01 | 公司批准与执行、验收权分开 | 3／3 |
| A3-02 | 同版、不同自然人、精确任职签认 | 5／5 |
| A3-03 | 当前执行授权与精确任务接收 | 4／4 |
| A3-04 | 指定 IC 的必需计划与 What 约束 | 6／6 |
| A3-05 | v1 退回、v2 回应与证据追溯 | 4／4 |
| A3-06 | 错误 IC、自验、共同作者和任命边界 | 5／5 |
| A3-07 | 旧版本、其他任务退回和重复终态评审 | 3／3 |
| A3-08 | 撤权、自然到期、代次失效和旧回执 | 5／5 |
| A3-09 | 双 API 并发、幂等和单一提交胜者 | 4／4 |
| A3-10 | 首条业务写入后失败的整笔回滚 | 3／3 |
| A3-11 | 交付、Outcome 与 MF 分别判断 | 4／4 |
| A3-12 | 服务端协议归属、通用入口与 prepare 围栏 | 3／3 |
| A3-13 | 来源变更、最终提交顺序与外域权限 | 3／3 |
| A3-14 | API／Worker 重启和无新增外部效果 | 3／3 |

8 项质量门禁为：实际数据库角色／RLS／权限、35 项旧请求序列化、执行权与验收权独立、读取时复核权限、零外部业务派发、写入 capability、不可变历史、前后源码一致。

验收使用真实 HTTP、普通应用数据库角色、原始 S3 字节和版本；两个 API 进程使用不同 PostgreSQL application_name，实际锁等待与提交顺序通过 `pg_blocking_pids` 观察。自然到期在最终业务栅栏处按数据库时钟发生；回滚在提交或评审的首条业务写入后注入失败，并比较治理状态和对象存储快照。生产 checkpoint 默认无操作，不导入测试代码。

成功业务记录经真实 HTTP 产生。owner 只用于新库迁移、合成身份／策略初始化和明确的负向授权夹具，不用 SQL 伪造成功业务链。A3 每个成功回执的 `effect_task_ids=[]`；没有运行外部业务派单。

## 兼容与回归

| 核验 | 实际结果 | 证据 |
|---|---|---|
| A2 完整矩阵 | 18／18 类、66／66 必验检查、27／27 场景、8／8 门禁 | [A2 回归报告](../artifacts/runtime-acceptance/a3-20260911/a2-regression-r1/report.json) |
| 升级前 legacy 交付历史 | 90 项核对通过；原始状态、存储和请求回放不变 | [旧历史保留](../artifacts/runtime-acceptance/a3-20260911/legacy-preserve-r1/legacy-preservation.json) |
| 当前源码新建 legacy 交付闭环 | 20 个真实 HTTP 命令完成 v1／退回／v2／验收；Outcome 未评估、MF 仍在调查 | [新建旧协议流程](../artifacts/runtime-acceptance/a3-20260911/legacy-current-r1/pre-a2-history.json) |
| 升级前真实 A2 激活历史 | 10 个对象、10 个版本、10 个原始回执回放及 27 张历史表保留 | [A2 历史保留](../artifacts/runtime-acceptance/a3-20260911/a2-preserve-r1/history-preservation.json) |
| Python 项目测试 | 604 passed、15 skipped；其中 14 项叙述测试随后单独通过 | [主回归](../artifacts/runtime-acceptance/a3-20260911/project-tests-r3.json)、[叙述回归](../artifacts/runtime-acceptance/a3-20260911/narrative-legacy-r1.json) |
| OpenAPI 与代码质量 | 当前生成结果与快照一致；`git diff --check` 通过 | [交付核对](../artifacts/runtime-acceptance/a3-20260911/delivery-checks.json) |

Python 两次运行合计 **618 个不同测试通过，1 项仍跳过**。剩余项是未配置 `agent_service` fixture 的既有健康检查。叙述测试使用受控模型响应，不代表真实 LLM 连通性验收。

`tests/test_migrations.py` 的管理库创建测试没有执行：隔离环境没有提供它所需的 `TEST_ADMIN_DATABASE_URL`／CREATE DATABASE 管理身份。另行真实验证了空库按 A2 基线初始化、0020 升级、重复迁移跳过、DDL 回滚审查及最终权限目录；不把未执行的管理测试计为通过。本轮未重跑 A1 独立矩阵、浏览器 UI、离线包或生产切换验收。

A2 兼容入口只扩充新 schema 的表清单和两个 A3 state 表 UPDATE 白名单；原 A2 业务用例和期望不变。测试适配还包括提取后的 legacy 派发 helper、读取假连接识别 A3 查询注释，以及 catalog 新增 ExecutionPlan 的条目数，不改变旧交付断言。

## 迁移、环境与可追溯性

唯一应用新迁移的数据库为 `tkos_a1_a2_a3_4dd88c7d58ef4d73`。在新空库运行基线的 12 个迁移后，通过真实旧代码保存历史，再应用 `0020_execution_handover.sql`；第二次 migration runner 返回 `applied=[]`。这证明 runner 幂等，不声明手工重复执行原始 SQL 可成功。

0020 SHA256：`f46815d29d0ab8ef06be4acaa986515b791f01f9b5da3135574706e62cf1343c`。新增 7 张表全部 ENABLE／FORCE RLS；普通应用角色不拥有表、无 superuser／BYPASSRLS，只有两个 A3 state 表可 UPDATE，其余 5 张仅追加，全部无 DELETE 权限。scope、版本、权利和角色主体通过复合外键关联。

最终 116 个源码文件与 A3、A2 两次完整运行的前后清单完全一致。见 [源码 SHA256](acceptance/runtime-a3-source-sha256.json) 与 [机器可读结论](acceptance/runtime-a3-summary.json)。原始运行报告维持各自 `runtime_accepted=false` 的局部报告边界；本文和汇总 JSON 另外组合 A3、A2、旧历史及项目回归，给出 **A3 本地正常交接验收通过**，不改写原始报告。

最终环境核对：原 27 个容器（14 个运行中）的身份、镜像、状态、端口、挂载、网络及 Docker context 不变；原有 19 个待处理部署文件 hash 不变；本轮测试 API／Worker 进程已关闭。没有更改现有演示数据库或 Clark，没有新增生产依赖。见 [保留核对](../artifacts/runtime-acceptance/a3-20260911/preservation-final.json)。

原始失败证据保留：初始真实链路发现了不存在的域字段引用，后续全量验收发现撤销 IC 任职后、本人凭另一剩余角色仍可重放原命令的缺口，均已修复并完整重验。独立客户端修正了既有 `handshake_id` 字段名称和 `{receipt, effects}` 响应包装的读取方式；未降低冻结的 55 项检查。

## 后续边界

本轮仅实现正常交接。替岗、临时代理、验收人变更、暂停／恢复、正式调整、Profile 迁移以及完整反馈／记忆复用闭环仍待后续阶段。合成事件评估只支持明示的客户激活指标，不等于真实经营成效或通用指标体系。

A3 对象、版本、关系、回执和 Outcome 已提供受权限约束的读取接口；原 `/responsibility` 仍按 legacy 解释，对 A3 明确拒绝，A3 使用 `a3_projection`。Clark A3 操作页、工作台交互接入和浏览器验收尚未推进。本轮未提交、推送、制作镜像或部署。

可复现命令和环境约束见 [独立验收入口](../acceptance/execution_a3_independent/README.md)。原始运行工件位于本机忽略的 `artifacts/runtime-acceptance/a3-20260911/`；可提交的摘要保留报告 hash 与源码 hash。私有环境、随机凭据和未脱敏日志位于忽略目录，不进入交付文档或代码。
