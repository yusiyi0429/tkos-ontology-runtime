"""Read-only business map: 44 Method inventory entries vs runtime implementation.

Three separate views are combined but never merged:

* ``method_definition_map`` — the curated, dated working-document mapping.  It is
  a read-only snapshot (``method_map_snapshot``); reading it never changes a
  server rule, a protocol registration or an object binding.
* ``runtime_implementation`` — two distinct server facts:
  - **compiled**: the object types/contracts this process was built with
    (``protocol.SUPPORTED_PROTOCOL_CONTRACTS`` + the compiled registries);
  - **scope-enabled**: whether the caller's scope currently has a support
    registry row granting read/write for that contract version.
  A compiled contract whose scope row is missing is reported as
  ``compiled_not_enabled_in_scope`` — never as callable.
* ``authorized_read_availability`` — what the current identity can actually read,
  which is neither of the above.

Runtime links are only emitted for object types whose contract version is both
compiled and scope-read-enabled.  Availability for a mapped but uncompiled or
scope-disabled type is ``not_implemented``/``not_applicable``, never ``failed``.
Infrastructure read failures propagate (they must not be downgraded to "no
data"); only the application-level ``GovernedError`` of one type is recorded and
partial results stay visible.
"""
from __future__ import annotations

from typing import Any

from . import dashboard, db, method_map_snapshot, protocol
from .errors import GovernedError

SCHEMA_VERSION = "tkos.dashboard/0.1"

# Contracts whose rules are documented in this repository.  Method 0.4 has a
# compiled profile identity but is not part of the compiled protocol set yet;
# workspace 0.2 support is reported by its own contract until compiled.
DOCUMENTED_CONTRACTS = ("tkos.method/0.4", "tkos.workspace/0.2")
AVAILABILITY_MODES = ("none", "query")

# States: enabled_in_scope | compiled_not_enabled_in_scope | documented_not_compiled
METHOD_V04 = "tkos.method/0.4"
WORKSPACE_V02 = "tkos.workspace/0.2"


def compiled_types() -> dict[str, frozenset[str]]:
    """Object types per *compiled* Method contract, from the real registry.

    ``protocol.SUPPORTED_PROTOCOL_CONTRACTS`` is the authoritative compiled set;
    a contract that is compiled but not scope-registered is reported as
    ``compiled_not_enabled_in_scope`` (never as merely documented), while an old
    binding keeps its own contract version.  ``dashboard.ONTOLOGY_CONTRACT_VERSIONS``
    remains the concept-directory list and is not used here.
    """
    from . import method_models
    result: dict[str, frozenset[str]] = {}
    for protocol_id, version in sorted(protocol.SUPPORTED_PROTOCOL_CONTRACTS):
        if protocol_id != "tkos.method":
            continue
        _params, _targets, payloads = method_models.registry(version)
        result[version] = frozenset(payloads) | {"EvidenceAsset"}
    return result


def _scope_registry(conn: Any, scope_id: str, version: str) -> dict[str, Any]:
    """Current scope registry facts for one contract version (compiled set only)."""
    row = protocol.current_registry(conn, scope_id, "tkos.method", version)
    if row is None or row["contract_version"] != version:
        return {"scope_registered": False, "scope_read_enabled": False,
                "scope_write_enabled": False}
    try:
        content = protocol.RegistryContent.model_validate(row["content"])
    except Exception:
        # An uninterpretable registry row grants nothing; it is not a read error.
        return {"scope_registered": True, "scope_read_enabled": False,
                "scope_write_enabled": False}
    return {"scope_registered": True, "scope_read_enabled": content.can_read,
            "scope_write_enabled": content.can_write}


def contract_states(conn: Any, scope_id: str,
                    compiled: dict[str, frozenset[str]] | None = None) -> dict[str, dict[str, Any]]:
    compiled = compiled if compiled is not None else compiled_types()
    states: dict[str, dict[str, Any]] = {}
    for version in compiled:
        scope = _scope_registry(conn, scope_id, version)
        states[version] = {"compiled": True, **scope,
                           "scope_enabled": scope["scope_read_enabled"]}
    return states


def _workspace_v02_state() -> str:
    """The workspace 0.2 surface is separate from Method scope registries.

    It is counted as compiled when its runtime modules are present; grant
    enforcement is per-scene membership, never a Method scope registry row.
    """
    try:
        from . import workspace_v02_service  # noqa: F401
    except Exception:
        return "documented_not_compiled"
    return "compiled_surface"


