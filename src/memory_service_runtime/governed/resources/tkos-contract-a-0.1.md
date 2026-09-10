# 契约包 A：版本基线、公司组合与执行交接 v0.1

日期：2026-09-10。状态：实验协议的开发契约，非已发布的公司制度；本文中新增接口尚未实现。本轮交付契约、数据结构、正反样例与校验结果，随后分 A1／A2／A3 进入 Runtime 实现。

业务范围保持统一核心＋一个业务域先落地；第二个模拟参与域用于验证公司组合。规则与验收由 Codex 负责，契约校验工具和后续 Runtime 实现由 Kimi Code 开发，Clark 伙伴负责业务应用。

前置依据：[六项治理研究](../tkos-runtime-governance-research-20260910/研究结论与技术决策.md)、[Method 评估](../tkos-method-runtime-review-20260910/评估与推进方案.md)。本机 Runtime HEAD `3cd9109d726a9a9069a7960a2f2665ce677785d2`、Clark HEAD `82a94e0a60d6fc946029710288eec77f84bcd3e3`；两者均有既有未提交修改，本轮不覆盖。部署状态未检查。

## 1. 本包固定的规则和实验假设

### 1.1 固定技术不变量

1. 正式身份、权限、对象状态、版本、签认与 Receipt/Audit/Outbox 仍由 Runtime 治理内核负责。
2. MethodProfile 固定业务含义；历史读取、新动作和异步执行检查当前权限，不能借旧 Profile 保留已撤销权利。
3. object_id 不变；正式 content revision 不可变；object_version 是状态头部 CAS；确认记录单独追加。
4. 签认绑定精确版本、责任人任职、判断权与 manifest。改变内容／成员／binding 依赖不能继承旧组合签认。
5. 公司组合生效需要完整集合复查和一次事务切换。草稿、共享建议、正式提交、共同确认、当前生效是不同状态。
6. 公司组合批准不代替 IC 的执行责任接受；任务接收不再生成另一份相同 What 承诺。
7. 旧 URL、缺少版本字段、旧 Worker 或客户端声明 legacy 都不能降级新对象的治理规则。
8. 所有新业务动作最终在现有 scope 栅栏和当前授权下检查；准备接口、缓存和旧回执不是永久操作许可。
9. 交付通过、Outcome 评估和反馈关闭分别判断。任一接口不得自动把另两项改成成功。

### 1.2 实验假设，不等于公司正式规定

- `record_origin=synthetic`；同一个 company scope 下两个必须参加的责任域。
- CEO＋每个当前必需域的指定 DRI 对同一个公司组合 manifest 全体签认、整体生效；初始 A/B 两域为三位不同自然人，新增 C 后为四人，不把初始名单固化进 Profile。不做部分生效或签认继承。
- DRI 与 IC 为不同自然人；本实验使用独立第三人验收，DRI 也不兼任验收者，并禁止执行人／本次交付的可信作者或共同作者自验。生产是否允许 DRI 验收仍待组织确认。
- IC 可调整承诺内 How；结果、标准、硬期限、责任人、对外依赖变更须经过有权变更与必要重签。
- 新增／移除参与域、正式 Submission、binding 依赖或已采用 Profile 的变化使旧待生效组合失效；未提交草稿、无关授权变化和未采用新文档不直接触发重签。
- 一期单周期单活动 Formation；共享容量作为明确的 binding 依据。同一资源不得被多个未协调组合重复消费。
- 未定词义用稳定技术概念 ID 表达并保留来源别名；不宣称 PB/CO 与 PDO 必然等义，也不自行决定 Mission Owner 永远等于 IC。

模拟参数和来源版本见[模拟策略与数据](./模拟策略与数据.md)和[实验 Profile](./synthetic-profile.json)。正式业务启用前需替换或确认这些规则；仅修改状态标签不能把实验 Profile 自动变成公司发布基线。

## 2. 三批工程边界

