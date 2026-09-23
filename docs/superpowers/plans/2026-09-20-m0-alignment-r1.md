# M0 本体对齐 · R1 前置增量（tkos.method/0.5）实施计划

> **已被取代（2026-09-23 补注）：** 本计划未实施。实际交付的 `tkos.method/0.5` 是 [`2026-09-22-method-05-ontology-v07.md`](2026-09-22-method-05-ontology-v07.md)（本体 v0.7 对齐，契约 [`docs/contracts/tkos-method-0.5.md`](../../contracts/tkos-method-0.5.md)），内容与本文不同；本文里的 `tkos.method/0.5`（议题主 Scope、Scope 级 State、证据锚点）不是已实现的 0.5。正文保持原样，只作历史记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动 0.1–0.4 任何既有语义的前提下，交付 `tkos.method/0.5` 增量与配套读取面，使 Runtime 能回答《TKOS 本体图结构梳理 v0.1》（M0）的八个能力问题中当前答不了的三个（战场健康、按战场列议题、证据出生即归框），并把 M0 的关系表变成服务端可查询的注册表，供治理工作台与 Clark 共用。

**Architecture:** 沿用「新契约版本 = 新 scope 显式启用、旧对象保留原绑定」的既有机制：新增 `method_v05_models.py` / `method_v05.py` 只覆盖与 0.4 有差异的三处（议题主 Scope、Scope 级 OperatingState、证据锚点），其余动作原样委托 `method_v04`。读取侧新增关系注册表模块、Scope/Company 可寻址读取器、议题生命周期投影；workspace 0.1 会议场景追加 `action_status` 事件。所有正式写入仍走同一治理事务，不引入第二套状态或权限。

**Tech Stack:** Python 3.12 + FastAPI + Pydantic v2（`extra="forbid"`、Strict 类型）+ psycopg + PostgreSQL 17（RLS、append-only 迁移）；前端 React + TypeScript + Vite + vitest（`workbench/dashboard`）；验收脚本走真实 HTTP + PG（`acceptance/`）。

**Spec:** 本计划的依据是三份材料，执行者先读：
- M0：飞书文档《TKOS 本体图结构梳理 v0.1》（`https://tokenking.feishu.cn/docx/LyGjdMThGoLfXmxvrztcXGe7nUb`，2026-09-20 读取 revision 7）。
- 产品路线图《TKOS 产品路线图｜Web First》（`https://tokenking.feishu.cn/docx/RVtOdWTXJoNGSExfiGvcI4CtnGW`，revision 357），尤其 04 修订的 MVP 验收口径。
- 仓库内冻结契约 `docs/contracts/tkos-method-0.4.md` 与 `docs/contracts/tkos-workspace-0.2.md`（0.5 只做增量，不改写）。

## 全局约束

- 迁移严格 append-only：新增 `0029_method_v05.sql`、`0030_workspace_action_status.sql`，同步更新 `tests/test_migrations.py` 的完整清单；不改已应用文件。
- `SUPPORTED_PROTOCOL_CONTRACTS` 是编译期全集；0.5 必须加入该集合并配 profile SHA 绑定与迁移门禁，否则任何 0.5 请求返回 `PROTOCOL_NOT_SUPPORTED`。
- 0.1–0.4 的模型、执行器、读取语义原样保留：`method_v04_models.py`、`method_v04.py` 不得被修改（只允许被导入）。对 0.4 对象的行为差异为零，靠回归测试证明。
- 治理代码不得中途 commit、切换连接或跨事务复用 `AuthContext`（见 `governed/db.py` 顶部）。
- 测试数据一律随机 scope，禁止 `local/local-org`；业务记录只经治理路径播种。`tests/conftest.py` 在收集阶段就要求 `DATABASE_URL`，纯模型测试也不例外；`tests/dashboard` 用 `PYTHONPATH=src pytest tests/dashboard --confcutdir=tests/dashboard` 单独跑。
- 文档与代码注释中文为主；提交信息用 `feat(method): ...`、`feat(runtime): ...`、`test(...)`、`docs: ...`，末尾附 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。
- 不打印或提交凭据、`.runtime-acceptance/`、原始验收产物。汇报时把「已执行的检查」「跳过」「未验证边界」分开写。

## 计划假设（M0 裁决前提）

以下四条是本计划的前提，来自 2026-09-20 的对照评估。任何一条被否决，只影响标注的任务，其余任务不变。

| 编号 | 假设 | 若否决则影响 |
| --- | --- | --- |
| A1 | OperatingState 的主体可以是 Scope（Battlefield 或 Domain 的稳定 `unit_id`），由该 Scope 映射授权域的唯一当前 DOMAIN_DRI 确认；Agent 只能推荐，不自动汇总下层 RAG。这与 0.4 契约 §6 的本意（禁止自动聚合）一致，只放开「人确认的 Scope 级状态」。 | 删除 Task 6，Task 10 的 `latest_state` 字段改为固定 `not_applicable` |
| A2 | StrategicIssue 可选记录唯一主 Scope（`primary_scope_id`），存在正式 Architecture 时必须能在其中找到；公司级议题允许为空。 | 删除 Task 5，Task 10 的 issues 列表不做 |
| A3 | 0.5 scope 的证据上传必须声明锚点（company / scope / object 三选一）；私有来源（workspace 0.2）保持无锚点，带入议题时以挂锚会议场景（workspace 0.1）落库。 | 删除 Task 7；EvidenceAsset 在注册表中标 `not_implemented` |
| A4 | R1 的「会议行动」留在 workspace 0.1 场景记录，只补完成状态事件，不提前建 WorkPackage。 | 删除 Task 12 |

另外两条不是假设而是既定规则：0.4 已冻结，所有变化都进 0.5 新 scope；Clark 是伙伴仓库，本计划只交付它需要的读取接口与契约文档，不改 Clark 代码。

## 文件结构

新建：

| 路径 | 职责 |
| --- | --- |
| `docs/contracts/tkos-method-0.5.md` | 0.5 冻结契约文本（SHA 被 profile 与迁移门禁固定） |
| `docs/contracts/method-profile-0.5.json` | 0.5 profile（`canonical_hash` 由代码生成） |
| `docs/runtime-method-registry-0.5.json` | 0.5 动作与对象类型登记模板 |
| `src/memory_service_runtime/governed/method_v05_profile.py` | 0.5 契约身份与 SHA 绑定 |
| `src/memory_service_runtime/governed/method_v05_models.py` | 0.5 严格模型：仅覆盖有差异的 payload/params |
| `src/memory_service_runtime/governed/method_v05.py` | 0.5 执行增量：议题主 Scope 校验、Scope 级 State；其余委托 0.4 |
| `src/memory_service_runtime/governed/evidence_anchor.py` | 证据锚点模型与解析 |
| `src/memory_service_runtime/governed/relations.py` | 关系注册表（M0 4.2 → 运行时字段） |
| `src/memory_service_runtime/governed/scope_readers.py` | Company / Scope 可寻址读取器 |
| `src/memory_service_runtime/governed/issue_lifecycle.py` | 议题生命周期投影（纯函数 + 输入采集） |
| `src/memory_service_app/migrations/0029_method_v05.sql` | 0.5 绑定门禁 + 两个表达式索引 |
| `src/memory_service_app/migrations/0030_workspace_action_status.sql` | 会议 `action_status` 事件种类 |
| `tests/test_method_v05_models.py`、`tests/test_method_v05_scope_state.py`、`tests/test_evidence_anchor.py`、`tests/test_relations.py`、`tests/test_issue_lifecycle.py`、`tests/dashboard/test_scope_readers.py` | 对应单元/契约测试 |
| `acceptance/method_v05/{__init__,fixture,flow,run}.py` | 真实 HTTP/PG 独立验收 |
| `docs/method-05-implementation-status.md` | 实施与验收状态 |

修改（精确位置见各任务）：`protocol.py`、`method_models.py`、`models.py`、`method_service.py`、`method_access.py`、`method_readers.py`、`governance.py`、`workbench.py`、`profile.py`、`control.py`、`method_map.py`、`dashboard.py`、`dashboard_routes.py`、`routes.py`、`workspace_models.py`、`workspace_service.py`、`workspace_readers.py`、`src/memory_service_app/dashboard.py`、`src/memory_service_app/governance_commands.py`、`tests/test_migrations.py`、`tests/dashboard/test_dashboard_facade.py`、`workbench/dashboard/src/lib/{ontology,labels,api,types}.ts`、`workbench/dashboard/src/components/{OntologyMap,TypeInfoCard}.tsx`、`workbench/dashboard/src/App.tsx`、`CONTEXT.md`、`README.md`、`docs/reviews/2026-09-17-business-data/inventory-runtime-map.csv`。

## 任务顺序与依赖

```
Task 0 (M0 裁决文本)  ─┐
Task 1 契约与 profile ──┤
Task 2 模型与注册表 ────┤──► Task 4 执行器骨架与版本分发 ──► Task 5 议题主 Scope
Task 3 迁移 0029 ───────┘                                  └► Task 6 Scope 级 State
Task 7 证据锚点（依赖 Task 3、4）
Task 8 关系注册表模块 ──► Task 9 注册表接入看板 ──► Task 13 前端
Task 10 Scope/Company 读取器（依赖 Task 5、6、7）
Task 11 议题生命周期投影（依赖 Task 10 的 SQL 形态；可与 Task 12 并行）
Task 12 会议行动状态（独立，只依赖迁移编号）
Task 14 独立验收（依赖 Task 1–12）
Task 15 文档、快照重核、OpenAPI 导出（最后）
```

Task 0 不产生代码；Task 1–3 可并行；Task 8 与 Task 12 可与 Task 4–7 并行。

---

### Task 0: M0 裁决文本与词汇表（非代码，评审会输入）

**Files:**
- Modify: `CONTEXT.md`（末尾追加 4 条术语）
- 外部：飞书 M0 文档《TKOS 本体图结构梳理 v0.1》第四、五、七节（由用户或 DRI 在评审会后落笔；本任务只准备逐字文本）

**Interfaces:**
- Produces: 四条裁决结论（A1–A4）与 M0 新增的 5 条关系行，是 Task 1 契约文本的直接来源。

- [ ] **Step 1: 准备提交评审会的 M0 修订文本（逐字）**

把下面的文本作为评审材料发给人豪、明华与 04：

```text
【M0 v0.1 → v0.2 修订提案，2026-09-20】

一、类表（4.1）增加/修改三行
- OperatingState：主体列改为「LTCO / PCO / Mission / Scope」。Scope 级 State 由该 Scope 映射授权域的唯一当前 DRI 确认；Agent 只推荐；不对下层 RAG 求平均；写入门保持「Anchor 级（推荐 + 有权人确认）」，而非过程级。
- StrategicIssue：新增属性 primary_scope_id（可空）。存在正式 Architecture 时必须能在其中找到；公司级议题允许为空。
- MeetingRecord（会议核对稿）：Evidence 子类，anchoredTo 指向 StrategicIssue，由主责 DRI 发布；其中 CEO 判断项才进入 Agreement。

二、关系表（4.2）增加五行
| 谓词 | 起点 → 终点 | 基数 | 依据 |
| concerns | StrategicIssue → Scope | N:1，可空 | CEO 首页战场卡按战场列议题 |
| anchoredTo | MeetingRecord → StrategicIssue | N:1 必填 | 修正一；workspace 0.1 场景强制挂锚 |
| feeds | CeoJudgment（核对稿条目）→ Agreement | N:1 | 概念版「CEO 只接五类判断」 |
| assesses | OperatingState → Scope | N:1 + asOf 唯一 | 战场健康（手标 = 推荐后 DRI 确认） |
| hasStatus | MeetingAction → {accepted, done, verified, dropped} | 1:1 最新 | 议题生命周期「进入执行 → 结果已验证」 |

三、第五节补一句
私有来源（会议录音、个人文档、本人选定对话）在被带入议题之前不是 Evidence，不要求锚点；带入议题的那一刻以挂锚会议场景入库，anchoredTo 该议题。

四、第七节缺口回应
- 缺口一 Period：本轮不建节点；PCO/Review 的 period 仍为起止值。待 DRI 确认周期制度后决定。
- 缺口二 横向依赖：R2 到期（Co-agent 需可查依赖边）；R1 用 Evidence 记录依赖事实。
- 缺口三 Agent 身份：R2 到期（数字员工矩阵治理需 Skill 版本与工具权限）；Runtime 已有 agent 类型主体、四种 Agent 角色和绑定表。
- 新增：Play 在路线图 R3 未出现；按最小承诺，暂不建类，待 R3 明确。

五、术语对齐
Domain Outcome（路线图）= Scope 级 LTCO/PCO（M0）= primary_scope_id 指向 Domain 的 LTCO/PCO（Runtime）。
```

- [ ] **Step 2: 追加 CONTEXT.md 术语**

在 `CONTEXT.md` 末尾追加：

```markdown
**Scope（业务范围）**：
责任结构中的 Battlefield 或 Domain 定义项，以所属 Architecture 对象加稳定 `unit_id` 寻址。它是 LTCO、PCO、Mission、Scope 级经营状态、证据锚点的挂载点，本身没有独立生命周期。
_Avoid_: 授权域、部门

**Scope 级经营状态**：
以某个 Scope 为主体、由该 Scope 映射授权域的当前 DRI 确认的经营状况。它不是下层目标状态的平均值，也不自动生成。
_Avoid_: 战场健康自动评分、聚合 RAG

**证据锚点**：
证据上传时声明的唯一主归属：公司、某个 Scope，或某个确切对象版本。跨域影响不改变锚点。
_Avoid_: 事后分拣、多锚点

**Domain Outcome**：
路线图用语，指主 Scope 为 Domain 的 LTCO 或 PCO；Runtime 没有同名对象。
_Avoid_: 独立 Outcome 对象、PDO
```

- [ ] **Step 3: 记录裁决结果**

评审会后，把 A1–A4 的结论（通过 / 否决 / 修改）写进 `docs/method-05-implementation-status.md` 的「M0 裁决」小节（Task 15 建该文件；先在本任务留一份 `docs/superpowers/plans/2026-09-20-m0-decisions.md`，内容为上面五节加每条的结论与日期）。

- [ ] **Step 4: 提交**

```bash
git add CONTEXT.md docs/superpowers/plans/2026-09-20-m0-decisions.md
git commit -m "docs: record M0 v0.2 alignment decisions and Scope glossary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 1: tkos.method/0.5 契约文本与 profile 绑定

**Files:**
- Create: `docs/contracts/tkos-method-0.5.md`
- Create: `src/memory_service_runtime/governed/method_v05_profile.py`
- Create: `docs/contracts/method-profile-0.5.json`（由代码生成）
- Create: `docs/runtime-method-registry-0.5.json`
- Test: `tests/test_method_v05_models.py`（本任务只写 pin 测试；Task 2 追加模型测试）

**Interfaces:**
- Produces: `method_v05_profile.CONTRACT_VERSION == "tkos.method/0.5"`、`SCHEMA_VERSION == "tkos.method-profile/0.5"`、`CONTRACT_SHA256`、`validate(data)`、`content()`。Task 3 的迁移门禁与 Task 4 的 `profile.py`/`control.py` 都引用这些常量。

- [ ] **Step 1: 写契约文本**

创建 `docs/contracts/tkos-method-0.5.md`，内容如下（全文即契约，之后任何一个字节改动都要重新 pin）：

```markdown
# tkos.method/0.5 — M0 对齐增量契约（议题主 Scope / Scope 级 State / 证据锚点）

状态：**契约文字定稿，等待 M0 v0.2 评审会确认 A1–A4；Runtime 启用由 0.5 实施增量交付**。0.5 只有在协议注册表显式登记、且支持状态为本进程编译支持后才可调用；未启用前任何 0.5 请求返回 `PROTOCOL_NOT_SUPPORTED`。0.1／0.2／0.3／0.4 的解释、绑定、历史回执与回归保持不变；不自动迁移旧对象，不做跨版本放宽。

来源与校准：《TKOS 本体图结构梳理 v0.1》（2026-09-18 评审会三条修正）、《TKOS 产品路线图｜Web First》04 修订（2026-09-17）、`docs/contracts/tkos-method-0.4.md`。本契约只在 0.4 之上增加三处差异，其余条款逐字沿用 0.4。

## 1. 版本、启用与隔离

- 每个隔离 scope 显式选择业务规则版本。0.5 使用独立的新 scope 启用；0.4 及更早 scope 继续按原绑定解释。
- 0.5 的动作集合、对象类型集合与 0.4 相同（41 个动作、15 个对象类型）；差异只在三个 payload 的字段与校验规则。
- 未列入本契约的规则，以 0.4 契约为准。

## 2. 议题主 Scope（Strategic Issue）

- `StrategicIssue` 新增可选字段 `primary_scope_id`：议题主要关切的 Battlefield 或 Domain 稳定 `unit_id`。
- 提供 `primary_scope_id` 时必须同时提供 `architecture_ref`，且该 `unit_id` 必须存在于所引用的确切 Architecture 版本中；否则拒绝（`INVALID_REQUEST`）。
- 公司级议题允许不填。重新界定（reframe）可以改变主 Scope；历史版本保留当时的主 Scope。
- 主 Scope 只用于关系与读取（按战场列议题），不授予任何权限。

## 3. Scope 级经营状态（Operating State）

- `OperatingState` 新增可选字段 `subject_scope_id`。填写时，`subject_ref` 必须指向确切的 `StrategicArchitecture` 版本，`subject_scope_id` 必须是该版本中的 Battlefield 或 Domain 稳定 `unit_id`。
- 责任人：该 Scope 显式映射的授权域（`auth_domain_id`）中唯一的当前 DOMAIN_DRI。没有映射或当前 DRI 不唯一时拒绝（`INVALID_REQUEST` / `FORBIDDEN`）。
- 推荐与 canonical 分离：Agent（CO_AGENT 或 CEO_AGENT）或责任人本人可推荐；只有责任人本人确认后成为正式 Scope 级状态。确认时可附理由修正 summary 与评级，保留原推荐与证据。
- 无证据只能 Unknown 并列明缺口。**不对下层 LTCO／PCO／Mission 的 RAG 求平均，不自动生成任何 Scope 级状态。**
- 身份：同一 Architecture 对象、同一 `subject_scope_id`、同一 `as_of` 只有一个 State 身份；跨 Architecture 修订保持同一身份。重新推荐必须提供 `previous_state_ref`。
- baseline_refs 只能包含该确切 Architecture 版本及其配对 Strategy。
- 0.5 不允许 Scope 级 State 作为 OperatingProblem 的 `state_ref`，也不允许 PeriodReview 引用它；两者仍只面向 LTCO／PCO／Mission。

## 4. 证据锚点（Evidence Asset）

- 0.5 scope 的证据上传必须声明 `anchor`，三选一：`company`（公司级参考材料）、`scope`（确切 Architecture 版本 + `unit_id`）、`object`（确切对象版本）。缺少锚点拒绝（`INVALID_REQUEST`），并且不写对象存储。
- 锚点在上传时由上传方判断，之后不可更改；跨域次要关联不在本版本表达。
- 锚点指向的对象必须对上传者可读；`payload_hash` 必须与所指版本一致。
- 0.4 及更早 scope 的证据上传不接受 `anchor` 字段（拒绝，而不是忽略）。
- workspace 0.2 的私有来源不受本条约束；私有来源带入议题时经 workspace 0.1 挂锚会议场景落库。

## 5. 读取

- 新增只读读取器：`GET /v1/dashboard/company?domain_id=`、`GET /v1/dashboard/scopes/{architecture_object_id}/{unit_id}`、`GET /v1/dashboard/ontology/relations?contract_version=`。读取按当前授权过滤，不返回不可见记录的计数。
- `StrategicIssue` 详情附带 `lifecycle` 投影，只在 Runtime 事实能证明的阶段间取值；不能证明的阶段标为 `not_derivable`。

## 6. 未交付边界（本契约不承诺）

- Play、Human + AI Plan、WorkPackage、M2/M3 执行链。
- Scope 之间的依赖边、Agent/Skill 类（R2）。
- Period 节点。
- 跨版本接续、旧对象自动升级。
```

- [ ] **Step 2: 计算契约 SHA256**

```bash
shasum -a 256 docs/contracts/tkos-method-0.5.md
```

记下输出的 64 位十六进制值，下面三处（本任务的 profile 模块、Task 3 的迁移、Task 15 的状态文档）都用这个值。以后每次改契约文本都要重跑并重 pin（0.4 的 `0028_method_v04_contract_repin.sql` 就是这么来的）。

- [ ] **Step 3: 写 profile 模块**

创建 `src/memory_service_runtime/governed/method_v05_profile.py`（把 `<SHA256>` 换成 Step 2 的值）：

```python
"""Method 0.5 M0-alignment contract identity, independent of 0.1-0.4."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from . import canon, method_profile

PROTOCOL_ID = "tkos.method"
CONTRACT_VERSION = "tkos.method/0.5"
SCHEMA_VERSION = "tkos.method-profile/0.5"
CONTRACT_SHA256 = "<SHA256>"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal["tkos.method"] = "tkos.method"
    revision: Literal["0.5"] = "0.5"
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class Profile(method_profile.MethodProfileCore):
    profile_core_schema_version: Literal[SCHEMA_VERSION]
    revision: Literal["0.5.0"]
    display_name: Literal["Method 0.5 M0 alignment 2026-09-20"]
    action_contract_ref: ContractRef


def validate(data):
    value = Profile.model_validate(data)
    if value.canonical_hash != canon.digest_excluding(value.model_dump(mode="json"), frozenset({"canonical_hash"})):
        raise ValueError("Method profile canonical_hash mismatch")
    return value


def content():
    value = {**method_profile.content(), "profile_core_schema_version": SCHEMA_VERSION,
             "revision": "0.5.0", "display_name": "Method 0.5 M0 alignment 2026-09-20",
             "action_contract_ref": ContractRef().model_dump(mode="json")}
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
```

- [ ] **Step 4: 生成 profile JSON 与注册表 JSON**

```bash
cd /Users/yusiyi/ysy/tkos-ontology-runtime && PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from memory_service_runtime.governed import method_v05_profile as p
open('docs/contracts/method-profile-0.5.json', 'w').write(json.dumps(p.content(), ensure_ascii=False, indent=2) + '\n')
registry = json.load(open('docs/runtime-method-registry-0.4.json'))
registry['readonly_compat'] = ['tkos.method/0.5']
registry['notes'] = 'Method 0.5 M0 alignment: issue primary Scope, Scope-level State, evidence anchor; explicit per-scope registration required.'
open('docs/runtime-method-registry-0.5.json', 'w').write(json.dumps(registry, ensure_ascii=False, indent=2) + '\n')
PY
```

- [ ] **Step 5: 写 pin 测试**

创建 `tests/test_method_v05_models.py`：

```python
"""Strict 0.5 schema, registry and dispatch boundaries (no DB required)."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from memory_service_runtime.governed import method_v05_profile as profile

ROOT = Path(__file__).resolve().parents[1]


