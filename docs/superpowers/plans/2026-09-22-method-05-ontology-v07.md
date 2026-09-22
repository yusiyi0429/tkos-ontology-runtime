# Method 0.5 · 本体 v0.7 对齐（r2）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动 0.1–0.4 任何既有语义的前提下，交付 `tkos.method/0.5`，让 Runtime 能按《TKOS 本体结构 v0.7（M1 范围）》承载本期 M1 的对象与关系：Constraint 一级对象、LTCO 审视结论、Period Review 的 CEO 确认门、PCO 承接已确认复盘与约束、Mission 的贡献／依赖／资源、责任域 DRI 唯一承诺与 Owner 生效时点、无人工确认且带起止时间的 Operating State，以及公司集合视图与确认记录投影两个读取面；并把本体登记（`ontology-registry-0.7`）冻结钉进 0.5 profile。

**Architecture:** 沿用「新契约版本 = 新 scope 显式启用、旧对象保留原绑定」的既有机制。新增 `method_v05_models.py`（只重定义与 0.4 有差异的 payload 与动作）、`method_v05.py`（用 collector / runner 登记表覆盖有差异的动作，其余委托 `method_v04`）、`method_v05_profile.py`（契约与本体登记双重钉定）、迁移 `0029`（Constraint 对象类型、0.5 绑定门禁、主 Scope 索引）。`method_v04.py` 只做一处行为不变的参数化：`_LightExecution` 接受调用方的契约版本（默认仍是 0.4），否则 0.4 的责任解析与候选依据校验对 0.5 对象一律报 `PROTOCOL_BINDING_CONFLICT`。所有正式写入仍走同一治理事务，不引入第二套状态或权限。

**Tech Stack:** Python 3.12 + FastAPI + Pydantic v2（`extra="forbid"`、Strict 类型）+ psycopg + PostgreSQL 17（RLS、append-only 迁移）；PyYAML（已在依赖里）；前端 React + TypeScript + Vite + vitest（`workbench/dashboard`）；验收脚本走真实 HTTP + PG（`acceptance/`）。

**Spec:** 执行者先读这五份：
- 《TKOS 本体结构 v0.7（M1 范围）｜CEO 对齐稿 2026-09-22》，飞书 docx `KuU8d7rq6oozKDxtZqkcHI0nnJa`（表 A2 二十个对象、表 B 三十三条关系、第四节待定项）。
- 本体登记 `docs/contracts/ontology-registry-0.7.yaml`（与上稿逐字一致；`runtime_0_4` 记录当前差距，本计划 Task 0 补 `runtime_0_5` 目标状态）。
- 冻结契约 `docs/contracts/tkos-method-0.4.md`（0.5 只写差异，其余逐字沿用）。
- 9/22 内部对齐会妙记 `obcn629472ww9266s9qy47l4`（结论已并入 v0.7 第三节）。
- 被搁置的 r1 计划 `docs/superpowers/plans/2026-09-20-m0-alignment-r1.md`：只借用它的机制（契约钉定、模型继承、迁移门禁、验收骨架），它的三项内容目标（议题主 Scope、Scope 级 State、证据锚点）不在本计划内。

## 全局约束

- 迁移严格 append-only：新增 `0029_method_v05.sql`，同步更新 `tests/test_migrations.py` 的完整清单；不改已应用文件。当前最新是两个 `0028_*`。
- `SUPPORTED_PROTOCOL_CONTRACTS` 是编译期全集；0.5 必须加入该集合并配 profile 绑定与迁移门禁，否则任何 0.5 请求返回 `PROTOCOL_NOT_SUPPORTED`。
- 0.1–0.4 的模型、执行器、读取语义原样保留。`method_v04_models.py` 不得修改。`method_v04.py` 只允许 Task 4 列出的参数化改动（默认参数 = 0.4，行为不变），靠 0.4 回归测试与 `acceptance/method_v04` 复跑证明。
- 治理代码不得中途 commit、切换连接或跨事务复用 `AuthContext`（`governed/db.py` 顶部）。
- 测试数据一律随机 scope，禁止 `local/local-org`；业务记录只经治理路径播种。`tests/conftest.py` 在收集阶段就要求 `DATABASE_URL`，纯模型测试也不例外。`uv run` 离线时会因重建包失败，本计划统一用 `DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest …`。前端测试在 `workbench/dashboard` 下 `npm test -- <文件>`。
- 不打印或提交凭据、`.runtime-acceptance/`、原始验收产物；`artifacts/`、`draft_*` 不当源码。
- 文档与注释中文为主；代码标识符、动作名、对象类型名保持英文。
- 每个任务结束提交一次，提交信息末尾带 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。
- 汇报时把已执行的检查、跳过项、未验证的部署边界分开写；本地通过不等于已部署。

## 计划前提与决策点

下面八条是本计划的默认取向，改变任何一条都要先改契约文本（Task 1）再改代码。

- **D1 新版本、旧版冻结。** 0.5 是新契约版本，用新 scope 启用；不就地重解释 0.4 对象，不做跨版本依赖。
- **D2 承诺人只有责任域 DRI。** v0.7 表 B「Mission 由谁负责 = Owner，端到端负责，实际执行可分派」与 A2「DRI 拍板成立与指派，Owner 参与讨论」。0.5 的候选承诺只对 PCO 做，一次覆盖该 PCO 与其全部 Mission；`m1b_commit_candidate` 指向 Mission 一律拒绝。Mission 最终确认人是 CEO 待定项，若裁决为 Owner，进 0.6，本版不动（0.4 仍是 Owner commit，可对照）。
- **D3 Operating State 无人工确认，带起止时间。** v0.7 A2「无人确认，MF / Co-Agent 生成」「对象与时间范围（起止时间）」。0.5 去掉 `method_confirm_state`，`method_propose_state` 直接产生正式状态；`period` 新增，`as_of` 保留且必须等于 `period.end`，这样 `gov_method_state_keys` 不用改。
- **D4 Period Review 有 CEO 确认门。** v0.7 A2「CEO，可改；Co-Agent 起草」「确认后才起草 PCO」。新增 `m1b_confirm_review`；PCO 的 `period_review_ref` 必须指向已确认复盘；scope 内尚无更早已确认复盘时（首个周期）允许为空。
- **D5 Constraint 是独立对象类型。** 适用范围 company / scope / mission 三选一，确认人按范围解析（CEO / 该 Scope 的当前 DRI / Mission 主 Scope 的 DRI）。LTCO、PCO、Mission 只能引用已确认的 Constraint，引用是参考不是承接；抢人、超限、错期的校验是 Agent 分析，本版不自动判定。
- **D6 本体登记冻结进 profile；关系投影表不在本计划。** 登记 YAML 导出为确定性 JSON，SHA-256 钉进 0.5 profile 的 `ontology_registry_ref`，迁移门禁一并校验。反向查询继续用 JSONB 路径，只加一个 `primary_scope_id` 表达式索引；`gov_method_relations` 投影表留到 0.6。
- **D7 工作台只补人工门。** 0.5 规则版本、Constraint 的读法、三个人工门表单（LTCO 确认加结论、复盘确认、约束确认）。登记 / 修订 Constraint 走 API 或 Agent，本版不做浏览器表单；本体登记驱动的地图也不做。
- **D8 M2 / M3 / MF 对象不进 0.5。** Mission Play、人 + Agent Plan、Mission Result、Finding、Management Issue 不新增对象；M1B.3 沿用 0.4 的 `OperatingProblem`；M1-A 与 Signal 四类不动。

## 文件结构

新建：
- `scripts/export_ontology_registry.py` — 登记 YAML → 确定性 JSON。
- `docs/contracts/ontology-registry-0.7.json` — 冻结字节，被 profile 与迁移钉定。
- `docs/contracts/tkos-method-0.5.md` — 契约文本（全文即契约，字节级钉定）。
- `docs/contracts/method-profile-0.5.json`、`docs/runtime-method-registry-0.5.json` — 由代码生成。
- `src/memory_service_runtime/governed/method_v05_profile.py` — 契约身份与本体登记引用。
- `src/memory_service_runtime/governed/method_v05_models.py` — 0.5 严格模型与动作表。
- `src/memory_service_runtime/governed/method_v05.py` — 0.5 执行器（登记表覆盖 + 委托 0.4）。
- `src/memory_service_app/migrations/0029_method_v05.sql`。
- `tests/test_ontology_registry.py`、`tests/test_method_v05_models.py`、`tests/test_method_v05_dispatch.py`、`tests/test_method_v05_readers.py`。
- `acceptance/method_v05/{__init__,fixture,flow,run}.py`、`acceptance/method_v05/README.md`。
- `docs/method-05-implementation-status.md`、`docs/method-v05-freeze-checkpoint.md`。

修改：
- `docs/contracts/ontology-registry-0.7.yaml`（补 `runtime_0_5`，revision 0.7.1）。
- `method_v04.py`（`_LightExecution` 契约版本参数化，共六处签名）。
- `method_models.py`、`protocol.py`、`models.py`、`method_service.py`、`method_readers.py`、`method_access.py`、`workbench.py`、`governance.py`、`profile.py`、`control.py`、`dashboard.py`、`method_map.py`、`routes.py`、`src/memory_service_app/governance_commands.py`（版本分发点）。
- `tests/test_migrations.py`（清单）。
- `workbench/dashboard/src/lib/ontology.ts`、`lib/labels.ts`、`MethodActions.tsx` 及其测试。
- `README.md`。

## 任务顺序与依赖

Task 0 → 1 → 2 → 3 → 4 是串行骨架；5、6、7、8、9 各自依赖 4，彼此独立（可并行，但都改 `method_v05.py`，串行执行最省合并）；10 依赖 5–9；11 依赖 2 与 10；12 依赖 5–10；13 最后。每个任务结束时 0.4 及更早的测试用例数量不变、全部通过。

---

### Task 0: 本体登记补 `runtime_0_5` 目标状态并导出冻结 JSON

**Files:**
- Modify: `docs/contracts/ontology-registry-0.7.yaml`
- Create: `scripts/export_ontology_registry.py`
- Create: `docs/contracts/ontology-registry-0.7.json`
- Test: `tests/test_ontology_registry.py`

**Interfaces:**
- Produces: 登记里每个对象、关系、人工门都有 `runtime_0_5: {status, …}`；`docs/contracts/ontology-registry-0.7.json` 是 `json.dumps(yaml, ensure_ascii=False, sort_keys=True, indent=1) + "\n"` 的字节；Task 1 对它取 SHA-256，Task 13 用它核对 0.5 模型字段。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_ontology_registry.py`：

```python
"""本体登记（docs/contracts/ontology-registry-0.7.yaml）与其冻结 JSON、运行时模型的一致性。"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Annotated, Union, get_args, get_origin

import yaml
from pydantic import BaseModel

from memory_service_runtime.governed import method_v04_models as v4
from memory_service_runtime.governed.method_m1a_models import ExactRef

ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = ROOT / "docs/contracts/ontology-registry-0.7.yaml"
JSON_PATH = ROOT / "docs/contracts/ontology-registry-0.7.json"
STATUSES = {"exists", "partial", "missing", "product_mechanism"}


def registry():
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def _unwrap(annotation):
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    return annotation


def model_paths(model, prefix="", depth=0):
    """模型上所有字段路径；列表加 []，嵌套模型下钻两层，到 ExactRef 为止。"""
    out = set()
    for name, field in model.model_fields.items():
        annotation = _unwrap(field.annotation)
        candidates = [annotation]
        if get_origin(annotation) in (Union, types.UnionType):
            candidates = [_unwrap(a) for a in get_args(annotation) if a is not type(None)]
        for candidate in candidates:
            path = prefix + name
            if get_origin(candidate) is list:
                candidate = _unwrap(get_args(candidate)[0])
                path += "[]"
            out.add(path)
            if (isinstance(candidate, type) and issubclass(candidate, BaseModel)
                    and not issubclass(candidate, ExactRef) and depth < 2):
                out |= model_paths(candidate, path + ".", depth + 1)
    return out


def test_frozen_json_is_the_canonical_export_of_the_yaml():
    expected = json.dumps(registry(), ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    assert JSON_PATH.read_text(encoding="utf-8") == expected


def test_ids_are_unique_relations_name_registered_objects_and_statuses_are_known():
    data = registry()
    assert data["revision"] == "0.7.1"
    ids = [o["id"] for o in data["objects"]]
    assert len(ids) == len(set(ids)) == 20
    rids = [r["id"] for r in data["relations"]]
    assert len(rids) == len(set(rids)) == 33
    for relation in data["relations"]:
        assert relation["from"] in ids and relation["to"] in ids, relation["id"]
    for item in [*data["objects"], *data["relations"], *data["gates"]]:
        for key in ("runtime_0_4", "runtime_0_5"):
            assert item[key]["status"] in STATUSES, (item["id"], key)


def test_runtime_0_4_fields_exist_on_the_0_4_models():
    paths = {kind: model_paths(model) for kind, model in v4.PAYLOAD_MODELS.items()}
    for item in registry()["objects"]:
        runtime = item["runtime_0_4"]
        kind = runtime.get("object_type")
        if kind in paths:
            for field in runtime.get("fields", []):
                assert field in paths[kind], (item["id"], field)
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_ontology_registry.py -q
```

Expected: 第一条 `FileNotFoundError`（JSON 不存在），第二条 `KeyError: 'runtime_0_5'`。

- [ ] **Step 3: 给登记补 `runtime_0_5`**

在仓库根目录执行（脚本按 id 定位条目，在每条 `runtime_0_4` 之后插入 `runtime_0_5`；对象是块写法，关系与人工门是流式写法）：

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
p = Path("docs/contracts/ontology-registry-0.7.yaml")
lines = p.read_text(encoding="utf-8").split("\n")
OBJECTS = {
 "Company": '{status: partial, object_type: CompanyReference, notes: 与 0.4 相同，稳定公司陈述走 A2 CompanyReference}',
 "Strategy": '{status: exists, object_type: Strategy, fields: [statement, "required_capabilities[]", source_agreement_ref, source_proposal_ref], notes: 与 0.4 相同}',
 "StrategicArchitecture": '{status: exists, object_type: StrategicArchitecture, fields: ["battlefields[]", "domains[]", strategy_ref, source_agreement_ref, source_proposal_ref], notes: 与 0.4 相同}',
 "BattlefieldDomain": '{status: partial, object_type: StrategicArchitecture, notes: 与 0.4 相同，仍是 Architecture 的定义项}',
 "RoleAssignment": '{status: exists, table: gov_role_assignments, notes: 与 0.4 相同；Mission Owner 的生效时点记在 Mission 的 state.owner_activation_record_id}',
 "Constraint": '{status: exists, object_type: Constraint, fields: [applies_to.kind, applies_to.scope_id, applies_to.mission_ref, architecture_ref, statement, constraint_type, effective, source, authority, severity, "evidence_refs[]"], notes: 0.5 新增；动作 m1b_record_constraint / m1b_revise_constraint / m1b_confirm_constraint，确认人按 applies_to 解析}',
 "LTCO": '{status: exists, object_type: LTCO, fields: [primary_scope_id, period, architecture_ref, strategy_ref, result_statement, "criteria[]", boundary, horizon, why, "baseline_refs[]", realization_logic, "key_assumptions[]", "constraint_refs[]"], notes: 审视结论在 m1b_confirm_ltco 的 conclusion 与 state.last_review}',
 "PCO": '{status: exists, object_type: PCO, fields: [primary_scope_id, period, parent_ltco_ref, period_review_ref, architecture_ref, strategy_ref, current_reality, result_statement, "criteria[]", expected_lt_advance, why, boundary, "constraint_refs[]"], notes: period_review_ref 必须指向已确认的 Period Review，首个周期可空}',
 "Mission": '{status: partial, object_type: Mission, fields: [owner_principal_id, primary_scope_id, parent_pco_ref, why, "requirements[]", "criteria[]", "evidence_refs[]", period, boundary, "contributes_to_scope_ids[]", "dependencies[]", "resource_needs[]", "constraint_refs[]"], notes: 承诺只由责任域 DRI 做；Owner 指派在 CEO 激活时生效；不成立 / 待调整 / 已关闭状态随 M3 的 Mission Result}',
 "MissionPlay": '{status: missing, notes: M2 / M3}',
 "HumanAgentPlan": '{status: missing, notes: M3}',
 "OperatingState": '{status: exists, object_type: OperatingState, fields: [subject_ref, as_of, period, summary, rag, "baseline_refs[]", "evidence_refs[]", "drilldown_refs[]", "data_gaps[]", generation_version], notes: 0.5 无人工确认，method_propose_state 直接成为 canonical；as_of 等于 period.end}',
 "PeriodReview": '{status: exists, object_type: PeriodReview, fields: [review_id, period, "target_refs[]", "state_refs[]", "fact_refs[]", "findings[]", "learnings[]", "implications[]", generation_version], notes: 新增 CEO 确认动作 m1b_confirm_review，可改 findings / learnings / implications}',
 "MissionResult": '{status: missing, notes: M3}',
 "StrategicIssue": '{status: partial, object_type: StrategicIssue, notes: 与 0.4 相同，M1-A 本期不展开}',
 "Agreement": '{status: exists, object_type: StrategicAgreement, notes: 与 0.4 相同}',
 "Finding": '{status: missing, notes: MF}',
 "ManagementIssue": '{status: partial, object_type: OperatingProblem, notes: 与 0.4 相同，M1B.3 本期不做}',
 "Evidence": '{status: partial, object_type: EvidenceAsset, notes: 与 0.4 相同，没有子类字段}',
 "TraceabilityRecord": '{status: partial, notes: 新增确认记录投影 GET /v1/method/objects/{id}/confirmations 与 review_records 的 effect 分类；交接、会议记录、通知、重开条件随 M2 / M3 / MF}',
}
RELATIONS = {
 "architecture_contains_unit": '{status: exists, path: "StrategicArchitecture.battlefields[] / domains[]"}',
 "unit_dri": '{status: exists, path: "battlefields[].current_dri_principal_id + gov_role_assignments(DOMAIN_DRI, domain_id)"}',
 "ltco_belongs_unit": '{status: exists, path: "LTCO.primary_scope_id"}',
 "pco_belongs_unit_period": '{status: exists, path: "PCO.primary_scope_id + PCO.period"}',
 "company_outcome_is_view": '{status: exists, path: "GET /v1/method/company-view 按主 Scope 汇总"}',
 "mission_belongs_pco": '{status: exists, path: "Mission.parent_pco_ref + Mission.primary_scope_id"}',
 "mission_contributes_unit": '{status: exists, path: "Mission.contributes_to_scope_ids[]"}',
 "mission_owner": '{status: exists, path: "Mission.owner_principal_id + state.owner_activation_record_id（CEO 激活时生效）"}',
 "company_owns": '{status: exists, path: "gov_scopes / gov_domains 的 scope 归属；公司级 Constraint.applies_to.kind = company"}',
 "architecture_serves_strategy": '{status: exists, path: "StrategicArchitecture.strategy_ref"}',
 "ltco_serves_architecture_unit": '{status: exists, path: "LTCO.architecture_ref + LTCO.primary_scope_id"}',
 "ltco_review_refs": '{status: exists, path: "LTCO.strategy_ref + LTCO.baseline_refs[] + LTCO.constraint_refs[]"}',
 "pco_serves_ltco_pr": '{status: exists, path: "PCO.parent_ltco_ref + PCO.period_review_ref（已确认的 Period Review）"}',
 "draft_refs_previous": '{status: exists, path: "ReviewWindow.previous_window_ref + PCO.constraint_refs[] / Mission.constraint_refs[]"}',
 "mission_serves_pco": '{status: exists, path: "Mission.parent_pco_ref"}',
 "mission_depends": '{status: exists, path: "Mission.dependencies[]（mission_ref 或 scope_id，含 needed_by）"}',
 "mission_resources_vs_constraint": '{status: partial, path: "Mission.resource_needs[] + Mission.constraint_refs[]", notes: 只存引用，校验抢人、超限、错期是 Agent 分析}',
 "constraint_scope": '{status: exists, path: "Constraint.applies_to.kind / scope_id / mission_ref"}',
 "os_describes": '{status: exists, path: "OperatingState.subject_ref + period{start,end}；as_of = period.end 仍是唯一键"}',
 "os_from_evidence": '{status: exists, path: "OperatingState.evidence_refs[]"}',
 "os_drilldown": '{status: exists, path: "OperatingState.drilldown_refs[]（只能引用 canonical 状态）"}',
 "pr_based_on": '{status: exists, path: "PeriodReview.target_refs[] + state_refs[] + fact_refs[]"}',
 "pr_consumes_result": '{status: missing}',
 "finding_from_os": '{status: partial, path: "OperatingProblem.state_ref（最接近）"}',
 "mi_from_finding": '{status: missing}',
 "change_enters_m1a3": '{status: partial, path: "m1a_transfer_problem / StrategicIssue.source_refs[]"}',
 "si_formed_after_reopen": '{status: partial, notes: 没有重开门}',
 "agreement_resolves_si": '{status: exists, path: "StrategicAgreement.issue_ref"}',
 "new_version_based_on_agreement": '{status: exists, path: "Strategy.source_agreement_ref / StrategyUpdateProposal.agreement_ref"}',
 "confirmation_points_version": '{status: exists, path: "gov_method_reviews.target_revision_id / gov_method_commitments.candidate_revision_id；GET /v1/method/objects/{id}/confirmations"}',
 "confirmation_authority": '{status: exists, path: "gov_method_commitments.assignment_id + gov_role_assignments.valid_from / valid_to"}',
 "evidence_relates": '{status: exists, path: "EvidenceAsset.anchor_ref / BusinessFact.subject_ref"}',
 "trace_records": '{status: exists, path: "gov_object_revisions + gov_lifecycle_events + gov_action_receipts；review_records 的 effect 分类"}',
}
GATES = {
 "gate_1_confirm_ltco": '{status: exists, action: m1b_confirm_ltco, notes: conclusion established / revised / maintained 与 statement 一起进确认记录}',
 "gate_2_confirm_period_review": '{status: exists, action: m1b_confirm_review, notes: CEO 可改 findings / learnings / implications}',
 "gate_3_dri_commit": '{status: exists, action: m1b_commit_candidate, notes: 只接受 PCO 责任，由本域 DRI 承诺；Mission 目标拒绝}',
 "gate_4_ceo_composition": '{status: exists, action: m1b_activate_candidates, notes: 激活记录即 Owner 指派生效时点，写入 Mission state.owner_activation_record_id}',
}
def after_runtime04(start, text, flow):
    j = start
    while "runtime_0_4:" not in lines[j]:
        j += 1
    if flow:
        assert lines[j].rstrip().endswith("}}"), lines[j]
        lines[j] = lines[j].rstrip()[:-1] + ",\n     runtime_0_5: " + text + "}"
    else:
        lines.insert(j + 1, "    runtime_0_5: " + text)
for oid, text in OBJECTS.items():
    after_runtime04(lines.index(f"  - id: {oid}"), text, flow=False)
for rid, text in RELATIONS.items():
    after_runtime04(next(k for k, l in enumerate(lines) if l.startswith("  - {id: " + rid + ",")), text, flow=True)
for gid, text in GATES.items():
    after_runtime04(next(k for k, l in enumerate(lines) if l.startswith("  - {id: " + gid + ",")), text, flow=True)
text = "\n".join(lines)
assert text.count('revision: "0.7.0"') == 1
text = text.replace('revision: "0.7.0"', 'revision: "0.7.1"')
text = text.replace("# 用途：① 文档的 A1 / A2 / 表 B 以后从这里生成；",
    "# 0.7.1：为 tkos.method/0.5 补 runtime_0_5（交付后的目标状态），JSON 导出后钉进 method-profile-0.5.json 的 ontology_registry_ref。\n# 用途：① 文档的 A1 / A2 / 表 B 以后从这里生成；")
text = text.replace("  runtime_status: exists 已有 | partial 部分（字段或语义有差） | missing 没有 | product_mechanism 产品或运行机制，不是经营对象\n",
    "  runtime_status: exists 已有 | partial 部分（字段或语义有差） | missing 没有 | product_mechanism 产品或运行机制，不是经营对象\n  runtime_0_5: tkos.method/0.5 交付后的目标状态；tests/test_ontology_registry.py 核对其 fields 都在 method_v05_models.PAYLOAD_MODELS 上\n")
p.write_text(text, encoding="utf-8")
import yaml
data = yaml.safe_load(text)
print("objects", sum(1 for o in data["objects"] if "runtime_0_5" in o), "relations", sum(1 for r in data["relations"] if "runtime_0_5" in r), "gates", sum(1 for g in data["gates"] if "runtime_0_5" in g))
PY
```

Expected 输出：`objects 20 relations 33 gates 4`。若某个 `replace` 的断言失败，说明登记文件头部与本计划写时（rev 0.7.0，2026-09-22）不同，先看 `git diff` 再决定。

- [ ] **Step 4: 写导出脚本并导出**

创建 `scripts/export_ontology_registry.py`：

```python
"""把本体登记 YAML 导出为确定性 JSON（sort_keys、ensure_ascii=False、indent=1）。