| 批次 | 交付范围 | 不得冒充完成的部分 |
| --- | --- | --- |
| A1 | 不可变 Profile 登记、服务端 scope/domain/object 协议归属、读取／prepare／execute／证据／Worker 的协议围栏；旧 v0.2 回归 | 没有组合生效、IC 新交接成功或新业务系统接入 |
| A2 | Round、域正式 Submission、完整组合 manifest、四项审查、共同签认与原子生效 | 公司批准不表示 IC 承接；组合可行性不是 LLM 自动证明 |
| A3 | Mission 与执行 What 的明确引用、DRI–IC 同版确认、执行授权与验收任命、任务接收、计划与 v1→退回→v2→验收 | 暂不实现完整替岗、复杂临时代理、全部 MF／经验生命周期 |

A3 第一批只支持正常交接；替岗、暂停、正式调整等未实现的新动作必须明确拒绝。已有 v0.2 任务继续按其原协议处理，不能为了演示新链而放松旧行为。

## 3. A1：服务器决定协议归属

### 3.1 登记结构

以下为逻辑模型，实际数据库命名在实现评审中确认：

| 记录 | 必需字段 | 不变量 |
| --- | --- | --- |
| MethodProfileRevision | profile_id、revision、schema_version、canonical_hash、来源引用、稳定概念、动作／签认规则、action_contract_ref、实验状态 | 内容不可变；同 ID+revision 不得安装不同内容；源码文档新版本不自动替换 |
| ScopeProtocolPolicy | scope_id、namespace/domain 范围、允许的协议／Profile、默认协议、是否允许 legacy 新建、实验运行标记、策略版本 | 服务端安装与当前授权检查；不由请求自由覆盖 |
| ObjectProtocolBinding | scope_id、object_id、protocol_id、contract_version、method_profile_ref、run_id、record_origin、binding_version | 对象创建时写入；既有对象迁移须显式流程；普通改版不能改绑定 |
| ProtocolSupportRegistry | handler 支持的契约与对象类型、只读兼容范围、是否可创建／写入 | 不支持的版本拒绝新动作，不默默调用最新 handler |

Profile 安装与 namespace 策略是受控初始化／管理操作。本包不开放任何用户都能改绑现有对象、声明正式 Profile 或恢复旧写入的公共 API。演练基础身份与协议配置可受控初始化；签认、组合生效和交付结果必须通过真实业务 API 产生。

本包 ProfileCore 以 `action_contract_ref={contract_id:"tkos.contract-a", revision:"0.1", content_sha256}` 绑定本主契约的确切内容；content_sha256 为本文件原始 UTF-8 字节 SHA256，本机路径不进入核心。Profile 的来源清单还保留观察到的视觉工件摘要与表定位信息。仅生成引用不代表已安装或正式批准；后续改规则须发布新的契约／Profile 版本，不能修改旧版本再沿用旧 hash。

### 3.2 新旧请求共存

新增请求字段拟为 `contract_version="tkos.contract-a/0.1"`，其作用是声明客户端理解的动作格式。它不决定服务端对象实际归属。

| 请求情况 | 处理 |
| --- | --- |
| 旧客户端对旧 v0.2 对象 | 继续原严格语义，当前权限仍有效；不得隐式改为 IC 模型 |
| 旧客户端对 A 协议对象，缺新 contract_version | 已授权定位后返回 `PROTOCOL_UPGRADE_REQUIRED`，不自动补新前置 |
| 新客户端声明 legacy，但目标登记为 A | 拒绝降级 |
| 新客户端对已迁移／已停写旧对象 | 遵守服务端迁移围栏，旧对象 ID 不能恢复可写 |
| 创建请求尚无 object_id | 由服务端 namespace 策略确定可创建协议与 Profile；不接受请求自行选择宽松 legacy |
| 相同幂等请求的历史成功重放 | 当前读取权允许时返回历史回执，不再产生新业务效果；不得据此认定当前仍可执行 |
| 用户对目标无读取／动作范围权限 | 延续现有 401/403/404 边界，不泄漏对象属于哪种协议 |