def test_contract_pin_and_profile_validate():
    assert profile.validate(profile.content())
    assert sha256((ROOT / 'docs/contracts/tkos-method-0.5.md').read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    saved = json.loads((ROOT / 'docs/contracts/method-profile-0.5.json').read_text())
    assert profile.validate(saved).canonical_hash == profile.content()['canonical_hash']
```

- [ ] **Step 6: 运行测试确认通过**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_models.py -q
```

Expected: `1 passed`。

- [ ] **Step 7: 提交**

```bash
git add docs/contracts/tkos-method-0.5.md docs/contracts/method-profile-0.5.json docs/runtime-method-registry-0.5.json src/memory_service_runtime/governed/method_v05_profile.py tests/test_method_v05_models.py
git commit -m "feat(method): freeze the tkos.method/0.5 M0-alignment contract and profile

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: 0.5 严格模型、注册表分发与请求信封

**Files:**
- Create: `src/memory_service_runtime/governed/method_v05_models.py`
- Modify: `src/memory_service_runtime/governed/method_models.py:15-25`（`registry()` 增加 0.5 分支）
- Modify: `src/memory_service_runtime/governed/protocol.py:43-52`（`SUPPORTED_PROTOCOL_CONTRACTS`）
- Modify: `src/memory_service_runtime/governed/models.py:460-470, 508-560`（`ActionRequest` 的参数选择与信封规则）
- Test: `tests/test_method_v05_models.py`

**Interfaces:**
- Consumes: `method_v04_models` 的全部模型（只导入，不修改）。
- Produces: `method_v05_models.CONTRACT_VERSION`、`PAYLOAD_MODELS`、`ACTION_PARAMS`、`ACTION_TARGETS`、`OBJECT_TYPES`、`HUMAN_ACTIONS`、`AGENT_ACTIONS`；`StrategicIssuePayload.primary_scope_id: str | None`；`StatePayload.subject_scope_id: str | None`。Task 4–6 的执行器与 Task 8 的覆盖测试都以这些名字为准。

- [ ] **Step 1: 写失败的模型测试**

在 `tests/test_method_v05_models.py` 末尾追加：

```python
import pytest
from pydantic import ValidationError
from uuid import uuid4

from memory_service_runtime.governed import method_models, method_v05_models as m, protocol
from memory_service_runtime.governed.models import ActionRequest


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


def issue_payload(**updates):
    return {'title': 't', 'summary': 's', 'core_question': 'q', 'business_scope': 'strategic',
            'urgency': 'green', 'source_refs': [ref()], **updates}


def state_payload(**updates):
    subject = ref()
    return {'subject_ref': subject, 'as_of': '2026-09-20T00:00:00Z', 'summary': 'Evidence gap',
            'rag': 'unknown', 'baseline_refs': [subject], 'evidence_refs': [],
            'data_gaps': ['Awaiting evidence'], 'generation_version': 'test-1', **updates}


def test_registry_matches_generated_json_and_action_target_parity():
    registry = json.loads((ROOT / 'docs/runtime-method-registry-0.5.json').read_text())
    assert set(registry['actions']) == set(m.ACTION_PARAMS)
    assert set(registry['object_types']) == set(m.PAYLOAD_MODELS) | {'EvidenceAsset'}
    params, targets, payloads = method_models.registry('tkos.method/0.5')
    assert params is m.ACTION_PARAMS and targets is m.ACTION_TARGETS and payloads is m.PAYLOAD_MODELS
    assert set(m.ACTION_PARAMS) == set(m.ACTION_TARGETS)
    assert ('tkos.method', 'tkos.method/0.5') in protocol.SUPPORTED_PROTOCOL_CONTRACTS


def test_issue_primary_scope_requires_the_architecture_it_belongs_to():
    with pytest.raises(ValidationError):
        m.StrategicIssuePayload.model_validate(issue_payload(primary_scope_id='bf-1'))
    accepted = m.StrategicIssuePayload.model_validate(
        issue_payload(primary_scope_id='bf-1', strategy_ref=ref(), architecture_ref=ref()))
    assert accepted.primary_scope_id == 'bf-1'
    assert m.StrategicIssuePayload.model_validate(issue_payload()).primary_scope_id is None


def test_scope_state_keeps_the_evidence_rule_and_the_0_4_shape():
    scoped = m.StatePayload.model_validate(state_payload(subject_scope_id='dom-1'))
    assert scoped.subject_scope_id == 'dom-1'
    with pytest.raises(ValidationError):  # known rating without evidence is still forbidden
        m.StatePayload.model_validate(state_payload(subject_scope_id='dom-1', rag='green', data_gaps=[]))
    with pytest.raises(ValidationError):  # 0.4 model never widens
        from memory_service_runtime.governed import method_v04_models as v4
        v4.StatePayload.model_validate(state_payload(subject_scope_id='dom-1'))


def test_action_request_dispatches_v05_and_keeps_older_contracts_frozen():
    body = {'action_type': 'm1a_create_issue', 'target': None, 'expected_versions': [],
            'idempotency_key': 'v05-unit-dispatch-0001', 'reason': 'Dispatch boundary check',
            'contract_version': 'tkos.method/0.5',
            'params': {'domain_id': str(uuid4()),
                       'payload': issue_payload(primary_scope_id='bf-1', strategy_ref=ref(), architecture_ref=ref())}}
    accepted = ActionRequest.model_validate(body)
    assert accepted.params.payload.primary_scope_id == 'bf-1'
    for old in ('tkos.method/0.1', 'tkos.method/0.2', 'tkos.method/0.3', 'tkos.method/0.4'):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body, 'contract_version': old})
    with pytest.raises(ValidationError):  # targetless 0.5 action with a target
        ActionRequest.model_validate({**body, 'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()),
                                                         'expected_version': 1}})
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_models.py -q
```

Expected: `ModuleNotFoundError: memory_service_runtime.governed.method_v05_models`。

- [ ] **Step 3: 写 0.5 模型模块**

创建 `src/memory_service_runtime/governed/method_v05_models.py`：

```python
"""Strict ``tkos.method/0.5`` schemas: issue primary Scope and Scope-level State.

Only the three payloads that differ from 0.4 are redefined here; every other
0.4 model, action and target set is reused by reference.  The 0.4 module is
frozen and never widened in place.
"""
from __future__ import annotations

from pydantic import model_validator

from . import method_v04_models as v4
from .a2_models import NEStr

CONTRACT_VERSION = "tkos.method/0.5"


class StrategicIssuePayload(v4.StrategicIssuePayload):
    """0.5 adds the optional unique primary Scope (Battlefield/Domain stable unit)."""

    primary_scope_id: NEStr | None = None

    @model_validator(mode="after")
    def scope_belongs_to_an_architecture(self) -> "StrategicIssuePayload":
        if self.primary_scope_id is not None and self.architecture_ref is None:
            raise ValueError("a primary Scope requires the exact Architecture it belongs to")
        return self


class CreateIssue(v4.CreateIssue):
    payload: StrategicIssuePayload


class ReframeIssue(v4.ReframeIssue):
    payload: StrategicIssuePayload


class StatePayload(v4.StatePayload):
    """When ``subject_scope_id`` is set, ``subject_ref`` names the exact
    StrategicArchitecture revision and the State assesses that Scope."""

    subject_scope_id: NEStr | None = None


class ProposeState(v4.ProposeState):
    payload: StatePayload


PAYLOAD_MODELS = {**v4.PAYLOAD_MODELS,
                  "StrategicIssue": StrategicIssuePayload,
                  "OperatingState": StatePayload}
ACTION_PARAMS = {**v4.ACTION_PARAMS,
                 "m1a_create_issue": CreateIssue,
                 "m1a_reframe_issue": ReframeIssue,
                 "method_propose_state": ProposeState}
ACTION_TARGETS = dict(v4.ACTION_TARGETS)
OBJECT_TYPES = frozenset(PAYLOAD_MODELS) | {"EvidenceAsset"}
HUMAN_ACTIONS = v4.HUMAN_ACTIONS
AGENT_ACTIONS = v4.AGENT_ACTIONS
```

- [ ] **Step 4: 接入注册表分发与编译支持集合**

`src/memory_service_runtime/governed/method_models.py` 的 `registry()` 最前面加：

```python
    if version == "tkos.method/0.5":
        from . import method_v05_models as v05
        return v05.ACTION_PARAMS, v05.ACTION_TARGETS, v05.PAYLOAD_MODELS
```

`src/memory_service_runtime/governed/protocol.py` 的 `SUPPORTED_PROTOCOL_CONTRACTS` 在 0.4 那行后加：

```python
        ("tkos.method", "tkos.method/0.5"),
```

- [ ] **Step 5: 接入请求信封**

`src/memory_service_runtime/governed/models.py`：

在第 465 行 0.4 导入后加：

```python
from .method_v05_models import ACTION_PARAMS as METHOD_V05_PARAMS, ACTION_TARGETS as METHOD_V05_TARGETS
```

`ActionType` / `ActionParams` 两个 `Union` 各追加一项 `Literal[tuple(METHOD_V05_PARAMS)]` 与 `Union[tuple(METHOD_V05_PARAMS.values())]`。

`select_params` 里 0.4 分支之前插入：

```python
            if data.get("contract_version") == "tkos.method/0.5" and action_type in METHOD_V05_PARAMS:
                data = dict(data)
                data["params"] = METHOD_V05_PARAMS[action_type].model_validate(data.get("params"))
            elif data.get("contract_version") == "tkos.method/0.4" and action_type in METHOD_V04_PARAMS:
```

（原来的 `if ... == "tkos.method/0.4"` 改成 `elif`。）

`envelope_rules` 里把第一个分支改为同时覆盖 0.4 与 0.5：

```python
        if self.contract_version in {"tkos.method/0.4", "tkos.method/0.5"} and self.action_type in METHOD_V05_TARGETS:
            if bool(METHOD_V05_TARGETS[self.action_type]) != (self.target is not None):
                raise ValueError("Method action target does not match its typed contract")
```

（0.4 与 0.5 的 target 集合相同，用 0.5 的表判断即可。）

把 run 关联那行：

```python
        if self.action_type not in METHOD_V02_TARGETS and self.contract_version != "tkos.method/0.4" and (self.run_ref is not None or self.step_key is not None):
```

改为：

```python
        if (self.action_type not in METHOD_V02_TARGETS
                and self.contract_version not in {"tkos.method/0.4", "tkos.method/0.5"}
                and (self.run_ref is not None or self.step_key is not None)):
```

- [ ] **Step 6: 运行确认通过，并跑 0.4 回归**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_models.py tests/test_method_v04_models.py tests/test_method_v03.py -q
```

Expected: 全部 passed；0.4/0.3 用例数量与改动前一致。

- [ ] **Step 7: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05_models.py src/memory_service_runtime/governed/method_models.py src/memory_service_runtime/governed/protocol.py src/memory_service_runtime/governed/models.py tests/test_method_v05_models.py
git commit -m "feat(method): add the tkos.method/0.5 strict models and request dispatch

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 3: 迁移 0029（0.5 绑定门禁与读取索引）

**Files:**
- Create: `src/memory_service_app/migrations/0029_method_v05.sql`
- Modify: `tests/test_migrations.py:51-57`（完整清单）
- Test: `tests/test_migrations.py`、`tests/test_method_v05_models.py`

**Interfaces:**
- Consumes: Task 1 的 `CONTRACT_SHA256`。
- Produces: `gov_binding_insert_gate()` 接受 `tkos.method/0.5` 绑定；索引 `ix_gov_revisions_primary_scope`、`ix_gov_revisions_anchor_scope` 供 Task 10 的读取器使用。

- [ ] **Step 1: 更新迁移清单测试**

`tests/test_migrations.py` 的 `expected` 列表在 `"0028_workspace_v02_grant_repair.sql",` 之后加一行 `"0029_method_v05.sql",`。在 `tests/test_method_v05_models.py` 追加：

```python
def test_migration_pins_the_same_contract_bytes():
    sql = (ROOT / 'src/memory_service_app/migrations/0029_method_v05.sql').read_text()
    assert profile.CONTRACT_SHA256 in sql
    assert "'tkos.method/0.5'" in sql
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_models.py::test_migration_pins_the_same_contract_bytes -q
```

Expected: `FileNotFoundError`。

- [ ] **Step 3: 写迁移**

创建 `src/memory_service_app/migrations/0029_method_v05.sql`。门禁函数整体复制 `0028_method_v04_contract_repin.sql` 的 `gov_binding_insert_gate()`，只在 0.4 分支之后插入 0.5 分支（`<SHA256>` 换成 Task 1 Step 2 的值）；另加两个表达式索引：

```sql
-- 0029: tkos.method/0.5 M0-alignment support.
--
-- No new object_type and no new table: 0.5 reuses the 0.4 object set and adds
-- three payload fields (StrategicIssue.primary_scope_id, OperatingState.
-- subject_scope_id, EvidenceAsset.anchor_*).  Scope-level State identity reuses
-- gov_method_state_keys with outcome_id = 'scope:<unit_id>'.  The two indexes
-- serve the Scope/Company readers; they do not change any rule.
CREATE INDEX IF NOT EXISTS ix_gov_revisions_primary_scope
    ON gov_object_revisions (scope_id, (payload->>'primary_scope_id'))
    WHERE payload ? 'primary_scope_id';
CREATE INDEX IF NOT EXISTS ix_gov_revisions_anchor_scope
    ON gov_object_revisions (scope_id, (payload->>'anchor_kind'), (payload->>'anchor_scope_id'))
    WHERE payload ? 'anchor_kind';

-- Preserve prior binding contracts; add the exact 0.5 M0-alignment identity.
CREATE OR REPLACE FUNCTION gov_binding_insert_gate()
RETURNS trigger
LANGUAGE plpgsql
AS $gov_binding_insert_gate$
DECLARE
    prow record;
BEGIN
    IF NEW.binding_version <> 1 THEN
        IF gov_control_plane_on() IS NOT TRUE THEN
            RAISE EXCEPTION 'rebinding (binding_version>1) requires the control plane'
                USING ERRCODE = '55000';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM gov_object_protocol_bindings b
         WHERE b.scope_id = NEW.scope_id AND b.object_id = NEW.object_id
    ) THEN
        RAISE EXCEPTION 'object % already has a protocol binding; rebinding requires the control plane', NEW.object_id
            USING ERRCODE = '55000';
    END IF;
    SELECT p.schema_version, p.content INTO prow
      FROM gov_method_profile_revisions p
     WHERE p.scope_id = NEW.scope_id
       AND p.profile_id = NEW.profile_id
       AND p.revision = NEW.profile_revision
       AND p.canonical_hash = NEW.profile_canonical_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'bound method profile % revision % hash % is not installed in this scope',
            NEW.profile_id, NEW.profile_revision, NEW.profile_canonical_hash
            USING ERRCODE = '23514';
    END IF;
    IF NEW.protocol_id = 'tkos.legacy-governed' AND NEW.contract_version = 'tkos.governed/v0.2' THEN
        IF (NEW.profile_id = 'urn:tkos:legacy:governed-v0.2'
                AND NEW.profile_revision = '0.2.0'
                AND NEW.profile_canonical_hash = '93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598'
                AND prow.schema_version = 'tkos.legacy-interpretation-record/0.2') IS NOT TRUE THEN
            RAISE EXCEPTION 'legacy protocol bindings require the pinned legacy interpretation record'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.contract-a' AND NEW.contract_version = 'tkos.contract-a/0.1' THEN
        IF (prow.schema_version = 'tkos.profile-core/0.1'
                AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.contract-a'
                AND prow.content->'action_contract_ref'->>'revision' = '0.1'
                AND prow.content->'action_contract_ref'->>'content_sha256'
                    = 'fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4') IS NOT TRUE THEN
            RAISE EXCEPTION 'contract-a bindings require a ProfileCore bound to the exact main contract bytes'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.1' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.1'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.1'
            AND prow.content->'action_contract_ref'->>'content_sha256' = 'd108d228e903384182b6945181aa5bcafde4a3f64b4c564d897372805588d1c3'
            AND prow.content->>'m1a_source_revision' = '21'
            AND prow.content->>'m1b_source_revision' = '837') IS NOT TRUE THEN
            RAISE EXCEPTION 'method bindings require the independently frozen Method profile' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.2' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.2'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.2'
            AND prow.content->'action_contract_ref'->>'content_sha256' = '82568bdff37c711207a1a010ae553f72b2f036122b1f88a3b92aec286f9fbbf3') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.2 requires its exact lifecycle contract' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.3' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.3'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.3'
            AND prow.content->'action_contract_ref'->>'content_sha256' = '17f0896993ac91bf232aa8d60de618ccec9b61b1d81fa68f815414943574381d') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.3 requires its exact Anchor contract' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.4' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.4'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.4'
            AND prow.content->'action_contract_ref'->>'content_sha256' = '984c3e09dc9771e29e26aea858d19bb4639bb3d93dc4df12841130ef4f8e44aa') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.4 requires its exact formal-governance contract' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.protocol_id = 'tkos.method' AND NEW.contract_version = 'tkos.method/0.5' THEN
        IF (prow.schema_version = 'tkos.method-profile/0.5'
            AND prow.content->'action_contract_ref'->>'contract_id' = 'tkos.method'
            AND prow.content->'action_contract_ref'->>'revision' = '0.5'
            AND prow.content->'action_contract_ref'->>'content_sha256' = '<SHA256>') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.5 requires its exact M0-alignment contract' USING ERRCODE='23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'protocol % contract version % is not implemented by this schema',
            NEW.protocol_id, NEW.contract_version
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$gov_binding_insert_gate$;
```

- [ ] **Step 4: 在隔离库跑迁移测试**

```bash
python3 acceptance/runtime/infra.py up
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -m pytest tests/test_migrations.py -q
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_models.py -q
```

Expected: `test_migrations.py` 1 passed（空库到 0029，重复执行应用 0 个）；模型测试全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_app/migrations/0029_method_v05.sql tests/test_migrations.py tests/test_method_v05_models.py
git commit -m "feat(runtime): gate tkos.method/0.5 bindings and index Scope lookups (0029)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 4: 0.5 执行器与全链路版本分发

**Files:**
- Create: `src/memory_service_runtime/governed/method_v05.py`
- Modify: `src/memory_service_runtime/governed/method_service.py:44-95, 236-244, 346-361, 380-392, 412-425`
- Modify: `src/memory_service_runtime/governed/method_readers.py:96, 284, 293, 297, 299, 323, 398, 428`
- Modify: `src/memory_service_runtime/governed/method_access.py:349, 371, 374`
- Modify: `src/memory_service_runtime/governed/workbench.py:278`
- Modify: `src/memory_service_runtime/governed/governance.py:262-270, 298-300`
- Modify: `src/memory_service_runtime/governed/profile.py:296-299, 331-338`
- Modify: `src/memory_service_runtime/governed/control.py:113-118`
- Modify: `src/memory_service_runtime/governed/protocol.py:577-580`
- Modify: `src/memory_service_runtime/governed/dashboard.py:419-424`
- Modify: `src/memory_service_runtime/governed/method_map.py:37-41, 96-106`
- Modify: `src/memory_service_app/governance_commands.py:30-34, 112-118`
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: `method_v04` 的 `collect`、`run`、`scoped_assignment`、`_state_subject_owner_static`、`_scope_definition`、`_pco_responsibility`、`_LightExecution`、`_agent`、`_phase`、`_same`、`_exact`；`method_access.scope_state_subject_assignment`（Task 6 提供，本任务先声明调用）。
- Produces: `method_v05.collect(e)`、`run(e)`、`scoped_assignment(conn, ctx, kind, target, revision)`、`state_subject_owner_static(conn, ctx, payload)`、`is_scope_state(payload)`、`scope_state_key(payload)`、常量 `FORMAL_GOVERNANCE_VERSIONS`（放在 `method_service.py`）。

- [ ] **Step 1: 写失败的分发测试**

创建 `tests/test_method_v05_dispatch.py`：

```python
"""0.5 delegates every unchanged action to the frozen 0.4 executor (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import method_v04, method_v05, method_service, workbench, protocol
from memory_service_runtime.governed.errors import GovernedError


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


def test_scope_state_identity_is_stable_across_architecture_revisions():
    subject = ref()
    payload = {'subject_ref': subject, 'subject_scope_id': 'bf-1', 'as_of': '2026-09-20T08:00:00+08:00'}
    key = method_v05.scope_state_key(payload)
    assert key[0] == subject['object_id'] and key[1] == 'scope:bf-1'
    assert key == method_v05.scope_state_key({**payload, 'as_of': '2026-09-20T00:00:00Z',
                                              'subject_ref': {**subject, 'revision_id': str(uuid4())}})
    assert not method_v05.is_scope_state({'subject_ref': subject})


def test_unchanged_actions_delegate_to_the_frozen_0_4_executor(monkeypatch):
    seen = []
    monkeypatch.setattr(method_v04, 'collect', lambda e: seen.append(('collect', e.kind)))
    monkeypatch.setattr(method_v04, 'run', lambda e: seen.append(('run', e.kind)) or {'ok': True})
    e = SimpleNamespace(kind='m1b_confirm_ltco', params={}, target_revision={'payload': {}})
    method_v05.collect(e)
    assert method_v05.run(e) == {'ok': True}
    assert seen == [('collect', 'm1b_confirm_ltco'), ('run', 'm1b_confirm_ltco')]


def test_issue_scope_must_exist_in_the_exact_architecture(monkeypatch):
    architecture = {'battlefields': [{'unit_id': 'bf-1'}], 'domains': [{'unit_id': 'dom-1'}]}
    e = SimpleNamespace(kind='m1a_create_issue',
                        params={'payload': {'primary_scope_id': 'bf-9', 'architecture_ref': ref()}},
                        ref=lambda reference, **kw: ({'object_type': 'StrategicArchitecture'}, {'payload': architecture}))
    with pytest.raises(GovernedError) as exc:
        method_v05._check_issue_scope(e)
    assert exc.value.code == 'INVALID_REQUEST'
    e.params['payload']['primary_scope_id'] = 'dom-1'
    method_v05._check_issue_scope(e)  # accepted: no exception
    e.params['payload'].pop('primary_scope_id')
    method_v05._check_issue_scope(e)  # company-level issue


def test_service_routes_0_5_to_the_0_5_executor():
    assert 'tkos.method/0.5' in method_service.FORMAL_GOVERNANCE_VERSIONS
    assert workbench.object_types(None, None, 'tkos.method/0.5')['contract_version'] == 'tkos.method/0.5'
    assert ('tkos.method', 'tkos.method/0.5') in protocol.SUPPORTED_PROTOCOL_CONTRACTS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_dispatch.py -q
```

Expected: `ImportError: cannot import name 'method_v05'`。

- [ ] **Step 3: 写 0.5 执行器**

创建 `src/memory_service_runtime/governed/method_v05.py`：