用法：PYTHONPATH=src .venv/bin/python scripts/export_ontology_registry.py \
        docs/contracts/ontology-registry-0.7.yaml docs/contracts/ontology-registry-0.7.json
导出的字节被 method-profile-0.5.json 的 ontology_registry_ref 与迁移 0029 钉定；
登记内容一变就要重新导出、重新 pin。
"""
import json
import sys
from pathlib import Path

import yaml


def main() -> None:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    target.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    print(target, "objects", len(data["objects"]), "relations", len(data["relations"]))


if __name__ == "__main__":
    main()
```

```bash
PYTHONPATH=src .venv/bin/python scripts/export_ontology_registry.py docs/contracts/ontology-registry-0.7.yaml docs/contracts/ontology-registry-0.7.json
```

- [ ] **Step 5: 运行确认通过**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_ontology_registry.py -q
```

Expected: `3 passed`。

- [ ] **Step 6: 提交**

```bash
git add docs/contracts/ontology-registry-0.7.yaml docs/contracts/ontology-registry-0.7.json scripts/export_ontology_registry.py tests/test_ontology_registry.py
git commit -m "docs(ontology): register the 0.5 target state and freeze the registry as JSON

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 1: tkos.method/0.5 契约文本与 profile（双重钉定）

**Files:**
- Create: `docs/contracts/tkos-method-0.5.md`
- Create: `src/memory_service_runtime/governed/method_v05_profile.py`
- Create: `docs/contracts/method-profile-0.5.json`（由代码生成）
- Test: `tests/test_method_v05_models.py`（本任务只写 pin 测试；Task 2 追加）

**Interfaces:**
- Produces: `method_v05_profile.CONTRACT_VERSION`、`SCHEMA_VERSION`、`CONTRACT_SHA256`、`ONTOLOGY_REGISTRY_SHA256`、`ONTOLOGY_REGISTRY_REVISION == "0.7.1"`、`validate(data)`、`content()`。Task 3 的迁移门禁、Task 4 的 `profile.py` / `control.py` 引用这些常量。

- [ ] **Step 1: 写契约文本**

创建 `docs/contracts/tkos-method-0.5.md`（全文即契约，之后任何一个字节的改动都要重新 pin）：

```markdown
# tkos.method/0.5 — 本体 v0.7 对齐增量契约（Constraint / 审视结论 / 复盘确认 / DRI 承诺 / 无人确认状态）

状态：**契约文字按《TKOS 本体结构 v0.7（M1 范围）｜CEO 对齐稿 2026-09-22》定稿；Mission 最终确认人待 CEO 裁决，本版按 DRI；Runtime 启用由 0.5 实施增量交付**。0.5 只有在协议注册表显式登记、且支持状态为本进程编译支持后才可调用；未启用前任何 0.5 请求返回 `PROTOCOL_NOT_SUPPORTED`，不产生业务成功。0.1／0.2／0.3／0.4 的解释、绑定、历史回执与回归保持不变；不自动迁移旧对象，不做跨版本放宽。

来源与校准：本体结构 v0.7（飞书 docx `KuU8d7rq6oozKDxtZqkcHI0nnJa`）、本体登记 `tkos.ontology-registry` 0.7.1（`docs/contracts/ontology-registry-0.7.json`）、总表 v0.8（即 v0.8.1）、M1-B v5.1、2026-09-22 内部对齐会（妙记 `obcn629472ww9266s9qy47l4`）、`docs/contracts/tkos-method-0.4.md`。本契约只写与 0.4 的差异，其余条款逐字沿用 0.4。

## 1. 版本、启用与隔离

- 每个隔离 scope 显式选择业务规则版本。0.5 使用独立的新 scope 启用；0.4 及更早 scope 继续按原绑定解释，不新增跨版本依赖、不就地重解释历史。
- 对象类型：0.4 的 15 个加 `Constraint`，共 16 个。动作：0.4 的 41 个去掉 `method_confirm_state`，加 `m1b_record_constraint`、`m1b_revise_constraint`、`m1b_confirm_constraint`、`m1b_confirm_review`，共 44 个。
- profile 除钉定本契约字节外，同时钉定本体登记 `tkos.ontology-registry` 0.7.1 的 JSON 字节（`ontology_registry_ref`）；两者任一改动都产生新 profile 修订并重新 pin。
- 未列入本契约的规则，以 0.4 契约为准。

## 2. Constraint（经营条件与约束）

- 独立对象类型 `Constraint`：`applies_to` 三选一（`company`；`scope` 加确切 Architecture 版本中的 Battlefield／Domain `unit_id`；`mission` 加确切 Mission 版本引用）、`statement`、`constraint_type`（people／money／capacity／policy／dependency／other）、`effective` 起止、`source`、`authority`、`severity`（hard／soft）、`evidence_refs`。
- 登记（`m1b_record_constraint`）与修订（`m1b_revise_constraint`）：CO_AGENT，或该范围的责任人本人。修订保持 `applies_to` 不变，产生新草稿版本；历史正式版本不变。
- 确认（`m1b_confirm_constraint`）：`company` 由当前 CEO；`scope` 由该 Scope 映射授权域的唯一当前 DOMAIN_DRI；`mission` 由其主 Scope 的当前 DRI。确认后成为正式版本（effective）。
- LTCO、PCO、Mission 的 `constraint_refs` 只能引用已确认的 Constraint，且其 `applies_to` 为 `company`、或与本对象主 Scope 相同（Mission 还可引用 `applies_to` 为本 Mission 的）。引用是参考，不是承接；抢人、超限、错期的校验是 Agent 分析，本契约不自动判定。

## 3. LTCO 审视结论

- `LTCO` 新增 `realization_logic`、`key_assumptions`、`constraint_refs`。`horizon` 的语义是"从当下起滚动的未来 6 个月"，文本承载，不自动计算。
- `m1b_confirm_ltco` 携带 `conclusion`：`established`（首次确认，对象尚无正式版本）、`revised`（对象已有正式版本，本次确认新草稿）、`maintained`（对象已是正式版本，本期审视维持不变：不产生新版本，只留一条带结论与说明的确认记录，只推进 CAS）。草稿目标不接受 `maintained`；已确认目标只接受 `maintained`，且必须指向确切的生效版本。
- 每次确认写入 `state.last_review = {conclusion, record_id}`；历次确认记录不覆盖。

## 4. Period Review 确认门与 PCO 承接

- `m1b_confirm_review`：当前 CEO 本人确认 `generated` 状态的 PeriodReview；可同时改写 `findings`／`learnings`／`implications`（产生 CEO 署名的新版本，Agent 起草版保留并记在 `state.agent_generation_ref`）。确认后 effective，`phase=confirmed`。`m1b_regenerate_review` 在 0.5 不再使复盘生效，生效只来自确认。
- `PCO` 新增 `period_review_ref`、`boundary`、`constraint_refs`。`period_review_ref` 必须指向已确认（effective 且 `phase=confirmed`）、且 `period.end` 不晚于本 PCO `period.start` 的 PeriodReview；本 scope 若已存在任一满足该时间条件的已确认 PeriodReview，则不允许为空（首个周期例外）。
- 候选与激活沿用 0.4；候选 PCO 保留起草时的 `period_review_ref`。

## 5. Mission：贡献、依赖、资源与承诺人

- `Mission` 新增 `boundary`、`contributes_to_scope_ids`（父 PCO 所引确切 Architecture 中、且不等于主 Scope 的 `unit_id`）、`dependencies`（`mission` 指确切 Mission 版本，或 `scope` 指 `unit_id`；`needed_by` 落在本 Mission `period` 内）、`resource_needs`、`constraint_refs`。
- 责任承诺只由责任域 DRI 对本域 PCO 做，一次承诺覆盖该 PCO 及其全部 Mission；`m1b_commit_candidate` 指向 Mission 一律拒绝（`INVALID_REQUEST`）。激活前置只检查每个 PCO 的 DRI 承诺；`unresolved_differences` 的关键分歧仍阻止激活。
- Mission Owner 参与窗口讨论，不握手；Owner 的正式指派在 CEO 整组激活时生效，Mission `state.owner_activation_record_id` 记该激活记录。Mission 最终确认人若 CEO 裁决为 Owner，进入 0.6，本版不变。

## 6. Operating State：起止时间、无人确认、下钻

- `OperatingState` 新增 `period`（起止）与 `drilldown_refs`；`as_of` 保留且必须等于 `period.end`。
- `method_propose_state` 直接产生正式（canonical）状态：`status=active`、effective、`phase=recorded`、`canonical_ref` 指向自身；0.5 没有 `method_confirm_state`。再次生成用 `previous_state_ref` 产生同一身份（同主体、同 `period`）的新版本；同一主体同一 `as_of` 只有一个身份。
- 生成人：CO_AGENT、CEO_AGENT，或主体责任人本人（责任解析沿用 0.4）。无证据只能 Unknown 并列明缺口（沿用 0.4）。
- `drilldown_refs` 只能引用已是 canonical 的 OperatingState，且不能引用与自身同主体的状态；上层引用下层，不自动汇总，不新增公司／Battlefield／Domain 聚合 State 对象（沿用 0.4）。
- 对状态有异议的责任人走 `method_open_problem`（0.4 语义），不改写状态。

## 7. 读取

- `GET /v1/method/company-view?period_start=&period_end=`：按主 Scope 汇总当前正式 LTCO、与该时段重叠的 PCO 与 Mission（Mission 含 `owner_effective_from`）、生效 Constraint；公司级 Constraint 单列，Mission 级 Constraint 按 Mission 归集。是投影，不是对象。
- `GET /v1/method/objects/{id}/confirmations`：该对象的决定类记录（确认、正式化、激活、重开、关闭、移交）与承诺行，各带 `principal_id`、`recorded_at`；承诺行带 `assignment_id`。
- `GET /v1/method/objects/{id}/reviews` 的每条记录附 `effect`：`decision`／`opinion`／`analysis`／`record`。
- `GET /v1/method/{collection}` 新增 `ltcos`、`pcos`、`missions`、`constraints`、`operating-states`。
- 以上读取按当前授权过滤，不返回不可见记录的计数；no-store。

## 8. 未交付边界（本契约不承诺）

- Mission Play、人 + Agent Plan、Mission Result、Finding、Management Issue 的新对象；M1B.3 仍用 0.4 的 `OperatingProblem` 语义。
- 战略相关输入（Signal）四类分型；M1-A 本期不展开。
- Mission Owner 作为 Role Assignment 行；`gov_method_relations` 投影表；旧对象自动升级；跨版本接续；Clark 与真实模型编排。
```

- [ ] **Step 2: 计算两个 SHA256**

```bash
shasum -a 256 docs/contracts/tkos-method-0.5.md docs/contracts/ontology-registry-0.7.json
```

记下两个 64 位十六进制值：契约的记为 `<CONTRACT_SHA256>`，登记 JSON 的记为 `<REGISTRY_SHA256>`。下面的 profile 模块、Task 3 的迁移、Task 13 的状态文档都用这两个值。以后改契约或登记都要重跑并重 pin（0.4 的 `0028_method_v04_contract_repin.sql` 就是这么来的）。

- [ ] **Step 3: 写 profile 模块**

创建 `src/memory_service_runtime/governed/method_v05_profile.py`（把两个占位符换成 Step 2 的值）：

```python
"""Method 0.5 ontology-v0.7 alignment contract identity, independent of 0.1-0.4.

Besides the contract bytes, a 0.5 profile pins the exact bytes of the ontology
registry JSON (docs/contracts/ontology-registry-0.7.json).
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from . import canon, method_profile

PROTOCOL_ID = "tkos.method"
CONTRACT_VERSION = "tkos.method/0.5"
SCHEMA_VERSION = "tkos.method-profile/0.5"
CONTRACT_SHA256 = "<CONTRACT_SHA256>"
ONTOLOGY_REGISTRY_ID = "tkos.ontology-registry"
ONTOLOGY_REGISTRY_REVISION = "0.7.1"
ONTOLOGY_REGISTRY_SHA256 = "<REGISTRY_SHA256>"
DISPLAY_NAME = "Method 0.5 ontology v0.7 alignment 2026-09-22"


class ContractRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_id: Literal["tkos.method"] = "tkos.method"
    revision: Literal["0.5"] = "0.5"
    content_sha256: Literal[CONTRACT_SHA256] = CONTRACT_SHA256


class OntologyRegistryRef(BaseModel):
    """本体登记 JSON 原始字节的 SHA256；登记改动必须产生新 revision 并重新 pin。"""
    model_config = ConfigDict(extra="forbid")
    registry_id: Literal[ONTOLOGY_REGISTRY_ID] = ONTOLOGY_REGISTRY_ID
    revision: Literal[ONTOLOGY_REGISTRY_REVISION] = ONTOLOGY_REGISTRY_REVISION
    content_sha256: Literal[ONTOLOGY_REGISTRY_SHA256] = ONTOLOGY_REGISTRY_SHA256


class Profile(method_profile.MethodProfileCore):
    profile_core_schema_version: Literal[SCHEMA_VERSION]
    revision: Literal["0.5.0"]
    display_name: Literal[DISPLAY_NAME]
    action_contract_ref: ContractRef
    ontology_registry_ref: OntologyRegistryRef


def validate(data):
    value = Profile.model_validate(data)
    if value.canonical_hash != canon.digest_excluding(value.model_dump(mode="json"), frozenset({"canonical_hash"})):
        raise ValueError("Method profile canonical_hash mismatch")
    return value


def content():
    value = {**method_profile.content(), "profile_core_schema_version": SCHEMA_VERSION,
             "revision": "0.5.0", "display_name": DISPLAY_NAME,
             "action_contract_ref": ContractRef().model_dump(mode="json"),
             "ontology_registry_ref": OntologyRegistryRef().model_dump(mode="json")}
    value["canonical_hash"] = canon.digest_excluding(value, frozenset({"canonical_hash"}))
    return value
```

- [ ] **Step 4: 生成 profile JSON**

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from memory_service_runtime.governed import method_v05_profile as p
open('docs/contracts/method-profile-0.5.json', 'w').write(json.dumps(p.content(), ensure_ascii=False, indent=2) + '\n')
print(p.content()['canonical_hash'])
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


def test_contract_and_registry_pins_and_profile_validate():
    assert profile.validate(profile.content())
    assert sha256((ROOT / 'docs/contracts/tkos-method-0.5.md').read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    assert sha256((ROOT / 'docs/contracts/ontology-registry-0.7.json').read_bytes()).hexdigest() == profile.ONTOLOGY_REGISTRY_SHA256
    registry = json.loads((ROOT / 'docs/contracts/ontology-registry-0.7.json').read_text())
    assert registry['revision'] == profile.ONTOLOGY_REGISTRY_REVISION
    saved = json.loads((ROOT / 'docs/contracts/method-profile-0.5.json').read_text())
    assert profile.validate(saved).canonical_hash == profile.content()['canonical_hash']
```

- [ ] **Step 6: 运行确认通过**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_models.py -q
```

Expected: `1 passed`。

- [ ] **Step 7: 提交**

```bash
git add docs/contracts/tkos-method-0.5.md docs/contracts/method-profile-0.5.json src/memory_service_runtime/governed/method_v05_profile.py tests/test_method_v05_models.py
git commit -m "feat(method): freeze the tkos.method/0.5 ontology-alignment contract and profile

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 2: 0.5 严格模型、注册表分发与请求信封

**Files:**
- Create: `src/memory_service_runtime/governed/method_v05_models.py`
- Create: `docs/runtime-method-registry-0.5.json`（由代码生成）
- Modify: `src/memory_service_runtime/governed/method_models.py:15-25`（`registry()` 增加 0.5 分支）
- Modify: `src/memory_service_runtime/governed/protocol.py:43-52`（`SUPPORTED_PROTOCOL_CONTRACTS`）
- Modify: `src/memory_service_runtime/governed/models.py:465-470, 509-560`（`ActionRequest` 的参数选择与信封规则）
- Test: `tests/test_method_v05_models.py`

**Interfaces:**
- Consumes: `method_v04_models` 的全部模型（只导入，不修改）。
- Produces: `method_v05_models.CONTRACT_VERSION`、`PAYLOAD_MODELS`（16 项）、`ACTION_PARAMS`（44 项）、`ACTION_TARGETS`、`OBJECT_TYPES`、`HUMAN_ACTIONS`、`AGENT_ACTIONS`、`V05_ONLY_ACTIONS`；模型 `ConstraintScope`、`ConstraintPayload`、`RecordConstraint`、`ReviseConstraint`、`ConfirmConstraint`、`LTCOPayload`、`ConfirmLTCO(conclusion, statement)`、`PCOPayload`、`MissionDependency`、`MissionPayload`、`CandidatePCO`、`CandidateMission`、`ResolveWindow`、`StatePayload`、`ProposeState`、`ConfirmReview`。Task 4–10 与 Task 12 都以这些名字为准。

- [ ] **Step 1: 写失败的模型测试**

在 `tests/test_method_v05_models.py` 末尾追加：

```python
import pytest
from pydantic import ValidationError
from uuid import uuid4

from memory_service_runtime.governed import method_models, method_v04_models as v4, method_v05_models as m, protocol
from memory_service_runtime.governed.models import ActionRequest


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


PERIOD = {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}


def constraint_payload(**updates):
    return {'title': 'Two engineers only', 'applies_to': {'kind': 'scope', 'scope_id': 'bf-1'},
            'architecture_ref': ref(), 'statement': 'Only two engineers are available this period.',
            'constraint_type': 'people', 'effective': PERIOD, 'source': 'Headcount plan',
            'authority': 'Scope DRI', 'severity': 'hard', 'evidence_refs': [], **updates}


def pco_payload(**updates):
    return {'title': 'P', 'primary_scope_id': 'bf-1', 'period': PERIOD, 'parent_ltco_ref': ref(),
            'period_review_ref': None, 'architecture_ref': ref(), 'strategy_ref': ref(),
            'current_reality': 'c', 'result_statement': 'r', 'criteria': ['c'],
            'expected_lt_advance': 'a', 'why': 'w', 'boundary': 'not promised', 'constraint_refs': [], **updates}


def mission_payload(**updates):
    return {'title': 'M', 'owner_principal_id': str(uuid4()), 'primary_scope_id': 'bf-1', 'parent_pco_ref': ref(),
            'why': 'w', 'requirements': ['r'], 'criteria': ['c'], 'evidence_refs': [],
            'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-15T00:00:00Z'},
            'boundary': 'b', 'contributes_to_scope_ids': ['dom-1'],
            'dependencies': [{'kind': 'scope', 'scope_id': 'dom-1', 'needed_by': '2026-10-10T00:00:00Z', 'note': 'n'}],
            'resource_needs': ['one designer'], 'constraint_refs': [], **updates}


def state_payload(**updates):
    subject = ref()
    return {'subject_ref': subject, 'as_of': '2026-10-31T00:00:00Z', 'period': PERIOD, 'summary': 'Evidence gap',
            'rag': 'unknown', 'baseline_refs': [subject], 'evidence_refs': [], 'drilldown_refs': [],
            'data_gaps': ['Awaiting evidence'], 'generation_version': 'test-1', **updates}


def test_registry_matches_generated_json_and_action_target_parity():
    registry = json.loads((ROOT / 'docs/runtime-method-registry-0.5.json').read_text())
    assert set(registry['actions']) == set(m.ACTION_PARAMS) and len(m.ACTION_PARAMS) == 44
    assert set(registry['object_types']) == m.OBJECT_TYPES and len(m.OBJECT_TYPES) == 16
    params, targets, payloads = method_models.registry('tkos.method/0.5')
    assert params is m.ACTION_PARAMS and targets is m.ACTION_TARGETS and payloads is m.PAYLOAD_MODELS
    assert set(m.ACTION_PARAMS) == set(m.ACTION_TARGETS)
    assert 'method_confirm_state' not in m.ACTION_PARAMS and 'method_confirm_state' in v4.ACTION_PARAMS
    assert m.V05_ONLY_ACTIONS == {'m1b_record_constraint', 'm1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'}
    assert m.V05_ONLY_ACTIONS <= m.HUMAN_ACTIONS and 'method_confirm_state' not in m.HUMAN_ACTIONS
    assert ('tkos.method', 'tkos.method/0.5') in protocol.SUPPORTED_PROTOCOL_CONTRACTS


def test_constraint_scope_is_exactly_one_of_company_scope_mission():
    assert m.ConstraintPayload.model_validate(constraint_payload()).applies_to.scope_id == 'bf-1'
    company = constraint_payload(applies_to={'kind': 'company'}, architecture_ref=None)
    assert m.ConstraintPayload.model_validate(company).architecture_ref is None
    with pytest.raises(ValidationError):  # scope without unit id
        m.ConstraintPayload.model_validate(constraint_payload(applies_to={'kind': 'scope'}))
    with pytest.raises(ValidationError):  # scope without the Architecture it belongs to
        m.ConstraintPayload.model_validate(constraint_payload(architecture_ref=None))
    with pytest.raises(ValidationError):  # company constraint must not carry a scope id
        m.ConstraintPayload.model_validate(constraint_payload(applies_to={'kind': 'company', 'scope_id': 'bf-1'}))
    mission = constraint_payload(applies_to={'kind': 'mission', 'mission_ref': ref()}, architecture_ref=None)
    assert m.ConstraintPayload.model_validate(mission).applies_to.kind == 'mission'


def test_ltco_confirmation_carries_a_conclusion_and_0_4_never_widens():
    assert m.ConfirmLTCO.model_validate({'conclusion': 'maintained', 'statement': 'Still valid'}).conclusion == 'maintained'
    with pytest.raises(ValidationError):
        m.ConfirmLTCO.model_validate({'statement': 'no conclusion'})
    with pytest.raises(ValidationError):
        v4.ConfirmLTCO.model_validate({'conclusion': 'maintained', 'statement': 'Still valid'})
    ltco = {'title': 'L', 'primary_scope_id': 'bf-1', 'period': PERIOD, 'architecture_ref': ref(), 'strategy_ref': ref(),
            'result_statement': 'r', 'criteria': ['c'], 'boundary': 'b', 'horizon': 'rolling 6 months', 'why': 'w',
            'baseline_refs': [ref()], 'realization_logic': 'logic', 'key_assumptions': ['a'], 'constraint_refs': []}
    assert m.LTCOPayload.model_validate(ltco).realization_logic == 'logic'
    with pytest.raises(ValidationError):
        v4.LTCOPayload.model_validate(ltco)


def test_pco_and_mission_keep_the_0_5_shape():
    assert m.PCOPayload.model_validate(pco_payload()).boundary == 'not promised'
    with pytest.raises(ValidationError):  # boundary is required in 0.5
        m.PCOPayload.model_validate({k: v for k, v in pco_payload().items() if k != 'boundary'})
    mission = m.MissionPayload.model_validate(mission_payload())
    assert mission.dependencies[0].scope_id == 'dom-1'
    with pytest.raises(ValidationError):  # a scope dependency never carries a mission ref
        m.MissionPayload.model_validate(mission_payload(dependencies=[
            {'kind': 'scope', 'scope_id': 'dom-1', 'mission_ref': ref(), 'needed_by': '2026-10-10T00:00:00Z', 'note': 'n'}]))
    with pytest.raises(ValidationError):  # duplicate contribution
        m.MissionPayload.model_validate(mission_payload(contributes_to_scope_ids=['dom-1', 'dom-1']))
    resolve = m.ResolveWindow.model_validate({
        'title': 'C', 'pcos': [{'object_id': str(uuid4()), 'payload': pco_payload()}],
        'missions': [{'object_id': str(uuid4()), **{k: v for k, v in mission_payload().items() if k != 'parent_pco_ref'}}],
        'dispositions': [], 'unresolved_differences': [], 'summary': 's'})
    assert resolve.missions[0].resource_needs == ['one designer']


def test_state_period_and_as_of_agree_and_confirmation_is_gone():
    assert m.StatePayload.model_validate(state_payload()).period.end == PERIOD['end']
    with pytest.raises(ValidationError):
        m.StatePayload.model_validate(state_payload(as_of='2026-10-15T00:00:00Z'))
    with pytest.raises(ValidationError):  # known rating without evidence is still forbidden
        m.StatePayload.model_validate(state_payload(rag='green', data_gaps=[]))
    with pytest.raises(ValidationError):  # 0.4 model never widens
        v4.StatePayload.model_validate(state_payload())
    assert m.ConfirmReview.model_validate({'statement': 'Confirmed', 'findings': ['f']}).learnings is None


def test_action_request_dispatches_v05_and_keeps_older_contracts_frozen():
    body = {'action_type': 'm1b_record_constraint', 'target': None, 'expected_versions': [],
            'idempotency_key': 'v05-unit-dispatch-0001', 'reason': 'Dispatch boundary check',
            'contract_version': 'tkos.method/0.5',
            'params': {'domain_id': str(uuid4()), 'payload': constraint_payload()}}
    accepted = ActionRequest.model_validate(body)
    assert accepted.params.payload.applies_to.kind == 'scope'
    for old in ('tkos.method/0.1', 'tkos.method/0.2', 'tkos.method/0.3', 'tkos.method/0.4'):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body, 'contract_version': old})
    with pytest.raises(ValidationError):  # 0.5 has no state confirmation
        ActionRequest.model_validate({**body, 'action_type': 'method_confirm_state', 'params': {'reason': 'x' * 8},
                                      'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'expected_version': 1}})
    with pytest.raises(ValidationError):  # targetless 0.5 action with a target
        ActionRequest.model_validate({**body, 'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()),
                                                         'expected_version': 1}})
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_models.py -q
```

