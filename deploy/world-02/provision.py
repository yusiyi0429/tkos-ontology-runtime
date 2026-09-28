"""world-02 实验实例的供给：按 spec 建 scope、域、主体与角色指派，并生成控制面装 tkos.world/0.2 要用的策略文件。

用 owner 连接串（migrate 服务的 DATABASE_URL）运行，和验收夹具、实验播种一样的合成控制面写法；只用于实验实例，
不是生产身份管理工具。凭证不在这里签，用 tkos-governed-control issue-credential。

用法：python provision.py <spec.json> <输出目录>
输出：ids.json（scope、域、主体 id）、scope-policy.json（默认契约 tkos.world/0.2）、每个域一份
activation-<域id>.json（与 0.2 验收一致：只开支持登记里已实现的动作，门动作按登记的门表，其余对全部 world 角色
开放，谁能做什么由服务代码的责任人规则拦下）。输出目录里已有 ids.json 就什么都不做：重跑会建出第二个 scope。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import uuid

DOCS = Path("/opt/tkos/docs")
WORLD_ROLES = ["AGENT", "CEO", "DOMAIN_DRI", "IC", "MISSION_DRI", "OWNER", "VERIFIER"]
KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # 主体键也是凭证文件名 out/<键>.token


def check_spec(spec: object) -> None:
    """spec 不成立就抛 ValueError，列出全部问题；什么都没写库之前调用。"""
    problems = []
    if not isinstance(spec, dict) or set(spec) - {"tenant_id", "domains", "principals", "notes"}:
        raise ValueError("spec 只能有 tenant_id、domains、principals、notes 四项")
    if not isinstance(spec.get("tenant_id"), str) or not spec["tenant_id"].strip():
        problems.append("tenant_id 必须是非空文本")
    domains = spec.get("domains")
    if not isinstance(domains, dict) or "company" not in domains:
        problems.append("domains 必须含公司域 company")
        domains = domains if isinstance(domains, dict) else {}
    for key, name in domains.items():
        if not KEY.match(key) or not isinstance(name, str) or not name.strip():
            problems.append(f"域 {key!r}：键为小写字母、数字与连字符，名称非空")
    principals = spec.get("principals")
    if not isinstance(principals, dict) or not principals:
        problems.append("principals 不能为空")
        principals = {}
    for key, principal in principals.items():
        where = f"主体 {key!r}"
        if not KEY.match(key):
            problems.append(f"{where}：键为小写字母、数字与连字符（它也是凭证文件名）")
        if not isinstance(principal, dict) or set(principal) != {"type", "display_name", "roles"}:
            problems.append(f"{where}：只能且必须有 type、display_name、roles")
            continue
        if principal["type"] not in ("human", "agent"):
            problems.append(f"{where}：type 是 human 或 agent")
        if not isinstance(principal["display_name"], str) or not principal["display_name"].strip():
            problems.append(f"{where}：display_name 非空")
        roles = principal["roles"]
        if not isinstance(roles, dict) or not roles:
            problems.append(f"{where}：roles 至少在一个域有角色")
            continue
        for domain, names in roles.items():
            if domain not in domains:
                problems.append(f"{where}：域 {domain!r} 不在 domains 里")
            if (not isinstance(names, list) or not names or len(set(names)) != len(names)
                    or any(name not in WORLD_ROLES for name in names)):
                problems.append(f"{where} 在 {domain!r}：角色是不重复的 world 角色 {WORLD_ROLES}")
            elif names != ["AGENT"] if principal["type"] == "agent" else "AGENT" in names:
                problems.append(f"{where} 在 {domain!r}：Agent 只持 AGENT，人不持 AGENT")
    if problems:
        raise ValueError("；".join(problems))


def activation_policy(registry: dict, support: dict, note: str) -> dict:
    """0.2 验收（acceptance/world_v02/fixture.py 的 action_roles）同一规则：支持登记里已实现的动作，门动作取登记的
    门表（ADR-0005），其余对全部 world 角色开放。"""
    implemented = set(support["actions"])
    action_roles = {a["action"]: list(a["gate_roles"] or WORLD_ROLES)
                    for a in registry["actions"] if a["action"] in implemented}
    if set(action_roles) != implemented:
        raise ValueError(f"支持登记里有登记没有的动作：{sorted(implemented - set(action_roles))}")
    return {"action_roles": action_roles, "notes": note}


def scope_policy(profile: dict, notes: str) -> dict:
    """scope 默认策略：默认协议 tkos.world、默认契约 0.2，钉 0.2 profile。"""
    if (profile.get("profile_core_schema_version"), profile["action_contract_ref"].get("revision")) != (
            "tkos.world-profile/0.2", "0.2"):
        raise ValueError("profile 不是 tkos.world 0.2 的 profile")
    return {"default_protocol": "tkos.world", "default_contract_version": "tkos.world/0.2",
            "allow_legacy_create": False, "record_origin": "synthetic",
            "default_profile_ref": {"profile_id": profile["profile_id"], "revision": profile["revision"]},
            "experimental": True, "notes": notes}


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    if (out / "ids.json").exists():
        print(f"{out / 'ids.json'} 已存在，跳过供给（重建要先 compose down -v 并删掉输出目录）")
        return
    check_spec(spec)
    load = lambda name: json.loads((DOCS / name).read_text(encoding="utf-8"))  # noqa: E731
    registry, support = load("contracts/world-registry-0.2.json"), load("runtime-world-support-0.2.json")
    policy = scope_policy(load("contracts/world-profile-0.2.json"), spec.get("notes", "world-02"))
    ids = {"scope_id": str(uuid.uuid4()), "tenant_id": spec["tenant_id"], "company_id": str(uuid.uuid4()),
           "contract_version": "tkos.world/0.2",
           "domains": {key: str(uuid.uuid4()) for key in spec["domains"]}, "principals": {}}
    activation = {key: activation_policy(registry, support, f"world-02 {key}：0.2 已实现的动作，门动作按登记门表，"
                                                             "其余对全部 world 角色开放")
                  for key in spec["domains"]}

    import psycopg  # 只在写库时需要；纯逻辑（上面三个函数）的测试不连库

    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
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
        # 文件在提交之前写：写不成就整个回滚，不留下没有 ids.json 的 scope。
        for key, domain_id in ids["domains"].items():
            (out / f"activation-{domain_id}.json").write_text(
                json.dumps(activation[key], ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "scope-policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"scope_id": ids["scope_id"], "domains": ids["domains"],
                      "principals": {k: v["principal_id"] for k, v in ids["principals"].items()}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
