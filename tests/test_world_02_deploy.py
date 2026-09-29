"""deploy/world-02 的纯逻辑：spec 校验、0.2 策略生成、env 模板与构建脚本的 wheel 规则。不连库、不起容器。"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "world-02"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"world_02_{name}", DEPLOY / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provision, smoke, build = load("provision"), load("smoke"), load("build_images")
EXAMPLE = json.loads((DEPLOY / "spec.example.json").read_text(encoding="utf-8"))
EO_EXAMPLE = json.loads((DEPLOY / "spec.eo.example.json").read_text(encoding="utf-8"))
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
SUPPORT = json.loads((ROOT / "docs/runtime-world-support-0.2.json").read_text(encoding="utf-8"))


def test_the_example_spec_is_valid_and_carries_what_the_smoke_uses() -> None:
    provision.check_spec(EXAMPLE)
    assert set(smoke.KEYS) == set(EXAMPLE["principals"]) and set(smoke.DOMAINS) == set(EXAMPLE["domains"])
    agents = {key for key, p in EXAMPLE["principals"].items() if p["type"] == "agent"}
    assert agents == {"tianshu", "eo-coagent", "exec-agent"}
    assert set(EXAMPLE["principals"]["tianshu"]["roles"]) == set(EXAMPLE["domains"])


def test_the_experiment_scope_roster_is_the_company_and_eo_with_placeholder_names() -> None:
    provision.check_spec(EO_EXAMPLE)
    assert set(EO_EXAMPLE["domains"]) == {"company", "eo"}
    assert {key: p["roles"] for key, p in EO_EXAMPLE["principals"].items()} == {
        "ceo": {"company": ["CEO"], "eo": ["CEO"]}, "eo-dri": {"eo": ["DOMAIN_DRI", "OWNER", "IC"]},
        "eo-owner": {"eo": ["OWNER", "IC"]}, "tianshu": {"company": ["AGENT"], "eo": ["AGENT"]},
        "eo-coagent": {"eo": ["AGENT"]}, "exec-agent": {"eo": ["AGENT"]}}
    humans = {key: p["display_name"] for key, p in EO_EXAMPLE["principals"].items() if p["type"] == "human"}
    assert humans == {"ceo": "CEO", "eo-dri": "E&O DRI", "eo-owner": "E&O Mission Owner"}  # 真名只在主机上填


@pytest.mark.parametrize(("change", "says"), [
    (lambda s: s["domains"].pop("company"), "company"),
    (lambda s: s["principals"]["ceo"]["roles"].update({"growth": ["CEO"]}), "不在 domains"),
    (lambda s: s["principals"]["eo-ic"]["roles"].update({"eo": ["IC", "IC"]}), "不重复"),
    (lambda s: s["principals"]["eo-ic"]["roles"].update({"eo": ["BOSS"]}), "world 角色"),
    (lambda s: s["principals"]["tianshu"]["roles"].update({"eo": ["AGENT", "DOMAIN_DRI"]}), "Agent 只持 AGENT"),
    (lambda s: s["principals"]["eo-ic"]["roles"].update({"eo": ["IC", "AGENT"]}), "人不持 AGENT"),
    (lambda s: s["principals"].update({"Bad Key": s["principals"]["eo-ic"]}), "凭证文件名"),
    (lambda s: s["principals"]["eo-ic"].update({"email": "x"}), "只能且必须"),
    (lambda s: s.update({"tenant_id": " "}), "tenant_id"),
    (lambda s: s.update({"extra": 1}), "四项"),
])
def test_a_spec_that_does_not_hold_is_refused_before_anything_is_written(change, says) -> None:
    spec = deepcopy(EXAMPLE)
    change(spec)
    with pytest.raises(ValueError, match=says):
        provision.check_spec(spec)


def test_the_activation_policy_is_the_one_the_0_2_acceptance_installs() -> None:
    from acceptance.world_v02.fixture import action_roles
    policy = provision.activation_policy(REGISTRY, SUPPORT, "note")
    assert policy == {"action_roles": action_roles(), "notes": "note"}
    roles = policy["action_roles"]
    assert set(roles) == set(SUPPORT["actions"])
    assert roles["world_confirm_period_goal"] == ["CEO"] and roles["world_commit_mission"] == ["OWNER"]
    assert roles["world_confirm_mission"] == ["DOMAIN_DRI"] and roles["world_mark_core_battle"] == ["CEO"]
    assert roles["world_start"] == roles["world_grant_delegation"] == provision.WORLD_ROLES


def test_an_action_the_registry_does_not_list_is_not_opened() -> None:
    with pytest.raises(ValueError, match="world_fly"):
        provision.activation_policy(REGISTRY, {**SUPPORT, "actions": [*SUPPORT["actions"], "world_fly"]}, "note")


def test_the_scope_policy_defaults_to_world_0_2_and_pins_its_profile() -> None:
    profile = json.loads((ROOT / "docs/contracts/world-profile-0.2.json").read_text(encoding="utf-8"))
    policy = provision.scope_policy(profile, "n")
    assert (policy["default_protocol"], policy["default_contract_version"]) == ("tkos.world", "tkos.world/0.2")
    assert policy["default_profile_ref"] == {"profile_id": profile["profile_id"], "revision": profile["revision"]}
    assert policy["allow_legacy_create"] is False and policy["experimental"] is True
    with pytest.raises(ValueError):
        provision.scope_policy(json.loads((ROOT / "docs/contracts/world-profile-0.1.json").read_text()), "n")


def test_the_env_template_gives_every_variable_the_compose_file_needs() -> None:
    env = dict(line.split("=", 1) for line in (DEPLOY / "world-02.env.example").read_text().splitlines()
               if line and not line.startswith("#"))
    compose = (ROOT / "deploy/offline-release/compose.yaml").read_text()
    assert set(re.findall(r"(?<!\$)\$\{([A-Z_]+)\}", compose)) <= set(env)
    assert (env["COMPOSE_PROJECT_NAME"], env["RUNTIME_HTTP_PORT"], env["TARGET_ARCH"]) == ("tkos-world-02", "8050", "amd64")
    assert env["RUNTIME_API_IMAGE"] == "tkos/ontology-runtime:world-02-__COMMIT__-amd64"
    assert env["RUNTIME_WORKER_IMAGE"] == "tkos/ontology-worker:world-02-__COMMIT__-amd64"
    assert all(env[name].endswith(":v0.5.0-amd64") for name in ("POSTGRES_IMAGE", "MINIO_IMAGE", "MINIO_MC_IMAGE"))


def test_the_build_takes_only_locked_wheels_for_its_architecture() -> None:
    specs = build.lock_specs((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    names = {build.project_name(spec) for spec in specs}
    assert "psycopg-binary" in names and "pydantic-core" in names and len(names) == len(specs)
    assert build.project_name("psycopg_binary-3.3.5-cp312-cp312-manylinux2014_x86_64.whl") == "psycopg-binary"
    assert build.wheel_fits("pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "amd64")
    assert not build.wheel_fits("pydantic_core-2.46.5-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", "amd64")
    assert build.wheel_fits("anyio-4.14.2-py3-none-any.whl", "arm64")


def test_the_smoke_expects_the_tools_of_the_0_2_mcp_face() -> None:
    from tkos_world_mcp.tools_v02 import TOOLS
    assert smoke.MCP_TOOLS == sorted(TOOLS)


@pytest.mark.parametrize(("tenant", "allowed"), [("tokenking-world-02", False), ("tokenking-world-02-smoke", True)])
def test_the_full_smoke_runs_only_on_the_smoke_scope(tmp_path, tenant, allowed) -> None:
    ids = {"scope_id": "s", "tenant_id": tenant, "domains": {key: key for key in smoke.DOMAINS},
           "principals": {key: {"principal_id": key} for key in smoke.KEYS}}
    (tmp_path / "ids.json").write_text(json.dumps(ids))
    for key in smoke.KEYS:
        (tmp_path / f"{key}.token").write_text("not-a-credential")
    run = smoke.Smoke("http://127.0.0.1:1", tmp_path)
    if allowed:
        run.load_state()
        assert run.state == {"scope_id": "s", "skeleton": {}, "runs": {}}
    else:
        with pytest.raises(SystemExit):
            run.load_state()
    assert not (tmp_path / "smoke-world-02.json").exists()


def test_the_probe_takes_the_principals_the_scope_has_and_the_full_smoke_its_own_roster(tmp_path, monkeypatch) -> None:
    ids = {"scope_id": "s", "tenant_id": EO_EXAMPLE["tenant_id"], "domains": {key: key for key in EO_EXAMPLE["domains"]},
           "principals": {key: {"principal_id": key} for key in EO_EXAMPLE["principals"]}}
    (tmp_path / "ids.json").write_text(json.dumps(ids))
    for key in EO_EXAMPLE["principals"]:
        (tmp_path / f"{key}.token").write_text(f"token-of-{key}")
    probe = smoke.Smoke("http://127.0.0.1:1", tmp_path, probe_only=True)
    assert probe.keys == tuple(EO_EXAMPLE["principals"]) and set(probe.tokens) == set(EO_EXAMPLE["principals"])
    asked = []

    def answer(method, path, body=None, who=None):
        asked.append(who)
        if path == "/v1/health":
            return 200, {"ok": True, "db": True}
        if path == "/openapi.json":
            return 200, {"paths": {f"/v1/world/{n}": {} for n in range(5)}}
        return (404 if who else 401), {}
    monkeypatch.setattr(probe, "call", answer)
    probe.basics()
    assert [who for who in asked if who] == list(EO_EXAMPLE["principals"])
    with pytest.raises(SystemExit):
        smoke.Smoke("http://127.0.0.1:1", tmp_path)  # 完整冒烟仍要冒烟那套主体与域