```python
"""tkos.method/0.5 增量执行：议题主 Scope 与 Scope 级 OperatingState。

0.4 模块保持冻结；本模块只对 0.5 有差异的动作做前置校验或独立分支，其余动作
原样委托 ``method_v04``。所有校验仍在同一治理事务内进行，不产生执行副作用。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from . import db, method_access as access, method_v04 as v4
from .errors import GovernedError

SCOPE_STATE_OUTCOME_PREFIX = "scope:"


def fail(message: str, code: str = "INVALID_STATE") -> None:
    raise GovernedError(code, message)


def is_scope_state(payload) -> bool:
    return payload.get("subject_scope_id") is not None


def scope_state_key(payload):
    """``gov_method_state_keys`` 身份：(Architecture 对象, 'scope:<unit>', as_of)。跨修订稳定。"""
    return (payload["subject_ref"]["object_id"],
            SCOPE_STATE_OUTCOME_PREFIX + payload["subject_scope_id"],
            datetime.fromisoformat(payload["as_of"]))


# ------------------------------------------------------------ issue scope


def _check_issue_scope(e):
    """可选主 Scope 必须存在于所引用的确切 Architecture 版本中。"""
    payload = e.params["payload"]
    if payload.get("primary_scope_id") is None:
        return
    _head, revision = e.ref(payload["architecture_ref"], types={"StrategicArchitecture"},
                            effective=True, current=False)
    v4._scope_definition(e, revision["payload"], payload["primary_scope_id"])


# ------------------------------------------------------------ scope state


def _scope_owner(e, payload):
    """Scope 级 State 责任人：该 Scope 映射授权域的唯一当前 DOMAIN_DRI（复用 0.4 的 PCO 责任解析）。"""
    principal, _role, _auth_domain, assignment = v4._pco_responsibility(
        e, {"architecture_ref": payload["subject_ref"], "primary_scope_id": payload["subject_scope_id"]})
    return principal, assignment


def _collect_propose_scope_state(e):
    payload = e.params["payload"]
    _head, revision = e.ref(payload["subject_ref"], types={"StrategicArchitecture"}, effective=True, current=True)
    v4._scope_definition(e, revision["payload"], payload["subject_scope_id"])
    owner, _assignment = _scope_owner(e, payload)
    if e.ctx.principal_type == "human":
        e.require_actor(owner, "human")
    else:
        v4._agent(e, "CO_AGENT") if "CO_AGENT" in {a["role"] for a in e.ctx.assignments} else v4._agent(e, "CEO_AGENT")
    allowed = [payload["subject_ref"]]
    if revision["payload"].get("strategy_ref"):
        allowed.append(revision["payload"]["strategy_ref"])
    for reference in payload["baseline_refs"]:
        if not any(v4._same(reference, limit) for limit in allowed):
            fail("Scope State baselines are limited to the exact Architecture and its paired Strategy.", "INVALID_REQUEST")
    if not any(v4._same(reference, payload["subject_ref"]) for reference in payload["baseline_refs"]):
        fail("State baseline must include the exact Architecture subject.", "INVALID_REQUEST")
    for reference in payload["baseline_refs"]:
        e.ref(reference, current=False)
    for reference in payload["evidence_refs"]:
        e.ref(reference, types={"EvidenceAsset", "BusinessFact", "PeriodReview", "OperatingState"}, current=False)
    key = scope_state_key(payload)
    previous = e.params.get("previous_state_ref")
    if previous:
        _prev_head, prev = e.ref(previous, types={"OperatingState"})
        if not is_scope_state(prev["payload"]) or scope_state_key(prev["payload"]) != key:
            fail("A new recommendation must preserve the Scope State identity and as-of.", "VERSION_CONFLICT")
    else:
        row = e.conn.execute(
            "SELECT state_id FROM gov_method_state_keys WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s AND as_of=%s",
            (e.ctx.scope_id, *key)).fetchone()
        if row:
            fail("This Scope and as-of already have a State; re-recommend with previous_state_ref.", "VERSION_CONFLICT")


def _collect_confirm_scope_state(e):
    v4._phase(e.state(e.target), "proposed")
    payload = e.target_revision["payload"]
    owner, _assignment = _scope_owner(e, payload)
    e.require_actor(owner, "human")
    e.validate_principal(owner, "human")
    if e.params.get("rag") and e.params["rag"] != "unknown" and not payload["evidence_refs"]:
        fail("Missing evidence cannot be overridden into a known rating.", "INVALID_REQUEST")


# -------------------------------------------------------------- dispatch


def collect(e):
    kind = e.kind
    if kind in {"m1a_create_issue", "m1a_reframe_issue"}:
        v4.collect(e)
        _check_issue_scope(e)
        return
    if kind == "method_propose_state" and is_scope_state(e.params["payload"]):
        e._v04 = {}
        return _collect_propose_scope_state(e)
    if kind == "method_confirm_state" and is_scope_state(e.target_revision["payload"]):
        e._v04 = {}
        return _collect_confirm_scope_state(e)
    return v4.collect(e)


def run(e):
    kind = e.kind
    if kind == "method_propose_state" and is_scope_state(e.params["payload"]):
        collect(e)
        payload = e.params["payload"]
        previous = e.params.get("previous_state_ref")
        if previous:
            head, _old = e.ref(previous, types={"OperatingState"})
            head, revision = e.revise(head, payload)
        else:
            head, revision = e.create("OperatingState", payload)
            e.conn.execute(
                "INSERT INTO gov_method_state_keys(scope_id,subject_id,outcome_id,as_of,state_id) VALUES(%s,%s,%s,%s,%s)",
                (e.ctx.scope_id, *scope_state_key(payload), head["object_id"]))
        state = deepcopy(e.state(head))
        state.update(phase="proposed", recommendation_ref=v4._exact(head, revision))
        e.set_state(head, state)
        return v4._exact(head, revision)
    if kind == "method_confirm_state" and is_scope_state(e.target_revision["payload"]):
        collect(e)
        original = v4._exact(e.target, e.target_revision)
        head, revision = e.target, e.target_revision
        if e.params.get("rag"):
            head, revision = e.revise(head, {**revision["payload"], "rag": e.params["rag"],
                                             "summary": e.params["summary"]})
        e.transition(head, status="active", effective=True)
        reference = v4._exact(head, revision)
        state = deepcopy(e.state(head))
        state.update(phase="confirmed", canonical_ref=reference, confirmed_by=e.ctx.principal_id)
        e.set_state(head, state)
        record = e.review("state_confirmation", reference, {**e.params, "recommendation_ref": original})
        return {**reference, "review_record_id": record}
    if kind in {"m1a_create_issue", "m1a_reframe_issue"}:
        _check_issue_scope(e)
    return v4.run(e)


# ------------------------------------------------------- scoped authority


def scoped_assignment(conn, ctx, kind, target, revision):
    """Scope 级 State 的确认落到该 Scope 的当前 DRI；其余沿用 0.4。对 0.4 对象行为不变。"""
    payload = revision["payload"]
    if kind == "method_confirm_state" and is_scope_state(payload):
        return access.scope_state_subject_assignment(conn, ctx, payload)
    return v4.scoped_assignment(conn, ctx, kind, target, revision)


def state_subject_owner_static(conn, ctx, payload):
    """只读责任解析（治理工作台可用性投影）。Scope 级走 0.5，其余沿用 0.4。"""
    if is_scope_state(payload):
        head = db.jsonable(conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                        (ctx.scope_id, payload["subject_ref"]["object_id"])).fetchone())
        if head is None or head["object_type"] != "StrategicArchitecture" or not head.get("effective_revision_id"):
            raise GovernedError("FORBIDDEN")
        context = v4._LightExecution(conn, ctx)
        return v4._pco_responsibility(context, {"architecture_ref": payload["subject_ref"],
                                                "primary_scope_id": payload["subject_scope_id"]})[0]
    return v4._state_subject_owner_static(conn, ctx, payload["subject_ref"])
```

- [ ] **Step 4: 接入 method_service**

`src/memory_service_runtime/governed/method_service.py`：

在 `class MethodExecution` 之前（模块顶部 import 之后）加常量：

```python
# 0.4 与 0.5 共用同一套正式治理规则（全体确认、整组激活、scoped 授权）；0.5 只增三处字段。
FORMAL_GOVERNANCE_VERSIONS = frozenset({"tkos.method/0.4", "tkos.method/0.5"})
```

逐处替换：

1. 第 48 行与第 419 行：`self.contract_version == "tkos.method/0.4"` → `self.contract_version in FORMAL_GOVERNANCE_VERSIONS`。
2. 第 52 行：`self.contract_version != "tkos.method/0.4"` → `self.contract_version not in FORMAL_GOVERNANCE_VERSIONS`。
3. 第 67–75 行 scoped 回退改为：

```python
                    if self.contract_version in FORMAL_GOVERNANCE_VERSIONS:
                        from .method_v04 import V04_SCOPED_ACTIONS
                        if self.kind not in V04_SCOPED_ACTIONS:
                            raise
                        from . import method_v05
                        member = method_v05.scoped_assignment(self.conn, self.ctx, self.kind,
                                                              self.target, self.target_revision)
                        self.method_scoped_domain = member["domain_id"]
                        self.action_assignments = db.authorize_domain(self.conn, self.ctx, member["domain_id"], self.kind)
```

（`method_v05.scoped_assignment` 对 0.4 对象原样委托 `method_v04.scoped_assignment`。）

4. 第 89–92 行 `method_propose_state` 回退改为：

```python
                if self.contract_version in FORMAL_GOVERNANCE_VERSIONS and self.kind == "method_propose_state":
                    payload = self.params['payload']
                    if self.contract_version == "tkos.method/0.5" and payload.get('subject_scope_id') is not None:
                        member = access.scope_state_subject_assignment(self.conn, self.ctx, payload)
                    else:
                        member = access.state_subject_assignment(self.conn, self.ctx, payload['subject_ref'])
                    self.method_scoped_domain = member['domain_id']
                    self.action_assignments = db.authorize_domain(self.conn, self.ctx, member['domain_id'], self.kind)
```

5. 第 240 行 `create()`：`self.contract_version in {"tkos.method/0.3", "tkos.method/0.4"}` → `self.contract_version in {"tkos.method/0.3", *FORMAL_GOVERNANCE_VERSIONS}`。
6. `collect_dependencies()` 最前面加：

```python
        if self.contract_version == "tkos.method/0.5":
            from . import method_v05
            method_v05.collect(self)
        elif self.contract_version == "tkos.method/0.4":
```

7. `run_action()` 同样在 0.4 分支前加：

```python
        if self.contract_version == "tkos.method/0.5":
            from . import method_v05
            return method_v05.run(self)
```

- [ ] **Step 5: 接入其余版本判断点**

按行号逐处修改（每处都是把「等于 0.4」扩为「0.4 或 0.5」，或把集合加入 0.5）：

- `method_readers.py:96`：`binding["contract_version"] == "tkos.method/0.4"` → `in {"tkos.method/0.4", "tkos.method/0.5"}`；第 284、293、323、428 行的集合加入 `"tkos.method/0.5"`；第 297、398 行的 `{"tkos.method/0.3", "tkos.method/0.4"}` 加入 0.5；第 299 行 `== "tkos.method/0.4"` → `in {"tkos.method/0.4", "tkos.method/0.5"}`。
- `method_access.py:349`：`binding['contract_version'] == 'tkos.method/0.4'` → `in {'tkos.method/0.4', 'tkos.method/0.5'}`；第 371 行集合加入 0.5；第 374 行 `=='tkos.method/0.4'` → `in {...0.4, 0.5}`，并在其 `OperatingState` 分支前加：

```python
        if head['object_type']=='OperatingState' and payload.get('subject_scope_id') is not None:
            return scope_state_subject_assignment(conn,ctx,payload)
```

（`scope_state_subject_assignment` 在 Task 6 定义；本任务先写调用，Task 6 的测试覆盖它。）

- `workbench.py:278`：集合加入 `"tkos.method/0.5"`。
- `governance.py:262-266`：`from . import method_v04` / `method_v04.scoped_assignment(...)` → `from . import method_v05` / `method_v05.scoped_assignment(...)`；第 268–269 行改为：

```python
        from . import method_v05
        owner = method_v05.state_subject_owner_static(conn, ctx, obj['latest_revision']['payload'])
```

第 298–300 行 `method_object_actions`：`if obj['protocol']['contract_version'] != 'tkos.method/0.4'` → `not in {'tkos.method/0.4', 'tkos.method/0.5'}`。

- `profile.py:296`：在 0.4 分支前加：

```python
    if isinstance(data, dict) and data.get("profile_core_schema_version") == "tkos.method-profile/0.5":
        from .method_v05_profile import validate
        return validate(data)
```

`implied_protocol()` 同样在 0.4 分支前加 0.5 分支（`method_v05_profile.validate(content)`，返回 `(method_v05_profile.PROTOCOL_ID, method_v05_profile.CONTRACT_VERSION)`）。

- `control.py:113-118`：导入加 `method_v05_profile`，`pinned_sha` 表达式最前面加 `method_v05_profile.CONTRACT_SHA256 if core.profile_core_schema_version == method_v05_profile.SCHEMA_VERSION else ...`。
- `protocol.py:577`：在 0.4 分支前加：

```python
        if binding["contract_version"] == "tkos.method/0.5":
            return "method_v0_5", ("Method 0.5; 0.4 rules plus issue primary Scope, "
                                    "Scope-level State and evidence anchors.")
```

- `dashboard.py:MISSION_PARENT_REF_FIELDS` 加 `"tkos.method/0.5": "parent_pco_ref",`。
- `method_map.py`：`DOCUMENTED_CONTRACTS = ("tkos.method/0.5", "tkos.method/0.4", "tkos.workspace/0.2")`；`documented_contract_state()` 第一个条件 `if version == METHOD_V04:` → `if version.startswith("tkos.method/"):`。
- `src/memory_service_app/governance_commands.py`：`v04_human_actions()` 改名保留并新增：

```python
def human_actions_for(version):
    if version == 'tkos.method/0.5':
        from memory_service_runtime.governed.method_v05_models import HUMAN_ACTIONS
        return HUMAN_ACTIONS
    return v04_human_actions()
```

第 115–116 行改为 `elif version in {'tkos.method/0.4', 'tkos.method/0.5'}: if action not in human_actions_for(version): raise GovernedError('FORBIDDEN')`。

- [ ] **Step 6: 运行新测试与全部 Method 回归**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_dispatch.py tests/test_method_v05_models.py -q
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v04_models.py tests/test_method_v03.py tests/test_method_v02.py tests/test_method_m1a.py tests/test_method_m1b_workflow.py tests/test_method_map.py tests/test_workspace_v02.py -q
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
```

Expected: 全部 passed，0.4 及更早的用例数量不变。

- [ ] **Step 7: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py src/memory_service_runtime/governed/method_service.py src/memory_service_runtime/governed/method_readers.py src/memory_service_runtime/governed/method_access.py src/memory_service_runtime/governed/workbench.py src/memory_service_runtime/governed/governance.py src/memory_service_runtime/governed/profile.py src/memory_service_runtime/governed/control.py src/memory_service_runtime/governed/protocol.py src/memory_service_runtime/governed/dashboard.py src/memory_service_runtime/governed/method_map.py src/memory_service_app/governance_commands.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): route tkos.method/0.5 through a delegating executor

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 5: 议题主 Scope（假设 A2）

**Files:**
- Modify: `src/memory_service_runtime/governed/dashboard.py`（`_normalised_business` 中 StrategicIssue 的字段列表；见 Step 3）
- Test: `tests/test_method_v05_dispatch.py`（已含 `_check_issue_scope`），`tests/dashboard/test_dashboard_logic.py`

**Interfaces:**
- Consumes: Task 2 的 `StrategicIssuePayload.primary_scope_id`；Task 4 的 `_check_issue_scope`。
- Produces: 看板 `business.primary_scope_id` 字段；Task 10 的 issues SQL 以 `payload->>'primary_scope_id'` 过滤。

- [ ] **Step 1: 写失败的看板投影测试**

在 `tests/dashboard/test_dashboard_logic.py` 末尾追加：

```python
def test_v05_issue_business_keeps_the_recorded_primary_scope():
    business = dashboard._normalised_business("StrategicIssue", {
        "title": "Issue", "summary": "s", "core_question": "q", "business_scope": "strategic",
        "urgency": "red", "urgency_reason": "why", "source_refs": [], "primary_scope_id": "bf-1"})
    assert business["primary_scope_id"] == "bf-1"
    assert dashboard._normalised_business("StrategicIssue", {"title": "Issue"}).get("primary_scope_id") is None
```

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_dashboard_logic.py::test_v05_issue_business_keeps_the_recorded_primary_scope -q
```

Expected: FAIL（`KeyError` 或返回值缺少 `primary_scope_id`）。

- [ ] **Step 3: 在 `_normalised_business` 保留该字段**

`dashboard.py:769` 的 `_normalised_business(kind, payload)` 是所有类型共用的白名单投影。在返回字典里 `"level": payload.get("level"),`（第 816 行）之后加两行：

```python
        "primary_scope_id": payload.get("primary_scope_id"),
        "subject_scope_id": payload.get("subject_scope_id"),
```

（第二行给 Task 6 的 Scope 级 State 用；两者对 0.4 及更早的 payload 都是 `None`，不改变旧投影的其它键。）

- [ ] **Step 4: 运行确认通过**

```bash
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_dispatch.py -q
```

Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/dashboard.py tests/dashboard/test_dashboard_logic.py
git commit -m "feat(method): expose the 0.5 issue primary Scope in dashboard projections

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 6: Scope 级 OperatingState 的授权与读取围栏（假设 A1）

**Files:**
- Modify: `src/memory_service_runtime/governed/method_access.py`（在 `state_subject_assignment` 之后新增 `scope_state_subject_assignment`）
- Modify: `src/memory_service_runtime/governed/dashboard.py:1222-1231`（`_subject_outcome_view` 对 Scope 主体返回明确缺失）
- Test: `tests/test_method_v05_scope_state.py`

**Interfaces:**
- Consumes: `method_access.assignment(conn, ctx, assignment_id, principal_id, principal_type)`、`method_access.raw_revision(conn, ctx, object_id, revision_id)`、`db._assignments(conn, ctx)`；Task 4 的 `method_v05.scoped_assignment` / `state_subject_owner_static` 已调用本任务的函数。
- Produces: `method_access.scope_state_subject_assignment(conn, ctx, payload) -> assignment dict`（含 `domain_id`）。

- [ ] **Step 1: 写失败的授权测试（无数据库，monkeypatch）**

创建 `tests/test_method_v05_scope_state.py`：

```python
"""Scope-level State responsibility resolves to the mapped Scope DRI only (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import method_access as access, method_v05
from memory_service_runtime.governed.errors import GovernedError


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


class Conn:
    def __init__(self, head):
        self.head = head

    def execute(self, sql, params=None):
        return SimpleNamespace(fetchone=lambda: self.head, fetchall=lambda: [self.head] if self.head else [])


def architecture(auth_domain):
    return {'title': 'A', 'battlefields': [{'unit_id': 'bf-1', 'auth_domain_id': auth_domain}],
            'domains': [{'unit_id': 'dom-1', 'auth_domain_id': None}]}


def test_scope_dri_gets_the_scoped_assignment(monkeypatch):
    auth_domain, principal, assignment_id = str(uuid4()), str(uuid4()), str(uuid4())
    subject = ref()
    ctx = SimpleNamespace(scope_id=str(uuid4()), principal_id=principal, principal_type='human')
    head = {'object_type': 'StrategicArchitecture', 'domain_id': str(uuid4()), 'effective_revision_id': subject['revision_id']}
    monkeypatch.setattr(access, 'raw_revision', lambda conn, ctx, oid, rid: {'payload': architecture(auth_domain)})
    monkeypatch.setattr(access.db, '_assignments', lambda conn, ctx: [
        {'assignment_id': assignment_id, 'domain_id': auth_domain, 'role': 'DOMAIN_DRI', 'principal_id': principal}])
    monkeypatch.setattr(access, 'assignment', lambda conn, ctx, aid, pid, kind: {'assignment_id': aid, 'domain_id': auth_domain})
    member = access.scope_state_subject_assignment(Conn(head), ctx, {'subject_ref': subject, 'subject_scope_id': 'bf-1'})
    assert member == {'assignment_id': assignment_id, 'domain_id': auth_domain}


@pytest.mark.parametrize('scope_id,role,kind', [
    ('dom-1', 'DOMAIN_DRI', 'human'),   # Scope without an explicit auth-domain mapping
    ('bf-1', 'CEO', 'human'),           # CEO is not the Scope DRI
    ('bf-1', 'DOMAIN_DRI', 'agent'),    # an Agent never holds the scoped confirmation
    ('bf-9', 'DOMAIN_DRI', 'human'),    # unknown unit
])
def test_scope_state_is_forbidden_without_the_mapped_current_dri(monkeypatch, scope_id, role, kind):
    auth_domain, principal = str(uuid4()), str(uuid4())
    subject = ref()
    ctx = SimpleNamespace(scope_id=str(uuid4()), principal_id=principal, principal_type=kind)
    head = {'object_type': 'StrategicArchitecture', 'domain_id': str(uuid4()), 'effective_revision_id': subject['revision_id']}
    monkeypatch.setattr(access, 'raw_revision', lambda conn, ctx, oid, rid: {'payload': architecture(auth_domain)})
    monkeypatch.setattr(access.db, '_assignments', lambda conn, ctx: [
        {'assignment_id': str(uuid4()), 'domain_id': auth_domain, 'role': role, 'principal_id': principal}])
    with pytest.raises(GovernedError) as exc:
        access.scope_state_subject_assignment(Conn(head), ctx, {'subject_ref': subject, 'subject_scope_id': scope_id})
    assert exc.value.code == 'FORBIDDEN'


def test_non_scope_state_still_uses_the_0_4_path(monkeypatch):
    called = []
    monkeypatch.setattr(method_v05.v4, 'scoped_assignment', lambda *args: called.append(args) or {'domain_id': 'd'})
    out = method_v05.scoped_assignment(None, None, 'method_confirm_state', {}, {'payload': {'subject_ref': ref()}})
    assert out == {'domain_id': 'd'} and len(called) == 1
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_scope_state.py -q
```

Expected: `AttributeError: module ... has no attribute 'scope_state_subject_assignment'`。

- [ ] **Step 3: 写授权函数**

在 `method_access.py` 的 `state_subject_assignment` 函数之后加：

```python
def scope_state_subject_assignment(conn, ctx, payload):
    """0.5 Scope 级 State：调用者必须是该 Scope 映射授权域中的当前 DOMAIN_DRI 本人。

    Architecture 里的映射只用来定位授权域，从不授予访问；最终仍由
    ``assignment`` 校验任职当前有效。
    """
    subject = payload['subject_ref']
    head = conn.execute('SELECT object_type,domain_id,effective_revision_id FROM gov_objects WHERE scope_id=%s AND object_id=%s',
                        (ctx.scope_id, subject['object_id'])).fetchone()
    if not head or head['object_type'] != 'StrategicArchitecture' or not head['effective_revision_id']:
        raise GovernedError('FORBIDDEN')
    revision = raw_revision(conn, ctx, subject['object_id'], str(head['effective_revision_id']))
    units = [*revision['payload'].get('battlefields', []), *revision['payload'].get('domains', [])]
    definition = next((u for u in units if u.get('unit_id') == payload.get('subject_scope_id')), None)
    if definition is None or not definition.get('auth_domain_id') or ctx.principal_type != 'human':
        raise GovernedError('FORBIDDEN')
    for row in db._assignments(conn, ctx):
        if row['role'] == 'DOMAIN_DRI' and row['domain_id'] == definition['auth_domain_id']:
            return assignment(conn, ctx, row['assignment_id'], ctx.principal_id, 'human')
    raise GovernedError('FORBIDDEN')
```

- [ ] **Step 4: 看板对 Scope 主体给出明确缺失**

`dashboard.py:1222` 的 `_subject_outcome_view` 第一行判断改为：

```python
    subject = payload.get("subject_ref")
    if payload.get("subject_scope_id"):
        return _missing("scope_subject_has_no_outcome")
    if not isinstance(subject, dict) or not subject.get("outcome_id"):
        return _missing("no_outcome_recorded")
```

