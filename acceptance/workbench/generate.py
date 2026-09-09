"""本体与记忆工作台 v0.1 演练数据生成入口。

在隔离的 synthetic scope 中通过真实 HTTP Action 产生一条完整业务链：
双方承诺（BC/EC 握手+激活）→ MF 反馈路由并进入跟进（investigating，不关闭）
→ WorkItem 冻结基线 → DRI 接受 → 证据经 HTTP 上传 → 交付 v1 提交 →
独立验收人退回（changes_requested）→ 补充证据再提交 v2 → 验收通过。
Outcome 保持未评估（not_assessed），MF 保持跟进中，交付最终通过。

随后以真实 HTTP 调用七个 workbench 读取接口并输出脱敏 manifest
（仅对象/revision/receipt/证据 hash 标识；凭据只留在 .runtime-acceptance 私有目录）。

前提：测试基础设施由 Codex 管理；本入口不启动/停止 Docker，
只消费 .runtime-acceptance/env.json 并复用 acceptance/runtime 的 Harness/Scenario。

用法：
    PYTHONPATH=src:. python acceptance/workbench/generate.py [--run-id workbench-xxx]

每次运行都新建隔离 synthetic scope；--run-id 只允许安全文件名
（字母数字开头，仅含字母、数字、'-'、'_'，最长 64 字符）。

入口保护是程序执行的检查，不是文字声明：seed 任何数据之前，先通过
`acceptance.runtime.infra.load_environment()` 在程序内部消费既有 harness
配置（不读取、不打印其中秘密），并复用独立 QA 入口的
`acceptance.workbench.independent.validate_environment(environment, parse_dsn)`
（parse_dsn 为 psycopg.conninfo.conninfo_to_dict）实际拒绝非本机回环地址、
非 tkos_runtime_acceptance 数据库及非本机对象存储；随后用
`independent.reserve_paths(run_id)` 原子保留全新 private/output 运行目录，
拒绝已存在目录或符号链接。任何一项不满足都在 create_fixture 之前失败，
不会发生 seed 或进程启动。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from acceptance.runtime.client import sanitized, utc_now
from acceptance.runtime.harness import Harness

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def drive_business_chain(scenario):
    """全部业务变迁只经认证 HTTP Action 产生；返回演练标识集合（无凭据）。"""
    s = scenario
    outcome = s.outcome  # bootstrap 的已确认 CompanyOutcome 起点

    bc, bc_signatures = s.signed("BusinessCommitment", outcome)
    bc_receipt = s.activate(bc, bc_signatures, outcome)
    ec, ec_signatures = s.signed("ExecutionCommitment", bc)
    ec_receipt = s.activate(ec, ec_signatures, bc)

    feedback = s.feedback_investigation(f"Workbench drill feedback {uuid.uuid4().hex[:8]}")
    feedback_revision = s.object(feedback)["latest_revision_id"]

    work_payload = {
        "title": f"Workbench drill delivery {uuid.uuid4().hex[:8]}",
        "execution_commitment_ref": s.upstream(ec),
        "dri_assignment_id": s.f["actors"]["mission_dri"]["assignment_id"],
        "acceptor_assignment_id": s.f["actors"]["verifier"]["assignment_id"],
        "acceptance_criteria": [
            {"criterion_id": "complete", "description": "All raw sources are included"},
            {"criterion_id": "consistent", "description": "Values reconcile with the supplied sources"},
        ],
        "feedback_ref": {"object_id": feedback, "revision_id": feedback_revision},
    }
    work_item = s.create("WorkItem", work_payload)
    baseline_revision = s.object(work_item)["latest_revision_id"]

    def upload(label, content: bytes):
        return s.mission.json("POST", "/v1/evidence-assets", {
            "domain_id": s.domain, "title": label,
            "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain",
        })

    evidence_v1 = upload("Drill evidence v1", b"Delivery v1 package; raw source absent.\n")
    s.act(s.mission, "accept_work_item", {}, oid=work_item, prepare=True)
    submit1 = s.act(s.mission, "submit_deliverable", {
        "title": "Drill delivery v1", "summary": "First package",
        "evidence_revision_ids": [evidence_v1["revision_id"]],
    }, oid=work_item, prepare=True)["result"]

    def review(submission, result):
        failed = result == "changes_requested"
        return s.act(s.verifier, "review_deliverable", {
            "deliverable_revision_id": submission["deliverable_revision_id"],
            "delivery_payload_hash": submission["payload_hash"],
            "verification_result": result,
            "criterion_results": [
                {"criterion_id": "complete", "result": "failed" if failed else "passed",
                 "note": "Raw source missing" if failed else "Source package complete"},
                {"criterion_id": "consistent", "result": "passed", "note": "Values reconcile"},
            ],
            "review_note": "Supplement the raw source" if failed else "All frozen criteria independently checked",
        }, oid=work_item, prepare=True)

    returned = review(submit1, "changes_requested")
    evidence_v2 = upload("Drill evidence v2", b"Delivery v2 package with complete raw source.\n")
    submit2 = s.act(s.mission, "submit_deliverable", {
        "title": "Drill delivery v2", "summary": "Raw source added after return",
        "evidence_revision_ids": [evidence_v2["revision_id"]],
        "responds_to_acceptance_id": returned["result"]["acceptance_id"],
    }, oid=work_item, prepare=True)["result"]
    accepted = review(submit2, "accepted")

    # 演练终态只通过 HTTP 读取断言，不写库。
    assert s.object(work_item)["lifecycle_status"] == "delivery_accepted"
    deliverable = submit2["deliverable_object_id"]
    assert s.object(deliverable)["lifecycle_status"] == "accepted"
    assert s.object(outcome)["outcome_achievement"] == "not_assessed", "Outcome 必须保持未评估"
    assert s.object(feedback)["lifecycle_status"] == "investigating", "MF 必须保持跟进中"

    return {
        "domain_id": s.domain,
        "outcome": {"object_id": outcome, "state": "confirmed, not_assessed"},
        "business_commitment": {"object_id": bc, "activation_receipt_id": bc_receipt["receipt_id"]},
        "execution_commitment": {"object_id": ec, "activation_receipt_id": ec_receipt["receipt_id"]},
        "feedback": {"object_id": feedback, "state": "investigating"},
        "work_item": {"object_id": work_item, "baseline_revision_id": baseline_revision,
                      "state": "delivery_accepted"},
        "deliverable": {"object_id": deliverable,
                        "revision_ids": [submit1["deliverable_revision_id"], submit2["deliverable_revision_id"]],
                        "payload_hashes": [submit1["payload_hash"], submit2["payload_hash"]],
                        "state": "accepted"},
        "evidence": [{"object_id": ev["object_id"], "revision_id": ev["revision_id"], "sha256": ev["sha256"]}
                     for ev in (evidence_v1, evidence_v2)],
        "delivery_acceptance_ids": [returned["result"]["acceptance_id"], accepted["result"]["acceptance_id"]],
        "receipts": [{"receipt_id": r["receipt_id"], "action_type": r["action_type"]} for r in s.receipts],
    }


def exercise_read_api(scenario, ids):
    """以真实 HTTP 调用四页所需的读取接口，返回脱敏后的响应样本。"""
    s = scenario
    domain, work = ids["domain_id"], ids["work_item"]["object_id"]
    deliverable = ids["deliverable"]["object_id"]

    pages = []
    cursor = None
    for _ in range(20):
        suffix = f"&cursor={cursor}" if cursor else ""
        page = s.ceo.json("GET", f"/v1/objects?domain_id={domain}&limit=2{suffix}")
        pages.append({"items": page["items"], "next_cursor": page["next_cursor"]})
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert not cursor, "pagination did not terminate"

    reads = {
        "object_types": s.ceo.json("GET", "/v1/object-types"),
        "domains": s.ceo.json("GET", "/v1/domains"),
        "objects_paginated": pages,
        "work_item_revisions": s.ceo.json("GET", f"/v1/objects/{work}/revisions"),
        "deliverable_relations": s.ceo.json("GET", f"/v1/objects/{deliverable}/relations"),
        "work_item_action_receipts": s.ceo.json("GET", f"/v1/objects/{work}/action-receipts"),
        "work_item_responsibility": s.ceo.json("GET", f"/v1/objects/{work}/responsibility"),
        "existing_object_detail": s.ceo.json("GET", f"/v1/objects/{work}"),
        "existing_receipt_detail": s.ceo.json(
            "GET", f"/v1/action-receipts/{ids['receipts'][-1]['receipt_id']}"),
    }
    return sanitized(reads)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="显式 run_id（安全文件名）；缺省生成 workbench-<random>")
    args = parser.parse_args()

    run_id = args.run_id or f"workbench-{uuid.uuid4().hex[:12]}"
    if not RUN_ID_PATTERN.fullmatch(run_id):
        parser.error("--run-id 必须是安全文件名：字母数字开头，仅含字母数字、'-'、'_'，最长 64 字符")

    # 程序执行的入口保护：在任何 seed/进程启动之前验证连接目标并原子保留目录。
    # 配置仅在内存中校验；validate_environment 的错误消息不含 DSN 内容。
    from psycopg.conninfo import conninfo_to_dict
    from acceptance.runtime import infra
    from acceptance.workbench.independent import reserve_paths, validate_environment

    try:
        validate_environment(infra.load_environment(), conninfo_to_dict)
        reserve_paths(run_id)
    except ValueError as exc:
        parser.error(str(exc))

    from acceptance.runtime.seed import create_fixture
    run_id, fixture_file = create_fixture(run_id)

    from acceptance.runtime.run import Scenario
    harness = Harness(run_id=run_id)
    fixture = json.loads(fixture_file.read_text())
    scenario = None
    try:
        scenario = Scenario(harness, fixture)
        ids = drive_business_chain(scenario)
        reads = exercise_read_api(scenario, ids)
        manifest = {
            "run_id": run_id,
            "generated_at": utc_now(),
            "environment": "isolated local HTTP API + PostgreSQL + versioned object store",
            "final_states": {"delivery": "delivery_accepted/accepted",
                             "outcome": "not_assessed", "mf": "investigating"},
            "identifiers": ids,
            "read_api_samples": reads,
            "page_mapping": {
                "对象与关系": ["/v1/object-types", "/v1/domains", "/v1/objects",
                             "/v1/objects/{id}/relations"],
                "实例详情": ["/v1/objects/{id}", "/v1/objects/{id}/revisions",
                           "/v1/objects/{id}/responsibility"],
                "动作与回执": ["/v1/objects/{id}/action-receipts", "/v1/action-receipts/{id}"],
                "Context Pack": ["/v1/context-packs", "/v1/context-packs/{id}"],
            },
        }
        path = harness.output / "workbench-manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"run_id": run_id, "manifest": str(path),
                          "transcript": str(harness.log.path)}, indent=2))
    finally:
        if scenario:
            for client in scenario.clients.values():
                client.close()
        harness.stop_all()


if __name__ == "__main__":
    main()