Expected: `ModuleNotFoundError: memory_service_runtime.governed.method_v05_models`。

- [ ] **Step 3: 写 0.5 模型模块**

创建 `src/memory_service_runtime/governed/method_v05_models.py`：

```python
"""Strict ``tkos.method/0.5`` schemas: ontology v0.7 M1 alignment on top of frozen 0.4.

Only payloads and actions that differ from 0.4 are (re)defined here; every
other 0.4 model, action and target set is reused by reference.  The 0.4
module is frozen and never widened in place.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from . import method_v04_models as v4
from .a2_models import CanonicalUUID, IsoDateTime, NEStr, StrictModel
from .method_m1a_models import ExactRef
from .method_m1b_models import Period, distinct

CONTRACT_VERSION = "tkos.method/0.5"


# ------------------------------------------------------------- Constraint


class ConstraintScope(StrictModel):
    """适用范围三选一：company；scope（Battlefield / Domain 稳定 unit_id）；mission（确切 Mission 版本）。"""

    kind: Literal["company", "scope", "mission"]
    scope_id: NEStr | None = None
    mission_ref: ExactRef | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "ConstraintScope":
        if (self.kind == "scope") != (self.scope_id is not None):
            raise ValueError("a Scope constraint names exactly one unit_id")
        if (self.kind == "mission") != (self.mission_ref is not None):
            raise ValueError("a Mission constraint names exactly one exact Mission")
        return self


class ConstraintPayload(StrictModel):
    title: NEStr
    applies_to: ConstraintScope
    architecture_ref: ExactRef | None = None
    statement: NEStr
    constraint_type: Literal["people", "money", "capacity", "policy", "dependency", "other"]
    effective: Period
    source: NEStr
    authority: NEStr
    severity: Literal["hard", "soft"]
    evidence_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def scope_needs_its_architecture(self) -> "ConstraintPayload":
        if (self.applies_to.kind == "scope") != (self.architecture_ref is not None):
            raise ValueError("a Scope constraint cites the exact Architecture its unit belongs to; others do not")
        distinct([(r.object_id, r.revision_id) for r in self.evidence_refs], "constraint evidence")
        return self


class RecordConstraint(StrictModel):
    domain_id: CanonicalUUID
    payload: ConstraintPayload


class ReviseConstraint(StrictModel):
    payload: ConstraintPayload


class ConfirmConstraint(StrictModel):
    statement: NEStr


# ------------------------------------------------------------------- LTCO


class LTCOPayload(v4.LTCOPayload):
    realization_logic: NEStr
    key_assumptions: list[NEStr] = Field(default_factory=list)
    constraint_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_constraints(self) -> "LTCOPayload":
        distinct([(r.object_id, r.revision_id) for r in self.constraint_refs], "LTCO constraints")
        return self


class ProposeLTCO(v4.ProposeLTCO):
    payload: LTCOPayload


class ReviseLTCO(v4.ReviseLTCO):
    payload: LTCOPayload


class ConfirmLTCO(StrictModel):
    conclusion: Literal["established", "revised", "maintained"]
    statement: NEStr


# -------------------------------------------------------------------- PCO


class PCOPayload(v4.PCOPayload):
    period_review_ref: ExactRef | None = None
    boundary: NEStr
    constraint_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_constraints(self) -> "PCOPayload":
        distinct([(r.object_id, r.revision_id) for r in self.constraint_refs], "PCO constraints")
        return self


class DraftPCO(v4.DraftPCO):
    payload: PCOPayload


class RevisePCO(v4.RevisePCO):
    payload: PCOPayload


class CandidatePCO(v4.CandidatePCO):
    payload: PCOPayload


# ---------------------------------------------------------------- Mission


class MissionDependency(StrictModel):
    kind: Literal["mission", "scope"]
    mission_ref: ExactRef | None = None
    scope_id: NEStr | None = None
    needed_by: IsoDateTime
    note: NEStr

    @model_validator(mode="after")
    def exactly_one(self) -> "MissionDependency":
        if (self.kind == "mission") != (self.mission_ref is not None):
            raise ValueError("a Mission dependency names exactly one exact Mission")
        if (self.kind == "scope") != (self.scope_id is not None):
            raise ValueError("a Scope dependency names exactly one unit_id")
        return self


class _MissionExtras(StrictModel):
    boundary: NEStr
    contributes_to_scope_ids: list[NEStr] = Field(default_factory=list)
    dependencies: list[MissionDependency] = Field(default_factory=list)
    resource_needs: list[NEStr] = Field(default_factory=list)
    constraint_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_extras(self) -> "_MissionExtras":
        distinct(self.contributes_to_scope_ids, "Mission contributions")
        distinct([(r.object_id, r.revision_id) for r in self.constraint_refs], "Mission constraints")
        return self


class MissionPayload(v4.MissionPayload, _MissionExtras):
    pass


class DraftMission(v4.DraftMission):
    payload: MissionPayload


class ReviseMission(v4.ReviseMission):
    payload: MissionPayload


class CandidateMission(v4.CandidateMission, _MissionExtras):
    pass


class ResolveWindow(v4.ResolveWindow):
    pcos: list[CandidatePCO] = Field(min_length=1)
    missions: list[CandidateMission] = Field(min_length=1)


# ------------------------------------------------------ Operating State


class StatePayload(v4.StatePayload):
    period: Period
    drilldown_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def as_of_is_period_end(self) -> "StatePayload":
        if datetime.fromisoformat(self.as_of) != datetime.fromisoformat(self.period.end):
            raise ValueError("as_of must equal period.end")
        distinct([(r.object_id, r.revision_id) for r in self.drilldown_refs], "drill-down states")
        return self


class ProposeState(v4.ProposeState):
    payload: StatePayload


# ---------------------------------------------------------- Period Review


class ConfirmReview(StrictModel):
    statement: NEStr
    findings: list[NEStr] | None = Field(default=None, min_length=1)
    learnings: list[NEStr] | None = None
    implications: list[NEStr] | None = None


# --------------------------------------------------------------- tables


PAYLOAD_MODELS = {**v4.PAYLOAD_MODELS,
                  "Constraint": ConstraintPayload,
                  "LTCO": LTCOPayload,
                  "PCO": PCOPayload,
                  "Mission": MissionPayload,
                  "OperatingState": StatePayload}

V05_ONLY_ACTIONS = frozenset({"m1b_record_constraint", "m1b_revise_constraint",
                              "m1b_confirm_constraint", "m1b_confirm_review"})

ACTION_PARAMS = {**{k: v for k, v in v4.ACTION_PARAMS.items() if k != "method_confirm_state"},
                 "m1b_record_constraint": RecordConstraint,
                 "m1b_revise_constraint": ReviseConstraint,
                 "m1b_confirm_constraint": ConfirmConstraint,
                 "m1b_confirm_review": ConfirmReview,
                 "m1b_propose_ltco": ProposeLTCO,
                 "m1b_revise_ltco": ReviseLTCO,
                 "m1b_confirm_ltco": ConfirmLTCO,
                 "m1b_draft_pco": DraftPCO,
                 "m1b_revise_pco": RevisePCO,
                 "m1b_draft_mission": DraftMission,
                 "m1b_revise_mission": ReviseMission,
                 "m1b_resolve_window": ResolveWindow,
                 "method_propose_state": ProposeState}

ACTION_TARGETS = {**{k: v for k, v in v4.ACTION_TARGETS.items() if k != "method_confirm_state"},
                  "m1b_record_constraint": frozenset(),
                  "m1b_revise_constraint": frozenset({"Constraint"}),
                  "m1b_confirm_constraint": frozenset({"Constraint"}),
                  "m1b_confirm_review": frozenset({"PeriodReview"})}

OBJECT_TYPES = frozenset(PAYLOAD_MODELS) | {"EvidenceAsset"}

# Human actions offered to the governance workbench; Agent-only drafting stays out.
HUMAN_ACTIONS = (v4.HUMAN_ACTIONS - {"method_confirm_state"}) | V05_ONLY_ACTIONS
AGENT_ACTIONS = frozenset(set(ACTION_PARAMS) - HUMAN_ACTIONS - {
    "method_open_run", "method_attach_run", "method_pause_run", "method_resume_run",
    "method_record_attempt",
})
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

第 465 行 0.4 导入之后加：

```python
from .method_v05_models import ACTION_PARAMS as METHOD_V05_PARAMS, ACTION_TARGETS as METHOD_V05_TARGETS
```

第 467–470 行的两个 `Union` 各追加一项：`ActionType` 加 `Literal[tuple(METHOD_V05_PARAMS)]`，`ActionParams` 加 `Union[tuple(METHOD_V05_PARAMS.values())]`。

`select_params`（第 512 行）在 0.4 分支之前插入，并把原来的 `if ... == "tkos.method/0.4"` 改成 `elif`：

```python
            if data.get("contract_version") == "tkos.method/0.5" and action_type in METHOD_V05_PARAMS:
                data = dict(data)
                data["params"] = METHOD_V05_PARAMS[action_type].model_validate(data.get("params"))
            elif data.get("contract_version") == "tkos.method/0.4" and action_type in METHOD_V04_PARAMS:
```

`envelope_rules`（第 531 行）第一个分支改为：

```python
        if self.contract_version == "tkos.method/0.5":
            if self.action_type not in METHOD_V05_TARGETS:
                raise ValueError("action is not supported by Method 0.5")
            if bool(METHOD_V05_TARGETS[self.action_type]) != (self.target is not None):
                raise ValueError("Method action target does not match its typed contract")
        elif self.contract_version == "tkos.method/0.4" and self.action_type in METHOD_V04_TARGETS:
            if bool(METHOD_V04_TARGETS[self.action_type]) != (self.target is not None):
                raise ValueError("Method action target does not match its typed contract")
```

（0.5 分支先判断动作是否属于 0.5，这样 `method_confirm_state` 在 0.5 下直接 422，而不是掉进后面的旧版分支。）

第 557 行 run 关联那行改为：

```python
        if (self.action_type not in METHOD_V02_TARGETS
                and self.contract_version not in {"tkos.method/0.4", "tkos.method/0.5"}
                and (self.run_ref is not None or self.step_key is not None)):
```

- [ ] **Step 6: 生成 0.5 注册表 JSON**

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from memory_service_runtime.governed import method_v05_models as m
registry = {'can_read': True, 'can_create': True, 'can_write': True, 'evidence_upload': True,
            'actions': sorted(m.ACTION_PARAMS), 'object_types': sorted(m.OBJECT_TYPES),
            'readonly_compat': ['tkos.method/0.5'],
            'notes': 'Method 0.5 ontology v0.7 alignment: Constraint, LTCO review conclusion, CEO-confirmed Period Review, DRI-only commitment, canonical State without human confirmation; explicit per-scope registration required.'}
open('docs/runtime-method-registry-0.5.json', 'w').write(json.dumps(registry, ensure_ascii=False, indent=2) + '\n')
print(len(registry['actions']), len(registry['object_types']))
PY
```

Expected 输出：`44 16`。

- [ ] **Step 7: 运行确认通过，并跑 0.4 / 0.3 回归**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_models.py tests/test_method_v04_models.py tests/test_method_v03.py tests/test_method_v02.py -q
```

Expected: 全部 passed；0.4 / 0.3 / 0.2 用例数量与改动前一致。

- [ ] **Step 8: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05_models.py src/memory_service_runtime/governed/method_models.py src/memory_service_runtime/governed/protocol.py src/memory_service_runtime/governed/models.py docs/runtime-method-registry-0.5.json tests/test_method_v05_models.py
git commit -m "feat(method): add the tkos.method/0.5 strict models and request dispatch

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 3: 迁移 0029（Constraint 对象类型、0.5 绑定门禁、主 Scope 索引）

**Files:**
- Create: `src/memory_service_app/migrations/0029_method_v05.sql`
- Modify: `tests/test_migrations.py:51-57`（完整清单）
- Test: `tests/test_migrations.py`、`tests/test_method_v05_models.py`

**Interfaces:**
- Consumes: Task 1 的 `CONTRACT_SHA256`、`ONTOLOGY_REGISTRY_SHA256`。
- Produces: `gov_objects.ck_gov_object_type` 接受 `Constraint`；`gov_binding_insert_gate()` 接受 `tkos.method/0.5` 绑定并校验双重钉定；索引 `ix_gov_revisions_primary_scope` 供 Task 10 的公司视图使用。

- [ ] **Step 1: 更新清单测试并写 pin 测试**

`tests/test_migrations.py` 的 `expected` 列表在 `"0028_workspace_v02_grant_repair.sql",` 之后加一行 `"0029_method_v05.sql",`。在 `tests/test_method_v05_models.py` 末尾追加：

```python
def test_migration_pins_the_same_contract_and_registry_bytes():
    sql = (ROOT / 'src/memory_service_app/migrations/0029_method_v05.sql').read_text()
    assert profile.CONTRACT_SHA256 in sql and profile.ONTOLOGY_REGISTRY_SHA256 in sql
    assert "'tkos.method/0.5'" in sql and "'Constraint'" in sql
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_models.py::test_migration_pins_the_same_contract_and_registry_bytes -q
```

Expected: `FileNotFoundError`。

- [ ] **Step 3: 写迁移**

创建 `src/memory_service_app/migrations/0029_method_v05.sql`。对象类型约束照 `0025_method_anchors_v03.sql` 第 1–9 行的做法追加；门禁函数整体复制 `0028_method_v04_contract_repin.sql` 的 `gov_binding_insert_gate()`，只在 0.4 分支之后插入 0.5 分支（两个占位符换成 Task 1 Step 2 的值）：

```sql
-- 0029: tkos.method/0.5 ontology v0.7 alignment.
--
-- One new object_type (Constraint) and no new table: LTCO/PCO/Mission/
-- OperatingState only gain payload fields, Period Review gains a confirmation
-- action, commitments stay in gov_method_commitments.  The 0.5 binding gate
-- pins both the contract bytes and the ontology registry JSON bytes.  The
-- index serves the company-view reader; it changes no rule.
DO $$
DECLARE old_definition text;
BEGIN
 SELECT pg_get_constraintdef(oid) INTO old_definition FROM pg_constraint
 WHERE conrelid='gov_objects'::regclass AND conname='ck_gov_object_type';
 IF old_definition IS NULL THEN RAISE EXCEPTION 'expected object constraint'; END IF;
 ALTER TABLE gov_objects DROP CONSTRAINT ck_gov_object_type;
 EXECUTE 'ALTER TABLE gov_objects ADD CONSTRAINT ck_gov_object_type CHECK (' || substring(old_definition from 7) || ' OR object_type IN (''Constraint''))';
END $$;

CREATE INDEX IF NOT EXISTS ix_gov_revisions_primary_scope
    ON gov_object_revisions (scope_id, (payload->>'primary_scope_id'))
    WHERE payload ? 'primary_scope_id';

-- Preserve prior binding contracts; add the exact 0.5 ontology-alignment identity.
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
            AND prow.content->'action_contract_ref'->>'content_sha256' = '<CONTRACT_SHA256>'
            AND prow.content->'ontology_registry_ref'->>'registry_id' = 'tkos.ontology-registry'
            AND prow.content->'ontology_registry_ref'->>'revision' = '0.7.1'
            AND prow.content->'ontology_registry_ref'->>'content_sha256' = '<REGISTRY_SHA256>') IS NOT TRUE THEN
            RAISE EXCEPTION 'method 0.5 requires its exact ontology-alignment contract and registry' USING ERRCODE='23514';
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
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_models.py -q
```

Expected: `test_migrations.py` 1 passed（空库到 0029，重复执行应用 0 个）；模型测试全部通过。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_app/migrations/0029_method_v05.sql tests/test_migrations.py tests/test_method_v05_models.py
git commit -m "feat(runtime): add the Constraint object type and gate tkos.method/0.5 bindings (0029)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 4: 0.5 执行器骨架与全链路版本分发

**Files:**
- Create: `src/memory_service_runtime/governed/method_v05.py`
- Modify: `src/memory_service_runtime/governed/method_v04.py:104-110, 205-206, 246-263, 280-291, 905-915, 1019-1029`（`_LightExecution` 契约版本参数化，默认 0.4）
- Modify: `src/memory_service_runtime/governed/method_service.py:44-95, 236-244, 346-361, 380-392, 412-425`
- Modify: `src/memory_service_runtime/governed/method_readers.py:96, 284, 293, 297, 299, 323, 398, 428`
- Modify: `src/memory_service_runtime/governed/method_access.py:349, 371, 374`
- Modify: `src/memory_service_runtime/governed/workbench.py:278`
- Modify: `src/memory_service_runtime/governed/governance.py:146-148, 262-274, 296-302, 322-327, 371-376, 399-404, 436-441`
- Modify: `src/memory_service_runtime/governed/profile.py:296-299, 331-338`
- Modify: `src/memory_service_runtime/governed/control.py:113-122, 477-484`
- Modify: `src/memory_service_runtime/governed/protocol.py:577-580`
- Modify: `src/memory_service_runtime/governed/dashboard.py:419-424`
- Modify: `src/memory_service_runtime/governed/method_map.py:37-41, 104-106`
- Modify: `src/memory_service_runtime/governed/routes.py:38, 154, 189`
- Modify: `src/memory_service_app/governance_commands.py:30-34, 112-118`
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: `method_v04` 的 `collect`、`run`、`scoped_assignment`、`_state_subject_owner_static`、`_commitment_owner`、`_required_committers`、`activation_blockers`、`_LightExecution`。
- Produces: `method_v05.COLLECTORS`、`RUNNERS`（kind → 函数，Task 5–9 登记）、`collect(e)`、`run(e)`、`V05_SCOPED_ACTIONS`、`scoped_assignment(conn, ctx, kind, target, revision)`、`state_subject_owner_static(conn, ctx, subject)`、`constraint_assignment_static(conn, ctx, payload)`（Task 5 实现，本任务先占位抛 FORBIDDEN）、`required_committers`（本任务等于 0.4 的，Task 8 换成 0.5 规则）、`activation_blockers(conn, ctx, obj)`、`light(conn, ctx)`；`method_service.FORMAL_GOVERNANCE_VERSIONS`；`governance_commands.human_actions_for(version)`。

- [ ] **Step 1: 写失败的分发测试**

创建 `tests/test_method_v05_dispatch.py`：

```python
"""0.5 delegates unchanged actions to the frozen 0.4 executor; light reads follow the caller's version (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import (method_access, method_service, method_v04, method_v05,
                                             protocol, workbench)
from memory_service_runtime.governed.errors import GovernedError


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, head):
        self.head = head

    def execute(self, sql, params=None):
        return _Cursor(dict(self.head))


def _light_env(monkeypatch, binding_version):
    head = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'effective_revision_id': None, 'latest_revision_id': None}
    revision = {'object_id': head['object_id'], 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    monkeypatch.setattr(method_access, 'raw_revision', lambda conn, ctx, oid, rid: dict(revision))
    monkeypatch.setattr(protocol, 'current_binding',
                        lambda conn, scope_id, oid: {'contract_version': binding_version})
    return _Conn(head), SimpleNamespace(scope_id=str(uuid4()), principal_id=str(uuid4())), head, revision


def test_light_execution_follows_the_callers_contract_version(monkeypatch):
    conn, ctx, head, revision = _light_env(monkeypatch, 'tkos.method/0.5')
    reference = {'object_id': head['object_id'], 'revision_id': revision['revision_id'], 'payload_hash': 'a' * 64}
    with pytest.raises(GovernedError) as exc:  # default stays 0.4: a 0.5 binding is foreign to it
        method_v04._LightExecution(conn, ctx).ref(reference)
    assert exc.value.code == 'PROTOCOL_BINDING_CONFLICT'
    assert method_v05.light(conn, ctx).ref(reference)[0]['object_id'] == head['object_id']
    conn, ctx, head, revision = _light_env(monkeypatch, 'tkos.method/0.4')
    reference = {'object_id': head['object_id'], 'revision_id': revision['revision_id'], 'payload_hash': 'a' * 64}
    assert method_v04._LightExecution(conn, ctx).ref(reference)[0]['object_id'] == head['object_id']
    with pytest.raises(GovernedError):
        method_v05.light(conn, ctx).ref(reference)


def test_unchanged_actions_delegate_to_the_frozen_0_4_executor(monkeypatch):
    seen = []
    monkeypatch.setattr(method_v04, 'collect', lambda e: seen.append(('collect', e.kind)))
    monkeypatch.setattr(method_v04, 'run', lambda e: seen.append(('run', e.kind)) or {'ok': True})
    e = SimpleNamespace(kind='m1a_confirm_agreement', params={}, target_revision={'payload': {}})
    method_v05.collect(e)
    assert method_v05.run(e) == {'ok': True}
    assert seen == [('collect', 'm1a_confirm_agreement'), ('run', 'm1a_confirm_agreement')]


def test_registered_handlers_win_and_unknown_actions_are_refused(monkeypatch):
    seen = []
    monkeypatch.setitem(method_v05.COLLECTORS, 'probe_action', lambda e: seen.append('collect'))
    monkeypatch.setitem(method_v05.RUNNERS, 'probe_action', lambda e: seen.append('run') or {'done': True})
    monkeypatch.setattr(method_v05, 'ACTION_PARAMS', {**method_v05.ACTION_PARAMS, 'probe_action': object})
    e = SimpleNamespace(kind='probe_action', params={})
    assert method_v05.run(e) == {'done': True} and seen == ['collect', 'run']
    with pytest.raises(GovernedError) as exc:
        method_v05.collect(SimpleNamespace(kind='method_confirm_state', params={}))
    assert exc.value.code == 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'


def test_service_routes_0_5_to_the_0_5_executor():
    assert method_service.FORMAL_GOVERNANCE_VERSIONS == frozenset({'tkos.method/0.4', 'tkos.method/0.5'})
    assert workbench.object_types(None, None, 'tkos.method/0.5')['contract_version'] == 'tkos.method/0.5'
    assert any(item['object_type'] == 'Constraint' for item in workbench.object_types(None, None, 'tkos.method/0.5')['items'])
    assert ('tkos.method', 'tkos.method/0.5') in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    assert 'method_confirm_state' not in method_v05.V05_SCOPED_ACTIONS
    assert {'m1b_confirm_constraint', 'm1b_revise_constraint', 'm1b_commit_candidate'} <= method_v05.V05_SCOPED_ACTIONS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q
```

Expected: `ImportError: cannot import name 'method_v05'`。

- [ ] **Step 3: 参数化 `method_v04._LightExecution`（行为不变）**

`src/memory_service_runtime/governed/method_v04.py`，六处，默认值都是 `"tkos.method/0.4"`，0.4 调用方看到的行为完全不变：

1. 第 280–291 行 `class _LightExecution.__init__`：

```python
    def __init__(self, conn, ctx, contract_version="tkos.method/0.4"):
        self.conn, self.ctx = conn, ctx
        self.contract_version = contract_version
        self.domain_id = None
        self.required_assignments = set()
        self.heads = {}
        self.revisions = {}
```

`ref()` 与 `head()` 里两处 `binding["contract_version"] != "tkos.method/0.4"` 改为 `binding["contract_version"] != self.contract_version`。

2. 第 104–110 行 `_architecture_payload(e, reference)`：`_LightExecution(e.conn, e.ctx)` → `_LightExecution(e.conn, e.ctx, getattr(e, "contract_version", "tkos.method/0.4"))`。

3. 第 905–915 行 `_candidate_basis_current(e, payload, *, lock=False)`：`light = _LightExecution(e.conn, e.ctx)` → `light = _LightExecution(e.conn, e.ctx, getattr(e, "contract_version", "tkos.method/0.4"))`。

4. 第 246 行 `def _commitment_owner(conn, ctx, reference):` → `def _commitment_owner(conn, ctx, reference, contract_version="tkos.method/0.4"):`，函数内 `context = _LightExecution(conn, ctx)` → `context = _LightExecution(conn, ctx, contract_version)`。

5. 第 263 行 `def _state_subject_owner_static(conn, ctx, subject):` → `def _state_subject_owner_static(conn, ctx, subject, contract_version="tkos.method/0.4"):`，函数内同样把 `_LightExecution(conn, ctx)` 换成 `_LightExecution(conn, ctx, contract_version)`。

6. 第 205 行 `def scoped_assignment(conn, ctx, kind, target, revision):` → `def scoped_assignment(conn, ctx, kind, target, revision, contract_version="tkos.method/0.4"):`，其中对 `_commitment_owner(conn, ctx, reference)` 与 `_state_subject_owner_static(conn, ctx, subject)` 的调用各加 `, contract_version` 实参；第 1019 行 `def activation_blockers(conn, ctx, obj):` → `def activation_blockers(conn, ctx, obj, contract_version="tkos.method/0.4"):`，函数内 `light = _LightExecution(conn, ctx)` → `light = _LightExecution(conn, ctx, contract_version)`。

`_LightExecution` 是这些 0.4 责任解析的唯一版本闸口；参数化后 0.4 调用路径没有一处传入新值。

- [ ] **Step 4: 写 0.5 执行器骨架**

创建 `src/memory_service_runtime/governed/method_v05.py`：

```python
"""tkos.method/0.5：本体 v0.7 对齐增量执行器。