- [ ] **Step 5: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_v05_scope_state.py tests/test_method_v05_dispatch.py -q
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
```

Expected: 全部 passed。

- [ ] **Step 6: 提交**

```bash
git add src/memory_service_runtime/governed/method_access.py src/memory_service_runtime/governed/dashboard.py tests/test_method_v05_scope_state.py
git commit -m "feat(method): resolve Scope-level State responsibility to the mapped current DRI

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 7: 证据锚点（假设 A3）

**Files:**
- Create: `src/memory_service_runtime/governed/evidence_anchor.py`
- Modify: `src/memory_service_runtime/governed/routes.py:66-72`（`EvidenceRequest`）、`:268-283`（`evidence_create`）
- Modify: `src/memory_service_runtime/governed/dashboard.py:88-113`（`DOWNSTREAM_FIELDS` 加 `EvidenceAsset`）
- Test: `tests/test_evidence_anchor.py`

**Interfaces:**
- Consumes: `db.object_row(conn, ctx, object_id)`、`db.revision_row(conn, ctx, object_id, revision_id)`、`protocol.evidence_protocol_fields(...)["contract_version"]`、`method_m1a_models.ExactRef`。
- Produces: `evidence_anchor.EvidenceAnchor`（pydantic）、`evidence_anchor.resolve(conn, ctx, contract_version, anchor) -> dict`（返回要并入证据 payload 的 `anchor_kind` / `anchor_ref` / `anchor_scope_id`）。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_evidence_anchor.py`：

```python
"""0.5 evidence must be anchored at birth; older contracts reject the field (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import evidence_anchor as anchor
from memory_service_runtime.governed.errors import GovernedError


def ref(payload_hash='a' * 64):
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': payload_hash}


@pytest.mark.parametrize('body', [
    {'kind': 'company', 'object_ref': None, 'scope_id': 'bf-1'},
    {'kind': 'scope', 'object_ref': None, 'scope_id': 'bf-1'},
    {'kind': 'object', 'object_ref': None},
    {'kind': 'object', 'object_ref': ref(), 'scope_id': 'bf-1'},
    {'kind': 'elsewhere'},
])
def test_anchor_shape_is_exact(body):
    with pytest.raises(ValidationError):
        anchor.EvidenceAnchor.model_validate(body)


def test_older_contracts_reject_the_field_and_0_5_requires_it():
    with pytest.raises(GovernedError) as exc:
        anchor.resolve(None, None, 'tkos.method/0.4', anchor.EvidenceAnchor(kind='company'))
    assert exc.value.code == 'INVALID_REQUEST'
    assert anchor.resolve(None, None, 'tkos.method/0.4', None) == {}
    with pytest.raises(GovernedError) as exc:
        anchor.resolve(None, None, 'tkos.method/0.5', None)
    assert exc.value.code == 'INVALID_REQUEST'
    assert anchor.resolve(None, None, 'tkos.method/0.5', anchor.EvidenceAnchor(kind='company')) == {'anchor_kind': 'company'}


def test_scope_anchor_checks_unit_and_hash(monkeypatch):
    target = ref()
    monkeypatch.setattr(anchor.db, 'object_row', lambda conn, ctx, oid: {'object_id': oid, 'object_type': 'StrategicArchitecture'})
    monkeypatch.setattr(anchor.db, 'revision_row', lambda conn, ctx, oid, rid: {
        'payload_hash': 'a' * 64, 'payload': {'battlefields': [{'unit_id': 'bf-1'}], 'domains': []}})
    fields = anchor.resolve(None, SimpleNamespace(), 'tkos.method/0.5',
                            anchor.EvidenceAnchor(kind='scope', object_ref=target, scope_id='bf-1'))
    assert fields == {'anchor_kind': 'scope', 'anchor_ref': target, 'anchor_scope_id': 'bf-1'}
    with pytest.raises(GovernedError) as exc:
        anchor.resolve(None, SimpleNamespace(), 'tkos.method/0.5',
                       anchor.EvidenceAnchor(kind='scope', object_ref=target, scope_id='bf-9'))
    assert exc.value.code == 'INVALID_REQUEST'
    with pytest.raises(GovernedError) as exc:
        anchor.resolve(None, SimpleNamespace(), 'tkos.method/0.5',
                       anchor.EvidenceAnchor(kind='object', object_ref=ref('b' * 64)))
    assert exc.value.code == 'VERSION_CONFLICT'
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_evidence_anchor.py -q
```

Expected: `ModuleNotFoundError`。

- [ ] **Step 3: 写锚点模块**

创建 `src/memory_service_runtime/governed/evidence_anchor.py`：

```python
"""0.5 证据锚点：出生即归框。

只对 ``tkos.method/0.5`` scope 生效；更早契约的上传不接受该字段（拒绝而不是忽略）。
解析在任何对象存储写入之前完成，失败不留下 S3 对象或数据库行。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import db
from .errors import GovernedError
from .method_m1a_models import ExactRef

ANCHOR_CONTRACT = "tkos.method/0.5"


class EvidenceAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["company", "scope", "object"]
    object_ref: ExactRef | None = None
    scope_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def shape(self) -> "EvidenceAnchor":
        if self.kind == "company" and (self.object_ref is not None or self.scope_id is not None):
            raise ValueError("a company anchor names no object")
        if self.kind == "scope" and (self.object_ref is None or self.scope_id is None):
            raise ValueError("a scope anchor names the exact Architecture revision and the stable unit")
        if self.kind == "object" and (self.object_ref is None or self.scope_id is not None):
            raise ValueError("an object anchor names exactly one exact object revision")
        return self


def resolve(conn, ctx, contract_version: str, anchor: EvidenceAnchor | None) -> dict:
    """返回要并入证据 payload 的锚点字段；不可见对象按 NOT_FOUND 失败。"""
    if contract_version != ANCHOR_CONTRACT:
        if anchor is not None:
            raise GovernedError("INVALID_REQUEST", "Evidence anchors belong to tkos.method/0.5 scopes", status=422)
        return {}
    if anchor is None:
        raise GovernedError("INVALID_REQUEST", "tkos.method/0.5 evidence must name its anchor at upload", status=422)
    fields: dict = {"anchor_kind": anchor.kind}
    if anchor.kind == "company":
        return fields
    reference = anchor.object_ref.model_dump(mode="json")
    obj = db.object_row(conn, ctx, reference["object_id"])
    revision = db.revision_row(conn, ctx, reference["object_id"], reference["revision_id"])
    if revision["payload_hash"] != reference["payload_hash"]:
        raise GovernedError("VERSION_CONFLICT", "Anchor payload hash does not match the named revision", status=409)
    fields["anchor_ref"] = reference
    if anchor.kind == "scope":
        if obj["object_type"] != "StrategicArchitecture":
            raise GovernedError("INVALID_REQUEST", "A scope anchor names a StrategicArchitecture revision", status=422)
        units = [*revision["payload"].get("battlefields", []), *revision["payload"].get("domains", [])]
        if not any(unit.get("unit_id") == anchor.scope_id for unit in units):
            raise GovernedError("INVALID_REQUEST", "The anchor Scope is absent from the named Architecture revision", status=422)
        fields["anchor_scope_id"] = anchor.scope_id
    return fields
```

- [ ] **Step 4: 接入上传路由**

`routes.py` 顶部导入加 `from . import evidence_anchor`。`EvidenceRequest` 增加字段：

```python
    anchor: evidence_anchor.EvidenceAnchor | None = None
```

`evidence_create` 中，在 `creation = protocol.evidence_protocol_fields(...)` 之后、`payload = evidence.store_bytes(...)` 之前加：

```python
        anchor_fields = evidence_anchor.resolve(conn, ctx, creation["contract_version"], body.anchor)
```

并把 `payload = evidence.store_bytes(...)` 改为：

```python
        payload = {**evidence.store_bytes(ctx, str(body.domain_id), body.title, content, body.media_type),
                   **anchor_fields}
```

（`digest`、revision 插入与回执沿用原代码，锚点字段随 payload 一起进入 `payload_hash`。）

- [ ] **Step 5: 看板 downstream 认识锚点**

`dashboard.py` 的 `DOWNSTREAM_FIELDS` 增加一行 `"EvidenceAsset": ("anchor_ref",),`（Task 9 会把整张表改为由注册表派生，届时这一行随之迁移）。

- [ ] **Step 6: 运行测试与 OpenAPI 快照检查**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_evidence_anchor.py -q
DATABASE_URL=postgresql://unused MEMORY_TENANT=t MEMORY_ORG=o .venv/bin/python -c "from memory_service_app.main import app; import json; s=app.openapi(); print('anchor' in json.dumps(s['components']['schemas']['EvidenceRequest']))"
```

Expected: 测试 passed；第二条打印 `True`。

- [ ] **Step 7: 提交**

```bash
git add src/memory_service_runtime/governed/evidence_anchor.py src/memory_service_runtime/governed/routes.py src/memory_service_runtime/governed/dashboard.py tests/test_evidence_anchor.py
git commit -m "feat(runtime): require an anchor on tkos.method/0.5 evidence uploads

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 8: 关系注册表模块与覆盖测试

**Files:**
- Create: `src/memory_service_runtime/governed/relations.py`
- Test: `tests/test_relations.py`

**Interfaces:**
- Consumes: `method_models.registry(version)` 的 `PAYLOAD_MODELS`（0.4、0.5）。
- Produces: `relations.Relation`（frozen dataclass）、`relations.RELATIONS`、`relations.for_version(version) -> list[Relation]`、`relations.downstream_fields() -> dict[str, tuple[str, ...]]`、`relations.downstream_array_fields() -> frozenset[str]`、`relations.predicate_for(object_type, field_path) -> str | None`、`relations.catalog(version) -> dict`。Task 9 用前四个替换看板的字面量，Task 13 的前端消费 `catalog()` 的 JSON。

- [ ] **Step 1: 写失败的覆盖测试**

创建 `tests/test_relations.py`：

```python
"""关系显式化的机械保证：0.4/0.5 每个 ExactRef 字段都在注册表里，反之亦然。"""
from __future__ import annotations

import types
from typing import Annotated, Union, get_args, get_origin

from pydantic import BaseModel

from memory_service_runtime.governed import method_models, relations
from memory_service_runtime.governed.method_m1a_models import ExactRef

# 改动前 dashboard.py 中的字面量；注册表必须逐字复现，看板行为才不变。
LEGACY_DOWNSTREAM_FIELDS = {
    "Strategy": ("source_agreement_ref", "source_proposal_ref"),
    "StrategicArchitecture": ("strategy_ref", "source_agreement_ref", "source_proposal_ref"),
    "StrategicJudgment": ("strategy_ref", "source_agreement_ref", "source_proposal_ref"),
    "LTCO": ("strategy_ref", "architecture_ref", "advice_ref", "baseline_refs"),
    "PCO": ("strategy_ref", "ltco_ref", "parent_ltco_ref", "architecture_ref"),
    "Mission": ("pco_ref", "parent_pco_ref", "architecture_ref", "evidence_refs"),
    "OperatingState": ("subject_ref", "baseline_refs", "evidence_refs"),
    "OperatingProblem": ("state_ref", "evidence_refs"),
    "BusinessFact": ("subject_ref", "corrects_ref", "source_ref"),
    "PeriodReview": ("state_refs", "target_refs", "fact_refs"),
    "LTCOReviewAdvice": ("period_review_ref", "strategy_ref", "ltco_ref"),
    "ReviewWindow": ("strategy_ref", "architecture_ref", "ltco_ref", "ltco_refs",
                     "target_refs", "pco_refs", "mission_refs", "previous_window_ref"),
    "CandidateSet": ("window_ref", "strategy_ref", "architecture_ref", "ltco_ref", "ltco_refs",
                     "target_refs"),
    "Signal": ("source_refs",),
    "PotentialIssue": ("signal_refs", "source_refs"),
    "StrategicIssue": ("potential_issue_ref", "direct_source_refs", "source_refs",
                       "reframe_of_ref", "strategy_ref", "architecture_ref"),
    "ResearchMemo": ("issue_ref", "source_refs"),
    "ResearchPlan": ("issue_ref", "memo_ref"),
    "ResearchReport": ("issue_ref", "plan_ref", "evidence_refs"),
    "ResearchBrief": ("issue_ref", "source_refs"),
    "MeetingRound": ("issue_ref", "report_ref", "brief_ref", "material_refs"),
    "MeetingMinutes": ("issue_ref", "meeting_ref", "source_refs"),
    "StrategicAgreement": ("issue_ref", "meeting_ref", "minutes_ref", "evidence_refs"),
    "StrategyUpdateProposal": ("issue_ref", "agreement_ref"),
    "EvidenceAsset": ("anchor_ref",),
}
LEGACY_ARRAY_FIELDS = frozenset({
    "state_refs", "target_refs", "fact_refs", "source_refs", "signal_refs",
    "direct_source_refs", "evidence_refs", "baseline_refs", "material_refs",
    "ltco_refs", "pco_refs", "mission_refs",
})


def _unwrap(annotation):
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def ref_fields(model, payload_models, prefix=""):
    """所有 ExactRef 或 list[ExactRef] 字段路径；嵌套模型递归，已注册的顶层 payload 不再下钻。"""
    found = set()
    for name, field in model.model_fields.items():
        annotation = _unwrap(field.annotation)
        origin = get_origin(annotation)
        candidates = [annotation]
        if origin in (Union, types.UnionType):
            candidates = [_unwrap(a) for a in get_args(annotation) if a is not type(None)]
        for candidate in candidates:
            path = prefix + name
            if get_origin(candidate) is list:
                candidate = _unwrap(get_args(candidate)[0])
                path += "[]"
            if isinstance(candidate, type) and issubclass(candidate, ExactRef):
                found.add(path)
            elif (isinstance(candidate, type) and issubclass(candidate, BaseModel)
                  and candidate not in payload_models):
                found |= ref_fields(candidate, payload_models, path + ".")
    return found


def test_every_exact_ref_field_of_0_4_and_0_5_has_exactly_one_registry_row():
    for version in ("tkos.method/0.4", "tkos.method/0.5"):
        _params, _targets, payloads = method_models.registry(version)
        models = set(payloads.values())
        for object_type, model in payloads.items():
            expected = ref_fields(model, models)
            declared = [r.field for r in relations.for_version(version)
                        if r.from_type == object_type and r.status in {"exact_ref", "exact_ref_nested"}]
            assert sorted(declared) == sorted(expected), (version, object_type, set(declared) ^ expected)
            assert len(declared) == len(set(declared)), (version, object_type)


def test_registry_reproduces_the_dashboard_downstream_map_verbatim():
    assert relations.downstream_fields() == LEGACY_DOWNSTREAM_FIELDS
    assert relations.downstream_array_fields() == LEGACY_ARRAY_FIELDS