所有正式写入口遵循相同协议归属，包括通用 create/propose、交付专用动作、证据登记、Worker 和将来导入入口。读取保留原字段含义；新增治理说明使用明确附加字段，不把旧 `dri_assignment_id` 改释为 IC。

既有对象启用登记前先清点并显式回填 legacy 绑定；缺失绑定不得自动解释为 legacy。兼容层保持旧请求序列化及 request_hash：缺省的新 `contract_version` 不得被自动写入旧请求 canonical payload。同键同请求的旧回执应仍可按当前读取权重放。

A1 不开放新的可执行经营对象时，也必须证明围栏确实阻止旧入口修改已登记的实验对象；不能只提供一个未被入口调用的工具函数。该反例可使用专用 synthetic 元数据夹具，不能预置成功业务签认。

## 4. A2：公司组合的冻结动作

沿用 `POST /v1/actions` 及 `/v1/actions/prepare` 的公共形式；以下 6 个新 action_type 为本包拟定契约，当前 Runtime 尚不支持。

| action_type | 当前执行主体 | 输入与前置 | 成功结果 |
| --- | --- | --- | --- |
| open_formation_round | CEO | 周期、允许且已安装 Profile、公司正式 Reference、完整初始成员与责任任职；所有需管理范围均有权 | 新 Round；固定 Profile/周期/成员定义，初始化输入代次；没有已生效承诺 |
| amend_formation_round | CEO | 未激活 Round 的精确目标及 CAS、新成员／责任绑定／公司 Reference、变更原因；保持 period 和 Profile 不变 | 追加本轮定义版本、更新成员／输入代次、旧候选失去生效资格；保留旧提交和签认历史，不补造新成员提交 |
| publish_domain_submission | 本域指定 DRI 本人 | Round 精确目标与 CAS、本域草案检查点、结果／Mission／容量及 binding 依赖、本人的明确承诺说明；跨域来源按共享协议可读 | 新不可变 Submission 与本人确认记录；更新该域正式提交指针及 round 输入代次；不激活公司组合 |
| form_company_composition | CEO | Round 当前 CAS、服务端算出的完整最新提交集合、四项审查与依据、未决冲突；不得使用未正式提交草稿 | 不可变组合候选及 manifest；四项审查有真实判断主体；不是全体最终同意 |
| confirm_company_composition | manifest 指定 CEO／DRI 本人 | 精确候选 revision/hash、当前头部 CAS、本人任职、本人的确认说明；组合当前资格有效 | 追加该主体对同版 manifest 的签认；可变审批进度改变，manifest 不变 |
| activate_company_composition | CEO | 精确组合、Round 当前 CAS／输入代次、全部所需签认；事务内完整集合、依赖、权限、当前时间和冲突复核 | 整体建立正式组合基线与对应承诺关联，写 Receipt/Audit/Outbox；不自动形成 IC 已接受状态 |

`prepare` 只计算当时可见依赖与版本，不冻结资源、不持有长事务、不代替业务判断。后续真实请求必须重新检查。fail／unknown 或硬冲突的候选可以保留供审查，但本实验 `confirm_company_composition` 也必须先通过四项判断与硬约束，否则返回 `COMPOSITION_NOT_READY`；激活时再复核一次。离线“全票＋容量不足”只是检验防线的声明输入，不能作为 HTTP 中已经合法产生这些票的证据。

### 4.1 Round 与 Submission

Round 记录稳定身份、period、Profile、成员定义、member_set_version、input_set_version、当前正式提交指针和当前生效基线。成员规则的 revision 与提交进度的可变头部分开，避免每次签字把整份规则改版。

`amend_formation_round` 是本包检验成员新增／移除与公司 Reference 变化的真实入口，不能用任意 SQL 修改代替。它不允许改变已激活 Round、周期或 Profile；这些变更需要后续调整／迁移契约。旧 Submission 可作为历史和候选输入保留，但须按新的完整集合及当前 binding 依据重新判断可用性，新成员必须正式提交，新组合由所需人员重新签认。

