"""world-lab 联调实例的供给：按 spec 建 scope、域、主体与角色指派，并生成控制面要用的策略文件。

用 owner 连接串（migrate 服务的 DATABASE_URL）运行，和验收夹具、实验播种一样的合成控制面写法；
只用于联调实例，不是生产身份管理工具。凭证不在这里签，用 tkos-governed-control issue-credential。

用法：python provision.py <spec.json> <输出目录>
输出：ids.json（scope、域、主体 id）、scope-policy.json、activation-<域id>.json。
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import psycopg

DOCS = Path("/opt/tkos/docs")
ROLES = ["AGENT", "CEO", "DOMAIN_DRI", "IC", "MISSION_DRI", "OWNER", "VERIFIER"]


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    dsn = os.environ["DATABASE_URL"]
    ids = {"scope_id": str(uuid.uuid4()), "tenant_id": spec["tenant_id"], "company_id": str(uuid.uuid4()),
           "domains": {key: str(uuid.uuid4()) for key in spec["domains"]}, "principals": {}}
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (ids["scope_id"],))
        conn.execute("INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES (%s,%s,%s)",
                     (ids["scope_id"], ids["tenant_id"], ids["company_id"]))
        for key, name in spec["domains"].items():
            conn.execute("INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)",
                         (ids["domains"][key], ids["scope_id"], name))
        for key, principal in spec["principals"].items():
            principal_id = str(uuid.uuid4())
            conn.execute("INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)",
                         (principal_id, ids["scope_id"], principal["type"], principal["display_name"]))
            assignments = []
            for domain_key, roles in principal["roles"].items():
                for role in roles:
                    assignment_id = str(uuid.uuid4())
                    conn.execute("INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role)"
                                 " VALUES (%s,%s,%s,%s,%s)",
                                 (assignment_id, ids["scope_id"], principal_id, ids["domains"][domain_key], role))
                    assignments.append({"domain": domain_key, "role": role, "assignment_id": assignment_id})
            ids["principals"][key] = {"principal_id": principal_id, "type": principal["type"],
                                      "display_name": principal["display_name"], "assignments": assignments}
    registry = json.loads((DOCS / "contracts/world-registry-0.1.json").read_text(encoding="utf-8"))
    action_roles = {a["action"]: (a["gate_roles"] if a.get("gate_roles") else ROLES) for a in registry["actions"]}
    for key, domain_id in ids["domains"].items():
        (out / f"activation-{domain_id}.json").write_text(json.dumps(
            {"action_roles": action_roles, "notes": f"world-lab {key}：门动作按登记门表，其余动作对全部角色开放"},
            ensure_ascii=False, indent=1), encoding="utf-8")
    profile = json.loads((DOCS / "contracts/world-profile-0.1.json").read_text(encoding="utf-8"))
    (out / "scope-policy.json").write_text(json.dumps({
        "default_protocol": "tkos.world", "default_contract_version": "tkos.world/0.1",
        "allow_legacy_create": False, "record_origin": "synthetic",
        "default_profile_ref": {"profile_id": profile["profile_id"], "revision": profile["revision"]},
        "experimental": True, "notes": spec.get("notes", "world-lab")}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"scope_id": ids["scope_id"], "domains": ids["domains"],
                      "principals": {k: v["principal_id"] for k, v in ids["principals"].items()}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