def test_predicates_and_catalog_shape():
    assert relations.predicate_for("Mission", "parent_pco_ref") == "contributesTo"
    assert relations.predicate_for("OperatingState", "evidence_refs[]") == "basedOn"
    assert relations.predicate_for("StrategyUpdateProposal", "change.strategy_target_ref") == "retains"
    assert relations.predicate_for("Mission", "unknown_field") is None
    catalog = relations.catalog("tkos.method/0.5")
    assert catalog["contract_version"] == "tkos.method/0.5"
    keys = {"predicate", "from_type", "to_type", "cardinality", "field", "versions", "label", "status", "load_bearing", "m0"}
    assert all(keys == set(row) for row in catalog["relations"])
    assert {r["predicate"] for r in catalog["relations"] if r["load_bearing"]} >= {
        "scopedTo", "advances", "contributesTo", "resolves", "assesses", "anchoredTo"}
    assert not any(r["field"] == "anchor_ref" for r in relations.catalog("tkos.method/0.4")["relations"])
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_relations.py -q
```

Expected: `ModuleNotFoundError: ... relations`。

- [ ] **Step 3: 写注册表模块**

创建 `src/memory_service_runtime/governed/relations.py`：

```python
"""关系注册表：M0《本体图结构梳理》4.2 关系表到运行时 payload 字段的显式映射。

三个用途共用一份数据，不再各自维护字面量：
* 看板 downstream / detail 的类型化引用字段（``downstream_fields``）；
* 对象详情里每条引用的 M0 谓词（``predicate_for``）；
* ``GET /v1/dashboard/ontology/relations`` 给治理工作台与 Clark 的注册表（``catalog``）。

``status`` 说明该 M0 边在运行时的落法：
``exact_ref`` 顶层 ExactRef 字段；``exact_ref_nested`` 嵌套 ExactRef（不参与 downstream SQL）；
``scope_id`` 稳定 unit_id；``principal_id`` 主体 id；``value`` 值字段；
``derived`` 由任职/修订/状态派生；``text_only`` 只有自由文本；``not_implemented`` 未交付。
这里只是读取用的数据，不是任何服务端规则。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

OLD = ("tkos.method/0.1", "tkos.method/0.2", "tkos.method/0.3")
V45 = ("tkos.method/0.4", "tkos.method/0.5")
V3P = ("tkos.method/0.3", *V45)
V5 = ("tkos.method/0.5",)
ALL = (*OLD, *V45)
EXACT = frozenset({"exact_ref", "exact_ref_nested"})


@dataclass(frozen=True)
class Relation:
    predicate: str            # 运行时字段方向上的谓词；旧类型沿用字段含义
    from_type: str            # 持有字段的对象类型，或虚拟节点 Company/Scope
    to_type: str              # 对象类型、虚拟节点（Company/Scope/Person/Period/Level）或 "*"
    cardinality: str          # 从 from 看向 to
    field: str | None         # payload 字段路径；"[]" 表示列表，"." 表示嵌套；派生/未实现为 None
    versions: tuple[str, ...]
    label: str                # 业务语言标签（中文）
    status: str = "exact_ref"
    load_bearing: bool = False  # M0 加粗的承重边
    m0: str | None = None       # 对应的 M0 谓词（按 M0 的方向）


R = Relation
RELATIONS: tuple[Relation, ...] = (
    # ---- Strategy / Architecture（顺序 = 看板 downstream 字面量顺序）
    R("derivedFrom", "Strategy", "StrategicAgreement", "N:1", "source_agreement_ref", ALL, "由该共识产生", m0="updates"),
    R("derivedFrom", "Strategy", "StrategyUpdateProposal", "N:1", "source_proposal_ref", ALL, "由该提案确认产生"),
    R("expresses", "StrategicArchitecture", "Strategy", "1:1", "strategy_ref", V3P, "表达该战略的责任结构", load_bearing=True, m0="expressedAs"),
    R("derivedFrom", "StrategicArchitecture", "StrategicAgreement", "N:1", "source_agreement_ref", V45, "由该共识产生", m0="updates"),
    R("derivedFrom", "StrategicArchitecture", "StrategyUpdateProposal", "N:1", "source_proposal_ref", V3P, "由该提案确认产生"),
    R("judges", "StrategicJudgment", "Strategy", "N:1", "strategy_ref", OLD, "判断所属战略"),
    R("derivedFrom", "StrategicJudgment", "StrategicAgreement", "N:1", "source_agreement_ref", OLD, "由该共识产生"),
    R("derivedFrom", "StrategicJudgment", "StrategyUpdateProposal", "N:1", "source_proposal_ref", OLD, "由该提案确认产生"),
    # ---- LTCO
    R("serves", "LTCO", "Strategy", "N:1", "strategy_ref", ALL, "服务的战略（指整个 Strategy 版本，尚未到 Choice 粒度）", m0="serves"),
    R("basedOnArchitecture", "LTCO", "StrategicArchitecture", "N:1", "architecture_ref", V3P, "责任结构依据"),
    R("advisedBy", "LTCO", "LTCOReviewAdvice", "0:1", "advice_ref", OLD, "审视建议"),
    R("basedOn", "LTCO", "*", "N:M", "baseline_refs[]", ALL, "基准依据"),
    R("scopedTo", "LTCO", "Scope", "N:1", "primary_scope_id", V45, "唯一主责 Scope", status="scope_id", load_bearing=True, m0="scopedTo"),
    R("inPeriod", "LTCO", "Period", "N:1", "period", ALL, "所属周期（起止值，非节点）", status="value", m0="inPeriod"),
    # ---- PCO
    R("serves", "PCO", "Strategy", "N:1", "strategy_ref", ALL, "服务的战略"),
    R("advances", "PCO", "LTCO", "N:1", "ltco_ref", OLD, "推进的长期结果", load_bearing=True, m0="advances"),
    R("advances", "PCO", "LTCO", "N:1", "parent_ltco_ref", V45, "推进的长期结果", load_bearing=True, m0="advances"),
    R("basedOnArchitecture", "PCO", "StrategicArchitecture", "N:1", "architecture_ref", V3P, "责任结构依据"),
    R("scopedTo", "PCO", "Scope", "N:1", "primary_scope_id", V45, "唯一主责 Scope", status="scope_id", load_bearing=True, m0="scopedTo"),
    R("inPeriod", "PCO", "Period", "N:1", "period", ALL, "所属周期（起止值，非节点）", status="value", m0="inPeriod"),
    # ---- Mission
    R("contributesTo", "Mission", "PCO", "N:1", "pco_ref", OLD, "贡献的周期结果", load_bearing=True, m0="contributesTo"),
    R("contributesTo", "Mission", "PCO", "N:1", "parent_pco_ref", V45, "贡献的周期结果", load_bearing=True, m0="contributesTo"),
    R("basedOnArchitecture", "Mission", "StrategicArchitecture", "N:1", "architecture_ref", V3P, "责任结构依据"),
    R("tracesTo", "Mission", "EvidenceAsset", "N:M", "evidence_refs[]", ALL, "证据", m0="tracesTo"),
    R("ownedBy", "Mission", "Person", "N:1", "owner_principal_id", ALL, "唯一 Owner", status="principal_id", m0="ownedBy"),
    R("scopedTo", "Mission", "Scope", "N:1", "primary_scope_id", V3P, "唯一主责 Scope", status="scope_id", m0="scopedTo"),
    R("inPeriod", "Mission", "Period", "N:1", "period", ALL, "承诺窗口（起止值，非节点）", status="value", m0="inPeriod"),
    # ---- OperatingState / OperatingProblem
    R("assesses", "OperatingState", "*", "N:1", "subject_ref", V3P, "评估对象（0.5 为 Scope 时指向 Architecture 版本）", load_bearing=True, m0="assesses"),
    R("basedOn", "OperatingState", "*", "N:M", "baseline_refs[]", V3P, "判断基准"),
    R("basedOn", "OperatingState", "EvidenceAsset", "N:M", "evidence_refs[]", V3P, "证据", m0="basedOn"),
    R("assesses", "OperatingState", "Scope", "N:1", "subject_scope_id", V5, "评估的 Scope（同 Scope 同 as_of 唯一）", status="scope_id", load_bearing=True, m0="assesses"),
    R("arisesFrom", "OperatingProblem", "OperatingState", "N:1", "state_ref", V3P, "来源状态", m0="arisesFrom"),
    R("tracesTo", "OperatingProblem", "EvidenceAsset", "N:M", "evidence_refs[]", V3P, "证据", m0="tracesTo"),
    R("routedTo", "OperatingProblem", "Level", "N:1", "level", V3P, "责任层级 mission/domain/company/strategic（M0 用方法环节命名）", status="value", m0="routedTo"),
    R("escalatesTo", "OperatingProblem", "StrategicIssue", "0:1", None, V3P, "经 m1a_transfer_problem 移交为议题来源（method_state.transferred_problems）", status="derived", m0="escalatesTo"),
    # ---- BusinessFact / PeriodReview / LTCOReviewAdvice
    R("recordsFor", "BusinessFact", "*", "N:1", "subject_ref", OLD, "事实记录对象"),
    R("corrects", "BusinessFact", "BusinessFact", "0:1", "corrects_ref", OLD, "更正的原事实"),
    R("sourcedFrom", "BusinessFact", "EvidenceAsset", "N:1", "source_ref", OLD, "原始证据"),
    R("cites", "PeriodReview", "OperatingState", "N:M", "state_refs[]", V3P, "引用正式状态", m0="cites"),
    R("reviews", "PeriodReview", "PCO", "N:M", "target_refs[]", ALL, "复盘目标", m0="reviews"),
    R("cites", "PeriodReview", "BusinessFact", "N:M", "fact_refs[]", ALL, "引用事实（0.4/0.5 处理器拒绝非空）"),
    R("inPeriod", "PeriodReview", "Period", "N:1", "period", ALL, "复盘周期（起止值，非节点）", status="value", m0="inPeriod"),
    R("supports", "LTCOReviewAdvice", "PeriodReview", "N:1", "period_review_ref", OLD, "依据周期复盘"),
    R("serves", "LTCOReviewAdvice", "Strategy", "N:1", "strategy_ref", OLD, "战略依据"),
    R("advises", "LTCOReviewAdvice", "LTCO", "N:1", "ltco_ref", OLD, "审视对象"),
    # ---- ReviewWindow / CandidateSet
    R("serves", "ReviewWindow", "Strategy", "N:1", "strategy_ref", ALL, "战略依据"),
    R("basedOnArchitecture", "ReviewWindow", "StrategicArchitecture", "N:1", "architecture_ref", V3P, "责任结构依据"),
    R("freezes", "ReviewWindow", "LTCO", "N:1", "ltco_ref", OLD, "冻结的长期目标"),
    R("freezes", "ReviewWindow", "LTCO", "N:M", "ltco_refs[]", V45, "冻结确切长期结果集合"),
    R("freezes", "ReviewWindow", "*", "N:M", "target_refs[]", OLD, "冻结的核对目标"),
    R("freezes", "ReviewWindow", "PCO", "N:M", "pco_refs[]", V45, "冻结的周期结果成员"),
    R("freezes", "ReviewWindow", "Mission", "N:M", "mission_refs[]", V45, "冻结的 Mission 成员"),
    R("supersedes", "ReviewWindow", "ReviewWindow", "0:1", "previous_window_ref", ALL, "重开自的上一窗口", m0="supersedes"),
    R("resolvesWindow", "CandidateSet", "ReviewWindow", "N:1", "window_ref", ALL, "关窗收拢自"),
    R("serves", "CandidateSet", "Strategy", "N:1", "strategy_ref", ALL, "战略依据"),
    R("basedOnArchitecture", "CandidateSet", "StrategicArchitecture", "N:1", "architecture_ref", V3P, "责任结构依据"),
    R("advances", "CandidateSet", "LTCO", "N:1", "ltco_ref", OLD, "推进的长期目标"),
    R("advances", "CandidateSet", "LTCO", "N:M", "ltco_refs[]", V45, "推进的长期结果集合"),
    R("proposes", "CandidateSet", "*", "N:M", "target_refs[]", ALL, "候选目标集合"),
    # ---- 研究链（0.1–0.3）与议题
    R("sourcedFrom", "Signal", "EvidenceAsset", "N:M", "source_refs[]", OLD, "来源材料"),
    R("triggeredBy", "PotentialIssue", "Signal", "N:M", "signal_refs[]", OLD, "触发信号"),
    R("sourcedFrom", "PotentialIssue", "*", "N:M", "source_refs[]", OLD, "来源材料"),
    R("confirmedFrom", "StrategicIssue", "PotentialIssue", "N:1", "potential_issue_ref", OLD, "来源候选议题"),
    R("sourcedFrom", "StrategicIssue", "*", "N:M", "direct_source_refs[]", OLD, "直接来源"),
    R("triggeredBy", "StrategicIssue", "*", "N:M", "source_refs[]", ALL, "触发来源（证据/信号/问题）", m0="triggers"),
    R("supersedes", "StrategicIssue", "StrategicIssue", "0:1", "reframe_of_ref", V45, "重新界定自的上一版本", m0="supersedes"),
    R("concerns", "StrategicIssue", "Strategy", "N:1", "strategy_ref", V45, "关切的当前战略", m0="concerns"),
    R("basedOnArchitecture", "StrategicIssue", "StrategicArchitecture", "N:1", "architecture_ref", V45, "责任结构依据"),
    R("concerns", "StrategicIssue", "Scope", "0:1", "primary_scope_id", V5, "主要关切的 Scope（公司级议题可空）", status="scope_id", m0="concerns"),
    R("clarifies", "ResearchMemo", "StrategicIssue", "N:1", "issue_ref", OLD, "澄清的议题"),
    R("sourcedFrom", "ResearchMemo", "*", "N:M", "source_refs[]", OLD, "来源材料"),
    R("plansFor", "ResearchPlan", "StrategicIssue", "N:1", "issue_ref", OLD, "研究对象"),
    R("follows", "ResearchPlan", "ResearchMemo", "N:1", "memo_ref", OLD, "依据备忘"),
    R("reportsOn", "ResearchReport", "StrategicIssue", "N:1", "issue_ref", OLD, "研究对象"),
    R("follows", "ResearchReport", "ResearchPlan", "N:1", "plan_ref", OLD, "依据计划"),
    R("tracesTo", "ResearchReport", "EvidenceAsset", "N:M", "evidence_refs[]", OLD, "证据"),
    R("briefs", "ResearchBrief", "StrategicIssue", "N:1", "issue_ref", OLD, "研究对象"),
    R("sourcedFrom", "ResearchBrief", "*", "N:M", "source_refs[]", OLD, "来源材料"),
    R("discusses", "MeetingRound", "StrategicIssue", "N:1", "issue_ref", OLD, "讨论议题"),
    R("locks", "MeetingRound", "ResearchReport", "0:1", "report_ref", OLD, "锁定的报告"),
    R("locks", "MeetingRound", "ResearchBrief", "0:1", "brief_ref", OLD, "锁定的简报"),
    R("uses", "MeetingRound", "*", "N:M", "material_refs[]", OLD, "会议材料"),
    R("minutesOf", "MeetingMinutes", "StrategicIssue", "N:1", "issue_ref", OLD, "议题"),
    R("minutesOf", "MeetingMinutes", "MeetingRound", "N:1", "meeting_ref", OLD, "所属会议"),
    R("sourcedFrom", "MeetingMinutes", "*", "N:M", "source_refs[]", OLD, "来源材料"),
    # ---- Agreement / 更新提案
    R("resolves", "StrategicAgreement", "StrategicIssue", "N:1", "issue_ref", ALL, "解决的议题（同一议题可多轮）", load_bearing=True, m0="resolves"),
    R("reachedIn", "StrategicAgreement", "MeetingRound", "0:1", "meeting_ref", OLD, "达成于会议"),
    R("basedOnMinutes", "StrategicAgreement", "MeetingMinutes", "0:1", "minutes_ref", OLD, "依据纪要"),
    R("tracesTo", "StrategicAgreement", "EvidenceAsset", "N:M", "evidence_refs[]", ALL, "证据", m0="tracesTo"),
    R("proposesFor", "StrategyUpdateProposal", "StrategicIssue", "N:1", "issue_ref", ALL, "议题依据"),
    R("basedOnAgreement", "StrategyUpdateProposal", "StrategicAgreement", "N:1", "agreement_ref", ALL, "更新依据（Agreement 可为无变化）", load_bearing=True, m0="updates"),
    R("retains", "StrategyUpdateProposal", "Strategy", "0:1", "change.strategy_target_ref", V45, "未变对象保留的确切版本", status="exact_ref_nested"),
    R("retains", "StrategyUpdateProposal", "StrategicArchitecture", "0:1", "change.architecture_target_ref", V45, "未变对象保留的确切版本", status="exact_ref_nested"),
    # ---- 证据锚点（0.5）
    R("anchoredTo", "EvidenceAsset", "*", "N:1", "anchor_ref", V5, "出生即挂的唯一主锚点（对象或 Architecture 版本）", load_bearing=True, m0="anchoredTo"),
    R("anchoredTo", "EvidenceAsset", "Scope", "N:1", "anchor_scope_id", V5, "锚定的 Scope", status="scope_id", m0="anchoredTo"),
    R("anchoredTo", "EvidenceAsset", "Company", "N:1", "anchor_kind", V5, "anchor_kind=company 的公司级参考材料", status="value", m0="anchoredTo"),
    R("relatesTo", "EvidenceAsset", "*", "0:N", None, V5, "跨域次要关联；本版本未表达", status="not_implemented", m0="relatesTo"),
    # ---- 派生与虚拟节点（责任网络主干）
    R("partOf", "Scope", "Company", "N:1", None, V3P, "Battlefield/Domain 是 Architecture 的定义项，属于公司", status="derived", m0="partOf"),
    R("hasDRI", "Scope", "Person", "N:1", None, V45, "以 auth_domain_id 上当前唯一 DOMAIN_DRI 任职为准；Architecture 映射不授权", status="derived", m0="hasDRI"),
    R("basedOn", "Scope", "Strategy", "N:1", None, V3P, "Battlefield.strategic_basis 为自由文本，未指向确切 Choice", status="text_only", m0="basedOn"),
    R("hasStrategy", "Company", "Strategy", "1:1", None, ALL, "公司域当前正式战略（gov_method_strategy_heads）", status="derived"),
    R("confirmedBy", "*", "Person", "N:1", None, ALL, "确认记录与回执（gov_method_reviews / gov_action_receipts）", status="derived", m0="confirmedBy"),
    R("supersedes", "*", "*", "1:1", None, ALL, "同对象修订链（gov_object_revisions.object_version）", status="derived", m0="supersedes"),
    R("for", "Play", "Mission", "N:1", None, V5, "未交付", status="not_implemented", m0="for"),
    R("realizes", "HumanAIPlan", "Play", "1:1", None, V5, "未交付", status="not_implemented", m0="realizes"),
    R("partOf", "WorkPackage", "HumanAIPlan", "N:1", None, V5, "未交付", status="not_implemented", m0="partOf"),
    R("executedBy", "WorkPackage", "Person", "N:M", None, V5, "未交付", status="not_implemented", m0="executedBy"),
)


def for_version(version: str) -> list[Relation]:
    return [row for row in RELATIONS if version in row.versions]


def downstream_fields() -> dict[str, tuple[str, ...]]:
    """看板 downstream 的顶层 ExactRef 字段，按注册表顺序；嵌套字段不参与 SQL。"""
    result: dict[str, list[str]] = {}
    for row in RELATIONS:
        if row.status != "exact_ref" or row.field is None:
            continue
        fields = result.setdefault(row.from_type, [])
        name = row.field[:-2] if row.field.endswith("[]") else row.field
        if name not in fields:
            fields.append(name)
    return {kind: tuple(fields) for kind, fields in result.items()}


def downstream_array_fields() -> frozenset[str]:
    return frozenset(row.field[:-2] for row in RELATIONS
                     if row.status == "exact_ref" and row.field and row.field.endswith("[]"))


def predicate_for(object_type: str, field_path: str) -> str | None:
    for row in RELATIONS:
        if row.from_type == object_type and row.field == field_path:
            return row.predicate
    return None


def catalog(version: str) -> dict:
    return {"contract_version": version,
            "relations": [asdict(row) | {"versions": list(row.versions)} for row in for_version(version)]}


__all__ = ["Relation", "RELATIONS", "for_version", "downstream_fields",
           "downstream_array_fields", "predicate_for", "catalog"]
```

- [ ] **Step 4: 运行测试；按失败信息补齐/删除注册表行**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_relations.py -q
```

Expected: 3 passed。若第一条测试报出某类型的对称差，说明模型字段与注册表不一致：以模型为准补行或删行，不改模型。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/relations.py tests/test_relations.py
git commit -m "feat(runtime): add the M0 relation registry with exact-ref coverage tests

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 9: 注册表接入看板（downstream 派生、详情谓词、只读接口）

**Files:**
- Modify: `src/memory_service_runtime/governed/dashboard.py:88-113`（字面量改为派生）、`:2083-2100`（`detail()` 的 `relations` 加谓词）、`:1530` 之后新增 `ontology_relations()`
- Modify: `src/memory_service_runtime/governed/dashboard_routes.py:101-104`（新增 `read_ontology_relations`）、`:235-242`（新增路由）
- Modify: `src/memory_service_app/dashboard.py:314-326`（新增 facade 路由）
- Test: `tests/dashboard/test_dashboard_logic.py`、`tests/dashboard/test_dashboard_facade.py`

**Interfaces:**
- Consumes: Task 8 的 `relations.downstream_fields()`、`downstream_array_fields()`、`predicate_for()`、`catalog()`；`dashboard.ONTOLOGY_CONTRACT_VERSIONS`。
- Produces: `dashboard.ontology_relations(conn, ctx, contract_version)`；`dashboard_routes.read_ontology_relations(token, *, contract_version)`；`GET /v1/dashboard/ontology/relations?contract_version=`；facade `GET /dashboard/api/v1/ontology/relations?contract_version=`；`detail()["relations"]["own_basis_refs"][i]["predicate"]` 与 `upstream_refs[i]["predicate"]`。

- [ ] **Step 1: 写失败的测试**

在 `tests/dashboard/test_dashboard_logic.py` 末尾追加：

```python
def test_downstream_fields_come_from_the_relation_registry():
    from memory_service_runtime.governed import relations
    assert dashboard.DOWNSTREAM_FIELDS == relations.downstream_fields()
    assert dashboard.DOWNSTREAM_ARRAY_FIELDS == relations.downstream_array_fields()


def test_ref_path_to_registry_field():
    assert dashboard._registry_field("/evidence_refs/0") == "evidence_refs[]"
    assert dashboard._registry_field("/change/strategy_target_ref") == "change.strategy_target_ref"
    assert dashboard._registry_field("strategy_ref") == "strategy_ref"


def test_ontology_relations_rejects_unknown_versions(monkeypatch):
    monkeypatch.setattr(dashboard, "_read_at", lambda conn: "2026-09-20T00:00:00+00:00")
    result = dashboard.ontology_relations(None, CTX, "tkos.method/0.4")
    assert result["schema_version"] == dashboard.SCHEMA_VERSION
    assert result["contract_version"] == "tkos.method/0.4"
    assert any(r["predicate"] == "contributesTo" for r in result["relations"])
    with pytest.raises(GovernedError) as exc:
        dashboard.ontology_relations(None, CTX, "tkos.method/9.9")
    assert exc.value.code == "INVALID_REQUEST"
```

在 `tests/dashboard/test_dashboard_facade.py` 的 `calls` 夹具名单里加 `"read_ontology_relations"`，并追加：

```python
def test_relations_read_forwards_the_contract_version(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    assert fetch(app, "/dashboard/api/v1/ontology/relations").status_code == 422
    response = fetch(app, "/dashboard/api/v1/ontology/relations?contract_version=tkos.method/0.5")
    assert response.status_code == 200
    assert calls["read_ontology_relations"]["kwargs"] == {"contract_version": "tkos.method/0.5"}
    assert calls["read_ontology_relations"]["token"] == token_file.read_text()
```

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
```

Expected: 新增 4 个用例 FAIL（`AttributeError`），其余通过。

- [ ] **Step 3: 看板改为派生字段表并加谓词**

`dashboard.py`：

1. 顶部 `from . import db, method_access as access, method_readers, readers, protocol, workbench` 加 `relations`。
2. 删除第 88–113 行的 `DOWNSTREAM_FIELDS` / `DOWNSTREAM_ARRAY_FIELDS` 字面量（保留上方注释的意思），改为：

```python
# Typed inbound (downstream) reference fields, derived from the relation
# registry so the map, the detail predicates and this search share one source.
DOWNSTREAM_FIELDS: dict[str, tuple[str, ...]] = relations.downstream_fields()
DOWNSTREAM_ARRAY_FIELDS: frozenset[str] = relations.downstream_array_fields()
```

3. 在 `_resolve_refs` 之前加：

```python
def _registry_field(path: str) -> str:
    """``_tree_refs`` 路径（``/evidence_refs/0``）或裸字段名 → 注册表字段路径（``evidence_refs[]``）。"""
    parts = [part for part in path.split("/") if part]
    out: list[str] = []
    for part in parts:
        if part.isdigit():
            out[-1] = out[-1] + "[]"
        else:
            out.append(part)
    return ".".join(out)
```

4. `detail()` 里构造 `relations` 字典处（第 2083 行起），把 `own_basis_refs` 与 `upstream_refs` 两个列表包一层谓词标注：

```python
    def _with_predicates(items):
        return [{**item, "predicate": relations.predicate_for(head["object_type"], _registry_field(item["path"]))}
                for item in items]
```

（定义放在 `detail()` 内、`downstream_page = ...` 之后），然后 `"own_basis_refs": _with_predicates(_resolve_refs(...))`、`"upstream_refs": _with_predicates(upstream_refs)`。

5. 在 `ontology_catalog()` 之后加：

```python
def ontology_relations(conn: Any, ctx: Any, contract_version: str) -> dict[str, Any]:
    """M0 关系注册表的只读投影；不查业务数据，不返回实例。"""
    if contract_version not in ONTOLOGY_CONTRACT_VERSIONS:
        raise GovernedError("INVALID_REQUEST", "Unknown business rule version.", status=422)
    return db.jsonable({"schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
                        **relations.catalog(contract_version),
                        "note": "status 说明 M0 边在运行时的落法；derived/text_only/not_implemented 不是可查询的记录关系。"})
```

- [ ] **Step 4: 路由与 facade**

`dashboard_routes.py` 在 `read_ontology_catalog` 后加：

```python
def read_ontology_relations(token: str, *, contract_version: str) -> dict:
    with db.transaction(token) as (conn, ctx):
        return dashboard.ontology_relations(conn, ctx, contract_version)
```

在 `ontology_catalog_route` 后加：

```python
@router.get("/ontology/relations")
def ontology_relations_route(request: Request, response: Response,
                             token: Annotated[str, Depends(bearer)],
                             contract_version: Annotated[str, Query(min_length=1, max_length=64)]):
    strict_query(request.query_params, {"contract_version"})
    result = read_ontology_relations(token, contract_version=contract_version)
    response.headers["Cache-Control"] = "no-store"
    return result
```

`src/memory_service_app/dashboard.py` 在 `ontology_catalog` facade 后加：

```python
@api.get("/ontology/relations")
def ontology_relations(request: Request,
                       contract_version: Annotated[str, Query(min_length=1, max_length=64)]):
    invalid = _validate(request, {"contract_version"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error(request)
    if error:
        return error
    return _read(reads.read_ontology_relations, token, contract_version=contract_version)
```

- [ ] **Step 5: 运行看板测试与主回归里的看板 HTTP 用例**

```bash
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
DATABASE_URL=$DATABASE_URL uv run pytest tests/dashboard/test_dashboard_http.py tests/test_method_map.py -q
```

Expected: 全部 passed；`downstream` 相关既有用例结果不变。

- [ ] **Step 6: 提交**

```bash
git add src/memory_service_runtime/governed/dashboard.py src/memory_service_runtime/governed/dashboard_routes.py src/memory_service_app/dashboard.py tests/dashboard/test_dashboard_logic.py tests/dashboard/test_dashboard_facade.py
git commit -m "feat(runtime): derive dashboard reference fields from the relation registry and expose it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 10: Scope / Company 可寻址读取器（战场卡的数据源）

**Files:**
- Create: `src/memory_service_runtime/governed/scope_readers.py`
- Modify: `src/memory_service_runtime/governed/dashboard_routes.py`（新增 `read_company`、`read_scope` 与两条路由）
- Modify: `src/memory_service_app/dashboard.py`（新增两条 facade 路由）
- Test: `tests/dashboard/test_scope_readers.py`、`tests/dashboard/test_dashboard_facade.py`

**Interfaces:**
- Consumes: `dashboard._visible_head(conn, ctx, object_id) -> (head, allowed)`、`dashboard._visible_revision(conn, ctx, object_id, revision_id, allowed)`、`dashboard._domain_name`、`dashboard._payload_title`、`dashboard._missing`、`method_readers.object_state`、`method_v05.SCOPE_STATE_OUTCOME_PREFIX`。
- Produces: `scope_readers.company(conn, ctx, domain_id) -> dict`、`scope_readers.scope(conn, ctx, architecture_object_id, unit_id) -> dict`；`GET /v1/dashboard/company?domain_id=`、`GET /v1/dashboard/scopes/{architecture_object_id}/{unit_id}`；facade 同路径前缀 `/dashboard/api/v1`。Task 11 往 `scope()["issues"][i]` 里加 `lifecycle`。

- [ ] **Step 1: 写失败的读取器测试**

创建 `tests/dashboard/test_scope_readers.py`：

```python
"""Scope/Company 读取器：只返回当前身份可读的记录，缺失项显式标注（无数据库）。"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import scope_readers
from memory_service_runtime.governed.errors import GovernedError
from tests.dashboard.dashboard_fakes import CTX, head, revision, uid


class Conn:
    """按 SQL 片段返回固定行；未命中的语句返回空。"""

    def __init__(self, rows_by_marker):
        self.rows_by_marker = rows_by_marker

    def execute(self, sql, params=None):
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return _Result(rows)
        return _Result([])


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


ARCH, ARCH_REV, AUTH, DRI = uid(10), uid(11), uid(12), uid(13)
ARCHITECTURE = {"title": "A", "strategy_ref": None,
                "battlefields": [{"unit_id": "bf-1", "name": "iOS", "definition": "d", "strategic_basis": ["b"],
                                  "boundary": "x", "auth_domain_id": AUTH}],
                "domains": [{"unit_id": "dom-1", "name": "ENO", "definition": "d", "responsibility": "r",
                             "auth_domain_id": None}]}


@pytest.fixture
def visible(monkeypatch):
    heads = {ARCH: head(ARCH, "StrategicArchitecture", latest=ARCH_REV, effective=ARCH_REV)}
    revisions = {(ARCH, ARCH_REV): revision(ARCH, ARCH_REV, ARCHITECTURE)}

    def _head(conn, ctx, object_id):
        if object_id not in heads:
            raise GovernedError("NOT_FOUND")
        return heads[object_id], True

    monkeypatch.setattr(scope_readers, "_visible_head", _head)
    monkeypatch.setattr(scope_readers, "_visible_revision", lambda c, x, oid, rid, allowed: revisions[(oid, rid)])
    monkeypatch.setattr(scope_readers, "_read_at", lambda conn: "2026-09-20T00:00:00+00:00")
    return heads, revisions


def test_scope_view_names_the_definition_dri_and_explicit_gaps(visible):
    conn = Conn({"role='DOMAIN_DRI'": [{"assignment_id": uid(20), "principal_id": DRI, "display_name": "Scope DRI"}]})
    view = scope_readers.scope(conn, CTX, ARCH, "bf-1")
    assert view["definition"]["unit_id"] == "bf-1" and view["definition"]["kind"] == "battlefield"
    assert view["current_dri"] == {"status": "available", "reason": None,
                                   "value": {"assignment_id": uid(20), "principal_id": DRI, "display_name": "Scope DRI"}}
    assert view["latest_state"]["status"] == "missing" and view["latest_state"]["reason"] == "no_confirmed_scope_state"
    assert view["issues"] == [] and view["evidence"] == [] and view["commitments"] == []