Submission 至少包含本域结果标准、结构化 PDO 与 `missions[]` 定义、资源／容量约束、日期、上游和对外依赖、未知项、DRI 任职和提交声明。PDO 首期保存在 Submission 内，以 `submission_ref + pdo_key` 精确引用；每个 Mission 定义含稳定 `mission_key`、结果、边界、验收标准与依赖，不能放入自由格式 terms。

`publish_domain_submission` 在同一事务追加不可变 Submission，并由服务端为各 Mission 生成候选身份／版本，保存 `origin_submission_ref + mission_key` 的来源映射。正式重提保持可追溯的稳定业务身份，新候选版本不覆盖旧版本；Submission 的 hash 只覆盖其内含定义，不再反向包含所生成 Mission 的 hash，以免循环依赖。`activate_company_composition` 才整体切换相关 Mission／域承诺的生效引用。普通 `create_object`／`propose_revision` 不独立创建、修订或激活 Mission，Mission 不等同 WorkItem。

同周期不同域都出现“3”不能直接相加。容量约束要有资源 ID、周期、单位、可分配数、已保留数及来源；因果贡献与可加总数量分开。来自用户输入的数字不自动成为已核验的事实。

### 4.2 CompositionManifest 的最小字段

```text
manifest_schema_version = "tkos.composition-manifest/0.1"
scope_id, company_id, round_id, period_id
method_profile_ref = {profile_id, revision, canonical_hash}
member_set_version, input_set_version
company_reference_ref = {object_id, revision_id, payload_hash}
members[] = {domain_id, dri_assignment_id, dri_principal_id,
             submission_ref:{object_id, revision_id, payload_hash}}
binding_dependencies[] = {dependency_id, relation_type, source_ref,
                          constraint:{kind,resource_id,period_id,unit,required,available}}
judgments = {coverage, coherence, feasibility, tradeoff}
required_signers[] = {principal_id, assignment_id, responsibility_role}
hash_scheme = "tkos-json-v1"
manifest_hash
```

四项 judgment 各含 conclusion、reason、evidence_refs、judge_principal_id；conclusion 使用 pass／fail／unknown。不能漏项，也不能把 unknown 当 pass。当前实验策略要求 CEO 对四项判断负责；算法可指出确定冲突，不能替 CEO 凭空填写正式判断。

哈希排除 `manifest_hash` 本身，且不得包含会随签认增加的回执、签认列表或对象头 CAS。规范化采用服务器固定的 UTF-8、键排序、紧凑 JSON，禁止 NaN/Infinity；数量使用非负整数或另行版本化的十进制字符串，不由客户端浮点实现决定。该方案不宣称符合 RFC 8785 JCS。

成员、签认人、依赖引用必须唯一；排序规则固定。exact revision/hash 用来识别所签版本；运行中的真实存在、当前有效、完整集合和权限检查只能由 Runtime 查权威数据，离线 schema 不能证明。

容量项表达本组合对同一资源池的汇总约束：`resource_id + period_id + unit` 唯一，不能分成多个各自不过限的重复项绕过总量；Runtime 负责从完整域提交汇总需求，离线工具只检查给定汇总数。签认集合校验须另给预期 Composition 的 object/revision/hash，不从第一张票反推正在签哪个对象；缺少集合或目标引用时不能报告签认齐全。

### 4.3 完整校验与并发

激活事务重新计算所需域集合，要求与 manifest **相等**。新增 C 即使不改变原 A/B 的 revision，也使旧 manifest 失效。依赖闭包由服务器规则计算，有明确上限；超限返回错误，不截断后继续确认。

scope 栅栏保护成员变更、正式提交、当前授权、资源约束和激活。所有相关入口必须参加；只锁原对象行不能替代完整集合。自然到期独立检查 DB 当前时钟；auth_epoch 未变不意味着任职仍有效。