def documented_contract_state(version: str,
                              states: dict[str, dict[str, Any]] | None = None) -> str:
    if version == METHOD_V04:
        if version not in (states or {}):
            return "documented_not_compiled"
        state = states[version]
        if state.get("scope_enabled"):
            return "enabled_in_scope"
        if state.get("compiled"):
            return "compiled_not_enabled_in_scope"
        return "documented_not_compiled"
    if version == WORKSPACE_V02:
        return _workspace_v02_state()
    return "unknown"


def _support(entry: dict[str, Any], compiled: dict[str, frozenset[str]],
             states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    types = list(entry["runtime_object_types"])
    per_type = {name: sorted(version for version, names in compiled.items() if name in names)
                for name in types}
    enabled: dict[str, list[str]] = {
        name: [version for version in versions if states.get(version, {}).get("scope_enabled")]
        for name, versions in per_type.items()
    }
    if not types:
        return {"assessment": entry["runtime_support_assessment"],
                "compiled": "no_runtime_object_type",
                "compiled_contract_versions": [], "scope_enabled_contract_versions": [],
                "links": []}
    with_compiled = {name: versions for name, versions in per_type.items() if versions}
    with_enabled = {name: versions for name, versions in enabled.items() if versions}
    if len(with_compiled) == len(types):
        state = "compiled"
    elif with_compiled:
        state = "partially_compiled"
    else:
        state = "not_compiled"
    compiled_versions = sorted({version for values in with_compiled.values() for version in values})
    enabled_versions = sorted({version for values in with_enabled.values() for version in values})
    # A runtime link is offered only for a type whose contract version is both
    # compiled and scope-read-enabled; documented-only concepts get no link.
    links = [{"object_type": name, "contract_versions": enabled[name],
              "catalog_path": f"/catalog/objects?object_type={name}"}
             for name in sorted(with_enabled)]
    return {"assessment": entry["runtime_support_assessment"], "compiled": state,
            "compiled_contract_versions": compiled_versions,
            "scope_enabled_contract_versions": enabled_versions, "links": links}


def _availability(conn: Any, ctx: Any, types: list[str]) -> dict[str, str]:
    """Authorized visibility per *queryable* runtime object type; no counts.

    Only application-level ``GovernedError`` is absorbed per type: one type's
    authorization outcome must not hide another type's rows, while an
    infrastructure/SQL failure propagates and fails the whole read.
    """
    statuses: dict[str, str] = {}
    for name in types:
        try:
            page = dashboard.catalog_objects(conn, ctx, object_type=name, limit=1)
            statuses[name] = "visible" if page["loaded_count"] else "none_visible"
        except GovernedError:
            statuses[name] = "failed"
    return statuses


def _entry_availability(entry: dict[str, Any], statuses: dict[str, str] | None,
                        per_type_queryable: dict[str, list[str]]) -> dict[str, Any]:
    if statuses is None:
        return {"status": "not_queried",
                "note": "当前身份授权范围未查询；not_queried 不是“无数据”。"}
    types = entry["runtime_object_types"]
    if not types:
        return {"status": "not_applicable",
                "note": "该概念没有已编译的运行时对象类型，无法按对象查询。"}
    queryable = [name for name in types if per_type_queryable.get(name)]
    if not queryable:
        return {"status": "not_implemented",
                "note": "该概念映射的对象类型未编译或未在当前 scope 启用，不查询数据。"}
    observed: dict[str, str] = {}
    for name in types:
        if name in queryable:
            observed[name] = statuses.get(name, "failed")
        else:
            observed[name] = "not_implemented"
    values = {value for name, value in observed.items() if name in queryable}
    if "visible" in values and "failed" in values:
        status = "partial_failed"
    elif "visible" in values and len(values) > 1:
        # Some mapped types are visible and others have no authorized rows (or
        # a failed read).  "none_visible" is not proof of unreadability, so the
        # combined label only states partial visibility.
        status = "partially_visible"
    elif values == {"visible"}:
        status = "visible"
    elif "none_visible" in values and "failed" in values:
        status = "partial_failed"
    elif values == {"none_visible"}:
        status = "none_visible"
    elif values == {"failed"}:
        status = "failed"
    else:
        status = "unknown"
    return {"status": status, "by_object_type": observed,
            "note": "visible 表示按当前身份至少可读一条；none_visible 表示该对象类型当前身份未读到，"
                    "不是“不可读”证明；failed 表示读取失败；不返回记录条数；"
                    "not_implemented 表示未编译或未在 scope 启用，不是查询失败。"}


def build(conn: Any, ctx: Any, *, availability: str = "none") -> dict[str, Any]:
    if availability not in AVAILABILITY_MODES:
        raise GovernedError("INVALID_REQUEST", "Unknown availability mode.", status=422)
    compiled = compiled_types()
    states = contract_states(conn, ctx.scope_id, compiled)

    def versions_for(name: str) -> list[str]:
        return [version for version, names in compiled.items() if name in names]

    def enabled_for(name: str) -> list[str]:
        return [version for version in versions_for(name) if states[version]["scope_enabled"]]

    snapshot = method_map_snapshot.SNAPSHOT
    entries = snapshot["entries"]
    statuses = None
    if availability == "query":
        queried = sorted({name for entry in entries for name in entry["runtime_object_types"]
                          if enabled_for(name)})
        statuses = _availability(conn, ctx, queried)
    result_entries = []
    for entry in entries:
        support = _support(entry, compiled, states)
        queryable = {name: enabled_for(name) for name in entry["runtime_object_types"]}
        result_entries.append(db.jsonable({
            **entry,
            "runtime_support": support,
            "runtime_links": support["links"],
            "authorized_read_availability": _entry_availability(entry, statuses, queryable),
        }))
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["business_category"]] = counts.get(entry["business_category"], 0) + 1
    support_counts: dict[str, int] = {}
    for entry in result_entries:
        key = entry["runtime_support"]["assessment"]
        support_counts[key] = support_counts.get(key, 0) + 1
    return db.jsonable({
        "schema_version": SCHEMA_VERSION,
        "read_at": dashboard._read_at(conn),
        "source_snapshot": {
            "checked_date": snapshot["checked_date"],
            "scope": snapshot["scope"],
            "snapshot_sha256": method_map_snapshot.CONTENT_SHA256,
            "entry_count": len(entries),
            "sources": snapshot["sources"],
            "note": snapshot["source_note"],
        },
        "method_definition_map": {
            "role": "documented_business_map",
            "documented_contracts": [
                {"contract_version": version,
                 "support_status": documented_contract_state(version, states)}
                for version in DOCUMENTED_CONTRACTS
            ],
            "note": ("最新业务地图是带日期的只读快照，不自动更新服务器规则或对象绑定；"
                     "文档变化需人工重新核对受影响条目。"),
        },
        "runtime_implementation": {
            "role": "compiled_runtime_support",
            "compiled_contract_versions": sorted(compiled),
            "scope_enabled_contract_versions": sorted(
                version for version, state in states.items() if state["scope_enabled"]),
            "scope_registered_contract_versions": sorted(
                version for version, state in states.items() if state["scope_registered"]),
            "object_types_by_contract": {version: sorted(names)
                                         for version, names in sorted(compiled.items())},
            "documented_contract_states": {
                version: documented_contract_state(version, states)
                for version in DOCUMENTED_CONTRACTS},
            "note": ("compiled 表示本进程编译支持；scope_enabled 表示当前 scope 登记了读取支持。"
                     "运行时链接需要两者同时成立；未启用的契约不产生可调用动作。"
                     "workspace/0.2 是独立 surface：compiled_surface 表示运行时模块存在，"
                     "其授权按场景成员与精确分享执行，不使用 Method scope registry。"),
        },
        "availability": {
            "mode": availability,
            "queried_object_types": 0 if statuses is None else len(statuses),
            "note": ("not_queried 表示未按当前身份查询授权数据；"
                     "visible/none_visible/failed 均不返回可读记录条数。"),
        },
        "inventory_counts": {
            "by_business_category": counts,
            "by_runtime_support_assessment": support_counts,
            "note": "计数为该 44 项工作文档清单的固定分类计数，不是业务记录条数。",
        },
        "entries": result_entries,
        "notes": [
            "业务成熟度（Method标准状态）、编译/启用支持与授权数据可得性分别记录，不互相替代。",
            "概念条目在无运行时对象或无实例时保留；空结果只表示当前身份未读到。",
            "0.1／0.2／0.3 的旧绑定与历史解释不因本地图变化。",
        ],
    })


__all__ = ["build", "compiled_types", "contract_states", "documented_contract_state",
           "DOCUMENTED_CONTRACTS", "AVAILABILITY_MODES"]