0.4 模块保持冻结；本模块只对 0.5 有差异的动作在 ``COLLECTORS`` / ``RUNNERS`` 登记
自己的处理器，其余动作原样委托 ``method_v04``。所有校验仍在同一治理事务内进行，
不产生执行副作用。Task 5–9 往两张登记表里加条目。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from . import db, method_access as access, method_v04 as v4
from .errors import GovernedError
from .method_v04 import _agent, _current_ceo, _exact, _human_ceo, _phase, _same, _scope_definition, _source_refs
from .method_v05_models import ACTION_PARAMS, HUMAN_ACTIONS

CONTRACT_VERSION = "tkos.method/0.5"
COLLECTORS: dict = {}   # kind -> collect(e)
RUNNERS: dict = {}      # kind -> run(e)；由 run() 先调用 collect(e) 再执行

# 0.4 的 scoped 动作去掉已不存在的 method_confirm_state，加上按范围确认 / 修订的 Constraint。
V05_SCOPED_ACTIONS = (v4.V04_SCOPED_ACTIONS - {"method_confirm_state"}) | {"m1b_confirm_constraint", "m1b_revise_constraint"}


def fail(message: str, code: str = "INVALID_STATE") -> None:
    raise GovernedError(code, message)


def light(conn, ctx):
    """0.5 对象的只读责任解析；只接受 0.5 绑定。"""
    return v4._LightExecution(conn, ctx, CONTRACT_VERSION)


# -------------------------------------------------------------- dispatch