公司动作使用受限的多域授权与依赖收集器：逐项核对控制域、参与域、准确任职与可读组合内容，并在最终准入点重查。现有单域 assignment／same_domain／finish 检查继续服务旧协议，不能通过全局删除单域限制来实现公司组合。新无 target 动作也须明确分派，不能误入现有 revoke_assignment 分支。

无关撤权导致 auth_epoch 变化时，重查受影响资格而非强制所有组合重签。实质输入变化则返回需重新审查，不能自动刷新 manifest 并沿用人的旧同意。

首次激活与后续 Adjustment 分开。旧 active 基线在新候选形成期间仍有历史身份；发生暂停或撤权后，未来可执行性按当前规则判断。A 包尚未实现的调整途径明确拒绝，不用旧同域 ManagementAdjustment 伪装为跨域新组合调整。

## 5. A3：DRI–IC 正常交接

### 5.1 逻辑对象

| 对象 | 本包要求 |
| --- | --- |
| Mission | 结构化结果单元，含上游组合／域承诺精确引用、结果标准、边界、责任 DRI；独立身份不等于另建服务 |
| ExecutionCommitment | 同一 Mission 下 DRI 与 IC 对 What／边界的同版承诺，绑定不同自然人的任职 |
| ExecutionAuthority | 对该承诺的当前执行资格及 execution_epoch，允许动作、时间、范围、激活回执 |
| WorkItem / WorkReceipt | WorkItem 是不超出 What 的交付单元；WorkReceipt 记录 IC 接收精确任务版本，不复制 What |
| ExecutionPlan | IC 的 How；结构化步骤／里程碑及其边界引用；不能通过任意 terms 改写标准、责任或外部依赖 |
| AcceptanceAppointment | 当前验收人和标准版本，独立于执行授权；至少能解释是谁以何种任命评审 |

具体持久化可共享 governed entity/revision 和受限关联表。不能为了少建对象就把 Mission、Commitment、Plan、Task 的状态混为一个字段。

### 5.2 复用既有动作名，扩展受限 handler

正常链为：

1. 组合生效后形成 Mission 与域承诺的精确关联，不能直接把组合内容作为已接受的执行责任。
2. `create_object` 的白名单为 ExecutionCommitment 草案、受限 ExecutionPlan、已有执行授权下的 WorkItem；`propose_revision` 仅限未激活 ExecutionCommitment 与受限 ExecutionPlan。两者均检查类型、当前责任与 ProtocolBinding。Mission 已由 A2 控制候选与生效，已有 WorkItem/Deliverable 禁止通用改版。
3. `accept_commitment` 在新 handler 中限定指定 DRI 和 IC 同一内容确认；当前旧的 DOMAIN_DRI／MISSION_DRI 两方规则不能原样充当新语义。
4. `activate_commitment` 的新 handler 在完整前置下释放 ExecutionAuthority；由实验策略指定的 DRI 执行，保留双方同版确认及公司基线检查。
5. 创建 WorkItem，`accept_work_item` 由指定 IC 接收；新增标准或提前硬期限等不能以接收任务暗加责任。
6. IC 发布 ExecutionPlan；合法的 `create_object`／`propose_revision` 原子追加不可变 Plan revision 并更新当前计划指针，无须再新增批准动作。A3 正常交付前要求已有当前计划，How 修改只限合法边界，越界必须返回正式变更流程；不能把未实现 Plan 列为 A3 全部通过。
7. `submit_deliverable` v1 → `review_deliverable` 退回 → v2 → 有权验收；保留证据 hash、提交作者、标准版本、当前任命与回应的退回记录。

执行入口检查 ExecutionAuthority；评审入口检查 AcceptanceAppointment。本轮 A3 范围仅为固定且持续有效验收任命的正常链；任命失效后的执行连续性与替换规则另立后续契约，不据此宣称已处理。实现时保留两条独立校验路径，不能沿用旧 validate_work 的耦合自行决定新制度。验收本身必须由当前有效且不自验的人执行。

同一终结交付评审不得因重试或换人重复出现。收到旧成功回执只表示过去已发生；不恢复旧执行代次，不重新排队，不证明当前 Outcome 达成。