def test_domain_without_mapping_has_no_dri_and_unknown_unit_is_not_found(visible):
    view = scope_readers.scope(Conn({}), CTX, ARCH, "dom-1")
    assert view["current_dri"]["reason"] == "no_authorization_domain_mapping"
    with pytest.raises(GovernedError) as exc:
        scope_readers.scope(Conn({}), CTX, ARCH, "bf-9")
    assert exc.value.code == "NOT_FOUND"


def test_two_current_dris_are_reported_not_chosen(visible):
    conn = Conn({"role='DOMAIN_DRI'": [{"assignment_id": uid(20), "principal_id": DRI, "display_name": "A"},
                                       {"assignment_id": uid(21), "principal_id": uid(22), "display_name": "B"}]})
    assert scope_readers.scope(conn, CTX, ARCH, "bf-1")["current_dri"]["reason"] == "dri_not_unique"


def test_company_view_lists_scopes_from_the_effective_architecture(visible, monkeypatch):
    heads, revisions = visible
    strategy, strategy_rev = uid(30), uid(31)
    heads[strategy] = head(strategy, "Strategy", latest=strategy_rev, effective=strategy_rev)
    revisions[(strategy, strategy_rev)] = revision(strategy, strategy_rev, {"title": "S", "statement": "s"})
    monkeypatch.setattr(scope_readers.method_readers, "object_state", lambda c, x, oid: {
        "method_state": {"architecture_ref": {"object_id": ARCH, "revision_id": ARCH_REV, "payload_hash": "a" * 64}}})
    conn = Conn({"gov_method_strategy_heads": [{"object_id": strategy, "revision_id": strategy_rev}]})
    view = scope_readers.company(conn, CTX, uid(1))
    assert view["strategy"]["ref"]["object_id"] == strategy
    assert [s["unit_id"] for s in view["scopes"]] == ["bf-1", "dom-1"]
    assert view["scopes"][0]["path"] == f"/scopes/{ARCH}/bf-1"
    assert view["evidence"] == []
```

- [ ] **Step 2: 运行确认失败**

```bash
PYTHONPATH=src uv run pytest tests/dashboard/test_scope_readers.py -q
```

Expected: `ModuleNotFoundError`。

- [ ] **Step 3: 写读取器**

创建 `src/memory_service_runtime/governed/scope_readers.py`：

```python
"""责任网络主干的可寻址节点：Company 与 Scope（Battlefield / Domain）。

Scope 以 (Architecture 对象, 稳定 unit_id) 寻址，不新建对象或表。所有记录都按
当前身份用看板的可见性判断过滤；不可见记录不出现、不计数；缺失项显式标注。
"""
from __future__ import annotations

from typing import Any

from . import db, method_readers
from .dashboard import (_domain_name, _missing, _payload_title, _read_at, _visible_head,
                        _visible_revision, SCHEMA_VERSION, _UNAVAILABLE_CODES)
from .errors import GovernedError
from .method_v05 import SCOPE_STATE_OUTCOME_PREFIX

LIST_LIMIT = 25


def _ref(head, revision):
    return {"object_id": head["object_id"], "revision_id": revision["revision_id"],
            "payload_hash": revision["payload_hash"]}


def _effective(conn, ctx, object_id, *, types=None):
    head, allowed = _visible_head(conn, ctx, object_id)
    if types and head["object_type"] not in types:
        raise GovernedError("NOT_FOUND")
    if not head.get("effective_revision_id"):
        raise GovernedError("NOT_FOUND")
    revision = _visible_revision(conn, ctx, head["object_id"], str(head["effective_revision_id"]), allowed)
    return head, revision


def _units(payload):
    for kind, key in (("battlefield", "battlefields"), ("domain", "domains")):
        for unit in payload.get(key) or []:
            yield {"kind": kind, **unit}


def _current_dri(conn, ctx, auth_domain_id):
    if not auth_domain_id:
        return _missing("no_authorization_domain_mapping")
    rows = conn.execute(
        """SELECT a.assignment_id, a.principal_id, p.display_name
             FROM gov_role_assignments a
             JOIN gov_principals p ON (p.scope_id, p.principal_id) = (a.scope_id, a.principal_id)
            WHERE a.scope_id=%s AND a.domain_id=%s AND a.role='DOMAIN_DRI' AND a.active AND p.active
              AND (a.valid_to IS NULL OR a.valid_to > now())
            ORDER BY a.valid_from""",
        (ctx.scope_id, auth_domain_id)).fetchall()
    rows = db.jsonable(rows)
    if not rows:
        return _missing("no_current_dri")
    if len(rows) != 1:
        return _missing("dri_not_unique")
    return {"status": "available", "reason": None, "value": rows[0]}


def _latest_scope_state(conn, ctx, architecture_object_id, unit_id):
    rows = conn.execute(
        """SELECT state_id, as_of FROM gov_method_state_keys
            WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s ORDER BY as_of DESC LIMIT 10""",
        (ctx.scope_id, architecture_object_id, SCOPE_STATE_OUTCOME_PREFIX + unit_id)).fetchall()
    pending = False
    for row in db.jsonable(rows):
        try:
            head, allowed = _visible_head(conn, ctx, str(row["state_id"]))
        except GovernedError as exc:
            if exc.code in _UNAVAILABLE_CODES:
                continue
            raise
        if not head.get("effective_revision_id"):
            pending = True
            continue
        revision = _visible_revision(conn, ctx, head["object_id"], str(head["effective_revision_id"]), allowed)
        payload = revision["payload"]
        return {"status": "available", "reason": None,
                "value": {"ref": _ref(head, revision), "rag": payload.get("rag"), "summary": payload.get("summary"),
                          "as_of": payload.get("as_of"), "data_gaps": payload.get("data_gaps") or [],
                          "pending_recommendation": pending}}
    value = _missing("no_confirmed_scope_state")
    value["pending_recommendation"] = pending
    return value


def _visible_items(conn, ctx, rows):
    items = []
    for row in db.jsonable(rows):
        try:
            head, allowed = _visible_head(conn, ctx, str(row["object_id"]))
            revision = _visible_revision(conn, ctx, head["object_id"], str(row["revision_id"]), allowed)
        except GovernedError as exc:
            if exc.code in _UNAVAILABLE_CODES:
                continue
            raise
        items.append({"object_type": head["object_type"], "ref": _ref(head, revision),
                      "title": _payload_title(revision["payload"]), "lifecycle_status": head["lifecycle_status"],
                      "domain_id": head["domain_id"], "recorded_at": revision["recorded_at"]})
    return items


_EFFECTIVE_BY_SCOPE = """
    SELECT o.object_id, o.effective_revision_id AS revision_id
      FROM gov_objects o
      JOIN gov_object_revisions r ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
     WHERE o.scope_id=%s AND o.object_type = ANY(%s)
       AND r.payload->>'primary_scope_id'=%s AND r.payload->'architecture_ref'->>'object_id'=%s
     ORDER BY r.recorded_at DESC LIMIT %s"""

_EVIDENCE_BY_SCOPE = """
    SELECT o.object_id, o.effective_revision_id AS revision_id
      FROM gov_objects o
      JOIN gov_object_revisions r ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
     WHERE o.scope_id=%s AND o.object_type='EvidenceAsset'
       AND r.payload->>'anchor_kind'='scope' AND r.payload->>'anchor_scope_id'=%s
       AND r.payload->'anchor_ref'->>'object_id'=%s
     ORDER BY r.recorded_at DESC LIMIT %s"""

_EVIDENCE_COMPANY = """
    SELECT o.object_id, o.effective_revision_id AS revision_id
      FROM gov_objects o
      JOIN gov_object_revisions r ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
     WHERE o.scope_id=%s AND o.domain_id=%s AND o.object_type='EvidenceAsset' AND r.payload->>'anchor_kind'='company'
     ORDER BY r.recorded_at DESC LIMIT %s"""


def scope(conn: Any, ctx: Any, architecture_object_id: str, unit_id: str) -> dict[str, Any]:
    head, revision = _effective(conn, ctx, architecture_object_id, types={"StrategicArchitecture"})
    definition = next((u for u in _units(revision["payload"]) if u.get("unit_id") == unit_id), None)
    if definition is None:
        raise GovernedError("NOT_FOUND")
    issues = _visible_items(conn, ctx, conn.execute(
        _EFFECTIVE_BY_SCOPE, (ctx.scope_id, ["StrategicIssue"], unit_id, head["object_id"], LIST_LIMIT)).fetchall())
    commitments = _visible_items(conn, ctx, conn.execute(
        _EFFECTIVE_BY_SCOPE, (ctx.scope_id, ["LTCO", "PCO", "Mission"], unit_id, head["object_id"], LIST_LIMIT)).fetchall())
    evidence = _visible_items(conn, ctx, conn.execute(
        _EVIDENCE_BY_SCOPE, (ctx.scope_id, unit_id, head["object_id"], LIST_LIMIT)).fetchall())
    return db.jsonable({
        "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
        "architecture": {"ref": _ref(head, revision), "title": _payload_title(revision["payload"]),
                         "domain_id": head["domain_id"], "domain_name": _domain_name(conn, ctx, head["domain_id"])},
        "definition": definition,
        "current_dri": _current_dri(conn, ctx, definition.get("auth_domain_id")),
        "latest_state": _latest_scope_state(conn, ctx, head["object_id"], unit_id),
        "issues": issues, "commitments": commitments, "evidence": evidence,
        "limits": {"list_limit": LIST_LIMIT},
        "note": "列表只含当前身份可读的正式记录，最多各 25 条；不返回不可见记录的数量。",
    })


def company(conn: Any, ctx: Any, domain_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT object_id, revision_id FROM gov_method_strategy_heads WHERE scope_id=%s AND domain_id=%s",
                       (ctx.scope_id, domain_id)).fetchone()
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
                              "domain_id": domain_id, "domain_name": _domain_name(conn, ctx, domain_id),
                              "strategy": _missing("no_formal_strategy"), "architecture": _missing("no_formal_architecture"),
                              "scopes": [], "evidence": []}
    if row is not None:
        try:
            strategy_head, strategy_revision = _effective(conn, ctx, str(row["object_id"]), types={"Strategy"})
        except GovernedError as exc:
            if exc.code not in _UNAVAILABLE_CODES:
                raise
            strategy_head = None
        if strategy_head is not None:
            result["strategy"] = {"ref": _ref(strategy_head, strategy_revision), "title": _payload_title(strategy_revision["payload"])}
            architecture_ref = method_readers.object_state(conn, ctx, strategy_head["object_id"])["method_state"].get("architecture_ref")
            if architecture_ref:
                arch_head, arch_revision = _effective(conn, ctx, str(architecture_ref["object_id"]), types={"StrategicArchitecture"})
                result["architecture"] = {"ref": _ref(arch_head, arch_revision), "title": _payload_title(arch_revision["payload"])}
                for unit in _units(arch_revision["payload"]):
                    result["scopes"].append({
                        "unit_id": unit["unit_id"], "kind": unit["kind"], "name": unit.get("name"),
                        "path": f"/scopes/{arch_head['object_id']}/{unit['unit_id']}",
                        "current_dri": _current_dri(conn, ctx, unit.get("auth_domain_id")),
                        "latest_state": _latest_scope_state(conn, ctx, arch_head["object_id"], unit["unit_id"]),
                    })
    result["evidence"] = _visible_items(conn, ctx, conn.execute(_EVIDENCE_COMPANY, (ctx.scope_id, domain_id, LIST_LIMIT)).fetchall())
    result["note"] = "公司视图是组合投影，不是正式对象；scopes 来自当前正式 Architecture 的定义项。"
    return db.jsonable(result)


__all__ = ["scope", "company", "LIST_LIMIT"]
```

- [ ] **Step 4: 路由与 facade**

`dashboard_routes.py` 顶部导入 `scope_readers`，在 `read_ontology_relations` 后加：

```python
def read_company(token: str, *, domain_id: str) -> dict:
    with db.transaction(token) as (conn, ctx):
        return scope_readers.company(conn, ctx, domain_id)


def read_scope(token: str, architecture_object_id: str, unit_id: str) -> dict:
    with db.transaction(token) as (conn, ctx):
        return scope_readers.scope(conn, ctx, architecture_object_id, unit_id)
