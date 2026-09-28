from memory_service_runtime.governed import method_versions, protocol


def test_method_versions_match_the_supported_protocol_contracts():
    supported = sorted(version for protocol_id, version in protocol.SUPPORTED_PROTOCOL_CONTRACTS if protocol_id == "tkos.method")
    assert list(method_versions.METHOD_VERSIONS) == supported


def test_version_families_are_suffixes_of_the_ordered_list():
    assert method_versions.SINCE_V02 == ("tkos.method/0.2", "tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5")
    assert method_versions.SINCE_V03 == ("tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5")
    assert method_versions.FORMAL_GOVERNANCE_VERSIONS == frozenset({"tkos.method/0.4", "tkos.method/0.5"})