## 6. 错误与回执的共同约定

沿用当前标准错误结构和权限边界；下列是新增建议码，不是现有 API 已返回的事实。HTTP 状态在实现前核对现有 errors.py，原则为请求形状不合法 422、合法请求的版本／状态冲突 409、权限仍按原 401/403/404。

| 原因 | 建议码／处理 |
| --- | --- |
| 新对象用旧契约或请求降级 | PROTOCOL_UPGRADE_REQUIRED |
| 未安装／不允许／不支持的 Profile 或 handler | METHOD_PROFILE_UNSUPPORTED / PROTOCOL_NOT_SUPPORTED |
| 同 Profile 版本安装不同内容 | PROFILE_CONTENT_CONFLICT |
| CAS 改变 | 沿用 VERSION_CONFLICT |
| 成员、提交、binding 依赖不等于 manifest | COMPOSITION_INPUT_CHANGED |
| 四项审查失败／未知或硬依赖冲突 | COMPOSITION_NOT_READY |
| 缺同版确认／责任人资格已失效 | CONFIRMATION_INCOMPLETE / FORBIDDEN，按可披露程度处理 |
| 旧执行代次、未释放或未接收任务 | EXECUTION_AUTHORITY_STALE / INVALID_STATE |
| 新协议尚不支持的替岗／调整 | ACTION_NOT_SUPPORTED_FOR_PROTOCOL |

成功回执至少能追溯 contract_version、Profile、目标及依赖版本、实际 actor/assignment、判断／签认记录、变化后的 object_version、相关执行意图。为兼容旧客户端可放在明确的 result.governance 元数据中；不要改变旧字段原义。

包 A 的生效和执行授权仅改变 Runtime 治理状态，新增动作不自动预约对外经营操作，`effect_task_ids=[]`；审计／事务事件不等于外部 dispatch。旧协议已有效果仍按原契约与当前权限处理；新协议的外部效果待单独定义消费方、幂等与暂停连续性后接入。

拒绝动作不产生成功回执和部分业务变更。是否新增独立拒绝审计另列设计，不能把当前错误响应冒充已持久化 Receipt。

## 7. 契约校验与真实验收是两层交付

本轮可执行契约检查：Profile 与 manifest 字段、枚举、严格类型、额外字段拒绝、精确引用、唯一性、签认集合、hash、4 项判断、周期／单位等约束；使用 synthetic 正例和负例验证。缺服务器状态时，只能证明样例内部一致。

后续独立 Runtime 验收：用真实 HTTP 创建业务记录，SQL/证据/回执核对；控制两个连接的提交顺序，验证新成员、自然到期、签认前后改版、旧接口降级和恢复；不能直接 SQL 种入成功签名代替调用。

对应详细场景见[独立验收契约](./独立验收契约.md)。A1 只报告 `contract_a1_accepted`，A2/A3 分别报告，不复用旧 `runtime_accepted=true` 宣称全链已通过。

## 8. Kimi 开发交付与保护范围

本轮 Kimi 先完成契约校验工具、正反样例与技术预审。允许使用 Runtime 已有 Python/Pydantic 环境，但不修改 Runtime 依赖、源码、数据库、容器或 Clark。

后续 A1 实现任务以本契约和独立验收门槛为输入：先固定实际代码快照和已有用户修改清单，限定允许改动文件；如需 worktree 使用临时隔离目录，验收后只迁入本轮批准变更。不创建新的长期冗余项目。

Kimi 自测后由 Codex 按契约另做反例、源码与真实 HTTP 验收；提交、推送、部署继续按用户后续指令分别执行。工作台仍用于核查，业务写交互由 Clark 伙伴承接。

本包仍待组织确认：正式 MethodProfile、成员/签认范围、DRI/IC/验收兼任关系、资源取舍、暂停和调整权。这些不会阻止合成数据的契约与技术验证，也不能被合成测试自动确认成正式制度。