def collect(e):
    if e.kind not in ACTION_PARAMS:
        fail("Unsupported Method 0.5 action.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    handler = COLLECTORS.get(e.kind)
    if handler is not None:
        e._v04 = {}
        return handler(e)
    return v4.collect(e)


def run(e):
    if e.kind not in ACTION_PARAMS:
        fail("Unsupported Method 0.5 action.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    runner = RUNNERS.get(e.kind)
    if runner is not None:
        collect(e)
        return runner(e)
    return v4.run(e)


# ------------------------------------------------------- scoped authority


required_committers = v4._required_committers   # Task 8 换成 0.5 规则（只有 PCO 的 DRI）


def constraint_assignment_static(conn, ctx, payload):
    """Constraint 范围责任人的只读解析（Task 5 实现）。"""
    raise GovernedError("FORBIDDEN")


def scoped_assignment(conn, ctx, kind, target, revision):
    """0.5 scoped 动作的本人责任；对 0.4 语义不变的动作委托 0.4 并带上 0.5 版本。"""
    payload = revision["payload"]
    if kind in {"m1b_confirm_constraint", "m1b_revise_constraint"}:
        return constraint_assignment_static(conn, ctx, payload)
    if kind == "m1b_commit_candidate":
        for reference in payload["target_refs"]:
            head = db.jsonable(conn.execute("SELECT object_type FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                            (ctx.scope_id, reference["object_id"])).fetchone())
            if head is None or head["object_type"] != "PCO":
                continue
            owner = v4._commitment_owner(conn, ctx, reference, CONTRACT_VERSION)
            if owner is None:
                continue
            for row in db._assignments(conn, ctx):
                if row["principal_id"] == owner:
                    try:
                        return access.assignment(conn, ctx, row["assignment_id"], owner, "human")
                    except GovernedError:
                        continue
        raise GovernedError("FORBIDDEN")
    return v4.scoped_assignment(conn, ctx, kind, target, revision, CONTRACT_VERSION)


def state_subject_owner_static(conn, ctx, subject):
    return v4._state_subject_owner_static(conn, ctx, subject, CONTRACT_VERSION)


def activation_blockers(conn, ctx, obj):
    return v4.activation_blockers(conn, ctx, obj, CONTRACT_VERSION)   # Task 8 换成 0.5 规则
```

- [ ] **Step 5: 接入 method_service**

`src/memory_service_runtime/governed/method_service.py`：

在 `class MethodExecution` 之前加常量：

```python
# 0.4 与 0.5 共用同一套正式治理机制（全体确认、整组激活、scoped 授权）；0.5 只改本体对齐的差异。
FORMAL_GOVERNANCE_VERSIONS = frozenset({"tkos.method/0.4", "tkos.method/0.5"})
```

逐处替换：

1. 第 48 行与第 419 行：`self.contract_version == "tkos.method/0.4"` → `self.contract_version in FORMAL_GOVERNANCE_VERSIONS`。
2. 第 52 行：`self.contract_version != "tkos.method/0.4"` → `self.contract_version not in FORMAL_GOVERNANCE_VERSIONS`。
3. 第 67–75 行 scoped 回退改为：

```python
                    if self.contract_version in FORMAL_GOVERNANCE_VERSIONS:
                        if self.contract_version == "tkos.method/0.5":
                            from . import method_v05
                            if self.kind not in method_v05.V05_SCOPED_ACTIONS:
                                raise
                            member = method_v05.scoped_assignment(self.conn, self.ctx, self.kind,
                                                                  self.target, self.target_revision)
                        else:
                            from .method_v04 import V04_SCOPED_ACTIONS
                            if self.kind not in V04_SCOPED_ACTIONS:
                                raise
                            from . import method_v04
                            member = method_v04.scoped_assignment(self.conn, self.ctx, self.kind,
                                                                  self.target, self.target_revision)
                        self.method_scoped_domain = member["domain_id"]
                        self.action_assignments = db.authorize_domain(self.conn, self.ctx, member["domain_id"], self.kind)
```

4. 第 89–92 行无目标动作的 scoped 回退改为：

```python
                if self.contract_version in FORMAL_GOVERNANCE_VERSIONS and self.kind == "method_propose_state":
                    member = access.state_subject_assignment(self.conn, self.ctx, self.params['payload']['subject_ref'])
                    self.method_scoped_domain = member['domain_id']
                    self.action_assignments = db.authorize_domain(self.conn, self.ctx, member['domain_id'], self.kind)
                elif self.contract_version == "tkos.method/0.5" and self.kind == "m1b_record_constraint":
                    from . import method_v05
                    member = method_v05.constraint_assignment_static(self.conn, self.ctx, self.params['payload'])
                    self.method_scoped_domain = member['domain_id']
                    self.action_assignments = db.authorize_domain(self.conn, self.ctx, member['domain_id'], self.kind)
```

5. 第 93 行起的 `create_type` 字典加一项 `"m1b_record_constraint": "Constraint",`。
6. 第 240 行 `create()` 的 scoped 条件改为：

```python
            (self.contract_version in {"tkos.method/0.3", *FORMAL_GOVERNANCE_VERSIONS}
             and self.kind in {"method_propose_state", "method_confirm_state", "m1b_record_constraint"}))
```

7. `collect_dependencies()` 最前面加，并把原来的 `if self.contract_version == "tkos.method/0.4":` 改成 `elif`：

```python
        if self.contract_version == "tkos.method/0.5":
            from . import method_v05
            method_v05.collect(self)
```

8. `run_action()` 在 0.4 分支前加：

```python
        if self.contract_version == "tkos.method/0.5":
            from . import method_v05
            return method_v05.run(self)
```

- [ ] **Step 6: 接入其余版本判断点**

每处都是把「等于 0.4」扩为「0.4 或 0.5」，或把集合加入 0.5：

- `method_readers.py:96`：`== "tkos.method/0.4"` → `in {"tkos.method/0.4", "tkos.method/0.5"}`；第 284、293、323、428 行的集合加入 `"tkos.method/0.5"`；第 297、398 行的 `{"tkos.method/0.3", "tkos.method/0.4"}` 加入 0.5；第 299 行 `== "tkos.method/0.4"` → `in {"tkos.method/0.4", "tkos.method/0.5"}`。
- `method_access.py:349`：`binding['contract_version'] == 'tkos.method/0.4'` → `in {'tkos.method/0.4', 'tkos.method/0.5'}`；第 371 行集合加入 0.5；第 374 行 `=='tkos.method/0.4'` → `in {'tkos.method/0.4','tkos.method/0.5'}`。
- `workbench.py:278`：集合加入 `"tkos.method/0.5"`。
- `governance.py`：
  - 第 146–148 行：`method_v04._required_committers(method_v04._LightExecution(conn, ctx), payload)` 改为按版本：

```python
        if obj['protocol']['contract_version'] == 'tkos.method/0.5':
            from . import method_v05
            required = method_v05.required_committers(method_v05.light(conn, ctx), payload)
        else:
            from . import method_v04
            required = method_v04._required_committers(method_v04._LightExecution(conn, ctx), payload)
```

  - 第 262–274 行 `_availability` 里三处 `from . import method_v04` 调用改成按版本选择模块：在函数开头加 `executor = _executor(obj)`，然后 `method_v04.scoped_assignment(...)` → `executor.scoped_assignment(...)`；`method_v04._state_subject_owner_static(...)` → `executor.state_subject_owner_static(...)`；`method_v04.activation_blockers(...)` → `executor.activation_blockers(...)`。在模块里加：

```python
def _executor(obj):
    """0.5 对象用 0.5 执行器的只读投影；其余沿用 0.4。"""
    if obj['protocol']['contract_version'] == 'tkos.method/0.5':
        from . import method_v05
        return method_v05
    from . import method_v04
    return _V04Facade(method_v04)


class _V04Facade:
    def __init__(self, module):
        self.module = module

    def scoped_assignment(self, conn, ctx, action, obj, revision):
        return self.module.scoped_assignment(conn, ctx, action, obj, revision)

    def state_subject_owner_static(self, conn, ctx, subject):
        return self.module._state_subject_owner_static(conn, ctx, subject)

    def activation_blockers(self, conn, ctx, obj):
        return self.module.activation_blockers(conn, ctx, obj)
```

  - 第 296–302 行 `method_object_actions`：用版本注册表取动作表：

```python
def method_object_actions(conn, ctx, obj):
    """Allowlisted human actions for one 0.4 / 0.5 object with current availability."""
    version = obj['protocol']['contract_version']
    if version not in {'tkos.method/0.4', 'tkos.method/0.5'}:
        return {'items': []}
    from .method_models import registry
    _params, targets, _payloads = registry(version)
    human = human_actions_for(version)
    ceo = ctx.principal_type == 'human' and _caller_is_ceo(ctx, obj['domain_id'])
    offered = [action for action in sorted(human)
               if obj['object_type'] in targets.get(action, frozenset())]
```

（原来 `from .method_v04_models import ACTION_TARGETS, HUMAN_ACTIONS` 那行删掉；`human_actions_for` 在同文件新增：）

```python
def human_actions_for(version):
    if version == 'tkos.method/0.5':
        from .method_v05_models import HUMAN_ACTIONS as v05
        return v05
    from .method_v04_models import HUMAN_ACTIONS as v04
    return v04
```

  - 第 322–327、371–376、399–404、436–441 行：`== 'tkos.method/0.4'` → `in {'tkos.method/0.4', 'tkos.method/0.5'}`，`!= 'tkos.method/0.4'` → `not in {...}`。
  - `HUMAN_ACTION_LABELS` 加：`'m1b_confirm_review': '确认周期复盘（CEO）'`、`'m1b_confirm_constraint': '确认经营约束（按范围）'`、`'m1b_record_constraint': '登记经营约束'`、`'m1b_revise_constraint': '修订经营约束'`。`FORMAL_EFFECT` 加：`'m1b_confirm_review': 'confirmed_period_review'`、`'m1b_confirm_constraint': 'effective_constraint'`、`'m1b_record_constraint': 'constraint_draft'`、`'m1b_revise_constraint': 'constraint_draft'`。`CEO_ONLY_ACTIONS` 加 `'m1b_confirm_review'`。`SCOPED_HUMAN_ACTIONS` 加 `'m1b_confirm_constraint'`、`'m1b_revise_constraint'`。`PHASE_RULES` 加：`'m1b_confirm_review': {'generated'}`、`'m1b_confirm_constraint': {'draft'}`、`'m1b_revise_constraint': {'draft', 'confirmed'}`；`'m1b_confirm_ltco'` 的集合在原有 `'draft'` 上加 `'confirmed'`（0.5 的维持结论；0.4 的核心校验仍只放行草稿）。
- `profile.py:296`：在 0.4 分支前加：

```python
    if isinstance(data, dict) and data.get("profile_core_schema_version") == "tkos.method-profile/0.5":
        from .method_v05_profile import validate
        return validate(data)
```

`implied_protocol()` 第 331 行前加同样式的 0.5 分支（`method_v05_profile.validate(content)`，返回 `(method_v05_profile.PROTOCOL_ID, method_v05_profile.CONTRACT_VERSION)`）。
- `control.py:113-122`：导入加 `method_v05_profile`，`pinned_sha` 表达式最前面加 `method_v05_profile.CONTRACT_SHA256 if core.profile_core_schema_version == method_v05_profile.SCHEMA_VERSION else ...`；紧接着加本体登记校验：

```python
    if core.profile_core_schema_version == method_v05_profile.SCHEMA_VERSION:
        if not args.ontology_registry_file:
            _fail("PROFILE_CONTENT_CONFLICT",
                  "Method 0.5 profiles require --ontology-registry-file to verify ontology_registry_ref.")
        registry_sha = hashlib.sha256(Path(args.ontology_registry_file).read_bytes()).hexdigest()
        if registry_sha != core.ontology_registry_ref.content_sha256 or registry_sha != method_v05_profile.ONTOLOGY_REGISTRY_SHA256:
            _fail("PROFILE_CONTENT_CONFLICT",
                  "ontology_registry_ref.content_sha256 does not match the supplied registry bytes or the compiled pin.")
```

第 477–484 行 `install-profile` 子命令加参数：

```python
    p.add_argument("--ontology-registry-file", default=None,
                   help="Path to the exact ontology registry JSON bytes named by ontology_registry_ref (Method 0.5).")
```

- `protocol.py:577`：在 0.4 分支前加：

```python
        if binding["contract_version"] == "tkos.method/0.5":
            return "method_v0_5", ("Method 0.5; 0.4 rules plus Constraint, LTCO review conclusion, "
                                    "CEO-confirmed Period Review, DRI-only commitment and canonical State.")
```

- `dashboard.py:MISSION_PARENT_REF_FIELDS` 加 `"tkos.method/0.5": "parent_pco_ref",`。
- `method_map.py`：`DOCUMENTED_CONTRACTS = ("tkos.method/0.5", "tkos.method/0.4", "tkos.workspace/0.2")`；`documented_contract_state()` 第 104 行 `if version == METHOD_V04:` → `if version.startswith("tkos.method/"):`。
- `routes.py` 第 38、154、189 行的 `Literal[...]` 各加 `"tkos.method/0.5"`。
- `src/memory_service_app/governance_commands.py`：第 30–34 行的 `v04_human_actions()` 之后加：

```python
def human_actions_for(version):
    if version == 'tkos.method/0.5':
        from memory_service_runtime.governed.method_v05_models import HUMAN_ACTIONS as v05
        return v05
    return v04_human_actions()
```

第 115–116 行改为：

```python
    elif version in {'tkos.method/0.4', 'tkos.method/0.5'}:
        if action not in human_actions_for(version):
            raise GovernedError('FORBIDDEN')
```

- [ ] **Step 7: 运行新测试与全部 Method 回归**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py tests/test_method_v05_models.py -q
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v04_models.py tests/test_method_v03.py tests/test_method_v02.py tests/test_method_m1a.py tests/test_method_m1b_workflow.py tests/test_method_map.py tests/governance tests/test_workspace_v02.py -q
PYTHONPATH=src .venv/bin/python -m pytest tests/dashboard --confcutdir=tests/dashboard -q
```

Expected: 全部 passed，0.4 及更早的用例数量不变。

- [ ] **Step 8: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py src/memory_service_runtime/governed/method_v04.py src/memory_service_runtime/governed/method_service.py src/memory_service_runtime/governed/method_readers.py src/memory_service_runtime/governed/method_access.py src/memory_service_runtime/governed/workbench.py src/memory_service_runtime/governed/governance.py src/memory_service_runtime/governed/profile.py src/memory_service_runtime/governed/control.py src/memory_service_runtime/governed/protocol.py src/memory_service_runtime/governed/dashboard.py src/memory_service_runtime/governed/method_map.py src/memory_service_runtime/governed/routes.py src/memory_service_app/governance_commands.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): route tkos.method/0.5 through a delegating executor with version-aware light reads

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 5: Constraint：登记、修订与按范围确认

**Files:**
- Modify: `src/memory_service_runtime/governed/method_v05.py`（新增 Constraint 段，登记三个动作）
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: Task 2 的 `ConstraintPayload`；Task 4 的 `COLLECTORS` / `RUNNERS` / `light`。
- Produces: `_constraint_responsibility(e, payload) -> (principal_id, assignment)`、`_check_constraint_payload(e, payload)`、`_check_constraint_refs(e, refs, *, scope_id, mission_ref=None)`（Task 6–8 用）、`constraint_assignment_static(conn, ctx, payload)`（替换 Task 4 的占位）；确认记录 kind `constraint_confirmation`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_method_v05_dispatch.py` 末尾追加（用 SimpleNamespace 桩住 `e.ref` / `e.state`，只测纯规则）：

```python
def _fake_execution(*, refs, states=None, ctx_type='human'):
    states = states or {}

    def ref(reference, types=None, effective=False, current=True):
        head, revision = refs[reference['object_id']]
        if types and head['object_type'] not in types:
            raise GovernedError('NOT_FOUND')
        return head, revision

    return SimpleNamespace(ref=ref, state=lambda head: states.get(head['object_id'], {}),
                           ctx=SimpleNamespace(principal_type=ctx_type, principal_id=str(uuid4()), assignments=[]),
                           params={}, _v04={})


def _constraint(kind, scope_id=None, phase='confirmed'):
    oid = str(uuid4())
    applies = {'kind': kind}
    if kind == 'scope':
        applies['scope_id'] = scope_id
    if kind == 'mission':
        applies['mission_ref'] = ref()
    head = {'object_id': oid, 'object_type': 'Constraint'}
    revision = {'object_id': oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                'payload': {'applies_to': applies}}
    reference = {'object_id': oid, 'revision_id': revision['revision_id'], 'payload_hash': 'a' * 64}
    return oid, head, revision, reference, phase


def test_constraint_refs_must_be_confirmed_and_applicable():
    company = _constraint('company')
    own_scope = _constraint('scope', 'bf-1')
    other_scope = _constraint('scope', 'bf-2')
    draft = _constraint('company', phase='draft')
    refs = {c[0]: (c[1], c[2]) for c in (company, own_scope, other_scope, draft)}
    states = {c[0]: {'phase': c[4]} for c in (company, own_scope, other_scope, draft)}
    e = _fake_execution(refs=refs, states=states)
    method_v05._check_constraint_refs(e, [company[3], own_scope[3]], scope_id='bf-1')
    with pytest.raises(GovernedError) as exc:
        method_v05._check_constraint_refs(e, [other_scope[3]], scope_id='bf-1')
    assert exc.value.code == 'INVALID_REQUEST'
    with pytest.raises(GovernedError) as exc:
        method_v05._check_constraint_refs(e, [draft[3]], scope_id='bf-1')
    assert exc.value.code == 'STALE_DEPENDENCY'
    mission = _constraint('mission')
    refs[mission[0]] = (mission[1], mission[2])
    states[mission[0]] = {'phase': 'confirmed'}
    own = mission[2]['payload']['applies_to']['mission_ref']
    method_v05._check_constraint_refs(e, [mission[3]], scope_id='bf-1', mission_ref=own)
    with pytest.raises(GovernedError):  # a Mission constraint never applies to another Mission
        method_v05._check_constraint_refs(e, [mission[3]], scope_id='bf-1', mission_ref=ref())


def test_constraint_actions_are_registered_as_0_5_handlers():
    for kind in ('m1b_record_constraint', 'm1b_revise_constraint', 'm1b_confirm_constraint'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q -k constraint
```

Expected: `AttributeError: module ... has no attribute '_check_constraint_refs'`。

- [ ] **Step 3: 写 Constraint 段**

在 `method_v05.py` 的 `# --- scoped authority` 段之前插入，并把 Task 4 的 `constraint_assignment_static` 占位替换为下面的实现：

```python
# ------------------------------------------------------------- Constraint


def _constraint_responsibility(e, payload):
    """确认人：company → 当前 CEO；scope → 该 Scope 授权域的唯一当前 DRI；mission → 其主 Scope 的 DRI。"""
    applies = payload["applies_to"]
    if applies["kind"] == "company":
        return _current_ceo(e, e.domain_id if getattr(e, "domain_id", None) else e.target["domain_id"])
    if applies["kind"] == "scope":
        principal, _role, _auth_domain, assignment = v4._pco_responsibility(
            e, {"architecture_ref": payload["architecture_ref"], "primary_scope_id": applies["scope_id"]})
        return principal, assignment
    _mission_head, mission_revision = e.ref(applies["mission_ref"], types={"Mission"}, current=False)
    _pco_head, pco_revision = e.ref(mission_revision["payload"]["parent_pco_ref"], types={"PCO"}, current=False)
    return v4._pco_dri(e, pco_revision["payload"])


def _check_constraint_payload(e, payload):
    applies = payload["applies_to"]
    if applies["kind"] == "scope":
        _head, revision = e.ref(payload["architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=False)
        _scope_definition(e, revision["payload"], applies["scope_id"])
    _source_refs(e, payload["evidence_refs"])


def _check_constraint_refs(e, refs, *, scope_id, mission_ref=None):
    """LTCO / PCO / Mission 只能引用已确认、且适用于公司或本对象主 Scope（或本 Mission）的 Constraint。"""
    for reference in refs:
        head, revision = e.ref(reference, types={"Constraint"}, effective=True, current=False)
        if e.state(head).get("phase") != "confirmed":
            fail("Only confirmed Constraints can be referenced.", "STALE_DEPENDENCY")
        applies = revision["payload"]["applies_to"]
        if applies["kind"] == "company":
            continue
        if applies["kind"] == "scope" and applies["scope_id"] == scope_id:
            continue
        if (applies["kind"] == "mission" and mission_ref is not None
                and applies["mission_ref"]["object_id"] == mission_ref["object_id"]):
            continue
        fail("A referenced Constraint must apply to the company or to this object's own Scope.", "INVALID_REQUEST")


def _constraint_actor(e, payload):
    owner, assignment = _constraint_responsibility(e, payload)
    if e.ctx.principal_type == "human":
        e.require_actor(owner, "human")
    else:
        _agent(e, "CO_AGENT")
    e._v04.update(constraint_owner=owner, constraint_assignment=assignment)


def _collect_record_constraint(e):
    payload = e.params["payload"]
    _check_constraint_payload(e, payload)
    _constraint_actor(e, payload)


def _collect_revise_constraint(e):
    _phase(e.state(e.target), "draft", "confirmed")
    payload = e.params["payload"]
    if payload["applies_to"] != e.target_revision["payload"]["applies_to"]:
        fail("A revision keeps the constraint's applicability; record a new Constraint instead.", "INVALID_REQUEST")
    _check_constraint_payload(e, payload)
    _constraint_actor(e, payload)


def _collect_confirm_constraint(e):
    _phase(e.state(e.target), "draft")
    payload = e.target_revision["payload"]
    _check_constraint_payload(e, payload)
    owner, assignment = _constraint_responsibility(e, payload)
    e.require_actor(owner, "human")
    e._v04.update(constraint_owner=owner, constraint_assignment=assignment)


def _run_record_constraint(e):
    head, revision = e.create("Constraint", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_constraint(e):
    head, revision = e.revise(e.target, e.params["payload"])
    state = deepcopy(e.state(head))
    state.update(phase="draft")
    e.set_state(head, state)
    return {**_exact(head, revision), "phase": "draft"}


def _run_confirm_constraint(e):
    record = e.review("constraint_confirmation", _exact(e.target, e.target_revision),
                      {"statement": e.params["statement"], "principal_id": e._v04["constraint_owner"],
                       "assignment_id": e._v04["constraint_assignment"]["assignment_id"]})
    state = deepcopy(e.state(e.target))
    state.update(phase="confirmed", confirmation_record_id=record)
    e.set_state(e.target, state)
    e.transition(e.target, status="confirmed", effective=True)
    return {**_exact(e.target, e.target_revision), "phase": "confirmed", "review_record_id": record}


COLLECTORS.update({"m1b_record_constraint": _collect_record_constraint,
                   "m1b_revise_constraint": _collect_revise_constraint,
                   "m1b_confirm_constraint": _collect_confirm_constraint})
RUNNERS.update({"m1b_record_constraint": _run_record_constraint,
                "m1b_revise_constraint": _run_revise_constraint,
                "m1b_confirm_constraint": _run_confirm_constraint})


def constraint_assignment_static(conn, ctx, payload):
    """只读解析 Constraint 范围责任人，供 scoped 授权回退与工作台可用性投影使用。"""
    context = light(conn, ctx)
    applies = payload["applies_to"]
    if applies["kind"] == "company":
        rows = conn.execute(
            "SELECT assignment_id, principal_id, domain_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND role='CEO'",
            (ctx.scope_id, ctx.principal_id)).fetchall()
        for row in db.jsonable(rows):
            try:
                return access.assignment(conn, ctx, str(row["assignment_id"]), ctx.principal_id, "human")
            except GovernedError:
                continue
        raise GovernedError("FORBIDDEN")
    if applies["kind"] == "scope":
        basis = {"architecture_ref": payload["architecture_ref"], "primary_scope_id": applies["scope_id"]}
    else:
        _head, mission = context.ref(applies["mission_ref"], types={"Mission"})
        _pco_head, pco = context.ref(mission["payload"]["parent_pco_ref"], types={"PCO"})
        basis = {"architecture_ref": pco["payload"]["architecture_ref"], "primary_scope_id": pco["payload"]["primary_scope_id"]}
    principal, _role, _auth_domain, assignment = v4._pco_responsibility(context, basis)
    if principal != ctx.principal_id or ctx.principal_type != "human":
        raise GovernedError("FORBIDDEN")
    return assignment
```

（`_LightExecution` 没有 `domain_id` 之外的执行属性，`_pco_responsibility` 只用到 `conn`、`ctx`、`validate_principal`，可以直接传。）

- [ ] **Step 4: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q
```

Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): record, revise and confirm Constraints by applicability scope in 0.5

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 6: LTCO：审视结论与约束引用

**Files:**
- Modify: `src/memory_service_runtime/governed/method_v05.py`（新增 LTCO 段）
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: Task 5 的 `_check_constraint_refs`；`v4._ltco_check`。
- Produces: `_collect_ltco_draft`、`_collect_confirm_ltco`、`_run_confirm_ltco`；`state.last_review = {conclusion, record_id}`；`ltco_confirmation` 记录含 `conclusion`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_method_v05_dispatch.py` 末尾追加：

```python
def _ltco_target(phase, effective):
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'LTCO', 'domain_id': str(uuid4()),
            'effective_revision_id': rid if effective else None, 'latest_revision_id': rid}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64,
                'payload': {'primary_scope_id': 'bf-1', 'constraint_refs': []}}
    return head, revision, {oid: {'phase': phase}}


def test_ltco_conclusion_must_match_the_objects_history(monkeypatch):
    monkeypatch.setattr(method_v04, '_ltco_check', lambda e, payload: None)
    monkeypatch.setattr(method_v05, '_current_ceo', lambda e, domain_id: (e.ctx.principal_id, {'assignment_id': 'a'}))
    for phase, effective, conclusion, ok in [('draft', False, 'established', True), ('draft', False, 'revised', False),
                                             ('draft', True, 'revised', True), ('draft', True, 'established', False),
                                             ('draft', True, 'maintained', False), ('confirmed', True, 'maintained', True),
                                             ('confirmed', True, 'revised', False)]:
        head, revision, states = _ltco_target(phase, effective)
        e = _fake_execution(refs={}, states=states)
        e.target, e.target_revision = head, revision
        e.params = {'conclusion': conclusion, 'statement': 'reason'}
        e.require_actor = lambda principal, kind: None
        if ok:
            method_v05._collect_confirm_ltco(e)
        else:
            with pytest.raises(GovernedError) as exc:
                method_v05._collect_confirm_ltco(e)
            assert exc.value.code == 'INVALID_REQUEST', (phase, effective, conclusion)
    for kind in ('m1b_propose_ltco', 'm1b_revise_ltco', 'm1b_confirm_ltco'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q -k ltco
```

Expected: `AttributeError: ... '_collect_confirm_ltco'`。

- [ ] **Step 3: 写 LTCO 段**

在 `method_v05.py` 的 Constraint 段之后加：

```python
# ------------------------------------------------------------------- LTCO


def _collect_ltco_draft(e):
    v4.collect(e)
    payload = e.params["payload"]
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])


def _collect_confirm_ltco(e):
    state = e.state(e.target)
    _phase(state, "draft", "confirmed")
    payload = e.target_revision["payload"]
    v4._ltco_check(e, payload)
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])
    owner, _assignment = _current_ceo(e, e.target["domain_id"])
    e.require_actor(owner, "human")
    conclusion = e.params["conclusion"]
    has_formal = e.target.get("effective_revision_id") is not None
    if state.get("phase") == "confirmed":
        if conclusion != "maintained" or not has_formal:
            fail("A confirmed LTCO can only be maintained; revise it for a new version.", "INVALID_REQUEST")
        if str(e.target["effective_revision_id"]) != str(e.target_revision["revision_id"]):
            fail("Maintain the exact effective LTCO version.", "STALE_DEPENDENCY")
        return
    expected = "revised" if has_formal else "established"
    if conclusion != expected:
        fail(f"This confirmation must conclude '{expected}'.", "INVALID_REQUEST")


def _run_propose_ltco(e):
    head, revision = e.create("LTCO", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_ltco(e):
    head, revision = e.revise(e.target, e.params["payload"])
    state = deepcopy(e.state(head))
    state.update(phase="draft")
    e.set_state(head, state)
    record = e.review("ltco_revision_response", _exact(head, revision), {"response": e.params["response"]})
    return {**_exact(head, revision), "phase": "draft", "review_record_id": record}


def _run_confirm_ltco(e):
    conclusion = e.params["conclusion"]
    record = e.review("ltco_confirmation", _exact(e.target, e.target_revision),
                      {"conclusion": conclusion, "statement": e.params["statement"]})
    state = deepcopy(e.state(e.target))
    state.update(phase="confirmed", confirmation_record_id=record,
                 last_review={"conclusion": conclusion, "record_id": record})
    e.set_state(e.target, state)
    if conclusion == "maintained":
        e.transition(e.target)   # 只推进 CAS；正式版本不变
    else:
        e.transition(e.target, status="confirmed", effective=True)
    return {**_exact(e.target, e.target_revision), "phase": "confirmed", "conclusion": conclusion,
            "review_record_id": record}


COLLECTORS.update({"m1b_propose_ltco": _collect_ltco_draft, "m1b_revise_ltco": _collect_ltco_draft,
                   "m1b_confirm_ltco": _collect_confirm_ltco})
RUNNERS.update({"m1b_propose_ltco": _run_propose_ltco, "m1b_revise_ltco": _run_revise_ltco,
                "m1b_confirm_ltco": _run_confirm_ltco})
```

（`_run_revise_ltco` 保留 0.4 的 `ltco_revision_response` 记录，因为 `ReviseLTCO.response` 沿用；`v4.collect` 在 revise 时已检查 `draft` 阶段。）

- [ ] **Step 4: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q
```

Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): record the LTCO review conclusion and constraint references in 0.5

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 7: Period Review 确认门与 PCO 承接

**Files:**
- Modify: `src/memory_service_runtime/governed/method_v05.py`（新增 Period Review 与 PCO 段）
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: `v4._human_ceo`、`v4._canonical_state`、`v4._resolve_window`、Task 5 的 `_check_constraint_refs`。
- Produces: `_check_period_review_ref(e, payload)`（Task 8 的候选校验也用）、`_collect_confirm_review`、`_run_confirm_review`、`_collect_pco_draft`、`_collect_resolve_window`（Task 8 追加 Mission 校验）；记录 kind `review_confirmation`；`state.agent_generation_ref`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_method_v05_dispatch.py` 末尾追加：

```python
def _review(phase, end, effective=True):
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'PeriodReview', 'effective_revision_id': rid if effective else None}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64,
                'payload': {'period': {'start': '2026-09-01T00:00:00Z', 'end': end}}}
    return oid, head, revision, {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}, phase


class _RowsConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        rows = self.rows
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)


def test_pco_must_cite_a_confirmed_earlier_period_review_when_one_exists():
    confirmed = _review('confirmed', '2026-09-30T00:00:00Z')
    generated = _review('generated', '2026-09-30T00:00:00Z', effective=False)
    late = _review('confirmed', '2026-10-15T00:00:00Z')
    refs = {r[0]: (r[1], r[2]) for r in (confirmed, generated, late)}
    states = {r[0]: {'phase': r[4]} for r in (confirmed, generated, late)}
    payload = {'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}}
    e = _fake_execution(refs=refs, states=states)
    e.conn = _RowsConn([])
    method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': confirmed[3]})
    with pytest.raises(GovernedError) as exc:
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': generated[3]})
    assert exc.value.code == 'STALE_DEPENDENCY'
    with pytest.raises(GovernedError) as exc:
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': late[3]})
    assert exc.value.code == 'INVALID_REQUEST'
    method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': None})   # first period
    e.conn = _RowsConn([{'payload': confirmed[2]['payload']}])
    with pytest.raises(GovernedError) as exc:  # a confirmed earlier review exists and must be cited
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': None})
    assert exc.value.code == 'INVALID_REQUEST'
    for kind in ('m1b_confirm_review', 'm1b_regenerate_review', 'm1b_draft_pco', 'm1b_revise_pco', 'm1b_resolve_window'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q -k period_review
```

Expected: `AttributeError: ... '_check_period_review_ref'`。

- [ ] **Step 3: 写 Period Review 与 PCO 段**

在 `method_v05.py` 的 LTCO 段之后加：

```python
# ---------------------------------------------------------- Period Review


def _collect_confirm_review(e):
    _human_ceo(e)
    _phase(e.state(e.target), "generated")
    for reference in e.target_revision["payload"]["state_refs"]:
        v4._canonical_state(e, reference)


def _run_confirm_review(e):
    head, revision = e.target, e.target_revision
    generated = _exact(head, revision)
    overrides = {key: e.params[key] for key in ("findings", "learnings", "implications") if e.params.get(key) is not None}
    if overrides:
        head, revision = e.revise(head, {**revision["payload"], **overrides})
    record = e.review("review_confirmation", _exact(head, revision),
                      {"statement": e.params["statement"], "overrides": sorted(overrides)})
    e.transition(head, status="confirmed", effective=True)
    e.set_state(head, {"phase": "confirmed", "confirmation_record_id": record, "agent_generation_ref": generated})
    return {**_exact(head, revision), "phase": "confirmed", "review_record_id": record}


def _run_regenerate_review(e):
    # 0.5：重新生成不再使复盘生效，生效只来自 CEO 确认。
    head, revision = e.revise(e.target, e.params["payload"], status="recorded")
    e.set_state(head, {"phase": "generated"})
    return {**_exact(head, revision), "nature": "agent_analysis", "phase": "generated"}


# -------------------------------------------------------------------- PCO


def _check_period_review_ref(e, payload):
    start = datetime.fromisoformat(payload["period"]["start"])
    reference = payload.get("period_review_ref")
    if reference is not None:
        head, revision = e.ref(reference, types={"PeriodReview"}, effective=True, current=False)
        if e.state(head).get("phase") != "confirmed":
            fail("PCO must cite a CEO-confirmed Period Review.", "STALE_DEPENDENCY")
        if datetime.fromisoformat(revision["payload"]["period"]["end"]) > start:
            fail("The cited Period Review must precede the PCO period.", "INVALID_REQUEST")
        return
    rows = e.conn.execute(
        """SELECT r.payload FROM gov_objects o JOIN gov_object_revisions r
             ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
           WHERE o.scope_id=%s AND o.object_type='PeriodReview' AND o.effective_revision_id IS NOT NULL""",
        (e.ctx.scope_id,)).fetchall()
    for row in db.jsonable(rows):
        if datetime.fromisoformat(row["payload"]["period"]["end"]) <= start:
            fail("A confirmed Period Review exists for an earlier period; the PCO must cite it.", "INVALID_REQUEST")


def _check_pco_extras(e, payload):
    _check_period_review_ref(e, payload)
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])


def _collect_pco_draft(e):
    v4.collect(e)
    _check_pco_extras(e, e.params["payload"])


def _collect_resolve_window(e):
    v4.collect(e)
    for candidate in e.params["pcos"]:
        _check_pco_extras(e, candidate["payload"])
    # Task 8 在这里追加候选 Mission 的贡献 / 依赖 / 约束校验。


def _run_draft_pco(e):
    head, revision = e.create("PCO", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_pco(e):
    head, revision = e.revise(e.target, e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


COLLECTORS.update({"m1b_confirm_review": _collect_confirm_review,
                   "m1b_regenerate_review": lambda e: v4.collect(e),
                   "m1b_draft_pco": _collect_pco_draft, "m1b_revise_pco": _collect_pco_draft,
                   "m1b_resolve_window": _collect_resolve_window})
RUNNERS.update({"m1b_confirm_review": _run_confirm_review,
                "m1b_regenerate_review": _run_regenerate_review,
                "m1b_draft_pco": _run_draft_pco, "m1b_revise_pco": _run_revise_pco,
                "m1b_resolve_window": v4._resolve_window})
```

（`v4._resolve_window` 只读 `e._m1b`，由 `v4.collect` 在我们的 collector 里填好；候选 Mission 的额外字段由 0.5 的 `CandidateMission` 模型带入，`_resolve_window` 原样合并进新 Mission 版本。）

- [ ] **Step 4: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q
```

Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): gate Period Review on CEO confirmation and let 0.5 PCOs inherit it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 8: Mission 贡献 / 依赖 / 资源、DRI 唯一承诺与 Owner 生效

**Files:**
- Modify: `src/memory_service_runtime/governed/method_v05.py`（新增 Mission 与承诺段；替换 Task 4 的 `required_committers` 与 `activation_blockers`）
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: `v4._pco_responsibility`、`v4._candidate_basis_current`、`v4._commit_candidate`、`v4._activate_candidates`、`v4.activation_blockers` 的结构；Task 7 的 `_collect_resolve_window`。
- Produces: `_check_mission_extras(e, payload, *, architecture_ref, mission_ref=None)`、`_required_committers`（PCO 唯一）、`_collect_commit_candidate`、`_collect_activate_candidates`、`_run_activate_candidates`、`activation_blockers`；Mission `state.owner_activation_record_id`。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_method_v05_dispatch.py` 末尾追加：

```python
def _architecture_env():
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'StrategicArchitecture'}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64,
                'payload': {'battlefields': [{'unit_id': 'bf-1'}], 'domains': [{'unit_id': 'dom-1'}]}}
    return {oid: (head, revision)}, {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}


def _mission(**updates):
    return {'primary_scope_id': 'bf-1', 'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-15T00:00:00Z'},
            'contributes_to_scope_ids': ['dom-1'], 'dependencies': [], 'constraint_refs': [], **updates}


def test_mission_contributions_and_dependencies_bind_to_the_exact_architecture():
    refs, architecture = _architecture_env()
    e = _fake_execution(refs=refs)
    method_v05._check_mission_extras(e, _mission(), architecture_ref=architecture)
    with pytest.raises(GovernedError):  # contributing to its own primary Scope
        method_v05._check_mission_extras(e, _mission(contributes_to_scope_ids=['bf-1']), architecture_ref=architecture)
    with pytest.raises(GovernedError):  # unknown unit
        method_v05._check_mission_extras(e, _mission(contributes_to_scope_ids=['bf-9']), architecture_ref=architecture)
    inside = {'kind': 'scope', 'scope_id': 'dom-1', 'needed_by': '2026-10-10T00:00:00Z', 'note': 'n'}
    outside = {**inside, 'needed_by': '2026-11-10T00:00:00Z'}
    method_v05._check_mission_extras(e, _mission(dependencies=[inside]), architecture_ref=architecture)
    with pytest.raises(GovernedError):
        method_v05._check_mission_extras(e, _mission(dependencies=[outside]), architecture_ref=architecture)


def test_only_pco_responsibilities_need_a_commitment(monkeypatch):
    pco_id, mission_id = str(uuid4()), str(uuid4())
    heads = {pco_id: {'object_id': pco_id, 'object_type': 'PCO'}, mission_id: {'object_id': mission_id, 'object_type': 'Mission'}}
    e = SimpleNamespace(ref=lambda reference, **kw: None, head=lambda oid: heads[oid],
                        revision=lambda oid, rid: {'payload': {'primary_scope_id': 'bf-1'}})
    monkeypatch.setattr(method_v04, '_pco_responsibility', lambda e, payload: ('dri', 'DOMAIN_DRI', 'auth', {'assignment_id': 'x'}))
    payload = {'target_refs': [{'object_id': pco_id, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64},
                               {'object_id': mission_id, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}]}
    required = method_v05._required_committers(e, payload)
    assert set(required) == {pco_id} and required[pco_id]['owner'] == 'dri' and required[pco_id]['role'] == 'DOMAIN_DRI'
    assert method_v05.required_committers is method_v05._required_committers
    for kind in ('m1b_draft_mission', 'm1b_revise_mission', 'm1b_commit_candidate', 'm1b_activate_candidates'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q -k "mission or commitment"
```

Expected: `AttributeError: ... '_check_mission_extras'`。

- [ ] **Step 3: 写 Mission 与承诺段**

在 `method_v05.py` 的 PCO 段之后加，并删除 Task 4 的 `required_committers = v4._required_committers` 与占位 `activation_blockers`：

```python
# ---------------------------------------------------------------- Mission


def _check_mission_extras(e, payload, *, architecture_ref, mission_ref=None):
    _head, revision = e.ref(architecture_ref, types={"StrategicArchitecture"}, current=False)
    units = {d["unit_id"] for d in [*revision["payload"]["battlefields"], *revision["payload"]["domains"]]}
    for unit in payload["contributes_to_scope_ids"]:
        if unit == payload["primary_scope_id"] or unit not in units:
            fail("Contributions name other Scopes of the exact Architecture.", "INVALID_REQUEST")
    start = datetime.fromisoformat(payload["period"]["start"])
    end = datetime.fromisoformat(payload["period"]["end"])
    for dependency in payload["dependencies"]:
        needed = datetime.fromisoformat(dependency["needed_by"])
        if not (start <= needed <= end):
            fail("A dependency's needed_by must fall inside the Mission period.", "INVALID_REQUEST")
        if dependency["kind"] == "mission":
            e.ref(dependency["mission_ref"], types={"Mission"}, current=False)
        elif dependency["scope_id"] not in units:
            fail("A Scope dependency must name a unit of the exact Architecture.", "INVALID_REQUEST")
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"], mission_ref=mission_ref)


def _collect_mission_draft(e):
    v4.collect(e)
    payload = e.params["payload"]
    _pco_head, pco_revision = e.ref(payload["parent_pco_ref"], types={"PCO"}, current=False)
    mission_ref = _exact(e.target, e.target_revision) if getattr(e, "target", None) else None
    _check_mission_extras(e, payload, architecture_ref=pco_revision["payload"]["architecture_ref"], mission_ref=mission_ref)


def _run_draft_mission(e):
    head, revision = e.create("Mission", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_mission(e):
    head, revision = e.revise(e.target, e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _collect_resolve_window_missions(e):
    _collect_resolve_window(e)
    window = e.target_revision["payload"]
    frozen = e._m1b["missions"]
    for candidate in e.params["missions"]:
        head = frozen[str(candidate["object_id"])]["head"]
        revision = frozen[str(candidate["object_id"])]["revision"]
        _check_mission_extras(e, candidate, architecture_ref=window["architecture_ref"],
                              mission_ref=_exact(head, revision))


# ----------------------------------------------------- commitment / activation


def _required_committers(e, payload):
    """0.5：只有 PCO 责任需要承诺，由本域 DRI 做；Mission 由其 PCO 的承诺覆盖。"""
    required = {}
    for reference in payload["target_refs"]:
        e.ref(reference, types={"PCO", "Mission"}, current=True)
        member = e.head(reference["object_id"])
        if member["object_type"] != "PCO":
            continue
        revision = e.revision(reference["object_id"], reference["revision_id"])
        owner, role, domain_id, _assignment = v4._pco_responsibility(e, revision["payload"])
        required[str(reference["object_id"])] = {"reference": reference, "owner": owner,
                                                 "role": role, "domain_id": domain_id}
    return required


required_committers = _required_committers


def _collect_commit_candidate(e):
    if e.ctx.principal_type != "human":
        fail("Only the responsibility Scope DRI can commit.", "FORBIDDEN")
    state = _phase(e.state(e.target), "pending")
    payload = e.target_revision["payload"]
    v4._candidate_basis_current(e, payload)
    reference = e.params["responsibility_ref"]
    if not any(_same(reference, ref) for ref in payload["target_refs"]):
        fail("The commitment must name one exact candidate responsibility.", "INVALID_REQUEST")
    for ref in payload["target_refs"]:
        e.ref(ref, types={"PCO", "Mission"}, current=True)
    member = e.head(reference["object_id"])
    if member["object_type"] != "PCO":
        fail("0.5 commitments are made per responsibility Scope on its PCO; Missions are covered by their PCO.",
             "INVALID_REQUEST")
    revision = e.revision(reference["object_id"], reference["revision_id"])
    owner, expected_role, expected_domain, _assignment = v4._pco_responsibility(e, revision["payload"])
    if owner != e.ctx.principal_id:
        fail("A responsibility commitment can only be published by the Scope's own DRI.", "FORBIDDEN")
    assignment = None
    for row in db._assignments(e.conn, e.ctx):
        if row["principal_id"] != owner or row["role"] != expected_role or str(row["domain_id"]) != str(expected_domain):
            continue
        try:
            assignment = e.validate_assignment(row["assignment_id"], owner, "human")
            break
        except GovernedError:
            continue
    if assignment is None:
        fail("The DRI has no current assignment for this responsibility.", "FORBIDDEN")
    e._v04.update(commitment_owner=owner, commitment_assignment=assignment, candidate_state=state)


def _collect_activate_candidates(e):
    _human_ceo(e)
    state = _phase(e.state(e.target), "pending")
    payload = e.target_revision["payload"]
    v4._candidate_basis_current(e, payload)
    required = _required_committers(e, payload)
    for reference in payload["target_refs"]:
        member = e.head(reference["object_id"])
        member_state = e.state(member)
        if member_state.get("phase") != "candidate" or not _same(member_state.get("candidate_ref"), _exact(e.target, e.target_revision)):
            fail("The candidate set is no longer the authoritative member state.", "STALE_DEPENDENCY")
    rows = e.conn.execute(
        """SELECT responsibility_object_id, responsibility_revision_id, principal_id, assignment_id
           FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s""",
        (e.ctx.scope_id, e.target_revision["revision_id"])).fetchall()
    committed = {str(r["responsibility_object_id"]): db.jsonable(r) for r in db.jsonable(rows)}
    for oid, spec in required.items():
        recorded = committed.get(oid)
        reference = spec["reference"]
        if recorded is None or str(recorded["principal_id"]) != str(spec["owner"]):
            fail("Every responsibility Scope DRI must commit the exact candidate set before activation.", "INVALID_STATE")
        if str(recorded["responsibility_revision_id"]) != str(reference["revision_id"]):
            fail("A recorded commitment names a different responsibility revision.", "INVALID_STATE")
        try:
            assignment = access.assignment(e.conn, e.ctx, str(recorded["assignment_id"]), spec["owner"], "human")
        except GovernedError:
            fail("A recorded commitment assignment is no longer current; explicit recommit is required.", "INVALID_STATE")
        if assignment["role"] != spec["role"] or str(assignment["domain_id"]) != str(spec["domain_id"]):
            fail("A recorded commitment assignment no longer matches the named responsibility.", "INVALID_STATE")
        e.validate_assignment(str(recorded["assignment_id"]), spec["owner"], "human")
    if [d for d in payload.get("unresolved_differences", []) if d["critical"]]:
        fail("A critical unresolved difference blocks activation.", "INVALID_STATE")
    window_head, window_revision = e.ref(payload["window_ref"], types={"ReviewWindow"}, current=False)
    if e.state(window_head).get("phase") != "resolved":
        fail("The reviewed window is no longer in its resolved state.", "INVALID_STATE")
    e._v04["activation"] = {"state": state, "payload": payload, "window_head": window_head,
                            "window_revision": window_revision, "required": required}


def _run_activate_candidates(e):
    result = v4._activate_candidates(e)
    for reference in e._v04["activation"]["payload"]["target_refs"]:
        member = e.head(reference["object_id"])
        if member["object_type"] != "Mission":
            continue
        state = deepcopy(e.state(member))
        state["owner_activation_record_id"] = result["review_record_id"]
        e.set_state(member, state)
    return result


def activation_blockers(conn, ctx, obj):
    """0.5 工作台投影：与 0.4 同结构，只是承诺人规则换成 PCO 的 DRI。"""
    payload = (obj.get("latest_revision") or {}).get("payload") or {}
    candidate_revision_id = (obj.get("latest_revision") or {}).get("revision_id")
    context = light(conn, ctx)
    blockers = []
    try:
        v4._candidate_basis_current(context, payload)
    except GovernedError:
        blockers.append("stale_basis")
    try:
        candidate_head = context.head(obj["object_id"])
        candidate_revision = context.revision(obj["object_id"], obj["latest_revision"]["revision_id"])
        for reference in payload["target_refs"]:
            member = context.head(reference["object_id"])
            state = context.state(member)
            if state.get("phase") != "candidate" or not _same(state.get("candidate_ref"), _exact(candidate_head, candidate_revision)):
                blockers.append("member_state_changed")
                break
    except (GovernedError, KeyError):
        blockers.append("member_state_changed")
    if any(item.get("critical") for item in payload.get("unresolved_differences", [])):
        blockers.append("critical_difference")
    try:
        required = _required_committers(context, payload)
    except GovernedError:
        blockers.append("missing_commitment")
        return blockers
    rows = conn.execute(
        """SELECT responsibility_object_id, responsibility_revision_id, principal_id, assignment_id
           FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s""",
        (ctx.scope_id, candidate_revision_id)).fetchall()
    committed = {str(row["responsibility_object_id"]): db.jsonable(row) for row in db.jsonable(rows)}
    for oid, spec in required.items():
        recorded = committed.get(oid)
        if recorded is None or str(recorded["principal_id"]) != str(spec["owner"]):
            blockers.append("missing_commitment")
            continue
        if str(recorded["responsibility_revision_id"]) != str(spec["reference"]["revision_id"]):
            blockers.append("member_state_changed")
            continue
        try:
            assignment = access.assignment(conn, ctx, str(recorded["assignment_id"]), spec["owner"], "human")
        except GovernedError:
            blockers.append("commitment_assignment_revoked")
            continue
        if assignment["role"] != spec["role"] or str(assignment["domain_id"]) != str(spec["domain_id"]):
            blockers.append("commitment_assignment_mismatch")
    return blockers


COLLECTORS.update({"m1b_draft_mission": _collect_mission_draft, "m1b_revise_mission": _collect_mission_draft,
                   "m1b_resolve_window": _collect_resolve_window_missions,
                   "m1b_commit_candidate": _collect_commit_candidate,
                   "m1b_activate_candidates": _collect_activate_candidates})
RUNNERS.update({"m1b_draft_mission": _run_draft_mission, "m1b_revise_mission": _run_revise_mission,
                "m1b_commit_candidate": v4._commit_candidate,
                "m1b_activate_candidates": _run_activate_candidates})
```

（`_collect_activate_candidates` 与 `activation_blockers` 和 0.4 只差承诺人规则；`v4._commit_candidate` / `v4._activate_candidates` 只读 `e._v04` 里我们填好的键。`_LightExecution` 有 `head`、`revision`、`state`，`activation_blockers` 可直接用。）

- [ ] **Step 4: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q
```

Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): make the Scope DRI the only committer and record Owner activation in 0.5

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 9: Operating State：起止时间、无人确认、下钻

**Files:**
- Modify: `src/memory_service_runtime/governed/method_v05.py`（新增 State 段）
- Test: `tests/test_method_v05_dispatch.py`

**Interfaces:**
- Consumes: `v4._collect_propose_state`、`v4._state_key`。
- Produces: `_collect_propose_state`、`_run_propose_state`；状态 `phase=recorded`、`canonical_ref` 指向自身。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_method_v05_dispatch.py` 末尾追加：

```python
def test_drilldown_refs_must_be_canonical_states_of_other_subjects(monkeypatch):
    monkeypatch.setattr(method_v04, '_collect_propose_state', lambda e: None)
    subject = ref()
    lower_id, lower_rid = str(uuid4()), str(uuid4())
    lower_ref = {'object_id': lower_id, 'revision_id': lower_rid, 'payload_hash': 'a' * 64}
    lower_head = {'object_id': lower_id, 'object_type': 'OperatingState'}
    lower_revision = {'object_id': lower_id, 'revision_id': lower_rid, 'payload_hash': 'a' * 64, 'payload': {'subject_ref': ref()}}
    e = _fake_execution(refs={lower_id: (lower_head, lower_revision)}, states={lower_id: {'canonical_ref': lower_ref}})
    e.conn = _RowsConn([])
    e.params = {'payload': {'subject_ref': subject, 'as_of': '2026-10-31T00:00:00Z',
                            'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'},
                            'drilldown_refs': [lower_ref]}}
    method_v05._collect_propose_state(e)
    e.params['payload']['drilldown_refs'] = [{**lower_ref, 'revision_id': str(uuid4())}]
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_propose_state(e)
    assert exc.value.code == 'STALE_DEPENDENCY'
    lower_revision['payload']['subject_ref'] = subject
    e.params['payload']['drilldown_refs'] = [lower_ref]
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_propose_state(e)
    assert exc.value.code == 'INVALID_REQUEST'
    assert 'method_propose_state' in method_v05.COLLECTORS and 'method_propose_state' in method_v05.RUNNERS
    assert 'method_confirm_state' not in method_v05.COLLECTORS
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py -q -k drilldown
```

Expected: `AttributeError: ... '_collect_propose_state'`。

- [ ] **Step 3: 写 State 段**

在 `method_v05.py` 的承诺段之后加：

```python
# ------------------------------------------------------ Operating State


def _collect_propose_state(e):
    v4._collect_propose_state(e)     # 责任解析、基线、证据、previous_state_ref 沿用 0.4
    payload = e.params["payload"]
    for reference in payload["drilldown_refs"]:
        head, revision = e.ref(reference, types={"OperatingState"}, effective=True, current=False)
        if not _same(e.state(head).get("canonical_ref"), reference):
            fail("Drill-down references must cite canonical States.", "STALE_DEPENDENCY")
        if _same(revision["payload"]["subject_ref"], payload["subject_ref"]):
            fail("A State cannot drill down into its own subject.", "INVALID_REQUEST")
    previous = e.params.get("previous_state_ref")
    if previous:
        _head, prev = e.ref(previous, types={"OperatingState"})
        if prev["payload"]["period"] != payload["period"]:
            fail("A new generation must preserve the State identity and period.", "VERSION_CONFLICT")
    else:
        key = v4._state_key(payload)
        row = e.conn.execute(
            "SELECT state_id FROM gov_method_state_keys WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s AND as_of=%s",
            (e.ctx.scope_id, *key)).fetchone()
        if row:
            fail("This subject and as-of already have a State; regenerate with previous_state_ref.", "VERSION_CONFLICT")


def _run_propose_state(e):
    payload = e.params["payload"]
    previous = e.params.get("previous_state_ref")
    if previous:
        head, _old = e.ref(previous, types={"OperatingState"})
        head, revision = e.revise(head, payload, status="active", effective=True)
    else:
        head, revision = e.create("OperatingState", payload, status="active")
        e.transition(head, effective=True)
        e.conn.execute(
            "INSERT INTO gov_method_state_keys(scope_id,subject_id,outcome_id,as_of,state_id) VALUES(%s,%s,%s,%s,%s)",
            (e.ctx.scope_id, *v4._state_key(payload), head["object_id"]))
    reference = _exact(head, revision)
    state = deepcopy(e.state(head))
    state.update(phase="recorded", canonical_ref=reference, recommendation_ref=reference,
                 generated_by=e.ctx.principal_id)
    e.set_state(head, state)
    return {**reference, "phase": "recorded",
            "nature": "agent_analysis" if e.ctx.principal_type == "agent" else "owner_statement"}


COLLECTORS["method_propose_state"] = _collect_propose_state
RUNNERS["method_propose_state"] = _run_propose_state
```

（`e.transition(head, effective=True)` 把刚创建的版本标为生效；`canonical_ref` 指向自身，0.4 的 `_canonical_state` 检查在 Period Review 与 Problem 路径上继续成立。）

- [ ] **Step 4: 运行测试**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_dispatch.py tests/test_method_v04_models.py -q
```

Expected: 全部 passed。

- [ ] **Step 5: 提交**

```bash
git add src/memory_service_runtime/governed/method_v05.py tests/test_method_v05_dispatch.py
git commit -m "feat(method): make 0.5 Operating State canonical on generation with an explicit period

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 10: 读取面：公司集合视图、确认记录投影、集合列表

**Files:**
- Modify: `src/memory_service_runtime/governed/method_readers.py:110-131`（`review_records` 加 `effect`）及文件末尾（新增 `company_view`、`confirmations`）
- Modify: `src/memory_service_runtime/governed/routes.py:363-390`（集合表加五项；在 `/method/{collection}` 之前新增 `/method/company-view`；新增 `/method/objects/{object_id}/confirmations`）
- Test: `tests/test_method_v05_readers.py`

**Interfaces:**
- Produces: `method_readers.DECISION_KINDS`、`OPINION_KINDS`、`ANALYSIS_KINDS`、`review_effect(kind) -> str`、`confirmations(conn, ctx, object_id) -> {"items", "commitments"}`、`company_view(conn, ctx, period_start, period_end) -> {...}`；HTTP `GET /v1/method/company-view?period_start&period_end`、`GET /v1/method/objects/{id}/confirmations`。Task 11 的工作台与 Task 12 的验收消费这两个端点。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_method_v05_readers.py`：

```python
"""0.5 read projections: review effects, company view grouping (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from memory_service_runtime.governed import method_readers


def test_every_0_4_and_0_5_review_kind_has_one_effect():
    kinds = {'agent_issue_initiation', 'agreement_confirmation', 'agreement_formalized', 'candidate_set_activation',
             'ceo_reopen', 'issue_association', 'issue_participants', 'issue_reframe', 'ltco_confirmation',
             'ltco_revision_response', 'personal_agent_analysis', 'problem_closure', 'problem_transfer',
             'state_confirmation', 'strategy_update_confirmation', 'strategy_update_impact_review', 'window_closed',
             'window_comment', 'window_opinion_withdrawal', 'window_resolution',
             'review_confirmation', 'constraint_confirmation'}
    effects = {kind: method_readers.review_effect(kind) for kind in kinds}
    assert set(effects.values()) == {'decision', 'opinion', 'analysis', 'record'}
    assert effects['ltco_confirmation'] == 'decision' and effects['constraint_confirmation'] == 'decision'
    assert effects['review_confirmation'] == 'decision' and effects['candidate_set_activation'] == 'decision'
    assert effects['window_comment'] == 'opinion' and effects['personal_agent_analysis'] == 'analysis'
    assert effects['window_closed'] == 'record' and method_readers.review_effect('unknown_kind') == 'record'


def test_company_view_groups_effective_objects_by_primary_scope(monkeypatch):
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    old_pco = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-08-01T00:00:00+00:00', 'end': '2026-08-31T00:00:00+00:00'}}}
    mission = {'object_id': str(uuid4()), 'object_type': 'Mission', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-15T00:00:00+00:00'}}}
    company = {'object_id': str(uuid4()), 'object_type': 'Constraint', 'payload': {'applies_to': {'kind': 'company'}, 'effective': period}}
    scoped = {'object_id': str(uuid4()), 'object_type': 'Constraint', 'payload': {'applies_to': {'kind': 'scope', 'scope_id': 'bf-1'}, 'effective': period}}
    rows = [ltco, pco, old_pco, mission, company, scoped]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    monkeypatch.setattr(method_readers, 'object_state',
                        lambda conn, ctx, oid: {'object_id': oid, 'method_state': {}, 'latest_revision': None})
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    assert [s['scope_id'] for s in view['scopes']] == ['bf-1']
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert [p['object_id'] for p in scope['pcos']] == [pco['object_id']]
    assert [m['object_id'] for m in scope['missions']] == [mission['object_id']]
    assert [c['object_id'] for c in scope['constraints']] == [scoped['object_id']]
    assert [c['object_id'] for c in view['company_constraints']] == [company['object_id']]
    assert view['period'] == period and view['schema_version'] == 'method-read/0.5'
```

- [ ] **Step 2: 运行确认失败**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_readers.py -q
```

Expected: `AttributeError: ... 'review_effect'`。

- [ ] **Step 3: 写读取器**

`src/memory_service_runtime/governed/method_readers.py`：在 `review_records` 之前加常量与 `review_effect`，在 `review_records` 的 `row["effective_opinion"] = ...` 之前加一行 `row["effect"] = review_effect(row["kind"])`：

```python
# 决定类 = 有权人的正式确认 / 承诺 / 激活 / 重开 / 关闭 / 移交；意见类 = 窗口评论；分析类 = Agent 产出；其余为系统记录。
DECISION_KINDS = frozenset({
    "ltco_confirmation", "review_confirmation", "constraint_confirmation", "candidate_set_activation",
    "agreement_confirmation", "agreement_formalized", "strategy_update_confirmation", "state_confirmation",
    "ceo_reopen", "problem_closure", "problem_transfer",
})
OPINION_KINDS = frozenset({"window_comment", "window_opinion_withdrawal", "ltco_revision_response"})
ANALYSIS_KINDS = frozenset({"personal_agent_analysis", "strategy_update_impact_review", "window_resolution"})


def review_effect(kind):
    if kind in DECISION_KINDS:
        return "decision"
    if kind in OPINION_KINDS:
        return "opinion"
    if kind in ANALYSIS_KINDS:
        return "analysis"
    return "record"
```

在文件末尾加：

```python
def confirmations(conn, ctx, object_id):
    """一个对象的决定类记录与承诺行；不可见对象按 head 的 NOT_FOUND / FORBIDDEN 处理。"""
    access.head(conn, ctx, object_id)
    rows = conn.execute(
        "SELECT * FROM gov_method_reviews WHERE scope_id=%s AND target_object_id=%s ORDER BY recorded_at, record_id",
        (ctx.scope_id, object_id)).fetchall()
    items = []
    for row in db.jsonable(rows):
        if review_effect(row["kind"]) != "decision":
            continue
        try:
            access.revision(conn, ctx, row["target_object_id"], row["target_revision_id"])
        except GovernedError:
            continue
        row["effect"] = "decision"
        items.append(row)
    commitments = db.jsonable(conn.execute(
        """SELECT commitment_id, candidate_object_id, candidate_revision_id, responsibility_object_id,
                  responsibility_revision_id, principal_id, assignment_id, statement, recorded_at
           FROM gov_method_commitments WHERE scope_id=%s AND (responsibility_object_id=%s OR candidate_object_id=%s)
           ORDER BY recorded_at, commitment_id""",
        (ctx.scope_id, object_id, object_id)).fetchall())
    return {"items": items, "commitments": commitments}


def _overlaps(period, start, end):
    return _time(period["start"]) < _time(end) and _time(period["end"]) > _time(start)


def company_view(conn, ctx, period_start, period_end):
    """按主 Scope 汇总当前正式 LTCO、时段内 PCO / Mission 与生效 Constraint；投影，不是对象。"""
    rows = db.jsonable(conn.execute(
        """SELECT o.object_id, o.object_type, r.payload FROM gov_objects o
           JOIN gov_object_revisions r ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
           WHERE o.scope_id=%s AND o.object_type IN ('LTCO','PCO','Mission','Constraint')
           ORDER BY o.object_type, o.object_id""", (ctx.scope_id,)).fetchall())
    scopes, company_constraints, mission_constraints = {}, [], {}

    def visible(oid):
        if not access.is_method_object(conn, ctx, oid):
            return None
        try:
            return object_state(conn, ctx, oid)
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                return None
            raise

    def bucket(unit):
        return scopes.setdefault(unit, {"scope_id": unit, "ltco": None, "pcos": [], "missions": [], "constraints": []})

    for row in rows:
        oid, kind, payload = str(row["object_id"]), row["object_type"], row["payload"]
        if kind == "Constraint":
            if not _overlaps(payload["effective"], period_start, period_end):
                continue
            item = visible(oid)
            if item is None:
                continue
            applies = payload["applies_to"]
            if applies["kind"] == "company":
                company_constraints.append(item)
            elif applies["kind"] == "scope":
                bucket(applies["scope_id"])["constraints"].append(item)
            else:
                mission_constraints.setdefault(str(applies["mission_ref"]["object_id"]), []).append(item)
            continue
        if kind != "LTCO" and not _overlaps(payload["period"], period_start, period_end):
            continue
        item = visible(oid)
        if item is None:
            continue
        target = bucket(payload["primary_scope_id"])
        if kind == "LTCO":
            target["ltco"] = item
        elif kind == "PCO":
            target["pcos"].append(item)
        else:
            record = (item.get("method_state") or {}).get("owner_activation_record_id")
            item["owner_effective_from"] = None
            if record:
                found = conn.execute("SELECT recorded_at FROM gov_method_reviews WHERE scope_id=%s AND record_id=%s",
                                     (ctx.scope_id, record)).fetchone()
                item["owner_effective_from"] = db.jsonable(found["recorded_at"]) if found else None
            target["missions"].append(item)
    return {"schema_version": "method-read/0.5", "period": {"start": period_start, "end": period_end},
            "scopes": [scopes[key] for key in sorted(scopes)],
            "company_constraints": company_constraints, "mission_constraints": mission_constraints}
```

（`_time` 是文件里已有的时间解析；`object_state` 与 `access.is_method_object` 已在同文件使用，保持同样的可见性口径。）

- [ ] **Step 4: 接入路由**

`src/memory_service_runtime/governed/routes.py`：`method_list` 的 `kinds` 字典加五项：`"ltcos": "LTCO", "pcos": "PCO", "missions": "Mission", "constraints": "Constraint", "operating-states": "OperatingState"`。在 `@router.get("/method/{collection}")` **之前**（否则会被通配段落吃掉）插入：

```python
@router.get("/method/company-view")
def method_company_view(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                        period_start: AwareDatetime, period_end: AwareDatetime):
    from . import method_readers
    workbench.strict_query(request.query_params, {"period_start", "period_end"})
    if period_start >= period_end:
        raise GovernedError("INVALID_REQUEST", "period_start must precede period_end", status=422)
    with db.transaction(token) as (conn, ctx):
        result = method_readers.company_view(conn, ctx, period_start.isoformat(), period_end.isoformat())
    response.headers["Cache-Control"] = "no-store"
    return result
```

在 `method_reviews` 之后加：

```python
@router.get("/method/objects/{object_id}/confirmations")
def method_confirmations(object_id: uuid.UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    from . import method_readers
    with db.transaction(token) as (conn, ctx):
        result = method_readers.confirmations(conn, ctx, str(object_id))
    response.headers["Cache-Control"] = "no-store"
    return result
```

（`AwareDatetime` 已在该文件第 36 行的模型里使用；若该名字只在模型作用域导入，把它加进文件顶部的 pydantic 导入。）

- [ ] **Step 5: 运行测试与路由回归**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_method_v05_readers.py tests/test_method_map.py tests/test_dynamic_endpoint.py tests/test_static_endpoint.py -q
```

Expected: 全部 passed。

- [ ] **Step 6: 提交**

```bash
git add src/memory_service_runtime/governed/method_readers.py src/memory_service_runtime/governed/routes.py tests/test_method_v05_readers.py
git commit -m "feat(runtime): add the 0.5 company view, confirmation projection and review effects

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 11: 治理工作台前端：0.5 规则版本与三个人工门表单

**Files:**
- Modify: `workbench/dashboard/src/lib/ontology.ts:14-27`（版本枚举）、`BASE_TYPE_INFO`（加 `Constraint`）、`typeInfo`、`AREAS`（operations 加 `Constraint`）、`MAP_EDGES`（0.4 边扩到 0.5，加三条 Constraint 边）、`V04_TYPES` 之后加 `V05_TYPES` 与 `V05_OVERRIDES`、`versionTypes`
- Modify: `workbench/dashboard/src/lib/labels.ts:56-60`（`RULES_VERSION_LABELS` 加 0.4、0.5）
- Modify: `workbench/dashboard/src/MethodActions.tsx:44-50`（`BROWSER_ACTIONS`）、`:36-40`（`STATUS`）、`:90-130`（`FORMS`）、`:162-168`（`paramsForMethodAction`）
- Test: `workbench/dashboard/src/__tests__/ontology-semantics.test.ts`、`MethodActions.test.tsx`

**Interfaces:**
- Consumes: 后端按对象版本投影的动作列表（Task 4 的 `human_actions_for`）。
- Produces: `RulesVersion` 含 `"0.5"`；`m1b_confirm_ltco` 表单带 `conclusion`；`m1b_confirm_review`、`m1b_confirm_constraint` 表单；`paramsForMethodAction` 对应参数。

- [ ] **Step 1: 写失败的测试**

`ontology-semantics.test.ts` 末尾追加：

```ts
describe("0.5 rules", () => {
  const registered = new Set(["LTCO", "PCO", "Mission", "OperatingState", "PeriodReview", "Constraint", "CandidateSet"])
  it("is a first-class rules version", () => {
    expect(RULES_VERSIONS[0]).toBe("0.5")
    expect(rulesOfContractVersion("tkos.method/0.5")).toBe("0.5")
    expect(contractVersionOf("0.5")).toBe("tkos.method/0.5")
  })
  it("reads Constraint, canonical State and DRI-only commitment in the 0.5 text", () => {
    expect(text(typeInfo("Constraint", "0.5")!)).toContain("适用范围")
    expect(typeInfo("Constraint", "0.4")).toBeNull()
    expect(text(typeInfo("OperatingState", "0.5")!)).toContain("无人工确认")
    expect(text(typeInfo("OperatingState", "0.4")!)).not.toContain("无人工确认")
    expect(text(typeInfo("CandidateSet", "0.5")!)).toContain("DRI")
    expect(text(typeInfo("PeriodReview", "0.5")!)).toContain("CEO 确认")
  })
  it("keeps every 0.4 edge under 0.5 and adds the Constraint references", () => {
    const edges = mapEdges("0.5", registered)
    expect(edges).toContainEqual({ from: "Constraint", to: "PCO", label: "起草参考" })
    expect(edges.filter((edge) => edge.from === "Constraint")).toHaveLength(3)
    expect(mapEdges("0.4", registered).every((edge) => edges.some((e) => e.from === edge.from && e.to === edge.to))).toBe(true)
    expect(areasFor("0.5", registered).some((area) => area.types.includes("Constraint"))).toBe(true)
  })
})
```

`MethodActions.test.tsx` 末尾追加（沿用文件里 `render` 一个 task 的写法；`taskWith` 若不存在，用文件顶部现有的 fixture 构造函数）：

```tsx
it("builds 0.5 confirmation params: LTCO conclusion, review overrides and constraint statement", () => {
  expect(paramsForMethodAction('m1b_confirm_ltco', { conclusion: 'maintained', statement: 'Still valid this period' }))
    .toEqual({ conclusion: 'maintained', statement: 'Still valid this period' })
  expect(paramsForMethodAction('m1b_confirm_review', { statement: 'Confirmed', findings: 'a\n\nb', learnings: '', implications: '' }))
    .toEqual({ statement: 'Confirmed', findings: ['a', 'b'] })
  expect(paramsForMethodAction('m1b_confirm_constraint', { statement: 'Scope DRI confirms' }))
    .toEqual({ statement: 'Scope DRI confirms' })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd workbench/dashboard && npm test -- src/__tests__/ontology-semantics.test.ts src/__tests__/MethodActions.test.tsx
```

Expected: 类型错误 `"0.5"` 不在 `RulesVersion`，以及参数断言失败。

- [ ] **Step 3: 改 ontology.ts**

- 第 14–27 行：`export type RulesVersion = "0.1" | "0.2" | "0.3" | "0.4" | "0.5"`；`RULES_VERSIONS = ["0.5", "0.4", "0.3", "0.2", "0.1"]`；`rulesOfContractVersion` 加 `if (contractVersion === "tkos.method/0.5") return "0.5"`。
- `BASE_TYPE_INFO` 加：

```ts
  Constraint: {
    definition: "经营条件与约束：人、钱、产能、政策、依赖等真实限制，带适用范围（公司 / 责任域 / Mission）、生效起止、来源与权威、限制程度。",
    keyFacts: [
      "只能被 LTCO、PCO、Mission 以参考方式引用；引用是输入，不是承接。",
      "登记与修订可由 Co-agent 或范围责任人发起；确认按范围：公司级 CEO，责任域级该 Scope 的当前 DRI。",
      "抢人、超限、错期的校验是 Agent 分析，Runtime 不自动判定。",
    ],
    relations: [
      { target: "LTCO", label: "审视参考" }, { target: "PCO", label: "起草参考" }, { target: "Mission", label: "资源对照" },
    ],
    lifecycle: ["登记草稿", "范围责任人确认", "生效，被目标对象引用", "修订产生新草稿"],
    actors: "Co-agent 或范围责任人登记；范围责任人本人确认。",
    authority: "以已确认的确切版本为准；草稿不得被引用。",
  },
```

- `typeInfo`：

```ts
export function typeInfo(type: string, rules: RulesVersion): TypeInfo | null {
  const base = BASE_TYPE_INFO[type]
  if (!base) return null
  if (rules === "0.4" && !V04_TYPES.includes(type)) return null
  if (rules === "0.5" && !V05_TYPES.includes(type)) return null
  if (rules !== "0.4" && rules !== "0.5" && type === "Constraint") return null
  const override = rules === "0.5" ? { ...V04_OVERRIDES[type], ...V05_OVERRIDES[type] }
    : rules === "0.4" ? V04_OVERRIDES[type] : VERSION_OVERRIDES[type]?.[rules]
  return override && Object.keys(override).length > 0 ? { ...base, ...override } : base
}
```

- `AREAS` 的 `operations` 项 `types` 改为 `["OperatingState", "Constraint", "BusinessFact", "PeriodReview", "OperatingProblem"]`。
- `MAP_EDGES`：所有 `versions: ["0.4"]` 改为 `["0.4", "0.5"]`，所有 `versions: ["0.3", "0.4"]` 改为 `["0.3", "0.4", "0.5"]`；追加三条：

```ts
  { from: "Constraint", to: "LTCO", label: "审视参考", versions: ["0.5"] },
  { from: "Constraint", to: "PCO", label: "起草参考", versions: ["0.5"] },
  { from: "Constraint", to: "Mission", label: "资源对照", versions: ["0.5"] },
```

- `V04_TYPES` 之后加：

```ts
/** Compiled tkos.method/0.5 object types (docs/runtime-method-registry-0.5.json). */
const V05_TYPES: string[] = [...V04_TYPES, "Constraint"]

/** 0.5 curated readings on top of the 0.4 overrides. */
const V05_OVERRIDES: Record<string, Partial<TypeInfo>> = {
  OperatingState: {
    definition: "0.5 的经营状态：带起止时间，由 Co-agent / MF 生成即为正式状态，无人工确认；上层状态可下钻到下层状态与直接证据，不自动汇总。",
    keyFacts: ["生成即 canonical；对状态有异议走经营问题，不改写状态。", "同一主体同一截止时间只有一个身份，再次生成产生新版本。"],
    actors: "Co-agent、CEO Agent 或主体责任人本人生成。",
    authority: "以最新生成版本为准；不设确认人。",
  },
  PeriodReview: {
    definition: "0.5 的周期复盘：Co-agent 起草，CEO 确认（可改写发现、学习、含义）后生效；下一周期 PCO 必须承接已确认的复盘。",
    lifecycle: ["Co-agent 生成", "CEO 确认，可改", "生效，被下期 PCO 引用"],
    actors: "Co-agent 起草，CEO 本人确认。",
    authority: "只有 CEO 确认的版本生效。",
  },
  PCO: {
    keyFacts: [
      "承接已确认的复盘与本域 LTCO，引用已确认的约束，写明边界与预期推进幅度。",
      "候选集合只由责任域 DRI 承诺，一次覆盖本域 PCO 与其全部 Mission。",
    ],
  },
  LTCO: {
    keyFacts: ["含实现逻辑、关键假设与约束引用；每次确认带结论：首次确立、修订或维持。"],
  },
  Mission: {
    keyFacts: [
      "记录对其他责任域的贡献、依赖（Mission 或责任域，含需要时点）、资源需求与约束引用。",
      "Owner 端到端对交付结果负责，实际执行可分派；Owner 指派在 CEO 整组激活时生效。",
    ],
  },
  CandidateSet: {
    definition: "0.5 的候选集合：关窗收拢的完整 PCO + Mission 集合；只有各责任域 DRI 对本域 PCO 承诺，CEO 整组激活。",
  },
}
```

- `versionTypes`：在 `if (rules === "0.4") return new Set(V04_TYPES)` 前加 `if (rules === "0.5") return new Set(V05_TYPES)`。

- [ ] **Step 4: 改 labels.ts 与 MethodActions.tsx**

`labels.ts` 的 `RULES_VERSION_LABELS` 加 `"0.4": "业务规则 0.4"`、`"0.5": "业务规则 0.5"`。

`MethodActions.tsx`：
- `BROWSER_ACTIONS` 加 `'m1b_confirm_review', 'm1b_confirm_constraint'`。
- `STATUS` 加 `recorded: '已记录（正式状态）'`。
- `FORMS`：把 `m1b_confirm_ltco` 改为：

```ts
  m1b_confirm_ltco: [
    { kind: 'select', name: 'conclusion', label: '审视结论（0.5 必填；0.4 忽略）',
      options: [{ value: 'established', label: 'established · 首次确立' }, { value: 'revised', label: 'revised · 修订为新版本' },
                { value: 'maintained', label: 'maintained · 本期维持不变' }] },
    { kind: 'textarea', name: 'statement', label: '确认说明', required: true }],
  m1b_confirm_review: [
    { kind: 'textarea', name: 'statement', label: '确认说明', required: true },
    { kind: 'textarea', name: 'findings', label: '改写发现（每行一条，可空）' },
    { kind: 'textarea', name: 'learnings', label: '改写学习（每行一条，可空）' },
    { kind: 'textarea', name: 'implications', label: '改写含义（每行一条，可空）' }],
  m1b_confirm_constraint: [{ kind: 'textarea', name: 'statement', label: '确认说明（范围责任人本人）', required: true }],
```

- `paramsForMethodAction`：把 `case 'm1b_confirm_ltco':` 从与 agreement 合并的分支里拆出来，并加新分支：

```ts
    case 'm1b_confirm_ltco': {
      const params: Record<string, unknown> = { statement: text(values, 'statement') }
      if (text(values, 'conclusion')) params.conclusion = text(values, 'conclusion')
      return params
    }
    case 'm1b_confirm_review': {
      const params: Record<string, unknown> = { statement: text(values, 'statement') }
      for (const key of ['findings', 'learnings', 'implications'] as const) {
        const lines = text(values, key).split('\n').map((line) => line.trim()).filter(Boolean)
        if (lines.length > 0) params[key] = lines
      }
      return params
    }
    case 'm1b_confirm_constraint':
      return { statement: text(values, 'statement') }
```

（0.4 对象的 `m1b_confirm_ltco` 表单里 `conclusion` 留空即不发送，0.4 的 `ConfirmLTCO` 不接受该字段。）

- [ ] **Step 5: 运行前端测试、类型检查与构建**

```bash
cd workbench/dashboard && npm test && npm run typecheck && cd ../.. && scripts/build_dashboard.sh && PYTHONPATH=src .venv/bin/python scripts/verify_dashboard_assets.py
```

Expected: vitest 全部通过（文件数比改动前多 0，用例多 4），typecheck 无错误，构建产物与清单校验通过。

- [ ] **Step 6: 提交**

```bash
git add workbench/dashboard/src/lib/ontology.ts workbench/dashboard/src/lib/labels.ts workbench/dashboard/src/MethodActions.tsx workbench/dashboard/src/__tests__/ontology-semantics.test.ts workbench/dashboard/src/__tests__/MethodActions.test.tsx src/memory_service_app/dashboard_dist
git commit -m "feat(workbench): show Method 0.5 rules and the three 0.5 human gates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 12: 独立验收 `acceptance/method_v05` 与 0.4 复跑

**Files:**
- Create: `acceptance/method_v05/__init__.py`（空）、`acceptance/method_v05/fixture.py`、`acceptance/method_v05/flow.py`、`acceptance/method_v05/run.py`、`acceptance/method_v05/README.md`

**Interfaces:**
- Consumes: `acceptance/method_v04/fixture.py` 的 `seed_v04`；`acceptance/method_v04/flow.py` 的 `Flow`（0.4 驱动器）与 `period`；`acceptance/method_independent/harness.py` 的 `MethodHarness`；`acceptance/protocol_a1_independent/support.py` 的 `public_json`、`source_manifest`、`private_json`；`ControlAdapter.cli`。
- Produces: `summary.json` 含 `runtime_method_v05_api_accepted`。

- [ ] **Step 1: fixture**

`acceptance/method_v05/fixture.py`：

```python
"""0.5 身份与登记：复用 0.4 的合成身份，补 0.5 动作的角色策略，只把契约、profile 与注册表换成 0.5。"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.method_independent.fixture import METHOD_ROLES, uid
from acceptance.method_v04.fixture import seed_v04
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]
V05_ACTIONS = list(json.loads((ROOT / 'docs/runtime-method-registry-0.5.json').read_text())['actions'])


def seed_v05(env, path: Path, label: str):
    fixture = seed_v04(env, path, label)
    fixture['contract_version'] = 'tkos.method/0.5'
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (fixture['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (fixture['scope_id'],))
        for domain in (fixture['domains']['company'], fixture['domains']['a'], fixture['domains']['b'],
                       fixture['domains']['auth_a'], fixture['domains']['auth_b']):
            old = conn.execute(
                """SELECT policy_id,policy_seq,content FROM gov_activation_policies
                   WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1""",
                (fixture['scope_id'], domain)).fetchone()
            content = dict(old['content'])
            action_roles = dict(content.get('action_roles', {}))
            for action in V05_ACTIONS:
                action_roles[action] = sorted(set(METHOD_ROLES) | {'MISSION_DRI', 'AGENT'})
            content['action_roles'] = action_roles
            conn.execute(
                """INSERT INTO gov_activation_policies
                   (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (uid(), fixture['scope_id'], domain, old['policy_id'], old['policy_seq'] + 1,
                 Jsonb(content), fixture['actors']['ceo']['principal_id']))
    private_json(path, fixture)
    return fixture


def register_v05(h, source, f):
    """通过真实维护 CLI 安装 0.5 profile / policy / registry；profile 同时核对本体登记字节。"""
    profile_path = ROOT / 'docs/contracts/method-profile-0.5.json'
    registry_path = ROOT / 'docs/runtime-method-registry-0.5.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic Method 0.5 independent API acceptance']
    adapter.cli('method-v05-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-method-0.5.md'),
        '--ontology-registry-file', str(ROOT / 'docs/contracts/ontology-registry-0.7.json'),
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
"""0.5 公开 HTTP 驱动：0.4 的链路加 Constraint、审视结论、复盘确认、Mission 扩展字段与带周期的状态。"""
from __future__ import annotations

from datetime import datetime, timezone

from acceptance.method_independent.flow import exact
from acceptance.method_v04.flow import Flow as FlowV04, period


class Flow(FlowV04):
    def command(self, kind, params, *, oid=None, actor='ceo', key=None, run=None):
        body = super().command(kind, params, oid=oid, actor=actor, key=key, run=run)
        body['contract_version'] = 'tkos.method/0.5'
        return body

    # ------------------------------------------------------------ Constraint

    def record_constraint(self, applies_to, *, actor='co_agent', architecture_ref=None, effective, title='Synthetic constraint'):
        payload = {'title': title, 'applies_to': applies_to, 'architecture_ref': architecture_ref,
                   'statement': 'Only two engineers are available in this period.',
                   'constraint_type': 'people', 'effective': effective, 'source': 'Synthetic headcount plan',
                   'authority': 'Scope DRI', 'severity': 'hard', 'evidence_refs': []}
        return exact(self.act(actor, 'm1b_record_constraint',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_constraint(self, actor, constraint):
        return self.act(actor, 'm1b_confirm_constraint',
                        {'statement': f'{actor} confirms this exact constraint for its scope.'},
                        oid=constraint['object_id'])['result']

    # ------------------------------------------------------------------ M1B

    def propose_ltco(self, strategy_ref, architecture_ref, scope_id, ltco_period, *, title=None, constraints=()):
        payload = {'title': title or f'Synthetic LTCO {scope_id}',
                   'primary_scope_id': scope_id, 'period': ltco_period,
                   'architecture_ref': architecture_ref, 'strategy_ref': strategy_ref,
                   'result_statement': f'Long-term result for {scope_id} with traceable evidence.',
                   'criteria': ['Result is independently evidenced'],
                   'boundary': 'No execution or acceptance authority in this contract.',
                   'horizon': 'Rolling six months from now',
                   'why': 'The scope needs an explicit long-term result.',
                   'baseline_refs': [architecture_ref],
                   'realization_logic': 'Two key business shifts, sequenced.',
                   'key_assumptions': ['Demand holds through the horizon'],
                   'constraint_refs': list(constraints)}
        return exact(self.act('ceo_agent', 'm1b_propose_ltco',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_ltco(self, ltco, conclusion='established'):
        return self.act('ceo', 'm1b_confirm_ltco',
                        {'conclusion': conclusion, 'statement': 'The current CEO confirms this exact LTCO responsibility.'},
                        oid=ltco['object_id'])['result']

    def draft_pco(self, ltco_ref, scope_id, pco_period, *, title=None, period_review_ref=None, constraints=()):
        ltco = self.object(ltco_ref['object_id'])['latest_revision']['payload']
        payload = {'title': title or f'Synthetic PCO {scope_id}', 'primary_scope_id': scope_id,
                   'period': pco_period, 'parent_ltco_ref': ltco_ref, 'period_review_ref': period_review_ref,
                   'architecture_ref': ltco['architecture_ref'], 'strategy_ref': ltco['strategy_ref'],
                   'current_reality': 'Synthetic current reality with partial evidence.',
                   'result_statement': f'Period result for {scope_id}.',
                   'criteria': ['Period result is evidenced'],
                   'expected_lt_advance': 'Advances the long-term result by one stage.',
                   'why': 'The period needs an explicit result.',
                   'boundary': 'Not promised this period: the second market.',
                   'constraint_refs': list(constraints)}
        return exact(self.act('co_agent', 'm1b_draft_pco',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def draft_mission(self, pco_ref, owner, scope_id, mission_period, *, title=None, evidence=(),
                      contributes=(), dependencies=(), constraints=()):
        payload = {'title': title or f'Synthetic Mission {scope_id}', 'owner_principal_id': self.principal(owner),
                   'primary_scope_id': scope_id, 'parent_pco_ref': pco_ref,
                   'why': 'A necessary result unit under the PCO.',
                   'requirements': ['Deliver the scoped result with evidence'],
                   'criteria': ['Requirements are verifiable'],
                   'evidence_refs': list(evidence), 'period': mission_period,
                   'boundary': 'Does not cover onboarding.',
                   'contributes_to_scope_ids': list(contributes), 'dependencies': list(dependencies),
                   'resource_needs': ['one designer for two weeks'], 'constraint_refs': list(constraints)}
        return exact(self.act('co_agent', 'm1b_draft_mission',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_review(self, review, *, findings=None):
        params = {'statement': 'The CEO confirms this exact period review.'}
        if findings is not None:
            params['findings'] = findings
        return self.act('ceo', 'm1b_confirm_review', params, oid=review['object_id'])['result']

    # ---------------------------------------------------------------- State

    def propose_state(self, subject_ref, *, actor='co_agent', rag='unknown', summary='Evidence gap',
                      evidence=(), data_gaps=('Awaiting independent evidence',), state_period=None, drilldown=()):
        state_period = state_period or period(-30, 0)
        payload = {'subject_ref': subject_ref, 'as_of': state_period['end'], 'period': state_period,
                   'summary': summary, 'rag': rag, 'baseline_refs': [subject_ref],
                   'evidence_refs': list(evidence), 'drilldown_refs': list(drilldown), 'data_gaps': list(data_gaps),
                   'generation_version': 'controlled-generation-1'}
        return exact(self.act(actor, 'method_propose_state',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    # ---------------------------------------------------------------- reads

    def company_view(self, view_period, actor='ceo'):
        return self.clients[actor].json('GET', '/v1/method/company-view'
                                        f"?period_start={view_period['start']}&period_end={view_period['end']}")

    def confirmations(self, oid, actor='ceo'):
        return self.clients[actor].json('GET', f'/v1/method/objects/{oid}/confirmations')
```

（`FlowV04.propose_state` 的签名与 0.4 不同，0.5 的 `run.py` 只调用本文件的版本。）

- [ ] **Step 3: run**

`acceptance/method_v05/run.py`（`_change`、`_revoke` 与 `candidate_refs` 从 `acceptance/method_v04/run.py` 原样导入）：

```python
"""Real HTTP+PG acceptance of the tkos.method/0.5 ontology-alignment chain.

Happy path and the negative matrix both run through /v1/actions/prepare +
/v1/actions. SQL is used only for independent assertions and identity fixture
setup. This script does not prove real-model behaviour.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from acceptance.method_independent.harness import MethodHarness
from acceptance.method_v04.run import _change, candidate_refs
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import register_v05, seed_v05
from .flow import Flow, period


def happy_path(h, f, flow):
    checks = []

    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)

    evidence = flow.upload()
    run_ref = flow.open_run()
    issue = flow.create_issue(run_ref, evidence)
    flow.set_participants(issue)
    agreement = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement)
    agreement = flow.ref(agreement['object_id'], effective=True)
    proposal = flow.propose_update(issue, agreement, _change(flow, rationale='Initial pair: no retained object exists yet.'))
    flow.review_update(proposal)
    strategy_ref, architecture_ref = flow.confirm_update(proposal)['result']['changed_refs']
    check('m1a_chain_unchanged_under_0_5',
          flow.object(strategy_ref['object_id'])['effective_revision_id'] == strategy_ref['revision_id'])

    # ------------------------------------------------------------ Constraint
    ltco_period, pco_period, mission_period = period(-30, 335), period(-1, 30), period(0, 15)
    scope_constraint = flow.record_constraint({'kind': 'scope', 'scope_id': 'scope-a'}, architecture_ref=architecture_ref,
                                              effective=pco_period)
    flow.deny('owner_a', flow.command('m1b_confirm_constraint', {'statement': 'An IC tries to confirm.'},
                                      oid=scope_constraint['object_id']), codes={'FORBIDDEN'})
    flow.deny('ceo', flow.command('m1b_confirm_constraint', {'statement': 'The CEO is not the scope DRI.'},
                                  oid=scope_constraint['object_id']), codes={'FORBIDDEN'})
    flow.confirm_constraint('dri_a', scope_constraint)
    scope_constraint = flow.ref(scope_constraint['object_id'], effective=True)
    check('scope_constraint_confirmed_by_its_dri_only',
          flow.object(scope_constraint['object_id'])['method_state']['phase'] == 'confirmed')
    company_constraint = flow.record_constraint({'kind': 'company'}, actor='ceo', effective=ltco_period,
                                                title='Synthetic company cash constraint')
    flow.confirm_constraint('ceo', company_constraint)
    company_constraint = flow.ref(company_constraint['object_id'], effective=True)
    check('company_constraint_confirmed_by_ceo', True)

    # ------------------------------------------------------------------ LTCO
    ltco_a = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-a', ltco_period,
                               constraints=[company_constraint, scope_constraint])
    ltco_b = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-b', ltco_period)
    flow.deny('ceo', flow.command('m1b_confirm_ltco', {'conclusion': 'maintained', 'statement': 'Cannot maintain a draft.'},
                                  oid=ltco_a['object_id']), codes={'INVALID_REQUEST'})
    flow.confirm_ltco(ltco_a)
    flow.confirm_ltco(ltco_b)
    ltco_a = flow.ref(ltco_a['object_id'], effective=True)
    ltco_b = flow.ref(ltco_b['object_id'], effective=True)
    maintained = flow.confirm_ltco(ltco_a, conclusion='maintained')
    check('maintained_review_keeps_the_effective_version_and_records_the_conclusion',
          maintained['conclusion'] == 'maintained'
          and flow.object(ltco_a['object_id'])['effective_revision_id'] == ltco_a['revision_id']
          and flow.object(ltco_a['object_id'])['method_state']['last_review']['conclusion'] == 'maintained')
    conf = flow.confirmations(ltco_a['object_id'])
    check('confirmations_projection_lists_both_ltco_decisions',
          [item['content']['conclusion'] for item in conf['items'] if item['kind'] == 'ltco_confirmation'] == ['established', 'maintained'])
    flow.deny('co_agent', flow.command('m1b_propose_ltco', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(ltco_b['object_id'])['latest_revision']['payload'], 'title': 'Cross-scope constraint',
        'constraint_refs': [scope_constraint]}}), codes={'INVALID_REQUEST', 'FORBIDDEN'})
    check('ltco_cannot_reference_another_scopes_constraint', True)

    # ------------------------------------------------------ PCO / Mission
    pco_a = flow.draft_pco(ltco_a, 'scope-a', pco_period, constraints=[scope_constraint])
    pco_b = flow.draft_pco(ltco_b, 'scope-b', pco_period)
    mission_a = flow.draft_mission(pco_a, 'owner_a', 'scope-a', mission_period, evidence=[evidence],
                                   contributes=['scope-b'],
                                   dependencies=[{'kind': 'scope', 'scope_id': 'scope-b',
                                                  'needed_by': mission_period['end'], 'note': 'shared platform'}],
                                   constraints=[scope_constraint])
    mission_b = flow.draft_mission(pco_b, 'owner_b', 'scope-b', mission_period)
    flow.deny('co_agent', flow.command('m1b_draft_mission', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(mission_a['object_id'])['latest_revision']['payload'], 'contributes_to_scope_ids': ['scope-a']}}),
        codes={'INVALID_REQUEST'})
    check('mission_cannot_contribute_to_its_own_scope', True)

    window = flow.open_window([pco_a, pco_b], [mission_a, mission_b], [ltco_a, ltco_b], pco_period,
                              names=('dri_a', 'dri_b', 'owner_a', 'owner_b'))
    opinion = flow.comment(window, pco_a, 'dri_a')
    flow.close_window(window)
    resolve_params = {'title': 'Synthetic 0.5 candidate set', 'pcos': [], 'missions': [],
                      'dispositions': [{'review_record_id': opinion, 'decision': 'adopted', 'rationale': 'Adopted.'}],
                      'unresolved_differences': [], 'summary': 'Retain both PCO/Mission results.'}
    for ref in (pco_a, pco_b):
        resolve_params['pcos'].append({'object_id': ref['object_id'],
                                       'payload': deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])})
    for ref in (mission_a, mission_b):
        payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
        payload.pop('parent_pco_ref')
        resolve_params['missions'].append({'object_id': ref['object_id'], **payload})
    candidate = flow.resolve_window(window, resolve_params)
    candidate = flow.ref(candidate['object_id'])
    by_object = {ref['object_id']: ref for ref in candidate_refs(h, f, candidate)}
    check('candidate_missions_keep_contributions_and_dependencies',
          flow.object(mission_a['object_id'])['latest_revision']['payload']['contributes_to_scope_ids'] == ['scope-b'])
    flow.deny('owner_a', flow.command('m1b_commit_candidate', {
        'responsibility_ref': by_object[mission_a['object_id']], 'statement': 'An Owner tries to commit a Mission.'},
        oid=candidate['object_id']), codes={'INVALID_REQUEST', 'FORBIDDEN'})
    check('mission_owner_commitment_rejected_in_0_5', True)
    flow.deny('ceo', flow.command('m1b_activate_candidates', {'statement': 'Activate before commitments.', 'notes': []},
                                  oid=candidate['object_id']), codes={'INVALID_STATE'})
    flow.commit_candidate(candidate, by_object[pco_a['object_id']], 'dri_a')
    flow.commit_candidate(candidate, by_object[pco_b['object_id']], 'dri_b')
    activation = flow.activate_candidates(candidate)
    check('two_dri_commitments_activate_the_whole_set',
          len(h.sql(f, "SELECT * FROM gov_method_commitments WHERE scope_id=%s", (f['scope_id'],))) == 2
          and activation['execution_authority_created'] is False)
    check('owner_activation_recorded_on_missions',
          flow.object(mission_a['object_id'])['method_state']['owner_activation_record_id'] == activation['review_record_id'])

    # ---------------------------------------------------------------- State
    pco_state = flow.propose_state(by_object[pco_a['object_id']], state_period=period(-30, 0))
    state_obj = flow.object(pco_state['object_id'])
    check('generated_state_is_canonical_without_confirmation',
          state_obj['method_state']['phase'] == 'recorded' and state_obj['method_state']['canonical_ref'] == pco_state
          and state_obj['effective_revision_id'] == pco_state['revision_id'])
    denied = flow.clients['dri_a'].json('POST', '/v1/actions/prepare', flow.command(
        'method_confirm_state', {'reason': 'There is no confirmation in 0.5.'}, oid=pco_state['object_id']),
        expected={400, 422})
    check('state_confirmation_is_not_a_0_5_action', denied['error']['code'] == 'INVALID_REQUEST')
    mission_state = flow.propose_state(by_object[mission_a['object_id']], state_period=period(-30, 0), rag='green',
                                       summary='Evidence supports progress', evidence=[evidence], data_gaps=())
    ltco_state = flow.propose_state(ltco_a, state_period=period(-30, 0), drilldown=[pco_state, mission_state])
    check('upper_state_drills_down_to_lower_canonical_states',
          flow.object(ltco_state['object_id'])['latest_revision']['payload']['drilldown_refs'] == [pco_state, mission_state])

    # -------------------------------------------------------- Period Review
    review = flow.act('co_agent', 'm1b_generate_review', {'domain_id': f['domains']['company'], 'payload': {
        'review_id': 'synthetic-0-5-review', 'title': 'Synthetic 0.5 period review', 'period': period(-30, 0),
        'target_refs': [by_object[pco_a['object_id']], by_object[mission_a['object_id']]],
        'state_refs': [pco_state, mission_state], 'fact_refs': [],
        'findings': ['Canonical states are the reviewed basis.'], 'learnings': [], 'implications': [],
        'generation_version': 'controlled-review-1'}})['result']
    review = {k: review[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    next_period = period(1, 31)
    flow.deny('co_agent', flow.command('m1b_draft_pco', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(pco_a['object_id'])['latest_revision']['payload'], 'period': next_period,
        'period_review_ref': review, 'title': 'Cites an unconfirmed review'}}), codes={'STALE_DEPENDENCY'})
    confirmed = flow.confirm_review(review, findings=['CEO-adjusted finding.'])
    review = {k: confirmed[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    review_obj = flow.object(review['object_id'])
    check('ceo_confirmation_makes_the_review_effective_with_overrides',
          review_obj['method_state']['phase'] == 'confirmed' and review_obj['effective_revision_id'] == review['revision_id']
          and review_obj['latest_revision']['payload']['findings'] == ['CEO-adjusted finding.']
          and review_obj['method_state']['agent_generation_ref']['revision_id'] != review['revision_id'])
    flow.deny('co_agent', flow.command('m1b_draft_pco', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(pco_a['object_id'])['latest_revision']['payload'], 'period': next_period,
        'period_review_ref': None, 'title': 'Omits the confirmed review'}}), codes={'INVALID_REQUEST'})
    next_pco = flow.draft_pco(ltco_a, 'scope-a', next_period, period_review_ref=review, title='Next period PCO')
    check('next_pco_cites_the_confirmed_review',
          flow.object(next_pco['object_id'])['latest_revision']['payload']['period_review_ref'] == review)

    # ----------------------------------------------------------------- reads
    view = flow.company_view(pco_period)
    scope_a = next(s for s in view['scopes'] if s['scope_id'] == 'scope-a')
    check('company_view_groups_by_scope',
          scope_a['ltco']['object_id'] == ltco_a['object_id']
          and [p['object_id'] for p in scope_a['pcos']] == [pco_a['object_id']]
          and scope_a['missions'][0]['owner_effective_from'] is not None
          and [c['object_id'] for c in scope_a['constraints']] == [scope_constraint['object_id']]
          and [c['object_id'] for c in view['company_constraints']] == [company_constraint['object_id']])
    reviews = flow.clients['ceo'].json('GET', f"/v1/method/objects/{ltco_a['object_id']}/reviews")
    check('review_records_carry_effects',
          {item['effect'] for item in reviews['items']} == {'decision'})
    check('no_execution_or_acceptance_side_effects',
          not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))
    return {'checks': checks}