```

路由（放在 `ontology_relations_route` 之后）：

```python
@router.get("/company")
def company_route(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                  domain_id: Annotated[uuid.UUID, Query()]):
    strict_query(request.query_params, {"domain_id"})
    result = read_company(token, domain_id=str(domain_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/scopes/{architecture_object_id}/{unit_id}")
def scope_route(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                architecture_object_id: uuid.UUID,
                unit_id: Annotated[str, Path(min_length=1, max_length=200)]):
    strict_query(request.query_params, set())
    result = read_scope(token, str(architecture_object_id), unit_id)
    response.headers["Cache-Control"] = "no-store"
    return result
```

（`from fastapi import ... Path` 需加到该文件的导入。）

`src/memory_service_app/dashboard.py` 加两条 facade（模式同 `ontology_relations`）：`@api.get("/company")` 允许查询参数 `{"domain_id"}`，调用 `_read(reads.read_company, token, domain_id=str(domain_id))`；`@api.get("/scopes/{architecture_object_id}/{unit_id}")` 允许空查询集合，调用 `_read(reads.read_scope, token, str(architecture_object_id), unit_id)`。

`tests/dashboard/test_dashboard_facade.py` 的 `calls` 名单加 `"read_company"`、`"read_scope"`，并追加：

```python
def test_company_and_scope_reads_forward_their_addresses(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    assert fetch(app, "/dashboard/api/v1/company").status_code == 422
    domain = "00000000-0000-0000-0000-000000000001"
    assert fetch(app, f"/dashboard/api/v1/company?domain_id={domain}").status_code == 200
    assert calls["read_company"]["kwargs"] == {"domain_id": domain}
    arch = "00000000-0000-0000-0000-000000000010"
    assert fetch(app, f"/dashboard/api/v1/scopes/{arch}/bf-1").status_code == 200
    assert calls["read_scope"]["args"] == (arch, "bf-1")
```

- [ ] **Step 5: 运行测试**

```bash
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
```

Expected: 全部 passed。

- [ ] **Step 6: 提交**

```bash
git add src/memory_service_runtime/governed/scope_readers.py src/memory_service_runtime/governed/dashboard_routes.py src/memory_service_app/dashboard.py tests/dashboard/test_scope_readers.py tests/dashboard/test_dashboard_facade.py
git commit -m "feat(runtime): add addressable Company and Scope readers for the responsibility trunk

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 11: 议题生命周期投影（Clark 不存第二套状态机）

**Files:**
- Create: `src/memory_service_runtime/governed/issue_lifecycle.py`
- Modify: `src/memory_service_runtime/governed/dashboard.py`（`detail()` 中 `business = _normalised_business(...)` 之后）
- Modify: `src/memory_service_runtime/governed/scope_readers.py`（`scope()` 的 issues 项）
- Test: `tests/test_issue_lifecycle.py`

**Interfaces:**
- Consumes: `method_readers.object_state`、`workspace_readers.read(conn, ctx, scene_id)["meeting"]["phase"]`。
- Produces: `issue_lifecycle.STAGES`、`issue_lifecycle.stage(issue_state, *, agreement_phase=None, meeting_phase=None, proposal_phase=None) -> dict`、`issue_lifecycle.project(conn, ctx, issue_head) -> dict`；`detail()["business"]["lifecycle"]`、`scope()["issues"][i]["lifecycle"]`。

- [ ] **Step 1: 写失败的纯函数测试**

创建 `tests/test_issue_lifecycle.py`：

```python
"""概念版 8 态只从 Runtime 事实投影；证明不了的阶段明确标 not_derivable。"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import issue_lifecycle as life


@pytest.mark.parametrize("state,kwargs,expected", [
    ({"phase": "issue_confirmed"}, {}, "issue_formed"),
    ({"phase": "issue_confirmed", "research_principal_ids": ["p"]}, {}, "under_study"),
    ({"phase": "issue_confirmed"}, {"meeting_phase": "scheduled"}, "meeting_scheduled"),
    ({"phase": "issue_confirmed"}, {"meeting_phase": "published"}, "consensus_pending"),
    ({"phase": "issue_confirmed"}, {"agreement_phase": "awaiting_confirmation"}, "consensus_pending"),
    ({"phase": "agreement_formal"}, {"agreement_phase": "formal"}, "consensus_formal"),
    ({"phase": "agreement_formal"}, {"agreement_phase": "formal", "proposal_phase": "confirmed"}, "update_confirmed"),
])
def test_stage_is_the_latest_provable_step(state, kwargs, expected):
    result = life.stage(state, **kwargs)
    assert result["stage"] == expected
    assert result["not_derivable"] == ["in_execution", "result_verified"]
    assert result["stages"] == list(life.STAGES)


def test_project_collects_inputs_from_runtime_facts(monkeypatch):
    calls = []
    monkeypatch.setattr(life.method_readers, "object_state", lambda c, x, oid: calls.append(oid) or {
        "method_state": {"phase": "issue_confirmed", "agreement_ref": {"object_id": "agreement"}, "round": 2}
        if oid == "issue" else {"phase": "draft"}})
    monkeypatch.setattr(life, "_latest_meeting_phase", lambda c, x, oid: "review")
    monkeypatch.setattr(life, "_latest_proposal_phase", lambda c, x, oid: None)
    result = life.project(None, None, {"object_id": "issue"})
    assert result["stage"] == "consensus_pending" and result["round"] == 2
    assert result["basis"] == {"issue_phase": "issue_confirmed", "agreement_phase": "draft",
                               "meeting_phase": "review", "proposal_phase": None}
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_issue_lifecycle.py -q
```

Expected: `ModuleNotFoundError`。

- [ ] **Step 3: 写投影模块**

创建 `src/memory_service_runtime/governed/issue_lifecycle.py`：

```python
"""议题生命周期投影：概念版的 8 个状态只从 Runtime 事实推出，不另存状态机。

发现偏离 / 待定位原因 发生在议题形成之前（OperatingProblem），不在本投影内；
进入执行 / 结果已验证 目前没有可证明的运行时事实，明确标为 not_derivable。
"""
from __future__ import annotations

from typing import Any

from . import method_readers, workspace_readers
from .errors import GovernedError

STAGES = ("issue_formed", "under_study", "meeting_scheduled", "consensus_pending",
          "consensus_formal", "update_confirmed")
NOT_DERIVABLE = ("in_execution", "result_verified")
_MEETING_LIVE = {"scheduled", "in_progress", "ended", "review"}


def stage(issue_state: dict[str, Any], *, agreement_phase: str | None = None,
          meeting_phase: str | None = None, proposal_phase: str | None = None) -> dict[str, Any]:
    phase = issue_state.get("phase")
    if proposal_phase == "confirmed":
        value = "update_confirmed"
    elif agreement_phase == "formal" or phase in {"agreement_formal", "completed"}:
        value = "consensus_formal"
    elif agreement_phase in {"draft", "awaiting_confirmation"} or meeting_phase == "published":
        value = "consensus_pending"
    elif meeting_phase in _MEETING_LIVE:
        value = "meeting_scheduled"
    elif issue_state.get("research_principal_ids"):
        value = "under_study"
    else:
        value = "issue_formed"
    return {"stage": value, "stages": list(STAGES), "not_derivable": list(NOT_DERIVABLE),
            "round": issue_state.get("round"),
            "basis": {"issue_phase": phase, "agreement_phase": agreement_phase,
                      "meeting_phase": meeting_phase, "proposal_phase": proposal_phase}}


def _latest_meeting_phase(conn, ctx, issue_object_id) -> str | None:
    row = conn.execute(
        """SELECT scene_id FROM gov_workspace_events
            WHERE scope_id=%s AND anchor_object_id=%s AND kind='create' AND payload->>'scene_type'='meeting'
            ORDER BY recorded_at DESC LIMIT 1""",
        (ctx.scope_id, issue_object_id)).fetchone()
    if row is None:
        return None
    try:
        return workspace_readers.read(conn, ctx, str(row["scene_id"]))["meeting"]["phase"]
    except GovernedError as exc:
        if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
            return None
        raise


def _latest_proposal_phase(conn, ctx, issue_object_id) -> str | None:
    row = conn.execute(
        """SELECT o.object_id FROM gov_objects o
            JOIN gov_object_revisions r ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.latest_revision_id)
           WHERE o.scope_id=%s AND o.object_type='StrategyUpdateProposal' AND r.payload->'issue_ref'->>'object_id'=%s
           ORDER BY r.recorded_at DESC LIMIT 1""",
        (ctx.scope_id, issue_object_id)).fetchone()
    if row is None:
        return None
    try:
        return method_readers.object_state(conn, ctx, str(row["object_id"]))["method_state"].get("phase")
    except GovernedError as exc:
        if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
            return None
        raise


def project(conn, ctx, issue_head: dict[str, Any]) -> dict[str, Any]:
    state = method_readers.object_state(conn, ctx, issue_head["object_id"])["method_state"]
    agreement_phase = None
    reference = state.get("agreement_ref")
    if reference:
        try:
            agreement_phase = method_readers.object_state(conn, ctx, reference["object_id"])["method_state"].get("phase")
        except GovernedError as exc:
            if exc.code not in {"NOT_FOUND", "FORBIDDEN"}:
                raise
    return stage(state, agreement_phase=agreement_phase,
                 meeting_phase=_latest_meeting_phase(conn, ctx, issue_head["object_id"]),
                 proposal_phase=_latest_proposal_phase(conn, ctx, issue_head["object_id"]))


__all__ = ["STAGES", "NOT_DERIVABLE", "stage", "project"]
```

- [ ] **Step 4: 接入详情与 Scope 读取器**

`dashboard.py` 的 `detail()` 中，`business = _normalised_business(head["object_type"], payload)` 之后加：

```python
    if head["object_type"] == "StrategicIssue":
        from . import issue_lifecycle
        business["lifecycle"] = issue_lifecycle.project(conn, ctx, head)
```

`scope_readers.scope()` 的 `issues = _visible_items(...)` 之后加：

```python
    from . import issue_lifecycle
    for item in issues:
        item["lifecycle"] = issue_lifecycle.project(conn, ctx, {"object_id": item["ref"]["object_id"]})
```

并在 `tests/dashboard/test_scope_readers.py` 的第一个用例里加 `monkeypatch.setattr(scope_readers.issue_lifecycle, "project", lambda c, x, h: {"stage": "issue_formed"})`（把 `from . import issue_lifecycle` 提到 `scope_readers.py` 模块顶部，使其可被 monkeypatch）。

- [ ] **Step 5: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_issue_lifecycle.py -q
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
```

Expected: 全部 passed。

- [ ] **Step 6: 提交**

```bash
git add src/memory_service_runtime/governed/issue_lifecycle.py src/memory_service_runtime/governed/dashboard.py src/memory_service_runtime/governed/scope_readers.py tests/test_issue_lifecycle.py tests/dashboard/test_scope_readers.py
git commit -m "feat(runtime): project the issue lifecycle from Runtime facts only

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 12: 会议行动状态（workspace 0.1 `action_status`，假设 A4）

**Files:**
- Modify: `src/memory_service_runtime/governed/workspace_models.py:184-192`（新增 `ActionStatus` 并入 `Event` 联合）
- Modify: `src/memory_service_runtime/governed/workspace_service.py:19-25`（`SCENE_KINDS`）、`:113-135`（`authorize_write`）、`:268-292`（校验分支）
- Modify: `src/memory_service_runtime/governed/workspace_readers.py:69-86`（operations 原因）、`:126-135`（meeting 投影）
- Create: `src/memory_service_app/migrations/0030_workspace_action_status.sql`
- Modify: `tests/test_migrations.py`（清单加 0030）
- Test: `tests/test_workspace_scenes.py`

**Interfaces:**
- Consumes: `workspace_service.latest(history, kind, principal=None)`、`active(history)`、`is_owner(conn, ctx, seed)`、`exact(conn, ctx, ref)`、`method_access.assignment`。
- Produces: 事件 `{"kind": "action_status", "publish_event_id", "item_index", "status": accepted|done|verified|dropped, "note", "evidence_refs"}`；`workspace_readers.action_status_view(live, publication) -> list`；`read()["meeting"]["actions"]`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_workspace_scenes.py` 末尾追加：

```python
def test_action_status_event_shape():
    body = command()
    body["event"] = {"kind": "action_status", "publish_event_id": str(uuid4()), "item_index": 0,
                     "status": "done", "note": "Delivered", "evidence_refs": [reference()]}
    assert WorkspaceCommand.model_validate(body).event.kind == "action_status"
    for bad in ({"status": "finished"}, {"item_index": -1}, {"item_index": "0"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            WorkspaceCommand.model_validate(body | {"event": body["event"] | bad})


def test_action_status_view_reports_latest_status_per_item():
    from memory_service_runtime.governed.workspace_readers import action_status_view
    publication = {"event_id": "pub", "payload": {"actions": [
        {"text": "Ship", "quote": {"line_index": 0, "text": "ship"}, "owner_assignment_id": "a1", "due": None},
        {"text": "Review", "quote": {"line_index": 1, "text": "review"}, "owner_assignment_id": None, "due": None}]}}
    live = [
        {"event_id": "e1", "kind": "action_status", "payload": {"publish_event_id": "pub", "item_index": 0, "status": "accepted", "evidence_refs": []}},
        {"event_id": "e2", "kind": "action_status", "payload": {"publish_event_id": "pub", "item_index": 0, "status": "done", "evidence_refs": [reference()]}},
        {"event_id": "e3", "kind": "action_status", "payload": {"publish_event_id": "old", "item_index": 1, "status": "done", "evidence_refs": []}},
    ]
    view = action_status_view(live, publication)
    assert [(item["item_index"], item["status"], item["status_event_id"]) for item in view] == [(0, "done", "e2"), (1, "routed", None)]
    assert action_status_view(live, None) == []
```

并把既有 `test_openapi_exposes_finite_event_union_without_changing_method_contract` 里的断言后追加一行 `assert "action_status" in event["discriminator"]["mapping"]`。`tests/test_migrations.py` 清单在 0029 之后加 `"0030_workspace_action_status.sql",`。

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_workspace_scenes.py -q
```

Expected: 新增两个用例与 openapi 用例 FAIL。

- [ ] **Step 3: 模型**

`workspace_models.py` 在 `class Withdraw` 之前加：

```python
class ActionStatus(StrictModel):
    """会后核对稿中一条行动的最新状态；只由被路由的负责人或会议主责本人记录。"""
    kind: Literal["action_status"]
    publish_event_id: CanonicalUUID
    item_index: Annotated[int, Field(strict=True, ge=0)]
    status: Literal["accepted", "done", "verified", "dropped"]
    note: NEStr | None = None
    evidence_refs: Refs = []
```

并把 `Event` 联合里 `| MeetingPublish | Withdraw` 改为 `| MeetingPublish | ActionStatus | Withdraw`。

- [ ] **Step 4: 服务端校验**

`workspace_service.py`：

1. `SCENE_KINDS["meeting"]` 加 `"action_status"`。
2. `authorize_write` 在 `elif kind in {"weekly_answer", "weekly_confirm"}:` 之前加：

```python
    elif kind == "action_status":
        if ctx.principal_type != "human":
            fail("FORBIDDEN", "Action status is recorded by the responsible person themselves.")
```

3. 校验分支在 `elif kind == "withdraw":` 之前加：

```python
    elif kind == "action_status":
        publication = latest(active(history), "meeting_publish")
        if not publication or publication["event_id"] != event["publish_event_id"]:
            fail("STALE_DEPENDENCY", "Record action status against the current publication.")
        actions = publication["payload"]["actions"]
        if event["item_index"] >= len(actions):
            fail("INVALID_REQUEST", "Unknown action item.")
        responsible = actions[event["item_index"]]["owner_assignment_id"] or seed["owner_assignment_id"]
        if event["status"] == "verified":
            if not is_owner(conn, ctx, seed):
                fail("FORBIDDEN", "Only the meeting owner verifies an action result.")
        else:
            try:
                access.assignment(conn, ctx, responsible, ctx.principal_id, "human")
            except GovernedError:
                fail("FORBIDDEN", "Only the routed action owner records its status.")
        for reference in event["evidence_refs"]:
            exact(conn, ctx, reference)
```

- [ ] **Step 5: 读取投影**

`workspace_readers.py` 新增纯函数（放在 `read` 之前）：

```python
def action_status_view(live, publication):
    """每条已发布行动的最新状态；未记录的为 routed。只看当前发布版本的记录。"""
    if not publication:
        return []
    latest_by_index = {}
    for event in live:
        if event["kind"] == "action_status" and event["payload"]["publish_event_id"] == publication["event_id"]:
            latest_by_index[event["payload"]["item_index"]] = event
    items = []
    for index, action in enumerate(publication["payload"]["actions"]):
        record = latest_by_index.get(index)
        items.append({"item_index": index, "text": action["text"],
                      "owner_assignment_id": action.get("owner_assignment_id"), "due": action.get("due"),
                      "status": record["payload"]["status"] if record else "routed",
                      "status_event_id": record["event_id"] if record else None,
                      "evidence_refs": record["payload"].get("evidence_refs", []) if record else []})
    return items
```

在 `read()` 的 operations 循环里、`if kind == "meeting_publish" ...` 之后加：

```python
        if kind == "action_status" and result["materials"]["meeting_publish"]["status"] != "available":
            reason = "publication_missing"
```

在 meeting 分支 `result["meeting"] = {...}` 之后加：

```python
        result["meeting"]["actions"] = action_status_view(live, publication if published else None)
```

- [ ] **Step 6: 迁移 0030**

创建 `src/memory_service_app/migrations/0030_workspace_action_status.sql`：

```sql
-- 0030: workspace 0.1 meeting scenes may record per-action status after publication.
-- The inline CHECK from 0022 is auto-named gov_workspace_events_kind_check; replace it
-- with the same list plus 'action_status'.  Rows stay append-only.
DO $$
DECLARE old_definition text;
BEGIN
 SELECT pg_get_constraintdef(oid) INTO old_definition FROM pg_constraint
  WHERE conrelid='gov_workspace_events'::regclass AND conname='gov_workspace_events_kind_check';
 IF old_definition IS NULL THEN RAISE EXCEPTION 'expected gov_workspace_events kind constraint'; END IF;
 ALTER TABLE gov_workspace_events DROP CONSTRAINT gov_workspace_events_kind_check;
END $$;
ALTER TABLE gov_workspace_events ADD CONSTRAINT gov_workspace_events_kind_check CHECK(kind IN (
    'create','comment_anchor','diff_response','monthly_material','weekly_material','refresh_sources',
    'weekly_answer','weekly_confirm','read','request_supplement','bring_to_meeting',
    'meeting_start','meeting_finish','meeting_material','meeting_publish','withdraw','action_status'));
```

- [ ] **Step 7: 运行测试与迁移**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_workspace_scenes.py -q
python3 acceptance/runtime/infra.py run --migration -- .venv/bin/python -m pytest tests/test_migrations.py -q
```

Expected: 全部 passed；迁移到 0030。

- [ ] **Step 8: 提交**

```bash
git add src/memory_service_runtime/governed/workspace_models.py src/memory_service_runtime/governed/workspace_service.py src/memory_service_runtime/governed/workspace_readers.py src/memory_service_app/migrations/0030_workspace_action_status.sql tests/test_migrations.py tests/test_workspace_scenes.py
git commit -m "feat(workspace): record meeting action status against the current publication (0030)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 13: 治理工作台前端：0.5 规则版本与注册表关系

**Files:**
- Modify: `workbench/dashboard/src/lib/ontology.ts:13-27`（版本枚举）、`:508-516`（`typeInfo`）、`:579-600`（`V05_OVERRIDES`）、`:788-796`（`versionTypes`）、`:818-824`（`mapEdges`）
- Modify: `workbench/dashboard/src/lib/labels.ts:56-60`
- Modify: `workbench/dashboard/src/lib/api.ts:100-104`（新增 `fetchOntologyRelations`）
- Modify: `workbench/dashboard/src/lib/types.ts:326-332`（新增 `OntologyRelations`）
- Modify: `workbench/dashboard/src/components/OntologyMap.tsx:36-56`（接收 `relations`）
- Modify: `workbench/dashboard/src/components/TypeInfoCard.tsx:33-40, 94-100`（新增注册表关系分节）
- Modify: `workbench/dashboard/src/App.tsx:169-172, 494-511, 541-549`
- Test: `workbench/dashboard/src/__tests__/ontology-semantics.test.ts`、`OntologyMap.test.tsx`、新建 `RegistryRelations.test.tsx`

**Interfaces:**
- Consumes: Task 9 的 `GET /ontology/relations?contract_version=`（经 `API_BASE`）。
- Produces: `RulesVersion` 含 `"0.5"`；`mapEdges(rules, registered, relations?)`；`fetchOntologyRelations(contractVersion, signal)`；`OntologyRelations` 类型；`TypeInfoCard` 的 `relations?: OntologyRelations | null` 属性与 `data-testid="type-registry-relations"` 分节。

- [ ] **Step 1: 写失败的测试**

`ontology-semantics.test.ts` 末尾追加：

```ts
describe("0.5 rules", () => {
  const relations = { schema_version: "tkos.dashboard/0.1", read_at: "2026-09-20T00:00:00Z", contract_version: "tkos.method/0.5", note: "",
    relations: [
      { predicate: "contributesTo", from_type: "Mission", to_type: "PCO", cardinality: "N:1", field: "parent_pco_ref",
        versions: ["tkos.method/0.4", "tkos.method/0.5"], label: "贡献的周期结果", status: "exact_ref", load_bearing: true, m0: "contributesTo" },
      { predicate: "hasDRI", from_type: "Scope", to_type: "Person", cardinality: "N:1", field: null,
        versions: ["tkos.method/0.5"], label: "当前 DRI", status: "derived", load_bearing: false, m0: "hasDRI" },
    ] }
  const registered = new Set(["Mission", "PCO", "OperatingState", "StrategicIssue", "EvidenceAsset"])
  it("is a first-class rules version", () => {
    expect(RULES_VERSIONS[0]).toBe("0.5")
    expect(rulesOfContractVersion("tkos.method/0.5")).toBe("0.5")
    expect(contractVersionOf("0.5")).toBe("tkos.method/0.5")
  })
  it("reads Scope-level State and anchored evidence in the 0.5 type text", () => {
    expect(text(typeInfo("OperatingState", "0.5")!)).toContain("Scope")
    expect(text(typeInfo("EvidenceAsset", "0.5")!)).toContain("锚点")
    expect(text(typeInfo("OperatingState", "0.4")!)).not.toContain("subject_scope_id")
  })
  it("draws 0.5 map edges from the server registry and never from virtual nodes", () => {
    const edges = mapEdges("0.5", registered, relations.relations)
    expect(edges).toEqual([{ from: "Mission", to: "PCO", label: "contributesTo · 贡献的周期结果" }])
    expect(mapEdges("0.5", registered, null)).toEqual(mapEdges("0.4", registered))
  })
})
```

新建 `workbench/dashboard/src/__tests__/RegistryRelations.test.tsx`：

```tsx
import { afterEach, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { TypeInfoCard } from "@/components/TypeInfoCard"
import type { OntologyCatalog, OntologyRelations } from "@/lib/types"
afterEach(cleanup)
const catalog: OntologyCatalog = { schema_version: "tkos.dashboard/0.1", read_at: "2026-09-20T00:00:00Z",
  versions: [{ contract_version: "tkos.method/0.5", object_types: ["Mission", "PCO"] }], types: {}, note: "" }
const relations: OntologyRelations = { schema_version: "tkos.dashboard/0.1", read_at: "2026-09-20T00:00:00Z", contract_version: "tkos.method/0.5", note: "",
  relations: [{ predicate: "contributesTo", from_type: "Mission", to_type: "PCO", cardinality: "N:1", field: "parent_pco_ref",
    versions: ["tkos.method/0.5"], label: "贡献的周期结果", status: "exact_ref", load_bearing: true, m0: "contributesTo" }] }
it("lists registry relations touching the selected type", () => {
  render(<TypeInfoCard type="PCO" rules="0.5" catalog={catalog} relations={relations}
                       onSelectType={vi.fn()} onViewData={vi.fn()} onClose={vi.fn()} />)
  const section = screen.getByTestId("type-registry-relations")
  expect(section).toHaveTextContent("contributesTo")
  expect(section).toHaveTextContent("Mission → PCO")
  expect(section).toHaveTextContent("N:1")
})
it("says so when the registry is unavailable instead of showing nothing", () => {
  render(<TypeInfoCard type="PCO" rules="0.5" catalog={catalog} relations={null}
                       onSelectType={vi.fn()} onViewData={vi.fn()} onClose={vi.fn()} />)
  expect(screen.getByTestId("type-registry-relations")).toHaveTextContent("注册表未加载")
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd workbench/dashboard && npx vitest run src/__tests__/ontology-semantics.test.ts src/__tests__/RegistryRelations.test.tsx
```

Expected: 类型错误或断言失败。

- [ ] **Step 3: 版本枚举、类型说明与边**

`ontology.ts`：

```ts
export type RulesVersion = "0.1" | "0.2" | "0.3" | "0.4" | "0.5"
export const RULES_VERSIONS: RulesVersion[] = ["0.5", "0.4", "0.3", "0.2", "0.1"]
```

`rulesOfContractVersion` 加 `if (contractVersion === "tkos.method/0.5") return "0.5"`。

`versionTypes` 中 `if (rules === "0.4") return new Set(V04_TYPES)` → `if (rules === "0.4" || rules === "0.5") return new Set(V04_TYPES)`。

在 `V04_OVERRIDES` 之后加：

```ts
/** 0.5 只在 0.4 读法上追加三处差异；未列出的类型沿用 0.4 读法。 */
const V05_OVERRIDES: Record<string, Partial<TypeInfo>> = {
  StrategicIssue: {
    keyFacts: [
      "0.5 可记录唯一主 Scope（primary_scope_id）：存在正式责任结构时必须能在其中找到；公司级议题可空。",
      "主 Scope 只用于按战场列议题，不授予任何权限。",
    ],
  },
  OperatingState: {
    definition: "0.5 的经营状态主体可以是 LTCO、PCO、Mission，也可以是责任结构中的某个 Scope（subject_ref 指向确切 Architecture 版本，subject_scope_id 指向稳定定义项）。Scope 级状态由该 Scope 映射授权域的唯一当前 DRI 确认；不对下层 RAG 求平均，也不自动生成。",
    keyFacts: [
      "同一 Scope、同一观察时点只有一个状态身份，跨 Architecture 修订保持同一身份。",
      "无证据只能 Unknown 并列明缺口；Agent 只推荐，责任人本人确认后才是正式状态。",
    ],
  },
  EvidenceAsset: {
    keyFacts: [
      "0.5 上传时必须声明锚点：公司级、某个 Scope，或某个确切对象版本；缺锚点不写入。",
      "锚点在上传时判断，之后不可更改；私有来源带入议题时经挂锚会议场景落库。",
    ],
  },
}
```

`typeInfo` 改为：

```ts
export function typeInfo(type: string, rules: RulesVersion): TypeInfo | null {
  const base = BASE_TYPE_INFO[type]
  if (!base) return null
  if ((rules === "0.4" || rules === "0.5") && !V04_TYPES.includes(type)) return null
  const override = rules === "0.5" ? { ...V04_OVERRIDES[type], ...V05_OVERRIDES[type] }
    : rules === "0.4" ? V04_OVERRIDES[type] : VERSION_OVERRIDES[type]?.[rules]
  return override && Object.keys(override).length ? { ...base, ...override } : base
}
```

`mapEdges` 改为：

```ts
const DRAWABLE = new Set(["exact_ref", "exact_ref_nested", "scope_id"])

export function mapEdges(rules: RulesVersion, registered: Set<string> | null,
                         relations?: OntologyRelationRow[] | null): MapEdge[] {
  const present = versionTypes(rules, registered)
  if (rules === "0.5" && relations) {
    const seen = new Set<string>()
    return relations.filter((row) => DRAWABLE.has(row.status) && present.has(row.from_type) && present.has(row.to_type))
      .map((row) => ({ from: row.from_type, to: row.to_type, label: `${row.predicate} · ${row.label}` }))
      .filter((edge) => { const key = `${edge.from}->${edge.to}:${edge.label}`; if (seen.has(key)) return false; seen.add(key); return true })
  }
  const staticRules: RulesVersion = rules === "0.5" ? "0.4" : rules
  return MAP_EDGES.filter((edge) =>
    (!edge.versions || edge.versions.includes(staticRules))
    && present.has(edge.from) && present.has(edge.to))
    .map(({ from, to, label }) => ({ from, to, label }))
}
```

（`OntologyRelationRow` 从 `@/lib/types` 导入，见 Step 4。）

- [ ] **Step 4: 类型、API、标签**

`types.ts` 在 `OntologyCatalog` 之后加：

```ts
export interface OntologyRelationRow {
  predicate: string
  from_type: string
  to_type: string
  cardinality: string
  field: string | null
  versions: string[]
  label: string
  status: "exact_ref" | "exact_ref_nested" | "scope_id" | "principal_id" | "value" | "derived" | "text_only" | "not_implemented"
  load_bearing: boolean
  m0: string | null
}

export interface OntologyRelations {
  schema_version: string
  read_at: string
  contract_version: string
  relations: OntologyRelationRow[]
  note: string
}
```

`api.ts` 在 `fetchOntologyCatalog` 后加：

```ts
export const fetchOntologyRelations = (contractVersion: string, signal?: AbortSignal) =>
  getJson<import("@/lib/types").OntologyRelations>(
    `/ontology/relations?contract_version=${encodeURIComponent(contractVersion)}`, signal)
```

`labels.ts` 的 `RULES_VERSION_LABELS` 加 `"0.4": "业务规则 0.4",`、`"0.5": "业务规则 0.5",`。

- [ ] **Step 5: 组件与 App 接线**

`OntologyMap.tsx`：props 增加 `relations?: OntologyRelations | null`，`computeLayout(rules, registered, openAreas)` 增加第四个参数 `relations`，其中 `mapEdges(rules, registered)` 改为 `mapEdges(rules, registered, relations?.relations)`；组件内所有调用处传入 `props.relations`。

`TypeInfoCard.tsx`：props 增加 `relations?: OntologyRelations | null`；在「对象关系」分节之后加：

```tsx
            {(rules === "0.4" || rules === "0.5") ? (
              <Section title="M0 关系（服务端注册表）" testId="type-registry-relations">
                {relations == null ? (
                  <p className="text-[12px] text-muted-foreground">注册表未加载或读取失败；这不是“没有关系”。</p>
                ) : (
                  <ul className="space-y-1 text-[12px]">
                    {relations.relations.filter((row) => row.from_type === type || row.to_type === type).map((row) => (
                      <li key={`${row.predicate}:${row.from_type}:${row.to_type}:${row.field ?? ""}`}>
                        <span className="font-mono">{row.predicate}</span> · {row.from_type} → {row.to_type} · {row.cardinality} · {row.label}
                        {row.status !== "exact_ref" ? <Badge variant="outline" className="ml-1">{row.status}</Badge> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </Section>
            ) : null}
```

`App.tsx`：在 `catalog` 的 `useLiveResource` 之后加：

```ts
  const relations = useLiveResource({
    key: `ontology-relations#${view.rules}#${generation}`,
    fetcher: (_key, signal) => fetchOntologyRelations(contractVersionOf(view.rules as RulesVersion), signal),
  })
```

（其余选项与 `catalog` 那次调用保持一致；`fetchOntologyRelations`、`contractVersionOf` 加入对应 import。）两处 `<OntologyMap ... />` 与 `<TypeInfoCard ... />` 各加 `relations={relations.data}`。

- [ ] **Step 6: 运行前端全部测试、类型检查与构建**

```bash
cd workbench/dashboard && npx vitest run && npx tsc --noEmit
cd /Users/yusiyi/ysy/tkos-ontology-runtime && ./scripts/build_dashboard.sh
```

Expected: vitest 全部通过（含既有 184 项）；`tsc` 无错误；构建产物与清单校验通过（`src/memory_service_app/dashboard_dist/` 随之更新）。

- [ ] **Step 7: 提交**

```bash
git add workbench/dashboard/src src/memory_service_app/dashboard_dist
git commit -m "feat(workbench): show business rules 0.5 and the server relation registry

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 14: 独立验收 `acceptance/method_v05`

**Files:**
- Create: `acceptance/method_v05/__init__.py`（空）、`acceptance/method_v05/fixture.py`、`acceptance/method_v05/flow.py`、`acceptance/method_v05/run.py`
- Create: `acceptance/method_v05/README.md`

**Interfaces:**
- Consumes: `acceptance/method_v04/fixture.py` 的 `seed_v04`；`acceptance/method_v04/flow.py` 的 `Flow`（0.4 驱动器）；`acceptance/method_independent/harness.py` 的 `MethodHarness`；`acceptance/runtime/client.py` 的 `Client.json(method, path, body=None, expected=...)`。
- Produces: `summary.json` 含 `runtime_method_v05_api_accepted`。

- [ ] **Step 1: fixture**

`acceptance/method_v05/fixture.py`：

```python
"""0.5 身份与登记：复用 0.4 的合成身份，只把契约、profile 与注册表换成 0.5。"""
from __future__ import annotations

import json
from pathlib import Path

from acceptance.method_independent.fixture import uid
from acceptance.method_v04.fixture import seed_v04
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]


def seed_v05(env, path: Path, label: str):
    fixture = seed_v04(env, path, label)
    fixture['contract_version'] = 'tkos.method/0.5'
    private_json(path, fixture)
    return fixture


def register_v05(h, source, f):
    """通过真实维护 CLI 安装 0.5 profile / policy / registry。"""
    profile_path = ROOT / 'docs/contracts/method-profile-0.5.json'
    registry_path = ROOT / 'docs/runtime-method-registry-0.5.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic Method 0.5 independent API acceptance']
    adapter.cli('method-v05-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-method-0.5.md'),
    ], expected_exit=0)
    policy = {
        'default_protocol': 'tkos.method', 'default_contract_version': 'tkos.method/0.5',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': profile['profile_id'], 'revision': profile['revision']},
        'experimental': True, 'notes': 'Synthetic Method 0.5 fixture; no production authorization',
    }
    policy_file = h.private / ('method-v05-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('method-v05-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)], expected_exit=0)
    adapter.cli('method-v05-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.method',
        '--contract-version', 'tkos.method/0.5', '--content-json', str(registry_path),
    ], expected_exit=0)
    return profile
```

- [ ] **Step 2: flow**

`acceptance/method_v05/flow.py`：

```python
"""0.5 公开 HTTP 驱动：0.4 的链路加锚点证据、主 Scope 议题与 Scope 级状态。"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from acceptance.method_independent.flow import exact, uid
from acceptance.method_v04.flow import Flow as FlowV04


class Flow(FlowV04):
    def command(self, kind, params, *, oid=None, actor='ceo', key=None, run=None):
        body = super().command(kind, params, oid=oid, actor=actor, key=key, run=run)
        body['contract_version'] = 'tkos.method/0.5'
        return body

    # ------------------------------------------------------------- evidence

    def upload_anchored(self, anchor, *, actor='ceo', domain='company', text='Synthetic anchored evidence',
                        expected=None):
        raw = json.dumps({'record_origin': 'synthetic', 'id': uid(), 'text': text}, sort_keys=True).encode()
        body = {'domain_id': self.f['domains'][domain], 'title': 'Synthetic 0.5 anchored evidence',
                'media_type': 'application/json', 'content_base64': base64.b64encode(raw).decode()}
        if anchor is not None:
            body['anchor'] = anchor
        response = self.clients[actor].json('POST', '/v1/evidence-assets', body, expected=expected or {200, 201})
        if expected:
            return response
        reference = self.ref(response['object_id'], actor)
        self.evidence.append({'response': response, 'ref': reference, 'sha256': hashlib.sha256(raw).hexdigest(),
                              'bytes_hex': raw.hex(), 'actor': actor, 'anchor': anchor})
        return reference

    # --------------------------------------------------------------- issues

    def reframe_with_scope(self, issue, evidence, strategy_ref, architecture_ref, scope_id, *, expected=None):
        payload = {'title': 'Synthetic 0.5 issue (scoped round)', 'summary': 'Scoped reframing.',
                   'core_question': 'Which battlefield step should the company formalize next?',
                   'business_scope': 'strategic', 'urgency': 'green', 'source_refs': [evidence],
                   'strategy_ref': strategy_ref, 'architecture_ref': architecture_ref,
                   'primary_scope_id': scope_id}
        body = self.command('m1a_reframe_issue', {'payload': payload}, oid=issue['object_id'], actor='ceo_agent')
        if expected:
            return self.deny('ceo_agent', body, codes=expected)
        return self.act('ceo_agent', 'm1a_reframe_issue', {'payload': payload}, oid=issue['object_id'])['result']

    # ---------------------------------------------------------- scope state

    def scope_state_payload(self, architecture_ref, scope_id, *, rag='unknown', evidence=(), as_of=None,
                            data_gaps=('Awaiting independent evidence',)):
        return {'subject_ref': architecture_ref, 'subject_scope_id': scope_id,
                'as_of': as_of or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                'summary': 'Scope-level recommendation', 'rag': rag, 'baseline_refs': [architecture_ref],
                'evidence_refs': list(evidence), 'data_gaps': list(data_gaps),
                'generation_version': 'controlled-scope-recommendation-1'}

    def propose_scope_state(self, payload, *, actor='co_agent', previous=None):
        params = {'domain_id': self.f['domains']['company'], 'payload': payload}
        if previous is not None:
            params['previous_state_ref'] = previous
        return exact(self.act(actor, 'method_propose_state', params)['result'])

    # ---------------------------------------------------------------- reads

    def scope_view(self, architecture_object_id, scope_id, *, actor='ceo'):
        return self.clients[actor].json('GET', f'/v1/dashboard/scopes/{architecture_object_id}/{scope_id}')

    def company_view(self, *, actor='ceo'):
        return self.clients[actor].json('GET', f"/v1/dashboard/company?domain_id={self.f['domains']['company']}")

    def relations(self, version, *, actor='ceo'):
        return self.clients[actor].json('GET', f'/v1/dashboard/ontology/relations?contract_version={version}')
```

- [ ] **Step 3: run.py**

`acceptance/method_v05/run.py`：

```python
"""Real HTTP+PG acceptance of the tkos.method/0.5 M0-alignment increment.

Happy path and the negative matrix run through /v1/actions/prepare + /v1/actions
and the read endpoints; SQL is used only for independent assertions.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from acceptance.method_independent.harness import MethodHarness
from acceptance.method_v04.run import _change
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import register_v05, seed_v05
from .flow import Flow


def happy_path(h, f, flow):
    checks = []

    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)

    def payload_of(reference):
        return h.sql(f, "SELECT payload FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                     (f['scope_id'], reference['revision_id']))[0]['payload']

    company_evidence = flow.upload_anchored({'kind': 'company'})
    check('company_anchored_evidence_before_any_architecture',
          payload_of(company_evidence)['anchor_kind'] == 'company')

    run_ref = flow.open_run()
    issue = flow.create_issue(run_ref, company_evidence)
    flow.set_participants(issue)
    agreement = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        result = flow.confirm_agreement(actor, agreement)
    agreement = {k: result['result'][k] for k in ('object_id', 'revision_id', 'payload_hash')}
    proposal = flow.propose_update(issue, agreement, _change(flow, rationale='Initial pair under 0.5.'))
    flow.review_update(proposal)
    strategy_ref, architecture_ref = flow.confirm_update(proposal)['result']['changed_refs']
    fields = flow.architecture_fields()
    # 0.4 夹具里 scope-a 是映射到 auth_a、DRI 为 dri_a 的 Domain；bf-value 是无映射的 Battlefield。
    scope_id = 'scope-a'
    check('initial_pair_created_under_0_5',
          flow.object(architecture_ref['object_id'])['effective_revision_id'] == architecture_ref['revision_id'])

    scoped_evidence = flow.upload_anchored({'kind': 'scope', 'object_ref': architecture_ref, 'scope_id': scope_id})
    check('scope_anchored_evidence_records_scope',
          payload_of(scoped_evidence)['anchor_scope_id'] == scope_id)

    flow.reframe_with_scope(issue, scoped_evidence, strategy_ref, architecture_ref, scope_id)
    view = flow.scope_view(architecture_ref['object_id'], scope_id)
    check('scope_view_lists_the_scoped_issue',
          any(item['ref']['object_id'] == issue['object_id'] for item in view['issues']))
    check('scope_view_projects_issue_lifecycle',
          view['issues'][0]['lifecycle']['stage'] == 'consensus_formal')
    check('scope_view_lists_scoped_evidence',
          any(item['ref']['object_id'] == scoped_evidence['object_id'] for item in view['evidence']))
    check('scope_view_names_unique_current_dri',
          view['current_dri']['status'] == 'available' and view['current_dri']['value']['principal_id'] == flow.principal('dri_a'))
    check('scope_view_has_no_state_yet', view['latest_state']['status'] == 'missing')

    state = flow.propose_scope_state(flow.scope_state_payload(architecture_ref, scope_id))
    view = flow.scope_view(architecture_ref['object_id'], scope_id)
    check('recommendation_is_not_canonical',
          view['latest_state']['status'] == 'missing' and view['latest_state']['pending_recommendation'] is True)
    confirmed = flow.confirm_state('dri_a', state)
    view = flow.scope_view(architecture_ref['object_id'], scope_id)
    check('scope_dri_confirmation_makes_it_canonical',
          view['latest_state']['status'] == 'available' and view['latest_state']['value']['rag'] == 'unknown')
    check('state_key_uses_scope_identity',
          h.sql(f, "SELECT outcome_id FROM gov_method_state_keys WHERE scope_id=%s AND state_id=%s",
                (f['scope_id'], state['object_id']))[0]['outcome_id'] == 'scope:' + scope_id)

    company = flow.company_view()
    check('company_view_lists_every_scope_definition',
          {s['unit_id'] for s in company['scopes']} == {u['unit_id'] for u in fields['battlefields'] + fields['domains']})
    check('company_view_lists_company_evidence',
          any(item['ref']['object_id'] == company_evidence['object_id'] for item in company['evidence']))
    check('relations_registry_declares_anchoredTo_for_0_5_only',
          any(r['predicate'] == 'anchoredTo' for r in flow.relations('tkos.method/0.5')['relations'])
          and not any(r['field'] == 'anchor_ref' for r in flow.relations('tkos.method/0.4')['relations']))
    check('no_execution_side_effects',
          not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))
    return {'checks': checks, 'issue': issue, 'strategy_ref': strategy_ref, 'architecture_ref': architecture_ref,
            'scope_id': scope_id, 'state': confirmed, 'evidence': scoped_evidence}


def negatives(h, f, flow, ctx):
    checks = []

    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)

    response = flow.upload_anchored(None, expected={422})
    check('evidence_without_anchor_is_rejected_before_storage', response['error']['code'] == 'INVALID_REQUEST')
    response = flow.upload_anchored({'kind': 'scope', 'object_ref': ctx['architecture_ref'], 'scope_id': 'no-such-unit'}, expected={422})
    check('evidence_anchor_to_unknown_scope_is_rejected', response['error']['code'] == 'INVALID_REQUEST')

    flow.reframe_with_scope(ctx['issue'], ctx['evidence'], ctx['strategy_ref'], ctx['architecture_ref'], 'no-such-unit',
                            expected={'INVALID_REQUEST'})
    check('issue_scope_must_exist_in_the_exact_architecture')

    payload = flow.scope_state_payload(ctx['architecture_ref'], 'no-such-unit')
    flow.deny('co_agent', flow.command('method_propose_state', {'domain_id': f['domains']['company'], 'payload': payload},
                                       actor='co_agent'), codes={'INVALID_REQUEST'})
    check('scope_state_needs_an_existing_scope')
    payload = flow.scope_state_payload(ctx['architecture_ref'], 'bf-value')  # Battlefield without auth mapping
    flow.deny('co_agent', flow.command('method_propose_state', {'domain_id': f['domains']['company'], 'payload': payload},
                                       actor='co_agent'), codes={'INVALID_REQUEST'})
    check('scope_state_needs_an_explicit_authorization_domain_mapping')

    payload = flow.scope_state_payload(ctx['architecture_ref'], ctx['scope_id'])
    again = flow.propose_scope_state(payload)
    flow.deny('co_agent', flow.command('method_propose_state', {'domain_id': f['domains']['company'], 'payload': payload},
                                       actor='co_agent'), codes={'VERSION_CONFLICT'})
    check('same_scope_and_as_of_needs_previous_state_ref')
    body = flow.command('method_confirm_state', {'reason': 'Not my Scope.'}, oid=again['object_id'], actor='owner_a')
    flow.deny('owner_a', body, codes={'FORBIDDEN'})
    check('only_the_mapped_scope_dri_confirms')
    body = flow.command('method_confirm_state', {'reason': 'Known without evidence', 'summary': 'x', 'rag': 'green'},
                        oid=again['object_id'], actor='dri_a')
    flow.deny('dri_a', body, codes={'INVALID_REQUEST'})
    check('override_cannot_invent_a_known_rating_without_evidence')
    return checks


def run(h: MethodHarness, source: Path):
    f = seed_v05(h.env, h.private / 'identities.json', 'runtime-acceptance-method-v05')
    register_v05(h, source, f)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        ctx = happy_path(h, f, flow)
        negative_checks = negatives(h, f, flow, ctx)
        public_json(h.output / 'summary.json', {
            'happy_path_passed': True, 'happy_path_checks': ctx['checks'],
            'negative_matrix_passed': True, 'negative_checks': negative_checks,
            'runtime_method_v05_api_accepted': True,
            'scope': 'Synthetic 0.5 HTTP/PG chain; controlled Agent inputs, not real-model acceptance',
            'partner_wiring': 'not_verified', 'clark_browser': 'not_verified', 'real_model': 'not_run'})
    finally:
        flow.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError('Use fresh private and output paths')
    args.private.mkdir(parents=True, mode=0o700)
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    initial = source_manifest(Path('src').resolve())
    error = None
    try:
        run(h, Path('src').resolve())
    except BaseException as exc:  # noqa: BLE001 - re-raised after the harness is closed
        error = exc
    finally:
        try:
            h.close()
        finally:
            if source_manifest(Path('src').resolve()) != initial and error is None:
                error = RuntimeError('source changed during the acceptance run; rerun on a stable checkpoint')
    if error is not None:
        raise error


if __name__ == '__main__':
    main()
```

（`flow.create_issue`、`set_participants`、`draft_agreement`、`confirm_agreement`、`propose_update`、`review_update`、`confirm_update`、`architecture_fields`、`confirm_state`、`open_run` 均来自 `acceptance/method_v04/flow.py`，本子类只改契约版本。0.4 夹具的责任结构里：`scope-a` 是映射到 `auth_a`、当前 DRI 为 `dri_a` 的 Domain；`scope-b` 是映射到 `auth_b` 的 Battlefield；`bf-value` 是无映射的 Battlefield。正向链用 `scope-a`，无映射负例用 `bf-value`。）

- [ ] **Step 4: README 与跑法**

`acceptance/method_v05/README.md`：

```markdown
# Method 0.5 独立验收

前置：`python3 acceptance/runtime/infra.py up` 建好隔离 PG+MinIO，并按 `acceptance/method_v04/README` 同样方式准备 env 文件。

    .venv/bin/python -m acceptance.method_v05.run --env-file <env> --private <fresh-private-dir> --output <fresh-output-dir>

结束后 `summary.json` 里 `runtime_method_v05_api_accepted` 为 true 才算通过；`partner_wiring`、`clark_browser`、`real_model` 保持 not_verified / not_run，不由本脚本宣称。
之后必须再跑一遍 `acceptance/method_v04/run.py`，证明 0.4 scope 的行为未变。
```

- [ ] **Step 5: 执行两套验收**

```bash
.venv/bin/python -m acceptance.method_v05.run --env-file .runtime-acceptance/env --private artifacts/v05-private --output artifacts/v05-output
.venv/bin/python -m acceptance.method_v04.run --env-file .runtime-acceptance/env --private artifacts/v04-private --output artifacts/v04-output
```

Expected: 两份 `summary.json` 均 `*_api_accepted: true`；0.4 的检查数与 v0.4.0 发布记录一致（29 正向 + 39 负向）。任何失败先修代码再重跑，不改断言以迁就实现。

- [ ] **Step 6: 提交**

```bash
git add acceptance/method_v05
git commit -m "test(method): add the tkos.method/0.5 real HTTP/PG acceptance matrix

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 15: 文档、快照重核与 OpenAPI 导出

**Files:**
- Create: `docs/method-05-implementation-status.md`
- Modify: `README.md`（「Method 0.4」小节之后加「Method 0.5」小节；目录表加 `acceptance/method_v05/`）
- Modify: `docs/reviews/2026-09-17-business-data/inventory-runtime-map.csv`（I01、I03、I09、I13 四行）
- Regenerate: `docs/runtime-dashboard-openapi.json`、`docs/runtime-governance-openapi.json`、`docs/runtime-workspace-openapi.json`

- [ ] **Step 1: 状态文档**

创建 `docs/method-05-implementation-status.md`（数字在验收后填入实际值，不写估计）：

```markdown
# Method 0.5（M0 对齐增量）实施与独立验收状态

日期：<执行日期>。分支：<分支名>。仅修改 Runtime，未修改 Clark。

## M0 裁决

| 编号 | 假设 | 结论 | 日期 |
| --- | --- | --- | --- |
| A1 | OperatingState 主体可为 Scope，由 Scope DRI 确认 | <通过/否决/修改> | |
| A2 | StrategicIssue 可选唯一主 Scope | | |
| A3 | 0.5 证据上传必须声明锚点 | | |
| A4 | 会议行动留在 workspace 0.1 场景记录，只补状态 | | |

## 已交付

| 增量 | 结果 |
| --- | --- |
| 契约与迁移 | `tkos.method/0.5` 冻结契约 SHA `<SHA256>`；迁移 0029（绑定门禁、两个索引）、0030（会议 action_status） |
| 议题主 Scope | `primary_scope_id` 可选，存在正式结构时必须落在其中 |
| Scope 级 State | 推荐/确认分离；责任人为映射授权域的唯一当前 DRI；身份跨 Architecture 修订稳定 |
| 证据锚点 | company / scope / object 三选一；旧契约拒绝该字段 |
| 关系注册表 | `relations.py` 覆盖 0.4/0.5 全部 ExactRef 字段；看板 downstream 由其派生；`GET /v1/dashboard/ontology/relations` |
| 可寻址节点 | `GET /v1/dashboard/company`、`GET /v1/dashboard/scopes/{architecture}/{unit}` |
| 议题生命周期 | 六个可证明阶段 + 两个 not_derivable |

## 独立验收结果

- HTTP：0.5 正向 <n> 项、负向 <n> 项；0.4 回归 29 正向 + 39 负向（应与 v0.4.0 一致）。
- Python：<n> passed、<n> skipped；空库迁移至 0030 及重放 1 passed。
- 前端：<n> 个文件、<n> 项通过；类型检查、生产构建与资源清单校验通过。
- 快照重核：<已重生成 / 待定义文件不可得，暂未重核>。

## 交付边界

伙伴接线、Clark 浏览器、真实模型、生产迁移与部署未验证。八个能力问题中第 4、6 问需 0.5 scope 启用后才能回答；第 3 问仍到 Strategy 整体而非 Choice；第 8 问路径与 M0 不同（无 Signal 一跳）。
```

- [ ] **Step 2: README**

在 README「Method 0.4：正式链收口与独立来源」小节之后加：

```markdown
## Method 0.5：M0 本体对齐增量

- **议题主 Scope**：`StrategicIssue.primary_scope_id` 可选，存在正式责任结构时必须能在其中找到；CEO 首页按战场列议题的数据源。
- **Scope 级经营状态**：主体可为 Battlefield／Domain 定义项，由其映射授权域的唯一当前 DRI 确认；不对下层 RAG 求平均、不自动生成。
- **证据锚点**：0.5 scope 上传证据必须声明公司／Scope／对象锚点之一；旧契约拒绝该字段。
- **关系注册表与可寻址节点**：`GET /v1/dashboard/ontology/relations`、`/company`、`/scopes/{architecture}/{unit}`；议题详情附生命周期投影。

契约见 [tkos.method/0.5](docs/contracts/tkos-method-0.5.md)、[注册表](docs/runtime-method-registry-0.5.json)、[状态](docs/method-05-implementation-status.md)。0.1–0.4 对象保留原绑定与生效规则。
```

目录表加一行 `| \`acceptance/method_v05/\` | Method 0.5 M0 对齐增量隔离验收 |`。

- [ ] **Step 3: 快照重核**

编辑 `docs/reviews/2026-09-17-business-data/inventory-runtime-map.csv` 四行的「校准结论」「差异或边界」「建议下一步」：

- I01 Strategic Issue：差异改为「0.5 已加 primary_scope_id；候选对象与固定阶段仍在 0.1–0.3」，下一步「M0 v0.2 通过后按 0.5 契约核对」。
- I03 Strategic Architecture：差异改为「0.4 起 Battlefield + Domain 平行，0.5 起可作为 State 主体与证据锚点」。
- I09 Operating State：差异改为「0.5 覆盖 Battlefield/Domain 级（人确认，不聚合）」，校准结论保持「复用可行 / 部分」。
- I13 Values / Company Principles：Runtime 对应加「EvidenceAsset（0.5 anchor_kind=company）」。

然后重生成快照：

```bash
.venv/bin/python scripts/generate_method_map_snapshot.py --definitions-dir /tmp/tkos-method-current
DATABASE_URL=$DATABASE_URL uv run pytest tests/test_method_map.py -q
```

若 `/tmp/tkos-method-current` 下的规范化定义文件不存在（它们来自飞书导出，不入库），不要手改生成的 `method_map_snapshot.py`；在状态文档「快照重核」一栏写「待定义文件不可得，暂未重核」，CSV 修改照常提交。

- [ ] **Step 4: 重新导出 OpenAPI 快照**

```bash
DATABASE_URL=postgresql://unused .venv/bin/python scripts/export_dashboard_openapi.py
DATABASE_URL=postgresql://unused .venv/bin/python scripts/export_governance_openapi.py
.venv/bin/python acceptance/workspace_scenes/export_contract.py --help
```

前两条直接更新 `docs/runtime-dashboard-openapi.json` 与 `docs/runtime-governance-openapi.json`；第三条先看参数，再按其用法导出 `docs/runtime-workspace-openapi.json`（`action_status` 必须出现在事件联合里）。

- [ ] **Step 5: 全量回归并填数**

```bash
DATABASE_URL=$DATABASE_URL uv run pytest tests -q
PYTHONPATH=src uv run pytest tests/dashboard --confcutdir=tests/dashboard -q
node --test tests/workbench-ui/*.test.mjs
cd workbench/dashboard && npx vitest run
```

把实际数字填进状态文档；跳过项逐条写原因。

- [ ] **Step 6: 提交**

```bash
git add README.md docs/method-05-implementation-status.md docs/reviews/2026-09-17-business-data/inventory-runtime-map.csv docs/runtime-dashboard-openapi.json docs/runtime-governance-openapi.json docs/runtime-workspace-openapi.json src/memory_service_runtime/governed/method_map_snapshot.py
git commit -m "docs: record the Method 0.5 M0-alignment increment and refresh contract snapshots

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 后续版本待办（不在本计划内）

| 版本 | 事项 | 依据 |
| --- | --- | --- |
| R2 | Scope 之间的依赖边（Mission dependsOn Mission/Scope），Co-agent 识别跨 Mission 阻塞需要可查边 | M0 缺口二；路线图 R2 |
| R2 | Agent / Skill 类与 `executedBy`，数字员工矩阵治理需要 Skill 版本与工具权限 | M0 缺口三；路线图 R2 |
| R3 | WorkPackage / Human+AI Plan；Play 是否建类由 R3 决定 | 契约 §9；路线图 R3 |
| 待定 | Period 节点；周期制度由 DRI 确认后再定 | M0 缺口一 |
| 待定 | `serves` 到 Strategy Choice 粒度；需要 Strategy 内部 Choice 有稳定 id | M0 4.2 |
| 待定 | OperatingProblem `level` 与 M0 `routedTo` 四级命名统一（文档侧或 0.6） | 对照评估第 3 条 |

## 自检记录（写计划时执行）

- 规格覆盖：M0 八问中第 4 问（Task 6 + 10）、第 6 问（Task 7 + 10）、第 2 问的按战场列议题（Task 5 + 10）、关系显式化（Task 8 + 9）、路线图 MVP 的会议行动状态（Task 12）与议题生命周期投影（Task 11）各有任务；第 3、8 问的差异记入后续待办，不冒充完成。
- 占位符：契约 SHA 与验收数字是执行时才能得到的值，均给出计算命令；快照重核在定义文件不可得时有明确的不作为路径。
- 类型一致性：`scope_state_key` / `is_scope_state` / `SCOPE_STATE_OUTCOME_PREFIX`（Task 4）被 Task 10 引用；`scope_state_subject_assignment`（Task 6）被 Task 4 的 `method_v05.scoped_assignment` 与 `method_service` 引用；`relations.downstream_fields()` / `predicate_for()` / `catalog()`（Task 8）被 Task 9 引用；`OntologyRelations`（Task 13）与 Task 9 的 JSON 字段一一对应；`_visible_head` / `_visible_revision` / `_read_at` 均为 `dashboard.py` 现有名字。
