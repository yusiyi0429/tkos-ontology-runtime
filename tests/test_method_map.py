"""Contract tests for the read-only Method→Runtime map (B1).

Focused on the three separations the review requires:

* compiled support (this process) vs scope-enabled support (registry row);
* availability for a mapped but uncompiled/disabled type is
  ``not_implemented``/``not_applicable``, never ``failed``;
* one type's application-level read failure stays visible without hiding other
  types' data, while an infrastructure failure propagates.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from memory_service_runtime.governed import dashboard, dashboard_routes, db, method_map, method_map_snapshot, method_models, protocol
from memory_service_runtime.governed.errors import GovernedError


def registry_row(version: str, *, read: bool = True, write: bool = True) -> dict:
    return {"contract_version": version, "content": {
        "can_read": read, "can_create": write, "can_write": write,
        "evidence_upload": write, "actions": ["probe"], "object_types": ["Mission"],
        "readonly_compat": [version], "notes": "test row"}}


@pytest.fixture
def map_env(monkeypatch):
    """Registry stubs + read clock; no database connection is opened."""
    env = {"rows": {}}
    monkeypatch.setattr(method_map.protocol, "current_registry",
                        lambda conn, scope_id, protocol_id, contract_version=None:
                        env["rows"].get(contract_version))
    monkeypatch.setattr(method_map.dashboard, "_read_at",
                        lambda conn: "2026-09-17T00:00:00+00:00")
    return env


def run_build(*, availability="none"):
    return method_map.build(object(), SimpleNamespace(scope_id="scope-1"),
                            availability=availability)


# ------------------------------------------------------------- snapshot data

def test_snapshot_covers_44_inventory_entries_with_sources():
    snapshot = method_map_snapshot.SNAPSHOT
    entries = snapshot["entries"]
    assert len(entries) == 44
    assert [entry["id"] for entry in entries] == [f"I{index:02d}" for index in range(1, 45)]
    categories: dict[str, int] = {}
    for entry in entries:
        categories[entry["business_category"]] = categories.get(entry["business_category"], 0) + 1
        assert entry["business_maturity"] in {
            "defined", "partial", "to_define", "open_classification", "defined_for_m1a"}
        assert entry["runtime_support_assessment"] in {
            "reusable_partial", "pending_business_close", "requires_contract_change",
            "not_implemented", "unknown"}
        assert entry["source_ref"]["source_id"].startswith("B")
        assert entry["source_ref"]["table_id"]
        assert entry["source_ref"]["record_ref"]
        assert isinstance(entry["runtime_object_types"], list)
    assert categories == {"anchor": 10, "reference": 8,
                           "business_artifact": 11, "evidence_runtime_record": 15}
    source_ids = {source["source_id"] for source in snapshot["sources"]}
    assert source_ids == {f"B{index:02d}" for index in range(1, 11)}
    assert snapshot["checked_date"] == "2026-09-17"


def test_snapshot_is_reproducible_from_the_committed_generator():
    import hashlib
    import json
    # The digest covers the canonical data, so the generated module stays
    # readable (pprint literals) while a hand edit or generator drift is caught.
    canonical = json.dumps(method_map_snapshot.SNAPSHOT, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == method_map_snapshot.CONTENT_SHA256


def test_every_entry_has_a_concise_business_definition_and_purpose():
    for entry in method_map_snapshot.SNAPSHOT["entries"]:
        assert entry["business_name"], entry["id"]
        assert 6 <= len(entry["purpose"]) <= 320, entry["id"]
        assert 10 <= len(entry["definition"]) <= 480, entry["id"]
        assert entry["definition_source"]["document"].endswith("-normalized.json")
        # The definition is business language, not only the engineering gap text.
        assert entry["definition"] != entry["gap"]


def test_unimplemented_and_reference_concepts_carry_definitions():
    entries = {entry["id"]: entry for entry in method_map_snapshot.SNAPSHOT["entries"]}
    play = entries["I07"]
    assert play["business_name"] and "赢法" in play["purpose"]
    assert play["runtime_object_types"] == []
    plan = entries["I08"]
    assert "Agent" in plan["purpose"] or "人" in plan["purpose"]
    assert plan["runtime_object_types"] == []
    reference = entries["I11"]
    assert reference["business_category"] == "reference"
    assert "根本目的" in reference["purpose"]
    assert reference["runtime_object_types"] == []


def test_planned_contracts_are_documented_ones():
    planned = {contract for entry in method_map_snapshot.SNAPSHOT["entries"]
               for contract in entry["planned_contracts"]}
    assert planned <= set(method_map.DOCUMENTED_CONTRACTS)


# --------------------------------------------- compiled vs scope-enabled facts

def test_documented_contract_state_separates_compiled_from_scope_enabled():
    assert method_map.documented_contract_state("tkos.method/0.4", {}) == "documented_not_compiled"
    assert method_map.documented_contract_state(
        "tkos.method/0.4", {"tkos.method/0.4": {"compiled": True, "scope_enabled": False}}
    ) == "compiled_not_enabled_in_scope"
    assert method_map.documented_contract_state(
        "tkos.method/0.4", {"tkos.method/0.4": {"compiled": True, "scope_enabled": True}}
    ) == "enabled_in_scope"
    # workspace 0.2 is a separate surface: compiled when its modules exist,
    # never judged by the Method protocol/scope registry.
    assert method_map.documented_contract_state("tkos.workspace/0.2", {}) == "compiled_surface"


def test_build_reports_compiled_and_scope_enabled_separately(map_env):
    map_env["rows"] = {"tkos.method/0.3": registry_row("tkos.method/0.3")}
    result = run_build()
    runtime = result["runtime_implementation"]
    assert runtime["compiled_contract_versions"] == sorted(method_map.compiled_types())
    assert runtime["scope_enabled_contract_versions"] == ["tkos.method/0.3"]
    assert runtime["scope_registered_contract_versions"] == ["tkos.method/0.3"]
    assert set(runtime["documented_contract_states"]) == set(method_map.DOCUMENTED_CONTRACTS)
    mission = next(entry for entry in result["entries"] if entry["id"] == "I06")
    assert mission["runtime_support"]["compiled"] == "compiled"
    assert mission["runtime_support"]["scope_enabled_contract_versions"] == ["tkos.method/0.3"]
    assert [link["object_type"] for link in mission["runtime_links"]] == ["Mission"]
    assert mission["runtime_links"][0]["contract_versions"] == ["tkos.method/0.3"]
    play = next(entry for entry in result["entries"] if entry["id"] == "I07")
    assert play["runtime_support"]["compiled"] == "no_runtime_object_type"
    assert play["runtime_links"] == []


def test_scope_enabled_needs_a_current_registry_row(map_env):
    map_env["rows"] = {}
    result = run_build()
    assert result["runtime_implementation"]["scope_enabled_contract_versions"] == []
    mission = next(entry for entry in result["entries"] if entry["id"] == "I06")
    assert mission["runtime_support"]["scope_enabled_contract_versions"] == []
    assert mission["runtime_links"] == []
    assert mission["runtime_support"]["compiled"] == "compiled"
    assert mission["authorized_read_availability"]["status"] == "not_queried"


def test_scope_read_disabled_row_grants_nothing(map_env):
    map_env["rows"] = {"tkos.method/0.3": registry_row("tkos.method/0.3", read=False, write=True)}
    result = run_build()
    assert result["runtime_implementation"]["scope_enabled_contract_versions"] == []
    mission = next(entry for entry in result["entries"] if entry["id"] == "I06")
    assert mission["runtime_links"] == []


def test_uninterpretable_scope_row_grants_nothing(map_env):
    map_env["rows"] = {"tkos.method/0.3": {"contract_version": "tkos.method/0.3",
                                           "content": {"bogus": True}}}
    result = run_build()
    assert result["runtime_implementation"]["scope_registered_contract_versions"] == ["tkos.method/0.3"]
    assert result["runtime_implementation"]["scope_enabled_contract_versions"] == []
    assert result["runtime_implementation"]["compiled_contract_versions"]


# ------------------------------------------------------------ availability

def test_availability_is_not_queried_by_default(map_env, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("catalog must not be queried in the default mode")

    monkeypatch.setattr(method_map.dashboard, "catalog_objects", explode)
    map_env["rows"] = {"tkos.method/0.3": registry_row("tkos.method/0.3")}
    result = run_build()
    assert result["availability"]["mode"] == "none"
    assert result["availability"]["queried_object_types"] == 0
    assert all(entry["authorized_read_availability"]["status"] == "not_queried"
               for entry in result["entries"])


def test_uncompiled_or_scope_disabled_entry_is_not_implemented(map_env, monkeypatch):
    calls: list[str] = []

    def catalog(conn, ctx, *, object_type, limit):
        calls.append(object_type)
        return {"loaded_count": 0}

    monkeypatch.setattr(method_map.dashboard, "catalog_objects", catalog)
    map_env["rows"] = {}
    result = run_build(availability="query")
    assert calls == []  # nothing is scope-enabled, so nothing is queried
    mission = next(entry for entry in result["entries"] if entry["id"] == "I06")
    assert mission["authorized_read_availability"]["status"] == "not_implemented"
    assert "未编译" in mission["authorized_read_availability"]["note"]
    play = next(entry for entry in result["entries"] if entry["id"] == "I07")
    assert play["authorized_read_availability"]["status"] == "not_applicable"


def test_availability_partial_failure_keeps_visible_data(map_env, monkeypatch):
    def catalog(conn, ctx, *, object_type, limit):
        if object_type == "EvidenceAsset":
            raise GovernedError("FORBIDDEN")
        if object_type == "BusinessFact":
            return {"loaded_count": 1}
        return {"loaded_count": 0}

    monkeypatch.setattr(method_map.dashboard, "catalog_objects", catalog)
    map_env["rows"] = {"tkos.method/0.3": registry_row("tkos.method/0.3")}
    result = run_build(availability="query")
    entries = {entry["id"]: entry for entry in result["entries"]}
    # I30 maps BusinessFact + EvidenceAsset: one visible, one application failure
    combined = entries["I30"]["authorized_read_availability"]
    assert combined["status"] == "partial_failed"
    assert combined["by_object_type"]["BusinessFact"] == "visible"
    assert combined["by_object_type"]["EvidenceAsset"] == "failed"
    # I06 has only Mission, which has no rows here but must not be "failed"
    assert entries["I06"]["authorized_read_availability"]["status"] == "none_visible"
    # I03 (StrategicArchitecture) is uncompiled at this checkpoint
    if "StrategicArchitecture" not in method_map.compiled_types().get("tkos.method/0.3", set()):
        assert entries["I03"]["authorized_read_availability"]["status"] == "not_implemented"


def test_infrastructure_failure_propagates(map_env, monkeypatch):
    def broken(conn, ctx, *, object_type, limit):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(method_map.dashboard, "catalog_objects", broken)
    map_env["rows"] = {"tkos.method/0.3": registry_row("tkos.method/0.3")}
    with pytest.raises(RuntimeError):
        run_build(availability="query")


def test_invalid_availability_mode_fails_closed(map_env):
    with pytest.raises(GovernedError) as error:
        run_build(availability="all")
    assert error.value.code == "INVALID_REQUEST"
    assert error.value.status == 422


# ------------------------------------------------------------ read boundary

def test_read_method_map_requires_a_current_read_assignment(monkeypatch):
    @contextmanager
    def transaction(token):
        yield object(), SimpleNamespace(assignments=[])

    monkeypatch.setattr(db, "transaction", transaction)
    with pytest.raises(GovernedError) as error:
        dashboard_routes.read_method_map("token")
    assert error.value.code == "FORBIDDEN"


def test_map_endpoint_is_registered_on_both_surfaces():
    paths = {route.path for route in dashboard_routes.router.routes}
    assert "/v1/dashboard/ontology/method-map" in paths
    assert "read_method_map" in dashboard_routes.__all__
    from memory_service_app import dashboard as facade
    facade_paths = {route.path for route in facade.api.routes}
    assert "/dashboard/api/v1/ontology/method-map" in facade_paths


def test_catalog_lists_only_compiled_registries(map_env):
    # The concept directory lists only contracts whose registries are compiled;
    # documented-not-enabled contracts are reported by the map instead.
    assert set(dashboard.ONTOLOGY_CONTRACT_VERSIONS) <= set(method_map.compiled_types())
    map_env["rows"] = {}
    result = run_build()
    registered = set(result["runtime_implementation"]["compiled_contract_versions"])
    assert set(dashboard.ONTOLOGY_CONTRACT_VERSIONS) == registered
    for version in dashboard.ONTOLOGY_CONTRACT_VERSIONS:
        assert version in registered
    assert result["runtime_implementation"]["documented_contract_states"]["tkos.workspace/0.2"] == "compiled_surface"


def test_v04_requests_fail_closed_while_not_compiled():
    if ("tkos.method", "tkos.method/0.4") in protocol.SUPPORTED_PROTOCOL_CONTRACTS:
        pytest.skip("0.4 support was enabled by the B3/B4 increment; fail-closed is covered there")
    with pytest.raises(GovernedError) as error:
        method_models.registry("tkos.method/0.4")
    assert error.value.code == "PROTOCOL_NOT_SUPPORTED"


def test_http_boundary_requires_a_bearer_identity():
    """The route is wired into the real app and fails closed before any SQL."""
    from memory_service_app.main import app as service_app
    from tests.asgi_client import get

    response = get(service_app, "/v1/dashboard/ontology/method-map")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    facade = get(service_app, "/dashboard/api/v1/ontology/method-map")
    assert facade.status_code in {403, 404}


def test_partially_visible_does_not_claim_unreadable_types(map_env, monkeypatch):
    """One type visible + another with no authorized rows is partial visibility.

    ``none_visible`` only means no authorized rows were read; it never proves
    the type is unreadable, so the combined label must not say so.
    """
    def catalog(conn, ctx, *, object_type, limit):
        if object_type == "ResearchReport":
            return {"loaded_count": 2}
        return {"loaded_count": 0}

    monkeypatch.setattr(method_map.dashboard, "catalog_objects", catalog)
    map_env["rows"] = {"tkos.method/0.3": registry_row("tkos.method/0.3")}
    result = run_build(availability="query")
    entries = {entry["id"]: entry for entry in result["entries"]}
    # I23 maps ResearchReport + EvidenceAsset: visible + none_visible
    combined = entries["I23"]["authorized_read_availability"]
    assert combined["status"] == "partially_visible"
    assert combined["by_object_type"] == {"ResearchReport": "visible", "EvidenceAsset": "none_visible"}
    # The note corrects the meaning explicitly instead of implying unreadability.
    assert "不是“不可读”证明" in combined["note"]


def test_compiled_reporting_follows_the_real_registry(map_env):
    """0.4 must not be reported as document-only while the protocol compiles it."""
    compiled = method_map.compiled_types()
    assert "tkos.method/0.3" in compiled
    from memory_service_runtime.governed import method_v04_models
    if ("tkos.method", "tkos.method/0.4") in protocol.SUPPORTED_PROTOCOL_CONTRACTS:
        assert "tkos.method/0.4" in compiled
        assert "StrategicAgreement" in compiled["tkos.method/0.4"]
        assert method_v04_models.ACTION_PARAMS  # compiled registry is the source
    map_env["rows"] = {}
    result = run_build()
    assert "tkos.method/0.4" in result["runtime_implementation"]["compiled_contract_versions"] or \
        ("tkos.method", "tkos.method/0.4") not in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    # A compiled-but-unregistered 0.4 is never labeled document-only.
    if "tkos.method/0.4" in compiled:
        assert result["runtime_implementation"]["documented_contract_states"]["tkos.method/0.4"] == \
            "compiled_not_enabled_in_scope"