def run(h: MethodHarness, source: Path):
    f = seed_v05(h.env, h.private / 'identities.json', 'runtime-acceptance-method-v05')
    register_v05(h, source, f)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        ctx = happy_path(h, f, flow)
        public_json(h.output / 'summary.json', {
            'happy_path_passed': True, 'happy_path_checks': ctx['checks'],
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
            final = source_manifest(Path('src').resolve())
            if final != initial:
                if error is None:
                    error = RuntimeError('source changed during the acceptance run; rerun on a stable checkpoint')
                else:
                    print('WARNING: src changed during the run; original exception retained', flush=True)
    if error is not None:
        raise error


if __name__ == '__main__':
    main()
```

（`flow.deny` 的 `codes` 给出集合是因为 prepare 与 commit 两个入口在个别校验上返回的码不同，与 0.4 验收同口径。`expected={400, 422}` 用 `Client.json` 已有的参数。）

- [ ] **Step 4: README**

`acceptance/method_v05/README.md`：

```markdown
# Method 0.5 独立 API 验收

合成人与受控 Agent 的真实 HTTP／PostgreSQL 验收，覆盖 0.5 相对 0.4 的全部差异：按范围确认的 Constraint 与跨域引用拒绝、LTCO 结论（首次确立 / 维持）、只有 DRI 的承诺与 Owner 生效记录、生成即正式的状态与下钻、CEO 确认复盘与下期 PCO 承接、公司集合视图与确认记录投影。不证明真实模型行为，不代替 0.4 复跑。

前提与 `acceptance/method_v04/README.md` 相同（隔离 PG + MinIO、应用与迁移角色分离、`.runtime-acceptance/*/env.json`）。

```sh
.venv/bin/python -m acceptance.method_v05.run \
  --env-file .runtime-acceptance/method-local-database/env.json \
  --private .runtime-acceptance/method-v05-$(date +%Y%m%d-%H%M) \
  --output artifacts/runtime-acceptance/method-v05-$(date +%Y%m%d-%H%M)
```

`summary.json` 的 `runtime_method_v05_api_accepted` 只有在全部检查通过时才为 true。
```

- [ ] **Step 5: 跑 0.5 验收与 0.4 复跑**

```bash
python3 acceptance/runtime/infra.py up
.venv/bin/python -m acceptance.method_v05.run --env-file .runtime-acceptance/method-local-database/env.json --private .runtime-acceptance/method-v05-$(date +%Y%m%d-%H%M) --output artifacts/runtime-acceptance/method-v05-$(date +%Y%m%d-%H%M)
.venv/bin/python -m acceptance.method_v04.run --env-file .runtime-acceptance/method-local-database/env.json --private .runtime-acceptance/method-v04-rerun-$(date +%Y%m%d-%H%M) --output artifacts/runtime-acceptance/method-v04-rerun-$(date +%Y%m%d-%H%M)
```

Expected: 两个 `summary.json` 分别 `runtime_method_v05_api_accepted: true`、`runtime_method_v04_api_accepted: true`；0.4 的检查数与 `docs/method-04-delivery-report.md` 记录的一致。任一失败都不得改测试来迁就：回到对应任务修实现。

- [ ] **Step 6: 提交**

```bash
git add acceptance/method_v05
git commit -m "test(acceptance): add the tkos.method/0.5 independent HTTP chain

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

### Task 13: 本体登记对照测试、文档、OpenAPI 与冻结检查点

**Files:**
- Modify: `tests/test_ontology_registry.py`（加 0.5 字段核对与统计）
- Create: `docs/method-05-implementation-status.md`、`docs/method-v05-freeze-checkpoint.md`
- Modify: `README.md`（「Method 0.4」小节之后加「Method 0.5」小节；目录表加 `acceptance/method_v05/`）
- Regenerate: `docs/runtime-governance-openapi.json`、`docs/runtime-dashboard-openapi.json`；新建 `docs/runtime-method-v05-openapi.json`

- [ ] **Step 1: 写失败的登记对照测试**

`tests/test_ontology_registry.py` 末尾追加：

```python
def test_runtime_0_5_fields_exist_on_the_0_5_models_and_tallies_match_the_plan():
    from memory_service_runtime.governed import method_v05_models as v5
    data = registry()
    paths = {kind: model_paths(model) for kind, model in v5.PAYLOAD_MODELS.items()}
    for item in data["objects"]:
        runtime = item["runtime_0_5"]
        kind = runtime.get("object_type")
        if kind in paths:
            for field in runtime.get("fields", []):
                assert field in paths[kind], (item["id"], field)
    for kind in ("Constraint", "LTCO", "PCO", "Mission", "OperatingState", "PeriodReview"):
        registered = next(o for o in data["objects"] if o["runtime_0_5"].get("object_type") == kind)
        assert registered["runtime_0_5"]["status"] in {"exists", "partial"}
    tally = lambda items, key: {s: sum(1 for i in items if i[key]["status"] == s) for s in ("exists", "partial", "missing")}
    assert tally(data["objects"], "runtime_0_5") == {"exists": 9, "partial": 7, "missing": 4}
    assert tally(data["relations"], "runtime_0_5") == {"exists": 27, "partial": 4, "missing": 2}
    assert all(g["runtime_0_5"]["status"] == "exists" for g in data["gates"])
```

- [ ] **Step 2: 运行；若字段名与模型不符，改登记而不是改模型，然后重新导出、重新 pin**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests/test_ontology_registry.py -q
```

Expected: `4 passed`。若登记要改：改 YAML → `PYTHONPATH=src .venv/bin/python scripts/export_ontology_registry.py docs/contracts/ontology-registry-0.7.yaml docs/contracts/ontology-registry-0.7.json` → `shasum -a 256 docs/contracts/ontology-registry-0.7.json` → 更新 `method_v05_profile.ONTOLOGY_REGISTRY_SHA256`、重新生成 `method-profile-0.5.json`（Task 1 Step 4）、更新 `0029_method_v05.sql` 里的登记哈希（迁移尚未在任何非隔离环境应用，本计划内允许改；若已应用则新增 `0030_method_v05_registry_repin.sql`）→ 重跑 `tests/test_method_v05_models.py` 与 `tests/test_migrations.py`。

- [ ] **Step 3: 状态文档**

创建 `docs/method-05-implementation-status.md`（数字在验收后填实际值，不写估计）：

```markdown
# Method 0.5（本体 v0.7 对齐）实施与独立验收状态

日期：<执行日期>。分支：<分支名>。仅修改 Runtime 与治理工作台，未修改 Clark。

## 依据

《TKOS 本体结构 v0.7（M1 范围）｜CEO 对齐稿 2026-09-22》（docx KuU8d7rq6oozKDxtZqkcHI0nnJa）、本体登记 tkos.ontology-registry 0.7.1（docs/contracts/ontology-registry-0.7.json，SHA256 <REGISTRY_SHA256>）、契约 docs/contracts/tkos-method-0.5.md（SHA256 <CONTRACT_SHA256>）。

## 已交付

| 批次 | 内容 |
| --- | --- |
| 契约与钉定 | 0.5 契约、profile 双重钉定（契约字节 + 本体登记字节）、迁移 0029、注册表 44 动作 / 16 对象类型 |
| Constraint | 对象类型、登记 / 修订 / 按范围确认、LTCO / PCO / Mission 的约束引用 |
| LTCO 与复盘 | 审视结论（首次确立 / 修订 / 维持）、CEO 确认复盘、下期 PCO 承接 |
| Mission 与承诺 | 贡献 / 依赖 / 资源需求、责任域 DRI 唯一承诺、Owner 生效记录 |
| Operating State | 起止时间、生成即正式、下钻引用 |
| 读取 | 公司集合视图、确认记录投影、review effect、五个集合列表 |
| 工作台 | 0.5 规则版本、Constraint 读法、三个人工门表单 |

## 本体登记对照（tests/test_ontology_registry.py）

一级对象 runtime_0_5：exists <n> / partial <n> / missing <n>；关系：exists <n> / partial <n> / missing <n>；人工门 4 / 4。

## 独立验收结果

- Python：<n> passed / <n> skipped（应用角色）；迁移到 0029 与空重放 <n> passed（owner 角色）。
- 前端：<n> 个文件、<n> 项通过；typecheck、构建与资产校验通过。
- HTTP：acceptance/method_v05 <n> 项检查通过；acceptance/method_v04 复跑 <n> 项通过，与 0.4 交付报告一致。

## 未验证

真实业务模型、Clark 接线、浏览器人类链、生产迁移与部署均未运行。CEO 对 Mission 最终确认人的裁决未定，本版按 DRI。
```

- [ ] **Step 4: README 与 OpenAPI**

`README.md`：在「## Method 0.4：正式链收口与独立来源」小节之前加：

```markdown
## Method 0.5：本体 v0.7 对齐

`tkos.method/0.5` 按《TKOS 本体结构 v0.7（M1 范围）》把本期 M1 对象落进正式链：Constraint 一级对象与按范围确认、LTCO 审视结论、CEO 确认复盘并由下期 PCO 承接、Mission 的贡献／依赖／资源与责任域 DRI 唯一承诺、生成即正式的带起止时间状态，以及公司集合视图与确认记录投影。契约见 [tkos-method-0.5.md](docs/contracts/tkos-method-0.5.md)，profile 同时钉定 [本体登记 0.7.1](docs/contracts/ontology-registry-0.7.json)；动作注册表见 [0.5 注册表](docs/runtime-method-registry-0.5.json)；实施与验收状态见 [Method 0.5 状态](docs/method-05-implementation-status.md)；复跑入口在 `acceptance/method_v05/`。迁移头到 0029；0.1–0.4 对象保留原协议绑定与原生效规则。
```

目录表加一行 `| \`acceptance/method_v05/\` | Method 0.5 本体对齐链隔离验收 |`。

导出 OpenAPI：

```bash
PYTHONPATH=src .venv/bin/python scripts/export_governance_openapi.py
PYTHONPATH=src .venv/bin/python scripts/export_dashboard_openapi.py
MEMORY_TENANT=openapi-export MEMORY_ORG=openapi-export DATABASE_URL=postgresql://unused PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from memory_service_app.main import app
spec = app.openapi()
paths = {p: v for p, v in spec['paths'].items() if p.startswith('/v1/method') or p in {'/v1/actions', '/v1/actions/prepare', '/v1/objects/{object_id}'}}
open('docs/runtime-method-v05-openapi.json', 'w').write(json.dumps({**spec, 'paths': paths}, ensure_ascii=False, indent=2) + '\n')
print(sorted(paths))
PY
```

Expected: 输出里含 `/v1/method/company-view` 与 `/v1/method/objects/{object_id}/confirmations`。

- [ ] **Step 5: 冻结检查点**

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
import hashlib, subprocess
from datetime import datetime, timezone
files = ['src/memory_service_runtime/governed/method_v05.py', 'src/memory_service_runtime/governed/method_v05_models.py',
         'src/memory_service_runtime/governed/method_v05_profile.py', 'src/memory_service_runtime/governed/method_v04.py',
         'src/memory_service_runtime/governed/method_service.py', 'src/memory_service_runtime/governed/method_readers.py',
         'src/memory_service_runtime/governed/governance.py', 'src/memory_service_runtime/governed/routes.py',
         'src/memory_service_app/migrations/0029_method_v05.sql', 'docs/contracts/tkos-method-0.5.md',
         'docs/contracts/method-profile-0.5.json', 'docs/contracts/ontology-registry-0.7.json',
         'docs/runtime-method-registry-0.5.json', 'workbench/dashboard/src/lib/ontology.ts',
         'workbench/dashboard/src/MethodActions.tsx']
head = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
lines = ['# Method 0.5 freeze checkpoint', '', f'- 时间（UTC）: {datetime.now(timezone.utc).isoformat()}', f'- HEAD: `{head}`', '',
         '| 文件 | SHA256 |', '|---|---|']
for path in files:
    lines.append(f'| `{path}` | `{hashlib.sha256(open(path, "rb").read()).hexdigest()}` |')
open('docs/method-v05-freeze-checkpoint.md', 'w').write('\n'.join(lines) + '\n')
PY
```

- [ ] **Step 6: 全量回归后提交**

```bash
DATABASE_URL=$DATABASE_URL PYTHONPATH=src .venv/bin/python -m pytest tests -q --ignore=tests/dashboard
PYTHONPATH=src .venv/bin/python -m pytest tests/dashboard --confcutdir=tests/dashboard -q
cd workbench/dashboard && npm test && cd ../..
git add tests/test_ontology_registry.py docs/method-05-implementation-status.md docs/method-v05-freeze-checkpoint.md README.md docs/runtime-governance-openapi.json docs/runtime-dashboard-openapi.json docs/runtime-method-v05-openapi.json docs/contracts docs/runtime-method-registry-0.5.json src/memory_service_runtime/governed/method_v05_profile.py src/memory_service_app/migrations
git commit -m "docs(method): record the 0.5 ontology-alignment delivery, registry pin and freeze checkpoint

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Expected: Python 总数 = 改动前 941 passed 加本计划新增用例数，skipped 数不增；前端 184 passed 加 4。

## 后续版本待办（不在本计划内）

- `gov_method_relations` 关系投影表：按登记 `relations` 在写入时物化钉版本的边，替换 `relation_items` 的 JSONB 扫描。
- Mission 最终确认人若 CEO 裁决为 Owner：0.6 恢复 Owner 承诺（可直接复用 0.4 的 `_required_committers`）。
- Mission Owner 作为 `gov_role_assignments` 行（角色枚举需加 `MISSION_OWNER`，CHECK 约束迁移）。
- M2 / M3 / MF 对象：Mission Play、人 + Agent Plan、Mission Result、Finding、Management Issue；Period Review 消费 Mission Result。
- Signal 四类来源与 M1-A 重开门；工作台里登记 / 修订 Constraint 的表单；本体登记驱动的地图与关系目录。
- 版本号升到 0.5.0 与离线发布包，按 v0.4.0 的发布流程另开 PR。

## 自检记录（写计划时执行）

- 规格覆盖：v0.7 表 A2 二十个对象里本期落地的十一个（Company 只读入、Strategy、Architecture、Battlefield / Domain、Role Assignment、Constraint、LTCO、PCO、Mission、Operating State、Period Review、Evidence 只读入、Traceability Record 部分）都有任务落点；表 B 三十三条关系的 0.5 目标状态写在 Task 0 的登记里，27 条 exists 由 Task 5–10 交付；四道人工门对应 Task 6、7、8 与 0.4 已有的激活。第四节待定项（Mission 确认人、War Map、指派规则、责任域级复盘）都不改代码，记在决策点与后续待办。
- 占位符扫描：全文没有 TBD / TODO / "类似 Task N"；每个代码步骤都给出完整代码；两个 `<…SHA256>` 占位符是执行时计算的值，Task 1 Step 2 说明了来源。
- 类型一致性：`COLLECTORS` / `RUNNERS` 的键与 Task 2 的 `ACTION_PARAMS` 键一致；`_check_constraint_refs(e, refs, *, scope_id, mission_ref=None)` 在 Task 5 定义、Task 6–8 调用同一签名；`required_committers` 在 Task 4 定义别名、Task 8 替换同名；`light(conn, ctx)` 在 Task 4 定义、Task 5 与 8 使用；`review_effect` 在 Task 10 定义并被 Task 12 的 `review_records_carry_effects` 检查；`FORMAL_GOVERNANCE_VERSIONS` 只在 Task 4 定义；`state.owner_activation_record_id` 在 Task 8 写、Task 10 读、Task 12 断言。
- 已知风险：`_LightExecution` 参数化触碰了冻结的 `method_v04.py`，靠 0.4 单元回归与 `acceptance/method_v04` 复跑（Task 12 Step 5）证明行为不变；`governance.py` 的 `_availability` 改造涉及三处调用，Task 4 给了 facade，执行时用 `grep -n "method_v04" src/memory_service_runtime/governed/governance.py` 确认没有漏改。
