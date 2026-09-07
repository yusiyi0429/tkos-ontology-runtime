"""Context Graph v1 machine-readable type and relation contracts."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

CONFIG_DIR = Path(__file__).parent
SCHEMA_VERSION = 1
SOURCE_MARKER_KEY = "context_graph_schema_version"


def is_typed_context_graph(source: Mapping[str, Any] | None) -> bool:
    """Return whether proposal metadata marks the current governed graph contract."""
    return bool(source) and source.get(SOURCE_MARKER_KEY) == SCHEMA_VERSION


class ContextGraphValidationError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_type_contract() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((CONFIG_DIR / "types_v1.yaml").read_text(encoding="utf-8"))
    if raw.get("version") != 1 or not isinstance(raw.get("types"), dict):
        raise RuntimeError("invalid Context Graph type contract")
    return raw["types"]


@lru_cache(maxsize=1)
def load_relation_contract() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load((CONFIG_DIR / "relation_rules_v1.yaml").read_text(encoding="utf-8"))
    if raw.get("version") != 1 or not isinstance(raw.get("relations"), dict):
        raise RuntimeError("invalid Context Graph relation contract")
    return raw["relations"]


TYPE_KEYS = frozenset(load_type_contract())
RELATION_TYPES = frozenset(load_relation_contract())


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


# 判别式属性：仅当 type_key 命中时必填，其余类型必须为 NULL。与 0005_context_graph.sql 的
# ck_sem_ent_level_strategic / _level_outcome / _scope / _orgsub / _moat 一一对应。
#   {属性名: (归属 type_key, 允许取值集合 or None 表示任意非空)}
_DISCRIMINATORS: dict[str, tuple[str, frozenset[str] | None]] = {
    "strategic_level": ("StrategicChoice", frozenset({"enterprise", "sub_strategy"})),
    "strategic_period": ("StrategicChoice", None),
    "outcome_level": ("Outcome", frozenset({"company", "domain"})),
    "status_scope": ("OperatingStatus", frozenset({"company", "outcome"})),
    "org_subtype": ("Organization", frozenset({"company", "business_unit", "team", "role", "person"})),
    "is_moat": ("OrganizationalCapability", None),
}


def _validate_discriminators(type_key: str, values: Mapping[str, Any]) -> None:
    """判别式属性双向校验（预检与受确认写入共用同一份策略）。

    以前这 5 条只由 PostgreSQL CHECK 兜底，预检看不见：validate_manifest 会对一个
    「OrganizationalCapability 缺 is_moat」的 manifest 返回 fileable=True，提案照样落库，
    直到人工确认那一刻才被 ck_sem_ent_moat 拒绝——提案卡死在 pending 且错误信息是裸约束名。
    把规则搬到这里，preflight 与 governed write 不再是两套子集（见本模块
    required_relation_types 的同类意图）。
    """
    for attr, (owner_type, allowed) in _DISCRIMINATORS.items():
        value = values.get(attr)
        if type_key == owner_type:
            if value is None:
                raise ContextGraphValidationError(f"{type_key} requires {attr}")
            if allowed is not None and value not in allowed:
                raise ContextGraphValidationError(
                    f"{type_key}.{attr} must be one of {sorted(allowed)}, got {value!r}"
                )
        elif value is not None:
            raise ContextGraphValidationError(
                f"{attr} is only allowed on {owner_type}, not {type_key}"
            )


def validate_entity_content(
    type_key: str,
    content: dict[str, Any],
    rationale: str | None,
    *,
    strategic_level: str | None = None,
    is_moat: bool | None = None,
    strategic_period: str | None = None,
    outcome_level: str | None = None,
    status_scope: str | None = None,
    org_subtype: str | None = None,
) -> None:
    """Thin business validation plus the type discriminators mirrored from the DB CHECKs."""
    contract = load_type_contract().get(type_key)
    if contract is None:
        raise ContextGraphValidationError(f"unknown type_key: {type_key}")
    if not isinstance(content, dict):
        raise ContextGraphValidationError("content must be an object")
    missing = [key for key in contract.get("required_content", []) if not _present(content.get(key))]
    if missing:
        raise ContextGraphValidationError(f"{type_key} missing required content: {missing}")
    required_any = contract.get("required_any_content", [])
    if required_any and not any(_present(content.get(key)) for key in required_any):
        raise ContextGraphValidationError(f"{type_key} requires one of {required_any}")
    if contract.get("rationale_required") and not _present(rationale):
        raise ContextGraphValidationError(f"{type_key} requires rationale")
    if type_key == "StrategicChoice" and strategic_level == "enterprise" and not _present(content.get("time_window")):
        raise ContextGraphValidationError("enterprise StrategicChoice requires content.time_window")
    if type_key == "OrganizationalCapability" and is_moat is True and not _present(content.get("moat_basis")):
        raise ContextGraphValidationError("moat capability requires content.moat_basis")
    _validate_discriminators(
        type_key,
        {
            "strategic_level": strategic_level,
            "strategic_period": strategic_period,
            "outcome_level": outcome_level,
            "status_scope": status_scope,
            "org_subtype": org_subtype,
            "is_moat": is_moat,
        },
    )


# 契约未列为 required 但属于业务描述的条件字段（按 type_key 生效）。
# 这里是唯一权威：叙事投影（context_pack）与 embedding 文本组装（backfill）共用同一份，
# 避免「加了一个条件字段但只改了其中一处」的漂移（与本模块
# required_relation_types / _DISCRIMINATORS 同类意图）。
CONDITIONAL_CONTENT_KEYS: dict[str, tuple[str, ...]] = {
    "StrategicChoice": ("time_window",),
    "OrganizationalCapability": ("moat_basis",),
    # DRI 不进 required_content：不是每类组织都有 DRI（org_subtype='person' 就没有），
    # 强制必填会把它变成占位字段。作为条件字段，有就进叙事投影与 embedding
    # 文本，没就自然缺省（两侧消费方都跳过空值）。
    "Organization": ("dri",),
}


def description_keys(type_key: str | None) -> tuple[str, ...]:
    """从 types_v1 契约派生业务字段顺序：required_content -> required_any -> 条件字段。

    契约是唯一来源；谁都不得维护第二份字段清单。
    """
    if not type_key:
        return ()
    contract = load_type_contract().get(type_key)
    if contract is None:
        return ()
    keys = [str(key) for key in contract.get("required_content", [])]
    keys += [str(key) for key in contract.get("required_any_content", []) if key not in keys]
    keys += [key for key in CONDITIONAL_CONTENT_KEYS.get(type_key, ()) if key not in keys]
    return tuple(keys)


def type_label_zh(type_key: str) -> str:
    """类型的中文标签（契约缺失时退回 type_key 本身，不报错）。"""
    contract = load_type_contract().get(type_key)
    if not contract:
        return type_key
    return str(contract.get("label_zh") or type_key)


def required_relation_types(entity: Mapping[str, Any]) -> dict[str, int]:
    """Return the exact required-relation cardinalities for one entity draft.

    This is the canonical policy shared by confirmation and shadow-manifest validation.
    Keeping it beside the machine-readable type contracts prevents preflight and governed
    writes from drifting into two subtly different graph schemas.
    """
    type_key = entity.get("type_key")
    required: dict[str, int] = {}
    if type_key != "CompanyVision":
        required["primary_alignment"] = 1
    if type_key == "StrategicChoice" and entity.get("strategic_level") == "sub_strategy":
        required["sub_strategy_of"] = 1
    if type_key == "Outcome":
        required["responsible_for"] = 1
        if entity.get("outcome_level") == "domain":
            required["domain_outcome_of"] = 1
    return required


def validate_relation_matrix(
    relation_type: str,
    source: dict[str, Any],
    target: dict[str, Any],
) -> None:
    rule = load_relation_contract().get(relation_type)
    if rule is None:
        raise ContextGraphValidationError(f"unsupported new-generation relation_type: {relation_type}")
    source_type, target_type = source.get("type_key"), target.get("type_key")
    if source_type == "CompanyVision":
        raise ContextGraphValidationError("CompanyVision cannot be a relation source")
    expected_source = rule["source"]
    expected_target = rule["target"]
    if expected_source not in ("any", "any_non_root") and source_type != expected_source:
        raise ContextGraphValidationError(
            f"{relation_type} source must be {expected_source}, got {source_type}"
        )
    if expected_target != "any" and target_type != expected_target:
        raise ContextGraphValidationError(
            f"{relation_type} target must be {expected_target}, got {target_type}"
        )
    if relation_type == "domain_outcome_of":
        if source.get("outcome_level") != "domain" or target.get("outcome_level") != "company":
            raise ContextGraphValidationError("domain_outcome_of requires domain Outcome -> company Outcome")
    if relation_type == "sub_strategy_of":
        if source.get("strategic_level") != "sub_strategy":
            raise ContextGraphValidationError("sub_strategy_of source must be sub_strategy")
        if target.get("strategic_level") not in ("enterprise", "sub_strategy"):
            raise ContextGraphValidationError("sub_strategy_of target must be enterprise/sub_strategy")
        if source.get("strategic_period") != target.get("strategic_period"):
            raise ContextGraphValidationError("sub-strategy must inherit target strategic_period")
